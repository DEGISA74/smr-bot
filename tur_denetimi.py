#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tur_denetimi.py — "Bugün Master Scan kaç kez koştu?" (19 Ağu 2026)

Neden var: 18 Ağu akşamı günlük karne "19 tarama yazım sayısı uyuşmuyor" dedi.
Sebep veri bozukluğu değil, aynı gün İKİ tarama koşmasıydı: karnenin `scan_runs`
alanı O TURUN ürettiği sinyali sayar, `written_signal_counts` ise GÜNÜN veritabanı
satırlarını (iki turun birleşimi) sayar. İkisi doğal olarak tutmaz.

`scan_signals`'ta zaman damgası yok, ama `id` artan: bir tur, her tarayıcının
satırlarını arka arkaya yazar. Aynı tarama tipinin satırları büyük id boşluklarıyla
ayrılmış bloklara düşüyorsa, o gün birden fazla tur koşmuş demektir.

Kullanım:
    python tur_denetimi.py                 # bugün
    python tur_denetimi.py --gun 2026-08-18
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "patron.db"
KARNE_DIR = ROOT / "logs"
TR = timezone(timedelta(hours=3))
# Bir turun içindeki iki satır arasındaki id farkı bu değeri aşarsa "yeni blok".
# Tek tur bile tarayıcılar arasında birkaç yüz id atlayabilir; 300 güvenli sınır.
BLOK_ESIGI = 300


def blok_sayisi(ids: list[int]) -> int:
    n = 1
    for a, b in zip(ids, ids[1:]):
        if b - a > BLOK_ESIGI:
            n += 1
    return n


def denetle(gun: str) -> dict:
    con = sqlite3.connect(DB)
    tipler = [r[0] for r in con.execute(
        "select distinct scan_type from scan_signals where scan_date=?", (gun,))]
    coklu, tekli = [], 0
    for st in tipler:
        ids = [r[0] for r in con.execute(
            "select id from scan_signals where scan_date=? and scan_type=? order by id", (gun, st))]
        n = blok_sayisi(ids)
        if n > 1:
            coklu.append((st, n, len(ids)))
        else:
            tekli += 1
    toplam = con.execute(
        "select count(*) from scan_signals where scan_date=?", (gun,)).fetchone()[0]
    mukerrer = con.execute(
        "select count(*) from (select symbol,scan_type,count(*) n from scan_signals "
        "where scan_date=? group by 1,2 having n>1)", (gun,)).fetchone()[0]
    con.close()

    # Karne dosyası varsa sayı uyuşmazlığını da göster.
    karne = {}
    kp = KARNE_DIR / ("master_scan_karne_%s.json" % gun.replace("-", ""))
    if kp.exists():
        try:
            karne = json.loads(kp.read_text(encoding="utf-8"))
        except Exception:
            karne = {}

    # Tahmini tur sayısı: tarayıcıların çoğunluğunda kaç blok görülüyor?
    if coklu:
        tur = max(n for _, n, _ in coklu)
    else:
        tur = 1 if toplam else 0
    return {"gun": gun, "toplam_satir": toplam, "tarama_tipi": len(tipler),
            "coklu_bloklu": coklu, "tek_bloklu": tekli, "mukerrer_sinyal": mukerrer,
            "tahmini_tur": tur, "karne_ok": karne.get("ok"),
            "karne_issues": karne.get("issues") or []}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gun", default=str(datetime.now(TR).date()))
    a = ap.parse_args()
    r = denetle(a.gun)
    print("GUN            :", r["gun"])
    print("satir / tarama :", r["toplam_satir"], "/", r["tarama_tipi"], "tip")
    print("mukerrer sinyal:", r["mukerrer_sinyal"])
    print("tek bloklu     :", r["tek_bloklu"], "tarama")
    print("cok bloklu     :", len(r["coklu_bloklu"]), "tarama")
    for st, n, adet in sorted(r["coklu_bloklu"], key=lambda x: -x[2])[:10]:
        print("   %-22s blok=%d adet=%d" % (st, n, adet))
    if r["karne_ok"] is not None:
        print("karne ok       :", r["karne_ok"], r["karne_issues"])
    print("HUKUM          :", "TEK TUR" if r["tahmini_tur"] <= 1
          else "%d TUR (ayni gun birden fazla Master Scan)" % r["tahmini_tur"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
