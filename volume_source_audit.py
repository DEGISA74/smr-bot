#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only comparison of TradingView/borsapy daily volume and BIST parquet.

The comparison deliberately reads the parent of the current active version so
that the 29 Aug borsapy fallback cannot validate its own newly written rows.
It prints JSON only and never writes, promotes, or changes a data version.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd


LIQUID = [
    "AKBNK", "ASELS", "BIMAS", "EREGL", "FROTO", "GARAN", "HEKTS",
    "KCHOL", "KOZAA", "KOZAL", "MGROS", "ULKER", "PETKM", "PGSUS", "SAHOL", "SASA",
    "SISE", "TAVHL", "TCELL", "THYAO", "TOASO", "TUPRS",
]
INDEX_PREFIXES = ("XU", "XB", "XT", "XY", "XK", "XG", "XI", "XUS")


def _date_index(frame: pd.DataFrame) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="coerce"))
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def _json_number(value):
    if value is None or not np.isfinite(float(value)):
        return None
    return float(value)


def _fetch_borsapy(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    from borsapy._providers.tradingview import get_tradingview_provider

    frame = get_tradingview_provider().get_history(
        symbol=symbol.replace(".IS", ""), interval="1d", start=start, end=end
    )
    if frame is None or frame.empty or "Volume" not in frame.columns:
        return pd.DataFrame()
    frame = frame.copy()
    frame.index = _date_index(frame)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame["Volume"] = pd.to_numeric(frame["Volume"], errors="coerce")
    return frame


def _fetch_isyatirim(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Fetch an independent reference without using the cached result."""
    from isyatirim_gateway import fetch_once

    frame = fetch_once(
        symbol, period_days=max(190, (end - start).days),
        start_date=start.strftime("%Y-%m-%d"), end_date=end.strftime("%Y-%m-%d"),
        priority="audit", max_wait=120,
    )
    if frame is None or frame.empty or "Volume" not in frame.columns:
        return pd.DataFrame()
    frame = frame.copy()
    frame.index = _date_index(frame)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame["Volume"] = pd.to_numeric(frame["Volume"], errors="coerce")
    return frame


def _volume_pair_summary(left, right, days: int):
    """Compact comparison used for the independent İş Yatırım triangulation."""
    if isinstance(left, pd.DataFrame):
        left = left["Volume"]
    if isinstance(right, pd.DataFrame):
        right = right["Volume"]
    common = sorted(set(left.index).intersection(right.index))[-days:]
    if not common:
        return {"common_dates": 0, "valid": 0}
    l = pd.to_numeric(left.loc[common], errors="coerce")
    r = pd.to_numeric(right.loc[common], errors="coerce")
    valid = l.notna() & r.notna() & (l > 0) & (r > 0)
    l = l[valid].astype(float)
    r = r[valid].astype(float)
    if not len(l):
        return {"common_dates": int(len(common)), "valid": 0}
    pct = (r / l - 1.0).abs() * 100.0
    worst_idx = pct.sort_values(ascending=False).head(5).index
    return {
        "common_dates": int(len(common)),
        "valid": int(len(l)),
        "within_1pct": int((pct <= 1.0).sum()),
        "within_5pct": int((pct <= 5.0).sum()),
        "within_10pct": int((pct <= 10.0).sum()),
        "median_abs_diff_pct": _json_number(pct.median()),
        "p90_abs_diff_pct": _json_number(pct.quantile(0.90)),
        "max_abs_diff_pct": _json_number(pct.max()),
        "median_ratio_right_over_left": _json_number((r / l).median()),
        "total_ratio_right_over_left": _json_number(r.sum() / l.sum()),
        "pearson_corr": _json_number(l.corr(r)),
        "spearman_corr": _json_number(l.rank().corr(r.rank())),
        "worst": [
            {
                "date": ts.strftime("%Y-%m-%d"),
                "left": float(l.loc[ts]),
                "right": float(r.loc[ts]),
                "abs_diff_pct": float(pct.loc[ts]),
            }
            for ts in worst_idx
        ],
    }


def _compare(symbol: str, version_id: str, source: str, days: int):
    from bist_data_store import read_active

    parquet = read_active(symbol, version_id=version_id)
    if parquet is None or parquet.empty or "Volume" not in parquet.columns:
        return {"symbol": symbol, "status": "parquet_missing"}
    parquet = parquet.copy()
    parquet.index = _date_index(parquet)
    parquet = parquet[~parquet.index.duplicated(keep="last")].sort_index()
    parquet["Volume"] = pd.to_numeric(parquet["Volume"], errors="coerce")
    latest = parquet.index.max()
    start = latest.to_pydatetime() - timedelta(days=max(190, days + 40))
    end = latest.to_pydatetime() + timedelta(days=2)
    try:
        tv = _fetch_borsapy(symbol, start, end)
    except Exception as exc:
        return {"symbol": symbol, "status": "borsapy_error", "error": str(exc)[:240]}
    if tv.empty:
        return {"symbol": symbol, "status": "borsapy_empty", "parquet_source": source}

    tv = tv.loc[tv.index <= latest]
    common = sorted(set(parquet.index).intersection(tv.index))[-days:]
    if not common:
        return {
            "symbol": symbol, "status": "no_common_dates",
            "parquet_source": source,
            "parquet_dates": int((parquet.index <= latest).sum()),
            "borsapy_dates": int(len(tv)),
        }
    left = parquet.loc[common, "Volume"]
    right = tv.loc[common, "Volume"]
    valid = left.notna() & right.notna() & (left > 0) & (right > 0)
    l = left[valid].astype(float)
    r = right[valid].astype(float)
    if len(l):
        pct = (r / l - 1.0).abs() * 100.0
        ratio = r / l
        exact = pct <= 0.001
        within_1 = pct <= 1.0
        within_5 = pct <= 5.0
        within_10 = pct <= 10.0
        within_25 = pct <= 25.0
        worst_idx = pct.sort_values(ascending=False).head(5).index
        worst = [
            {
                "date": ts.strftime("%Y-%m-%d"),
                "parquet": float(l.loc[ts]),
                "borsapy": float(r.loc[ts]),
                "abs_diff_pct": float(pct.loc[ts]),
            }
            for ts in worst_idx
        ]
        metrics = {
            "valid": int(len(l)),
            "parquet_missing": int(left.isna().sum()),
            "borsapy_missing": int(right.isna().sum()),
            "nonpositive_either": int(((left <= 0) | (right <= 0)).sum()),
            "exact_0_001pct": int(exact.sum()),
            "within_1pct": int(within_1.sum()),
            "within_5pct": int(within_5.sum()),
            "within_10pct": int(within_10.sum()),
            "within_25pct": int(within_25.sum()),
            "median_abs_diff_pct": _json_number(pct.median()),
            "p90_abs_diff_pct": _json_number(pct.quantile(0.90)),
            "max_abs_diff_pct": _json_number(pct.max()),
            "median_ratio_borsapy_over_parquet": _json_number(ratio.median()),
            "total_ratio_borsapy_over_parquet": _json_number(r.sum() / l.sum()),
            "pearson_corr": _json_number(l.corr(r)),
            # pandas' spearman corr yolu scipy ister; rank'leri kendimiz alarak
            # denetimi VPS'in üretim bağımlılıklarına bağlamıyoruz.
            "spearman_corr": _json_number(l.rank().corr(r.rank())),
            "worst": worst,
        }
    else:
        metrics = {
            "valid": 0,
            "parquet_missing": int(left.isna().sum()),
            "borsapy_missing": int(right.isna().sum()),
            "nonpositive_either": int(((left <= 0) | (right <= 0)).sum()),
        }
    isy_summary = None
    isy_path = (
        __import__("pathlib").Path(__file__).resolve().parent
        / "health" / "isy_cache" / f"{symbol.replace('.IS', '')}.parquet"
    )
    if isy_path.exists():
        try:
            isy = pd.read_parquet(isy_path)
            if "Volume" in isy.columns:
                isy.index = _date_index(isy)
                isy = isy[~isy.index.duplicated(keep="last")].sort_index()
                isy = isy.loc[isy.index <= latest]
                isy_summary = _volume_pair_summary(tv["Volume"], isy["Volume"], days)
        except Exception as exc:
            isy_summary = {"error": str(exc)[:240]}
    isy_live_summary = None
    parquet_isy_live_summary = None
    try:
        isy_live = _fetch_isyatirim(symbol, start, latest.to_pydatetime())
        if not isy_live.empty:
            isy_live = isy_live.loc[isy_live.index <= latest]
            isy_live_summary = _volume_pair_summary(tv["Volume"], isy_live["Volume"], days)
            parquet_isy_live_summary = _volume_pair_summary(parquet["Volume"], isy_live["Volume"], days)
    except Exception as exc:
        isy_live_summary = {"error": str(exc)[:240]}
    return {
        "symbol": symbol,
        "status": "ok",
        "parquet_source": source,
        "latest_parquet_date": latest.strftime("%Y-%m-%d"),
        "common_dates": int(len(common)),
        "parquet_dates_in_window": int((parquet.index.intersection(pd.DatetimeIndex(common))).size),
        "borsapy_dates_in_window": int(tv.index.intersection(pd.DatetimeIndex(common)).size),
        "metrics": metrics,
        "isyatirim_cache": isy_summary,
        "isyatirim_live": isy_live_summary,
        "parquet_vs_isyatirim_live": parquet_isy_live_summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--liquid", type=int, default=20)
    parser.add_argument("--random", dest="random_count", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260830)
    args = parser.parse_args()

    from bist_data_store import active_version_id, load_manifest

    active_id = active_version_id()
    active_file = load_manifest(active_id)
    if not active_file:
        raise RuntimeError("aktif manifest okunamadı")
    parent_id = None
    try:
        from pathlib import Path
        store = Path(__file__).resolve().parent / "health" / "bist_store"
        active_meta = json.loads((store / "active.json").read_text(encoding="utf-8"))
        parent_id = active_meta.get("parent")
    except Exception:
        parent_id = None
    version_id = parent_id or active_id
    manifest = load_manifest(version_id)
    if not manifest:
        raise RuntimeError(f"kontrol manifesti okunamadı: {version_id}")
    universe = sorted(
        s for s in manifest.get("symbols", {})
        if s.endswith(".IS") and not s.split(".")[0].startswith(INDEX_PREFIXES)
    )
    liquid = [f"{s}.IS" for s in LIQUID if f"{s}.IS" in universe][:args.liquid]
    remainder = [s for s in universe if s not in liquid]
    rng = random.Random(args.seed)
    rng.shuffle(remainder)
    random_sample = remainder[:args.random_count]
    sample = liquid + random_sample

    results = []
    for i, symbol in enumerate(sample, 1):
        entry = manifest["symbols"].get(symbol, {})
        source = (entry.get("field_sources") or {}).get("Volume", "unknown")
        result = _compare(symbol, version_id, source, args.days)
        result["sample_group"] = "liquid" if symbol in liquid else "random"
        results.append(result)
        print(f"PROGRESS {i}/{len(sample)} {symbol} {result['status']}", file=sys.stderr)

    ok = [x for x in results if x.get("status") == "ok" and x.get("metrics", {}).get("valid", 0)]
    all_metrics = [x["metrics"] for x in ok]
    tri = [x["isyatirim_cache"] for x in ok if x.get("isyatirim_cache") and x["isyatirim_cache"].get("valid", 0)]
    live_tri = [x["isyatirim_live"] for x in ok if x.get("isyatirim_live") and x["isyatirim_live"].get("valid", 0)]
    live_parquet = [x["parquet_vs_isyatirim_live"] for x in ok if x.get("parquet_vs_isyatirim_live") and x["parquet_vs_isyatirim_live"].get("valid", 0)]
    def total(key):
        return int(sum(m.get(key, 0) for m in all_metrics))
    valid = total("valid")
    summary = {
        "sample_total": len(sample),
        "liquid_total": len(liquid),
        "random_total": len(random_sample),
        "successful_symbols": len(ok),
        "failed_symbols": len(sample) - len(ok),
        "valid_rows": valid,
        "exact_0_001pct_rows": total("exact_0_001pct"),
        "within_1pct_rows": total("within_1pct"),
        "within_5pct_rows": total("within_5pct"),
        "within_10pct_rows": total("within_10pct"),
        "within_25pct_rows": total("within_25pct"),
        "parquet_missing_rows": total("parquet_missing"),
        "borsapy_missing_rows": total("borsapy_missing"),
        "nonpositive_either_rows": total("nonpositive_either"),
        "median_of_symbol_median_abs_diff_pct": _json_number(
            np.median([m["median_abs_diff_pct"] for m in all_metrics if m.get("median_abs_diff_pct") is not None])
        ) if all_metrics else None,
        "median_of_symbol_p90_abs_diff_pct": _json_number(
            np.median([m["p90_abs_diff_pct"] for m in all_metrics if m.get("p90_abs_diff_pct") is not None])
        ) if all_metrics else None,
        "within_1pct_rate": (total("within_1pct") / valid) if valid else None,
        "within_5pct_rate": (total("within_5pct") / valid) if valid else None,
        "within_10pct_rate": (total("within_10pct") / valid) if valid else None,
        "isyatirim_cache_symbols": len(tri),
        "triangulated_valid_rows": int(sum(m.get("valid", 0) for m in tri)),
        "triangulated_within_1pct_rate": (
            sum(m.get("within_1pct", 0) for m in tri) / sum(m.get("valid", 0) for m in tri)
            if tri and sum(m.get("valid", 0) for m in tri) else None
        ),
        "triangulated_within_5pct_rate": (
            sum(m.get("within_5pct", 0) for m in tri) / sum(m.get("valid", 0) for m in tri)
            if tri and sum(m.get("valid", 0) for m in tri) else None
        ),
        "triangulated_within_10pct_rate": (
            sum(m.get("within_10pct", 0) for m in tri) / sum(m.get("valid", 0) for m in tri)
            if tri and sum(m.get("valid", 0) for m in tri) else None
        ),
        "triangulated_median_of_symbol_median_abs_diff_pct": _json_number(
            np.median([m["median_abs_diff_pct"] for m in tri if m.get("median_abs_diff_pct") is not None])
        ) if tri else None,
        "triangulated_max_abs_diff_pct": _json_number(
            max(m["max_abs_diff_pct"] for m in tri if m.get("max_abs_diff_pct") is not None)
        ) if tri else None,
        "isyatirim_live_symbols": len(live_tri),
        "live_tri_valid_rows": int(sum(m.get("valid", 0) for m in live_tri)),
        "live_tri_within_1pct_rate": (
            sum(m.get("within_1pct", 0) for m in live_tri) / sum(m.get("valid", 0) for m in live_tri)
            if live_tri and sum(m.get("valid", 0) for m in live_tri) else None
        ),
        "live_tri_within_5pct_rate": (
            sum(m.get("within_5pct", 0) for m in live_tri) / sum(m.get("valid", 0) for m in live_tri)
            if live_tri and sum(m.get("valid", 0) for m in live_tri) else None
        ),
        "live_tri_within_10pct_rate": (
            sum(m.get("within_10pct", 0) for m in live_tri) / sum(m.get("valid", 0) for m in live_tri)
            if live_tri and sum(m.get("valid", 0) for m in live_tri) else None
        ),
        "live_tri_median_of_symbol_median_abs_diff_pct": _json_number(
            np.median([m["median_abs_diff_pct"] for m in live_tri if m.get("median_abs_diff_pct") is not None])
        ) if live_tri else None,
        "live_tri_max_abs_diff_pct": _json_number(
            max(m["max_abs_diff_pct"] for m in live_tri if m.get("max_abs_diff_pct") is not None)
        ) if live_tri else None,
        "live_parquet_symbols": len(live_parquet),
        "live_parquet_valid_rows": int(sum(m.get("valid", 0) for m in live_parquet)),
        "live_parquet_within_1pct_rate": (
            sum(m.get("within_1pct", 0) for m in live_parquet) / sum(m.get("valid", 0) for m in live_parquet)
            if live_parquet and sum(m.get("valid", 0) for m in live_parquet) else None
        ),
        "live_parquet_within_5pct_rate": (
            sum(m.get("within_5pct", 0) for m in live_parquet) / sum(m.get("valid", 0) for m in live_parquet)
            if live_parquet and sum(m.get("valid", 0) for m in live_parquet) else None
        ),
        "live_parquet_within_10pct_rate": (
            sum(m.get("within_10pct", 0) for m in live_parquet) / sum(m.get("valid", 0) for m in live_parquet)
            if live_parquet and sum(m.get("valid", 0) for m in live_parquet) else None
        ),
        "live_parquet_median_of_symbol_median_abs_diff_pct": _json_number(
            np.median([m["median_abs_diff_pct"] for m in live_parquet if m.get("median_abs_diff_pct") is not None])
        ) if live_parquet else None,
        "live_parquet_max_abs_diff_pct": _json_number(
            max(m["max_abs_diff_pct"] for m in live_parquet if m.get("max_abs_diff_pct") is not None)
        ) if live_parquet else None,
    }
    print(json.dumps({
        "audit": "borsapy_vs_parent_parquet_volume",
        "active_version": active_id,
        "control_version": version_id,
        "days_requested": args.days,
        "seed": args.seed,
        "sample": sample,
        "summary": summary,
        "results": results,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
