# -*- coding: utf-8 -*-
"""analysis_core.py — PANEL + AI PROMPT ANALIZ MOTORLARI (Adim 9a, 11 Tem 2026)
========================================================================
app.py bolme — tek-hisse panellerini ve AI prompt'unu besleyen 14 analiz motoru
BIREBIR tasindi (davranis degisikligi YOK, sadece adres): 8 maddelik yol haritasi,
haftalik cerceve, OBV uyumsuzluk, STP, kirilim, MTF hizalama, sentetik sentiment,
ICT donus, gelismis seviyeler, ER prompt metni, risk profili, tarama tier/guc,
hacim kalite etiketi. app.py'de kalanlar (UI/goldmine-yapisik): _compute_kanit_ozeti.
Fotograf: golden_record an_* hedefleri (sifir fark).
"""
import os
import numpy as np
import pandas as pd
import pytz
import streamlit as st
from datetime import datetime, timedelta

from data_layer import (CACHE_DIR, _yf_download_with_retry, get_benchmark_data,
                        get_safe_historical_data)
from evidence import SCANNER_TIER_MAP
from ict_core import calculate_ict_deep_analysis, calculate_price_action_dna
from indicators import (calculate_fib_levels, calculate_supertrend,
                        calculate_volume_profile_poc, check_lazybear_squeeze_breakout,
                        compute_cmf, compute_flow_momentum)
from scan_pipeline import _compute_signal_features, scan_chart_patterns
from scanners import _er_bearish_div, _er_rsi

try:
    from bist_calendar import is_trading_day as _bist_is_trading_day
    _BIST_CAL_OK = True
except ImportError:
    def _bist_is_trading_day(_dt=None): return True
    _BIST_CAL_OK = False

_TZ_ISTANBUL = pytz.timezone("Europe/Istanbul")


def _risk_profile(ticker):
    """Faz 2 (19 Haz 2026) — RİSK PROFİLİ: beta/volatilite/drawdown/skew + likidite/manip → sade
    etiketler + genel seviye. Eşikler Haziran scan_signals dağılımıyla. 'Risk nerede' önce gelir."""
    out = {'rows': [], 'level': None}
    try:
        f = _compute_signal_features(ticker) or {}
        beta = f.get('f_beta_xu100'); dd = f.get('f_dd_zirveden'); hv = f.get('f_hv_oran')
        skew = f.get('f_skew_60g'); liq = f.get('f_liquidity_tier'); manip = f.get('f_manip_risk')
        rows = []; pts = 0  # rows: (etiket, sade açıklama, yön 'high'/'mid'/'low')
        if beta is not None:
            if beta > 1.1:   rows.append(("Oynaklık", f"Endeksten %{(beta-1)*100:.0f} daha sert oynuyor ({beta:.1f}×)", 'high')); pts += 1
            elif beta < 0.6: rows.append(("Oynaklık", f"Endeksten daha sakin ({beta:.1f}×)", 'low'))
            else:            rows.append(("Oynaklık", f"Endeksle uyumlu ({beta:.1f}×)", 'mid'))
        if hv is not None:
            if hv > 1.2:     rows.append(("Volatilite trendi", "Artıyor; son dönemde sertleşiyor", 'high')); pts += 1
            elif hv < 0.75:  rows.append(("Volatilite trendi", "Azalıyor; sakinleşiyor", 'low'))
            else:            rows.append(("Volatilite trendi", "Sabit; son dönemde değişmedi", 'mid'))
        if dd is not None:
            if dd < -35:     rows.append(("Zirveden uzaklık", f"Zirvesinin %{abs(dd):.0f} altında; derin düşüşte", 'high')); pts += 1
            elif dd > -10:   rows.append(("Zirveden uzaklık", f"Zirvesine yakın (%{abs(dd):.0f} altında)", 'low'))
            else:            rows.append(("Zirveden uzaklık", f"Zirvesinin %{abs(dd):.0f} altında", 'mid'))
        if skew is not None:
            if skew < -0.5:  rows.append(("Sürpriz yönü", "Dikkat: ani sert düşüş eğilimli", 'high')); pts += 1
            elif skew > 0.5: rows.append(("Sürpriz yönü", "Ani yukarı sıçrama eğilimli", 'low'))
            else:            rows.append(("Sürpriz yönü", "Dengeli; ani çakılma da sıçrama da eğilimi yok", 'mid'))
        if liq:
            if liq == 'düşük':  rows.append(("Likidite", "Dikkat: bu tahta sığ, zor çıkarsın", 'high')); pts += 2
            elif liq == 'orta': rows.append(("Likidite", "Orta; idare eder", 'mid'))
            else:               rows.append(("Likidite", "Yüksek; rahat girip çıkarsın", 'low'))
        if manip and manip != 'yok':
            rows.append(("Manipülasyon", "Dikkat: manipülasyona açık, tahtacı olabilir", 'high')); pts += 2
        out['rows'] = rows
        out['level'] = 'yüksek' if pts >= 3 else ('orta' if pts >= 1 else 'düşük')
    except Exception:
        pass
    return out

def get_active_scanner_tiers(ticker: str) -> list:
    """Bu ticker'ı flag eden TIER'lı scanner'ları döndür.
    Returns: list of dict {scan_type, tier, hit10, avg10, display, note}"""
    out = []
    try:
        # Klasik scanner → session_state map (df içinde 'Sembol' kolonu)
        # 15 Haz 2026 — 'nadir_firsat' (Royal Flush) kaldırıldı, backtest negatif beklenti gösterdi.
        # 'guclu_donus' map'te ama SCANNER_TIER_MAP'te yok → koşullu loop atlayacak (8 Haz 2026 not).
        _classic_map = {
            'prelaunch_bos': 'prelaunch_bos_data',
            'guclu_donus':   'guclu_donus_data',
        }
        for _scan_type, _key in _classic_map.items():
            if _scan_type not in SCANNER_TIER_MAP: continue
            _df = st.session_state.get(_key)
            if _df is None or (hasattr(_df, 'empty') and _df.empty): continue
            _col = 'Sembol' if 'Sembol' in _df.columns else ('Ticker' if 'Ticker' in _df.columns else None)
            if _col is None: continue
            if (_df[_col] == ticker).any():
                _t = SCANNER_TIER_MAP[_scan_type]
                out.append({'scan_type': _scan_type, 'tier': _t[0],
                            'hit10': _t[1], 'avg10': _t[2], 'display': _t[3], 'note': _t[4]})

        # Erken Radar — önce Master Scan batch (erken_radar_data) sonra tek-hisse cache.
        # 16 Haz 2026 — Restart sonrası session_state boş kalınca KONTR gibi TIER_1
        # hissede yeşil kart çıkmıyordu. render_erken_radar_panel() zaten
        # evaluate_erken_radar çağırıyor — sonucu _er_single_cache'e yaz, burası da oku.
        _matched_ids = []
        _er = st.session_state.get('erken_radar_data')
        if _er is not None and hasattr(_er, 'empty') and not _er.empty:
            if 'Sembol' in _er.columns and 'ScenarioId' in _er.columns:
                _rows = _er[_er['Sembol'] == ticker]
                for _, _r in _rows.iterrows():
                    _matched_ids.append(str(_r.get('ScenarioId', '')))
        # Fallback — Master Scan yoksa tek-hisse render cache'i kullan
        if not _matched_ids:
            _single = st.session_state.get('_er_single_cache', {}).get(ticker)
            if _single:
                if _single.get('primary'):
                    _matched_ids.append(str(_single['primary'].get('id', '')))
                for _c in _single.get('confirmations', []) or []:
                    _matched_ids.append(str(_c.get('id', '')))
        for _sid in _matched_ids:
            _key = f"er_{_sid}"
            if _key in SCANNER_TIER_MAP:
                _t = SCANNER_TIER_MAP[_key]
                out.append({'scan_type': _key, 'tier': _t[0],
                            'hit10': _t[1], 'avg10': _t[2], 'display': _t[3], 'note': _t[4]})
    except Exception:
        pass
    # Tier önceliği: TIER_1 > TIER_2 > TIER_VADE_UZUN > TIER_3
    _order = {'TIER_1_ELIT': 0, 'TIER_2_GUVENILIR': 1, 'TIER_VADE_UZUN': 2, 'TIER_3_ZAYIF': 3}
    out.sort(key=lambda x: _order.get(x['tier'], 99))
    return out

def _scanner_setup_strength(ticker: str) -> list:
    """Güç-filtreli 3 tarama (Harmonik/VIP/Erken Radar) bu ticker'da ateşlediyse
    52H/RSI güç etiketini döndürür. İki-rejim backtest kanıtlı (19 Haz 2026):
    güçlü kurulum ayı ayında bile tutar, zayıf kurulum çöker.
    Returns: list of (scanner, guc_str, deger)."""
    out = []
    _tu = str(ticker).upper(); _tc = _tu.replace('.IS', '')

    def _match(df, isiz=False):
        if df is None or (hasattr(df, 'empty') and df.empty) or 'Sembol' not in getattr(df, 'columns', []):
            return None
        try:
            _ser = df['Sembol'].astype(str).str.upper()
            _hit = df[(_ser == _tu) | (_ser == _tc)]
            return _hit.iloc[0] if not _hit.empty else None
        except Exception:
            return None

    try:
        _r = _match(st.session_state.get('harmonic_confluence_data'))
        if _r is not None and _r.get('Guc'):
            out.append(('harmonik_confluence', str(_r.get('Guc')), _r.get('Guc_52h')))
        # VIP Formasyon güç → AI'a EMIT EDİLMİYOR (20 Haz 2026): 52H güç backtest'te ters çıktı
        # (dönüş formasyonu dipte olur). RSI 55-70 adayı izlemede, Temmuz cross-rejim doğrulanınca
        # gerçek güç olarak eklenecek. Yanıltıcı 52H etiketini AI'a sokmuyoruz.
        _r = _match(st.session_state.get('erken_radar_data'), isiz=True)
        if _r is not None and _r.get('Guc'):
            out.append(('erken_radar', str(_r.get('Guc')), _r.get('Guc_rsi')))
        # Pre-Launch BOS güç → AI'a EMIT EDİLMİYOR (20 Haz 2026): 52H güç denetimde TERS+ZARARLI
        # çıktı (🟢 5g −%2.31/10g −%4.06). Yanıltıcı etiketi AI'a sokmuyoruz. VIP ile aynı işlem.
    except Exception:
        pass
    return out

def _compute_volume_quality_label(ticker, df=None, vol_missing=False):
    """
    Bugünkü hacim verisinin GÜVENİLİRLİK ETİKETİ.
    AI prompt YAML'ına enjekte edilir; AI bu etikete göre RVOL yorumunu kalibre eder.

    Dönen değerler:
      "FINAL"             — Seans kapanmış (>=18:15) veya hafta sonu/tatil.
                            Hacim son finalize değeri, GÜVENİLİR.
      "FINAL_ISYATIRIM"   — finalize_volume.py marker dosyası varsa (bugün İsyatirim
                            override yapıldı), KESİN doğru.
      "PROJECTED_LATE"    — Seans son 2 saatte (16:15-18:15). Projeksiyon sapması <%10.
      "PROJECTED_MID"     — Seans orta dilim (11:55-16:15). Sapma %10-25.
      "PROJECTED_EARLY"   — İlk 2 saat (09:55-11:55). Projeksiyon güvensiz.
      "PRE_SESSION"       — Saat 09:55 öncesi, dünden kalma final.
      "DATA_MISSING"      — RVOL hesaplanmadı (vol_missing_flag=True).
      "NON_BIST"          — BIST dışı sembol (kripto/ABD/endeks); ayrı kurallarla bak.
    """
    if vol_missing:
        return "DATA_MISSING"
    try:
        # Sembol kategorisi
        _is_bist = ".IS" in ticker and not ticker.startswith("XU") and not ticker.startswith("X")
        # ".IS" eki var ama XU100/XBANK gibi endeks değil → gerçek hisse
        if not (".IS" in ticker and not ticker.upper().startswith("XU") and not ticker.upper().startswith("XBANK") and not ticker.upper().startswith("XBIST")):
            # BIST dışı (kripto, ABD, endeks, emtia)
            if "-USD" in ticker:
                return "NON_BIST"  # Kripto 7/24, ayrı yorum
            if "^" in ticker or ticker.startswith("XU") or "=F" in ticker:
                return "NON_BIST"
        # Finalize marker kontrolü — finalize_volume.py bugün çalıştıysa "FINAL_ISYATIRIM"
        try:
            _marker = os.path.join(CACHE_DIR, ".finalize_marker")
            if os.path.exists(_marker):
                with open(_marker, "r", encoding="utf-8") as _f:
                    _mdate = _f.read().strip()
                if _mdate == datetime.now(_TZ_ISTANBUL).strftime("%Y-%m-%d"):
                    # Sadece BIST hisseleri için geçerli
                    if ".IS" in ticker:
                        return "FINAL_ISYATIRIM"
        except Exception:
            pass
        # Saat tabanlı kalite — sadece BIST için
        if ".IS" not in ticker:
            return "FINAL"  # BIST dışı varsayılan
        _now = datetime.now(_TZ_ISTANBUL)
        # Hafta sonu veya tatil → dünkü kapanış değeri zaten final
        if _now.weekday() >= 5:
            return "FINAL"
        try:
            if _BIST_CAL_OK and not _bist_is_trading_day(_now):
                return "FINAL"
        except Exception:
            pass
        _now_min = _now.hour * 60 + _now.minute
        # BIST normal seans: 09:55–18:15 = 595–1095 dk
        if _now_min < 595:
            return "PRE_SESSION"
        if _now_min >= 1095:
            return "FINAL"
        if _now_min < 715:  # 09:55-11:55 (ilk 2 saat)
            return "PROJECTED_EARLY"
        if _now_min < 975:  # 11:55-16:15 (orta dilim)
            return "PROJECTED_MID"
        return "PROJECTED_LATE"  # 16:15-18:15 (son 2 saat)
    except Exception:
        return "FINAL"  # Hata varsa muhafazakar

@st.cache_data(ttl=600)
def calculate_synthetic_sentiment(ticker):
    try:
        df = get_safe_historical_data(ticker, period="6mo")
        if df is None or df.empty: return None

        # Hayalet bar temizliği (1 Haz 2026, v2): SADECE V=0 + O=H=L=C flat bar = tatil hayaleti.
        # Endeksler bazen V=0 dönüyor ama OHLC oynuyor — onlar legitimate, dokunma.
        # _strip_holiday_bars'ın trailing-only versiyonu; biz gömülü hayalet barları da yakalarız.
        if 'Volume' in df.columns:
            _v = df['Volume'].fillna(0)
            _o = df['Open']; _h = df['High']; _l = df['Low']; _c = df['Close']
            _flat = ((_o - _c).abs() < 1e-9) & ((_h - _c).abs() < 1e-9) & ((_l - _c).abs() < 1e-9)
            _ghost_mask = (_v <= 0) & _flat
            df = df[~_ghost_mask].copy()
            if df.empty:
                return None

        close = df['Close']; high = df['High']; low = df['Low']; volume = df['Volume']

        # --- İVME KARIŞIMI (9 Tem 2026) — eski DEMA6 konum formülünün yerine ---
        # bar = %50 STP eğimi + %50 kompozit puan (RSI+MFI+20g konum) değişimi.
        # TEK KAYNAK: indicators.compute_flow_momentum (bot/infografik/site de aynı).
        mf_smooth, stp = compute_flow_momentum(df)
        if mf_smooth is None:
            return None
        
        df = df.reset_index()
        # Index name kaybolmuş olabilir — 'Date', 'Datetime', 'index' gibi kontrol et
        _date_col = None
        for _dc in ('Date', 'Datetime', 'date', 'datetime'):
            if _dc in df.columns:
                _date_col = _dc
                break
        if _date_col:
            df['Date'] = pd.to_datetime(df[_date_col])
        else:
            # Fallback: index sütununu kullan veya RangeIndex'ten tarih üret
            _idx_cols = [c for c in df.columns if 'index' in str(c).lower() or df[c].dtype == 'datetime64[ns]']
            if _idx_cols:
                df['Date'] = pd.to_datetime(df[_idx_cols[0]])
            else:
                # Son çare: mevcut son satırdan geriye giderek tarih üret
                _end = pd.Timestamp.today().normalize()
                df['Date'] = pd.date_range(end=_end, periods=len(df), freq='B')
        
        # Hacim ortalaması (20g) — annotation katmanı için, ana hesaplamaya etki etmez
        _vol_avg20 = volume.rolling(20, min_periods=1).mean()

        plot_df = pd.DataFrame({
            'Date': df['Date'],
            'MF_Smooth': mf_smooth.values,
            'STP': stp.values,
            'Price': close.values,
            'Volume': volume.values,
            'Vol_Avg20': _vol_avg20.values,
        }).tail(30).reset_index(drop=True)

        plot_df['Date_Str'] = plot_df['Date'].dt.strftime('%d %b')
        # RVOL hesabı — annotation katmanı için
        plot_df['RVOL'] = (plot_df['Volume'] / plot_df['Vol_Avg20']).replace([np.inf, -np.inf], np.nan)
        return plot_df
    except Exception: return None

@st.cache_data(ttl=600)
def get_obv_divergence_status(ticker):
    """
    OBV ile Fiyat arasındaki uyumsuzluğu (Profesyonel SMA Filtreli) hesaplar.
    Dönüş: (Başlık, Renk, Açıklama)
    """
    try:
        # Periyodu biraz geniş tutuyoruz ki SMA20 hesaplanabilsin
        df = get_safe_historical_data(ticker, period="3mo") 
        if df is None or len(df) < 30: return ("Veri Yok", "#64748B", "Yetersiz veri.")
        
        # 1. OBV ve SMA Hesapla
        change = df['Close'].diff()
        direction = np.sign(change).fillna(0)
        obv = (direction * df['Volume']).cumsum()
        obv_sma = obv.rolling(20).mean()  # Güç filtresi

        # 1b. Para Akışı (20g) — OBV teyit katmanı
        # OBV sadece kapanış yönüne bakar; CMF bar içi dinamiği de ölçer
        # (fiyat günün üst yarısında mı kapandı, alt yarısında mı).
        cmf_20 = compute_cmf(df, period=20)
        # CMF eşikleri: ±0.05 standart (Chaikin'in orijinal önerisi)
        cmf_strong_pos = cmf_20 > 0.05    # Güçlü alış baskısı (bar içi)
        cmf_strong_neg = cmf_20 < -0.05   # Güçlü satış baskısı (bar içi)
        cmf_txt = (f" | Para Akışı: {cmf_20:+.3f}"
                   + (" ✓ alış teyitli" if cmf_strong_pos else
                      (" ✗ satış baskısı" if cmf_strong_neg else " (nötr)")))

        # 2. Dual-Window: 5g (kısa ivme) + 14g (RSI uyumlu orta vade)
        p_now     = float(df['Close'].iloc[-1])
        p_5       = float(df['Close'].iloc[-6])
        p_14      = float(df['Close'].iloc[-15])
        obv_now   = float(obv.iloc[-1])
        obv_5     = float(obv.iloc[-6])
        obv_14    = float(obv.iloc[-15])
        obv_sma_now = float(obv_sma.iloc[-1])

        obv_5g_up  = obv_now > obv_5    # Kısa vadeli ivme
        obv_14g_up = obv_now > obv_14   # Orta vadeli yön
        price_5g_up  = p_now > p_5
        price_14g_up = p_now > p_14
        is_obv_strong = obv_now > obv_sma_now

        # 12 Haz 2026 — Tek-mum dominance check
        # Bugünkü OBV katkısı, 5g deltasının >%60'ı ise dual-window okuması
        # şişirilmiş. Pozitif/güçlü etiketlere "(⚠ tek-mum ağırlıklı: %X)" eklenir.
        try:
            _obv_today = obv_now - float(obv.iloc[-2])
            _obv_5g_d  = obv_now - obv_5
            _spike_r   = (abs(_obv_today) / abs(_obv_5g_d)) if abs(_obv_5g_d) > 1e-9 else 0.0
            _same_dir  = (_obv_today > 0) == (_obv_5g_d > 0)
            _spike_dom = (_spike_r > 0.60) and _same_dir
            # >%95: "%190'ını oluşturdu" gibi kafa karıştıran yüzdeler yerine kelime (10 Tem 2026)
            _spike_pct_txt = "tamamına yakınını" if _spike_r >= 0.95 else f"%{_spike_r*100:.0f}'ini"
            _spike_tag = f"  (⚠ tek-mum ağırlıklı: bugünkü mum 5g akışın {_spike_pct_txt} oluşturdu — teyit bekleniyor)" if _spike_dom else ""
        except Exception:
            _spike_dom = False; _spike_tag = ""

        # 3. Karar Mekanizması — dual window çakışma mantığı
        # ── Kafa çevirme tespiti ──────────────────────────────────────────
        if obv_5g_up and not obv_14g_up:
            return ("🔄 OBV KAFA ÇEVİRİYOR (Toparlanma)", "#38bdf8",
                    "5 günlük OBV yönünü yukarı çevirdi ancak 14 günlük eğim hâlâ negatif. "
                    "Kısa vadeli alım baskısı başlamış, orta vade henüz teyit vermedi — erken toparlanma sinyali."
                    + cmf_txt)
        if not obv_5g_up and obv_14g_up:
            return ("🔄 OBV KAFA ÇEVİRİYOR (Zayıflama)", "#f59e0b",
                    "14 günlük OBV trendi hâlâ yukarı ama 5 günlük eğim aşağıya döndü. "
                    "Kısa vadede para çıkışı başlamış, orta vade bozulmadan önce erken uyarı."
                    + cmf_txt)

        # ── Güçlü sinyal: her iki pencere aynı yönde ─────────────────────
        if not price_5g_up and obv_5g_up and obv_14g_up:
            if is_obv_strong:
                # CMF teyiti varsa güçlü; CMF negatifse "Şüpheli" downgrade
                if cmf_strong_neg:
                    return ("⚠️ ŞÜPHELİ GİRİŞ (Para Akışı Çelişkili)", "#d97706",
                            "OBV'ye göre birikim var (5g+14g+SMA20 yukarı) ama Para Akışı "
                            "negatif — bar içi satış baskısı sürüyor. OBV'nin gördüğü 'giriş' büyük "
                            "ihtimalle gece/açılış hacmi; gün içi gerçek alış yok."
                            + cmf_txt)
                return ("🔥 GÜÇLÜ GİZLİ GİRİŞ" + (" ⚠ tek-mum" if _spike_dom else ""), "#16a34a",
                        "Hem 5 hem 14 günlük OBV yukarı — fiyat düşerken akıllı para iki periyotta da "
                        "birikimi sürdürüyor. OBV 20 günlük ortalamasını da aştı: güçlü akümülasyon sinyali."
                        + cmf_txt + _spike_tag)
            else:
                return ("👀 Olası Toplama (Zayıf)", "#d97706",
                        "5 ve 14 günlük OBV fiyat düşüşüne rağmen yükseliyor, ancak henüz 20 günlük "
                        "ortalamasını kesmedi. Birikim var ama büyük oyuncu onayı eksik."
                        + cmf_txt)

        if price_5g_up and not obv_5g_up and not obv_14g_up:
            return ("⚠️ GİZLİ ÇIKIŞ (Dağıtım)", "#f87171",
                    "Hem 5 hem 14 günlük OBV fiyat yükselişini onaylamıyor. "
                    "İki periyotta da para çıkışı sürüyor — yükseliş hacimsiz, dağıtım baskısı teyitli."
                    + cmf_txt)

        if is_obv_strong and obv_5g_up and obv_14g_up:
            p_yesterday = float(df['Close'].iloc[-2])
            if p_now < p_yesterday:
                # CMF güçlü pozitif ise emilim teyitli; negatif ise "False Strength"
                if cmf_strong_neg:
                    return ("⚠️ SAHTE GÜÇ (OBV-Para Akışı Çelişkisi)", "#f59e0b",
                            "OBV güçlü görünüyor ama Para Akışı negatif. Mum içi satıcı "
                            "baskın — OBV yanıltıcı (muhtemel gap/açılış etkisi). Gerçek bir "
                            "emilim sinyali değil."
                            + cmf_txt)
                return ("🛡️ DÜŞÜŞE DİRENÇ (Kurumsal Emilim)" + (" ⚠ tek-mum" if _spike_dom else ""), "#d97706",
                        "Bugün fiyat geri çekilse de hem 5 hem 14 günlük OBV yukarı ve 20 günlük "
                        "ortalamanın üzerinde. Panik satışları kurumsal alımla karşılanıyor."
                        + cmf_txt + _spike_tag)
            else:
                # CMF teyitli sağlıklı trend; CMF negatifse "Zayıf Teyit"
                if cmf_strong_neg:
                    return ("⚠️ ZAYIF TEYİT (OBV güçlü, Para Akışı zayıf)", "#f59e0b",
                            "OBV her iki pencerede yukarı ama Para Akışı negatif. "
                            "Yükselişin arkasında bar içi gerçek alıcı yok — kapanışlar günün "
                            "alt yarısında. Trend kırılgan."
                            + cmf_txt)
                return ("✅ SAĞLIKLI TREND (Hacim Onaylı)" + (" ⚠ tek-mum" if _spike_dom else ""), "#15803d",
                        "5 ve 14 günlük OBV fiyat yükselişini iki periyotta da teyit ediyor. "
                        "Trendin arkasında tutarlı akıllı para desteği var."
                        + cmf_txt + _spike_tag)

        return ("⚖️ ZAYIF İVME (Hacimsiz Bölge)", "#64748B",
                "OBV 20 günlük ortalamasının altında; 5 ve 14 günlük pencerede net yön yok. "
                "Fiyat hareketini destekleyecek kararlı bir para akışı görünmüyor."
                + cmf_txt)
            
    except: return ("Hesaplanamadı", "#64748B", "-")

def process_single_stock_stp(symbol, df):
    """
    Tek bir hissenin STP hesaplamasını yapar.
    """
    try:
        if df.empty or 'Close' not in df.columns: return None
        df = df.dropna(subset=['Close'])
        if len(df) < 200: return None

        close = df['Close']
        high = df['High']
        low = df['Low']
        volume = float(df['Volume'].iloc[-1]) if 'Volume' in df.columns else 0
        # --- YENİ EKLENEN: 20 Günlük Ortalamaya Göre Hacim Artış Katı ---
        avg_vol = float(df['Volume'].rolling(20).mean().iloc[-1]) if 'Volume' in df.columns and len(df) >= 20 else 1.0
        vol_ratio = volume / avg_vol if avg_vol > 0 else 1.0

        typical_price = (high + low + close) / 3
        stp = typical_price.ewm(span=6, adjust=False).mean()
        sma200 = close.rolling(200).mean()
        
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        c_last = float(close.iloc[-1]); c_prev = float(close.iloc[-2])
        s_last = float(stp.iloc[-1]); s_prev = float(stp.iloc[-2])
        
        result = None
        
        if c_prev <= s_prev and c_last > s_last:
            result = {
                "type": "cross_up",
                "data": {"Sembol": symbol, "Fiyat": c_last, "STP": s_last, "Fark": ((c_last/s_last)-1)*100, "Hacim": volume, "Hacim_Kat": vol_ratio}
            }
            sma_val = float(sma200.iloc[-1])
            rsi_val = float(rsi.iloc[-1])
            if (c_last > sma_val) and (20 < rsi_val < 70):
                result["is_filtered"] = True
            else:
                result["is_filtered"] = False

        # --- AŞAĞI KESİŞİM (SAT) ---
        elif c_prev >= s_prev and c_last < s_last:
            result = {
                "type": "cross_down",
                "data": {"Sembol": symbol, "Fiyat": c_last, "STP": s_last, "Fark": ((c_last/s_last)-1)*100, "Hacim": volume, "Hacim_Kat": vol_ratio}
            }

        # YUKARI TREND
        elif c_prev > s_prev and c_last > s_last:
            above = close > stp
            streak = (above != above.shift()).cumsum()
            streak_count = above.groupby(streak).sum().iloc[-1]
            
            result = {
                "type": "trend_up",
                "data": {
                    "Sembol": symbol, 
                    "Fiyat": c_last, 
                    "STP": s_last, 
                    "Fark": ((c_last/s_last)-1)*100,
                    "Gun": int(streak_count),
                    "Hacim": volume
                }
            }

        # --- YENİ: AŞAĞI TREND ---
        elif c_prev < s_prev and c_last < s_last:
            below = close < stp
            streak = (below != below.shift()).cumsum()
            streak_count = below.groupby(streak).sum().iloc[-1]
            
            result = {
                "type": "trend_down",
                "data": {
                    "Sembol": symbol, 
                    "Fiyat": c_last, 
                    "STP": s_last, 
                    "Fark": ((c_last/s_last)-1)*100,
                    "Gun": int(streak_count),
                    "Hacim": volume
                }
            }
            
        return result
    except Exception: return None

# ==============================================================================
# BÖLÜM 13 — KIRILIM TARAMALARI (BREAKOUT SCANNER)
# Agent3 erken kırılım tespiti ve onaylı kırılım taraması.
# ==============================================================================
def process_single_breakout(symbol, df):
    try:
        if df.empty or 'Close' not in df.columns: return None
        df = df.dropna(subset=['Close'])
        # Minimum veri şartı (EMA/SMA hesapları için)
        if len(df) < 50: return None 

        close = df['Close']; high = df['High']; low = df['Low']; open_ = df['Open']
        volume = df['Volume'] if 'Volume' in df.columns else pd.Series([1]*len(df))
        
        # --- 1. HACİM KONTROLÜ (Sabah koruması ana depoda halledildi) ---
        curr_vol_raw = float(volume.iloc[-1])
        curr_vol_projected = curr_vol_raw # Zaten ana depodan projeksiyonlu geldi
        
        vol_20 = volume.iloc[:-1].tail(20).mean()
        if pd.isna(vol_20) or vol_20 == 0: vol_20 = 1

        rvol = curr_vol_projected / vol_20
     
        # --- TEKNİK HESAPLAMALAR ---
        ema5 = close.ewm(span=5, adjust=False).mean()
        ema20 = close.ewm(span=20, adjust=False).mean()
        sma20 = close.rolling(20).mean(); sma50 = close.rolling(50).mean()
        
        # 👑 DÜZELTME BURADA: Artık iğnelere (high) değil, gövdelere (close) bakıyoruz!
        high_val = close.iloc[:-1].tail(45).max()
        curr_price = close.iloc[-1]
        
        # RSI
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]
        
        # --- OMI (OBV Momentum Index) — Volume momentum filtresi ---
        # OMI = EMA(OBV, 5) - EMA(OBV, 20), 50-bar StdDev ile normalize edilir.
        # < -0.5σ ise: fiyat kırılmaya hazır gibi görünüyor ama hacim momentum
        # negatif — fake breakout riski yüksek.
        try:
            _obv_change = close.diff()
            _obv_dir = np.sign(_obv_change).fillna(0)
            _obv_ser = (_obv_dir * volume).cumsum()
            _obv_ema5  = _obv_ser.ewm(span=5,  adjust=False).mean()
            _obv_ema20 = _obv_ser.ewm(span=20, adjust=False).mean()
            _omi_ser = _obv_ema5 - _obv_ema20
            _omi_std = float(_omi_ser.tail(50).std())
            omi_norm = float(_omi_ser.iloc[-1] / _omi_std) if (_omi_std and not pd.isna(_omi_std) and _omi_std > 0) else 0.0
        except Exception:
            omi_norm = 0.0
        omi_ok = omi_norm > -0.5   # -0.5σ altı = fake breakout riski

        # --- ŞARTLAR ---
        cond_ema = ema5.iloc[-1] > ema20.iloc[-1]
        cond_vol = rvol > 1.2
        cond_prox = (curr_price > high_val * 0.90) and (curr_price <= high_val * 1.1)
        cond_rsi = rsi < 70
        sma_ok = sma20.iloc[-1] > sma50.iloc[-1]

        if cond_ema and cond_vol and cond_prox and cond_rsi and omi_ok:
            
            sq_now, sq_prev = check_lazybear_squeeze_breakout(df)
            is_firing = sq_prev and not sq_now
            
            sort_score = rvol + (1000 if is_firing else 0)

            # Görsel Metin
            prox_pct = (curr_price / high_val) * 100
            
            if is_firing:
                prox_str = f"🚀 TETİKLENDİ"
            elif sq_now:
                prox_str = f"💣 Sıkışma Var"
            else:
                # DÜZELTME: Eğer fiyat zaten direnci (%100'ü) geçmişse ekranda KIRIYOR yazsın
                if prox_pct >= 100:
                    prox_str = f"%{prox_pct:.1f} (Direnç Üstü)"
                else:
                    prox_str = f"%{prox_pct:.1f}" + (" (Sınırda)" if prox_pct >= 98 else " (Hazırlık)")
            
            # Fitil Uyarısı
            body_size = abs(close.iloc[-1] - open_.iloc[-1])
            upper_wick = high.iloc[-1] - max(open_.iloc[-1], close.iloc[-1])
            is_wick_rejected = (upper_wick > body_size * 1.5) and (upper_wick > 0)
            wick_warning = " ⚠️ Satış Baskısı" if is_wick_rejected else ""
            
            if (curr_vol_raw < vol_20) and (rvol > 1.2):
                rvol_text = "Hız Yüksek (Proj.) 📈"
            else:
                rvol_text = "Olağanüstü 🐳" if rvol > 2.0 else "İlgi Artıyor 📈"
            # OMI teyit rozeti
            if omi_norm > 1.0:
                rvol_text += " · OMI ⚡"   # Güçlü volume momentum
            elif omi_norm > 0:
                rvol_text += " · OMI ✓"   # Pozitif teyit

            return { 
                "Sembol_Raw": symbol, 
                "Sembol_Display": symbol, 
                "Fiyat": f"{curr_price:.2f}", 
                "Zirveye Yakınlık": prox_str + wick_warning, 
                "Hacim Durumu": rvol_text, 
                "Trend Durumu": f"✅EMA | {'✅SMA' if sma_ok else '❌SMA'}", 
                "RSI": f"{rsi:.0f}", 
                "SortKey": sort_score,
                "Hacim": curr_vol_raw
            }
        return None
    except: return None

# ==============================================================================
# BÖLÜM 22 — ICT DERİN ANALİZ VE FİYAT HAREKETİ DNA
# Tekli hisse için tam ICT analizi: MSS, BOS, OB, FVG, Displacement.
# calculate_price_action_dna: 0-100 güven skoru, S&D bağlamı, haftalık mum ve öncelik sıralaması.
# ==============================================================================
def detect_ict_reversal(df):
    if len(df) < 40:
        return "NÖTR"
        
    recent_df = df.iloc[-40:]
    
    # 1. Fraktal Noktalarını Bul (Sweep tespiti için)
    highs, lows = [], []
    for i in range(2, len(recent_df)-2):
        if recent_df['High'].iloc[i] == max(recent_df['High'].iloc[i-2:i+3]):
            highs.append(recent_df['High'].iloc[i])
        if recent_df['Low'].iloc[i] == min(recent_df['Low'].iloc[i-2:i+3]):
            lows.append(recent_df['Low'].iloc[i])
            
    if len(highs) < 2 or len(lows) < 2:
        return "NÖTR"
        
    son_tepe = highs[-1]
    son_dip = lows[-1]
    onceki_dip = lows[-2]
    onceki_tepe = highs[-2]
    
    son_kapanis = recent_df['Close'].iloc[-1]
    son_acilis = recent_df['Open'].iloc[-1]
    
    # --- DISPLACEMENT (FİŞEK GİBİ FIRLAMA) HESAPLAMASI ---
    # Son mumun mutlak gövde büyüklüğü
    son_mum_govde = abs(son_kapanis - son_acilis)
    
    # Son 20 mumun ortalama gövde büyüklüğü
    ortalama_govde = abs(recent_df['Close'] - recent_df['Open']).rolling(20).mean().iloc[-1]
    
    # Son mumun hacmi ve son 20 mumun ortalama hacmi
    son_hacim = recent_df['Volume'].iloc[-1]
    ortalama_hacim = recent_df['Volume'].rolling(20).mean().iloc[-1]
    
    # Displacement Şartı: Gövde ortalamadan %50 büyük VE hacim ortalamadan %20 büyük olmalı!
    is_displacement = (son_mum_govde > ortalama_govde * 1.5) and (son_hacim > ortalama_hacim * 1.2)
    # --------------------------------------------------------------------

    # 🟢 BULLISH REVERSAL (Dipten Dönüş)
    is_ssl_swept = son_dip < onceki_dip
    is_bullish_mss = son_kapanis > son_tepe
    
    if is_ssl_swept and is_bullish_mss and is_displacement:
        return "BULLISH_MSS"
        
    # 🔴 BEARISH REVERSAL (Tepeden Dönüş)
    is_bsl_swept = son_tepe > onceki_tepe
    is_bearish_mss = son_kapanis < son_dip
    
    if is_bsl_swept and is_bearish_mss and is_displacement:
        return "BEARISH_MSS"
        
    return "NÖTR"

@st.cache_data(ttl=600)
def get_advanced_levels_data(ticker):
    """
    Arayüz için verileri paketler. (GÜNCELLENMİŞ: Güçlü Tip Dönüşümü)
    """
    df = get_safe_historical_data(ticker, period="1y")
    if df is None or df.empty: return None
    
    # 1. SuperTrend
    st_val, st_dir = calculate_supertrend(df)
    
    # 2. Fibonacci 
    fibs = calculate_fib_levels(df, st_dir=st_dir, period=120)
    
    close_s = df['Close'].iloc[:, 0] if isinstance(df['Close'], pd.DataFrame) else df['Close']
    curr_price = float(close_s.iloc[-1])
    
    # En yakın destek ve direnci bulma
    sorted_fibs = sorted(fibs.items(), key=lambda x: float(x[1]))
    support = (None, -999999.0)
    resistance = (None, 999999.0)
    
    # TAMPON BÖLGE (BUFFER) - Binde 2
    buffer = 0.002 
    
    for label, val in sorted_fibs:
        val = float(val)
        # Destek: Fiyatın altında kalan en büyük değer
        if val < curr_price and val > support[1]:
            support = (label, val)
            
        # Direnç: Fiyatın (ve tamponun) üzerinde kalan en küçük değer
        if val > (curr_price * (1 + buffer)) and val < resistance[1]:
            resistance = (label, val)
            
    if resistance[1] == 999999.0:
        resistance = ("UZAY BOŞLUĞU 🚀", curr_price * 1.15) 

    return {
        "st_val": float(st_val) if st_val else 0.0,
        "st_dir": st_dir,
        "fibs": fibs,
        "nearest_sup": support,
        "nearest_res": resistance,
        "curr_price": curr_price
    }

def build_erken_radar_prompt_text(er_data, quality=None):
    """
    Erken Radar evaluator çıktısını AI prompt için formatlar.
    er_data: evaluate_erken_radar() çıktısı
    quality: opsiyonel — wrapper'dan gelen 0-100 skor (yoksa er_data['overall_quality'])
    """
    if not er_data:
        return "Erken Radar verisi yok"
    if quality is None:
        quality = er_data.get('overall_quality', 0)
    primary  = er_data.get('primary')
    confirms = er_data.get('confirmations', []) or []
    red_flags = er_data.get('red_flags', []) or []
    matched_n = er_data.get('matched_count', 0)

    cat_label = {'A': 'GERİ DÖNÜŞ', 'B': 'SIKIŞMA', 'C': 'TREND DEVAMI', 'D': 'UYARI'}
    parts = []
    parts.append(f"📊 Kalite Skoru: {quality}/100 — Toplam {matched_n} senaryo eşleşti")
    if primary:
        stars = primary.get('stars', 0)
        sgs = '★' * (int(stars) if isinstance(stars, int) else 0)
        parts.append(f"\n🎯 ANA SENARYO [{primary['id']}] {primary['name']} ({sgs} — {cat_label.get(primary['category'], '')})")
        parts.append(f"   → {primary['description']}")
    else:
        if red_flags:
            parts.append("\n🎯 ANA SENARYO: Yok — sadece risk sinyalleri var")
        else:
            parts.append("\n🎯 ANA SENARYO: Yok — bu hisse Erken Radar kriterlerini tetiklemiyor")
    if confirms:
        parts.append(f"\n✅ EK TEYİTLER ({len(confirms)}):")
        for c in confirms:
            stars = c.get('stars', 0)
            sgs = '★' * (int(stars) if isinstance(stars, int) else 0)
            parts.append(f"   • [{c['id']}] {c['name']} ({sgs}) — {c['description']}")
    if red_flags:
        parts.append(f"\n⚠ RİSK UYARILARI ({len(red_flags)}):")
        for rf in red_flags:
            parts.append(f"   • [{rf['id']}] {rf['name']} — {rf['description']}")
    if matched_n >= 3 and not red_flags:
        parts.append(f"\n💡 ÇOKLU TEYİT: {matched_n} farklı senaryo aynı anda tetiklendi — güçlü sinyal")
    return "\n".join(parts)

# ==============================================================================
# BÖLÜM 30 — 8 MADDELİK HİBRİT YOL HARİTASI
# PA + Quant kombinasyonu. Çok zaman dilimli hizalama analizi ve
# 8 kriterli teknik yol haritası hesaplama motoru.
# ==============================================================================
@st.cache_data(ttl=600)
def calculate_multi_timeframe_alignment(ticker):
    """
    Multi-timeframe trend uyumu: 4H, Günlük, Haftalık, Aylık vadelerde
    Trend / Momentum (RSI) / Hacim yönlerini hesaplar.
    Returns: dict with 'matrix' (3x4 yön matrisi) ve 'overall_pct' (toplam uyum %)
    """
    try:
        # Vade verileri çek
        timeframes = {}
        try:
            _df_4h = _yf_download_with_retry(ticker, period="60d", interval="4h")
            if _df_4h is not None and not _df_4h.empty and len(_df_4h) > 30:
                if isinstance(_df_4h.columns, pd.MultiIndex):
                    _df_4h.columns = _df_4h.columns.get_level_values(0)
                timeframes['4H'] = _df_4h
        except Exception:
            pass

        _df_d = get_safe_historical_data(ticker, period="1y")
        if _df_d is not None and len(_df_d) > 50:
            timeframes['Günlük'] = _df_d
            # Günlük'ten haftalık ve aylık türet
            try:
                _df_w = _df_d.resample('W').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
                if len(_df_w) > 20:
                    timeframes['Haftalık'] = _df_w
                _df_m = _df_d.resample('ME').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
                if len(_df_m) > 8:
                    timeframes['Aylık'] = _df_m
            except Exception:
                pass

        if not timeframes:
            return None

        # Her vade için 3 sinyal hesapla
        matrix = {}
        for tf_name, df_tf in timeframes.items():
            try:
                c = df_tf['Close']; v = df_tf['Volume']

                # 1. Trend: SMA20 ve SMA50'nin yönü + fiyatın bunlara göre konumu
                sma20 = c.rolling(min(20, len(c)//2)).mean()
                sma50 = c.rolling(min(50, len(c)-1)).mean()
                _trend_dir = 0
                if c.iloc[-1] > sma20.iloc[-1]: _trend_dir += 1
                if c.iloc[-1] > sma50.iloc[-1]: _trend_dir += 1
                if sma20.iloc[-1] > sma20.iloc[-min(5, len(sma20)-1)]: _trend_dir += 1
                trend_sig = 1 if _trend_dir >= 2 else (-1 if _trend_dir == 0 else 0)

                # 2. Momentum (RSI 14)
                _delta = c.diff()
                _gain = (_delta.where(_delta > 0, 0)).rolling(14).mean()
                _loss = (-_delta.where(_delta < 0, 0)).rolling(14).mean()
                _rsi = (100 - (100 / (1 + _gain/_loss))).iloc[-1]
                if pd.isna(_rsi): _rsi = 50
                if _rsi >= 60:    mom_sig = 1
                elif _rsi <= 40:  mom_sig = -1
                else:             mom_sig = 0

                # 3. Hacim: Son 5 mum hacmi vs son 20 mum ortalaması
                _v5 = v.tail(5).mean()
                _v20 = v.rolling(20).mean().iloc[-1]
                if pd.isna(_v20) or _v20 == 0:
                    vol_sig = 0
                else:
                    _ratio = _v5 / _v20
                    if _ratio >= 1.2:   vol_sig = 1
                    elif _ratio <= 0.7: vol_sig = -1
                    else:               vol_sig = 0

                matrix[tf_name] = {"trend": trend_sig, "momentum": mom_sig, "hacim": vol_sig}
            except Exception:
                matrix[tf_name] = {"trend": 0, "momentum": 0, "hacim": 0}

        # Toplam uyum %: kaç hücre aynı yöne işaret ediyor (en baskın yön)
        all_signals = [s for tf in matrix.values() for s in tf.values()]
        if not all_signals:
            return None
        bull_cnt = sum(1 for s in all_signals if s == 1)
        bear_cnt = sum(1 for s in all_signals if s == -1)
        total = len(all_signals)
        dominant = "YUKARI" if bull_cnt > bear_cnt else ("AŞAĞI" if bear_cnt > bull_cnt else "KARARSIZ")
        dominant_count = max(bull_cnt, bear_cnt)
        overall_pct = round(dominant_count / total * 100) if total > 0 else 0

        return {
            "matrix":         matrix,
            "timeframes":     list(matrix.keys()),
            "overall_pct":    overall_pct,
            "dominant":       dominant,
            "bull_cnt":       bull_cnt,
            "bear_cnt":       bear_cnt,
            "total":          total,
        }
    except Exception:
        return None

@st.cache_data(ttl=600, show_spinner=False)
def calculate_8_point_roadmap(ticker, cat=None):
    """Fiyat davranışı (PA), VSA, Hacim ve Trend algoritmalarını birleştiren sentez model.
    cat: RS karşılaştırması için kategori (benchmark seçimi). @st.cache_data anahtarına
    dahil edilsin diye parametre olarak alınır; None ise 'BIST' varsayılır (session_state
    okuması cache'li gövde içine sızmasın)."""
    try:
        df = get_safe_historical_data(ticker, period="1y")
        if df is None or len(df) < 200: return None

        c = df['Close']; h = df['High']; l = df['Low']; o = df['Open']; v = df['Volume']
        cp = float(c.iloc[-1])

        clean_ticker = ticker.split('.')[0].replace("=F", "").replace("-USD", "")
        def fmt(val): return f"{int(val):,}" if val >= 1000 else f"{val:.2f}"

        # ---------------------------------------------------------
        # ORTAK MATEMATİKSEL DEĞİŞKENLER (EN TEPEDE TANIMLANDI)
        # ---------------------------------------------------------
        sma200 = c.rolling(200).mean().iloc[-1]
        sma50 = c.rolling(50).mean().iloc[-1]
        atr = (h - l).rolling(14).mean().iloc[-1]

        # EMA5/8/13 serisi (hizalama tespiti için tam seri gerekli)
        _ema5_s  = c.ewm(span=5,  adjust=False).mean()
        _ema8_s  = c.ewm(span=8,  adjust=False).mean()
        _ema13_s = c.ewm(span=13, adjust=False).mean()
        _ema_bull_now = _ema5_s.iloc[-1] > _ema8_s.iloc[-1] > _ema13_s.iloc[-1]
        _ema_bear_now = _ema5_s.iloc[-1] < _ema8_s.iloc[-1] < _ema13_s.iloc[-1]
        _ema_streak = 0
        for _i in range(len(c) - 1, max(len(c) - 120, 0) - 1, -1):
            if _ema_bull_now and _ema5_s.iloc[_i] > _ema8_s.iloc[_i] > _ema13_s.iloc[_i]:
                _ema_streak += 1
            elif _ema_bear_now and _ema5_s.iloc[_i] < _ema8_s.iloc[_i] < _ema13_s.iloc[_i]:
                _ema_streak += 1
            else:
                break
        
        # Hacim Güvenlik Duvarı (Yahoo Bug Koruması)
        curr_vol = float(v.iloc[-1])
        if curr_vol <= 100 and len(v) > 1: 
            curr_vol = float(v.iloc[-2])
            
        avg_vol = v.rolling(20).mean().iloc[-1]
        vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 1
        
        res_20 = h.tail(20).max()
        sup_20 = l.tail(20).min()

        # Mum Geometrisi
        alt_fitil = min(o.iloc[-1], c.iloc[-1]) - l.iloc[-1]
        govde = abs(c.iloc[-1] - o.iloc[-1])
        std_20 = c.rolling(20).std().iloc[-1]
        
        # Z-Score (Pylance Hatasının Sebebi - Eksikti, eklendi)
        z_score = (cp - c.rolling(20).mean().iloc[-1]) / std_20 if std_20 > 0 else 0

        # RSI (Pylance Hatasının Sebebi - En tepeye alındı)
        delta_rsi = c.diff()
        gain_rsi = (delta_rsi.where(delta_rsi > 0, 0)).rolling(14).mean()
        loss_rsi = (-delta_rsi.where(delta_rsi < 0, 0)).rolling(14).mean()
        rsi_val = (100 - (100 / (1 + gain_rsi/loss_rsi))).iloc[-1]

        # ---------------------------------------------------------
        # MADDELERİN HESAPLANMASI
        # ---------------------------------------------------------

        # --- 1. FİYAT DAVRANIŞI VE MUM FORMASYONLARI (Son 1-3 Gün) ---
        pa_data = calculate_price_action_dna(ticker)
        mum_formasyonu = pa_data['candle']['desc'] if pa_data else "Belirgin formasyon yok"
        
        if c.iloc[-1] < o.iloc[-1] and govde > atr*0.5:
            m1_mum = "Kırmızı (Satış baskısı)"
        elif c.iloc[-1] > o.iloc[-1] and govde > atr*0.5:
            m1_mum = "Yeşil (Alıcı momentumu)"
        else:
            m1_mum = "Konsolidasyon / Kararsız"
            
        if _ema_bull_now:
            _ema_align_html = (f'<b>EMA Hizalaması:</b> <span style="color:#16a34a;font-weight:700;">▲ Yukarı Hizalı</span>'
                               f'<span style="color:#64748b;font-size:0.7em;"> ({_ema_streak} gün)</span>')
        elif _ema_bear_now:
            _ema_align_html = (f'<b>EMA Hizalaması:</b> <span style="color:#f87171;font-weight:700;">▼ Aşağı Hizalı</span>'
                               f'<span style="color:#64748b;font-size:0.7em;"> ({_ema_streak} gün)</span>')
        else:
            _ema_align_html = '<b>EMA Hizalaması:</b> <span style="color:#d97706;font-weight:700;">⇄ Karışık</span>'
        m1 = f"<b>Günlük Mum:</b> {m1_mum}<br><b>PA Sinyali:</b> {mum_formasyonu}<br>{_ema_align_html}"

        # --- 2. FORMASYON TESPİTİ (1-6 Ay) ---
        pat_df = pd.DataFrame()
        try: pat_df = scan_chart_patterns([ticker])
        except: pass
        
        if not pat_df.empty:
            pat_name  = pat_df.iloc[0]['Formasyon']
            chart_dat = pat_df.iloc[0].get('ChartData', None)
            # 6 Tem 2026 — TEK KAYNAK FİYAT: formasyon panelinin fiyatı, seviyelerle AYNI
            # taramadan (aynı df'in son kapanışı) gelmeli. Ayrı current_price kullanınca
            # ölçek ayrışıp (XU100: seviye ~10 vs fiyat 14424) yüzde -%99.9 çıkıyordu.
            _m2_scan_price = float(pat_df.iloc[0].get('Fiyat', 0) or 0) or None
            _hint = ('<br><span style="color:#38bdf8;font-weight:600;font-size:11px;">'
                     '🔍 Detaylı grafik ve kilit seviyeleri için aşağıdaki butona tıklayın</span>') if chart_dat else ""
            # 9 Haz 2026 Oturum 20 — BREAKOUT rozeti
            _bk_badge = ""
            try:
                if isinstance(chart_dat, dict) and 'breakout_state' in chart_dat:
                    _bk_st = int(chart_dat.get('breakout_state', 0) or 0)
                    _bk_vr = float(chart_dat.get('breakout_vol_ratio', 1.0) or 1.0)
                    if _bk_st == 3:
                        _bk_badge = ('<br><span style="background:linear-gradient(90deg,#ec4899,#a855f7);'
                                     'color:#fff;font-weight:700;font-size:11px;padding:2px 8px;border-radius:6px;">'
                                     f'🚀💨 BREAKAWAY GAP · {_bk_vr:.1f}× hacim</span>')
                    elif _bk_st == 2:
                        _bk_badge = ('<br><span style="background:linear-gradient(90deg,#22d3ee,#3b82f6);'
                                     'color:#fff;font-weight:700;font-size:11px;padding:2px 8px;border-radius:6px;">'
                                     f'🚀 KIRILIM · {_bk_vr:.1f}× hacim</span>')
                    elif _bk_st == 1:
                        _bk_badge = ('<br><span style="background:#1f2937;color:#fcd34d;'
                                     'font-weight:600;font-size:11px;padding:2px 8px;border-radius:6px;'
                                     'border:1px solid #d97706;">⏳ BOUNDARY TEST</span>')
            except Exception:
                pass
            m2 = (f"<b>Mevcut Formasyon:</b> {pat_name}<br>"
                  f"<b>Ana Yapı:</b> {'İtki (Trend)' if cp > sma50 else 'Düzeltme (Pullback)'}{_bk_badge}{_hint}")
        else:
            chart_dat = None
            _m2_scan_price = None
            m2 = f"<b>Mevcut Formasyon:</b> Kitabi bir yapı görünmüyor.<br><b>Ana Yapı:</b> {'İtki (Trend)' if cp > sma50 else 'Düzeltme Fazı'}"

        # --- 3. EFOR VS SONUÇ (VSA) ---
        if vol_ratio > 1.5 and govde < atr * 0.4:
            vsa = "⚠️ Anomalilik (Churning)"
            vsa_desc = "Hacim yüksek (Efor) ama fiyat gitmiyor (Sonuç yok). Gizli karşılayıcı (Emilim) var."
        elif vol_ratio > 1.1 and govde > atr * 0.8:
            vsa = "✅ Sağlıklı İtki"
            vsa_desc = "Geniş gövde ve yüksek hacim. Efor ve Sonuç uyumlu."
        elif vol_ratio < 0.75:
            vsa = "💤 Sığ Piyasa"
            vsa_desc = "İlgi düşük, satıcılar/alıcılar isteksiz."
        else:
            vsa = "Standart Akış"
            vsa_desc = "Hacim ve fiyat hareketi ortalamalarla uyumlu seyrediyor."
            
        m3 = f"<b>VSA Tespiti:</b> {vsa}<br><b>Durum:</b> {vsa_desc}"

        # --- 4. TREND SKORU (Enerji Birikimi) ---
        sq_puan = 8 if std_20 < atr else 4
        vol_puan = 7 if vol_ratio < 0.8 else 4
        ema_puan = 8 if abs(cp - sma50)/sma50 < 0.02 else 5
        # Max toplam = 8+7+8 = 23 → /8 skalasına normalize
        enerji_skor = (sq_puan + vol_puan + ema_puan) / 23 * 8
        def _m4_bar(label, val, max_val):
            # Renk veriye göre değişken (9 Haz 2026 fix): mor sabit yerine severity rengi
            pct = (val / max_val) * 100
            if pct >= 65:   _bar_rgb = "74,222,128"   # yeşil
            elif pct >= 40: _bar_rgb = "251,191,36"   # sarı
            else:           _bar_rgb = "248,113,113"  # kırmızı
            return (
                f"<div style='margin-bottom:3px;'>"
                f"<div style='background:rgba({_bar_rgb},0.13);border-radius:3px;height:16px;position:relative;overflow:hidden;'>"
                f"<div style='width:{pct:.0f}%;background:rgba({_bar_rgb},0.78);height:100%;border-radius:3px;"
                f"box-shadow:inset 0 0 4px rgba(255,255,255,0.18);'></div>"
                f"<span style='position:absolute;left:0;top:0;width:100%;height:100%;display:flex;align-items:center;"
                f"justify-content:space-between;padding:0 7px;box-sizing:border-box;'>"
                # Label: beyaz + güçlü shadow (her arka planda okunur)
                f"<span style='font-size:0.66rem;font-weight:800;color:#fff;"
                f"text-shadow:0 1px 2px rgba(0,0,0,0.85),0 0 3px rgba(0,0,0,0.7);'>{label}</span>"
                # Skor: beyaz + güçlü shadow (her zaman okunur)
                f"<span style='font-size:0.7rem;font-weight:900;color:#fff;"
                f"text-shadow:0 1px 2px rgba(0,0,0,0.85),0 0 3px rgba(0,0,0,0.7);"
                f"font-family:\"JetBrains Mono\",monospace;'>{val}/{max_val}</span>"
                f"</span></div></div>"
            )
        m4 = _m4_bar("Sıkışma", sq_puan, 8) + _m4_bar("Daralma", vol_puan, 8) + _m4_bar(f"Enerji", round(enerji_skor, 1), 8)

        # --- 5. HACİM ALGORİTMASI (Kurumsal Ayak İzi) ---
        m5_abs = f"<b>Absorption:</b> {fmt(sup_20)} bandında alım ihtimali." if alt_fitil > atr*0.5 else "<b>Absorption:</b> Belirgin bir kurumsal emilim yok."
        if c.iloc[-1] < o.iloc[-1] and vol_ratio > 1.3:
            m5_agresif = "<b>Akış:</b> Düşüşte algoritmik dağıtım/satış."
        elif c.iloc[-1] > o.iloc[-1] and vol_ratio > 1.3:
            m5_agresif = "<b>Akış:</b> Yükselişte agresif kurumsal alım."
        else:
            m5_agresif = "<b>Akış:</b> Standart / Pasif işlem hacmi."
        m5 = f"{m5_abs}<br>{m5_agresif}"

        # --- 6. YÖN BEKLENTİSİ (~1 Aylık Momentum) ---
        # Base: RSI katkısı + SMA50 pozisyonu (orijinal)
        boga_w = 50 + (rsi_val - 50) * 0.5 + (15 if cp > sma50 else -15)
        # MACD katkısı (±5): momentum yön onayı
        _ema12 = c.ewm(span=12, adjust=False).mean()
        _ema26 = c.ewm(span=26, adjust=False).mean()
        _macd_l = _ema12 - _ema26
        _macd_s = _macd_l.ewm(span=9, adjust=False).mean()
        boga_w += 5 if _macd_l.iloc[-1] > _macd_s.iloc[-1] else -5
        # OBV katkısı (±5): kurumsal akış yönü
        _obv = (v * c.diff().fillna(0).apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))).cumsum()
        _obv_sma = _obv.rolling(20).mean()
        boga_w += 5 if _obv.iloc[-1] > _obv_sma.iloc[-1] else -5
        # RS katkısı (±5): son 20 günde endekse göre göreli güç
        try:
            _cat = cat if cat is not None else 'BIST'
            _bench = get_benchmark_data(_cat)
            if _bench is not None and len(_bench) >= 21:
                _bench_ret = float(_bench.iloc[-1]) / float(_bench.iloc[-21]) - 1
                _stock_ret = float(c.iloc[-1]) / float(c.iloc[-21]) - 1
                boga_w += 5 if _stock_ret > _bench_ret else -5
        except: pass
        boga_w = int(min(max(boga_w, 15), 85))
        ayi_w  = 100 - boga_w
        if boga_w >= 65:   kisa_baski = "Alıcılar baskın, momentum güçlü."
        elif boga_w >= 50: kisa_baski = "Alıcılar hafif önde, trend desteği var."
        elif boga_w >= 35: kisa_baski = "Satış baskısı hafif üstün, dikkatli ol."
        else:              kisa_baski = "Satıcılar baskın, momentum zayıf."
        m6 = (
            f"<div style='background:rgba(100,116,139,0.12);border-radius:4px;height:18px;"
            f"position:relative;overflow:hidden;margin-bottom:4px;'>"
            f"<div style='width:{boga_w}%;background:rgba(22,163,74,0.78);height:100%;"
            f"border-radius:4px 0 0 4px;position:absolute;left:0;top:0;'></div>"
            f"<div style='width:{ayi_w}%;background:rgba(248,113,113,0.75);height:100%;"
            f"border-radius:0 4px 4px 0;position:absolute;right:0;top:0;'></div>"
            f"<span style='position:absolute;left:5px;top:50%;transform:translateY(-50%);"
            f"font-size:0.62rem;font-weight:800;color:#fff;'>🐂 %{boga_w}</span>"
            f"<span style='position:absolute;right:5px;top:50%;transform:translateY(-50%);"
            f"font-size:0.62rem;font-weight:800;color:#fff;'>🐻 %{ayi_w}</span>"
            f"</div>"
            f"<div style='font-size:0.65rem;line-height:1.3;'>{kisa_baski}</div>"
        )

        # --- 7. AYI BOĞA SENARYOLARI (Kompakt ve Hedefli) ---
        h1 = res_20 + (atr*1.5)
        a1 = sup_20 - (atr*1.5)
        m7 = f"<b>Boğa Olması İçin:</b> {fmt(res_20)} yukarı geçilmeli | <b>Sonraki Hedef:</b> {fmt(h1)}<br><b>Ayı Olması İçin:</b> {fmt(sup_20)} aşağıya kırılmalı | <b>Sonraki Hedef:</b> {fmt(a1)}"

        # --- 8. TEKNİK ÖZET (GELİŞMİŞ SENTEZ MOTORU) ---
        is_macro_bull = cp > sma200
        is_micro_bull = cp > sma50
        
        is_overheated = (z_score >= 1.5) or (rsi_val > 75)
        is_oversold = (z_score <= -1.5) or (rsi_val < 30)
        is_churning = "Churning" in vsa
        
        is_accumulation = (alt_fitil > atr * 0.5) or ("Yeşil" in m1_mum and z_score < -1)

        if is_macro_bull and is_micro_bull:
            if is_overheated or is_churning:
                ozet_metin = f"Makro yapı güçlü olsa da, kısa vadede aşırı ısınma ve yorulma (Dağıtım) emareleri göze çarpıyor. Hacim tarafındaki '{vsa.split()[1]}' durumu kurumsal kâr satışlarına işaret edebilir. Yeni alımlar yüksek riskli olup, {fmt(sup_20)} desteği ana stop hattı olarak izlenmelidir."
            elif enerji_skor > 6.5:
                ozet_metin = f"Kusursuz bir yükseliş ivmesi! Fiyat {fmt(sma50)} (SMA50) üzerinde güvenle taşınırken, {enerji_skor:.1f}/10'luk enerji skoru yeni bir patlamaya işaret ediyor. {fmt(res_20)} kilit direnci hacimle aşıldığı an 'Fiyat Keşfi' (Price Discovery) fazı tetiklenecektir."
            else:
                ozet_metin = f"Ana trend yukarı yönlü (Boğa %{boga_w}), ancak mevcut seviyelerde momentum yataya sarmış durumda. Trendin yeni bir dalga başlatması için {fmt(res_20)} üzerinde taze kurumsal hacme ihtiyaç var. Aşağıda {fmt(sup_20)} korundukça trend yapısı güvendedir."
                
        elif not is_macro_bull:
            if is_oversold or is_accumulation:
                ozet_metin = f"Fiyat makro ortalamaların altında ezilse de, aşırı satım bölgesinden gelen tepkiler dikkat çekici. VSA ve mum fitilleri, buraların bir 'Akümülasyon' (Gizli Mal Toplama) alanı olabileceğini fısıldıyor. {fmt(res_20)} direncinin alınması, trendi resmen tersine çevirecektir."
            elif enerji_skor > 6.5:
                ozet_metin = f"Satıcıların mutlak hakimiyeti sürüyor ve aşağı yönlü tehlikeli bir enerji birikimi ({enerji_skor:.1f}/10) mevcut. {fmt(sup_20)} desteği çökerse panik satışları şelaleye dönüşebilir. Mevcut yapıda uzun (Long) yönlü denemeler 'Düşen bıçağı tutmak' anlamına gelir."
            else:
                ozet_metin = f"Baskılı piyasa yapısı sürüyor, belirgin bir hacim efor artışı görülmüyor. {fmt(res_20)} seviyesi kısa vadeli teknik eşik olarak öne çıkıyor — bu seviyenin hacimle yukarı geçilmesi yapıyı güçlendirir, geçilememesi durumunda zayıflama devam edebilir."
                
        else: # Karmaşık durum
            if is_oversold:
                ozet_metin = f"Uzun vadeli ana trend pozitif olsa da (SMA200 üstü), kısa vadede sert bir düzeltme (Pullback) yaşanıyor. Fiyat iskontolu (Ucuz) bölgelere inmiş durumda. {fmt(sup_20)} seviyesinden gelecek bir 'V-Dönüş' reaksiyonu harika bir fırsat sunabilir."
            else:
                ozet_metin = f"Fiyat {fmt(sup_20)} ile {fmt(res_20)} arasında sıkışmış, yön arayışında olan bir testere (Choppy) piyasasında. Ne alıcılar ne de satıcılar tam kontrol sağlayabilmiş değil. Kırılım yönü teknik olarak belirleyici olacaktır."
            
        m8 = f"<b>Piyasa Sentezi:</b> {ozet_metin}"

        # ── STATUS hesapları (renk kodlaması için) ────────────────────
        s1 = "bull" if "Yeşil" in m1_mum else ("bear" if "Kırmızı" in m1_mum else "neutral")
        s2 = ("bull" if (not pat_df.empty and cp > sma50)
              else "warning" if (not pat_df.empty and cp <= sma50)
              else "neutral")
        s3 = ("bull" if "Sağlıklı İtki" in vsa
              else "warning" if "Churning" in vsa or "Anomali" in vsa
              else "neutral")
        s4 = "bull" if enerji_skor > 6 else ("warning" if enerji_skor > 4 else "bear")
        s5 = ("bull" if "agresif kurumsal alım" in m5_agresif
              else "bear" if "algoritmik dağıtım" in m5_agresif
              else "neutral")
        s6 = "bull" if boga_w >= 60 else ("warning" if boga_w >= 45 else "bear")
        s7 = "neutral"
        s8 = ("bull" if (is_macro_bull and is_micro_bull and not is_overheated and not is_churning)
              else "bear" if (not is_macro_bull and not is_oversold and not is_accumulation)
              else "warning")

        # ── COMPOSITE TECHNICAL SCORE (5 alt faktör → tek skor 0-100) ──
        # 5 faktör: Trend (uzun vade), Momentum (RSI+ivme), Hacim (akış+absorption), Yapı (formasyon+fitil), Senaryo (yön+sentez)
        def _norm_status(s):
            """status string'i 0-100'e çevir."""
            return {"bull": 100, "warning": 50, "neutral": 50, "bear": 0}.get(s, 50)

        # Faktör 1: TREND (uzun vade — SMA50/SMA200 + EMA hizalama)
        _f_trend = 0
        if cp > sma200: _f_trend += 35
        if cp > sma50:  _f_trend += 30
        if _ema_bull_now: _f_trend += 25
        elif _ema_bear_now: _f_trend -= 15
        _f_trend = max(0, min(100, _f_trend + 10))  # baseline +10

        # Faktör 2: MOMENTUM (RSI + Yön Beklentisi'ndeki boğa ağırlığı)
        _rsi_score = 50
        if 45 <= rsi_val <= 65:   _rsi_score = 80   # ideal sağlıklı momentum
        elif 65 < rsi_val <= 75:  _rsi_score = 65   # güçlü ama riskli
        elif rsi_val > 75:        _rsi_score = 40   # aşırı alım
        elif 35 <= rsi_val < 45:  _rsi_score = 55   # nötr alt
        elif rsi_val < 35:        _rsi_score = 30   # zayıf
        _f_momentum = round(0.5 * _rsi_score + 0.5 * boga_w)

        # Faktör 3: HACİM (VSA + agresif akış + ratio)
        _f_volume = 50
        if "Sağlıklı İtki" in vsa: _f_volume += 25
        elif "Churning" in vsa or "Anomali" in vsa: _f_volume -= 15
        if "agresif kurumsal alım" in m5_agresif: _f_volume += 20
        elif "algoritmik dağıtım" in m5_agresif: _f_volume -= 20
        if vol_ratio >= 1.5: _f_volume += 10
        elif vol_ratio < 0.7: _f_volume -= 5
        _f_volume = max(0, min(100, _f_volume))

        # Faktör 4: YAPI (Formasyon + Stopping Volume + alt fitil = absorption)
        _f_yapi = 50
        if not pat_df.empty: _f_yapi += 20
        if alt_fitil > atr * 0.5 and "Yeşil" in m1_mum: _f_yapi += 20  # alttan toplama
        if "Kırmızı" in m1_mum and govde > atr * 0.7: _f_yapi -= 15   # güçlü satıcı
        _f_yapi = max(0, min(100, _f_yapi))

        # Faktör 5: SENARYO (Card 8 sentezinden — makro+mikro+overheat)
        _f_senaryo = _norm_status(s8)

        # Composite (ağırlıklı ortalama)
        _w = {"trend": 0.30, "momentum": 0.25, "volume": 0.20, "yapi": 0.15, "senaryo": 0.10}
        composite_score = round(
            _f_trend * _w["trend"] + _f_momentum * _w["momentum"] +
            _f_volume * _w["volume"] + _f_yapi * _w["yapi"] +
            _f_senaryo * _w["senaryo"]
        )

        # Karar etiketi
        if composite_score >= 70:   _comp_decision, _comp_color = "POZİTİF", "#16a34a"
        elif composite_score >= 55: _comp_decision, _comp_color = "DİKKAT/İZLE", "#ca8a04"
        elif composite_score >= 40: _comp_decision, _comp_color = "BEKLEMEDE", "#d97706"
        else:                       _comp_decision, _comp_color = "NEGATİF", "#f87171"

        return {
            "M1": m1, "M2": m2, "M3": m3, "M4": m4, "M5": m5, "M6": m6, "M7": m7, "M8": m8,
            "M2_chart_data": chart_dat,
            "M2_scan_price": _m2_scan_price,   # 6 Tem — seviyelerle aynı ölçekteki fiyat (tek kaynak)
            "S": [s1, s2, s3, s4, s5, s6, s7, s8],
            "composite_score":    composite_score,
            "comp_decision":      _comp_decision,
            "comp_color":         _comp_color,
            "factor_scores":      {"trend": _f_trend, "momentum": _f_momentum,
                                   "volume": _f_volume, "yapi": _f_yapi, "senaryo": _f_senaryo},
            # Trade plan için ham veriler (Phase E'de Card C'de kullanılacak)
            "tp_curr_price":      cp,
            "tp_stop_5g":         float(l.tail(5).min()),
            "tp_stop_20g":        float(sup_20),
            "tp_target_20g":      float(res_20),
            "tp_target_atr":      float(cp + 2 * atr),
            "tp_atr":             float(atr),
        }
    except Exception as e:
        return None

@st.cache_data(ttl=3600, show_spinner=False)
def compute_weekly_frame(ticker):
    """Hafta sonu büyük-resim çerçevesi. Dict döner (5 blok + ai_block) veya None."""
    try:
        df_d = get_safe_historical_data(ticker, period="2y")
    except Exception:
        df_d = None
    if df_d is None or len(df_d) < 80 or 'Close' not in df_d.columns:
        return None
    try:
        df_w = df_d.resample('W-FRI').agg(
            {'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}
        ).dropna()
    except Exception:
        return None
    if len(df_w) < 30:
        return None

    cw = df_w['Close']; hw = df_w['High']; lw = df_w['Low']; ow = df_w['Open']
    n = len(df_w)
    last = float(cw.iloc[-1])
    UP = "#10b981"; DN = "#f87171"; NEU = "#f59e0b"; MUT = "#94a3b8"

    # ── Blok 1: KONUM (uzun vade) ────────────────────────────────────────────
    w50 = cw.rolling(min(50, n - 1)).mean()
    above50 = last > float(w50.iloc[-1])
    slope50 = float(w50.iloc[-1]) - float(w50.iloc[-min(8, n - 1)])
    win52 = df_w.tail(52)
    hi52 = float(win52['High'].max()); lo52 = float(win52['Low'].min())
    pos52 = ((last - lo52) / (hi52 - lo52) * 100) if hi52 > lo52 else 50.0
    if above50 and slope50 > 0:
        pos_lbl, pos_clr = "Uzun vade YUKARI", UP
        pos_det = (f"Fiyat 50-haftalık ortalamanın üzerinde ve ortalama yükseliyor — ana eğilim "
                   f"alıcıda. 52-hafta bandında %{pos52:.0f} seviyesinde.")
    elif (not above50) and slope50 < 0:
        pos_lbl, pos_clr = "Uzun vade AŞAĞI", DN
        pos_det = (f"Fiyat 50-haftalık ortalamanın altında ve ortalama düşüyor — ana eğilim "
                   f"satıcıda. 52-hafta bandında %{pos52:.0f} seviyesinde.")
    else:
        pos_lbl, pos_clr = "Uzun vade YATAY/KARARSIZ", NEU
        pos_det = (f"Fiyat ile 50-haftalık ortalama net yön vermiyor — denge bölgesi. "
                   f"52-hafta bandında %{pos52:.0f}.")

    # ── Blok 2: DOW birincil trend (haftalık salınım yapısı) ─────────────────
    k = 2
    sh = []; sl = []
    _h = hw.values; _l = lw.values
    for i in range(k, n - k):
        if _h[i] == _h[i - k:i + k + 1].max(): sh.append((i, float(_h[i])))
        if _l[i] == _l[i - k:i + k + 1].min(): sl.append((i, float(_l[i])))
    dow_lbl, dow_clr = "Belirsiz / Yatay", NEU
    dow_det = "Haftalık salınım yapısı net bir birincil yön vermiyor."
    if len(sh) >= 2 and len(sl) >= 2:
        hh = sh[-1][1] > sh[-2][1]; hl = sl[-1][1] > sl[-2][1]
        lh = sh[-1][1] < sh[-2][1]; ll = sl[-1][1] < sl[-2][1]
        if hh and hl:
            dow_lbl, dow_clr = "Birincil YÜKSELİŞ", UP
            dow_det = ("Dow teorisi: yükselen tepe + yükselen dip → birincil trend yukarı. "
                       "Düzeltmeler alım fırsatı sayılır.")
        elif lh and ll:
            dow_lbl, dow_clr = "Birincil DÜŞÜŞ", DN
            dow_det = ("Dow teorisi: alçalan tepe + alçalan dip → birincil trend aşağı. "
                       "Yükselişler satış/tepki sayılır.")
        else:
            dow_lbl, dow_clr = "Geçiş / Yatay", NEU
            dow_det = ("Dow teorisi: tepe-dip yapısı karışık → trend geçiş aşamasında, yön belirsiz.")

    # ── Blok 3: PİYASA YAPISI (son kırılım) ──────────────────────────────────
    conf_sh = [p for (i, p) in sh if i <= n - 1 - k]
    conf_sl = [p for (i, p) in sl if i <= n - 1 - k]
    struct_lbl, struct_clr = "Bant içinde", MUT
    struct_det = "Fiyat son salınım tepe/dibi arasında — yapı korunuyor, kırılım yok."
    if conf_sh and last > max(conf_sh[-2:]):
        struct_lbl, struct_clr = "Yukarı yapı kırılımı (BOS↑)", UP
        struct_det = (f"Haftalık kapanış son onaylı salınım tepesini ({max(conf_sh[-2:]):.2f}) aştı — "
                      f"yukarı yapı kırılımı, alıcı kontrolü güçlendi.")
    elif conf_sl and last < min(conf_sl[-2:]):
        struct_lbl, struct_clr = "Aşağı yapı kırılımı (BOS↓)", DN
        struct_det = (f"Haftalık kapanış son onaylı salınım dibini ({min(conf_sl[-2:]):.2f}) kırdı — "
                      f"aşağı yapı kırılımı, satıcı kontrolü güçlendi.")

    # ── Blok 4: GÜNLÜK × HAFTALIK UYUM ───────────────────────────────────────
    w_dir = 1 if (above50 and slope50 > 0) else (-1 if ((not above50) and slope50 < 0) else 0)
    cd = df_d['Close']
    d50 = cd.rolling(50).mean()
    _d50_last = d50.iloc[-1]
    if pd.isna(_d50_last):
        d_dir = 0
    else:
        d_above = float(cd.iloc[-1]) > float(_d50_last)
        d_slope = float(_d50_last) - float(d50.iloc[-min(10, len(d50) - 1)])
        d_dir = 1 if (d_above and d_slope > 0) else (-1 if ((not d_above) and d_slope < 0) else 0)
    if w_dir != 0 and w_dir == d_dir:
        align_lbl = "UYUMLU — güçlü teyit"; align_clr = UP if w_dir > 0 else DN
        align_det = ("Günlük ve haftalık trend aynı yönde — en güvenilir senaryo. "
                     "İki zaman dilimi birbirini doğruluyor.")
    elif w_dir != 0 and d_dir != 0 and w_dir != d_dir:
        align_lbl, align_clr = "ÇELİŞKİ — dikkat", NEU
        align_det = ("Günlük ve haftalık trend ZIT yönde — tuzak riski. Kısa vade hareketi ana "
                     "eğilime karşı; teyit beklemek mantıklı.")
    else:
        align_lbl, align_clr = "KISMİ — biri nötr", MUT
        align_det = ("Bir zaman dilimi net yön verirken diğeri kararsız — geçiş ortamı, acele etme.")

    # ── Blok 5: HAFTALIK MUM ÖZETİ ───────────────────────────────────────────
    lo_ = float(lw.iloc[-1]); hi_ = float(hw.iloc[-1]); oo = float(ow.iloc[-1]); cc = last
    rng = (hi_ - lo_) if hi_ > lo_ else 1e-9
    body_pct = abs(cc - oo) / rng * 100
    green = cc >= oo
    upper_w = (hi_ - max(cc, oo)) / rng * 100
    lower_w = (min(cc, oo) - lo_) / rng * 100
    _wk_rsi_s = _er_rsi(cw)
    wk_rsi = float(_wk_rsi_s.iloc[-1]) if not pd.isna(_wk_rsi_s.iloc[-1]) else 50.0
    n8 = min(8, n - 1)
    chg8 = (last / float(cw.iloc[-1 - n8]) - 1) * 100 if n8 > 0 else 0.0
    greens = int((cw.diff().tail(n8) > 0).sum())
    if green and body_pct >= 55:
        cdl_lbl, cdl_clr = "Güçlü yeşil mum", UP
    elif (not green) and body_pct >= 55:
        cdl_lbl, cdl_clr = "Güçlü kırmızı mum", DN
    elif upper_w >= 40:
        cdl_lbl, cdl_clr = "Üst fitilli — satış baskısı", NEU
    elif lower_w >= 40:
        cdl_lbl, cdl_clr = "Alt fitilli — alıcı geldi", NEU
    else:
        cdl_lbl, cdl_clr = (("Yeşil" if green else "Kırmızı") + " — kararsız gövde"), NEU
    cdl_det = (f"Son haftalık mum: {cdl_lbl.lower()} (gövde %{body_pct:.0f}). Haftalık RSI {wk_rsi:.0f}. "
               f"Son {n8} hafta: {greens} yeşil / {n8 - greens} kırmızı, net %{chg8:+.1f}.")

    # ══ GELİŞTİRMELER (21 Haz 2026) — hacim + yapısal likidite ile güçlendirme ══
    # 1) 1y Hacim Profili POC (uzun vade maliyetlenme) → KONUM
    _poc1y = None; poc_side = "—"
    try:
        _poc1y = calculate_volume_profile_poc(df_d, lookback=min(250, len(df_d)), bins=30)
        if _poc1y and not pd.isna(_poc1y) and _poc1y > 0:
            _pdif = (last - _poc1y) / _poc1y * 100
            poc_side = "üstünde" if _pdif >= 0 else "altında"
            pos_det += (f" 1y hacim merkezi (POC) {_poc1y:.2f} — fiyat %{abs(_pdif):.0f} {poc_side}, "
                        f"yani yıllık maliyetlenmenin {poc_side}.")
        else:
            _poc1y = None
    except Exception:
        _poc1y = None

    # 2) Trend iptal seviyesi (MSS) — son onaylı yükselen dip → DOW
    mss_level = float(conf_sl[-1]) if conf_sl else None
    if dow_clr == UP and mss_level is not None:
        dow_det += f" Trend iptal seviyesi: {mss_level:.2f} (haftalık kapanış bu seviyenin altında → yükselen yapı bozulur)."

    # 3) Premium/Discount (EQ %50) + bant likiditesi (BSL/SSL mesafe) → PİYASA YAPISI
    eq = (hi52 + lo52) / 2
    zone = "premium (pahalı yarı)" if last > eq else "discount (ucuz yarı)"
    bsl_dist = (hi52 - last) / last * 100 if last > 0 else 0.0
    ssl_dist = (last - lo52) / last * 100 if last > 0 else 0.0
    struct_det += (f" Denge çizgisi (EQ %50): {eq:.2f} → fiyat {zone}. Üst likidite (52H zirve) "
                   f"%{bsl_dist:.0f} yukarıda, alt likidite (52H dip) %{ssl_dist:.0f} aşağıda.")

    # 4) Günlük momentum uyumsuzluğu (divergence) → GÜNLÜK×HAFTALIK tuzak filtresi
    div_bear = False
    try:
        if w_dir > 0:
            div_bear = bool(_er_bearish_div(df_d['High'], _er_rsi(df_d['Close'])))
    except Exception:
        div_bear = False
    if div_bear and "UYUMLU" in align_lbl:
        align_lbl, align_clr = "TEYİT AMA UYUMSUZLUK", NEU
        align_det = ("Günlük ve haftalık yön aynı GÖRÜNÜYOR ama günlük fiyat yeni tepe yaparken RSI "
                     "daha düşük tepe yapıyor (negatif momentum uyumsuzluğu) — 'güçlü teyit' bir tuzağa "
                     "dönebilir, momentum alttan zayıflıyor.")

    # 5) Haftalık Göreceli Hacim (RVol) → HAFTALIK MUM teyit katmanı
    wk_rvol = None; rvol_str = "—"
    try:
        _vw = df_w['Volume']
        _avg20 = float(_vw.iloc[-21:-1].mean()) if len(_vw) > 21 else float(_vw.iloc[:-1].mean())
        _lastvol = float(_vw.iloc[-1])
        if _avg20 > 0 and _lastvol > 0:
            wk_rvol = _lastvol / _avg20
            rvol_str = f"{wk_rvol:.1f}x"
            if wk_rvol >= 1.2:
                cdl_det += f" Hacim ortalamanın üstünde (RVol {rvol_str}) — kurumsal katılım, hareket gerçek."
            elif wk_rvol < 0.8:
                cdl_det += f" Hacim ortalamanın altında (RVol {rvol_str}) — sığ piyasa, gövde büyük olsa da teyit zayıf."
            else:
                cdl_det += f" Hacim ortalama civarı (RVol {rvol_str})."
    except Exception:
        pass

    # ── AI için HAM VERİ NOTLARI (bitmiş cümle DEĞİL — papağanlamayı önler, AI sentezler) ──
    _gh = ('uyumsuzluk: günlük fiyat yeni tepe ama RSI düşük tepe (tuzak riski)'
           if div_bear else align_lbl.lower())
    ai_data = (
        f"- uzun vade: {pos_lbl.replace('Uzun vade ','').lower()}, 52H bandında %{pos52:.0f}, "
        f"1y hacim merkezi (POC) {('%.2f'%_poc1y) if _poc1y else '—'} → fiyat {poc_side}\n"
        f"- Dow yapısı: {dow_lbl.lower()}; trend iptal seviyesi {('%.2f'%mss_level) if mss_level else '—'}\n"
        f"- bölge: {zone}; üst likidite %{bsl_dist:.0f} yukarı / alt likidite %{ssl_dist:.0f} aşağı\n"
        f"- günlük×haftalık: {_gh}\n"
        f"- son hafta mumu: {'yeşil' if green else 'kırmızı'}, gövde %{body_pct:.0f}, RVol {rvol_str}, haftalık RSI {wk_rsi:.0f}\n"
    )

    return {
        'pos': (pos_lbl, pos_det, pos_clr),
        'dow': (dow_lbl, dow_det, dow_clr),
        'structure': (struct_lbl, struct_det, struct_clr),
        'align': (align_lbl, align_det, align_clr),
        'candle': (cdl_lbl, cdl_det, cdl_clr),
        'pos52': pos52, 'wk_rsi': wk_rsi, 'n_weeks': n,
        'ai_data': ai_data,
    }
