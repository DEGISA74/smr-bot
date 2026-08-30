#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kapanış sonrası hacmi onaylı BIST sürümüne teklif eder.

İş Yatırım ana kaynaktır; yalnız cevap vermeyen sembollerde borsapy kontrollü
yedek olarak, sadece beklenen son işlem günü için aday üretir. Doğrudan parquet
yazmaz. Kapsama oranı yeterli değilse marker üretmez ve aktif sürüm değişmez.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = Path(os.environ.get("SMR_CACHE_DIR", BASE_DIR / "veriler"))
LOG_DIR = BASE_DIR / "logs"; LOG_DIR.mkdir(exist_ok=True)
MARKER_FILE = CACHE_DIR / ".finalize_marker"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "finalize_volume.log", encoding="utf-8"),
              logging.StreamHandler()])
log = logging.getLogger(__name__)

BORSAPY_MIN_VOLUME_SCALE = float(os.environ.get("SMR_BORSAPY_MIN_VOLUME_SCALE", "0.125"))
BORSAPY_MAX_VOLUME_SCALE = float(os.environ.get("SMR_BORSAPY_MAX_VOLUME_SCALE", "8.0"))


def _tickers() -> list[str]:
    from bist_data_store import load_manifest
    manifest = load_manifest() or {}
    return sorted(s for s in manifest.get("symbols", {})
                  if s.endswith(".IS") and not s.startswith(
                      ("XU", "XB", "XT", "XY", "XK", "XG", "XI", "XUS")))


def _borsapy_volume_scale_guard(active: pd.DataFrame, candidate: pd.DataFrame,
                                target: pd.Timestamp) -> tuple[bool, str]:
    """Borsapy'nin bariz ölçek hatasını yakalar; kaynak doğrulaması iddia etmez."""
    history = pd.to_numeric(
        active.loc[active.index < target, "Volume"], errors="coerce"
    )
    history = history[history > 0].tail(20)
    value = float(candidate.at[target, "Volume"])
    if len(history) < 10 or float(history.median()) <= 0:
        return True, "yeterli geçmiş ölçek referansı yok"
    multiple = value / float(history.median())
    if multiple < BORSAPY_MIN_VOLUME_SCALE or multiple > BORSAPY_MAX_VOLUME_SCALE:
        return False, f"hacim ölçek anomalisi ({multiple:.2f}x geçmiş medyan)"
    return True, f"ölçek kontrolü geçti ({multiple:.2f}x geçmiş medyan)"


def _borsapy_volume_candidate(ticker: str, start_date: str, end_date: str,
                              expected_date: str):
    """İş Yatırım cevap vermezse yalnız hacmi TradingView/borsapy'den aday yap."""
    try:
        from provider_traffic import (ProviderCooldown, acquire_slot,
                                      record_failure, record_success)
        from bist_data_store import read_active
        from borsapy._providers.tradingview import get_tradingview_provider

        active = read_active(ticker)
        if active is None or active.empty or "Close" not in active.columns:
            return ticker, None, "aktif kapanış yok"

        acquire_slot("borsapy", priority="final_volume", max_wait=120)
        frame = get_tradingview_provider().get_history(
            symbol=ticker.replace(".IS", ""), interval="1d",
            start=datetime.strptime(start_date, "%Y-%m-%d"),
            end=datetime.strptime(end_date, "%Y-%m-%d"))
        if frame is None or frame.empty or not {"Close", "Volume"}.issubset(frame.columns):
            record_failure("borsapy", kind="empty", error=f"{ticker}: boş/eksik cevap")
            return ticker, None, "boş/eksik cevap"

        idx = pd.DatetimeIndex([
            (pd.Timestamp(ts).tz_localize(None) if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts)).normalize()
            for ts in frame.index
        ])
        frame = frame.set_axis(idx)
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
        frame["Volume"] = pd.to_numeric(frame["Volume"], errors="coerce")
        common = frame.index.intersection(active.index)
        if common.empty:
            record_failure("borsapy", kind="empty", error=f"{ticker}: aktif günle kesişim yok")
            return ticker, None, "aktif günle kesişim yok"
        target = pd.Timestamp(expected_date).normalize()
        if target not in common:
            record_failure("borsapy", kind="empty", error=f"{ticker}: hedef gün yok ({expected_date})")
            return ticker, None, f"hedef gün yok ({expected_date})"

        aligned = frame.loc[common].copy()
        old_close = pd.to_numeric(active.loc[common, "Close"], errors="coerce")
        close_gap = (aligned["Close"] - old_close).abs() / old_close.abs().replace(0, pd.NA)
        aligned = aligned.loc[close_gap.fillna(999) <= 0.05]
        aligned = aligned[aligned["Volume"].notna() & (aligned["Volume"] > 0)]
        aligned = aligned.loc[aligned.index == target]
        if aligned.empty:
            record_failure("borsapy", kind="invalid", error=f"{ticker}: kapanış eşleşmesi yok")
            return ticker, None, "kapanış eşleşmesi yok"

        scale_ok, scale_note = _borsapy_volume_scale_guard(active, aligned, target)
        if not scale_ok:
            record_failure("borsapy", kind="invalid", error=f"{ticker}: {scale_note}")
            return ticker, None, scale_note

        record_success("borsapy")
        log.info("borsapy kontrollü aday %s: %s", ticker, scale_note)
        return ticker, aligned[["Volume"]], "borsapy"
    except ProviderCooldown as exc:
        return ticker, None, str(exc)[:160]
    except Exception as exc:
        try:
            from provider_traffic import record_failure
            record_failure("borsapy", kind="error", error=str(exc))
        except Exception as record_exc:
            log.warning("borsapy hata kaydı yazılamadı %s: %s", ticker, record_exc)
        return ticker, None, str(exc)[:160]


def main() -> int:
    started = time.time()
    tickers = _tickers()
    if not tickers:
        log.error("Aktif BIST sürümünde hisse yok; hacim turu başlamadı")
        return 2
    from isyatirim_gateway import robust_isyatirim
    candidates = {}
    fallback_tickers = []
    isyatirim_ok = 0
    expected_dates = {}
    for i, ticker in enumerate(tickers, 1):
        from bist_data_store import read_active
        active = read_active(ticker)
        if active is None or active.empty:
            fallback_tickers.append(ticker)
            continue
        expected_date = pd.Timestamp(active.index.max()).strftime("%Y-%m-%d")
        expected_dates[ticker] = expected_date
        end = datetime.now(); start = end - timedelta(days=12)
        df, source = robust_isyatirim(
            ticker, start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"), tries=1, allow_stale=False,
            want_dates=[expected_date], priority="final_volume", max_wait=120)
        if (df is not None and not df.empty and "Volume" in df.columns
                and expected_date in pd.DatetimeIndex(df.index).strftime("%Y-%m-%d")):
            candidates[ticker] = {"volume_df": df[["Volume"]].dropna().tail(5),
                                  "volume_source": "isyatirim"}
            isyatirim_ok += 1
        else:
            fallback_tickers.append(ticker)
        if i % 50 == 0:
            log.info("İlerleme %d/%d · aday=%d", i, len(tickers), len(candidates))

    borsapy_ok = 0
    if fallback_tickers and os.environ.get("SMR_VOLUME_BORSAPY_FALLBACK", "1") != "0":
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=12)).strftime("%Y-%m-%d")
        log.warning("İş Yatırım eksikleri için borsapy/TradingView yedeği başlıyor: %d hisse",
                    len(fallback_tickers))
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(_borsapy_volume_candidate, ticker, start_date, end_date,
                            expected_dates[ticker]): ticker
                for ticker in fallback_tickers
                if ticker in expected_dates
            }
            for i, future in enumerate(as_completed(futures), 1):
                ticker = futures[future]
                try:
                    ticker, volume_df, source = future.result()
                except Exception as exc:
                    log.warning("borsapy yedeği beklenmedik hata %s: %s", ticker, exc)
                    continue
                if volume_df is not None and not volume_df.empty:
                    candidates[ticker] = {"volume_df": volume_df,
                                          "volume_source": source}
                    borsapy_ok += 1
                if i % 50 == 0:
                    log.info("borsapy yedeği %d/%d · aday=%d", i,
                             len(fallback_tickers), len(candidates))

    coverage = len(candidates) / len(tickers)
    threshold = float(os.environ.get("SMR_FINAL_VOLUME_MIN_COVERAGE", "0.85"))
    if coverage < threshold:
        log.error("Hacim turu RED: kapsama %.1f%% < %.1f%%",
                  coverage * 100, threshold * 100)
        return 2

    from bist_data_store import promote_batch
    result = promote_batch(
        candidates, reason="final_isyatirim_then_borsapy_volume",
        source_run={"coverage": coverage,
                    "source": "isyatirim_then_borsapy",
                    "isyatirim_ok": isyatirim_ok,
                    "borsapy_ok": borsapy_ok,
                    "unresolved": len(tickers) - len(candidates)},
        max_reject_ratio=0.20)
    if not result.get("ok"):
        log.error("Hacim turu doğrulama kapısında RED; aktif sürüm korundu")
        return 2

    today = datetime.now().strftime("%Y-%m-%d")
    tmp = MARKER_FILE.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(today, encoding="utf-8")
    os.replace(tmp, MARKER_FILE)
    log.info("BİTTİ %.1fs · kapsama %.1f%% · sürüm %s",
             time.time() - started, coverage * 100, result.get("version_id"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
