#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lokal Yahoo kapanışını VPS'e gönderilecek BIST düzeltme adayına çevirir.

Bu program ana parquet'lere yazmaz. Yalnız aktif sürümle bağlanmış bir aday ZIP
üretir; VPS paketi kendi güncel sürümüyle karşılaştırıp doğrular ve kabul ederse
yeni sürüm yayınlar.
"""
import glob
import os
import sys
import time
import warnings
from pathlib import Path

import pandas as pd
import yfinance as yf

from bist_data_store import active_version_id, read_active
from bist_exchange import create_candidate_package

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
VER = ROOT / "veriler"


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def main() -> int:
    syms = sorted(Path(f).name.replace("_1d.parquet", "")
                  for f in glob.glob(str(VER / "*.IS_1d.parquet")))
    candidates = {}
    checked = 0
    for grp in chunks(syms, 100):
        try:
            from provider_traffic import acquire_slot, record_success, record_failure
            acquire_slot("yahoo", priority="post_close", max_wait=60.0)
            data = yf.download(grp, period="3d", interval="1d", progress=False,
                               auto_adjust=False, group_by="ticker", threads=True,
                               timeout=20)
            if data is None or data.empty:
                record_failure("yahoo", kind="empty", error="empty_batch")
                continue
            record_success("yahoo")
        except Exception:
            try:
                record_failure("yahoo", kind="error", error="batch_exception")
            except Exception:
                pass
            continue
        for sym in grp:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    if sym not in data.columns.get_level_values(0):
                        continue
                    yd = data[sym].dropna(subset=["Close"])
                elif len(grp) == 1 and sym == grp[0]:
                    yd = data.dropna(subset=["Close"])
                else:
                    continue
                if yd.empty:
                    continue
                ld = pd.Timestamp(yd.index[-1])
                ld = (ld.tz_localize(None) if ld.tz else ld).normalize()
                old = read_active(sym)
                checked += 1
                if old is None or old.empty or ld not in old.index:
                    continue
                row = yd.loc[[yd.index[-1]], ["Open", "High", "Low", "Close"]].copy()
                row.index = pd.DatetimeIndex([ld])
                changed = any(
                    abs(float(old.at[ld, col]) - float(row[col].iloc[-1])) > 0.0001
                    for col in ("Open", "High", "Low", "Close")
                    if col in old.columns and pd.notna(row[col].iloc[-1]))
                if changed:
                    candidates[sym] = {"price_df": row,
                                       "price_source": "yahoo_settled"}
            except Exception:
                continue
        time.sleep(0.4)

    if not candidates:
        print(f"settle_kapanis: {checked} kontrol edildi, aday değişiklik yok.")
        return 0
    package = create_candidate_package(
        candidates, source="local_yahoo_settled",
        reason="post_close_settlement", parent_version=active_version_id())
    # Shell yalnız bu tek satırdan paket yolunu alır.
    print(f"CANDIDATE_PACKAGE={package}")
    print(f"settle_kapanis: {checked} kontrol edildi, {len(candidates)} aday paketlendi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
