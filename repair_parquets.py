#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bozuk imzalı BIST fiyatlarını Yahoo tam-seri adayıyla özel onarıma teklif eder."""
from __future__ import annotations

import datetime
import logging
import os
import sys

import pandas as pd
import yfinance as yf

from bist_data_store import load_manifest, read_active, promote_batch
from data_policy import AUTO_ADJUST, drop_adj_close
from provider_traffic import acquire_slot, record_failure, record_success

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def is_corrupt(df):
    if df is None or len(df) < 10:
        return False
    if {"Open", "High", "Low", "Close"}.issubset(df.columns):
        o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
        if int(((o > h * 1.001) | (o < l * .999) |
                (c > h * 1.001) | (c < l * .999)).sum()) >= 1:
            return True
    return bool((df["Close"].pct_change(fill_method=None).abs() > .30).any())


def main() -> int:
    if os.environ.get("SMR_BIST_WRITER", "0") != "1":
        print("repair_parquets salt-okuma makinede durdu. Onarım yalnız VPS'te "
              "SMR_BIST_WRITER=1 ile sürüm kapısından çalıştırılır.")
        return 0
    manifest = load_manifest() or {}
    corrupt = []
    for sym in manifest.get("symbols", {}):
        if sym.startswith(("XU", "XB", "XT", "XY", "XK", "XG", "XI", "XUS")):
            continue
        if is_corrupt(read_active(sym)):
            corrupt.append(sym)
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] bozuk imzalı: {len(corrupt)}")
    if not corrupt:
        return 0

    candidates = {}
    for sym in corrupt:
        try:
            acquire_slot("yahoo", priority="repair", max_wait=120)
            df = yf.download(sym, period="1y", interval="1d", progress=False,
                             auto_adjust=AUTO_ADJUST, threads=False, timeout=20)
            df = drop_adj_close(df)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if df is None or df.empty or is_corrupt(df):
                record_failure("yahoo", kind="invalid", error=f"{sym} repair invalid")
                continue
            record_success("yahoo")
            candidates[sym] = {
                "price_df": df[["Open", "High", "Low", "Close"]].copy(),
                "price_source": "repair_yahoo",
                "evidence": {"verified_full_source": True},
            }
        except Exception as exc:
            record_failure("yahoo", kind="error", error=str(exc))

    if not candidates:
        print("Onarım adayı üretilemedi; aktif sürüm korundu")
        return 2
    result = promote_batch(candidates, reason="verified_full_price_repair",
                           source_run={"source": "yahoo", "repair": True},
                           repair=True, max_reject_ratio=.20)
    print(f"SONUÇ: ok={result.get('ok')} değişen={result.get('changed', 0)} "
          f"reddedilen={len(result.get('rejected', {}))}")
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
