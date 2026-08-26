# -*- coding: utf-8 -*-
"""
tarama_karne_cikisli.py — HANGİ ERKEN RADAR İYİ? (23 Tem 2026)

Önceki adım (cikis_kurali_backtest.py) tüm senaryoları TEK HAVUZDA ölçtü ve
"çıkış zamanlaması fark eder" sonucunu verdi. Bu adım havuzu AÇAR: her radar
tek tek, aynı çıkış kuralıyla ölçülür.

⚠ NEDEN ÇIKIŞ ARAMIYORUZ: 30 tarama × 40 kural = 1.200 deneme. Elimizde yalnız
41 bağımsız tarama günü var — 1.200 denemede tesadüfen parlayan bir kombinasyon
KESİNLİKLE çıkar. O yüzden çıkış SEÇİLİR, aranmaz. [[feedback-extrapolation-yasak]]

ÜÇ SABİT ÇIKIŞ (hepsi ayrı koşar, eleme testi):
  A · 3 gün sonra çık              — hiç eşiği yok, uydurulacak parametre yok
  B · +%6 hedef / -%3 stop         — havuz testinde nominal birinci
  C · CMF 5g negatife dönünce çık  — akıllı para tarafının en iyisi

KARAR KURALI: bir tarama ÜÇÜNDE DE artı alfa veriyorsa gerçek sayılır.
Sadece birinde parlıyorsa tesadüf sayılır ve elenir.

ÖRNEKLEM KAPISI: N < 150 olan taramalar sıralamaya girmez, ayrı listelenir.
"""
import os
import numpy as np
import pandas as pd

import cikis_kurali_backtest as C

BASE = os.path.dirname(os.path.abspath(__file__))
MIN_N = 150


def main():
    C.cmf_dogrula()
    sinyaller = C.sinyalleri_al()
    print(f"bullish ER sinyali: {len(sinyaller):,} | senaryo: {sinyaller.scan_type.nunique()}")

    bench = C._oku("XU100")
    bc = bench["Close"].astype(float)

    semboller = sorted(sinyaller["symbol"].unique())
    veri, apser = {}, {}
    for s in semboller:
        d = C._oku(s)
        if d is None or len(d) < 60:
            continue
        veri[s] = d
        apser[s] = C.akilli_para_serileri(d)
    print(f"veri bulunan hisse: {len(veri)}\n")

    # üç sabit çıkış — (ad, fonksiyon(yol, ap) -> (bar, getiri) | None)
    KURALLAR = [
        ("A · 3 gün", lambda y, ap: C._kapanis(y, 2)),
        ("B · +6/-3", lambda y, ap: C._hedef_stop(y, 6, 3)),
        ("C · CMF5g", lambda y, ap: C._gosterge(y, C._israrli(ap["cmf5"] < 0, 1))),
    ]

    kayit = {ad: {} for ad, _ in KURALLAR}     # kural -> scan_type -> [alfa...]

    for _, sg in sinyaller.iterrows():
        s, tip = sg["symbol"], sg["scan_type"]
        d = veri.get(s)
        if d is None:
            continue
        idx = d.index
        pos = idx.searchsorted(sg["scan_date"])
        gir = pos + 1 if (pos < len(idx) and idx[pos] == sg["scan_date"]) else pos
        if gir + 2 >= len(idx):
            continue
        girfiy = float(d["Close"].iloc[gir])
        if girfiy <= 0:
            continue
        son = min(gir + C.MAX_GUN, len(idx) - 1)
        dilim = d.iloc[gir + 1: son + 1]
        if dilim.empty:
            continue

        yol = pd.DataFrame({
            "yuk": (dilim["High"].astype(float) / girfiy - 1) * 100,
            "dus": (dilim["Low"].astype(float) / girfiy - 1) * 100,
            "kap": (dilim["Close"].astype(float) / girfiy - 1) * 100,
        }, index=dilim.index)
        ap = apser[s].loc[dilim.index]

        try:
            b0 = float(bc.loc[:idx[gir]].iloc[-1])
            piyasa = (bc.reindex(yol.index, method="ffill") / b0 - 1) * 100
        except Exception:
            piyasa = pd.Series(0.0, index=yol.index)

        for ad, fn in KURALLAR:
            cik = fn(yol, ap)
            if cik is None:
                gun, r = len(yol) - 1, float(yol["kap"].iloc[-1])
            else:
                gun, r = cik
                gun = min(int(gun), len(yol) - 1)
            piy = float(piyasa.iloc[gun]) if not pd.isna(piyasa.iloc[gun]) else 0.0
            kayit[ad].setdefault(tip, []).append(r - piy)

    # ── tablo ──
    tipler = sorted({t for ad, _ in KURALLAR for t in kayit[ad]})
    satir = []
    for t in tipler:
        r = {"tarama": t, "N": len(kayit[KURALLAR[0][0]].get(t, []))}
        for ad, _ in KURALLAR:
            v = np.array(kayit[ad].get(t, []))
            r[ad] = v.mean() if v.size else np.nan
        alfalar = [r[ad] for ad, _ in KURALLAR]
        r["ort"] = float(np.nanmean(alfalar))
        pozitif = sum(1 for a in alfalar if a > 0)
        r["hüküm"] = ("3/3 ARTI" if pozitif == 3 else
                      "3/3 EKSİ" if pozitif == 0 else f"karışık {pozitif}/3")
        satir.append(r)

    t = pd.DataFrame(satir)
    buyuk = t[t.N >= MIN_N].sort_values("ort", ascending=False)
    kucuk = t[t.N < MIN_N].sort_values("N", ascending=False)

    pd.set_option("display.width", 200)
    print("=" * 92)
    print(f"ERKEN RADAR KARNESİ — üç sabit çıkış, XU100'e göre alfa (N ≥ {MIN_N})")
    print("=" * 92)
    print(buyuk.round(2).to_string(index=False))
    print()
    print(f"--- örneklem yetersiz (N < {MIN_N}) — sıralamaya girmez ---")
    print(kucuk.round(2).to_string(index=False))

    tut = buyuk[buyuk["hüküm"] == "3/3 ARTI"]
    print()
    print(f"ÜÇ ÇIKIŞTA DA ARTI VEREN: {len(tut)} tarama")
    if len(tut):
        print("  " + ", ".join(f"{r.tarama} ({r.ort:+.2f})" for r in tut.itertuples()))
    t.to_csv(os.path.join(BASE, "tarama_karne_cikisli.csv"), index=False, encoding="utf-8")
    print("\nyazıldı: tarama_karne_cikisli.csv")


if __name__ == "__main__":
    main()
