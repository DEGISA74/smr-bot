# -*- coding: utf-8 -*-
"""
İş 5 — Yeni dip dönüş motoru / gölge test
==========================================

Bu dosya canlı tarama, Streamlit, patron.db veya scan_signals'a yazmaz.
Günlük ve 4 saatlik parquet kasalarını okuyarak hipotezi geçmişte sınar ve
yalnızca smr_lab/out altına rapor üretir.

Hipotez (önceden mühürlü eşiklerle):
  1) Fiyat önceki 15 seansın en düşük seviyesinin altına iğne atar.
  2) Aynı gün kapanışı o seviyenin üstüne döner.
  3) Hacim, önceki 20 seans medyanının en az 1,5 katıdır.
  4) RSI, önceki 15 seanstaki düşük noktaya göre en az 2 puan daha yüksektir.
  5) 4 saatlik son bar, kendisinden önceki üç mini tepenin üstünde kapanır.

Kabul kapısı:
  - en az üç vade (T+3/T+5/T+20)
  - XU100_CLOSE_VS_SMA50 ile iki rejim
  - girişten sonraki ilk beş seansta +%3 veya -%%2,5 hangisine önce ulaştı
  - hisse ve XU100 aynı ertesi açılış cetveli; hisse tavan kilidi atlar
  - bağımsız olaylar (is_event_start=1, olaylar arası 10 seans)
  - eğitim döneminden sonra görülmemiş doğrulama dönemi

Eşikler sonuç görüldükten sonra gevşetilmez. Kapı geçilmezse motor ekrana
alınmaz ve yalnız laboratuvar adayı olarak raporlanır.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

# Betik doğrudan smr_lab altından çalıştırıldığında kök modülleri bulabilsin.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from signal_policy import (
    MEASUREMENT_REGIME_FALLING,
    MEASUREMENT_REGIME_RISING,
    measurement_regime_series,
    resolve_next_open_entry,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


DAILY_DIR = ROOT / "veriler"
FOUR_HOUR_DIR = ROOT / "veriler_4s"
OUT_DIR = ROOT / "smr_lab" / "out"
DEFAULT_JSON = OUT_DIR / "dip_motor_shadow_report.json"
DEFAULT_MD = OUT_DIR / "dip_motor_shadow_report.md"

# Bu eşikler ölçüm başlamadan önce sabitlendi.
DIP_LOOKBACK_SESSIONS = 15
VOLUME_LOOKBACK_SESSIONS = 20
VOLUME_MULTIPLIER = 1.5
RSI_PERIOD = 14
RSI_DIVERGENCE_POINTS = 2.0
FOUR_HOUR_MINI_HIGH_BARS = 3
EVENT_GAP_SESSIONS = 10
HORIZONS = (3, 5, 10, 20)
TARGET_PCT = 3.0
STOP_PCT = 2.5
MIN_EVENTS_TOTAL = 300
MIN_EVENTS_PER_REGIME = 75
MIN_VALIDATION_EVENTS = 50
VALIDATION_START = date(2026, 6, 1)


def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Parquet kolonlarını sade isimlere indirir."""
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [c[0] if isinstance(c, tuple) else c for c in out.columns]
    out.index = pd.to_datetime(out.index)
    return out.sort_index()


def _load_daily(path: Path) -> pd.DataFrame | None:
    try:
        df = _clean_columns(pd.read_parquet(path))
    except Exception:
        return None
    needed = {"Open", "High", "Low", "Close"}
    if not needed.issubset(df.columns):
        return None
    cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    out = df[cols].copy()
    for col in cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "Volume" not in out.columns:
        out["Volume"] = np.nan
    return out.dropna(subset=["Open", "High", "Low", "Close"])


def _load_four_hour(path: Path) -> pd.DataFrame | None:
    try:
        df = _clean_columns(pd.read_parquet(path))
    except Exception:
        return None
    needed = {"High", "Close"}
    if not needed.issubset(df.columns):
        return None
    out = df[["High", "Close"]].copy()
    out["High"] = pd.to_numeric(out["High"], errors="coerce")
    out["Close"] = pd.to_numeric(out["Close"], errors="coerce")
    return out.dropna(subset=["High", "Close"])


def _rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    result = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss.replace(0.0, np.nan)))
    result = result.mask((avg_loss == 0.0) & (avg_gain > 0.0), 100.0)
    result = result.mask((avg_gain == 0.0) & (avg_loss > 0.0), 0.0)
    result = result.mask((avg_gain == 0.0) & (avg_loss == 0.0), 50.0)
    return result


def _four_hour_break_map(df: pd.DataFrame) -> dict[date, bool]:
    """Her günün son 4S kapanışı önceki üç mini tepeyi geçti mi?"""
    if df is None or df.empty:
        return {}
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_convert("Europe/Istanbul").tz_localize(None)
    dates = idx.normalize()
    high = df["High"].to_numpy(float)
    close = df["Close"].to_numpy(float)
    result: dict[date, bool] = {}
    for day, positions in pd.Series(np.arange(len(df)), index=dates).groupby(level=0):
        pos = positions.to_numpy(dtype=int)
        last = int(pos[-1])
        first = max(0, last - FOUR_HOUR_MINI_HIGH_BARS)
        prior = high[first:last]
        ok = (
            len(prior) == FOUR_HOUR_MINI_HIGH_BARS
            and np.isfinite(close[last])
            and np.isfinite(prior).all()
            and close[last] > float(np.max(prior))
        )
        result[day.date()] = bool(ok)
    return result


def _detect_events(
    ticker: str,
    daily: pd.DataFrame,
    four_hour_breaks: dict[date, bool],
    regime_map: dict[date, str],
) -> tuple[list[dict], int]:
    """Bir hissenin ham adaylarını ve bağımsız olay sayısını üretir."""
    if daily is None or len(daily) < 80:
        return [], 0
    close = daily["Close"].astype(float).reset_index(drop=True)
    high = daily["High"].astype(float).reset_index(drop=True)
    low = daily["Low"].astype(float).reset_index(drop=True)
    volume = daily["Volume"].astype(float).reset_index(drop=True)
    dates = pd.to_datetime(daily.index).normalize().date
    rsi = _rsi(close).reset_index(drop=True)
    raw = 0
    independent: list[dict] = []
    last_event_pos: int | None = None
    warmup = max(DIP_LOOKBACK_SESSIONS, VOLUME_LOOKBACK_SESSIONS, RSI_PERIOD) + 1
    for i in range(warmup, len(daily)):
        prior_low_window = low.iloc[i - DIP_LOOKBACK_SESSIONS:i]
        prior_low = float(prior_low_window.min())
        today_low = float(low.iloc[i])
        today_close = float(close.iloc[i])
        vol_ref = float(volume.iloc[i - VOLUME_LOOKBACK_SESSIONS:i].median())
        vol_ratio = (float(volume.iloc[i]) / vol_ref) if vol_ref > 0 else math.nan
        prev_low_offset = int(np.nanargmin(prior_low_window.to_numpy(float)))
        prev_low_pos = i - DIP_LOOKBACK_SESSIONS + prev_low_offset
        rsi_now = float(rsi.iloc[i]) if np.isfinite(rsi.iloc[i]) else math.nan
        rsi_prev = float(rsi.iloc[prev_low_pos]) if np.isfinite(rsi.iloc[prev_low_pos]) else math.nan
        sweep = today_low < prior_low
        reclaim = today_close > prior_low
        volume_ok = np.isfinite(vol_ratio) and vol_ratio >= VOLUME_MULTIPLIER
        divergence = np.isfinite(rsi_now) and np.isfinite(rsi_prev) and rsi_now >= rsi_prev + RSI_DIVERGENCE_POINTS
        mini_break = bool(four_hour_breaks.get(dates[i], False))
        if not (sweep and reclaim and volume_ok and divergence and mini_break):
            continue
        raw += 1
        if last_event_pos is not None and i - last_event_pos < EVENT_GAP_SESSIONS:
            continue
        last_event_pos = i
        independent.append(
            {
                "ticker": ticker,
                "signal_date": dates[i].isoformat(),
                "regime": regime_map.get(dates[i], "BILINMIYOR"),
                "is_event_start": 1,
                "signal_pos": i,
                "prior_15_low": round(prior_low, 6),
                "signal_low": round(today_low, 6),
                "signal_close": round(today_close, 6),
                "volume_ratio": round(float(vol_ratio), 4),
                "rsi_previous_low": round(rsi_prev, 4),
                "rsi_signal": round(rsi_now, 4),
                "four_hour_mini_high_break": True,
            }
        )
    return independent, raw


def _date_position(df: pd.DataFrame) -> dict[date, int]:
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    out: dict[date, int] = {}
    for pos, value in enumerate(idx.normalize()):
        out[value.date()] = pos
    return out


def _first_five_barrier(df: pd.DataFrame, entry_pos: int, entry_price: float) -> dict:
    target = entry_price * (1.0 + TARGET_PCT / 100.0)
    stop = entry_price * (1.0 - STOP_PCT / 100.0)
    for offset in range(5):
        pos = entry_pos + offset
        if pos >= len(df):
            return {"status": "insufficient_forward_data", "day": None}
        hi = float(df["High"].iloc[pos])
        lo = float(df["Low"].iloc[pos])
        hit_target = hi >= target
        hit_stop = lo <= stop
        if hit_target and hit_stop:
            return {"status": "ambiguous_same_bar", "day": offset + 1}
        if hit_target:
            return {"status": "target_first", "day": offset + 1}
        if hit_stop:
            return {"status": "stop_first", "day": offset + 1}
    return {"status": "neither_in_first_5", "day": None}


def _evaluate_event(event: dict, daily: pd.DataFrame, xu100: pd.DataFrame) -> dict:
    signal_date = event["signal_date"]
    entry = resolve_next_open_entry(
        daily,
        signal_date,
        bias="bullish",
        apply_bist_limit=True,
        max_locked_sessions=3,
    )
    out = dict(event)
    out["entry_status"] = str(entry.get("status", "unknown"))
    out["entry_date"] = entry.get("entry_date")
    out["entry_price"] = entry.get("entry_price")
    out["locked_sessions"] = int(entry.get("locked_sessions", 0) or 0)
    out["same_entry_day"] = False
    out["barrier_status"] = None
    out["barrier_day"] = None
    out.update({f"ret_{h}g": None for h in HORIZONS})
    out.update({f"bench_{h}g": None for h in HORIZONS})
    out.update({f"alpha_{h}g": None for h in HORIZONS})
    if not str(entry.get("status", "")).startswith("filled"):
        return out
    entry_pos = int(entry["entry_pos"])
    entry_date = date.fromisoformat(str(entry["entry_date"]))
    xu_positions = _date_position(xu100)
    xu_entry_pos = xu_positions.get(entry_date)
    if xu_entry_pos is None:
        return out
    out["same_entry_day"] = True
    barrier = _first_five_barrier(daily, entry_pos, float(entry["entry_price"]))
    out["barrier_status"] = barrier["status"]
    out["barrier_day"] = barrier["day"]
    for horizon in HORIZONS:
        stock_exit = entry_pos + horizon - 1
        xu_exit = xu_entry_pos + horizon - 1
        if stock_exit >= len(daily) or xu_exit >= len(xu100):
            continue
        stock_close = float(daily["Close"].iloc[stock_exit])
        xu_open = float(xu100["Open"].iloc[xu_entry_pos])
        xu_close = float(xu100["Close"].iloc[xu_exit])
        entry_price = float(entry["entry_price"])
        if min(entry_price, stock_close, xu_open, xu_close) <= 0:
            continue
        stock_ret = (stock_close / entry_price - 1.0) * 100.0
        bench_ret = (xu_close / xu_open - 1.0) * 100.0
        out[f"ret_{horizon}g"] = round(stock_ret, 6)
        out[f"bench_{horizon}g"] = round(bench_ret, 6)
        out[f"alpha_{horizon}g"] = round(stock_ret - bench_ret, 6)
    return out


def _stats(rows: list[dict], horizon: int) -> dict:
    key = f"ret_{horizon}g"
    alpha_key = f"alpha_{horizon}g"
    bench_key = f"bench_{horizon}g"
    values = np.array([r[key] for r in rows if r.get(key) is not None], dtype=float)
    alphas = np.array([r[alpha_key] for r in rows if r.get(alpha_key) is not None], dtype=float)
    bench = np.array([r[bench_key] for r in rows if r.get(bench_key) is not None], dtype=float)
    if not len(values):
        return {"n": 0}
    return {
        "n": int(len(values)),
        "mean_ret_pct": round(float(np.mean(values)), 4),
        "median_ret_pct": round(float(np.median(values)), 4),
        "positive_rate_pct": round(float(np.mean(values > 0) * 100.0), 2),
        "mean_benchmark_pct": round(float(np.mean(bench)), 4) if len(bench) else None,
        "mean_alpha_pct": round(float(np.mean(alphas)), 4) if len(alphas) else None,
        "median_alpha_pct": round(float(np.median(alphas)), 4) if len(alphas) else None,
        "alpha_positive_rate_pct": round(float(np.mean(alphas > 0) * 100.0), 2) if len(alphas) else None,
    }


def _barrier_stats(rows: list[dict]) -> dict:
    counts = Counter(r.get("barrier_status") for r in rows)
    resolved = counts.get("target_first", 0) + counts.get("stop_first", 0)
    return {
        "target_first": int(counts.get("target_first", 0)),
        "stop_first": int(counts.get("stop_first", 0)),
        "ambiguous_same_bar": int(counts.get("ambiguous_same_bar", 0)),
        "neither_in_first_5": int(counts.get("neither_in_first_5", 0)),
        "insufficient_forward_data": int(counts.get("insufficient_forward_data", 0)),
        "resolved_target_or_stop": int(resolved),
        "target_share_of_resolved_pct": round(100.0 * counts.get("target_first", 0) / resolved, 2) if resolved else None,
    }


def _group_stats(rows: list[dict]) -> dict:
    return {
        "events": len(rows),
        "barrier": _barrier_stats(rows),
        "horizons": {str(h): _stats(rows, h) for h in HORIZONS},
    }


def _alpha_pass(rows: list[dict]) -> bool:
    """Önceden belirlenen üstünlük kapısı: iki rejimde 3 vadeden en az 2'si."""
    if len(rows) < MIN_EVENTS_PER_REGIME:
        return False
    passing = 0
    for horizon in (3, 5, 20):
        s = _stats(rows, horizon)
        if s.get("n", 0) >= MIN_EVENTS_PER_REGIME and (s.get("mean_alpha_pct") or -math.inf) > 0 and (s.get("median_alpha_pct") or -math.inf) > 0:
            passing += 1
    return passing >= 2


def _barrier_pass(rows: list[dict]) -> bool:
    s = _barrier_stats(rows)
    return s["resolved_target_or_stop"] > 0 and s["target_first"] > s["stop_first"]


def _gate_report(
    all_rows: list[dict],
    train_rows: list[dict],
    validation_rows: list[dict],
    validation_start: date,
) -> dict:
    regimes = {r.get("regime") for r in all_rows}
    regime_rows = {
        MEASUREMENT_REGIME_RISING: [r for r in all_rows if r.get("regime") == MEASUREMENT_REGIME_RISING],
        MEASUREMENT_REGIME_FALLING: [r for r in all_rows if r.get("regime") == MEASUREMENT_REGIME_FALLING],
    }
    val_regime_rows = {
        key: [r for r in validation_rows if r.get("regime") == key]
        for key in (MEASUREMENT_REGIME_RISING, MEASUREMENT_REGIME_FALLING)
    }
    known_regime_total = sum(len(rows) for rows in regime_rows.values())
    known_validation_total = sum(len(rows) for rows in val_regime_rows.values())
    unknown_regime_total = len(all_rows) - known_regime_total
    alignment = all(bool(r.get("same_entry_day")) for r in all_rows) if all_rows else False
    is_event_clean = all(int(r.get("is_event_start", 0)) == 1 for r in all_rows) if all_rows else False
    gates = {
        "1_three_horizons": {"pass": len(HORIZONS) >= 3, "detail": list(HORIZONS)},
        "2_two_regimes": {
            "pass": regimes.issuperset({MEASUREMENT_REGIME_RISING, MEASUREMENT_REGIME_FALLING}),
            "detail": sorted(str(x) for x in regimes),
        },
        "3_first_five_target_vs_stop": {
            "pass": all(_barrier_pass(regime_rows[k]) for k in regime_rows),
            "all": _barrier_stats(all_rows),
            "rising": _barrier_stats(regime_rows[MEASUREMENT_REGIME_RISING]),
            "falling": _barrier_stats(regime_rows[MEASUREMENT_REGIME_FALLING]),
        },
        "4_xu100_alpha": {
            "pass": all(_alpha_pass(regime_rows[k]) for k in regime_rows),
            "rising": _group_stats(regime_rows[MEASUREMENT_REGIME_RISING]),
            "falling": _group_stats(regime_rows[MEASUREMENT_REGIME_FALLING]),
        },
        "5_same_entry_table": {"pass": alignment, "all_rows_same_entry_day": alignment},
        "6_independent_events": {
            "pass": known_regime_total >= MIN_EVENTS_TOTAL and all(len(regime_rows[k]) >= MIN_EVENTS_PER_REGIME for k in regime_rows) and is_event_clean,
            "total": known_regime_total,
            "all_events_including_unknown_regime": len(all_rows),
            "unknown_regime": unknown_regime_total,
            "rising": len(regime_rows[MEASUREMENT_REGIME_RISING]),
            "falling": len(regime_rows[MEASUREMENT_REGIME_FALLING]),
            "minimum_total": MIN_EVENTS_TOTAL,
            "minimum_per_regime": MIN_EVENTS_PER_REGIME,
            "all_is_event_start_1": is_event_clean,
        },
        "7_unseen_validation": {
            "pass": known_validation_total >= MIN_VALIDATION_EVENTS and all(len(val_regime_rows[k]) >= 1 for k in val_regime_rows) and all(_barrier_pass(val_regime_rows[k]) for k in val_regime_rows) and all(_alpha_pass(val_regime_rows[k]) for k in val_regime_rows),
            "validation_start": validation_start.isoformat(),
            "events": known_validation_total,
            "all_validation_rows_including_unknown_regime": len(validation_rows),
            "minimum_events": MIN_VALIDATION_EVENTS,
            "rising": _group_stats(val_regime_rows[MEASUREMENT_REGIME_RISING]),
            "falling": _group_stats(val_regime_rows[MEASUREMENT_REGIME_FALLING]),
        },
    }
    return {"gates": gates, "passed": all(bool(item.get("pass")) for item in gates.values())}


def _markdown(report: dict) -> str:
    p = report["parameters"]
    gate_rows = []
    for key, value in report["gates"].items():
        status = "GEÇTİ" if value.get("pass") else "KALDI"
        gate_rows.append(f"| {key} | **{status}** |")
    lines = [
        "# İş 5 — Yeni Dip Motoru Gölge Testi",
        "",
        f"**Genel hüküm:** {'EKRANA ALINABİLİR DEĞİL — laboratuvarda kaldı' if not report['passed'] else 'TÜM KAPILAR GEÇTİ — ürün kararı bekliyor'}.",
        "",
        f"- Veri: {report['data']['symbols_with_daily']} günlük dosya · {report['data']['symbols_with_four_hour']} dosyada 4S doğrulama",
        f"- Dönem: {report['data']['start']} → {report['data']['end']} · doğrulama başlangıcı: {p['validation_start']}",
        f"- Ham aday: {report['data']['raw_candidates']} · bağımsız ve olgun olay: **{report['data']['mature_events']}**",
        f"- Giriş: ertesi işlem yapılabilir açılış; tavan kilidi en fazla 3 seans atlandı; hisse ve XU100 aynı giriş gününde ölçüldü.",
        f"- Rejim: `{report['data']['regime_rule']}`; yalnız ölçüm bölmesi, canlı tarama filtresi değil.",
        "",
        "## Sabitlenen hipotez eşikleri",
        "",
        f"15 seans dip penceresi · hacim ≥ {p['volume_multiplier']}× önceki 20 seans medyanı · RSI farkı ≥ {p['rsi_divergence_points']} puan · 4S önceki 3 mini tepe kırılımı · olay aralığı {p['event_gap_sessions']} seans · hedef +%{p['target_pct']} / zarar −%{p['stop_pct']}.",
        "",
        "## Yedi maddelik kabul kapısı",
        "",
        "| Kapı | Sonuç |",
        "|---|---|",
        *gate_rows,
        "",
        "## Toplu sonuç",
        "",
        "| Grup | Olay | Hedef önce | Zarar önce | T+3 alfa ort/med | T+5 alfa ort/med | T+20 alfa ort/med |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, rows in (
        ("Tüm olgun olaylar", report["groups"]["all"]["rows"]),
        ("Eğitim dönemi", report["groups"]["train"]["rows"]),
        ("Görülmemiş doğrulama", report["groups"]["validation"]["rows"]),
    ):
        barrier = rows["barrier"]
        cells = []
        for h in (3, 5, 20):
            s = rows["horizons"][str(h)]
            cells.append("—" if not s.get("n") else f"{s['mean_alpha_pct']:+.2f}/{s['median_alpha_pct']:+.2f}")
        lines.append(f"| {label} | {rows['events']} | {barrier['target_first']} | {barrier['stop_first']} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Rejim kırılımı — tüm olgun olaylar",
        "",
        "| Rejim | Olay | Hedef önce | Zarar önce | T+3 alfa ort/med | T+5 alfa ort/med | T+10 alfa ort/med | T+20 alfa ort/med |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, key in (("Yükselen", "rising"), ("Düşen", "falling")):
        group = report["gates"]["4_xu100_alpha"][key]
        barrier = group["barrier"]
        cells = []
        for h in HORIZONS:
            stats = group["horizons"][str(h)]
            cells.append("—" if not stats.get("n") else f"{stats['mean_alpha_pct']:+.2f}/{stats['median_alpha_pct']:+.2f}")
        lines.append(f"| {label} | {group['events']} | {barrier['target_first']} | {barrier['stop_first']} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Görülmemiş doğrulama — rejim kırılımı",
        "",
        "| Rejim | Olay | Hedef önce | Zarar önce | T+3 alfa ort/med | T+5 alfa ort/med | T+10 alfa ort/med | T+20 alfa ort/med |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, key in (("Yükselen", "rising"), ("Düşen", "falling")):
        group = report["gates"]["7_unseen_validation"][key]
        barrier = group["barrier"]
        cells = []
        for h in HORIZONS:
            stats = group["horizons"][str(h)]
            cells.append("—" if not stats.get("n") else f"{stats['mean_alpha_pct']:+.2f}/{stats['median_alpha_pct']:+.2f}")
        lines.append(f"| {label} | {group['events']} | {barrier['target_first']} | {barrier['stop_first']} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Güvenlik hükmü",
        "",
        "Bu betik patron.db'ye, scan_signals'a, app.py'ye ve canlı ekrana yazmaz. Kapı geçmediyse hipotez yalnız laboratuvar çıktısıdır; eşikler gevşetilmez.",
        "",
    ]
    return "\n".join(lines)


def run(validation_start: date = VALIDATION_START, json_out: Path = DEFAULT_JSON, md_out: Path = DEFAULT_MD) -> dict:
    xu100 = _load_daily(DAILY_DIR / "XU100.IS_1d.parquet")
    if xu100 is None:
        raise RuntimeError("XU100 günlük verisi bulunamadı.")
    regime_series = measurement_regime_series(xu100)
    regime_map = {}
    for stamp, regime in regime_series.items():
        if pd.notna(regime):
            regime_map[pd.Timestamp(stamp).date()] = str(regime)

    daily_paths = sorted(DAILY_DIR.glob("*.IS_1d.parquet"))
    four_hour_paths = {p.name.replace(".IS_4h.parquet", ""): p for p in FOUR_HOUR_DIR.glob("*.IS_4h.parquet")}
    events: list[dict] = []
    raw_candidates = 0
    symbols_daily = 0
    symbols_four_hour = 0
    data_dates: list[date] = []
    for path in daily_paths:
        ticker = path.name.replace(".IS_1d.parquet", "")
        if ticker == "XU100":
            continue
        daily = _load_daily(path)
        if daily is None:
            continue
        symbols_daily += 1
        data_dates += [x.date() for x in pd.to_datetime(daily.index)]
        intraday_path = four_hour_paths.get(ticker)
        if intraday_path is None:
            continue
        four_hour = _load_four_hour(intraday_path)
        if four_hour is None:
            continue
        symbols_four_hour += 1
        detected, raw = _detect_events(ticker, daily, _four_hour_break_map(four_hour), regime_map)
        raw_candidates += raw
        for event in detected:
            evaluated = _evaluate_event(event, daily, xu100)
            # T+20 kapanışı oluşmamış olay, kapı karne örneğine alınmaz.
            if evaluated.get("alpha_20g") is None:
                continue
            events.append(evaluated)

    events.sort(key=lambda row: (row["signal_date"], row["ticker"]))
    train = [r for r in events if date.fromisoformat(r["signal_date"]) < validation_start]
    validation = [r for r in events if date.fromisoformat(r["signal_date"]) >= validation_start]
    gate = _gate_report(events, train, validation, validation_start)
    report = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "passed": gate["passed"],
        "parameters": {
            "dip_lookback_sessions": DIP_LOOKBACK_SESSIONS,
            "volume_lookback_sessions": VOLUME_LOOKBACK_SESSIONS,
            "volume_multiplier": VOLUME_MULTIPLIER,
            "rsi_period": RSI_PERIOD,
            "rsi_divergence_points": RSI_DIVERGENCE_POINTS,
            "four_hour_mini_high_bars": FOUR_HOUR_MINI_HIGH_BARS,
            "event_gap_sessions": EVENT_GAP_SESSIONS,
            "horizons": list(HORIZONS),
            "target_pct": TARGET_PCT,
            "stop_pct": STOP_PCT,
            "validation_start": validation_start.isoformat(),
        },
        "data": {
            "symbols_with_daily": symbols_daily,
            "symbols_with_four_hour": symbols_four_hour,
            "start": min(data_dates).isoformat() if data_dates else None,
            "end": max(data_dates).isoformat() if data_dates else None,
            "raw_candidates": raw_candidates,
            "mature_events": len(events),
            "regime_rule": "XU100_CLOSE_VS_SMA50",
            "is_event_start_rule": f"raw sinyal araligi >= {EVENT_GAP_SESSIONS} seans; rapora giren her olay is_event_start=1",
        },
        "gates": gate["gates"],
        "groups": {
            "all": {"rows": _group_stats(events)},
            "train": {"rows": _group_stats(train)},
            "validation": {"rows": _group_stats(validation)},
        },
        "events": events,
        "note": "Kapı geçmezse canlı ekrana alınmaz; bu rapor laboratuvar çıktısıdır.",
    }
    json_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_out.write_text(_markdown(report), encoding="utf-8")
    print("=== İŞ 5 — YENİ DİP MOTORU GÖLGE TESTİ ===")
    print(f"Günlük dosya: {symbols_daily} · 4S doğrulamalı: {symbols_four_hour}")
    print(f"Ham aday: {raw_candidates} · olgun bağımsız olay: {len(events)}")
    for key, value in gate["gates"].items():
        print(f"  {'✅' if value.get('pass') else '❌'} {key}")
    print(f"HÜKÜM: {'GEÇTİ — ürün kararı bekliyor' if gate['passed'] else 'GEÇMEDİ — laboratuvarda kaldı, ekrana çıkmaz'}")
    print(f"Rapor: {md_out}")
    print(f"Ham JSON: {json_out}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="İş 5 yeni dip motoru gölge testi")
    parser.add_argument("--validation-start", default=VALIDATION_START.isoformat())
    parser.add_argument("--json-out", default=str(DEFAULT_JSON))
    parser.add_argument("--md-out", default=str(DEFAULT_MD))
    args = parser.parse_args()
    try:
        start = date.fromisoformat(args.validation_start)
        report = run(start, Path(args.json_out), Path(args.md_out))
    except Exception as exc:
        print(f"Gölge test çalışmadı: {exc}", file=sys.stderr)
        return 2
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
