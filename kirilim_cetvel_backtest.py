"""kirilim_cetvel_backtest.py — SABİT YÜZDE mi, ATR mi?

Soru: kırılımı ölçerken sabit yüzde cetveli (mevcut sistemimiz) yerine
hissenin kendi oynaklığı (ATR) kullanılırsa sinyal kalitesi artar mı?

Yöntem — scan_signals'a HİÇ bakmaz, doğrudan fiyat verisinden yeniden kurar:
  yapı seviyesi = önceki 20 barın en yükseği
  A cetveli (MEVCUT): kapanış > seviye × 1.01  + hacim > 1.5x
  B cetveli (ATR)   : (kapanış - seviye) / ATR >= eşik + hacim > 1.5x
  Her ikisinde de "YENİ kırılım" şartı: önceki bar seviyenin üstünde değildi.

Sonra T+5 / T+10 / T+20 getirileri ölçülür ve mühürlü REJİME göre ayrılır
(XU100 kapanışı > SMA50), çünkü tek rejimde ölçüm yanıltır.

Çıktı: konsol + logs/kirilim_cetvel_backtest.md
⚠ Bu araç sadece ÖLÇER. Sonuç kural olmadan koda girmez.
"""
import sys, glob, os
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import pandas as pd
from signal_policy import (
    MEASUREMENT_REGIME_FALLING,
    MEASUREMENT_REGIME_RISING,
    measurement_regime_series,
)

VERI = Path(__file__).with_name("veriler")
OUT = Path(__file__).with_name("logs") / "kirilim_cetvel_backtest.md"

YAPI_LEN = 20            # yapı seviyesi geriye bakış
ATR_LEN = 14
HACIM_LEN = 20
HACIM_ESIK = 1.5         # her iki cetvelde de aynı — tek değişken cetvel olsun
SABIT_ESIK = 1.01        # mevcut sistem: seviye × 1.01
ATR_ESIKLER = (0.10, 0.25, 0.50)
UFUKLAR = (5, 10, 20)
MIN_BAR = 60
SICRAMA_LIMIT = 0.50     # |günlük değişim| > %50 → bozuk veri şüphesi, bar atılır


def _atr(h, l, c, n=ATR_LEN):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).rolling(n).mean().to_numpy()


def _xu100_rejim():
    """tarih → +1 (yükselen) / -1 (düşen); mühürlü XU100/SMA50 tanımı."""
    f = VERI / "XU100.IS_1d.parquet"
    if not f.exists():
        return None
    d = pd.read_parquet(f)
    d.index = pd.to_datetime(d.index)
    regimes = measurement_regime_series(d)
    return {
        ts.normalize(): (1 if state == MEASUREMENT_REGIME_RISING else -1)
        for ts, state in regimes.dropna().items()
        if state in (MEASUREMENT_REGIME_RISING, MEASUREMENT_REGIME_FALLING)
    }


def _olaylar(df, rejim):
    """Bir hisse için tüm kırılım olaylarını üretir."""
    c = df["Close"].to_numpy(float)
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    v = df["Volume"].to_numpy(float)
    n = len(c)
    if n < MIN_BAR:
        return []

    seviye = pd.Series(h).rolling(YAPI_LEN).max().shift(1).to_numpy()
    atr = _atr(h, l, c)
    vma = pd.Series(v).rolling(HACIM_LEN).mean().shift(1).to_numpy()
    sicrama = np.abs(pd.Series(c).pct_change().to_numpy())

    out = []
    for i in range(MIN_BAR, n - max(UFUKLAR)):
        s, a, vm = seviye[i], atr[i], vma[i]
        if not np.isfinite(s) or not np.isfinite(a) or a <= 0 or not np.isfinite(vm) or vm <= 0:
            continue
        if sicrama[i] > SICRAMA_LIMIT:      # bozuk/bölünme şüphesi
            continue
        hacim_ok = (v[i] / vm) > HACIM_ESIK
        if not hacim_ok:
            continue

        # YENİ kırılım şartı: önceki bar seviyenin üstünde kapanmamış olmalı
        onceki_ustte = c[i - 1] > seviye[i - 1] if np.isfinite(seviye[i - 1]) else False
        if onceki_ustte:
            continue

        mesafe_atr = (c[i] - s) / a
        a_cetvel = c[i] > s * SABIT_ESIK
        if not a_cetvel and mesafe_atr < min(ATR_ESIKLER):
            continue

        getiriler = {}
        for k in UFUKLAR:
            if c[i] > 0:
                getiriler[k] = (c[i + k] / c[i] - 1) * 100

        ts = df.index[i].normalize()
        out.append(dict(
            tarih=ts,
            rejim=rejim.get(ts, 0) if rejim else 0,
            a_cetvel=a_cetvel,
            mesafe_atr=mesafe_atr,
            **{f"r{k}": getiriler.get(k, np.nan) for k in UFUKLAR},
        ))
    return out


def _satir(ad, sub):
    p = []
    for k in UFUKLAR:
        r = sub[f"r{k}"].dropna()
        if len(r) == 0:
            p.append("— | —")
            continue
        p.append(f"{r.mean():+.2f} | {(r > 0).mean() * 100:.1f}")
    return f"| {ad} | {len(sub)} | " + " | ".join(p) + " |"


def _basliklar(ad_sutun):
    b = f"| {ad_sutun} | N | " + " | ".join(
        f"T+{k} ort% | T+{k} isabet%" for k in UFUKLAR) + " |"
    a = "|---|---|" + "---|" * (2 * len(UFUKLAR))
    return [b, a]


def main():
    rejim = _xu100_rejim()
    dosyalar = sorted(glob.glob(str(VERI / "*_1d.parquet")))
    dosyalar = [f for f in dosyalar if "XU" not in os.path.basename(f)]
    print(f"taranan hisse: {len(dosyalar)}")

    kayit = []
    hatali = 0
    for j, f in enumerate(dosyalar):
        try:
            d = pd.read_parquet(f)
            d.index = pd.to_datetime(d.index)
            d = d[~d.index.duplicated(keep="last")].sort_index()
            if not {"Open", "High", "Low", "Close", "Volume"} <= set(d.columns):
                hatali += 1
                continue
            kayit += _olaylar(d, rejim)
        except Exception:
            hatali += 1
        if (j + 1) % 200 == 0:
            print(f"  ... {j + 1}/{len(dosyalar)} — şu ana kadar {len(kayit)} olay")

    df = pd.DataFrame(kayit)
    print(f"toplam kırılım olayı: {len(df)} | okunamayan dosya: {hatali}")
    if df.empty:
        print("olay yok — çıkılıyor")
        return

    sat = ["# KIRILIM CETVELİ: SABİT YÜZDE vs ATR", "",
           f"- Evren: {len(dosyalar)} BIST hissesi · günlük",
           f"- Toplam kırılım olayı: **{len(df)}**",
           f"- Hacim şartı her iki cetvelde AYNI (>{HACIM_ESIK}x) — tek değişken cetvel.",
           "- Rejim: mühürlü XU100 kapanışı > SMA50 (yükselen / düşen).", ""]

    # 1) A cetveli (mevcut sabit yüzde)
    sat += ["## 1) Mevcut cetvel — sabit yüzde (seviye × 1.01)", ""]
    sat += _basliklar("Kova")
    sat.append(_satir("A cetveli (kırılım)", df[df.a_cetvel]))
    sat.append(_satir("TÜM olaylar (taban)", df))
    sat.append("")

    # 2) ATR cetveli eşikleri
    sat += ["## 2) ATR cetveli — eşiğe göre", ""]
    sat += _basliklar("ATR eşiği")
    for e in ATR_ESIKLER:
        sat.append(_satir(f"mesafe ≥ {e:.2f} ATR", df[df.mesafe_atr >= e]))
    sat.append("")

    # 3) REJİME göre — asıl sınav
    sat += ["## 3) Rejime göre (tek rejim tuzağına karşı)", ""]
    for rj, ad in ((1, "YÜKSELEN tape"), (-1, "DÜŞEN tape")):
        sub = df[df.rejim == rj]
        sat += [f"### {ad} (N={len(sub)})", ""]
        sat += _basliklar("Cetvel")
        sat.append(_satir("A — sabit yüzde", sub[sub.a_cetvel]))
        for e in ATR_ESIKLER:
            sat.append(_satir(f"B — ≥ {e:.2f} ATR", sub[sub.mesafe_atr >= e]))
        sat.append(_satir("taban (tüm olaylar)", sub))
        sat.append("")

    # 4) İkisinin ayrıştığı yer: A diyor B demiyor / tersi
    sat += ["## 4) Cetveller nerede ayrışıyor?", ""]
    sat += _basliklar("Kesişim")
    e = 0.25
    sat.append(_satir("İKİSİ de kırılım", df[df.a_cetvel & (df.mesafe_atr >= e)]))
    sat.append(_satir("SADECE sabit yüzde", df[df.a_cetvel & (df.mesafe_atr < e)]))
    sat.append(_satir("SADECE ATR", df[~df.a_cetvel & (df.mesafe_atr >= e)]))
    sat.append("")

    metin = "\n".join(sat)
    print("\n" + metin)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(metin, encoding="utf-8")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
