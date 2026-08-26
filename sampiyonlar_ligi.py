# -*- coding: utf-8 -*-
"""
sampiyonlar_ligi.py — ŞAMPİYONLAR LİGİ (31 Tem 2026)
=====================================================
İki rejimli (boğa 1 Oca–18 Şub · düşen 1 May–10 Tem 2026) backtest + vade
taramasından çıkan EN İYİ 8 tarama. Tekli hisse analizinde bunlardan biri
ateşlerse fiyat kartı altında linkli buton çıkar (app.py), tıklayınca Tarama
Merkezi kurulum-detay pop-up'ı açılır.

Kaynak ölçüm: _vade_sweep.py (BOGA/DUSEN) + backtest_dip_ayna.py. Rakamlar
tek-rejim değil, iki rejim ayrı raporlanır (dürüstlük). [[feedback-extrapolation-yasak]]

Bu modül SAF hesap + veri: kadro sözlüğü + `hits()` dedektörü. Render app.py'de.
İçe bağımlılık: scanners (ER context) + zirve_taramalari (zirve ölçüsü). Döngü yok.
"""
import numpy as np
import pandas as pd

import scanners
import zirve_taramalari as _zt

# ── KADRO ─────────────────────────────────────────────────────────────────
# key: iç kimlik · ad: buton/başlık · emoji · tip LONG/SHORT · vade: ideal tutma
# karne_boga / karne_dusen: "getiri · isabet · BIST-alpha" (20g bazlı, vade notu ayrı)
SAMPIYONLAR = {
    "D5_ayna": {
        "ad": "Güç-Devam", "emoji": "🟢", "tip": "LONG", "vade": "20 gün",
        "aciklama": "50 günlük ortalama üstünde, hacimli yeşil gün ve endekse karşı güçlü — "
                    "gücün devamı. İki rejimde de kazanan tek 'her hava' kurulumu.",
        "karne_boga": "+%9.8 · isabet %68 · BIST+4.1",
        "karne_dusen": "+%4.2 · isabet %45 · BIST+5.8",
    },
    "zirve_devam": {
        "ad": "Zirvede Kalıcı", "emoji": "🏔", "tip": "LONG", "vade": "20 gün",
        "aciklama": "250 günün en üst %5'inde ve 50 günlük ortalama üstünde — zirvede güç. "
                    "Düşen/yatay piyasada şampiyon; boğada endeksin gerisinde kalabilir.",
        "karne_boga": "+%2.2 · isabet %69 · BIST−1.2 (zayıf)",
        "karne_dusen": "+%13.6 · isabet %73 · BIST+17.1",
    },
    "zirve_sikisma": {
        "ad": "Zirve Sıkışması", "emoji": "🎯", "tip": "LONG", "vade": "20 gün",
        "aciklama": "Üst %20 bölgede ve oynaklık normalin altına düşmüş — sıkışma sonrası "
                    "yukarı kırılım adayı.",
        "karne_boga": "az örnek",
        "karne_dusen": "+%3.9 · isabet %59 · BIST+7.4",
    },
    "er_B11": {
        "ad": "Tepede Yay Geriliyor", "emoji": "📐", "tip": "LONG", "vade": "20 gün",
        "aciklama": "60 günlük zirvenin yakınında sıkışıyor ve endekse karşı güçlü — "
                    "iki rejimde de istikrarlı pozitif.",
        "karne_boga": "+%4.1 · isabet %50 · BIST+3.5",
        "karne_dusen": "+%7.2 · isabet %48 · BIST+8.2",
    },
    "prelaunch_bos": {
        "ad": "Sıkışmayı Kırdı", "emoji": "🚀", "tip": "LONG", "vade": "20 gün",
        "aciklama": "3-5 hafta daraldıktan sonra son 3 günde 45 günlük zirvesini hacimle "
                    "kırdı — sıkışma bitti, hareket yeni başlıyor.",
        "karne_boga": "iki rejimde de pozitif alfa",
        "karne_dusen": "ortak pencerede BIST+3,7",
    },
    "er_C8": {
        "ad": "Yukarı Kanal Testi", "emoji": "🚀", "tip": "LONG", "vade": "20 gün",
        "aciklama": "Yukarı trendde 20 günlük ortalamaya çekilip toparlandı — boğa piyasası "
                    "canavarı; düşüşte sönük.",
        "karne_boga": "+%15.5 · isabet %63 · BIST+14.2",
        "karne_dusen": "+%1.0 · isabet %44 · BIST+2.0 (sönük)",
    },
    "er_C5": {
        "ad": "Bayrak Formasyonu", "emoji": "🚩", "tip": "LONG", "vade": "20 gün",
        "aciklama": "Sağlam trendde bayrak/pullback — boğada güçlü, düşüşte zayıf.",
        "karne_boga": "+%9.4 · isabet %54 · BIST+7.8",
        "karne_dusen": "+%1.6 · isabet %38 · BIST+2.6 (zayıf)",
    },
    # ── SHORT (kaçın/koru + short kurulumu) ──
    "er_D4": {
        "ad": "Kurumsal Satış Riski", "emoji": "⚠", "tip": "SHORT", "vade": "~15 gün",
        "aciklama": "Son 5 günde 2+ hacimli kırmızı kapanış (dağıtım) — alımdan kaçın; "
                    "short kurulumu olarak iki rejimde de zayıf hisseyi ayıklıyor.",
        "karne_boga": "short +%2.4 · isabet %59 · BIST+6.2",
        "karne_dusen": "short +%3.8 · isabet %67 · BIST+3.6",
    },
    "er_D5": {
        "ad": "Trend Bozuldu", "emoji": "⚠", "tip": "SHORT", "vade": "~12 gün",
        "aciklama": "50 ortalama altı + hacimli kırmızı + endekse karşı uzun süre zayıf — "
                    "yapı bozulmuş; short kurulumu olarak sağlam.",
        "karne_boga": "short +%4.1 · isabet %67 · BIST+10.0",
        "karne_dusen": "short +%4.6 · isabet %65 · BIST+5.1",
    },
}

# ateşleme sırası (LONG önce, kanıt gücüne göre yaklaşık)
_ORDER = ["D5_ayna", "zirve_devam", "prelaunch_bos", "zirve_sikisma", "er_B11", "er_C8", "er_C5", "er_D4", "er_D5"]
_ER_KEYS = {"er_B11": "B11", "er_C8": "C8", "er_C5": "C5", "er_D4": "D4", "er_D5": "D5"}


def _rs_pct(close, bench_close, days):
    """Hisse − endeks getirisi (yüzde puan), son `days` gün."""
    try:
        common = close.index.intersection(bench_close.index)
        if len(common) < days + 1:
            return 0.0
        s = close.loc[common].values.astype(float)
        b = bench_close.loc[common].values.astype(float)
        return float(((s[-1] / s[-days - 1] - 1) - (b[-1] / b[-days - 1] - 1)) * 100)
    except Exception:
        return 0.0


def _d5_ayna_fires(df, bench_df):
    """Güç-Devam (D5 aynası): 50MA üstü + bugün hacimli yeşil + RS60 > +%5."""
    try:
        if df is None or len(df) < 61:
            return False
        c = df["Close"].astype(float); o = df["Open"].astype(float); v = df["Volume"].astype(float)
        sma50 = c.rolling(50).mean().iloc[-1]
        if not np.isfinite(sma50) or c.iloc[-1] <= sma50:
            return False
        av20 = v.iloc[-21:-1].mean()
        accu = (c.iloc[-1] > o.iloc[-1]) and (v.iloc[-1] > av20 * 1.5)
        if not accu:
            return False
        bc = bench_df["Close"] if bench_df is not None else None
        if bc is None:
            return False
        return _rs_pct(c, bc, 60) > 5.0
    except Exception:
        return False


def _zirve_fires(df):
    """(zirve_devam, zirve_sikisma) bool ikilisi — zirve_taramalari kuralları."""
    try:
        o = _zt._olcumler(df)
        if o is None or not np.isfinite(o.get("konum", np.nan)):
            return False, False
        devam = o["konum"] >= _zt.KONUM_DEVAM and o["ust"]
        sik = (o["konum"] >= _zt.KONUM_SIKISMA and o["ust"]
               and np.isfinite(o.get("daralma", np.nan)) and o["daralma"] <= _zt.DARALMA_ESIK)
        return bool(devam), bool(sik)
    except Exception:
        return False, False


def hits(ticker, df, bench_df=None):
    """Bu hisse ŞU AN hangi şampiyonları ateşliyor? → kadro girdisi listesi (sıralı).
    df: hissenin OHLCV'si (son bar = bugün) · bench_df: XU100 ('Close' kolonlu)."""
    fired = set()

    # ER tabanlı (B11/C8/C5/D4/D5) — context'i bir kez kur, senaryo detect'lerini doğrudan çağır
    try:
        ctx = scanners._er_build_context(df, bench_df)
    except Exception:
        ctx = None
    if ctx is not None:
        for key, sid in _ER_KEYS.items():
            sc = scanners.ERKEN_RADAR_SCENARIOS.get(sid)
            if not sc:
                continue
            try:
                if sc["detect"](ctx):
                    fired.add(key)
            except Exception:
                pass

    # zirve ikilisi
    _devam, _sik = _zirve_fires(df)
    if _devam:
        fired.add("zirve_devam")
    if _sik:
        fired.add("zirve_sikisma")

    # D5 aynası (güç-devam)
    if _d5_ayna_fires(df, bench_df):
        fired.add("D5_ayna")

    # Sıkışmayı Kırdı (prelaunch_bos) — 17 Ağu 2026: ortak pencerede BIST+3,7 ölçüldü,
    # şampiyon kadrosuna alındı. Çekirdek hesap scanners'ta, burada sadece ateşleme.
    try:
        _bench_close = None
        if bench_df is not None and hasattr(bench_df, 'columns') and 'Close' in bench_df.columns:
            _bench_close = bench_df['Close'].dropna()
        if scanners.calculate_prelaunch_bos("", df, _bench_close):
            fired.add("prelaunch_bos")
    except Exception:
        pass

    # 17 Ağu 2026 — ELEME KAPISI: Şampiyonlar Ligi senaryo detect'lerini DOĞRUDAN
    # çağırıyor, evaluate_erken_radar'daki filtreyi atlıyordu → elenen er_C5/D4/D5
    # burada yaşamaya devam ediyordu. Tek noktadan süzülür.
    try:
        from evidence import elendi_mi as _elendi
        fired = {k for k in fired if not _elendi(k)}
    except Exception:
        pass

    out = []
    for key in _ORDER:
        if key in fired:
            e = dict(SAMPIYONLAR[key]); e["key"] = key
            out.append(e)
    return out
