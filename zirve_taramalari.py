# -*- coding: utf-8 -*-
"""
zirve_taramalari.py — İKİ YENİ TARAMA (23 Tem 2026)

Bu iki tarama SEZGİYLE değil ÖLÇÜMLE doğdu. Beş bağımsız çalışma aynı şeyi
söyledi: zirveye yakın olan kazanıyor, dipte olan kaybediyor. Hacim/akış
göstergeleri fiyat konumunun üçte biri kadar ayırıyor.

  1) 🏔 ZİRVE DEVAM      (zirve_devam)   — hızlı, sık;   önerilen vade 3 gün
  2) 🎯 ZİRVE SIKIŞMASI  (zirve_sikisma) — yavaş, seyrek; önerilen vade 6 gün

ÖLÇÜM (89 hisse × 2 yıl × 4 saatlik = 120.135 gözlem, piyasa-nötr alfa):
  zirve_devam   3g +%1,33 · isabet %55,9 · barların %6,3'ünde (taban +%0,44)
  zirve_sikisma 6g +%1,41 · isabet %58,0 · barların %3,2'sinde (taban +%0,74)
  ⚠ En kötü aday "zirvede ama geri çekilmiş olanı al" idi (+%0,61 = taban) —
    yükseliş trendinde bile düşüşte almak işe yaramıyor.

⚠ GÜNLÜK VERİDE de doğrulandı ama örneklem ZAYIF (N=395/288, parquet'ler
  1 yıllık). Günlük rakamlar (+%2,63 / +%0,97) muhtemelen ŞİŞKİN — güvenilir
  tahmin 4 saatlik ölçümdür. Karne olgunlaşana dek ELİT ETİKETİ VERİLMEZ.
  [[feedback-extrapolation-yasak]]

Saf hesap — Streamlit yok, render yok. scan_signals'a yazımı çağıran taraf yapar.
"""
import numpy as np
import pandas as pd
from deepening_policy import distribution_profile

# scan_signals'a yazılan kimlikler + ekranda görünecek adlar
SCAN_ZIRVE_DEVAM = "zirve_devam"
SCAN_ZIRVE_SIKISMA = "zirve_sikisma"

ADLAR = {
    SCAN_ZIRVE_DEVAM:   "🏔 Zirve Devam",
    SCAN_ZIRVE_SIKISMA: "🎯 Zirve Sıkışması",
}
ONERILEN_VADE = {SCAN_ZIRVE_DEVAM: 3, SCAN_ZIRVE_SIKISMA: 6}

# Eşikler ölçümden geldi — sezgiyle oynanmaz. Değiştirilecekse önce yeniden ölç.
KONUM_DEVAM = 95.0      # 250 günlük aralığın en üst %5'i
KONUM_SIKISMA = 80.0    # en üst %20
DARALMA_ESIK = 0.70     # son 20 günün bandı, 100 günlük normalin %70 altı
MIN_BAR = 220


def _olcumler(df):
    """Tek hisse için gerekli üç ölçü: konum · ortalama üstü mü · daralma."""
    c = df["Close"].astype(float)
    if len(c) < MIN_BAR:
        return None
    dip = c.rolling(250).min()
    tepe = c.rolling(250).max()
    konum = (c - dip) / (tepe - dip).replace(0, np.nan) * 100
    sma50 = c.rolling(50).mean()
    bant = (c.rolling(20).max() - c.rolling(20).min()) / c
    daralma = bant / bant.rolling(100).mean().replace(0, np.nan)
    try:
        return {
            "fiyat":   float(c.iloc[-1]),
            "konum":   float(konum.iloc[-1]),
            "sma50":   float(sma50.iloc[-1]),
            "ust":     bool(c.iloc[-1] > sma50.iloc[-1]),
            "daralma": float(daralma.iloc[-1]),
        }
    except Exception:
        return None


def tara(veri_sozlugu):
    """veri_sozlugu: {sembol: OHLCV DataFrame} → (devam_df, sikisma_df)

    İki tarama da 'bugün itibarıyla ateşliyor mu' sorusunu sorar; geçmişe
    dönük sinyal üretmez (nokta-zamanlı çağrılar için df'i o güne kesip verin).
    """
    devam, sikisma = [], []
    for sym, df in (veri_sozlugu or {}).items():
        if df is None or df.empty or "Close" not in df.columns:
            continue
        o = _olcumler(df)
        if o is None or not np.isfinite(o["konum"]):
            continue

        if o["konum"] >= KONUM_DEVAM and o["ust"]:
            dagitim = distribution_profile(df)
            devam.append({
                "Hisse": sym, "Fiyat": round(o["fiyat"], 2),
                "Konum": round(o["konum"], 1),
                "SMA50_Uzak": round((o["fiyat"] / o["sma50"] - 1) * 100, 1),
                "Dagitim_Kontrolu": dagitim["label"],
                "Dagitim_Risk": dagitim["risk_count"],
                "Dagitim_Gunu": dagitim.get("distribution_days"),
                "Kalite_Skoru": dagitim["score"],
                "Kalite": dagitim["label"],
                "Kalite_Detay": dagitim["detail"],
                "Yolculuk_Asamasi": dagitim["label"],
                "Yolculuk_Gunu": 1,
                "Yolculuk_Anahtari": "zirve_devam_dagitim",
                "Aciklama": (f"250 günün en üst %{100 - o['konum']:.0f}'inde, "
                             f"50 günlük ortalamanın %"
                             f"{(o['fiyat'] / o['sma50'] - 1) * 100:.0f} üstünde · "
                             f"dağıtım kontrolü: {dagitim['label']}"),
                "Vade": ONERILEN_VADE[SCAN_ZIRVE_DEVAM],
            })

        if (o["konum"] >= KONUM_SIKISMA and o["ust"]
                and np.isfinite(o["daralma"]) and o["daralma"] <= DARALMA_ESIK):
            sikisma.append({
                "Hisse": sym, "Fiyat": round(o["fiyat"], 2),
                "Konum": round(o["konum"], 1),
                "Daralma": round(o["daralma"], 2),
                "Aciklama": (f"Üst %{100 - o['konum']:.0f} bölgede ve oynaklık "
                             f"normalin %{o['daralma'] * 100:.0f}'ine düşmüş — "
                             f"sıkışma"),
                "Vade": ONERILEN_VADE[SCAN_ZIRVE_SIKISMA],
            })

    dd = pd.DataFrame(devam)
    sd = pd.DataFrame(sikisma)
    if not dd.empty:
        dd = dd.sort_values(
            ["Dagitim_Risk", "Konum"], ascending=[True, False]
        ).reset_index(drop=True)
    if not sd.empty:
        sd = sd.sort_values("Daralma").reset_index(drop=True)
    return dd, sd
