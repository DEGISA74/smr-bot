# -*- coding: utf-8 -*-
"""
intraday_4s_olcum.py — 4 SAATLİK VERİ YENİ BİR ŞEY SÖYLÜYOR MU? (23 Tem 2026)

SORU: Kullanıcı kararlarını 4 saatlik grafikte veriyor, sistem günlük bakıyor.
4 saatlik göstergeler, günlükte görünmeyen bir ayrım üretiyor mu?

YÖNTEM: Her 4 saatlik mumda göstergeler okunur, sonraki 6 mum (≈3 işlem günü —
çıkış ölçümünde kazanan pencere) getirisi hesaplanır, XU100'ün aynı penceredeki
getirisi çıkarılır (piyasa-nötr alfa). Sonra her gösterge dilimlere bölünür ve
dilim başına ortalama alfa yazılır.

OKUMA: Bir gösterge işe yarıyorsa dilimler arasında MONOTON bir eğim görülür
(alt dilim eksi → üst dilim artı). Eğim yoksa o gösterge ayırt etmiyor demektir.

⚠ Bu bir KEŞİF taraması, kural üretimi değil. Çıkan eğim tek başına tarama
  filtresi olmaz — ayrı doğrulama ister. [[feedback-extrapolation-yasak]]
"""
import os
import glob

import numpy as np
import pandas as pd

import intraday_4s as X

BASE = os.path.dirname(os.path.abspath(__file__))
DEPO = os.path.join(BASE, "veriler_4s")
ILERI = 6          # 6 adet 4 saatlik mum ≈ 3 işlem günü
DILIM = 5          # beşte birlik dilimler

OLCUTLER = [
    ("konum%",        "hisse 500 mumluk aralıkta nerede"),
    ("sma200_uzak%",  "SMA200'e uzaklık"),
    ("sma50_uzak%",   "SMA50'ye uzaklık"),
    ("rsi14",         "RSI 14"),
    ("rsi9",          "RSI 9"),
    ("cci20",         "CCI 20"),
    ("adx20",         "ADX 20"),
    ("wt1",           "WaveTrend"),
    ("cmf5",          "para akışı 5"),
    ("cmf20",         "para akışı 20"),
    ("obv_egim",      "OBV eğimi"),
    ("udvr",          "alım/satım hacim oranı"),
]


def hazirla(yol):
    d = pd.read_parquet(yol)
    if d.empty or len(d) < 250:
        return None
    ind = X.gostergeler_4s(d)
    c = d["Close"].astype(float)
    t = pd.DataFrame(index=d.index)
    t["ret"] = (c.shift(-ILERI) / c - 1) * 100
    t["konum%"] = (c - c.rolling(500).min()) / \
                  (c.rolling(500).max() - c.rolling(500).min()).replace(0, np.nan) * 100
    t["sma200_uzak%"] = (c / ind.sma200 - 1) * 100
    t["sma50_uzak%"] = (c / ind.sma50 - 1) * 100
    for k in ("rsi14", "rsi9", "cci20", "adx20", "wt1", "cmf5", "cmf20",
              "obv_egim", "udvr"):
        t[k] = ind[k]
    return t


def main():
    dosyalar = sorted(glob.glob(os.path.join(DEPO, "*_4h.parquet")))
    print(f"4 saatlik dosya: {len(dosyalar)}")

    bench = None
    for f in dosyalar:
        if os.path.basename(f).startswith("XU100"):
            bc = pd.read_parquet(f)["Close"].astype(float)
            bench = (bc.shift(-ILERI) / bc - 1) * 100
            break

    parcalar = []
    for f in dosyalar:
        s = os.path.basename(f).replace(".IS_4h.parquet", "")
        if s.upper().startswith(("XU", "XB", "XT", "XY")):
            continue
        try:
            t = hazirla(f)
        except Exception:
            continue
        if t is None:
            continue
        t["sym"] = s
        parcalar.append(t)
    if not parcalar:
        print("veri yok"); return

    d = pd.concat(parcalar)
    print(f"hisse: {d.sym.nunique()} | gözlem: {len(d):,}")

    # PİYASA-NÖTR: endeks varsa ondan, yoksa aynı mumdaki evren medyanından
    if bench is not None:
        d["piyasa"] = d.index.map(bench)
        kaynak = "XU100"
    else:
        d["piyasa"] = d.groupby(level=0)["ret"].transform("median")
        kaynak = "evren medyanı"
    d["alfa"] = d["ret"] - d["piyasa"]
    d = d.dropna(subset=["alfa"])
    print(f"piyasa referansı: {kaynak} | geçerli gözlem: {len(d):,}")
    print(f"ileri pencere: {ILERI} adet 4s mum (≈{ILERI/2:.0f} işlem günü)\n")

    print("=" * 78)
    print("HANGİ GÖSTERGE AYIRIYOR? — dilim başına ortalama alfa (%)")
    print("=" * 78)
    ozet = []
    for kol, ad in OLCUTLER:
        v = d[[kol, "alfa"]].dropna()
        if len(v) < 5000:
            continue
        try:
            v["dilim"] = pd.qcut(v[kol], DILIM, labels=False, duplicates="drop")
        except Exception:
            continue
        g = v.groupby("dilim")["alfa"].mean()
        if len(g) < DILIM:
            continue
        yayilim = float(g.iloc[-1] - g.iloc[0])
        monoton = bool((g.diff().dropna() > 0).all() or (g.diff().dropna() < 0).all())
        ozet.append({"gösterge": ad, "en alt": g.iloc[0], "2": g.iloc[1],
                     "3": g.iloc[2], "4": g.iloc[3], "en üst": g.iloc[-1],
                     "yayılım": yayilim, "düzenli": "EVET" if monoton else "-",
                     "N": len(v)})
    t = pd.DataFrame(ozet).sort_values("yayılım", key=abs, ascending=False)
    pd.set_option("display.width", 220)
    print(t.round(3).to_string(index=False))
    t.to_csv(os.path.join(BASE, "intraday_4s_olcum.csv"), index=False, encoding="utf-8")
    print("\nyazıldı: intraday_4s_olcum.csv")
    print("\nOKUMA: 'yayılım' = en üst dilim ile en alt dilim arasındaki alfa farkı.")
    print("       'düzenli' = dilimler baştan sona tek yönde ilerliyor mu.")


if __name__ == "__main__":
    main()
