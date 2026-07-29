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


# ═══════════════════════════════════════════════════════════════════════════
# Adım 7 (9 Tem 2026) — ANALİZ MOTORLARI app.py'den BİREBİR taşındı
# (davranış değişikliği YOK, sadece adres). ICT Deep + PA-DNA + Minervini SEPA +
# Harmonik küme + PA-with-context. Girdi genelde ticker → veriyi data_layer'dan
# çeker. Fotoğraf: golden_record ict_deep/pa_dna/... hedefleri (sıfır fark).
# ═══════════════════════════════════════════════════════════════════════════
import os
import random
import pytz
import yfinance as yf
import streamlit as st
from datetime import datetime, timedelta

from ta.volume import VolumeWeightedAveragePrice
from data_policy import AUTO_ADJUST
from data_layer import (get_safe_historical_data, _yf_download_with_retry,
                        is_last_bar_projected, final_bist100_list as _BIST_TICKERS)
from indicators import (calculate_full_volume_profile, calculate_volume_delta,
                        compute_cmf, detect_naked_poc, detect_supply_demand_zones,
                        find_smart_sr_levels, compute_force_index_dual)
try:
    from bist_calendar import (is_closed as _bist_is_closed,
                               get_rvol_day_factor as _bist_rvol_factor,
                               get_session_hours as _bist_session_hours)
    _BIST_CAL_OK = True
except ImportError:
    def _bist_is_closed(_dt=None):   return False
    def _bist_rvol_factor(_dt=None): return 1.0
    def _bist_session_hours(_dt=None): return ("10:00", "18:00")
    _BIST_CAL_OK = False

_TZ_ISTANBUL = pytz.timezone("Europe/Istanbul")

# Profil altyapısı (app.py ~126-155 ile aynı) — SMR_PROFILE=1 ile aktif, kapalıyken no-op.
_PROFILE_ENABLED = os.environ.get('SMR_PROFILE', '0') == '1'
if _PROFILE_ENABLED:
    import time as _ptime
    _profile_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'profile.log')
    try:
        os.makedirs(os.path.dirname(_profile_log_path), exist_ok=True)
    except Exception:
        pass
    def _tlog(name, elapsed_ms, extra=""):
        try:
            with open(_profile_log_path, 'a', encoding='utf-8') as _f:
                _f.write(f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} | {name:48s} | {elapsed_ms:>8.1f} ms {extra}\n")
        except Exception:
            pass
    class _Timer:
        def __init__(self, name, extra=""):
            self.name = name; self.extra = extra
        def __enter__(self):
            self.t0 = _ptime.perf_counter(); return self
        def __exit__(self, *a):
            _tlog(self.name, (_ptime.perf_counter() - self.t0) * 1000, self.extra)
else:
    class _Timer:
        def __init__(self, name, extra=""): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
    def _tlog(name, elapsed_ms, extra=""): pass


def _harmonic_zigzag(high, low, window=3):
    """Basit zigzag pivot noktaları — scipy gerektirmez."""
    n = len(high)
    pivots = []  # (bar_idx, price, 'H'|'L')
    i = window
    while i < n - window:
        h_win = high[max(0, i - window): i + window + 1]
        l_win = low[max(0, i - window): i + window + 1]
        is_peak   = float(high[i]) >= max(h_win) - 1e-9
        is_trough = float(low[i])  <= min(l_win) + 1e-9
        if is_peak and not is_trough:
            if not pivots or pivots[-1][2] == 'L':
                pivots.append((i, float(high[i]), 'H'))
            elif pivots[-1][2] == 'H' and float(high[i]) > pivots[-1][1]:
                pivots[-1] = (i, float(high[i]), 'H')
        elif is_trough and not is_peak:
            if not pivots or pivots[-1][2] == 'H':
                pivots.append((i, float(low[i]), 'L'))
            elif pivots[-1][2] == 'L' and float(low[i]) < pivots[-1][1]:
                pivots[-1] = (i, float(low[i]), 'L')
        i += 1
    return pivots

def _check_harmonic_ratio(ratio, target=None, tol=0.06, lo=None, hi=None):
    """Nokta veya aralık kontrolü."""
    if target is not None:
        return abs(ratio - target) <= tol
    return lo <= ratio <= hi

def detect_price_action_with_context(df):
    """
    1. Smart Money (Likidite Avı / Fitil Reddi) arar.
    2. Klasik Dönüş Mumlarını (Engulfing, Morning Star, vb.) arar.
    3. Fibonacci Pinbar dönüşlerini arar.
    4. Bunların Anlamlı Kurumsal Seviyelere (Confluence) denk gelip gelmediğini kontrol eder.
    """
    if len(df) < 50: 
        return "NÖTR", ""

    # Son 3 mumun verilerini alıyoruz (Formasyonlar için gerekli)
    curr = df.iloc[-1]   # 3. Mum (Bugün)
    prev = df.iloc[-2]   # 2. Mum (Dün)
    prev2 = df.iloc[-3]  # 1. Mum (Evvelsi gün)
    
    # Kısaltmalar
    O3, C3, H3, L3 = curr['Open'], curr['Close'], curr['High'], curr['Low']
    O2, C2, H2, L2 = prev['Open'], prev['Close'], prev['High'], prev['Low']
    O1, C1, H1, L1 = prev2['Open'], prev2['Close'], prev2['High'], prev2['Low']

    # --- 1. KURUMSAL SEVİYELERİN (CONFLUENCE) HESAPLANMASI ---
    sma50 = df['Close'].rolling(50).mean().iloc[-1] if len(df) >= 50 else 0
    sma100 = df['Close'].rolling(100).mean().iloc[-1] if len(df) >= 100 else 0
    sma200 = df['Close'].rolling(200).mean().iloc[-1] if len(df) >= 200 else 0
    ema89 = df['Close'].ewm(span=89, adjust=False).mean().iloc[-1] if len(df) >= 89 else 0
    ema144 = df['Close'].ewm(span=144, adjust=False).mean().iloc[-1] if len(df) >= 144 else 0
    
    pdh, pdl = H2, L2 # Önceki Günün Tepesi ve Dibi

    # TAM DONANIMLI FIBONACCI HESAPLAMASI (Son 40 Günlük Dalga Boyu)
    recent_40 = df.iloc[-40:]
    wave_high = recent_40['High'].max()
    wave_low = recent_40['Low'].min()
    fib_382 = wave_high - (wave_high - wave_low) * 0.382
    fib_500 = wave_high - (wave_high - wave_low) * 0.500
    fib_618 = wave_high - (wave_high - wave_low) * 0.618
    fib_786 = wave_high - (wave_high - wave_low) * 0.786

    def is_near(price, level):
        if pd.isna(level) or level == 0: return False
        return abs(price - level) / level < 0.015 

    bounced_from = []   
    rejected_from = []  
    
    # Destekler
    if is_near(L3, sma50): bounced_from.append("SMA50 Desteği")
    if is_near(L3, sma100): bounced_from.append("SMA100 Desteği")
    if is_near(L3, sma200): bounced_from.append("SMA200 Majör Desteği")
    if is_near(L3, ema89): bounced_from.append("EMA89")
    if is_near(L3, ema144): bounced_from.append("EMA144")
    if is_near(L3, pdl): bounced_from.append("PDL (Dünün Dibi)")
    if is_near(L3, fib_382): bounced_from.append("Fib %38.2 Desteği")
    if is_near(L3, fib_500): bounced_from.append("Fib %50.0 (Denge) Desteği")
    if is_near(L3, fib_618) or is_near(L3, fib_786): bounced_from.append("ICT OTE (Altın Oran)")

    # Dirençler
    if is_near(H3, sma50): rejected_from.append("SMA50 Direnci")
    if is_near(H3, sma100): rejected_from.append("SMA100 Direnci")
    if is_near(H3, sma200): rejected_from.append("SMA200 Majör Direnci")
    if is_near(H3, ema89): rejected_from.append("EMA89")
    if is_near(H3, ema144): rejected_from.append("EMA144")
    if is_near(H3, pdh): rejected_from.append("PDH (Dünün Tepesi)")
    if is_near(H3, fib_382): rejected_from.append("Fib %38.2 Direnci")
    if is_near(H3, fib_500): rejected_from.append("Fib %50.0 (Denge) Direnci")
    if is_near(H3, fib_618) or is_near(H3, fib_786): rejected_from.append("ICT OTE (Altın Oran)")

    # --- 2. MUM ANATOMİSİ VE FORMASYONLARIN TESPİTİ ---
    body3, body2, body1 = abs(C3 - O3), abs(C2 - O2), abs(C1 - O1)
    is_green3, is_red3 = C3 > O3, C3 < O3
    is_green2, is_red2 = C2 > O2, C2 < O2
    is_green1, is_red1 = C1 > O1, C1 < O1

    found_bullish_pattern = ""
    found_bearish_pattern = ""

    lower_wick3 = min(O3, C3) - L3
    upper_wick3 = H3 - max(O3, C3)

    # 🚨 HATA DÜZELTME: EKSİK TANIMLAMALAR BURAYA EKLENDİ 🚨
    dow_suffix_bull = ""
    dow_suffix_bear = ""
    try:
        # Son 15 günün en düşük ve en yüksek seviyelerine bakarak HL/LH tespiti
        recent_min = df['Low'].iloc[-15:-3].min()
        recent_max = df['High'].iloc[-15:-3].max()
        
        if L3 >= recent_min: 
            dow_suffix_bull = " + Yükselen Dip (HL) Onayı 🔥"
        else:
            dow_suffix_bull = " + Yeni Dip (LL) Riskli Dönüş ⚠️"
            
        if H3 <= recent_max:
            dow_suffix_bear = " + Alçalan Tepe (LH) Baskısı 🩸"
        else:
            dow_suffix_bear = " + Yeni Tepe (HH) Fırsatı 🚀"
    except:
        pass

    # A. SMART MONEY (LİKİDİTE AVI VE V-DÖNÜŞ)
    if is_red2 and (L3 < L2) and (lower_wick3 > body3 * 1.5 or (is_green3 and C3 > C2)):
        found_bullish_pattern = "Smart Money Likidite Avı (V-Dönüşü)"
        
    elif is_green2 and (H3 > H2) and (upper_wick3 > body3 * 1.5 or (is_red3 and C3 < C2)):
        found_bearish_pattern = "Smart Money Boğa Tuzağı (V-Dönüşü)"

    # B. KLASİK VE FIBONACCI DÖNÜŞ FORMASYONLARI (BULLISH)
    if not found_bullish_pattern:
        # 1. Fibonacci Nokta Atışı (Pinbar)
        is_touching_bull_fib = is_near(L3, fib_382) or is_near(L3, fib_500) or is_near(L3, fib_618) or is_near(L3, fib_786)
        if is_touching_bull_fib and is_green3 and lower_wick3 > (body3 * 1.5):
            found_bullish_pattern = f"Fibonacci Nokta Atışı (Pinbar Rejection){dow_suffix_bull}"
        
        # 2. Bullish Engulfing (Yutan Boğa)
        elif is_red2 and is_green3 and C3 > O2 and O3 < C2:
            found_bullish_pattern = f"Yutan Boğa (Bullish Engulfing){dow_suffix_bull}"
            
        # 3. Three Inside Up (Harami Onaylı) - H1 VE L1 KULLANILDI
        elif is_red1 and (max(O2, C2) < O1) and (min(O2, C2) > C1) and is_green3 and C3 > H1:
            found_bullish_pattern = f"Three Inside Up (Harami Onaylı){dow_suffix_bull}"

        # 4. Morning Star (Sabah Yıldızı)
        elif is_red1 and body2 < (body1 * 0.5) and max(O2, C2) <= C1 and is_green3 and C3 > (O1 + C1) / 2:
            found_bullish_pattern = f"Sabah Yıldızı (Morning Star){dow_suffix_bull}"
            
        # 5. Three Outside Up
        elif is_red1 and is_green2 and C2 > O1 and O2 < C1 and is_green3 and C3 > C2:
            found_bullish_pattern = f"Three Outside Up{dow_suffix_bull}"
            
        # 6. Piercing Line (Delen Mum)
        elif is_red2 and is_green3 and O3 <= C2 and C3 > (O2 + C2) / 2:
            found_bullish_pattern = f"Delen Mum (Piercing Line){dow_suffix_bull}"

    # C. KLASİK VE FIBONACCI DÖNÜŞ FORMASYONLARI (BEARISH)
    if not found_bearish_pattern:
        # 1. Fibonacci Nokta Atışı (Pinbar)
        is_touching_bear_fib = is_near(H3, fib_382) or is_near(H3, fib_500) or is_near(H3, fib_618) or is_near(H3, fib_786)
        if is_touching_bear_fib and is_red3 and upper_wick3 > (body3 * 1.5):
            found_bearish_pattern = f"Fibonacci Nokta Atışı (Pinbar Rejection){dow_suffix_bear}"

        # 2. Bearish Engulfing (Yutan Ayı)
        elif is_green2 and is_red3 and C3 < O2 and O3 > C2:
            found_bearish_pattern = f"Yutan Ayı (Bearish Engulfing){dow_suffix_bear}"
            
        # 3. Three Inside Down (Harami Onaylı) - H1 VE L1 KULLANILDI
        elif is_green1 and (max(O2, C2) < C1) and (min(O2, C2) > O1) and is_red3 and C3 < L1:
            found_bearish_pattern = f"Three Inside Down (Harami Onaylı){dow_suffix_bear}"

        # 4. Evening Star (Akşam Yıldızı)
        elif is_green1 and body2 < (body1 * 0.5) and min(O2, C2) >= C1 and is_red3 and C3 < (O1 + C1) / 2:
            found_bearish_pattern = f"Akşam Yıldızı (Evening Star){dow_suffix_bear}"
            
        # 5. Three Outside Down
        elif is_green1 and is_red2 and C2 < O1 and O2 > C1 and is_red3 and C3 < C2:
            found_bearish_pattern = f"Three Outside Down{dow_suffix_bear}"
            
        # 6. Kara Bulut (Dark Cloud Cover)
        elif is_green2 and is_red3 and O3 >= C2 and C3 < (O2 + C2) / 2:
            found_bearish_pattern = f"Kara Bulut (Dark Cloud Cover){dow_suffix_bear}"

    # --- 3. SONUÇLARIN YAPAY ZEKAYA AKTARIMI ---
    if found_bullish_pattern:
        conf_txt = " + ".join(bounced_from) if bounced_from else "Ara Bölge (Majör Destek Yok)"
        return "PA_BULLISH", f"{found_bullish_pattern} | Kesişim: {conf_txt}"

    if found_bearish_pattern:
        conf_txt = " + ".join(rejected_from) if rejected_from else "Ara Bölge (Majör Direnç Yok)"
        return "PA_BEARISH", f"{found_bearish_pattern} | Kesişim: {conf_txt}"

    return "NÖTR", ""

def _bos_sonuc_rafa_konmasin(_cached_fn, _ticker, _sonuc, _bos_mu, *_extra_keys):
    """BOŞ SONUÇ RAFA KONMAZ (17 Tem 2026 — XBANK vakası).

    st.cache_data başarısız sonucu da raf ömrü boyunca saklar: veri bekçisi bir
    ticker'ı geçici bloklarsa panel "veri yok" cevabını rafa koyup blok kalksa
    bile göstermeye devam eder (PA-DNA'da 24 SAAT). Çare: sonuç boşsa o
    ticker'ın raf kaydını hemen sil → sonraki render yeniden hesaplar.
    Sadece o ticker'ın kaydı düşer, diğerleri sıcak kalır (Streamlit 1.36+).

    27 Tem 2026: _extra_keys — cached fonksiyonun ticker DIŞINDA anahtar parametresi
    varsa (örn. PA-DNA gun_key) doğru kaydı temizlemek için iletilir. Parametresiz
    çağıranlar (ICT deep) etkilenmez — geriye tam uyumlu."""
    if _bos_mu:
        try:
            _cached_fn.clear(_ticker, *_extra_keys)
        except Exception:
            pass  # raf temizliği başarısızsa akışı KESME — eski davranış sürsün
    return _sonuc


def calculate_ict_deep_analysis(ticker):
    out = _calculate_ict_deep_analysis_cached(ticker)
    return _bos_sonuc_rafa_konmasin(
        _calculate_ict_deep_analysis_cached, ticker, out,
        not out or out.get("status") == "Error")


@st.cache_data(ttl=600)
def _calculate_ict_deep_analysis_cached(ticker):
    error_ret = {"status": "Error", "msg": "Veri Yok", "structure": "-", "bias": "-", "entry": 0, "target": 0, "structural_target": 0, "stop": 0, "rr": 0, "desc": "Veri bekleniyor", "displacement": "-", "fvg_txt": "-", "ob_txt": "-", "zone": "-", "mean_threshold": 0, "curr_price": 0, "setup_type": "BEKLE", "bottom_line": "-", "eqh_eql_txt": "-", "sweep_txt": "-", "model_score": 0, "model_checks": [], "ob_age": 0, "fvg_age": 0, "struct_age": 0}
    
    try:
        df = get_safe_historical_data(ticker, period="1y")
        if df is None or len(df) < 60: return error_ret
        
        high = df['High']; low = df['Low']; close = df['Close']; open_ = df['Open']
        
        tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        avg_body_size = abs(open_ - close).rolling(20).mean()

        # 5 — Swing tespiti ict_core'a taşındı: 2-bar mini fraktal yerine
        # 5-bar fraktal + volatiliteye uyarlanan pivot budama. Bias artık tek
        # gürültülü pivota göre dönmez (panel + FIYAT kartı + AI prompt stabil).
        sw_highs, sw_lows = ict_swings(df)

        if not sw_highs or not sw_lows: return error_ret

        curr_price = close.iloc[-1]
        last_sh = sw_highs[-1][1] 
        last_sl = sw_lows[-1][1]  

        # --- 👇 YENİ: DOW TEORİSİ (ZİNCİRLEME TREND OKUMASI HH/HL) 👇 ---
        dow_pattern = "Belirsiz"
        dow_desc = "Nötr"
        if len(sw_highs) >= 2 and len(sw_lows) >= 2:
            h1 = sw_highs[-1][1]; h2 = sw_highs[-2][1] # h1 son tepe, h2 bir önceki tepe
            l1 = sw_lows[-1][1]; l2 = sw_lows[-2][1]   # l1 son dip, l2 bir önceki dip
            
            h_txt = "HH (Yükselen Tepe)" if h1 >= h2 else "LH (Alçalan Tepe)"
            l_txt = "HL (Yükselen Dip)" if l1 >= l2 else "LL (Alçalan Dip)"
            dow_pattern = f"{h_txt} / {l_txt}"
            
            # Trendin Anatomisi (Yapay Zeka Mantığı)
            if h1 > h2 and l1 > l2:
                dow_desc = "Güçlü Yükseliş Zinciri"
            elif h1 < h2 and l1 < l2:
                dow_desc = "Güçlü Düşüş Zinciri"
            elif h1 < h2 and l1 > l2:
                dow_desc = "Sıkışma (Zayıflayan Momentum / Düzeltme)"
            elif h1 > h2 and l1 < l2:
                dow_desc = "Genişleyen Volatilite (Yön Arayışı)"
        # --- 👆 -------------------------------------------------------- 👆 ---

        # --- BİAS VE YAPI TESPİTİ ---
        structure = "YATAY / KONSOLİDE"
        bias = "neutral"
        displacement_txt = "Zayıf (Hacimsiz Hareket)"
        
        # MSS (Market Structure Shift) Tespiti için bir önceki bias kontrolü
        prev_close = close.iloc[-2]
        is_prev_bearish = prev_close < last_sl
        is_prev_bullish = prev_close > last_sh

        # 5 Tem 2026 — Displacement ORTAK fonksiyona taşındı (ict_core).
        # Bot'un 13 Haz endeks/0-hacim fix'i app'e geldi: fiyat-öncelikli ölçüm,
        # hacim sadece teyit rozeti. XU100 gibi 0-hacim endekslerde "Hacimsiz
        # Hareket (Sahte Olabilir)" yanlış etiketi artık üretilmez.
        displacement_txt = displacement_status(
            open_.values, close.values, high.values, low.values,
            df['Volume'].values,
            float(atr) if pd.notna(atr) else 0.0,
            float(avg_body_size.iloc[-1]) if pd.notna(avg_body_size.iloc[-1]) else 0.0)

        breakout_margin_up   = (curr_price - last_sh) / last_sh if last_sh > 0 else 0
        breakout_margin_down = (last_sl - curr_price) / last_sl if last_sl > 0 else 0

        if curr_price > last_sh:
            if is_prev_bearish:
                structure = f"MSS (Trend Döndü) 🐂 | {dow_desc}"
            elif breakout_margin_up < 0.005:
                structure = f"⚠️ Zayıf Kırılım — Onay Bekleniyor 🐂 | {dow_desc}"
            else:
                structure = f"BOS (Yükseliş Kırılımı) 🐂 | {dow_desc}"
            # 7 — Kırılım onay katmanı: kaç gündür seviyenin üstünde kapanıyor?
            _bc = break_confirm(close.values, float(last_sh), bullish=True)
            structure += f" [{_bc['label']}]"
            bias = "bullish"
        elif curr_price < last_sl:
            if is_prev_bullish:
                structure = f"MSS (Trend Döndü) 🐻 | {dow_desc}"
            elif breakout_margin_down < 0.005:
                structure = f"⚠️ Zayıf Kırılım — Onay Bekleniyor 🐻 | {dow_desc}"
            else:
                structure = f"BOS (Düşüş Kırılımı) 🐻 | {dow_desc}"
            _bc = break_confirm(close.values, float(last_sl), bullish=False)
            structure += f" [{_bc['label']}]"
            bias = "bearish"
        else:
            if len(sw_highs) >= 2 and len(sw_lows) >= 2:
                _h1 = sw_highs[-1][1]; _h2 = sw_highs[-2][1]
                _l1 = sw_lows[-1][1];  _l2 = sw_lows[-2][1]
                if _h1 > _h2 and _l1 > _l2:
                    structure = f"📦 Boğa Sıkışması — Kırılım Yukarı Olabilir | {dow_pattern}"
                elif _h1 < _h2 and _l1 < _l2:
                    structure = f"📦 Ayı Sıkışması — Dikkatli Ol | {dow_pattern}"
                else:
                    structure = f"Internal Range | Dow: {dow_pattern}"
            else:
                structure = f"Internal Range | Dow: {dow_pattern}"
            # 7 — Sahte kırılım etiketi: son 10 barda kırıp geri dönme (tuzak)
            if fake_break(close.values, float(last_sh), bullish=True):
                structure = f"🪤 Sahte Yukarı Kırılım (tuzak olabilir) | {structure}"
            elif fake_break(close.values, float(last_sl), bullish=False):
                structure = f"🪤 Sahte Aşağı Kırılım (silkeleme) | {structure}"
            # 6 — Nötr bölgede yön: tek mum rengi yerine Dow zinciri + 5 bar eğim
            bias = retrace_bias(close.values, dow_desc)

        # --- YAPISAL HEDEF (swing tabanlı, orta vade) ---
        next_bsl = min([h[1] for h in sw_highs if h[1] > curr_price], default=float(high.max()))
        next_ssl = max([l[1] for l in sw_lows  if l[1] < curr_price], default=float(low.min()))
        structural_target = next_bsl if "bullish" in bias else next_ssl

        # --- MIKNATIS (DOL): en yakın likidite havuzları — 3: çift hesap silindi,
        # yukarıdaki next_bsl/next_ssl aynen kullanılıyor.
        # Ayı piyasasında mıknatıs aşağıdaki DİP, Boğa piyasasında yukarıdaki TEPE'dir.
        magnet_target = next_bsl if "bullish" in bias else next_ssl
        # --- LİKİDİTE HAVUZLARI (EQH / EQL) VE LİKİDİTE AVI (SWEEP) ---
        eqh_eql_txt = "Yok"
        sweep_txt = "Yok"

        # 8 — Eşitlik toleransı volatiliteye uyarlanır (eski sabit %0.3)
        tol = curr_price * eq_tolerance(close.values)
        
        # EQL / EQH (Eşit Tepe ve Dipler) Tespiti
        if len(sw_lows) >= 2:
            l1 = sw_lows[-1][1]; l2 = sw_lows[-2][1]
            if abs(l1 - l2) < tol: eqh_eql_txt = f"EQL (Eşit Dipler): {l1:.2f}"
                
        if len(sw_highs) >= 2:
            h1 = sw_highs[-1][1]; h2 = sw_highs[-2][1]
            if abs(h1 - h2) < tol:
                if eqh_eql_txt == "Yok": eqh_eql_txt = f"EQH (Eşit Tepeler): {h1:.2f}"
                else: eqh_eql_txt += f" | EQH: {h1:.2f}"

        # LİKİDİTE AVI (SWEEP / TURTLE SOUP) Tespiti
        # 8 — Pencere volatiliteye uyarlanır: oynak hissede 3, sakin hissede 5 bar
        _sw_win = sweep_window(close.values)
        recent_lows = low.iloc[-_sw_win:]
        recent_highs = high.iloc[-_sw_win:]
        
        # BSL Sweep (Tepe Likidite Avı - Ayı Sinyali)
        if (recent_highs.max() > last_sh) and (close.iloc[-1] < last_sh):
            sweep_txt = f"🧹 BSL Sweep (Tepe Avı): {last_sh:.2f}"
            
        # SSL Sweep (Dip Likidite Avı - Boğa Sinyali)
        elif (recent_lows.min() < last_sl) and (close.iloc[-1] > last_sl):
            sweep_txt = f"🧹 SSL Sweep (Dip Avı): {last_sl:.2f}"
        # --- 👆 ------------------------------------------------------------- 👆 ---
        # FVG ve OB Taraması
        _ob_l = _ob_h = _fvg_l = _fvg_h = 0.0   # Fiyat cetveli için sayısal değerler
        bullish_fvgs = []; bearish_fvgs = []
        active_fvg_txt = "Yok"
        for i in range(len(df)-30, len(df)-1):
            if i < 2: continue
            if low.iloc[i] > high.iloc[i-2]:
                gap_size = low.iloc[i] - high.iloc[i-2]
                if gap_size > atr * 0.05:
                    bullish_fvgs.append({'top': low.iloc[i], 'bot': high.iloc[i-2], 'idx': i})
            elif high.iloc[i] < low.iloc[i-2]:
                gap_size = low.iloc[i-2] - high.iloc[i]
                if gap_size > atr * 0.05:
                    bearish_fvgs.append({'top': low.iloc[i-2], 'bot': high.iloc[i], 'idx': i})

        # 10 — Mitigasyon filtresi: sonradan TAMAMEN doldurulan FVG "açık" değildir
        # (SMC grafiğin state mantığıyla tutarlılık); test edilenler etiketlenir.
        bullish_fvgs = filter_open_fvgs(bullish_fvgs, low.values, high.values, bullish=True)
        bearish_fvgs = filter_open_fvgs(bearish_fvgs, low.values, high.values, bullish=False)

        active_ob_txt = "Yok"
        mean_threshold = 0.0
        lookback = 20
        start_idx = max(0, len(df) - lookback)
        ob_bar_idx  = -1   # OB'un oluştuğu bar (yaş hesabı için)
        fvg_bar_idx = -1   # FVG'nin açıldığı bar (yaş hesabı için)

        # OB kalite değerlendirmesi için hacim ortalaması
        avg_vol_20 = df['Volume'].rolling(20).mean()

        # ── OB GENİŞLİK FİLTRESİ — ATR bazlı ──────────────────────────────
        # Gerçek kurumsal OB'lar dar ve spesifiktir. Volatilite günlerinde
        # oluşan geniş mumlar (>1.8×ATR) gerçek OB değil, gürültüdür.
        try:
            _tr_h_l = df['High'] - df['Low']
            _tr_h_c = (df['High'] - df['Close'].shift()).abs()
            _tr_l_c = (df['Low']  - df['Close'].shift()).abs()
            _true_range = pd.concat([_tr_h_l, _tr_h_c, _tr_l_c], axis=1).max(axis=1)
            _atr14 = _true_range.rolling(14).mean()
        except Exception:
            _atr14 = None

        def _ob_width_ok(ob_idx, ob_low, ob_high):
            """OB genişliği ATR ile karşılaştırılır. Çok geniş ise False döner."""
            if _atr14 is None:
                return True
            try:
                _atr_i = float(_atr14.iloc[ob_idx])
                if _atr_i <= 0 or pd.isna(_atr_i):
                    return True
                # OB genişliği ATR'nin 1.8 katından geniş ise gürültü
                return (ob_high - ob_low) <= 1.8 * _atr_i
            except Exception:
                return True

        def _ob_quality(ob_idx, ob_low, ob_high, is_bullish_ob):
            """A: Hacim kalitesi  B: FVG çakışması  C: Tazelik"""
            tags = []
            # A — OB mumunun hacmi ortalama üzerinde mi?
            try:
                ob_vol = float(df['Volume'].iloc[ob_idx])
                avg_v  = float(avg_vol_20.iloc[ob_idx])
                if avg_v > 0 and ob_vol > avg_v * 1.2:
                    tags.append("🏦 Kurumsal Hacim")
            except: pass
            # B — OB bölgesiyle örtüşen FVG var mı?
            try:
                check_fvgs = bullish_fvgs if is_bullish_ob else bearish_fvgs
                for fvg in check_fvgs:
                    overlap = min(ob_high, fvg['top']) - max(ob_low, fvg['bot'])
                    if overlap > 0:
                        tags.append("🎯 FVG+OB Çakışma")
                        break
            except: pass
            # C — Tazelik: OB oluşumundan sonra fiyat bu bölgeye geri döndü mü?
            try:
                future_prices = close.iloc[ob_idx+1:]
                if is_bullish_ob:
                    revisits = (future_prices <= ob_high).sum()
                else:
                    revisits = (future_prices >= ob_low).sum()
                if revisits == 0:
                    tags.append("✨ Taze OB (İlk Test)")
                elif revisits <= 2:
                    tags.append("⚡ OB 2. Test")
                else:
                    tags.append("⚠️ Yıpranmış OB")
            except: pass
            return " | ".join(tags) if tags else ""

        if bias == "bullish" or bias == "bullish_retrace":
            if bullish_fvgs:
                f = bullish_fvgs[-1]
                _fvg_state = " · test edildi" if f.get('state') == 'tested' else ""
                active_fvg_txt = f"Açık FVG var (Destek): {f['bot']:.2f} - {f['top']:.2f}{_fvg_state}"
                fvg_bar_idx = f['idx']
                _fvg_l = f['bot']; _fvg_h = f['top']
            lowest_idx = df['Low'].iloc[start_idx:].idxmin()
            if isinstance(lowest_idx, pd.Timestamp): lowest_idx = df.index.get_loc(lowest_idx)
            for i in range(lowest_idx, max(0, lowest_idx-5), -1):
                if df['Close'].iloc[i] < df['Open'].iloc[i]:
                    ob_low = df['Low'].iloc[i]; ob_high = df['High'].iloc[i]
                    # Genişlik filtresi: ATR'den çok geniş mum gerçek OB değil
                    if not _ob_width_ok(i, ob_low, ob_high):
                        continue
                    ob_q = _ob_quality(i, ob_low, ob_high, True)
                    ob_q_txt = f" [{ob_q}]" if ob_q else ""
                    if ob_high >= curr_price:
                        break  # OB fiyatın üstünde → Talep değil, gösterme
                    active_ob_txt = f"{ob_low:.2f} - {ob_high:.2f} (Talep Bölgesi){ob_q_txt}"
                    mean_threshold = (ob_low + ob_high) / 2
                    _ob_l = ob_low; _ob_h = ob_high
                    ob_bar_idx = i
                    break
        elif bias == "bearish" or bias == "bearish_retrace":
            if bearish_fvgs:
                f = bearish_fvgs[-1]
                _fvg_state = " · test edildi" if f.get('state') == 'tested' else ""
                active_fvg_txt = f"Açık FVG var (Direnç): {f['bot']:.2f} - {f['top']:.2f}{_fvg_state}"
                fvg_bar_idx = f['idx']
                _fvg_l = f['bot']; _fvg_h = f['top']
            highest_idx = df['High'].iloc[start_idx:].idxmax()
            if isinstance(highest_idx, pd.Timestamp): highest_idx = df.index.get_loc(highest_idx)
            for i in range(highest_idx, max(0, highest_idx-5), -1):
                if df['Close'].iloc[i] > df['Open'].iloc[i]:
                    ob_low = df['Low'].iloc[i]; ob_high = df['High'].iloc[i]
                    # Genişlik filtresi: ATR'den çok geniş mum gerçek OB değil
                    if not _ob_width_ok(i, ob_low, ob_high):
                        continue
                    ob_q = _ob_quality(i, ob_low, ob_high, False)
                    ob_q_txt = f" [{ob_q}]" if ob_q else ""
                    if ob_low <= curr_price:
                        break  # OB fiyatın altında → Arz değil, gösterme
                    active_ob_txt = f"{ob_low:.2f} - {ob_high:.2f} (Arz Bölgesi){ob_q_txt}"
                    mean_threshold = (ob_low + ob_high) / 2
                    _ob_l = ob_low; _ob_h = ob_high
                    ob_bar_idx = i
                    break

        range_high = max(high.tail(60)); range_low = min(low.tail(60))
        range_loc = (curr_price - range_low) / (range_high - range_low) if range_high > range_low else 0.5
        # 9 — Üçlü bölge: %45-55 arası DENGE (tek %50 çizgisi titremesi biter)
        zone = zone_of(range_loc)

        # Fallback: OB bulunamadıysa 60-bar range midpoint (denge noktası) kullan
        if mean_threshold == 0.0:
            mean_threshold = (range_high + range_low) / 2

        # --- MODEL BÜTÜNLÜĞÜ VE ZAMAN FAKTÖRÜ ---
        ob_age  = (len(df) - 1 - ob_bar_idx)  if ob_bar_idx  >= 0 else 0
        fvg_age = (len(df) - 1 - fvg_bar_idx) if fvg_bar_idx >= 0 else 0
        struct_age = 0
        try:
            if bias in ["bullish", "bullish_retrace"] and sw_highs:
                struct_age = len(df) - 1 - sw_highs[-1][2]
            elif bias in ["bearish", "bearish_retrace"] and sw_lows:
                struct_age = len(df) - 1 - sw_lows[-1][2]
        except: struct_age = 0

        _m1 = bias in ["bullish", "bearish"]
        _m2 = ("bullish" in bias and zone == "DISCOUNT (Ucuz)") or ("bearish" in bias and zone == "PREMIUM (Pahalı)")
        _m3 = active_ob_txt  != "Yok"
        _m4 = active_fvg_txt != "Yok"
        _m5 = "Güçlü" in displacement_txt and "Hacim" in displacement_txt
        model_score  = sum([_m1, _m2, _m3, _m4, _m5])
        model_checks = [("Bias Net", _m1), ("Doğru Bölge", _m2), ("OB Aktif", _m3), ("FVG Açık", _m4), ("Displacement", _m5)]

        # --- SETUP VE HEDEF KARARI ---
        setup_type = "BEKLE"
        entry_price = 0.0; stop_loss = 0.0; take_profit = 0.0; rr_ratio = 0.0
        # Varsayılan hedefi mıknatıs (DOL) olarak belirliyoruz
        final_target = magnet_target 
        setup_desc = "İdeal bir setup (Entry) bekleniyor. Mevcut yön mıknatısı takip ediliyor."

        if bias in ["bullish", "bullish_retrace"] and zone == "DISCOUNT (Ucuz)":
            valid_fvgs = [f for f in bullish_fvgs if f['top'] < curr_price]
            if valid_fvgs and next_bsl > curr_price:
                best_fvg = valid_fvgs[-1]; temp_entry = best_fvg['top']
                if next_bsl > temp_entry:
                    entry_price = temp_entry; take_profit = next_bsl
                    stop_loss = last_sl if last_sl < entry_price else best_fvg['bot'] - atr * 0.5
                    final_target = take_profit # Setup varsa hedef kâr al seviyesidir
                    setup_type = "LONG"; setup_desc = "Fiyat ucuzluk bölgesinde. FVG desteğinden likidite (BSL) hedefleniyor."
                    # 2 — rr artık gerçekten hesaplanıyor (eskiden hep 0 dönüyordu)
                    _rr_risk = entry_price - stop_loss
                    rr_ratio = round((take_profit - entry_price) / _rr_risk, 2) if _rr_risk > 0 else 0.0

        elif bias in ["bearish", "bearish_retrace"] and zone == "PREMIUM (Pahalı)":
            valid_fvgs = [f for f in bearish_fvgs if f['bot'] > curr_price]
            if valid_fvgs and next_ssl < curr_price:
                best_fvg = valid_fvgs[-1]; temp_entry = best_fvg['bot']
                if next_ssl < temp_entry:
                    entry_price = temp_entry; take_profit = next_ssl
                    stop_loss = last_sh if last_sh > entry_price else best_fvg['top'] + atr * 0.5
                    final_target = take_profit # Setup varsa hedef kâr al seviyesidir
                    setup_type = "SHORT"; setup_desc = "Fiyat pahalılık bölgesinde. Direnç bloğundan likidite (SSL) hedefleniyor."
                    # 2 — rr artık gerçekten hesaplanıyor (eskiden hep 0 dönüyordu)
                    _rr_risk = stop_loss - entry_price
                    rr_ratio = round((entry_price - take_profit) / _rr_risk, 2) if _rr_risk > 0 else 0.0

        # --- 👇 YENİ: AKSİYON ÖZETİ (THE BOTTOM LINE) ANALİZÖRÜ 👇 ---
        struct_summary = "Yapı zayıf (Order Flow Negatif)" if "bearish" in bias else "Yapı güçlü (Order Flow Pozitif)"
        zone_summary = "fiyat pahalı bölgesinden" if zone == "PREMIUM (Pahalı)" else "fiyat ucuzluk bölgesinden"
        
        # --- GÜVENLİ SEVİYE MANTIĞI (DÜZELTİLDİ: Trader Mantığı) ---
        safety_lvl = 0.0
        
        if "bearish" in bias:
            # Ayı piyasasında "Güvenli Alım" için Önümüzdeki İLK CİDDİ ENGELE (FVG veya Swing High) bakarız.
            candidates = []
            
            # 1. Aday: En yakın üst direnç FVG'sinin TEPESİ
            valid_fvgs = [f for f in bearish_fvgs if f['bot'] > curr_price]
            if valid_fvgs:
                # En yakındaki FVG'yi bul
                closest_fvg = min(valid_fvgs, key=lambda x: x['bot'] - curr_price)
                candidates.append(closest_fvg['top'])
            
            # 2. Aday: Son Swing High (MSS Seviyesi)
            if last_sh > curr_price:
                candidates.append(last_sh)
            
            # Hiçbiri yoksa mecburen Mean Threshold veya %5 yukarı
            if not candidates:
                 safety_lvl = mean_threshold if mean_threshold > curr_price else curr_price * 1.05
            else:
                 # En yakın (en düşük) direnci seçiyoruz.
                 safety_lvl = min(candidates)

        else:
            # Boğa piyasasında destek kırılımı (Stop) seviyesi
            safety_lvl = last_sl

        # ====================================================================
        # ICT UYUMLU YAKIN LİKİDİTE (DEALING RANGE) HESAPLAMASI
        # Minimum mesafe filtreleri: anlamsız gürültü hedefleri engellenir
        # Yakın hedef: en az %0.8 uzakta | Asıl hedef: yakın hedeften en az %1.5 uzakta
        # ====================================================================
        MIN_NEAR  = 0.008   # Yakın hedef minimum %0.8 uzaklık
        MIN_FAR   = 0.015   # Asıl hedef, yakın hedeften minimum %1.5 daha uzakta

        recent_df = df.iloc[-20:]

        # Fiyatın altındaki dipler (SSL) — minimum mesafe filtreli
        lows_below = recent_df[recent_df['Low'] < curr_price * (1 - MIN_NEAR)]['Low'].drop_duplicates()
        nearest_ssl = lows_below.sort_values(ascending=False)

        # Fiyatın üstündeki tepeler (BSL) — minimum mesafe filtreli
        highs_above = recent_df[recent_df['High'] > curr_price * (1 + MIN_NEAR)]['High'].drop_duplicates()
        nearest_bsl = highs_above.sort_values(ascending=True)

        # Yapısal swing high/low (tüm geçmiş) — asıl hedef için
        struct_bsl_list = sorted([h[1] for h in sw_highs if h[1] > curr_price * (1 + MIN_NEAR)], reverse=False)
        struct_ssl_list = sorted([l[1] for l in sw_lows  if l[1] < curr_price * (1 - MIN_NEAR)], reverse=True)

        if "bearish" in bias:
            # Yakın hedef: son 20 mumun en yakın SSL'i (min %0.8 aşağıda)
            # 1 — FIX: SHORT setup varsa onun kâr-al hedefi EZİLMEZ (eskiden
            # bu satır setup hedefini koşulsuz eziyordu — setup TP çöpe gidiyordu)
            _near_tgt = float(nearest_ssl.iloc[0]) if len(nearest_ssl) > 0 else curr_price * (1 - MIN_NEAR * 2)
            final_target = take_profit if (setup_type == "SHORT" and take_profit > 0) else _near_tgt
            # Asıl hedef: yapısal SSL — yakın hedeften en az %1.5 daha aşağıda
            _far_ssl = [v for v in struct_ssl_list if v < final_target * (1 - MIN_FAR)]
            derin_hedef = _far_ssl[0] if _far_ssl else final_target * (1 - MIN_FAR)
            ileri_hedef = curr_price * 1.02
            safety_lvl  = float(nearest_bsl.iloc[0]) if len(nearest_bsl) > 0 else curr_price * (1 + MIN_NEAR)
        else:
            # Yakın hedef: son 20 mumun en yakın BSL'i (min %0.8 yukarıda)
            # 1 — FIX: LONG setup varsa onun kâr-al hedefi EZİLMEZ
            _near_tgt = float(nearest_bsl.iloc[0]) if len(nearest_bsl) > 0 else curr_price * (1 + MIN_NEAR * 2)
            final_target = take_profit if (setup_type == "LONG" and take_profit > 0) else _near_tgt
            # Asıl hedef: yapısal BSL — yakın hedeften en az %1.5 daha yukarıda
            _far_bsl = [v for v in struct_bsl_list if v > final_target * (1 + MIN_FAR)]
            ileri_hedef = _far_bsl[0] if _far_bsl else final_target * (1 + MIN_FAR)
            derin_hedef = curr_price * 0.98
            safety_lvl  = float(nearest_ssl.iloc[0]) if len(nearest_ssl) > 0 else curr_price * (1 - MIN_NEAR)

        # Emniyet kilidi — sıra garantisi
        if "bearish" in bias and derin_hedef >= final_target:
            derin_hedef = final_target * (1 - MIN_FAR)
        if "bullish" in bias and ileri_hedef <= final_target:
            ileri_hedef = final_target * (1 + MIN_FAR)

        # KARAR MATRİSİ: Yön (Bias) x Konum (Zone) Çaprazlaması (HİBRİT SENARYOLAR)
        is_bullish = "bullish" in bias
        is_premium = "PREMIUM" in zone

        # --- YÜZDESEL MESAFEYE DUYARLI AKILLI DEĞİŞKENLER ---
        # Hedeflerin fiyata olan % uzaklığını hesaplıyoruz
        cp = curr_price if curr_price > 0 else 1
        dist_final = abs(cp - final_target) / cp * 100
        dist_derin = abs(cp - derin_hedef) / cp * 100
        dist_ileri = abs(cp - ileri_hedef) / cp * 100
        dist_safety = abs(cp - safety_lvl) / cp * 100

        # Mesafeye göre kelime seçimi (%1'den küçükse yakın destek/direnç, büyükse uçurum/ralli)
        hedef_1_txt = f"yakınındaki {final_target:.2f}" if dist_final < 1.0 else f"{final_target:.2f} ana hedefine"
        hedef_2_txt = f"hemen üstündeki {ileri_hedef:.2f}" if dist_ileri < 1.0 else f"güçlü {ileri_hedef:.2f} direncine"
        hedef_derin_txt = f"altındaki {derin_hedef:.2f} desteğine" if dist_derin < 1.0 else f"ana geri çekilme bölgesi olan {derin_hedef:.2f} seviyesine"
        if "bearish" in bias:
            # Ayı senaryosunda safety_lvl = son 20 günün en yakın swing high'ı (BSL)
            safety_txt = (f"hemen üstündeki swing tepe {safety_lvl:.2f}" if dist_safety < 1.0
                         else f"son 20 günün en yakın swing tepe seviyesi (iptal noktası) {safety_lvl:.2f}")
        else:
            # Boğa senaryosunda safety_lvl = son swing low (stop seviyesi)
            safety_txt = (f"hemen dibindeki swing dip {safety_lvl:.2f}" if dist_safety < 1.0
                         else f"son 20 günün en yakın swing dip seviyesi (iptal noktası) {safety_lvl:.2f}")

        # Hedefler arası anlamlılık kontrolü: %1.5'ten küçük fark = ayrı seviye değil, küme
        second_gap = abs(ileri_hedef - final_target) / max(abs(final_target), 1) * 100
        deep_gap   = abs(derin_hedef - final_target) / max(abs(final_target), 1) * 100

        # ── Sayı formatlama: 1000+ → tam sayı, altı → 2 ondalık ──────
        def _bl_fmt(v):
            return f"{int(round(v)):,}" if abs(v) >= 1000 else f"{v:.2f}"

        ft  = _bl_fmt(final_target)
        ih  = _bl_fmt(ileri_hedef)
        dh  = _bl_fmt(derin_hedef)
        sl2 = _bl_fmt(safety_lvl)

        # Aralık gösterimi: fark %0.5'ten küçükse tek sayı yeter
        bull_range = f"{ft}–{ih}" if second_gap >= 0.5 else ft
        bear_range = f"{ft}–{dh}" if deep_gap   >= 0.5 else ft

        if "DENGE" in zone:
            # 9 — DENGE bölgesi: ne ucuz ne pahalı; metin tarafsız konuşur
            if is_bullish:
                lines = [
                    f"Trend yukarı ancak fiyat 60 günlük aralığın ortasında (denge bölgesi) — ne ucuz ne pahalı. İlk izlenecek seviye {hedef_1_txt}; iptal noktası {safety_txt}.",
                    f"Yapı pozitif fakat fiyat denge bölgesinde: kurumsallar için ne cazip alım ne kâr-al noktası. {ft} üzeri kalıcılık yükselişi ivmelendirir; {safety_txt} altı yapıyı bozar.",
                ]
            else:
                lines = [
                    f"Trend aşağı ve fiyat denge bölgesinde — satıcılar baskın ama fiyat ne pahalı ne ucuz. {ft} altına sarkma düşüşü derinleştirir; dönüş için {safety_txt} üzeri kapanış gerekir.",
                    f"Yapı negatif fakat fiyat aralığın ortasında. Kısa vadede {bear_range} bölgesi izlenmeli; {safety_txt} üzerinde kalıcılık dengeyi alıcılara çevirir.",
                ]
        elif is_bullish and not is_premium:
            # 1. ÇEYREK: Boğa + Ucuzluk (İdeal Long Bölgesi)
            if second_gap >= 1.5:
                lines = [
                    f"Trend yukarı (Bullish) ve fiyat cazip (Discount) bölgesinde. Kurumsal alım iştahı ivmeleniyor. İlk olarak {hedef_1_txt} doğru hareket, ardından {hedef_2_txt} yürüyüşü izlenebilir. Sermaye koruması için {safety_txt} yakından takip edilmeli.",
                    f"İdeal 'Smart Money' koşulları devrede: Yön yukarı, fiyat iskontolu. Toplanan emirlerle {hedef_1_txt} doğru likidite avı hedefleniyor. Olası tuzaklara karşı {safety_txt} seviyesinin altı yapısal iptal alanıdır.",
                ]
            else:
                lines = [
                    f"Trend yukarı (Bullish) ve fiyat cazip (Discount) bölgesinde. Yakın hedef {bull_range} bölgesinde sıkışmış (dar konsolidasyon). Bu bölgeyi yukarı kırarsa yükseliş ivmelenebilir. İptal seviyesi: {safety_txt}.",
                    f"İdeal 'Smart Money' koşulları devrede: Yön yukarı, fiyat iskontolu. Fiyat dar bir konsolidasyon bölgesinde; {ft} üzerinde kalıcılık yükseliş için kritik. {safety_txt} altı yapısal iptal alanıdır.",
                ]
        elif is_bullish and is_premium:
            # 2. ÇEYREK: Boğa + Pahalılık (FOMO / Kâr Realizasyonu Riski)
            if second_gap >= 1.5:
                lines = [
                    f"Trend yukarı (Bullish) ancak fiyat pahalılık (Premium) bölgesinde. {hedef_1_txt} doğru ivme sürse de, bu bölgelerde kurumsal kâr satışları (Realizasyon) gelebileceği unutulmamalı. {safety_txt} kırılırsa trend bozulur.",
                    f"Yapı pozitif olsa da fiyat 'Premium' seviyelerde yorulma emareleri gösterebilir. Sıradaki dirençler {ft} ve {ih} seviyeleri. Buralardan yeni maliyetlenmek risklidir; {safety_txt} altı kapanışlarda anında savunmaya geçilmeli.",
                ]
            else:
                lines = [
                    f"Trend yukarı (Bullish) ancak fiyat pahalılık (Premium) bölgesinde. Yakın dirençler {bull_range} arasında kümelenmiş; bu bölgede kurumsal realizasyon riski yüksek. Yeni alım için erken, {safety_txt} takip edilmeli.",
                    f"Yapı pozitif olsa da fiyat 'Premium' seviyelerde. Dar direnç kümesi ({bull_range}) aşılmadan güçlü bir hareket beklenmemeli. {safety_txt} altı kapanışlarda anında savunmaya geçilmeli.",
                ]
        elif not is_bullish and is_premium:
            # 3. ÇEYREK: Ayı + Pahalılık (İdeal Short / Dağıtım Bölgesi)
            if deep_gap >= 1.5:
                lines = [
                    f"Trend aşağı (Bearish) ve fiyat tam dağıtım (Premium) bölgesinde. Satış baskısı sürüyor; ilk durak olan {ft} kırıldıktan sonra gözler {hedef_derin_txt} çevrilebilir. Dönüş için {safety_txt} üzerinde kalıcılık şart.",
                    f"Piyasa yapısı zayıf ve kurumsal oyuncular mal çıkıyor (Distribution). Pahalılık bölgesinden başlayan düşüş trendinde {hedef_derin_txt} doğru çekilme ihtimali masada. İptal seviyesi: {sl2}.",
                ]
            else:
                lines = [
                    f"Trend aşağı (Bearish) ve fiyat dağıtım (Premium) bölgesinde. Alt hedef bölgesi {bear_range} arasında sıkışmış; anlamlı düşüş için bu bölgenin altına kalıcı geçiş gerekiyor. Dönüş onayı: {safety_txt} üzerinde kapanış.",
                    f"Piyasa yapısı zayıf, dağıtım devam ediyor. Yakın hedefler dar bir bantta kümelenmiş ({bear_range}). Bu bölge kırılmadıkça gerçek bir düşüş hamlesi başlamaz; {safety_txt} direnç olarak izlenmeli.",
                ]
        else:
            # 4. ÇEYREK: Ayı + Ucuzluk (Aşırı Satım / Sweep Beklentisi)
            if deep_gap >= 1.5:
                lines = [
                    f"Trend aşağı (Bearish) ancak fiyat iskontolu (Discount) bölgeye inmiş durumda. İlk durak {ft} olsa da buralardan 'Short' açmak risklidir, kurumsallar stop patlatıp dönebilir. Dönüş onayı için {safety_txt} izlenmeli.",
                    f"Aşırı satım (Oversold) bölgesi! Yapı negatif görünse de fiyat ucuzlamış. {hedef_derin_txt} doğru son bir silkeleme (Liquidity Hunt) yaşanıp sert tepki gelebilir. Trend dönüşü için {sl2} aşılmalı.",
                ]
            else:
                lines = [
                    f"Trend aşağı (Bearish) ancak fiyat aşırı satılmış bölgede. Hedef seviyeleri {bear_range} arasında kümelenmiş — anlamlı ek düşüş için alan kalmamış. Olası stop avı (Liquidity Hunt) sonrası tepki için {safety_txt} üzeri izlenmeli.",
                    f"Aşırı satım bölgesi! Hedefler birbirine yakın ({bear_range}); büyük fonlar bu dar bantta stop avı yapabilir. Trend dönüşü için {safety_txt} üzerinde kalıcılık gerekli.",
                ]

        # 4 — Deterministik seçim: aynı hisse aynı gün hep AYNI cümleyi görür
        # (eski random.choice cache dolunca farklı cümle gösteriyordu)
        bottom_line = random.Random(f"{ticker}|{df.index[-1]}").choice(lines)
        
        # --- 🚨 YENİ: BOTTOM LINE (SONUÇ) İÇİN DİNAMİK MÜDAHALE (OVERRIDE PROTOKOLÜ) 🚨 ---
        try:
            pa_signal, pa_context = detect_price_action_with_context(df)
            
            # 1. Ralli varken "Düşüş derinleşecek" demesini yasakla! (bias.lower() yaptık)
            if pa_signal == "PA_BULLISH" and "bearish" in bias.lower():
                bottom_line = f"🚨 KRİTİK UYARI (TREND ÇATIŞMASI): Makro yapı düşüş yönünde olsa da, an itibariyle {pa_context} seviyesinden bir alıcı tepkisi geldi! Klasik düşüş senaryosu askıya alındı. Ayılar (satıcılar) tuzağa düşmüş olabilir, yukarı yönlü bir kırılım izlenebilir."
                
            # 2. Çakılırken "Alım Fırsatı" demesini yasakla! (bias.lower() yaptık)
            elif pa_signal == "PA_BEARISH" and "bullish" in bias.lower():
                bottom_line = f"🚨 KRİTİK UYARI (BOĞA TUZAĞI): Ana trend yükseliş yönünde olsa da, fiyat {pa_context} direncinden reddedildi! Kurumsalların bu bölgede 'Gel-Gel' yapıp mal dağıtmış olabileceğine dair göstergeler görülüyor. Yeni alım için oldukça tehlikeli bir bölgedeyiz."
        except Exception as e:
            pass 
        # --------------------------------------------------------------------------------

        return {
            "status": "OK", "structure": structure, "bias": bias, "zone": zone,
            "setup_type": setup_type, "entry": entry_price, "stop": stop_loss,
            "target": final_target, "structural_target": ileri_hedef if "bullish" in bias else derin_hedef,
            "rr": rr_ratio, "desc": setup_desc, "last_sl": last_sl, "last_sh": last_sh,
            "displacement": displacement_txt, "fvg_txt": active_fvg_txt, "ob_txt": active_ob_txt,
            "mean_threshold": mean_threshold, "curr_price": curr_price,
            "bottom_line": bottom_line,
            "eqh_eql_txt": eqh_eql_txt,
            "sweep_txt": sweep_txt,
            "model_score": model_score, "model_checks": model_checks,
            "ob_age": ob_age, "fvg_age": fvg_age, "struct_age": struct_age,
            "ob_low_num": _ob_l, "ob_high_num": _ob_h,
            "fvg_low_num": _fvg_l, "fvg_high_num": _fvg_h,
        }

    except Exception: return error_ret

def compute_sfp_flags(df):
    """SFP (Swing Failure Pattern / tuzak) tespiti — TEK KAYNAK (17 Tem 2026, ekran reformu 2c).
    Son mum önceki 19 günün zirvesini fitille aşıp ALTINDA kapatmışsa boğa tuzağı (bearish SFP),
    dibini fitille delip ÜSTÜNDE kapatmışsa ayı tuzağı (bullish SFP).
    Kullanıcılar: PA-DNA paneli + scan_pipeline f_sfp_* loglaması (Eylül 2026 karnesi).
    Döner: (bull, bear) 0/1 — veri yetersizse (None, None)."""
    try:
        if df is None or len(df) < 20:
            return None, None
        _h = df['High']; _l = df['Low']
        _c1_h = float(_h.iloc[-1]); _c1_l = float(_l.iloc[-1]); _c1_c = float(df['Close'].iloc[-1])
        _rh = float(_h.iloc[-20:-1].max()); _rl = float(_l.iloc[-20:-1].min())
        _bear = 1 if (_c1_h > _rh and _c1_c < _rh) else 0
        _bull = 1 if (_c1_l < _rl and _c1_c > _rl) else 0
        return _bull, _bear
    except Exception:
        return None, None


def _pa_dna_cache_key(ticker, now=None):
    """BIST seansında 15 dk, seans dışında dönemsel PA-DNA fotoğraf anahtarı."""
    _now = now or datetime.now(_TZ_ISTANBUL)
    if _now.tzinfo is None:
        _now = _TZ_ISTANBUL.localize(_now)
    else:
        _now = _now.astimezone(_TZ_ISTANBUL)

    _day = _now.date().isoformat()
    _ticker = str(ticker or "").upper()
    _is_bist = (".IS" in _ticker or
                _ticker.startswith(("XU", "XB", "XT", "XY")) or
                f"{_ticker}.IS" in _BIST_TICKERS)
    if not _is_bist:
        return f"{_day}:daily"

    _hours = None if _bist_is_closed(_now) else _bist_session_hours(_now)
    if not _hours:
        return f"{_day}:closed"

    _open_h, _open_m = map(int, _hours[0].split(":"))
    _close_h, _close_m = map(int, _hours[1].split(":"))
    _minute = _now.hour * 60 + _now.minute
    _open_minute = _open_h * 60 + _open_m
    _close_minute = _close_h * 60 + _close_m

    if _minute < _open_minute:
        return f"{_day}:pre"
    if _minute >= _close_minute:
        return f"{_day}:close"

    _bucket = (_minute - _open_minute) // 15
    return f"{_day}:session:{_bucket:02d}"


def calculate_price_action_dna(ticker):
    # 28 Tem 2026: BIST seansı içinde 15 dakikalık fotoğraf; seans öncesi,
    # kapanış sonrası ve kapalı günlerde dönem boyunca tek fotoğraf.
    # Böylece sabahki eksik günlük bar akşama taşınmaz, aynı 15 dakikada Yahoo
    # tekrar tekrar çağrılmaz. BIST dışı sembollerde eski günlük davranış korunur.
    _cache_key = _pa_dna_cache_key(ticker)
    out = _calculate_price_action_dna_cached(ticker, _cache_key)
    return _bos_sonuc_rafa_konmasin(
        _calculate_price_action_dna_cached, ticker, out, not out, _cache_key)


@st.cache_data(ttl=86400)  # 15 Haz 2026 — 600s→24sa. PA-DNA pahalı (cache miss 3.5-4sn).
                            # 28 Tem 2026 — cache_key BIST seansında 15 dakikada bir,
                            # seans dışında dönem değişince yenilenir. ALT-ÇİZGİSİZ olmalı;
                            # yoksa Streamlit anahtara katmaz (16 Tem boş-anahtar dersi).
def _calculate_price_action_dna_cached(ticker, cache_key):
    try:
        if _PROFILE_ENABLED:
            import time as _pt_pa
            _pa_t_total = _pt_pa.perf_counter()
            _tlog("    ╔ PA-DNA cache MISS (içeri girildi)", 0.0, extra=f"ticker={ticker}")
        with _Timer("    PA-DNA: get_safe_historical_data(6mo)"):
            df = get_safe_historical_data(ticker, period="6mo")
        if df is None or len(df) < 50: return None
        # İş 4 (28 Tem 2026): son barın hacmi gün-içi TAHMİN mi — damgayı HEMEN,
        # sonraki transform'lar (mask/copy/volume_delta) attrs'ı düşürmeden önce yakala.
        # smart_volume dict'ine konur → panel + AI prompt "kesin gerçek" gibi sunmaz.
        _vol_proj, _vol_prog = is_last_bar_projected(df)
        # --- YENİ HACİM HESAPLAMALARI (ADIM 2) BURAYA EKLENDİ ---
        df = df[df['Close'] > 0].copy() # Sadece hacmi olan günleri değil, fiyatı olan her günü al (Canlı mumu yakalamak için)
        # HAFTA SONU / TATİL / BAYRAM FIX: yfinance kapalı günlere Close=önceki kapanış,
        # Volume=0 sahte bar ekler. Bugün BIST kapalıysa (hafta sonu VEYA milli/dini tatil),
        # trailing 0-hacimli barları at — son geçerli işlem günü iloc[-1] olsun.
        # Hafta içi normal seansta UYGULAMA — canlı mum Volume=0 ile başlar, geçerli.
        _wknd_now = datetime.now(_TZ_ISTANBUL).weekday() >= 5  # 5=Cmt, 6=Paz
        _bist_closed_now = False
        try:
            _is_bist_pa = ".IS" in ticker or ticker.startswith(("XU", "XB", "XT", "XY"))
            if _BIST_CAL_OK and _is_bist_pa and _bist_is_closed():
                _bist_closed_now = True
        except Exception:
            pass
        if _wknd_now or _bist_closed_now:
            # Çok günlü bayramlarda (örn. Kurban Bayramı 4 gün) birden fazla 0-bar olabilir
            while len(df) > 1 and float(df['Volume'].iloc[-1]) == 0:
                df = df.iloc[:-1].copy()
        if len(df) < 20: return None
        with _Timer("    PA-DNA: calculate_volume_delta"):
            df = calculate_volume_delta(df)
        with _Timer("    PA-DNA: calculate_full_volume_profile(POC/VAH/VAL)"):
            _vp = calculate_full_volume_profile(df, lookback=20, bins=20)
        poc_price = _vp['poc']
        vah_price = _vp['vah']
        val_price = _vp['val']
        with _Timer("    PA-DNA: detect_naked_poc (4 pencere)"):
            naked_pocs = detect_naked_poc(df, lookback=20, bins=20, n_windows=4)
        # --------------------------------------------------------
        o = df['Open']; h = df['High']; l = df['Low']; c = df['Close']; v = df['Volume']
        
        # --- VERİ HAZIRLIĞI (SON 3 GÜN) ---
        # Şimdi iloc[-1] dediğinde her zaman hacmi olan EN SON GERÇEK günü alacak
        c1_o, c1_h, c1_l, c1_c = float(o.iloc[-1]), float(h.iloc[-1]), float(l.iloc[-1]), float(c.iloc[-1]) 
        c1_v = float(v.iloc[-1])
        c2_o, c2_h, c2_l, c2_c = float(o.iloc[-2]), float(h.iloc[-2]), float(l.iloc[-2]), float(c.iloc[-2]) # Dün
        c3_o, c3_h, c3_l, c3_c = float(o.iloc[-3]), float(h.iloc[-3]), float(l.iloc[-3]), float(c.iloc[-3]) # Önceki Gün
        
        c1_v = float(v.iloc[-1])
        # RVOL için avg_v: yfinance fast_info'dan 3 aylık ortalama (TradingView uyumlu).
        # today_v: df'den gelen c1_v kullan — get_safe_historical_data zaten
        # apply_volume_projection çalıştırdı, yani c1_v = gün içi projeksiyon uygulanmış tam gün tahmini.
        # Kural: geçmiş barlara dokunma, sadece son barı normalize et.
        with _Timer("    PA-DNA: yf.Ticker.fast_info NETWORK"):
            try:
                _yf_info     = yf.Ticker(ticker).fast_info
                _avg_vol_yf  = float(getattr(_yf_info, 'three_month_average_volume', 0) or 0)
                _fi_last_vol = float(getattr(_yf_info, 'last_volume', 0) or 0)
            except Exception:
                _avg_vol_yf  = 0.0
                _fi_last_vol = 0.0

        # Bugünkü bar hacmi 0 veya çok küçükse (endeks/API gecikmesi) → fast_info.last_volume ile doldur
        # Bu sadece raw_today_v için geçerli; geçmiş barlara dokunmuyoruz.
        _last_date = df.index[-1].date()
        _now_date  = datetime.now(_TZ_ISTANBUL).date()
        _is_today  = (_last_date == _now_date)
        if c1_v < 100 and _is_today and _fi_last_vol > 100:
            # fast_info.last_volume = raw seans hacmi; apply_volume_projection ile projekte et
            _now_tr   = datetime.now(_TZ_ISTANBUL)
            _now_min  = _now_tr.hour * 60 + _now_tr.minute
            _is_bist  = ".IS" in ticker or ticker.startswith("XU")
            _open_min = 9 * 60 + 55 if _is_bist else 16 * 60 + 30
            _elapsed  = _now_min - _open_min
            if _elapsed >= 60:
                # U-şekilli progress (BIST)
                if _is_bist:
                    if _elapsed <= 120:
                        _prog = (_elapsed / 120) * 0.40
                    elif _elapsed <= 380:
                        _prog = 0.40 + ((_elapsed - 120) / 260) * 0.20
                    else:
                        _prog = 0.60 + ((_elapsed - 380) / 120) * 0.40
                else:
                    if _elapsed <= 60:
                        _prog = (_elapsed / 60) * 0.25
                    elif _elapsed <= 330:
                        _prog = 0.25 + ((_elapsed - 60) / 270) * 0.40
                    else:
                        _prog = 0.65 + ((_elapsed - 330) / 60) * 0.35
                _prog = max(0.05, min(_prog, 1.0))
                c1_v = _fi_last_vol / _prog  # projeksiyonlu tahmin
            else:
                c1_v = _fi_last_vol  # projeksiyon yok, ham hacim

        # avg_v: geçmiş 20 GERÇEK işlem günü ortalaması (Volume=0 olan tatil/bayram günleri hariç)
        # KRİPTO İSTİSNASI: Binance BTC cinsinden hacim verir
        _is_crypto = "-USD" in ticker
        _v_hist    = v.iloc[:-1]
        _v_nonzero = _v_hist[_v_hist > 0]

        # Stale veri tespiti (gelişmiş): son 30 takvim günündeki non-zero işlem günü sayısı.
        # Sadece "son tarih eski mi?" değil, "yeterli taze veri var mı?" kontrol ediyoruz.
        # Nisan 10-21 gibi aralarda Volume=0 döndüyse bu tarz gap'ler artık yakalanır.
        _avg_stale = False
        if not _is_crypto:
            try:
                import datetime as _dt_avg
                _today_d  = _dt_avg.date.today()
                _30ago    = _today_d - _dt_avg.timedelta(days=30)
                # Son 30 gündeki non-zero sayısı
                _recent_count = 0
                for _d in _v_nonzero.index:
                    _dd = _d.date() if hasattr(_d, 'date') else _d
                    if _30ago <= _dd < _today_d:
                        _recent_count += 1
                # 30 takvim günü ≈ 21 işlem günü; 16'dan az varsa veri eksik say
                _avg_stale = _recent_count < 16
            except Exception:
                pass

        if _avg_stale and not _is_crypto:
            if _PROFILE_ENABLED:
                _tlog("    ⚠ PA-DNA: _avg_stale=TRUE → 2 ek Yahoo network çağrısı yapılacak!", 0.0, extra=f"ticker={ticker}")
            # Parquet/cache bozuk: iki farklı yfinance endpoint'i dene.
            # 1) yf.download(period="2mo")  — v7 CSV endpoint
            # 2) yf.Ticker().history(period="3mo") — v8 Chart endpoint
            # İkisi de Volume=0 döndürebilir; son 30 günde daha fazla non-zero veren kazanır.
            def _nz_count_30d(vol_series):
                """Son 30 takvim günündeki non-zero hacim günü sayısı."""
                try:
                    import datetime as _dtt
                    _t = _dtt.date.today()
                    _ago = _t - _dtt.timedelta(days=30)
                    cnt = 0
                    for _d in vol_series.index:
                        _dd = _d.date() if hasattr(_d, 'date') else _d
                        if _ago <= _dd < _t and vol_series[_d] > 0:
                            cnt += 1
                    return cnt
                except Exception:
                    return 0

            def _normalize_vol_df(df_raw):
                """MultiIndex sütunları düzleştir, Volume sütununu çıkar."""
                if df_raw is None or df_raw.empty:
                    return None
                if isinstance(df_raw.columns, pd.MultiIndex):
                    _lvl0 = df_raw.columns.get_level_values(0)
                    df_raw.columns = _lvl0 if 'Volume' in _lvl0 else df_raw.columns.get_level_values(1)
                df_raw = df_raw.loc[:, ~df_raw.columns.duplicated()].copy()
                df_raw.columns = [str(c).capitalize() for c in df_raw.columns]
                if df_raw.index.tz is not None:
                    df_raw.index = df_raw.index.tz_localize(None)
                return df_raw if 'Volume' in df_raw.columns else None

            _best_nz_series = None
            _best_count = 0

            # Kaynak 1: yf.download period="2mo"
            try:
                with _Timer("    PA-DNA: _avg_stale Kaynak1 yf.download(2mo) NETWORK"):
                    _src1 = _normalize_vol_df(_yf_download_with_retry(ticker, period="2mo"))
                if _src1 is not None:
                    _s1_vol = _src1['Volume'].iloc[:-1]
                    _c1 = _nz_count_30d(_s1_vol)
                    if _c1 > _best_count:
                        _best_count = _c1
                        _best_nz_series = _s1_vol[_s1_vol > 0]
            except Exception:
                pass

            # Kaynak 2: yf.Ticker().history period="3mo"
            try:
                with _Timer("    PA-DNA: _avg_stale Kaynak2 yf.Ticker.history(3mo) NETWORK"):
                    _src2_raw = yf.Ticker(ticker).history(period="3mo", auto_adjust=AUTO_ADJUST)
                _src2 = _normalize_vol_df(_src2_raw)
                if _src2 is not None:
                    _s2_vol = _src2['Volume'].iloc[:-1]
                    _c2 = _nz_count_30d(_s2_vol)
                    if _c2 > _best_count:
                        _best_count = _c2
                        _best_nz_series = _s2_vol[_s2_vol > 0]
            except Exception:
                pass

            if _best_nz_series is not None and len(_best_nz_series) >= 3:
                # Öncelik: son 30 takvim günündeki non-zero günler
                # (Farklı hacim rejimleri — Mart düşük, Nisan yüksek — karışmasın)
                try:
                    import datetime as _dtr
                    _30ago_r = _dtr.date.today() - _dtr.timedelta(days=30)
                    _recent_mask = [
                        (d.date() if hasattr(d, 'date') else d) >= _30ago_r
                        for d in _best_nz_series.index
                    ]
                    _recent_nz = _best_nz_series[_recent_mask]
                    if len(_recent_nz) >= 5:
                        avg_v = float(_recent_nz.mean())   # Sadece son 30 gün
                    else:
                        avg_v = float(_best_nz_series.tail(20).mean())  # Fallback
                except Exception:
                    avg_v = float(_best_nz_series.tail(20).mean())
            else:
                avg_v = float(_v_nonzero.tail(20).mean()) if len(_v_nonzero) >= 3 else 1.0
            # Yeterli taze veri bulunamadıysa → UI'da "Veri Eksik" gösterilecek
            _vol_data_missing = (_best_count < 16)
        else:
            _vol_20g = float(_v_nonzero.tail(20).mean()) if len(_v_nonzero) >= 3 else 0.0
            avg_v = _vol_20g if (_vol_20g > 0 and not pd.isna(_vol_20g)) else 1.0
            _vol_data_missing = False

        # raw_today_v: projeksiyon uygulanmış son bar hacmi (c1_v = v.iloc[-1], veya yukarıda fast_info ile dolduruldu)
        # Birim uyumsuzluğu koruması: fast_info.last_volume bazen tarihsel hacimlere göre ~100x farklı birimde döner.
        # Kontrol: fast_info devreye girdiyse (_fi_last_vol kullanıldıysa) 5G tarihsel medyanla karşılaştır.
        # Oran >100x ise birim sorunu kesin — /100 uygula (lot→adet veya adet→lot düzeltmesi).
        _fi_was_used = (float(v.iloc[-1]) < 100 and _is_today and _fi_last_vol > 100)
        raw_today_v = c1_v
        if _fi_was_used and avg_v > 0:
            _v5_ref = float(_v_nonzero.tail(5).median()) if len(_v_nonzero) >= 1 else 0.0
            if _v5_ref > 0 and raw_today_v / _v5_ref > 100:
                raw_today_v = raw_today_v / 100  # lot→adet birim düzeltmesi

        sma50 = c.rolling(50).mean().iloc[-1]
        # --- [YENİ] GELİŞMİŞ HACİM ANALİZİ DEĞİŞKENLERİ ---
        # Arefe günü RVOL normalizer: beklenen hacim avg_vol * 0.3125 → oran normalize et
        _rvol_af = _bist_rvol_factor()
        rvol = raw_today_v / (avg_v * _rvol_af) if avg_v > 0 else 1.0

        # RSI Serisi
        delta = c.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs_calc = gain / loss
        rsi_series = 100 - (100 / (1 + rs_calc))
        rsi_val = rsi_series.iloc[-1]

        # Mum Geometrisi
        body = abs(c1_c - c1_o)
        total_len = c1_h - c1_l if (c1_h - c1_l) > 0 else 0.01
        u_wick = c1_h - max(c1_o, c1_c)
        l_wick = min(c1_o, c1_c) - c1_l
        is_green = c1_c > c1_o
        is_red = c1_c < c1_o
        
        # --- [YENİ] STOPPING & CLIMAX KONTROLLERİ ---
        stop_vol_msg = "Yok"
        if c1_v > (avg_v * 1.5) and body < (total_len * 0.3) and l_wick > (total_len * 0.5):
            stop_vol_msg = "VAR 🔥 (Dipten kurumsal toplama emaresi!)"

        climax_msg = "Yok"
        ema20_tmp = c.ewm(span=20).mean().iloc[-1]
        price_dist_tmp = (c1_c / ema20_tmp) - 1
        if c1_v == v.tail(50).max() and price_dist_tmp > 0.10:
            climax_msg = "VAR ⚠️ (Trend sonu tahliye/FOMO riski!)"

        # RSI Serisi (Uyumsuzluk için)
        delta = c.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs_calc = gain / loss
        rsi_series = 100 - (100 / (1 + rs_calc))
        rsi_val = rsi_series.iloc[-1]

        # Mum Geometrisi (Son gün)
        body = abs(c1_c - c1_o)
        total_len = c1_h - c1_l
        u_wick = c1_h - max(c1_o, c1_c)
        l_wick = min(c1_o, c1_c) - c1_l
        is_green = c1_c > c1_o
        is_red = c1_c < c1_o
        
        # Toleranslar
        wick_ratio = 2.0 
        doji_threshold = 0.15 
        tweezer_tol = c1_c * 0.001 

        bulls, bears, neutrals = [], [], []
        
        # --- BAĞLAM (CONTEXT) ANALİZİ ---
        trend_dir = "YÜKSELİŞ" if c1_c > sma50 else "DÜŞÜŞ"
        is_overbought = rsi_val > 70
        is_oversold = rsi_val < 30
        vol_confirmed = c1_v > avg_v * 1.2 

        # Sinyal Ekleme Fonksiyonu
        def add_signal(sig_list, name, is_bullish):
            prefix = ""
            if is_bullish:
                if trend_dir == "YÜKSELİŞ":
                    prefix = "🔥 Trend Yönünde "
                    # Boğa sinyali + yukarı trend = normal. RSI yüksek olsa da
                    # "Riskli Tepe" değil — Morning Star, Hammer vb. tepe değil, dip formasyonlarıdır.
                elif trend_dir == "DÜŞÜŞ":
                    prefix = "⚠️ Tepki/Dönüş "
                    # Boğa sinyali düşüş trendinde + aşırı satım dipinde → gerçek "Riskli Dip"
                    if is_oversold: prefix += "(Riskli Dip) "
            else:
                if trend_dir == "DÜŞÜŞ":
                    prefix = "📉 Trend Yönünde "
                    if is_oversold: prefix += "(Riskli Dip) "
                elif trend_dir == "YÜKSELİŞ":
                    prefix = "⚠️ Düzeltme/Dönüş "
                    # Ayı sinyali + yukarı trend + aşırı alım = gerçek "Riskli Tepe"
                    # (Evening Star, Hanging Man, Shooting Star, Bearish Engulfing)
                    if is_overbought: prefix += "(Riskli Tepe) "
            suffix = " (Hacimli!)" if vol_confirmed else ""
            sig_list.append(f"{prefix}{name}{suffix}")

        # ======================================================
        # 1. TEKLİ MUM FORMASYONLARI (KESİN ÇÖZÜM - FULL BLOK)
        # ======================================================
        if total_len > 0:
            # Doji çakışmasını ve hatalı "bağlam" atlamalarını önlemek için kilit değişken
            is_identified = False 

            # A) SHOOTING STAR / TERS PİNBAR (Üst Fitil Baskın)
            # Kural: Üst fitil mumun en az %60'ı kadar olmalı ve alt fitil küçük kalmalı.
            if u_wick > total_len * 0.60 and l_wick < total_len * 0.25:
                is_identified = True
                # Şekli tanıdık, şimdi bağlama göre isimlendirelim
                if trend_dir == "YÜKSELİŞ" or is_overbought:
                    add_signal(bears, "Shooting Star (Kayan Yıldız) 🌠", False)
                elif trend_dir == "DÜŞÜŞ":
                    add_signal(bulls, "Inverted Hammer (Ters Çekiç) 🏗️", True)
                else:
                    neutrals.append("Ters Pinbar (Üstten Ret) 📌")

            # B) HAMMER / ÇEKİÇ (Alt Fitil Baskın)
            # Kural: Alt fitil mumun en az %60'ı kadar olmalı ve üst fitil küçük kalmalı.
            elif l_wick > total_len * 0.60 and u_wick < total_len * 0.25:
                is_identified = True
                if trend_dir == "DÜŞÜŞ" or is_oversold:
                    add_signal(bulls, "Hammer (Çekiç) 🔨", True)
                elif trend_dir == "YÜKSELİŞ":
                    add_signal(bears, "Hanging Man (Asılı Adam) 💀", False)
                else:
                    neutrals.append("Pinbar (Alttan Destek) 📌")

            # C) MARUBOZU (Gövde Baskın - Güçlü Mum)
            elif body > total_len * 0.80:
                is_identified = True
                if is_green: 
                    add_signal(bulls, "Marubozu (Güçlü Boğa) 🚀", True)
                else: 
                    add_signal(bears, "Marubozu (Güçlü Ayı) 🔻", False)

            # D) STOPPING VOLUME (Fiyat Hareketi + Hacim Onayı)
            if not is_identified and (l_wick > body * 2.0) and (c1_v > avg_v * 1.5) and (c1_l < c2_l):
                bulls.append("🛑 STOPPING VOLUME (Kurumsal Alım)")
                is_identified = True

            # E) DOJİ (Son Çare / Çöp Kutusu)
            # Sadece yukarıdaki belirgin şekillerden biri DEĞİLSE ve gövde çok küçükse çalışır.
            if not is_identified and body < total_len * doji_threshold:
                neutrals.append("Doji (Kararsızlık) ⚖️")

        # ======================================================
        # 2. İKİLİ MUM FORMASYONLARI
        # ======================================================
        
        # Bullish Kicker (Sert Gap Up)
        if (c2_c < c2_o) and is_green and (c1_o > c2_o): 
            add_signal(bulls, "Bullish Kicker (Sert GAP) 🦵", True)

        # Engulfing (Yutan)
        if (c2_c < c2_o) and is_green and (c1_c > c2_o) and (c1_o < c2_c): add_signal(bulls, "Bullish Engulfing 🐂", True)
        if (c2_c > c2_o) and is_red and (c1_c < c2_o) and (c1_o > c2_c): add_signal(bears, "Bearish Engulfing 🐻", False)
        
        # Piercing / Dark Cloud
        c2_mid = (c2_o + c2_c) / 2
        if (c2_c < c2_o) and is_green and (c1_o < c2_c) and (c1_c > c2_mid) and (c1_c < c2_o): add_signal(bulls, "Piercing Line 🌤️", True)
        if (c2_c > c2_o) and is_red and (c1_o > c2_c) and (c1_c < c2_mid) and (c1_c > c2_o): add_signal(bears, "Dark Cloud Cover ☁️", False)
        
        # Tweezer (Cımbız)
        if abs(c1_l - c2_l) < tweezer_tol and (c1_l < c3_l): add_signal(bulls, "Tweezer Bottom 🥢", True)
        if abs(c1_h - c2_h) < tweezer_tol and (c1_h > c3_h): add_signal(bears, "Tweezer Top 🥢", False)
        
        # Harami
        if (c1_h < c2_h) and (c1_l > c2_l):
            # Eğer hacim de son 10 günün en düşüğüyse veya ortalamanın en az %35 altındaysa
            if c1_v < avg_v * 0.7:
                neutrals.append("NR4: 4 Gündür Dar Bantta (Patlama gelebilir)") # Çok daha değerli bir sinyal!
            else:
                neutrals.append("Inside Bar (Bekle) ⏸️")

        # ======================================================
        # 3. ÜÇLÜ MUM FORMASYONLARI
        # ======================================================
        
        # Morning Star (Sabah Yıldızı - Dipten Dönüş)
        # 1. Kırmızı, 2. Küçük Gövde, 3. Yeşil (ilk mumun yarısını geçen)
        c2_range = (c2_h - c2_l) if (c2_h - c2_l) > 0 else 0.01
        if (c3_c < c3_o) and (abs(c2_c - c2_o) < c2_range * 0.4) and is_green and (c1_c > (c3_o + c3_c)/2):
            add_signal(bulls, "Morning Star (Dipten Dönüş) ⭐", True)

        # [GÜNCELLENMİŞ] Evening Star (Akşam Yıldızı - Tepeden Dönüş)
        c2_range = (c2_h - c2_l) if (c2_h - c2_l) > 0 else 0.01
        if (c3_c > c3_o) and (abs(c2_c - c2_o) < c2_range * 0.4) and is_red and (c1_c < (c3_o + c3_c)/2):
             add_signal(bears, "Evening Star (Trend Dönüş Risk) 🌆", False)

        # 3 White Soldiers
        if (c1_c > c1_o) and (c2_c > c2_o) and (c3_c > c3_o) and (c1_c > c2_c > c3_c):
             if c1_c > c1_h * 0.95: add_signal(bulls, "3 White Soldiers ⚔️", True)

        # 3 Black Crows
        if (c1_c < c1_o) and (c2_c < c2_o) and (c3_c < c3_o) and (c1_c < c2_c < c3_c):
             if c1_c < c1_l * 1.05: add_signal(bears, "3 Black Crows 🦅", False)

        # ======================================================
        # HAFTALIK MUM HESAPLAMA (Günlük veriyi resample eder,
        # Yahoo'ya gitmiyor, ekstra süre yok)
        # ======================================================
        weekly_note = ""
        try:
            df_w = df.resample('W').agg({
                'Open':   'first',
                'High':   'max',
                'Low':    'min',
                'Close':  'last',
                'Volume': 'sum'
            }).dropna().tail(3)

            if len(df_w) >= 2:
                wc1_o = float(df_w['Open'].iloc[-1]);  wc1_c = float(df_w['Close'].iloc[-1])
                wc1_h = float(df_w['High'].iloc[-1]);  wc1_l = float(df_w['Low'].iloc[-1])
                wc2_o = float(df_w['Open'].iloc[-2]);  wc2_c = float(df_w['Close'].iloc[-2])
                wc2_h = float(df_w['High'].iloc[-2]);  wc2_l = float(df_w['Low'].iloc[-2])

                w_is_green = wc1_c > wc1_o
                w_is_red   = wc1_c < wc1_o
                w2_is_green = wc2_c > wc2_o
                w2_is_red   = wc2_c < wc2_o

                # Haftalık engulfing
                if w2_is_red and w_is_green and wc1_c > wc2_o and wc1_o < wc2_c:
                    weekly_note = "📅 Haftalık: Bullish Engulfing (Güçlü)"
                elif w2_is_green and w_is_red and wc1_c < wc2_o and wc1_o > wc2_c:
                    weekly_note = "📅 Haftalık: Bearish Engulfing ⚠️"
                # Haftalık hammer / shooting star
                elif w_is_green or w_is_red:
                    w_body     = abs(wc1_c - wc1_o)
                    w_total    = (wc1_h - wc1_l) if (wc1_h - wc1_l) > 0 else 0.01
                    w_l_wick   = min(wc1_o, wc1_c) - wc1_l
                    w_u_wick   = wc1_h - max(wc1_o, wc1_c)
                    if w_l_wick > w_total * 0.55 and w_u_wick < w_total * 0.25:
                        weekly_note = "📅 Haftalık: Hammer / Pinbar (Destek)"
                    elif w_u_wick > w_total * 0.55 and w_l_wick < w_total * 0.25:
                        weekly_note = "📅 Haftalık: Shooting Star (Direnç) ⚠️"
                    elif w_body > w_total * 0.75:
                        weekly_note = f"📅 Haftalık: {'Güçlü Boğa Mumu' if w_is_green else 'Güçlü Ayı Mumu ⚠️'}"
        except Exception:
            weekly_note = ""

        # ======================================================
        # S&D BAĞLAM KONTROLÜ (Formasyon + Zon Çakışması)
        # Ekstra veri çekimi yok — df zaten bellekte
        # ======================================================
        sd_context_note = ""
        try:
            sd_zone = detect_supply_demand_zones(df)
            if sd_zone:
                z_top = sd_zone['Top']
                z_bot = sd_zone['Bottom']
                z_type = sd_zone['Type']
                # Fiyat zon içinde veya ±%1 yakınında mı?
                tolerance = c1_c * 0.01
                in_zone = (z_bot - tolerance) <= c1_c <= (z_top + tolerance)
                if in_zone:
                    if "Talep" in z_type:
                        sd_context_note = "📍 Güçlü talep bölgesinde oluştu"
                    else:
                        sd_context_note = "📍 Güçlü arz bölgesinde oluştu"
        except Exception:
            sd_context_note = ""

        # ======================================================
        # FORMASYON GÜVEN SKORU (0-100)
        # Hacim onayı + Trend uyumu + S&D çakışması + RSI uyumu
        # ======================================================
        confidence_score = 0
        has_bullish = bool(bulls)
        has_bearish = bool(bears)

        if has_bullish or has_bearish:
            signal_is_bullish = has_bullish and not has_bearish

            # 1. Hacim onayı (+25)
            if c1_v > avg_v * 1.2:
                confidence_score += 25

            # 2. Trend uyumu (+25)
            if signal_is_bullish and trend_dir == "YÜKSELİŞ":
                confidence_score += 25
            elif not signal_is_bullish and trend_dir == "DÜŞÜŞ":
                confidence_score += 25

            # 3. S&D bölgesi çakışması (+25)
            if sd_context_note:
                if (signal_is_bullish and "talep" in sd_context_note.lower()) or \
                   (not signal_is_bullish and "arz" in sd_context_note.lower()):
                    confidence_score += 25

            # 4. RSI uyumu (+25)
            if signal_is_bullish and rsi_val < 45:
                confidence_score += 25
            elif not signal_is_bullish and rsi_val > 60:
                confidence_score += 25

        confidence_txt = f" (Güven: {confidence_score}/100)" if confidence_score > 0 else ""

        # ======================================================
        # ÇIKTI FORMATLAMA — Öncelik sırası + Bağlam notu
        # ======================================================
        # Güçlü formasyonlar öne alınır
        priority_strong = ["Bullish Kicker", "Stopping Volume", "3 White Soldiers",
                           "Bullish Engulfing", "Morning Star", "3 Black Crows",
                           "Bearish Engulfing", "Evening Star"]
        priority_medium = ["Hammer", "Hanging Man", "Shooting Star", "Inverted Hammer",
                           "Marubozu", "Piercing", "Dark Cloud"]
        # Zayıf formasyonlar (Doji, Inside Bar, Tweezer vb.) neutrals içinde kalıyor

        def sort_by_priority(sig_list, order):
            result = []
            rest   = list(sig_list)
            for p in order:
                for s in list(rest):
                    if p in s:
                        result.append(s)
                        rest.remove(s)
                        break
            return result + rest

        bulls    = sort_by_priority(bulls,   priority_strong + priority_medium)
        bears    = sort_by_priority(bears,   priority_strong + priority_medium)

        # En güçlü sinyal öne, geri kalanlar "destekleyici" olarak
        def format_signals(sig_list):
            if not sig_list:
                return ""
            if len(sig_list) == 1:
                return sig_list[0]
            return f"{sig_list[0]} (Destekleyici: {', '.join(sig_list[1:])})"

        signal_summary = ""
        if bulls:
            signal_summary += f"ALICI: {format_signals(bulls)}{confidence_txt} "
        if bears:
            signal_summary += f"SATICI: {format_signals(bears)}{confidence_txt} "
        if neutrals:
            signal_summary += f"NÖTR: {', '.join(neutrals)}"

        # S&D bağlam notu ekle
        if sd_context_note and (bulls or bears):
            signal_summary += f" | {sd_context_note}"

        # Haftalık not ekle
        if weekly_note:
            signal_summary += f" | {weekly_note}"

        candle_desc  = signal_summary if signal_summary else "Belirgin, güçlü bir formasyon yok."
        candle_title = "Formasyon Tespiti"

        # ======================================================
        # 4. DİĞER GÖSTERGELER (SFP, VSA, KONUM, SIKIŞMA)
        # ======================================================
        
        # SFP
        sfp_txt, sfp_desc = "Yok", "Önemli bir tuzak tespiti yok."
        # 17 Tem 2026 (reform 2c): koşullar compute_sfp_flags'a taşındı — tek kaynak.
        # Bear önceliği korundu (ikisi birden yanarsa eski davranış: Bearish gösterilir).
        _sfp_bull_f, _sfp_bear_f = compute_sfp_flags(df)
        if _sfp_bear_f: sfp_txt, sfp_desc = "⚠️ Bearish SFP (Boğa Tuzağı)", "Tepe temizlendi ama tutunamadı."
        elif _sfp_bull_f: sfp_txt, sfp_desc = "💎 Bullish SFP (Ayı Tuzağı)", "Dip temizlendi ve geri döndü."

        # VSA
        vol_txt, vol_desc = "Normal", "Hacim ortalama seyrediyor."
        if c1_v > avg_v * 1.5:
            if "🛑 STOPPING VOLUME" in signal_summary: vol_txt, vol_desc = "🛑 STOPPING VOLUME", "Düşüşte devasa hacimle frenleme."
            elif body < total_len * 0.3: vol_txt, vol_desc = "⚠️ Churning (Boşa Çaba)", "Yüksek hacme rağmen fiyat gidemiyor."
            else: vol_txt, vol_desc = "🔋 Trend Destekli", "Fiyat hareketi hacimle destekleniyor."

        # Konum (BOS)
        loc_txt, loc_desc = "Denge Bölgesi", "Fiyat konsolidasyon içinde."
        if c1_c > h.iloc[-20:-1].max(): loc_txt, loc_desc = "📈 Zirve Kırılımı (BOS)", "Son 20 günün zirvesi aşıldı."
        elif c1_c < l.iloc[-20:-1].min(): loc_txt, loc_desc = "📉 Dip Kırılımı (BOS)", "Son 20 günün dibi kırıldı."

        # Volatilite (Coil)
        atr = (h-l).rolling(14).mean().iloc[-1]
        range_5 = h.tail(5).max() - l.tail(5).min()
        sq_txt, sq_desc = "Normal", "Oynaklık normal seviyede."
        if range_5 < (1.5 * atr): sq_txt, sq_desc = "⏳ SÜPER SIKIŞMA (Coil)", "Fiyat yay gibi gerildi. Patlama yakın."

        # ======================================================
        # 5.5. OBV UYUMSUZLUĞU (SMART MONEY FİLTRELİ - YENİ)
        # ======================================================
        # A. OBV ve SMA Hesapla
        change_obv = c.diff()
        dir_obv = np.sign(change_obv).fillna(0)
        obv = (dir_obv * v).cumsum()
        
        # Profesyonel Filtre: OBV'nin 20 günlük ortalaması
        obv_sma = obv.rolling(20).mean()
        
        # B. Dual-Window Kıyaslamalar (5g kısa ivme + 14g orta vade)
        p_now = c.iloc[-1]; p_old = c.iloc[-6]
        obv_now    = obv.iloc[-1]
        obv_5      = obv.iloc[-6]   # 5 gün önceki OBV
        obv_14     = obv.iloc[-15]  # 14 gün önceki OBV
        obv_sma_now = obv_sma.iloc[-1]

        obv_5g_up  = obv_now > obv_5
        obv_14g_up = obv_now > obv_14
        p_tr       = "YUKARI" if p_now > p_old else "AŞAĞI"
        is_obv_strong = obv_now > obv_sma_now

        # CMF (20g) teyit katmanı — OBV ile çapraz kontrol
        # OBV yönü ve CMF çelişirse sinyal downgrade yapılır.
        _cmf_dna     = compute_cmf(df, period=20, vol_series=v)
        _cmf_neg_dna = _cmf_dna < -0.05   # Bar içi satış baskısı — OBV'yi sorgula

        obv_data = {"title": "⚖️ ZAYIF İVME (Hacimsiz Bölge)", "desc": "Hacim akışı ortalamanın altında.", "color": "#64748B"}

        # Kafa çevirme: iki pencere zıt yön
        if obv_5g_up and not obv_14g_up:
            obv_data = {"title": "🔄 OBV KAFA ÇEVİRİYOR (Toparlanma)", "desc": "5g OBV yukarı ama 14g hâlâ baskılı — erken toparlanma sinyali.", "color": "#38bdf8"}
        elif not obv_5g_up and obv_14g_up:
            obv_data = {"title": "🔄 OBV KAFA ÇEVİRİYOR (Zayıflama)", "desc": "5g OBV aşağı ama 14g hâlâ pozitif — kısa vadeli zayıflama.", "color": "#f59e0b"}
        # Senaryo 1: GİZLİ GİRİŞ (Fiyat Düşerken Mal Toplama)
        elif p_tr == "AŞAĞI" and obv_5g_up and obv_14g_up:
            if is_obv_strong:
                if _cmf_neg_dna:
                    obv_data = {"title": "⚠️ ŞÜPHELİ GİRİŞ (Para Akışı Çelişkili)",
                                "desc": f"OBV birikim görüyor ama bar içi alıcı zayıf (Para Akışı: {_cmf_dna:+.3f}) — gün içi gerçek talep yok.",
                                "color": "#f59e0b"}
                else:
                    obv_data = {"title": "🔥 GÜÇLÜ GİZLİ GİRİŞ", "desc": "Fiyat düşerken her iki OBV penceresi yukarı & ort. üstünde (Smart Money).", "color": "#16a34a"}
            else:
                obv_data = {"title": "👀 Olası Toplama (Zayıf)", "desc": "OBV artıyor ama henüz ortalamayı geçemedi.", "color": "#d97706"}
        # Senaryo 2: GİZLİ ÇIKIŞ
        elif p_tr == "YUKARI" and not obv_5g_up and not obv_14g_up:
            obv_data = {"title": "⚠️ GİZLİ ÇIKIŞ (Dağıtım)", "desc": "Fiyat çıkarken her iki OBV penceresi de düşüyor.", "color": "#f87171"}
        # Senaryo 3: TREND DESTEĞİ
        elif is_obv_strong and obv_5g_up and obv_14g_up:
            _p_yest = c.iloc[-2]
            if p_now < _p_yest:
                if _cmf_neg_dna:
                    obv_data = {"title": "⚠️ SAHTE GÜÇ (OBV-Para Akışı Çelişkisi)",
                                "desc": f"OBV güçlü görünüyor ama bar içi satış baskısı var (Para Akışı: {_cmf_dna:+.3f}) — kurumsal destek sorgulanabilir.",
                                "color": "#f59e0b"}
                else:
                    obv_data = {"title": "🛡️ DÜŞÜŞE DİRENÇ (Kurumsal Emilim)", "desc": "Bugün fiyat kırmızı ama OBV her iki pencerede güçlü.", "color": "#0ea5e9"}
            else:
                if _cmf_neg_dna:
                    obv_data = {"title": "⚠️ ZAYIF TEYİT (OBV güçlü, Para Akışı zayıf)",
                                "desc": f"OBV ortalamasının üzerinde ama CMF satış baskısı gösteriyor (Para Akışı: {_cmf_dna:+.3f}) — trend kırılgan olabilir.",
                                "color": "#f59e0b"}
                else:
                    obv_data = {"title": "✅ SAĞLIKLI TREND (Hacim Onaylı)", "desc": "OBV her iki pencerede de ortalamasının üzerinde.", "color": "#15803d"}

        # ======================================================
        # 6. RSI UYUMSUZLUK (DIVERGENCE) - GÜNCELLENMİŞ HASSASİYET
        # ==========================================================
        div_txt, div_desc, div_type = "Uyumlu", "RSI ve Fiyat paralel.", "neutral"
        try:
            # Son 5 gün vs Önceki 15 gün
            current_window = c.iloc[-5:]
            prev_window = c.iloc[-20:-5]

            # Negatif Uyumsuzluk (Ayı)
            p_curr_max = current_window.max(); p_prev_max = prev_window.max()
            r_curr_max = rsi_series.iloc[-5:].max(); r_prev_max = rsi_series.iloc[-20:-5].max()

            # --- FİLTRELER ---
            # 1. RSI Tavanı: 75 üstüyse "Sat" deme.
            is_rsi_saturated = rsi_val >= 75
            # 2. SMA50 Kuralı: Fiyat SMA50'nin %20'sinden fazla yukarıdaysa "Ralli Modu"dur.
            is_parabolic = c1_c > (sma50 * 1.20)
            # 3. Mum Rengi: Son mum (is_red) kırmızı değilse sat deme. (is_red yukarıda tanımlıydı)

            # Matematiksel Uyumsuzluk Kontrolü
            # DÜZELTME: ">" yerine ">=" kullanarak İkili Tepeleri de dahil ettik.
            if (p_curr_max >= p_prev_max) and (r_curr_max < r_prev_max) and (r_prev_max > 60):
                
                # KARAR MEKANİZMASI: Filtrelerin HEPSİNDEN geçerse uyarı ver
                if not is_rsi_saturated and is_red and not is_parabolic:
                    div_txt = "🐻 NEGATİF UYUMSUZLUK (Tepe Zayıflığı)"
                    div_desc = "Fiyat zirveyi zorluyor, RSI yoruluyor ve satış geldi."
                    div_type = "bearish"
                else:
                    # Uyumsuzluk var ama trend çok güçlü (Ralli Modu)
                    div_txt = "🚀 GÜÇLÜ MOMENTUM (Aşırı Alım)"
                    reason = "Fiyat koptu (%20+)" if is_parabolic else "RSI doygunlukta"
                    div_desc = f"Negatif uyumsuzluk var ANCAK trend çok güçlü ({reason}). Henüz dönüş onayı yok."
                    div_type = "neutral"

            # Pozitif Uyumsuzluk (Boğa)
            p_curr_min = current_window.min(); p_prev_min = prev_window.min()
            r_curr_min = rsi_series.iloc[-5:].min(); r_prev_min = rsi_series.iloc[-20:-5].min()

            # DÜZELTME: "<" yerine "<=" kullanarak İkili Dipleri de dahil ettik.
            if (p_curr_min <= p_prev_min) and (r_curr_min > r_prev_min) and (r_prev_min < 45):
                div_txt = "💎 POZİTİF UYUMSUZLUK (Gizli Güç)"
                div_desc = "Fiyat dipte tutunuyor ve RSI yükseliyor. Toplama sinyali!"
                div_type = "bullish"

        except: pass

        # ======================================================
        # 7. & 8. SMART MONEY VERİLERİ (VWAP & RS)
        # ======================================================
        
        # --- 7. VWAP (KURUMSAL MALİYET) ---
        vwap_now = c1_c; vwap_diff = 0
        try:
            # 'ta' kütüphanesi ile 20 günlük (Aylık) VWAP hesabı
            vwap_indicator = VolumeWeightedAveragePrice(high=h, low=l, close=c, volume=v, window=20)
            vwap_series = vwap_indicator.volume_weighted_average_price()
            vwap_now = float(vwap_series.iloc[-1])
            
            # Sapma Yüzdesi
            vwap_diff = ((c1_c - vwap_now) / vwap_now) * 100
        except:
            pass

        # --- 8. RS (PİYASA GÜCÜ / ALPHA) ---
        alpha_val = 0.0
        try:
            bench_ticker = "XU100.IS" if ".IS" in ticker else "^GSPC"
            df_bench = get_safe_historical_data(bench_ticker, period="1mo")

            if df_bench is not None and not df_bench.empty:
                # 1. Verileri kopyala ve tarih formatlarını (Timezone) temizle
                s_series = df['Close'].copy()
                b_series = df_bench['Close'].copy()
                s_series.index = s_series.index.tz_localize(None)
                b_series.index = b_series.index.tz_localize(None)

                # 2. Tarih bazlı senkronize birleştirme
                combined = pd.concat([s_series, b_series], axis=1, keys=['Stock', 'Bench']).sort_index().dropna()
                
                # 3. Eğer bugün (en son satır) her iki veri de mevcutsa:
                if len(combined) >= 2:
                    s_now = combined['Stock'].iloc[-1]; s_prev = combined['Stock'].iloc[-2]
                    b_now = combined['Bench'].iloc[-1]; b_prev = combined['Bench'].iloc[-2]
                    
                    stock_chg = ((s_now - s_prev) / s_prev) * 100
                    bench_chg = ((b_now - b_prev) / b_prev) * 100
                    alpha_val = stock_chg - bench_chg
                else:
                    # Veri eşleşmediyse (Lag varsa) direkt son değerleri zorla kıyasla
                    s_chg_forced = ((c1_c - c2_c) / c2_c) * 100
                    b_last_chg = ((df_bench['Close'].iloc[-1] - df_bench['Close'].iloc[-2]) / df_bench['Close'].iloc[-2]) * 100
                    alpha_val = s_chg_forced - b_last_chg
        except Exception as e:
            alpha_val = 0.0 # Güvenli çıkış
        # ======================================================
        # 9. GELİŞMİŞ HACİM ANALİZİ (SMART VOLUME)
        # ======================================================
        std_v_20 = float(v.rolling(20).std().iloc[-1])
        c_std = std_v_20 if std_v_20 > 0 else 1.0
        # raw_today_v: projeksiyon uygulanmış c1_v | avg_v: fast_info 3 aylık ortalama
        # Arefe günü RVOL normalizer: beklenen hacim avg_vol * 0.3125 → oran normalize et
        _rvol_af2 = _bist_rvol_factor()
        rvol = raw_today_v / (avg_v * _rvol_af2) if avg_v > 0 else 1.0
        
        # Stopping Volume: Fiyat dipteyken gelen devasa karşılayıcı hacim
        stop_vol_msg = "Yok"
        if c1_v > (avg_v * 1.5) and body < (total_len * 0.3) and l_wick > (total_len * 0.5):
            stop_vol_msg = "VAR 🔥 (Dipten kurumsal toplama emaresi!)"

        # Climax Volume: Trend sonunda gelen aşırı şişkin hacim
        climax_msg = "Yok"
        ema20_val = c.ewm(span=20).mean().iloc[-1]
        price_dist_ema20 = (c1_c / ema20_val) - 1
        if c1_v == v.tail(50).max() and price_dist_ema20 > 0.10:
            climax_msg = "VAR ⚠️ (Trend sonu tahliye/FOMO riski!)"

        # ======================================================
        # 10. HACİM DELTASI VE POC İLİŞKİSİ (YENİ FORMAT + YÜZDE)
        # ======================================================
        son_mum = df.iloc[-1]
        onceki_mum = df.iloc[-2]
        delta_val = son_mum['Volume_Delta']
        fiyat = son_mum['Close']
        toplam_hacim = son_mum['Volume']
        
        # DELTA GÜCÜ (tek mum, geriye uyumluluk için korundu)
        if toplam_hacim > 0:
            delta_gucu_yuzde = abs((delta_val / toplam_hacim) * 100)
        else:
            delta_gucu_yuzde = 0

        # 5 SEANS KÜMÜLATİF DELTA
        cum_delta_5 = float(df['Volume_Delta'].iloc[-5:].sum()) if 'Volume_Delta' in df.columns else 0.0
        total_vol_5 = float(df['Volume'].iloc[-5:].sum())
        cum_delta_pct = abs(cum_delta_5 / total_vol_5 * 100) if total_vol_5 > 0 else 0.0

        # VALUE AREA POZİSYONU
        if fiyat > vah_price:
            va_pos = "ÜSTÜNDE"
        elif fiyat < val_price:
            va_pos = "ALTINDA"
        else:
            va_pos = "İÇİNDE"

        # ANA BAŞLIK + BASIT AÇIKLAMA (senaryo matrisi)
        # 16 Tem 2026: matris smart_volume_title_desc'e taşındı — TEK KAYNAK.
        # app paneli (buradan) ve smr_core bot prompt'u aynı cümleleri kullanır.
        main_title, simple_text = smart_volume_title_desc(va_pos, cum_delta_5, val_price, vah_price, rvol=rvol)

        # HACİM 4-PARÇA HÜKMÜ — tek başlığı yön/katılım/süreklilik/fiyat teyidine böler
        try:
            _delta_ser = df['Volume_Delta'].iloc[-5:].tolist() if 'Volume_Delta' in df.columns else []
            _close_ser = df['Close'].iloc[-6:].tolist() if 'Close' in df.columns else []
            # Fiyat-hacim gücü uyumsuzluğu (Force Index) → hükme karşı-sinyal
            _fi_karsi = None
            try:
                _fi_hd = compute_force_index_dual(df, span_short=2, span_long=13)
                if _fi_hd:
                    _fi_karsi = _fi_hd.get('divergence')  # 'bullish'/'bearish'/None
            except Exception:
                pass
            hacim_4soru = hacim_dort_soru(cum_delta_5, rvol, _delta_ser, _close_ser,
                                          vol_missing=_vol_data_missing, karsi=_fi_karsi)
        except Exception:
            hacim_4soru = None

        # NAKED POC — en yakın olanı seç
        naked_txt = ""
        if naked_pocs:
            closest = min(naked_pocs, key=lambda x: abs(x - fiyat))
            direction = "aşağıda" if closest < fiyat else "yukarıda"
            n_pct = abs(closest - fiyat) / (fiyat + 1e-9) * 100
            naked_txt = f"{closest:.2f} (fiyattan %{n_pct:.1f} {direction})"

        # OBV direction sayısal flag (GENEL ÖZET voting için — dual-window)
        if p_tr == "AŞAĞI" and obv_5g_up and obv_14g_up:
            _obv_direction = +1   # Gizli giriş (akümülasyon)
        elif p_tr == "YUKARI" and not obv_5g_up and not obv_14g_up:
            _obv_direction = -1   # Gizli çıkış (dağıtım)
        elif obv_5g_up and not obv_14g_up:
            _obv_direction = +1   # Kafa çeviriyor toparlanma — hafif pozitif
        elif not obv_5g_up and obv_14g_up:
            _obv_direction = 0    # Kafa çeviriyor zayıflama — nötr
        elif is_obv_strong:
            _obv_direction = +1
        else:
            _obv_direction = 0

        _pa_result = {
            "candle": {"title": candle_title, "desc": candle_desc},
            "sfp": {"title": sfp_txt, "desc": sfp_desc},
            "vol": {"title": vol_txt, "desc": vol_desc},
            "loc": {"title": loc_txt, "desc": loc_desc},
            "sq": {"title": sq_txt, "desc": sq_desc},
            "obv": obv_data,
            "obv_direction": _obv_direction,
            "rsi_val":       float(rsi_val),
            "sma50_val":     float(sma50),
            "div": {"title": div_txt, "desc": div_desc, "type": div_type},
            "vwap": {"val": vwap_now, "diff": vwap_diff},
            "rs": {"alpha": alpha_val},
            "smart_volume": {
                "title":          main_title,
                "desc":           simple_text,
                "poc":            poc_price,
                "vah":            vah_price,
                "val":            val_price,
                "va_pos":         va_pos,
                "delta":          delta_val,
                "delta_yuzde":    delta_gucu_yuzde,
                "cum_delta_5":    cum_delta_5,
                "cum_delta_pct":  round(cum_delta_pct, 1),
                "naked_poc_txt":  naked_txt,
                "rvol":           round(rvol, 2),
                "vol_data_missing": _vol_data_missing,
                "vol_projected":  _vol_proj,   # İş 4: son bar hacmi gün-içi TAHMİN mi
                "vol_progress":   _vol_prog,   # seansın geçen oranı (0.4=%40; düşük=spekülatif)
                "dort_soru":      hacim_4soru,
                "stopping":       stop_vol_msg,
                "climax":         climax_msg
            }
        }
        if _PROFILE_ENABLED:
            _tlog("    ╚ PA-DNA TOPLAM (cache MISS)", (_pt_pa.perf_counter() - _pa_t_total) * 1000, extra=f"ticker={ticker}")
        return _pa_result
    except Exception:
        if _PROFILE_ENABLED:
            try: _tlog("    ╚ PA-DNA EXCEPTION (cache MISS)", (_pt_pa.perf_counter() - _pa_t_total) * 1000, extra=f"ticker={ticker}")
            except Exception: pass
        return None

def smart_volume_title_desc(va_pos, cum_delta_5, val_price, vah_price, rvol=None):
    """Smart Money hacim özeti — ANA BAŞLIK + BASIT AÇIKLAMA (senaryo matrisi).

    TEK KAYNAK (16 Tem 2026): app paneli (calculate_price_action_dna üzerinden)
    ve smr_core bot prompt'u (_base_data_block) aynı cümleleri buradan alır —
    metin burada değişirse panel VE Telegram analizi birlikte değişir.
    """
    # Fiyat formatı: büyükse tam sayı, küçükse ondalıklı
    def _fmt(v): return f"{v:.0f}" if v >= 100 else f"{v:.2f}" if v >= 1 else f"{v:.4f}"
    _poc_range = f"({_fmt(val_price)}–{_fmt(vah_price)})"
    # 21 Tem 2026 (Codex geri bildirimi): başlık katılıma duyarlı. Fiyat kırılsa bile
    # HACİM zayıfsa "Güçlü" deme — fiyat ayağı güçlü, hacim ayağı aynı kuvvette değil.
    _zayif_katilim = (rvol is not None and 0.05 < float(rvol) < 0.8)

    if va_pos == "ÜSTÜNDE":
        if cum_delta_5 > 0:
            if _zayif_katilim:
                main_title = f"🚀 POC ALANI {_poc_range} ÜSTÜNDE — Kırılım (hacim teyidi zayıf)"
                simple_text = "POC alanının üstüne çıkıldı ama son 5 günde katılım (işlem hacmi) zayıf. Fiyat kırılımı güçlü, hacim ayağı aynı kuvvette değil — teyit kısmi."
            else:
                main_title = f"🚀 POC ALANI {_poc_range} ÜSTÜNDE — Güçlü Kırılım"
                simple_text = "Büyük oyuncuların yoğun işlem yaptığı POC alanının üstüne çıkıldı ve son 5 günde alım hacmi bunu destekliyor. Trend güçlü görünüyor."
        else:
            main_title = f"⚠️ POC ALANI {_poc_range} ÜSTÜNDE — Ama Satış Var"
            simple_text = "Fiyat yukarıda görünüyor ama son 5 günde büyük oyuncular sessizce mal veriyor olabilir. Boğa tuzağı riski taşıyor olabilir."
    elif va_pos == "ALTINDA":
        if cum_delta_5 > 0:
            main_title = f"🟢 POC ALANI {_poc_range} ALTINDA — Gizli Alım"
            simple_text = "Fiyat ucuz bölgede ama son 5 günde alım hacmi artıyor. Akıllı para sessizce topluyor olabilir."
        else:
            main_title = f"🔴 POC ALANI {_poc_range} ALTINDA — Baskı Devam"
            simple_text = "Fiyat adil değerin altında ve son 5 günde satış baskısı sürüyor. Kırılım onaylanmış gibi görünüyor."
    else:  # İÇİNDE
        if cum_delta_5 > 0:
            main_title = f"⚖️ POC ALANINDA {_poc_range} — Alım Ağırlıklı"
            simple_text = "Fiyat en yoğun hacim bölgesinde (POC). Son 5 günde alım ağırlıklı işlem akışı görülüyor — POC üstünde tutunursa yapı güçlü kalır, altına iner ve kalırsa baskı sürebilir."
        elif cum_delta_5 < 0:
            main_title = f"⚖️ POC ALANINDA {_poc_range} — Satış Ağırlıklı"
            simple_text = "Piyasa büyük oyuncuların en çok işlem yaptığı POC alanında. Son 5 günde satış ağır basıyor, aşağı kırılım riski var."
        else:
            main_title = f"⚖️ POC ALANINDA {_poc_range} — Yön Bekleniyor"
            simple_text = "Fiyat en yoğun hacim bölgesinde (POC). Alıcı ve satıcı dengede — POC'un hangi yönde kalıcı olarak terk edileceği sonraki yapıyı belirler."
    return main_title, simple_text


def _hacim_hukum_cumlesi(yon_sign, kat_lvl, sur_lvl, teyit_durum, karsi=None):
    """4 parçadan (yön/katılım/süreklilik/fiyat teyidi) birleşik, dürüst tek cümle.
    Çelişkiyi ('para var ama hacim düşük') puan eksilten ayrıntı olmaktan çıkarıp
    tek hikâyeye çevirir. hacim_dort_soru içinden çağrılır.

    karsi: fiyat-hacim gücü uyumsuzluğu yönü ('bearish'/'bullish'/None). Para yönüyle
    ÇELİŞİYORSA (Codex geri bildirimi) hükme temkin cümlesi eklenir — hüküm sadece
    yeşil tarafı değil, zayıf halkayı da söyler."""
    if yon_sign == 0:
        return "Alıcı ve satıcı dengede — hacimde net bir yön yok, beklemek mantıklı."

    if yon_sign > 0:  # para giriyor
        if teyit_durum == "var" and kat_lvl == "yogun":
            base = "Kalabalık ve fiyatı taşıyan gerçek alım — hacim yönü teyit ediyor, güçlü."
        elif teyit_durum == "var":
            base = "Katılım güçlü olmasa da alım fiyatı taşıyor — teyitli ama ölçülü bir giriş."
        elif teyit_durum == "kismi":
            israr = "ısrarlı" if sur_lvl == "israrli" else "yeni"
            base = (f"Para girişi {israr} ve fiyatı taşıyor ama zayıf katılımla — teyit KISMİ, "
                    "güçlü katılım artışı aranmalı.")
        elif kat_lvl == "yogun" and teyit_durum == "eksik":
            base = "Yoğun para geldi ama fiyat kımıldamadı — emilme/dağıtım olabilir, teyit bekle."
        else:
            israr = "ısrarla" if sur_lvl == "israrli" else "sessizce"
            base = ("Az kişi ama " + israr + " alıyor gibi — henüz güçlü, kalabalık bir alım "
                    "değil; fiyat teyidi bekleniyor.")
    else:  # yon_sign < 0 — para çıkıyor
        if teyit_durum == "var" and kat_lvl == "yogun":
            base = "Yoğun ve fiyatı düşüren gerçek satış — baskı teyitli, sürüyor."
        elif teyit_durum == "var":
            base = "Ölçülü ama fiyatı aşağı çeken satış — baskı devam ediyor."
        elif teyit_durum == "kismi":
            base = "Satış fiyatı aşağı çekiyor ama zayıf katılımla — teyit KISMİ."
        elif teyit_durum == "iraksama":
            base = "Para çıkıyor ama fiyat direniyor — zayıf ralli / dağıtım riski, dikkat."
        else:
            base = "Satış ağırlığı var ama fiyat henüz kırılmadı — baskı oluşuyor, teyit bekle."

    # Karşı sinyal (fiyat-hacim gücü uyumsuzluğu) — para yönüyle ÇELİŞİYORSA temkin ekle
    if yon_sign > 0 and karsi == 'bearish':
        base += " Ancak fiyat-hacim gücü zirveyi teyit etmiyor (ayı uyumsuzluğu) — temkinli."
    elif yon_sign < 0 and karsi == 'bullish':
        base += " Ancak fiyat-hacim gücü dibi teyit etmiyor (boğa uyumsuzluğu) — dönüş riskine dikkat."
    return base


def hacim_dort_soru(cum_delta_5, rvol, delta_serisi, close_serisi, vol_missing=False, karsi=None):
    """HACİM 4-PARÇA HÜKMÜ — tek hacim başlığını dört ayrı soruya böler.

    Sorun: 'POC ALANINDA — Alım Baskısı Var' tek başlığı, aynı ekrandaki 'hacim
    ortalamanın altında' ile çelişki gibi görünüyordu. Oysa hacim tek soru değil,
    DÖRT ayrı soru — dördü de aynı anda doğru olabilir:
      1. Yön        — para giriyor mu, çıkıyor mu?      (cum_delta_5)
      2. Katılım    — kalabalık mı, tenha mı?           (rvol)
      3. Süreklilik — tek günlük mü, ısrarlı mı?        (son 5 günün delta işaretleri)
      4. Fiyat teyidi — gelen para fiyatı taşıdı mı?    (son ~5 günün fiyat değişimi)

    BETİMLEYİCİ — puanlı yeni bir sinyal DEĞİL; var olan sayıları dürüstçe adlandırır
    (bu yüzden backtest borcu doğurmaz). TEK KAYNAK: panel (render_smart_volume_panel)
    + AI prompt + bot (smr_core) buradan okur; metin/eşik burada değişirse üçü birden
    değişir.

    Dönüş: dict {yon, katilim, sureklilik, fiyat_teyidi, satir, hukum} — veya veri
           yoksa None (endeks/hacimsiz sembol)."""
    if vol_missing:
        return None
    try:
        cum = float(cum_delta_5 or 0)
        rv  = float(rvol or 0)
    except Exception:
        return None

    # ── 1. YÖN — para giriyor mu, çıkıyor mu? ─────────────────────────────────
    if cum > 0:
        yon_lbl, yon_kisa, yon_sign = "Pozitif (para giriyor)", "Pozitif", +1
    elif cum < 0:
        yon_lbl, yon_kisa, yon_sign = "Negatif (para çıkıyor)", "Negatif", -1
    else:
        yon_lbl, yon_kisa, yon_sign = "Nötr (dengede)", "Nötr", 0

    # ── 2. KATILIM — kalabalık mı, tenha mı? (rvol; panel Tile eşikleriyle uyumlu)
    if rv >= 1.5:
        kat_lbl, kat_kisa, kat_lvl = "Yoğun (ortalama üstü)", "Yoğun", "yogun"
    elif rv >= 0.8:
        kat_lbl, kat_kisa, kat_lvl = "Normal", "Normal", "normal"
    else:
        kat_lbl, kat_kisa, kat_lvl = "Zayıf (tenha)", "Zayıf", "zayif"

    # ── 3. SÜREKLİLİK — son 5 günün kaçı baskın yönle aynı? ───────────────────
    gun = 0
    try:
        _dser = [float(x) for x in list(delta_serisi)[-5:] if x is not None]
        if yon_sign > 0:
            gun = sum(1 for x in _dser if x > 0)
        elif yon_sign < 0:
            gun = sum(1 for x in _dser if x < 0)
    except Exception:
        pass
    if gun >= 4:
        sur_lbl, sur_kisa, sur_lvl = f"Israrlı ({gun}/5 gün aynı yönde)", "Israrlı", "israrli"
    elif gun == 3:
        sur_lbl, sur_kisa, sur_lvl = "Birkaç gündür sürüyor (3/5)", "Birkaç gün", "birkac"
    else:
        sur_lbl, sur_kisa, sur_lvl = "Yeni / tek güne dayalı", "Yeni", "yeni"

    # ── 4. FİYAT TEYİDİ — gelen para fiyatı gerçekten taşıdı mı? ───────────────
    _FLAT = 1.0  # ±%1 → yatay say (BIST 5 günlük gürültü payı)
    pct = None
    try:
        _c = [float(x) for x in list(close_serisi) if x is not None and float(x) > 0]
        if len(_c) >= 6:
            pct = (_c[-1] / _c[-6] - 1) * 100
        elif len(_c) >= 2:
            pct = (_c[-1] / _c[0] - 1) * 100
    except Exception:
        pct = None

    # 3 KADEME (21 Tem 2026): fiyat yükseldi ama KATILIM zayıfsa → tam "Var" değil
    # "Kısmi" (Codex geri bildirimi: EREGL'de fiyat teyidi fazla kesin görünüyordu).
    if pct is None or yon_sign == 0:
        teyit_lbl, teyit_kisa, teyit_durum = "—", "—", "yok"
    elif yon_sign > 0:
        if pct > _FLAT:
            if kat_lvl == "zayif":
                teyit_lbl, teyit_kisa, teyit_durum = "Kısmi (fiyat yükseldi ama katılım zayıf)", "Kısmi", "kismi"
            else:
                teyit_lbl, teyit_kisa, teyit_durum = "Var (fiyat yükseldi, katılım da destekliyor)", "Var", "var"
        else:
            teyit_lbl, teyit_kisa, teyit_durum = "Eksik (para geldi ama fiyat taşınmadı)", "Eksik", "eksik"
    else:  # yon_sign < 0
        if pct < -_FLAT:
            if kat_lvl == "zayif":
                teyit_lbl, teyit_kisa, teyit_durum = "Kısmi (fiyat düştü ama katılım zayıf)", "Kısmi", "kismi"
            else:
                teyit_lbl, teyit_kisa, teyit_durum = "Var (fiyat düştü, katılım da destekliyor)", "Var", "var"
        else:
            teyit_lbl, teyit_kisa, teyit_durum = "Iraksama (para çıkıyor ama fiyat direniyor)", "Iraksama", "iraksama"

    hukum = _hacim_hukum_cumlesi(yon_sign, kat_lvl, sur_lvl, teyit_durum, karsi)
    satir = (f"Para yönü: {yon_kisa} · Katılım: {kat_kisa} · "
             f"Süreklilik: {sur_kisa} · Fiyat teyidi: {teyit_kisa}")

    return {
        "yon":          {"label": yon_lbl, "kisa": yon_kisa, "sign": yon_sign},
        "katilim":      {"label": kat_lbl, "kisa": kat_kisa, "level": kat_lvl},
        "sureklilik":   {"label": sur_lbl, "kisa": sur_kisa, "level": sur_lvl, "gun": gun},
        "fiyat_teyidi": {"label": teyit_lbl, "kisa": teyit_kisa, "durum": teyit_durum},
        "satir":        satir,
        "hukum":        hukum,
    }


@st.cache_data(ttl=600)
def calculate_minervini_sepa(ticker, benchmark_ticker="^GSPC", provided_df=None):
    """
    GÖRSEL: Eski (Sade)
    MANTIK: Sniper (Çok Sert)
    """
    try:
        # 1. VERİ YÖNETİMİ (Batch taramadan geliyorsa provided_df kullan, yoksa indir)
        if provided_df is not None:
            df = provided_df
        else:
            df = get_safe_historical_data(ticker, period="1y")
            
        if df is None or len(df) < 260: return None
        
        # MultiIndex Temizliği
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # Endeks verisi (RS için) - Eğer cache'de yoksa indir
        bench_df = get_safe_historical_data(benchmark_ticker, period="1y")
        
        close = df['Close']; volume = df['Volume']
        curr_price = float(close.iloc[-1])
        
        # ---------------------------------------------------------
        # KRİTER 1: TREND ŞABLONU (ACIMASIZ FİLTRE)
        # ---------------------------------------------------------
        sma50 = float(close.rolling(50).mean().iloc[-1])
        sma150 = float(close.rolling(150).mean().iloc[-1])
        sma200 = float(close.rolling(200).mean().iloc[-1])
        
        # Eğim Kontrolü: SMA200, 1 ay önceki değerinden yüksek olmalı
        sma200_prev = float(close.rolling(200).mean().iloc[-22])
        sma200_up = sma200 >= (sma200_prev * 0.99)
        
        year_high = float(close.rolling(250).max().iloc[-1])
        year_low = float(close.rolling(250).min().iloc[-1])
        
        # Zirveye Yakınlık: BIST daha volatil → %15 gevşeklik; diğerleri %10
        _near_high_thr = 0.85 if (".IS" in ticker or ticker.startswith("XU")) else 0.90
        near_high = curr_price >= (year_high * _near_high_thr)
        above_low = curr_price >= (year_low * 1.30)
        
        # HEPSİ DOĞRU OLMALI
        trend_ok = (curr_price > sma150 > sma200) and \
                   (sma50 > sma150) and \
                   (curr_price > sma50) and \
                   sma200_up and \
                   near_high and \
                   above_low
                   
        if not trend_ok: return None # Trend yoksa elendi.

        # ---------------------------------------------------------
        # KRİTER 2: RS KONTROLÜ (ACIMASIZ)
        # ---------------------------------------------------------
        rs_val = 0; rs_rating = "ZAYIF"
        if bench_df is not None:
            common = close.index.intersection(bench_df.index)
            if len(common) > 50:
                s_p = close.loc[common]; b_p = bench_df['Close'].loc[common]
                ratio = s_p / b_p
                rs_val = float(((ratio / ratio.rolling(50).mean()) - 1).iloc[-1] * 10)
        
        # Endeksten Zayıfsa ELE (0 altı kabul edilmez)
        if rs_val <= 1: return None
        
        rs_rating = f"GÜÇLÜ (RS: {rs_val:.1f})"

        # ---------------------------------------------------------
        # KRİTER 3: PUANLAMA (VCP + ARZ + PIVOT)
        # ---------------------------------------------------------
        raw_score = 60 # Başlangıç puanı (Trend ve RS geçtiği için)
        
        # VCP (Sertleşmiş Formül: %65 daralma)
        std_10 = close.pct_change().rolling(10).std().iloc[-1]
        std_50 = close.pct_change().rolling(50).std().iloc[-1]
        is_vcp = std_10 < (std_50 * 0.65)
        if is_vcp: raw_score += 20
        
        # Arz Kuruması (Sertleşmiş: %75 altı)
        avg_vol = volume.rolling(20).mean().iloc[-1]
        last_5 = df.tail(5)
        down_days = last_5[last_5['Close'] < last_5['Open']]
        is_dry = True if down_days.empty else (down_days['Volume'].mean() < avg_vol * 0.75)
        if is_dry: raw_score += 10
        
        # Pivot Bölgesi (Zirveye %5 kala)
        dist_high = curr_price / year_high
        in_pivot = 0.95 <= dist_high <= 1.02
        if in_pivot: raw_score += 10

        # ---------------------------------------------------------
        # ÇIKTI (ESKİ TASARIMIN ANLAYACAĞI FORMAT)
        # ---------------------------------------------------------
        # Buradaki key isimleri (Durum, Detay vs.) senin eski kodunla aynı.
        # Böylece UI bozulmayacak.
        
        status = "🔥 GÜÇLÜ TREND"
        if is_vcp and in_pivot: status = "💎💎 SÜPER BOĞA (VCP)"
        elif in_pivot: status = "🔥 KIRILIM EŞİĞİNDE"
        
        # Renk (Skor bazlı)
        color = "#16a34a" if raw_score >= 80 else "#ea580c"

        return {
            "Sembol": ticker,
            "Fiyat": f"{curr_price:.2f}",
            "Durum": status,
            "Detay": f"{rs_rating} | VCP: {'Sıkışmada düşük oynaklık' if is_vcp else '-'} | Arz: {'Kurudu(satıcılar yoruldu)' if is_dry else '-'}",
            "Raw_Score": raw_score,
            "score": raw_score, # UI bazen bunu arıyor
            "trend_ok": True,
            "is_vcp": is_vcp,
            "is_dry": is_dry,
            "rs_val": rs_val,
            "rs_rating": rs_rating,
            "reasons": ["Trend: Mükemmel", f"VCP: {is_vcp}", f"RS: {rs_val:.1f}"],
            "color": color,
            "sma200": sma200,
            "year_high": year_high
        }
    except Exception: return None

def calculate_harmonic_patterns(ticker, df):
    """
    🔮 HARMONİK FORMASYON TESPİTİ (XABCD Fibonacci Oranları)
    Desteklenen: Gartley, Butterfly, Bat, Crab, Shark

    Üç durum döner:
      state='fresh'      → D 0-3 gün önce, fiyat PRZ'den <%8 uzakta
      state='approaching'→ XAB(C) tamamlandı, fiyat tahmini D'ye <%8 yaklaşıyor
      None               → her ikisi de yok (gösterme)
    """
    if df is None or len(df) < 60:
        return None
    try:
        h = df['High'].values
        l = df['Low'].values
        c = df['Close'].values
        n = len(c)
        curr_price = float(c[-1])
        TOL = 0.06

        def ok(ratio, target=None, tol=TOL, lo=None, hi=None):
            return _check_harmonic_ratio(ratio, target, tol, lo, hi)

        pivots = _harmonic_zigzag(h, l, window=5)

        # ── AŞAMA 1: TAMAMLANMIŞ (D oluşmuş, taze) ────────────────────────
        if len(pivots) >= 5:
            for pi in range(len(pivots) - 5, max(len(pivots) - 20, -1), -1):
                pts = pivots[pi: pi + 5]
                if len(pts) < 5:
                    continue
                Xi, Xp, Xt = pts[0]
                Ai, Ap, At = pts[1]
                Bi, Bp, Bt = pts[2]
                Ci, Cp, Ct = pts[3]
                Di, Dp, Dt = pts[4]

                if Xt == 'L' and At == 'H' and Bt == 'L' and Ct == 'H' and Dt == 'L':
                    direction = 'Bullish'
                    XA = Ap - Xp; AB = Ap - Bp; BC = Cp - Bp
                    CD = Cp - Dp; XD = abs(Dp - Xp)
                elif Xt == 'H' and At == 'L' and Bt == 'H' and Ct == 'L' and Dt == 'H':
                    direction = 'Bearish'
                    XA = Xp - Ap; AB = Bp - Ap; BC = Bp - Cp
                    CD = Dp - Cp; XD = abs(Dp - Xp)
                else:
                    continue

                if XA <= 0 or AB <= 0 or BC <= 0 or CD <= 0:
                    continue

                AB_XA = AB / XA; BC_AB = BC / AB
                CD_BC = CD / BC; XD_XA = XD / XA
                prz = Dp
                bars_ago = n - 1 - Di
                fark_pct = abs(curr_price - prz) / (prz + 1e-9) * 100

                # TAZE FİLTRE: D en fazla 10 gün önce, fiyat %8'den uzakta değil
                if bars_ago > 10 or fark_pct > 8:
                    continue

                _pidx = [Xi, Ai, Bi, Ci, Di]
                # Bullish: X=low,A=high,B=low,C=high,D=low  /  Bearish: X=high,A=low,B=high,C=low,D=high
                if direction == 'Bullish':
                    _pprices = [l[Xi], h[Ai], l[Bi], h[Ci], l[Di]]
                else:
                    _pprices = [h[Xi], l[Ai], h[Bi], l[Ci], h[Di]]

                pat = None
                if ok(AB_XA, 0.618) and ok(BC_AB, lo=0.382, hi=0.886) and ok(CD_BC, lo=1.272, hi=1.618) and ok(XD_XA, 0.786):
                    pat = 'Gartley'
                elif ok(AB_XA, 0.786) and ok(BC_AB, lo=0.382, hi=0.886) and ok(CD_BC, lo=1.618, hi=2.618) and ok(XD_XA, lo=1.27, hi=1.618):
                    pat = 'Butterfly'
                elif ok(AB_XA, lo=0.382, hi=0.500) and ok(BC_AB, lo=0.382, hi=0.886) and ok(CD_BC, lo=1.618, hi=2.618) and ok(XD_XA, 0.886):
                    pat = 'Bat'
                elif ok(AB_XA, lo=0.382, hi=0.618) and ok(BC_AB, lo=0.382, hi=0.886) and ok(CD_BC, lo=2.618, hi=3.618) and ok(XD_XA, 1.618):
                    pat = 'Crab'
                elif ok(AB_XA, lo=0.382, hi=0.618) and ok(BC_AB, lo=1.13, hi=1.618) and ok(XD_XA, lo=0.886, hi=1.13):
                    pat = 'Shark'

                if pat:
                    # D noktası major destek/direnç confluence kontrolü
                    d_sr_confluence = False
                    try:
                        sr_levels = find_smart_sr_levels(df, window=5, cluster_tolerance=0.015, min_touches=3)
                        d_sr_confluence = any(abs(prz - lvl) / (lvl + 1e-9) <= 0.015 for lvl in sr_levels)
                    except Exception:
                        pass
                    return {'pattern': pat, 'direction': direction, 'prz': prz,
                            'AB_XA': round(AB_XA, 3), 'XD_XA': round(XD_XA, 3),
                            'bars_ago': bars_ago, 'curr_price': curr_price,
                            'pivot_idx': _pidx, 'pivot_prices': _pprices, 'state': 'fresh',
                            'd_sr_confluence': d_sr_confluence}

        # ── AŞAMA 2: YAKLAŞAN (XABC tamamlandı, D henüz oluşmadı) ─────────
        # CD bacağının tahmini bitiş noktasını Fibonacci ortalamasıyla hesapla
        if len(pivots) >= 4:
            for pi in range(len(pivots) - 4, max(len(pivots) - 15, -1), -1):
                pts = pivots[pi: pi + 4]
                if len(pts) < 4:
                    continue
                Xi, Xp, Xt = pts[0]
                Ai, Ap, At = pts[1]
                Bi, Bp, Bt = pts[2]
                Ci, Cp, Ct = pts[3]

                # C çok eski olmasın (son 15 bar içinde oluşmuş olmalı)
                bars_since_c = n - 1 - Ci
                if bars_since_c > 15:
                    continue

                if Xt == 'L' and At == 'H' and Bt == 'L' and Ct == 'H':
                    direction = 'Bullish'
                    XA = Ap - Xp; AB = Ap - Bp; BC = Cp - Bp
                elif Xt == 'H' and At == 'L' and Bt == 'H' and Ct == 'L':
                    direction = 'Bearish'
                    XA = Xp - Ap; AB = Bp - Ap; BC = Bp - Cp
                else:
                    continue

                if XA <= 0 or AB <= 0 or BC <= 0:
                    continue

                AB_XA = AB / XA; BC_AB = BC / AB

                # Her pattern için D tahmini (CD'nin orta noktası × BC)
                projected = None; pat = None
                if ok(AB_XA, 0.618) and ok(BC_AB, lo=0.382, hi=0.886):
                    cd_est = BC * 1.445   # Gartley CD orta: (1.272+1.618)/2
                    projected = (Cp - cd_est) if direction == 'Bullish' else (Cp + cd_est)
                    pat = 'Gartley'
                elif ok(AB_XA, 0.786) and ok(BC_AB, lo=0.382, hi=0.886):
                    cd_est = BC * 2.118   # Butterfly CD orta
                    projected = (Cp - cd_est) if direction == 'Bullish' else (Cp + cd_est)
                    pat = 'Butterfly'
                elif ok(AB_XA, lo=0.382, hi=0.500) and ok(BC_AB, lo=0.382, hi=0.886):
                    cd_est = BC * 2.118   # Bat CD orta
                    projected = (Cp - cd_est) if direction == 'Bullish' else (Cp + cd_est)
                    pat = 'Bat'
                elif ok(AB_XA, lo=0.382, hi=0.618) and ok(BC_AB, lo=0.382, hi=0.886):
                    cd_est = BC * 3.118   # Crab CD orta
                    projected = (Cp - cd_est) if direction == 'Bullish' else (Cp + cd_est)
                    pat = 'Crab'
                elif ok(AB_XA, lo=0.382, hi=0.618) and ok(BC_AB, lo=1.13, hi=1.618):
                    cd_est = BC * 0.9     # Shark: D ≈ C ± kısa mesafe
                    projected = (Cp - cd_est) if direction == 'Bullish' else (Cp + cd_est)
                    pat = 'Shark'

                if projected and pat and projected > 0:
                    dist = abs(curr_price - projected) / (projected + 1e-9) * 100
                    # Fiyat tahmini D'ye %8'den yakın VE doğru yönde ilerliyorsa
                    heading_right = (
                        (direction == 'Bullish' and curr_price <= projected * 1.08) or
                        (direction == 'Bearish' and curr_price >= projected * 0.92)
                    )
                    if dist <= 8 and heading_right:
                        if direction == 'Bullish':
                            _app = [l[Xi], h[Ai], l[Bi], h[Ci], None]
                        else:
                            _app = [h[Xi], l[Ai], h[Bi], l[Ci], None]
                        # D tahmini major destek/direnç confluence kontrolü
                        d_sr_confluence = False
                        try:
                            sr_levels = find_smart_sr_levels(df, window=5, cluster_tolerance=0.015, min_touches=3)
                            d_sr_confluence = any(abs(projected - lvl) / (lvl + 1e-9) <= 0.015 for lvl in sr_levels)
                        except Exception:
                            pass
                        return {'pattern': pat, 'direction': direction, 'prz': projected,
                                'AB_XA': round(AB_XA, 3), 'XD_XA': 0,
                                'bars_ago': 0, 'curr_price': curr_price,
                                'pivot_idx': [Xi, Ai, Bi, Ci, None],
                                'pivot_prices': _app,
                                'state': 'approaching',
                                'bars_since_c': bars_since_c,
                                'd_sr_confluence': d_sr_confluence}

        return None
    except Exception:
        return None

def calculate_harmonic_confluence(ticker, df=None):
    """
    PRZ mandatory. ICT Discount + RSI Div opsiyonel bonus rozet olarak eklenir.
      Zorunlu: Harmonik formasyon (fresh veya approaching)
      Bonus 1: ICT Discount (Bullish) / Premium (Bearish) bölgesi → '🧭 ICT Discount' rozeti
      Bonus 2: RSI Uyumsuzluğu eşleşiyor → '💎 RSI Div' rozeti
    PRZ sağlanıyorsa dict döner (bonus olmasa bile), aksi halde None.
    """
    try:
        if df is None:
            df = get_safe_historical_data(ticker, period="1y")
        if df is None or df.empty:
            return None

        harm = calculate_harmonic_patterns(ticker, df)
        if not harm:
            return None

        direction = harm['direction']
        badges = []
        bonus_notes = []

        # --- BONUS 1: ICT Zone ---
        ict = calculate_ict_deep_analysis(ticker) or {}
        zone = ict.get('zone', '')
        ict_match = False
        if direction == 'Bullish' and 'DISCOUNT' in zone.upper():
            ict_match = True
        elif direction == 'Bearish' and any(k in zone.upper() for k in ('PREMIUM', 'SUPPLY', 'OB')):
            ict_match = True
        if ict_match:
            badges.append('🧭 ICT Discount')
            bonus_notes.append(f'ICT {zone}')

        # --- BONUS 2: RSI Divergence ---
        pa = calculate_price_action_dna(ticker) or {}
        div_type = pa.get('div', {}).get('type', 'neutral')
        rsi_match = False
        if direction == 'Bullish' and div_type == 'bullish':
            rsi_match = True
        elif direction == 'Bearish' and div_type == 'bearish':
            rsi_match = True
        if rsi_match:
            badges.append('💎 RSI Div')
            bonus_notes.append('RSI Diverjans')

        badge_str = ' '.join(badges)
        aciklama = 'PRZ teyitli' + (f' + {", ".join(bonus_notes)}' if bonus_notes else '')

        return {
            'pattern':      harm['pattern'],
            'direction':    direction,
            'prz':          harm['prz'],
            'state':        harm.get('state', 'fresh'),
            'zone':         zone,
            'div_type':     div_type,
            'AB_XA':        harm['AB_XA'],
            'XD_XA':        harm['XD_XA'],
            'bars_ago':     harm['bars_ago'],
            'ict_match':    ict_match,
            'rsi_match':    rsi_match,
            'badge_str':    badge_str,
            'Aciklama':     aciklama,
        }
    except Exception:
        return None
