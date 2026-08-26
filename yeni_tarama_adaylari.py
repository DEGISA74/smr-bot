# -*- coding: utf-8 -*-
"""
yeni_tarama_adaylari.py — YENİ TARAMA TASARIMI, ÖLÇÜMLE (23 Tem 2026)

Dört bağımsız ölçüm aynı şeyi söyledi: KONUM (zirveye yakınlık) + ortalamaların
üstünde olmak ayırıyor; hacim göstergeleri zayıf. Bu betik o bulgudan aday kural
setleri kurar ve HANGİSİNİN GERÇEKTEN KÂRLI olduğunu ölçer.

VERİ: veriler_4s/ (89 hisse × ~1.440 adet 4 saatlik mum, ~2 yıl)
ALFA: aynı mumdaki evren medyanı çıkarılır (piyasa-nötr) — endeksin saatliği yok.

VADE de ölçülür: 2 mum (1 gün) · 6 mum (3 gün) · 12 mum (6 gün).

⚠ 9 aday × 3 vade = 27 deneme. Az sayıda ve hepsi ÖNCEDEN ölçülmüş bulgudan
  türetildi — kör arama değil. Yine de tek rejimlik pencere; çıkan sonuç
  "aday", "kural" değil. [[feedback-extrapolation-yasak]]
"""
import os
import glob

import numpy as np
import pandas as pd

import intraday_4s as X

BASE = os.path.dirname(os.path.abspath(__file__))
DEPO = os.path.join(BASE, "veriler_4s")
VADELER = [(2, "1 gün"), (6, "3 gün"), (12, "6 gün")]


def hisse_tablosu(yol):
    d = pd.read_parquet(yol)
    if d.empty or len(d) < 300:
        return None
    ind = X.gostergeler_4s(d)
    c = d["Close"].astype(float)
    v = d["Volume"].astype(float)
    t = pd.DataFrame(index=d.index)

    dip = c.rolling(500).min()
    tepe = c.rolling(500).max()
    t["konum"] = (c - dip) / (tepe - dip).replace(0, np.nan) * 100
    t["sma50_ust"] = (c > ind.sma50)
    t["sma200_ust"] = (c > ind.sma200)
    t["sma50_uzak"] = (c / ind.sma50 - 1) * 100
    t["rsi14"] = ind.rsi14
    t["cmf20"] = ind.cmf20
    t["adx20"] = ind.adx20
    t["hacim_kat"] = v / v.rolling(20).mean().replace(0, np.nan)
    # daralma: son 20 mumun bant genişliği, 100 mumluk normale göre
    bant = (c.rolling(20).max() - c.rolling(20).min()) / c
    t["daralma"] = bant / bant.rolling(100).mean().replace(0, np.nan)
    t["dususte"] = (c / c.shift(6) - 1) * 100      # son 3 günün getirisi

    for n, _ in VADELER:
        t[f"ret{n}"] = (c.shift(-n) / c - 1) * 100
    return t


ADAYLAR = [
    ("1 · Zirvede + ortalama üstü",
     lambda t: (t.konum >= 80) & t.sma50_ust & t.sma200_ust),
    ("2 · Zirvede + para akışı artı",
     lambda t: (t.konum >= 80) & t.sma50_ust & (t.cmf20 > 0)),
    ("3 · Zirvede + SIKIŞMA (daralma)",
     lambda t: (t.konum >= 80) & t.sma50_ust & (t.daralma <= 0.7)),
    ("4 · Zirvede + hacimli mum",
     lambda t: (t.konum >= 80) & t.sma50_ust & (t.hacim_kat >= 1.5)),
    ("5 · Zirvede + GERİ ÇEKİLME",
     lambda t: (t.konum >= 80) & t.sma50_ust & (t.dususte < 0)),
    ("6 · Çok zirvede (konum ≥ 95)",
     lambda t: (t.konum >= 95) & t.sma50_ust),
    ("7 · Zirvede + güçlü trend (ADX≥30)",
     lambda t: (t.konum >= 80) & t.sma50_ust & (t.adx20 >= 30)),
    ("8 · Zirvede + RSI ılımlı (50-70)",
     lambda t: (t.konum >= 80) & t.sma50_ust & t.rsi14.between(50, 70)),
    ("9 · TABAN — sadece SMA50 üstü",
     lambda t: t.sma50_ust),
]


def main():
    dosyalar = [f for f in sorted(glob.glob(os.path.join(DEPO, "*_4h.parquet")))
                if not os.path.basename(f).upper().startswith(("XU", "XB", "XT", "XY"))]
    print(f"hisse dosyası: {len(dosyalar)}")

    parcalar = []
    for f in dosyalar:
        try:
            t = hisse_tablosu(f)
        except Exception:
            continue
        if t is not None:
            t["sym"] = os.path.basename(f).replace(".IS_4h.parquet", "")
            parcalar.append(t)
    d = pd.concat(parcalar)
    print(f"hisse: {d.sym.nunique()} | gözlem: {len(d):,}")

    # piyasa-nötr: aynı mumdaki evren medyanı
    for n, _ in VADELER:
        d[f"alfa{n}"] = d[f"ret{n}"] - d.groupby(level=0)[f"ret{n}"].transform("median")

    print(f"\nevren geneli ortalama alfa (taban): "
          + " · ".join(f"{lbl} {d[f'alfa{n}'].mean():+.3f}%" for n, lbl in VADELER))

    satir = []
    for ad, fn in ADAYLAR:
        try:
            m = fn(d).fillna(False)
        except Exception:
            continue
        alt = d[m]
        if len(alt) < 2000:
            continue
        r = {"aday": ad, "N": len(alt),
             "sinyal%": round(len(alt) / len(d) * 100, 1)}
        for n, lbl in VADELER:
            a = alt[f"alfa{n}"].dropna()
            r[f"{lbl} alfa"] = a.mean()
            r[f"{lbl} poz%"] = (a > 0).mean() * 100
        satir.append(r)

    t = pd.DataFrame(satir)
    pd.set_option("display.width", 240)
    print("\n" + "=" * 104)
    print("ADAY TARAMALAR — piyasa-nötr alfa (%) ve pozitif oran, üç vadede")
    print("=" * 104)
    print(t.sort_values("3 gün alfa", ascending=False).round(3).to_string(index=False))
    t.to_csv(os.path.join(BASE, "yeni_tarama_adaylari.csv"), index=False, encoding="utf-8")
    print("\nyazıldı: yeni_tarama_adaylari.csv")


if __name__ == "__main__":
    main()
