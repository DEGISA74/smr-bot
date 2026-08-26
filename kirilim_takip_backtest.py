"""kirilim_takip_backtest.py — TAKİP DEFTERİ İŞE YARIYOR MU?

Soru: kırılım sonrası olayı takip etmek (retest onayı / çöküş tespiti)
gerçekten karar değiştiriyor mu?

ÖNEMLİ — geleceğe bakma yok: retest ve çöküş GERÇEK ZAMANLI tespit edilebilir
olaylardır. Getiri, olayın tespit edildiği barDAN SONRA ölçülür.

  Kırılım    : kapanış > önceki 20 bar zirvesi × 1.01 + hacim > 1.5x  (MEVCUT cetvel)
  Retest     : sonraki 10 bar içinde dip seviyeye değdi VE kapanış seviyenin üstünde
  Çöküş      : seviye - 0.25×ATR altında ARKA ARKAYA 2 kapanış
  Sessiz     : 10 bar içinde ikisi de olmadı

Kıyas: her kovada, tespit barından sonraki T+5/10/20 getirisi.
Çıktı: konsol + logs/kirilim_takip_backtest.md
"""
import sys, glob, os
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import pandas as pd

VERI = Path(__file__).with_name("veriler")
OUT = Path(__file__).with_name("logs") / "kirilim_takip_backtest.md"

YAPI_LEN, ATR_LEN, HACIM_LEN = 20, 14, 20
HACIM_ESIK, SABIT_ESIK = 1.5, 1.01
TAKIP_BAR = 10          # olay ömrü
RETEST_ATR = 0.20       # seviyenin bu kadar üstü = retest bölgesi
COKUS_ATR = 0.25        # seviyenin bu kadar altı = çöküş eşiği
COKUS_KAPANIS = 2       # arka arkaya kaç kapanış
UFUKLAR = (5, 10, 20)
MIN_BAR, SICRAMA_LIMIT = 60, 0.50


def _atr(h, l, c, n=ATR_LEN):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).rolling(n).mean().to_numpy()


def _olaylar(df):
    c = df["Close"].to_numpy(float); h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float);   v = df["Volume"].to_numpy(float)
    n = len(c)
    if n < MIN_BAR:
        return []
    seviye = pd.Series(h).rolling(YAPI_LEN).max().shift(1).to_numpy()
    atr = _atr(h, l, c)
    vma = pd.Series(v).rolling(HACIM_LEN).mean().shift(1).to_numpy()
    sicrama = np.abs(pd.Series(c).pct_change().to_numpy())

    out = []
    son_gerekli = TAKIP_BAR + max(UFUKLAR)
    for i in range(MIN_BAR, n - son_gerekli):
        s, a, vm = seviye[i], atr[i], vma[i]
        if not (np.isfinite(s) and np.isfinite(a) and a > 0 and np.isfinite(vm) and vm > 0):
            continue
        if sicrama[i] > SICRAMA_LIMIT or (v[i] / vm) <= HACIM_ESIK:
            continue
        if np.isfinite(seviye[i - 1]) and c[i - 1] > seviye[i - 1]:
            continue                       # yeni kırılım değil
        if not (c[i] > s * SABIT_ESIK):
            continue

        # --- takip: hangisi ÖNCE gerçekleşir?
        sonuc, tespit_bar = "sessiz", None
        ardisik = 0
        for k in range(1, TAKIP_BAR + 1):
            j = i + k
            if c[j] < s - a * COKUS_ATR:
                ardisik += 1
            else:
                ardisik = 0
            if ardisik >= COKUS_KAPANIS:
                sonuc, tespit_bar = "cokus", j
                break
            if l[j] <= s + a * RETEST_ATR and c[j] >= s:
                sonuc, tespit_bar = "retest", j
                break

        if tespit_bar is None:
            tespit_bar = i + TAKIP_BAR      # sessiz: ömür sonunda bak

        baz = c[tespit_bar]
        if baz <= 0 or tespit_bar + max(UFUKLAR) >= n:
            continue
        kayit = dict(sonuc=sonuc, gun=tespit_bar - i)
        # tespit barından SONRAKİ getiri
        for k in UFUKLAR:
            kayit[f"r{k}"] = (c[tespit_bar + k] / baz - 1) * 100
        # kıyas için: kırılım barından ölçülen getiri
        for k in UFUKLAR:
            kayit[f"b{k}"] = (c[i + k] / c[i] - 1) * 100
        out.append(kayit)
    return out


def _satir(ad, sub, on="r"):
    p = []
    for k in UFUKLAR:
        r = sub[f"{on}{k}"].dropna()
        p.append("— | —" if len(r) == 0
                 else f"{r.mean():+.2f} | {(r > 0).mean() * 100:.1f}")
    return f"| {ad} | {len(sub)} | " + " | ".join(p) + " |"


def _bas(ad):
    return [f"| {ad} | N | " + " | ".join(f"T+{k} ort% | T+{k} isabet%" for k in UFUKLAR) + " |",
            "|---|---|" + "---|" * (2 * len(UFUKLAR))]


def main():
    dosyalar = [f for f in sorted(glob.glob(str(VERI / "*_1d.parquet")))
                if "XU" not in os.path.basename(f)]
    print(f"taranan hisse: {len(dosyalar)}")
    kayit = []
    for j, f in enumerate(dosyalar):
        try:
            d = pd.read_parquet(f); d.index = pd.to_datetime(d.index)
            d = d[~d.index.duplicated(keep="last")].sort_index()
            if {"Open", "High", "Low", "Close", "Volume"} <= set(d.columns):
                kayit += _olaylar(d)
        except Exception:
            pass
        if (j + 1) % 200 == 0:
            print(f"  ... {j + 1}/{len(dosyalar)} — {len(kayit)} olay")

    df = pd.DataFrame(kayit)
    print(f"toplam olay: {len(df)}")
    if df.empty:
        return

    sat = ["# KIRILIM TAKİP DEFTERİ — İŞE YARIYOR MU?", "",
           f"- Toplam kırılım olayı: **{len(df)}**",
           f"- Takip penceresi: {TAKIP_BAR} bar · çöküş = seviye−{COKUS_ATR}×ATR altı {COKUS_KAPANIS} kapanış",
           "- ⚠ Geleceğe bakma yok: getiri TESPİT barından sonra ölçülür.", "",
           "## 1) Takip sonucuna göre — tespit barından SONRAKİ getiri", ""]
    sat += _bas("Sonuç")
    for s_, ad in (("retest", "✓ RETEST onaylandı"), ("cokus", "✗ ÇÖKTÜ"), ("sessiz", "· sessiz (10 bar)")):
        sat.append(_satir(ad, df[df.sonuc == s_]))
    sat.append(_satir("TÜMÜ (taban)", df))
    sat += ["",
            "## 2) Kıyas — takip YOKSA (kırılım barından ölçüm, mevcut hâlimiz)", ""]
    sat += _bas("Sonuç")
    for s_, ad in (("retest", "✓ RETEST olanlar"), ("cokus", "✗ ÇÖKENLER"), ("sessiz", "· sessizler")):
        sat.append(_satir(ad, df[df.sonuc == s_], on="b"))
    sat.append(_satir("TÜMÜ (taban)", df, on="b"))
    sat += ["", "## 3) Dağılım", "",
            "| Sonuç | Adet | Pay % | Ort. tespit günü |", "|---|---|---|---|"]
    for s_ in ("retest", "cokus", "sessiz"):
        k = df[df.sonuc == s_]
        sat.append(f"| {s_} | {len(k)} | {len(k)/len(df)*100:.1f} | {k.gun.mean():.1f} |")

    metin = "\n".join(sat)
    print("\n" + metin)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(metin, encoding="utf-8")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
