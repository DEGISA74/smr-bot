"""
Formasyon V2 için geleceği görmeyen yürüyen-zaman doğrulaması.

Bu modül canlı uygulamadan bağımsızdır. Her tarih kesitinde formasyon_v2
motoruna yalnız o tarihe kadar oluşmuş mumları verir; sonraki mumlar yalnızca
sonucu ölçmek için kullanılır.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

from formasyon_v2 import ENGINE_VERSION, TIMEFRAME_CONFIG, analyze_formations


BACKTEST_VERSION = "1.3.0-research"
DEFAULT_STAGES = ("KIRILIM_DOĞRULANDI",)
ALL_BREAK_STAGES = (
    "KIRILIM_ADAYI",
    "KIRILIM_DOĞRULANDI",
    "YENİDEN_TEST",
)


def _ticker_from_path(path: Path, timeframe: str) -> str:
    suffix = "_1d.parquet" if timeframe == "1d" else "_4h.parquet"
    return path.name[: -len(suffix)].replace(".IS", "")


def _clean_source_frame(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    if not isinstance(work.index, pd.DatetimeIndex):
        work.index = pd.to_datetime(work.index)
    work = work[~work.index.duplicated(keep="last")].sort_index()
    needed = ["Open", "High", "Low", "Close"]
    keep = needed + (["Volume"] if "Volume" in work.columns else [])
    work = work[keep]
    for column in keep:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work = work.dropna(subset=needed)
    valid = (
        (work["Open"] > 0)
        & (work["High"] > 0)
        & (work["Low"] > 0)
        & (work["Close"] > 0)
        & (work["High"] >= work[["Open", "Close", "Low"]].max(axis=1))
        & (work["Low"] <= work[["Open", "Close", "High"]].min(axis=1))
    )
    return work.loc[valid]


def _first_level_event(
    future: pd.DataFrame,
    direction: str,
    target: Optional[float],
    invalidation: Optional[float],
) -> tuple[str, Optional[int]]:
    for offset, (_, bar) in enumerate(future.iterrows(), start=1):
        if direction == "bullish":
            target_hit = target is not None and float(bar["High"]) >= float(target)
            invalid_hit = (
                invalidation is not None
                and float(bar["Low"]) <= float(invalidation)
            )
        else:
            target_hit = target is not None and float(bar["Low"]) <= float(target)
            invalid_hit = (
                invalidation is not None
                and float(bar["High"]) >= float(invalidation)
            )
        if target_hit and invalid_hit:
            return "AYNI_BAR_BELİRSİZ", offset
        if target_hit:
            return "HEDEF", offset
        if invalid_hit:
            return "GEÇERSİZLİK", offset
    return "HİÇBİRİ", None


def _forward_metrics(
    frame: pd.DataFrame,
    signal_bar: int,
    direction: str,
    horizons: tuple[int, ...],
    target: Optional[float],
    invalidation: Optional[float],
) -> dict[str, Any]:
    entry = float(frame["Close"].iloc[signal_bar])
    sign = 1.0 if direction == "bullish" else -1.0
    metrics: dict[str, Any] = {"entry_price": round(entry, 6)}
    for horizon in horizons:
        exit_price = float(frame["Close"].iloc[signal_bar + horizon])
        raw_return = exit_price / entry - 1.0
        metrics[f"return_{horizon}_bars_pct"] = round(
            sign * raw_return * 100.0,
            4,
        )

    max_horizon = max(horizons)
    future = frame.iloc[signal_bar + 1 : signal_bar + max_horizon + 1]
    if direction == "bullish":
        favorable = float(future["High"].max()) / entry - 1.0
        adverse = float(future["Low"].min()) / entry - 1.0
    else:
        favorable = entry / float(future["Low"].min()) - 1.0
        adverse = entry / float(future["High"].max()) - 1.0
    favorable = max(0.0, favorable)
    adverse = min(0.0, adverse)
    event, event_bar = _first_level_event(
        future,
        direction,
        target,
        invalidation,
    )
    metrics.update(
        {
            "max_favorable_excursion_pct": round(favorable * 100.0, 4),
            "max_adverse_excursion_pct": round(adverse * 100.0, 4),
            "first_level_event": event,
            "first_level_event_after_bars": event_bar,
        }
    )
    return metrics


def walk_forward_ticker(
    frame: pd.DataFrame,
    ticker: str,
    timeframe: str,
    horizons: tuple[int, ...] = (5, 10, 20),
    step: int = 3,
    min_quality: float = 70.0,
    stages: Iterable[str] = DEFAULT_STAGES,
) -> list[dict[str, Any]]:
    if timeframe not in TIMEFRAME_CONFIG:
        raise ValueError(f"Desteklenmeyen zaman dilimi: {timeframe}")
    if not horizons or min(horizons) <= 0:
        raise ValueError("İleri ölçüm barları pozitif olmalı.")
    if step <= 0:
        raise ValueError("Adım en az 1 olmalı.")

    work = _clean_source_frame(frame)
    minimum = int(TIMEFRAME_CONFIG[timeframe]["min_rows"])
    max_horizon = max(horizons)
    if len(work) < minimum + max_horizon:
        return []

    accepted_stages = set(stages)
    seen_structures: set[tuple[str, str]] = set()
    signals: list[dict[str, Any]] = []
    final_signal_bar = len(work) - max_horizon - 1

    cutoffs = list(range(minimum - 1, final_signal_bar + 1, step))
    if cutoffs and cutoffs[-1] != final_signal_bar:
        cutoffs.append(final_signal_bar)

    for signal_bar in cutoffs:
        history = work.iloc[: signal_bar + 1]
        report = analyze_formations(
            history,
            ticker=ticker,
            timeframe=timeframe,
        )
        if not report.data_ok:
            continue
        for candidate in report.patterns:
            if (
                candidate.stage not in accepted_stages
                or candidate.quality_score < min_quality
            ):
                continue
            structure_key = (candidate.pattern, candidate.start_time)
            if structure_key in seen_structures:
                continue
            seen_structures.add(structure_key)
            signal = {
                "ticker": ticker,
                "timeframe": timeframe,
                "pattern": candidate.pattern,
                "direction": candidate.direction,
                "stage": candidate.stage,
                "quality_score": candidate.quality_score,
                "signal_bar": signal_bar,
                "signal_time": pd.Timestamp(work.index[signal_bar]).isoformat(),
                "structure_start_time": candidate.start_time,
                "trigger": candidate.trigger,
                "invalidation": candidate.invalidation,
                "target": candidate.target,
                "breakout_volume_ratio": candidate.metrics.get(
                    "breakout_volume_ratio"
                ),
                "two_close_confirmation": candidate.metrics.get(
                    "two_close_confirmation"
                ),
            }
            signal.update(
                _forward_metrics(
                    work,
                    signal_bar,
                    candidate.direction,
                    horizons,
                    candidate.target,
                    candidate.invalidation,
                )
            )
            signals.append(signal)
    return signals


def _average(values: list[float]) -> Optional[float]:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return round(float(np.mean(finite)), 4) if finite else None


def _median(values: list[float]) -> Optional[float]:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return round(float(np.median(finite)), 4) if finite else None


def _summarize_group(
    rows: list[dict[str, Any]],
    horizons: tuple[int, ...],
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "signals": len(rows),
        "average_quality": _average([row["quality_score"] for row in rows]),
        "target_first": sum(row["first_level_event"] == "HEDEF" for row in rows),
        "invalidation_first": sum(
            row["first_level_event"] == "GEÇERSİZLİK" for row in rows
        ),
        "same_bar_unclear": sum(
            row["first_level_event"] == "AYNI_BAR_BELİRSİZ" for row in rows
        ),
        "neither_level": sum(row["first_level_event"] == "HİÇBİRİ" for row in rows),
        "average_max_favorable_pct": _average(
            [row["max_favorable_excursion_pct"] for row in rows]
        ),
        "average_max_adverse_pct": _average(
            [row["max_adverse_excursion_pct"] for row in rows]
        ),
    }
    for horizon in horizons:
        key = f"return_{horizon}_bars_pct"
        values = [float(row[key]) for row in rows]
        summary[f"average_{horizon}_bars_pct"] = _average(values)
        summary[f"median_{horizon}_bars_pct"] = _median(values)
        summary[f"win_rate_{horizon}_bars_pct"] = (
            round(sum(value > 0 for value in values) / len(values) * 100.0, 2)
            if values
            else None
        )
        excess_key = f"excess_return_{horizon}_bars_pct"
        excess_values = [
            float(row[excess_key])
            for row in rows
            if row.get(excess_key) is not None
        ]
        summary[f"average_excess_{horizon}_bars_pct"] = _average(excess_values)
        summary[f"median_excess_{horizon}_bars_pct"] = _median(excess_values)
        summary[f"excess_win_rate_{horizon}_bars_pct"] = (
            round(
                sum(value > 0 for value in excess_values)
                / len(excess_values)
                * 100.0,
                2,
            )
            if excess_values
            else None
        )
    return summary


def summarize_signals(
    signals: list[dict[str, Any]],
    horizons: tuple[int, ...],
) -> dict[str, Any]:
    by_pattern: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_quality: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_confirmation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for signal in signals:
        by_pattern[signal["pattern"]].append(signal)
        quality = float(signal["quality_score"])
        quality_band = (
            "90-100"
            if quality >= 90
            else "80-89"
            if quality >= 80
            else "70-79"
        )
        by_quality[quality_band].append(signal)

        volume_ratio = signal.get("breakout_volume_ratio")
        volume_confirmed = (
            volume_ratio is not None and float(volume_ratio) >= 1.30
        )
        two_close = bool(signal.get("two_close_confirmation"))
        confirmation = (
            "HACİM+İKİ_KAPANIŞ"
            if volume_confirmed and two_close
            else "İKİ_KAPANIŞ"
            if two_close
            else "HACİM"
            if volume_confirmed
            else "DİĞER"
        )
        by_confirmation[confirmation].append(signal)
    summary = {
        "overall": _summarize_group(signals, horizons),
        "by_pattern": {
            pattern: _summarize_group(rows, horizons)
            for pattern, rows in sorted(by_pattern.items())
        },
        "by_quality_band": {
            band: _summarize_group(rows, horizons)
            for band, rows in sorted(by_quality.items())
        },
        "by_confirmation": {
            confirmation: _summarize_group(rows, horizons)
            for confirmation, rows in sorted(by_confirmation.items())
        },
    }
    if signals:
        ordered_times = sorted(
            pd.Timestamp(signal["signal_time"])
            for signal in signals
        )
        split_time = ordered_times[len(ordered_times) // 2]
        early = [
            signal
            for signal in signals
            if pd.Timestamp(signal["signal_time"]) < split_time
        ]
        late = [
            signal
            for signal in signals
            if pd.Timestamp(signal["signal_time"]) >= split_time
        ]
        summary["time_stability"] = {
            "split_time": split_time.isoformat(),
            "early": {
                "overall": _summarize_group(early, horizons),
                "by_pattern": {
                    pattern: _summarize_group(
                        [row for row in early if row["pattern"] == pattern],
                        horizons,
                    )
                    for pattern in sorted(by_pattern)
                    if any(row["pattern"] == pattern for row in early)
                },
            },
            "late": {
                "overall": _summarize_group(late, horizons),
                "by_pattern": {
                    pattern: _summarize_group(
                        [row for row in late if row["pattern"] == pattern],
                        horizons,
                    )
                    for pattern in sorted(by_pattern)
                    if any(row["pattern"] == pattern for row in late)
                },
            },
        }
    return summary


def _backtest_file(
    job: tuple[
        Path,
        str,
        tuple[int, ...],
        int,
        float,
        tuple[str, ...],
    ],
) -> dict[str, Any]:
    path, timeframe, horizons, step, min_quality, stages = job
    symbol = _ticker_from_path(path, timeframe)
    if not path.exists():
        return {
            "ticker": symbol,
            "signals": [],
            "failure": f"Dosya yok: {path.name}",
        }
    try:
        signals = walk_forward_ticker(
            pd.read_parquet(path),
            symbol,
            timeframe,
            horizons=horizons,
            step=step,
            min_quality=min_quality,
            stages=stages,
        )
        return {"ticker": symbol, "signals": signals, "failure": None}
    except Exception as exc:
        return {
            "ticker": symbol,
            "signals": [],
            "failure": f"{type(exc).__name__}: {exc}",
        }


def _plain_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    return timestamp


def attach_benchmark_returns(
    root: Path,
    signals: list[dict[str, Any]],
    timeframe: str,
    horizons: tuple[int, ...],
    benchmark_symbol: str = "XU100",
) -> dict[str, Any]:
    folder = root / ("veriler" if timeframe == "1d" else "veriler_4s")
    suffix = "_1d.parquet" if timeframe == "1d" else "_4h.parquet"
    path = folder / f"{benchmark_symbol}.IS{suffix}"
    if not path.exists():
        return {
            "symbol": benchmark_symbol,
            "available": False,
            "matched_signals": 0,
        }

    benchmark = _clean_source_frame(pd.read_parquet(path))
    benchmark = benchmark.copy()
    benchmark.index = pd.DatetimeIndex(
        [_plain_timestamp(value) for value in benchmark.index]
    )
    close = benchmark["Close"].to_numpy(dtype=float)
    index = benchmark.index
    matched = 0
    for signal in signals:
        signal_time = _plain_timestamp(signal["signal_time"])
        position = int(index.searchsorted(signal_time, side="right") - 1)
        if position < 0:
            continue
        gap = signal_time - index[position]
        max_gap = pd.Timedelta(days=7 if timeframe == "1d" else 3)
        if gap < pd.Timedelta(0) or gap > max_gap:
            continue
        entry = float(close[position])
        direction_sign = 1.0 if signal["direction"] == "bullish" else -1.0
        usable = True
        for horizon in horizons:
            key = f"benchmark_return_{horizon}_bars_pct"
            excess_key = f"excess_return_{horizon}_bars_pct"
            if position + horizon >= len(close):
                signal[key] = None
                signal[excess_key] = None
                usable = False
                continue
            benchmark_raw = float(close[position + horizon] / entry - 1.0)
            benchmark_directional = direction_sign * benchmark_raw * 100.0
            signal[key] = round(benchmark_directional, 4)
            signal[excess_key] = round(
                float(signal[f"return_{horizon}_bars_pct"])
                - benchmark_directional,
                4,
            )
        if usable:
            matched += 1
    return {
        "symbol": benchmark_symbol,
        "available": True,
        "matched_signals": matched,
        "total_signals": len(signals),
    }


def run_backtest(
    root: Path,
    timeframe: str,
    horizons: tuple[int, ...],
    step: int,
    min_quality: float,
    stages: tuple[str, ...],
    ticker: Optional[str] = None,
    limit: Optional[int] = None,
    workers: int = 1,
) -> dict[str, Any]:
    folder = root / ("veriler" if timeframe == "1d" else "veriler_4s")
    suffix = "_1d.parquet" if timeframe == "1d" else "_4h.parquet"
    if ticker:
        symbol = ticker.upper().replace(".IS", "")
        files = [folder / f"{symbol}.IS{suffix}"]
    else:
        files = sorted(folder.glob(f"*{suffix}"))
        if limit is not None:
            files = files[: max(0, int(limit))]

    workers = max(1, int(workers))
    jobs = [
        (path, timeframe, horizons, step, min_quality, stages)
        for path in files
    ]
    if workers == 1:
        results = [_backtest_file(job) for job in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_backtest_file, jobs))

    signals: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for result in results:
        signals.extend(result["signals"])
        if result["failure"]:
            failures.append(
                {"ticker": result["ticker"], "reason": result["failure"]}
            )
    benchmark = attach_benchmark_returns(
        root,
        signals,
        timeframe,
        horizons,
    )

    return {
        "backtest_version": BACKTEST_VERSION,
        "engine_version": ENGINE_VERSION,
        "timeframe": timeframe,
        "files_scanned": len(files),
        "workers": workers,
        "step_bars": step,
        "forward_horizons_bars": list(horizons),
        "minimum_quality": min_quality,
        "accepted_stages": list(stages),
        "lookahead_guard": (
            "Motor her kesitte yalnız sinyal tarihine kadarki mumları gördü; "
            "gelecek mumlar yalnız sonuç ölçümünde kullanıldı."
        ),
        "benchmark": benchmark,
        "summary": summarize_signals(signals, horizons),
        "failures": failures,
        "signals": signals,
    }


def _parse_horizons(value: str) -> tuple[int, ...]:
    horizons = tuple(sorted({int(item.strip()) for item in value.split(",")}))
    if not horizons or min(horizons) <= 0:
        raise argparse.ArgumentTypeError("Örnek kullanım: 5,10,20")
    return horizons


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Formasyon V2 geleceği görmeyen geçmiş sonuç ölçümü"
    )
    parser.add_argument("--timeframe", choices=("1d", "4h"), default="1d")
    parser.add_argument("--ticker", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--step", type=int, default=3)
    parser.add_argument("--horizons", type=_parse_horizons, default=(5, 10, 20))
    parser.add_argument("--min-quality", type=float, default=70.0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--resummarize",
        type=Path,
        default=None,
        help="Mevcut JSON sinyallerini yeniden taramadan güncel özetle işle",
    )
    parser.add_argument(
        "--all-break-stages",
        action="store_true",
        help="Doğrulanmış kırılıma ek olarak aday kırılım ve yeniden testi de ölç",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.resummarize:
        payload = json.loads(args.resummarize.read_text(encoding="utf-8"))
        horizons = tuple(int(value) for value in payload["forward_horizons_bars"])
        payload["benchmark"] = attach_benchmark_returns(
            Path(__file__).resolve().parent,
            payload["signals"],
            payload["timeframe"],
            horizons,
        )
        payload["backtest_version"] = BACKTEST_VERSION
        payload["summary"] = summarize_signals(payload["signals"], horizons)
    else:
        stages = ALL_BREAK_STAGES if args.all_break_stages else DEFAULT_STAGES
        payload = run_backtest(
            Path(__file__).resolve().parent,
            timeframe=args.timeframe,
            horizons=args.horizons,
            step=args.step,
            min_quality=args.min_quality,
            stages=stages,
            ticker=args.ticker,
            limit=args.limit,
            workers=args.workers,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
