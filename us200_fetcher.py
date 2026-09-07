# -*- coding: utf-8 -*-
"""S&P 500 içindeki ilk 200 hisse için kontrollü Yahoo parquet kasası.

VPS bu modülle günlük ve saatlik ham kasayı besler. Dört saatlik mumlar saatlik
kasadan, New York normal seansına çapalı olarak üretilir. Eski sağlam dosya boş,
kısa veya bozuk sağlayıcı cevabıyla ezilmez.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from provider_traffic import (
    ProviderCooldown,
    acquire_slot,
    record_failure,
    record_success,
)
from us200_universe import US200_AS_OF, US200_CATEGORY, US200_SOURCE, US200_TICKERS


ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get("SMR_US_DATA_ROOT", ROOT / "us200_data"))
DAILY_DIR = DATA_ROOT / "daily"
HOURLY_DIR = DATA_ROOT / "hourly"
FOUR_HOUR_DIR = DATA_ROOT / "four_hour"
MANIFEST_DIR = DATA_ROOT / "manifests"
NEW_YORK = ZoneInfo("America/New_York")
OHLCV = ("Open", "High", "Low", "Close", "Volume")
BENCHMARK_SYMBOL = "^GSPC"
SINGLE_REFRESH_LOCK = DATA_ROOT / ".single_refresh.lock"
SINGLE_REFRESH_STATE = MANIFEST_DIR / "single_refresh_state.json"


class RateLimited(RuntimeError):
    """Yahoo bu turu durdurmamızı istediğinde kalan sembolleri korur."""


def ensure_directories() -> None:
    for path in (DAILY_DIR, HOURLY_DIR, FOUR_HOUR_DIR, MANIFEST_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _single_level_columns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        level0 = frame.columns.get_level_values(0)
        frame.columns = level0 if "Close" in level0 else frame.columns.get_level_values(1)
    frame = frame.loc[:, ~frame.columns.duplicated()].copy()
    frame.columns = [str(column).strip().title() for column in frame.columns]
    return frame


def clean_ohlcv(frame: pd.DataFrame | None, *, interval: str) -> pd.DataFrame:
    """Sağlayıcı cevabını tek biçime getirir ve fiziksel olarak bozuk barı reddeder."""
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame(columns=OHLCV)
    frame = _single_level_columns(frame)
    if not set(OHLCV).issubset(frame.columns):
        return pd.DataFrame(columns=OHLCV)
    frame = frame.loc[:, list(OHLCV)].copy()
    for column in OHLCV:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    valid = (
        (frame[["Open", "High", "Low", "Close"]] > 0).all(axis=1)
        & (frame["High"] >= frame[["Open", "Close", "Low"]].max(axis=1))
        & (frame["Low"] <= frame[["Open", "Close", "High"]].min(axis=1))
        & frame["Volume"].fillna(-1).ge(0)
    )
    frame = frame.loc[valid].copy()
    frame["Volume"] = frame["Volume"].fillna(0).astype("float64")
    for column in ("Open", "High", "Low", "Close"):
        frame[column] = frame[column].astype("float64")

    index = pd.DatetimeIndex(pd.to_datetime(frame.index))
    if interval == "1d":
        if index.tz is not None:
            index = index.tz_convert(NEW_YORK).tz_localize(None)
        frame.index = index.normalize()
    else:
        if index.tz is None:
            index = index.tz_localize(NEW_YORK)
        else:
            index = index.tz_convert(NEW_YORK)
        frame.index = index
        minutes = frame.index.hour * 60 + frame.index.minute
        frame = frame[(minutes >= 9 * 60 + 30) & (minutes < 16 * 60)]
    frame.index.name = "Date"
    return frame


def _download(symbol: str, *, interval: str, period: str | None = None,
              start: str | None = None, end: str | None = None) -> pd.DataFrame:
    acquire_slot("yahoo", max_wait=60, priority="regular_fetch")
    kwargs: dict[str, Any] = {
        "tickers": symbol,
        "interval": interval,
        "progress": False,
        "auto_adjust": True,
        "prepost": False,
        "threads": False,
        "actions": False,
        "timeout": 25,
    }
    if period:
        kwargs["period"] = period
    if start:
        kwargs["start"] = start
        kwargs["end"] = end or (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    try:
        try:
            raw = yf.download(multi_level_index=False, **kwargs)
        except TypeError:
            raw = yf.download(**kwargs)
        cleaned = clean_ohlcv(raw, interval=interval)
        if cleaned.empty:
            record_failure("yahoo", error=f"{symbol} {interval}: boş", kind="empty")
        else:
            record_success("yahoo")
        return cleaned
    except Exception as exc:
        text = str(exc)
        limited = "429" in text or "too many requests" in text.lower() or "ratelimit" in type(exc).__name__.lower()
        record_failure(
            "yahoo", status_code=429 if limited else None, error=text,
            retry_after=1800 if limited else None,
            kind="error",
        )
        if limited:
            raise RateLimited(f"Yahoo hız kapısı: {text}") from exc
        raise


def _read(path: Path, *, interval: str) -> pd.DataFrame:
    try:
        return clean_ohlcv(pd.read_parquet(path), interval=interval)
    except Exception:
        return pd.DataFrame(columns=OHLCV)


def _rebase_old_for_split(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Yeni indirme geçmiş fiyatları bölünme nedeniyle yeniden ölçeklediyse eskiyi hizalar."""
    common = old.index.intersection(new.index)
    if len(common) < 2:
        return old
    old_close = old.loc[common, "Close"].replace(0, np.nan)
    ratio = (new.loc[common, "Close"] / old_close).replace([np.inf, -np.inf], np.nan).dropna()
    if ratio.empty:
        return old
    factor = float(ratio.median())
    if not np.isfinite(factor) or factor <= 0 or 0.97 <= factor <= 1.03:
        return old
    dispersion = float((ratio / factor - 1).abs().median())
    if dispersion > 0.01:
        return old
    rebased = old.copy()
    for column in ("Open", "High", "Low", "Close"):
        rebased[column] = rebased[column] * factor
    rebased["Volume"] = rebased["Volume"] / factor
    return rebased


def merge_history(old: pd.DataFrame, new: pd.DataFrame, *, interval: str) -> pd.DataFrame:
    if old.empty:
        return new.copy()
    if new.empty:
        return old.copy()
    old = _rebase_old_for_split(old, new)
    merged = pd.concat([old.loc[~old.index.isin(new.index)], new]).sort_index()
    merged = merged[~merged.index.duplicated(keep="last")]
    return clean_ohlcv(merged, interval=interval)


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    frame.to_parquet(temporary)
    os.replace(temporary, path)


def build_four_hour(hourly: pd.DataFrame, *, now: datetime | None = None) -> pd.DataFrame:
    """New York 09:30 açılışına çapalı iki seans mumu üretir."""
    hourly = clean_ohlcv(hourly, interval="1h")
    if hourly.empty:
        return pd.DataFrame(columns=(*OHLCV, "SourceBars", "BarMinutes"))
    now_ny = now.astimezone(NEW_YORK) if now and now.tzinfo else (now.replace(tzinfo=NEW_YORK) if now else datetime.now(NEW_YORK))
    pieces: list[pd.DataFrame] = []
    for session_date, day in hourly.groupby(hourly.index.date):
        if session_date == now_ny.date() and now_ny.time() < datetime.strptime("16:05", "%H:%M").time():
            continue
        anchor = pd.Timestamp(f"{session_date} 09:30", tz=NEW_YORK)
        bars = day.resample("4h", origin=anchor, label="left", closed="left").agg({
            "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum",
        }).dropna(subset=["Open", "Close"])
        if bars.empty:
            continue
        counts = day["Close"].resample("4h", origin=anchor, label="left", closed="left").count()
        bars["SourceBars"] = counts.reindex(bars.index).fillna(0).astype("int64")
        bars["BarMinutes"] = [240 if stamp.hour == 9 else 150 for stamp in bars.index]
        pieces.append(bars)
    if not pieces:
        return pd.DataFrame(columns=(*OHLCV, "SourceBars", "BarMinutes"))
    result = pd.concat(pieces).sort_index()
    return result[~result.index.duplicated(keep="last")]


def _delay() -> None:
    minimum = max(0.0, float(os.environ.get("US200_MIN_DELAY", "2.5")))
    maximum = max(minimum, float(os.environ.get("US200_MAX_DELAY", "4.0")))
    if maximum:
        time.sleep(random.uniform(minimum, maximum))


def _closed_daily_bars(frame: pd.DataFrame, *, now: datetime | None = None) -> pd.DataFrame:
    """Açık ABD seansının henüz kapanmamış günlük mumunu kasaya sokma."""
    if frame.empty:
        return frame
    now_ny = now.astimezone(NEW_YORK) if now and now.tzinfo else (now.replace(tzinfo=NEW_YORK) if now else datetime.now(NEW_YORK))
    if now_ny.weekday() < 5 and (now_ny.hour, now_ny.minute) < (16, 15):
        return frame[frame.index.normalize() < pd.Timestamp(now_ny.date())].copy()
    return frame


def _history_window(old: pd.DataFrame, *, interval: str, now: datetime | None = None) -> dict[str, str]:
    """Kasadaki boşluğa göre en dar Yahoo isteğini seçer."""
    now_ny = now.astimezone(NEW_YORK) if now and now.tzinfo else (now.replace(tzinfo=NEW_YORK) if now else datetime.now(NEW_YORK))
    history_days = 730 if interval == "1d" else 60
    target_start = pd.Timestamp(now_ny.date() - timedelta(days=history_days)).normalize()
    if old.empty:
        return {"kind": "bootstrap", "period": "2y" if interval == "1d" else "60d"}

    first = pd.Timestamp(old.index[0]).tz_localize(None).normalize()
    last = pd.Timestamp(old.index[-1]).tz_localize(None)
    if first > target_start + pd.Timedelta(days=10):
        return {
            "kind": "head_repair",
            "start": target_start.date().isoformat(),
            "end": (first + pd.Timedelta(days=1)).date().isoformat(),
        }

    if interval == "1d":
        dates = pd.DatetimeIndex(old.index).sort_values()
        gaps = dates.to_series().diff().dropna()
        long_gaps = gaps[gaps > pd.Timedelta(days=10)]
        if not long_gaps.empty:
            gap_end = pd.Timestamp(long_gaps.index[0]).normalize()
            gap_start = (gap_end - long_gaps.iloc[0]).normalize()
            return {
                "kind": "gap_repair",
                "start": (gap_start - pd.Timedelta(days=1)).date().isoformat(),
                "end": (gap_end + pd.Timedelta(days=1)).date().isoformat(),
            }

    return {
        "kind": "tail",
        "start": (last - pd.Timedelta(days=1)).date().isoformat(),
        "end": (now_ny.date() + timedelta(days=1)).isoformat(),
    }


def _window_download(symbol: str, *, interval: str, window: dict[str, str]) -> pd.DataFrame:
    if "period" in window:
        return _download(symbol, interval=interval, period=window["period"])
    return _download(symbol, interval=interval, start=window["start"], end=window["end"])


def refresh_daily(symbol: str, *, full: bool = False) -> dict[str, Any]:
    path = DAILY_DIR / f"{symbol}_1d.parquet"
    old = _read(path, interval="1d") if path.exists() else pd.DataFrame(columns=OHLCV)
    window = {"kind": "full", "period": "2y"} if full else _history_window(old, interval="1d")
    new = _closed_daily_bars(_window_download(symbol, interval="1d", window=window))
    if new.empty:
        return {"ok": False, "rows": len(old), "reason": "boş veya kapanmamış cevap", "mode": window["kind"]}
    merged = merge_history(old, new, interval="1d")
    if len(merged) < len(old):
        return {"ok": False, "rows": len(old), "reason": "tarihçe küçülmesi reddedildi", "mode": window["kind"]}
    _atomic_parquet(merged, path)
    return {
        "ok": True, "rows": len(merged), "last": str(merged.index[-1].date()),
        "mode": window["kind"], "downloaded_rows": len(new),
    }


def refresh_hourly(symbol: str, *, full: bool = False) -> dict[str, Any]:
    path = HOURLY_DIR / f"{symbol}_1h.parquet"
    old = _read(path, interval="1h") if path.exists() else pd.DataFrame(columns=OHLCV)
    window = {"kind": "full", "period": "60d"} if full else _history_window(old, interval="1h")
    new = _window_download(symbol, interval="1h", window=window)
    if new.empty:
        return {"ok": False, "rows": len(old), "reason": "boş cevap", "mode": window["kind"]}
    merged = merge_history(old, new, interval="1h")
    if len(merged) < len(old):
        return {"ok": False, "rows": len(old), "reason": "tarihçe küçülmesi reddedildi", "mode": window["kind"]}
    four_hour = build_four_hour(merged)
    if four_hour.empty:
        return {"ok": False, "rows": len(old), "reason": "4 saatlik üretilemedi", "mode": window["kind"]}
    _atomic_parquet(merged, path)
    _atomic_parquet(four_hour, FOUR_HOUR_DIR / f"{symbol}_4h.parquet")
    return {
        "ok": True, "rows": len(merged), "four_hour_rows": len(four_hour),
        "last": merged.index[-1].isoformat(), "mode": window["kind"],
        "downloaded_rows": len(new),
    }


@contextmanager
def _single_refresh_guard() -> Any:
    """Aynı bilgisayarda iki tekli yenilemenin üst üste yazmasını engeller."""
    SINGLE_REFRESH_LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(str(SINGLE_REFRESH_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        try:
            if time.time() - SINGLE_REFRESH_LOCK.stat().st_mtime > 180:
                SINGLE_REFRESH_LOCK.unlink(missing_ok=True)
                handle = os.open(str(SINGLE_REFRESH_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            else:
                raise RuntimeError("Başka bir tekli veri yenilemesi sürüyor") from exc
        except FileNotFoundError:
            handle = os.open(str(SINGLE_REFRESH_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(handle, f"{os.getpid()} {time.time()}".encode("ascii", "ignore"))
        yield
    finally:
        try:
            os.close(handle)
        except OSError:
            pass
        try:
            SINGLE_REFRESH_LOCK.unlink(missing_ok=True)
        except OSError:
            pass


def refresh_single(symbol: str) -> dict[str, Any]:
    """Kullanıcının açtığı tek hisse için dar aralıklı günlük+saatlik yenileme."""
    symbol = str(symbol or "").strip().upper()
    if symbol not in set(US200_TICKERS):
        return {"ok": False, "reason": "Sembol S&P 200 evreninde değil"}
    ensure_directories()
    with _single_refresh_guard():
        now = time.time()
        try:
            state = json.loads(SINGLE_REFRESH_STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            state = {}
        last_run = float((state.get(symbol) or {}).get("at", 0) or 0)
        if now - last_run < 45:
            wait = int(45 - (now - last_run))
            return {"ok": True, "skipped": True, "reason": f"Bu hisse az önce yenilendi ({wait} sn bekle)"}

        outcome: dict[str, Any] = {"symbol": symbol, "started_at": datetime.now(timezone.utc).isoformat()}
        for label, refresh in (("daily", refresh_daily), ("hourly", refresh_hourly)):
            try:
                outcome[label] = refresh(symbol)
            except (RateLimited, ProviderCooldown, TimeoutError) as exc:
                outcome[label] = {"ok": False, "reason": str(exc)}
            except Exception as exc:
                outcome[label] = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
            if label == "daily":
                _delay()
        outcome["finished_at"] = datetime.now(timezone.utc).isoformat()
        outcome["ok"] = bool(outcome.get("daily", {}).get("ok") or outcome.get("hourly", {}).get("ok"))
        state[symbol] = {"at": now, "result": outcome}
        _atomic_json(state, SINGLE_REFRESH_STATE)
        return outcome


def _atomic_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def health_report() -> dict[str, Any]:
    ensure_directories()
    daily = list(DAILY_DIR.glob("*_1d.parquet"))
    hourly = list(HOURLY_DIR.glob("*_1h.parquet"))
    four_hour = list(FOUR_HOUR_DIR.glob("*_4h.parquet"))
    expected = set(US200_TICKERS)
    present_daily = {path.name.removesuffix("_1d.parquet") for path in daily}
    present_hourly = {path.name.removesuffix("_1h.parquet") for path in hourly}
    present_four = {path.name.removesuffix("_4h.parquet") for path in four_hour}
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "category": US200_CATEGORY,
        "universe_as_of": US200_AS_OF,
        "expected": len(expected),
        "daily_files": len(present_daily & expected),
        "hourly_files": len(present_hourly & expected),
        "four_hour_files": len(present_four & expected),
        "benchmark_daily": (DAILY_DIR / f"{BENCHMARK_SYMBOL}_1d.parquet").exists(),
        "missing_daily": sorted(expected - present_daily),
        "missing_hourly": sorted(expected - present_hourly),
        "missing_four_hour": sorted(expected - present_four),
    }


def run(symbols: list[str], *, mode: str, full: bool = False) -> tuple[int, dict[str, Any]]:
    ensure_directories()
    started = datetime.now(timezone.utc)
    results: dict[str, Any] = {}
    stopped_reason = ""
    if mode in ("daily", "all"):
        try:
            results[BENCHMARK_SYMBOL] = {"daily": refresh_daily(BENCHMARK_SYMBOL, full=full)}
            print(f"[BENCHMARK] {BENCHMARK_SYMBOL}: {results[BENCHMARK_SYMBOL]}", flush=True)
            _delay()
        except (RateLimited, ProviderCooldown) as exc:
            stopped_reason = str(exc)
        except Exception as exc:
            results[BENCHMARK_SYMBOL] = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    if stopped_reason:
        symbols = []
    for position, symbol in enumerate(symbols, start=1):
        item: dict[str, Any] = {}
        try:
            if mode in ("daily", "all"):
                item["daily"] = refresh_daily(symbol, full=full)
                _delay()
            if mode in ("hourly", "all"):
                item["hourly"] = refresh_hourly(symbol, full=full)
                _delay()
            results[symbol] = item
            print(f"[{position:03d}/{len(symbols):03d}] {symbol}: {item}", flush=True)
        except (RateLimited, ProviderCooldown) as exc:
            stopped_reason = str(exc)
            results[symbol] = {"ok": False, "reason": stopped_reason}
            print(f"DURDU: {stopped_reason}", file=sys.stderr, flush=True)
            break
        except Exception as exc:
            results[symbol] = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
            print(f"[{position:03d}/{len(symbols):03d}] {symbol}: HATA {exc}", file=sys.stderr, flush=True)
            _delay()
    finished = datetime.now(timezone.utc)
    payload = {
        "schema": 1,
        "category": US200_CATEGORY,
        "universe_as_of": US200_AS_OF,
        "universe_source": US200_SOURCE,
        "mode": mode,
        "full": full,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 2),
        "requested": len(symbols),
        "processed": len(results),
        "stopped_reason": stopped_reason,
        "results": results,
        "health": health_report(),
    }
    _atomic_json(payload, MANIFEST_DIR / "latest_run.json")
    return (2 if stopped_reason else 0), payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S&P 200 Yahoo parquet kasası")
    parser.add_argument("--mode", choices=("daily", "hourly", "all", "health"), default="all")
    parser.add_argument("--full", action="store_true", help="Günlükte 2y, saatlikte 60g yeniden kontrol")
    parser.add_argument("--limit", type=int, default=0, help="İlk N sembolle güvenli prova")
    parser.add_argument("--symbols", nargs="*", help="Yalnız verilen semboller")
    args = parser.parse_args(argv)
    ensure_directories()
    if args.mode == "health":
        report = health_report()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not report["missing_daily"] and not report["missing_hourly"] else 1
    symbols = [str(symbol).upper() for symbol in (args.symbols or US200_TICKERS)]
    unknown = sorted(set(symbols) - set(US200_TICKERS))
    if unknown:
        parser.error(f"Evren dışında sembol: {', '.join(unknown)}")
    if args.limit > 0:
        symbols = symbols[:args.limit]
    code, _ = run(symbols, mode=args.mode, full=args.full)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
