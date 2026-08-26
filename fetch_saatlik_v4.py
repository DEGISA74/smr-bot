#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V4 kapanış öncesi saatlik veri ön-çekicisi.

V3'ün sabah aday listesindeki hisselerin (ve XU100'ün) güncel saatlik verisini
kapanış öncesi (17:35) Yahoo'dan indirip veriler_saatlik/ altına yazar.
Böylece V4 motoru 17:45 ve 17:50'de taze verilerle sorunsuz çalışır.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from intraday_4s import kaydet_saatlik, saatlik_cek


def main() -> int:
    state_path = BASE / "yuksek_getiri_v3_state.json"
    symbols = ["XU100"]

    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            for item in state.get("list", []):
                ticker = str(item.get("ticker", "")).strip().upper()
                if ticker and ticker not in symbols:
                    symbols.append(ticker)
        except Exception as exc:
            print(f"V3 durum dosyası okunamadı: {exc}")

    print(f"V4 için saatlik veri indirilecek semboller: {symbols}")
    os.makedirs(BASE / "veriler_saatlik", exist_ok=True)
    os.makedirs(BASE / "veriler_4s", exist_ok=True)

    success = 0
    for sym in symbols:
        try:
            df = saatlik_cek(sym, period="60d")
            if df is not None and not df.empty:
                kaydet_saatlik(sym, df)
                print(f"  ✓ {sym}: {len(df)} saatlik bar güncellendi (son: {df.index[-1]})")
                success += 1
            else:
                print(f"  ✗ {sym}: veri boş")
        except Exception as exc:
            print(f"  ✗ {sym}: hata {exc}")
        time.sleep(0.3)

    print(f"Tamamlandı: {success}/{len(symbols)} sembol başarıyla güncellendi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
