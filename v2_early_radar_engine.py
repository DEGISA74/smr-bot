#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V2 Erken Radar: tamamlanmamış günün saatlik fotoğrafını ayrı olarak puanlar.

Resmî V2 motoruna, modeline, durum dosyasına veya Patron2 siciline yazmaz.
Mevcut V2 modelini yalnız gölge/erken puanlama için okur.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


DEFAULT_TOP_N = 10
DEFAULT_MIN_BARS = 8
TIMEZONE = "Europe/Istanbul"
INDEX_SYMBOLS = {"XU100", "XU030", "XU050", "XBANK", "XUSIN", "XUMAL"}


def _load_v2_engine(project_root: Path):
    path = project_root / "yuksek_getiri_engine_v2.py"
    spec = importlib.util.spec_from_file_location("official_yuksek_getiri_engine_v2", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Resmî V2 motoru yüklenemedi: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _hourly_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    index = pd.to_datetime(frame.index)
    if getattr(index, "tz", None) is None:
        index = index.tz_localize(TIMEZONE)
    else:
        index = index.tz_convert(TIMEZONE)
    frame = frame.copy()
    frame.index = index
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    columns = ["Open", "High", "Low", "Close", "Volume"]
    return frame.loc[:, columns].apply(pd.to_numeric, errors="coerce").dropna(how="all")


def _daily_frame(path: Path, v2) -> pd.DataFrame:
    return v2._numeric_ohlcv(pd.read_parquet(path))


def _aggregate_day(hourly: pd.DataFrame, signal_date: pd.Timestamp) -> tuple[pd.Series, int, pd.Timestamp]:
    date = pd.Timestamp(signal_date).date()
    day = hourly[hourly.index.date == date].copy()
    if day.empty:
        raise ValueError("İstenen tarihte saatlik bar yok.")
    values = {
        "Open": float(day["Open"].iloc[0]),
        "High": float(day["High"].max()),
        "Low": float(day["Low"].min()),
        "Close": float(day["Close"].iloc[-1]),
        "Volume": float(day["Volume"].sum()),
    }
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError("Saatlik barlardan geçerli günlük fotoğraf üretilemedi.")
    if min(values["Open"], values["High"], values["Low"], values["Close"]) <= 0:
        raise ValueError("Saatlik fiyatlardan biri pozitif değil.")
    return pd.Series(values, name=pd.Timestamp(signal_date).normalize()), int(len(day)), pd.Timestamp(day.index[-1])


def _append_partial_day(daily: pd.DataFrame, partial: pd.Series, signal_date: pd.Timestamp) -> pd.DataFrame:
    signal_date = pd.Timestamp(signal_date).normalize()
    clean = daily[daily.index < signal_date].copy()
    clean.loc[signal_date, ["Open", "High", "Low", "Close", "Volume"]] = partial[
        ["Open", "High", "Low", "Close", "Volume"]
    ].to_numpy(dtype=float)
    return clean.sort_index()


def _iter_hourly_symbols(hourly_dir: Path) -> Iterable[tuple[str, Path]]:
    for path in sorted(hourly_dir.glob("*.IS_1h.parquet")):
        symbol = path.name.replace(".IS_1h.parquet", "")
        if symbol not in INDEX_SYMBOLS:
            yield symbol, path


def _reason_labels(latest: pd.DataFrame, x: np.ndarray, beta: np.ndarray, v2) -> list[str]:
    standardized = pd.DataFrame(x, columns=v2.FEATURES, index=latest.index)
    contribution = standardized.mul(beta[1:], axis=1)
    reasons: list[str] = []
    for idx in latest.index:
        family_value: dict[str, float] = {}
        for feature in v2.FEATURES:
            family = v2.FEATURE_FAMILY[feature]
            family_value[family] = family_value.get(family, 0.0) + float(contribution.at[idx, feature])
        positive = sorted(family_value.items(), key=lambda item: item[1], reverse=True)
        reasons.append(" + ".join(name for name, value in positive[:3] if value > 0) or "taban olasılık")
    return reasons


def scan_early_radar(
    project_root: Path,
    *,
    top_n: int = DEFAULT_TOP_N,
    min_bars: int = DEFAULT_MIN_BARS,
    as_of: str | None = None,
) -> tuple[pd.DataFrame, dict]:
    project_root = Path(project_root).resolve()
    daily_dir = project_root / "veriler"
    hourly_dir = project_root / "veriler_saatlik"
    v2 = _load_v2_engine(project_root)
    model = v2.load_model(project_root / "yuksek_getiri_v2_model.json")

    hourly_market = _hourly_frame(hourly_dir / "XU100.IS_1h.parquet")
    if as_of:
        signal_date = pd.Timestamp(as_of).normalize()
    else:
        signal_date = pd.Timestamp(hourly_market.index[-1].date())
    market_partial, market_bars, market_timestamp = _aggregate_day(hourly_market, signal_date)
    if market_bars < min_bars:
        raise RuntimeError(f"XU100 için yalnız {market_bars} saatlik bar var; en az {min_bars} gerekli.")
    market_daily = _daily_frame(daily_dir / "XU100.IS_1d.parquet", v2)
    market_combined = _append_partial_day(market_daily, market_partial, signal_date)
    market_context = v2._market_context(market_combined)

    rows: list[pd.DataFrame] = []
    scanned = fresh = skipped_bars = skipped_daily = skipped_quality = 0
    latest_timestamps: list[pd.Timestamp] = []
    minimum_turnover = float(model["universe"]["min_median_turnover_20_tl"])
    for symbol, hourly_path in _iter_hourly_symbols(hourly_dir):
        scanned += 1
        daily_path = daily_dir / f"{symbol}.IS_1d.parquet"
        if not daily_path.exists():
            skipped_daily += 1
            continue
        try:
            hourly = _hourly_frame(hourly_path)
            partial, bar_count, latest_timestamp = _aggregate_day(hourly, signal_date)
            if bar_count < min_bars:
                skipped_bars += 1
                continue
            fresh += 1
            daily = _daily_frame(daily_path, v2)
            if len(daily[daily.index < signal_date]) < v2.MIN_HISTORY_BARS:
                skipped_daily += 1
                continue
            combined = _append_partial_day(daily, partial, signal_date)
            feature_frame = v2.build_feature_frame(combined, market_context)
            row = feature_frame.loc[[signal_date]].copy()
            eligible = (
                float(row["history_bars"].iloc[0]) >= v2.MIN_HISTORY_BARS
                and float(row["turnover_20"].iloc[0]) >= minimum_turnover
                and bool(row["corporate_action_clean"].iloc[0])
            )
            if not eligible:
                skipped_quality += 1
                continue
            row["ticker"] = symbol
            row["hourly_bars"] = bar_count
            row["snapshot_timestamp"] = latest_timestamp.isoformat()
            rows.append(row.reset_index(drop=True))
            latest_timestamps.append(latest_timestamp)
        except Exception:
            skipped_quality += 1

    if not rows:
        raise RuntimeError("V2 Erken Radar için uygun aday havuzu üretilemedi.")
    latest = pd.concat(rows, ignore_index=True)
    prep = v2.Preprocessor.from_dict(model["preprocessor"])
    beta = np.asarray(
        [model["intercept"]] + [model["coefficients"][name] for name in v2.FEATURES],
        dtype=float,
    )
    x = prep.transform(latest)
    latest["probability"] = v2.predict_probability(x, beta)
    latest["neden"] = _reason_labels(latest, x, beta, v2)
    latest["olasilik_pct"] = latest["probability"] * 100.0
    out = latest.nlargest(top_n, "probability").copy()
    columns = [
        "ticker",
        "olasilik_pct",
        "neden",
        "close",
        "ret_5g",
        "ret_10g",
        "rsi_14",
        "pos_52h",
        "volume_ratio_1_20",
        "turnover_20",
        "b11_teyidi",
        "hourly_bars",
        "snapshot_timestamp",
    ]
    metadata = {
        "motor": "V2 Erken Radar",
        "signal_date": str(signal_date.date()),
        "market_snapshot_timestamp": market_timestamp.isoformat(),
        "market_hourly_bars": market_bars,
        "latest_candidate_timestamp": max(latest_timestamps).isoformat() if latest_timestamps else None,
        "scanned_hourly_files": scanned,
        "fresh_hourly_symbols": fresh,
        "eligible": int(len(latest)),
        "skipped_bars": skipped_bars,
        "skipped_daily": skipped_daily,
        "skipped_quality": skipped_quality,
        "top_n": int(len(out)),
        "model_version": str(model.get("model_version", "")),
        "official_v2_untouched": True,
        "partial_day_shadow_model": True,
    }
    return out.loc[:, columns].reset_index(drop=True), metadata


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="V2 Erken Radar saatlik gölge taraması")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--min-bars", type=int, default=DEFAULT_MIN_BARS)
    parser.add_argument("--as-of")
    parser.add_argument("--output")
    args = parser.parse_args()
    candidates, metadata = scan_early_radar(
        Path(args.project_root), top_n=args.top, min_bars=args.min_bars, as_of=args.as_of
    )
    payload = {"metadata": metadata, "candidates": candidates.to_dict("records")}
    text = json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
