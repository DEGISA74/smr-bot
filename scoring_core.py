# -*- coding: utf-8 -*-
"""scoring_core.py — SAF SKORLAMA MOTORLARI (Adım 7b, 10 Tem 2026)
========================================================================
app.py bölme — 9 saf skorlama motoru BİREBİR taşındı (davranış değişikliği YOK,
sadece adres). Smart Money skor + Sentiment + Master skor + SMC elements +
split scores (yapısal/tactical) + risk profile + liquidity manip + breakout state
+ tech card. Girdi genelde ticker → veriyi data_layer'dan çeker. Orkestrasyon
(_compute_signal_features + log_scan_signal + scan_*_batch) app.py'de KALDI.
Fotoğraf: golden_record smart_money_score/master_score/... hedefleri (sıfır fark).
"""
import numpy as np
import pandas as pd
import streamlit as st

from indicators import check_lazybear_squeeze_breakout
from scanners import evaluate_erken_radar, process_single_radar2
from data_layer import get_safe_historical_data
from db_layer import log_error
from ict_core import calculate_ict_deep_analysis, calculate_minervini_sepa

# app.py ile birebir — iFVG/BB flag'leri Eylül 2026 backtest'e kadar AI'dan çekili.
SMC_IFVG_BB_AI_ENABLED = False


def _detect_breakout_state(df, boundary):
    try:
        if df is None or len(df) < 21 or boundary is None or float(boundary) <= 0:
            return (0, 0.0, 1.0)
        boundary = float(boundary)
        close = df['Close']; high = df['High']; low = df['Low']
        opn = df['Open']; vol = df['Volume']
        n = len(df)
        cur_close = float(close.iloc[-1])
        cur_open  = float(opn.iloc[-1])
        cur_low   = float(low.iloc[-1])
        prev_close = float(close.iloc[-2])
        cur_vol = float(vol.iloc[-1])
        vol_20 = float(vol.iloc[-21:-1].mean()) if n >= 21 else float(vol.iloc[:-1].mean())
        vol_ratio = (cur_vol / vol_20) if vol_20 > 0 else 1.0
        gap_pct = ((cur_open - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0
        # state 2/3 — kırılım kapanışı + hacim
        if cur_close > boundary * 1.01 and vol_ratio > 1.5:
            gap_ok = gap_pct > 1.5 and cur_low > boundary
            return (3 if gap_ok else 2, round(gap_pct, 2), round(vol_ratio, 2))
        # state 1 — testing (son 3 barda en az 1 high boundary'ye değdi, close altta)
        for k in range(1, 4):
            if k > n: break
            h = float(high.iloc[-k])
            if boundary * 0.99 <= h <= boundary * 1.005:
                return (1, round(gap_pct, 2), round(vol_ratio, 2))
        return (0, round(gap_pct, 2), round(vol_ratio, 2))
    except Exception:
        return (0, 0.0, 1.0)

@st.cache_data(ttl=600, show_spinner=False)
def _liquidity_manip(df):
    """Faz 1 (19 Haz 2026) — BIST LİKİDİTE + MANİPÜLASYON kalkanı.
    ADV = 20g ort günlük TL hacim (fiyat×hacim). İnce + dik koşu + hacim sıçraması = pompa riski.
    Dönüş: dict(adv_mn [milyon TL], tier [yüksek/orta/düşük], manip [yok/orta/yüksek])."""
    out = {'adv_mn': None, 'tier': None, 'manip': None}
    try:
        c = df['Close']; v = df['Volume']
        if len(c) < 20:
            return out
        adv = float((c * v).tail(20).mean())
        out['adv_mn'] = round(adv / 1e6, 1)
        # 19 Haz 2026 VERİYLE KALİBRE (606 BIST: medyan 89mn, %23'ü <30mn). Enflasyonla periyodik
        # gözden geçir. düşük<30mn = en ince ~%23 (riskli) · yüksek≥300mn = en likit ~%21.
        out['tier'] = 'yüksek' if adv >= 300e6 else ('orta' if adv >= 30e6 else 'düşük')
        avgv = float(v.tail(20).mean())
        rvol = float(v.iloc[-1] / avgv) if avgv > 0 else 0.0
        run5  = float(c.iloc[-1] / c.iloc[-6] - 1)  if len(c) > 6  else 0.0
        run10 = float(c.iloc[-1] / c.iloc[-11] - 1) if len(c) > 11 else 0.0
        # Manipülasyon İNCE TAHTA gerektirir — likit hissedeki sıçrama meşru momentum olabilir.
        thin  = out['tier'] in ('düşük', 'orta')
        steep = (run5 > 0.20) or (run10 > 0.40)     # dik koşu (limit-up serisi gibi)
        spike = rvol > 3                             # hacim sıçraması
        if thin and steep and spike:
            out['manip'] = 'yüksek'
        elif thin and (steep or spike):
            out['manip'] = 'orta'
        else:
            out['manip'] = 'yok'
    except Exception:
        pass
    return out

def _compute_smc_elements(highs_arr, lows_arr, opens_arr, closes_arr, n_pivot=5):
    """Pure-Python SMC: FVG, Order Blocks, BOS/CHoCH, EQH/EQL, Premium/Discount.

    Improvements v2:
      1. Fractal-based pivot detection  — 2-bar window each side
      2. Mitigation tracking            — mitigated OBs/FVGs filtered out
      3. OB body-based rectangles       — (body_hi, body_lo) = (max, min) of open/close
    """
    n = len(highs_arr)
    result = dict(fvg_bull=[], fvg_bear=[], ob_bull=[], ob_bear=[],
                  bos_lines=[], eqh=[], eql=[], swing_high=None, swing_low=None)
    if n < 10:
        return result

    # ── 1. Fractal-based Pivot detection (2-bar window each side) ────
    # Pivot high: highest bar with at least 2 lower bars on each side
    # Pivot low:  lowest  bar with at least 2 higher bars on each side
    fp = 2
    ph = []  # (idx, price) pivot highs
    pl = []  # (idx, price) pivot lows
    for i in range(fp, n - fp):
        h = highs_arr[i]
        if (h > highs_arr[i - 1] and h >= highs_arr[i - fp] and
                h > highs_arr[i + 1] and h >= highs_arr[i + fp]):
            ph.append((i, float(h)))
        lo = lows_arr[i]
        if (lo < lows_arr[i - 1] and lo <= lows_arr[i - fp] and
                lo < lows_arr[i + 1] and lo <= lows_arr[i + fp]):
            pl.append((i, float(lo)))

    # ── 2. Fair Value Gaps (3-bar gap) + STATE tracking ──────────────
    # 9 Haz 2026 Oturum 20: state'li detector. Eski sürüm mitigated FVG'leri
    # filtreliyordu. Yeni sürüm tutuyor + state etiketliyor:
    #   - 'fresh'      : fiyat henüz gap'e girmedi (orijinal rolü aktif — destek/direnç)
    #   - 'tested'     : gap'e dokundu ama ortadan kırmadı (orijinal rolü hâlâ aktif)
    #   - 'inverted'   : gap tamamen kırıldı, rolü ters döndü (Inverse FVG / iFVG)
    #                    → bull FVG artık direnç, bear FVG artık destek
    # Tuple yapısı: (i_start, p_lo, p_hi, state)
    fvg_bull_raw = []
    fvg_bear_raw = []
    for i in range(2, n):
        if lows_arr[i] > highs_arr[i - 2]:
            p_lo = float(highs_arr[i - 2])
            p_hi = float(lows_arr[i])
            # Subsequent bar'larda durum tespiti
            _state = 'fresh'
            for j in range(i + 1, n):
                # Tam kırılım: low p_lo'nun altına geçtiyse → inverted
                if lows_arr[j] < p_lo:
                    _state = 'inverted'
                    break
                # Test: low gap içine girdiyse → tested (kırılım yok)
                if lows_arr[j] < p_hi:
                    _state = 'tested'
            fvg_bull_raw.append((i - 2, p_lo, p_hi, _state))
        elif highs_arr[i] < lows_arr[i - 2]:
            p_lo = float(highs_arr[i])
            p_hi = float(lows_arr[i - 2])
            _state = 'fresh'
            for j in range(i + 1, n):
                if highs_arr[j] > p_hi:
                    _state = 'inverted'
                    break
                if highs_arr[j] > p_lo:
                    _state = 'tested'
            fvg_bear_raw.append((i - 2, p_lo, p_hi, _state))
    # En son 6 FVG (4'ten 6'ya çıkarıldı — inverted iFVG'ler de tutulsun)
    result['fvg_bull'] = fvg_bull_raw[-6:]
    result['fvg_bear'] = fvg_bear_raw[-6:]

    # ── 3. BOS / CHoCH + Order Blocks (body-based) + STATE tracking ───
    # 9 Haz 2026 Oturum 20: OB state'li detector.
    # OB tuple: (bar_idx, open, close, body_hi, body_lo, state)
    #   - 'fresh'      : fiyat hiç test etmemiş → orijinal rol aktif (rölatif güçlü)
    #   - 'mitigation' : fiyat içine girdi/dokundu ama kırmadı → rol hâlâ aktif
    #                    (Mitigation Block — "tested OB", retest devam ediyor)
    #   - 'breaker'    : fiyat OB body'sini kırdı → rol TERSİNE DÖNDÜ (Breaker Block)
    #                    bull OB artık direnç (failed support), bear OB artık destek
    def _classify_bull_ob(j, b_lo, b_hi, n):
        # OB sonrası bar'larda durum tespiti
        state = 'fresh'
        for k in range(j + 1, n):
            if lows_arr[k] < b_lo:
                return 'breaker'   # tam kırılım — rol ters döndü
            if lows_arr[k] < b_hi:  # body içine girdi (tepe-orta arası)
                state = 'mitigation'
        return state
    def _classify_bear_ob(j, b_lo, b_hi, n):
        state = 'fresh'
        for k in range(j + 1, n):
            if highs_arr[k] > b_hi:
                return 'breaker'
            if highs_arr[k] > b_lo:
                state = 'mitigation'
        return state

    if len(ph) >= 2 and len(pl) >= 2:
        trend_up  = ph[-1][1] > ph[-2][1]
        last_ph   = ph[-1]
        last_pl   = pl[-1]
        start_bar = max(last_ph[0], last_pl[0]) + 1

        # BOS: close breaks last pivot in trend direction
        for i in range(start_bar, n):
            if trend_up and closes_arr[i] > last_ph[1]:
                result['bos_lines'].append((i, last_ph[1], 'BOS'))
                for j in range(i - 1, max(0, i - 15), -1):
                    if closes_arr[j] < opens_arr[j]:          # bearish candle → bull OB
                        b_lo = float(min(opens_arr[j], closes_arr[j]))
                        b_hi = float(max(opens_arr[j], closes_arr[j]))
                        _st = _classify_bull_ob(j, b_lo, b_hi, n)
                        result['ob_bull'].append(
                            (j, float(opens_arr[j]), float(closes_arr[j]), b_hi, b_lo, _st))
                        break
                break
            elif not trend_up and closes_arr[i] < last_pl[1]:
                result['bos_lines'].append((i, last_pl[1], 'BOS'))
                for j in range(i - 1, max(0, i - 15), -1):
                    if closes_arr[j] > opens_arr[j]:          # bullish candle → bear OB
                        b_lo = float(min(opens_arr[j], closes_arr[j]))
                        b_hi = float(max(opens_arr[j], closes_arr[j]))
                        _st = _classify_bear_ob(j, b_lo, b_hi, n)
                        result['ob_bear'].append(
                            (j, float(opens_arr[j]), float(closes_arr[j]), b_hi, b_lo, _st))
                        break
                break

        # CHoCH: close breaks pivot counter-trend
        for i in range(start_bar, n):
            if trend_up and closes_arr[i] < last_pl[1]:
                result['bos_lines'].append((i, last_pl[1], 'CHoCH'))
                break
            elif not trend_up and closes_arr[i] > last_ph[1]:
                result['bos_lines'].append((i, last_ph[1], 'CHoCH'))
                break

        # Extra OBs from recent pivot extremes (fill up to 3 of each kind — breaker'lar dahil)
        for k in range(min(6, len(ph))):
            if len(result['ob_bull']) >= 3:
                break
            piv = ph[-(k + 1)]
            for j in range(piv[0] - 1, max(0, piv[0] - 10), -1):
                if closes_arr[j] < opens_arr[j]:
                    b_lo = float(min(opens_arr[j], closes_arr[j]))
                    b_hi = float(max(opens_arr[j], closes_arr[j]))
                    _st = _classify_bull_ob(j, b_lo, b_hi, n)
                    candidate = (j, float(opens_arr[j]), float(closes_arr[j]), b_hi, b_lo, _st)
                    # Aynı bar'da iki kere ekleme
                    if not any(c[0] == j for c in result['ob_bull']):
                        result['ob_bull'].append(candidate)
                    break
        for k in range(min(6, len(pl))):
            if len(result['ob_bear']) >= 3:
                break
            piv = pl[-(k + 1)]
            for j in range(piv[0] - 1, max(0, piv[0] - 10), -1):
                if closes_arr[j] > opens_arr[j]:
                    b_lo = float(min(opens_arr[j], closes_arr[j]))
                    b_hi = float(max(opens_arr[j], closes_arr[j]))
                    _st = _classify_bear_ob(j, b_lo, b_hi, n)
                    candidate = (j, float(opens_arr[j]), float(closes_arr[j]), b_hi, b_lo, _st)
                    if not any(c[0] == j for c in result['ob_bear']):
                        result['ob_bear'].append(candidate)
                    break

    # ── Equal Highs / Equal Lows ─────────────────────────────────────
    atr = float(np.mean(highs_arr - lows_arr)) * 0.35
    recent_ph = [p for p in ph if p[0] >= n - 45]
    recent_pl = [p for p in pl if p[0] >= n - 45]
    for a in range(len(recent_ph)):
        for b in range(a + 1, len(recent_ph)):
            if abs(recent_ph[a][1] - recent_ph[b][1]) <= atr:
                result['eqh'].append((recent_ph[a][0], recent_ph[b][0],
                                      (recent_ph[a][1] + recent_ph[b][1]) / 2))
    for a in range(len(recent_pl)):
        for b in range(a + 1, len(recent_pl)):
            if abs(recent_pl[a][1] - recent_pl[b][1]) <= atr:
                result['eql'].append((recent_pl[a][0], recent_pl[b][0],
                                      (recent_pl[a][1] + recent_pl[b][1]) / 2))
    result['eqh'] = result['eqh'][-3:]
    result['eql'] = result['eql'][-3:]

    # ── Premium / Discount swing levels ─────────────────────────────
    if ph:
        result['swing_high'] = max(p[1] for p in ph[-4:])
    if pl:
        result['swing_low']  = min(p[1] for p in pl[-4:])

    return result

@st.cache_data(ttl=600)
def get_tech_card_data(ticker):
    try:
        df = get_safe_historical_data(ticker, period="1y")
        if df is None: return None
        
        close = df['Close'].iloc[:, 0] if isinstance(df['Close'], pd.DataFrame) else df['Close']
        high = df['High'].iloc[:, 0] if isinstance(df['High'], pd.DataFrame) else df['High']
        low = df['Low'].iloc[:, 0] if isinstance(df['Low'], pd.DataFrame) else df['Low']
        
        sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) > 50 else 0.0
        sma100 = float(close.rolling(100).mean().iloc[-1]) if len(close) > 100 else 0.0
        sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) > 200 else 0.0
        ema144 = float(close.ewm(span=144, adjust=False).mean().iloc[-1])
        atr = float((high-low).rolling(14).mean().iloc[-1])
        
        return {
            "sma50": sma50, "sma100": sma100, "sma200": sma200, "ema144": ema144,
            "stop_level": float(close.iloc[-1] - (2 * atr)), "risk_pct": float((2 * atr) / close.iloc[-1] * 100),
            "atr": atr, "close_last": float(close.iloc[-1])
        }
    except: return None

@st.cache_data(ttl=600)
def _compute_risk_profile(ticker: str) -> dict:
    """3 risk metriği hesaplar. Hata olursa None ile döner.
    Çıktı: beta_xu100, dd_zirveden, dd_max_1y, dipten_recovery, hv_oran."""
    out = {
        'beta_xu100': None,
        'dd_zirveden': None,
        'dd_max_1y': None,
        'dipten_recovery': None,
        'hv_oran': None,
        'skew_60g': None,    # Asimetri — pozitif: yukarı sıçramaya eğilimli, negatif: aşağı çakılmaya
    }
    try:
        df = get_safe_historical_data(ticker, period="1y")
        if df is None or df.empty or len(df) < 60:
            return out
        close = df['Close'].dropna()
        if len(close) < 60: return out

        # ── DRAWDOWN ──
        last = float(close.iloc[-1])
        high_1y = float(close.max())
        low_1y = float(close.min())
        out['dd_zirveden'] = round(((last/high_1y) - 1) * 100, 1) if high_1y > 0 else None
        # Max drawdown = cumulative running max'tan en derin düşüş
        cum_max = close.cummax()
        dd_series = (close / cum_max - 1) * 100
        out['dd_max_1y'] = round(float(dd_series.min()), 1)
        # Dipten toparlanma: 1y dibinden bu yana
        out['dipten_recovery'] = round(((last/low_1y) - 1) * 100, 1) if low_1y > 0 else None

        # ── BETA (XU100'e göre, son 60g) ──
        try:
            bench = get_safe_historical_data("XU100.IS", period="1y")
            if bench is not None and not bench.empty and len(bench) >= 60:
                stock_ret = close.pct_change().dropna()
                bench_ret = bench['Close'].pct_change().dropna()
                aligned = pd.concat([stock_ret, bench_ret], axis=1, join='inner').dropna()
                if len(aligned) >= 60:
                    a = aligned.tail(60).iloc[:, 0].values
                    b = aligned.tail(60).iloc[:, 1].values
                    var_b = float(np.var(b))
                    if var_b > 0:
                        beta = float(np.cov(a, b)[0][1] / var_b)
                        out['beta_xu100'] = round(beta, 2)
        except Exception:
            pass

        # ── HV ORANI (20g/60g günlük std × sqrt(252)) ──
        try:
            ret = close.pct_change().dropna()
            if len(ret) >= 60:
                hv20 = float(ret.tail(20).std() * np.sqrt(252))
                hv60 = float(ret.tail(60).std() * np.sqrt(252))
                if hv60 > 0:
                    out['hv_oran'] = round(hv20 / hv60, 2)
                # ── ASİMETRİ (Skewness 60g günlük getiri) ──
                # > 0: pozitif kuyruk (yukarı sıçramaya eğilimli)
                # < 0: negatif kuyruk (aşağı çakılmaya eğilimli)
                # |skew| < 0.2: simetrik (büyük hareketler iki yönde dengeli)
                _skew = float(ret.tail(60).skew())
                if not np.isnan(_skew) and abs(_skew) < 10:   # outlier guard
                    out['skew_60g'] = round(_skew, 2)
        except Exception:
            pass
    except Exception as _ex:
        try: log_error("risk_profile", _ex, ctx={'ticker': ticker})
        except Exception: pass
    return out

def compute_smart_money_split_scores(feature_dict: dict) -> dict:
    """Akıllı Para sinyallerini YAPISAL vs TACTICAL ölçeklere ayırır.

    Sorun: 'Akıllı para birikiyor' demek iki farklı zaman ölçeği:
      - YAPISAL: haftalar/aylar boyunca kurumsal pozisyon kurma
        (MKK yabancı, OBV 20g+, CMF 20g, POC multi-TF) — TEFAS/KAP kaldırıldı (endpoint bloklu)
      - TACTICAL: günlük veride birkaç gün/birkaç haftalık kısa-pencere davranışı
        (cum_delta 5g, MFI 5g, RVOL spike, climax, pocket pivot)

    Bu fonksiyon her sinyali sınıflandırır ve iki ayrı 0-100 skor üretir.
    Kullanıcı/AI tek bakışta görür:
      - 'Yapısal güçlü ama tactical zayıf' → sabırlı, hareket olgun değil
      - 'Yapısal zayıf ama tactical güçlü' → kısa vade fırsat, uzun temkinli
      - 'Her ikisi güçlü' → uyum, ana hikaye
      - 'Her ikisi zayıf' → beklemede

    Args:
        feature_dict: _compute_signal_features çıktısı. Her flag/state'i okur.
    Returns:
        {
          'structural_score': 0-100,
          'tactical_score': 0-100,
          'structural_signals': [aktif yapısal sinyal listesi],
          'tactical_signals': [aktif tactical sinyal listesi],
          'verdict': string özet
        }
    """
    try:
        f = feature_dict or {}
        # ─── YAPISAL puanlama (max +/- 100) ────────────────────────────
        struct_score = 50.0  # nötr başla
        struct_pos = []; struct_neg = []

        # (12 Haz Oturum 21) TEFAS + KAP + f_kurumsal_anchor skor katkıları KALDIRILDI
        # (TEFAS makro-rejim yasağı, KAP endpoint bloklu). Yabancı katkısı korundu.
        # MKK Yabancı
        # (4 Tem 2026 karne) tek-gün f_yabanci_giris TERS çalışıyor (giris=1 → 10g ret
        # -%9.05, n=102) → +6 katkısı KALDIRILDI. Puan sadece süreklilik (streak) veya
        # giriş+streak kombinine verilir. Kolon scan_signals'a yazılmaya devam eder.
        if f.get('f_yabanci_giris') == 1 and f.get('f_yabanci_streak') == 1:
            struct_score += 10; struct_pos.append('Yabancı giriş + 3g+ süreklilik (kombin)')
        elif f.get('f_yabanci_streak') == 1:
            struct_score += 4; struct_pos.append('Yabancı 3+ gün üst üste artış')
        if f.get('f_yabanci_cikis') == 1:
            struct_score -= 8; struct_neg.append('Yabancı top-3 net satış listesinde')
        # Relative OBV hesaplanır ve ayrı alanda raporlanır; bağımsız katkısı henüz
        # ölçülmediği için yapısal canlı skora ve pozitif/negatif oy listesine girmez.
        # POC mıknatıs / confluence (multi-TF = yapısal)
        if f.get('f_poc_magnet') == 1:
            struct_score += 5; struct_pos.append('POC mıknatıs aktif')
        if f.get('f_poc_confluence') == 1:
            struct_score += 4; struct_pos.append('Multi-TF POC çakışması')
        # CMF dual orta-uzun vade
        _cmf = f.get('f_cmf_dual')
        if _cmf in ('strong_pos', 'pos'):
            struct_score += 4; struct_pos.append('CMF birikim')
        elif _cmf in ('strong_neg', 'neg'):
            struct_score -= 5; struct_neg.append('CMF dağıtım')

        struct_score = max(0, min(100, struct_score))

        # ─── TACTICAL puanlama (birkaç gün/birkaç hafta; günlük veri) ──
        tact_score = 50.0
        tact_pos = []; tact_neg = []

        # RSI/MFI Bouquet — RSI ve MFI aynı yönde ekstrem (climax).
        # 27 Tem 2026 (borsacı geri bildirimi, rec 5): YÖNSÜZ +15 ödülü KALDIRILDI.
        # Sebep: bouquet 4 farklı durumu tek bayrağa topluyordu — "ikisi de tepe
        # yorgunluğu" (RİSK) ile "ikisi de dipten dönüş" (FIRSAT) aynı +15'i alıyor,
        # bu da bearish climax'ın doğru negatif skorunu (aşağıdaki mfi/rsi_dual) iptal
        # ediyordu. Yön zaten f_mfi_dual + f_rsi_dual üzerinden ayrı puanlanıyor →
        # bouquet artık SADECE yön-farkında bilgi etiketi (skora katkı 0). 4 durumun
        # 5/10/20g katkısı ölçülene kadar ağırlık verilmez. Flag scan_signals'a yazılır.
        if f.get('f_rsi_mfi_bouquet') == 1:
            _bq_mfi = f.get('f_mfi_dual')
            if _bq_mfi in ('oversold_both', 'smart_money_recovery'):
                tact_pos.append('RSI/MFI çift ekstrem: dipte climax (bilgi — ölçüm bekliyor, skorsuz)')
            elif _bq_mfi in ('overbought_both', 'cooling_smart_exit'):
                tact_neg.append('RSI/MFI çift ekstrem: tepede yorgunluk (bilgi — ölçüm bekliyor, skorsuz)')
            else:
                tact_pos.append('RSI/MFI çift ekstrem (bilgi — ölçüm bekliyor, skorsuz)')
        # MFI dual (kısa pencere = tactical)
        _mfi = f.get('f_mfi_dual')
        if _mfi == 'oversold_both':
            tact_score += 10; tact_pos.append('MFI iki pencere aşırı satım')
        elif _mfi == 'overbought_both':
            tact_score -= 8; tact_neg.append('MFI iki pencere aşırı alım')
        elif _mfi == 'early_overbought':
            tact_score += 8; tact_pos.append('MFI erken giriş sinyali')
        elif _mfi == 'smart_money_recovery':
            tact_score += 10; tact_pos.append('MFI dipten akıllı para dönüşü')
        elif _mfi == 'cooling_smart_exit':
            tact_score -= 12; tact_neg.append('MFI tepe yorgunluğu (smart exit)')
        elif _mfi == 'early_oversold':
            tact_score -= 3; tact_neg.append('MFI ani panik')
        # RSI dual
        _rsi = f.get('f_rsi_dual')
        if _rsi == 'oversold_both':
            tact_score += 6; tact_pos.append('RSI iki pencere aşırı satım')
        elif _rsi == 'overbought_both':
            tact_score -= 5; tact_neg.append('RSI iki pencere aşırı alım')
        elif _rsi == 'dip_recovery':
            tact_score += 6; tact_pos.append('RSI dip dönüşü')
        elif _rsi == 'cooling_overheat':
            tact_score -= 6; tact_neg.append('RSI tepe yorgunluğu')
        # cum_delta dual (kısa+orta — büyük ağırlık kısa için)
        _cd = f.get('f_cum_delta_dual')
        if _cd == 'strong_pos':
            tact_score += 8; tact_pos.append('Cumulative Delta güçlü pozitif')
        elif _cd == 'strong_neg':
            tact_score -= 10; tact_neg.append('Cumulative Delta güçlü negatif')
        elif _cd == 'turning_up':
            tact_score += 6; tact_pos.append('Cum delta kafa çeviriyor (yukarı)')
        elif _cd == 'turning_down':
            tact_score -= 6; tact_neg.append('Cum delta kafa çeviriyor (aşağı)')
        # Breakout state (Kibar Type 1 = tactical kırılım)
        _bo = f.get('f_breakout_state')
        if _bo is not None:
            try:
                _bo_i = int(_bo)
                if _bo_i == 3:
                    tact_score += 15; tact_pos.append('Breakaway gap (Type 1)')
                elif _bo_i == 2:
                    tact_score += 10; tact_pos.append('Klasik breakout')
                elif _bo_i == 1:
                    tact_score += 3; tact_pos.append('Breakout testing')
            except Exception: pass
        # iFVG, BB (ICT-tactical, kısa vade) — gürültü nedeniyle skora KATILMAZ
        # (12 Haz: >%50 ateşliyor, SMC_IFVG_BB_AI_ENABLED=False ile pasif)
        if SMC_IFVG_BB_AI_ENABLED and f.get('f_near_ifvg') == 1:
            tact_score += 4; tact_pos.append('Inverse FVG yakını')
        if SMC_IFVG_BB_AI_ENABLED and f.get('f_breaker_block_active') == 1:
            tact_score += 4; tact_pos.append('Breaker block aktif')
        # VWAP -2σ (mean reversion tactical fırsat)
        if f.get('f_at_vwap_minus_2sigma') == 1:
            tact_score += 7; tact_pos.append('VWAP -2σ bölgesi (institutional buy zone)')
        # UDVR ve Force Index hesaplanır ve ayrı alanlarda raporlanır; bağımsız
        # katkıları ölçülene kadar taktik canlı skora veya oy listesine girmez.

        tact_score = max(0, min(100, tact_score))

        # ─── VERDICT ──────────────────────────────────────────────────
        _struct_strong = struct_score >= 65
        _struct_weak   = struct_score <= 35
        _tact_strong   = tact_score >= 65
        _tact_weak     = tact_score <= 35

        if _struct_strong and _tact_strong:
            _verdict = "Uzun ve kısa pencere aynı yönde güçlü — hacim/katılım görünümü uyumlu"
        elif _struct_strong and _tact_weak:
            _verdict = "Uzun pencere güçlü, kısa pencere zayıf — yakın vadede teyit eksik"
        elif _struct_weak and _tact_strong:
            _verdict = "Kısa pencere güçlü, uzun pencere zayıf — hareket yapısal katmanla teyit edilmiyor"
        elif _struct_weak and _tact_weak:
            _verdict = "Uzun ve kısa pencere zayıf — hacim/katılım desteği yok"
        elif _struct_strong:
            _verdict = "Uzun pencere güçlü, kısa pencere nötr — yakın vade teyidi bekleniyor"
        elif _tact_strong:
            _verdict = "Kısa pencere güçlü, uzun pencere nötr — birkaç günlük hareket desteği var"
        elif _struct_weak:
            _verdict = "Uzun pencere zayıf, kısa pencere nötr — göreli hacim/katılım desteği eksik"
        elif _tact_weak:
            _verdict = "Kısa pencere zayıf, uzun pencere nötr — birkaç günlük satıcı baskısı"
        else:
            _verdict = "Uzun ve kısa pencere nötr"

        return {
            'structural_score': round(struct_score, 1),
            'tactical_score': round(tact_score, 1),
            'structural_signals_pos': struct_pos,
            'structural_signals_neg': struct_neg,
            'tactical_signals_pos': tact_pos,
            'tactical_signals_neg': tact_neg,
            'verdict': _verdict,
        }
    except Exception:
        return None

@st.cache_data(ttl=600)
def calculate_sentiment_score(ticker):
    try:
        # Veri Çekme (2y: SMA200 garantisi için)
        df = get_safe_historical_data(ticker, period="1y")
        if df is None or len(df) < 200: return None
        
        close = df['Close']; high = df['High']; low = df['Low']; volume = df['Volume']
        
        # --- TANIMLAMALAR (Endeks/Hisse Ayrımı) ---
        bist_indices_roots = [
            "XU100", "XU030", "XU050", "XBANK", "XUSIN", "XTEKN", 
            "XBLSM", "XGMYO", "XTRZM", "XILET", "XKMYA", "XMANA", 
            "XSPOR", "XILTM", "XINSA", "XHOLD", "XTUMY"
        ]
        is_global_index = ticker.startswith("^")
        is_bist_index = any(root in ticker for root in bist_indices_roots)
        is_crypto = "-USD" in ticker
        is_index = is_global_index or is_bist_index or is_crypto
        
        # --- PUAN AĞIRLIKLARI (Fama-French ampirik consensus: momentum+RS en prediktif) ---
        if is_index:
            W_STR, W_TR, W_VOL = 10, 25, 25
            W_MOM, W_VOLA = 25, 15
            W_RS = 0
        else:
            W_STR, W_TR, W_VOL = 10, 20, 20
            W_MOM, W_VOLA = 20, 10
            W_RS = 20

        # =========================================================
        # 1. YAPI (MARKET STRUCTURE) — HH+HL zinciri (ICT mantığı)
        # =========================================================
        score_str = 0; reasons_str = []
        curr_close = float(close.iloc[-1])

        # Son 40 barda pivot noktaları tespit et (2-bar her yanda)
        _hh_count = 0; _hl_count = 0
        _prev_ph = None; _prev_pl = None
        _h_arr = high.values; _l_arr = low.values; _c_arr = close.values
        _n = len(_h_arr)
        for _i in range(2, min(_n - 2, 40)):
            _ri = _n - 1 - _i  # geriden say
            if _ri < 2: continue
            _ph = _h_arr[_ri]
            if (_ph > _h_arr[_ri-1] and _ph >= _h_arr[_ri-2] and
                    _ph > _h_arr[_ri+1] and _ph >= _h_arr[_ri+2]):
                if _prev_ph is not None and _ph > _prev_ph:
                    _hh_count += 1
                _prev_ph = _ph
            _pl = _l_arr[_ri]
            if (_pl < _l_arr[_ri-1] and _pl <= _l_arr[_ri-2] and
                    _pl < _l_arr[_ri+1] and _pl <= _l_arr[_ri+2]):
                if _prev_pl is not None and _pl > _prev_pl:
                    _hl_count += 1
                _prev_pl = _pl

        if _hh_count >= 2 and _hl_count >= 2:
            score_str += W_STR; reasons_str.append("HH+HL Zinciri (Güçlü Yapı)")
        elif _hh_count >= 1 and _hl_count >= 1:
            score_str += (W_STR * 0.5); reasons_str.append("Gelişen HH+HL Yapısı")

        # =========================================================
        # 2. TREND
        # =========================================================
        score_tr = 0; reasons_tr = []
        sma50 = close.rolling(50).mean(); sma200 = close.rolling(200).mean()
        ema20 = close.ewm(span=20, adjust=False).mean()
        
        if close.iloc[-1] > sma200.iloc[-1]: score_tr += (W_TR * 0.4); reasons_tr.append("Ana Trend+")
        if close.iloc[-1] > ema20.iloc[-1]: score_tr += (W_TR * 0.4); reasons_tr.append("Kısa Vade+")
        if ema20.iloc[-1] > sma50.iloc[-1]: score_tr += (W_TR * 0.2); reasons_tr.append("Hizalı")

        # =========================================================
        # 3. HACİM (ARTIK GLOBAL OLARAK PROJEKSİYONLU)
        # =========================================================
        score_vol = 0; reasons_vol = []
        
        # A. Ortalamayı hesapla (Bugünü hariç tutarak)
        avg_vol_20 = volume.iloc[:-1].tail(20).mean()
        if pd.isna(avg_vol_20) or avg_vol_20 == 0: avg_vol_20 = 1
        
        # B. Projeksiyonlu Hacim (Ana depodan zaten işlenmiş olarak geliyor)
        projected_vol = float(volume.iloc[-1])
        
        # KURAL 1: Hacim Artışı (Ortalamadan Büyük mü?)
        if projected_vol > avg_vol_20:
            score_vol += (W_VOL * 0.6)
            reasons_vol.append("Hacim Artışı")
            
        # KURAL 2: OBV — EMA(20) smoothing + eğim kontrolü
        obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
        obv_ema = obv.ewm(span=20, adjust=False).mean()
        obv_slope = float(obv_ema.iloc[-1]) - float(obv_ema.iloc[-5])
        if obv.iloc[-1] > obv_ema.iloc[-1]:
            score_vol += (W_VOL * 0.25); reasons_vol.append("OBV>EMA")
        if obv_slope > 0:
            score_vol += (W_VOL * 0.15); reasons_vol.append("OBV Eğim+")

        # =========================================================
        # 4. MOMENTUM
        # =========================================================
        score_mom = 0; reasons_mom = []
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs)).fillna(50)
        
        if rsi.iloc[-1] > 50: score_mom += 5; reasons_mom.append("RSI>50")
        if rsi.iloc[-1] > rsi.iloc[-5]: score_mom += 5; reasons_mom.append("RSI İvme")

        ema12 = close.ewm(span=12, adjust=False).mean(); ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26; signal = macd.ewm(span=9, adjust=False).mean()
        if macd.iloc[-1] > signal.iloc[-1]: score_mom += 5; reasons_mom.append("MACD Al")

        # --- DIVERGENCE TESPİTİ (Bullish RSI + OBV) ---
        try:
            _w = 20  # son 20 bar içinde bakıyoruz
            _c_w  = close.iloc[-_w:].values
            _r_w  = rsi.iloc[-_w:].values
            _obve = obv_ema.iloc[-_w:].values  # obv_ema yukarıda hesaplandı

            # Fiyat yeni dip yaptı mı?
            _price_low_now  = float(_c_w[-1])
            _price_low_prev = float(_c_w[:-1].min())
            _price_new_low  = _price_low_now < _price_low_prev * 0.995

            # Bullish RSI divergence: fiyat dip → RSI önceki dipten yüksek
            _rsi_now  = float(_r_w[-1])
            _rsi_prev = float(_r_w[:-1].min())
            if _price_new_low and _rsi_now > (_rsi_prev + 3):
                score_mom += 5; reasons_mom.append("📈 RSI Div (Bullish)")

            # Bullish OBV divergence: fiyat düşüyor ama OBV EMA artıyor
            _obv_slope_w = float(_obve[-1]) - float(_obve[-6])
            _price_slope = float(_c_w[-1]) - float(_c_w[-6])
            if _price_slope < 0 and _obv_slope_w > 0:
                score_mom += 5; reasons_mom.append("📈 OBV Div (Kurumsal Toplama)")
        except Exception:
            pass

        # =========================================================
        # 5. HACİM KALİTESİ (Para Akışı mantığı)
        # =========================================================
        score_vola = 0; reasons_vola = []
        try:
            _is_up = close > close.shift(1)
            # Kriter 1: Son 20 günde yükseliş hacmi ort. > düşüş hacmi ort.
            _up_vol   = volume.where(_is_up, 0).iloc[-20:]
            _down_vol = volume.where(~_is_up, 0).iloc[-20:]
            _avg_up   = _up_vol[_up_vol > 0].mean() if (_up_vol > 0).any() else 0
            _avg_down = _down_vol[_down_vol > 0].mean() if (_down_vol > 0).any() else 1
            if _avg_up > _avg_down:
                score_vola += 8; reasons_vola.append("Alım Hacmi > Satım")
            # Kriter 2: Son 10 günde hacmin %60'ı yükseliş günlerinde mi?
            _last10_up_vol   = float(volume.where(_is_up, 0).iloc[-10:].sum())
            _last10_total_vol = float(volume.iloc[-10:].sum())
            if _last10_total_vol > 0 and (_last10_up_vol / _last10_total_vol) >= 0.60:
                score_vola += 7; reasons_vola.append("Birikim Ağırlıklı (10G)")
        except Exception:
            pass
            
        # =========================================================
        # 6. GÜÇ (RS)
        # =========================================================
        score_rs = 0; reasons_rs = []
        if not is_index:
            bench_ticker = "XU100.IS" if ".IS" in ticker else "^GSPC"
            try:
                bench_df = get_safe_historical_data(bench_ticker, period="1y")
                if bench_df is not None:
                    common_idx = close.index.intersection(bench_df.index)
                    stock_p = close.loc[common_idx]
                    bench_p = bench_df['Close'].loc[common_idx]
                    
                    rs_ratio = stock_p / bench_p
                    rs_ma = rs_ratio.rolling(50).mean()
                    mansfield = ((rs_ratio / rs_ma) - 1) * 10
                    
                    if mansfield.iloc[-1] > 0: score_rs += 5; reasons_rs.append("Mansfield+")
                    if mansfield.iloc[-1] > mansfield.iloc[-5]: score_rs += 5; reasons_rs.append("RS İvme")
                    
                    stock_chg = (stock_p.iloc[-1] - stock_p.iloc[-2]) / stock_p.iloc[-2]
                    bench_chg = (bench_p.iloc[-1] - bench_p.iloc[-2]) / bench_p.iloc[-2]
                    if bench_chg < 0 and stock_chg > 0: score_rs += 5; reasons_rs.append("Alpha (Lider)")
                    elif stock_chg > bench_chg: score_rs += 3; reasons_rs.append("Endeks Üstü")
            except: reasons_rs.append("Veri Yok")

        total = int(score_str + score_tr + score_vol + score_mom + score_vola + score_rs)
        bars = int(total / 5)
        bar_str = "【" + "█" * bars + "░" * (20 - bars) + "】"
        def fmt(lst): 
            if not lst: return ""
            return f"<span style='font-size:0.7rem; color:#94a3b8; font-style:italic; font-weight:300;'>({' + '.join(lst)})</span>"
        
        if is_index:
            rs_text = f"<span style='color:#94a3b8; font-style:italic; font-weight:600;'>Devre Dışı</span>"
        else:
            rs_text = f"{int(score_rs)}/{W_RS} {fmt(reasons_rs)}"

        return {
            "total": total, "bar": bar_str, 
            "mom": f"{int(score_mom)}/{W_MOM} {fmt(reasons_mom)}",
            "vol": f"{int(score_vol)}/{W_VOL} {fmt(reasons_vol)}", 
            "tr": f"{int(score_tr)}/{W_TR} {fmt(reasons_tr)}",
            "vola": f"{int(score_vola)}/{W_VOLA} {fmt(reasons_vola)}", 
            "str": f"{int(score_str)}/{W_STR} {fmt(reasons_str)}",
            "rs": rs_text, 
            "raw_rsi": rsi.iloc[-1], "raw_macd": (macd-signal).iloc[-1], "raw_obv": obv.iloc[-1], "raw_atr": 0,
            "is_index": is_index
        }
    except: return None

@st.cache_data(ttl=600, show_spinner=False)
def calculate_smart_money_score(ticker):
    """
    Backwards-compatible wrapper:
      - Endeksler (XU100, ^GSPC, =F, -USD, GC=F): eski 5 kriterli EMA hizalama mantığı (Endeks Radarı)
      - Hisseler: Erken Radar 36 senaryo motoru (evaluate_erken_radar)
    Çıktı eski formatla aynı: dict (score, status, criteria, summary_text, pre_launch, ...).
    """
    # --- HİSSELER İÇİN: Erken Radar ---
    _is_index_tk = (ticker.startswith("XU") or ticker.startswith("^") or
                    ticker.endswith("=F") or "-USD" in ticker or ticker == "GC=F")
    if not _is_index_tk:
        try:
            df_stock = get_safe_historical_data(ticker)
            if df_stock is None or len(df_stock) < 60:
                return None
            _is_bist_tk = ".IS" in ticker
            _bench_t = "XU100.IS" if _is_bist_tk else "^GSPC"
            _bench_df = None
            try:
                _bench_df = get_safe_historical_data(_bench_t)
            except Exception:
                pass
            er = evaluate_erken_radar(ticker, df_stock, _bench_df)
            if er is None:
                return None
            primary  = er.get('primary')
            quality  = er.get('overall_quality', 0)
            red_flags = er.get('red_flags', []) or []
            # Status text
            if primary:
                status_txt = primary['name']
            elif red_flags:
                status_txt = "Risk Sinyali"
            else:
                status_txt = "Senaryo Yok"
            # Status color
            if red_flags and not primary:
                s_color = "#ef4444"
            elif quality >= 75:
                s_color = "#10b981"
            elif quality >= 50:
                s_color = "#22c55e"
            elif quality >= 30:
                s_color = "#f59e0b"
            else:
                s_color = "#94a3b8"
            # pre_launch: 5★ B-kategorisi (sıkışma) senaryosu varsa
            pre_launch = bool(primary and primary.get('category') == 'B' and primary.get('stars') == 5)
            # Summary text — primary + confirms
            sums = []
            if primary:
                sums.append(f"{primary['name']}: {primary['description']}")
            for c in (er.get('confirmations') or []):
                sums.append(f"+ {c['name']}: {c['description']}")
            for rf in red_flags:
                sums.append(f"⚠ {rf['name']}: {rf['description']}")
            summary_text = " | ".join(sums) if sums else "Belirgin Erken Radar senaryosu yok"
            # PİYASA FİLTRESİ (BIST için)
            market_score = None
            market_note = ""
            if _is_bist_tk:
                try:
                    _mkt = calculate_smart_money_score("XU100.IS")
                    if _mkt:
                        market_score = _mkt.get("score")
                        if market_score is not None:
                            if market_score < 40:
                                market_note = f"BIST puanı: {market_score}/100 — temkinli olmak lazım"
                            elif market_score >= 65:
                                market_note = f"BIST puanı: {market_score}/100 — Endeks de destekliyor"
                            else:
                                market_note = f"BIST puanı: {market_score}/100 — Endeks nötr"
                except Exception:
                    pass
            return {
                "score": quality,
                "score5": None,
                "score_trend": "",
                "status": status_txt,
                "status_color": s_color,
                "criteria": {},   # eski format, kullanılmıyor artık
                "pre_launch": pre_launch,
                "market_score": market_score,
                "market_note": market_note,
                "summary_text": summary_text,
                "_er_data": er,   # paneller için tam veri
            }
        except Exception:
            return None
    # --- ENDEKSLER İÇİN: ESKİ MANTIK (EMA hizalama) ---
    try:
        df = get_safe_historical_data(ticker)
        if df is None or len(df) < 60:
            return None

        is_index = (ticker.startswith("XU") or ticker.startswith("^") or
                    ticker.endswith("=F") or "-USD" in ticker or ticker == "GC=F")
        is_bist  = ".IS" in ticker or ticker.startswith("XU")

        close  = df['Close'].squeeze()
        volume = df['Volume'].squeeze()

        # ── KRİTER 1: TREND ZEMİNİ ───────────────────────────────
        sma50       = close.rolling(50).mean()
        above_sma50 = bool(close.iloc[-1] > sma50.iloc[-1])
        sma50_up    = bool(sma50.iloc[-1] > sma50.iloc[-6])
        trend_pass  = above_sma50 and sma50_up

        trend_days = 0
        if trend_pass:
            for i in range(1, min(60, len(df) - 1)):
                if close.iloc[-i-1] > sma50.iloc[-i-1] and sma50.iloc[-i-1] > sma50.iloc[-i-6] if i+6 < len(df) else True:
                    trend_days += 1
                else:
                    break
        trend_days = max(1, trend_days) if trend_pass else 0
        trend_desc = (f"{trend_days} gündür 50MA üstünde ve yönü yukarı"
                      if trend_pass else
                      ("Fiyat 50MA altında" if not above_sma50 else "50MA yönü aşağı"))
        trend_edu  = "50 günlük hareketli ortalama trendin omurgasıdır. Fiyat bu çizginin üstündeyken ve çizgi yukarı eğimliyken, piyasanın genel eğilimi alış yönünde demektir. Kurumsal fonlar genellikle bu koşulu karşılayan hisseler için pozisyon açar."

        # ── KRİTER 2: RELATİF GÜÇ (hisse) / EMA HİZALAMASI (endeks) ──
        # Hisseler: 20G RS vs endeks (swing vade için yeterli)
        # Endeksler: EMA8>EMA21 hizalaması + ivme (N/A yerine anlamlı kriter)
        rs_pass = None
        rs_days = 0
        rs_desc = "Endeks — kıyaslama yapılmaz"
        rs_edu  = "Relatif güç, bir hissenin kendi endeksine kıyasla ne kadar iyi performans gösterdiğini ölçer. Endeksten güçlü seyreden hisseler kurumsal ilgi görüyor anlamına gelir; zayıf piyasada bile ayakta kalabilirler."

        if not is_index:
            try:
                bench_t  = "XU100.IS" if is_bist else "^GSPC"
                bench_df = get_safe_historical_data(bench_t)
                if bench_df is not None and len(bench_df) >= 25:
                    bench_close = bench_df['Close'].squeeze()
                    common = close.index.intersection(bench_close.index)
                    if len(common) >= 22:
                        s_ret = float(close.loc[common].iloc[-1] / close.loc[common].iloc[-21] - 1)
                        b_ret = float(bench_close.loc[common].iloc[-1] / bench_close.loc[common].iloc[-21] - 1)
                        diff  = (s_ret - b_ret) * 100
                        rs_pass = diff > 0
                        for i in range(1, min(30, len(common) - 21)):
                            sr = float(close.loc[common].iloc[-i-1] / close.loc[common].iloc[-i-22] - 1)
                            br = float(bench_close.loc[common].iloc[-i-1] / bench_close.loc[common].iloc[-i-22] - 1)
                            if sr > br:
                                rs_days += 1
                            else:
                                break
                        rs_days = max(1, rs_days) if rs_pass else 0
                        rs_desc = (f"{rs_days} gündür endeksten %{abs(diff):.1f} güçlü"
                                   if rs_pass else f"Endeksten %{abs(diff):.1f} zayıf")
            except:
                rs_pass = False
                rs_desc = "RS hesaplanamadı"
        else:
            # Endeks için EMA hizalaması — swing trade ivmesini ölçer
            try:
                _ema8  = close.ewm(span=8,  adjust=False).mean()
                _ema21 = close.ewm(span=21, adjust=False).mean()
                _ema_aligned = bool(close.iloc[-1] > _ema8.iloc[-1]) and bool(_ema8.iloc[-1] > _ema21.iloc[-1])
                _ema_accel   = bool(_ema8.iloc[-1] > _ema8.iloc[-3])  # EMA8 son 3 günde yukarı mı?
                _pct_vs_ema21 = (float(close.iloc[-1]) / float(_ema21.iloc[-1]) - 1) * 100
                rs_pass = _ema_aligned and _ema_accel
                if rs_pass:
                    rs_desc = f"EMA8>EMA21 hizalı, ivme artıyor (+%{_pct_vs_ema21:.1f} EMA21 üstünde)"
                elif _ema_aligned:
                    rs_desc = f"EMA dizilimi hizalı ama ivme kaybediyor"
                else:
                    rs_desc = f"EMA dizilimi bozuk (fiyat veya EMA8, EMA21 altında)"
                rs_edu = "Endeksler için EMA hizalaması (fiyat>EMA8>EMA21) kısa vadeli momentum sağlığını gösterir. EMA8 son 3 günde yükseliyorsa swing trade için trend desteği aktif demektir."
            except:
                rs_pass = False
                rs_desc = "EMA hesaplanamadı"

        # ── KRİTER 3: BIRIKIM ────────────────────────────────────────
        # Hisseler: OBV (hacim bazlı) | Endeksler: EMA20 fiyat momentum proxy
        # (Endekslerde Yahoo hacim verisi = 0 veya bozuk → OBV güvenilmez)
        if is_index:
            _ema20_acc  = close.ewm(span=20, adjust=False).mean()
            _above_e20  = bool(close.iloc[-1] > _ema20_acc.iloc[-1])
            _e20_rising = bool(_ema20_acc.iloc[-1] > _ema20_acc.iloc[-5])
            accum_pass  = _above_e20 and _e20_rising
            accum_days  = 0
            if accum_pass:
                for i in range(1, min(40, len(df) - 6)):
                    try:
                        if (close.iloc[-i-1] > _ema20_acc.iloc[-i-1] and
                                _ema20_acc.iloc[-i-1] > _ema20_acc.iloc[-i-6]):
                            accum_days += 1
                        else:
                            break
                    except: break
            accum_days = max(1, accum_days) if accum_pass else 0
            accum_desc = (f"{accum_days} gündür EMA20 üstünde, eğim yukarı — fiyat momentumu sağlıklı"
                          if accum_pass else "EMA20 altında veya EMA20 eğimi aşağı")
            accum_edu  = ("Endeksler için fiyat EMA20 üstünde ve EMA20 yönü yukarıyken momentum "
                          "sağlıklı demektir. Hacim verisi endekslerde güvenilmez olduğundan "
                          "bu kriter fiyat hareketine dayanır.")
        else:
            price_chg  = close.diff()
            direction  = price_chg.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
            obv        = (volume * direction).cumsum()
            obv_sma20  = obv.rolling(20).mean()
            obv_5g_up  = bool(obv.iloc[-1] > obv.iloc[-6])   # kısa ivme
            obv_14g_up = bool(obv.iloc[-1] > obv.iloc[-15])  # orta vade (RSI uyumlu)
            obv_above  = bool(obv.iloc[-1] > obv_sma20.iloc[-1])
            accum_pass = obv_5g_up and obv_14g_up and obv_above
            accum_days = 0
            if accum_pass:
                for i in range(1, min(30, len(df) - 20)):
                    if obv.iloc[-i-1] > obv_sma20.iloc[-i-1]:
                        accum_days += 1
                    else:
                        break
            accum_days = max(1, accum_days) if accum_pass else 0
            price_flat = abs(float(close.iloc[-1] / close.iloc[-6] - 1)) < 0.02
            if obv_5g_up and not obv_14g_up:
                accum_desc = "OBV kafa çeviriyor (toparlanma) — 5g yukarı ama 14g hâlâ baskılı"
            elif not obv_5g_up and obv_14g_up:
                accum_desc = "OBV kafa çeviriyor (zayıflama) — 5g aşağı, 14g hâlâ pozitif"
            elif accum_pass and price_flat:
                accum_desc = f"{accum_days} gündür OBV yükseliyor, fiyat hareketsiz (gizli birikim diverjansı)"
            elif accum_pass:
                accum_desc = f"{accum_days} gündür OBV 5g+14g pencerelerinde yükseliyor — aktif kurumsal alım"
            else:
                accum_desc = "Net birikim sinyali yok"
            accum_edu  = ("OBV (On-Balance Volume) para akışını izler. 5 günlük (kısa ivme) ve "
                          "14 günlük (RSI uyumlu orta vade) çift pencere ile değerlendirilir. "
                          "Her ikisi de yükseliyorsa aktif kurumsal alım baskısı var demektir.")

        # ── KRİTER 4: ENERJİ/YAPI ────────────────────────────────────
        # Hisseler: BB Squeeze (kırılım enerjisi) | Endeksler: EMA20>EMA50 + aşırı uzama yok
        # (Güçlü trendlerde bantlar genişler → squeeze hiç olmaz → endekste anlamsız)
        squeeze_days_ago = 0
        if is_index:
            _ema20_sq   = close.ewm(span=20, adjust=False).mean()
            _ema50_sq   = close.rolling(50).mean()
            _dist_pct   = abs(float(close.iloc[-1]) / float(_ema50_sq.iloc[-1]) - 1) * 100 if float(_ema50_sq.iloc[-1]) > 0 else 999
            _golden     = bool(_ema20_sq.iloc[-1] > _ema50_sq.iloc[-1])
            _not_overex = _dist_pct < 15  # EMA50'den %15'ten az uzakta
            squeeze_pass = _golden and _not_overex
            squeeze_days = 0
            if squeeze_pass:
                for i in range(1, min(40, len(df) - 1)):
                    try:
                        if _ema20_sq.iloc[-i-1] > _ema50_sq.iloc[-i-1]:
                            squeeze_days += 1
                        else:
                            break
                    except: break
            squeeze_days = max(1, squeeze_days) if squeeze_pass else 0
            if squeeze_pass:
                squeeze_desc = (f"{squeeze_days} gündür EMA20 > EMA50 (golden zone) · "
                                f"EMA50'den %{_dist_pct:.1f} uzakta")
            elif _dist_pct >= 15:
                squeeze_desc = f"EMA50'den %{_dist_pct:.1f} uzakta — aşırı uzama, geri çekilme riski"
            else:
                squeeze_desc = "EMA20 < EMA50 — orta vade momentumu bozuk"
            squeeze_edu = ("Endeksler için EMA20>EMA50 hizalaması orta vadeli golden zone'u gösterir. "
                           "Fiyat EMA50'den %15'ten fazla uzaklaşırsa aşırı uzama riski artar.")
        else:
            sq_now, sq_prev = check_lazybear_squeeze_breakout(df)
            squeeze_pass    = sq_now or sq_prev
            if not squeeze_pass:
                for _back in range(2, 11):
                    if len(df) > _back + 22:
                        try:
                            sq_b, _ = check_lazybear_squeeze_breakout(df.iloc[:-_back])
                            if sq_b:
                                squeeze_pass    = True
                                squeeze_days_ago = _back
                                break
                        except:
                            break
            squeeze_days = 0
            if squeeze_pass and squeeze_days_ago == 0:
                for i in range(1, min(30, len(df) - 22)):
                    try:
                        sq_i, _ = check_lazybear_squeeze_breakout(df.iloc[:-i])
                        if sq_i:
                            squeeze_days += 1
                        else:
                            break
                    except:
                        break
            squeeze_days = max(1, squeeze_days) if (squeeze_pass and squeeze_days_ago == 0) else 0
            if squeeze_days_ago > 0:
                squeeze_desc = f"{squeeze_days_ago} gün önce sıkışma sona erdi — kırılım enerjisi hâlâ taze"
            elif squeeze_pass:
                squeeze_desc = (f"{squeeze_days} gündür BB sıkışması aktif — enerji birikimi"
                                if squeeze_days > 0 else "BB sıkışması aktif")
            else:
                squeeze_desc = "Aktif volatilite sıkışması yok"
            squeeze_edu = ("Bollinger Bantları Keltner Kanalı'nın içine girdiğinde 'squeeze' oluşur. "
                           "Tarihsel olarak squeeze sonrası güçlü yönlü hareketler gelir.")

        # ── KRİTER 5: TETİKLEYİCİ (SWING ENTRY KALİTESİ) ──────────
        vol_sma20 = volume.rolling(20).mean()
        high20    = close.iloc[-21:-1].max()

        # RSI — aşırı alım/satım filtresi
        # Hisse: ≤73 (en güçlü kırılımlar 68-73 bandında olur, 70 çok katıydı)
        # Endeks: ≤75 (endeksler 70+ uzun süre kalabilir)
        _tr_delta = close.diff()
        _tr_gain  = (_tr_delta.where(_tr_delta > 0, 0)).rolling(14).mean()
        _tr_loss  = (-_tr_delta.where(_tr_delta < 0, 0)).rolling(14).mean().replace(0, 0.00001)
        _rsi      = float((100 - (100 / (1 + _tr_gain / _tr_loss))).iloc[-1])
        _rsi_ok   = 35 <= _rsi <= (75 if is_index else 73)

        # [4 — DEĞİŞİKLİK] Uzama filtresi: SMA50'den %25+ uzak = geç kalınmış uyarısı
        _sma50_now   = float(close.rolling(50).mean().iloc[-1])
        _sma50_dist  = ((float(close.iloc[-1]) / _sma50_now) - 1) * 100 if _sma50_now > 0 else 0
        _overextended = not is_index and _sma50_dist > 25  # Endeksler için uygulanmaz

        # R/R tahmini: stop = son 5G dip, hedef = 20G zirve
        _low5   = float(df['Low'].iloc[-5:].min())
        _curr   = float(close.iloc[-1])
        _risk   = max(_curr - _low5, 0.001)
        _reward = max(float(high20) - _curr, 0)
        _rr     = _reward / _risk if _risk > 0 else 0

        trigger_pass     = False
        trigger_days_ago = 0

        for i in range(1, 4):
            try:
                idx      = -(i)
                day_cl   = float(close.iloc[idx])
                prev_cl  = float(close.iloc[idx - 1])
                day_vol  = float(volume.iloc[idx])
                avg_vol  = float(vol_sma20.iloc[idx])
                is_green = day_cl > prev_cl
                vol_high = day_vol > avg_vol * 1.5
                breakout = day_cl > float(high20) and vol_high
                if ((is_green and vol_high) or breakout) and _rsi_ok:
                    trigger_pass     = True
                    trigger_days_ago = i
                    break
            except:
                continue

        _rr_txt   = f" | R/R {_rr:.1f}:1" if _rr > 0.1 else ""
        _rsi_txt  = f" | RSI {_rsi:.0f}"
        _rr_warn  = " ⚠️ Hedef yakın" if (trigger_pass and _rr < 1.5) else ""
        _ext_warn = f" ⚠️ SMA50'den %{_sma50_dist:.0f} uzakta" if _overextended else ""

        if trigger_pass:
            trigger_desc = (f"Bugün: Yüksek hacimli sinyal{_rr_txt}{_rsi_txt}{_rr_warn}{_ext_warn}"
                            if trigger_days_ago == 1
                            else f"{trigger_days_ago}g önce kırılım{_rr_txt}{_rsi_txt}{_rr_warn}{_ext_warn}")
        elif _rsi > (75 if is_index else 73):
            trigger_desc = f"RSI {_rsi:.0f} — aşırı alım, giriş riskli{_rr_txt}{_ext_warn}"
        elif _rsi < 35:
            trigger_desc = f"RSI {_rsi:.0f} — aşırı satım, dönüş sinyalleri gözlemleniyor{_rr_txt}"
        elif accum_pass:
            trigger_desc = f"Hazır ama tetik atılmadı{_rr_txt}{_rsi_txt}{_ext_warn}"
        else:
            trigger_desc = f"Tetikleyici oluşmadı{_rsi_txt}{_ext_warn}"
        trigger_edu = ("Kırılım: 20G zirve aşıldı + hacim 1.5x ort. + RSI 35-73 bölgesi. "
                       "SMA50'den %25+ uzaklaşma geç kalınmış sinyalidir. "
                       "R/R 1.5:1 altındaysa kısa vadeli direnç yakın.")

        # ── SKOR ─────────────────────────────────────────────────
        # Hisse için squeeze ZORUNLU KRİTER DEĞİL → bonus puan sistemi
        # Squeeze varsa +8 puan bonus (pre-launch için ayrıca değerlendiriliyor)
        # OBV diverjansı (fiyat yatay + OBV yükseliyor) nadir ve değerli → +5 bonus
        # Uzama varsa (SMA50 %25+) → -7 puan ceza
        if is_index:
            w = {"trend": 1.5, "rs": 1.3, "accum": 0.8, "squeeze": 1.1, "trigger": 1.2}
        else:
            w = {"trend": 1.0, "rs": 1.3, "accum": 1.7, "trigger": 1.2}  # squeeze yok

        def _bool(v): return 1.0 if v is True else 0.0

        if is_index:
            raw   = (_bool(trend_pass)*w["trend"] + _bool(rs_pass)*w["rs"] +
                     _bool(accum_pass)*w["accum"] + _bool(squeeze_pass)*w["squeeze"] +
                     _bool(trigger_pass)*w["trigger"])
            max_w = sum(w.values())
            score = round((raw / max_w) * 100)
        else:
            # Hisse: squeeze bonus sistemi
            max_w  = sum(w.values())  # trend+rs+accum+trigger = 5.2
            raw    = (_bool(trend_pass)*w["trend"] + _bool(rs_pass)*w["rs"] +
                      _bool(accum_pass)*w["accum"] + _bool(trigger_pass)*w["trigger"])
            score  = round((raw / max_w) * 100)
            # Bonuslar
            _obv_div = accum_pass and not is_index and abs(float(close.iloc[-1] / close.iloc[-6] - 1)) < 0.02
            if squeeze_pass:  score = min(100, score + 8)   # squeeze bonus
            if _obv_div:      score = min(100, score + 5)   # OBV diverjans bonus
            if _overextended: score = max(0,   score - 7)   # uzama cezası

        # ── PRE-LAUNCH TESPİTİ ────────────────────────────────────
        # squeeze artık pre-launch için de bonus mantığıyla çalışıyor:
        # trend+accum+rs hazır + tetikleyici yok = pre-launch (squeeze varsa güçlü)
        non_trigger_ok = (
            trend_pass and
            accum_pass and
            (rs_pass is True or rs_pass is None)
        )
        pre_launch = non_trigger_ok and (not trigger_pass)
        pre_launch_days = accum_days

        # [6 — DEĞİŞİKLİK] Senaryo etiketi: set-up'ın türünü belirt
        if trigger_pass and not squeeze_pass:
            _scenario = "Ana Trend Devamı"       # hareket başlamış, squeeze olmadan kırılım
        elif squeeze_pass and not trigger_pass:
            _scenario = "Kırılım Öncesi"     # enerji birikmiş, henüz ateşlenmedi
        elif squeeze_pass and trigger_pass:
            _scenario = "Güçlü Set-up"      # hem squeeze hem tetikleyici
        elif pre_launch:
            _scenario = "Pre-Launch"
        elif _overextended:
            _scenario = "Geç Kalmış"
        else:
            _scenario = ""

        trigger_age = ""
        if trigger_pass:
            trigger_age = " · bugün" if trigger_days_ago == 1 else f" · {trigger_days_ago}g önce"

        # Durum etiketi
        _idx_overbought = is_index and trend_pass and _rsi > 75 and not trigger_pass
        if _idx_overbought:
            status, status_color, status_bg = "🔥 Trendin içindesin — düzeltme bekle", "#ea580c", "#fff7ed"
        elif pre_launch:
            _pl_tag = f" [{_scenario}]" if _scenario else ""
            if pre_launch_days >= 15:
                status, status_color, status_bg = f"⚠️ Bayatladı ({pre_launch_days}g){_pl_tag}", "#f59e0b", "#fffbeb"
            else:
                status, status_color, status_bg = f"🎯 BİRİKİM TAMAMLANDI ({pre_launch_days}g){_pl_tag}", "#06b6d4", "#ecfeff"
        elif _overextended and score < 75:
            status, status_color, status_bg = f"⚠️ Geç Kalmış Olabilir (SMA50 +%{_sma50_dist:.0f})", "#f59e0b", "#fffbeb"
        elif score >= 85: status, status_color, status_bg = f"🔥 Harekete geç{trigger_age}" + (f" · {_scenario}" if _scenario else ""), "#10b981", "#ecfdf5"
        elif score >= 65: status, status_color, status_bg = f"⚡ YÜKSELİŞ KRİTERLERİ KARŞILANDI{trigger_age}" + (f" · {_scenario}" if _scenario else ""), "#3b82f6", "#eff6ff"
        elif score >= 45: status, status_color, status_bg = "🏕 Henüz değil, takipte",          "#f59e0b", "#fffbeb"
        elif score >= 25: status, status_color, status_bg = "🌱 Çok erken, sıra gelecek",       "#94a3b8", "#f8fafc"
        else:             status, status_color, status_bg = "😴 Boşver şimdilik",               "#64748b", "#f1f5f9"

        # ── SKOR TRENDİ (5 gün önce) ─────────────────────────────
        score_trend = ""
        score5 = None
        if len(df) >= 65:
            try:
                df5     = df.iloc[:-5].copy()
                close5  = df5['Close'].squeeze()
                vol5    = df5['Volume'].squeeze()
                dir5    = close5.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
                obv5    = (vol5 * dir5).cumsum()
                obv_s5  = obv5.rolling(20).mean()
                sma50_5 = close5.rolling(50).mean()
                trend5  = bool(close5.iloc[-1] > sma50_5.iloc[-1]) and bool(sma50_5.iloc[-1] > sma50_5.iloc[-6])
                accum5  = bool(obv5.iloc[-1] > obv_s5.iloc[-1]) and bool(obv5.iloc[-1] > obv5.iloc[-6])
                sq5_n, sq5_p = check_lazybear_squeeze_breakout(df5)
                squeeze5 = sq5_n or sq5_p
                vsma5   = vol5.rolling(20).mean()
                h20_5   = float(close5.iloc[-21:-1].max()) if len(close5) >= 21 else float(close5.max())
                trig5   = False
                for j in range(1, 4):
                    try:
                        if ((float(close5.iloc[-j]) > float(close5.iloc[-j-1]) and float(vol5.iloc[-j]) > float(vsma5.iloc[-j]) * 1.5)
                                or (float(close5.iloc[-j]) > h20_5 and float(vol5.iloc[-j]) > float(vsma5.iloc[-j]) * 1.5)):
                            trig5 = True; break
                    except: continue
                # Hisse ve endeks için ayrı formül (ana skor hesabıyla tutarlı)
                if is_index:
                    raw5   = (_bool(trend5)*w["trend"] + _bool(rs_pass)*w["rs"] +
                              _bool(accum5)*w["accum"] + _bool(squeeze5)*w["squeeze"] +
                              _bool(trig5)*w["trigger"])
                    score5 = round((raw5 / max_w) * 100)
                else:
                    raw5   = (_bool(trend5)*w["trend"] + _bool(rs_pass)*w["rs"] +
                              _bool(accum5)*w["accum"] + _bool(trig5)*w["trigger"])
                    score5 = round((raw5 / max_w) * 100)
                    if squeeze5:  score5 = min(100, score5 + 8)
                diff5 = score - score5
                score_trend = f"↑{diff5}" if diff5 > 3 else (f"↓{abs(diff5)}" if diff5 < -3 else "→")
            except:
                score_trend = ""

        # ── PİYASA FİLTRESİ ──────────────────────────────────────
        market_note  = ""
        market_score = None
        if not is_index:
            try:
                bench_t = "XU100.IS" if is_bist else "^GSPC"
                _mkt    = calculate_smart_money_score(bench_t)
                if _mkt:
                    market_score = _mkt["score"]
                    if market_score < 40:
                        market_note = f"BIST puanı: {market_score}/100 — temkinli olmak lazım"
                    elif market_score >= 65:
                        market_note = f"BIST puanı: {market_score}/100 — Endeks de destekliyor"
                    else:
                        market_note = f"BIST puanı: {market_score}/100 — Endeks nötr"
            except:
                pass

        # ── AI PROMPT ÖZET ───────────────────────────────────────
        pre_launch_note = (f" ⚠️ PRE-LAUNCH: {pre_launch_days} gündür 4 kriter hazır, tetikleyici bekleniyor — "
                           + ("sinyal bayatlamış olabilir, dikkatli ol." if pre_launch_days >= 15
                              else "ideal giriş penceresi.")) if pre_launch else ""
        trend_note = f" | Skor trendi (5g): {score_trend} ({score5}/100 → {score}/100)" if score5 is not None else ""
        criteria_summary = (
            f"Trend: {'✅ ' + trend_desc if trend_pass else '❌ ' + trend_desc} | "
            f"RS: {'✅ ' + rs_desc if rs_pass else ('N/A' if rs_pass is None else '❌ ' + rs_desc)} | "
            f"Birikim OBV: {'✅ ' + accum_desc if accum_pass else '❌ ' + accum_desc} | "
            f"BB Squeeze: {'✅ ' + squeeze_desc if squeeze_pass else '❌ ' + squeeze_desc} | "
            f"Tetikleyici: {'✅ ' + trigger_desc if trigger_pass else '❌ ' + trigger_desc}"
            f"{pre_launch_note}{trend_note}{market_note}"
        )

        return {
            "score":          score,
            "score5":         score5,
            "score_trend":    score_trend,
            "status":         status,
            "status_color":   status_color,
            "status_bg":      status_bg,
            "pre_launch":     pre_launch,
            "pre_launch_days": pre_launch_days,
            "market_score":   market_score,
            "market_note":    market_note,
            "criteria": {
                "trend":   {"pass": trend_pass,   "desc": trend_desc,   "edu": trend_edu,   "label": "Trend Zemini"},
                "rs":      {"pass": rs_pass,      "desc": rs_desc,      "edu": rs_edu,      "label": "EMA Hizalaması" if is_index else "Relatif Güç"},
                "accum":   {"pass": accum_pass,   "desc": accum_desc,   "edu": accum_edu,   "label": "Akıllı Para Birikimi"},
                "squeeze": {"pass": squeeze_pass, "desc": squeeze_desc, "edu": squeeze_edu, "label": "Volatilite Sıkışması"},
                "trigger": {"pass": trigger_pass, "desc": trigger_desc, "edu": trigger_edu, "label": "Kırılım Tetikleyici"},
            },
            "is_index":       is_index,
            "summary_text":   criteria_summary,
        }
    except Exception:
        return None

@st.cache_data(ttl=900)
def calculate_master_score(ticker, return_breakdown=False):
    """
    MASTER SKOR V2:
    - Temel analiz kaldırıldı (BIST için güvenilir veri yok)
    - ICT hesaplanır ve raporlanır; geçmiş karne nedeniyle canlı ağırlığı %0
    - Radar2: session_state yerine anlık hesaplama
    - Momentum eşiği 60 → 50'ye indirildi
    - ICT dışındaki mevcut ağırlıklar oransal normalize edilerek 0-100 ölçeği korunur

    return_breakdown=True → (final, pros, cons, breakdown_dict) — alt skorları döndürür
    """
    # 1. VERİLERİ TOPLA
    mini_data = calculate_minervini_sepa(ticker)
    sent_data = calculate_sentiment_score(ticker)
    ict_data  = calculate_ict_deep_analysis(ticker)
    tech      = get_tech_card_data(ticker)

    # Radar2: önce session_state'e bak, yoksa anlık hesapla
    r2_score = 0.0
    radar2_df = st.session_state.get('radar2_data')
    if radar2_df is not None and not radar2_df.empty and 'Sembol' in radar2_df.columns:
        row = radar2_df[radar2_df['Sembol'] == ticker]
        if not row.empty:
            r2_score = float(row.iloc[0]['Skor'])
    if r2_score == 0.0:
        try:
            df_live = get_safe_historical_data(ticker)
            bench_ticker = "XU100.IS" if ".IS" in ticker or ticker.startswith("XU") else "^GSPC"
            idx_data = get_safe_historical_data(bench_ticker)
            idx_close = idx_data['Close'] if idx_data is not None else None
            r2_res = process_single_radar2(ticker, df_live, idx_close, 0, 999999, 0)
            if r2_res:
                r2_score = float(r2_res.get('Skor', 0))
        except Exception:
            r2_score = 0.0

    is_index = ticker.startswith("^") or "XU" in ticker or "-USD" in ticker

    # AĞIRLIKLAR: ICT hesaplanmaya devam eder ama canlı katkısı sıfırdır.
    # Kalan eski ağırlıkları 0-100 ölçeğini bozmadan oransal olarak normalize et.
    _active_weight_total = 0.85
    w_trend = (0.50 if is_index else 0.40) / _active_weight_total
    w_mom   = (0.35 if is_index else 0.30) / _active_weight_total
    w_ict   = 0.00
    w_r2    = (0.00 if is_index else 0.15) / _active_weight_total

    pros = []; cons = []

    def format_pt(val):
        return f"+{int(val)}" if val > 0 and val.is_integer() else (f"+{val:.1f}" if val > 0 else f"{val:.1f}")

    # ---------------------------------------------------
    # A. TREND - KADEMELİ CEZA SİSTEMİ
    # ---------------------------------------------------
    s_trend = 0
    if tech:
        close  = tech.get('close_last', 0)
        sma200 = tech.get('sma200', 0)
        sma50  = tech.get('sma50', 0)

        # 1. Ana Trend (SMA200)
        if sma200 > 0:
            if close > sma200:
                uzaklik = ((close - sma200) / sma200) * 100
                if uzaklik <= 15:
                    s_trend += 40
                    pros.append(f"✅ Ana Trend: SMA200'e güvenli mesafede ({format_pt(40 * w_trend)} Puan)")
                elif uzaklik <= 30:
                    s_trend += 30
                    pros.append(f"⚠️ Trend Yukarıda: SMA200'den %{int(uzaklik)} uzaklaştı ({format_pt(30 * w_trend)} Puan)")
                elif uzaklik <= 50:
                    s_trend += 20
                    pros.append(f"⚠️ Trend Çok Primli: Ortalamadan %{int(uzaklik)} koptu ({format_pt(20 * w_trend)} Puan)")
                else:
                    s_trend += 10
                    pros.append(f"🚨 Köpük Riski: Fiyat SMA200'e göre %{int(uzaklik)} şişkin ({format_pt(10 * w_trend)} Puan)")
            else:
                cons.append(f"Ana Trend Negatif: Fiyat SMA200 altında (0 Puan)")

        # 2. Kısa/Orta Vade (SMA50)
        if sma50 > 0:
            if close > sma50:
                s_trend += 40
                pros.append(f"✅ Kısa Vadeli İvme: Fiyat SMA50 üzerinde ({format_pt(40 * w_trend)} Puan)")
            else:
                cons.append(f"Kısa Vade Zayıf: SMA50 altında baskı var (0 Puan)")

        # 3. Minervini Onayı
        if mini_data and mini_data.get('score', 0) > 50:
            s_trend += 20
            pros.append(f"✅ Trend Şablonu: Minervini Kriterleri Sağlanıyor ({format_pt(20 * w_trend)} Puan)")
    else:
        cons.append(f"Teknik Veri Hatası (0 Puan)")

    s_trend = min(s_trend, 100)

    # ---------------------------------------------------
    # B. MOMENTUM
    # ---------------------------------------------------
    sent_raw = sent_data.get('total', 50) if sent_data else 50
    rsi_val  = sent_data.get('raw_rsi', 50) if sent_data else 50

    s_mom = 0
    # Eşik 60 → 50'ye indirildi
    if sent_raw >= 50:
        s_mom += 60
        pros.append(f"✅ Net Para Girişi: Kurumsal duyarlılık pozitif ({format_pt(60 * w_mom)} Puan)")
    elif sent_raw <= 35:
        cons.append(f"Para Çıkışı: Kurumsal duyarlılık zayıf (0 Puan)")
    else:
        s_mom += 25
        pros.append(f"⚖️ Momentum Nötr: Net bir yön yok ({format_pt(25 * w_mom)} Puan)")

    if rsi_val > 55:
        s_mom += 40
        pros.append(f"✅ RSI Güçlü: Alım iştahı yüksek ({format_pt(40 * w_mom)} Puan)")
    elif rsi_val > 45:
        s_mom += 20
        pros.append(f"⚖️ RSI Toparlanıyor: Aşırı satımdan çıkış ({format_pt(20 * w_mom)} Puan)")
    else:
        cons.append(f"RSI Zayıf: Satış baskısı devam ediyor (0 Puan)")

    # ---------------------------------------------------
    # C. ICT — GÖZLEM/KAYIT (CANLI PUAN KATKISI YOK)
    # Bias = piyasa yapısının genel yönü (bullish/bearish).
    # Displacement ve zone tek başına anlamsız; bias olmadan
    # ikisi de güvenilir değil.
    # ---------------------------------------------------
    s_ict = 0
    if ict_data:
        bias = ict_data.get('bias', '')
        if "bullish" in bias:
            s_ict = 100
        elif "bearish" in bias:
            s_ict = 0
        else:
            s_ict = 40
    # ICT ham alt değeri breakdown/kayıt için korunur; ağırlığı sıfır olduğu için
    # pros/cons anlatısına da girmez ve canlı hükmü dolaylı biçimde eğemez.

    # ---------------------------------------------------
    # D. RADAR 2
    # ---------------------------------------------------
    s_r2_norm = (r2_score / 7) * 100
    r2_pt     = s_r2_norm * w_r2

    if not is_index:
        if r2_score >= 4:
            pros.append(f"✅ Formasyon: Radar-2 Setup Onaylandı ({format_pt(r2_pt)} Puan)")
        elif r2_score > 0:
            pros.append(f"⚠️ Zayıf Formasyon: Radar-2 Sinyali Eksik ({format_pt(r2_pt)} Puan)")
        else:
            cons.append(f"Formasyon Yok: Radar-2 temiz (0 Puan)")

    # ---------------------------------------------------
    # FİNAL HESAPLAMA
    # ---------------------------------------------------
    final = (s_trend * w_trend) + (s_mom * w_mom) + (s_ict * w_ict) + (s_r2_norm * w_r2)

    if return_breakdown:
        breakdown = {
            'trend':    {'score': int(s_trend),   'weight': w_trend, 'contribution': round(s_trend * w_trend, 1)},
            'momentum': {'score': int(s_mom),     'weight': w_mom,   'contribution': round(s_mom * w_mom, 1)},
            'ict':      {'score': int(s_ict),     'weight': w_ict,   'contribution': round(s_ict * w_ict, 1)},
            'radar2':   {'score': int(s_r2_norm), 'weight': w_r2,    'contribution': round(s_r2_norm * w_r2, 1)},
        }
        return int(final), pros, cons, breakdown

    return int(final), pros, cons
