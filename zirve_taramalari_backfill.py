# -*- coding: utf-8 -*-
"""
zirve_taramalari_backfill.py — İKİ YENİ TARAMAYI BACKTEST'E SOK (23 Tem 2026)

zirve_taramalari.py'deki iki taramayı GEÇMİŞE DÖNÜK, nokta-zamanlı çalıştırır ve
scan_signals'a yazar. Böylece mevcut backtest boru hattı (signal_results +
signal_exits) onları da diğer taramalarla aynı cetvelde ölçer.

SIZINTI YOK: her tarih için df o güne KESİLİR, tarama sadece o güne kadarki
veriyi görür.

Yazılan scan_type'lar: zirve_devam · zirve_sikisma
Idempotent — INSERT OR IGNORE, aynı gün+sembol+tip iki kez yazılmaz.

Kullanım:
    python zirve_taramalari_backfill.py            son 120 gün
    python zirve_taramalari_backfill.py 250        son 250 gün
"""
import os
import sqlite3
import sys
from datetime import datetime  # noqa: F401

import pandas as pd

import zirve_taramalari as Z
from deepening_policy import ensure_deepening_schema
from signal_policy import (
    assign_event_metadata_for_date,
    ensure_event_schema,
    register_scan_run,
)

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "patron.db")
VERI = os.path.join(BASE, "veriler")
MIN_TL_HACIM = 5e7          # ince tahtaları eleyen likidite kapısı


def veriyi_yukle():
    import glob
    out = {}
    for f in glob.glob(os.path.join(VERI, "*.IS_1d.parquet")):
        s = os.path.basename(f).replace(".IS_1d.parquet", "")
        if s.upper().startswith(("XU", "XB", "XT", "XY")):
            continue
        try:
            d = pd.read_parquet(f)
        except Exception:
            continue
        if d.empty or len(d) < Z.MIN_BAR or "Close" not in d.columns:
            continue
        d = d[~d.index.duplicated(keep="last")].sort_index()
        if float((d["Close"] * d["Volume"]).median()) < MIN_TL_HACIM:
            continue
        out[s] = d
    return out


def main():
    gun_sayisi = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    veri = veriyi_yukle()
    print(f"likit hisse: {len(veri)}")
    if not veri:
        print("veri yok"); return

    # ortak takvim — en uzun geçmişli hisseden
    takvim = sorted({t for d in veri.values() for t in d.index})[-gun_sayisi:]
    print(f"taranacak gün: {len(takvim)}  ({takvim[0].date()} → {takvim[-1].date()})")

    conn = sqlite3.connect(DB, timeout=60)
    ensure_event_schema(conn)
    ensure_deepening_schema(conn)
    yazilan = {Z.SCAN_ZIRVE_DEVAM: 0, Z.SCAN_ZIRVE_SIKISMA: 0}

    for gun in takvim:
        # SIZINTI KAPISI: her hissenin verisi o güne kesilir
        kesit = {}
        for s, d in veri.items():
            alt = d.loc[:gun]
            if len(alt) >= Z.MIN_BAR:
                kesit[s] = alt
        if not kesit:
            continue

        devam, sikisma = Z.tara(kesit)
        gun_str = gun.strftime("%Y-%m-%d")

        for tip, df in ((Z.SCAN_ZIRVE_DEVAM, devam), (Z.SCAN_ZIRVE_SIKISMA, sikisma)):
            satir = []
            if df is not None and not df.empty:
                satir = [
                    (
                        gun_str,
                        r["Hisse"],
                        tip,
                        float(r.get("Konum", 0)),
                        "bullish",
                        float(r["Fiyat"]),
                        "BIST 500 ",
                        r.get("Kalite_Skoru"),
                        r.get("Kalite"),
                        r.get("Kalite_Detay"),
                        r.get("Yolculuk_Asamasi"),
                        r.get("Yolculuk_Gunu"),
                        r.get("Yolculuk_Anahtari"),
                    )
                    for _, r in df.iterrows()
                ]
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO scan_signals (
                        scan_date, symbol, scan_type, score, bias, entry_price, category,
                        quality_score, quality_label, quality_detail,
                        journey_stage, journey_age, journey_key
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    satir,
                )
                yazilan[tip] += len(satir)
            previous = register_scan_run(
                conn, tip, gun_str, len(satir), "BIST 500 "
            )
            assign_event_metadata_for_date(conn, tip, gun_str, previous)
        conn.commit()

    print(f"\nyazılan sinyal:")
    for tip, n in yazilan.items():
        print(f"  {Z.ADLAR[tip]:22s} ({tip:14s}) : {n:,}")

    print("\n--- DB kontrolü ---")
    for tip in yazilan:
        r = conn.execute("SELECT COUNT(*), MIN(scan_date), MAX(scan_date) "
                         "FROM scan_signals WHERE scan_type=?", (tip,)).fetchone()
        print(f"  {tip:14s}: {r[0]:,} satır  {r[1]} → {r[2]}")
    conn.close()
    print("\nSIRADAKİ: python cikis_sonuclari_yaz.py   (çıkış karnelerini üretir)")


if __name__ == "__main__":
    main()
