"""Boyun/çizgi çekirdeği için geleceği görmeyen parquet değerlendirmesi.

Bu script işlem tavsiyesi üretmez. Bir çizgi kırılımının ileri barlarda çizgi
üstünde/altında kalıp kalmadığını ve fiyat davranışını ölçer. Her kesitte motor
yalnız o güne kadar olan mumları görür; gelecek yalnız sonuç ölçümündedir.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from neckline_core import ENGINE_VERSION, StructuralLine, analyze_structural_lines


BACKTEST_VERSION = "0.1.0-research"


def _clean(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
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


def _line_at(line: StructuralLine, bar: int) -> float:
    return float(line.slope_per_bar * bar + (line.level_now - line.slope_per_bar * line.end_bar))


def _signal_key(ticker: str, line: StructuralLine) -> tuple[str, str, str, str, int]:
    return (
        ticker,
        line.role,
        line.orientation,
        line.start_time[:10],
        int(round(line.level_now / max(line.touch_tolerance_pct / 100 * line.level_now, 0.01))),
    )


def _forward_metrics(frame: pd.DataFrame, signal_bar: int, line: StructuralLine, horizons: tuple[int, ...]) -> dict[str, Any]:
    entry = float(frame["Close"].iloc[signal_bar])
    bearish = line.role == "DESTEK"
    sign = -1.0 if bearish else 1.0
    metrics: dict[str, Any] = {"entry_price": round(entry, 6)}
    max_horizon = max(horizons)
    future = frame.iloc[signal_bar + 1 : signal_bar + max_horizon + 1]
    for horizon in horizons:
        bar = signal_bar + horizon
        if bar >= len(frame):
            continue
        close = float(frame["Close"].iloc[bar])
        level = _line_at(line, bar)
        raw_return = close / entry - 1.0
        hold = close >= level * (1 - line.break_buffer_pct / 100) if not bearish else close <= level * (1 + line.break_buffer_pct / 100)
        metrics[f"return_{horizon}_bars_pct"] = round(sign * raw_return * 100, 4)
        metrics[f"line_hold_{horizon}_bars"] = bool(hold)
    if not future.empty:
        if bearish:
            favorable = entry / float(future["Low"].min()) - 1.0
            adverse = entry / float(future["High"].max()) - 1.0
        else:
            favorable = float(future["High"].max()) / entry - 1.0
            adverse = float(future["Low"].min()) / entry - 1.0
        metrics["max_favorable_pct"] = round(max(0.0, favorable) * 100, 4)
        metrics["max_adverse_pct"] = round(min(0.0, adverse) * 100, 4)
    return metrics


def walk_forward_ticker(
    frame: pd.DataFrame,
    ticker: str,
    horizons: tuple[int, ...],
    step: int,
    min_score: float,
    roles: Iterable[str],
    orientations: Iterable[str],
) -> list[dict[str, Any]]:
    work = _clean(frame)
    max_horizon = max(horizons)
    if len(work) < 130 + max_horizon:
        return []
    accepted_roles = set(roles)
    accepted_orientations = set(orientations)
    signals: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, int]] = set()
    final_bar = len(work) - max_horizon - 1
    for signal_bar in range(119, final_bar + 1, step):
        history = work.iloc[: signal_bar + 1]
        report = analyze_structural_lines(history, max_results=24)
        if not report.data_ok:
            continue
        # Aynı gün aynı yön için yalnız en güçlü çizgi seçilir. Böylece tek
        # boynun küçük matematik varyasyonları ayrı sinyal sayılmaz.
        selected_groups: set[tuple[str, str]] = set()
        for line in report.lines:
            age = int(line.metrics.get("breakout_age_bars", 999))
            group = (line.role, line.orientation)
            if (
                line.role not in accepted_roles
                or line.orientation not in accepted_orientations
                or line.score < min_score
                or line.state != "BREAKOUT_CONFIRMED"
                or age > 2
                or group in selected_groups
            ):
                continue
            selected_groups.add(group)
            key = _signal_key(ticker, line)
            if key in seen:
                continue
            seen.add(key)
            signal: dict[str, Any] = {
                "ticker": ticker,
                "signal_time": pd.Timestamp(work.index[signal_bar]).isoformat(),
                "role": line.role,
                "orientation": line.orientation,
                "state": line.state,
                "score": line.score,
                "touch_count": line.metrics.get("touch_count"),
                "span_bars": line.metrics.get("span_bars"),
                "line_level": line.level_now,
                "breakout_age_bars": age,
                "signal_bar": signal_bar,
            }
            signal.update(_forward_metrics(work, signal_bar, line, horizons))
            signals.append(signal)
    return signals


def _process_file(
    job: tuple[str, tuple[int, ...], int, float, tuple[str, ...], tuple[str, ...]],
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    """Bir parquet'i bağımsız işler; çoklu işlemde güvenli taşınabilir iş birimi."""
    path_text, horizons, step, min_score, roles, orientations = job
    path = Path(path_text)
    ticker_name = path.name.replace(".IS_1d.parquet", "")
    try:
        return (
            walk_forward_ticker(
                pd.read_parquet(path),
                ticker_name,
                horizons,
                step,
                min_score,
                roles,
                orientations,
            ),
            None,
        )
    except Exception as exc:
        return [], {"ticker": ticker_name, "reason": f"{type(exc).__name__}: {exc}"}


def _average(values: list[float]) -> float | None:
    values = [float(value) for value in values if value is not None and np.isfinite(float(value))]
    return round(float(np.mean(values)), 4) if values else None


def _summary(rows: list[dict[str, Any]], horizons: tuple[int, ...]) -> dict[str, Any]:
    output: dict[str, Any] = {"signals": len(rows), "average_score": _average([row.get("score") for row in rows])}
    for horizon in horizons:
        returns = [row.get(f"return_{horizon}_bars_pct") for row in rows]
        holds = [row.get(f"line_hold_{horizon}_bars") for row in rows if row.get(f"line_hold_{horizon}_bars") is not None]
        usable_returns = [float(value) for value in returns if value is not None]
        output[f"average_return_{horizon}_bars_pct"] = _average(usable_returns)
        output[f"win_rate_{horizon}_bars_pct"] = round(sum(value > 0 for value in usable_returns) / len(usable_returns) * 100, 2) if usable_returns else None
        output[f"line_hold_rate_{horizon}_bars_pct"] = round(sum(bool(value) for value in holds) / len(holds) * 100, 2) if holds else None
    output["average_max_favorable_pct"] = _average([row.get("max_favorable_pct") for row in rows])
    output["average_max_adverse_pct"] = _average([row.get("max_adverse_pct") for row in rows])
    return output


def run_backtest(
    root: Path,
    horizons: tuple[int, ...],
    step: int,
    min_score: float,
    roles: tuple[str, ...],
    orientations: tuple[str, ...],
    ticker: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    workers: int = 1,
) -> dict[str, Any]:
    folder = root / "veriler"
    if ticker:
        symbol = ticker.upper().replace(".IS", "")
        files = [folder / f"{symbol}.IS_1d.parquet"]
    else:
        files = sorted(path for path in folder.glob("*.IS_1d.parquet") if not path.name.startswith("XU"))
        files = files[max(0, int(offset)) :]
        if limit is not None:
            files = files[: max(0, int(limit))]
    signals: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    jobs = [(str(path), horizons, step, min_score, roles, orientations) for path in files]
    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            outcomes = executor.map(_process_file, jobs)
            for rows, failure in outcomes:
                signals.extend(rows)
                if failure:
                    failures.append(failure)
    else:
        for job in jobs:
            rows, failure = _process_file(job)
            signals.extend(rows)
            if failure:
                failures.append(failure)
    by_role: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_orientation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for signal in signals:
        by_role[signal["role"]].append(signal)
        by_orientation[signal["orientation"]].append(signal)
    return {
        "backtest_version": BACKTEST_VERSION,
        "engine_version": ENGINE_VERSION,
        "files_scanned": len(files),
        "file_offset": max(0, int(offset)),
        "workers": max(1, int(workers)),
        "step_bars": step,
        "horizons": list(horizons),
        "minimum_score": min_score,
        "roles": list(roles),
        "orientations": list(orientations),
        "lookahead_guard": "Her kesitte çizgi motoru yalnız sinyal gününe kadar olan mumları gördü.",
        "summary": _summary(signals, horizons),
        "by_role": {name: _summary(rows, horizons) for name, rows in sorted(by_role.items())},
        "by_orientation": {name: _summary(rows, horizons) for name, rows in sorted(by_orientation.items())},
        "failures": failures,
        "signals": signals,
    }


def _parse_horizons(value: str) -> tuple[int, ...]:
    values = tuple(sorted({int(item.strip()) for item in value.split(",")}))
    if not values or min(values) <= 0:
        raise argparse.ArgumentTypeError("Örnek: 5,10,20")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Boyun çizgisi kırılımını parquet üzerinde ölçer")
    parser.add_argument("--ticker", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0, help="Parquet evreninde başlanacak sıralı dosya numarası.")
    parser.add_argument("--step", type=int, default=3)
    parser.add_argument("--workers", type=int, default=1, help="Sadece test hızını artırır; çizgi kuralını değiştirmez.")
    parser.add_argument("--horizons", type=_parse_horizons, default=(5, 10, 20))
    parser.add_argument("--min-score", type=float, default=70.0)
    parser.add_argument("--roles", default="DIRENC,DESTEK")
    parser.add_argument(
        "--orientations",
        default="YATAY",
        help="İlk boyun/range ölçümü için YATAY; kama/üçgen aşamasında diğerleri ayrıca açılır.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_backtest(
        Path(__file__).resolve().parent,
        args.horizons,
        args.step,
        args.min_score,
        tuple(item.strip().upper() for item in args.roles.split(",") if item.strip()),
        tuple(item.strip().upper() for item in args.orientations.split(",") if item.strip()),
        ticker=args.ticker,
        limit=args.limit,
        offset=max(0, args.offset),
        workers=max(1, args.workers),
    )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
