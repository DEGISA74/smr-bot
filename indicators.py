# -*- coding: utf-8 -*-
"""
indicators.py — SAF HESAP FONKSİYONLARI (teknik indikatörler + hacim profili)
=============================================================================
app.py bölme projesi Adım 4 (4 Tem 2026) — 25 saf fonksiyon app.py'den BİREBİR
taşındı (davranış değişikliği YOK). Hiçbiri Streamlit/DB/ağ kullanmaz; girdi
DataFrame, çıktı değer/dict — hepsi golden_record.py fotoğraf kapsamında.

Aileler: CMF/MFI/Force Index/UDVR/Relative-OBV (çift pencere) · HARSI · LazyBear
sıkışma · SuperTrend/Fibonacci/Z-Score · Hacim profili (POC/full/multi-TF/naked)
· aVWAP · Darvas · klasik mum kalıpları · S/R kümeleri · arz-talep bölgeleri ·
piyasa rejimi · 52H güç etiketi · spike dominance.
"""
import math
import re
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

def _spike_dom_ratio(today_delta, window_delta):
    """0..1+ döner. Aynı yönde değilse 0, pencere ~0 ise 0. >0.6 = dominant."""
    try:
        td = float(today_delta); wd = float(window_delta)
        if abs(wd) < 1e-9: return 0.0
        if (td > 0) != (wd > 0): return 0.0
        r = abs(td) / abs(wd)
        return r if r == r else 0.0  # NaN guard
    except Exception:
        return 0.0


def calculate_harsi(df, period=14):
    """
    Heikin Ashi RSI (HARSI) Hesaplayıcı - NaN HATASI GİDERİLMİŞ VERSİYON
    """
    try:
        # 1. Standart RSI Hesapla
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        
        # 🚨 KORUMA 1: Sıfıra bölünme (ZeroDivision) hatasını önlemek için
        loss = loss.replace(0, 0.00001) 
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        # 🚨 KORUMA 2: İlk 14 günün NaN değerlerini döngüye sokmadan önce ÇÖPE AT
        # Böylece zehirli veri Heikin Ashi döngüsünü bozamaz.
        rsi = rsi.dropna()
        
        # Eğer NaN'lar atıldıktan sonra geriye hesap yapacak veri kalmadıysa çık
        if len(rsi) < 2:
            return None
        
        # 2. Heikin Ashi Dönüşümü (İteratif hesaplama)
        ha_open_vals = np.zeros(len(rsi))
        ha_close_vals = np.zeros(len(rsi))
        
        for i in range(len(rsi)):
            if i == 0:
                ha_open_vals[i] = rsi.iloc[i]
                ha_close_vals[i] = rsi.iloc[i]
            else:
                ha_open_vals[i] = (ha_open_vals[i-1] + ha_close_vals[i-1]) / 2
                ha_close_vals[i] = (rsi.iloc[i] + ha_open_vals[i] + 
                                    max(rsi.iloc[i], ha_open_vals[i]) + 
                                    min(rsi.iloc[i], ha_open_vals[i])) / 4
        
        last_ha_open = ha_open_vals[-1]
        last_ha_close = ha_close_vals[-1]
        prev_ha_close = ha_close_vals[-2]
        
        # Renk ve Durum Belirle
        is_green = last_ha_close > last_ha_open
        color = "#16a34a" if is_green else "#f87171"
        trend_status = "BOĞA MOMENTUMU" if is_green else "AYI MOMENTUMU"
        
        return {
            "ha_open": last_ha_open,
            "ha_close": last_ha_close,
            "is_green": is_green,
            "color": color,
            "status": trend_status,
            "change": last_ha_close > prev_ha_close
        }
    except Exception as e:
        return None


def check_lazybear_squeeze_breakout(df):
    """
    Hem BUGÜNÜ hem DÜNÜ kontrol eder.
    Dönüş: (is_squeeze_now, is_squeeze_yesterday)
    """
    try:
        if df.empty or len(df) < 22: return False, False

        close = df['Close']
        high = df['High']
        low = df['Low']

        # 1. Bollinger Bantları (20, 2.0)
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_upper = sma20 + (2.0 * std20)
        bb_lower = sma20 - (2.0 * std20)

        # 2. Keltner Kanalları (20, 1.5 ATR)
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr20 = tr.rolling(20).mean()
        
        kc_upper = sma20 + (1.5 * atr20)
        kc_lower = sma20 - (1.5 * atr20)

        # 3. Kontrol (Son 2 gün)
        def is_sq(idx):
            return (bb_upper.iloc[idx] < kc_upper.iloc[idx]) and \
                   (bb_lower.iloc[idx] > kc_lower.iloc[idx])

        # -1: Bugün, -2: Dün
        sq_now = is_sq(-1)
        sq_prev = is_sq(-2)

        return sq_now, sq_prev

    except Exception:
        return False, False


def check_lazybear_squeeze(df):
    """
    LazyBear Squeeze Momentum Logic:
    Squeeze = Bollinger Bantları, Keltner Kanalının İÇİNDE mi?
    """
    try:
        if df.empty or len(df) < 20: return False, 0.0

        close = df['Close']
        high = df['High']
        low = df['Low']

        # 1. Bollinger Bantları (20, 2.0)
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_upper = sma20 + (2.0 * std20)
        bb_lower = sma20 - (2.0 * std20)

        # 2. Keltner Kanalları (20, 1.5 ATR)
        # TR Hesaplama
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr20 = tr.rolling(20).mean()
        
        kc_upper = sma20 + (1.5 * atr20)
        kc_lower = sma20 - (1.5 * atr20)

        # 3. Squeeze Kontrolü (Son Gün İçin)
        # BB Üst, KC Üst'ten KÜÇÜK VE BB Alt, KC Alt'tan BÜYÜK olmalı (İçinde olmalı)
        last_bb_u = float(bb_upper.iloc[-1])
        last_bb_l = float(bb_lower.iloc[-1])
        last_kc_u = float(kc_upper.iloc[-1])
        last_kc_l = float(kc_lower.iloc[-1])

        is_squeeze_on = (last_bb_u < last_kc_u) and (last_bb_l > last_kc_l)

        return is_squeeze_on

    except Exception:
        return False


def compute_mfi(df, period=14):
    """Money Flow Index — Wyckoff/VSA'nın hacim-ağırlıklı RSI versiyonu.

    RSI sadece fiyat değişimi gücüne bakar; MFI bu gücün arkasında HACİM var mı
    sorusunu cevaplar. Smart money divergence'larında RSI'den daha güvenilir.

    Formül:
      Typical Price = (H + L + C) / 3
      Raw MF = TP × Volume
      Pos MF = TP up day'lerinin TP×V toplamı
      Neg MF = TP down day'lerinin TP×V toplamı
      MFI = 100 - 100/(1 + Pos/Neg)

    Eşikler: MFI ≥ 80 aşırı alım (hacim teyitli) · MFI ≤ 20 aşırı satım (hacim teyitli).
    20-80 arası nötr — RSI'nin 30-70 standardından daha geniş çünkü hacim teyit
    aradığı için daha az ama daha güçlü extreme verir.

    Args:
        df: 'High','Low','Close','Volume' içeren DataFrame.
        period: 14 klasik, 5 erken-uyarı (dual window).
    Returns:
        pandas.Series of MFI values, period-1 NaN.
    """
    try:
        _tp = (df['High'] + df['Low'] + df['Close']) / 3.0
        _rmf = _tp * df['Volume']
        _tp_diff = _tp.diff()
        _pos_mf = _rmf.where(_tp_diff > 0, 0.0)
        _neg_mf = _rmf.where(_tp_diff < 0, 0.0)
        _pos_sum = _pos_mf.rolling(period, min_periods=max(2, period // 2)).sum()
        _neg_sum = _neg_mf.rolling(period, min_periods=max(2, period // 2)).sum()
        _mfr = _pos_sum / _neg_sum.replace(0, 1e-9)
        return 100.0 - (100.0 / (1.0 + _mfr))
    except Exception:
        return pd.Series([], dtype=float)


def compute_relative_obv_state(df_stock, df_bench, lookback=20):
    """Hisse OBV trendini endeks OBV trendiyle karşılaştırır.

    Mansfield RS sadece fiyat karşılaştırması yapar; bu fonksiyon hacim akışı
    karşılaştırması yapar — daha derin smart money izi.

    Yöntem:
      1. Her iki sembol için OBV serisi hesapla (np.sign(diff) * Volume).cumsum()
      2. Son `lookback` bar için linear slope (polyfit)
      3. Normalize slopes (her birinin son değerine göre %)
      4. Hisse_slope - Endeks_slope = göreli ivme

    5 state döner — CMF dual kalıbıyla aynı:
      - outperform_strong  : hisse↑↑ endeks↓ — gerçek ayrışma (smart money seçti)
      - outperform_mild    : hisse↑ endeks≈ — endeksten iyi
      - inline             : ikisi de benzer yönde — sıradan
      - underperform_mild  : hisse≈ endeks↑ — endeks sürüklüyor, hisse takip etmiyor
      - underperform_strong: hisse↓↓ endeks↑ — yapısal çıkış

    Args:
        df_stock: 'Close','Volume' içeren DataFrame.
        df_bench: aynı format (XU100.IS veya ^GSPC).
        lookback: slope hesaplanacak gün penceresi (default 20).
    Returns:
        dict {state, stock_slope_pct, bench_slope_pct, diff_pct} veya None.
    """
    try:
        import numpy as _np
        if df_stock is None or df_bench is None: return None
        if len(df_stock) < lookback or len(df_bench) < lookback: return None
        # Ortak tarihler
        _common = df_stock.index.intersection(df_bench.index)
        if len(_common) < lookback: return None
        _s = df_stock.loc[_common].tail(lookback)
        _b = df_bench.loc[_common].tail(lookback)
        if 'Volume' not in _s.columns or 'Volume' not in _b.columns: return None

        # OBV serileri
        _s_obv = (_np.sign(_s['Close'].diff()) * _s['Volume']).fillna(0).cumsum().values.astype(float)
        _b_obv = (_np.sign(_b['Close'].diff()) * _b['Volume']).fillna(0).cumsum().values.astype(float)
        if len(_s_obv) < lookback or len(_b_obv) < lookback: return None

        # Linear slope (her birinin son değerine % normalize)
        _x = _np.arange(lookback, dtype=float)
        _s_slope_raw = _np.polyfit(_x, _s_obv[-lookback:], 1)[0]
        _b_slope_raw = _np.polyfit(_x, _b_obv[-lookback:], 1)[0]
        # Normalize: slope'u (OBV-birimi/gün) ortalama günlük HACME böl → kararlı ölçek.
        # (12 Haz fix) Eski normalizör abs(OBV[-1]) idi; OBV cumsum sıfır etrafında
        # salınınca payda ~0 olup yüzde patlıyordu. Benchmark (XU100) tüm hisselerde
        # ortak olduğundan bu instabilite herkese yayılıp %57'sini 'outperform_strong'
        # gösteriyordu. Ortalama hacim sabit/anlamlı bir ölçek (boyutsuz birikim hızı).
        _s_vol_scale = float(_s['Volume'].tail(lookback).mean())
        _b_vol_scale = float(_b['Volume'].tail(lookback).mean())
        _s_vol_scale = _s_vol_scale if _s_vol_scale > 1e-6 else 1.0
        _b_vol_scale = _b_vol_scale if _b_vol_scale > 1e-6 else 1.0
        _s_slope_pct = (_s_slope_raw / _s_vol_scale) * 100
        _b_slope_pct = (_b_slope_raw / _b_vol_scale) * 100
        _diff = _s_slope_pct - _b_slope_pct

        # State (eşikler 5g/20g CMF/MFI dual pattern ile uyumlu)
        # 'strong' = ikisi zıt yönde + diff>1.5
        # 'mild' = aynı yönde ama hisse fark açık
        if _s_slope_pct > 0.3 and _b_slope_pct < -0.3 and _diff > 1.5:
            _state = 'outperform_strong'
        elif _s_slope_pct < -0.3 and _b_slope_pct > 0.3 and _diff < -1.5:
            _state = 'underperform_strong'
        elif _diff > 0.8:
            _state = 'outperform_mild'
        elif _diff < -0.8:
            _state = 'underperform_mild'
        else:
            _state = 'inline'

        return {
            'state': _state,
            'stock_slope_pct': round(_s_slope_pct, 2),
            'bench_slope_pct': round(_b_slope_pct, 2),
            'diff_pct': round(_diff, 2),
        }
    except Exception:
        return None


def compute_force_index_dual(df, span_short=2, span_long=13):
    """Alexander Elder's Force Index — dual-window state machine + divergence.

    Klasik formül:  FI = (Close - Close[-1]) × Volume
    Smoothed: EMA(span)

    Iki pencere:
      - span=2  (kısa) — anlık güç sinyali (Elder 'trigger' katmanı)
      - span=13 (uzun) — sürekli güç (Elder 'trend' katmanı)

    7 state (CMF/MFI dual pattern ile aynı kalıp):
      strong_pos    : ikisi de güçlü pozitif (alıcı tam egemen)
      strong_neg    : ikisi de güçlü negatif (satıcı tam egemen)
      turning_up    : trend negatif ama anlık dönüş — erken dönüş sinyali
      turning_down  : trend pozitif ama anlık zayıflık — momentum yorgunluğu
      pos           : sadece trend pozitif
      neg           : sadece trend negatif
      neutral       : ikisi de denge bölgesinde

    Divergence detection (Elder klasik):
      - Bullish divergence: Fiyat LL + FI(13) HL → alıcı dönüşü sinyali
      - Bearish divergence: Fiyat HH + FI(13) LH → satıcı dönüşü sinyali

    Args:
        df: 'Close','Volume' içeren DataFrame.
        span_short / span_long: EMA pencereleri.
    Returns:
        {
          'state': 7 state,
          'fi_short': son kısa FI değeri,
          'fi_long': son uzun FI değeri,
          'fi_short_norm': stdev normalize edilmiş kısa FI (z-score)
          'fi_long_norm': stdev normalize edilmiş uzun FI (z-score)
          'divergence': None|'bullish'|'bearish'
        }
    """
    try:
        if df is None or len(df) < max(span_long * 3, 50): return None
        if 'Close' not in df.columns or 'Volume' not in df.columns: return None

        _close = df['Close']
        _vol = df['Volume']
        _delta = _close.diff()
        _fi_raw = _delta * _vol

        _fi_short = _fi_raw.ewm(span=span_short, adjust=False).mean()
        _fi_long = _fi_raw.ewm(span=span_long, adjust=False).mean()

        # Z-score normalize — uzun pencere std'si baz alınır
        _fi_std = _fi_long.rolling(50, min_periods=20).std()
        _fi_std_last = float(_fi_std.iloc[-1]) if not pd.isna(_fi_std.iloc[-1]) and _fi_std.iloc[-1] > 0 else 1.0

        _fis = float(_fi_short.iloc[-1])
        _fil = float(_fi_long.iloc[-1])
        _fis_norm = _fis / _fi_std_last
        _fil_norm = _fil / _fi_std_last

        # State (z-score eşikleri: ±0.5 = orta, ±1.0 = güçlü)
        if   _fis_norm > 1.0 and _fil_norm > 0.5:  _state = 'strong_pos'
        elif _fis_norm < -1.0 and _fil_norm < -0.5: _state = 'strong_neg'
        elif _fis_norm > 0.5 and _fil_norm < -0.3:  _state = 'turning_up'    # trend neg + anlık dönüş
        elif _fis_norm < -0.5 and _fil_norm > 0.3:  _state = 'turning_down'  # trend poz + anlık zayıflık
        elif _fil_norm > 0.5:                        _state = 'pos'
        elif _fil_norm < -0.5:                       _state = 'neg'
        else:                                        _state = 'neutral'

        # Divergence — son 30 bar pivot bazlı (basit)
        _divergence = None
        try:
            _lookback = 30
            if len(df) >= _lookback + 5:
                _seg_close = _close.tail(_lookback)
                _seg_fi = _fi_long.tail(_lookback)
                # En düşük 2 close + en yüksek 2 close ara
                _close_low_idx = _seg_close.values.argmin()
                _close_high_idx = _seg_close.values.argmax()
                # Pivot tespiti basit: son 5 bar içinde extremde mi?
                _last_close = float(_seg_close.iloc[-1])
                _last_fi = float(_seg_fi.iloc[-1])
                _seg_close_vals = _seg_close.values
                _seg_fi_vals = _seg_fi.values
                # Önceki düşük dip (ilk 20 barda)
                _prev_low_idx = _seg_close_vals[:20].argmin()
                _prev_low_close = _seg_close_vals[_prev_low_idx]
                _prev_low_fi = _seg_fi_vals[_prev_low_idx]
                # Önceki yüksek tepe (ilk 20 barda)
                _prev_high_idx = _seg_close_vals[:20].argmax()
                _prev_high_close = _seg_close_vals[_prev_high_idx]
                _prev_high_fi = _seg_fi_vals[_prev_high_idx]
                # Son 5 bar içinde yeni LL mı?
                _recent_close_min = _seg_close.iloc[-5:].min()
                _recent_close_max = _seg_close.iloc[-5:].max()
                _recent_fi_min = _seg_fi.iloc[-5:].min()
                _recent_fi_max = _seg_fi.iloc[-5:].max()
                if (_recent_close_min < _prev_low_close * 0.99
                    and _recent_fi_min > _prev_low_fi * 1.05):
                    _divergence = 'bullish'   # Fiyat LL + FI HL
                elif (_recent_close_max > _prev_high_close * 1.01
                      and _recent_fi_max < _prev_high_fi * 0.95):
                    _divergence = 'bearish'   # Fiyat HH + FI LH
        except Exception:
            pass

        return {
            'state': _state,
            'fi_short': _fis,
            'fi_long': _fil,
            'fi_short_norm': round(_fis_norm, 2),
            'fi_long_norm': round(_fil_norm, 2),
            'divergence': _divergence,
        }
    except Exception:
        return None


def compute_updown_volume_ratio(df, period=20):
    """Up/Down Volume Ratio — Wyckoff Effort-vs-Result'un en sade hali.

    'Yükseliş günlerinin hacmi düşüş günlerinin hacminden ne kadar fazla?'
    OBV cumulative pattern eski hareketler kirletebilir; CMF bar-içi kapanış
    konumuna bakar. UDVR ham gerçek: son N gün net hacim yönü.

    Klasik eşikler (sektör standardı):
      ≥ 2.0  : strong_buyer   — alıcı net hakim
      1.3-2.0: buyer          — alıcı egemen, sağlıklı yukarı baskı
      0.77-1.3: balanced      — denge, yön kararsız
      0.5-0.77: seller         — satıcı egemen
      < 0.5  : strong_seller  — satıcı net hakim

    Wyckoff Climax tespiti:
      Fiyat yeni high yapıyor + UDVR < 0.8 → climax_top
      Fiyat yeni low yapıyor + UDVR > 1.25 → climax_bottom
      (Hacim Smart Money çekiliyor/giriyor)

    Args:
        df: 'Close','Volume' içeren DataFrame.
        period: pencere (5g tactical, 20g orta, 50g uzun).
    Returns:
        {
          'ratio': UDVR,
          'state': 5 state,
          'up_vol': toplam yükseliş hacmi,
          'down_vol': toplam düşüş hacmi,
          'climax': None|'climax_top'|'climax_bottom'
        }
    """
    try:
        if df is None or len(df) < period + 1: return None
        if 'Close' not in df.columns or 'Volume' not in df.columns: return None

        _seg = df.tail(period + 1)
        _close = _seg['Close']
        _vol = _seg['Volume']
        _diff = _close.diff()

        # period bar penceresi (ilk bar diff için kullanılır)
        _up_mask = _diff > 0
        _down_mask = _diff < 0
        _up_vol = float(_vol.where(_up_mask, 0).iloc[1:].sum())
        _down_vol = float(_vol.where(_down_mask, 0).iloc[1:].sum())

        if _down_vol <= 1e-6:
            _ratio = 99.0 if _up_vol > 0 else 1.0
        else:
            _ratio = _up_vol / _down_vol

        # State sınıflandırma
        if   _ratio >= 2.0:  _state = 'strong_buyer'
        elif _ratio >= 1.3:  _state = 'buyer'
        elif _ratio >= 0.77: _state = 'balanced'
        elif _ratio >= 0.5:  _state = 'seller'
        else:                _state = 'strong_seller'

        # Climax detection — fiyat extremde ama hacim yönü tersi
        _climax = None
        try:
            _high_full = df['High'].tail(period * 2) if 'High' in df.columns else df['Close'].tail(period * 2)
            _low_full = df['Low'].tail(period * 2) if 'Low' in df.columns else df['Close'].tail(period * 2)
            _cur_close = float(_close.iloc[-1])
            _high_max = float(_high_full.max())
            _low_min = float(_low_full.min())
            # Fiyat son 2 pencerelik high'a ≥%98 yakın + UDVR zayıf → tepe climax
            if _cur_close >= _high_max * 0.98 and _ratio < 0.8:
                _climax = 'climax_top'
            elif _cur_close <= _low_min * 1.02 and _ratio > 1.25:
                _climax = 'climax_bottom'
        except Exception: pass

        return {
            'ratio': round(_ratio, 2),
            'state': _state,
            'up_vol': _up_vol,
            'down_vol': _down_vol,
            'climax': _climax,
        }
    except Exception:
        return None


def compute_cmf(df, period=20, vol_series=None):
    """Para Akışı (varsayılan 20g) — OBV teyit katmanı için tek kaynak.
    df: 'High','Low','Close' içermeli. vol_series verilmezse df['Volume'] kullanılır.
    Money Flow Multiplier × Volume → period toplamı / hacim toplamı.
    Hata / sıfır hacimde 0.0 döner."""
    try:
        _vol = df['Volume'] if vol_series is None else vol_series
        _hl_range = df['High'] - df['Low']
        _mfm = np.where(
            _hl_range > 0,
            ((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / _hl_range,
            0.0
        )
        _mfv = pd.Series(_mfm, index=df.index) * _vol
        _vol_sum = float(_vol.tail(period).sum())
        return float(_mfv.tail(period).sum() / _vol_sum) if _vol_sum > 0 else 0.0
    except Exception:
        return 0.0


def find_smart_sr_levels(df, window=5, cluster_tolerance=0.015, min_touches=3, recency_limit=120):
    """
    Fitilleri (iğneleri) büyük oranda görmezden gelerek, mum gövdelerinin (kapanış/açılış)
    yığıldığı en güçlü ve taze destek/direnç bölgelerini bulur.
    """
    data = df.copy()
    
    # --- YENİ EKLENEN KISIM: İĞNELERİ KESİP GÖVDELERİ ALIYORUZ ---
    # Her mumun gövdesinin üst sınırını (Body Top) ve alt sınırını (Body Bottom) hesapla
    data['Body_Top'] = data[['Open', 'Close']].max(axis=1)
    data['Body_Bottom'] = data[['Open', 'Close']].min(axis=1)
    
    # Zirveleri ve dipleri High/Low yerine bu GÖVDELER üzerinden bul!
    data['Swing_High'] = data['Body_Top'][(data['Body_Top'] == data['Body_Top'].rolling(window=window*2+1, center=True).max())]
    data['Swing_Low'] = data['Body_Bottom'][(data['Body_Bottom'] == data['Body_Bottom'].rolling(window=window*2+1, center=True).min())]
    
    # Bundan sonraki kısımlar aynı kalıyor...
    highs = data['Swing_High'].dropna()
    lows = data['Swing_Low'].dropna()
    
    pivots_series = pd.concat([highs, lows]).sort_values() 
    pivots = list(zip(pivots_series.values, pivots_series.index))
    
    valid_levels = []
    total_bars = len(data)
    
    while len(pivots) > 0:
        base_price, base_idx = pivots.pop(0)
        
        cluster_prices = [base_price]
        cluster_indices = [base_idx]
        
        i = 0
        while i < len(pivots):
            compare_price, compare_idx = pivots[i]
            if abs(compare_price - base_price) / base_price <= cluster_tolerance:
                cluster_prices.append(compare_price)
                cluster_indices.append(compare_idx)
                pivots.pop(i)
            else:
                i += 1
                
        # Güç ve Tazelik (Recency) Kontrolü
        if len(cluster_prices) >= min_touches:
            last_touch_idx = max(cluster_indices)
            bars_since_last_touch = total_bars - data.index.get_loc(last_touch_idx)
            
            if bars_since_last_touch <= recency_limit:
                # Çizgiyi bu gövde yığılmasının tam ortasından (ortalamasından) geçir
                average_level = sum(cluster_prices) / len(cluster_prices)
                valid_levels.append(round(average_level, 2))
                
    return list(set(valid_levels))


def detect_darvas_box(df):
    """
    Swing-point bazlı Darvas kutu tespiti.
    2 sağ + 2 sol komşudan büyük pivot high → kutu tavanı.
    Kalite skoru: genişlik(25) + yaş(25) + hacim kontraksiyon(25) + pozisyon(25).
    Returns dict or None.
    """
    try:
        if df is None or len(df) < 60:
            return None
        close = df['Close']
        high  = df['High']
        low   = df['Low']
        vol   = df['Volume']
        cp    = float(close.iloc[-1])

        # Son 60 bar içinde pivot high bul (2 sol, 2 sağ komşudan büyük)
        n     = min(60, len(df))
        h_arr = high.iloc[-n:].values
        pivot_highs = []
        for _i in range(2, len(h_arr) - 2):
            if (h_arr[_i] > h_arr[_i-1] and h_arr[_i] > h_arr[_i-2] and
                    h_arr[_i] > h_arr[_i+1] and h_arr[_i] > h_arr[_i+2]):
                pivot_highs.append((_i, h_arr[_i]))

        if not pivot_highs:
            return None

        # Son pivot high → kutu tavanı
        last_ph_idx, box_top_val = pivot_highs[-1]
        bars_since = (len(h_arr) - 1) - last_ph_idx  # pivot'tan bu yana kaç bar

        if bars_since < 5:   # Kutu henüz oluşmadı
            return None

        # Kutu içi slice
        box_low_s = low.iloc[-bars_since:]
        box_vol_s = vol.iloc[-bars_since:]

        box_top    = float(box_top_val)
        box_bottom = float(box_low_s.min())
        box_age    = bars_since

        # Kutu tabanı kırıldıysa geçersiz kutu
        if cp < box_bottom * 0.97:
            return None

        # Durum
        status = 'breakout' if cp > box_top * 1.01 else 'forming'

        # ── Kalite Skoru (0–100) ──────────────────────────────
        quality = 0

        # 1. Kutu genişliği (dar = sıkışmış enerji = iyi)
        bw = (box_top - box_bottom) / box_bottom if box_bottom > 0 else 1.0
        if   bw < 0.05: quality += 25
        elif bw < 0.08: quality += 20
        elif bw < 0.12: quality += 10

        # 2. Kutu yaşı (uzun konsolidasyon = büyük kırılım potansiyeli)
        if   box_age >= 15: quality += 25
        elif box_age >= 10: quality += 20
        elif box_age >= 7:  quality += 12
        elif box_age >= 5:  quality += 5

        # 3. Hacim kontraksiyon (VCP imzası — gizli birikim)
        vol_sma20   = float(vol.rolling(20).mean().iloc[-1])
        avg_box_vol = float(box_vol_s.mean()) if len(box_vol_s) > 0 else vol_sma20
        if vol_sma20 > 0:
            vr = avg_box_vol / vol_sma20
            if   vr < 0.70: quality += 25
            elif vr < 0.85: quality += 18
            elif vr < 0.95: quality += 10

        # 4. Pozisyon kalitesi (SMA200 üstü + SMA50 eğimi + RSI sağlıklı)
        sma50  = float(close.rolling(50).mean().iloc[-1])
        sma200 = float(close.rolling(200).mean().iloc[-1])
        _d     = close.diff()
        _g     = _d.where(_d > 0, 0).rolling(14).mean()
        _l     = (-_d.where(_d < 0, 0)).rolling(14).mean()
        _lv    = float(_l.iloc[-1])
        rsi_v  = float((100 - 100 / (1 + _g / _l)).iloc[-1]) if _lv != 0 else 50.0
        if not np.isnan(sma200) and cp > sma200:    quality += 8
        if not np.isnan(sma50)  and sma50 > sma200: quality += 8
        if 40 <= rsi_v <= 65:                        quality += 9
        quality = min(100, quality)

        # ── Kırılım kalitesi (3 kapı) ─────────────────────────
        breakout_class = None
        if status == 'breakout':
            gate1 = cp > box_top
            gate2 = float(vol.iloc[-1]) > vol_sma20 * 1.5
            gate3 = 45 <= rsi_v <= 73
            gates = sum([gate1, gate2, gate3])
            if   gates == 3: breakout_class = 'A'
            elif gates >= 2: breakout_class = 'B'

        return {
            'box_top':        round(box_top, 2),
            'box_bottom':     round(box_bottom, 2),
            'box_age':        box_age,
            'quality':        quality,
            'status':         status,
            'breakout_class': breakout_class,
            'vol_ratio':      round(avg_box_vol / vol_sma20, 2) if vol_sma20 > 0 else 1.0,
        }
    except:
        return None


def calculate_volume_delta(df):
    """Mumun kapanışına göre tahmini Hacim Deltası hesaplar."""
    df = df.copy()
    df['Range'] = df['High'] - df['Low']
    df['Range'] = df['Range'].replace(0, 0.0001) # Sıfıra bölünme hatasını önle
    
    df['Buying_Pressure'] = (df['Close'] - df['Low']) / df['Range']
    df['Selling_Pressure'] = (df['High'] - df['Close']) / df['Range']
    
    df['Buying_Volume'] = df['Volume'] * df['Buying_Pressure']
    df['Selling_Volume'] = df['Volume'] * df['Selling_Pressure']
    
    # Günlük net hacim farkı (Alıcılar - Satıcılar)
    df['Volume_Delta'] = df['Buying_Volume'] - df['Selling_Volume']
    return df


def compute_flow_persistence(df, window=20):
    """Son `window` işlem gününde pozitif hacim deltalı gün sayısı (10 Tem 2026).

    Kurumsal birikim tek günlük olay değil kampanyadır — istikrar sayacı
    tek günlük gürültüyü haftalarca süren akıştan ayırır.
    Returns: (pos_days, n) — veri yetersizse (None, 0).
    """
    try:
        if df is None or len(df) < window + 1:
            return (None, 0)
        d = calculate_volume_delta(df)
        tail = d['Volume_Delta'].iloc[-window:]
        return (int((tail > 0).sum()), int(len(tail)))
    except Exception:
        return (None, 0)


def compute_panel_consensus_history(df, days=10):
    """Smart Money panel konsensüs skorunun son `days` günlük YAKLAŞIK geçmişi (10 Tem 2026).

    panel_verdict_backtest.py ile aynı bileşen yaklaşıklaması (OBV'de CMF teyidi yok).
    Returns: eskiden yeniye list[{'score': int|None, 'label': str|None}]; veri yetersizse [].
    """
    try:
        n = len(df) if df is not None else 0
        if n < 45 + days:
            return []
        d = calculate_volume_delta(df)
        c = d['Close'].astype(float)
        v = d['Volume'].astype(float)
        vd = d['Volume_Delta'].astype(float)
        obv = compute_obv_series(df)
        vma20 = v.rolling(20).mean()
        vd5 = vd.rolling(5).sum()
        out = []
        for i in range(n - days, n):
            try:
                vp = calculate_full_volume_profile(df.iloc[:i + 1], lookback=20)
                cp = float(c.iloc[i])
                s_va = 1 if cp > vp['vah'] else (-1 if cp < vp['val'] else 0)
                s_d1 = 1 if vd.iloc[i] > 0 else (-1 if vd.iloc[i] < 0 else 0)
                s_5g = 1 if vd5.iloc[i] > 0 else (-1 if vd5.iloc[i] < 0 else 0)
                vma_i = float(vma20.iloc[i])
                rvol = float(v.iloc[i]) / vma_i if vma_i > 0 else 0.0
                s_rv = 1 if rvol >= 2.0 else (-1 if 0 < rvol < 0.8 else 0)
                o_now = float(obv.iloc[i]); o5 = float(obv.iloc[i - 5]); o14 = float(obv.iloc[i - 14])
                s_obv = 1 if (o_now > o5 and o_now > o14) else (-1 if (o_now <= o5 and o_now <= o14) else 0)
                sigs = [s_va, s_d1, s_rv, s_5g, s_obv]
                pos = sum(1 for s in sigs if s > 0)
                neg = sum(1 for s in sigs if s < 0)
                scr = max(0, min(100, pos * 20 - neg * 20 + 50))
                if scr >= 80:   lbl = "ALIM BASKISI"
                elif scr >= 62: lbl = "HAFİF ALIM"
                elif scr > 38:  lbl = "NÖTR"
                elif scr >= 20: lbl = "HAFİF SATIŞ"
                else:           lbl = "SATIŞ BASKISI"
                out.append({'score': scr, 'label': lbl})
            except Exception:
                out.append({'score': None, 'label': None})
        return out
    except Exception:
        return []


def find_volume_gap_levels(df, lookback=60, bins=20):
    """60g hacim profilinden LVN (ince/boş bölge) seviyeleri (10 Tem 2026).

    LVN = hacmi profil ortalamasının 0.3x altında kalan fiyat dilimi — orada
    sahiplenilmiş pozisyon az, fiyat girerse hızlı geçer (destek/direnç tutmaz).
    Returns: {'above': (level, dist_pct) | None, 'below': (level, dist_pct) | None,
              'levels': [tüm LVN seviyeleri]}
    """
    out = {'above': None, 'below': None, 'levels': []}
    try:
        if df is None or len(df) < 30:
            return out
        vdf = df.tail(min(lookback, len(df)))
        lo = float(vdf['Low'].min()); hi = float(vdf['High'].max())
        if hi <= lo:
            return out
        edges = np.linspace(lo, hi, bins + 1)
        prof = np.zeros(bins)
        for _, r in vdf.iterrows():
            rh, rl, rv = float(r['High']), float(r['Low']), float(r['Volume'])
            rng = rh - rl
            if rng <= 0:
                idx = min(max(np.digitize((rh + rl) / 2, edges) - 1, 0), bins - 1)
                prof[idx] += rv
                continue
            for i in range(bins):
                ov = min(rh, edges[i + 1]) - max(rl, edges[i])
                if ov > 0:
                    prof[i] += rv * (ov / rng)
        avg = float(prof.mean())
        if avg <= 0:
            return out
        centers = [(edges[i] + edges[i + 1]) / 2 for i in range(bins)]
        lvn = sorted(round(centers[i], 2) for i in range(bins) if 0 < prof[i] <= avg * 0.3)
        out['levels'] = lvn
        cp = float(df['Close'].iloc[-1])
        above = [x for x in lvn if x > cp]
        below = [x for x in lvn if x < cp]
        if above:
            a = min(above)
            out['above'] = (a, round((a - cp) / cp * 100, 1))
        if below:
            b = max(below)
            out['below'] = (b, round((b - cp) / cp * 100, 1))
    except Exception:
        pass
    return out


def relative_strength_ratio(stk_close, idx_close, window=10):
    """Hisse ↔ endeks göreli güç (RS) — TEK KAYNAK (7 Ağu 2026).

    w-günlük getiri oranı: (S[-1]/S[-w-1]) / (I[-1]/I[-w-1]).
    app.py FİYAT kartı + infografik statbox ikisi de BUNU çağırır → pencere
    bir daha sessizce ayrışamaz (eskiden biri 20g biri 126g idi). Hesap/AI'a
    giden veri DEĞİL, yalnız iki render bu ortak fonksiyondan okur.
    Dönüş: float oran veya None (veri yetersiz / geçersiz).
    """
    try:
        import numpy as _np
        s = _np.asarray(stk_close, dtype=float)
        i = _np.asarray(idx_close, dtype=float)
        if len(s) < window + 1 or len(i) < window + 1:
            return None
        _s0, _i0 = s[-window - 1], i[-window - 1]
        if _s0 == 0 or _i0 == 0:
            return None
        _ir = i[-1] / _i0
        if _ir == 0:
            return None
        _rs = (s[-1] / _s0) / _ir
        if _rs != _rs:   # NaN guard
            return None
        return float(_rs)
    except Exception:
        return None


def compute_obv_series(df):
    """OBV serisi — get_obv_divergence_status ile aynı formül (TEK KAYNAK, 10 Tem 2026).

    Kapanış yönü işareti × hacim, kümülatif toplam.
    """
    change = df['Close'].diff()
    direction = np.sign(change).fillna(0)
    return (direction * df['Volume']).cumsum()


def intraday_obv_delta(ticker, prev_close, for_date=None):
    """Seans İÇİNDE bugünün OBV katkısını SAATLİK barlardan hesaplar (14 Ağu 2026).

    NEDEN: Günlük OBV'nin orta vitesi yok — gün yukarı kapanırsa TÜM hacim artı,
    aşağı kapanırsa TÜM hacim eksi. Yatay bir günde (XU100 14 Ağu: %-0,00) bu,
    114 milyar TL'yi yazı-turayla bir tarafa atıyor; fiyat dünkü kapanışın iki
    yanında gezindikçe panel hükmü gün içinde 17 puan zıplıyor.
    ÖLÇÜLDÜ: 8 hissenin 3'ünde (AKBNK/SISE/BIMAS — hepsi az hareketli gün)
    günlük kuralın YÖNÜ saatlik akışın tersi çıktı. AKBNK günlük -72mn, saatlik +3mn.

    Bu fonksiyon bugünü saat saat gezer, her barı kendi yönüne göre sayar ve NET
    değeri döner. Saatler birbirini dengelediği için yatay gün yatay sonuç verir.

    ⚠ SINIR: Yalnız bugünün barı için kullanılır; önceki günler günlük OBV'de kalır.
    Net katkı doğası gereği tam-gün hacminden ~3 kat küçüktür, yani bugün geçmişe
    kıyasla daha sessiz görünür. Bilinen ve kabul edilmiş takas (A planı).

    prev_close: dünkü GÜNLÜK kapanış (ilk saatlik bar onunla kıyaslanır).
    for_date  : hangi günün barları isteniyor (date). Verilmezse bugünün tarihi.

    ⚠ 14 Ağu 2026 HATA DÜZELTMESİ: "bugün" önce dosyanın KENDİ son gününden
    okunuyordu. Saatlik depo yalnız ~150 likit hisseyi güncelliyor, gerisi haftalar
    öncesinde takılı → ACSEL'in 28 Temmuz'u "bugün" sanılıp bugünün OBV'sine
    yazılıyordu. Artık takvim tarihiyle kıyaslanır; o güne ait bar yoksa None döner.

    ⚠ 19 Ağu 2026 — KAPI ŞARTI: "o güne ait bar var mı" yetmiyordu. 18 Ağu'da
    depo 13:30'da donmuştu; saat 17:45'te 5 bar duruyordu ve kod bunu bugünün
    tam verisi sayıp yarım günü OBV'ye yazıyordu. Artık `saatlik_kapi` beklenen
    bar sayısını da denetler; eksikse hesap YAPILMAZ. Kapı modülü yoksa da
    hesap yapılmaz — denetimsiz saatlik veri kullanmak yasak.

    Döner: float (TL, işaretli) veya None (saatlik veri kapıdan geçmedi →
    çağıran günlüğe düşer AMA bunu ekranda söylemek zorundadır).
    """
    try:
        from data_layer import INDEX_CIRO_TARGETS, compute_index_tl_ciro_series_hourly
        from saatlik_kapi import saatlik_oku
    except Exception:
        return None
    try:
        _sym = str(ticker).upper().replace(".IS", "")
        if for_date is None:
            from datetime import datetime as _dtc
            for_date = _dtc.now().date()
        _bars, _durum = saatlik_oku(_sym, for_date=for_date)
        if _bars is None or len(_bars) < 2:
            return None            # kapı kapalı (kapsam dışı / yarım / bayat)

        # Hacim kaynağı: endekslerin saatlik Volume'ü SIFIR (adet bile yok) →
        # günlük kardeşiyle aynı yöntem: bileşenlerin Σ(Close×Volume) TL cirosu.
        if _sym in INDEX_CIRO_TARGETS:
            # for_date verilir → toplam yalnız O GÜN kapıdan geçen bileşenlerden
            # kurulur; yarısı bayat bir bileşen listesiyle endeks cirosu şişmez.
            _ciro = compute_index_tl_ciro_series_hourly(
                _sym, allow_network=False, for_date=for_date)
            if _ciro is None or _ciro.empty:
                return None
            _vols = _ciro.reindex(_bars.index)
        else:
            _vols = pd.to_numeric(_bars['Volume'], errors='coerce')
        if _vols is None or _vols.isna().all() or float(_vols.fillna(0).sum()) <= 0:
            return None

        _delta = 0.0
        _prev = float(prev_close)
        for _t, _row in _bars.iterrows():
            _c = float(_row['Close'])
            _v = _vols.get(_t)
            if _v is None or pd.isna(_v):
                _prev = _c
                continue
            _v = float(_v)
            if _c > _prev:
                _delta += _v
            elif _c < _prev:
                _delta -= _v
            _prev = _c
        return _delta
    except Exception:
        return None


def compute_obv_divergence_duration(df, window=14, max_scan=30):
    """Fiyat ile OBV'nin 14g yönleri kaç gündür ayrışıyor (10 Tem 2026).

    Her gün için: fiyat son `window` günde yukarı mı, OBV yukarı mı?
    Zıtlarsa uyumsuzluk günü. Bugünden geriye kesintisiz sayılır.
    Returns: {'kind': 'bearish' | 'bullish' | None, 'days': int}
      bearish = fiyat ↑ OBV ↓ (yükseliş hacimle desteklenmiyor)
      bullish = fiyat ↓ OBV ↑ (düşüşte sessiz birikim)
    """
    out = {'kind': None, 'days': 0}
    try:
        if df is None or len(df) < window + max_scan + 2:
            return out
        obv = compute_obv_series(df)
        c = df['Close'].astype(float)

        def _state(i):
            p_up = float(c.iloc[-1 - i]) > float(c.iloc[-1 - i - window])
            o_up = float(obv.iloc[-1 - i]) > float(obv.iloc[-1 - i - window])
            if p_up and not o_up:
                return 'bearish'
            if (not p_up) and o_up:
                return 'bullish'
            return None

        k0 = _state(0)
        if k0 is None:
            return out
        days = 1
        for i in range(1, max_scan):
            if _state(i) == k0:
                days += 1
            else:
                break
        out = {'kind': k0, 'days': days}
    except Exception:
        pass
    return out


def detect_absorption_days(df, lookback=5, vol_mult=2.0, max_chg_pct=0.5):
    """Absorpsiyon günleri: hacim ≥ vol_mult × 20g ort VE |kapanış değişimi| ≤ max_chg_pct (10 Tem 2026).

    Wyckoff çaba-vs-sonuç: dev hacim dönüyor ama fiyat yerinde sayıyor →
    gelen mal emiliyor. Konum yorumu 20g aralığındaki yere göre:
    alt %25 → 'dip' (toplama emaresi), üst %25 → 'tepe' (dağıtım riski), arası 'orta'.
    Returns: en yeniden eskiye list[dict{days_ago, date, rvol, chg_pct, loc}].
    """
    out = []
    try:
        if df is None or len(df) < 25:
            return out
        v = df['Volume'].astype(float)
        c = df['Close'].astype(float)
        vma  = v.rolling(20).mean()
        hi20 = c.rolling(20).max()
        lo20 = c.rolling(20).min()
        for back in range(1, lookback + 1):
            i = len(df) - back
            if i < 21:
                break
            va = float(vma.iloc[i]); vd = float(v.iloc[i])
            cp = float(c.iloc[i]);   pp = float(c.iloc[i - 1])
            if va <= 0 or vd <= 0 or pp <= 0:
                continue
            rvol_d = vd / va
            chg = (cp - pp) / pp * 100
            if rvol_d >= vol_mult and abs(chg) <= max_chg_pct:
                rng = float(hi20.iloc[i]) - float(lo20.iloc[i])
                pos = (cp - float(lo20.iloc[i])) / rng if rng > 0 else 0.5
                loc = 'dip' if pos <= 0.25 else ('tepe' if pos >= 0.75 else 'orta')
                try:
                    dt = df.index[i].strftime('%d.%m')
                except Exception:
                    dt = ''
                out.append({'days_ago': back, 'date': dt, 'rvol': round(rvol_d, 1),
                            'chg_pct': round(chg, 2), 'loc': loc})
    except Exception:
        pass
    return out


def detect_classic_candle_patterns(df):
    """Son 3 mumda klasik formasyon tespiti.

    Returns: list of dict {name, type, confidence(0-100), context}
    Sadece TRENDLE UYUMLU veya net gövde/gölge oranlı patternler döner.
    Hiçbiri tetiklenmezse [] döner.
    """
    out = []
    if df is None or len(df) < 5:
        return out
    try:
        o = df['Open'].astype(float).values
        h = df['High'].astype(float).values
        l = df['Low'].astype(float).values
        c = df['Close'].astype(float).values

        # Trend bağlamı: son 10g SMA eğimi
        sma10 = pd.Series(c).rolling(10, min_periods=5).mean().values
        if len(sma10) >= 11:
            slope_pct = (sma10[-1] - sma10[-11]) / sma10[-11] * 100 if sma10[-11] > 0 else 0
        else:
            slope_pct = 0
        trend = "up" if slope_pct > 1.5 else ("down" if slope_pct < -1.5 else "flat")

        # 8 Haz 2026 Oturum 19 — STRÜKTÜREL LOKASYON BAĞLAMI
        # Pattern + trend yetmez; "dipten gelen Morning Star" / "tepeden gelen Evening Star"
        # gibi YERLEŞİM önemli. Konsolidasyon ortasındaki hammer = gürültü, dip yakınındaki
        # hammer = anlamlı reversal. Burada 4 lokasyon kriteri hesaplanır:
        #  (a) 52H pozisyon (dip %25 / tepe %25 → ekstrem)
        #  (b) RSI(14) ekstrem (<35 oversold / >65 overbought)
        #  (c) SMA200'den sapma (uzaklık |%|>8 → stretched)
        #  (d) Ardışık aynı renk mum (3+ → tükeniş)
        cur = float(c[-1])
        # 52H pos
        seg52 = df.tail(252) if len(df) >= 60 else df
        h52 = float(seg52['High'].max()); l52 = float(seg52['Low'].min())
        pos52 = (cur - l52) / (h52 - l52) if h52 > l52 else 0.5
        at_bottom_25 = pos52 < 0.25
        at_top_25    = pos52 > 0.75
        # RSI(14)
        try:
            _di = pd.Series(c).diff()
            _ri = 100 - 100 / (1 + (_di.where(_di > 0, 0).rolling(14).mean() / (-_di.where(_di < 0, 0)).rolling(14).mean()))
            rsi14 = float(_ri.iloc[-1])
        except Exception:
            rsi14 = 50
        oversold   = rsi14 < 35
        overbought = rsi14 > 65
        # SMA200 sapma
        try:
            sma200 = float(pd.Series(c).rolling(200, min_periods=50).mean().iloc[-1])
            sma200_dev = (cur - sma200) / sma200 * 100 if sma200 > 0 else 0
        except Exception:
            sma200_dev = 0
        stretched_down = sma200_dev < -8
        stretched_up   = sma200_dev > 8
        # Ardışık mum sayısı (son 5'te)
        consec_red = 0; consec_green = 0
        for k in range(-1, -min(6, len(c)+1), -1):
            if c[k] < o[k]: consec_red  += 1
            elif c[k] > o[k]: consec_green += 1
            else: break
            if (c[k] < o[k] and k != -1 and c[k+1] > o[k+1]) or (c[k] > o[k] and k != -1 and c[k+1] < o[k+1]):
                break

        # Bullish reversal pattern'i anlamlı yapan lokasyonlar
        def _bull_location_score():
            score = 0; reasons = []
            if at_bottom_25:   score += 2; reasons.append(f"52H %{pos52*100:.0f} dipte")
            if oversold:       score += 2; reasons.append(f"RSI {rsi14:.0f} aşırı satım")
            if stretched_down: score += 1; reasons.append(f"SMA200 -%{abs(sma200_dev):.1f} stretched")
            if consec_red >= 3: score += 1; reasons.append(f"{consec_red}g ardışık kırmızı (tükeniş)")
            return score, reasons

        # Bearish reversal pattern'i anlamlı yapan lokasyonlar
        def _bear_location_score():
            score = 0; reasons = []
            if at_top_25:    score += 2; reasons.append(f"52H %{pos52*100:.0f} tepede")
            if overbought:   score += 2; reasons.append(f"RSI {rsi14:.0f} aşırı alım")
            if stretched_up: score += 1; reasons.append(f"SMA200 +%{sma200_dev:.1f} stretched")
            if consec_green >= 3: score += 1; reasons.append(f"{consec_green}g ardışık yeşil (tükeniş)")
            return score, reasons

        # Lokasyon → confidence çarpan + "ANLAMLI / ORTA / GÜRÜLTÜ" etiketi
        # score >=3 → ANLAMLI (×1.0) · score 1-2 → ORTA (×0.7) · score 0 → GÜRÜLTÜ (×0.4, eşik altı düşer)
        def _location_tag(score):
            if score >= 3: return "ANLAMLI", 1.0
            elif score >= 1: return "ORTA", 0.7
            else: return "GÜRÜLTÜ", 0.4

        bull_score, bull_reasons = _bull_location_score()
        bear_score, bear_reasons = _bear_location_score()
        bull_tag, bull_mult = _location_tag(bull_score)
        bear_tag, bear_mult = _location_tag(bear_score)
        bull_loc_txt = (" | LOKASYON: " + " + ".join(bull_reasons)) if bull_reasons else ""
        bear_loc_txt = (" | LOKASYON: " + " + ".join(bear_reasons)) if bear_reasons else ""

        # Son 3 mum metrikleri
        def _m(i):
            rng = max(h[i] - l[i], 1e-9)
            body = abs(c[i] - o[i])
            upper_shadow = h[i] - max(c[i], o[i])
            lower_shadow = min(c[i], o[i]) - l[i]
            return {
                'range': rng, 'body': body,
                'upper': upper_shadow, 'lower': lower_shadow,
                'green': c[i] > o[i], 'red': c[i] < o[i],
                'body_pct': body / rng if rng > 0 else 0,
                'mid': (o[i] + c[i]) / 2,
            }
        t0, t1, t2 = _m(-3), _m(-2), _m(-1)  # t2 = bugün

        # NOT: Aşağıdaki her pattern için BASE_CONFIDENCE × LOCATION_MULT uygulanır.
        # bullish_reversal → bull_mult (52H dip + RSI oversold + SMA200 stretched + 3g+ ardışık kırmızı)
        # bearish_reversal → bear_mult (52H tepe + RSI overbought + SMA200 stretched + 3g+ ardışık yeşil)
        # Lokasyon "GÜRÜLTÜ" ise (×0.4) çoğu pattern eşik altı düşer → otomatik filtre.
        # Pattern context'ine "ANLAMLI/ORTA/GÜRÜLTÜ" + lokasyon nedenleri eklenir.

        # 1) BULLISH ENGULFING
        if t1['red'] and t2['green'] and o[-1] < c[-2] and c[-1] > o[-2] and t2['body'] > t1['body']:
            base = 85 if trend == "down" else 60
            out.append({'name': 'Bullish Engulfing', 'type': 'bullish_reversal',
                        'confidence': int(base * bull_mult),
                        'context': f"t-1 kırmızı + bugün yeşil + gövde kapsıyor | KONUM: {bull_tag}{bull_loc_txt}"})

        # 2) BEARISH ENGULFING
        if t1['green'] and t2['red'] and o[-1] > c[-2] and c[-1] < o[-2] and t2['body'] > t1['body']:
            base = 85 if trend == "up" else 60
            out.append({'name': 'Bearish Engulfing', 'type': 'bearish_reversal',
                        'confidence': int(base * bear_mult),
                        'context': f"t-1 yeşil + bugün kırmızı + gövde kapsıyor | KONUM: {bear_tag}{bear_loc_txt}"})

        # 3) PIERCING LINE
        if t1['red'] and t1['body_pct'] > 0.5 and t2['green']:
            t1_mid = (o[-2] + c[-2]) / 2
            if o[-1] <= c[-2] * 1.005 and c[-1] > t1_mid:
                pen = (c[-1] - c[-2]) / (o[-2] - c[-2]) * 100 if (o[-2] - c[-2]) > 0 else 0
                base = 80 if trend == "down" else 55
                out.append({'name': 'Piercing Line', 'type': 'bullish_reversal',
                            'confidence': int(base * bull_mult),
                            'context': f"penetrasyon %{pen:.0f} | KONUM: {bull_tag}{bull_loc_txt}"})

        # 4) DARK CLOUD COVER
        if t1['green'] and t1['body_pct'] > 0.5 and t2['red']:
            t1_mid = (o[-2] + c[-2]) / 2
            if o[-1] >= c[-2] * 0.995 and c[-1] < t1_mid:
                base = 80 if trend == "up" else 55
                out.append({'name': 'Dark Cloud Cover', 'type': 'bearish_reversal',
                            'confidence': int(base * bear_mult),
                            'context': f"üst yarıyı kestiği için | KONUM: {bear_tag}{bear_loc_txt}"})

        # 5) HAMMER
        if t2['body'] > 0 and t2['lower'] >= 2 * t2['body'] and t2['upper'] < 0.25 * t2['body'] and t2['body_pct'] < 0.4:
            base = 75 if trend == "down" else 35
            out.append({'name': 'Hammer', 'type': 'bullish_reversal',
                        'confidence': int(base * bull_mult),
                        'context': f"uzun alt gölge ({t2['lower']/t2['body']:.1f}x gövde) | KONUM: {bull_tag}{bull_loc_txt}"})

        # 6) SHOOTING STAR
        if t2['body'] > 0 and t2['upper'] >= 2 * t2['body'] and t2['lower'] < 0.25 * t2['body'] and t2['body_pct'] < 0.4:
            base = 75 if trend == "up" else 35
            out.append({'name': 'Shooting Star', 'type': 'bearish_reversal',
                        'confidence': int(base * bear_mult),
                        'context': f"uzun üst gölge ({t2['upper']/t2['body']:.1f}x gövde) | KONUM: {bear_tag}{bear_loc_txt}"})

        # 7) MORNING STAR (dipten gelen klasik 3-mum dönüş)
        if t0['red'] and t0['body_pct'] > 0.5 and t1['body_pct'] < 0.4 and t2['green']:
            t0_mid = (o[-3] + c[-3]) / 2
            if c[-1] > t0_mid:
                base = 85 if trend == "down" else 60
                out.append({'name': 'Morning Star', 'type': 'bullish_reversal',
                            'confidence': int(base * bull_mult),
                            'context': f"3 mumlu dip dönüş | KONUM: {bull_tag}{bull_loc_txt}"})

        # 8) EVENING STAR (tepeden gelen klasik 3-mum dönüş)
        if t0['green'] and t0['body_pct'] > 0.5 and t1['body_pct'] < 0.4 and t2['red']:
            t0_mid = (o[-3] + c[-3]) / 2
            if c[-1] < t0_mid:
                base = 85 if trend == "up" else 60
                out.append({'name': 'Evening Star', 'type': 'bearish_reversal',
                            'confidence': int(base * bear_mult),
                            'context': f"3 mumlu tepe dönüş | KONUM: {bear_tag}{bear_loc_txt}"})

        # 9) DOJI
        if t2['body_pct'] < 0.05 and t2['range'] > 0:
            doji_type = "Long-legged Doji" if (t2['upper'] > t2['range']*0.3 and t2['lower'] > t2['range']*0.3) else \
                        "Gravestone Doji" if (t2['upper'] > t2['range']*0.5 and t2['lower'] < t2['range']*0.1) else \
                        "Dragonfly Doji" if (t2['lower'] > t2['range']*0.5 and t2['upper'] < t2['range']*0.1) else \
                        "Doji"
            # Dragonfly = bullish reversal · Gravestone = bearish reversal · diğerleri = tereddüt
            if "Dragonfly" in doji_type:
                base = 65; mult = bull_mult; tag = bull_tag; loc_txt = bull_loc_txt; ptype = 'bullish_reversal'
            elif "Gravestone" in doji_type:
                base = 65; mult = bear_mult; tag = bear_tag; loc_txt = bear_loc_txt; ptype = 'bearish_reversal'
            else:
                # Tereddüt — sadece trend ekstreminde anlamlı
                base = 50; mult = max(bull_mult, bear_mult); tag = "EKSTREM" if (bull_tag=="ANLAMLI" or bear_tag=="ANLAMLI") else "ORTA"; loc_txt = ""; ptype = 'indecision'
            out.append({'name': doji_type, 'type': ptype,
                        'confidence': int(base * mult),
                        'context': f"alıcı-satıcı dengesi | KONUM: {tag}{loc_txt}"})

        # 10) THREE WHITE SOLDIERS / THREE BLACK CROWS (continuation — lokasyon farklı)
        # Continuation pattern: dipten kalkış (TWS) veya tepeden iniş (TBC) → bull/bear lokasyonu UYUMLU OLMALI
        if all(_m(i)['green'] and _m(i)['body_pct'] > 0.6 for i in (-3, -2, -1)) and c[-1] > c[-2] > c[-3]:
            # TWS: dipten kalkış için bullish location (dip + oversold + stretched_down)
            out.append({'name': 'Three White Soldiers', 'type': 'bullish_continuation',
                        'confidence': int(70 * bull_mult),
                        'context': f"3 ardışık güçlü yeşil mum, kapanışlar yükselen | KONUM: {bull_tag}{bull_loc_txt}"})
        elif all(_m(i)['red'] and _m(i)['body_pct'] > 0.6 for i in (-3, -2, -1)) and c[-1] < c[-2] < c[-3]:
            out.append({'name': 'Three Black Crows', 'type': 'bearish_continuation',
                        'confidence': int(70 * bear_mult),
                        'context': f"3 ardışık güçlü kırmızı mum, kapanışlar düşen | KONUM: {bear_tag}{bear_loc_txt}"})

        # Confidence eşiği: 40+ olanları döndür (lokasyon GÜRÜLTÜ olanlar otomatik düşer)
        out = [p for p in out if p.get('confidence', 0) >= 40]
        # En yüksek confidence'tan sırala
        out.sort(key=lambda p: p.get('confidence', 0), reverse=True)
    except Exception:
        pass
    return out


def calculate_volume_profile_poc(df, lookback=20, bins=20):
    """Belirtilen periyotta en çok hacmin yığıldığı fiyatı (POC) orantısal olarak bulur."""
    if len(df) < lookback:
        lookback = len(df)
        
    recent_df = df.tail(lookback).copy()
    min_price = float(recent_df['Low'].min())
    max_price = float(recent_df['High'].max())
    
    if min_price == max_price: # Fiyat hiç değişmemişse
        return min_price
        
    # Fiyat dilimlerini (bins) oluştur (Kenar noktaları için bins + 1 kullanıyoruz)
    price_bins = np.linspace(min_price, max_price, bins + 1)
    volume_profile = np.zeros(bins)
    
    # Her bir mumun hacmini, geçtiği dilimlere adil (orantısal) şekilde ekle
    for _, row in recent_df.iterrows():
        high = float(row['High'])
        low = float(row['Low'])
        vol = float(row['Volume'])
        candle_range = high - low
        
        if candle_range <= 0:
            # Doji mumu ise hacmi tek bir dilime at
            idx = np.digitize((high + low) / 2, price_bins) - 1
            idx = min(max(idx, 0), bins - 1)
            volume_profile[idx] += vol
            continue
            
        for i in range(bins):
            bin_bottom = price_bins[i]
            bin_top = price_bins[i+1]
            
            # Mum bu fiyat diliminden geçmiş mi?
            if high >= bin_bottom and low <= bin_top:
                overlap_top = min(high, bin_top)
                overlap_bottom = max(low, bin_bottom)
                overlap_range = overlap_top - overlap_bottom
                
                if overlap_range > 0:
                    # Kesiştiği alanın yüksekliğine göre hacmi bölüştür
                    volume_profile[i] += vol * (overlap_range / candle_range)
                    
    # En yüksek hacme sahip dilimi bul
    poc_index = np.argmax(volume_profile)
    
    # POC fiyatını dilimin TAM ORTASI olarak belirle (eski koddaki gibi alt sınır değil)
    poc_price = (price_bins[poc_index] + price_bins[poc_index + 1]) / 2.0

    return poc_price


def calculate_full_volume_profile(df, lookback=20, bins=20):
    """POC + VAH (Value Area High) + VAL (Value Area Low) döndürür.
    Value Area = toplam hacmin %70'ini kapsayan POC etrafındaki fiyat bölgesi.
    Kurumsal Volume Profile analizinin temel yapı taşı."""
    if len(df) < lookback:
        lookback = len(df)
    recent_df = df.tail(lookback).copy()
    min_price = float(recent_df['Low'].min())
    max_price = float(recent_df['High'].max())
    if min_price == max_price:
        return {'poc': min_price, 'vah': min_price, 'val': min_price}
    price_bins = np.linspace(min_price, max_price, bins + 1)
    volume_profile = np.zeros(bins)
    for _, row in recent_df.iterrows():
        high = float(row['High']); low = float(row['Low']); vol = float(row['Volume'])
        candle_range = high - low
        if candle_range <= 0:
            idx = min(max(np.digitize((high + low) / 2, price_bins) - 1, 0), bins - 1)
            volume_profile[idx] += vol; continue
        for i in range(bins):
            bb, bt = price_bins[i], price_bins[i + 1]
            if high >= bb and low <= bt:
                overlap = min(high, bt) - max(low, bb)
                if overlap > 0:
                    volume_profile[i] += vol * (overlap / candle_range)
    poc_index = int(np.argmax(volume_profile))
    poc_price = (price_bins[poc_index] + price_bins[poc_index + 1]) / 2.0
    # Value Area: POC'tan başla, her adımda daha yüksek hacimli komşuyu ekle
    total_vol = volume_profile.sum()
    target_vol = total_vol * 0.70
    included = [poc_index]
    cumulative = volume_profile[poc_index]
    lower, upper = poc_index - 1, poc_index + 1
    while cumulative < target_vol:
        lv = volume_profile[lower] if lower >= 0 else 0.0
        uv = volume_profile[upper] if upper < bins else 0.0
        if lv == 0 and uv == 0: break
        if uv >= lv:
            included.append(upper); cumulative += uv; upper += 1
        else:
            included.append(lower); cumulative += lv; lower -= 1
    hi_idx = max(included); lo_idx = min(included)
    vah = (price_bins[hi_idx] + price_bins[min(hi_idx + 1, bins)]) / 2.0
    val = (price_bins[lo_idx] + price_bins[min(lo_idx + 1, bins)]) / 2.0
    return {'poc': poc_price, 'vah': vah, 'val': val}


def calculate_multi_tf_pocs(df, current_price=None):
    """Multi-Timeframe POC stack — kısa(20g) + orta(60g) + uzun(250g) POC'ları birlikte hesaplar.
    Üçü birbirine yakınsa (max-min spread < %2) → POC Confluence Zone.
    Yakınlaştıkça mıknatıs etkisi kuvvetlenir (kurumsal seviyelerin üst üste binmesi).

    Returns:
        {
          'poc_20':  float,   'poc_60':  float,   'poc_250': float,
          'spread_pct': float (max-min / mean * 100),
          'confluence': bool,            # spread < %2
          'confluence_mid': float|None,  # 3 POC ortalaması (confluence ise)
          'dist_from_price_pct': dict    # her POC'a uzaklık % (current_price verildiyse)
        }
    """
    out = {'poc_20': None, 'poc_60': None, 'poc_250': None,
           'spread_pct': None, 'confluence': False, 'confluence_mid': None,
           'dist_from_price_pct': {}}
    try:
        if df is None or len(df) < 20:
            return out
        # 3 pencere — veri yetmiyorsa o slot None kalır
        if len(df) >= 20:
            out['poc_20'] = calculate_volume_profile_poc(df, lookback=20, bins=20)
        if len(df) >= 60:
            out['poc_60'] = calculate_volume_profile_poc(df, lookback=60, bins=25)
        if len(df) >= 250:
            out['poc_250'] = calculate_volume_profile_poc(df, lookback=250, bins=30)
        elif len(df) >= 120:
            # 250g yoksa eldeki en uzun pencere (yıllık yaklaşımı)
            out['poc_250'] = calculate_volume_profile_poc(df, lookback=len(df), bins=30)

        vals = [v for v in (out['poc_20'], out['poc_60'], out['poc_250']) if v is not None and not np.isnan(v)]
        if len(vals) >= 2:
            _mn, _mx = min(vals), max(vals)
            _mean = sum(vals) / len(vals)
            if _mean > 0:
                out['spread_pct'] = (_mx - _mn) / _mean * 100.0
                if out['spread_pct'] < 2.0 and len(vals) == 3:
                    out['confluence'] = True
                    out['confluence_mid'] = _mean

        if current_price is not None and current_price > 0:
            for k in ('poc_20', 'poc_60', 'poc_250'):
                v = out[k]
                if v is not None and not np.isnan(v):
                    out['dist_from_price_pct'][k] = (current_price - v) / v * 100.0
    except Exception:
        pass
    return out


def calculate_anchored_vwap(df, anchor_idx):
    """Belirli bir bardan başlatılan Anchored VWAP serisi döndürür.
    anchor_idx: df içinde anchor barın integer pozisyonu (df.iloc[anchor_idx]).
    Anchor noktasından bugüne kadar (P × V) / V kümülatif ortalama.

    Returns: pd.Series (index = df.index[anchor_idx:]) veya None
    """
    try:
        if df is None or anchor_idx is None or anchor_idx < 0 or anchor_idx >= len(df):
            return None
        sub = df.iloc[anchor_idx:].copy()
        # Tipik fiyat: (H+L+C)/3 — klasik VWAP formülü
        if all(c in sub.columns for c in ('High', 'Low', 'Close', 'Volume')):
            tp = (sub['High'].astype(float) + sub['Low'].astype(float) + sub['Close'].astype(float)) / 3.0
        else:
            tp = sub['Close'].astype(float)
        vol = sub['Volume'].astype(float).fillna(0)
        cum_pv = (tp * vol).cumsum()
        cum_v  = vol.cumsum().replace(0, np.nan)
        return cum_pv / cum_v
    except Exception:
        return None


def detect_naked_poc(df, lookback=20, bins=20, n_windows=4):
    """Geçmiş periyotlarda oluşmuş ama fiyatın test etmediği POC seviyelerini bulur.
    Naked POC = kurumsal limit emir bölgesi, güçlü mıknatıs."""
    naked = []
    n = len(df)
    curr_price = float(df['Close'].iloc[-1])
    for w in range(1, n_windows + 1):
        end = n - (w - 1) * lookback
        start = end - lookback
        if start < 0: break
        window_df = df.iloc[start:end]
        if len(window_df) < 5: break
        try:
            poc = calculate_volume_profile_poc(window_df, lookback=len(window_df), bins=bins)
        except Exception: continue
        # Bu POC'tan sonra fiyat bu seviyeye uğramış mı?
        if end < n:
            sub = df.iloc[end:]
            tested = bool(((sub['Low'] <= poc) & (sub['High'] >= poc)).any())
        else:
            tested = False
        if not tested and abs(poc - curr_price) / (curr_price + 1e-9) > 0.003:
            naked.append(poc)
    return naked


def detect_supply_demand_zones(df):
    """
    RBR, DBD, RBD ve DBR formasyonlarını ERC (Momentum Mumu) onayıyla tarar.
    En taze (test edilmemiş veya yeni) bölgeyi döndürür.
    """
    try:
        if df is None or df.empty or len(df) < 50: return None
        
        close = df['Close']
        open_ = df['Open']
        high = df['High']
        low = df['Low']
        
        # 1. Mum Geometrisi ve ERC (Extended Range Candle) Tespiti
        body = abs(close - open_)
        rng = high - low
        
        # Son 20 mumun ortalama gövde boyutu (Kıyaslama için)
        avg_body = body.rolling(20).mean()
        
        zones = []
        
        # Son 100 muma bakıyoruz (Taze bölgeler için yeterli bir derinlik)
        start_idx = max(2, len(df) - 100)
        
        for i in range(start_idx, len(df)):
            leg_in_idx = i - 2
            base_idx = i - 1
            leg_out_idx = i
            
            # --- ERC (Geniş Gövdeli Momentum Mumu) Şartları ---
            # Giriş ve Çıkış mumlarının gövdesi hem ortalamadan büyük olmalı hem de kendi fitillerinden (mumun %50'sinden) büyük olmalı.
            leg_in_erc = body.iloc[leg_in_idx] > avg_body.iloc[leg_in_idx] and body.iloc[leg_in_idx] > (rng.iloc[leg_in_idx] * 0.5)
            leg_out_erc = body.iloc[leg_out_idx] > avg_body.iloc[leg_out_idx] and body.iloc[leg_out_idx] > (rng.iloc[leg_out_idx] * 0.5)
            
            # --- Base (Denge) Mumu Şartları ---
            # Gövdesi küçük olmalı (kendi toplam boyunun %50'sinden küçük)
            is_base = body.iloc[base_idx] < (rng.iloc[base_idx] * 0.5)
            
            if leg_in_erc and leg_out_erc and is_base:
                # Yönleri Belirle
                in_green = close.iloc[leg_in_idx] > open_.iloc[leg_in_idx]
                in_red = close.iloc[leg_in_idx] < open_.iloc[leg_in_idx]
                out_green = close.iloc[leg_out_idx] > open_.iloc[leg_out_idx]
                out_red = close.iloc[leg_out_idx] < open_.iloc[leg_out_idx]
                
                z_type = ""
                z_top = 0.0
                z_bot = 0.0
                
                # Formasyon Eşleştirmeleri
                if in_green and out_green:
                    z_type = "RBR (Rally-Base-Rally) / Talep"
                    z_top = max(open_.iloc[base_idx], close.iloc[base_idx]) # Base gövde üstü
                    z_bot = low.iloc[base_idx] # Base fitil altı
                elif in_red and out_red:
                    z_type = "DBD (Drop-Base-Drop) / Arz"
                    z_bot = min(open_.iloc[base_idx], close.iloc[base_idx]) # Base gövde altı
                    z_top = high.iloc[base_idx] # Base fitil üstü
                elif in_green and out_red:
                    z_type = "RBD (Rally-Base-Drop) / Arz"
                    z_bot = min(open_.iloc[base_idx], close.iloc[base_idx])
                    z_top = high.iloc[base_idx]
                elif in_red and out_green:
                    z_type = "DBR (Drop-Base-Rally) / Talep"
                    z_top = max(open_.iloc[base_idx], close.iloc[base_idx])
                    z_bot = low.iloc[base_idx]
                    
                if z_type != "":
                    # Fiyat aralığı çok darsa (Hatalı veri engellemesi) alma
                    if z_top > z_bot:
                        zones.append({
                            'Type': z_type,
                            'Top': float(z_top),
                            'Bottom': float(z_bot),
                            'Age': len(df) - i # Kaç mum önce oluştu?
                        })
        
        if not zones: return None
        
        # En taze (en son oluşan) bölgeyi al
        latest_zone = zones[-1]
        curr_price = float(close.iloc[-1])
        
        # Bölge şu an ihlal edildi mi? (Test Durumu)
        status = "Taze / Beklemede"
        if "Talep" in latest_zone['Type'] and curr_price < latest_zone['Bottom']:
            status = "İhlal Edildi (Kırıldı)"
        elif "Arz" in latest_zone['Type'] and curr_price > latest_zone['Top']:
            status = "İhlal Edildi (Kırıldı)"
        elif latest_zone['Bottom'] <= curr_price <= latest_zone['Top']:
            status = "Bölge İçinde (Test Ediliyor)"
            
        latest_zone['Status'] = status
        return latest_zone
        
    except Exception:
        return None


def _harmonik_52h_strength(df):
    """52H konuma göre (etiket, sıra_rank, konum%) döndürür. Düşük rank = güçlü."""
    try:
        seg = df.tail(252)
        h52, l52, cv = float(seg['High'].max()), float(seg['Low'].min()), float(df['Close'].iloc[-1])
        if h52 <= l52:
            return '', 1, None
        p52 = (cv - l52) / (h52 - l52) * 100
        if p52 >= 60:   return '🟢 GÜÇLÜ', 0, round(p52, 1)
        elif p52 >= 40: return '🟡 ORTA',  1, round(p52, 1)
        else:           return '🔴 ZAYIF', 2, round(p52, 1)
    except Exception:
        return '', 1, None


def calculate_supertrend(df, period=10, multiplier=3.0):
    """
    SuperTrend indikatörünü hesaplar.
    Dönüş: (SuperTrend Değeri, Trend Yönü [1: Boğa, -1: Ayı])
    """
    try:
        high = df['High']
        low = df['Low']
        close = df['Close']
        
        # ATR Hesaplama
        tr1 = pd.DataFrame(high - low)
        tr2 = pd.DataFrame(abs(high - close.shift(1)))
        tr3 = pd.DataFrame(abs(low - close.shift(1)))
        frames = [tr1, tr2, tr3]
        tr = pd.concat(frames, axis=1, join='inner').max(axis=1)
        atr = tr.ewm(alpha=1/period, adjust=False).mean()

        # Temel Bantlar
        hl2 = (high + low) / 2
        final_upperband = hl2 + (multiplier * atr)
        final_lowerband = hl2 - (multiplier * atr)
        
        supertrend = [True] * len(df) # Başlangıç (True = Boğa varsayımı)
        st_value = [0.0] * len(df)
        
        # Döngüsel Hesaplama (SuperTrend doğası gereği önceki değere bakar)
        for i in range(1, len(df.index)):
            curr, prev = i, i-1
            
            # Üst Bant Mantığı
            if close.iloc[curr] > final_upperband.iloc[prev]:
                supertrend[curr] = True
            elif close.iloc[curr] < final_lowerband.iloc[prev]:
                supertrend[curr] = False
            else:
                supertrend[curr] = supertrend[prev]
                
                # Bantları Daraltma (Trailing Stop Mantığı)
                if supertrend[curr] == True and final_lowerband.iloc[curr] < final_lowerband.iloc[prev]:
                    final_lowerband.iloc[curr] = final_lowerband.iloc[prev]
                
                if supertrend[curr] == False and final_upperband.iloc[curr] > final_upperband.iloc[prev]:
                    final_upperband.iloc[curr] = final_upperband.iloc[prev]

            if supertrend[curr] == True:
                st_value[curr] = final_lowerband.iloc[curr]
            else:
                st_value[curr] = final_upperband.iloc[curr]
                
        return st_value[-1], (1 if supertrend[-1] else -1)
        
    except Exception:
        return 0, 0


def calculate_fib_levels(df, st_dir=1, period=144):
    """
    Dinamik Makro Döngü (Macro Cycle) Fibonacci Hesaplama
    Trend yönüne göre anlık sekmeleri değil, GEÇMİŞ MAKRO DALGAYI referans alır.
    """
    try:
        if len(df) < period: period = len(df)
        recent_data = df.tail(period)
        
        high_s = recent_data['High'].iloc[:, 0] if isinstance(recent_data['High'], pd.DataFrame) else recent_data['High']
        low_s = recent_data['Low'].iloc[:, 0] if isinstance(recent_data['Low'], pd.DataFrame) else recent_data['Low']
        
        max_h = float(high_s.max())
        min_l = float(low_s.min())
        diff = max_h - min_l
    
        # Trend yönü doğrudan SuperTrend'den alınır.
        is_uptrend = (st_dir == 1)
        
        levels = {}
        
        if is_uptrend:
            # YÜKSELİŞ SENARYOSU: Geçmiş makro düşüşü ölçüyoruz.
            # (Tepeden Dibe Çekilen Fibonacci) 
            # 0 noktası Dipte. Fiyat dipten yukarı çıkarken Premium dirençleri test eder.
            levels = {
                "-0.618 (Hedef)": max_h + (diff * 0.618),
                "-0.236 (Kırılım Hedefi)": max_h + (diff * 0.236),
                "1 (Tepe)": max_h,
                "0.618 (Golden - Satış)": min_l + (diff * 0.618), # OTE Direnci (Akıllı Para Short)
                "0.5 (Orta)": min_l + (diff * 0.5),
                "0.382": min_l + (diff * 0.382),
                "0.236": min_l + (diff * 0.236),
                "0 (Dip)": min_l
            }
        else:
            # DÜŞÜŞ SENARYOSU: Geçmiş makro yükselişi ölçüyoruz.
            # (Dipten Tepeye Çekilen Fibonacci)
            # 0 noktası Tepede. Fiyat zirveden aşağı düşerken Discount destekleri test eder.
            levels = {
                "0 (Tepe)": max_h,
                "0.236": max_h - (diff * 0.236),
                "0.382": max_h - (diff * 0.382),
                "0.5 (Orta)": max_h - (diff * 0.5),
                "0.618 (Golden - Alım)": max_h - (diff * 0.618), # Makro Destek (11764 - Akıllı Para Long)
                "1 (Dip)": min_l,
                "1.236 (Kırılım Hedefi)": min_l - (diff * 0.236),
                "1.618 (Hedef)": min_l - (diff * 0.618)
            }
            
        return levels
    except:
        return {}


def calculate_z_score_live(df, period=20):
    """
    Professional Multi-Window Detrended Z-Score
    ─────────────────────────────────────────────
    Katman 1 — Detrending   : Her pencereden lineer trend çıkarılır.
                              Yükselen trendde hep "pahalı" görünen Bollinger
                              hatasını ortadan kaldırır.
    Katman 2 — Multi-Window : 20G (%50) + 60G (%30) + 252G (%20) ağırlıklı
                              composite score. Tek pencere gürültüsünü azaltır.
    Katman 3 — Trend Regime : Güçlü trendde (>%0.3/gün) trende karşı sinyaller
                              yarıya indirilir. Trend yönündeki sinyaller tam güçte.
    Katman 4 — ATR Context  : Composite Z ile birlikte ATR katı da hesaplanır,
                              details dict'e eklenir (sinyal paneli tarafından kullanılır).
    Dönüş: composite Z-score (float) — geriye uyumlu
    """
    try:
        close = df['Close']
        n = len(close)
        if n < 20:
            return 0

        # ── Katman 1 + 2: Detrended Multi-Window Z ───────────────────────
        windows_weights = [(20, 0.50), (60, 0.30), (252, 0.20)]
        z_parts      = []
        total_weight = 0.0

        for win, w in windows_weights:
            if n < win:
                continue
            y = close.iloc[-win:].values.astype(float)
            x = np.arange(win, dtype=float)
            # Lineer trend çıkar
            coeffs    = np.polyfit(x, y, 1)
            detrended = y - np.polyval(coeffs, x)
            std_d     = float(np.std(detrended, ddof=1))
            if std_d < 1e-9:
                continue
            z_w = detrended[-1] / std_d      # son bar arındırılmış dağılımda nerede?
            z_parts.append(z_w * w)
            total_weight += w

        if total_weight < 0.1:
            return 0

        composite_z = sum(z_parts) / total_weight

        # ── Katman 3: Trend Regime Filtresi ──────────────────────────────
        y20   = close.iloc[-20:].values.astype(float)
        slope = float(np.polyfit(np.arange(20, dtype=float), y20, 1)[0])
        p_ref = float(close.iloc[-20]) if float(close.iloc[-20]) > 0 else 1.0
        trend_pct = slope / p_ref          # günlük normalize eğim

        THRESHOLD = 0.003                  # %0.3/gün
        if trend_pct > THRESHOLD and composite_z > 0:
            composite_z *= 0.5             # güçlü yükseliş → "pahalı" sinyali bastır
        elif trend_pct < -THRESHOLD and composite_z < 0:
            composite_z *= 0.5             # güçlü düşüş → "ucuz" sinyali bastır

        return round(composite_z, 3)

    except:
        return 0


def _z_score_details(df):
    """
    Sinyal paneli için Z-score detaylarını döndürür.
    Hem composite Z hem de bağlam bilgisi (trend rejimi, ATR katı, pencere Z'leri).
    """
    try:
        close = df['Close']
        n     = len(close)
        if n < 20:
            return None

        windows_weights = [(20, 0.50), (60, 0.30), (252, 0.20)]
        z_by_win     = {}
        z_parts      = []
        total_weight = 0.0

        for win, w in windows_weights:
            if n < win:
                continue
            y         = close.iloc[-win:].values.astype(float)
            x         = np.arange(win, dtype=float)
            coeffs    = np.polyfit(x, y, 1)
            detrended = y - np.polyval(coeffs, x)
            std_d     = float(np.std(detrended, ddof=1))
            if std_d < 1e-9:
                continue
            z_w             = detrended[-1] / std_d
            z_by_win[win]   = round(z_w, 2)
            z_parts.append(z_w * w)
            total_weight   += w

        if total_weight < 0.1:
            return None

        composite_raw = sum(z_parts) / total_weight

        # Trend regime
        y20      = close.iloc[-20:].values.astype(float)
        slope    = float(np.polyfit(np.arange(20, dtype=float), y20, 1)[0])
        p_ref    = float(close.iloc[-20]) if float(close.iloc[-20]) > 0 else 1.0
        trend_pct = slope / p_ref
        THRESHOLD = 0.003
        filtered  = False
        composite = composite_raw
        if trend_pct > THRESHOLD and composite_raw > 0:
            composite *= 0.5; filtered = True
        elif trend_pct < -THRESHOLD and composite_raw < 0:
            composite *= 0.5; filtered = True

        # ATR katı (volatilite bağlamı)
        atr_multiple = 0.0
        try:
            if n >= 14 and 'High' in df.columns and 'Low' in df.columns:
                tr = pd.concat([
                    df['High'] - df['Low'],
                    (df['High'] - close.shift(1)).abs(),
                    (df['Low']  - close.shift(1)).abs(),
                ], axis=1).max(axis=1)
                atr14 = float(tr.rolling(14).mean().iloc[-1])
                mean20 = float(close.iloc[-20:].mean())
                if atr14 > 0:
                    atr_multiple = round(abs(float(close.iloc[-1]) - mean20) / atr14, 2)
        except:
            pass

        trend_dir = "↑ Yükseliş" if trend_pct > THRESHOLD else ("↓ Düşüş" if trend_pct < -THRESHOLD else "→ Nötr")

        return {
            "composite":     round(composite, 3),
            "composite_raw": round(composite_raw, 3),
            "z20":           z_by_win.get(20, 0),
            "z60":           z_by_win.get(60, 0),
            "z252":          z_by_win.get(252, 0),
            "trend_pct":     round(trend_pct * 100, 3),
            "trend_dir":     trend_dir,
            "filtered":      filtered,
            "atr_multiple":  atr_multiple,
        }
    except:
        return None


def detect_market_regime(df, pa=None):
    """
    Piyasa fazını tespit eder: 1-Birikim, 2-Yükseliş, 3-Dağıtım, 4-Düşüş.
    Dönen dict:
        phase       : int (1-4)
        label       : str  ("Yükseliş" vb.)
        icon        : str
        confidence  : float 0-1
        color       : str  hex
        desc        : str  (kısa açıklama)
        bull_bias   : bool
    """
    try:
        close = df['Close']
        n = len(close)
        if n < 50:
            return {"phase": 0, "label": "Yetersiz Veri", "icon": "❓",
                    "confidence": 0.0, "color": "#64748b", "desc": "", "bull_bias": False}

        cp = float(close.iloc[-1])

        # SMA50 & SMA200
        sma50  = float(close.rolling(50).mean().iloc[-1])
        sma200 = float(close.rolling(200).mean().iloc[-1]) if n >= 200 else None

        # 20-günlük eğim (% / gün)
        y20   = close.iloc[-20:].values.astype(float)
        slope = float(np.polyfit(np.arange(20, dtype=float), y20, 1)[0])
        p_ref = float(close.iloc[-20]) if float(close.iloc[-20]) > 0 else 1.0
        slope_pct = slope / p_ref  # günlük % eğim

        # OBV trendi (20 günlük)
        obv_rising = False
        try:
            if 'Volume' in df.columns:
                direction = np.sign(close.diff().fillna(0))
                obv = (direction * df['Volume']).cumsum()
                obv_slope = float(np.polyfit(np.arange(20, dtype=float),
                                             obv.iloc[-20:].values.astype(float), 1)[0])
                obv_rising = obv_slope > 0
        except:
            pass

        # Z-score (tepe/dip bağlamı)
        zd = _z_score_details(df)
        z  = zd["composite"] if zd else 0.0

        # BOS / yapı yönü (ICT'den opsiyonel)
        bos_bull = False
        try:
            if pa:
                struct = str(pa.get('structure', ''))
                bos_bull = "YÜKSELİŞ" in struct.upper() or "MSS" in struct.upper()
        except:
            pass

        # ── Skor hesabı ──
        bull_pts = 0; bear_pts = 0; total_pts = 0

        # SMA50
        total_pts += 2
        if cp > sma50:     bull_pts += 2
        else:              bear_pts += 2

        # SMA200
        if sma200:
            total_pts += 2
            if cp > sma200:    bull_pts += 2
            else:              bear_pts += 2
            # Golden/Death Cross
            if sma50 > sma200: bull_pts += 1; total_pts += 1
            else:              bear_pts += 1; total_pts += 1

        # Eğim
        total_pts += 2
        if slope_pct > 0.001:    bull_pts += 2
        elif slope_pct < -0.001: bear_pts += 2

        # OBV
        total_pts += 2
        if obv_rising: bull_pts += 2
        else:          bear_pts += 2

        # Z-score (tepe → dağıtım / dip → birikim)
        total_pts += 1
        if z <= -1.5:   bull_pts += 1
        elif z >= 1.5:  bear_pts += 1

        confidence = bull_pts / total_pts if total_pts > 0 else 0.5
        bull_bias  = confidence >= 0.5

        # ── Faz tespiti ──
        if confidence >= 0.70:
            # Phase 2 – Yükseliş (güçlü boğa)
            phase = 2; label = "Yükseliş Fazı"; icon = "🚀"
            color = "#10b981"; desc = "Fiyat yapısal olarak yükseliş fazında: SMA üzeri, OBV artıyor, eğim pozitif."
        elif confidence >= 0.55:
            if obv_rising and cp < sma50:
                # Phase 1 – Birikim (OBV artıyor ama fiyat henüz kırılmadı)
                phase = 1; label = "Birikim Fazı"; icon = "🧲"
                color = "#38bdf8"; desc = "Fiyat yatay / baskılı ama OBV birikimi devam ediyor. Kırılım hazırlığı."
            else:
                phase = 2; label = "Yükseliş Fazı"; icon = "📈"
                color = "#4ade80"; desc = "Genel boğa yapısı baskın, bazı karışık sinyaller var."
        elif confidence <= 0.30:
            # Phase 4 – Düşüş (güçlü ayı)
            phase = 4; label = "Düşüş Fazı"; icon = "🔻"
            color = "#f87171"; desc = "Fiyat yapısal olarak düşüş fazında: SMA altı, OBV geriliyor, eğim negatif."
        elif confidence <= 0.45:
            if not obv_rising and cp > sma50:
                # Phase 3 – Dağıtım (fiyat yüksek ama OBV bozuluyor)
                phase = 3; label = "Dağıtım Fazı"; icon = "⚠️"
                color = "#f97316"; desc = "Fiyat zirvelerde ama OBV aşınıyor, kurumsal çıkış belirtileri var."
            else:
                phase = 4; label = "Düşüş Fazı"; icon = "📉"
                color = "#f87171"; desc = "Genel ayı yapısı baskın, bazı karışık sinyaller var."
        else:
            # Nötr geçiş bölgesi
            phase = 0; label = "Geçiş / Belirsiz"; icon = "↔️"
            color = "#94a3b8"; desc = "Boğa ve ayı güçleri dengede. Net trend oluşmamış."
            bull_bias = False

        return {
            "phase":      phase,
            "label":      label,
            "icon":       icon,
            "confidence": round(confidence, 2),
            "color":      color,
            "desc":       desc,
            "bull_bias":  bull_bias,
        }
    except:
        return {"phase": 0, "label": "Hata", "icon": "❓",
                "confidence": 0.0, "color": "#64748b", "desc": "", "bull_bias": False}


def compute_flow_momentum(df, stp_span=6):
    """Para Akış İvmesi barları — S1+S3 %50-%50 KARIŞIM (9 Tem 2026, TEK KAYNAK).

    Eski formül (TP − DEMA6)/DEMA6 KONUM ölçüyordu: fiyat trendin üstünde mi.
    Referans davranış İVME: puan dünden bugüne arttı mı. 4 hisse (EREGL/SISE/
    TUPRS/AKBNK) görsel karşılaştırması sonrası seçilen karışım (_bartest_karisim.py):

      S1 = STP(TP'nin EMA'sı) günlük eğimi / Close × 1000   ← zamanlama omurgası
      S3 = kompozit 0-100 puanın günlük değişimi             ← hacim + sentiment dokusu
           (puan = RSI14 + MFI14 + 20g konum ortalaması, EMA3 yumuşatma)
      bar = (S1/σ90 × 0.5 + S3/σ90 × 0.5) × 10               ← ortak ölçek + eski büyüklük

    Hacim bekçisi: son 30 barın %20'sinden fazlası V=0 ise (endeks/gap-fill/FX)
    MFI kompozitten düşer → puan = (RSI + konum)/2. Downstream tüketicilerin hepsi
    yalnız İŞARET okur (>0 mavi/pozitif) — ölçek değişimi güvenli.

    Kullanan yerler: app.py calculate_synthetic_sentiment (panel) · smr_core
    _calc_synth_data (bot görseli) · clean_chart_plotly build_ivme_fig (infografik)
    · public/backend/scan_core generate_xu100_chart_data (site) · produce_stock_json
    + produce_light_stock calc_mf_smooth (site hisse JSON'ları).

    Args:
        df: 'High','Low','Close' (+tercihen 'Volume') içeren DataFrame, ≥25 satır önerilir.
        stp_span: STP EMA süresi (panel standardı 6).
    Returns:
        (mf_blend, stp) — iki pandas.Series (df index'inde). Hata → (None, None).
    """
    try:
        c = df['Close'].astype(float)
        h = df['High'].astype(float)
        l = df['Low'].astype(float)
        v = df['Volume'].fillna(0).astype(float) if 'Volume' in df.columns else pd.Series(0.0, index=df.index)

        tp  = (h + l + c) / 3.0
        stp = tp.ewm(span=stp_span, adjust=False).mean()

        # ── S1: STP günlük eğimi (fiyata normalize, binde) ─────────────────
        s1 = stp.diff() / c.replace(0, np.nan) * 1000.0

        # ── S3: kompozit puan değişimi ──────────────────────────────────────
        d   = c.diff()
        up  = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        dn  = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rsi = (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50)

        rng   = (c.rolling(20).max() - c.rolling(20).min()).replace(0, np.nan)
        pos20 = ((c - c.rolling(20).min()) / rng * 100).fillna(50)

        _vol_ok = len(v) >= 5 and float((v.tail(30) > 0).mean()) >= 0.8
        if _vol_ok:
            mfi = compute_mfi(df, period=14)
            mfi = mfi.fillna(50) if mfi is not None else pd.Series(50.0, index=df.index)
            puan = (rsi + mfi + pos20) / 3.0
        else:
            puan = (rsi + pos20) / 2.0   # endeks/hacimsiz: MFI kompozitten düşer
        puan = puan.ewm(span=3, adjust=False).mean()
        # diff sonrası ikinci EMA3 ŞART: bu adım olmadan karışım 'ham S3' gibi
        # kırpışıyor (test: _bartest_karisim.py K-A elendi, yumuşak %50-50 seçildi)
        s3 = puan.diff().ewm(span=3, adjust=False).mean()

        # ── ortak ölçek (90g oynaklık) + %50-%50 karışım + eski bar büyüklüğü ─
        def _n(s):
            sd = s.tail(90).std()
            return s / sd if sd and not np.isnan(sd) and sd > 0 else s
        blend = (0.5 * _n(s1) + 0.5 * _n(s3)) * 10.0
        return blend.fillna(0.0), stp
    except Exception:
        return None, None
