#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""İş Yatırım için tek, ortak, önbellekli ve hız-sınırlı ağ kapısı."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from provider_traffic import (
    ProviderCooldown, acquire_slot, provider_status,
    record_failure, record_success,
)

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "health" / "isy_cache"
CACHE.mkdir(parents=True, exist_ok=True)
BASE_URL = "https://www.isyatirim.com.tr/_layouts/15/Isyatirim.Website/Common/Data.aspx/HisseTekil"
TR = timezone(timedelta(hours=3))
TRIES = int(os.environ.get("ISY_TRIES", "2"))
SLEEP_BETWEEN = float(os.environ.get("ISY_SLEEP", "1.5"))
CONNECT_TIMEOUT = float(os.environ.get("ISY_CONNECT_TIMEOUT", "4"))
READ_TIMEOUT = float(os.environ.get("ISY_READ_TIMEOUT", "10"))


def _cache_path(symbol: str) -> Path:
    return CACHE / f"{symbol.replace('.IS', '').upper()}.parquet"


def _read_cache(symbol: str, max_age_seconds: float | None = None):
    p = _cache_path(symbol)
    if not p.exists():
        return None
    if max_age_seconds is not None and time.time() - p.stat().st_mtime > max_age_seconds:
        return None
    try:
        return pd.read_parquet(p)
    except Exception:
        return None


def _write_cache(symbol: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    p = _cache_path(symbol)
    tmp = p.with_suffix(p.suffix + f".{os.getpid()}.tmp")
    df.to_parquet(tmp, compression="snappy")
    os.replace(tmp, p)
    meta = p.with_suffix(".json")
    mtmp = meta.with_suffix(f".json.{os.getpid()}.tmp")
    mtmp.write_text(json.dumps({
        "symbol": symbol.replace(".IS", "").upper(),
        "fetched_at": datetime.now(TR).isoformat(),
        "rows": int(len(df)), "first": str(df.index.min())[:10],
        "last": str(df.index.max())[:10],
    }, ensure_ascii=False), encoding="utf-8")
    os.replace(mtmp, meta)


def _has_dates(df, want_dates) -> bool:
    if not want_dates:
        return True
    if df is None or df.empty:
        return False
    present = set(pd.DatetimeIndex(df.index).strftime("%Y-%m-%d"))
    return all(str(d)[:10] in present for d in want_dates)


def _number(raw: pd.DataFrame, name: str, fallback):
    value = raw[name] if name in raw.columns else fallback
    return pd.to_numeric(value, errors="coerce")


def _parse(payload: dict) -> pd.DataFrame | None:
    raw = pd.DataFrame(payload.get("value", []) if isinstance(payload, dict) else [])
    needed = {"HGDG_TARIH", "HGDG_AOF", "HGDG_HACIM", "HGDG_KAPANIS"}
    if raw.empty or not needed.issubset(raw.columns):
        return None
    aof = _number(raw, "HGDG_AOF", 0)
    raw = raw[aof > 0].copy()
    if raw.empty:
        return None
    idx = pd.to_datetime(raw["HGDG_TARIH"], errors="coerce", dayfirst=True)
    close = _number(raw, "HGDG_KAPANIS", 0)
    aof = _number(raw, "HGDG_AOF", 0)
    hacim = _number(raw, "HGDG_HACIM", 0)
    out = pd.DataFrame({
        "Open": _number(raw, "HGDG_ACILIS", close).values,
        "High": _number(raw, "HGDG_MAX", close).values,
        "Low": _number(raw, "HGDG_MIN", close).values,
        "Close": close.values,
        "Volume": (hacim / aof).values,
    }, index=idx)
    out.index.name = "Date"
    if out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    out = out[~out.index.isna()]
    out = out[(out["Close"] > 0) & (out["Volume"] >= 0)]
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out if not out.empty else None


def fetch_once(symbol: str, period_days: int = 20, *, priority: str = "normal",
               max_wait: float = 60.0, start_date: str | None = None,
               end_date: str | None = None) -> pd.DataFrame | None:
    sym = symbol.replace(".IS", "").replace(".is", "").upper()
    if sym.startswith("X"):
        return None
    end_dt = (datetime.now(TR) if end_date is None else
              datetime.strptime(end_date[:10], "%Y-%m-%d").replace(tzinfo=TR))
    start_dt = (end_dt - timedelta(days=period_days) if start_date is None else
                datetime.strptime(start_date[:10], "%Y-%m-%d").replace(tzinfo=TR))
    params = {"hisse": sym, "startdate": start_dt.strftime("%d-%m-%Y"),
              "enddate": end_dt.strftime("%d-%m-%Y")}
    try:
        acquire_slot("isyatirim", max_wait=max_wait, priority=priority)
        response = requests.get(BASE_URL, params=params,
                                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        if response.status_code in (403, 429):
            try:
                retry_after = float(response.headers.get("Retry-After", ""))
            except Exception:
                retry_after = None
            record_failure("isyatirim", status_code=response.status_code,
                           retry_after=retry_after, error=response.text[:200])
            return None
        response.raise_for_status()
        df = _parse(response.json())
        if df is None:
            record_failure("isyatirim", kind="empty", error=f"{sym}: boş/kısmi cevap")
            return None
        record_success("isyatirim")
        return df
    except ProviderCooldown:
        raise
    except requests.Timeout as exc:
        record_failure("isyatirim", kind="timeout", error=str(exc))
    except requests.ConnectionError as exc:
        record_failure("isyatirim", kind="connection", error=str(exc))
    except Exception as exc:
        record_failure("isyatirim", kind="error", error=str(exc))
    return None


def robust_isyatirim(symbol: str, period_days: int = 20, want_dates=None,
                     tries: int = TRIES, allow_stale: bool = True,
                     max_cache_age: float | None = None,
                     priority: str = "normal", max_wait: float = 60.0,
                     start_date: str | None = None, end_date: str | None = None):
    cooldown = False
    for attempt in range(max(1, int(tries))):
        try:
            df = fetch_once(symbol, period_days, priority=priority, max_wait=max_wait,
                            start_date=start_date, end_date=end_date)
        except ProviderCooldown:
            cooldown = True
            break
        if df is not None and _has_dates(df, want_dates):
            try:
                _write_cache(symbol, df)
            except Exception:
                pass
            return df, "canli"
        if attempt < max(1, int(tries)) - 1:
            time.sleep(SLEEP_BETWEEN * (attempt + 1))
    if allow_stale:
        cached = _read_cache(symbol, max_cache_age)
        if cached is not None and _has_dates(cached, want_dates):
            return cached, "cache"
    return None, "cooldown" if cooldown else "yok"


def breaker_status() -> str:
    st = provider_status("isyatirim")
    remaining = float(st.get("cooldown_until", 0.0) or 0.0) - time.time()
    if remaining > 0:
        return f"SİGORTA AÇIK ({remaining:.0f} sn)"
    return f"normal (üst üste hata: {int(st.get('consecutive_failures', 0) or 0)})"
