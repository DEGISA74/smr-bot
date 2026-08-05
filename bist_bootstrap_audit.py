#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mevcut BIST parquetlerini ilk sürüm kapısıyla yazmadan ön kontrol eder."""
from pathlib import Path
import pandas as pd
import bist_data_store as s


def main():
    good, degraded, bad = [], [], []
    for p in Path("veriler").glob("*.IS_1d.parquet"):
        try:
            df = s._normalise_frame(pd.read_parquet(p), allow_partial=False)
            if df is None or df.empty:
                bad.append((p.name, "boş/eksik")); continue
            dated, dw = s._candidate_dates_ok(df.copy(), p.stem)
            if len(dated) != len(df):
                removed = df.loc[df.index.difference(dated.index)]
                flat = ((removed["Open"] == removed["Close"])
                        & (removed["High"] == removed["Close"])
                        & (removed["Low"] == removed["Close"]))
                zero = pd.to_numeric(removed.get("Volume", 0), errors="coerce").fillna(0) <= 0
                if bool((flat & zero).all()):
                    df = dated
                else:
                    bad.append((p.name, "; ".join(dw[:3]))); continue
            checked, pw = s._valid_price_rows(df.copy())
            if len(checked) != len(df):
                if checked.empty:
                    bad.append((p.name, "; ".join(pw))); continue
                degraded.append((p.name, len(df) - len(checked), "; ".join(pw)))
            good.append(p.name)
        except Exception as exc:
            bad.append((p.name, str(exc)[:100]))
    print(f"TOTAL={len(good)+len(bad)} ACCEPTED={len(good)} "
          f"DEGRADED={len(degraded)} HARD_BAD={len(bad)}")
    for name, count, reason in degraded[:40]:
        print(f"DEGRADED {name}: {count} gün karantina | {reason}")
    for name, reason in bad[:40]:
        print(f"BAD {name}: {reason}")
    return 0 if good else 2


if __name__ == "__main__":
    raise SystemExit(main())
