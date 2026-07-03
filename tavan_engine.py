"""
TAVAN MOTORU — TEK KAYNAK (single source of truth)
====================================================
18 Haz 2026'da app.py (B38), tavan_scanner.py ve tavan_backtest.py'de ÜÇ AYRI
kopya halinde yaşayan tavan skorlama mantığı bu modülde birleştirildi.

KANONİK DAVRANIŞ = CANLI app.py B38 (3 Tem 2026 kararı):
  - REJIM_AGIRLIK: Kalibre Tur-2 (60g/1131 tavan backtest)
  - Confluence eşiği: 30 (canlı motorla aynı — backtest eskiden 50 ölçüyordu, düzeltildi)
  - Manipülasyon filtresi: is_manipulated (yakın-dönem dikey tavan dahil)

Önceki drift (bu modül çözmeden önce):
  - tavan_scanner.py: kalibrasyon-öncesi ESKİ ağırlıklar + manip filtresi YOK + conf 30
  - tavan_backtest.py: Kalibre Tur-2 ağırlıklar + manip filtresi VAR + conf 50
  - app.py B38 (canlı): Kalibre Tur-2 ağırlıklar + manip filtresi VAR + conf 30
Sonuç: backtest, canlının ölçtüğünden farklı bir motoru doğruluyordu. Artık üçü de
bu modülü kullanır → "ölçülen = yayınlanan" garantisi.

app.py'nin gömülü B38 kopyası (tek-dosya `scp app.py` deploy'unu bozmamak için)
yerinde bırakıldı; test_tavan_engine.py iki tarafın ağırlıklarının aynı kaldığını
her koşuda doğrular (sessiz sapma bir daha olamaz).

Sadece pandas + numpy'e bağlıdır — Streamlit YOK, I/O YOK. Saf hesap.
"""
import numpy as np
import pandas as pd

# ───────────────────── Sabitler ─────────────────────
TAVAN_ESIK = 9.5            # +%9.5+ günlük değişim = tavan
BUYUK_HAREKET_ESIK = 5.0    # +%5+ = büyük hareket
MIN_VOL_TL = 2_000_000      # Likidite tabanı (günlük TL hacim)
CONF_THRESHOLD = 30         # Confluence eşiği — KANONİK (canlı app.py B38)
ALARM_ESIK = 150            # Skor ≥150 → 🚨 ALARM (60g backtest sweet spot)

# Rejim → her kalıp için çarpan. KALİBRE Tur-2 (18 Haz 2026, 60g/1131 tavan).
# Tur 2 sonuç: TOP 30 3.04x, Skor ≥150 %11.24 (random×3.44).
REJIM_AGIRLIK = {
    'HIZLI_RALLI':   {'A': 1.1, 'C': 0.8, 'E': 1.0, 'D': 0.7},
    'ILIMLI_YUKARI': {'A': 1.1, 'C': 0.9, 'E': 1.0, 'D': 0.9},
    'YATAY':         {'A': 1.2, 'C': 0.9, 'E': 1.1, 'D': 0.9},
    'ZAYIF':         {'A': 1.1, 'C': 0.8, 'E': 1.0, 'D': 1.0},
    'DUSUS':         {'A': 0.9, 'C': 0.7, 'E': 0.8, 'D': 1.0},
    'BILINMEZ':      {'A': 1.0, 'C': 1.0, 'E': 1.0, 'D': 1.0},
}

KATEGORI_ACK = {
    'A': 'Momentum süren — 10g zaten +%15+, 52H zirvede',
    'C': 'Sıkışma kırılımı — bantlar dar, direnç dibinde',
    'E': '20g direnci kırma — zirvenin nefesinde, hacim hafif arttı',
    'D': 'Dipten dönüş — RSI 30, sessizlik, fiyat 52H dibinde',
}


# ───────────────────── Yardımcı hesaplamalar ─────────────────────
def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def is_manipulated(df, i):
    """BRMEN tipi manipülasyon şüphesi: fitilsiz mum çok, tavan/taban açılış çok,
    range çöküşü çok. En az 2 ölçüt kırmızıysa True → hisse motordan elenir.
    Ayrıca yakın-dönem dikey tavan (bugün tavanda ya da son 5g'de 2+ tavan) → direkt ele."""
    # Yakın-dönem dikey tavan (BRMEN tipi) — kronik filtre bunu kaçırıyordu.
    _rc = df['Close'].pct_change() * 100
    try:
        if _rc.iloc[i] >= 9.0 or int((_rc.iloc[max(0, i - 4):i + 1] >= 9.0).sum()) >= 2:
            return True
    except Exception:
        pass
    if i < 60:
        return False
    last30 = df.iloc[i - 30:i]
    rng = last30['High'] - last30['Low']
    body = (last30['Close'] - last30['Open']).abs()
    body_ratio = (body / rng.replace(0, np.nan)).fillna(1.0)
    fitilsiz_oran = (body_ratio > 0.85).mean()
    last60 = df.iloc[max(0, i - 60):i]
    pct_chg = last60['Close'].pct_change().abs() * 100
    tavan_taban_oran = (pct_chg > 9.5).mean()
    last20 = df.iloc[i - 20:i]
    rel_range = ((last20['High'] - last20['Low']) / last20['Close']) * 100
    range_collapse = (rel_range < 1.0).mean()
    kirmizi = 0
    if fitilsiz_oran > 0.45:
        kirmizi += 1
    if tavan_taban_oran > 0.12:
        kirmizi += 1
    if range_collapse > 0.30:
        kirmizi += 1
    return kirmizi >= 2


def features(df, i, skip_manip=False):
    """T (i) günü için 5g dizilim dahil teknik resim. skip_manip=True → manipülasyon
    filtresini atla (çağıran kendi ek filtresini uygular, örn. app.py _liquidity_manip)."""
    if i < 60:
        return None
    if not skip_manip and is_manipulated(df, i):
        return None
    t = df.iloc[i]
    t1 = df.iloc[i - 1]
    hist = df.iloc[:i + 1]
    close = hist['Close']
    high = hist['High']
    low = hist['Low']
    vol = hist['Volume']
    rsi14 = rsi(close).iloc[-1]
    look = min(252, len(hist))
    hh = high.tail(look).max()
    ll = low.tail(look).min()
    pos_52h = (t['Close'] - ll) / (hh - ll) * 100 if hh > ll else np.nan
    bb_w = (close.tail(20).std() / close.tail(20).mean()) * 100
    bb_60 = close.rolling(20).std() / close.rolling(20).mean() * 100
    bb_pct_rank = (bb_60.tail(60) <= bb_w).mean() * 100
    vol20 = vol.tail(20).mean()
    vr_t = t['Volume'] / vol20 if vol20 > 0 else np.nan
    ret_10g = (t['Close'] / df.iloc[i - 10]['Close'] - 1) * 100 if i >= 10 else np.nan
    ret_5g = (t['Close'] / df.iloc[i - 5]['Close'] - 1) * 100 if i >= 5 else np.nan
    near_h20 = (t['Close'] / close.tail(20).max()) * 100

    # 5g dizilim
    pct_seq = []
    vol_seq = []
    for k in range(5):
        idx = i - (4 - k)
        if idx < 1:
            pct_seq.append(np.nan)
            vol_seq.append(np.nan)
            continue
        prev = df.iloc[idx - 1]['Close']
        cur = df.iloc[idx]['Close']
        pct_seq.append((cur / prev - 1) * 100 if prev > 0 else np.nan)
        v20 = df.iloc[max(0, idx - 20):idx]['Volume'].mean()
        vol_seq.append(df.iloc[idx]['Volume'] / v20 if v20 > 0 else np.nan)
    vs = [v for v in vol_seq if not (v is None or (isinstance(v, float) and np.isnan(v)))]
    vol_5g_slope = (vs[-1] - vs[0]) if len(vs) >= 2 else 0
    pct_T = pct_seq[-1] if pct_seq else np.nan
    vol_T = vol_seq[-1] if vol_seq else np.nan

    # Mum tipi (T günü)
    rng = t['High'] - t['Low']
    body = abs(t['Close'] - t['Open'])
    body_pct = body / rng * 100 if rng > 0 else 0
    is_doji = body_pct < 10
    is_green = t['Close'] > t['Open']
    lower_wick = min(t['Close'], t['Open']) - t['Low']
    lw_pct = lower_wick / rng * 100 if rng > 0 else 0
    is_hammer = (lw_pct > 40) and (body_pct > 10)
    return dict(
        close=t['Close'], rsi=rsi14, pos_52h=pos_52h, bb_rank=bb_pct_rank,
        vr_t=vr_t, near_h20=near_h20, ret_5g=ret_5g, ret_10g=ret_10g,
        vol_tl=t['Close'] * t['Volume'], pct_T=pct_T, vol_T=vol_T,
        vol_5g_slope=vol_5g_slope, is_doji=is_doji, is_green=is_green,
        is_hammer=is_hammer, body_pct=body_pct,
    )


# ───────────────────── Kalıp skorları (0-100, ham) ─────────────────────
# Her kalıbın 60g backtest'teki gerçek tavan yapanların medyanlarına yakın hisseye
# yüksek skor verir.

def score_A(f):
    """A: Momentum süren — RSI 75, 52H 90, NearH20 100, Ret10g +25."""
    s = 0
    if f['ret_10g'] >= 20: s += 35
    elif f['ret_10g'] >= 15: s += 25
    elif f['ret_10g'] >= 10: s += 15
    elif f['ret_10g'] >= 5: s += 5
    if f['pos_52h'] >= 90: s += 25
    elif f['pos_52h'] >= 75: s += 15
    elif f['pos_52h'] >= 60: s += 6
    if f['near_h20'] >= 99: s += 20
    elif f['near_h20'] >= 95: s += 8
    if 70 <= f['rsi'] <= 85: s += 12
    elif 60 <= f['rsi'] <= 90: s += 5
    if 1.0 <= f['vr_t'] <= 2.5: s += 8
    elif 0.7 <= f['vr_t'] <= 3.5: s += 3
    return s


def score_C(f):
    """C: Sıkışma kırılımı — BBrank 12, NearH20 95, RSI 51, 52H 48."""
    s = 0
    if f['bb_rank'] <= 10: s += 40
    elif f['bb_rank'] <= 20: s += 28
    elif f['bb_rank'] <= 30: s += 15
    elif f['bb_rank'] <= 40: s += 5
    if f['near_h20'] >= 97: s += 25
    elif f['near_h20'] >= 93: s += 15
    elif f['near_h20'] >= 88: s += 5
    if 45 <= f['rsi'] <= 60: s += 15
    elif 35 <= f['rsi'] <= 68: s += 7
    if 35 <= f['pos_52h'] <= 65: s += 12
    elif 25 <= f['pos_52h'] <= 75: s += 5
    if 0.8 <= f['vr_t'] <= 1.3: s += 8
    return s


def score_E(f):
    """E: 20g direnci kırma — NearH20 100, pos_52h 73, RSI 66, Vol 1.27x."""
    s = 0
    if f['near_h20'] >= 99.5: s += 35
    elif f['near_h20'] >= 97: s += 22
    elif f['near_h20'] >= 94: s += 8
    if 65 <= f['pos_52h'] <= 85: s += 22
    elif 55 <= f['pos_52h'] <= 90: s += 10
    if 60 <= f['rsi'] <= 75: s += 15
    elif 50 <= f['rsi'] <= 80: s += 6
    if f['vr_t'] >= 1.2: s += 15
    elif f['vr_t'] >= 0.9: s += 6
    if 3 <= f['ret_10g'] <= 14: s += 10
    elif 0 <= f['ret_10g'] <= 20: s += 4
    return s


def score_D(f):
    """D: Dipten dönüş — pos_52h 9, RSI 33, vol 0.6x, Ret10g -10."""
    s = 0
    if f['pos_52h'] <= 10: s += 35
    elif f['pos_52h'] <= 18: s += 22
    elif f['pos_52h'] <= 28: s += 8
    if f['rsi'] <= 28: s += 25
    elif f['rsi'] <= 38: s += 15
    elif f['rsi'] <= 45: s += 5
    if f['vr_t'] <= 0.65: s += 20
    elif f['vr_t'] <= 0.9: s += 8
    if f['ret_10g'] <= -10: s += 15
    elif f['ret_10g'] <= -4: s += 8
    return s


# ───────────────────── 5g dizilim booster'ları ─────────────────────
def _boosters(f):
    """T-1 güç + 5g hacim ısınması + mum tipi + 5g getiri → (bA, bC, bE, bD)."""
    bA = bC = bE = bD = 0
    if pd.notna(f['pct_T']) and pd.notna(f['vol_T']):
        if f['pct_T'] > 2 and f['vol_T'] > 1.2:
            bA += 12; bE += 18; bC += 6
        elif f['pct_T'] > 1:
            bA += 6; bE += 9; bC += 3
        elif f['pct_T'] < -3 and f['vol_T'] < 0.7:
            bD += 15
    if pd.notna(f['vol_5g_slope']):
        if f['vol_5g_slope'] > 0.5:
            bA += 8; bE += 10; bC += 8
        elif f['vol_5g_slope'] > 0.2:
            bA += 4; bE += 5; bC += 4
    if f['is_doji']:
        bC += 12
    if f['is_green'] and f['body_pct'] > 60:
        bA += 8; bE += 10
    if f['is_hammer']:
        bD += 10
    if pd.notna(f['ret_5g']):
        if f['ret_5g'] > 10:
            bA += 8
        elif f['ret_5g'] < -8:
            bD += 8
    return bA, bC, bE, bD


# ───────────────────── Rejim tespiti ─────────────────────
def detect_rejim(xu_close, i, lookback=10):
    """XU100'ün son N günlük değişimine göre rejim. xu_close = Close serisi (veya DataFrame).
    Döner: (rejim, chg%)."""
    close = xu_close['Close'] if hasattr(xu_close, 'columns') else xu_close
    if close is None or i < lookback:
        return 'BILINMEZ', 0.0
    start = close.iloc[i - lookback]
    end = close.iloc[i]
    chg = (end / start - 1) * 100
    if chg >= 5: return 'HIZLI_RALLI', chg
    if chg >= 2: return 'ILIMLI_YUKARI', chg
    if chg >= -2: return 'YATAY', chg
    if chg >= -5: return 'ZAYIF', chg
    return 'DUSUS', chg


# ───────────────────── Ana skorlama ─────────────────────
def score_row(f, agirlik):
    """Bir hissenin feature dict'i + rejim ağırlığı → tam skor sözlüğü.
    KANONİK: kalıp skoru × rejim çarpanı + 5g booster + confluence bonusu (eşik=30)."""
    sA = score_A(f) * agirlik['A']
    sC = score_C(f) * agirlik['C']
    sE = score_E(f) * agirlik['E']
    sD = score_D(f) * agirlik['D']
    bA, bC, bE, bD = _boosters(f)
    sA += bA; sC += bC; sE += bE; sD += bD
    scores = {'A': sA, 'C': sC, 'E': sE, 'D': sD}
    best_kat = max(scores, key=scores.get)
    best_score = scores[best_kat]
    srt = sorted(scores.values(), reverse=True)
    conf = max(0, (srt[1] - CONF_THRESHOLD)) * 0.6 if len(srt) > 1 else 0
    if len(srt) >= 3 and srt[2] > CONF_THRESHOLD:
        conf += (srt[2] - CONF_THRESHOLD) * 0.3
    total = best_score + conf
    return {
        'A': sA, 'C': sC, 'E': sE, 'D': sD,
        'kat': best_kat, 'skor': total,
        'confluence_n': sum(1 for v in srt if v > CONF_THRESHOLD),
    }


def rejim_from_close(xu_close, lookback=10):
    """En son bar için rejim (canlı panel kullanımı). xu_close = XU100 Close serisi/DataFrame."""
    close = xu_close['Close'] if hasattr(xu_close, 'columns') else xu_close
    if close is None or len(close) < lookback + 1:
        return 'BILINMEZ', 0.0
    return detect_rejim(close, len(close) - 1, lookback=lookback)
