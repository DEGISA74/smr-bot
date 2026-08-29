# -*- coding: utf-8 -*-
"""
trajectory_forward_collector.py — T+3 TRAJECTORY FORWARD COLLECTION

Salt-okur collector. patron.db, veriler/*_1d.parquet ve veriler_saatlik/*_1h.parquet
okunur; ana günlük parquet'e, patron.db'ye ve app.py'ye yazılmaz.

Modlar:
  close      Günlük kapanış snapshot'ı: live_close
  intraday   Son tamamlanmış saatlik barla provizyonel snapshot: live_intraday
  settle     T+4 girişten sonra olgunlaşan 5 / 10 / 20 günlük sonuçları hesaplar

Kural:
  T0 event başlangıcı → T+1/T+2/T+3 gözlem → T+3 kapanışında karar
  → T+4 açılışında planlanan giriş → karar sonrası sonuç.

Çıktılar yalnızca bu klasöre yazılır:
  trajectory_forward_snapshots.csv
  trajectory_forward_outcomes.csv
  trajectory_forward_run.json

Gün içi değer kesin sinyal değildir. Saatlik parquet'teki son barın zamanı ve
veri tazeliği ayrıca kaydedilir. Kapanış snapshot'ı ana referanstır.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "patron.db"
DAILY_DIR = ROOT / "veriler"
HOURLY_DIR = ROOT / "veriler_saatlik"
OUT_DIR = Path(__file__).resolve().parent
FORWARD_START_DATE = "2026-08-07"  # İlk canlı kohort: 07 Ağustos 2026 Master Scan
SNAPSHOT_OUT = OUT_DIR / "trajectory_forward_snapshots.csv"
OUTCOME_OUT = OUT_DIR / "trajectory_forward_outcomes.csv"
RUN_OUT = OUT_DIR / "trajectory_forward_run.json"

FOLLOW_DAYS = 3
OUTCOME_HORIZONS = (5, 10, 20)
HOLD_DAYS = max(OUTCOME_HORIZONS)
TARGET_PCT = 30.0
NOISE_SCAN_TYPES = {"radar1"}
SWING_LEFT_BARS = 2
SWING_RIGHT_BARS = 2
SWING_LOOKBACK_BARS = 60

STORED_FEATURES = [
    "f_smart_money_score", "f_smart_structural_score",
    "f_smart_tactical_score", "f_master_score", "f_spike_dominance",
    "f_breakout_state", "f_rel_obv_state", "f_udvr_state",
    "f_force_index_dual", "f_mfi_dual", "f_cmf_dual",
]

COMPONENTS = [
    "rsi", "rs", "obv", "cmf", "mfi", "volr", "ma20", "atr_move",
    "sm_score", "struct_tact", "rel_obv", "udvr", "fidx", "mfi_dual",
    "spike", "breakout", "master", "yab_in", "yab_streak", "yab_bps",
    "israr", "genislik",
]

REL_MAP = {"underperform_strong": -2, "underperform_mild": -1,
           "inline": 0, "outperform_mild": 1, "outperform_strong": 2}
UDV_MAP = {"strong_seller": -2, "seller": -1, "balanced": 0,
           "buyer": 1, "strong_buyer": 2}
FLOW_MAP = {"strong_neg": -3, "neg": -2, "turning_down": -1,
            "neutral": 0, "turning_up": 1, "pos": 2, "strong_pos": 3}
MFI_MAP = {"cooling_smart_exit": -2, "overbought_both": -1,
           "early_overbought": 0, "neutral": 0, "early_oversold": 1,
           "oversold_both": 1, "smart_money_recovery": 2}


def clean_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    return text[:-3] if text.endswith(".IS") else text


def parse_date(value: object) -> str:
    return str(pd.Timestamp(value).date())


def now_istanbul() -> pd.Timestamp:
    return pd.Timestamp.now(tz="Europe/Istanbul")


def read_frame(path: Path, *, hourly: bool = False) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    needed = {"Open", "High", "Low", "Close", "Volume"}
    if not needed.issubset(set(df.columns)):
        return None
    df = df.loc[:, ["Open", "High", "Low", "Close", "Volume"]].copy()
    idx = pd.DatetimeIndex(pd.to_datetime(df.index, errors="coerce"))
    if hourly:
        if idx.tz is None:
            idx = idx.tz_localize("Europe/Istanbul")
        else:
            idx = idx.tz_convert("Europe/Istanbul")
    else:
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        idx = idx.normalize()
    df.index = idx
    df = df[~df.index.isna()]
    df = df[~df.index.duplicated(keep="last")].sort_index()
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["Close"])


def daily_path(symbol: str) -> Path:
    return DAILY_DIR / f"{clean_symbol(symbol)}.IS_1d.parquet"


def hourly_path(symbol: str) -> Path:
    return HOURLY_DIR / f"{clean_symbol(symbol)}.IS_1h.parquet"


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close, high, low, vol = out["Close"], out["High"], out["Low"], out["Volume"]
    delta = close.diff()
    up = delta.clip(lower=0).rolling(14).mean()
    down = (-delta.clip(upper=0)).rolling(14).mean()
    out["rsi"] = 100 - 100 / (1 + up / down.replace(0, np.nan))
    out["sma20"] = close.rolling(20).mean()
    out["obv"] = (np.sign(delta).fillna(0) * vol).cumsum()
    width = (high - low).replace(0, np.nan)
    mfv = ((((close - low) - (high - close)) / width) * vol).fillna(0.0)
    out["cmf"] = mfv.rolling(20).sum() / vol.rolling(20).sum().replace(0, np.nan)
    tp = (high + low + close) / 3
    rmf = tp * vol
    pmf = rmf.where(tp > tp.shift(1), 0)
    nmf = rmf.where(tp < tp.shift(1), 0)
    out["mfi"] = 100 - 100 / (1 + pmf.rolling(14).sum() / nmf.rolling(14).sum().replace(0, np.nan))
    out["volr"] = vol / vol.rolling(20).mean().replace(0, np.nan)
    true_range = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    out["atr"] = true_range.rolling(14).mean()
    return out


def load_events() -> pd.DataFrame:
    con = sqlite3.connect(DB)
    query = """
        SELECT symbol, event_start_date, scan_date, scan_type
        FROM scan_signals
        WHERE event_start_date IS NOT NULL
          AND scan_date IS NOT NULL
    """
    df = pd.read_sql_query(query, con)
    con.close()
    if df.empty:
        return df
    df["symbol"] = df["symbol"].map(clean_symbol)
    df["event_start_date"] = df["event_start_date"].map(parse_date)
    df["scan_date"] = df["scan_date"].map(parse_date)
    df["scan_type"] = df["scan_type"].astype(str).str.strip()
    df = df.dropna(subset=["symbol", "event_start_date", "scan_date"])
    df = df[df["scan_date"] >= FORWARD_START_DATE].copy()
    # Forward sistem 07 Ağustos'ta başladı. Daha eski bir teknik event o gün
    # yeniden görülse bile geçmiş üç günü canlı teyitmiş gibi kullanamayız;
    # ilk forward gözlem günü T0 kabul edilir.
    df.loc[df["event_start_date"] < FORWARD_START_DATE, "event_start_date"] = FORWARD_START_DATE
    return df


def load_stored_features() -> dict[str, dict[str, dict[str, object]]]:
    con = sqlite3.connect(DB)
    cols = ",".join(STORED_FEATURES)
    query = f"SELECT symbol, scan_date, {cols} FROM scan_signals"
    df = pd.read_sql_query(query, con)
    con.close()
    result: dict[str, dict[str, dict[str, object]]] = {}
    if df.empty:
        return result
    df["symbol"] = df["symbol"].map(clean_symbol)
    df["scan_date"] = df["scan_date"].map(parse_date)
    for (sym, day), group in df.groupby(["symbol", "scan_date"]):
        row: dict[str, object] = {}
        for col in STORED_FEATURES:
            values = group[col].dropna()
            row[col] = values.iloc[0] if not values.empty else None
        result.setdefault(sym, {})[day] = row
    return result


def load_foreign() -> dict[str, dict[str, list[tuple[object, object, object]]]]:
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT report_date, symbol, direction, bps_change, streak_days FROM mkk_yabanci",
        con,
    )
    con.close()
    result: dict[str, dict[str, list[tuple[object, object, object]]]] = {}
    if df.empty:
        return result
    df["symbol"] = df["symbol"].map(clean_symbol)
    df["report_date"] = df["report_date"].map(parse_date)
    for row in df.itertuples(index=False):
        result.setdefault(row.symbol, {}).setdefault(row.report_date, []).append(
            (row.direction, row.bps_change, row.streak_days)
        )
    return result


def ord_value(column: str, value: object) -> float | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if column == "f_rel_obv_state":
        return REL_MAP.get(str(value))
    if column == "f_udvr_state":
        return UDV_MAP.get(str(value))
    if column in {"f_force_index_dual", "f_cmf_dual"}:
        return FLOW_MAP.get(str(value))
    if column == "f_mfi_dual":
        return MFI_MAP.get(str(value))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def bool_change(a: object, b: object) -> int | None:
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return None
    return int(float(b) > float(a))


def calendar_from_xu(xu: pd.DataFrame) -> list[str]:
    dates = [str(x.date()) for x in xu.index]
    # Günlük parquet kapanışa kadar gelecekteki T+3/T+4 günlerini içermez.
    # Aktif event'in planlanan karar/giriş tarihini bulabilmek için takvimi
    # yalnızca ileriye doğru uzatırız; bu günlere fiyat verisi varmış gibi davranmayız.
    try:
        from bist_calendar import is_trading_day
    except Exception:
        is_trading_day = lambda value: value.weekday() < 5
    if dates:
        cursor = pd.Timestamp(dates[-1]).date() + timedelta(days=1)
        for _ in range(60):
            if is_trading_day(cursor):
                dates.append(str(cursor))
            cursor += timedelta(days=1)
    return dates


def date_at(calendar: list[str], start: str, offset: int) -> str | None:
    try:
        pos = calendar.index(start)
    except ValueError:
        return None
    target = pos + offset
    return calendar[target] if 0 <= target < len(calendar) else None


def previous_date(index: pd.DatetimeIndex, day: str) -> str | None:
    days = [str(x.date()) for x in index]
    try:
        pos = days.index(day)
    except ValueError:
        return None
    return days[pos - 1] if pos > 0 else None


def confirmed_swing_lows(
    low_series: pd.Series,
    *,
    left: int = SWING_LEFT_BARS,
    right: int = SWING_RIGHT_BARS,
    lookback: int = SWING_LOOKBACK_BARS,
) -> list[dict[str, object]]:
    """Kapanmış sağ komşularla doğrulanmış salınım diplerini bulur.

    Bir dip, iki sol ve iki sağ bardaki Low değerlerinin tamamından kesinlikle
    düşükse pivot kabul edilir. Son ``right`` bar, henüz sağ komşu olmadığı
    için hiçbir zaman pivot olarak kullanılmaz; böylece as-of tarihinde ileri
    bilgi sızmaz. Eşit dipler tek bir salınım dibi diye zorla birleştirilmez.
    """
    if left < 1 or right < 1 or lookback < left + right + 1:
        return []
    if not isinstance(low_series, pd.Series):
        low_series = pd.Series(low_series)
    values = pd.to_numeric(low_series, errors="coerce").dropna()
    if len(values) < left + right + 1:
        return []
    values = values.iloc[-lookback:]
    array = values.to_numpy(dtype=float)
    lows: list[dict[str, object]] = []
    start = left
    stop = len(array) - right
    for position in range(start, stop):
        value = array[position]
        if not np.isfinite(value):
            continue
        neighbours = np.concatenate((
            array[position - left:position],
            array[position + 1:position + right + 1],
        ))
        if not np.isfinite(neighbours).all() or not value < float(neighbours.min()):
            continue
        pivot_date = pd.Timestamp(values.index[position])
        confirmation_date = pd.Timestamp(values.index[position + right])
        lows.append({
            "position": position,
            "date": pivot_date.date().isoformat(),
            "confirmation_date": confirmation_date.date().isoformat(),
            "low": float(value),
        })
    return lows


def higher_low_state(
    daily: pd.DataFrame,
    as_of: object = None,
    *,
    lookback: int = SWING_LOOKBACK_BARS,
    left: int = SWING_LEFT_BARS,
    right: int = SWING_RIGHT_BARS,
) -> dict[str, object]:
    """Son iki doğrulanmış salınım dibinin yapısını raporlar.

    Bu yalnızca gözlem alanıdır; tarama puanına, vade kararına veya eylem
    hükmüne bağlanmaz. ``as_of`` kesimi, son sağ komşu oluşmadan pivotun
    kullanılmasını engeller.
    """
    empty: dict[str, object] = {
        "state": "insufficient",
        "confirmed": False,
        "pivot_count": 0,
        "previous_low": None,
        "latest_low": None,
        "delta_pct": None,
        "previous_pivot_date": None,
        "latest_pivot_date": None,
        "latest_confirmation_date": None,
    }
    if not isinstance(daily, pd.DataFrame) or "Low" not in daily.columns:
        return empty
    frame = daily.loc[:, ["Low"]].copy()
    index = pd.to_datetime(frame.index, errors="coerce")
    valid_index = ~index.isna()
    frame = frame.loc[valid_index].copy()
    frame.index = index[valid_index]
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    if as_of is not None:
        cutoff = pd.to_datetime(as_of, errors="coerce")
        if pd.isna(cutoff):
            return empty
        cutoff = pd.Timestamp(cutoff)
        if cutoff.tzinfo is not None:
            cutoff = cutoff.tz_localize(None)
        frame = frame.loc[frame.index <= cutoff]
    pivots = confirmed_swing_lows(
        frame["Low"], left=left, right=right, lookback=lookback,
    )
    result = dict(empty)
    result["pivot_count"] = len(pivots)
    if len(pivots) < 2:
        return result
    previous, latest = pivots[-2], pivots[-1]
    previous_low = float(previous["low"])
    latest_low = float(latest["low"])
    delta_pct = (latest_low / previous_low - 1.0) * 100.0 if previous_low > 0 else None
    if delta_pct is None:
        state = "insufficient"
    elif delta_pct > 0:
        state = "higher_low"
    elif delta_pct < 0:
        state = "lower_low"
    else:
        state = "flat_low"
    result.update({
        "state": state,
        "confirmed": state == "higher_low",
        "previous_low": previous_low,
        "latest_low": latest_low,
        "delta_pct": float(delta_pct) if delta_pct is not None else None,
        "previous_pivot_date": previous["date"],
        "latest_pivot_date": latest["date"],
        "latest_confirmation_date": latest["confirmation_date"],
    })
    return result


def latest_hour_before(df: pd.DataFrame, cutoff: pd.Timestamp) -> tuple[pd.Timestamp, pd.Series] | None:
    part = df[df.index <= cutoff]
    if part.empty:
        return None
    return part.index[-1], part.iloc[-1]


def scan_state(event_rows: pd.DataFrame, t0: str, asof: str) -> dict[str, object]:
    win = event_rows[(event_rows["scan_date"] >= t0) & (event_rows["scan_date"] <= asof)]
    days = sorted(win["scan_date"].unique())
    if not days:
        days = [t0]
    t0_types = set(win.loc[win["scan_date"] == t0, "scan_type"]) - NOISE_SCAN_TYPES
    last_types = set(win.loc[win["scan_date"] == days[-1], "scan_type"]) - NOISE_SCAN_TYPES
    new_scan_type_count = len(last_types - t0_types)
    repeat_scan_days = len([d for d in days if d != t0])
    genislik = new_scan_type_count > 0
    israr = repeat_scan_days >= 2
    return {
        "scan_days_seen": len(days),
        "last_scan_date": days[-1],
        "scan_types_t0": len(t0_types),
        "scan_types_last": len(last_types),
        "new_scan_type_count": new_scan_type_count,
        "repeat_scan_days": repeat_scan_days,
        "genislik": int(genislik),
        "new_joined": int(genislik),
        "israr": int(israr),
    }


def stored_state(
    stored: dict[str, dict[str, dict[str, object]]],
    symbol: str,
    t0: str,
    last_scan: str,
) -> dict[str, int | None]:
    start = stored.get(symbol, {}).get(t0)
    last = stored.get(symbol, {}).get(last_scan)
    out: dict[str, int | None] = {}
    for col in STORED_FEATURES:
        a = ord_value(col, start.get(col)) if start else None
        b = ord_value(col, last.get(col)) if last else None
        out[col] = bool_change(a, b)
    out["struct_tact"] = (
        None if out["f_smart_structural_score"] is None and out["f_smart_tactical_score"] is None
        else int(bool(out["f_smart_structural_score"]) or bool(out["f_smart_tactical_score"]))
    )
    out["breakout"] = None
    if last and last.get("f_breakout_state") is not None:
        value = ord_value("f_breakout_state", last.get("f_breakout_state"))
        out["breakout"] = int(value > 0) if value is not None else None
    return out


def price_state(
    symbol_daily: pd.DataFrame,
    xu_daily: pd.DataFrame,
    t0: str,
    snapshot_date: str,
    *,
    current_price: float | None = None,
    previous_daily_date: str | None = None,
) -> dict[str, object]:
    p = indicators(symbol_daily)
    if t0 not in p.index or snapshot_date not in p.index:
        return {"error": "daily_date_missing"}
    t0row = p.loc[t0]
    row = p.loc[snapshot_date]
    close_now = float(current_price) if current_price is not None else float(row["Close"])
    prev_day = previous_daily_date or previous_date(p.index, snapshot_date)
    prev_close = float(p.loc[prev_day, "Close"]) if prev_day and prev_day in p.index else np.nan
    t0_xu = float(xu_daily.loc[t0, "Close"]) if t0 in xu_daily.index else np.nan
    now_xu = float(xu_daily.loc[snapshot_date, "Close"]) if snapshot_date in xu_daily.index else np.nan
    rs0 = float(t0row["Close"]) / t0_xu if t0_xu > 0 else np.nan
    rs_now = close_now / now_xu if now_xu > 0 else np.nan
    atr = float(row["atr"]) if pd.notna(row["atr"]) else np.nan
    ma20 = float(row["sma20"]) if pd.notna(row["sma20"]) else np.nan
    atr_move = ((close_now - prev_close) / atr > 0.5) if np.isfinite(atr) and atr > 0 and np.isfinite(prev_close) else None
    return {
        "price": close_now,
        "rsi_up": bool_change(t0row["rsi"], row["rsi"]),
        "rs_up": int(np.isfinite(rs0) and np.isfinite(rs_now) and rs_now > rs0),
        "obv_up": bool_change(t0row["obv"], row["obv"]),
        "cmf_up": bool_change(t0row["cmf"], row["cmf"]),
        "mfi_up": bool_change(t0row["mfi"], row["mfi"]),
        "volr_up": bool_change(t0row["volr"], row["volr"]),
        "ma20": int(np.isfinite(ma20) and close_now > ma20),
        "atr_move": None if atr_move is None else int(atr_move),
        "sma20": ma20,
        "atr": atr,
        "rs_start": rs0,
        "rs_now": rs_now,
    }


def intraday_price_state(
    symbol: str,
    xu_symbol: str,
    t0: str,
    cutoff: pd.Timestamp,
    symbol_daily: pd.DataFrame,
    xu_daily: pd.DataFrame,
) -> dict[str, object]:
    h = read_frame(hourly_path(symbol), hourly=True)
    xh = read_frame(hourly_path(xu_symbol), hourly=True)
    if h is None or xh is None:
        return {"error": "hourly_missing"}
    hrow = latest_hour_before(h, cutoff)
    xrow = latest_hour_before(xh, cutoff)
    if hrow is None or xrow is None:
        return {"error": "hourly_cutoff_missing"}
    htime, current = hrow
    xtime, xcurrent = xrow
    current_day = str(htime.date())
    prior = symbol_daily[symbol_daily.index < current_day]
    xprior = xu_daily[xu_daily.index < current_day]
    if t0 not in symbol_daily.index or prior.empty or xprior.empty:
        return {"error": "intraday_daily_base_missing"}
    base = indicators(prior)
    xu_base = indicators(xprior)
    prev = base.iloc[-1]
    t0row = indicators(symbol_daily.loc[:t0]).iloc[-1]
    t0_xu = float(xu_daily.loc[t0, "Close"]) if t0 in xu_daily.index else np.nan
    rs0 = float(t0row["Close"]) / t0_xu if t0_xu > 0 else np.nan
    x_price = float(xcurrent["Close"])
    price = float(current["Close"])
    rs_now = price / x_price if x_price > 0 else np.nan
    atr = float(prev["atr"]) if pd.notna(prev["atr"]) else np.nan
    prev_close = float(prev["Close"])
    atr_move = ((price - prev_close) / atr > 0.5) if np.isfinite(atr) and atr > 0 else None
    ma20 = float(prev["sma20"]) if pd.notna(prev["sma20"]) else np.nan
    return {
        "price": price,
        "rsi_up": None,
        "rs_up": int(np.isfinite(rs0) and np.isfinite(rs_now) and rs_now > rs0),
        "obv_up": None, "cmf_up": None, "mfi_up": None, "volr_up": None,
        "ma20": int(np.isfinite(ma20) and price > ma20),
        "atr_move": None if atr_move is None else int(atr_move),
        "sma20": ma20, "atr": atr, "rs_start": rs0, "rs_now": rs_now,
        "hourly_timestamp": htime.isoformat(),
        "benchmark_hourly_timestamp": xtime.isoformat(),
        "intraday_date": current_day,
    }


def make_snapshot(
    event_rows: pd.DataFrame,
    symbol: str,
    t0: str,
    snapshot_date: str,
    calendar: list[str],
    stored: dict[str, dict[str, dict[str, object]]],
    foreign: dict[str, dict[str, list[tuple[object, object, object]]]],
    xu_daily: pd.DataFrame,
    *,
    mode: str,
    cutoff: pd.Timestamp | None = None,
) -> dict[str, object] | None:
    try:
        t0pos = calendar.index(t0)
        snap_pos = calendar.index(snapshot_date)
    except ValueError:
        return None
    event_day = snap_pos - t0pos
    if event_day < 0 or event_day > FOLLOW_DAYS:
        return None
    D = date_at(calendar, t0, FOLLOW_DAYS)
    entry_date = date_at(calendar, t0, FOLLOW_DAYS + 1)
    if D is None or entry_date is None:
        return None
    daily = read_frame(daily_path(symbol))
    if daily is None:
        return None
    if mode == "close":
        if snapshot_date not in daily.index or snapshot_date not in xu_daily.index:
            return None
        state = price_state(daily, xu_daily, t0, snapshot_date)
        snapshot_at = f"{snapshot_date}T18:30:00+03:00"
        source = "live_close"
        actual_snapshot_date = snapshot_date
    else:
        if cutoff is None:
            cutoff = now_istanbul()
        state = intraday_price_state(symbol, "XU100", t0, cutoff, daily, xu_daily)
        if state.get("error"):
            return None
        actual_snapshot_date = str(state.get("intraday_date"))
        try:
            actual_pos = calendar.index(actual_snapshot_date)
        except ValueError:
            actual_pos = snap_pos
        event_day = actual_pos - t0pos
        if event_day < 1 or event_day > FOLLOW_DAYS:
            return None
        snapshot_at = str(state.get("hourly_timestamp"))
        source = "live_intraday"
    if state.get("error"):
        return None
    structure = higher_low_state(daily, actual_snapshot_date)
    scans = event_rows[
        (event_rows["symbol"] == symbol)
        & (event_rows["event_start_date"] == t0)
    ]
    sstate = scan_state(scans, t0, actual_snapshot_date)
    last_scan = str(sstate["last_scan_date"])
    stored_state_row = stored_state(stored, symbol, t0, last_scan)
    price_bool = {
        "rsi": state.get("rsi_up"), "rs": state.get("rs_up"),
        "obv": state.get("obv_up"), "cmf": state.get("cmf_up"),
        "mfi": state.get("mfi_up"), "volr": state.get("volr_up"),
        "ma20": state.get("ma20"), "atr_move": state.get("atr_move"),
    }
    comp: dict[str, object] = dict(price_bool)
    comp.update({
        "sm_score": stored_state_row.get("f_smart_money_score"),
        "struct_tact": stored_state_row.get("struct_tact"),
        "rel_obv": stored_state_row.get("f_rel_obv_state"),
        "udvr": stored_state_row.get("f_udvr_state"),
        "fidx": stored_state_row.get("f_force_index_dual"),
        "mfi_dual": stored_state_row.get("f_mfi_dual"),
        "spike": stored_state_row.get("f_spike_dominance"),
        "breakout": stored_state_row.get("breakout"),
        "master": stored_state_row.get("f_master_score"),
        "israr": sstate["israr"], "genislik": sstate["genislik"],
    })
    ym = foreign.get(symbol, {})
    yrows = [item for day, items in ym.items() if t0 <= day <= actual_snapshot_date for item in items]
    comp["yab_in"] = int(any(str(direction).lower() == "in" for direction, _, _ in yrows)) if yrows else None
    streaks = [streak for _, _, streak in yrows if streak is not None]
    comp["yab_streak"] = int(max(streaks) >= 2) if streaks else None
    bps = [float(value) for _, value, _ in yrows if value is not None]
    comp["yab_bps"] = int(max(bps) > 0) if bps else None
    v1_keys = ["genislik", "new_joined", "israr", "rsi", "rs"]
    v1 = sum(int(bool(comp.get(k))) for k in v1_keys)
    v2 = sum(int(bool(comp.get(k))) for k in ["israr", "rsi", "rs"])
    available = [k for k in COMPONENTS if comp.get(k) is not None]
    grown = [k for k in available if bool(comp.get(k))]
    v3 = len(grown) / len(available) if available else None
    core_available = [k for k in ["rs", "ma20", "atr_move"] if comp.get(k) is not None]
    core = sum(int(bool(comp.get(k))) for k in ["rs", "ma20", "atr_move"])
    t0_close = None
    try:
        t0_close = float(daily.loc[pd.Timestamp(t0), "Close"])
    except (KeyError, TypeError, ValueError):
        pass
    price = float(state.get("price"))
    atr_value = state.get("atr")
    move_since_t0_pct = ((price / t0_close) - 1) * 100 if t0_close and t0_close > 0 else None
    move_since_t0_atr = (
        (price - t0_close) / float(atr_value)
        if t0_close is not None and atr_value is not None and np.isfinite(atr_value) and float(atr_value) > 0
        else None
    )
    price_positive_count = sum(
        int(bool(comp.get(key))) for key in ["rsi", "rs", "ma20", "atr_move"]
        if comp.get(key) is not None
    )
    return {
        "snapshot_key": f"{symbol}|{t0}|{mode}|{actual_snapshot_date}|{snapshot_at}",
        "snapshot_mode": mode,
        "feature_source": source,
        "snapshot_at": snapshot_at,
        "snapshot_date": actual_snapshot_date,
        "symbol": symbol,
        "event_start_date": t0,
        "event_day": event_day,
        "decision_date": D,
        "planned_entry_date": entry_date,
        "scan_days_seen": sstate["scan_days_seen"],
        "last_scan_date": last_scan,
        "scan_types_t0": sstate["scan_types_t0"],
        "scan_types_last": sstate["scan_types_last"],
        "new_scan_type_count": sstate["new_scan_type_count"],
        "repeat_scan_days": sstate["repeat_scan_days"],
        "price": round(price, 6),
        "t0_close": round(t0_close, 6) if t0_close is not None else None,
        "move_since_t0_pct": round(move_since_t0_pct, 4) if move_since_t0_pct is not None else None,
        "atr_value": round(float(atr_value), 6) if atr_value is not None and np.isfinite(atr_value) else None,
        "move_since_t0_atr": round(move_since_t0_atr, 4) if move_since_t0_atr is not None else None,
        "sma20_value": round(float(state.get("sma20")), 6) if pd.notna(state.get("sma20")) else None,
        "rs_start": round(float(state.get("rs_start")), 8) if pd.notna(state.get("rs_start")) else None,
        "rs_now": round(float(state.get("rs_now")), 8) if pd.notna(state.get("rs_now")) else None,
        # Yüksek dip yalnızca iki sağ komşusu kapanmış gerçek pivotlardan
        # okunur; şimdilik UI rozeti veya tarama kararı değildir.
        "higher_low_state": structure["state"],
        "higher_low_confirmed": int(bool(structure["confirmed"])),
        "higher_low_pivot_count": structure["pivot_count"],
        "higher_low_previous": structure["previous_low"],
        "higher_low_latest": structure["latest_low"],
        "higher_low_delta_pct": round(float(structure["delta_pct"]), 4)
        if structure["delta_pct"] is not None else None,
        "higher_low_previous_date": structure["previous_pivot_date"],
        "higher_low_latest_date": structure["latest_pivot_date"],
        "higher_low_confirmation_date": structure["latest_confirmation_date"],
        "price_positive_count": price_positive_count,
        "rs_up": comp["rs"], "ma20": comp["ma20"], "atr_move": comp["atr_move"],
        "rsi_up": comp["rsi"], "israr": comp["israr"], "genislik": comp["genislik"],
        "cur_core": core,
        "cur_core_available": len(core_available),
        "trajectory_v1": v1,
        "trajectory_v2": v2,
        "trajectory_v3": round(v3, 4) if v3 is not None else None,
        "v3_available": len(available),
        "breakout": comp["breakout"], "yab_in": comp["yab_in"],
        "yab_streak": comp["yab_streak"], "yab_bps": comp["yab_bps"],
        "hourly_timestamp": state.get("hourly_timestamp"),
        "benchmark_hourly_timestamp": state.get("benchmark_hourly_timestamp"),
        "status": ("new" if event_day == 0
                   else "decision_ready" if event_day == FOLLOW_DAYS
                   else "tracking"),
    }


def append_upsert(path: Path, rows: list[dict[str, object]], key: str) -> int:
    if not rows:
        return 0
    new = pd.DataFrame(rows)
    old = pd.read_csv(path) if path.exists() else pd.DataFrame()
    all_rows = new if old.empty else pd.concat([old, new], ignore_index=True, sort=False)
    all_rows = all_rows.drop_duplicates(subset=[key], keep="last")
    all_rows.to_csv(path, index=False, encoding="utf-8-sig")
    return len(new)


def prune_pre_forward_outputs(valid_event_keys: set[str] | None = None) -> None:
    """İlk canlı Master Scan tarihinden önceki forward kayıtlarını ayırır."""
    if SNAPSHOT_OUT.exists():
        old = pd.read_csv(SNAPSHOT_OUT)
        if "snapshot_date" in old.columns:
            old = old[old["snapshot_date"].astype(str) >= FORWARD_START_DATE]
        if valid_event_keys is not None and {"symbol", "event_start_date"}.issubset(old.columns):
            keys = old["symbol"].astype(str) + "|" + old["event_start_date"].astype(str)
            old = old[keys.isin(valid_event_keys)]
            old.to_csv(SNAPSHOT_OUT, index=False, encoding="utf-8-sig")
    if OUTCOME_OUT.exists():
        old = pd.read_csv(OUTCOME_OUT)
        if "entry_date" in old.columns:
            old = old[old["entry_date"].astype(str) >= FORWARD_START_DATE]
        if valid_event_keys is not None and {"symbol", "event_start_date"}.issubset(old.columns):
            keys = old["symbol"].astype(str) + "|" + old["event_start_date"].astype(str)
            old = old[keys.isin(valid_event_keys)]
            old.to_csv(OUTCOME_OUT, index=False, encoding="utf-8-sig")


def collect_snapshots(
    mode: str,
    requested_date: str | None,
    limit: int | None,
    write_output: bool = True,
) -> dict[str, object]:
    xu_daily = read_frame(DAILY_DIR / "XU100.IS_1d.parquet")
    if xu_daily is None or xu_daily.empty:
        raise RuntimeError("XU100 günlük parquet okunamadı")
    raw_calendar = [str(x.date()) for x in xu_daily.index]
    calendar = calendar_from_xu(xu_daily)
    events = load_events()
    stored = load_stored_features()
    foreign = load_foreign()
    if events.empty:
        return {"mode": mode, "events": 0, "snapshots": 0}
    if mode == "close":
        requested = requested_date or raw_calendar[-1]
        eligible_dates = [d for d in raw_calendar if d <= requested]
        if not eligible_dates:
            raise RuntimeError("close için uygun günlük tarih yok")
        snapshot_date = eligible_dates[-1]
        cutoff = None
    else:
        cutoff = pd.Timestamp(requested_date, tz="Europe/Istanbul") if requested_date else now_istanbul()
        xh = read_frame(hourly_path("XU100"), hourly=True)
        if xh is None or xh.empty:
            raise RuntimeError("XU100 saatlik parquet yok veya okunamadı")
        latest = latest_hour_before(xh, cutoff)
        if latest is None:
            raise RuntimeError("XU100 saatlik parquet'te cutoff öncesi bar yok")
        snapshot_date = str(latest[0].date())
        if snapshot_date not in calendar:
            calendar = sorted(set(calendar + [snapshot_date]))
    rows: list[dict[str, object]] = []
    grouped = events.groupby(["symbol", "event_start_date"], sort=True)
    for i, ((symbol, t0), group) in enumerate(grouped):
        if limit is not None and i >= limit:
            break
        row = make_snapshot(
            events, symbol, t0, snapshot_date, calendar, stored, foreign,
            xu_daily, mode=mode, cutoff=cutoff,
        )
        if row is not None:
            rows.append(row)
    if write_output:
        valid_keys = {f"{symbol}|{t0}" for symbol, t0 in grouped.groups}
        prune_pre_forward_outputs(valid_keys)
    count = append_upsert(SNAPSHOT_OUT, rows, "snapshot_key") if write_output else 0
    return {
        "mode": mode, "asof": snapshot_date, "events_seen": int(events.groupby(["symbol", "event_start_date"]).ngroups),
        "snapshots_found": len(rows), "snapshots_written": count, "output": str(SNAPSHOT_OUT),
    }


def settle_outcomes(
    requested_date: str | None,
    limit: int | None,
    write_output: bool = True,
) -> dict[str, object]:
    xu = read_frame(DAILY_DIR / "XU100.IS_1d.parquet")
    if xu is None or xu.empty:
        raise RuntimeError("XU100 günlük parquet okunamadı")
    calendar = calendar_from_xu(xu)
    latest_allowed = requested_date or str(xu.index[-1].date())
    events = load_events()
    rows: list[dict[str, object]] = []
    grouped = events.groupby(["symbol", "event_start_date"], sort=True)
    for i, ((symbol, t0), _) in enumerate(grouped):
        if limit is not None and i >= limit:
            break
        try:
            t0pos = calendar.index(t0)
        except ValueError:
            continue
        entry_date = date_at(calendar, t0, FOLLOW_DAYS + 1)
        if entry_date is None or entry_date > latest_allowed:
            continue
        if entry_date < FORWARD_START_DATE:
            continue
        daily = read_frame(daily_path(symbol))
        if daily is None or entry_date not in daily.index:
            continue
        entry = float(daily.loc[entry_date, "Open"])
        if entry <= 0:
            continue
        xu_entry = float(xu.loc[entry_date, "Open"]) if entry_date in xu.index else np.nan
        row: dict[str, object] = {
            "outcome_key": f"{symbol}|{t0}", "symbol": symbol, "event_start_date": t0,
            "decision_date": date_at(calendar, t0, FOLLOW_DAYS),
            "entry_date": entry_date, "entry_price": round(entry, 6),
            "feature_source": "live_close",
        }
        # Her vade, aynı T+4 açılışından sonra ayrı olgunlaşır. Böylece 20 günü
        # beklemeden 5/10 günlük sicil oluşur; 20 günlük sağ-kuyruk ölçüsü ise
        # kendi olgunluğu gelmeden asla doldurulmaz.
        for horizon in OUTCOME_HORIZONS:
            exit_date = date_at(calendar, t0, FOLLOW_DAYS + 1 + horizon)
            mature = bool(
                exit_date
                and exit_date <= latest_allowed
                and exit_date in daily.index
                and exit_date in xu.index
            )
            row[f"outcome_{horizon}_mature"] = int(mature)
            if not mature:
                continue
            segment = daily.loc[entry_date:exit_date]
            if segment.empty:
                row[f"outcome_{horizon}_mature"] = 0
                continue
            xu_exit = float(xu.loc[exit_date, "Close"])
            postret = (float(segment["Close"].iloc[-1]) / entry - 1) * 100
            mfe = (float(segment["High"].max()) / entry - 1) * 100
            mae = (float(segment["Low"].min()) / entry - 1) * 100
            close_mfe = (float(segment["Close"].max()) / entry - 1) * 100
            xu_ret = ((xu_exit / xu_entry) - 1) * 100 if xu_entry > 0 and np.isfinite(xu_exit) else np.nan
            row.update({
                f"exit_date_{horizon}": exit_date,
                f"postret_{horizon}": round(postret, 4),
                f"alpha_xu100_{horizon}": round(postret - xu_ret, 4) if np.isfinite(xu_ret) else None,
                f"xu100_return_{horizon}": round(xu_ret, 4) if np.isfinite(xu_ret) else None,
                f"mfe_{horizon}": round(mfe, 4),
                f"mae_{horizon}": round(mae, 4),
                f"close_mfe_{horizon}": round(close_mfe, 4),
                f"hit30_hi_{horizon}": int(mfe >= TARGET_PCT),
                f"hit30_close_{horizon}": int(close_mfe >= TARGET_PCT),
                f"clean30_{horizon}": int(mfe >= TARGET_PCT and mae > -15.0),
            })
            # Eski sağ-kuyruk raporunun sözleşmesini koru: bu alanlar yalnızca
            # gerçek 20 günlük pencere olgunlaştığında dolar.
            if horizon == HOLD_DAYS:
                row.update({
                    "exit_date": exit_date,
                    "postret": round(postret, 4),
                    "alpha_xu100": round(postret - xu_ret, 4) if np.isfinite(xu_ret) else None,
                    "mfe": round(mfe, 4), "mae": round(mae, 4), "close_mfe": round(close_mfe, 4),
                    "hit30_hi": int(mfe >= TARGET_PCT), "hit30_close": int(close_mfe >= TARGET_PCT),
                    "clean30": int(mfe >= TARGET_PCT and mae > -15.0),
                })
        rows.append(row)
    if write_output:
        valid_keys = {f"{symbol}|{t0}" for symbol, t0 in grouped.groups}
        prune_pre_forward_outputs(valid_keys)
    count = append_upsert(OUTCOME_OUT, rows, "outcome_key") if write_output else 0
    return {
        "mode": "settle", "outcomes_found": len(rows),
        "outcomes_written": count, "output": str(OUTCOME_OUT),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="T+3 trajectory forward collector (salt-okur)")
    parser.add_argument("--mode", choices=["close", "intraday", "settle"], default="close")
    parser.add_argument("--date", help="As-of date YYYY-MM-DD; intraday için cutoff tarihi")
    parser.add_argument("--limit", type=int, help="İlk N event ile sınırlama; test için")
    parser.add_argument("--dry-run", action="store_true", help="Hesapla, çıktı dosyasına yazma")
    args = parser.parse_args()
    if args.mode == "settle":
        result = settle_outcomes(args.date, args.limit, write_output=not args.dry_run)
    else:
        result = collect_snapshots(args.mode, args.date, args.limit, write_output=not args.dry_run)
    result["run_at"] = now_istanbul().isoformat()
    result["dry_run"] = bool(args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.dry_run:
        RUN_OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
