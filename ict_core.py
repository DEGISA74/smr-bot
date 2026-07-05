"""ICT CORE — ICT paneli hesap yardımcıları (4 Tem 2026)

Yeni hesap kodu app.py'ye yazılmaz kuralı gereği ICT geliştirme paketi bu
modülde; app.py (calculate_ict_deep_analysis + panel) sadece çağırır.

Kapsam (14 maddelik ICT paketi):
- 5  Swing tespiti: 2-bar mini fraktal → 5-bar fraktal + adaptif budama
- 6  Nötr bölgede yön: tek mum rengi → Dow zinciri + 5 bar eğim
- 7  Kırılım onay katmanı: onaylı / retest / onay bekleniyor + sahte kırılım
- 8  Uyarlanabilir eşikler: EQH/EQL toleransı + sweep penceresi
- 9  Bölge: %45-55 arası DENGE (tek %50 çizgisi titremesi biter)
- 10 FVG mitigasyon filtresi: dolan FVG "açık" sayılmaz (SMC grafikle tutarlı)
- 12 Model skor eşiği tek sabitte (Eylül 2026 karnesiyle kalibre edilecek)

⚠️ IC sözlüğündeki tüm eşikler BAŞLANGIÇ değeridir — Eylül/Ekim 2026
signal_returns karnesiyle kalibre edilecek (feedback_extrapolation_yasak).
"""

import numpy as np
import pandas as pd

import pattern_core

IC = dict(
    # 5 — swing tespiti
    swing_lookback=5,     # fraktal penceresi (eski: 2 → çok gürültülüydü)
    prune_scale=0.6,      # ICT yapısı formasyondan kısa vadeli → budama eşiği ×0.6
    # 8 — eşit tepe/dip toleransı (volatiliteye göre; eski sabit %0.3)
    eq_tol_lo=0.003, eq_tol_hi=0.012, eq_tol_scale=1 / 3.0,
    # 8 — sweep penceresi (sakin hissede sweep daha yavaş gelişir)
    sweep_win_calm=5, sweep_win_wild=3, sweep_wild_thr=0.04,
    # 9 — bölge sınırları
    zone_hi=0.55, zone_lo=0.45,
    # 7 — kırılım onayı
    confirm_days=3,       # bu kadar gün seviye ötesinde kapanış = onaylı
    confirm_lookback=15,  # onay sayacının üst sınırı
    retest_band=0.015,    # onay penceresinde seviyeye %1.5 yaklaşma = retest
    fake_lookback=10,     # sahte kırılım: son N barda kırıp geri dönme
    fake_margin=0.005,
    # 6 — nötr bölge eğim penceresi
    slope_bars=5,
)

# 12 — model skoru "güçlü" eşiği. Eylül 2026'da f_ict_model karnesi çıkınca
# tek buradan kalibre edilir (skor 4-5 gerçekten kazandırıyor mu?).
MODEL_SCORE_STRONG = 4


# ---------------------------------------------------------------------------
# 5 — Swing tespiti: fitil uçlarından 5-bar fraktal + adaptif budama
# ---------------------------------------------------------------------------
def ict_swings(df, lookback=None):
    """(sw_highs, sw_lows) döndürür — [(timestamp, fiyat, bar_index), ...]
    app.py'deki eski 2-bar fraktal formatıyla birebir uyumlu."""
    lb = int(lookback or IC['swing_lookback'])
    high = df['High'].values.astype(float)
    low = df['Low'].values.astype(float)
    close = df['Close'].values.astype(float)
    n = len(high)
    sw_h = []
    sw_l = []
    for i in range(lb, n - lb):
        if high[i] >= high[i - lb:i + lb + 1].max() - 1e-9:
            sw_h.append((i, float(high[i])))
        if low[i] <= low[i - lb:i + lb + 1].min() + 1e-9:
            sw_l.append((i, float(low[i])))
    thr = pattern_core.adaptive_threshold(close) * IC['prune_scale']
    sw_h, sw_l = pattern_core.prune_pivots(sw_h, sw_l, thr)
    idx = df.index
    sw_highs = [(idx[i], v, i) for i, v in sw_h]
    sw_lows = [(idx[i], v, i) for i, v in sw_l]
    return sw_highs, sw_lows


# ---------------------------------------------------------------------------
# 6 — Nötr bölgede yön: Dow zinciri öncelikli, yoksa 5 bar kapanış eğimi
# ---------------------------------------------------------------------------
def retrace_bias(close_arr, dow_desc):
    """Fiyat son swing aralığının içindeyken yön. Eski kural tek mumun
    rengine bakıyordu (her gün dönebiliyordu)."""
    try:
        d = str(dow_desc or "")
        if "Yükseliş" in d:
            return "bullish_retrace"
        if "Düşüş" in d:
            return "bearish_retrace"
        c = np.asarray(close_arr, dtype=float)
        nb = IC['slope_bars']
        if len(c) > nb and c[-1] >= c[-1 - nb]:
            return "bullish_retrace"
        return "bearish_retrace"
    except Exception:
        return "bearish_retrace"


# ---------------------------------------------------------------------------
# 7 — Kırılım onay katmanı
# ---------------------------------------------------------------------------
def break_confirm(close_arr, level, bullish=True):
    """Kırılım sonrası durum: kaç gündür seviye ötesinde kapanıyor?
    Döndürür: dict(status='pending'|'confirmed'|'retest', days, label)."""
    out = dict(status='pending', days=0, label="⏳ onay bekleniyor")
    try:
        c = np.asarray(close_arr, dtype=float)
        n = len(c)
        lvl = float(level)
        if n < 5 or lvl <= 0:
            return out
        days = 0
        for k in range(n - 1, -1, -1):
            beyond = c[k] > lvl if bullish else c[k] < lvl
            if not beyond:
                break
            days += 1
            if days >= IC['confirm_lookback']:
                break
        out['days'] = days
        if days >= IC['confirm_days']:
            seg = c[n - days:n]
            if bullish:
                near = float(np.min(seg)) <= lvl * (1 + IC['retest_band'])
            else:
                near = float(np.max(seg)) >= lvl * (1 - IC['retest_band'])
            if near and days > 1:
                out['status'] = 'retest'
                out['label'] = "✓ retest'le onaylı"
            else:
                out['status'] = 'confirmed'
                out['label'] = f"✓ {days} gün tutundu"
        else:
            out['label'] = f"⏳ onay bekleniyor ({max(days,1)}. gün)"
    except Exception:
        pass
    return out


def fake_break(close_arr, level, bullish=True):
    """Son N barda seviye kırılıp GERİ dönüldü mü? (tuzak / silkeleme)
    bullish=True → yukarı kırıp geri düşme; False → aşağı kırıp geri çıkma."""
    try:
        c = np.asarray(close_arr, dtype=float)
        lvl = float(level)
        if len(c) < IC['fake_lookback'] + 2 or lvl <= 0:
            return False
        seg = c[-IC['fake_lookback'] - 1:-1]
        cur = c[-1]
        if bullish:
            return bool((seg > lvl * (1 + IC['fake_margin'])).any() and cur < lvl)
        return bool((seg < lvl * (1 - IC['fake_margin'])).any() and cur > lvl)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 8 — Uyarlanabilir eşikler
# ---------------------------------------------------------------------------
def eq_tolerance(close_arr):
    """EQH/EQL eşitlik toleransı (yüzde, ondalık). Eski sabit %0.3 volatil
    tahtada hiç eşit dip yakalamıyordu."""
    try:
        thr = pattern_core.adaptive_threshold(np.asarray(close_arr, dtype=float))
        return float(min(max(thr * IC['eq_tol_scale'], IC['eq_tol_lo']), IC['eq_tol_hi']))
    except Exception:
        return 0.003


def sweep_window(close_arr):
    """Sweep arama penceresi: oynak hissede 3 bar, sakin hissede 5 bar."""
    try:
        thr = pattern_core.adaptive_threshold(np.asarray(close_arr, dtype=float))
        return IC['sweep_win_wild'] if thr >= IC['sweep_wild_thr'] else IC['sweep_win_calm']
    except Exception:
        return 3


# ---------------------------------------------------------------------------
# Displacement — FİYAT-öncelikli ORTAK ölçüm (5 Tem 2026)
# smr_core'un 13 Haz endeks/0-hacim fix'inin tek-kaynak hali: app paneli +
# bot bülteni aynı fonksiyonu kullanır. Hacim sadece TEYİT rozetidir —
# endeks/0-hacim barında "Hacimsiz Hareket" yanlış etiketi üretilmez.
# ---------------------------------------------------------------------------
def displacement_status(open_arr, close_arr, high_arr, low_arr, vol_arr, atr, avg_body_last):
    try:
        o = np.asarray(open_arr, dtype=float); c = np.asarray(close_arr, dtype=float)
        h = np.asarray(high_arr, dtype=float); l = np.asarray(low_arr, dtype=float)
        v = np.asarray(vol_arr, dtype=float)
        if len(c) < 21:
            return "Zayıf (Dar Hareket)"
        body = abs(c[-1] - o[-1])
        prev = c[-2]
        net = abs((c[-1] - prev) / prev) if prev > 0 else 0.0
        rng = float(h[-1] - l[-1])
        v20 = float(np.nanmean(v[-21:-1]))
        vol_ok = bool(v20 > 0 and v[-1] > v20 * 1.2)
        big_body = bool(avg_body_last and avg_body_last > 0 and body > avg_body_last * 1.1)
        strong_rng = bool(atr and atr > 0 and rng > atr * 1.2)
        price_strong = big_body or (net >= 0.01 and strong_rng)
        if price_strong and vol_ok:
            return "🔥 Güçlü Displacement (Hacim Onaylı)"
        if price_strong:
            return "🔥 Güçlü Displacement (Fiyat Hareketi)"
        return "Zayıf (Dar Hareket)"
    except Exception:
        return "Zayıf (Dar Hareket)"


# ---------------------------------------------------------------------------
# 9 — Bölge: üçlü (Premium / Denge / Discount)
# ---------------------------------------------------------------------------
def zone_of(range_loc):
    try:
        r = float(range_loc)
    except Exception:
        r = 0.5
    if r > IC['zone_hi']:
        return "PREMIUM (Pahalı)"
    if r < IC['zone_lo']:
        return "DISCOUNT (Ucuz)"
    return "DENGE (Orta Bölge)"


# ---------------------------------------------------------------------------
# 10 — FVG mitigasyon filtresi (SMC grafikle tutarlılık)
# ---------------------------------------------------------------------------
def filter_open_fvgs(fvgs, low_arr, high_arr, bullish=True):
    """Sonradan TAMAMEN doldurulan FVG'leri eler (artık 'açık' değil);
    kısmen test edilenlere state='tested', dokunulmamışlara 'fresh' yazar.
    Eski kod dolu FVG'yi hâlâ 'Açık FVG var (Destek)' diye sunabiliyordu."""
    out = []
    try:
        lo = np.asarray(low_arr, dtype=float)
        hi = np.asarray(high_arr, dtype=float)
        n = len(lo)
        for f in fvgs:
            i = int(f.get('idx', -1))
            if i < 0 or i + 1 >= n:
                g = dict(f)
                g['state'] = 'fresh'
                out.append(g)
                continue
            if bullish:
                later_low = float(lo[i + 1:n].min())
                if later_low <= f['bot'] + 1e-9:
                    continue                      # tamamen dolduruldu
                g = dict(f)
                g['state'] = 'tested' if later_low < f['top'] else 'fresh'
            else:
                later_high = float(hi[i + 1:n].max())
                if later_high >= f['top'] - 1e-9:
                    continue
                g = dict(f)
                g['state'] = 'tested' if later_high > f['bot'] else 'fresh'
            out.append(g)
    except Exception:
        return list(fvgs)
    return out
