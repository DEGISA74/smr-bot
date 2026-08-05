#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kapanış sonrası İş Yatırım hacmini onaylı BIST sürümüne teklif eder.

Doğrudan parquet yazmaz. Kapsama oranı yeterli değilse marker üretmez ve aktif
sürüm değişmez.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

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


def _tickers() -> list[str]:
    from bist_data_store import load_manifest
    manifest = load_manifest() or {}
    return sorted(s for s in manifest.get("symbols", {})
                  if s.endswith(".IS") and not s.startswith(
                      ("XU", "XB", "XT", "XY", "XK", "XG", "XI", "XUS")))


def main() -> int:
    started = time.time()
    tickers = _tickers()
    if not tickers:
        log.error("Aktif BIST sürümünde hisse yok; hacim turu başlamadı")
        return 2
    from isyatirim_gateway import robust_isyatirim
    candidates = {}
    for i, ticker in enumerate(tickers, 1):
        end = datetime.now(); start = end - timedelta(days=12)
        df, source = robust_isyatirim(
            ticker, start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"), tries=1, allow_stale=False,
            priority="final_volume", max_wait=120)
        if df is not None and not df.empty and "Volume" in df.columns:
            candidates[ticker] = {"volume_df": df[["Volume"]].dropna().tail(5),
                                  "volume_source": "isyatirim"}
        if i % 50 == 0:
            log.info("İlerleme %d/%d · aday=%d", i, len(tickers), len(candidates))

    coverage = len(candidates) / len(tickers)
    threshold = float(os.environ.get("SMR_FINAL_VOLUME_MIN_COVERAGE", "0.85"))
    if coverage < threshold:
        log.error("Hacim turu RED: kapsama %.1f%% < %.1f%%",
                  coverage * 100, threshold * 100)
        return 2

    from bist_data_store import promote_batch
    result = promote_batch(
        candidates, reason="final_isyatirim_volume",
        source_run={"coverage": coverage, "source": "isyatirim_gateway"},
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
