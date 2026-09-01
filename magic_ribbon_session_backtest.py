# -*- coding: utf-8 -*-
"""Magic Ribbon BIST seans-mumu için kısa tarihçeli, look-ahead'siz ölçüm.

TradingView 5 dakikalık ücretsiz akışı yaklaşık son 55 tam seansı sakladığı için
çıktı uzun dönem hüküm değil, yalnız canlı hattın ilk ölçüm kaydıdır. Her sinyal
kapanışta görülür; getiriler sonraki seans mumunun açılışından hesaplanır.

1 Eyl 2026 — DELİK FARKINDALIĞI. Bozuk gün reddedilince geriye kalan mumlar
hesapta yan yana sayılıyordu: araya bir hafta girmiş iki mum arasındaki eğim
gerçek eğim değil, "10 mum sonra" da delikli hissede 5 iş günü değil. Ölçüm
artık iki kova üretiyor: HAM (eski davranış) ve KESİNTİSİZ (hem sinyalin eğimi
hem tutma penceresi deliksiz). İkisinin farkı, deliğin hükmü ne kadar
oynattığını gösterir.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from magic_ribbon_core import add_ribbon_columns, load_bist100_symbols
from magic_ribbon_session_data import (
    SESSION_DIR,
    contiguous_prev_mask,
    session_block_ids,
    session_gap_report,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_COST = 0.002
HORIZONS = (10, 20, 40)  # yaklaşık T+5 / T+10 / T+20 işlem günü (günde iki seans mumu)


def _read(symbol: str) -> pd.DataFrame | None:
    path = SESSION_DIR / f"{symbol}.IS_session.parquet"
    try:
        frame = pd.read_parquet(path)
    except (OSError, ValueError, TypeError):
        return None
    required = ["Open", "High", "Low", "Close"]
    if frame.empty or any(column not in frame.columns for column in required):
        return None
    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=required)
    frame = frame[(frame["Open"] > 0) & (frame["High"] > 0) & (frame["Low"] > 0) & (frame["Close"] > 0)]
    if len(frame) < max(HORIZONS) + 20:
        return None
    return add_ribbon_columns(frame)


def _return(entry: float, exit_price: float, cost: float) -> float:
    half = cost / 2.0
    return (((exit_price / entry) * ((1.0 - half) / (1.0 + half))) - 1.0) * 100.0


def _metrics(values: list[float]) -> dict[str, float | int | None]:
    series = pd.Series(values, dtype=float).dropna()
    if series.empty:
        return {"n": 0, "win_rate_pct": None, "avg_net_pct": None, "median_net_pct": None, "profit_factor": None}
    wins = series[series > 0]
    losses = series[series < 0]
    profit_factor = float(wins.sum() / abs(losses.sum())) if not losses.empty else None
    return {
        "n": int(len(series)),
        "win_rate_pct": round(float((series > 0).mean() * 100.0), 2),
        "avg_net_pct": round(float(series.mean()), 3),
        "median_net_pct": round(float(series.median()), 3),
        "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
    }


def _bucket_block(signal: list[float], baseline: list[float]) -> dict[str, object]:
    """Bir kova için sinyal/taban karnesi ve aradaki alfa."""
    signal_metrics = _metrics(signal)
    baseline_metrics = _metrics(baseline)
    alpha = None
    if signal_metrics["avg_net_pct"] is not None and baseline_metrics["avg_net_pct"] is not None:
        alpha = round(float(signal_metrics["avg_net_pct"] - baseline_metrics["avg_net_pct"]), 3)
    return {"signal": signal_metrics, "baseline": baseline_metrics, "avg_net_alpha_pct": alpha}


def run(cost: float) -> dict[str, object]:
    raw_signal = {horizon: [] for horizon in HORIZONS}
    raw_baseline = {horizon: [] for horizon in HORIZONS}
    clean_signal = {horizon: [] for horizon in HORIZONS}
    clean_baseline = {horizon: [] for horizon in HORIZONS}
    used: list[str] = []
    ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    gap_rows: list[dict[str, object]] = []

    for symbol in sorted(load_bist100_symbols()):
        frame = _read(symbol)
        if frame is None:
            continue
        used.append(symbol)
        ranges.append((frame.index.min(), frame.index.max()))

        gap = session_gap_report(frame)
        gap_rows.append({
            "symbol": symbol,
            "kapsama": gap["kapsama"],
            "eksik_gun": gap["eksik_gun"],
            "beklenen_gun": gap["beklenen_gun"],
            "en_uzun_bosluk": gap["en_uzun_bosluk"],
        })

        mask = contiguous_prev_mask(frame).to_numpy(dtype=bool)
        blocks = session_block_ids(frame).to_numpy()
        valid = frame[["fast_line", "slow_line"]].notna().all(axis=1).to_numpy(dtype=bool)
        triggers = frame["up_trigger"].to_numpy(dtype=bool)

        for index in range(len(frame) - 1):
            if not valid[index]:
                continue
            entry_i = index + 1
            entry = float(frame["Open"].iloc[entry_i])
            if not np.isfinite(entry) or entry <= 0:
                continue
            # Sinyalin eğimi ancak bir önceki mum GERÇEKTEN komşuysa gerçektir.
            slope_real = bool(mask[index])
            for horizon in HORIZONS:
                exit_i = entry_i + horizon - 1
                if exit_i >= len(frame):
                    continue
                value = _return(entry, float(frame["Close"].iloc[exit_i]), cost)
                # Tutma penceresi tek blokta kalmalı: sinyalden çıkışa delik girmemeli.
                temiz = slope_real and blocks[index] == blocks[exit_i]
                raw_baseline[horizon].append(value)
                if temiz:
                    clean_baseline[horizon].append(value)
                if triggers[index]:
                    raw_signal[horizon].append(value)
                    if temiz:
                        clean_signal[horizon].append(value)

    horizons = {}
    for horizon in HORIZONS:
        horizons[str(horizon)] = {
            "session_bars": horizon,
            "approx_trading_days": horizon / 2.0,
            "ham": _bucket_block(raw_signal[horizon], raw_baseline[horizon]),
            "kesintisiz": _bucket_block(clean_signal[horizon], clean_baseline[horizon]),
        }

    kapsamalar = pd.Series([row["kapsama"] for row in gap_rows], dtype=float)
    return {
        "engine": "magic-ribbon-bist-session-v1",
        "scope": "BIST100",
        "cost_round_trip": cost,
        "symbols_used": len(used),
        "symbols": used,
        "first_bar": str(min(start for start, _ in ranges)) if ranges else None,
        "last_bar": str(max(end for _, end in ranges)) if ranges else None,
        "delik_ozeti": {
            "kapsama_min": round(float(kapsamalar.min()), 4) if not kapsamalar.empty else None,
            "kapsama_medyan": round(float(kapsamalar.median()), 4) if not kapsamalar.empty else None,
            "kapsama_max": round(float(kapsamalar.max()), 4) if not kapsamalar.empty else None,
            "delikli_sembol": int(sum(1 for row in gap_rows if row["eksik_gun"] > 0)),
            "en_kotu": sorted(gap_rows, key=lambda row: row["kapsama"])[:10],
        },
        "warning": "TradingView ücretsiz 5 dakika akışı yaklaşık 55 seans tutar; bu çıktı uzun dönem kanıtı değildir.",
        "horizons": horizons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Magic Ribbon BIST seans-mumu kısa tarihçe ölçümü")
    parser.add_argument("--maliyet", type=float, default=DEFAULT_COST)
    parser.add_argument("--output", default="magic_ribbon_session_backtest.json")
    args = parser.parse_args()
    report = run(float(args.maliyet))
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["symbols_used"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
