# ██████████████████████████████████████████████████████████████████████████████
# SMART MONEY RADAR — ANA UYGULAMA
# Tüm modüllerin birleştiği ana dosya. Veri çekme, analiz motoru, tarama sistemleri ve Streamlit arayüzü bu dosyada bir arada çalışır.
# ██████████████████████████████████████████████████████████████████████████████
# ==============================================================================
# BÖLÜM 1 — BAĞIMLILIKLAR VE KÜTÜPHANE İÇE AKTARIMLARI
# Tüm üçüncü parti kütüphaneler, standart Python modülleri ve proje genelinde kullanılan sabitler burada tanımlanır.
# ==============================================================================
import streamlit as st
import yfinance as yf
import pandas as pd
import feedparser
import urllib.parse
from ta.volume import VolumeWeightedAveragePrice
from textblob import TextBlob
from datetime import datetime, timedelta
import pytz
_TZ_ISTANBUL = pytz.timezone("Europe/Istanbul")
import streamlit.components.v1 as components
import numpy as np
import sqlite3
import os
import concurrent.futures
import re
import altair as alt
import random
import os
import io
import base64
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

CACHE_DIR = r"C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal\veriler"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

# ── TARAMA SONUCU AGRESİF CACHE (Piyasa Dışı Saatler) ───────────────────────
SCAN_CACHE_DIR = os.path.join(CACHE_DIR, "scan_cache")
if not os.path.exists(SCAN_CACHE_DIR):
    os.makedirs(SCAN_CACHE_DIR)

# ==============================================================================
# BÖLÜM 2 — TARAMA CACHE SİSTEMİ
# Piyasa dışı saatlerde tarama sonuçlarını diske yazıp okuyan fonksiyonlar.
# Gereksiz API çağrılarını önler, performansı artırır.
# ==============================================================================
def _scan_is_offhours():
    """Şu an piyasa dışı mı? (18:20 sonrası veya 09:45 öncesi, hafta sonu dahil)"""
    now = datetime.now(_TZ_ISTANBUL)
    if now.weekday() >= 5:          # Cumartesi / Pazar
        return True
    hm = now.hour * 60 + now.minute
    return hm >= 18 * 60 + 20 or hm < 9 * 60 + 45

def _scan_last_close_dt():
    """Son kapanış zamanı: bugün 18:20 veya (hafta sonunu atlayarak) önceki iş günü 18:20"""
    now = datetime.now(_TZ_ISTANBUL)
    hm  = now.hour * 60 + now.minute
    if now.weekday() < 5 and hm >= 18 * 60 + 20:
        # Bugün kapandı
        return now.replace(hour=18, minute=20, second=0, microsecond=0)
    # Önceki iş günü
    d = now - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.replace(hour=18, minute=20, second=0, microsecond=0)

def save_scan_result(key, data, category=""):
    """Tarama sonucunu diske pickle olarak yaz. Başarı/hata durumunu döndürür."""
    import pickle, logging
    safe_cat = category.replace(" ", "_").replace("&", "and").replace("/", "-")
    fpath = os.path.join(SCAN_CACHE_DIR, f"{key}__{safe_cat}.pkl")
    try:
        with open(fpath, "wb") as f:
            pickle.dump({"data": data, "ts": datetime.now(_TZ_ISTANBUL)}, f)
            f.flush()
            os.fsync(f.fileno())   # Dosya sistemi tamponunu diske zorla
        return True
    except Exception as e:
        logging.warning(f"[scan_cache] save_scan_result HATA — key={key}: {e}")
        return False, str(e)       # Hata detayını döndür

def load_scan_result(key, category=""):
    """
    Diskten tarama sonucu yükle.
    Geçerli sayılır: piyasa dışı saatlerde VE cache son kapanıştan sonra yapılmışsa.
    """
    import pickle
    if not _scan_is_offhours():
        return None                    # Piyasa açık — cache kullanma
    safe_cat = category.replace(" ", "_").replace("&", "and").replace("/", "-")
    fpath = os.path.join(SCAN_CACHE_DIR, f"{key}__{safe_cat}.pkl")
    if not os.path.exists(fpath):
        return None
    try:
        with open(fpath, "rb") as f:
            cached = pickle.load(f)
        if cached["ts"] >= _scan_last_close_dt():
            return cached["data"]      # Geçerli cache
    except Exception:
        pass
    return None

def _volume_is_stale(df, ticker):
    """
    Son işlem verisinde hacim bozukluğu var mı kontrol eder.
    Endeks/kripto hariç: son non-zero Volume tarihi 5+ takvim günü eskiyse
    (bugün hariç) → parquet bozuk/eksik demektir.
    """
    if df is None or df.empty:
        return False
    if ticker.startswith(("XU", "^")) or "-USD" in ticker:
        return False
    try:
        v = df['Volume']
        v_hist = v.iloc[:-1]           # bugünü hariç tut
        nonzero = v_hist[v_hist > 0]
        if len(nonzero) == 0:
            return True
        last_nz = nonzero.index[-1]
        if hasattr(last_nz, 'date'):
            last_nz = last_nz.date()
        import datetime as _dt_sv
        days_gap = (_dt_sv.date.today() - last_nz).days
        # 5+ takvim günü açık varsa bozuk say (hafta sonu = max 3 gün normal boşluk)
        return days_gap > 5
    except Exception:
        return False

def _fetch_bist_volume_isyatirim(symbol, start_date, end_date):
    """
    İş Yatırım API'sinden BIST hisse günlük hacim verisini çeker.
    Hisse adedi = HGDG_HACIM (TL cinsinden işlem hacmi) / HGDG_AOF (ağırlıklı ort. fiyat)
    Bu veri yfinance'in Volume=0 bug'ından tamamen bağımsızdır.

    symbol    : 'SASA' veya 'SASA.IS' — .IS suffix otomatik temizlenir
    start_date: 'yyyy-mm-dd'
    end_date  : 'yyyy-mm-dd'
    Returns   : pd.Series (DatetimeIndex, Volume adet cinsinden) veya None
    """
    try:
        from isyatirimhisse import fetch_stock_data
        _sym = symbol.replace(".IS", "").replace(".is", "")
        # isyatirimhisse dd-mm-yyyy formatı ister
        from datetime import datetime as _dtime
        _s = _dtime.strptime(start_date, "%Y-%m-%d").strftime("%d-%m-%Y")
        _e = _dtime.strptime(end_date,   "%Y-%m-%d").strftime("%d-%m-%Y")
        df_isy = fetch_stock_data(symbols=_sym, start_date=_s, end_date=_e)
        if df_isy is None or df_isy.empty:
            return None
        if 'HGDG_HACIM' not in df_isy.columns or 'HGDG_AOF' not in df_isy.columns:
            return None
        # TL hacmini hisse adedine çevir
        df_isy = df_isy[df_isy['HGDG_AOF'] > 0].copy()
        df_isy['_vol_shares'] = df_isy['HGDG_HACIM'] / df_isy['HGDG_AOF']
        df_isy['_date'] = pd.to_datetime(df_isy['HGDG_TARIH'])
        df_isy = df_isy.set_index('_date')
        df_isy.index = df_isy.index.tz_localize(None) if df_isy.index.tz else df_isy.index
        return df_isy['_vol_shares'].rename('Volume')
    except ImportError:
        return None   # paket yüklü değil → sessizce yfinance'e düş
    except Exception:
        return None


def _fix_stale_volume(df_base, clean_ticker, interval):
    """
    start/end ile gelen veride Volume=0 (bozuk) günler varsa,
    period='1mo' ile kısa vadeli çekiş yapıp son 30 günü override eder.
    Eski tarihsel veri korunur, sadece bozuk günler düzeltilir.
    """
    try:
        df_short = _yf_download_with_retry(clean_ticker, period="1mo", interval=interval)
        if df_short.empty:
            return df_base
        # Sütun temizleme
        if isinstance(df_short.columns, pd.MultiIndex):
            df_short.columns = (df_short.columns.get_level_values(0)
                                if 'Close' in df_short.columns.get_level_values(0)
                                else df_short.columns.get_level_values(1))
        df_short = df_short.loc[:, ~df_short.columns.duplicated()].copy()
        df_short.columns = [str(c).capitalize() for c in df_short.columns]
        if df_short.index.tz is not None:
            df_short.index = df_short.index.tz_convert(None)
        if 'Volume' not in df_short.columns or df_short['Volume'].isna().all():
            return df_base
        # Merge: df_base'den short'un başladığı tarihten öncesini al, geri kalanı short ile yaz
        cutoff = df_short.index[0]
        df_old = df_base[df_base.index < cutoff].copy()
        df_merged = pd.concat([df_old, df_short])
        df_merged = df_merged[~df_merged.index.duplicated(keep='last')].sort_index()
        return df_merged
    except Exception:
        return df_base

def is_yahoo_update_needed(ticker, local_last_date):
    now = datetime.now(_TZ_ISTANBUL)
    weekday = now.weekday()
    hour_min = now.hour * 100 + now.minute
    local_date = local_last_date.date()

    if ".IS" in ticker or ticker.startswith("XU"):
        if weekday >= 5:
            return local_date < (now - timedelta(days=(weekday - 4))).date()
        if hour_min < 1000:
            return local_date < (now - timedelta(days=1)).date()
        if hour_min > 1830:
            return local_date < now.date()
        return True

    elif "-USD" not in ticker and "=F" not in ticker:
        if weekday >= 5:
            return local_date < (now - timedelta(days=(weekday - 4))).date()
        if hour_min > 2330 or hour_min < 1630:
            target_date = now.date() if hour_min > 2330 else (now - timedelta(days=1)).date()
            return local_date < target_date
        return True

    return True

# ==============================================================================
# 1. AYARLAR VE STİL
# ==============================================================================
st.set_page_config(
    page_title="SMART MONEY RADAR", 
    layout="wide",
    page_icon="💸"
)

if 'theme' not in st.session_state:
    st.session_state.theme = "SMR Dark"

THEMES = {
    "SMR Dark": {"bg": "#060d1a", "box_bg": "#0d1829", "text": "#f1f5f9", "border": "#1e3a5f", "news_bg": "#0a1628"},
}
current_theme = THEMES[st.session_state.theme]

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=JetBrains+Mono:wght@400;700&display=swap');

    /* ── TEMEL LAYOUT ── */
    section[data-testid="stSidebar"] {{ width: 350px !important; }}
    section[data-testid="stSidebar"] div[data-testid="stMarkdownContainer"] * {{ font-family: 'Inter', sans-serif !important; }}
    div[data-testid="stMetricValue"] {{ font-size: 0.7rem !important; }}
    div[data-testid="stMetricLabel"] {{ font-size: 0.7rem !important; font-weight: 700; }}
    div[data-testid="stMetricDelta"] {{ font-size: 0.7rem !important; }}

    html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; color: {current_theme['text']}; }}
    .stApp {{ background-color: {current_theme['bg']}; }}
    section.main > div.block-container {{ padding-top: 1rem; padding-bottom: 1rem; }}
    .stMetricValue, .money-text {{ font-family: 'JetBrains Mono', monospace !important; }}

    /* ── IZGARA DESEN ── */
    .stApp::before {{
        content: '';
        position: fixed;
        inset: 0;
        background-image:
            linear-gradient(rgba(56,189,248,0.025) 1px, transparent 1px),
            linear-gradient(90deg, rgba(56,189,248,0.025) 1px, transparent 1px);
        background-size: 50px 50px;
        pointer-events: none;
        z-index: 0;
    }}

    /* ── SIDEBAR ── */
    section[data-testid="stSidebar"] {{
        background: linear-gradient(180deg, #060d1a 0%, #0a1628 100%) !important;
        border-right: 1px solid #1e3a5f !important;
    }}
    section[data-testid="stSidebar"] .stMarkdown p,
    section[data-testid="stSidebar"] label {{
        color: #cbd5e1 !important;
    }}

    /* ── KONTEYNERLER & KARTLAR ── */
    div[data-testid="stVerticalBlockBorderWrapper"] {{
        background: {current_theme['box_bg']} !important;
        border: 1px solid {current_theme['border']} !important;
        border-radius: 8px !important;
        resize: vertical !important;
        overflow: auto !important;
        min-height: 150px !important;
        margin-bottom: 10px !important;
        border-bottom-right-radius: 8px !important;
    }}

    /* ── TABS ── */
    div[data-testid="stTabs"] button {{
        color: #64748b !important;
        border-bottom: 2px solid transparent !important;
        font-weight: 600 !important;
    }}
    div[data-testid="stTabs"] button[aria-selected="true"] {{
        color: #10b981 !important;
        border-bottom: 2px solid #10b981 !important;
    }}
    div[data-testid="stTabs"] {{
        border-bottom: 1px solid {current_theme['border']} !important;
    }}

    /* ── BUTONLAR ── */
    div.stButton > button[kind="primary"], div.stButton > button[data-testid="baseButton-primary"] {{
        background: linear-gradient(135deg, #10b981, #059669) !important;
        border: none !important;
        color: white !important;
        opacity: 1 !important;
        border-radius: 6px;
        font-weight: 700;
        letter-spacing: 0.5px;
        box-shadow: 0 2px 8px rgba(16,185,129,0.3);
    }}
    div.stButton > button[kind="primary"]:hover, div.stButton > button[data-testid="baseButton-primary"]:hover {{
        background: linear-gradient(135deg, #059669, #047857) !important;
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(16,185,129,0.4);
    }}
    div.stButton button[data-testid="baseButton-secondary"] {{
        background: {current_theme['box_bg']} !important;
        border: 1px solid {current_theme['border']} !important;
        color: #38bdf8 !important;
        font-weight: 700 !important;
        transition: all 0.2s ease-in-out;
    }}
    div.stButton button[data-testid="baseButton-secondary"]:hover {{
        border-color: #10b981 !important;
        color: #10b981 !important;
        transform: translateY(-1px);
    }}
    .stButton button {{ width: 100%; border-radius: 6px; font-size: 0.75rem; padding: 0.1rem 0.4rem; }}

    /* ── INPUT & SELECTBOX ── */
    .stSelectbox, .stTextInput {{ margin-bottom: -10px; }}
    div[data-testid="stSelectbox"] > div,
    div[data-testid="stTextInput"] > div > div {{
        background: {current_theme['box_bg']} !important;
        border-color: {current_theme['border']} !important;
        color: {current_theme['text']} !important;
    }}

    /* ── SELECTBOX DROPDOWN LİSTE ── */
    /* Tüm baseweb popover katmanları */
    div[data-baseweb="popover"],
    div[data-baseweb="popover"] > div,
    div[data-baseweb="popover"] > div > div,
    div[data-baseweb="popover"] > div > div > div {{
        background: rgba(11,20,38,0.88) !important;
        backdrop-filter: blur(12px) !important;
        -webkit-backdrop-filter: blur(12px) !important;
        border: 1px solid rgba(56,189,248,0.18) !important;
        border-radius: 8px !important;
        box-shadow: 0 8px 32px rgba(0,0,0,0.6) !important;
    }}
    /* Liste wrapper ve listbox */
    div[data-baseweb="menu"],
    div[data-baseweb="menu"] > div,
    ul[role="listbox"] {{
        background: transparent !important;
        padding: 4px !important;
    }}
    /* Her liste ögesi */
    ul[role="listbox"] li,
    [role="option"],
    div[data-baseweb="menu"] li {{
        background: transparent !important;
        color: #94a3b8 !important;
        font-size: 0.82rem !important;
        border-radius: 4px !important;
    }}
    ul[role="listbox"] li:hover,
    [role="option"]:hover,
    div[data-baseweb="menu"] li:hover {{
        background: rgba(56,189,248,0.12) !important;
        color: #f1f5f9 !important;
    }}
    ul[role="listbox"] li[aria-selected="true"],
    [role="option"][aria-selected="true"] {{
        background: rgba(56,189,248,0.18) !important;
        color: #38bdf8 !important;
        font-weight: 700 !important;
    }}

    /* ── DATAFRAME ── */
    div[data-testid="stDataFrame"] {{
        border: 1px solid {current_theme['border']} !important;
        border-radius: 6px !important;
    }}
    div[data-testid="stDataFrame"] th {{
        background: #0d1829 !important;
        color: #38bdf8 !important;
        border-bottom: 1px solid {current_theme['border']} !important;
    }}

    /* ── METRIK KARTLAR ── */
    div[data-testid="stMetric"] {{
        background: {current_theme['box_bg']};
        border: 1px solid {current_theme['border']};
        border-radius: 6px;
        padding: 8px;
    }}
    div[data-testid="stMetricValue"] {{ color: #f1f5f9 !important; }}
    div[data-testid="stMetricLabel"] {{ color: #64748b !important; }}

    /* ── DIVIDER ── */
    hr {{ margin-top: 0.2rem; margin-bottom: 0.5rem; border-color: {current_theme['border']}; }}

    /* ── DELTA RENKLER ── */
    .delta-pos {{ color: #10b981; }} .delta-neg {{ color: #f87171; }}

    /* ── ÖZEL KARTLAR ── */
    .stat-box-small {{ background: {current_theme['box_bg']}; border: 1px solid {current_theme['border']}; border-radius: 6px; padding: 8px; text-align: center; margin-bottom: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.3); }}
    .stat-label-small {{ font-size: 0.6rem; color: #475569; text-transform: uppercase; margin: 0; font-weight: 700; letter-spacing: 0.5px; }}
    .stat-value-small {{ font-size: 1.1rem; font-weight: 700; color: {current_theme['text']}; margin: 2px 0 0 0; }}
    .stat-delta-small {{ font-size: 0.8rem; margin-left: 6px; font-weight: 600; }}

    .news-card {{ background: {current_theme['news_bg']}; border-left: 3px solid #10b981; padding: 6px; margin-bottom: 6px; font-size: 0.78rem; border-radius: 0 4px 4px 0; }}

    .info-card {{ background: {current_theme['box_bg']}; border: 1px solid {current_theme['border']}; border-radius: 6px; padding: 6px; margin-top: 5px; margin-bottom: 5px; font-size: 0.8rem; font-family: 'Inter', sans-serif; }}
    .info-header {{ font-weight: 700; color: #38bdf8; border-bottom: 1px solid {current_theme['border']}; padding-bottom: 4px; margin-bottom: 4px; }}
    .info-row {{ display: flex; align-items: flex-start; margin-bottom: 2px; }}
    .label-short {{ font-weight: 600; color: #475569; width: 80px; flex-shrink: 0; }}
    .label-long {{ font-weight: 600; color: #475569; width: 100px; flex-shrink: 0; }}
    .info-val {{ color: {current_theme['text']}; font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; }}
    .edu-note {{ font-size: 0.85rem; color: #cbd5e1; font-style: italic; margin-top: 2px; margin-bottom: 6px; line-height: 1.3; }}
    .tech-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 4px; }}
    .tech-item {{ display: flex; align-items: center; font-size: 0.8rem; }}

    /* ── EXPANDER ── */
    div[data-testid="stExpander"] {{
        background: {current_theme['box_bg']} !important;
        border: 1px solid {current_theme['border']} !important;
        border-radius: 8px !important;
    }}
    div[data-testid="stExpander"] summary {{
        color: #38bdf8 !important;
        font-weight: 600 !important;
    }}

    /* ── SCROLLBAR ── */
    ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
    ::-webkit-scrollbar-track {{ background: #060d1a; }}
    ::-webkit-scrollbar-thumb {{ background: #1e3a5f; border-radius: 3px; }}
    ::-webkit-scrollbar-thumb:hover {{ background: #10b981; }}

    /* ── INFO KARTLARI (SMR DARK) ── */
    .info-header {{ color: #38bdf8 !important; }}
    .info-card {{ background: #0d1829 !important; border-color: #1e3a5f !important; }}
    .info-val {{ color: #f1f5f9 !important; }}
    .label-short, .label-long {{ color: #64748b !important; }}
    .edu-note {{ color: #cbd5e1 !important; }}
    .usp-edu {{ color: #94a3b8 !important; }}

    /* ── ALTAIR CHART TRANSPARENT BG ── */
    .vega-embed .marks {{ background: transparent !important; }}

    /* ── SELECTBOX DROPDOWN — mavi çerçeve ── */
    div[data-baseweb="popover"] {{
        border: 2px solid #38bdf8 !important;
        border-radius: 8px !important;
        box-shadow: 0 0 0 1px rgba(56,189,248,0.15), 0 8px 32px rgba(0,0,0,0.6) !important;
        overflow: hidden !important;
    }}

    /* ── ST.DIALOG (MODAL) — mavi çerçeve ── */
    div[role="dialog"],
    div[data-testid="stModal"],
    section[data-testid="stModal"] > div {{
        border: 2px solid #38bdf8 !important;
        border-radius: 12px !important;
        box-shadow: 0 0 0 1px rgba(56,189,248,0.2), 0 16px 48px rgba(0,0,0,0.7) !important;
        overflow: hidden !important;
    }}
    /* Modal başlık çizgisi */
    div[role="dialog"] > div:first-child,
    div[data-testid="stModal"] > div:first-child {{
        border-bottom: 1px solid rgba(56,189,248,0.2) !important;
    }}
</style>
""", unsafe_allow_html=True)

# Dropdown + Modal çerçeve — geç enjekte
st.markdown("""<style>
div[data-baseweb="popover"] {
    border: 2px solid #38bdf8 !important;
    border-radius: 8px !important;
    box-shadow: 0 0 0 1px rgba(56,189,248,0.15), 0 8px 32px rgba(0,0,0,0.6) !important;
    overflow: hidden !important;
}
div[role="dialog"],
div[data-testid="stModal"],
section[data-testid="stModal"] > div {
    border: 2px solid #38bdf8 !important;
    border-radius: 12px !important;
    box-shadow: 0 0 0 1px rgba(56,189,248,0.2), 0 16px 48px rgba(0,0,0,0.7) !important;
    overflow: hidden !important;
}
div[data-baseweb="popover"],
div[data-baseweb="popover"] > div,
div[data-baseweb="popover"] > div > div,
div[data-baseweb="popover"] > div > div > div {
    background: rgba(11,20,38,0.88) !important;
    background-color: rgba(11,20,38,0.88) !important;
    backdrop-filter: blur(12px) !important;
    -webkit-backdrop-filter: blur(12px) !important;
    border: 1px solid rgba(56,189,248,0.18) !important;
    border-radius: 8px !important;
    box-shadow: 0 8px 32px rgba(0,0,0,0.6) !important;
}
div[data-baseweb="menu"],
div[data-baseweb="menu"] > div,
ul[role="listbox"] {
    background: transparent !important;
    background-color: transparent !important;
}
ul[role="listbox"] li,
[role="option"] {
    background: transparent !important;
    background-color: transparent !important;
    color: #94a3b8 !important;
    font-size: 0.82rem !important;
    border-radius: 4px !important;
}
ul[role="listbox"] li:hover,
[role="option"]:hover {
    background: rgba(56,189,248,0.12) !important;
    background-color: rgba(56,189,248,0.12) !important;
    color: #f1f5f9 !important;
}
ul[role="listbox"] li[aria-selected="true"],
[role="option"][aria-selected="true"] {
    background: rgba(56,189,248,0.18) !important;
    background-color: rgba(56,189,248,0.18) !important;
    color: #38bdf8 !important;
    font-weight: 700 !important;
}
</style>""", unsafe_allow_html=True)

# ==============================================================================
# 2. VERİTABANI VE LİSTELER
# ==============================================================================
DB_FILE = "patron.db"

# ==============================================================================
# BÖLÜM 3 — VERİTABANI (SQLite)
# Sinyal geçmişi, izleme listesi ve performans takibi için SQLite bağlantısı.
# signals.db ve patron.db burada yönetilir.
# ==============================================================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS watchlist (symbol TEXT PRIMARY KEY)')
    c.execute('''CREATE TABLE IF NOT EXISTS scan_signals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_date   TEXT NOT NULL,
        symbol      TEXT NOT NULL,
        scan_type   TEXT NOT NULL,
        score       REAL,
        bias        TEXT DEFAULT 'bullish',
        entry_price REAL,
        stop_level  REAL,
        category    TEXT,
        UNIQUE(scan_date, symbol, scan_type)
    )''')
    conn.commit()
    conn.close()

def log_scan_signal(scan_type: str, df_result, category: str = ""):
    """
    Scan sonuçlarını signals.db'ye (patron.db içinde scan_signals tablosuna) yazar.
    Aynı gün aynı scan_type + symbol kombinasyonu varsa INSERT OR IGNORE ile atlar.
    """
    if df_result is None or (hasattr(df_result, 'empty') and df_result.empty):
        return
    today = datetime.now(_TZ_ISTANBUL).strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        for _, row in df_result.iterrows():
            symbol = row.get('Sembol', '')
            if not symbol:
                continue
            entry_raw   = row.get('Fiyat', row.get('fiyat', None))
            score_raw   = row.get('ToplamSkor', row.get('Raw_Score', row.get('Skor', row.get('score', None))))
            stop_raw    = row.get('Stop', row.get('stop_level', row.get('StopSeviye', None)))
            try:
                entry_price = float(str(entry_raw).replace(',', '.')) if entry_raw is not None else None
            except Exception:
                entry_price = None
            try:
                score = float(score_raw) if score_raw is not None else None
            except Exception:
                score = None
            try:
                stop_level = float(str(stop_raw).replace(',', '.')) if stop_raw is not None else None
            except Exception:
                stop_level = None
            c.execute(
                '''INSERT OR IGNORE INTO scan_signals
                   (scan_date, symbol, scan_type, score, bias, entry_price, stop_level, category)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (today, symbol, scan_type, score, 'bullish', entry_price, stop_level, category)
            )
        conn.commit()
        conn.close()
    except Exception as e:
        import logging
        logging.warning(f"[log_scan_signal] HATA — scan_type={scan_type}: {e}")

def load_watchlist_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT symbol FROM watchlist')
    data = c.fetchall()
    conn.close()
    return [x[0] for x in data]

def add_watchlist_db(symbol):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute('INSERT INTO watchlist (symbol) VALUES (?)', (symbol,))
        conn.commit()
    except sqlite3.IntegrityError: 
        pass
    conn.close()

def remove_watchlist_db(symbol):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('DELETE FROM watchlist WHERE symbol = ?', (symbol,))
    conn.commit()
    conn.close()

def evaluate_signals(lookback_days=90, forward_windows=None):
    """
    scan_signals tablosundaki sinyalleri değerlendirir.
    Her sinyal için +5, +10, +20 günlük fiyat getirisini hesaplar.
    Minimum 5 gün geçmemiş sinyaller atlanır (henüz olgunlaşmamış).
    Parquet cache üzerinden çalışır — ek internet isteği yapmaz.
    """
    if forward_windows is None:
        forward_windows = [5, 10, 20]

    try:
        conn = sqlite3.connect(DB_FILE)
        signals = pd.read_sql(
            "SELECT * FROM scan_signals WHERE scan_date >= date('now', ?)",
            conn,
            params=(f'-{lookback_days} days',)
        )
        conn.close()
    except Exception:
        return pd.DataFrame()

    if signals.empty:
        return pd.DataFrame()

    today = datetime.now(_TZ_ISTANBUL).date()
    results = []

    for _, sig in signals.iterrows():
        try:
            signal_date = pd.to_datetime(sig['scan_date']).date()
            days_elapsed = (today - signal_date).days
            if days_elapsed < min(forward_windows):
                continue  # henüz değerlendirilemez

            # Parquet cache'ten tarihi veri al
            df_hist = get_safe_historical_data(sig['symbol'], period='1y', interval='1d')
            if df_hist is None or df_hist.empty:
                continue

            df_hist = df_hist.sort_index()

            # Sinyal tarihine en yakın index'i bul
            sig_ts = pd.Timestamp(sig['scan_date'])
            idx_arr = df_hist.index.searchsorted(sig_ts)
            if idx_arr >= len(df_hist):
                continue

            # Giriş fiyatı: kayıtlı entry_price yoksa o günün kapanışını kullan
            if sig['entry_price'] and not pd.isna(sig['entry_price']):
                entry = float(sig['entry_price'])
            else:
                entry = float(df_hist['Close'].iloc[idx_arr])

            if entry == 0:
                continue

            row_result = {
                'Sembol':        sig['symbol'],
                'Tarama':        sig['scan_type'],
                'Sinyal Tarihi': sig['scan_date'],
                'Giriş':         round(entry, 2),
                'Geçen Gün':     days_elapsed,
                'Kategori':      sig.get('category', ''),
            }

            for fwd in forward_windows:
                fwd_idx = idx_arr + fwd
                if fwd_idx < len(df_hist):
                    fwd_price = float(df_hist['Close'].iloc[fwd_idx])
                    ret = (fwd_price - entry) / entry * 100
                    row_result[f'Getiri_{fwd}G'] = round(ret, 2)
                    row_result[f'Hit_{fwd}G']    = ret > 0
                else:
                    row_result[f'Getiri_{fwd}G'] = None
                    row_result[f'Hit_{fwd}G']    = None

            results.append(row_result)
        except Exception:
            continue

    return pd.DataFrame(results) if results else pd.DataFrame()


def get_signal_performance_summary(lookback_days=90):
    """
    evaluate_signals() çıktısını scan_type bazında özetler.
    Hit rate, ortalama getiri ve sinyal sayısını döndürür.
    Minimum 3 değerlendirilebilir sinyal yoksa o satırı — gösterir.
    """
    df = evaluate_signals(lookback_days=lookback_days)
    if df.empty:
        return pd.DataFrame()

    scan_labels = {
        'guclu_donus':    '💪 Güçlü Dönüş',
        'nadir_firsat':   '🔥 Nadir Fırsat',
        'minervini':      '📈 Minervini SEPA',
        'rs_leaders':     '🚀 RS Momentum',
        'golden_pattern': '⭐ Altın Formasyon',
    }

    summary = []
    for scan_type, grp in df.groupby('Tarama'):
        row = {
            'Tarama':  scan_labels.get(scan_type, scan_type),
            'Sinyal':  len(grp),
        }
        for fwd in [5, 10, 20]:
            col_hit = f'Hit_{fwd}G'
            col_ret = f'Getiri_{fwd}G'
            valid_hits = grp[col_hit].dropna()
            valid_rets  = grp[col_ret].dropna()
            if len(valid_hits) >= 3:
                row[f'Hit {fwd}G']  = f"%{round(valid_hits.mean() * 100, 1)}"
                row[f'Ort +{fwd}G'] = f"%{round(valid_rets.mean(), 2):+.2f}"
            else:
                row[f'Hit {fwd}G']  = f"— (n={len(valid_hits)})"
                row[f'Ort +{fwd}G'] = "—"
        summary.append(row)

    return pd.DataFrame(summary)


init_db()

# ==============================================================================
# BÖLÜM 4 — VARLIK LİSTELERİ VE KATEGORİLER
# BIST, S&P 500, Kripto, Emtia grupları. Taramalarda kullanılan
# tüm ticker listeleri ve display ad sözlüğü burada tanımlıdır.
# ==============================================================================
# --- VARLIK LİSTELERİ ---
priority_sp = ["^GSPC", "^DJI", "^NDX", "^IXIC", "^RUT", "RKLB", "META", "TSPY", "ARCC", "JEPI", "QQQI", "SPYI"]

# S&P 500'ün Tamamı (503 Hisse - Güncel)
raw_sp500_rest = [
    "A", "AAL", "AAPL", "ABBV", "ABNB", "ABT", "ACGL", "ACN", "ADBE", "ADI", "ADM", "ADP", "ADSK", "AEE", "AEP", "AES", "AFL", "AGNC", "AIG", "AIZ", "AJG", 
    "AKAM", "ALB", "ALGN", "ALL", "ALLE", "AMAT", "AMCR", "AMD", "AME", "AMGN", "AMP", "AMT", "AMTM", "AMZN", "ANET", "ANSS", "AON", "AOS", "APA", 
    "APD", "APH", "APTV", "ARE", "ATO", "AVB", "AVGO", "AVY", "AWK", "AXON", "AXP", "AZO", "BA", "BAC", "BALL", "BAX", "BBWI", "BBY", "BDX", "BEN", 
    "BF-B", "BG", "BIIB", "BK", "BKNG", "BKR", "BLDR", "BLK", "BMY", "BR", "BRK-B", "BRO", "BSX", "BWA", "BX", "BXP", "C", "CAG", "CAH", "CARR", 
    "CAT", "CB", "CBOE", "CBRE", "CCI", "CCL", "CDNS", "CDW", "CE", "CEG", "CF", "CFG", "CHD", "CHRW", "CHTR", "CI", "CINF", "CL", "CLX", "CMCSA", 
    "CME", "CMG", "CMI", "CMS", "CNC", "CNP", "COF", "COO", "COP", "COR", "COST", "CPAY", "CPB", "CPRT", "CPT", "CRL", "CRM", "CRWD", "CSCO", 
    "CSGP", "CSX", "CTAS", "CTRA", "CTSH", "CTVA", "CVS", "CVX", "CZR", "D", "DAL", "DAY", "DD", "DE", "DECK", "DFS", "DG", "DGX", "DHI", "DHR", 
    "DIS", "DLR", "DLTR", "DOC", "DOV", "DOW", "DPZ", "DRI", "DTE", "DUK", "DVA", "DVN", "DXCM", "EA", "EBAY", "ECL", "ED", "EFX", "EG", "EIX", 
    "EL", "ELV", "EMN", "EMR", "ENPH", "EOG", "EQIX", "EQR", "EQT", "ERIE", "ES", "ESS", "ETN", "ETR", "EVRG", "EW", "EXC", "EXPD", "EXPE", "EXR", 
    "F", "FANG", "FAST", "FCX", "FDS", "FDX", "FE", "FFIV", "FI", "FICO", "FIS", "FITB", "FMC", "FOX", "FOXA", "FRT", "FSLR", "FTNT", "FTV", "GD", 
    "GE", "GEHC", "GEN", "GEV", "GILD", "GIS", "GL", "GLW", "GM", "GNRC", "GOOG", "GOOGL", "GPC", "GPN", "GRMN", "GS", "GWW", "HAL", "HAS", "HBAN", 
    "HCA", "HD", "HES", "HIG", "HII", "HLT", "HOLX", "HON", "HPE", "HPQ", "HRL", "HSY", "HUBB", "HUM", "HWM", "IBM", "ICE", "IDXX", "IEX", "IFF", 
    "ILMN", "INCY", "INTC", "INTU", "INVH", "IP", "IPG", "IQV", "IR", "IRM", "ISRG", "IT", "ITW", "IVZ", "J", "JBHT", "JBL", "JCI", "JEPQ", "JKHY", "JNJ", 
    "JNPR", "JPM", "K", "KDP", "KEY", "KEYS", "KHC", "KIM", "KKR", "KLAC", "KMB", "KMI", "KMX", "KO", "KR", "KVUE", "L", "LDOS", "LEN", "LH", 
    "LHX", "LIN", "LKQ", "LLY", "LMT", "LNT", "LOW", "LRCX", "LULU", "LUV", "LVS", "LW", "LYB", "LYV", "MA", "MAA", "MAR", "MAS", "MCD", "MCHP", 
    "MCK", "MCO", "MDLZ", "MDT", "MET", "META", "MGM", "MHK", "MKC", "MKTX", "MLM", "MMC", "MMM", "MNST", "MO", "MOH", "MOS", "MPC", "MPWR", "MRK", 
    "MRNA", "MS", "MSCI", "MSFT", "MSI", "MTB", "MTCH", "MTD", "MU", "NCLH", "NDSN", "NEE", "NEM", "NFLX", "NI", "NKE", "NOC", "NOW", "NRG", "NSC", 
    "NTAP", "NTRS", "NUE", "NVDA", "NVR", "NWS", "NWSA", "NXPI", "O", "ODFL", "OKE", "OMC", "ON", "ORCL", "ORLY", "OTIS", "OXY", "PANW", "PARA", 
    "PAYC", "PAYX", "PCAR", "PCG", "PEG", "PEP", "PFE", "PFG", "PG", "PGR", "PH", "PHM", "PKG", "PLD", "PLTR", "PM", "PNC", "PNR", "PNW", "POOL", 
    "PPG", "PPL", "PRU", "PSA", "PSX", "PTC", "PWR", "PYPL", "QCOM", "QRVO", "RCL", "REG", "REGN", "RF", "RJF", "RL", "RMD", "ROK", "ROL", "ROP", 
    "ROST", "RSG", "RTX", "RVTY", "SBAC", "SBUX", "SCHW", "SHW", "SJM", "SLB", "SMCI", "SNA", "SNPS", "SO", "SOLV", "SPG", "SPGI", "SRCL", "SRE", 
    "STE", "STLD", "STT", "STX", "STZ", "SW", "SWK", "SWKS", "SYF", "SYK", "SYY", "T", "TAP", "TDG", "TDY", "TECH", "TEL", "TER", "TFC", "TFX", 
    "TGT", "TJX", "TMO", "TMUS", "TPR", "TRGP", "TRMB", "TROW", "TRV", "TSCO", "TSLA", "TSN", "TT", "TTWO", "TXN", "TXT", "TYL", "UAL", "UBER", 
    "UDR", "UHS", "ULTA", "UNH", "UNP", "UPS", "URI", "USB", "V", "VICI", "VLO", "VLTO", "VMC", "VRSK", "VRSN", "VRTX", "VTR", "VTRS", "VZ", 
    "WAB", "WAT", "WBA", "WBD", "WDC", "WEC", "WELL", "WFC", "WM", "WMB", "WMT", "WRB", "WRK", "WST", "WTW", "WY", "WYNN", "XEL", "XOM", "XYL", 
    "YUM", "ZBH", "ZBRA", "ZTS", "SOFI", "RKLB"
]

# Kopyaları Temizle ve Birleştir
raw_sp500_rest = list(set(raw_sp500_rest) - set(priority_sp))
raw_sp500_rest.sort()
final_sp500_list = priority_sp + raw_sp500_rest

priority_crypto = ["BTC-USD", "ETH-USD"]
other_crypto = [
    # --- MAJOR ALTCOINS ---
    "BNB-USD", "SOL-USD", "XRP-USD", "ADA-USD", "DOGE-USD", "AVAX-USD", "TRX-USD",
    "DOT-USD", "MATIC-USD", "LINK-USD", "TON-USD", "SHIB-USD", "LTC-USD", "BCH-USD",
  
    # --- POPULER KATMAN 1 & 2 (L1/L2) ---
    "ICP-USD", "NEAR-USD", "APT-USD", "STX-USD", "FIL-USD", "ATOM-USD", "ARB-USD",
    "OP-USD", "INJ-USD", "KAS-USD", "TIA-USD", "SEI-USD", "SUI-USD", "ALGO-USD",
    "HBAR-USD", "EGLD-USD", "FTM-USD", "XLM-USD", "VET-USD", "ETC-USD", "EOS-USD",
    "XTZ-USD", "MINA-USD", "ASTR-USD", "FLOW-USD", "KLAY-USD", "IOTA-USD", "NEO-USD",
    
    # --- DEFI & WEB3 & AI ---
    "RNDR-USD", "GRT-USD", "FET-USD", "UNI-USD", "LDO-USD", "MKR-USD", "AAVE-USD",
    "SNX-USD", "RUNE-USD", "QNT-USD", "CRV-USD", "CFX-USD", "CHZ-USD", "AXS-USD",
    "SAND-USD", "MANA-USD", "THETA-USD", "GALA-USD", "ENJ-USD", "COMP-USD", "1INCH-USD",
    "ZIL-USD", "BAT-USD", "LRC-USD", "SUSHI-USD", "YFI-USD", "ZRX-USD", "ANKR-USD",
    
    # --- MEME & SPECULATIVE ---
    "PEPE-USD", "BONK-USD", "FLOKI-USD", "WIF-USD", "LUNC-USD",
    
    # --- ESKİ TOPRAKLAR (KLASİKLER) ---
    "XMR-USD", "DASH-USD", "ZEC-USD", "BTT-USD", "RVN-USD", "WAVES-USD", "OMG-USD",
    "ICX-USD", "IOST-USD", "ONT-USD", "QTUM-USD", "SC-USD", "DGB-USD", "XVG-USD"
]
other_crypto.sort()
final_crypto_list = priority_crypto + other_crypto

raw_nasdaq = [
    "AAPL", "MSFT", "NVDA", "AMZN", "AVGO", "META", "TSLA", "GOOGL", "GOOG", "COST", 
    "NFLX", "AMD", "PEP", "LIN", "TMUS", "CSCO", "QCOM", "INTU", "AMAT", "TXN", 
    "HON", "AMGN", "BKNG", "ISRG", "CMCSA", "SBUX", "MDLZ", "GILD", "ADP", "ADI", 
    "REGN", "VRTX", "LRCX", "PANW", "MU", "KLAC", "SNPS", "CDNS", "MELI", "MAR", 
    "ORLY", "CTAS", "NXPI", "CRWD", "CSX", "PCAR", "MNST", "WDAY", "ROP", "AEP", 
    "ROKU", "ZS", "OKTA", "TEAM", "DDOG", "MDB", "SHOP", "EA", "TTD", "DOCU", 
    "INTC", "SGEN", "ILMN", "IDXX", "ODFL", "EXC", "ADSK", "PAYX", "CHTR", "MRVL", 
    "KDP", "XEL", "LULU", "ALGN", "VRSK", "CDW", "DLTR", "SIRI", "JBHT", "WBA", 
    "PDD", "JD", "BIDU", "NTES", "NXST", "MTCH", "UAL", "SPLK", "ANSS", "SWKS", 
    "QRVO", "AVTR", "FTNT", "ENPH", "SEDG", "BIIB", "CSGP", "ASTS"
]
raw_nasdaq = sorted(list(set(raw_nasdaq)))

commodities_list = [
    "GC=F",   # Altın ONS (Vadeli - Gold Futures) - 7/24 Aktif
    "SI=F",   # Gümüş ONS (Vadeli - Silver Futures)
    "HG=F",   # Bakır (Copper Futures) - CPER yerine bu daha iyidir
    "CL=F",   # WTI Petrol (Crude Oil WTI Futures) - ABD teslimatlı ham petrol
    "NG=F",   # Doğalgaz (Natural Gas Futures)
    "BZ=F"    # Brent Petrol (Brent Crude Futures) - Kuzey Denizi, küresel referans fiyatı
]

# --- BIST LİSTESİ (GENİŞLETİLMİŞ - BIST 500) ---
priority_bist_indices = [
    "XU100.IS", "XU030.IS", "XBANK.IS", "XTUMY.IS", "XUSIN.IS", "EREGL.IS", "SISE.IS", "TUPRS.IS",
    # BIST30 (alfabetik, yukarıdakiler hariç)
    "AKBNK.IS", "ARCLK.IS", "ASELS.IS", "BIMAS.IS", "CCOLA.IS", "EKGYO.IS", "ENKAI.IS",
    "FROTO.IS", "GARAN.IS", "GUBRF.IS", "HALKB.IS", "ISCTR.IS", "KCHOL.IS", "KOZAA.IS",
    "KRDMD.IS", "PETKM.IS", "PGSUS.IS", "SAHOL.IS", "SASA.IS", "TCELL.IS", "THYAO.IS",
    "TKFEN.IS", "TOASO.IS", "TTKOM.IS", "TTRAK.IS", "VAKBN.IS", "YKBNK.IS",
]

# Buraya BIST TUM'deki hisseleri ekliyoruz
raw_bist_stocks = [
    "A1CAP.IS", "ACSEL.IS", "ADEL.IS", "ADESE.IS", "ADGYO.IS", "AEFES.IS", "AFYON.IS", "AGESA.IS", "AGHOL.IS", "AGROT.IS", "AGYO.IS", "AHGAZ.IS", "AKBNK.IS", "AKCNS.IS", "AKENR.IS", "AKFGY.IS", "AKGRT.IS", "AKMGY.IS", "AKSA.IS", "AKSEN.IS", "AKSGY.IS", "AKSUE.IS", "AKYHO.IS", "ALARK.IS", "ALBRK.IS", "ALCAR.IS", "ALCTL.IS", "ALFAS.IS", "ALGYO.IS", "ALKA.IS", "ALKIM.IS", "ALMAD.IS", "ALTNY.IS", "ALVES.IS", "ANELE.IS", "ANGEN.IS", "ANHYT.IS", "ANSGR.IS", "ARASE.IS", "ARCLK.IS", "ARDYZ.IS", "ARENA.IS", "ARSAN.IS", "ARTMS.IS", "ARZUM.IS", "ASELS.IS", "ASGYO.IS", "ASTOR.IS", "ASUZU.IS", "ATAGY.IS", "ATAKP.IS", "ATATP.IS", "ATEKS.IS", "ATLAS.IS", "ATSYH.IS", "AVGYO.IS", "AVHOL.IS", "AVOD.IS", "AVPGY.IS", "AVTUR.IS", "AYCES.IS", "AYDEM.IS", "AYEN.IS", "AYES.IS", "AYGAZ.IS", "AZTEK.IS",
    "BAGFS.IS", "BAKAB.IS", "BALAT.IS", "BANVT.IS", "BARMA.IS", "BASCM.IS", "BASGZ.IS", "BAYRK.IS", "BEGYO.IS", "BERA.IS", "BERK.IS", "BEYAZ.IS", "BFREN.IS", "BIENY.IS", "BIGCH.IS", "BIMAS.IS", "BINBN.IS", "BINHO.IS", "BIOEN.IS", "BIZIM.IS", "BJKAS.IS", "BLCYT.IS", "BMSCH.IS", "BMSTL.IS", "BNTAS.IS", "BOBET.IS", "BORLS.IS", "BOSSA.IS", "BRISA.IS", "BRKO.IS", "BRKSN.IS", "BRKVY.IS", "BRLSM.IS", "BRMEN.IS", "BRSAN.IS", "BRYAT.IS", "BSOKE.IS", "BTCIM.IS", "BUCIM.IS", "BURCE.IS", "BURVA.IS", "BVSAN.IS", "BYDNR.IS",
    "CANTE.IS", "CATES.IS", "CCOLA.IS", "CELHA.IS", "CEMAS.IS", "CEMTS.IS", "CEOEM.IS", "CIMSA.IS", "CLEBI.IS", "CMBTN.IS", "CMENT.IS", "CONSE.IS", "COSMO.IS", "CRDFA.IS", "CRFSA.IS", "CUSAN.IS", "CVKMD.IS", "CWENE.IS",
    "DAGH.IS", "DAGI.IS", "DAPGM.IS", "DARDL.IS", "DENGE.IS", "DERHL.IS", "DERIM.IS", "DESA.IS", "DESPC.IS", "DEVA.IS", "DGATE.IS", "DGGYO.IS", "DGNMO.IS", "DIRIT.IS", "DITAS.IS", "DMSAS.IS", "DNISI.IS", "DOAS.IS", "DOBUR.IS", "DOCO.IS", "DOFER.IS", "DOGUB.IS", "DOHOL.IS", "DOKTA.IS", "DURDO.IS", "DYOBY.IS", "DZGYO.IS",
    "EBEBK.IS", "ECILC.IS", "ECZYT.IS", "EDATA.IS", "EDIP.IS", "EGEEN.IS", "EGEPO.IS", "EGGUB.IS", "EGPRO.IS", "EGSER.IS", "EKGYO.IS", "EKIZ.IS", "EKSUN.IS", "ELITE.IS", "EMKEL.IS", "EMNIS.IS", "ENJSA.IS", "ENKAI.IS", "ENSRI.IS", "ENTRA.IS", "EPLAS.IS", "ERBOS.IS", "ERCB.IS", "EREGL.IS", "ERSU.IS", "ESCAR.IS", "ESCOM.IS", "ESEN.IS", "ETILR.IS", "ETYAT.IS", "EUHOL.IS", "EUKYO.IS", "EUPWR.IS", "EUREN.IS", "EUYO.IS", "EYGYO.IS",
    "FADE.IS", "FENER.IS", "FLAP.IS", "FMIZP.IS", "FONET.IS", "FORMT.IS", "FORTE.IS", "FRIGO.IS", "FROTO.IS", "FZLGY.IS",
    "GARAN.IS", "GARFA.IS", "GEDIK.IS", "GEDZA.IS", "GENIL.IS", "GENTS.IS", "GEREL.IS", "GESAN.IS", "GLBMD.IS", "GLCVY.IS", "GLRYH.IS", "GLYHO.IS", "GMTAS.IS", "GOKNR.IS", "GOLTS.IS", "GOODY.IS", "GOZDE.IS", "GRNYO.IS", "GRSEL.IS", "GSDDE.IS", "GSDHO.IS", "GSRAY.IS", "GUBRF.IS", "GWIND.IS", "GZNMI.IS",
    "HALKB.IS", "HATEK.IS", "HATSN.IS", "HDFGS.IS", "HEDEF.IS", "HEKTS.IS", "HKTM.IS", "HLGYO.IS", "HRKET.IS", "HTTBT.IS", "HUBVC.IS", "HUNER.IS", "HURGZ.IS",
    "ICBCT.IS", "ICUGS.IS", "IDGYO.IS", "IEYHO.IS", "IHAAS.IS", "IHEVA.IS", "IHGZT.IS", "ILVE.IS", "IMASM.IS", "INDES.IS", "INFO.IS", "INGRM.IS", "INTEM.IS", "INVEO.IS", "INVES.IS", "IPEKE.IS", "ISATR.IS", "ISBIR.IS", "ISBTR.IS", "ISCTR.IS", "ISDMR.IS", "ISFIN.IS", "ISGSY.IS", "ISGYO.IS", "ISKPL.IS", "ISKUR.IS", "ISMEN.IS", "ISSEN.IS", "ISYAT.IS", "ITTFH.IS", "IZENR.IS", "IZFAS.IS", "IZINV.IS", "IZMDC.IS",
    "JANTS.IS", "TRALT.IS", "ONRYT.IS", "EFOR.IS", "OZATD.IS",
    "KAPLM.IS", "KAREL.IS", "KARSN.IS", "KARYE.IS", "KATMR.IS", "KAYSE.IS", "KCAER.IS", "KCHOL.IS", "KENT.IS", "KERVN.IS", "KERVT.IS", "KFEIN.IS", "KGYO.IS", "KIMMR.IS", "KLGYO.IS", "KLKIM.IS", "KLMSN.IS", "KLNMA.IS", "KLSER.IS", "KLRHO.IS", "KMPUR.IS", "KNFRT.IS", "KOCMT.IS", "KONKA.IS", "KONTR.IS", "KONYA.IS", "KOPOL.IS", "KORDS.IS", "KOTON.IS", "KOZAA.IS", "KOZAL.IS", "KRDMA.IS", "KRDMB.IS", "KRDMD.IS", "KRGYO.IS", "KRONT.IS", "KRPLS.IS", "KRSTL.IS", "KRTEK.IS", "KRVGD.IS", "KSTUR.IS", "KTLEV.IS", "KTSKR.IS", "KUTPO.IS", "KUVVA.IS", "KUYAS.IS", "KZBGY.IS", "KZGYO.IS",
    "LIDER.IS", "LIDFA.IS", "LILAK.IS", "LINK.IS", "LKMNH.IS", "LMKDC.IS", "LOGO.IS", "LUKSK.IS",
    "MAALT.IS", "MACKO.IS", "MAGEN.IS", "MAKIM.IS", "MAKTK.IS", "MANAS.IS", "MARBL.IS", "MARKA.IS", "MARTI.IS", "MAVI.IS", "MEDTR.IS", "MEGAP.IS", "MEGMT.IS", "MEKAG.IS", "MEPET.IS", "MERCN.IS", "MERIT.IS", "MERKO.IS", "METEM.IS", "METRO.IS", "METUR.IS", "MGROS.IS", "MIATK.IS", "MIPAZ.IS", "MMCAS.IS", "MNDRS.IS", "MNDTR.IS", "MOBTL.IS", "MOGAN.IS", "MPARK.IS", "MRGYO.IS", "MRSHL.IS", "MSGYO.IS", "MTRKS.IS", "MTRYO.IS", "MZHLD.IS",
    "NATEN.IS", "NETAS.IS", "NIBAS.IS", "NTGAZ.IS", "NUGYO.IS", "NUHCM.IS",
    "OBASE.IS", "OBAMS.IS", "ODAS.IS", "ODINE.IS", "OFSYM.IS", "ONCSM.IS", "ORCA.IS", "ORGE.IS", "ORMA.IS", "OSMEN.IS", "OSTIM.IS", "OTKAR.IS", "OTTO.IS", "OYAKC.IS", "OYAYO.IS", "OYLUM.IS", "OYYAT.IS", "OZGYO.IS", "OZKGY.IS", "OZRDN.IS", "OZSUB.IS",
    "PAGYO.IS", "PAMEL.IS", "PAPIL.IS", "PARSN.IS", "PASEU.IS", "PCILT.IS", "PEGYO.IS", "PEKGY.IS", "PENGD.IS", "PENTA.IS", "PETKM.IS", "PETUN.IS", "PGSUS.IS", "PINSU.IS", "PKART.IS", "PKENT.IS", "PLAT.IS", "PNLSN.IS", "POLHO.IS", "POLTK.IS", "PRDGS.IS", "PRKAB.IS", "PRKME.IS", "PRZMA.IS", "PSDTC.IS", "PSGYO.IS", "PTEK.IS",
    "QNBFB.IS", "QNBFL.IS", "QUAGR.IS", "PLTUR.IS", "PATEK.IS",
    "RALYH.IS", "RAYSG.IS", "REEDR.IS", "RGYAS.IS", "RNPOL.IS", "RODRG.IS", "ROYAL.IS", "RTALB.IS", "RUBNS.IS", "RYGYO.IS", "RYSAS.IS",
    "SAFKR.IS", "SAHOL.IS", "SAMAT.IS", "SANEL.IS", "SANFM.IS", "SANKO.IS", "SARKY.IS", "SASA.IS", "SAYAS.IS", "SDTTR.IS", "SEGYO.IS", "SEKFK.IS", "SEKUR.IS", "SELEC.IS", "SELGD.IS", "SELVA.IS", "SEYKM.IS", "SILVR.IS", "SISE.IS", "SKBNK.IS", "SKTAS.IS", "SKYMD.IS", "SMART.IS", "SMRTG.IS", "SNGYO.IS", "SNICA.IS", "SNKRN.IS", "SNPAM.IS", "SODSN.IS", "SOKE.IS", "SOKM.IS", "SONME.IS", "SRVGY.IS", "SUMAS.IS", "SUNTK.IS", "SURGY.IS", "SUWEN.IS", "SYS.IS",
    "TABGD.IS", "TARAF.IS", "TATGD.IS", "TAVHL.IS", "TBORG.IS", "TCELL.IS", "TDGYO.IS", "TEKTU.IS", "TERA.IS", "TETMT.IS", "TEZOL.IS", "TGSAS.IS", "THYAO.IS", "TKFEN.IS", "TKNSA.IS", "TLMAN.IS", "TMPOL.IS", "TMSN.IS", "TNZTP.IS", "TOASO.IS", "TRCAS.IS", "TRGYO.IS", "TRILC.IS", "TSGYO.IS", "TSKB.IS", "TSPOR.IS", "TTKOM.IS", "TTRAK.IS", "TUCLK.IS", "TUKAS.IS", "TUPRS.IS", "TUREX.IS", "TURGG.IS", "TURSG.IS",
    "UFUK.IS", "ULAS.IS", "ULKER.IS", "ULUFA.IS", "ULUSE.IS", "ULUUN.IS", "UMPAS.IS", "UNLU.IS", "USAK.IS", "UZERB.IS", "TATEN.IS",
    "VAKBN.IS", "VAKFN.IS", "VAKKO.IS", "VANGD.IS", "VBTYZ.IS", "VERUS.IS", "VESBE.IS", "VESTL.IS", "VKFYO.IS", "VKGYO.IS", "VKING.IS", "VRGYO.IS",
    "YAPRK.IS", "YATAS.IS", "YAYLA.IS", "YBTAS.IS", "YEOTK.IS", "YESIL.IS", "YGGYO.IS", "YGYO.IS", "YKBNK.IS", "YKSLN.IS", "YONGA.IS", "YUNSA.IS", "YYAPI.IS", "YYLGD.IS",
    "ZEDUR.IS", "ZOREN.IS", "ZRGYO.IS", "GIPTA.IS", "TEHOL.IS", "PAHOL.IS", "MARMR.IS", "BIGEN.IS", "GLRMK.IS", "TRHOL.IS", "AAGYO.IS"
]

# Kopyaları Temizle ve Birleştir
raw_bist_stocks = list(set(raw_bist_stocks) - set(priority_bist_indices))
raw_bist_stocks.sort()
final_bist100_list = priority_bist_indices + raw_bist_stocks

ASSET_GROUPS = {
    "BIST 500 ": final_bist100_list,
    "S&P 500": final_sp500_list,
    "NASDAQ-100": raw_nasdaq,
    "KRİPTO": final_crypto_list,
    "EMTİALAR": commodities_list
}
INITIAL_CATEGORY = "BIST 500 "

# --- GÖRÜNTÜ ADI SÖZLÜĞÜ ---
# Yahoo ticker kodu → Kullanıcıya gösterilecek isim
TICKER_DISPLAY_NAMES = {
    "GC=F":    "ONS ALTIN",
    "SI=F":    "GÜMÜŞ",
    "CL=F":    "WTI PETROL",
    "BZ=F":    "BRENT PETROL",
    "NG=F":    "DOĞAL GAZ",
    "HG=F":    "BAKIR",
    "ZW=F":    "BUĞDAY",
    "ZC=F":    "MISIR",
    "BTC-USD": "BITCOIN",
    "ETH-USD": "ETHEREUM",
    "BNB-USD": "BNB",
    "SOL-USD": "SOLANA",
    "XRP-USD": "XRP",
    "DOGE-USD":"DOGECOIN",
    "AVAX-USD":"AVALANCHE",
    "^GSPC":   "S&P 500",
    "^IXIC":   "NASDAQ",
    "^DJI":    "DOW JONES",
}

def get_display_name(ticker):
    """Ticker kodunu görüntü adına çevirir. Bilinmiyorsa temiz kodu döner."""
    if ticker in TICKER_DISPLAY_NAMES:
        return TICKER_DISPLAY_NAMES[ticker]
    return ticker.split('.')[0].replace("=F", "").replace("-USD", "")

# ==============================================================================
# BÖLÜM 5 — SESSION STATE VE CALLBACK YÖNETİMİ
# Streamlit oturum değişkenleri, kategori/varlık değişim olayları
# ve izleme listesi toggle fonksiyonları.
# ==============================================================================
# --- STATE YÖNETİMİ ---
if 'category' not in st.session_state: st.session_state.category = INITIAL_CATEGORY
if 'ticker' not in st.session_state: st.session_state.ticker = "XU100.IS"
if 'scan_data' not in st.session_state: st.session_state.scan_data = None
if 'generate_prompt' not in st.session_state: st.session_state.generate_prompt = False
if 'radar2_data' not in st.session_state: st.session_state.radar2_data = None
if 'watchlist' not in st.session_state: st.session_state.watchlist = load_watchlist_db()
if 'accum_data' not in st.session_state: st.session_state.accum_data = None
if 'minervini_data' not in st.session_state: st.session_state.minervini_data = None

# --- CALLBACKLER ---
def on_category_change():
    new_cat = st.session_state.get("selected_category_key")
    if new_cat and new_cat in ASSET_GROUPS:
        st.session_state.category = new_cat
        st.session_state.ticker = ASSET_GROUPS[new_cat][0]
        st.session_state.scan_data = None
        st.session_state.radar2_data = None
        st.session_state.accum_data = None

def on_asset_change():
    new_asset = st.session_state.get("selected_asset_key")
    if new_asset: st.session_state.ticker = new_asset

def on_manual_button_click():
    if st.session_state.manual_input_key:
        st.session_state.ticker = st.session_state.manual_input_key.upper()

def on_scan_result_click(symbol): 
    st.session_state.ticker = symbol

def toggle_watchlist(symbol):
    wl = st.session_state.watchlist
    if symbol in wl:
        remove_watchlist_db(symbol)
        wl.remove(symbol)
    else:
        add_watchlist_db(symbol)
        wl.append(symbol)
    st.session_state.watchlist = wl

# ==============================================================================
# BÖLÜM 6 — VERİ ÇEKME VE ÖNBELLEKLEME MOTORU
# Yahoo Finance, Binance ve İş Yatırım'dan veri çekme fonksiyonları.
# Parquet önbellek, canlı fiyat yaması ve retry mekanizması burada.
# ==============================================================================

def apply_volume_projection(df, ticker=""):
    if df is None or df.empty or 'Volume' not in df.columns:
        return df

    # Türkiye saatini güvenli şekilde al
    try:
        from datetime import timezone, timedelta
        _tz_tr = timezone(timedelta(hours=3))
        now = datetime.now(_tz_tr).replace(tzinfo=None)
    except Exception:
        now = datetime.now()

    # Hafta sonuysa projeksiyon yapma
    if now.weekday() >= 5:
        return df

    # Elimizdeki son veri BUGÜNE mi ait? Değilse dokunma.
    last_date = df.index[-1].date()
    if last_date != now.date():
        return df

    now_min = now.hour * 60 + now.minute
    current_volume = float(df['Volume'].iloc[-1])

    # KARANTİNA KONTROLÜ: Hacim sıfır veya anlamsız küçükse projeksiyon yapma
    if current_volume < 100:
        return df

    progress = 1.0

    # PİYASA TÜRÜNE GÖRE DİNAMİK AĞIRLIK HARİTASI (U-Şekilli)
    if "-USD" in ticker:
        # Kripto 24 saat kesintisiz (doğrusal akışa daha uygundur)
        progress = max(0.01, now_min / 1440)

    elif "^" in ticker or (not ".IS" in ticker and not ticker.startswith("XU")):
        # ABD Piyasası 16:30-23:00 TR Saati (Toplam 390 dakika)
        open_min = 16 * 60 + 30
        close_min = 23 * 60
        if now_min < open_min:
            return df
        if now_min >= close_min:
            progress = 1.0
        else:
            elapsed = now_min - open_min
            # İlk 60 dk: çarpan >4x — güvenilmez, projeksiyon yapma
            if elapsed < 60:
                return df
            # U-Şeklinde Ağırlık: İlk 60 dk %25 | Orta 270 dk %40 | Son 60 dk %35
            if elapsed <= 60:
                progress = (elapsed / 60) * 0.25
            elif elapsed <= 330:
                progress = 0.25 + ((elapsed - 60) / 270) * 0.40
            else:
                progress = 0.65 + ((elapsed - 330) / 60) * 0.35

    else:
        # BIST 09:55-18:15 TR Saati (Toplam 500 dakika)
        open_min = 9 * 60 + 55
        close_min = 18 * 60 + 15
        if now_min < open_min:
            return df
        if now_min >= close_min:
            progress = 1.0
        else:
            elapsed = now_min - open_min
            # İlk 60 dk: çarpan >5x — güvenilmez, projeksiyon yapma
            if elapsed < 60:
                return df
            # U-Şeklinde Ağırlık: İlk 120 dk %40 | Orta 260 dk %20 | Son 120 dk %40
            if elapsed <= 120:
                progress = (elapsed / 120) * 0.40
            elif elapsed <= 380:
                progress = 0.40 + ((elapsed - 120) / 260) * 0.20
            else:
                progress = 0.60 + ((elapsed - 380) / 120) * 0.40

    # Güvenlik kilidi: çarpan çok uçuk çıkmasın
    progress = max(0.05, min(progress, 1.0))

    df_proj = df.copy()
    projected_volume = current_volume / progress
    df_proj.loc[df_proj.index[-1], 'Volume'] = projected_volume

    return df_proj

@st.cache_data(ttl=3600)
def get_benchmark_data(category):
    """
    Seçili kategoriye göre Endeks verisini (S&P 500 veya BIST 100) çeker.
    RS (Göreceli Güç) hesaplaması için referans noktasıdır.
    """
    try:
        # Kategoriye göre sembol seçimi
        ticker = "XU100.IS" if "BIST" in category else "^GSPC"
        
        # Hisse verileriyle uyumlu olması için 1 yıllık çekiyoruz
        df = yf.download(ticker, period="1y", progress=False, auto_adjust=True, prepost=False)
        
        if df.empty: return None
        return df['Close']
    except:
        return None

# --- GLOBAL DATA CACHE KATMANI ---
@st.cache_data(ttl=900, show_spinner=False)
def get_batch_data_cached(asset_list, period="1y"):
    """
    GATEKEEPER DESTEKLİ TOPLU TARAMA MOTORU - MULTIINDEX HATASI GİDERİLDİ
    """
    if not asset_list:
        return pd.DataFrame()
        
    missing_assets = []
    combined_dict = {}
    
    for sym in asset_list:
        clean_sym = sym.replace(".IS", "")
        if "BIST" in sym or ".IS" in sym or sym.startswith("XU"):
            clean_sym = sym if sym.endswith(".IS") else f"{sym}.IS"
        file_path = os.path.join(CACHE_DIR, f"{clean_sym}_1d.parquet")

        needs_download = True
        if os.path.exists(file_path):
            try:
                df_cached = pd.read_parquet(file_path)
                if not df_cached.empty:
                    if not is_yahoo_update_needed(sym, df_cached.index[-1]):
                        # 👇 ESKİ HALİ: combined_dict[sym] = df_cached.tail(500)
                        # 👇 YENİ HALİ:
                        df_ready = df_cached.tail(500).copy()
                        combined_dict[sym] = apply_volume_projection(df_ready, sym)
                        needs_download = False
            except: pass
        if needs_download:
            missing_assets.append(sym)

    if missing_assets:
        df_new = _yf_download_with_retry(
            " ".join(missing_assets),
            period="1y", group_by='ticker', threads=True, prepost=False
        )

        for sym in missing_assets:
            clean_sym = sym.replace(".IS", "")
            if "BIST" in sym or ".IS" in sym or sym.startswith("XU"):
                clean_sym = sym if sym.endswith(".IS") else f"{sym}.IS"

            try:
                # MULTIINDEX TEMİZLİĞİ: Katmanları tekilleştir
                if len(missing_assets) > 1 and isinstance(df_new.columns, pd.MultiIndex):
                    df_sym_new = df_new.xs(sym, axis=1, level=1).copy() if sym in df_new.columns.get_level_values(1) else df_new[sym].copy()
                else:
                    df_sym_new = df_new.copy()
                
                if isinstance(df_sym_new.columns, pd.MultiIndex):
                    if 'Close' in df_sym_new.columns.get_level_values(0):
                        df_sym_new.columns = df_sym_new.columns.get_level_values(0)
                    else:
                        df_sym_new.columns = df_sym_new.columns.get_level_values(1)
                
                df_sym_new = df_sym_new.loc[:, ~df_sym_new.columns.duplicated()]
                df_sym_new.columns = [str(c).capitalize() for c in df_sym_new.columns]
                if 'Volume' not in df_sym_new.columns or df_sym_new['Volume'].isna().all():
                    df_sym_new['Volume'] = 0.0  # KARANTİNA: Sahte hacim yerine sıfır atıyoruz

                df_sym_new = df_sym_new.dropna(subset=['Close'])
                df_sym_new.index = df_sym_new.index.tz_localize(None) # Zaman dilimi çakışmasını önle
                
                file_path = os.path.join(CACHE_DIR, f"{clean_sym}_1d.parquet")
                
                # Doğrudan yeni gelen düzeltilmiş veriyi kaydediyoruz
                df_sym_new.to_parquet(file_path) 
                
                df_ready = df_sym_new.tail(500).copy()
                combined_dict[sym] = apply_volume_projection(df_ready, sym)
                
            except Exception as e: 
                continue

    if combined_dict:
        return pd.concat(combined_dict.values(), axis=1, keys=combined_dict.keys())
    return pd.DataFrame()

# ── BİNANCE VERİ ÇEKİCİ (KRİPTO PARALAR İÇİN) ──────────────────────
def _fetch_from_binance(ticker, limit=730):
    """
    Kripto tickerları (-USD) için Binance API'den günlük mum verisi çeker.
    Dönen DataFrame yfinance formatıyla birebir uyumludur (Open/High/Low/Close/Volume + Date index).
    Binance'de coin yoksa veya hata olursa None döner → yfinance'e fallback yapılır.
    """
    import requests as _req
    symbol = ticker.replace("-USD", "USDT").upper()   # BTC-USD → BTCUSDT
    url = (
        f"https://api.binance.com/api/v3/klines"
        f"?symbol={symbol}&interval=1d&limit={limit}"
    )
    try:
        resp = _req.get(url, timeout=8)
        data = resp.json()
        # Binance coin bulamazsa {'code': -1121, 'msg': ...} döndürür
        if isinstance(data, dict) and "code" in data:
            return None
        if not data:
            return None

        cols = [
            "timestamp", "Open", "High", "Low", "Close", "Volume",
            "close_time", "quote_vol", "trades",
            "taker_base", "taker_quote", "ignore"
        ]
        df = pd.DataFrame(data, columns=cols)
        for c in ["Open", "High", "Low", "Close", "Volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["Date"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("Date", inplace=True)
        df.index = df.index.tz_localize(None)
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        return df

    except Exception:
        return None
# ─────────────────────────────────────────────────────────────────────

# ── YF RETRY HELPER ──────────────────────────────────────────────────
def _yf_download_with_retry(ticker, max_tries=3, base_delay=1.5, **kwargs):
    """
    yf.download() için retry sarmalayıcı.
    max_tries kez dener; her başarısızlıkta bekleme süresi katlanır.
    Tüm denemeler başarısızsa boş DataFrame döner.
    """
    import time
    last_exc = None
    for attempt in range(max_tries):
        try:
            df = yf.download(ticker, progress=False, auto_adjust=True, **kwargs)
            if not df.empty:
                return df
        except Exception as e:
            last_exc = e
        if attempt < max_tries - 1:
            time.sleep(base_delay * (attempt + 1))   # 1.5s, 3s, 4.5s
    if last_exc:
        import logging
        logging.warning(f"[yf_retry] {ticker} — {max_tries} denemede veri alınamadı: {last_exc}")
    return pd.DataFrame()
# ─────────────────────────────────────────────────────────────────────

# --- CANLI FİYAT YARDIMCI FONKSİYONLARI ---
@st.cache_data(ttl=60)
def get_live_price(ticker: str) -> float:
    """fast_info üzerinden canlı fiyat çeker. 60 sn cache'li.
    Not: fast_info bir dict değil, object — .get() yerine attribute erişimi kullanılıyor."""
    try:
        fi = yf.Ticker(ticker).fast_info
        # Önce last_price, yoksa regular_market_price dene (attribute, dict değil)
        for _attr in ("last_price", "regular_market_price", "previousClose"):
            try:
                _v = getattr(fi, _attr, None)
                if _v is not None and float(_v) > 0:
                    return float(_v)
            except Exception:
                continue
        # Fallback: info dict'ten dene (daha yavaş ama güvenli)
        _info2 = yf.Ticker(ticker).info
        for _key in ("currentPrice", "regularMarketPrice", "previousClose"):
            _v = _info2.get(_key)
            if _v and float(_v) > 0:
                return float(_v)
        return 0.0
    except Exception:
        return 0.0

def _patch_live_price(df: pd.DataFrame, ticker: str, interval: str = "1d") -> pd.DataFrame:
    """
    Günlük veri için son satırın Close fiyatını canlı fiyatla günceller.
    Grafik ile fiyat kutusu arasındaki cache kaynaklı uyuşmazlığı giderir.
    Sadece interval='1d' için çalışır; intraday verilere dokunmaz.
    """
    if interval != "1d" or df is None or df.empty:
        return df
    try:
        _live = get_live_price(ticker)
        if _live <= 0:
            return df

        _now   = datetime.now(_TZ_ISTANBUL)
        _today = _now.date()
        # Cumartesi=5, Pazar=6 → hafta sonu
        _is_weekend = (_now.weekday() >= 5)

        _last_date = df.index[-1]
        if hasattr(_last_date, "date"):
            _last_date = _last_date.date()

        if _last_date == _today:
            # Bugünün satırı var → Close/High/Low'u canlı fiyatla güncelle
            df = df.copy()
            df.loc[df.index[-1], "Close"] = _live
            df.loc[df.index[-1], "High"]  = max(float(df.loc[df.index[-1], "High"]),  _live)
            df.loc[df.index[-1], "Low"]   = min(float(df.loc[df.index[-1], "Low"]),   _live)

        elif _is_weekend:
            # ── HAFTA SONU: Sahte cumartesi/pazar barı EKLEME ──
            # Piyasa kapalı; son barın (Cuma) kapanışını gerçek son fiyatla güncelle.
            # Bu sayede parquet gün içinde yazılmışsa eksik kapanış düzelir.
            df = df.copy()
            df.loc[df.index[-1], "Close"] = _live
            # High'ı ancak _live daha büyükse yukarı çek (aşağı çekme)
            df.loc[df.index[-1], "High"]  = max(float(df.loc[df.index[-1], "High"]), _live)

        else:
            # Hafta içi + bugünün satırı yok → yeni satır ekle (seans açık, veri henüz gelmemiş)
            df = df.copy()
            new_row = df.iloc[-1].copy()
            new_row["Close"]  = _live
            new_row["Open"]   = _live
            new_row["High"]   = _live
            new_row["Low"]    = _live
            new_row["Volume"] = 0.0
            try:
                _tz = df.index.tz if df.index.tz is not None else None
                _new_idx = pd.Timestamp(_today)
                if _tz is not None:
                    _new_idx = _new_idx.tz_localize(_tz)
            except Exception:
                _new_idx = pd.Timestamp(_today)
            # ÖNEMLİ: index name'i koru — yoksa reset_index() 'Date' kolonunu kaybeder
            _orig_name = df.index.name
            _new_df = pd.DataFrame([new_row], index=[_new_idx])
            _new_df.index.name = _orig_name
            df = pd.concat([df, _new_df])

    except Exception as _pe:
        import logging
        logging.warning(f"[patch_live_price] {ticker} — {_pe}")
    return df

# --- SINGLE STOCK CACHE (DETAY SAYFASI İÇİN) ---
# _get_safe_historical_data_cached: saf OHLCV verisi, 300 sn cache'li
# get_safe_historical_data: wrapper — cache sonrasına canlı fiyat patch'i uygular
@st.cache_data(ttl=300)
def _get_safe_historical_data_cached(ticker, period="1y", interval="1d"):
    try:
        # ── KRİPTO YÖNLENDİRMESİ: -USD tickerları Binance'den çek ──
        if "-USD" in ticker and interval == "1d":
            clean_ticker = ticker.upper()
            file_path = os.path.join(CACHE_DIR, f"{clean_ticker}_1d.parquet")

            # Cache varsa ve güncel ise doğrudan kullan
            if os.path.exists(file_path):
                df_cached = pd.read_parquet(file_path)
                if not is_yahoo_update_needed(ticker, df_cached.index[-1]):
                    return apply_volume_projection(df_cached.tail(500).copy(), ticker)

            # Binance'den taze veri çek
            df_bnc = _fetch_from_binance(ticker, limit=730)
            if df_bnc is not None and not df_bnc.empty:
                df_bnc.to_parquet(file_path)
                return apply_volume_projection(df_bnc.tail(500).copy(), ticker)

            # Binance başarısız → yfinance'e fallback (coin bazı API'lerde farklı formatta olabilir)
            # (aşağıdaki genel yfinance akışına düşer)
        # ─────────────────────────────────────────────────────────────

        clean_ticker = ticker.replace(".IS", "")
        if "BIST" in ticker or ".IS" in ticker or ticker.startswith("XU"):
            clean_ticker = ticker if ticker.endswith(".IS") else f"{ticker}.IS"
        file_path = os.path.join(CACHE_DIR, f"{clean_ticker}_{interval}.parquet")

        def safe_clean_columns(df):
            if isinstance(df.columns, pd.MultiIndex):
                if 'Close' in df.columns.get_level_values(0):
                    df.columns = df.columns.get_level_values(0)
                else:
                    df.columns = df.columns.get_level_values(1)
            df = df.loc[:, ~df.columns.duplicated()].copy()
            df.columns = [str(c).capitalize() for c in df.columns]
            if 'Volume' not in df.columns or df['Volume'].isna().all():
                df['Volume'] = 0.0  # KARANTİNA: Sahte 1.0 atamasını iptal ettik
            return df

        import datetime as _dt
        _start = str(_dt.date.today() - _dt.timedelta(days=380))
        _end   = str(_dt.date.today() + _dt.timedelta(days=1))

        if os.path.exists(file_path):
            df_cached = pd.read_parquet(file_path)
            df_cached = safe_clean_columns(df_cached)
            # tz-aware ise convert, tz-naive ise dokunma (her iki durumda güvenli)
            if df_cached.index.tz is not None:
                df_cached.index = df_cached.index.tz_convert(None)

            # Hacim bozukluğu kontrolü: son non-zero hacim 5+ gün eskiyse yenile
            _vol_stale = _volume_is_stale(df_cached, ticker)

            if not is_yahoo_update_needed(ticker, df_cached.index[-1]) and not _vol_stale:
                return apply_volume_projection(df_cached.tail(500).copy(), ticker)

            # Güncelleme gerekiyor — retry ile dene
            df_new = _yf_download_with_retry(
                clean_ticker, start=_start, end=_end, interval=interval
            )
            if not df_new.empty:
                df_new = safe_clean_columns(df_new)
                if df_new.index.tz is not None:
                    df_new.index = df_new.index.tz_convert(None)
                # İSYATIRIM HACIM ENTEGRASYONU (BIST hisseleri, endeks hariç)
                # yfinance Volume=0 bug'ını köklü çözer: Volume sütununu İş Yatırım'dan al.
                _is_bist_stock = (".IS" in ticker or "BIST" in ticker) and not ticker.startswith(("XU", "XB", "XT"))
                if _is_bist_stock and interval == "1d":
                    _isy_vol = _fetch_bist_volume_isyatirim(clean_ticker, _start, _end)
                    if _isy_vol is not None and len(_isy_vol) > 0:
                        _common = df_new.index.intersection(_isy_vol.index)
                        if len(_common) > 0:
                            df_new.loc[_common, 'Volume'] = _isy_vol.loc[_common]
                    else:
                        # isyatirimhisse başarısız → eski parquet hacim koruması
                        if 'Volume' in df_cached.columns:
                            _ci = df_new.index.intersection(df_cached.index)
                            if len(_ci) > 0:
                                _nz = df_new.loc[_ci, 'Volume'] == 0
                                _ov = df_cached.loc[_ci, 'Volume'] > 0
                                _rx = _ci[_nz & _ov]
                                if len(_rx) > 0:
                                    df_new.loc[_rx, 'Volume'] = df_cached.loc[_rx, 'Volume']
                df_new.to_parquet(file_path)
                return apply_volume_projection(df_new.tail(500).copy(), ticker)

            # Retry başarısız → eski cache'i döndür + staleness işareti
            _stale_days = (datetime.now(_TZ_ISTANBUL).date() - df_cached.index[-1].date())
            st.session_state['_data_stale'] = {
                'ticker': ticker,
                'days':   _stale_days.days,
                'last':   df_cached.index[-1].strftime('%d.%m.%Y'),
            }
            return apply_volume_projection(df_cached.tail(500).copy(), ticker)

        else:
            # Parquet yok — önce start/end ile dene, başarısız olursa period ile dene
            df_full = _yf_download_with_retry(
                clean_ticker, start=_start, end=_end, interval=interval
            )
            if df_full.empty:
                # start/end başarısız → period="1y" ile dene (Yahoo farklı davranıyor)
                import logging as _log2
                _log2.warning(f"[get_hist] {clean_ticker} start/end başarısız → period='1y' fallback")
                df_full = _yf_download_with_retry(
                    clean_ticker, period="1y", interval=interval
                )
            if not df_full.empty:
                df_full = safe_clean_columns(df_full)
                # tz-aware ise convert, tz-naive ise dokunma
                if df_full.index.tz is not None:
                    df_full.index = df_full.index.tz_convert(None)
                # İSYATIRIM HACIM ENTEGRASYONU (BIST hisseleri, endeks hariç)
                _is_bist_stock = (".IS" in ticker or "BIST" in ticker) and not ticker.startswith(("XU", "XB", "XT"))
                if _is_bist_stock and interval == "1d":
                    _isy_vol = _fetch_bist_volume_isyatirim(clean_ticker, _start, _end)
                    if _isy_vol is not None and len(_isy_vol) > 0:
                        _common = df_full.index.intersection(_isy_vol.index)
                        if len(_common) > 0:
                            df_full.loc[_common, 'Volume'] = _isy_vol.loc[_common]
                    # isyatirimhisse başarısız → _fix_stale_volume fallback
                    elif _volume_is_stale(df_full, ticker):
                        df_full = _fix_stale_volume(df_full, clean_ticker, interval)
                df_full.to_parquet(file_path)
                return apply_volume_projection(df_full.tail(500).copy(), ticker)
            else:
                # Her iki yöntem de başarısız — session_state'e hata işareti bırak
                st.session_state['_data_stale'] = {
                    'ticker': ticker,
                    'days':   999,
                    'last':   'Hiç çekilemedi',
                }

        return None

    except Exception as e:
        import logging
        logging.warning(f"[get_safe_historical_data] {ticker} hata: {e}")
        return None

def _ensure_parquet_on_disk(ticker: str, interval: str = "1d") -> None:
    """
    @st.cache_data YAN ETKİ SORUNU İÇİN GÜVENLİK NETI:
    Cached fonksiyon bellekten döndüğünde parquet yazılmayabilir.
    Bu fonksiyon cache DIŞINDA çalışır — her çağrıda parquet var mı kontrol eder,
    yoksa indirir ve kaydeder. Crypto ve intraday atlanır.
    """
    # Sadece günlük ve kripto olmayan tickerlar için
    if interval != "1d" or "-USD" in ticker:
        return
    try:
        # file_path'i hesapla (cached fonksiyonla aynı mantık)
        _ct = ticker.replace(".IS", "")
        if "BIST" in ticker or ".IS" in ticker or ticker.startswith("XU"):
            _ct = ticker if ticker.endswith(".IS") else f"{ticker}.IS"
        _fp = os.path.join(CACHE_DIR, f"{_ct}_{interval}.parquet")

        if os.path.exists(_fp):
            return  # Zaten var → işlem yok

        import datetime as _dt2
        _s = str(_dt2.date.today() - _dt2.timedelta(days=380))
        _e = str(_dt2.date.today() + _dt2.timedelta(days=1))

        _df = _yf_download_with_retry(_ct, start=_s, end=_e, interval=interval)
        if _df.empty:
            _df = _yf_download_with_retry(_ct, period="1y", interval=interval)
        if _df.empty:
            return

        # Sütunları temizle
        if isinstance(_df.columns, pd.MultiIndex):
            _lvl = _df.columns.get_level_values(0)
            _df.columns = _lvl if "Close" in _lvl else _df.columns.get_level_values(1)
        _df = _df.loc[:, ~_df.columns.duplicated()].copy()
        _df.columns = [str(c).capitalize() for c in _df.columns]
        if "Volume" not in _df.columns or _df["Volume"].isna().all():
            _df["Volume"] = 0.0
        # Timezone temizle
        if _df.index.tz is not None:
            _df.index = _df.index.tz_convert(None)
        # Kaydet
        _df.to_parquet(_fp)
        import logging as _lg2
        _lg2.info(f"[ensure_parquet] {_ct} yazildi: {len(_df)} satir")
    except Exception as _ep:
        import logging as _lg3
        _lg3.warning(f"[ensure_parquet] {ticker} hata: {_ep}")


def get_safe_historical_data(ticker, period="1y", interval="1d"):
    """
    Public wrapper — önce cache'li OHLCV'yi alır, sonra
    cache dışında canlı fiyat patch'ini uygular.
    Bu sayede fiyat her render'da güncellenir, OHLCV cache'i bozulmaz.

    _ensure_parquet_on_disk: cache'in bellek sonucu döndürdüğü durumlarda
    parquet'in diske yazılmasını garanti eder.
    """
    _ensure_parquet_on_disk(ticker, interval)
    df = _get_safe_historical_data_cached(ticker, period=period, interval=interval)
    return _patch_live_price(df, ticker, interval)

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

@st.cache_data(ttl=900)
def get_ma_data_for_ui(ticker):
    """Arayüzdeki 4. sütun için hızlıca EMA ve SMA verilerini hesaplar."""
    try:
        # Son 1 yıllık veriyi hızlıca çek
        df = yf.download(ticker, period="1y", interval="1d", progress=False, auto_adjust=True, prepost=False)
        if df.empty: 
            return None
        
        # yfinance bazen MultiIndex döndürebilir, bunu güvenli hale getirelim
        if isinstance(df.columns, pd.MultiIndex):
            close_col = df['Close'][ticker] if ticker in df['Close'] else df['Close'].iloc[:, 0]
        else:
            close_col = df['Close']
            
        close = float(close_col.iloc[-1])
        
        # EMA Hesaplamaları
        ema5 = float(close_col.ewm(span=5, adjust=False).mean().iloc[-1])
        ema8 = float(close_col.ewm(span=8, adjust=False).mean().iloc[-1])
        ema13 = float(close_col.ewm(span=13, adjust=False).mean().iloc[-1])
        
        # SMA Hesaplamaları
        sma50 = float(close_col.rolling(window=50).mean().iloc[-1])
        sma100 = float(close_col.rolling(window=100).mean().iloc[-1])
        sma200 = float(close_col.rolling(window=200).mean().iloc[-1])
        
        return {
            "close": close,
            "ema5": ema5, "ema8": ema8, "ema13": ema13,
            "sma50": sma50, "sma100": sma100, "sma200": sma200
        }
    except Exception as e:
        return None
    
@st.cache_data(ttl=600)
def fetch_stock_info(ticker):
    try:
        t = yf.Ticker(ticker)
        price = prev_close = volume = None
        try:
            fi = getattr(t, "fast_info", None)
            if fi:
                price = fi.get("last_price")
                prev_close = fi.get("previous_close")
                volume = fi.get("last_volume")
        except: pass

        if price is None or prev_close is None:
            try:
                # Yahoo quirk: period="5d" gives older close (good for prev_close),
                # period="1d" gives the most recent close (good for current price).
                h1 = yf.Ticker(ticker).history(period="1d")
                h5 = yf.Ticker(ticker).history(period="5d")
            except Exception:
                h1 = h5 = None
            if h1 is not None and not h1.empty:
                price  = float(h1["Close"].iloc[-1])
                volume = float(h1["Volume"].iloc[-1])
                if h5 is not None and not h5.empty:
                    # h1 ve h5 farklı son tarihte ise, h5'in son satırı önceki kapanış
                    if h1.index[-1].date() != h5.index[-1].date():
                        prev_close = float(h5["Close"].iloc[-1])
                    elif len(h5) > 1:
                        prev_close = float(h5["Close"].iloc[-2])
                    else:
                        prev_close = price
                else:
                    prev_close = price
            elif h5 is not None and not h5.empty:
                price      = float(h5["Close"].iloc[-1])
                prev_close = float(h5["Close"].iloc[-2]) if len(h5) > 1 else price
                volume     = float(h5["Volume"].iloc[-1])
            else:
                return None

        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0
        return { "price": price, "change_pct": change_pct, "volume": volume or 0, "sector": "-", "target": "-" }
    except: return None

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

@st.cache_data(ttl=1200)
def fetch_google_news(ticker):
    try:
        clean = ticker.replace(".IS", "").replace("=F", "")
        rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(f'{clean} stock news site:investing.com OR site:seekingalpha.com')}&hl=tr&gl=TR&ceid=TR:tr"
        feed = feedparser.parse(rss_url)
        news = []
        for entry in feed.entries[:6]:
            try: dt = datetime(*entry.published_parsed[:6])
            except: dt = datetime.now()
            if dt < datetime.now() - timedelta(days=10): continue
            pol = TextBlob(entry.title).sentiment.polarity
            color = "#16A34A" if pol > 0.1 else "#DC2626" if pol < -0.1 else "#64748B"
            news.append({'title': entry.title, 'link': entry.link, 'date': dt.strftime('%d %b'), 'source': entry.source.title, 'color': color})
        return news
    except: return []

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

@st.cache_data(ttl=600)
def calculate_synthetic_sentiment(ticker):
    try:
        df = get_safe_historical_data(ticker, period="6mo")
        if df is None or df.empty: return None

        close = df['Close']; high = df['High']; low = df['Low']; volume = df['Volume']

        # --- DEMA6 (Orijinal Formül) ---
        typical_price = (high + low + close) / 3
        ema1 = typical_price.ewm(span=6, adjust=False).mean()
        ema2 = ema1.ewm(span=6, adjust=False).mean()
        dema6 = (2 * ema1) - ema2
        mf_smooth = (typical_price - dema6) / dema6 * 1000

        stp = ema1
        
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
        
        plot_df = pd.DataFrame({
            'Date': df['Date'], 
            'MF_Smooth': mf_smooth.values, 
            'STP': stp.values, 
            'Price': close.values
        }).tail(30).reset_index(drop=True)
        
        plot_df['Date_Str'] = plot_df['Date'].dt.strftime('%d %b')
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
        obv_sma = obv.rolling(20).mean() # Profesyonel Filtre
        
        # 2. Son 10 Günlük Trend Kıyaslaması
        p_now = df['Close'].iloc[-1]; p_old = df['Close'].iloc[-11]
        obv_now = obv.iloc[-1]; obv_old = obv.iloc[-11]
        obv_sma_now = obv_sma.iloc[-1]
        
        price_trend = "YUKARI" if p_now > p_old else "AŞAĞI"
        # Klasik OBV trendi (Eski usul)
        obv_trend_raw = "YUKARI" if obv_now > obv_old else "AŞAĞI"
        
        # 3. GÜÇ FİLTRESİ: OBV şu an ortalamasının üzerinde mi?
        is_obv_strong = obv_now > obv_sma_now
        
        # 4. Karar Mekanizması
        if price_trend == "AŞAĞI" and obv_trend_raw == "YUKARI":
            if is_obv_strong:
                return ("🔥 GÜÇLÜ GİZLİ GİRİŞ", "#16a34a", "Son 10 günde fiyat düşmesine rağmen, gerçek hacim (OBV) 20 günlük ortalamasını yukarı kesti. Akıllı para gizlice mal topluyor olabilir!")
            else:
                return ("👀 Olası Toplama (Zayıf)", "#d97706", "Son 10 günde fiyat düşerken OBV hafifçe yükseliyor, ancak henüz 20 günlük ortalamasını aşacak kadar güçlü bir para girişi yok.")
                
        elif price_trend == "YUKARI" and obv_trend_raw == "AŞAĞI":
            return ("⚠️ GİZLİ ÇIKIŞ (Dağıtım)", "#f87171", "Son 10 günde fiyat yükselmesine rağmen kümülatif hacim (OBV) düşüyor. Yükseliş sahte olabilir, büyük oyuncular çıkış yapıyor olabilir.")
            
        elif is_obv_strong:
            # DÜZELTME: Trende değil, BUGÜNKÜ mumun rengine bakıyoruz.
            # 10 günlük trend yukarı olsa bile, bugün fiyat düşüyorsa "Yükseliş" deme.
            p_yesterday = df['Close'].iloc[-2]
            
            if p_now < p_yesterday: # Bugün Fiyat Düşüyorsa (Kırmızı Mum)
                return ("🛡️ DÜŞÜŞE DİRENÇ (Kurumsal Emilim)", "#d97706", "Bugün fiyat düşüş eğiliminde olsa da kümülatif hacim (OBV) hala 20 günlük ortalamasının üzerinde gücünü koruyor. Panik satışları büyük oyuncular tarafından karşılanıyor olabilir.")
            else:
                return ("✅ SAĞLIKLI TREND (Hacim Onaylı)", "#15803d", "Fiyattaki yükseliş, gerçek hacim (OBV) tarafından net bir şekilde destekleniyor. Trendin arkasında akıllı paranın itici gücü var.")
            
        else:
            return ("⚖️ ZAYIF İVME (Hacimsiz Bölge)", "#64748B", "Kümülatif hacim akışı (OBV) 20 günlük ortalamasının altında süzülüyor. Fiyat hareketlerini destekleyecek net ve iştahlı bir para girişi görünmüyor.")
            
    except: return ("Hesaplanamadı", "#64748B", "-")

# ==============================================================================
# BÖLÜM 7 — TEKNİK ANALİZ FONKSİYONLARI
# HaRSI, LazyBear Squeeze, OBV diverjansı, sentetik sentiment,
# hareketli ortalamalar ve temel teknik göstergeler.
# ==============================================================================

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

import pandas as pd
import numpy as np

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

# ==============================================================================
# BÖLÜM 8 — FORMASYON TARAMA SİSTEMİ (CHART PATTERNS)
# Çift dip, omuz baş omuz, bayrak, üçgen, fincan-kulp vb.
# Tüm klasik formasyon tespit algoritmaları bu bölümdedir.
# ==============================================================================
def scan_chart_patterns(asset_list):
    """
    V6: ZIGZAG TABANLI FORMASYON MOTORU
    - Gürültüyü eler, yalnızca anlamlı salınımları (zigzag iskelet) kullanır.
    - İnsan gözünün gördüğü şekli sayısal olarak tespit eder.
    - TOBO: L,H,L*,H,L — son 5 anlamlı pivot üzerinden
    - Fincan-Kulp: H,L,H≈ilk,L(kulp) — son 4 anlamlı pivot üzerinden
    - 2 yıllık veri ile büyük formasyonlar kaçmaz.
    """
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty: return pd.DataFrame()

    current_cat = st.session_state.get('category', 'S&P 500')
    benchmark = get_benchmark_data(current_cat)

    # ---------------------------------------------------------------
    # ZIGZAG ALGORİTMASI — %threshold kadar ters dönen hareketleri kaydet.
    # İnsan gözünün grafik iskeleti olarak gördüğü anlamlı tepeler/dipler.
    # Döndürür: [(bar_index, fiyat, 'H'/'L'), ...]
    # ---------------------------------------------------------------
    def zigzag_pivots(close, threshold=0.04):
        pivots = []
        if len(close) < 10: return pivots
        direction = None
        last_i, last_p = 0, float(close.iloc[0])
        for i in range(1, len(close)):
            p = float(close.iloc[i])
            if direction is None:
                if p >= last_p * (1 + threshold):
                    direction = 'up'; last_i, last_p = i, p
                elif p <= last_p * (1 - threshold):
                    direction = 'down'; last_i, last_p = i, p
            elif direction == 'up':
                if p > last_p:
                    last_i, last_p = i, p
                elif p <= last_p * (1 - threshold):
                    pivots.append((last_i, last_p, 'H'))
                    direction = 'down'; last_i, last_p = i, p
            else:
                if p < last_p:
                    last_i, last_p = i, p
                elif p >= last_p * (1 + threshold):
                    pivots.append((last_i, last_p, 'L'))
                    direction = 'up'; last_i, last_p = i, p
        # Son segment
        if direction == 'up':   pivots.append((last_i, last_p, 'H'))
        elif direction == 'down': pivots.append((last_i, last_p, 'L'))
        return pivots

    def process_single_pattern(symbol):
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol not in data.columns.levels[0]: return None
                df = data[symbol].dropna()
            else:
                df = data.dropna()

            if len(df) < 150: return None

            close      = df['Close']
            high       = df['High']
            low        = df['Low']
            open_      = df['Open']
            volume     = df['Volume']
            curr_price = float(close.iloc[-1])
            bar_total  = len(df)

            sma200 = close.rolling(200).mean().iloc[-1]

            # Mansfield RS
            mansfield_val = 0.0
            if benchmark is not None:
                try:
                    common = close.index.intersection(benchmark.index)
                    if len(common) > 55:
                        rs_r = close.reindex(common) / benchmark.reindex(common)
                        rs_m = rs_r.rolling(50).mean()
                        m = ((rs_r / rs_m) - 1) * 10
                        mansfield_val = float(m.iloc[-1]) if not np.isnan(m.iloc[-1]) else 0.0
                except: pass

            # Ani dump filtresi
            prev_close = float(close.iloc[-2])
            if (curr_price - prev_close) / prev_close <= -0.025: return None

            pattern_found = False
            pattern_name  = ""
            desc          = ""
            base_score    = 0
            chart_d       = None   # mini grafik verisi (sadece Fincan-Kulp / TOBO)

            # ---------------------------------------------------------------
            # ZIGZAG İSKELETİ — %4 eşikli (insan gözüne yakın)
            # ---------------------------------------------------------------
            zz       = zigzag_pivots(close, threshold=0.04)
            zz_chron = sorted(zz, key=lambda x: x[0])   # Kronolojik sıra
            zz_h     = [(i, p) for (i, p, t) in zz_chron if t == 'H']
            zz_l     = [(i, p) for (i, p, t) in zz_chron if t == 'L']

            # ---------------------------------------------------------------
            # WICK/BODY FİLTRESİ — Gürültülü bölgeleri eliyor
            # Formasyon bölgesindeki barların fitil/gövde oranını kontrol eder.
            # Median fitil > 2 × median gövde ise formasyon geçersiz sayılır.
            # ---------------------------------------------------------------
            def is_clean_zone(start_idx, end_idx):
                """True döndürürse bölge temiz, False ise gürültülü/fitilli."""
                try:
                    s = max(0, start_idx)
                    e = min(bar_total, end_idx + 1)
                    if e - s < 5: return True  # Çok kısa bölge, filtre uygulama
                    o_arr = open_.iloc[s:e].values.astype(float)
                    c_arr = close.iloc[s:e].values.astype(float)
                    h_arr = high.iloc[s:e].values.astype(float)
                    l_arr = low.iloc[s:e].values.astype(float)
                    bodies = np.abs(c_arr - o_arr)
                    wicks  = (h_arr - l_arr) - bodies
                    med_body = np.median(bodies)
                    med_wick = np.median(wicks)
                    if med_body < 1e-9: return False  # Doji bölgesi — geçersiz
                    return med_wick <= 2.0 * med_body
                except:
                    return True  # Hata durumunda filtreyi geç

            # ---------------------------------------------------------------
            # 1. BOĞA BAYRAĞI — Kısa vadeli, ham fiyat bazlı (zigzag gerekmez)
            # ---------------------------------------------------------------
            if not pattern_found:
                pole_start = float(close.iloc[-20])
                pole_end   = float(close.iloc[-6])
                pole       = (pole_end - pole_start) / pole_start
                flag_h     = float(high.iloc[-5:].max())
                flag_l     = float(low.iloc[-5:].min())
                tight      = (flag_h - flag_l) / flag_l if flag_l > 0 else 1
                retrace    = (pole_end - curr_price) / (pole_end - pole_start) if (pole_end - pole_start) > 0 else 1
                if (pole > 0.15 and tight < 0.06
                        and retrace < 0.50
                        and curr_price >= flag_l * 0.99
                        and curr_price >= flag_h * 0.98):
                    chart_d = {
                        "type": "flag",
                        "date_start": str(close.index[max(0, bar_total - 22)].date()),
                        "flag_h": float(flag_h),
                        "flag_l": float(flag_l),
                        "pole_end_date": str(close.index[bar_total - 6].date()),
                    }
                    pattern_found = True
                    pattern_name  = "🚩 BOĞA BAYRAĞI"
                    base_score    = 85
                    desc = f"Direk: %{pole*100:.1f} | Sıkışma: %{tight*100:.1f} | Geri Alım: %{retrace*100:.0f}"

            # ---------------------------------------------------------------
            # YARDIMCI: Swing High / Low tespiti (lookback bar sol-sağ)
            # ---------------------------------------------------------------
            def find_swings(series, lookback=8):
                highs, lows = [], []
                arr = series.values.astype(float)
                n   = len(arr)
                for i in range(lookback, n - lookback):
                    w = arr[i - lookback: i + lookback + 1]
                    if arr[i] >= w.max() - 1e-9:
                        highs.append((i, arr[i]))
                    if arr[i] <= w.min() + 1e-9:
                        lows.append((i, arr[i]))
                return highs, lows

            sw_h, sw_l = find_swings(close, lookback=8)
            sw_h_y = [(i, v) for i, v in sw_h if i >= bar_total - 252]  # son 12 ay
            sw_l_y = [(i, v) for i, v in sw_l if i >= bar_total - 252]

            # ---------------------------------------------------------------
            # 2. FİNCAN-KULP — Swing tabanlı + polinom U-şekil doğrulaması
            # Min: 40 bar (~2 ay), Max: 252 bar (12 ay), R/R >= 1.0
            # Son pivot 60 günden eski ise gösterilmez.
            # ---------------------------------------------------------------
            if not pattern_found and len(sw_h_y) >= 2 and len(sw_l_y) >= 1:
                for ri in range(len(sw_h_y) - 1, 0, -1):
                    if pattern_found: break
                    sh2_i, sh2_v = sw_h_y[ri]           # Sağ rim
                    if bar_total - sh2_i > 60: continue  # Son pivot 60 günden eski
                    for li in range(ri - 1, max(ri - 12, -1), -1):
                        sh1_i, sh1_v = sw_h_y[li]       # Sol rim
                        cup_dur = sh2_i - sh1_i
                        if not (40 <= cup_dur <= 252): continue
                        # Cup içi en derin swing low
                        cup_lows = [(i, v) for i, v in sw_l_y if sh1_i < i < sh2_i]
                        if not cup_lows: continue
                        sl_i, sl_v = min(cup_lows, key=lambda x: x[1])
                        # Derinlik ve rim hizası
                        depth = (sh1_v - sl_v) / sh1_v
                        if not (0.12 <= depth <= 0.55): continue
                        # FIX 2: Rim hizalaması daraltıldı (12% → 6%) — asimetrik fincanları eler
                        if abs(sh1_v - sh2_v) / sh1_v > 0.06: continue
                        # U-şekil: polinom fit (R² > 0.72, konkav yukarı)
                        try:
                            cup_arr = close.iloc[sh1_i:sh2_i + 1].values.astype(float)
                            if len(cup_arr) < 10: continue
                            xf  = np.linspace(0, 1, len(cup_arr))
                            cf  = np.polyfit(xf, cup_arr, 2)
                            yp  = np.polyval(cf, xf)
                            ss_res = np.sum((cup_arr - yp) ** 2)
                            ss_tot = np.sum((cup_arr - cup_arr.mean()) ** 2)
                            r2  = 1 - ss_res / ss_tot if ss_tot > 0 else 0
                            # FIX 1: R² eşiği yükseltildi (0.55 → 0.72) — V-shape ve asimetrik şekilleri eler
                            if r2 < 0.72 or cf[0] <= 0: continue  # Konkav yukarı zorunlu
                        except: continue
                        # Wick/Body filtresi: fincan bölgesi gürültülü değil mi?
                        if not is_clean_zone(sh1_i, sh2_i): continue
                        # Handle: sh2'den sonraki ilk swing low
                        h_lows = [(i, v) for i, v in sw_l_y if i > sh2_i]
                        if h_lows:
                            hl_i, hl_v = h_lows[0]
                        else:
                            after = close.iloc[sh2_i:]
                            if len(after) < 3: continue
                            rel  = int(after.values.argmin())
                            hl_i = sh2_i + rel
                            hl_v = float(after.iloc[rel])
                        if not (hl_v > sl_v + (sh2_v - sl_v) * 0.35): continue  # Kulp üst %65'te
                        if not (hl_v > sh2_v * 0.82): continue                  # Fazla derin değil
                        # R/R filtresi
                        target = sh2_v + (sh2_v - sl_v)
                        risk   = max(curr_price - hl_v * 0.98, 0.01)
                        rr     = (target - curr_price) / risk
                        if rr < 1.0: continue
                        # Durum tespiti
                        breaking = curr_price >= sh2_v * 0.97 and curr_price <= sh2_v * 1.10
                        forming  = curr_price >= hl_v * 0.98 and not breaking
                        if not (breaking or forming): continue
                        dur_months = max(1, round(cup_dur / 21))
                        dist = ((sh2_v - curr_price) / sh2_v * 100) if curr_price < sh2_v else 0
                        if breaking:
                            p_name     = f"☕ FİNCAN KULP ({dur_months} Ay) — Kırılım Bölgesinde"
                            base_score = 92
                        else:
                            p_name     = f"⏳ OLUŞAN FİNCAN KULP ({dur_months} Ay) — %{dist:.1f} kaldı"
                            base_score = 75
                        p_desc  = (f"Sol Rim: {sh1_v:.2f} | Dip: {sl_v:.2f} | Sağ Rim: {sh2_v:.2f} | "
                                   f"Kulp: {hl_v:.2f} | Hedef: {target:.2f} | R²: {r2:.2f}")
                        chart_d = {
                            "pivot_dates":  [str(close.index[sh1_i].date()),
                                             str(close.index[sl_i].date()),
                                             str(close.index[sh2_i].date()),
                                             str(close.index[min(hl_i, bar_total - 1)].date())],
                            "pivot_prices": [sh1_v, sl_v, sh2_v, hl_v],
                            "pivot_types":  ["H", "L", "H", "L"],
                            "neck": float(sh2_v),
                            "type": "cup",
                        }
                        pattern_found = True
                        pattern_name  = p_name; desc = p_desc
                        break

            # ---------------------------------------------------------------
            # 3. TOBO — Swing tabanlı: 5 pivot L, H, L(derin), H, L
            # Min: 40 bar, Max: 252 bar, R/R >= 1.0
            # ---------------------------------------------------------------
            if not pattern_found and len(sw_h_y) >= 2 and len(sw_l_y) >= 3:
                for i_rs in range(len(sw_l_y) - 1, 1, -1):
                    if pattern_found: break
                    sl3_i, sl3_v = sw_l_y[i_rs]             # Sağ omuz
                    if bar_total - sl3_i > 60: continue      # Son pivot 60 günden eski
                    for i_hd in range(i_rs - 1, 0, -1):
                        if pattern_found: break
                        sl2_i, sl2_v = sw_l_y[i_hd]         # Baş (en derin)
                        for i_ls in range(i_hd - 1, max(i_hd - 8, -1), -1):
                            sl1_i, sl1_v = sw_l_y[i_ls]     # Sol omuz
                            dur = sl3_i - sl1_i
                            if not (40 <= dur <= 252): continue
                            # Baş en derin olmalı
                            if not (sl2_v < sl1_v * 0.95 and sl2_v < sl3_v * 0.95): continue
                            # Boyun noktaları: her omuz ile baş arasındaki en yüksek swing high
                            sh1_cands = [(i, v) for i, v in sw_h_y if sl1_i < i < sl2_i]
                            sh2_cands = [(i, v) for i, v in sw_h_y if sl2_i < i < sl3_i]
                            if not sh1_cands or not sh2_cands: continue
                            sh1_i, sh1_v = max(sh1_cands, key=lambda x: x[1])
                            sh2_i, sh2_v = max(sh2_cands, key=lambda x: x[1])
                            neck = (sh1_v + sh2_v) / 2
                            if abs(sh1_v - sh2_v) / sh1_v > 0.06: continue  # Boyun yatay
                            if abs(sl1_v - sl3_v) / sl1_v > 0.15: continue  # Omuz simetrisi
                            recovery = (sl3_v - sl2_v) / (neck - sl2_v) if (neck - sl2_v) > 0 else 0
                            if recovery < 0.45: continue
                            # Wick/Body filtresi: TOBO bölgesi gürültülü değil mi?
                            if not is_clean_zone(sl1_i, sl3_i): continue
                            # R/R filtresi
                            target = neck + (neck - sl2_v)
                            risk   = max(curr_price - sl3_v * 0.98, 0.01)
                            rr     = (target - curr_price) / risk
                            if rr < 1.0: continue
                            # Durum tespiti
                            breaking = curr_price >= neck * 0.97 and curr_price <= neck * 1.08
                            forming  = curr_price > sl3_v * 1.01 and curr_price < neck * 0.96
                            if not (breaking or forming): continue
                            dur_months = max(1, round(dur / 21))
                            dist = ((neck - curr_price) / neck * 100) if curr_price < neck else 0
                            if breaking:
                                p_name     = f"🧛 TOBO ({dur_months} Ay) — Kırılım Bölgesinde"
                                base_score = 90
                            else:
                                p_name     = f"⏳ OLUŞAN TOBO ({dur_months} Ay) — %{dist:.1f} kaldı"
                                base_score = 72
                            p_desc  = (f"Boyun: {neck:.2f} | Baş: {sl2_v:.2f} | "
                                       f"Sol/Sağ Omuz: {sl1_v:.2f}/{sl3_v:.2f} | "
                                       f"Hedef: {target:.2f} | Geri Alım: %{recovery*100:.0f}")
                            chart_d = {
                                "pivot_dates":  [str(close.index[sl1_i].date()),
                                                 str(close.index[sh1_i].date()),
                                                 str(close.index[sl2_i].date()),
                                                 str(close.index[sh2_i].date()),
                                                 str(close.index[sl3_i].date())],
                                "pivot_prices": [sl1_v, sh1_v, sl2_v, sh2_v, sl3_v],
                                "pivot_types":  ["L", "H", "L", "H", "L"],
                                "neck": float(neck),
                                "type": "tobo",
                            }
                            pattern_found = True
                            pattern_name  = p_name; desc = p_desc
                            break

            # ---------------------------------------------------------------
            # 4. YÜKSELEN ÜÇGEN — Düz direnç + yükselen destek (linregress)
            # En az 2 tepe (≤%4 fark), en az 2 dip (yükselen eğim), Max 252 bar
            # R/R >= 1.0, son pivot 60 günden yeni
            # ---------------------------------------------------------------
            if not pattern_found and len(sw_h_y) >= 2 and len(sw_l_y) >= 2:
                top_v   = max(v for _, v in sw_h_y)
                flat_sh = [(i, v) for i, v in sw_h_y if abs(v - top_v) / top_v < 0.04]
                if len(flat_sh) >= 2:
                    first_sh_i = min(i for i, _ in flat_sh)
                    tri_lows   = [(i, v) for i, v in sw_l_y if i >= first_sh_i]
                    if len(tri_lows) >= 2:
                        tri_lows_s = sorted(tri_lows, key=lambda x: x[0])
                        x_l = np.array([i for i, _ in tri_lows_s], dtype=float)
                        y_l = np.array([v for _, v in tri_lows_s], dtype=float)
                        sl_coef = np.polyfit(x_l, y_l, 1)
                        if sl_coef[0] > 0:   # Yükselen destek
                            avg_res = sum(v for _, v in flat_sh) / len(flat_sh)
                            first_i = min(first_sh_i, tri_lows_s[0][0])
                            last_i  = max(max(i for i, _ in flat_sh), tri_lows_s[-1][0])
                            dur_bars = last_i - first_i
                            if 20 <= dur_bars <= 252 and (bar_total - last_i) <= 60:
                                support_now = float(np.polyval(sl_coef, bar_total - 1))
                                breaking    = curr_price >= avg_res * 0.98 and curr_price <= avg_res * 1.06
                                approaching = support_now * 0.99 <= curr_price <= avg_res * 0.98
                                if breaking or approaching:
                                    target = avg_res + (avg_res - support_now)
                                    risk   = max(curr_price - support_now * 0.98, 0.01)
                                    rr     = (target - curr_price) / risk
                                    if rr >= 1.0:
                                        dur_months = max(1, round(dur_bars / 21))
                                        p_name  = (f"📐 YÜKS. ÜÇGEN ({dur_months} Ay) — Kırılım"
                                                   if breaking else
                                                   f"⏳ OLUŞAN ÜÇGEN ({dur_months} Ay) — Dirence Yaklaşıyor")
                                        p_desc  = (f"Direnç: {avg_res:.2f} | Destek: {support_now:.2f} | "
                                                   f"Hedef: {target:.2f} | {len(flat_sh)} tepe temas")
                                        chart_d = {
                                            "type":       "triangle",
                                            "date_start": str(close.index[max(0, first_i)].date()),
                                            "resistance": float(avg_res),
                                            "pivot_dates":  ([str(close.index[i].date()) for i, _ in flat_sh] +
                                                             [str(close.index[i].date()) for i, _ in tri_lows_s]),
                                            "pivot_prices": ([v for _, v in flat_sh] +
                                                             [v for _, v in tri_lows_s]),
                                            "pivot_types":  (["H"] * len(flat_sh) +
                                                             ["L"] * len(tri_lows_s)),
                                        }
                                        pattern_found = True
                                        pattern_name  = p_name; desc = p_desc
                                        base_score    = 88 if breaking else 68

            # ---------------------------------------------------------------
            # 4.5 RANGE (YATAY BANT) — Ham fiyat bazlı
            # ---------------------------------------------------------------
            if not pattern_found:
                for rng_window in [60, 90, 120, 180]:
                    if len(df) < rng_window: continue
                    period_max  = float(high.iloc[-rng_window:].max())
                    period_min  = float(low.iloc[-rng_window:].min())
                    if period_min <= 0: continue
                    range_width = (period_max - period_min) / period_min
                    if range_width < 0.15:
                        breaking_up = curr_price >= period_max * 0.98 and curr_price <= period_max * 1.04
                        bouncing_up = curr_price >= period_min * 0.98 and curr_price <= period_min * 1.04
                        if breaking_up or bouncing_up:
                            chart_d = {
                                "type": "range",
                                "date_start": str(close.index[max(0, bar_total - rng_window)].date()),
                                "resistance": float(period_max),
                                "support":    float(period_min),
                            }
                            pattern_found = True
                            p_name = f"🧱 RANGE DİRENCİ ({rng_window} Gün)" if breaking_up else f"🧱 RANGE DESTEĞİ ({rng_window} Gün)"
                            p_desc = (f"{rng_window} gündür süren yatay kanal direnci kırılıyor!" if breaking_up
                                      else f"{rng_window} gündür süren bandın dibinden destek aldı.")
                            pattern_name = p_name; desc = p_desc
                            base_score   = 88 if breaking_up else 85
                            break

            # ---------------------------------------------------------------
            # 4.6 ÇANAK (Saucer / Rounding Bottom)
            # ---------------------------------------------------------------
            if not pattern_found and len(df) >= 100:
                lb  = min(len(df), 120)
                seg = lb // 3
                left_part   = df.iloc[-lb:        -lb + seg]
                middle_part = df.iloc[-lb + seg:  -lb + 2*seg]
                right_part  = df.iloc[-lb + 2*seg:]
                if len(left_part) > 5 and len(middle_part) > 5 and len(right_part) > 5:
                    left_high  = float(left_part['High'].max())
                    cup_bottom = float(middle_part['Low'].min())
                    right_high = float(right_part['High'].max())
                    if ((left_high - cup_bottom) / cup_bottom > 0.12
                            and float(middle_part['Low'].mean()) < float(left_part['Low'].mean())
                            and (curr_price - cup_bottom) / cup_bottom > 0.08
                            and right_high >= left_high * 0.60
                            and curr_price >= right_high * 0.98):
                        chart_d = {
                            "type": "saucer",
                            "date_start": str(close.index[max(0, bar_total - lb)].date()),
                            "left_high":  float(left_high),
                            "cup_bottom": float(cup_bottom),
                            "right_high": float(right_high),
                        }
                        pattern_found = True
                        pattern_name  = "🥣 ÇANAK (Dipten Dönüş)"
                        base_score    = 88
                        desc = f"Sol Tepe: {left_high:.2f} | Dip: {cup_bottom:.2f} | Sağ Direnç: {right_high:.2f}"

            # ---------------------------------------------------------------
            # 5. QUASIMODO (QML) — Son 6 zigzag pivotu üzerinden
            # ---------------------------------------------------------------
            if not pattern_found and len(zz_chron) >= 4:
                recent = zz_chron[-6:]
                r_l = [(i, p) for (i, p, t) in recent if t == 'L']
                r_h = [(i, p) for (i, p, t) in recent if t == 'H']
                if len(r_l) >= 2 and len(r_h) >= 2:
                    for qi in range(len(r_l) - 1):
                        l_left_idx, l_left_p = r_l[qi]
                        mid_h = [(i, p) for (i, p) in r_h if i > l_left_idx]
                        if not mid_h: continue
                        h_mid_idx, h_mid_p = mid_h[0]
                        ll_list = [(i, p) for (i, p) in r_l if i > h_mid_idx]
                        if not ll_list: continue
                        ll_idx, ll_p = ll_list[0]
                        hh_list = [(i, p) for (i, p) in r_h if i > ll_idx]
                        if not hh_list: continue
                        hh_idx, hh_p = hh_list[0]
                        if (ll_p < l_left_p * 0.98 and hh_p > h_mid_p * 1.01
                                and curr_price >= l_left_p * 0.95 and curr_price <= l_left_p * 1.05):
                            chart_d = {
                                "type": "qml",
                                "date_start": str(close.index[max(0, l_left_idx - 3)].date()),
                                "pivot_dates":  [str(close.index[i].date()) for i in [l_left_idx, h_mid_idx, ll_idx, hh_idx]],
                                "pivot_prices": [float(l_left_p), float(h_mid_p), float(ll_p), float(hh_p)],
                                "pivot_types":  ["L", "H", "L", "H"],
                                "qml_line": float(l_left_p),
                            }
                            pattern_found = True
                            pattern_name  = "🧲 QUASIMODO (QML)"
                            base_score    = 92
                            desc = f"QML Çizgisi: {l_left_p:.2f} | Baş Dip: {ll_p:.2f} | Kırılım Tepesi: {hh_p:.2f}"
                            break

            # ---------------------------------------------------------------
            # 6. 3 DRIVE (Üç Düşen Dip) — Son 3 zigzag dibi
            # ---------------------------------------------------------------
            if not pattern_found and len(zz_l) >= 3:
                (d1_i, d1), (d2_i, d2), (d3_i, d3) = zz_l[-3], zz_l[-2], zz_l[-1]
                if d1 > d2 > d3:
                    drop1 = d1 - d2; drop2 = d2 - d3
                    if drop1 > 0 and abs(drop1 - drop2) / drop1 < 0.25:
                        if curr_price > d3 * 1.015 and curr_price < d2:
                            chart_d = {
                                "type": "three_drive",
                                "date_start": str(close.index[max(0, d1_i - 3)].date()),
                                "pivot_dates":  [str(close.index[i].date()) for i in [d1_i, d2_i, d3_i]],
                                "pivot_prices": [float(d1), float(d2), float(d3)],
                            }
                            pattern_found = True
                            pattern_name  = "🎢 3 DRIVE (DİP)"
                            base_score    = 85
                            desc = f"Dip1: {d1:.2f} | Dip2: {d2:.2f} | Dip3: {d3:.2f} | Simetri Sapması: %{abs(drop1-drop2)/drop1*100:.0f}"

            # ---------------------------------------------------------------
            # 7. GÜÇLÜ DESTEK / DİRENÇ TESTİ
            # ---------------------------------------------------------------
            if not pattern_found and len(df) >= 100:
                sr_levels = find_smart_sr_levels(df, window=5, cluster_tolerance=0.015, min_touches=3)
                for level in sorted(sr_levels, key=lambda x: abs(x - curr_price)):
                    if abs(curr_price - level) / level <= 0.015:
                        chart_d = {
                            "type": "sr_level",
                            "date_start": str(close.index[max(0, bar_total - 60)].date()),
                            "level":      float(level),
                            "is_support": curr_price >= level,
                        }
                        pattern_found = True
                        if curr_price >= level:
                            pattern_name = "🧱 GÜÇLÜ DESTEK TESTİ"
                            desc = f"Geçmişte ≥3 kez test edilen destek: {level:.2f}"
                            base_score = 85
                        else:
                            pattern_name = "⚔️ GÜÇLÜ DİRENÇ TESTİ"
                            desc = f"Geçmişte ≥3 kez reddedilen direnç: {level:.2f}"
                            base_score = 88
                        break

            # ---------------------------------------------------------------
            # KALİTE PUANLAMASI
            # ---------------------------------------------------------------
            if pattern_found:
                q_score = base_score
                if "FİNCAN" in pattern_name or "TOBO" in pattern_name or "QML" in pattern_name:
                    q_score += 15
                avg_vol   = float(volume.iloc[-20:].mean())
                vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1
                if vol_ratio > 2.5:
                    q_score += 25; desc += " (🚀 Ultra Hacim)"
                elif vol_ratio > 1.5:
                    q_score += 12
                sma50 = float(close.rolling(50).mean().iloc[-1])
                if curr_price > sma50: q_score += 8
                if (float(close.iloc[-1]) < float(open_.iloc[-1])
                        and float(close.iloc[-2]) < float(open_.iloc[-2])):
                    q_score -= 35; desc += " (⚠️ Düşüşte)"
                if avg_vol < 5000000:
                    pattern_name += " (⚠️ SIĞ TAHTA)"
                    desc += " | 🚨 Dikkat: Ortalama işlem hacmi 5 Milyon lotun altında."
                if not np.isnan(sma200) and curr_price < sma200:
                    pattern_name += " (⚠️ SMA200 Altında)"
                    desc += " | 📉 Risk Uyarısı: Fiyat 200 günlük ana ortalamanın altında."
                    q_score -= 10
                if mansfield_val > 0:   q_score += 10
                elif mansfield_val < 0: q_score -= 10
                return {
                    "Sembol":    symbol,
                    "Fiyat":     curr_price,
                    "Formasyon": pattern_name,
                    "Detay":     desc,
                    "Skor":      int(q_score),
                    "Hacim":     float(volume.iloc[-1]),
                    "ChartData": chart_d,
                }

        except Exception:
            return None
        return None

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_single_pattern, sym) for sym in asset_list]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)

    if results:
        return pd.DataFrame(results).sort_values(by=["Skor", "Hacim"], ascending=[False, False])
    return pd.DataFrame()

@st.cache_data(ttl=900)
def scan_golden_pattern_agent(asset_list, category="S&P 500"):
    """
    💎 Altın Fırsat & VIP Formasyon Ajanı (Mesafe Kontrollü)
    1. AŞAMA: Orijinal Altın Fırsat kriterlerini arar (Güç, Ucuzluk, Enerji).
    2. AŞAMA: Sadece bu kriterleri geçenlerde formasyon ve "kırılıma kalan mesafe" hesaplaması yapar.
    Formasyon bulunamazsa → Hazırlık Listesi (Baz Kurulumu veya Beklemede).
    """
    data = get_batch_data_cached(asset_list, period="1y")

    if data.empty:
        return {"formations": pd.DataFrame(), "hazirlik": pd.DataFrame()}

    bench = get_benchmark_data(category)
    results = []
    hazirlik_list = []
    
    for symbol in asset_list:
        try:
            # Sütun yapısını kontrol et (MultiIndex vs Tekli)
            if isinstance(data.columns, pd.MultiIndex):
                if symbol not in data.columns.levels[0]: 
                    continue
                df = data[symbol].dropna()
            else:
                df = data.dropna()
            
            # Yeterli veri var mı?
            if len(df) < 150: 
                continue
            
            # Temel verileri al
            close = df['Close']
            high = df['High']
            low = df['Low']
            volume = df['Volume']
            open_ = df['Open']
            
            curr_price = float(close.iloc[-1])
            prev_close = float(close.iloc[-2])
            
            # Hacim kontrolü (Sığ tahtaları ele)
            avg_vol = volume.iloc[-20:].mean()
            if avg_vol < 1000000: 
                continue 
            
            last_vol = float(volume.iloc[-1])
            
            # =========================================================
            # 🚀 1. AŞAMA: ALTIN FIRSAT KRİTERLERİ
            # (get_golden_trio_batch_scan ile birebir aynı mantık)
            # =========================================================

            # RSI hesabı (Royal Flush Nadir Fırsat + enerji için gerekli)
            delta   = close.diff()
            gain    = delta.clip(lower=0).rolling(window=14).mean()
            loss    = -delta.clip(upper=0).rolling(window=14).mean()
            rsi_s   = 100 - (100 / (1 + gain / loss))
            last_rsi = float(rsi_s.iloc[-1]) if not pd.isna(rsi_s.iloc[-1]) else 50.0

            # KRİTER 1 — Son 10 günde endeksten güçlü
            is_powerful = False
            if bench is not None and len(bench) > 10 and len(close) > 10:
                try:
                    stock_ret = (curr_price / float(close.iloc[-10])) - 1
                    index_ret = (float(bench.iloc[-1]) / float(bench.iloc[-10])) - 1
                    is_powerful = stock_ret > index_ret
                except Exception:
                    is_powerful = last_rsi > 45   # fallback
            else:
                is_powerful = last_rsi > 45

            # KRİTER 2 — Son 60 güne göre ucuz (bandın alt %65'i — ICT Discount zone ile uyumlu)
            high_60 = high.iloc[-60:].max()
            low_60  = low.iloc[-60:].min()
            rng_60  = high_60 - low_60
            is_discount = (rng_60 > 0) and ((curr_price - low_60) / rng_60 < 0.65)

            # KRİTER 3 — Hacim/Enerji artıyor
            is_energy = (last_vol > avg_vol * 1.05) or (last_rsi > 45)

            # Mansfield RS (görüntüleme için, filtre değil)
            mansfield_gp = 0.0
            if bench is not None and len(close) > 60:
                try:
                    common_i = close.index.intersection(bench.index)
                    if len(common_i) > 55:
                        rs_r = close.reindex(common_i) / bench.reindex(common_i)
                        rs_m = rs_r.rolling(50).mean()
                        m_s  = ((rs_r / rs_m) - 1) * 10
                        mansfield_gp = float(m_s.iloc[-1]) if not np.isnan(m_s.iloc[-1]) else 0.0
                except Exception:
                    pass

            # Altın Fırsat değilse geç
            if not (is_powerful and is_discount and is_energy):
                continue
                
            # =========================================================
            # 🚀 2. AŞAMA: FORMASYON VE MESAFE (CEZA) ARAMASI
            # =========================================================
            
            body_top = df[['Open', 'Close']].max(axis=1)
            body_bottom = df[['Open', 'Close']].min(axis=1)
            
            vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1
            
            warnings = []
            if vol_ratio < 1.1: 
                warnings.append("Hacim Cılız")
            
            pct_change = (curr_price - prev_close) / prev_close
            if pct_change <= -0.01: 
                warnings.append("Düşüşte")
            
            body_size = curr_price - open_.iloc[-1]
            is_strong_candle = body_size > 0 and curr_price > (high.iloc[-1] + low.iloc[-1]) / 2
            if not is_strong_candle and pct_change > -0.01: 
                warnings.append("Kararsız Mum")
            
            # Hareketli Ortalama Kontrolleri
            sma50 = close.rolling(50).mean().iloc[-1]
            sma200 = close.rolling(200).mean().iloc[-1]
            if curr_price < sma50:
                warnings.append("SMA50 Altında")
            if curr_price < sma200:
                warnings.append("SMA200 Altında")

            warning_text = f" (⚠️ {', '.join(warnings)})" if warnings else " (✅ Kusursuz)"

            # ── Platin tespiti — VIP Formasyon listesinde ♠️ ikonu için (SMA200+SMA50+RSI<70)
            is_platin = (
                curr_price > sma200 and   # Uzun vade trend yukarı
                curr_price > sma50  and   # Kısa vade yapı sağlam
                last_rsi < 70             # Aşırı ısınmamış
            )

            pattern_found = False
            p_name = ""
            base_score = 0

            # Zigzag iskelet — scan_chart_patterns ile aynı fonksiyon
            def _zigzag_gp(c, threshold=0.04):
                pivots = []
                if len(c) < 10: return pivots
                direction = None
                last_i, last_p = 0, float(c.iloc[0])
                for i in range(1, len(c)):
                    p = float(c.iloc[i])
                    if direction is None:
                        if p >= last_p * (1 + threshold):   direction = 'up';   last_i, last_p = i, p
                        elif p <= last_p * (1 - threshold): direction = 'down'; last_i, last_p = i, p
                    elif direction == 'up':
                        if p > last_p: last_i, last_p = i, p
                        elif p <= last_p * (1 - threshold):
                            pivots.append((last_i, last_p, 'H'))
                            direction = 'down'; last_i, last_p = i, p
                    else:
                        if p < last_p: last_i, last_p = i, p
                        elif p >= last_p * (1 + threshold):
                            pivots.append((last_i, last_p, 'L'))
                            direction = 'up'; last_i, last_p = i, p
                if direction == 'up':   pivots.append((last_i, last_p, 'H'))
                elif direction == 'down': pivots.append((last_i, last_p, 'L'))
                return pivots

            zz_gp      = _zigzag_gp(close, threshold=0.04)
            zz_chron   = sorted(zz_gp, key=lambda x: x[0])

            # A) FİNCAN KULP — Swing tabanlı + polinom U-şekil doğrulaması
            def _find_swings_gp(series, lookback=8):
                highs, lows = [], []
                arr = series.values.astype(float)
                n   = len(arr)
                for i in range(lookback, n - lookback):
                    w = arr[i - lookback: i + lookback + 1]
                    if arr[i] >= w.max() - 1e-9: highs.append((i, arr[i]))
                    if arr[i] <= w.min() + 1e-9: lows.append((i, arr[i]))
                return highs, lows

            _bt    = len(close)
            _swh, _swl = _find_swings_gp(close, lookback=8)
            _swh_y = [(i, v) for i, v in _swh if i >= _bt - 252]
            _swl_y = [(i, v) for i, v in _swl if i >= _bt - 252]

            if not pattern_found and len(_swh_y) >= 2 and len(_swl_y) >= 1:
                for ri in range(len(_swh_y) - 1, 0, -1):
                    if pattern_found: break
                    sh2_i, sh2_v = _swh_y[ri]
                    if _bt - sh2_i > 60: continue
                    for li in range(ri - 1, max(ri - 12, -1), -1):
                        sh1_i, sh1_v = _swh_y[li]
                        cup_dur = sh2_i - sh1_i
                        if not (40 <= cup_dur <= 252): continue
                        cup_lows = [(i, v) for i, v in _swl_y if sh1_i < i < sh2_i]
                        if not cup_lows: continue
                        sl_i, sl_v = min(cup_lows, key=lambda x: x[1])
                        depth = (sh1_v - sl_v) / sh1_v
                        if not (0.12 <= depth <= 0.55): continue
                        if abs(sh1_v - sh2_v) / sh1_v > 0.12: continue
                        try:
                            cup_arr = close.iloc[sh1_i:sh2_i + 1].values.astype(float)
                            if len(cup_arr) < 10: continue
                            xf = np.linspace(0, 1, len(cup_arr))
                            cf = np.polyfit(xf, cup_arr, 2)
                            yp = np.polyval(cf, xf)
                            ss_res = np.sum((cup_arr - yp) ** 2)
                            ss_tot = np.sum((cup_arr - cup_arr.mean()) ** 2)
                            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
                            if r2 < 0.55 or cf[0] <= 0: continue
                        except: continue
                        # Wick/Body filtresi: fincan bölgesi gürültülü değil mi?
                        o_z = open_.iloc[sh1_i:sh2_i+1].values.astype(float)
                        c_z = close.iloc[sh1_i:sh2_i+1].values.astype(float)
                        h_z = high.iloc[sh1_i:sh2_i+1].values.astype(float)
                        l_z = low.iloc[sh1_i:sh2_i+1].values.astype(float)
                        _bodies = np.abs(c_z - o_z); _wicks = (h_z - l_z) - _bodies
                        if np.median(_bodies) > 1e-9 and np.median(_wicks) > 2.0 * np.median(_bodies): continue
                        h_lows = [(i, v) for i, v in _swl_y if i > sh2_i]
                        if h_lows:
                            hl_i, hl_v = h_lows[0]
                        else:
                            after = close.iloc[sh2_i:]
                            if len(after) < 3: continue
                            rel = int(after.values.argmin())
                            hl_i, hl_v = sh2_i + rel, float(after.iloc[rel])
                        if not (hl_v > sl_v + (sh2_v - sl_v) * 0.35): continue
                        if not (hl_v > sh2_v * 0.82): continue
                        target = sh2_v + (sh2_v - sl_v)
                        risk   = max(curr_price - hl_v * 0.98, 0.01)
                        if (target - curr_price) / risk < 1.0: continue
                        breaking = curr_price >= sh2_v * 0.97 and curr_price <= sh2_v * 1.10
                        forming  = curr_price >= hl_v * 0.98 and not breaking
                        if not (breaking or forming): continue
                        dur_months = max(1, round(cup_dur / 21))
                        dist = ((sh2_v - curr_price) / sh2_v * 100) if curr_price < sh2_v else 0
                        if breaking:
                            p_name = f"☕ FİNCAN KULP ({dur_months} Ay) — Kırılım Bölgesinde"
                            base_score = 92
                        else:
                            p_name = f"⏳ OLUŞAN FİNCAN KULP ({dur_months} Ay) — %{dist:.1f} kaldı"
                            base_score = 75
                        pattern_found = True
                        break

            # B) TOBO — Swing tabanlı: 5 pivot L, H, L(derin), H, L
            if not pattern_found and len(_swh_y) >= 2 and len(_swl_y) >= 3:
                for i_rs in range(len(_swl_y) - 1, 1, -1):
                    if pattern_found: break
                    sl3_i, sl3_v = _swl_y[i_rs]
                    if _bt - sl3_i > 60: continue
                    for i_hd in range(i_rs - 1, 0, -1):
                        if pattern_found: break
                        sl2_i, sl2_v = _swl_y[i_hd]
                        for i_ls in range(i_hd - 1, max(i_hd - 8, -1), -1):
                            sl1_i, sl1_v = _swl_y[i_ls]
                            dur = sl3_i - sl1_i
                            if not (40 <= dur <= 252): continue
                            if not (sl2_v < sl1_v * 0.95 and sl2_v < sl3_v * 0.95): continue
                            sh1_c = [(i, v) for i, v in _swh_y if sl1_i < i < sl2_i]
                            sh2_c = [(i, v) for i, v in _swh_y if sl2_i < i < sl3_i]
                            if not sh1_c or not sh2_c: continue
                            sh1_i, sh1_v = max(sh1_c, key=lambda x: x[1])
                            sh2_i, sh2_v = max(sh2_c, key=lambda x: x[1])
                            neck = (sh1_v + sh2_v) / 2
                            if abs(sh1_v - sh2_v) / sh1_v > 0.06: continue
                            if abs(sl1_v - sl3_v) / sl1_v > 0.15: continue
                            recovery = (sl3_v - sl2_v) / (neck - sl2_v) if (neck - sl2_v) > 0 else 0
                            if recovery < 0.45: continue
                            # Wick/Body filtresi: TOBO bölgesi gürültülü değil mi?
                            o_z = open_.iloc[sl1_i:sl3_i+1].values.astype(float)
                            c_z = close.iloc[sl1_i:sl3_i+1].values.astype(float)
                            h_z = high.iloc[sl1_i:sl3_i+1].values.astype(float)
                            l_z = low.iloc[sl1_i:sl3_i+1].values.astype(float)
                            _bodies = np.abs(c_z - o_z); _wicks = (h_z - l_z) - _bodies
                            if np.median(_bodies) > 1e-9 and np.median(_wicks) > 2.0 * np.median(_bodies): continue
                            target = neck + (neck - sl2_v)
                            risk   = max(curr_price - sl3_v * 0.98, 0.01)
                            if (target - curr_price) / risk < 1.0: continue
                            breaking = curr_price >= neck * 0.97 and curr_price <= neck * 1.08
                            forming  = curr_price > sl3_v * 1.01 and curr_price < neck * 0.96
                            if not (breaking or forming): continue
                            dur_months = max(1, round(dur / 21))
                            dist = ((neck - curr_price) / neck * 100) if curr_price < neck else 0
                            if breaking:
                                p_name = f"🧛 TOBO ({dur_months} Ay) — Kırılım Bölgesinde"
                                base_score = 90
                            else:
                                p_name = f"⏳ OLUŞAN TOBO ({dur_months} Ay) — %{dist:.1f} kaldı"
                                base_score = 72
                            pattern_found = True
                            break

            # C) YÜKSELEN ÜÇGEN — Düz direnç + yükselen destek
            if not pattern_found and len(_swh_y) >= 2 and len(_swl_y) >= 2:
                top_v_gp   = max(v for _, v in _swh_y)
                flat_sh_gp = [(i, v) for i, v in _swh_y if abs(v - top_v_gp) / top_v_gp < 0.04]
                if len(flat_sh_gp) >= 2:
                    first_sh_i_gp = min(i for i, _ in flat_sh_gp)
                    tri_lows_gp   = [(i, v) for i, v in _swl_y if i >= first_sh_i_gp]
                    if len(tri_lows_gp) >= 2:
                        tri_lows_s_gp = sorted(tri_lows_gp, key=lambda x: x[0])
                        x_l_gp = np.array([i for i, _ in tri_lows_s_gp], dtype=float)
                        y_l_gp = np.array([v for _, v in tri_lows_s_gp], dtype=float)
                        sl_coef_gp = np.polyfit(x_l_gp, y_l_gp, 1)
                        if sl_coef_gp[0] > 0:
                            avg_res_gp  = sum(v for _, v in flat_sh_gp) / len(flat_sh_gp)
                            first_i_gp  = min(first_sh_i_gp, tri_lows_s_gp[0][0])
                            last_i_gp   = max(max(i for i, _ in flat_sh_gp), tri_lows_s_gp[-1][0])
                            dur_bars_gp = last_i_gp - first_i_gp
                            if 20 <= dur_bars_gp <= 252 and (_bt - last_i_gp) <= 60:
                                sup_gp   = float(np.polyval(sl_coef_gp, _bt - 1))
                                breaking = curr_price >= avg_res_gp * 0.98 and curr_price <= avg_res_gp * 1.06
                                approach = sup_gp * 0.99 <= curr_price <= avg_res_gp * 0.98
                                if breaking or approach:
                                    target_gp = avg_res_gp + (avg_res_gp - sup_gp)
                                    risk_gp   = max(curr_price - sup_gp * 0.98, 0.01)
                                    if (target_gp - curr_price) / risk_gp >= 1.0:
                                        dur_months_gp = max(1, round(dur_bars_gp / 21))
                                        p_name = (f"📐 YÜKS. ÜÇGEN ({dur_months_gp} Ay) — Kırılım"
                                                  if breaking else
                                                  f"⏳ OLUŞAN ÜÇGEN ({dur_months_gp} Ay) — Dirence Yaklaşıyor")
                                        base_score    = 88 if breaking else 68
                                        pattern_found = True


            # D) RANGE (YATAY BANT) — direnç kırılımı veya tabandan destek
            if not pattern_found:
                for rng_window in [60, 90, 120, 180]:
                    if len(df) < rng_window: continue
                    period_max  = float(high.iloc[-rng_window:].max())
                    period_min  = float(low.iloc[-rng_window:].min())
                    if period_min <= 0: continue
                    range_width = (period_max - period_min) / period_min
                    if range_width < 0.15:
                        breaking_up = curr_price >= period_max * 0.98 and curr_price <= period_max * 1.04
                        bouncing_up = curr_price >= period_min * 0.98 and curr_price <= period_min * 1.04
                        if breaking_up or bouncing_up:
                            p_name = (f"🧱 RANGE DİRENCİ ({rng_window} Gün)"
                                      if breaking_up else
                                      f"🧱 RANGE DESTEĞİ ({rng_window} Gün)")
                            base_score    = 88 if breaking_up else 85
                            pattern_found = True
                            break

            # E) GÜÇLÜ DESTEK / DİRENÇ TESTİ
            if not pattern_found and len(df) >= 100:
                try:
                    sr_levels = find_smart_sr_levels(df, window=5, cluster_tolerance=0.015, min_touches=3)
                    for level in sorted(sr_levels, key=lambda x: abs(x - curr_price)):
                        if abs(curr_price - level) / level <= 0.015:
                            is_sup = curr_price >= level
                            p_name = (f"🟢 DESTEK TESTİ ({level:.2f})"
                                      if is_sup else
                                      f"🔴 DİRENÇ TESTİ ({level:.2f})")
                            base_score    = 82 if is_sup else 78
                            pattern_found = True
                            break
                except Exception:
                    pass

            # --- 3. LİSTEYE ALMA VE PUANLAMA ---
            if pattern_found:
                # Hacim çarpanı ekle
                base_score += (vol_ratio * 5)

                # Mansfield bonusu/cezası
                if mansfield_gp > 0: base_score += 8
                elif mansfield_gp < -1: base_score -= 8

                # Ceza puanlarını uygula
                if "Hacim Cılız" in warning_text: base_score -= 10
                if "Düşüşte" in warning_text: base_score -= 15
                if "SMA200 Altında" in warning_text: base_score -= 8
                if "Kararsız Mum" in warning_text: base_score -= 5

                results.append({
                    "Sembol":    symbol,
                    "Puan":      int(min(max(base_score, 10), 100)),
                    "RSI":       round(float(last_rsi), 1),
                    "Mansfield": round(mansfield_gp, 1),
                    "Hacim_Kat": round(vol_ratio, 1),
                    "Detay":     p_name + warning_text,
                    "is_nadir":  is_platin,
                })
            else:
                # Formasyon yok → Hazırlık Listesi
                _sma20_h = close.rolling(20).mean()
                _std20_h = close.rolling(20).std()
                _bb_w = ((_sma20_h + 2*_std20_h) - (_sma20_h - 2*_std20_h)) / (_sma20_h + 0.0001)
                _pct30 = _bb_w.rolling(60).quantile(0.30).iloc[-1]
                is_baz = (not pd.isna(_pct30)) and (_bb_w.iloc[-1] < _pct30 * 1.1)
                etiket = "📦 Baz Kurulumu" if is_baz else "⏳ Hazırlık"
                hazirlik_list.append({
                    "Sembol":    symbol,
                    "RSI":       round(float(last_rsi), 1),
                    "Mansfield": round(mansfield_gp, 1),
                    "Hacim_Kat": round(vol_ratio, 1),
                    "Durum":     etiket,
                    "is_nadir":  is_platin,
                })

        except Exception as e:
            # Hata durumunda (örneğin veri eksikliği) o sembolü atla
            continue

    formations_df = (pd.DataFrame(results)
                       .sort_values(by=["is_nadir", "Puan"], ascending=[False, False])
                       .reset_index(drop=True)) if results else pd.DataFrame()  # is_nadir sütunu is_platin değerini taşır
    hazirlik_df   = (pd.DataFrame(hazirlik_list)
                       .sort_values(by=["is_nadir", "Mansfield"], ascending=[False, False])
                       .reset_index(drop=True)) if hazirlik_list else pd.DataFrame()
    return {"formations": formations_df, "hazirlik": hazirlik_df}

# ==============================================================================
# BÖLÜM 9 — STP SİNYAL TARAMASI
# Supertrend + Price Action kombinasyonu. Momentum sinyallerini
# batch olarak işleyen scanner.
# ==============================================================================
@st.cache_data(ttl=900)
def scan_stp_signals(asset_list):
    """
    Optimize edilmiş STP tarayıcı.
    """
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty: return [], [], []

    cross_signals = []
    trend_signals = []
    filtered_signals = []

    stock_dfs = []
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]:
                    stock_dfs.append((symbol, data[symbol]))
            else:
                if len(asset_list) == 1:
                    stock_dfs.append((symbol, data))
        except: continue

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_single_stock_stp, sym, df) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                if res["type"] == "cross_up":
                    cross_signals.append(res["data"])
                    if res.get("is_filtered"):
                        filtered_signals.append(res["data"])
                elif res["type"] == "trend_up":
                    trend_signals.append(res["data"])

    cross_signals.sort(key=lambda x: x.get("Hacim_Kat", 0), reverse=True)
    filtered_signals.sort(key=lambda x: x.get("Hacim_Kat", 0), reverse=True) 
    trend_signals.sort(key=lambda x: x["Gun"], reverse=False) # Trend olanları hala gün sayısına göre sıralamak mantıklı
    return cross_signals, trend_signals, filtered_signals

# ==============================================================================
# BÖLÜM 10 — GİZLİ BİRİKİM TARAMASI (HIDDEN ACCUMULATION)
# Kurumsal alım izlerini tespit eden akıllı hacim + fiyat analizi.
# Benchmark'a göre rölatif güç hesabı da burada yapılır.
# ==============================================================================
def process_single_accumulation(symbol, df, benchmark_series):
    try:
        if df.empty or 'Close' not in df.columns: return None
        df = df.dropna(subset=['Close'])
        if len(df) < 60: return None

        close = df['Close']
        open_ = df['Open']
        high = df['High']
        low = df['Low']
        volume = df['Volume'] if 'Volume' in df.columns else pd.Series([1]*len(df), index=df.index)
        
        # --- 1. SAVAŞ'IN GÜVENLİK KALKANI (SON 2 GÜN KURALI) ---
        price_now = float(close.iloc[-1])
        if len(close) > 2:
            price_2_days_ago = float(close.iloc[-3]) 
            # Son 2 gün toplam %3'ten fazla düştüyse (0.97 altı) ELE.
            if price_now < (price_2_days_ago * 0.97): 
                return None 

        # --- 2. HACİM KONTROLÜ (Artık Global Olarak Hesaplanıyor) ---
        volume_for_check = float(volume.iloc[-1])

        # --- 3. MEVCUT MANTIK (TOPLAMA & FORCE INDEX) ---
        delta = close.diff()
        force_index = delta * volume 
        mf_smooth = force_index.ewm(span=2, adjust=False).mean()

        last_10_mf = mf_smooth.tail(10)
        last_10_close = close.tail(10)
        
        if len(last_10_mf) < 10: return None
        
        pos_days_count = (last_10_mf > 0).sum()
        if pos_days_count < 7: return None 

        price_start = float(last_10_close.iloc[0]) 
        if price_start == 0: return None
        
        change_pct = (price_now - price_start) / price_start
        avg_mf = float(last_10_mf.mean())
        
        if avg_mf <= 0: return None
        if change_pct > 0.05: return None 
        # --- HALÜSİNASYON ÖNLEYİCİ (RSI FİLTRESİ) ---
        # Eğer RSI > 60 ise, fiyat tepededir. Bu 'Toplama' değil, 'Dağıtım'dır.
        # Bu yüzden RSI şişikse, bu hisseyi listeden atıyoruz.
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi_check = 100 - (100 / (1 + (gain / loss))).iloc[-1]
        
        if rsi_check > 60: return None # Şişkin hisseyi yok say.

        # --- 4. MANSFIELD RS (GÜÇ) ---
        rs_status = "Zayıf"
        rs_score = 0
        if benchmark_series is not None:
            try:
                common_idx = close.index.intersection(benchmark_series.index)
                if len(common_idx) > 50:
                    stock_aligned = close.loc[common_idx]
                    bench_aligned = benchmark_series.loc[common_idx]
                    rs_ratio = stock_aligned / bench_aligned
                    rs_ma = rs_ratio.rolling(50).mean()
                    mansfield = ((rs_ratio / rs_ma) - 1) * 10
                    curr_rs = float(mansfield.iloc[-1])
                    if curr_rs > 0: 
                        rs_status = "GÜÇLÜ (Endeks Üstü)"
                        rs_score = 1 
                        if curr_rs > float(mansfield.iloc[-5]): 
                            rs_status += " 🚀"
                            rs_score = 2
            except: pass

        # --- 5. POCKET PIVOT (ZAMAN AYARLI KONTROL) ---
        is_pocket_pivot = False
        pp_desc = "-"
        
        is_down_day = close < open_
        down_volumes = volume.where(is_down_day, 0)
        max_down_vol_10 = down_volumes.iloc[-4:-1].max()
        
        is_up_day = float(close.iloc[-1]) > float(open_.iloc[-1])
        
        if is_up_day and (volume_for_check > max_down_vol_10):
            is_pocket_pivot = True
            if float(volume.iloc[-1]) < max_down_vol_10:
                pp_desc = "⚡ PIVOT (Hacim Hızı Yüksek)"
            else:
                pp_desc = "⚡ POCKET PIVOT (Onaylı)"
            rs_score += 3 

        # --- YENİ EKLENEN: LAZYBEAR SQUEEZE KONTROLÜ ---
        is_sq = check_lazybear_squeeze(df)
        
        # Kalite Etiketi Belirleme
        if is_sq:
            quality_label = "A KALİTE (Sıkışmış)"
            # Squeeze varsa skoru ödüllendir (Listede üste çıksın)
            rs_score += 5 
        else:
            quality_label = "B KALİTE (Normal)"

        # --- SKORLAMA ---
        base_score = avg_mf * (10.0 if change_pct < 0 else 5.0)
        final_score = base_score * (1 + rs_score) 
        if avg_mf > 1_000_000: mf_str = f"{avg_mf/1_000_000:.1f}M"
        elif avg_mf > 1_000: mf_str = f"{avg_mf/1_000:.0f}K"
        else: mf_str = f"{int(avg_mf)}"
        squeeze_score = final_score  # Bölme kaldırıldı: sıfır değişim ödüllendirme hatası düzeltildi

        return {
            "Sembol": symbol,
            "Fiyat": f"{price_now:.2f}",
            "Degisim_Raw": change_pct,
            "Degisim_Str": f"%{change_pct*100:.1f}",
            "MF_Gucu_Goster": mf_str, 
            "Gun_Sayisi": f"{pos_days_count}/10",
            "Skor": squeeze_score,
            "RS_Durumu": rs_status,       
            "Pivot_Sinyali": pp_desc,     
            "Pocket_Pivot": is_pocket_pivot,
            "Kalite": quality_label,
            "Hacim": float(volume.iloc[-1])
        }
    except Exception: return None

@st.cache_data(ttl=900)
def scan_hidden_accumulation(asset_list):
    # 1. Önce Hisse Verilerini Çek
    data = get_batch_data_cached(asset_list, period="1y") # RS için süreyi 1y yaptım (önce 1mo idi)
    if data.empty: return pd.DataFrame()

    # 2. Endeks Verisini Çek (Sadece tek sefer)
    current_cat = st.session_state.get('category', 'S&P 500')
    benchmark = get_benchmark_data(current_cat)

    results = []
    stock_dfs = []
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]:
                    stock_dfs.append((symbol, data[symbol]))
            else:
                if len(asset_list) == 1: stock_dfs.append((symbol, data))
        except: continue

    # 3. Paralel İşlem (Benchmark'ı da gönderiyoruz)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        # benchmark serisini her fonksiyona argüman olarak geçiyoruz
        futures = [executor.submit(process_single_accumulation, sym, df, benchmark) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)

    if results: 
        df_res = pd.DataFrame(results)
        # Önce Pocket Pivot olanları, sonra Skoru yüksek olanları üste al
        return df_res.sort_values(by=["Pocket_Pivot", "Kalite", "Skor", "Hacim"], ascending=[False, True, False, False])
    
    return pd.DataFrame()

# ==============================================================================
# BÖLÜM 11 — RADAR 1 VE RADAR 2 TARAMALARI
# Radar1: momentum + trend öncü sinyaller.
# Radar2: hacim filtreli kırılım adayları.
# ==============================================================================
def process_single_radar1(symbol, df, bench_series=None):
    try:
        if df.empty or 'Close' not in df.columns: return None
        df = df.dropna(subset=['Close'])
        if len(df) < 60: return None
        
        close = df['Close']; high = df['High']; low = df['Low']
        volume = df['Volume'] if 'Volume' in df.columns else pd.Series([0]*len(df))
        
        # Göstergeler
        ema5 = close.ewm(span=5, adjust=False).mean()
        ema20 = close.ewm(span=20, adjust=False).mean()
        sma20 = close.rolling(20).mean(); std20 = close.rolling(20).std()
        
        # Bollinger Squeeze Hesabı
        bb_width = ((sma20 + 2*std20) - (sma20 - 2*std20)) / (sma20 - 0.0001)

        # MACD Hesabı
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        hist = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        
        # RSI Hesabı
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss)))
        
        # ADX Hesabı (Trend Gücü)
        try:
            plus_dm = high.diff(); minus_dm = low.diff()
            plus_dm[plus_dm < 0] = 0; minus_dm[minus_dm > 0] = 0
            tr1 = pd.DataFrame(high - low); tr2 = pd.DataFrame(abs(high - close.shift(1))); tr3 = pd.DataFrame(abs(low - close.shift(1)))
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr_adx = tr.rolling(14).mean()
            plus_di = 100 * (plus_dm.ewm(alpha=1/14).mean() / atr_adx)
            minus_di = 100 * (abs(minus_dm).ewm(alpha=1/14).mean() / atr_adx)
            dx = (abs(plus_di - minus_di) / abs(plus_di + minus_di)) * 100
            curr_adx = float(dx.rolling(14).mean().iloc[-1])
        except: curr_adx = 20

        score = 0; reasons = []; details = {}
        curr_c = float(close.iloc[-1]); curr_vol = float(volume.iloc[-1])
        avg_vol = float(volume.rolling(5).mean().iloc[-1]) if len(volume) > 5 else 1.0
        
        # --- PUANLAMA (7 MADDE) ---
        
        # 1. Squeeze (Patlama Hazırlığı)
        if bb_width.iloc[-1] <= bb_width.tail(60).min() * 1.1: score += 1; reasons.append("🚀 Squeeze"); details['Squeeze'] = True
        else: details['Squeeze'] = False
        
        # 2. Trend (Kısa Vade Yükseliş)
        trend_condition = (ema5.iloc[-1] > ema20.iloc[-1] * 1.01) 
            
        if trend_condition: 
                score += 1
                reasons.append("⚡ Trend")
                details['Trend'] = True
        else: 
                details['Trend'] = False
        
        # 3. MACD (Momentum Artışı)
        if hist.iloc[-1] > hist.iloc[-2]: score += 1; reasons.append("🟢 MACD"); details['MACD'] = True
        else: details['MACD'] = False
        
        # 4. Hacim (İlgi Var mı?)
        if curr_vol > avg_vol * 1.2: score += 1; reasons.append("🔊 Hacim"); details['Hacim'] = True
        else: details['Hacim'] = False
        
        # 5. Breakout (Zirveye Yakınlık)
        if curr_c >= high.tail(20).max() * 0.98: score += 1; reasons.append("🔨 Breakout"); details['Breakout'] = True
        else: details['Breakout'] = False
        
        # 6. RSI Güçlü (İvme)
        rsi_c = float(rsi.iloc[-1])
        if 30 < rsi_c < 65 and rsi_c > float(rsi.iloc[-2]): score += 1; reasons.append("⚓ RSI Güçlü"); details['RSI Güçlü'] = (True, rsi_c)
        else: details['RSI Güçlü'] = (False, rsi_c)
        
        # 7. ADX (Trendin Gücü Yerinde mi?)
        if curr_adx > 25:
            score += 1; reasons.append(f"💪 Güçlü Trend"); details['ADX Durumu'] = (True, curr_adx)
        else:
            details['ADX Durumu'] = (False, curr_adx)

        # 8. Mansfield RS (Endekse Göre Göreceli Güç)
        mansfield_r1 = 0.0
        if bench_series is not None and len(close) > 60:
            try:
                common = close.index.intersection(bench_series.index)
                if len(common) > 55:
                    rs_r = close.reindex(common) / bench_series.reindex(common)
                    rs_m = rs_r.rolling(50).mean()
                    m = ((rs_r / rs_m) - 1) * 10
                    mansfield_r1 = float(m.iloc[-1]) if not np.isnan(m.iloc[-1]) else 0.0
            except: pass
        if mansfield_r1 > 0: score += 1; reasons.append("📈 RS Lider"); details['Mansfield RS'] = (True, mansfield_r1)
        elif mansfield_r1 < 0: details['Mansfield RS'] = (False, mansfield_r1)
        else: details['Mansfield RS'] = (False, 0.0)

        return { "Sembol": symbol, "Fiyat": f"{curr_c:.2f}", "Skor": score, "Nedenler": " | ".join(reasons), "Detaylar": details }
    except: return None

@st.cache_data(ttl=3600)
def analyze_market_intelligence(asset_list, category="S&P 500"):
    data = get_batch_data_cached(asset_list, period="6mo")
    if data.empty: return pd.DataFrame()

    bench = get_benchmark_data(category)

    signals = []
    stock_dfs = []
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]: stock_dfs.append((symbol, data[symbol]))
            else:
                if len(asset_list) == 1: stock_dfs.append((symbol, data))
        except: continue

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_single_radar1, sym, df, bench) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: signals.append(res)

    return pd.DataFrame(signals).sort_values(by="Skor", ascending=False) if signals else pd.DataFrame()

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


def process_single_radar2(symbol, df, idx, min_price, max_price, min_avg_vol_m):
    try:
        if df.empty or 'Close' not in df.columns: return None
        df = df.dropna(subset=['Close'])
        if len(df) < 120: return None
        
        close = df['Close']; high = df['High']; low = df['Low']
        volume = df['Volume'] if 'Volume' in df.columns else pd.Series([0]*len(df))
        curr_c = float(close.iloc[-1])
        
        # Filtreler
        if curr_c < min_price or curr_c > max_price: return None
        avg_vol_20 = float(volume.rolling(20).mean().iloc[-1])
        if avg_vol_20 < min_avg_vol_m * 1e6: return None
        
        # Trend Ortalamaları
        sma20 = close.rolling(20).mean(); sma50 = close.rolling(50).mean()
        sma100 = close.rolling(100).mean(); sma200 = close.rolling(200).mean()
        
        trend = "Yatay"
        if not np.isnan(sma200.iloc[-1]):
            if curr_c > sma50.iloc[-1] > sma100.iloc[-1] > sma200.iloc[-1] and sma200.iloc[-1] > sma200.iloc[-20]: trend = "Boğa"
            elif curr_c < sma200.iloc[-1] and sma200.iloc[-1] < sma200.iloc[-20]: trend = "Ayı"
        
        # RSI ve MACD (Sadece Setup için histogram hesabı kalıyor, puanlamadan çıkacak)
        delta = close.diff(); gain = (delta.where(delta > 0, 0)).rolling(14).mean(); loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss))); rsi_c = float(rsi.iloc[-1])
        ema12 = close.ewm(span=12, adjust=False).mean(); ema26 = close.ewm(span=26, adjust=False).mean()
        hist = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        
        # Breakout Oranı
        recent_high_60 = float(high.rolling(60).max().iloc[-1])
        breakout_ratio = curr_c / recent_high_60 if recent_high_60 > 0 else 0
        
        # Mansfield RS Skoru (Endeks)
        rs_score = 0.0
        if idx is not None and len(close) > 60 and len(idx) > 60:
            common_index = close.index.intersection(idx.index)
            if len(common_index) > 55:
                cs = close.reindex(common_index); isx = idx.reindex(common_index)
                rs_ratio = cs / isx
                rs_ma = rs_ratio.rolling(50).mean()
                mansfield = ((rs_ratio / rs_ma) - 1) * 10
                rs_score = float(mansfield.iloc[-1]) if not np.isnan(mansfield.iloc[-1]) else 0.0
        
        # --- YENİ EKLENEN: ICHIMOKU BULUTU (Kumo) ---
        # Bulut şu anki fiyatın altında mı? (Trend Desteği)
        # Ichimoku değerleri 26 periyot ileri ötelenir. Yani bugünün bulutu, 26 gün önceki verilerle çizilir.
        tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
        kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
        
        # Span A (Bugün için değeri 26 gün önceki hesaptan gelir)
        span_a_calc = (tenkan + kijun) / 2
        # Span B (Bugün için değeri 26 gün önceki hesaptan gelir)
        span_b_calc = (high.rolling(52).max() + low.rolling(52).min()) / 2
        
        # Bugünün bulut sınırları (Veri setinin sonundan 26 önceki değerler)
        cloud_a = float(span_a_calc.iloc[-26])
        cloud_b = float(span_b_calc.iloc[-26])
        is_above_cloud = curr_c > max(cloud_a, cloud_b)
        # -----------------------------------------------

        setup = "-"; tags = []; score = 0; details = {}
        avg_vol_20 = max(avg_vol_20, 1); vol_spike = volume.iloc[-1] > avg_vol_20 * 1.3
        
        # Setup Tespiti
        if trend == "Boğa" and breakout_ratio >= 0.97: setup = "Breakout"; tags.append("Zirve")
        if trend == "Boğa" and setup == "-":
            if sma20.iloc[-1] <= curr_c <= sma50.iloc[-1] * 1.02 and 40 <= rsi_c <= 55: setup = "Pullback"; tags.append("Düzeltme")
            if volume.iloc[-1] < avg_vol_20 * 0.9: score += 0; tags.append("Sığ Satış")
        if setup == "-":
            if rsi.iloc[-2] < 30 <= rsi_c and hist.iloc[-1] > hist.iloc[-2]: setup = "Dip Dönüşü"; tags.append("Dip Dönüşü")
        
        # --- PUANLAMA (7 Madde) ---
        
        # 1. Hacim Patlaması
        if vol_spike: score += 1; tags.append("Hacim+"); details['Hacim Patlaması'] = True
        else: details['Hacim Patlaması'] = False

        # 2. RS (Endeks Gücü)
        if rs_score > 0: score += 1; tags.append("RS+"); details['RS (S&P500)'] = True
        else: details['RS (S&P500)'] = False
        
        # 3. Boğa Trendi (SMA Dizilimi)
        if trend == "Boğa": score += 1; details['Boğa Trendi'] = True
        else:
            if trend == "Ayı": score -= 1
            details['Boğa Trendi'] = False
            
        # 4. Ichimoku Bulutu (YENİ - MACD YERİNE GELDİ)
        if is_above_cloud: score += 1; details['Ichimoku'] = True
        else: details['Ichimoku'] = False

        # 5. 60 Günlük Zirveye Yakınlık
        details['60G Zirve'] = breakout_ratio >= 0.90
        if details['60G Zirve']: score += 1

        # 6. RSI Uygun Bölge (Aşırı şişmemiş)
        is_rsi_suitable = (40 <= rsi_c <= 65) # Biraz genişlettim
        details['RSI Bölgesi'] = (is_rsi_suitable, rsi_c)
        if is_rsi_suitable: score += 1
        
        # 7. Setup Puanı (Yukarıda hesaplandı, max 2 puan ama biz varlığını kontrol edelim)
        # Setup varsa ekstra güvenilirdir.
        if setup != "-": score += 1
        
        # ── Darvas Kutu ───────────────────────────────────────────
        _dbox = detect_darvas_box(df)
        _d_quality = _dbox['quality']        if _dbox else None
        _d_status  = _dbox['status']         if _dbox else None
        _d_top     = _dbox['box_top']        if _dbox else None
        _d_bottom  = _dbox['box_bottom']     if _dbox else None
        _d_age     = _dbox['box_age']        if _dbox else None
        _d_class   = _dbox['breakout_class'] if _dbox else None

        return {
            "Sembol": symbol, "Fiyat": round(curr_c, 2), "Trend": trend,
            "Setup": setup, "Skor": score, "RS": round(rs_score * 100, 1),
            "Etiketler": " | ".join(tags), "Detaylar": details,
            "Darvas_Quality": _d_quality, "Darvas_Status": _d_status,
            "Darvas_Top": _d_top, "Darvas_Bottom": _d_bottom,
            "Darvas_Age": _d_age, "Darvas_Class": _d_class,
        }
    except: return None

# ==============================================================================
# BÖLÜM 12 — HACİM ANALİZ MODÜLLERİ
# Volume Delta, Volume Profile (POC), Naked POC tespiti.
# Kurumsal alım/satım baskısını ölçen gelişmiş hacim fonksiyonları.
# ==============================================================================
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

def calculate_volume_profile(df, lookback=50, bins=20):
    """
    Son 'lookback' kadar mumu alır, fiyatı 'bins' kadar parçaya böler 
    ve en çok hacmin döndüğü fiyatı (Point of Control) orantısal dağılımla bulur.
    """
    if len(df) < lookback:
        lookback = len(df)
        
    recent_df = df.tail(lookback).copy()
    
    # Fiyatı min ve max arasında belirle
    min_price = float(recent_df['Low'].min())
    max_price = float(recent_df['High'].max())
    
    if min_price == max_price: 
        return min_price
        
    # Fiyat dilimlerini oluştur
    price_bins = np.linspace(min_price, max_price, bins + 1)
    volume_profile = np.zeros(bins)
    
    # Typical_Price yerine Orantısal Dağılım Döngüsü
    for _, row in recent_df.iterrows():
        high = float(row['High'])
        low = float(row['Low'])
        vol = float(row['Volume'])
        candle_range = high - low
        
        if candle_range <= 0:
            idx = np.digitize((high + low) / 2, price_bins) - 1
            idx = min(max(idx, 0), bins - 1)
            volume_profile[idx] += vol
            continue
            
        for i in range(bins):
            bin_bottom = price_bins[i]
            bin_top = price_bins[i+1]
            
            if high >= bin_bottom and low <= bin_top:
                overlap_top = min(high, bin_top)
                overlap_bottom = max(low, bin_bottom)
                overlap_range = overlap_top - overlap_bottom
                
                if overlap_range > 0:
                    volume_profile[i] += vol * (overlap_range / candle_range)
                    
    # En yüksek hacme sahip dilimi (POC) bul
    poc_index = np.argmax(volume_profile)
    
    # POC Fiyatını belirle
    poc_price = (price_bins[poc_index] + price_bins[poc_index + 1]) / 2.0
        
    return poc_price
    
# ==============================================================================
# 🧠 MERKEZİ VERİ ÖNBELLEĞİ (BAN KORUMASI VE SÜPER HIZ)
# ==============================================================================
@st.cache_data(ttl=900, show_spinner=False)
def fetch_market_data_cached(tickers_tuple):
    import yfinance as yf
    tickers_str = " ".join(tickers_tuple)
    return yf.download(tickers_str, period="1y", group_by='ticker', auto_adjust=True, progress=False, threads=True)

@st.cache_data(ttl=900, show_spinner=False)
def fetch_index_data_cached():
    import yfinance as yf
    import pandas as pd
    try:
        index_df = yf.download("XU100.IS", period="1y", progress=False)
        if not index_df.empty:
            if isinstance(index_df.columns, pd.MultiIndex):
                return index_df['Close'].iloc[:, 0] if not index_df['Close'].empty else None
            else:
                return index_df['Close']
    except:
        pass
    return None

@st.cache_data(ttl=900)
def radar2_scan(asset_list, min_price=5, max_price=5000, min_avg_vol_m=0.5):
    # Akıllı önbellek + ban korumalı veri çekimi
    try:
        data = get_batch_data_cached(asset_list, period="1y")
    except Exception as e:
        return pd.DataFrame()

    if data.empty: return pd.DataFrame()

    # Kategori bazlı doğru benchmark (BIST → XU100, diğerleri → S&P500)
    cat = st.session_state.get('category', 'S&P 500')
    bench_ticker = "XU100.IS" if "BIST" in cat else "^GSPC"
    try:
        idx_df = get_safe_historical_data(bench_ticker, period="1y")
        idx = idx_df['Close'] if idx_df is not None and not idx_df.empty else None
    except:
        idx = None

    results = []
    stock_dfs = []
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]: stock_dfs.append((symbol, data[symbol]))
            else:
                if len(asset_list) == 1: stock_dfs.append((symbol, data))
        except: continue

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_single_radar2, sym, df, idx, min_price, max_price, min_avg_vol_m) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)

    return pd.DataFrame(results).sort_values(by=["Skor", "RS"], ascending=False).head(50) if results else pd.DataFrame()

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
        
        # --- ŞARTLAR ---
        cond_ema = ema5.iloc[-1] > ema20.iloc[-1]
        cond_vol = rvol > 1.2 
        cond_prox = (curr_price > high_val * 0.90) and (curr_price <= high_val * 1.1)
        cond_rsi = rsi < 70
        sma_ok = sma20.iloc[-1] > sma50.iloc[-1]
        
        if cond_ema and cond_vol and cond_prox and cond_rsi:
            
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

@st.cache_data(ttl=3600)
def agent3_breakout_scan(asset_list):
    data = get_batch_data_cached(asset_list, period="6mo")
    if data.empty: return pd.DataFrame()

    results = []
    stock_dfs = []
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]: stock_dfs.append((symbol, data[symbol]))
            else:
                if len(asset_list) == 1: stock_dfs.append((symbol, data))
        except: continue

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_single_breakout, sym, df) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)
    
    return pd.DataFrame(results).sort_values(by="Hacim", ascending=False) if results else pd.DataFrame()

def process_single_confirmed(symbol, df, bench_series=None):
    try:
        if df.empty or 'Close' not in df.columns: return None
        df = df.dropna(subset=['Close'])
        if len(df) < 100: return None

        # DÜZELTME 1: open_ eklendi (Aşağıda Gap hesabı hata vermesin diye)
        close = df['Close']; high = df['High']; open_ = df['Open']; volume = df['Volume'] if 'Volume' in df.columns else pd.Series([1]*len(df))
        
        # --- 1. ADIM: ZİRVE KONTROLÜ (Son 20 İş Günü) ---
        # 👑 DÜZELTME 2: Artık iğnelere (high) değil, mum kapanışlarına (close) bakıyoruz!
        high_val = close.iloc[:-1].tail(20).max()
        curr_close = float(close.iloc[-1])
        
        # Eğer bugünkü fiyat, geçmiş 20 günün zirvesini geçmediyse ELE.
        if curr_close <= high_val: return None 

        # --- 2. ADIM: GÜVENLİ HACİM HESABI ---
        # Geçmiş 20 günün ortalama hacmi (Bugün hariç)
        avg_vol_20 = volume.rolling(20).mean().shift(1).iloc[-1]
        curr_vol = float(volume.iloc[-1]) # Bu hacim zaten ana merkezde güncellendi!
        
        # PERFORMANS ORANI
        if avg_vol_20 > 0:
            performance_ratio = curr_vol / avg_vol_20
        else:
            performance_ratio = 0
            
        # Filtre: Eğer o saate kadar yapması gereken hacmi yapmadıysa ELE.
        if performance_ratio < 0.6: return None
       
        # --- GÜVENLİK 3: GAP (BOŞLUK) TUZAĞI ---
        prev_close = float(close.iloc[-2])
        curr_open = float(open_.iloc[-1])
        gap_pct = (curr_open - prev_close) / prev_close
        if gap_pct > 0.03: return None # %3'ten fazla GAP'li açıldıysa tren kaçmıştır.
       
        # --- GÖRSEL ETİKETLEME ---
        # Kullanıcıya "Günlük ortalamanın kaç katına gidiyor" bilgisini verelim
        # Bu 'Projected Volume' (Tahmini Gün Sonu Hacmi) mantığıdır.
        vol_display = f"{performance_ratio:.1f}x (Hız)"
        
        if performance_ratio > 1.5: vol_display = f"{performance_ratio:.1f}x (Patlama🔥)"
        elif performance_ratio >= 1.0: vol_display = f"{performance_ratio:.1f}x (Güçlü✅)"
        else: vol_display = f"{performance_ratio:.1f}x (Yeterli🆗)"

        # --- 3. DİĞER TEKNİK FİLTRELER ---
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_upper = sma20 + (2 * std20); bb_lower = sma20 - (2 * std20)
        bb_width = (bb_upper - bb_lower) / sma20
        avg_width = bb_width.rolling(20).mean().iloc[-1]
        
        is_range_breakout = bb_width.iloc[-2] < avg_width * 0.9 
        breakout_type = "📦 RANGE" if is_range_breakout else "🏔️ ZİRVE"
        
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]
        
        if rsi > 85: return None

        # --- MANSFIELD RS (Endekse Göre Göreceli Güç) ---
        mansfield_cb = 0.0
        if bench_series is not None and len(close) > 60:
            try:
                common = close.index.intersection(bench_series.index)
                if len(common) > 55:
                    rs_r = close.reindex(common) / bench_series.reindex(common)
                    rs_m = rs_r.rolling(50).mean()
                    m = ((rs_r / rs_m) - 1) * 10
                    mansfield_cb = float(m.iloc[-1]) if not np.isnan(m.iloc[-1]) else 0.0
            except: pass
        if mansfield_cb < -1.5: return None  # Kırılım yapıyor ama endeksin gerisinde = elenir
        if mansfield_cb > 0: breakout_type = breakout_type + " 📈RS"

        return {
            "Sembol": symbol,
            "Fiyat": f"{curr_close:.2f}",
            "Kirim_Turu": breakout_type,
            "Hacim_Kati": vol_display,
            "RSI": int(rsi),
            "SortKey": performance_ratio,
            "Hacim": curr_vol
        }
    except: return None

@st.cache_data(ttl=3600)
def scan_confirmed_breakouts(asset_list, category="S&P 500"):
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty: return pd.DataFrame()

    bench = get_benchmark_data(category)

    results = []
    stock_dfs = []
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]: stock_dfs.append((symbol, data[symbol]))
            else:
                if len(asset_list) == 1: stock_dfs.append((symbol, data))
        except: continue

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_single_confirmed, sym, df, bench) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)
    
    return pd.DataFrame(results).sort_values(by="Hacim", ascending=False).head(20) if results else pd.DataFrame()

# ==============================================================================
# BÖLÜM 14 — TEMEL SKOR VE MASTER SKOR
# Finansal temel verilerden skor üretimi. Tüm alt skorları
# (Trend %40, Momentum %30, ICT %15, Radar2 %15) birleştiren calculate_master_score fonksiyonu burada.
# ==============================================================================
@st.cache_data(ttl=3600)
def get_fundamental_score(ticker):
    """
    GLOBAL STANDART V2: Kademeli Puanlama (Grading System)
    AGNC gibi sektörleri veya Apple gibi devleri '0' ile cezalandırmaz.
    """
    # Endeks veya Kripto kontrolü
    if ticker.startswith("^") or "XU" in ticker or "-USD" in ticker:
        return {"score": 50, "details": [], "valid": False} 

    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        if not info: return {"score": 50, "details": ["Veri Yok"], "valid": False}
        
        score = 0
        details = []
        
        # --- KADEMELİ PUANLAMA MOTORU ---
        def rate(val, thresholds, max_p):
            if not val: return 0
            val = val * 100 if val < 10 else val # Yüzdeye çevir
            # Eşikler: [Düşük, Orta, Yüksek] -> Puanlar kademeli artar
            step = max_p / len(thresholds)
            earned = 0
            for t in thresholds:
                if val > t: earned += step
            return earned

        # 1. BÜYÜME (GROWTH) - Max 40 Puan
        # Ciro Büyümesi: %0 üstü puan almaya başlar. %25 üstü tavan yapar.
        rev_g = info.get('revenueGrowth', 0)
        s_rev = rate(rev_g, [0, 10, 20, 25], 20) 
        score += s_rev
        if s_rev >= 10: details.append(f"Ciro Büyümesi: %{rev_g*100:.1f}")

        # Kâr Büyümesi
        earn_g = info.get('earningsGrowth', 0)
        s_earn = rate(earn_g, [0, 10, 20, 25], 20)
        score += s_earn
        if s_earn >= 10: details.append(f"Kâr Büyümesi: %{earn_g*100:.1f}")

        # 2. KALİTE (QUALITY) - Max 40 Puan
        # ROE: %5 üstü puan başlar. %20 üstü tavan.
        roe = info.get('returnOnEquity', 0)
        s_roe = rate(roe, [5, 10, 15, 20], 20)
        score += s_roe
        if s_roe >= 10: details.append(f"ROE: %{roe*100:.1f}")

        # Marjlar
        margin = info.get('profitMargins', 0)
        s_marg = rate(margin, [5, 10, 15, 20], 20)
        score += s_marg
        if s_marg >= 10: details.append(f"Net Marj: %{margin*100:.1f}")

        # 3. KURUMSAL SAHİPLİK - Max 20 Puan
        inst = info.get('heldPercentInstitutions', 0)
        s_inst = rate(inst, [10, 30, 50, 70], 20)
        score += s_inst
        if s_inst >= 10: details.append(f"Kurumsal: %{inst*100:.0f}")

        return {"score": min(score, 100), "details": details, "valid": True}
        
    except Exception:
        return {"score": 50, "details": [], "valid": False}

# ==============================================================================
# BÖLÜM 15 — GÜÇLÜ DÖNÜŞ ADAYLARI TARAMASI
# Düzeltme sonrası güç kazanan hisseleri tespit eder.
# Benchmark karşılaştırmalı rölatif güç analizi içerir.
# ==============================================================================
def calculate_guclu_donus_adaylari(ticker, df, bist100_close=None):
    """
    🔄 GÜÇLÜ DÖNÜŞ+ ADAYLARI (v9 — 7 Bağımsız Kriter, min 5/7)
    ZORUNLU  : 50 ≤ RSI ≤ 65  (bant dışı = direkt elenme)
    PUANLANAN: 7 bağımsız kriter — her biri farklı bir boyut ölçer
      P1. Fiyat > EMA13              : Kısa vade trend
      P2. EMA13 eğimi + (son 5g)     : Trend kalitesi / ivme
      P3. RS vs BIST100 > 0 (20g)    : Göreceli güç — endeksten güçlü mü?
      P4. RSI > RSI EMA9             : Momentum ivmeleniyor
      P5. OBV son 5g ↑               : Akıllı para yönü
      P6. Hacim 5+/10g ort. üstünde  : Kurumsal aktivite frekansı
      P7. Fiyat > Yıllık VWAP        : Kurumsal ortalama maliyet üstünde
    BONUS (puan ekler, zorunlu değil):
      +1   Geçen ay stop avı 🔍
      +0.5 Haftalık yükseliş ↑
    MİNİMUM: 5/7 baz puan (bonus sayılmaz)
    """
    try:
        if df is None or df.empty or len(df) < 30:
            return None

        close  = df['Close']

        if 'Volume' not in df.columns or df['Volume'].isnull().all():
            return None
        volume = df['Volume']

        # ── ZORUNLU: 50 ≤ RSI ≤ 65 ──────────────────────────────────────
        delta   = close.diff()
        gain    = delta.where(delta > 0, 0).rolling(14).mean()
        loss    = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi     = 100 - (100 / (1 + gain / loss))
        rsi_val = float(rsi.iloc[-1])
        if pd.isna(rsi_val) or rsi_val < 50 or rsi_val > 65:
            return None   # bant dışı → direkt elenme

        # ── PUANLAMA ─────────────────────────────────────────────────────
        score  = 0
        passed = []
        missed = []

        # P1: Fiyat > EMA13  [Kısa vade trend]
        ema13 = close.ewm(span=13, adjust=False).mean()
        if float(close.iloc[-1]) > float(ema13.iloc[-1]):
            score += 1; passed.append("EMA13↑")
        else:
            missed.append("EMA13")

        # P2: EMA13 eğimi pozitif (son 5 günde yükseliyor)  [Trend kalitesi]
        if len(ema13) >= 6 and float(ema13.iloc[-1]) > float(ema13.iloc[-6]):
            score += 1; passed.append("EMA eğimi↑")
        else:
            missed.append("EMA eğimi")

        # P3: Göreceli Güç — hisse son 20g BIST100'den güçlü mü?  [Farklı boyut]
        rs_pozitif = False
        rs_pct     = 0.0
        if bist100_close is not None and len(bist100_close) >= 21:
            try:
                # Ortak tarihleri hizala
                _merged = close.align(bist100_close, join='inner')
                _hisse_c, _bist_c = _merged[0], _merged[1]
                if len(_hisse_c) >= 21:
                    hisse_ret = float(_hisse_c.iloc[-1]) / float(_hisse_c.iloc[-21]) - 1
                    bist_ret  = float(_bist_c.iloc[-1])  / float(_bist_c.iloc[-21])  - 1
                    rs_pct    = round((hisse_ret - bist_ret) * 100, 1)
                    rs_pozitif = rs_pct > 2.0   # en az %2 endeks üstü güç
            except Exception:
                rs_pozitif = False
        if rs_pozitif:
            score += 1; passed.append(f"RS+{rs_pct:.1f}%")
        else:
            missed.append(f"RS({rs_pct:+.1f}%)")

        # P4: RSI > RSI EMA9 (momentum ivmeleniyor)  [Momentum]
        rsi_ema9 = rsi.ewm(span=9, adjust=False).mean()
        if float(rsi.iloc[-1]) > float(rsi_ema9.iloc[-1]):
            score += 1; passed.append("RSI ivme")
        else:
            missed.append("RSI ivme")

        # P5: OBV son 5 günde yükseliyor  [Akıllı para yönü]
        obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
        if float(obv.iloc[-1]) > float(obv.iloc[-6]):
            score += 1; passed.append("OBV↑")
        else:
            missed.append("OBV")

        # P6: Son 10 günde 5+ kez hacim ortalaması üstünde  [Kurumsal aktivite]
        avg_vol20 = volume.rolling(20).mean()
        if pd.isna(avg_vol20.iloc[-1]):
            return None
        vol_arr = volume.values
        avg_arr = avg_vol20.values
        vol_10g = sum(1 for i in range(-10, 0)
                      if not np.isnan(avg_arr[i]) and avg_arr[i] > 0
                      and float(vol_arr[i]) > float(avg_arr[i]))
        if vol_10g >= 5:
            score += 1; passed.append(f"Hacim {vol_10g}/10g")
        else:
            missed.append(f"Hacim {vol_10g}/10g")

        # P7: Fiyat > Yıllık VWAP  [Kurumsal referans fiyat]
        try:
            vwap_annual = (close * volume).cumsum() / volume.cumsum()
            vwap_val    = float(vwap_annual.iloc[-1])
            if not np.isnan(vwap_val) and float(close.iloc[-1]) > vwap_val:
                score += 1; passed.append("VWAP↑")
            else:
                missed.append("VWAP")
        except Exception:
            missed.append("VWAP")

        # ── BONUS ────────────────────────────────────────────────────────
        weekly_up   = len(close) >= 6 and float(close.iloc[-1]) > float(close.iloc[-6])
        sweep_month = False
        if len(close) >= 42:
            c1 = float(close.iloc[-42])
            c2 = float(close.iloc[-21])
            sweep_month = (c2 < c1) and (float(close.iloc[-1]) > c2)

        bonus = 0.0
        if sweep_month: bonus += 1.0
        if weekly_up:   bonus += 0.5

        total_score = score + bonus

        # ── MINIMUM PUAN KONTROLÜ (6/7 baz) ─────────────────────────────
        _MIN_SCORE = 6
        if score < _MIN_SCORE:
            return None

        # ── AÇIKLAMA ─────────────────────────────────────────────────────
        hacim_kat = round(float(vol_arr[-1]) / float(avg_arr[-1]), 2) if float(avg_arr[-1]) > 0 else 0.0

        _ac_parts = []
        if sweep_month:
            _ac_parts.append("⚡ stop avı")
        _ac_parts.append(f"RSI {rsi_val:.0f} · {score}/7 kriter")
        _ac_parts.append(" + ".join(passed))
        if missed:
            _ac_parts.append(f"eksik: {', '.join(missed)}")
        if weekly_up:
            _ac_parts.append("haftalık ↑")

        return {
            "Sembol"     : ticker,
            "Fiyat"      : float(close.iloc[-1]),
            "RSI"        : round(rsi_val, 1),
            "Skor"       : score,
            "ToplamSkor" : total_score,
            "RS_Pct"     : rs_pct,
            "Z-Score"    : 0.0,
            "Z_Cheap"    : False,
            "Hacim_Kat"  : hacim_kat,
            "Hacim_10g"  : vol_10g,
            "Sweep_Ay"   : sweep_month,
            "Weekly_Up"  : weekly_up,
            "RSI_Div"    : "✅",
            "Passed"     : passed,
            "Missed"     : missed,
            "Aciklama"   : " · ".join(_ac_parts),
        }

    except Exception:
        return None

def scan_guclu_donus_batch(asset_list):
    """
    Güçlü Dönüş Adayları — Toplu Tarama Ajanı (v9)
    BIST100 verisini RS hesabı için ayrıca çeker (parquet cache'den — ek Yahoo isteği yok)
    """
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty: return pd.DataFrame()

    # ── BIST100 göreceli güç için ────────────────────────────────────────
    bist100_close = None
    try:
        _bist_ticker = "XU100.IS"
        _bist_data   = get_batch_data_cached([_bist_ticker], period="1y")
        if not _bist_data.empty:
            if isinstance(_bist_data.columns, pd.MultiIndex):
                if _bist_ticker in _bist_data.columns.levels[0]:
                    bist100_close = _bist_data[_bist_ticker]['Close'].dropna()
            else:
                bist100_close = _bist_data['Close'].dropna()
    except Exception:
        bist100_close = None

    results   = []
    stock_dfs = []

    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]:
                    df = data[symbol].dropna()
                    if not df.empty: stock_dfs.append((symbol, df))
            else:
                if len(asset_list) == 1:
                    df = data.dropna()
                    if not df.empty: stock_dfs.append((symbol, df))
        except: continue

    for symbol, df in stock_dfs:
        res = calculate_guclu_donus_adaylari(symbol, df, bist100_close=bist100_close)
        if res: results.append(res)

    if not results:
        return pd.DataFrame()

    df_out = pd.DataFrame(results)
    # Skor DESC → RS_Pct DESC → Sweep_Ay DESC → Hacim_10g DESC
    df_out = df_out.sort_values(
        by=['Skor', 'RS_Pct', 'Sweep_Ay', 'Hacim_10g'],
        ascending=[False, False, False, False]
    ).reset_index(drop=True)
    log_scan_signal("guclu_donus", df_out, category=st.session_state.get('category', ''))
    return df_out

# ==============================================================================
# BÖLÜM 16 — PRE-LAUNCH BOS TARAMASI
# Henüz kırılım yapmamış, birikim aşamasındaki hisseleri tespit eder.
# ICT yapı analizi ile BOS öncesi setup'ları filtreler.
# ==============================================================================

def calculate_prelaunch_bos(ticker, df, bist100_close=None):
    """
    Sert Eleme:
      1. Son 3 gün içinde 45-günlük swing high kırıldı (BOS)
      2. BOS öncesi 15-25 günde ≥5 gün Bollinger/Keltner sıkışması (Squeeze)
    Puanlama (100 üzerinden, min geçer: 55):
      Hacim BOS günü ≥1.5x→+25, 1.2-1.5x→+15
      RS > BIST100 (10g)→+20
      RSI 50-65→+20, 65-70→+10, >70→ELENME
      BOS'tan uzaklık <3%→+20, 3-6%→+10
      SMA50 üzerinde→+15
      BOS Day0→0, Day1→-5, Day2→-10, Day3→-15
    """
    try:
        if df is None or len(df) < 50:
            return None

        close  = df['Close']
        high   = df['High']
        volume = df['Volume']

        # ── 1. BOS: Son 3 günde 45-günlük swing high kırıldı mı? ──────────
        bos_day  = None
        bos_level = None
        for lookback in range(1, 4):          # Day 0=bugün, Day1, Day2, Day3
            idx = -(lookback)
            swing_window = close.iloc[:idx - 1] if abs(idx - 1) < len(close) else close.iloc[:-1]
            if len(swing_window) < 45:
                continue
            swing_high_45 = swing_window.iloc[-45:].max()
            if close.iloc[idx] > swing_high_45:
                bos_day   = lookback - 1     # 0-indexed
                bos_level = swing_high_45
                break

        if bos_day is None:
            return None

        # ── 2. Squeeze: BOS öncesi 15-25 günde ≥5 gün sıkışma ────────────
        pre_bos_end   = -(bos_day + 1) if bos_day > 0 else -1
        pre_bos_start = pre_bos_end - 25

        sq_slice_close = close.iloc[pre_bos_start:pre_bos_end] if pre_bos_end != 0 else close.iloc[pre_bos_start:]
        sq_slice_high  = high.iloc[pre_bos_start:pre_bos_end]  if pre_bos_end != 0 else high.iloc[pre_bos_start:]
        sq_slice_low   = df['Low'].iloc[pre_bos_start:pre_bos_end] if pre_bos_end != 0 else df['Low'].iloc[pre_bos_start:]

        if len(sq_slice_close) < 10:
            return None

        # Bollinger Bands
        bb_mid = sq_slice_close.rolling(20).mean()
        bb_std = sq_slice_close.rolling(20).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_width = (bb_upper - bb_lower) / bb_mid

        # Keltner Channel
        atr_sq = (sq_slice_high - sq_slice_low).rolling(10).mean()
        kc_width = (2 * atr_sq) / bb_mid

        squeeze_days = int((bb_width < kc_width).sum())
        if squeeze_days < 5:
            return None

        # ── 3. RSI: >70 elenme ────────────────────────────────────────────
        delta  = close.diff()
        gain   = delta.where(delta > 0, 0).rolling(14).mean()
        loss   = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi_val = float((100 - 100 / (1 + gain / loss.replace(0, 1e-9))).iloc[-1])

        if rsi_val > 70:
            return None

        # ── 4. Puanlama ───────────────────────────────────────────────────
        score = 0
        aciklama_parts = []

        # Hacim (BOS günü)
        bos_idx    = -(bos_day + 1) if bos_day > 0 else -1
        vol_bos    = float(volume.iloc[bos_idx])
        vol_avg20  = float(volume.iloc[-25:-5].mean()) if len(volume) >= 25 else float(volume.mean())
        vol_ratio  = vol_bos / vol_avg20 if vol_avg20 > 0 else 0
        if vol_ratio >= 1.5:
            score += 25
            aciklama_parts.append(f"Hacim {vol_ratio:.1f}x")
        elif vol_ratio >= 1.2:
            score += 15
            aciklama_parts.append(f"Hacim {vol_ratio:.1f}x")
        else:
            aciklama_parts.append(f"Hacim {vol_ratio:.1f}x (zayıf)")

        # RS vs BIST100 (son 10 gün)
        if bist100_close is not None and len(bist100_close) >= 11:
            stock_ret = float(close.iloc[-1] / close.iloc[-11] - 1)
            idx_ret   = float(bist100_close.iloc[-1] / bist100_close.iloc[-11] - 1)
            rs_diff   = (stock_ret - idx_ret) * 100
            if stock_ret > idx_ret:
                score += 20
                aciklama_parts.append(f"RS+{rs_diff:.1f}%")
        else:
            rs_diff = 0.0

        # RSI
        if rsi_val <= 65:
            score += 20
        elif rsi_val <= 70:
            score += 10
        aciklama_parts.append(f"RSI {rsi_val:.0f}")

        # BOS'tan uzaklık
        current_price = float(close.iloc[-1])
        bos_dist_pct  = (current_price - bos_level) / bos_level * 100
        if bos_dist_pct < 3:
            score += 20
            aciklama_parts.append(f"BOS yakın +{bos_dist_pct:.1f}%")
        elif bos_dist_pct < 6:
            score += 10
            aciklama_parts.append(f"BOS +{bos_dist_pct:.1f}%")
        else:
            aciklama_parts.append(f"BOS uzak +{bos_dist_pct:.1f}%")

        # SMA50
        sma50 = float(close.rolling(50).mean().iloc[-1])
        if current_price > sma50:
            score += 15

        # BOS Day cezası
        score -= bos_day * 5

        if score < 55:
            return None

        return {
            'Sembol':      ticker,
            'Fiyat':       round(current_price, 2),
            'Skor':        min(score, 100),
            'BOS_Day':     bos_day,
            'BOS_Level':   round(float(bos_level), 2),
            'Hacim_Kat':   round(vol_ratio, 2),
            'RSI':         round(rsi_val, 1),
            'RS_Pct':      round(rs_diff if bist100_close is not None else 0.0, 2),
            'Squeeze_Gun': squeeze_days,
            'BOS_Dist':    round(bos_dist_pct, 2),
            'Aciklama':    ' · '.join(aciklama_parts),
        }

    except Exception:
        return None


def scan_prelaunch_bos(asset_list):
    """Pre-Launch BOS — toplu tarama."""
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty:
        return pd.DataFrame()

    bist100_close = None
    try:
        _bd = get_batch_data_cached(["XU100.IS"], period="1y")
        if not _bd.empty:
            if isinstance(_bd.columns, pd.MultiIndex):
                bist100_close = _bd["XU100.IS"]['Close'].dropna()
            else:
                bist100_close = _bd['Close'].dropna()
    except Exception:
        pass

    results = []
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol not in data.columns.levels[0]:
                    continue
                df = data[symbol].dropna()
            else:
                if len(asset_list) != 1:
                    continue
                df = data.dropna()
            if df.empty:
                continue
            res = calculate_prelaunch_bos(symbol, df, bist100_close)
            if res:
                results.append(res)
        except Exception:
            continue

    if not results:
        return pd.DataFrame()

    df_out = pd.DataFrame(results).sort_values(
        by=['Skor', 'BOS_Day', 'Hacim_Kat'],
        ascending=[False, True, False]
    ).reset_index(drop=True)
    log_scan_signal("prelaunch_bos", df_out, category=st.session_state.get('category', ''))
    return df_out

# ==============================================================================
# YENİ: TEMEL ANALİZ VE MASTER SKOR MOTORU (GLOBAL STANDART)
# ==============================================================================

@st.cache_data(ttl=3600)
def get_fundamental_score(ticker):
    """
    GLOBAL STANDART: IBD, Stockopedia ve Buffett Kriterlerine Göre Puanlama.
    Veri Kaynağı: yfinance
    """
    # Endeks veya Kripto ise Temel Analiz Yoktur
    if ticker.startswith("^") or "XU" in ticker or "-USD" in ticker:
        return {"score": 0, "details": [], "valid": False}

    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        if not info: return {"score": 50, "details": ["Veri Yok"], "valid": False}
        
        score = 0
        details = []
        
        # 1. KALİTE (QUALITY) - %40 Etki (Warren Buffett Kriterleri)
        # ROE (Özkaynak Kârlılığı) - Şirketin verimliliği
        roe = info.get('returnOnEquity', 0)
        if roe and roe > 0.20: score += 20; details.append(f"Müthiş ROE: %{roe*100:.1f}")
        elif roe and roe > 0.12: score += 10
            
        # Net Kâr Marjı (Profit Margins) - Rekabet gücü
        margin = info.get('profitMargins', 0)
        if margin and margin > 0.20: score += 20; details.append(f"Yüksek Marj: %{margin*100:.1f}")
        elif margin and margin > 0.10: score += 10

        # 2. BÜYÜME (GROWTH) - %40 Etki (IBD / CANSLIM Kriterleri)
        # Çeyreklik Ciro Büyümesi
        rev_growth = info.get('revenueGrowth', 0)
        if rev_growth and rev_growth > 0.25: score += 20; details.append(f"Ciro Patlaması: %{rev_growth*100:.1f}")
        elif rev_growth and rev_growth > 0.15: score += 10
            
        # Çeyreklik Kâr Büyümesi
        earn_growth = info.get('earningsGrowth', 0)
        if earn_growth and earn_growth > 0.20: score += 20; details.append(f"Kâr Büyümesi: %{earn_growth*100:.1f}")
        elif earn_growth and earn_growth > 0.10: score += 10

        # 3. SAHİPLİK (SMART MONEY) - %20 Etki
        inst_own = info.get('heldPercentInstitutions', 0)
        if inst_own and inst_own > 0.40: score += 20; details.append("Fonlar Topluyor")
        elif inst_own and inst_own > 0.20: score += 10
            
        return {"score": min(score, 100), "details": details, "valid": True}
        
    except Exception:
        return {"score": 50, "details": ["Veri Hatası"], "valid": False}

@st.cache_data(ttl=900)
def calculate_master_score(ticker):
    """
    MASTER SKOR V2:
    - Temel analiz kaldırıldı (BIST için güvenilir veri yok)
    - ICT: sadece bias (en değerli kriter) — tek şart, tam puan
    - Radar2: session_state yerine anlık hesaplama
    - Momentum eşiği 60 → 50'ye indirildi
    - Yeni ağırlıklar: Trend %40, Momentum %30, ICT %15, Radar2 %15
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

    # AĞIRLIKLAR (temel analiz çıktı, ağırlıklar yeniden dağıtıldı)
    w_trend = 0.50 if is_index else 0.40
    w_mom   = 0.35 if is_index else 0.30
    w_ict   = 0.15 if is_index else 0.15
    w_r2    = 0.00 if is_index else 0.15

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
    # C. ICT — SADECE BIAS (En Değerli Kriter)
    # Bias = piyasa yapısının genel yönü (bullish/bearish).
    # Displacement ve zone tek başına anlamsız; bias olmadan
    # ikisi de güvenilir değil.
    # ---------------------------------------------------
    s_ict = 0
    if ict_data:
        bias = ict_data.get('bias', '')
        if "bullish" in bias:
            s_ict = 100
            pros.append(f"✅ Smart Money Yönü: Piyasa yapısı Bullish ({format_pt(100 * w_ict)} Puan)")
        elif "bearish" in bias:
            s_ict = 0
            cons.append(f"Smart Money Yönü: Piyasa yapısı Bearish (0 Puan)")
        else:
            s_ict = 40
            pros.append(f"⚖️ Smart Money Yönü: Nötr / Konsolidasyon ({format_pt(40 * w_ict)} Puan)")
    else:
        cons.append(f"ICT Verisi Yok (0 Puan)")

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

    return int(final), pros, cons

# ==============================================================================
# BÖLÜM 17 — ARZ/TALEP BÖLGELERİ TESPİTİ (SUPPLY/DEMAND ZONES)
# Kurumsal işlem izlerinin bıraktığı fiyat bölgelerini tespit eder.
# ICT metodolojisi temel alınmıştır.
# ==============================================================================
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
    
# ==============================================================================
# BÖLÜM 18 — ICT SETUP TARAMASI
# Order Block, FVG, Displacement ve yapısal kırılım kriterlerini birleştiren ICT batch tarama sistemi.
# ==============================================================================
def process_single_ict_setup(symbol, df):
    """
    ICT Sniper — BIST Günlük Uyarlaması
    Klasik ICT'nin BIST günlük verisinde çalışan özü:
      1. SMA50 üstünde (kısa vade trend sağlam)
      2. Son 30 barda swing low sweep + recovery (stop avı + dönüş)
      3. Sweep sonrası kapanış swept seviyenin üstünde
      4. Son 10 günde pozitif momentum (close bugün > close 10 gün önce)
      5. RRR ≥ 1.5 (yakın direnç hedef)
    """
    try:
        if df.empty or len(df) < 60: return None

        close = df['Close']; high = df['High']; low = df['Low']
        current_price = float(close.iloc[-1])
        n = len(df)

        # ── 1. TREND FİLTRESİ: SMA50 üstünde olmalı ──────────────────────────
        sma50 = float(close.rolling(50).mean().iloc[-1])
        if current_price <= sma50: return None

        # ── 2. MOMENTUM: Son 10 günde yükseliş ────────────────────────────────
        if float(close.iloc[-1]) <= float(close.iloc[-6]): return None

        # ── 3. HACİM HAZIRLIĞI: 20 günlük ortalama hacim ─────────────────────
        has_vol = 'Volume' in df.columns and not df['Volume'].isnull().all()
        vol_arr = df['Volume'].values if has_vol else None
        avg_vol_arr = df['Volume'].rolling(20).mean().values if has_vol else None

        # ── 4. REFERANS DIPLARI: Pencere [-90:-30] arası swing low'lar ────────
        #    (sweep taramasının dışında kalan "eski dip" seviyeleri)
        ref_lows = []
        lookback = 5  # her yanda 5 bar
        ref_start = max(lookback, n - 90)
        ref_end   = max(lookback, n - 30)
        low_arr   = low.values
        high_arr  = high.values
        for i in range(ref_start, ref_end):
            lo = float(low_arr[i])
            window = low_arr[max(0, i - lookback): i + lookback + 1]
            if lo <= float(window.min()) + 1e-9:
                ref_lows.append((i, lo))

        if not ref_lows: return None

        # ── 5. SWEEP + RECOVERY + HACİM TEYİDİ: Son 30 barda ────────────────
        #    a) Herhangi bir ref_low'un altına inilmiş (sweep)
        #    b) Sweep gününde hacim 20g ort. × 1.5 üstünde (kurumsal baskı)
        #    c) O gün veya sonrasında kapanış swept seviyenin ÜSTÜNDE (recovery)
        sweep_found   = False
        sweep_ref_low = None
        sweep_bar_idx = None

        for ref_i, ref_lo in ref_lows:
            sweep_window_start = n - 30
            for j in range(sweep_window_start, n):
                if float(low_arr[j]) < ref_lo:           # sweep gerçekleşti
                    # Hacim teyidi: sweep gününde yeterli hacim var mı?
                    if has_vol and avg_vol_arr is not None:
                        avg_v = float(avg_vol_arr[j]) if not np.isnan(avg_vol_arr[j]) else 0
                        sweep_vol = float(vol_arr[j])
                        if avg_v > 0 and sweep_vol < avg_v * 2.0:
                            continue                       # Hacimsiz sweep → geçersiz
                    # Recovery: j veya sonraki barlarda kapanış > ref_lo
                    for k in range(j, min(j + 6, n)):
                        if float(close.iloc[k]) > ref_lo:
                            sweep_found   = True
                            sweep_ref_low = ref_lo
                            sweep_bar_idx = k
                            break
                if sweep_found: break
            if sweep_found: break

        if not sweep_found: return None

        # ── 5. STOP / HEDEF / RRR ─────────────────────────────────────────────
        # ATR tabanlı dinamik stop (sabit % yerine volatiliteye göre)
        atr_period = 14
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs()
        ], axis=1).max(axis=1)
        atr14 = float(tr.rolling(atr_period).mean().iloc[-1])

        # Stop: sweep seviyesinin altı — en az 1.0×ATR, en fazla 2.0×ATR mesafe
        atr_buffer  = max(atr14 * 1.0, min(atr14 * 2.0, sweep_ref_low * 0.02))
        stop_loss   = sweep_ref_low - atr_buffer
        entry_price = current_price
        risk        = entry_price - stop_loss
        if risk <= 0: return None

        # Hedef: mevcut fiyatın üzerindeki en yakın swing high (son 90 bar)
        sw_highs = []
        for i in range(max(lookback, n - 90), n - lookback):
            hi = float(high_arr[i])
            window = high_arr[max(0, i - lookback): i + lookback + 1]
            if hi >= float(window.max()) - 1e-9:
                sw_highs.append(hi)

        targets = [h for h in sw_highs if h > entry_price * 1.01]
        if not targets: return None
        target_price = min(targets)                  # En yakın direnç

        rrr = (target_price - entry_price) / risk
        if rrr < 2.0: return None

        # ── 6. SKOR & AÇIKLAMA ───────────────────────────────────────────────
        skor = 75
        if rrr >= 2.5: skor += 15
        elif rrr >= 2.0: skor += 10
        elif rrr >= 1.5: skor += 5
        skor = min(100, skor)

        days_since_sweep = n - 1 - sweep_bar_idx
        aciklama = (
            f"Stop avı yapıldı ({days_since_sweep} gün önce), fiyat toparlandı ve "
            f"SMA50 üstünde momentum devam ediyor — giriş bölgesinde kurulum hazır"
        )

        return {
            "Sembol":    symbol,
            "Fiyat":     current_price,
            "Yön":       "LONG",
            "İkon":      "🎯",
            "Renk":      "#16a34a",
            "Durum":     f"RRR: {rrr:.1f} | Stop: {stop_loss:.2f} | Hedef: {target_price:.2f}",
            "Aciklama":  aciklama,
            "Stop_Loss": f"{stop_loss:.2f}",
            "Skor":      skor
        }

    except Exception:
        return None


@st.cache_data(ttl=900)
def scan_ict_batch(asset_list):
    """
    ICT Toplu Tarama Ajanı (Paralel Çalışır)
    """
    # 1. Veri Çek (Cache'den)
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty: return pd.DataFrame()
    
    results = []
    stock_dfs = []
    
    # Veriyi hisselere ayır
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]:
                    stock_dfs.append((symbol, data[symbol]))
            else:
                if len(asset_list) == 1: stock_dfs.append((symbol, data))
        except: continue

    # 2. Paralel İşleme (Dedektörü Çalıştır)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_single_ict_setup, sym, df) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)
            
    # 3. Sonuç Döndür
    if results:
        return pd.DataFrame(results)
    
    return pd.DataFrame()

# ==============================================================================
# BÖLÜM 19 — ROYAL FLUSH NADİR FIRSAT TARAMASI
# En katı çok-kriterli filtre. Tüm sistemlerin üst üste çakıştığı
# "Royal Flush" seviyesindeki setup'ları arar.
# ==============================================================================
def _nadir_firsat_single_fast(symbol, df):
    """
    Tek sembol için Royal Flush kontrolü — batch DataFrame'den inline hesaplar.
    calculate_ict_deep_analysis / calculate_price_action_dna çağırmaz → hızlı.
    5/5 Kriter:
      1. BOS : son lokal swing high kırıldı (bullish yapı)
      2. RS  : son 20 günlük getiri > %1.5
      3. VWAP sapması < %10
      4. Hacim canlanması (3-gün veya 2-gün patlaması)
      5. RSI < 65
    """
    try:
        if df is None or df.empty or len(df) < 60:
            return None

        close = df['Close']; high = df['High']
        low   = df['Low'];   vol  = df['Volume']
        curr  = float(close.iloc[-1])

        # ── 1. BOS: son swing high'ı kır ──
        sh = None
        for i in range(len(close) - 3, max(len(close) - 61, 2), -1):
            if (high.iloc[i] > high.iloc[i-1] and high.iloc[i] > high.iloc[i-2]
                    and high.iloc[i] > high.iloc[i+1] and high.iloc[i] > high.iloc[i+2]):
                sh = float(high.iloc[i])
                break
        if sh is None or curr <= sh:
            return None

        # ── 2. RS proxy: son 20 günlük getiri > %1.5 ──
        if len(close) < 21:
            return None
        ret20 = (close.iloc[-1] / close.iloc[-21] - 1) * 100
        if ret20 <= 1.5:
            return None

        # ── 3. VWAP sapması < %10 ──
        typical  = (high + low + close) / 3
        vwap_val = float(
            (typical * vol).rolling(20).sum().iloc[-1] /
            vol.rolling(20).sum().iloc[-1]
        ) if vol.rolling(20).sum().iloc[-1] > 0 else 0
        if vwap_val <= 0:
            return None
        vwap_diff = (curr - vwap_val) / vwap_val * 100
        if vwap_diff >= 10:
            return None

        # ── 4. Hacim canlanması ──
        if len(vol) < 22:
            return None
        ort20    = vol.iloc[-22:-2].mean()
        son3_ort = vol.iloc[-3:].mean()
        son2_ort = vol.iloc[-2:].mean()
        onc5_ort = vol.iloc[-7:-2].mean()
        if not ((son3_ort > ort20 * 1.3) or (son2_ort > onc5_ort * 1.3)):
            return None

        # ── 5. RSI < 65 ──
        dd  = close.diff()
        gg  = dd.where(dd > 0, 0).rolling(14).mean()
        ll  = (-dd.where(dd < 0, 0)).rolling(14).mean()
        rsi = float((100 - (100 / (1 + gg / ll))).iloc[-1])
        if rsi >= 65:
            return None

        return {
            'Sembol':   symbol,
            'Fiyat':    round(curr, 2),
            'Durum':    '5/5 | BOS+RS+VWAP+VOL+RSI',
            'Aciklama': (
                f"BOS yapı kırılımı teyitli · "
                f"RS +{ret20:.1f}% (endeks üstü güç) · "
                f"VWAP sapması %{vwap_diff:.1f} (şişmemiş) · "
                f"RSI {rsi:.0f} (aşırı alım yok) · "
                f"Hacim canlandı"
            ),
        }
    except Exception:
        return None


def scan_nadir_firsat_batch(asset_list):
    """
    Royal Flush Nadir Fırsat — batch veri + paralel (ThreadPoolExecutor).
    Eski sürüm: 500 sembol × ~2sn sıralı = ~17 dakika.
    Yeni sürüm: batch indir + 8 paralel thread = ~30 saniye.
    """
    # Adım 1: Batch veriyi al (zaten master scan'de indirildi, cache'den gelir)
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty:
        return pd.DataFrame()

    # Adım 2: Her sembol için DataFrame'i ayır
    stock_dfs = []
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]:
                    df_sym = data[symbol].dropna(how='all')
                    if not df_sym.empty:
                        stock_dfs.append((symbol, df_sym))
            else:
                if len(asset_list) == 1:
                    stock_dfs.append((symbol, data.dropna(how='all')))
        except Exception:
            continue

    # Adım 3: Paralel işle (8 thread, sembol başı 5sn timeout)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_nadir_firsat_single_fast, sym, df): sym
            for sym, df in stock_dfs
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result(timeout=5)
                if res:
                    results.append(res)
            except Exception:
                continue

    if not results:
        return pd.DataFrame()
    df_nadir = pd.DataFrame(results).sort_values('Sembol').reset_index(drop=True)
    log_scan_signal("nadir_firsat", df_nadir, category=st.session_state.get('category', ''))
    return df_nadir

# ==============================================================================
# BÖLÜM 20 — MİNERVİNİ SEPA METODU VE RS MOMENTUM LİDERLERİ
# Mark Minervini'nin SEPA kriterlerini uygulayan tarama.
# Rölatif güç lideri hisseleri ayrıca listelenir.
# ==============================================================================
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


# *****************************************************************
# 👑 SMART MONEY RADAR: TOP 20 MASTER LİSTESİ (ARKETİP MODEL V2)
# *****************************************************************
import streamlit as st

def fetch_technical_engine_data(ticker, sources_list):
    """
    YENİ HİYERARŞİK TEKNİK MOTOR V5: "10 Gerçek Piyasa Senaryosu" Eklendi
    Hisse özel kombinasyonlar yakaladığında jenerik mesaj yerine stratejik tanım gösterir.
    """
    # 1. ANA MODELLER (TABAN PUAN - BASE SCORES)
    base_powers = {
        '🔄 Güçlü Dönüş Adayları': 85,
        '🦅 ICT Sniper': 85,
        '🦁 Minervini': 80,
        '💎 Platin Fırsat (Klasik)': 80,
        '🔨 Breakout Yapan': 70,
        '🏆 RS Lideri': 60
    }

    # 2. DESTEKLEYİCİ MODELLER (BONUS PUANLAR)
    bonus_powers = {
        '🏆 RS Lideri': 15,
        '🤫 Sentiment (Akıllı Para)': 15,
        '📈 RSI Pozitif Uyumsuzluk': 10,
        '🔨 Breakout Yapan': 10,
        '💎 Altın Fırsat': 12,
        '📊 VIP Formasyon': 10,
        '🔥 Isınan (STP)': 8,
        '📡 1-5 Günlük Yükseliş': 5,
        '⭐ Yıldız Adayı': 5
    }

    # 3. TABAN PUANI BELİRLE
    max_base_score = 0
    primary_model = ""
    
    for src in sources_list:
        if src in base_powers and base_powers[src] > max_base_score:
            max_base_score = base_powers[src]
            primary_model = src

    current_score = max_base_score if max_base_score > 0 else 20

    # 4. ÇOKLU ANA MODEL BONUSU VE YARDIMCI SİNYALLERİ EKLE
    for src in sources_list:
        if src == primary_model:
            continue
            
        if src in base_powers:
            current_score += 10  # Diğer her bir dev model için +10 Puan
        elif src in bonus_powers:
            current_score += bonus_powers[src]

    # 5. KORUMA SİSTEMİ (KILL-SWITCH)
    if '🦁 Minervini' in sources_list and '🤫 Sentiment (Akıllı Para)' not in sources_list:
        current_score -= 15 # Hacim yoksa trend sahtedir, cezalandır.

    total_score = min(100, int(current_score))
    
    # İkonları ayıkla
    all_icons = ['🩸', '🦅', '🦁', '🏆', '📈', '🤫', '🔨', '📡', '♠️', '⭐']
    icons = [src.split(' ')[0] for src in sources_list if any(src.startswith(i) for i in all_icons)]
    icon_str = " ".join(icons)

    # 👇 İŞTE EKSİK OLAN O SATIR (BUNU EKLE) 👇
    confluence_count = len(sources_list)

    # ====================================================================
    # 6. ÖZEL SENARYO DEDEKTÖRÜ (AĞIRLIKLI PUANLAMA MİMARİSİ)
    # Sistem tüm senaryoları okur ve hisse için en güçlü olanı seçer.
    # ====================================================================
    
    # Sinyallerin varlık kontrolü
    has_dip = '🔄 Güçlü Dönüş Adayları' in sources_list
    has_ict = '🦅 ICT Sniper' in sources_list
    has_min = '🦁 Minervini' in sources_list
    has_rfc = '💎 Platin Fırsat (Klasik)' in sources_list
    has_break = '🔨 Breakout Yapan' in sources_list
    has_rs = '🏆 RS Lideri' in sources_list
    has_sent = '🤫 Sentiment (Akıllı Para)' in sources_list
    has_div = '📈 RSI Pozitif Uyumsuzluk' in sources_list
    has_1_5 = '📡 1-5 Günlük Yükseliş' in sources_list
    has_star = '⭐ Yıldız Adayı' in sources_list

    # Hissenin uygun olduğu tüm senaryoları bu sepete atacağız
    gecerli_senaryolar = []

    # 1. ZEHİRLİ KIRILIM (Acil Durum Kalkanı - Ağırlık: 999)
    # Sadece tamamen desteksiz, sığ ve trendi olmayan sahte kırılımları avlar.
    if has_break and not (has_sent or has_rs or has_min or has_rfc):
        gecerli_senaryolar.append((999, "☠️ Zehirli Kırılım (Boğa Tuzağı): Direnç kırıldı ancak arkasında hiçbir trend, hacim veya RS gücü yok! Sahte kırılım (Fakeout) riski çok yüksek."))

    # 2. BÜYÜME PATLAMASI (En Güçlü Alım Fırsatı - Ağırlık: 90)
    if (has_min or has_rfc) and has_break and (has_sent or has_rs):
        gecerli_senaryolar.append((90, "🌪️ Büyüme Patlaması: Kusursuz ralli! Fiyat daralmayı tamamladı, arz kurudu ve kurumsal hacim/güç onayıyla direnci paramparça etti."))

    # 3. KURUMSAL LİKİDİTE AVI (Dipten Dönüş - Ağırlık: 85)
    if (has_ict or has_dip) and (has_div or has_sent):
        gecerli_senaryolar.append((85, "🪤 Kurumsal Likidite Avı: Küçük yatırımcının stopları patlatıldı (Sweep). Akıllı para bu paniği fırsat bilip dipten malı topladı, V-Dönüşü tetikleniyor."))

    # 4. TREND İÇİ İSKONTO (Güvenli Katılım - Ağırlık: 80)
    if (has_rfc or has_min) and (has_ict or has_dip or has_div):
        gecerli_senaryolar.append((80, "🌊 Trend İçi İskonto: Güçlü ana trendde, kurumsal maliyetlenme bölgesine (OTE/FVG) harika bir düzeltme (Pullback) yaşandı. Güvenli katılım noktası."))

    # 5. SESSİZ FIRTINA (Kırılım Öncesi Pusu - Ağırlık: 75)
    if has_star and has_sent and not has_break:
        gecerli_senaryolar.append((75, "🤫 Sessiz Fırtına: Ekranda yaprak kıpırdamıyor gibi görünse de arka planda sinsi ve güçlü bir mal toplama (Akümülasyon) evresindeyiz. Patlama yakın."))

    # 6. GÜVENLİ LİMAN (Piyasa Kötüyken Ayakta Kalanlar - Ağırlık: 70)
    if has_rs and (has_rfc or has_min):
        gecerli_senaryolar.append((70, "🛡️ Güvenli Liman: Piyasa kan ağlarken veya yatayken bu hisse endeksi eziyor (Alpha Pozitif). Fonların parayı park ettiği gerçek piyasa lideri."))


    # --- EN GÜÇLÜ SENARYOYU SEÇME MOTORU ---
    scenario_msg = ""
    if gecerli_senaryolar:
        # Sepetteki senaryoları sahip oldukları ağırlık (Puan) numarasına göre büyükten küçüğe sırala
        gecerli_senaryolar.sort(key=lambda x: x[0], reverse=True)
        # En yüksek puanlı olanın (listenin 0. indeksinin) metnini (1. indeksini) al
        scenario_msg = gecerli_senaryolar[0][1]

    # Eğer hisse hiçbir spesifik senaryoya uymuyorsa, toplam puana göre genel durum ver:
    if not scenario_msg:
        if total_score >= 90:
            scenario_msg = f"💎 NADİR KONFLUANS: {primary_model.split(' ')[1] if primary_model else 'Ana'} modeli ve {confluence_count-1} adet destekleyici teyit!"
        elif total_score >= 75:
            scenario_msg = f"🔥 GÜÇLÜ KURULUM: Kurumsal ayak izleri net. Strateji kurmak için güvenli bölge."
        elif total_score >= 60:
            scenario_msg = f"⚡ İYİ İVME: Destekleyici sinyaller toplanıyor. Yakından izlemeye değer."
        else:
            scenario_msg = "⚠️ RİSKLİ BÖLGE: Net bir kurumsal onay veya ana model desteği yok."

    return total_score, scenario_msg, icon_str

def compile_top_20_summary():
    """
    12 tarama kaynağından gelen verileri toplar ve hiyerarşik modelle skorlar.
    """
    candidates = {} 
    
    def add_candidates(df, source_name, limit=5):
        if df is not None and not df.empty:
            for i, row in df.head(limit).iterrows():
                sym = row.get('Sembol', row.get('Sembol_Raw', row.get('Hisse', None)))
                if not sym: continue
                fiyat = row.get('Fiyat', row.get('Güncel_Fiyat', 0))
                if sym not in candidates:
                    candidates[sym] = {'sources': [], 'price': fiyat}
                if source_name not in candidates[sym]['sources']:
                    candidates[sym]['sources'].append(source_name)

    # 1. HAVUZU OLUŞTUR
    # Yüksek hassasiyetli scanner'lar — limit 5 (az ama kaliteli sinyal)
    add_candidates(st.session_state.get('guclu_donus_data'), '🔄 Güçlü Dönüş Adayları', limit=5)
    add_candidates(st.session_state.get('prelaunch_bos_data'), '🚀 Pre-Launch BOS', limit=5)
    add_candidates(st.session_state.get('platin_results'), '💎 Platin Fırsat (Klasik)', limit=5)
    add_candidates(st.session_state.get('nadir_firsat_scan_data'), '♠️ Royal Flush Nadir Fırsat', limit=5)
    add_candidates(st.session_state.get('ict_scan_data'), '🦅 ICT Sniper', limit=5)
    add_candidates(st.session_state.get('minervini_data'), '🦁 Minervini', limit=5)
    # Geniş tarama yapan scanner'lar — limit 10 (daha fazla aday)
    add_candidates(st.session_state.get('radar2_data'), '⭐ Yıldız Adayı', limit=10)
    add_candidates(st.session_state.get('scan_data'), '📡 1-5 Günlük Yükseliş', limit=10)
    add_candidates(st.session_state.get('harmonic_confluence_data'), '⚡ Harmonik Confluence (3\'lü Teyit)', limit=5)
    add_candidates(st.session_state.get('golden_results'), '🏆 Altın Fırsat', limit=10)
    # golden_pattern_data dict formatı: {"formations": df, "hazirlik": df}
    _gp_top20 = st.session_state.get('golden_pattern_data')
    if isinstance(_gp_top20, dict):
        add_candidates(_gp_top20.get('formations', pd.DataFrame()), '💎 VIP Formasyon', limit=5)

    candidate_list = [{'Sembol': k, **v} for k, v in candidates.items()]
    
    # 2. TEKNİK SINAV (HİYERARŞİK SKORLAMA)
    for item in candidate_list:
        tot_score, msg, icons = fetch_technical_engine_data(item['Sembol'], item['sources'])
        item['score'] = tot_score
        item['katalizor'] = f"{icons} | {msg}"
        item['onay_sayisi'] = len(item['sources'])
        
    # 3. SIRALAMA
    candidate_list.sort(key=lambda x: (x['score'], x['onay_sayisi']), reverse=True)

    return candidate_list[:20]


def compile_confluence_hits():
    """
    Cross-Scanner Confluence Motoru.
    3 bağımsız metodoloji grubunda her birinde kaç grupta çıktığını sayar.
      Grup 1 — Yapısal : ICT, Royal Flush Nadir Fırsat
      Grup 2 — Momentum: Minervini, RS Leaders, Radar1/2
      Grup 3 — Formasyon/Değer: Altın Fırsat, VIP Formasyon, Gizli Birikim, Confirmed Breakout
    Sadece 2/3 veya 3/3 gruba giren hisseler döner.
    """
    groups = {
        'yapi':      {'label': '🏗️ Yapısal',   'scanned': False, 'sources': {}},
        'momentum':  {'label': '📈 Momentum',   'scanned': False, 'sources': {}},
        'formasyon': {'label': '💎 Formasyon',  'scanned': False, 'sources': {}},
    }

    def add_to_group(g_key, df, source_name, limit=10):
        if df is None or (hasattr(df, 'empty') and df.empty): return
        groups[g_key]['scanned'] = True
        for _, row in df.head(limit).iterrows():
            sym = row.get('Sembol') or row.get('Sembol_Raw') or row.get('Hisse')
            if not sym: continue
            price = row.get('Fiyat') or row.get('Güncel_Fiyat') or 0
            if sym not in groups[g_key]['sources']:
                groups[g_key]['sources'][sym] = {'price': float(price) if price else 0, 'scanners': []}
            if source_name not in groups[g_key]['sources'][sym]['scanners']:
                groups[g_key]['sources'][sym]['scanners'].append(source_name)

    # --- GRUP 1: YAPISAL ---
    add_to_group('yapi', st.session_state.get('ict_scan_data'),          'ICT Sniper')
    add_to_group('yapi', st.session_state.get('nadir_firsat_scan_data'), 'Royal Flush Nadir Fırsat')
    add_to_group('yapi', st.session_state.get('platin_results'),         'Platin Fırsat')
    add_to_group('yapi', st.session_state.get('guclu_donus_data'),       'Güçlü Dönüş')

    # --- GRUP 2: MOMENTUM ---
    add_to_group('momentum', st.session_state.get('minervini_data'),     'Minervini')
    add_to_group('momentum', st.session_state.get('prelaunch_bos_data'),'Pre-Launch BOS')
    add_to_group('momentum', st.session_state.get('radar2_data'),        'Radar2')
    add_to_group('momentum', st.session_state.get('scan_data'),          'Radar1')

    # --- GRUP 3: FORMASYON/DEĞER ---
    add_to_group('formasyon', st.session_state.get('accum_data'),     'Gizli Birikim')
    add_to_group('formasyon', st.session_state.get('golden_results'), 'Altın Fırsat')
    _gp_conf = st.session_state.get('golden_pattern_data')
    if isinstance(_gp_conf, dict):
        add_to_group('formasyon', _gp_conf.get('formations', pd.DataFrame()), 'VIP Formasyon')
    # Harmonik Confluence = 3 bağımsız metodoloji (Harmonik + ICT + RSI) → her 3 gruba ekle
    _hconf_df = st.session_state.get('harmonic_confluence_data')
    if _hconf_df is not None and not (hasattr(_hconf_df, 'empty') and _hconf_df.empty):
        add_to_group('yapi',      _hconf_df, 'Harmonik Confluence')
        add_to_group('momentum',  _hconf_df, 'Harmonik Confluence')
        add_to_group('formasyon', _hconf_df, 'Harmonik Confluence')

    # --- KAÇ GRUP TARANMIŞ ---
    scanned_groups = [k for k, v in groups.items() if v['scanned']]

    # --- CONFLUENCE HESABI ---
    all_syms = set()
    for g in groups.values():
        all_syms.update(g['sources'].keys())

    results = []
    for sym in all_syms:
        hit_groups = []
        missing_groups = []
        total_scanners = 0
        price = 0
        for g_key, g_data in groups.items():
            if sym in g_data['sources']:
                scanners = g_data['sources'][sym]['scanners']
                hit_groups.append({'key': g_key, 'label': g_data['label'], 'scanners': scanners})
                total_scanners += len(scanners)
                if price == 0:
                    price = g_data['sources'][sym]['price']
            elif g_data['scanned']:
                missing_groups.append(g_data['label'])

        group_count = len(hit_groups)
        if group_count >= 2:
            results.append({
                'Sembol':         sym,
                'group_count':    group_count,
                'total_scanners': total_scanners,
                'hit_groups':     hit_groups,
                'missing_groups': missing_groups,
                'scanned_groups': len(scanned_groups),
                'price':          price,
            })

    results.sort(key=lambda x: (x['group_count'], x['total_scanners']), reverse=True)
    return results


@st.cache_data(ttl=900)
def scan_minervini_batch(asset_list):
    # 1. Veri İndirme (Hızlı Batch)
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty: return pd.DataFrame()
    
    # 2. Endeks Belirleme
    cat = st.session_state.get('category', 'S&P 500')
    bench = "XU100.IS" if "BIST" in cat else "^GSPC"

    results = []
    stock_dfs = []
    
    # Veriyi hazırlama (Hisselere bölme)
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]:
                    stock_dfs.append((symbol, data[symbol]))
            elif len(asset_list) == 1:
                stock_dfs.append((symbol, data))
        except: continue

    # 3. Paralel Tarama (Yukarıdaki sertleştirilmiş fonksiyonu çağırır)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        # provided_df argümanını kullanarak internetten tekrar indirmeyi engelliyoruz
        futures = [executor.submit(calculate_minervini_sepa, sym, bench, df) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)
            
    # 4. Sıralama ve Kesme
    if results:
        df = pd.DataFrame(results)
        # En yüksek Puanlı ve en yüksek RS'li olanları üste al
        # Sadece ilk 30'u göster ki kullanıcı boğulmasın.
        df_min = df.sort_values(by=["Raw_Score", "rs_val"], ascending=[False, False]).head(30)
        log_scan_signal("minervini", df_min, category=st.session_state.get('category', ''))
        return df_min

    return pd.DataFrame()

@st.cache_data(ttl=900)
def scan_rs_momentum_leaders(asset_list):
    """
    GÜNCELLENMİŞ: RS MOMENTUM + BETA AYARLI ALPHA
    Hız Tuzağına Düşmeden, İşlemci Gücüyle Beta ve Sigma Hesabı Yapar.
    Profesyonel Fon Yöneticisi Mantığı: Beta Adjusted Alpha + Dynamic Sigma Safety Lock.
    """
    # 1. Verileri Çek (3 ay yeterli, Beta için ideal)
    data = get_batch_data_cached(asset_list, period="3mo")
    if data.empty: return pd.DataFrame()

    # 2. Endeks Verisi
    cat = st.session_state.get('category', 'S&P 500')
    bench_ticker = "XU100.IS" if "BIST" in cat else "^GSPC"
    df_bench = get_safe_historical_data(bench_ticker, period="3mo")
    
    if df_bench is None or df_bench.empty: return pd.DataFrame()
    
    # Endeks Performansları ve Getirileri (Beta hesabı için kritik)
    b_close = df_bench['Close']
    bench_returns = b_close.pct_change().dropna() 
    
    # Basit Kıyaslama (Eski yöntem - Referans ve ham hesap için)
    bench_5d = ((b_close.iloc[-1] - b_close.iloc[-6]) / b_close.iloc[-6]) * 100
    bench_1d = ((b_close.iloc[-1] - b_close.iloc[-2]) / b_close.iloc[-2]) * 100

    # Piyasa çöküş filtresi: Endeks günlük -%2'nin altındaysa tarama anlamsız —
    # çöken piyasada tüm hisseler yapay alpha gösterebilir.
    if bench_1d <= -2.0:
        return pd.DataFrame()

    results = []

    # 3. Hisseleri Tara
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol not in data.columns.levels[0]: continue
                df = data[symbol].dropna()
            else:
                df = data.dropna()

            # Beta ve Sigma hesabı için en az 60 bar veri lazım
            if len(df) < 60: continue 

            close = df['Close']; volume = df['Volume']
            stock_returns = close.pct_change().dropna()

            # --- A. YENİ NESİL BETA HESAPLAMASI (CPU Hızıyla) ---
            # Hissenin ve Endeksin zaman serilerini eşle (Alignment)
            aligned_stock = stock_returns.reindex(bench_returns.index).dropna()
            aligned_bench = bench_returns.reindex(aligned_stock.index).dropna()
            
            # Kovaryans / Varyans = Beta
            if len(aligned_bench) > 20: # Yeterli ortak gün varsa hesapla
                covariance = np.cov(aligned_stock, aligned_bench)[0][1]
                variance = np.var(aligned_bench)
                beta = covariance / variance if variance != 0 else 1.0
            else:
                beta = 1.0 # Veri yetmezse varsayılan
            
            # --- B. PERFORMANS HESAPLARI ---
            stock_now = float(close.iloc[-1])
            stock_old_5 = float(close.iloc[-6])
            
            # 5 Günlük Performans
            stock_perf_5d = ((stock_now - stock_old_5) / stock_old_5) * 100
            
            # Beta Ayarlı Alpha (Jensen's Alpha Mantığı)
            # Beklenen Getiri = Beta * Endeks Getirisi
            expected_return_5d = bench_5d * beta
            adjusted_alpha_5d = stock_perf_5d - expected_return_5d

            # --- C. DİNAMİK EMNİYET KİLİDİ (SIGMA) ---
            # Hissenin endekse göre "normal" sapmasını bul
            alpha_series = (stock_returns - bench_returns).dropna().tail(20)
            alpha_std = alpha_series.std() * 100 # Yüzde cinsinden standart sapma
            
            # Kilit Eşiği: Kendi oynaklığının 1.5 katı kadar negatif ayrışma
            safety_threshold = -(alpha_std * 1.5)
            
            # Bugünün durumu
            stock_perf_1d = ((stock_now - float(close.iloc[-2])) / float(close.iloc[-2])) * 100
            today_raw_alpha = stock_perf_1d - bench_1d

            # Hacim Kontrolü
            curr_vol = float(volume.iloc[-1])
            avg_vol = float(volume.iloc[-21:-1].mean())
            vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 0

            # --- FİLTRELEME (PROFESYONEL KRİTERLER) ---
            # 1. Beta Ayarlı Alpha > 1.25 (Gerçek Güç)
            # 2. Hacim > 0.9 (İlgi var)
            # 3. Bugün "Güvenli Eşik"ten daha fazla düşmemiş (Momentum Kırılmamış)
            if adjusted_alpha_5d >= 1.25 and vol_ratio > 0.9 and today_raw_alpha > safety_threshold:
                
                results.append({
                    "Sembol": symbol,
                    "Fiyat": stock_now,
                    "Beta": round(beta, 2), # Bilgi için ekranda görünebilir
                    "Alpha_5D": adjusted_alpha_5d,     # İsmi Alpha_5D olarak düzelttik
                    "Adj_Alpha_5D": adjusted_alpha_5d, # Sıralama kriteri
                    "Ham_Alpha_5D": stock_perf_5d - bench_5d, # Eski usül (referans)
                    "Eşik": round(safety_threshold, 2),
                    "Hacim_Kat": vol_ratio,
                    "Skor": adjusted_alpha_5d # Skor artık "Gerçek Alpha"
                })

        except Exception as e: continue

    # 4. Sıralama
    if results:
        # Skora göre azalan sırala
        df_rs = pd.DataFrame(results).sort_values(by="Skor", ascending=False)
        log_scan_signal("rs_leaders", df_rs, category=st.session_state.get('category', ''))
        return df_rs

    return pd.DataFrame()

# ==============================================================================
# BÖLÜM 21 — SENTİMENT SKOR SİSTEMİ
# RSI, hacim, momentum ve benchmark karşılaştırmasından
# bileşik bir duygu skoru üretir.
# ==============================================================================
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
        # 5. HACİM KALİTESİ (Chaikin Money Flow mantığı)
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
        
def get_deep_xray_data(ticker):
    sent = calculate_sentiment_score(ticker)
    if not sent: return None
    def icon(cond): return "✅" if cond else "❌"
    return {
        "mom_rsi": f"{icon(sent['raw_rsi']>50)} RSI Trendi",
        "mom_macd": f"{icon(sent['raw_macd']>0)} MACD Hist",
        "vol_obv": f"{icon('OBV ↑' in sent['vol'])} OBV Akışı",
        "tr_ema": f"{icon('GoldCross' in sent['tr'])} EMA Dizilimi",
        "tr_adx": f"{icon('P > SMA50' in sent['tr'])} Trend Gücü",
        "vola_bb": f"{icon('BB Break' in sent['vola'])} BB Sıkışması",
        "str_bos": f"{icon('BOS ↑' in sent['str'])} Yapı Kırılımı"
    }

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

# ====================================================================
# NİHAİ PRICE ACTION (PA) + KLASİK FORMASYONLAR + FIBONACCI + CONFLUENCE RADARI
# ====================================================================
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

@st.cache_data(ttl=600)
def calculate_ict_deep_analysis(ticker):
    error_ret = {"status": "Error", "msg": "Veri Yok", "structure": "-", "bias": "-", "entry": 0, "target": 0, "structural_target": 0, "stop": 0, "rr": 0, "desc": "Veri bekleniyor", "displacement": "-", "fvg_txt": "-", "ob_txt": "-", "zone": "-", "mean_threshold": 0, "curr_price": 0, "setup_type": "BEKLE", "bottom_line": "-", "eqh_eql_txt": "-", "sweep_txt": "-", "model_score": 0, "model_checks": [], "ob_age": 0, "fvg_age": 0, "struct_age": 0}
    
    try:
        df = get_safe_historical_data(ticker, period="1y")
        if df is None or len(df) < 60: return error_ret
        
        high = df['High']; low = df['Low']; close = df['Close']; open_ = df['Open']
        
        tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        avg_body_size = abs(open_ - close).rolling(20).mean()

        sw_highs = []; sw_lows = []
        for i in range(2, len(df)-2):
            try:
                if high.iloc[i] >= max(high.iloc[i-2:i]) and high.iloc[i] >= max(high.iloc[i+1:i+3]):
                    sw_highs.append((df.index[i], high.iloc[i], i))
                if low.iloc[i] <= min(low.iloc[i-2:i]) and low.iloc[i] <= min(low.iloc[i+1:i+3]):
                    sw_lows.append((df.index[i], low.iloc[i], i))
            except: continue

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

        last_candle_body = abs(open_.iloc[-1] - close.iloc[-1])
        avg_vol_20 = df['Volume'].rolling(20).mean().iloc[-1]
        vol_confirmed = float(df['Volume'].iloc[-1]) > avg_vol_20 * 1.2
        if last_candle_body > avg_body_size.iloc[-1] * 1.1 and vol_confirmed:
            displacement_txt = "🔥 Güçlü Displacement (Hacim Onaylı)"
        elif last_candle_body > avg_body_size.iloc[-1] * 1.1:
            displacement_txt = "⚠️ Hacimsiz Hareket (Sahte Olabilir)"
        
        breakout_margin_up   = (curr_price - last_sh) / last_sh if last_sh > 0 else 0
        breakout_margin_down = (last_sl - curr_price) / last_sl if last_sl > 0 else 0

        if curr_price > last_sh:
            if is_prev_bearish:
                structure = f"MSS (Trend Döndü) 🐂 | {dow_desc}"
            elif breakout_margin_up < 0.005:
                structure = f"⚠️ Zayıf Kırılım — Onay Bekleniyor 🐂 | {dow_desc}"
            else:
                structure = f"BOS (Yükseliş Kırılımı) 🐂 | {dow_desc}"
            bias = "bullish"
        elif curr_price < last_sl:
            if is_prev_bullish:
                structure = f"MSS (Trend Döndü) 🐻 | {dow_desc}"
            elif breakout_margin_down < 0.005:
                structure = f"⚠️ Zayıf Kırılım — Onay Bekleniyor 🐻 | {dow_desc}"
            else:
                structure = f"BOS (Düşüş Kırılımı) 🐻 | {dow_desc}"
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
            if close.iloc[-1] > open_.iloc[-1]: bias = "bullish_retrace"
            else: bias = "bearish_retrace"

        # --- YAPISAL HEDEF (swing tabanlı, orta vade) ---
        next_bsl = min([h[1] for h in sw_highs if h[1] > curr_price], default=float(high.max()))
        next_ssl = max([l[1] for l in sw_lows  if l[1] < curr_price], default=float(low.min()))
        structural_target = next_bsl if "bullish" in bias else next_ssl

        # --- 👇 YENİ: MIKNATIS (DOL) HESAPLAMA MANTIĞI 👇 ---
        # Fiyatın gitmek isteyeceği en yakın Likidite havuzlarını buluyoruz
        next_bsl = min([h[1] for h in sw_highs if h[1] > curr_price], default=high.max())
        next_ssl = max([l[1] for l in sw_lows if l[1] < curr_price], default=low.min())
        # Eğer bir setup yoksa, sistemin "Nereye bakacağını" belirleyen DOL (Draw on Liquidity)
        # Ayı piyasasında mıknatıs aşağıdaki DİP, Boğa piyasasında yukarıdaki TEPE'dir.
        magnet_target = next_bsl if "bullish" in bias else next_ssl
        # --- 👆 ---------------------------------------- 👆 ---
        # --- 👇 YENİ: LİKİDİTE HAVUZLARI (EQH / EQL) VE LİKİDİTE AVI (SWEEP) 👇 ---
        eqh_eql_txt = "Yok"
        sweep_txt = "Yok"
        
        tol = curr_price * 0.003 # Eşitlik için %0.3 tolerans payı
        
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
        # Fiyat son 3 mumda son tepenin/dibin dışına çıkıp (iğne atıp), ters yönde kapattıysa
        recent_lows = low.iloc[-3:]
        recent_highs = high.iloc[-3:]
        
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

        active_ob_txt = "Yok"
        mean_threshold = 0.0
        lookback = 20
        start_idx = max(0, len(df) - lookback)
        ob_bar_idx  = -1   # OB'un oluştuğu bar (yaş hesabı için)
        fvg_bar_idx = -1   # FVG'nin açıldığı bar (yaş hesabı için)

        # OB kalite değerlendirmesi için hacim ortalaması
        avg_vol_20 = df['Volume'].rolling(20).mean()

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
                active_fvg_txt = f"Açık FVG var (Destek): {f['bot']:.2f} - {f['top']:.2f}"
                fvg_bar_idx = f['idx']
                _fvg_l = f['bot']; _fvg_h = f['top']
            lowest_idx = df['Low'].iloc[start_idx:].idxmin()
            if isinstance(lowest_idx, pd.Timestamp): lowest_idx = df.index.get_loc(lowest_idx)
            for i in range(lowest_idx, max(0, lowest_idx-5), -1):
                if df['Close'].iloc[i] < df['Open'].iloc[i]:
                    ob_low = df['Low'].iloc[i]; ob_high = df['High'].iloc[i]
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
                active_fvg_txt = f"Açık FVG var (Direnç): {f['bot']:.2f} - {f['top']:.2f}"
                fvg_bar_idx = f['idx']
                _fvg_l = f['bot']; _fvg_h = f['top']
            highest_idx = df['High'].iloc[start_idx:].idxmax()
            if isinstance(highest_idx, pd.Timestamp): highest_idx = df.index.get_loc(highest_idx)
            for i in range(highest_idx, max(0, highest_idx-5), -1):
                if df['Close'].iloc[i] > df['Open'].iloc[i]:
                    ob_low = df['Low'].iloc[i]; ob_high = df['High'].iloc[i]
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
        range_loc = (curr_price - range_low) / (range_high - range_low)
        zone = "PREMIUM (Pahalı)" if range_loc > 0.5 else "DISCOUNT (Ucuz)"

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

        elif bias in ["bearish", "bearish_retrace"] and zone == "PREMIUM (Pahalı)":
            valid_fvgs = [f for f in bearish_fvgs if f['bot'] > curr_price]
            if valid_fvgs and next_ssl < curr_price:
                best_fvg = valid_fvgs[-1]; temp_entry = best_fvg['bot']
                if next_ssl < temp_entry:
                    entry_price = temp_entry; take_profit = next_ssl
                    stop_loss = last_sh if last_sh > entry_price else best_fvg['top'] + atr * 0.5
                    final_target = take_profit # Setup varsa hedef kâr al seviyesidir
                    setup_type = "SHORT"; setup_desc = "Fiyat pahalılık bölgesinde. Direnç bloğundan likidite (SSL) hedefleniyor."

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
            final_target = float(nearest_ssl.iloc[0]) if len(nearest_ssl) > 0 else curr_price * (1 - MIN_NEAR * 2)
            # Asıl hedef: yapısal SSL — yakın hedeften en az %1.5 daha aşağıda
            _far_ssl = [v for v in struct_ssl_list if v < final_target * (1 - MIN_FAR)]
            derin_hedef = _far_ssl[0] if _far_ssl else final_target * (1 - MIN_FAR)
            ileri_hedef = curr_price * 1.02
            safety_lvl  = float(nearest_bsl.iloc[0]) if len(nearest_bsl) > 0 else curr_price * (1 + MIN_NEAR)
        else:
            # Yakın hedef: son 20 mumun en yakın BSL'i (min %0.8 yukarıda)
            final_target = float(nearest_bsl.iloc[0]) if len(nearest_bsl) > 0 else curr_price * (1 + MIN_NEAR * 2)
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

        if is_bullish and not is_premium:
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

        bottom_line = random.choice(lines)
        
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
        
@st.cache_data(ttl=600)
def calculate_price_action_dna(ticker):
    try:
        df = get_safe_historical_data(ticker, period="6mo") 
        if df is None or len(df) < 50: return None
        # --- YENİ HACİM HESAPLAMALARI (ADIM 2) BURAYA EKLENDİ ---
        df = df[df['Close'] > 0].copy() # Sadece hacmi olan günleri değil, fiyatı olan her günü al (Canlı mumu yakalamak için) 
        if len(df) < 20: return None
        df = calculate_volume_delta(df)
        _vp = calculate_full_volume_profile(df, lookback=20, bins=20)
        poc_price = _vp['poc']
        vah_price = _vp['vah']
        val_price = _vp['val']
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
                _src2_raw = yf.Ticker(ticker).history(period="3mo", auto_adjust=True)
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
        rvol = raw_today_v / avg_v if avg_v > 0 else 1.0

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
        recent_highs = h.iloc[-20:-1].max(); recent_lows = l.iloc[-20:-1].min()
        if c1_h > recent_highs and c1_c < recent_highs: sfp_txt, sfp_desc = "⚠️ Bearish SFP (Boğa Tuzağı)", "Tepe temizlendi ama tutunamadı."
        elif c1_l < recent_lows and c1_c > recent_lows: sfp_txt, sfp_desc = "💎 Bullish SFP (Ayı Tuzağı)", "Dip temizlendi ve geri döndü."

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
        
        # B. Kıyaslamalar
        p_now = c.iloc[-1]; p_old = c.iloc[-11]
        obv_now = obv.iloc[-1]; obv_old = obv.iloc[-11]
        obv_sma_now = obv_sma.iloc[-1]
        
        p_tr = "YUKARI" if p_now > p_old else "AŞAĞI"
        o_tr_raw = "YUKARI" if obv_now > obv_old else "AŞAĞI"
        
        # Güç Filtresi: OBV şu an ortalamasının üzerinde mi?
        is_obv_strong = obv_now > obv_sma_now

        obv_data = {"title": "Nötr / Zayıf", "desc": "Hacim akışı ortalamanın altında.", "color": "#64748B"}
        
        # Senaryo 1: GİZLİ GİRİŞ (Fiyat Düşerken Mal Toplama)
        if p_tr == "AŞAĞI" and o_tr_raw == "YUKARI":
            if is_obv_strong:
                obv_data = {"title": "🔥 GÜÇLÜ GİZLİ GİRİŞ", "desc": "Fiyat düşerken OBV ortalamasını kırdı (Smart Money).", "color": "#16a34a"}
            else:
                obv_data = {"title": "👀 Olası Toplama (Zayıf)", "desc": "OBV artıyor ama henüz ortalamayı geçemedi.", "color": "#d97706"}
                
        # Senaryo 2: GİZLİ ÇIKIŞ (Fiyat Çıkarken Mal Çakma)
        elif p_tr == "YUKARI" and o_tr_raw == "AŞAĞI":
            obv_data = {"title": "⚠️ GİZLİ ÇIKIŞ", "desc": "Fiyat çıkarken OBV düşüyor.", "color": "#f87171"}
            
        # Senaryo 3: TREND DESTEĞİ
        elif is_obv_strong:
            obv_data = {"title": "✅ Hacim Destekli Trend", "desc": "OBV ortalamasının üzerinde.", "color": "#15803d"}

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
        rvol = raw_today_v / avg_v if avg_v > 0 else 1.0
        
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
        # Fiyat formatı: büyükse tam sayı, küçükse ondalıklı
        def _fmt(v): return f"{v:.0f}" if v >= 100 else f"{v:.2f}" if v >= 1 else f"{v:.4f}"
        _poc_range = f"({_fmt(val_price)}–{_fmt(vah_price)})"

        if va_pos == "ÜSTÜNDE":
            if cum_delta_5 > 0:
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
                main_title = f"⚖️ POC ALANINDA {_poc_range} — Alım Baskısı Var"
                simple_text = "Fiyat en yoğun hacim bölgesinde (POC). Son 5 günde alım ağırlıklı işlem akışı görülüyor — POC üstünde tutunursa yapı güçlü kalır, altına iner ve kalırsa baskı sürebilir."
            elif cum_delta_5 < 0:
                main_title = f"⚖️ POC ALANINDA {_poc_range} — Satış Baskısı Var"
                simple_text = "Piyasa büyük oyuncuların en çok işlem yaptığı POC alanında. Son 5 günde satış ağır basıyor, aşağı kırılım riski var."
            else:
                main_title = f"⚖️ POC ALANINDA {_poc_range} — Yön Bekleniyor"
                simple_text = "Fiyat en yoğun hacim bölgesinde (POC). Alıcı ve satıcı dengede — POC'un hangi yönde kalıcı olarak terk edileceği sonraki yapıyı belirler."

        # NAKED POC — en yakın olanı seç
        naked_txt = ""
        if naked_pocs:
            closest = min(naked_pocs, key=lambda x: abs(x - fiyat))
            direction = "aşağıda" if closest < fiyat else "yukarıda"
            n_pct = abs(closest - fiyat) / (fiyat + 1e-9) * 100
            naked_txt = f"{closest:.2f} (fiyattan %{n_pct:.1f} {direction})"

        # OBV direction sayısal flag (GENEL ÖZET voting için — string matching yok)
        if p_tr == "AŞAĞI" and o_tr_raw == "YUKARI":
            _obv_direction = +1
        elif p_tr == "YUKARI" and o_tr_raw == "AŞAĞI":
            _obv_direction = -1
        elif is_obv_strong:
            _obv_direction = +1
        else:
            _obv_direction = 0

        return {
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
                "stopping":       stop_vol_msg,
                "climax":         climax_msg
            }
        }
    except Exception: return None
# ==============================================================================
# BÖLÜM 23 — BANNER / ROZET RENDER FONKSİYONLARI
# Altın Fırsat, Platin Fırsat, Güçlü Dönüş, Double Hit, Pre-Launch BOS banner'larını HTML olarak üreten görsel bileşenler.
# ==============================================================================
def render_golden_trio_banner(ict_data, sent_data, ticker=None):
    if not ict_data or not sent_data: return

    # --- 1. MANTIK KONTROLÜ ---
    rs_text = sent_data.get('rs', '').lower()
    cond_power  = ("artıda" in rs_text or "lider" in rs_text or "pozitif" in rs_text or
                   sent_data.get('total', 0) >= 50 or sent_data.get('raw_rsi', 0) > 50)
    cond_loc    = "DISCOUNT" in ict_data.get('zone', '') or "MSS" in ict_data.get('structure', '') or "BOS" in ict_data.get('structure', '')
    cond_energy = ("Güçlü" in ict_data.get('displacement', '') or
                   "Hacim" in sent_data.get('vol', '') or
                   sent_data.get('raw_rsi', 0) > 55)

    if not (cond_power and cond_loc and cond_energy):
        return

    # --- 2. Kırmızı mum kontrolü ---
    red_note = ""
    try:
        if ticker:
            _df_rc = get_safe_historical_data(ticker)
            if _df_rc is not None and len(_df_rc) >= 1:
                if float(_df_rc['Close'].iloc[-1]) < float(_df_rc['Open'].iloc[-1]):
                    red_note = '<span style="color:#f87171;font-size:0.72rem;font-weight:700;margin-left:8px;">🟠 son gün kırmızı</span>'
    except Exception:
        pass

    # --- 3. HTML ÇIKTISI ---
    bg = "linear-gradient(90deg, #ca8a04 0%, #eab308 100%)"
    border = "#a16207"
    txt = "#ffffff"

    st.markdown(f"""<div style="background:{bg}; border:1px solid {border}; border-radius:8px; padding:12px; margin-bottom:15px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
<div style="display:flex; justify-content:space-between; align-items:center;">
<div style="display:flex; align-items:center; gap:10px;">
<span style="font-size:1.6rem;">🏆</span>
<div style="line-height:1.2;">
<div style="font-weight:800; color:{txt}; font-size:1rem; letter-spacing:0.5px;">ALTIN SET-UP (GOLDEN TRIO){red_note}</div>
<div style="font-size:0.75rem; color:{txt}; opacity:0.95;">RS Gücü + Ucuz Konum + Güçlü Enerji (ICT): Mükemmel Uyum.</div>
</div>
</div>
<div style="font-family:'JetBrains Mono'; font-weight:800; font-size:1.2rem; color:{txt}; background:rgba(255,255,255,0.25); padding:4px 10px; border-radius:6px;">3/3</div>
</div>
</div>""", unsafe_allow_html=True)

def render_platin_live_banner(ticker, ict_data, sent_data):
    """
    PLATİN FIRSAT (A seçeneği — tutarlı hiyerarşi):
      Adım 1 — ALTIN kontrolü (zorunlu): RS güçlü + Discount/Yapı + Enerji
      Adım 2 — PLATİN ek kontrolü: SMA200 + SMA50 + RSI < 70
    İkisi de sağlanırsa banner göster.
    """
    try:
        df = get_safe_historical_data(ticker)
        if df is None or len(df) < 200: return

        # --- ADIM 1: ALTIN FIRSAT (zorunlu temel) ---
        if not ict_data or not sent_data:
            return  # Altın kontrolü için ICT/Sentiment verisi şart
        rs_text     = sent_data.get('rs', '').lower()
        cond_power  = ("artıda" in rs_text or "lider" in rs_text or "pozitif" in rs_text or
                       sent_data.get('total', 0) >= 50 or sent_data.get('raw_rsi', 0) > 50)
        cond_loc    = ("DISCOUNT" in ict_data.get('zone', '') or
                       "MSS" in ict_data.get('structure', '') or
                       "BOS" in ict_data.get('structure', ''))
        cond_energy = ("Güçlü" in ict_data.get('displacement', '') or
                       "Hacim" in sent_data.get('vol', '') or
                       sent_data.get('raw_rsi', 0) > 55)
        if not (cond_power and cond_loc and cond_energy):
            return  # Altın kriterini geçemedi → Platin gösterilmez

        # --- ADIM 2: PLATİN EK KRİTERLER ---
        c      = df['Close']
        cp     = float(c.iloc[-1])
        sma200 = float(c.rolling(200).mean().iloc[-1])
        sma50  = float(c.rolling(50).mean().iloc[-1])
        delta  = c.diff()
        gain   = delta.where(delta > 0, 0).rolling(14).mean()
        loss   = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi    = float(100 - (100 / (1 + gain / loss)).iloc[-1])
        if not (cp > sma200 and cp > sma50 and rsi < 70):
            return  # Platin ek kriterini geçemedi

        # Kırmızı mum notu
        _red_note_p = ""
        if float(df['Close'].iloc[-1]) < float(df['Open'].iloc[-1]):
            _red_note_p = ' <span style="color:#f87171;font-size:0.72rem;font-weight:700;">🟠 son gün kırmızı</span>'

        st.markdown(f"""<div style="background:linear-gradient(90deg, #1e3a8a 0%, #3b82f6 100%); border:1px solid #1e40af; border-radius:8px; padding:12px; margin-top:5px; margin-bottom:15px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2);">
<div style="display:flex; justify-content:space-between; align-items:center;">
<div style="display:flex; align-items:center; gap:10px;">
<span style="font-size:1.6rem;">💎</span>
<div style="line-height:1.2;">
<div style="font-weight:800; color:#ffffff; font-size:1rem; letter-spacing:0.5px;">💎 PLATİN SET-UP{_red_note_p}</div>
<div style="font-size:0.75rem; color:#ffffff; opacity:0.95;">Altın Set-Up (3/3) + SMA200 üstü + SMA50 üstü + RSI &lt; 70</div>
</div>
</div>
<div style="font-family:'JetBrains Mono'; font-weight:800; font-size:1.2rem; color:#ffffff; background:rgba(255,255,255,0.25); padding:4px 10px; border-radius:6px;">6/6</div>
</div>
</div>""", unsafe_allow_html=True)
    except: pass

def render_guclu_donus_banner(ticker):
    """
    BİREYSEL HİSSE ANALİZİ — Güçlü Dönüş Adayları paneli
    Hisse seçildiğinde 3 kriteri anlık hesaplar; geçerse banner gösterir.
    """
    try:
        df = get_safe_historical_data(ticker, period="1y")
        if df is None or df.empty: return

        res = calculate_guclu_donus_adaylari(ticker, df)
        if not res:
            return  # Kriterler sağlanmıyorsa sessizce dön

        bg     = "linear-gradient(90deg, #14532d 0%, #16a34a 100%)"
        border = "#166534"

        st.markdown(f'''
        <div style="background:{bg}; border:2px solid {border}; border-radius:8px; padding:15px; margin-top:10px; margin-bottom:10px; box-shadow:0 4px 6px rgba(0,0,0,0.1);">
            <h3 style="color:#ffffff; margin:0; font-size:1.4rem; font-weight:800; display:flex; align-items:center;">
                <span style="font-size:2rem; margin-right:10px;">🔄</span> GÜÇLÜ DÖNÜŞ ADAYI
            </h3>
            <p style="color:#bbf7d0; font-size:0.9rem; margin-top:5px; margin-bottom:10px; font-weight:600;">3 dönüş kriteri aynı anda tetiklendi. Dikkatli takip edilebilir.</p>
            <div style="display:flex; flex-wrap:wrap; gap:10px; margin-top:10px;">
                <span style="background:rgba(255,255,255,0.15); padding:4px 8px; border-radius:4px; font-size:0.85rem; color:white; font-weight:700;">📉 Z-Score: {res['Z-Score']} (Aşırı Satım)</span>
                <span style="background:rgba(255,255,255,0.15); padding:4px 8px; border-radius:4px; font-size:0.85rem; color:white; font-weight:700;">💸 OBV: Gizli Para Girişi (Birikim)</span>
                <span style="background:rgba(255,255,255,0.15); padding:4px 8px; border-radius:4px; font-size:0.85rem; color:white; font-weight:700;">📊 20-Bar Dip + Hacim: {res['Hacim_Kat']}x Ortalama</span>
                <span style="background:rgba(255,255,255,0.15); padding:4px 8px; border-radius:4px; font-size:0.85rem; color:white; font-weight:700;">💎 RSI Diverjans: {res['RSI_Div']}</span>
            </div>
        </div>
        ''', unsafe_allow_html=True)
    except Exception:
        pass


def render_double_hit_banner(ticker, ict_data, sent_data):
    """
    Hem ELİT hem Pre-Launch BOS kriterlerini aynı anda karşılayan hisseler için
    özel 'ÇİFT TEYİT' banner'ı. Canlı Sinyaller'de diğer banner'lardan önce gösterilir.
    """
    try:
        df = get_safe_historical_data(ticker, period="1y")
        if df is None or len(df) < 50:
            return

        # ELİT kriteri (platin live kontrolü)
        if not ict_data or not sent_data:
            return
        rs_text    = sent_data.get('rs', '').lower()
        c_power    = ("artıda" in rs_text or "lider" in rs_text or "pozitif" in rs_text or
                      sent_data.get('total', 0) >= 50 or sent_data.get('raw_rsi', 0) > 50)
        c_loc      = ("DISCOUNT" in ict_data.get('zone', '') or
                      "MSS" in ict_data.get('structure', '') or
                      "BOS" in ict_data.get('structure', ''))
        c_energy   = ("Güçlü" in ict_data.get('displacement', '') or
                      "Hacim" in sent_data.get('vol', '') or
                      sent_data.get('raw_rsi', 0) > 55)
        is_elit = c_power and c_loc and c_energy

        # Pre-Launch BOS kriteri (live hesaplama)
        bos_res = calculate_prelaunch_bos(ticker, df)
        is_bos  = bos_res is not None

        if not (is_elit and is_bos):
            return

        day_label = ["Bugün", "Dün", "2 gün önce", "3 gün önce"][bos_res['BOS_Day']]
        st.markdown(f"""
        <div style="background:linear-gradient(135deg,#1e1035 0%,#2d1b69 50%,#1e3a8a 100%);
                    border:2px solid #818cf8;border-radius:10px;padding:16px;
                    margin-top:10px;margin-bottom:12px;
                    box-shadow:0 0 20px rgba(129,140,248,0.3);">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
                <span style="font-size:1.6rem;">💎🚀</span>
                <div>
                    <div style="font-weight:900;color:#ffffff;font-size:1.05rem;letter-spacing:0.3px;">
                        ÇİFT TEYİT — ELİT + PRE-LAUNCH BOS
                    </div>
                    <div style="font-size:0.72rem;color:#c7d2fe;margin-top:2px;">
                        Hem kurumsal kalite kriterlerini hem de kırılım zamanlamasını karşılıyor
                    </div>
                </div>
                <div style="margin-left:auto;background:rgba(129,140,248,0.2);border:1px solid #818cf8;
                            border-radius:8px;padding:4px 12px;text-align:center;">
                    <div style="font-size:0.65rem;color:#a5b4fc;font-weight:700;">SKOR</div>
                    <div style="font-size:1rem;font-weight:900;color:#ffffff;">{bos_res['Skor']}/100</div>
                </div>
            </div>
            <div style="display:flex;flex-wrap:wrap;gap:6px;">
                <span style="background:rgba(255,255,255,0.1);border:1px solid rgba(129,140,248,0.4);
                             padding:3px 10px;border-radius:4px;font-size:0.78rem;color:#e0e7ff;font-weight:600;">
                    💎 ELİT: RS güçlü + Discount + Enerji
                </span>
                <span style="background:rgba(255,255,255,0.1);border:1px solid rgba(129,140,248,0.4);
                             padding:3px 10px;border-radius:4px;font-size:0.78rem;color:#e0e7ff;font-weight:600;">
                    🚀 BOS: {day_label} kırıldı ({bos_res['Squeeze_Gun']}g squeeze)
                </span>
                <span style="background:rgba(255,255,255,0.1);border:1px solid rgba(129,140,248,0.4);
                             padding:3px 10px;border-radius:4px;font-size:0.78rem;color:#e0e7ff;font-weight:600;">
                    📊 Hacim {bos_res['Hacim_Kat']}x · RSI {bos_res['RSI']:.0f}
                </span>
            </div>
        </div>
        """, unsafe_allow_html=True)
    except Exception:
        pass


def render_prelaunch_bos_banner(ticker):
    """Bireysel hisse için Pre-Launch BOS banner'ı."""
    try:
        df = get_safe_historical_data(ticker, period="1y")
        if df is None or df.empty:
            return
        res = calculate_prelaunch_bos(ticker, df)
        if not res:
            return

        day_label = ["Bugün", "Dün", "2 gün önce", "3 gün önce"][res['BOS_Day']]
        st.markdown(f'''
        <div style="background:linear-gradient(90deg,#1e1b4b 0%,#3730a3 100%);
                    border:2px solid #6366f1;border-radius:8px;padding:15px;
                    margin-top:10px;margin-bottom:10px;box-shadow:0 4px 6px rgba(0,0,0,0.2);">
            <h3 style="color:#ffffff;margin:0;font-size:1.3rem;font-weight:800;display:flex;align-items:center;">
                <span style="font-size:1.8rem;margin-right:10px;">🚀</span>
                PRE-LAUNCH BOS — {day_label} kırıldı
            </h3>
            <p style="color:#c7d2fe;font-size:0.85rem;margin-top:5px;margin-bottom:10px;font-weight:600;">
                Squeeze sonrası 45-günlük direnç aşıldı · Skor: {res['Skor']}/100
            </p>
            <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;">
                <span style="background:rgba(255,255,255,0.12);padding:4px 10px;border-radius:4px;font-size:0.82rem;color:white;font-weight:700;">
                    📦 Squeeze: {res['Squeeze_Gun']} gün sıkıştı
                </span>
                <span style="background:rgba(255,255,255,0.12);padding:4px 10px;border-radius:4px;font-size:0.82rem;color:white;font-weight:700;">
                    📊 BOS Hacim: {res['Hacim_Kat']}x
                </span>
                <span style="background:rgba(255,255,255,0.12);padding:4px 10px;border-radius:4px;font-size:0.82rem;color:white;font-weight:700;">
                    📈 RSI: {res['RSI']}
                </span>
                <span style="background:rgba(255,255,255,0.12);padding:4px 10px;border-radius:4px;font-size:0.82rem;color:white;font-weight:700;">
                    🎯 BOS seviyesi: {res['BOS_Level']} (+{res['BOS_Dist']:.1f}% uzakta)
                </span>
            </div>
        </div>
        ''', unsafe_allow_html=True)
    except Exception:
        pass


# ==============================================================================
# BÖLÜM 24 — HARMONİK FORMASYONLARI (XABCD) TESPİTİ
# Gartley, Bat, Butterfly, Crab, Cypher harmonik formasyonları.
# Batch tarama ve detay diyalog render fonksiyonları.
# ==============================================================================

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


@st.cache_data(ttl=900)
def scan_harmonic_patterns_batch(asset_list):
    """
    🔮 Harmonik Formasyon Toplu Taraması
    Tüm listede XABCD Fibonacci formasyonu arar, PRZ yakınındakileri döndürür.
    """
    data = get_batch_data_cached(asset_list, period="1y")
    if data is None or (hasattr(data, 'empty') and data.empty):
        return pd.DataFrame()

    results = []
    _PATTERN_EMOJI = {'Gartley': '🦋', 'Butterfly': '🦋', 'Bat': '🦇', 'Crab': '🦀', 'Shark': '🦈'}
    _DIR_EMOJI = {'Bullish': '🟢', 'Bearish': '🔴'}

    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol not in data.columns.levels[0]:
                    continue
                df = data[symbol].dropna()
            else:
                df = data.dropna()

            if len(df) < 60:
                continue

            avg_vol = df['Volume'].iloc[-20:].mean()
            if avg_vol < 500_000:
                continue

            res = calculate_harmonic_patterns(symbol, df)
            if res:
                fiyat = res['curr_price']
                emoji = _PATTERN_EMOJI.get(res['pattern'], '🔮')
                dir_e = _DIR_EMOJI.get(res['direction'], '')
                results.append({
                    'Sembol': symbol,
                    'Fiyat': round(fiyat, 2),
                    'Pattern': f"{emoji} {res['pattern']}",
                    'Yön': f"{dir_e} {res['direction']}",
                    'PRZ': round(res['prz'], 2),
                    'PRZ_Fark%': round(abs(fiyat - res['prz']) / res['prz'] * 100, 1),
                    'AB_XA': res['AB_XA'],
                    'XD_XA': res['XD_XA'],
                    'Bar_Önce': res['bars_ago'],
                    'Durum': '📍 Yaklaşıyor' if res.get('state') == 'approaching' else '✅ Taze',
                })
        except Exception:
            continue

    if not results:
        return pd.DataFrame()

    df_out = pd.DataFrame(results)
    # Önce yeni oluşanlar (bars_ago küçük), sonra PRZ'ye yakın olanlar
    df_out['_yon_sira'] = df_out['Yön'].apply(lambda x: 0 if 'Bullish' in x else 1)
    df_out.sort_values(['_yon_sira', 'Bar_Önce', 'PRZ_Fark%'], inplace=True)
    df_out.drop(columns=['_yon_sira'], inplace=True)
    df_out.reset_index(drop=True, inplace=True)
    return df_out


def render_harmonic_banner(ticker):
    """
    BİREYSEL HİSSE — Harmonik formasyon varsa banner gösterir.
    """
    try:
        df = get_safe_historical_data(ticker, period="1y")
        if df is None or df.empty:
            return
        res = calculate_harmonic_patterns(ticker, df)
        if not res:
            return

        pat   = res['pattern']
        direc = res['direction']
        prz   = res['prz']
        fark  = abs(res['curr_price'] - prz) / (prz + 1e-9) * 100

        # Soluk renkler — buton tonu ile uyumlu (dark navy bazlı)
        if direc == 'Bullish':
            bg     = "#67af8e"
            border = "#2a4035"
            dir_lbl = "🟢 LONG BEKLENTİSİ"
        else:
            bg     = "#610909C3"
            border = "#402a2a"
            dir_lbl = "🔴 SHORT BEKLENTİSİ"

        _EMOJI = {'Gartley': '🦋', 'Butterfly': '🦋', 'Bat': '🦇', 'Crab': '🦀', 'Shark': '🦈'}
        emoji = _EMOJI.get(pat, '🔮')

        # XABCD tarihleri
        pivot_labels = ['X', 'A', 'B', 'C', 'D']
        pivot_dates_html = ""
        p_idx    = res.get('pivot_idx', [])
        p_prices = res.get('pivot_prices', [])
        if p_idx and len(p_idx) == 5:
            rows = []
            for k, (lbl, idx) in enumerate(zip(pivot_labels, p_idx)):
                try:
                    if idx is None:
                        _d_prz_str = f" (~{prz:.2f})" if lbl == 'D' and prz else ""
                        dt_str  = f"bekleniyor{_d_prz_str}"
                        px_str  = ""
                    else:
                        dt      = df.index[idx]
                        dt_str  = pd.Timestamp(dt).strftime('%d/%m/%Y')
                        px_val  = p_prices[k] if k < len(p_prices) and p_prices[k] is not None else None
                        px_str  = f" <span style='color:#94a3b8;'>{px_val:.2f}</span>" if px_val else ""
                except Exception:
                    dt_str = "?"; px_str = ""
                rows.append(f"<span style='display:inline-block;min-width:150px;'>"
                            f"<b style='color:#aaa;'>{lbl}:</b> {dt_str}{px_str}</span>")
            pivot_dates_html = (
                "<div style='margin-top:8px; font-size:0.75rem; color:#ccc; "
                "background:rgba(255,255,255,0.05); border-radius:5px; padding:6px 10px; "
                "display:flex; flex-wrap:wrap; gap:4px 12px;'>"
                + "".join(rows) +
                "</div>"
            )

        state = res.get('state', 'fresh')
        if state == 'approaching':
            prz_note = f"PRZ'ye yaklaşıyor — izlemeye al"
        elif fark < 2:
            prz_note = "PRZ'de! Dönüş başlayabilir"
        else:
            prz_note = "PRZ'den yeni ayrıldı — dönüş izleniyor"

        _d_lbl = "D Tahmini — Henüz Oluşmadı" if state == "approaching" else f"D Noktası: {res['bars_ago']} Gün Önce"
        _sr_span = '<span style="background:rgba(34,197,94,0.18); color:#86efac; padding:3px 8px; border-radius:5px; font-size:0.78rem; font-weight:700;">🧲 D = S/R Confluence (Güçlü)</span>' if res.get('d_sr_confluence') else ''
        _harm_html = (
            f'<div style="background:{bg}; border:1px solid {border}; border-radius:8px; padding:13px; margin-top:8px; margin-bottom:10px;">'
            f'<div style="display:flex; justify-content:space-between; align-items:center;">'
            f'<div><span style="font-size:1.4rem;">{emoji}</span>'
            f'<span style="color:#e2e8f0; font-weight:900; font-size:1rem; margin-left:8px;">HARMONİK: {pat.upper()}</span>'
            f'<span style="color:#cbd5e1; font-size:0.78rem; margin-left:10px;">{dir_lbl}</span></div>'
            f'<span style="background:rgba(255,255,255,0.12); color:#e2e8f0; padding:3px 10px; border-radius:10px; font-weight:800; font-size:0.85rem;">PRZ: {prz:.2f}</span>'
            f'</div>'
            f'<div style="margin-top:9px; display:flex; flex-wrap:wrap; gap:7px;">'
            f'<span style="background:rgba(255,255,255,0.08); color:#cbd5e1; padding:3px 8px; border-radius:5px; font-size:0.78rem; font-weight:700;">📐 AB/XA: {res["AB_XA"]}</span>'
            f'<span style="background:rgba(255,255,255,0.08); color:#cbd5e1; padding:3px 8px; border-radius:5px; font-size:0.78rem; font-weight:700;">📏 XD/XA: {res["XD_XA"]}</span>'
            f'<span style="background:rgba(255,255,255,0.08); color:#cbd5e1; padding:3px 8px; border-radius:5px; font-size:0.78rem;">🎯 PRZ\'ye Uzaklık: %{fark:.1f} — {prz_note}</span>'
            f'<span style="background:rgba(255,255,255,0.08); color:#cbd5e1; padding:3px 8px; border-radius:5px; font-size:0.78rem; font-weight:700;">🕒 {_d_lbl}</span>'
            f'{_sr_span}'
            f'</div>'
            f'{pivot_dates_html}'
            f'</div>'
        )
        st.markdown(_harm_html, unsafe_allow_html=True)
    except Exception:
        pass

# ==============================================================================
# BÖLÜM 25 — HARMONİK CONFLUENCE MOTORU (Harmonic PRZ + ICT Discount + RSI Div)
# Birden fazla harmonik sinyalin aynı fiyat bölgesinde çakıştığı setup'ları tespit eder.
# ==============================================================================
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


def render_harmonic_confluence_banner(ticker):
    """
    BİREYSEL HİSSE — Harmonik Confluence varsa özel mor rozet gösterir.
    """
    try:
        df = get_safe_historical_data(ticker, period="1y")
        res = calculate_harmonic_confluence(ticker, df)
        if not res:
            return

        _EMOJI = {'Gartley': '🦋', 'Butterfly': '🦋', 'Bat': '🦇', 'Crab': '🦀', 'Shark': '🦈'}
        emoji   = _EMOJI.get(res['pattern'], '🔮')
        dir_lbl = "🟢 LONG" if res['direction'] == 'Bullish' else "🔴 SHORT"
        state_lbl = "PRZ'de" if res['state'] == 'fresh' else "PRZ'ye Yaklaşıyor"

        st.markdown(f'''
        <div style="background:linear-gradient(135deg,#7c3aed18,#7c3aed06);
                    border:1px solid #7c3aed50; border-radius:10px;
                    padding:10px 13px; margin-top:8px; margin-bottom:10px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                    <span style="font-size:1rem;">⚡</span>
                    <span style="color:#7c3aed; font-weight:900; font-size:0.92rem; margin-left:6px;">
                        HARMONİK CONFLUENCE — {res["pattern"].upper()}
                    </span>
                    <span style="color:#64748b; font-size:0.75rem; margin-left:8px;">{dir_lbl}</span>
                </div>
                <span style="background:#7c3aed50; color:#7c3aed; padding:2px 9px;
                             border-radius:10px; font-weight:800; font-size:0.78rem;">
                    PRZ: {res["prz"]:.2f}
                </span>
            </div>
            <div style="margin-top:7px; display:flex; flex-wrap:wrap; gap:5px;">
                <span style="background:#7c3aed18; color:#7c3aed; padding:2px 7px;
                             border-radius:5px; font-size:0.75rem; font-weight:700;">
                    {emoji} Harmonik: {res["pattern"]} ({state_lbl})
                </span>
                {'<span style="background:#7c3aed18; color:#7c3aed; padding:2px 7px; border-radius:5px; font-size:0.75rem; font-weight:700;">🧭 ICT: ' + res["zone"] + '</span>' if res.get("ict_match") else '<span style="background:rgba(100,100,100,0.08); color:#94a3b8; padding:2px 7px; border-radius:5px; font-size:0.75rem;">🧭 ICT Zone yok</span>'}
                {'<span style="background:#7c3aed18; color:#7c3aed; padding:2px 7px; border-radius:5px; font-size:0.75rem; font-weight:700;">💎 RSI Div: ' + ("Pozitif" if res["div_type"] == "bullish" else "Negatif") + '</span>' if res.get("rsi_match") else '<span style="background:rgba(100,100,100,0.08); color:#94a3b8; padding:2px 7px; border-radius:5px; font-size:0.75rem;">💎 RSI Div yok</span>'}
            </div>
            <div style="margin-top:6px; font-size:0.71rem; color:#64748b; font-style:italic;">
                {res.get("Aciklama", "Harmonik PRZ teyitli")} — kriterler: {int(res.get("ict_match", False)) + int(res.get("rsi_match", False))}/2
            </div>
        </div>
        ''', unsafe_allow_html=True)
    except Exception:
        pass


@st.cache_data(ttl=900)
def scan_harmonic_confluence_batch(asset_list):
    """
    Tüm listede Harmonic + ICT Discount + RSI Div üçlü confluence tarar.
    """
    data = get_batch_data_cached(asset_list, period="1y")
    if data is None or (hasattr(data, 'empty') and data.empty):
        return pd.DataFrame()

    results = []
    _EMOJI = {'Gartley': '🦋', 'Butterfly': '🦋', 'Bat': '🦇', 'Crab': '🦀', 'Shark': '🦈'}

    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol not in data.columns.levels[0]:
                    continue
                df = data[symbol].dropna()
            else:
                df = data.dropna()

            if len(df) < 60:
                continue

            res = calculate_harmonic_confluence(symbol, df)
            if res:
                emoji = _EMOJI.get(res['pattern'], '🔮')
                dir_e = '🟢' if res['direction'] == 'Bullish' else '🔴'
                results.append({
                    'Sembol':    symbol,
                    'Fiyat':     round(res['prz'], 2),
                    'Pattern':   f"{emoji} {res['pattern']}",
                    'Yön':       f"{dir_e} {res['direction']}",
                    'PRZ':       round(res['prz'], 2),
                    'ICT_Zone':  res['zone'],
                    'RSI_Div':   res['div_type'],
                    'Durum':     '✅ Taze' if res['state'] == 'fresh' else '📍 Yaklaşıyor',
                    'Badges':    res.get('badge_str', ''),
                    'Aciklama':  res.get('Aciklama', ''),
                })
        except Exception:
            continue

    if not results:
        return pd.DataFrame()
    df_out = pd.DataFrame(results)
    # Bullish önce
    df_out['_s'] = df_out['Yön'].apply(lambda x: 0 if 'Bullish' in x else 1)
    df_out.sort_values('_s', inplace=True)
    df_out.drop(columns=['_s'], inplace=True)
    df_out.reset_index(drop=True, inplace=True)
    return df_out


# --- ROYAL FLUSH NADİR FIRSAT HESAPLAYICI ---
def render_nadir_firsat_banner(ict_data, sent_data, ticker):
    if not ict_data or not sent_data: return

    # --- KRİTER 1: YAPI (ICT) ---
    # BOS veya MSS (Bullish) olmalı
    cond_struct = "BOS (Yükseliş" in ict_data.get('structure', '') or "MSS (Market Structure Shift) 🐂" in ict_data.get('structure', '')
    
    # --- KRİTER 2: GÜÇ (RS MOMENTUM) ---
    alpha_val = 0
    pa_data = calculate_price_action_dna(ticker)
    if pa_data:
        alpha_val = pa_data.get('rs', {}).get('alpha', 0)
    cond_rs = alpha_val > 0

    # --- KRİTER 3: MALİYET (VWAP) ---
    v_diff = pa_data.get('vwap', {}).get('diff', 0) if pa_data else 0
    cond_vwap = v_diff < 12

    # --- KRİTER 3: HACİM CANLANMASI ---
    try:
        df_vol = get_safe_historical_data(ticker)
        if df_vol is not None and len(df_vol) >= 22:
            vol      = df_vol['Volume']
            ort20    = vol.iloc[-22:-2].mean()
            son3_ort = vol.iloc[-3:].mean()
            son2_ort = vol.iloc[-2:].mean()
            onc5_ort = vol.iloc[-7:-2].mean()
            cond_vol = (son3_ort > ort20 * 1.2) or (son2_ort > onc5_ort * 1.3)
        else:
            cond_vol = False
    except:
        cond_vol = False

    # --- FİLTRE (YA HEP YA HİÇ - 4/4) ---
    if not (cond_struct and cond_rs and cond_vwap and cond_vol):
        return

    # --- HTML ÇIKTISI ---
    bg = "linear-gradient(90deg, #1e3a8a 0%, #3b82f6 100%)"
    border = "#1e40af"
    txt = "#ffffff"

    st.markdown(f"""<div style="background:{bg}; border:1px solid {border}; border-radius:8px; padding:12px; margin-top:5px; margin-bottom:15px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);">
<div style="display:flex; justify-content:space-between; align-items:center;">
<div style="display:flex; align-items:center; gap:10px;">
<span style="font-size:1.6rem;">♠️</span>
<div style="line-height:1.2;">
<div style="font-weight:800; color:{txt}; font-size:1rem; letter-spacing:0.5px;">ROYAL FLUSH NADİR SET-UP</div>
<div style="font-size:0.75rem; color:{txt}; opacity:0.95;">ICT Yapı + RS Liderliği + VWAP Uyumu + Hacim Canlanması: En Yüksek Olasılık.</div>
</div>
</div>
<div style="font-family:'JetBrains Mono'; font-weight:800; font-size:1.2rem; color:{txt}; background:rgba(255,255,255,0.25); padding:4px 10px; border-radius:6px;">4/4</div>
</div>
</div>""", unsafe_allow_html=True)

# ==============================================================================
# BÖLÜM 26 — SUPERTREND, FİBONACCİ VE Z-SCORE MOTORLARİ
# Supertrend hesabı, dinamik makro döngü Fibonacci seviyeleri ve Z-Score canlı hesaplama fonksiyonları.
# ==============================================================================
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

# --- YENİ KOD: Dinamik Makro Döngü Fibonacci Motoru ---
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

# ==============================================================================
# BÖLÜM 27 — PİYASA REJİMİ VE KONVİKSİYON SKORU
# Trend/yatay/düzeltme rejim tespiti. Tüm sinyalleri harmanlayarak 0-100 arası nihai konviksiyon skoru üretir.
# ==============================================================================
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


# ═══════════════════════════════════════════════════════════════════════════════
# CONVICTION SCORE  –  calculate_conviction_score(df, pa, ict_data, sent_data)
# ═══════════════════════════════════════════════════════════════════════════════
def calculate_conviction_score(df, pa=None, ict_data=None, sent_data=None, bench_s=None, ticker=""):
    """
    0-100 arası bileşik kanaat skoru.
    70-100 → GÜÇLÜ LONG  | 55-69 → LONG
    45-54  → NÖTR        | 30-44 → SHORT
    0-29   → GÜÇLÜ SHORT
    Dönen dict: score, label, color, icon, factors
    """
    try:
        close = df['Close']
        n     = len(close)
        cp    = float(close.iloc[-1])
        raw   = 0   # -100 … +100 aralığı

        factors = []  # (açıklama, puan)

        # ── 1. SMA50 pozisyonu (+10/-10) ──
        if n >= 50:
            s50 = float(close.rolling(50).mean().iloc[-1])
            pts = 10 if cp > s50 else -10
            raw += pts
            factors.append((f"SMA50 {'üzeri ✓' if pts>0 else 'altı ✗'}", pts))

        # ── 2. SMA200 pozisyonu (+10/-10) ──
        if n >= 200:
            s200 = float(close.rolling(200).mean().iloc[-1])
            pts  = 10 if cp > s200 else -10
            raw += pts
            factors.append((f"SMA200 {'üzeri ✓' if pts>0 else 'altı ✗'}", pts))

        # ── 3. OBV trendi (+10/-10) ──
        try:
            if 'Volume' in df.columns and n >= 20:
                direction = np.sign(close.diff().fillna(0))
                obv = (direction * df['Volume']).cumsum()
                obv_slope = float(np.polyfit(np.arange(20, dtype=float),
                                             obv.iloc[-20:].values.astype(float), 1)[0])
                pts = 10 if obv_slope > 0 else -10
                raw += pts
                factors.append((f"OBV Trend {'↑ ✓' if pts>0 else '↓ ✗'}", pts))
        except:
            pass

        # ── 4. Z-Score dip/tepe (+10/-10) ──
        try:
            zd = _z_score_details(df)
            if zd:
                z = zd["composite"]
                if z <= -1.5:
                    pts = 10; factors.append((f"Z-Score Dip ({z:.1f}) ✓", pts))
                elif z >= 1.5:
                    pts = -10; factors.append((f"Z-Score Tepe ({z:.1f}) ✗", pts))
                elif z <= -0.8:
                    pts = 5; factors.append((f"Z-Score Dip Yakın ({z:.1f})", pts))
                elif z >= 0.8:
                    pts = -5; factors.append((f"Z-Score Tepe Yakın ({z:.1f})", pts))
                else:
                    pts = 0
                raw += pts
        except:
            pass

        # ── 5. RSI Uyumsuzluk (+15/-15) ──
        try:
            if pa:
                div_type = pa.get('div', {}).get('type', 'neutral')
                if div_type == 'bullish':
                    pts = 15; raw += pts
                    factors.append(("RSI Pozitif Uyumsuzluk ✓", pts))
                elif div_type == 'bearish':
                    pts = -15; raw += pts
                    factors.append(("RSI Negatif Uyumsuzluk ✗", pts))
        except:
            pass

        # ── 6. ICT Bölgesi (+10/-10) ──
        try:
            if ict_data:
                zone = ict_data.get('zone', '')
                struct = ict_data.get('structure', '')
                if 'DISCOUNT' in zone or 'BOS' in struct or 'MSS' in struct:
                    pts = 10; raw += pts
                    factors.append(("ICT DISCOUNT / Yapı Kırılımı ✓", pts))
                elif 'PREMIUM' in zone:
                    pts = -10; raw += pts
                    factors.append(("ICT PREMIUM Bölgesi ✗", pts))
        except:
            pass

        # ── 7. Kümülatif Delta / Momentum (+10/-10) ──
        try:
            if pa:
                cd5 = pa.get('cum_delta_5', 0) or 0
                if cd5 > 0:
                    pts = 10 if cd5 > 500 else 5
                    raw += pts; factors.append((f"Cum Delta Pozitif ({cd5:+.0f}) ✓", pts))
                elif cd5 < 0:
                    pts = -10 if cd5 < -500 else -5
                    raw += pts; factors.append((f"Cum Delta Negatif ({cd5:+.0f}) ✗", pts))
        except:
            pass

        # ── 8. RVOL (+5/-5) ──
        try:
            if pa:
                rvol = pa.get('rvol', 1.0) or 1.0
                if rvol > 1.5:
                    pts = 5; raw += pts; factors.append((f"RVOL Yüksek ({rvol:.1f}x) ✓", pts))
                elif rvol < 0.5:
                    pts = -5; raw += pts; factors.append((f"RVOL Düşük ({rvol:.1f}x) ✗", pts))
        except:
            pass

        # ── 9. Stopping / Climax Hacim (+10/-10) ──
        try:
            if pa:
                sv = pa.get('smart_volume', {})
                if sv.get('stopping', 'Yok') != 'Yok':
                    pts = 10; raw += pts; factors.append(("Stopping Volume (Balina Fren) ✓", pts))
                if sv.get('climax', 'Yok') != 'Yok':
                    pts = -10; raw += pts; factors.append(("Climax Volume (Boşaltma) ✗", pts))
        except:
            pass

        # ── 10. RS Alpha (+10/-10) ──
        try:
            if bench_s is not None and n >= 6 and not ticker.startswith("^"):
                s5 = (cp / float(close.iloc[-6]) - 1) * 100
                b5 = (float(bench_s.iloc[-1]) / float(bench_s.iloc[-6]) - 1) * 100
                alpha = s5 - b5
                if alpha > 1.5:
                    pts = 10; raw += pts; factors.append((f"RS Alpha +{alpha:.1f}% ✓", pts))
                elif alpha < -1.5:
                    pts = -10; raw += pts; factors.append((f"RS Alpha {alpha:.1f}% ✗", pts))
                elif alpha > 0.5:
                    pts = 5; raw += pts; factors.append((f"RS Alpha +{alpha:.1f}%", pts))
                elif alpha < -0.5:
                    pts = -5; raw += pts; factors.append((f"RS Alpha {alpha:.1f}%", pts))
        except:
            pass

        # ── Normalize → 0-100 ──
        # raw aralığı teorik: -100 … +100
        # clamp → normalize
        raw_clamped = max(-100, min(100, raw))
        score = int(round((raw_clamped + 100) / 2))

        if score >= 70:
            label = "GÜÇLÜ LONG"; icon = "🟢🟢"; color = "#10b981"
        elif score >= 55:
            label = "LONG"; icon = "🟢"; color = "#4ade80"
        elif score >= 45:
            label = "NÖTR"; icon = "🟡"; color = "#f59e0b"
        elif score >= 30:
            label = "SHORT"; icon = "🔴"; color = "#f87171"
        else:
            label = "GÜÇLÜ SHORT"; icon = "🔴🔴"; color = "#f87171"

        return {
            "score":   score,
            "raw":     raw,
            "label":   label,
            "icon":    icon,
            "color":   color,
            "factors": factors,
        }
    except:
        return {"score": 50, "raw": 0, "label": "NÖTR", "icon": "🟡",
                "color": "#f59e0b", "factors": []}


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

## ==============================================================================
# BÖLÜM 28 — GRAFİK VE GÖRSELLEŞTİRME fonksiyonları
# Gauge chart, ana fiyat grafiği (Matplotlib + Plotly), sparkline,
# RSI bar, SMC elementleri. Tüm grafik render fonksiyonları burada.
# ==============================================================================
@st.cache_data(ttl=900, show_spinner=False)
def _gauge_chart_b64(score, dark_mode):
    from matplotlib.patches import Wedge, Circle
    score = max(0, min(100, int(score)))

    bg = '#f8fafc'
    fg = '#1e293b'

    # Arc zone colors (visible on both dark/light as fills)
    zones = [
        (0,  20,  "#b71c1c", "AŞIRI ZAYIF"),   # koyu kırmızı
        (20, 40,  "#ff7043", "ZAYIF"),
        (40, 60,  "#ffd600", "NÖTR"),
        (60, 80,  "#66bb6a", "GÜÇLÜ"),
        (80, 100, "#2e7d32", "AŞIRI GÜÇLÜ"),   # koyu yeşil
    ]

    # Light-mode text uses darker shades for legibility
    light_text = {
        "#b71c1c": "#b71c1c",
        "#ff7043": "#c2390f",
        "#ffd600": "#9a6c00",
        "#66bb6a": "#2e7d32",
        "#2e7d32": "#1b5e20",
    }

    cur_label = "NÖTR"
    cur_color = "#ffd600"
    for zs, ze, zc, zl in zones:
        if zs <= score <= ze:
            cur_label = zl
            cur_color = zc
            break

    score_text_color = light_text.get(cur_color, cur_color)

    needle_color = '#1e3a8a'   # koyu lacivert ibre, her modda

    fig, ax = plt.subplots(figsize=(4.5, 2.8))
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)
    ax.set_xlim(-1.45, 1.45)
    ax.set_ylim(-0.52, 1.3)
    ax.set_aspect('equal')
    ax.axis('off')

    outer_r = 1.0
    inner_r = 0.55

    # Zone arcs
    for zs, ze, zc, _ in zones:
        t1 = 180 - (ze / 100) * 180
        t2 = 180 - (zs / 100) * 180
        ax.add_patch(Wedge((0, 0), outer_r, t1, t2,
                           width=outer_r - inner_r,
                           facecolor=zc, edgecolor=bg, linewidth=2.5))

    # Tick marks + boundary labels at 0, 20, 40, 60, 80, 100
    for v in [0, 20, 40, 60, 80, 100]:
        ang = np.radians(180 - (v / 100) * 180)
        cx, cy = np.cos(ang), np.sin(ang)
        ax.plot([1.02 * cx, 1.14 * cx], [1.02 * cy, 1.14 * cy],
                color=fg, lw=1.2, alpha=0.75)
        ax.text(1.26 * cx, 1.26 * cy, str(v),
                ha='center', va='center', fontsize=10,
                color=fg, alpha=1.0, fontweight='bold', fontfamily='monospace')

    # Needle — koyu lacivert
    ang_rad = np.radians(180 - (score / 100) * 180)
    nx = 0.79 * np.cos(ang_rad)
    ny = 0.79 * np.sin(ang_rad)
    ax.plot([0, nx], [0, ny], color=needle_color, linewidth=2.5, zorder=8,
            solid_capstyle='round')
    ax.add_patch(Circle((0, 0), 0.062, facecolor=needle_color, zorder=9))

    # Score + zone label below center
    ax.text(0, -0.16, str(score),
            ha='center', va='center',
            fontsize=26, fontweight='bold',
            color=score_text_color, fontfamily='monospace')
    ax.text(0, -0.36, cur_label,
            ha='center', va='center',
            fontsize=10, fontweight='bold',
            color=fg, alpha=0.92)

    plt.tight_layout(pad=0)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor=bg)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def render_gauge_chart(score):
    b64 = _gauge_chart_b64(int(score), True)
    if b64:
        st.markdown(
            f"<img src='data:image/png;base64,{b64}' "
            f"style='width:100%;display:block;margin:0 auto;'/>",
            unsafe_allow_html=True
        )


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

    # ── 2. Fair Value Gaps (3-bar gap) + mitigation tracking ─────────
    # Bullish FVG zone: (highs[i-2], lows[i])  — gap must not be refilled yet
    # Bearish FVG zone: (highs[i],  lows[i-2]) — gap must not be refilled yet
    fvg_bull_raw = []
    fvg_bear_raw = []
    for i in range(2, n):
        if lows_arr[i] > highs_arr[i - 2]:
            p_lo = float(highs_arr[i - 2])
            p_hi = float(lows_arr[i])
            # Mitigated when any subsequent low re-enters the gap from above
            if not any(lows_arr[j] < p_hi for j in range(i + 1, n)):
                fvg_bull_raw.append((i - 2, p_lo, p_hi))
        elif highs_arr[i] < lows_arr[i - 2]:
            p_lo = float(highs_arr[i])
            p_hi = float(lows_arr[i - 2])
            # Mitigated when any subsequent high re-enters the gap from below
            if not any(highs_arr[j] > p_lo for j in range(i + 1, n)):
                fvg_bear_raw.append((i - 2, p_lo, p_hi))
    result['fvg_bull'] = fvg_bull_raw[-4:]
    result['fvg_bear'] = fvg_bear_raw[-4:]

    # ── 3. BOS / CHoCH + Order Blocks (body-based, mitigated filtered) ─
    # OB tuple layout: (bar_idx, open, close, body_hi, body_lo)
    #   body_hi = max(open, close),  body_lo = min(open, close)
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
                        # Mitigated if price later traded below OB body low
                        if not any(lows_arr[k] < b_lo for k in range(j + 1, n)):
                            result['ob_bull'].append(
                                (j, float(opens_arr[j]), float(closes_arr[j]), b_hi, b_lo))
                        break
                break
            elif not trend_up and closes_arr[i] < last_pl[1]:
                result['bos_lines'].append((i, last_pl[1], 'BOS'))
                for j in range(i - 1, max(0, i - 15), -1):
                    if closes_arr[j] > opens_arr[j]:          # bullish candle → bear OB
                        b_lo = float(min(opens_arr[j], closes_arr[j]))
                        b_hi = float(max(opens_arr[j], closes_arr[j]))
                        # Mitigated if price later traded above OB body high
                        if not any(highs_arr[k] > b_hi for k in range(j + 1, n)):
                            result['ob_bear'].append(
                                (j, float(opens_arr[j]), float(closes_arr[j]), b_hi, b_lo))
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

        # Extra OBs from recent pivot extremes (fill up to 2 of each kind)
        for k in range(min(4, len(ph))):
            if len(result['ob_bull']) >= 2:
                break
            piv = ph[-(k + 1)]
            for j in range(piv[0] - 1, max(0, piv[0] - 10), -1):
                if closes_arr[j] < opens_arr[j]:
                    b_lo = float(min(opens_arr[j], closes_arr[j]))
                    b_hi = float(max(opens_arr[j], closes_arr[j]))
                    if any(lows_arr[k2] < b_lo for k2 in range(j + 1, n)):
                        break   # mitigated — skip this pivot entirely
                    candidate = (j, float(opens_arr[j]), float(closes_arr[j]), b_hi, b_lo)
                    if candidate not in result['ob_bull']:
                        result['ob_bull'].append(candidate)
                    break
        for k in range(min(4, len(pl))):
            if len(result['ob_bear']) >= 2:
                break
            piv = pl[-(k + 1)]
            for j in range(piv[0] - 1, max(0, piv[0] - 10), -1):
                if closes_arr[j] > opens_arr[j]:
                    b_lo = float(min(opens_arr[j], closes_arr[j]))
                    b_hi = float(max(opens_arr[j], closes_arr[j]))
                    if any(highs_arr[k2] > b_hi for k2 in range(j + 1, n)):
                        break   # mitigated — skip this pivot entirely
                    candidate = (j, float(opens_arr[j]), float(closes_arr[j]), b_hi, b_lo)
                    if candidate not in result['ob_bear']:
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


@st.cache_data(ttl=900, show_spinner=False)
def _main_price_chart_b64(symbol, dark_mode):
    """1y candlestick + SMC (FVG/OB/BOS/EQH/EQL/zones) + EMA144 + SMA50/100/200 + volume."""
    try:
        from matplotlib.patches import Rectangle as MplRect

        df = get_safe_historical_data(symbol, period="1y")
        if df is None or len(df) < 10:
            return None

        close_s   = df['Close']
        sma50_arr  = close_s.rolling(50).mean().values
        sma100_arr = close_s.rolling(100).mean().values
        sma200_arr = close_s.rolling(200).mean().values
        ema144_arr = close_s.ewm(span=144, adjust=False).mean().values

        #HESAPLAMALAR BİTTİ, GÖSTERİLECEK 90 GÜNE KISALTIYORUZ
        show_bars = 90
        df = df.tail(show_bars)
        sma50_arr = sma50_arr[-show_bars:]
        sma100_arr = sma100_arr[-show_bars:]
        sma200_arr = sma200_arr[-show_bars:]
        ema144_arr = ema144_arr[-show_bars:]
        # ========================================

        n      = len(df)
        opens  = df['Open'].values.astype(float)
        highs  = df['High'].values.astype(float)
        lows   = df['Low'].values.astype(float)
        closes = df['Close'].values.astype(float)

        smc = _compute_smc_elements(highs, lows, opens, closes, n_pivot=5)

        bg   = '#ffffff'
        fg   = '#1e293b'
        grid = '#f1f5f9'
        c_up   = "#228dbb"
        c_down = "#d36664"

        fig = plt.figure(figsize=(14, 5.5), facecolor=bg)
        ax1 = fig.add_axes([0.04, 0.28, 0.93, 0.67])
        ax2 = fig.add_axes([0.04, 0.04, 0.93, 0.20], sharex=ax1)
        for ax in [ax1, ax2]:
            ax.set_facecolor(bg)
            ax.tick_params(colors=fg, labelsize=7)
            for spine in ax.spines.values():
                spine.set_edgecolor(grid)

        # ── LAYER 0: Premium / Discount / Equilibrium zones ─────────
        sh = smc['swing_high']
        sl = smc['swing_low']
        if sh is not None and sl is not None and sh > sl:
            rng     = sh - sl
            disc_hi = sl + rng * 0.30
            prem_lo = sh - rng * 0.30
            eq_lo   = sl + rng * 0.45
            eq_hi   = sl + rng * 0.55
            ax1.axhspan(sl,      disc_hi, facecolor='#26a69a', alpha=0.06, zorder=0)
            ax1.axhspan(prem_lo, sh,      facecolor='#ef5350', alpha=0.06, zorder=0)
            ax1.axhspan(eq_lo,   eq_hi,   facecolor='#a78bfa', alpha=0.08, zorder=0)
            ax1.axhline(eq_lo, color='#a78bfa', lw=0.6, linestyle=':', alpha=0.45, zorder=1)
            ax1.axhline(eq_hi, color='#a78bfa', lw=0.6, linestyle=':', alpha=0.45, zorder=1)
            _zx = n + 0.6
            ax1.text(_zx, (sl + disc_hi) / 2, 'DISCOUNT', fontsize=7,
                     color='#26a69a', va='center', ha='left', alpha=0.85)
            ax1.text(_zx, (prem_lo + sh) / 2, 'PREMIUM', fontsize=7,
                     color='#ef5350', va='center', ha='left', alpha=0.85)
            ax1.text(_zx, (eq_lo + eq_hi) / 2, 'EQ', fontsize=7,
                     color='#a78bfa', va='center', ha='left', alpha=0.85)

        # ── LAYER 1: Fair Value Gaps ──────────────────────────────────
        for (x0, p_lo, p_hi) in smc['fvg_bull']:
            ax1.add_patch(MplRect((x0, p_lo), n - x0, p_hi - p_lo,
                                  facecolor='#26a69a', alpha=0.11,
                                  edgecolor='#26a69a', linewidth=0.5, zorder=1))
            ax1.text(x0 + 0.5, p_hi, 'FVG', fontsize=5,
                     color='#26a69a', va='bottom', alpha=0.85)
        for (x0, p_lo, p_hi) in smc['fvg_bear']:
            ax1.add_patch(MplRect((x0, p_lo), n - x0, p_hi - p_lo,
                                  facecolor='#ef5350', alpha=0.11,
                                  edgecolor='#ef5350', linewidth=0.5, zorder=1))
            ax1.text(x0 + 0.5, p_lo, 'FVG', fontsize=5,
                     color='#ef5350', va='top', alpha=0.85)

        # ── LAYER 2: Order Blocks ─────────────────────────────────────
        for (bi, o, c, h, l) in smc['ob_bull']:
            ax1.add_patch(MplRect((bi - 0.5, l), min(4.0, n - bi + 0.5), h - l,
                                  facecolor='#26a69a', alpha=0.17,
                                  edgecolor='#26a69a', linewidth=1.0,
                                  linestyle='--', zorder=2))
            ax1.text(bi + 0.3, h, 'OB', fontsize=5.5, color='#26a69a',
                     va='bottom', fontweight='bold', alpha=0.9)
        for (bi, o, c, h, l) in smc['ob_bear']:
            ax1.add_patch(MplRect((bi - 0.5, l), min(4.0, n - bi + 0.5), h - l,
                                  facecolor='#ef5350', alpha=0.17,
                                  edgecolor='#ef5350', linewidth=1.0,
                                  linestyle='--', zorder=2))
            ax1.text(bi + 0.3, l, 'OB', fontsize=5.5, color='#ef5350',
                     va='top', fontweight='bold', alpha=0.9)

        # ── LAYER 3: BOS / CHoCH ─────────────────────────────────────
        for (bi, price, kind) in smc['bos_lines']:
            col = '#38bdf8' if kind == 'BOS' else '#f472b6'
            ax1.axhline(price, color=col, lw=0.85, linestyle=':', alpha=0.75, zorder=3)
            ax1.text(bi, price, f' {kind}', fontsize=6, color=col,
                     va='bottom', fontweight='bold', alpha=0.9)

        # ── LAYER 3b: Equal Highs / Equal Lows ───────────────────────
        for (ba, bb, price) in smc['eqh']:
            ax1.plot([ba, bb], [price, price], color='#ffd600', lw=0.85,
                     linestyle=':', alpha=0.75, zorder=3)
            ax1.text(bb + 0.5, price, 'EQH', fontsize=5.5,
                     color='#ffd600', va='bottom', alpha=0.88)
        for (ba, bb, price) in smc['eql']:
            ax1.plot([ba, bb], [price, price], color='#fb923c', lw=0.85,
                     linestyle=':', alpha=0.75, zorder=3)
            ax1.text(bb + 0.5, price, 'EQL', fontsize=5.5,
                     color='#fb923c', va='top', alpha=0.88)

        # ── LAYER 4-5: Candlesticks ───────────────────────────────────
        for i in range(n):
            col = c_up if closes[i] >= opens[i] else c_down
            ax1.plot([i, i], [lows[i], highs[i]], color=col, linewidth=0.5, zorder=4)
            bot = min(opens[i], closes[i])
            ht  = max(abs(closes[i] - opens[i]), (highs[i] - lows[i]) * 0.005)
            ax1.add_patch(MplRect((i - 0.35, bot), 0.70, ht,
                                  facecolor=col, edgecolor='none', zorder=5))

        # ── LAYER 6: MA lines ─────────────────────────────────────────
        x = np.arange(n)
        ma_cfg = [
            (sma50_arr,  "#df460a", 'SMA 50',  0.5),
            (sma100_arr, "#0790ca", 'SMA 100', 0.5),
            (ema144_arr, '#a78bfa', 'EMA 144', 0.5),
            (sma200_arr, "#eb920c", 'SMA 200', 0.5),
        ]
        legend_handles = []
        for arr, color, label, lw in ma_cfg:
            valid = ~np.isnan(arr)
            if valid.any():
                ax1.plot(x[valid], arr[valid], color=color, lw=lw,
                         alpha=0.88, zorder=6)
                legend_handles.append(
                    plt.Line2D([0], [0], color=color, lw=1.5, label=label))

        # ── LAYER 7: Current price ────────────────────────────────────
        curr = closes[-1]
        ax1.axhline(curr, color=fg, lw=0.7, linestyle='--', alpha=0.50, zorder=7)
        price_str = f"{int(curr)}" if curr >= 1000 else f"{curr:.2f}"
        ax1.text(n + 0.3, curr, price_str, va='center', ha='left',
                 fontsize=7.5, color=fg, fontfamily='monospace', fontweight='bold')

        ax1.set_xlim(-0.8, n + 6)
        ax1.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda v, _: f"{int(v)}" if v >= 1000 else f"{v:.1f}"))
        ax1.grid(True, axis='y', color=grid, linewidth=0.5, alpha=0.5)
        ax1.set_xticklabels([])

        if legend_handles:
            ax1.legend(handles=legend_handles, loc='upper left', fontsize=6.5,
                       facecolor=bg, edgecolor=grid, labelcolor=fg,
                       framealpha=0.85, ncol=4)

        # Tarih ekseni (5 tick)
        ticks = [0, n // 4, n // 2, 3 * n // 4, n - 1]
        ax2.set_xticks(ticks)
        ax2.set_xticklabels(
            [df.index[t].strftime("%b '%y") for t in ticks],
            fontsize=6.5, color=fg)

        # Hacim
        vols  = df['Volume'].values.astype(float)
        vcols = [c_up if closes[i] >= opens[i] else c_down for i in range(n)]
        ax2.bar(x, vols, color=vcols, alpha=0.50, width=0.8)
        ax2.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda v, _: f"{v/1e6:.0f}M" if v >= 1e6 else f"{v/1e3:.0f}K"))
        ax2.grid(True, axis='y', color=grid, linewidth=0.4, alpha=0.4)
        ax2.tick_params(colors=fg, labelsize=6)

        disp = TICKER_DISPLAY_NAMES.get(symbol,
               symbol.split('.')[0].replace('=F', '').replace('-USD', ''))
        ax1.set_title(f"  {disp}  ·  {n} Bar  ·  SMC",
                      fontsize=9, color=fg, fontweight='bold', loc='left', pad=5)

        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=120, bbox_inches='tight', facecolor=bg)
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode()
    except Exception:
        return None


def _main_price_chart_plotly(symbol, dark_mode):
    """İnteraktif Plotly candlestick: SMC + EMA144 + SMA50/100/200 + volume."""
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        df_full = get_safe_historical_data(symbol, period="1y")
        if df_full is None or len(df_full) < 10:
            return None, {}

        # MA'ları tam veri üzerinde hesapla (doğru seed için)
        close_full = df_full['Close']
        sma50_full  = close_full.rolling(50).mean()
        sma100_full = close_full.rolling(100).mean()
        sma200_full = close_full.rolling(200).mean()
        ema144_full = close_full.ewm(span=144, adjust=False).mean()

        # Son 90 günü al
        df = df_full.tail(90).copy()
        sma50_arr  = sma50_full.iloc[-90:]
        sma100_arr = sma100_full.iloc[-90:]
        sma200_arr = sma200_full.iloc[-90:]
        ema144_arr = ema144_full.iloc[-90:]

        opens  = df['Open'].values.astype(float)
        highs  = df['High'].values.astype(float)
        lows   = df['Low'].values.astype(float)
        closes = df['Close'].values.astype(float)
        dates  = df.index
        n      = len(df)

        smc = _compute_smc_elements(highs, lows, opens, closes, n_pivot=5)

        bg   = '#ffffff'
        fg   = '#1e293b'
        grid = 'rgba(0,0,0,0.06)'
        paper_bg = '#ffffff'

        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            row_heights=[0.87, 0.13],
            vertical_spacing=0.0,
        )

        # Fiyat formatı: >=1000 → tam sayı, <1000 → 2 ondalık
        _pfmt = ',.0f' if closes[-1] >= 1000 else ',.2f'

        # ── Candlestick ───────────────────────────────────────────────
        fig.add_trace(go.Candlestick(
            x=dates, open=opens, high=highs, low=lows, close=closes,
            name='Fiyat',
            increasing_line_color="#45B3E6", increasing_fillcolor='#26a69a',
            decreasing_line_color="#7c3635", decreasing_fillcolor='#ef5350',
            line_width=1,
            hovertemplate=(
                "<b>%{x|%d %b %Y}</b><br>"
                f"A: %{{open:{_pfmt}}}  "
                f"Y: %{{high:{_pfmt}}}  "
                f"D: %{{low:{_pfmt}}}  "
                f"K: %{{close:{_pfmt}}}"
                "<extra></extra>"
            ),
        ), row=1, col=1)

        # ── MA çizgileri ──────────────────────────────────────────────
        def _ma_lbl(name, arr):
            v = arr.dropna()
            if v.empty: return name
            val = v.iloc[-1]
            return f"{name}: {int(val):,}" if val >= 1000 else f"{name}: {val:.2f}"

        _l50  = _ma_lbl('SMA 50',  sma50_arr)
        _l100 = _ma_lbl('SMA 100', sma100_arr)
        _l144 = _ma_lbl('EMA 144', ema144_arr)
        _l200 = _ma_lbl('SMA 200', sma200_arr)

        for arr, color, name, width in [
            (sma50_arr,  '#ef5350', _l50,  1.3),
            (sma100_arr, '#2196F3', _l100, 1.3),
            (ema144_arr, '#a78bfa', _l144, 1.5),
            (sma200_arr, '#ff7043', _l200, 1.3),
        ]:
            fig.add_trace(go.Scatter(
                x=dates, y=arr,
                name=name, line=dict(color=color, width=width),
                hoverinfo='skip',
            ), row=1, col=1)

        # ── Volume ────────────────────────────────────────────────────
        vol_colors = ["#1872E9" if closes[i] >= opens[i] else "#f70606"
                      for i in range(n)]
        fig.add_trace(go.Bar(
            x=dates, y=df['Volume'].values,
            name='Hacim', marker_color=vol_colors,
            marker_line_width=0, opacity=0.55,
            showlegend=False,
        ), row=2, col=1)

        # ── SMC Shapes ────────────────────────────────────────────────
        shapes = []
        annotations = []

        # Premium / Discount / EQ zones
        sh = smc['swing_high']
        sl = smc['swing_low']
        if sh is not None and sl is not None and sh > sl:
            rng     = sh - sl
            disc_hi = sl + rng * 0.30
            prem_lo = sh - rng * 0.30
            eq_lo   = sl + rng * 0.45
            eq_hi   = sl + rng * 0.55
            x0_zone = dates[0]
            x1_zone = dates[-1]
            for y0, y1, fc in [
                (sl,      disc_hi, 'rgba(38,166,154,0.07)'),
                (prem_lo, sh,      'rgba(239,83,80,0.07)'),
                (eq_lo,   eq_hi,   'rgba(167,139,250,0.09)'),
            ]:
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=x0_zone, x1=x1_zone, y0=y0, y1=y1,
                    fillcolor=fc, line_width=0, layer='below'))
            for lbl, ypos, clr in [
                ('DISCOUNT', (sl + disc_hi) / 2,   '#26a69a'),
                ('EQ',   (eq_lo + eq_hi) / 2,  '#a78bfa'),
                ('PREMIUM', (prem_lo + sh) / 2,    '#ef5350'),
            ]:
                annotations.append(dict(
                    x=dates[-1], y=ypos, xref='x', yref='y',
                    text=f"<b>{lbl}</b>", showarrow=False,
                    xanchor='left', font=dict(size=11, color=clr), bgcolor='rgba(0,0,0,0)'))

        # FVG
        for (xi, p_lo, p_hi) in smc['fvg_bull']:
            if xi < n:
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=dates[xi], x1=dates[-1], y0=p_lo, y1=p_hi,
                    fillcolor='rgba(38,166,154,0.10)',
                    line=dict(color='#26a69a', width=0.5, dash='dot'), layer='below'))
                annotations.append(dict(
                    x=dates[min(xi + 1, n - 1)], y=p_hi, xref='x', yref='y',
                    text='FVG', showarrow=False, yanchor='bottom',
                    font=dict(size=10, color='#26a69a', family='monospace')))
        for (xi, p_lo, p_hi) in smc['fvg_bear']:
            if xi < n:
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=dates[xi], x1=dates[-1], y0=p_lo, y1=p_hi,
                    fillcolor='rgba(239,83,80,0.10)',
                    line=dict(color='#ef5350', width=0.5, dash='dot'), layer='below'))
                annotations.append(dict(
                    x=dates[min(xi + 1, n - 1)], y=p_lo, xref='x', yref='y',
                    text='FVG', showarrow=False, yanchor='top',
                    font=dict(size=10, color='#ef5350', family='monospace')))

        # Order Blocks
        for (bi, o, c, h, l) in smc['ob_bull']:
            if bi < n:
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=dates[max(0, bi - 1)], x1=dates[min(n - 1, bi + 3)],
                    y0=l, y1=h,
                    fillcolor='rgba(38,166,154,0.18)',
                    line=dict(color='#26a69a', width=1, dash='dash')))
                annotations.append(dict(
                    x=dates[bi], y=h, xref='x', yref='y',
                    text='OB', showarrow=False, yanchor='bottom',
                    font=dict(size=10, color='#26a69a', family='monospace')))
        for (bi, o, c, h, l) in smc['ob_bear']:
            if bi < n:
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=dates[max(0, bi - 1)], x1=dates[min(n - 1, bi + 3)],
                    y0=l, y1=h,
                    fillcolor='rgba(239,83,80,0.18)',
                    line=dict(color='#ef5350', width=1, dash='dash')))
                annotations.append(dict(
                    x=dates[bi], y=l, xref='x', yref='y',
                    text='OB', showarrow=False, yanchor='top',
                    font=dict(size=10, color='#ef5350', family='monospace')))

        # BOS / CHoCH
        for (bi, price, kind) in smc['bos_lines']:
            if bi < n:
                clr = '#38bdf8' if kind == 'BOS' else '#f472b6'
                shapes.append(dict(type='line', xref='x', yref='y',
                    x0=dates[0], x1=dates[-1], y0=price, y1=price,
                    line=dict(color=clr, width=1, dash='dot')))
                annotations.append(dict(
                    x=dates[bi], y=price, xref='x', yref='y',
                    text=f'<b>{kind}</b>', showarrow=False, yanchor='bottom',
                    font=dict(size=10, color=clr, family='monospace')))

        # EQH / EQL
        for (ba, bb, price) in smc['eqh']:
            if ba < n and bb < n:
                shapes.append(dict(type='line', xref='x', yref='y',
                    x0=dates[ba], x1=dates[bb], y0=price, y1=price,
                    line=dict(color='#ffd600', width=1, dash='dot')))
                annotations.append(dict(
                    x=dates[bb], y=price, xref='x', yref='y',
                    text='EQH', showarrow=False, yanchor='bottom',
                    font=dict(size=10, color='#ffd600')))
        for (ba, bb, price) in smc['eql']:
            if ba < n and bb < n:
                shapes.append(dict(type='line', xref='x', yref='y',
                    x0=dates[ba], x1=dates[bb], y0=price, y1=price,
                    line=dict(color='#fb923c', width=1, dash='dot')))
                annotations.append(dict(
                    x=dates[bb], y=price, xref='x', yref='y',
                    text='EQL', showarrow=False, yanchor='top',
                    font=dict(size=10, color='#fb923c')))

        disp = TICKER_DISPLAY_NAMES.get(symbol,
               symbol.split('.')[0].replace('=F','').replace('-USD',''))

        # ── 1. ANLIK FİYAT ÇİZGİSİ ───────────────────────────────────
        _cur_price = closes[-1]
        _pfmt_cur  = f"{int(_cur_price):,}" if _cur_price >= 1000 else f"{_cur_price:.2f}"
        shapes.append(dict(
            type='line', xref='x', yref='y',
            x0=dates[0], x1=dates[-1],
            y0=_cur_price, y1=_cur_price,
            line=dict(color='#facc15', width=1.2, dash='dot'),
            layer='above'
        ))
        annotations.append(dict(
            x=dates[-1], y=_cur_price, xref='x', yref='y',
            text=f"<b>{_pfmt_cur}</b>",
            showarrow=False, xanchor='left',
            font=dict(size=11, color='#facc15', family='monospace'),
            bgcolor='rgba(0,0,0,0.45)', borderpad=3,
        ))

        # ── 2. POC ÇİZGİSİ (Son 90g en çok işlem gören fiyat) ─────────
        _poc_val = None
        try:
            _poc_val = calculate_volume_profile_poc(df, lookback=90, bins=30)
            if _poc_val and not np.isnan(_poc_val):
                _poc_fmt = f"{int(_poc_val):,}" if _poc_val >= 1000 else f"{_poc_val:.2f}"
                shapes.append(dict(
                    type='line', xref='x', yref='y',
                    x0=dates[0], x1=dates[-1],
                    y0=_poc_val, y1=_poc_val,
                    line=dict(color='#fbbf24', width=1.8, dash='dashdot'),
                    layer='above'
                ))
                # Etiket — sağ kenara, fiyat etiketinin hemen altına
                annotations.append(dict(
                    x=dates[-1], y=_poc_val, xref='x', yref='y',
                    text=f"<b>POC {_poc_fmt}</b>",
                    showarrow=False, xanchor='left', yanchor='middle',
                    font=dict(size=11, color='#fbbf24', family='monospace'),
                    bgcolor='rgba(0,0,0,0.55)', borderpad=3,
                ))
        except Exception:
            _poc_val = None

        # ── 3. YILLIK VWAP ────────────────────────────────────────────
        _vwap_cur = None
        try:
            _vwap_s   = (df_full['Close'] * df_full['Volume']).cumsum() / df_full['Volume'].cumsum()
            _vwap_arr = _vwap_s.iloc[-90:].values.astype(float)
            _vwap_cur = _vwap_arr[-1]
            _vwap_fmt = f"{int(_vwap_cur):,}" if _vwap_cur >= 1000 else f"{_vwap_cur:.2f}"
            fig.add_trace(go.Scatter(
                x=dates, y=_vwap_arr,
                name=f'VWAP(Y): {_vwap_fmt}',
                line=dict(color='#e879f9', width=2.0, dash='dash'),
                hoverinfo='skip',
            ), row=1, col=1)
            # Sağ kenarda VWAP etiketi
            annotations.append(dict(
                x=dates[-1], y=_vwap_cur, xref='x', yref='y',
                text=f"<b>VWAP {_vwap_fmt}</b>",
                showarrow=False, xanchor='left', yanchor='middle',
                font=dict(size=11, color='#e879f9', family='monospace'),
                bgcolor='rgba(0,0,0,0.55)', borderpad=3,
            ))
        except Exception:
            _vwap_cur = None

        # ── 4. YAPISAL ÖZET — grafik dışında st.markdown ile gösterilecek ──
        # Hesapla, fig ile birlikte döndür
        _smc_summary = {}
        try:
            if sh is not None and sl is not None and sh > sl:
                _rng     = sh - sl
                _disc_hi = sl + _rng * 0.30
                _prem_lo = sh - _rng * 0.30
                _eq_lo   = sl + _rng * 0.45
                _eq_hi   = sl + _rng * 0.55
                if _cur_price >= _prem_lo:
                    _smc_summary['zone'] = ('PREMIUM', '#ef4444', '⚠️ dikkat')
                elif _cur_price <= _disc_hi:
                    _smc_summary['zone'] = ('DISCOUNT', '#26a69a', '✅ fırsat')
                elif _eq_lo <= _cur_price <= _eq_hi:
                    _smc_summary['zone'] = ('EQ', '#a78bfa', '➖ denge')
                else:
                    _smc_summary['zone'] = ('Geçiş', '#94a3b8', '')

            if _poc_val:
                _poc_dist = (_cur_price - _poc_val) / _poc_val * 100
                _smc_summary['poc'] = (_poc_val, _poc_dist)

            if _vwap_cur:
                _vwap_dist = (_cur_price - _vwap_cur) / _vwap_cur * 100
                _smc_summary['vwap'] = (_vwap_cur, _vwap_dist)

            if smc['bos_lines']:
                _bos_s = sorted(smc['bos_lines'], key=lambda x: abs(x[1] - _cur_price))
                _smc_summary['bos'] = (_bos_s[0][2], _bos_s[0][1],
                                       (_cur_price - _bos_s[0][1]) / _bos_s[0][1] * 100)

            _all_fvgs = [(p_lo, p_hi, 'bull') for (_, p_lo, p_hi) in smc['fvg_bull']] + \
                        [(p_lo, p_hi, 'bear') for (_, p_lo, p_hi) in smc['fvg_bear']]
            if _all_fvgs:
                _fvg_s = sorted(_all_fvgs, key=lambda x: abs((x[0]+x[1])/2 - _cur_price))
                _flo, _fhi, _fdir = _fvg_s[0]
                _fmid = (_flo + _fhi) / 2
                _smc_summary['fvg'] = (_fdir, _fmid, (_cur_price - _fmid) / _fmid * 100)
        except Exception:
            pass

        fig.update_layout(
            shapes=shapes,
            annotations=annotations,
            paper_bgcolor=paper_bg,
            plot_bgcolor=bg,
            font=dict(color=fg, size=10),
            margin=dict(l=8, r=60, t=42, b=4),
            height=480,
            title=dict(
                text=(
                    f"<b>{disp}</b>  ·  SMC  ·  Son {n} Gün"
                    f"    <span style='color:#38bdf8; font-size:11px;'>▬ Fiyat</span>"
                    f"  <span style='color:#ef5350; font-size:12px;'>— {_l50}</span>"
                    f"  <span style='color:#38bdf8; font-size:12px;'>— {_l100}</span>"
                    f"  <span style='color:#a78bfa; font-size:12px;'>— {_l144}</span>"
                    f"  <span style='color:#fb923c; font-size:12px;'>— {_l200}</span>"
                ),
                font=dict(size=11, color=fg), x=0.01, xanchor='left'),
            showlegend=False,
            xaxis_rangeslider_visible=False,
            hovermode='x unified',
            dragmode='pan',
        )
        # Hafta sonu + tatil günü boşluklarını kapat
        # Kriptolar 7/24 işlem görür — hafta sonu rangebreak uygulanmaz
        import pandas as _pd
        _is_crypto = (symbol.endswith('-USD') or symbol.endswith('USDT')
                      or symbol.endswith('-TRY') or symbol in ('BTC-USD','ETH-USD','BNB-USD'))
        if _is_crypto:
            _rangebreaks = []
        else:
            _all_bdays = _pd.date_range(start=dates[0], end=dates[-1], freq='B')
            _missing   = _all_bdays.difference(dates).tolist()
            _rangebreaks = [dict(bounds=['sat', 'mon'])]
            if _missing:
                _rangebreaks.append(dict(values=_missing))

        for row in [1, 2]:
            fig.update_xaxes(
                row=row, col=1,
                gridcolor=grid, showgrid=True,
                zeroline=False,
                showspikes=True, spikecolor=fg,
                spikedash='dot', spikethickness=1,
                rangebreaks=_rangebreaks,
                tickformat="%d %b",
            )
        fig.update_yaxes(
            row=1, col=1,
            gridcolor=grid, showgrid=True,
            zeroline=False, side='right',
            tickformat=_pfmt,
        )
        fig.update_yaxes(
            row=2, col=1,
            gridcolor=grid, showgrid=True,
            zeroline=False, side='right',
            tickformat='.2s',
        )
        return fig, _smc_summary
    except Exception as _e:
        import traceback; traceback.print_exc()
        return str(_e), {}   # hata mesajını döndür, None değil


@st.cache_data(ttl=900, show_spinner=False)
def _sparkline_b64(symbol, dark_mode):
    """5-bar kapanış fiyatı sparkline — scan result kartları için."""
    try:
        df = get_safe_historical_data(symbol)
        if df is None or len(df) < 5:
            return None
        closes = df['Close'].iloc[-5:].values.astype(float)

        bg    = '#f8fafc'
        up    = closes[-1] >= closes[0]
        color = '#26a69a' if up else '#ef5350'

        fig, ax = plt.subplots(figsize=(1.8, 0.55))
        fig.patch.set_facecolor(bg)
        ax.set_facecolor(bg)
        ax.axis('off')

        x = list(range(5))
        ax.plot(x, closes, color=color, linewidth=2.0,
                solid_capstyle='round', solid_joinstyle='round')
        ax.fill_between(x, closes, closes.min() * 0.998,
                        alpha=0.18, color=color)
        # Son nokta belirgin
        ax.scatter([4], [closes[-1]], color=color, s=18, zorder=5)

        plt.tight_layout(pad=0.1)
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=90,
                    bbox_inches='tight', facecolor=bg)
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode()
    except Exception:
        return None

def _rsi_bar_html(rsi_val):
    """Yatay RSI progress bar (30=kırmızı, 50=yeşil, 70=sarı)."""
    try:
        rsi = float(rsi_val)
    except:
        return ""
    if rsi <= 30:
        bar_color, zone = '#ef5350', 'AŞIRI SATIŞ'
    elif rsi <= 55:
        bar_color, zone = '#66bb6a', 'SAĞLIKLI'
    elif rsi <= 70:
        bar_color, zone = '#ffd600', 'ISINIYOR'
    else:
        bar_color, zone = '#ff7043', 'AŞIRI ALIM'
    pct = min(max(rsi, 0), 100)
    return (
        f"<div style='margin:6px 0 2px 0;'>"
        f"<div style='display:flex;justify-content:space-between;font-size:0.65rem;color:#64748b;font-family:monospace;margin-bottom:2px;'>"
        f"<span>RSI <b style='color:{bar_color};'>{rsi:.0f}</b></span>"
        f"<span style='color:{bar_color};font-weight:700;'>{zone}</span>"
        f"</div>"
        f"<div style='background:#0d1829;border-radius:4px;height:6px;width:100%;'>"
        f"<div style='background:{bar_color};width:{pct}%;height:6px;border-radius:4px;'></div>"
        f"</div>"
        f"<div style='display:flex;justify-content:space-between;font-size:0.6rem;color:#94a3b8;margin-top:1px;'>"
        f"<span>0</span><span>30</span><span>50</span><span>70</span><span>100</span>"
        f"</div>"
        f"</div>"
    )


def render_sentiment_card(sent):
    if not sent: return
    display_ticker = get_display_name(st.session_state.ticker)
    
    score = sent['total']
    # Renk ve İkon Belirleme
    if score >= 70:
        color = "#4ade80"; icon = "🔥"; status = "GÜÇLÜ BOĞA"; bg_tone = "rgba(16,185,129,0.07)"; border_tone = "rgba(16,185,129,0.25)"
    elif score >= 50:
        color = "#fbbf24"; icon = "↔️"; status = "NÖTR / POZİTİF"; bg_tone = "rgba(245,158,11,0.07)"; border_tone = "rgba(245,158,11,0.25)"
    elif score >= 30:
        color = "#f87171"; icon = "🐻"; status = "ZAYIF / AYI"; bg_tone = "rgba(248,113,113,0.07)"; border_tone = "rgba(248,113,113,0.25)"
    else:
        color = "#f87171"; icon = "❄️"; status = "ÇÖKÜŞ"; bg_tone = "rgba(248,113,113,0.07)"; border_tone = "rgba(248,113,113,0.25)"
    
    # Etiketler — ağırlıklarla senkron
    _idx = sent.get('is_index', False)
    lbl_str  = '10p'
    lbl_tr   = '25p' if _idx else '20p'
    lbl_vol  = '25p' if _idx else '20p'
    lbl_mom  = '25p' if _idx else '20p'
    lbl_vola = '15p' if _idx else '10p'
    lbl_rs   = 'Devre Dışı' if _idx else '20p'

    # --- KART OLUŞTURUCU (SOLA YASLI - HATA VERMEZ) ---
    def make_card(num, title, score_lbl, val, desc, emo):
        # DİKKAT: Aşağıdaki HTML kodları bilerek en sola yaslanmıştır.
        return f"""<div style="border:1px solid #1e3a5f; border-radius: 8px; margin-bottom: 8px; background:#0d1829; box-shadow: 0 1px 2px rgba(0,0,0,0.02);">
<div style="background:#0d1829; padding: 8px 12px; border-bottom: 1px solid #e2e8f0; display: flex; justify-content: space-between; align-items: center;">
<div style="display:flex; align-items:center; gap:6px;">
<span style="background:{color}; color:white; width:20px; height:20px; border-radius:50%; display:flex; justify-content:center; align-items:center; font-size:0.7rem; font-weight:bold;">{num}</span>
<span style="font-weight: 700; color:#94a3b8; font-size: 0.8rem;">{title} <span style="color:#94a3b8; font-weight:400; font-size:0.7rem;">({score_lbl})</span></span>
</div>
<div style="font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; font-weight: 700; color:#f1f5f9;">{val}</div>
</div>
<div style="padding: 10px; font-size: 0.85rem; color:#38bdf8; line-height: 1.4; background:#0d1829;">
<span style="color:{color}; font-size:1rem; float:left; margin-right:6px; line-height:1;">{emo}</span>
{desc}
</div>
</div>"""

    # --- KARTLARI OLUŞTUR ---
    cards_html = ""
    cards_html += make_card("1", "YAPI", lbl_str, sent['str'], "Piyasa yapısını HH+HL zinciriyle ölçer. Son 40 barda 2+ Higher High ve 2+ Higher Low zinciri varsa güçlü yapı (10). 1 HH+1 HL varsa gelişen yapı (5).", "🏗️")
    cards_html += make_card("2", "TREND", lbl_tr, sent['tr'], "Ortalamalara bakar. Hisse fiyatı SMA200 üstünde (8). EMA20 üstünde (8). Kısa vadeli ortalama, orta vadeli ortalamanın üzerinde, yani EMA20 > SMA50 (4)", "📈")
    cards_html += make_card("3", "HACİM", lbl_vol, sent['vol'], "Hacmin 20G ortalamaya oranını ve OBV'yi denetler. Bugünün hacmi son 20G ort.üstünde (12). OBV, EMA(20) üstünde (5). OBV eğimi pozitif (3)", "🌊")
    _rsi_raw = sent.get('raw_rsi', 50)
    _rsi_bar = _rsi_bar_html(_rsi_raw)
    cards_html += make_card("4", "MOMENTUM", lbl_mom, sent['mom'], f"RSI, MACD ve divergence ile itki gücünü ölçer. 50 üstü RSI (5). RSI ivmesi artıyor (5). MACD sinyal çizgisi üstünde (5). Bullish RSI/OBV divergence tespiti (+5 bonus her biri){_rsi_bar}", "🚀")
    cards_html += make_card("5", "HACİM KALİTESİ", lbl_vola, sent['vola'], "Para akışının yönünü ölçer. Son 20 günde yükseliş günlerinin ortalama hacmi, düşüş günlerinden fazlaysa kurumlar topluyor (8). Son 10 günde hacmin %60'ı yükseliş günlerine düşüyorsa birikim ağırlıklı (7).", "💰")
    cards_html += make_card("6", "GÜÇ", lbl_rs, sent['rs'], "Hissenin Endekse göre relatif gücünü (RS) ölçer. Mansfield RS göstergesi 0'ın üzerinde (5). RS trendi son 5 güne göre yükselişte (5). Endeks düşerken hisse artıda (Alpha) (5)", "💪")

    # --- ANA HTML (SOLA YASLI) ---
    final_html = f"""<div class="info-card" style="border-top: 3px solid {color}; background-color:#0d1829; padding-bottom: 2px;">
<div class="info-header" style="color:#38bdf8; display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
<span>💼 KURUMSAL İLGİ: {display_ticker}</span>
<span style="font-family:'JetBrains Mono'; font-weight:800; font-size:1.1rem; color:{color}; background:{color}15; padding:2px 8px; border-radius:6px;">{score}/100</span>
</div>
<div style="background:{bg_tone}; border:1px solid {border_tone}; border-radius:6px; padding:8px; text-align:center; margin-bottom:12px;">
<div style="font-weight:800; color:{color}; font-size:0.9rem; letter-spacing:0.5px;">{icon} {status}</div>
<div style="font-size:0.65rem; color:#64748b; margin-top:2px; font-family: monospace;">{sent['bar']}</div>
</div>
<div style="display:flex; flex-direction:column; gap:2px;">
{cards_html}
</div>
</div>"""
    
    st.markdown(final_html, unsafe_allow_html=True)

def render_deep_xray_card(xray):
    if not xray: return
    
    display_ticker = get_display_name(st.session_state.ticker)
    
    html_icerik = f"""
    <div class="info-card">
        <div class="info-header">🔍 Derin Teknik Röntgen: {display_ticker}</div>
        
        <div class="info-row">
            <div class="label-long">1. Momentum:</div>
            <div class="info-val">{xray['mom_rsi']} | {xray['mom_macd']}</div>
        </div>
        <div class="edu-note">RSI 50 üstü ve MACD pozitif bölgedeyse ivme alıcıların kontrolündedir. RSI 50 üstünde? MACD 0'dan büyük?</div>

        <div class="info-row">
            <div class="label-long">2. Hacim Akışı:</div>
            <div class="info-val">{xray['vol_obv']}</div>
        </div>
        <div class="edu-note">Para girişinin (OBV) fiyat hareketini destekleyip desteklemediğini ölçer. OBV, 5 günlük ortalamasının üzerinde?</div>

        <div class="info-row">
            <div class="label-long">3. Trend Sağlığı:</div>
            <div class="info-val">{xray['tr_ema']} | {xray['tr_adx']}</div>
        </div>
        <div class="edu-note">Fiyatın EMA50 ve EMA200 üzerindeki kalıcılığını ve trendin gücünü denetler. 1. EMA50 EMA200'ü yukarı kesmiş? 2. Zaten üstünde?</div>

        <div class="info-row">
            <div class="label-long">4. Volatilite:</div>
            <div class="info-val">{xray['vola_bb']}</div>
        </div>
        <div class="edu-note">Bollinger Bantlarındaki daralma, yakında bir patlama olabileceğini gösterir. Fiyat üst bandı yukarı kırdı?</div>

        <div class="info-row">
            <div class="label-long">5. Piyasa Yapısı:</div>
            <div class="info-val">{xray['str_bos']}</div>
        </div>
        <div class="edu-note">Kritik direnç seviyelerinin kalıcı olarak aşılması (BOS) yükselişin devamı için şarttır. Fiyat son 20 günün en yüksek seviyesini aştı?</div>
    </div>
    """.replace("\n", "")
    
    st.markdown(html_icerik, unsafe_allow_html=True)

# ==============================================================================
# BÖLÜM 29 — DETAY KARTI VE PANEL RENDER SİSTEMİ
# Tekli hisse için tüm panel bileşenleri: Sentiment, Deep X-Ray,
# Smart Volume, Price Action, Smart Money, ICT Sertifikasyon,
# ICT Derin Panel, Seviye Kartı, Minervini Paneli.
# ==============================================================================   
def render_detail_card_advanced(ticker):
    ACIKLAMALAR = {
        "Squeeze": "🚀 Squeeze: Bollinger Bant genişliği son 60 günün en dar aralığında (Patlama Hazır)",
        "Trend": "⚡ Trend: EMA5 > EMA20 üzerinde (Yükseliyor)",
        "MACD": "🟢 MACD: Histogram bir önceki günden yüksek (Momentum Artışı Var)",
        "Hacim": "🔊 Hacim: Son 5 günlük hacim ortalama hacmin %20 üzerinde",
        "Breakout": "🔨 Breakout: Fiyat son 20 gün zirvesinin %98 veya üzerinde",
        "RSI Güçlü": "⚓ RSI Güçlü: 30-65 arasında ve artışta",
        "Hacim Patlaması": "💥 Hacim son 20 gün ortalamanın %30 üzerinde seyrediyor",
        "RS (S&P500)": "💪 Hisse, Endeksten daha güçlü",
        "Boğa Trendi": "🐂 Boğa Trendi: Fiyat Üç Ortalamanın da (SMA50 > SMA100 > SMA200) üzerinde",
        "60G Zirve": "⛰️ Zirve: Fiyat son 60 günün tepesine %97 yakınlıkta",
        "RSI Bölgesi": "🎯 RSI Uygun: Pullback için uygun (40-55 arası)",
        "Ichimoku": "☁️ Ichimoku: Fiyat Bulutun Üzerinde (Trend Pozitif)",
        "RS": "💪 Relatif Güç (RS)",
        "Setup": "🛠️ Setup Durumu",
        "ADX Durumu": "💪 ADX Trend Gücü"
    }

    display_ticker = get_display_name(ticker)
    dt = get_tech_card_data(ticker)
    info = fetch_stock_info(ticker)
    
    price_val = f"{info['price']:.2f}" if info else "Veri Yok"
    ma_vals = f"SMA50: {dt['sma50']:.0f} | SMA200: {dt['sma200']:.0f}" if dt else ""
    stop_vals = f"{dt['stop_level']:.2f} (Risk: %{dt['risk_pct']:.1f})" if dt else ""

    # RADAR 1 VERİSİ
    r1_res = {}; r1_score = 0
    if st.session_state.scan_data is not None:
        row = st.session_state.scan_data[st.session_state.scan_data["Sembol"] == ticker]
        if not row.empty and "Detaylar" in row.columns: r1_res = row.iloc[0]["Detaylar"]; r1_score = row.iloc[0]["Skor"]
    if not r1_res:
        temp_df = analyze_market_intelligence([ticker], st.session_state.get('category', 'S&P 500'))
        if not temp_df.empty and "Detaylar" in temp_df.columns: r1_res = temp_df.iloc[0]["Detaylar"]; r1_score = temp_df.iloc[0]["Skor"]

    # RADAR 2 VERİSİ
    r2_res = {}; r2_score = 0
    if st.session_state.radar2_data is not None:
        if "Sembol" not in st.session_state.radar2_data.columns:
            st.session_state.radar2_data = st.session_state.radar2_data.reset_index()
            st.session_state.radar2_data.rename(columns={'index': 'Sembol', 'Symbol': 'Sembol', 'Ticker': 'Sembol'}, inplace=True)
        row = st.session_state.radar2_data[st.session_state.radar2_data["Sembol"] == ticker]
        if not row.empty and "Detaylar" in row.columns: r2_res = row.iloc[0]["Detaylar"]; r2_score = row.iloc[0]["Skor"]
    if not r2_res:
        temp_df2 = radar2_scan([ticker])
        if not temp_df2.empty and "Detaylar" in temp_df2.columns: r2_res = temp_df2.iloc[0]["Detaylar"]; r2_score = temp_df2.iloc[0]["Skor"]

    r1_suffix = ""
    if r1_score < 2: r1_suffix = " <span style='color:#f87171; font-weight:500; background:rgba(248,113,113,0.08); padding:1px 4px; border-radius:3px; margin-left:5px; font-size:0.7rem;'>(⛔ RİSKLİ)</span>"
    elif r1_score > 5: r1_suffix = " <span style='color:#16a34a; font-weight:500; background:rgba(16,185,129,0.07); padding:1px 4px; border-radius:3px; margin-left:5px; font-size:0.7rem;'>(🚀 GÜÇLÜ)</span>"

    def get_icon(val): return "✅" if val else "❌"

    # RADAR 1 HTML (FİLTRELİ)
    r1_html = ""
    for k, v in r1_res.items():
        if k in ACIKLAMALAR: 
            text = ACIKLAMALAR.get(k, k); is_valid = v
            if isinstance(v, (tuple, list)): 
                is_valid = v[0]; val_num = v[1]
                if k == "RSI Güçlü":
                    if is_valid:
                        # 30-65 arası ve yükseliyorsa
                        text = f"⚓ RSI Güçlü/İvmeli: ({int(val_num)})"
                    else:
                        # Eğer çarpı yemişse sebebini yazalım
                        if val_num >= 65:
                            text = f"🔥 RSI Şişkin (Riskli Olabilir): ({int(val_num)})"
                        elif val_num <= 30:
                            text = f"❄️ RSI Zayıf (Dipte): ({int(val_num)})"
                        else:
                            text = f"📉 RSI İvme Kaybı: ({int(val_num)})"
                elif k == "ADX Durumu": text = f"💪 ADX Güçlü: {int(val_num)}" if is_valid else f"⚠️ ADX Zayıf: {int(val_num)}"
            r1_html += f"<div class='tech-item' style='margin-bottom:2px;'>{get_icon(is_valid)} <span style='margin-left:4px;'>{text}</span></div>"

    # RADAR 2 HTML (FİLTRELİ ve DÜZELTİLMİŞ)
    r2_html = ""
    for k, v in r2_res.items():
        if k in ACIKLAMALAR:
            text = ACIKLAMALAR.get(k, k); is_valid = v
            
            if isinstance(v, (tuple, list)): 
                is_valid = v[0]; val_num = v[1]
                if k == "RSI Bölgesi": 
                    if is_valid:
                        text = f"🎯 RSI Uygun: ({int(val_num)})"
                    else:
                        # Eğer geçerli değilse nedenini yazalım
                        if val_num > 65:
                            text = f"🔥 RSI Şişkin (Riskli Olabilir): ({int(val_num)})"
                        else:
                            text = f"❄️ RSI Zayıf: ({int(val_num)})"

            # Ichimoku Özel Kontrolü (Gerekirse)
            if k == "Ichimoku":
                # Eğer özel bir şey yapmak istersen buraya, yoksa standart metin gelir
                pass 

            r2_html += f"<div class='tech-item' style='margin-bottom:2px;'>{get_icon(is_valid)} <span style='margin-left:4px;'>{text}</span></div>"

    full_html = f"""
    <div class="info-card">
        <div class="info-header">📋 Gelişmiş Teknik Kart: {display_ticker}</div>
        <div style="display:flex; justify-content:space-between; margin-bottom:8px; border-bottom:1px solid #1e3a5f; padding-bottom:4px;">
            <div style="font-size:0.8rem; font-weight:700; color:#38bdf8;">Fiyat: {price_val}</div>
            <div style="font-size:0.75rem; color:#64748B;">{ma_vals}</div>
        </div>
        <div style="font-size:0.8rem; color:#f87171; margin-bottom:8px;">🛑 Stop: {stop_vals}</div>
        <div style="background:rgba(56,189,248,0.05); padding:4px; border-radius:4px; margin-bottom:4px;">
            <div style="font-weight:700; color:#38bdf8; font-size:0.75rem; margin-bottom:4px;">🧠 RADAR 1 (3-12 gün): Momentum ve Hacim - SKOR: {r1_score}/7{r1_suffix}</div>
            <div class="tech-grid" style="font-size:0.75rem;">{r1_html}</div>
        </div>
        <div style="background:rgba(16,185,129,0.07); padding:4px; border-radius:4px;">
            <div style="font-weight:700; color:#4ade80; font-size:0.75rem; margin-bottom:4px;">🚀 RADAR 2 (10-50 gün): Trend Takibi - SKOR: {r2_score}/7</div>
            <div class="tech-grid" style="font-size:0.75rem;">{r2_html}</div>
        </div>
    </div>
    """
    st.markdown(full_html.replace("\n", " "), unsafe_allow_html=True)

def render_synthetic_sentiment_panel(data):
    if data is None or data.empty: return
    display_ticker = get_display_name(st.session_state.ticker)
    
    info = fetch_stock_info(st.session_state.ticker)
    current_price = info.get('price', 0) if info else 0
    
    header_color = "#3b82f6" 
    st.markdown(f"""
    <div class="info-card" style="border-top: 3px solid {header_color}; margin-bottom:15px;">
        <div class="info-header" style="color:#38bdf8; display:flex; justify-content:space-between; align-items:center;">
            <span style="font-size:1.1rem;">🌊 Para Akış İvmesi & Fiyat Dengesi: {display_ticker}</span>
            <span style="font-family:'JetBrains Mono'; font-weight:700; color:#f1f5f9; background:rgba(56,189,248,0.05); padding:2px 8px; border-radius:4px; font-size:1.25rem;">
                {current_price:.2f}
            </span>
        </div>
    </div>
    """, unsafe_allow_html=True)
    _price_fmt = ',.0f' if data['Price'].max() >= 1000 else '.2f'
    c1, c2 = st.columns([1, 1]); x_axis = alt.X('Date_Str', axis=alt.Axis(title=None, labelAngle=-45, labelOverlap=False, labelColor="#94a3b8"), sort=None)
    with c1:
        base = alt.Chart(data).encode(x=x_axis)
        color_condition = alt.condition(
            alt.datum.MF_Smooth > 0,
            alt.value("#5B84C4"),
            alt.value("#ef4444")
        )
        _tt1 = [alt.Tooltip('Date_Str:N', title='Tarih'),
                alt.Tooltip('Price:Q',    title='Fiyat',    format=_price_fmt),
                alt.Tooltip('MF_Smooth:Q',title='Para Akışı', format='.2f')]
        bars = base.mark_bar(size=12, opacity=0.9).encode(
            y=alt.Y('MF_Smooth:Q', axis=alt.Axis(title='Para Akışı (Güç)', labels=False, titleColor='#64748b')),
            color=color_condition,
            tooltip=_tt1
        )
        price_line = base.mark_line(color='#bfdbfe', strokeWidth=2).encode(y=alt.Y('Price:Q', scale=alt.Scale(zero=False), axis=alt.Axis(title='Fiyat', titleColor='#94a3b8')))
        st.altair_chart(alt.layer(bars, price_line).resolve_scale(y='independent').properties(height=280, title=alt.TitleParams("Momentum", fontSize=14, color="#38bdf8")), use_container_width=True)
    with c2:
        _ymin2 = min(data['STP'].min(), data['Price'].min()) * 0.999
        _ymax2 = max(data['STP'].max(), data['Price'].max()) * 1.001
        _ys2   = alt.Scale(zero=False, domain=[_ymin2, _ymax2])
        base2  = alt.Chart(data).encode(x=x_axis)
        _hover2 = alt.selection_point(fields=['Date_Str'], nearest=True, on='mouseover', empty=False)
        _tt2 = [alt.Tooltip('Date_Str:N', title='Tarih'),
                alt.Tooltip('Price:Q',    title='Fiyat', format='.2f'),
                alt.Tooltip('STP:Q',      title='STP',   format='.2f')]
        area       = base2.mark_area(opacity=0.15, color='gray').encode(y=alt.Y('STP:Q', scale=_ys2), y2=alt.Y2('Price:Q'))
        line_stp   = base2.mark_line(color='#fbbf24', strokeWidth=3).encode(y=alt.Y('STP:Q', scale=_ys2, axis=alt.Axis(title='Fiyat', titleColor='#94a3b8')))
        line_price = base2.mark_line(color='#bfdbfe', strokeWidth=2).encode(y=alt.Y('Price:Q', scale=_ys2))
        _vrule2    = base2.mark_rule(color='#94a3b8', strokeWidth=1, strokeDash=[4,3]).encode(
                        opacity=alt.condition(_hover2, alt.value(0.7), alt.value(0)))
        _dot_p2    = base2.mark_point(size=70, color='#bfdbfe', filled=True).encode(
                        y=alt.Y('Price:Q', scale=_ys2), opacity=alt.condition(_hover2, alt.value(1), alt.value(0)))
        _dot_s2    = base2.mark_point(size=70, color='#fbbf24', filled=True).encode(
                        y=alt.Y('STP:Q', scale=_ys2), opacity=alt.condition(_hover2, alt.value(1), alt.value(0)))
        _overlay2  = base2.mark_point(opacity=0, size=200).encode(
                        y=alt.Y('Price:Q', scale=_ys2), tooltip=_tt2).add_params(_hover2)
        st.altair_chart(alt.layer(area, line_stp, line_price, _vrule2, _dot_p2, _dot_s2, _overlay2).properties(
            height=280, title=alt.TitleParams("Sentiment Analizi: Mavi (Fiyat) Sarıyı (STP-DEMA6) Yukarı Keserse AL, aşağıya keserse SAT", fontSize=14, color="#38bdf8")), use_container_width=True)

def render_smart_volume_panel(ticker):
    """SMART MONEY HACİM ANALİZİ — 4 tile kompakt panel. ICT altında gösterilir."""
    pa = calculate_price_action_dna(ticker)
    if not pa or "smart_volume" not in pa:
        return
    sv          = pa["smart_volume"]
    va_pos      = sv.get("va_pos", "İÇİNDE")
    cum5        = sv.get("cum_delta_5", 0)
    cum5_pct    = sv.get("cum_delta_pct", 0)
    naked_txt   = sv.get("naked_poc_txt", "")
    poc         = sv.get("poc", 0)
    vah         = sv.get("vah", 0)
    val         = sv.get("val", 0)
    delta_val        = sv.get("delta", 0)
    delta_yuzde      = sv.get("delta_yuzde", 0)
    _vol_data_missing = sv.get("vol_data_missing", False)
    rvol        = sv.get("rvol", 1.0)
    is_index    = ticker.startswith(("XU", "XB", "XT", "XY", "^"))

    # ── XU100 + HAFTA SONU FİX ────────────────────────────────────────────────
    # XU100 için Yahoo bazen Cumartesi/Pazar'a 0-hacimli bar koyuyor → rvol=0
    # ve "Endeks için veri sağlanamadı" yazıyor. Hafta sonu açılmışsa son geçerli
    # işlem gününün (Cuma) verisini delta + rvol olarak göster.
    _is_xu100   = ticker == "XU100.IS" or ticker == "XU100" or ticker.upper().startswith("XU100")
    _is_weekend = datetime.now(_TZ_ISTANBUL).weekday() >= 5
    if _is_xu100 and _is_weekend:
        try:
            _df_we = get_safe_historical_data(ticker, period="3mo")
            if _df_we is not None and len(_df_we) >= 22:
                # Son volume>0 olan bar'ı bul (yfinance Cumartesi'ye 0 koyabilir)
                _last_valid_back = 0
                for _b in range(1, min(10, len(_df_we))):
                    if float(_df_we['Volume'].iloc[-_b]) > 0:
                        _last_valid_back = _b
                        break
                if _last_valid_back > 0:
                    _df_we_d = calculate_volume_delta(_df_we)
                    _last_v   = float(_df_we_d['Volume'].iloc[-_last_valid_back])
                    _last_dv  = float(_df_we_d['Volume_Delta'].iloc[-_last_valid_back])
                    if _last_v > 0:
                        delta_val   = _last_dv
                        delta_yuzde = abs((_last_dv / _last_v) * 100)
                    # 20G hacim ortalaması — yalnızca volume>0 olanlardan
                    _vol_hist = _df_we['Volume'].iloc[max(0, len(_df_we) - _last_valid_back - 20):len(_df_we) - _last_valid_back]
                    _vol_valid = _vol_hist[_vol_hist > 0]
                    if len(_vol_valid) > 0:
                        _avg_v = float(_vol_valid.mean())
                        if _avg_v > 0:
                            rvol = _last_v / _avg_v
        except Exception:
            pass
    # ──────────────────────────────────────────────────────────────────────────

    # Mevcut fiyat + display ticker — ICT paneli ile aynı kaynak (fetch_stock_info)
    display_ticker = get_display_name(ticker)
    try:
        _info = fetch_stock_info(ticker)
        _cp   = float(_info.get('price', 0)) if _info else 0
        if _cp == 0:
            raise ValueError("fiyat sıfır")
    except Exception:
        try:
            _df2 = get_safe_historical_data(ticker)
            _cp  = float(_df2['Close'].iloc[-1]) if _df2 is not None and len(_df2) > 0 else poc
        except Exception:
            _cp = poc

    # Fiyat formatı: 1000+ tam sayı, diğerleri 2 ondalık
    _cp_str = f"{int(_cp):,}" if _cp >= 1000 else f"{_cp:.2f}"

    # Bar fill: 0-100 ölçeği (her yarı bar kendi 100%'ü)
    d1_fill  = min(abs(delta_yuzde), 100) if not is_index else (65 if delta_val != 0 else 0)
    cum_fill = min(abs(cum5_pct), 100)

    dark = True

    # ── Renk paleti ──────────────────────────────────────────────
    if dark:
        if   "ÜSTÜNDE" in va_pos and cum5 > 0:  bc="#10b981"; bg="rgba(16,185,129,0.06)"
        elif "ÜSTÜNDE" in va_pos:               bc="#f59e0b"; bg="rgba(245,158,11,0.06)"
        elif "ALTINDA" in va_pos and cum5 <= 0: bc="#f87171"; bg="rgba(248,113,113,0.06)"
        else:                                   bc="#10b981"; bg="rgba(16,185,129,0.06)"
        text_main  = "#f1f5f9"; text_sub = "#cbd5e1"; text_muted = "#94a3b8"
        divider    = "rgba(255,255,255,0.10)"; track_bg = "rgba(255,255,255,0.12)"
        naked_bg   = "rgba(251,191,36,0.12)"; naked_bc = "#fbbf24"; naked_tc = "#fde68a"; naked_sub = "#cbd5e1"
    else:
        if   "ÜSTÜNDE" in va_pos and cum5 > 0:  bc="#15803d"; bg="#f0fdf4"
        elif "ÜSTÜNDE" in va_pos:               bc="#b45309"; bg="#fffbeb"
        elif "ALTINDA" in va_pos and cum5 <= 0: bc="#f87171"; bg="#fef2f2"
        else:                                   bc="#15803d"; bg="#f0fdf4"
        text_main  = "#111827"; text_sub = "#1e3a8a"; text_muted = "#3b4a6b"
        divider    = "#d1d5db"; track_bg = "#d1d5db"
        naked_bg   = "#fefce8"; naked_bc = "#ca8a04"; naked_tc = "#78350f"; naked_sub = "#1e3a8a"

    # ── TILE 1: Fiyat Konumu ─────────────────────────────────────
    if "ÜSTÜNDE" in va_pos:
        t1_ic = "#10b981" if dark else "#15803d"
        t1_bb = "rgba(16,185,129,0.13)" if dark else "#dcfce7"
        t1_icon = "&#9650;"; t1_label = "DEĞER BÖLGESİ (VA) ÜSTÜNDE"
        t1_sub  = "Kurumların yoğun işlem yaptığı bölgenin üstündeyiz. Güçlü pozitif sinyal."
    elif "ALTINDA" in va_pos:
        t1_ic = "#f87171"
        t1_bb = "rgba(248,113,113,0.13)" if dark else "#fee2e2"
        t1_icon = "&#9660;"; t1_label = "DEĞER BÖLGESİ (VA) ALTINDA"
        t1_sub  = "Kurumların işlem bölgesinin altındayız. Satış baskısı sürüyor."
    else:
        t1_ic = "#f59e0b" if dark else "#92400e"
        t1_bb = "rgba(245,158,11,0.13)" if dark else "#fef9c3"
        t1_icon = "&#9679;"; t1_label = "DENGE NOKTASI (VA) İÇİNDE"
        t1_sub  = "Kurumların en çok işlem yaptığı bölgenin tam içindeyiz. Karar noktası."

    # ── TILE 2: POC ──────────────────────────────────────────────
    t2_ic = "#fbbf24" if dark else "#92400e"
    t2_bb = "rgba(251,191,36,0.10)" if dark else "#fef3c7"
    poc_diff = (_cp - poc) / poc * 100 if poc > 0 else 0
    if   poc_diff >  2: poc_vs = f"Fiyat POC'un %{abs(poc_diff):.1f} üstünde"
    elif poc_diff < -2: poc_vs = f"Fiyat POC'un %{abs(poc_diff):.1f} altında"
    else:               poc_vs = "Fiyat POC'u test ediyor"

    # ── TILE 3: Bugünkü Delta ─────────────────────────────────────
    if delta_val > 0:
        t3_ic = "#10b981" if dark else "#15803d"
        t3_pct = f"+%{delta_yuzde:.0f}" if not is_index else "+Alım"
        t3_lbl = "Alıcılar Baskın"
        t3_sub = "Kapanışa doğru alıcılar daha agresif davrandı." if delta_yuzde >= 60 else "Hafif alım ağırlığı var, güçlü değil."
        t3_pos = True
    elif delta_val < 0:
        t3_ic = "#ef4444" if dark else "#f87171"
        t3_pct = f"-%{delta_yuzde:.0f}" if not is_index else "-Satış"
        t3_lbl = "Satıcılar Baskın"
        t3_sub = "Kapanışa doğru satıcılar daha agresif davrandı." if delta_yuzde >= 60 else "Hafif satış ağırlığı var, güçlü değil."
        t3_pos = False
    else:
        t3_ic = "#94a3b8" if dark else "#6b7280"
        # Eğer hacim de sıfırsa (volume=0) delta anlamsız — bağlama göre mesaj ver
        if rvol < 0.05:
            try:
                _now_hm2      = datetime.now(_TZ_ISTANBUL).hour * 100 + datetime.now(_TZ_ISTANBUL).minute
                _bist_open2   = 955 <= _now_hm2 <= 1820
                _us_open2     = 1630 <= _now_hm2 <= 2300
                _bist_tick2   = ".IS" in ticker or ticker.startswith("XU")
                _in_sess2     = _bist_open2 if _bist_tick2 else (_bist_open2 or _us_open2)
            except:
                _in_sess2 = False
            _bist_post2 = (not _in_sess2 and _bist_tick2 and 1820 <= _now_hm2 <= 2000)
            if _in_sess2 and is_index:
                t3_pct = "—"; t3_lbl = "Endeks Delta"
                t3_sub = "Seans içinde endeks delta verisi alınamıyor."
            elif _in_sess2:
                t3_pct = "—"; t3_lbl = "Veri Bekleniyor"
                t3_sub = "Hacim verisi 0 — delta hesaplanamıyor. Seans ilerledikçe güncellenecek."
            elif is_index:
                t3_pct = "—"; t3_lbl = "Endeks Delta"
                t3_sub = "Endeks icin delta verisi saglanamadi."
            else:
                t3_pct = "—"; t3_lbl = "Veri Yok"
                t3_sub = "Hacim verisi alınamadı — delta hesaplanamıyor."
        else:
            t3_pct = "%0"; t3_lbl = "Alım = Satım (Denge)"; t3_sub = "Bugün alıcı ve satıcı dengede. Yön yok."
        t3_pos = None

    # ── TILE 4: 20G Ortalamaya Göre Hacim (RVOL) ─────────────────
    if _vol_data_missing:  # Yahoo Finance bu dönem için hacim verisi sağlamıyor
        t4_ic   = "#94a3b8" if dark else "#9ca3af"
        t4_pct  = "Veri Eksik"
        t4_lbl  = "Hesaplanamadı"
        t4_sub  = "Bu dönem için kaynak hacim verisi yok."
        t4_pos  = None; rvol_fill = 0
    elif rvol < 0.05:  # veri yok / sıfır hacim
        t4_ic  = "#94a3b8" if dark else "#6b7280"
        # Seans içindeyse daha bilgilendirici mesaj ver
        try:
            _now_hm = datetime.now(_TZ_ISTANBUL).hour * 100 + datetime.now(_TZ_ISTANBUL).minute
            _is_bist_hours  = 955 <= _now_hm <= 1820
            _is_us_hours    = 1630 <= _now_hm <= 2300
            _is_bist_ticker = ".IS" in ticker or ticker.startswith("XU")
            # BIST tickerları için ABD saatini sayma — sadece kendi seansı
            _in_session = _is_bist_hours if _is_bist_ticker else (_is_bist_hours or _is_us_hours)
        except:
            _in_session = False
        # Kapanış sonrası endeks gecikmesi: BIST 18:20 kapanır, Yahoo ~30-90dk sonra yazar
        _is_bist_post_close = (not _in_session and _is_bist_ticker and 1820 <= _now_hm <= 2000)
        if _in_session and is_index:
            t4_pct = "—"; t4_lbl = "Endeks Hacmi"
            t4_sub = "Seans içinde endeks hacmi alınamıyor. Kapanıştan sonra güncellenir."
        elif _in_session:
            t4_pct = "—"; t4_lbl = "Güncelleniyor…"
            t4_sub = "Gün içi hacim verisi henüz alınamadı. Birkaç dakika içinde yenilenir."
        elif is_index:
            t4_pct = "—"; t4_lbl = "Endeks Hacmi"
            t4_sub = "Endeks icin hacim verisi saglanamadi."
        else:
            t4_pct = "—"; t4_lbl = "Veri Yok"
            t4_sub = "Hacim verisi alınamadı."
        t4_pos = None; rvol_fill = 0
    elif rvol >= 2.0:
        t4_ic  = "#10b981" if dark else "#15803d"
        _rvol_pct = (rvol - 1.0) * 100
        t4_pct = f"+%{_rvol_pct:.0f}"; t4_lbl = "Yüksek Hacim"
        t4_sub = f"Bugünün hacmi 20G ortalamanın %{_rvol_pct:.0f} üzerinde — kurumsal aktivite var."
        t4_pos = True; rvol_fill = min((rvol - 1.0) / 2.0 * 100, 100)
    elif rvol >= 0.8:
        t4_ic  = "#f59e0b" if dark else "#92400e"
        _rvol_pct = (rvol - 1.0) * 100
        _sign = "+%" if _rvol_pct >= 0 else "-%"
        t4_pct = f"{_sign}{abs(_rvol_pct):.0f}"
        t4_lbl = "Normale Yakın Hacim"
        _dir = "üzerinde" if _rvol_pct >= 0 else "altında"
        t4_sub = f"Bugünün hacmi 20G ortalamanın %{abs(_rvol_pct):.0f} {_dir} — bekleme modu."
        t4_pos = None; rvol_fill = 0
    else:
        t4_ic  = "#ef4444" if dark else "#f87171"
        _rvol_pct = (1.0 - rvol) * 100
        t4_pct = f"-%{_rvol_pct:.0f}"; t4_lbl = "Düşük Hacim"
        t4_sub = f"Bugünün hacmi 20G ortalamanın %{_rvol_pct:.0f} altında — piyasa ilgisiz, sinyal zayıf."
        t4_pos = False; rvol_fill = min((1.0 - rvol) * 100, 100)

    # ── TILE 5: 5 Seans Kümülatif Delta ──────────────────────────
    if cum5 > 0:
        t5_ic = "#10b981" if dark else "#15803d"
        t5_pct = f"+%{cum5_pct:.1f}"; t5_lbl = "5 Günde Net Alım"
        t5_sub = "Son 5 işlem günü boyunca alıcılar baskındı. Kurumsal birikim sinyali."
        t5_pos = True
    elif cum5 < 0:
        t5_ic = "#ef4444" if dark else "#f87171"
        t5_pct = f"-%{cum5_pct:.1f}"; t5_lbl = "5 Günde Net Satış"
        t5_sub = "Son 5 işlem günü boyunca satıcılar baskındı. Dağıtım baskısı."
        t5_pos = False
    else:
        t5_ic = "#94a3b8" if dark else "#6b7280"
        t5_pct = "%0"; t5_lbl = "5 Gün Dengede"; t5_sub = "Son 5 günde alım-satım dengede. Net sinyal yok."
        t5_pos = None

    # ── Tile arka plan renkleri (içeriğe göre) ───────────────────
    def _tile_bg(is_pos):
        if is_pos is True:
            return "rgba(16,185,129,0.11)" if dark else "#dcfce7"
        elif is_pos is False:
            return "rgba(248,113,113,0.11)" if dark else "#fee2e2"
        else:
            return "rgba(245,158,11,0.07)" if dark else "#fffbeb"

    t3_bb = _tile_bg(t3_pos)
    t4_bb = _tile_bg(t4_pos)
    t5_bb = _tile_bg(t5_pos)

    def bidir_bar(fill_pct, color, is_pos, track):
        """Çift yönlü bar — düz inline-block span'lar, nesting/overflow/height:100% yok.
        fill_pct = 0-100 (her yarının kendi ölçeği).
        Pozitif → sağ yarıda soldan fill_pct% renkli.
        Negatif → sol yarıda sağdan fill_pct% renkli.
        Merkez çizgisi position:absolute (screenshot'ta çalıştığı kanıtlandı)."""
        center_col = "#0f172a"
        fw = fill_pct / 2        # toplam bar genişliğinin yüzdesi olarak fill
        rw = 50.0 - fw           # kalan kısım

        if is_pos is True:
            spans = (
                f'<span style="display:inline-block;width:50%;height:8px;background:{track};border-radius:3px 0 0 3px;vertical-align:top;"></span>'
                f'<span style="display:inline-block;width:{fw:.1f}%;height:8px;background:{color};vertical-align:top;"></span>'
                f'<span style="display:inline-block;width:{rw:.1f}%;height:8px;background:{track};border-radius:0 3px 3px 0;vertical-align:top;"></span>'
            )
        elif is_pos is False:
            spans = (
                f'<span style="display:inline-block;width:{rw:.1f}%;height:8px;background:{track};border-radius:3px 0 0 3px;vertical-align:top;"></span>'
                f'<span style="display:inline-block;width:{fw:.1f}%;height:8px;background:{color};vertical-align:top;"></span>'
                f'<span style="display:inline-block;width:50%;height:8px;background:{track};border-radius:0 3px 3px 0;vertical-align:top;"></span>'
            )
        else:
            spans = (
                f'<span style="display:inline-block;width:50%;height:8px;background:{track};border-radius:3px 0 0 3px;vertical-align:top;"></span>'
                f'<span style="display:inline-block;width:50%;height:8px;background:{track};border-radius:0 3px 3px 0;vertical-align:top;"></span>'
            )
        return (
            f'<div style="position:relative;font-size:0;line-height:0;white-space:nowrap;height:8px;margin:3px 0 4px;">'
            + spans +
            f'<div style="position:absolute;left:50%;top:-2px;width:2px;height:12px;background:{center_col};transform:translateX(-50%);"></div>'
            f'</div>'
        )

    # Habersiz POC kaldırıldı — TILE 5 artık 5 Seans Baskı

    # ── Ticker-fiyat badge — ICT paneli ile aynı stil ────────────
    if dark:
        _badge_css = ("font-family:'JetBrains Mono'; font-weight:800; color:#10b981; font-size:0.9rem;"
                      " background:rgba(0,0,0,0.4); padding:2px 8px; border-radius:6px;"
                      " border:1px solid rgba(255,255,255,0.1); white-space:nowrap;")
    else:
        _badge_css = ("background:rgba(56,189,248,0.1); color:#38bdf8; padding:2px 10px; border-radius:4px;"
                      " font-family:'JetBrains Mono',monospace; font-weight:800; font-size:0.9rem;"
                      " border:1px solid rgba(30,58,138,0.2); white-space:nowrap;")

    # ── HTML ──────────────────────────────────────────────────────
    _html = (
        f'<div style="border:1px solid {bc}; background:{bg}; border-radius:8px; margin-top:10px; box-shadow:0 1px 6px rgba(0,0,0,{"0.22" if dark else "0.07"});">'

        # HEADER: sol=başlık, orta=senaryo, sağ=ticker+fiyat
        f'<div style="padding:7px 12px; border-bottom:1px solid {divider}; display:flex; align-items:center; gap:8px;">'
        f'<span style="font-weight:800; font-size:1.0rem; color:{bc}; white-space:nowrap;">&#128202; SMART MONEY HACİM ANALİZİ</span>'
        f'<span style="flex:1; font-size:0.81rem; color:{text_main}; font-weight:700; text-align:center; padding:0 6px;">{sv["title"]}</span>'
        f'<span style="{_badge_css}">{display_ticker} — {_cp_str}</span>'
        f'</div>'

        # AÇIKLAMA (tek satır)
        f'<div style="padding:5px 12px; border-bottom:1px solid {divider}; font-size:0.9rem; color:{text_sub}; line-height:1.4;">{sv["desc"]}</div>'

        # 5 TILE GRID
        f'<div style="display:grid; grid-template-columns:0.85fr 0.75fr 1.0fr 1.0fr 1.1fr; gap:0;">'

        # — TILE 1: POC (Merkez) —
        f'<div style="padding:6px 8px; border-right:1px solid {divider}; background:{t2_bb};">'
        f'<div style="font-size:0.62rem; color:{text_muted}; font-weight:700; letter-spacing:0.5px; margin-bottom:4px; text-transform:uppercase;">&#127919; POC (Merkez)</div>'
        f'<div style="font-size:0.97rem; font-weight:900; color:{t2_ic}; margin-bottom:4px;">{poc:.2f}</div>'
        f'<div style="font-size:0.80rem; color:{text_sub}; line-height:1.4;">En yoğun işlem fiyatı.<br>{poc_vs}.</div>'
        f'</div>'

        # — TILE 2: Fiyat Konumu —
        f'<div style="padding:6px 8px; border-right:1px solid {divider}; background:{t1_bb};">'
        f'<div style="font-size:0.62rem; color:{text_muted}; font-weight:700; letter-spacing:0.5px; margin-bottom:4px; text-transform:uppercase;">&#128205; Fiyat Konumu</div>'
        f'<div style="font-size:0.78rem; font-weight:900; color:{t1_ic}; margin-bottom:4px; line-height:1.2;">{t1_icon} {t1_label}</div>'
        f'<div style="font-size:0.80rem; color:{text_sub}; line-height:1.4;">{t1_sub}</div>'
        f'</div>'

        # — TILE 3: Bugünkü Delta —
        f'<div style="padding:6px 8px; border-right:1px solid {divider}; background:{t3_bb};">'
        f'<div style="font-size:0.62rem; color:{text_muted}; font-weight:700; letter-spacing:0.5px; margin-bottom:2px; text-transform:uppercase;">&#9889; Bugünkü Baskı</div>'
        f'<div style="display:flex; justify-content:{"flex-end" if t3_pos is True else "flex-start" if t3_pos is False else "center"}; margin-bottom:1px;">'
        f'<span style="font-size:0.92rem; font-weight:900; color:{t3_ic};">{t3_pct}</span></div>'
        f'{bidir_bar(d1_fill, t3_ic, t3_pos, track_bg)}'
        f'<div style="font-size:0.8rem; color:{text_main}; font-weight:700; margin-bottom:2px;">{t3_lbl}</div>'
        f'<div style="font-size:0.8rem; color:{text_sub}; line-height:1.35;">{t3_sub}</div>'
        f'</div>'

        # — TILE 4: Ortalamaya Göre Hacim —
        f'<div style="padding:6px 8px; border-right:1px solid {divider}; background:{t4_bb};">'
        f'<div style="font-size:0.62rem; color:{text_muted}; font-weight:700; letter-spacing:0.5px; margin-bottom:2px; text-transform:uppercase;">&#128202; 20G Ort. Göre Bugünkü Hacim</div>'
        f'<div style="display:flex; justify-content:{"flex-end" if t4_pos is True else "flex-start" if t4_pos is False else "center"}; margin-bottom:1px;">'
        f'<span style="font-size:0.92rem; font-weight:900; color:{t4_ic};">{t4_pct}</span></div>'
        f'{bidir_bar(rvol_fill, t4_ic, t4_pos, track_bg)}'
        f'<div style="font-size:0.8rem; color:{text_main}; font-weight:700; margin-bottom:2px;">{t4_lbl}</div>'
        f'<div style="font-size:0.8rem; color:{text_sub}; line-height:1.35;">{t4_sub}</div>'
        f'</div>'

        # — TILE 5: 5 Seans Delta —
        f'<div style="padding:6px 8px; background:{t5_bb};">'
        f'<div style="font-size:0.62rem; color:{text_muted}; font-weight:700; letter-spacing:0.5px; margin-bottom:2px; text-transform:uppercase;">&#128200; Son 5 Günlük Alım-Satım</div>'
        f'<div style="display:flex; justify-content:{"flex-end" if t5_pos is True else "flex-start" if t5_pos is False else "center"}; margin-bottom:1px;">'
        f'<span style="font-size:0.92rem; font-weight:900; color:{t5_ic};">{t5_pct}</span></div>'
        f'{bidir_bar(cum_fill, t5_ic, t5_pos, track_bg)}'
        f'<div style="font-size:0.8rem; color:{text_main}; font-weight:700; margin-bottom:2px;">{t5_lbl}</div>'
        f'<div style="font-size:0.8rem; color:{text_sub}; line-height:1.35;">{t5_sub}</div>'
        f'</div>'

        f'</div>'  # grid sonu
        f'</div>'
    )
    st.markdown(_html, unsafe_allow_html=True)


def render_price_action_panel(ticker):
    obv_title, obv_color, obv_desc = get_obv_divergence_status(ticker)
    pa = calculate_price_action_dna(ticker)
    if not pa:
        st.info("PA verisi bekleniyor...")
        return
    df_sd = get_safe_historical_data(ticker, period="1y")
    try: sd_data = detect_supply_demand_zones(df_sd)
    except: sd_data = None
    # =========================================================
    # 📐 YENİ EKLENEN: FORMASYON AJANI BİREYSEL TARAMA
    # =========================================================
    try:
        # Sadece seçili hisse için formasyon taraması yapıyoruz
        pat_df = scan_chart_patterns([ticker])
        if not pat_df.empty:
            pattern_name  = pat_df.iloc[0]['Formasyon']
            pattern_desc  = pat_df.iloc[0]['Detay']
            pattern_score = pat_df.iloc[0]['Skor']
            pattern_chart = pat_df.iloc[0].get('ChartData', None)
        else:
            pattern_name  = ""
            pattern_desc  = ""
            pattern_score = 0
            pattern_chart = None
    except:
        pattern_name  = ""
        pattern_desc  = ""
        pattern_score = 0
        pattern_chart = None

    # Mevcut mum başlığını ve açıklamasını alıyoruz
    pa_candle_title = pa['candle']['title']
    pa_candle_desc = pa['candle']['desc']

    # Eğer formasyon bulunduysa, mevcut mum verisinin yanına ekliyoruz
    if pattern_name:
        pa_candle_title = f"{pa_candle_title} | 📐 {pattern_name} (Skor: {pattern_score})"
        _chart_img = ""
        if pattern_chart and isinstance(pattern_chart, dict):
            _b64 = _mini_pattern_chart_b64(ticker, pattern_chart, False)
            if _b64:
                _chart_img = f"<img src='data:image/png;base64,{_b64}' style='width:100%;border-radius:4px;margin-top:6px;display:block;'/>"
        pa_candle_desc = (f"{pa_candle_desc}<br><br>"
                          f"<div style='background:rgba(56,189,248,0.08); border-left:3px solid #38bdf8; "
                          f"border-radius:0 4px 4px 0; padding:6px 10px; margin-top:4px;'>"
                          f"<span style='color:#38bdf8; font-weight:800; font-size:0.85rem;'>📐 {pattern_name}</span><br>"
                          f"<span style='color:#94a3b8; font-size:0.78rem;'>{pattern_desc}</span>"
                          f"{_chart_img}</div>")
    # =========================================================
    display_ticker = get_display_name(ticker)
    div_data = pa.get('div', {'type': 'neutral', 'title': '-', 'desc': '-'})
    vwap_data = pa.get('vwap', {'val': 0, 'diff': 0})
    rs_data = pa.get('rs', {'alpha': 0})
    v_diff = vwap_data['diff']
    alpha = rs_data['alpha']

    # --- ORİJİNAL MANTIK VE METİNLER (DOKUNULMADI) ---
    sd_txt = "Taze bölge (RBR/DBD vb.) görünmüyor."
    if sd_data:
        _sd_status_map = {
            "Aktif":                  "bölge aktif",
            "Test Ediliyor":          "fiyat bu bölgeyi test ediyor",
            "İhlal Edildi":           "bu bölge kırılmış görünüyor",
            "İhlal Edildi (Kırıldı)": "bu bölge kırılmış görünüyor",
            "Kırıldı":                "bu bölge kırılmış görünüyor",
        }
        _sd_status_raw = sd_data.get('Status', '')
        _sd_status_txt = _sd_status_map.get(_sd_status_raw, _sd_status_raw)
        sd_txt = (f"{sd_data['Type']} | {sd_data['Bottom']:.2f} - {sd_data['Top']:.2f}"
                  + (f" — {_sd_status_txt}" if _sd_status_txt else ""))

    # VWAP display — bağlamsal dil (mean reversion fallacy YOK).
    # VWAP referans seviyedir, sinyal değil. "Pahalı/Ucuz" yerine "üstünde/altında" + seviye fonksiyonu.
    if v_diff < -2.0:
        vwap_txt = "🟢 VWAP ALTINDA (İskonto Bölgesi)"
        vwap_desc = f"Fiyat VWAP'ın %{abs(v_diff):.1f} altında. VWAP yukarıda direnç olarak izlenir; üstüne kapanış trend dönüş teyidi olabilir."
    elif v_diff < 0.0:
        vwap_txt = "🟢 VWAP TEST (Yakın)"
        vwap_desc = "Fiyat VWAP'a yakın. VWAP geçişi alıcı/satıcı dengesinin kısa vadeli yön sinyali."
    elif v_diff < 8.0:
        vwap_txt = "🚀 VWAP ÜSTÜNDE (Trend Aktif)"
        vwap_desc = f"Fiyat VWAP'ın %{v_diff:.1f} üzerinde. VWAP geri çekilmede destek görevi yapabilir; altına düşmedikçe trend yapısı sağlam."
    elif v_diff < 15.0:
        vwap_txt = "🟠 VWAP'TAN GERİLDİ"
        vwap_desc = f"Fiyat VWAP'tan %{v_diff:.1f} uzakta. Trend ivmesinin doğal sonucu — pozisyon varsa izleyen stop yükseltme noktası olarak izlenir."
    else:
        vwap_txt = "🔴 VWAP'TAN AŞIRI UZAK"
        vwap_desc = f"Fiyat VWAP'tan %{v_diff:.1f} sapmış. Bu sadece momentum ölçüsüdür, tek başına dönüş sinyali değil. Yorgunluk teyidi için OBV/Hacim çelişkisi aranır."

    # ── RS STREAK & MOMENTUM ─────────────────────────────────────
    rs_streak   = 0
    rs_momentum = ""
    try:
        _is_bist_t = ".IS" in ticker or ticker.startswith("XU")
        _bench_t   = "XU100.IS" if _is_bist_t else "^GSPC"
        _df_s      = get_safe_historical_data(ticker)
        _df_b      = get_safe_historical_data(_bench_t)
        if _df_s is not None and _df_b is not None:
            _sc = _df_s['Close'].squeeze()
            _bc = _df_b['Close'].squeeze()
            _common = _sc.index.intersection(_bc.index)
            if len(_common) >= 26:
                _sc = _sc.loc[_common]
                _bc = _bc.loc[_common]
                _rs_daily = _sc.pct_change() - _bc.pct_change()
                # Streak: kaç gündür aynı yönde
                _streak_dir = 1 if _rs_daily.iloc[-1] >= 0 else -1
                for _i in range(1, min(40, len(_rs_daily))):
                    if (_rs_daily.iloc[-_i] >= 0) == (_streak_dir > 0):
                        rs_streak += 1
                    else:
                        break
                # Momentum: bugünkü 20g alpha vs 5g önceki 20g alpha
                _a_now  = float(_sc.iloc[-1]  / _sc.iloc[-21]  - 1) - float(_bc.iloc[-1]  / _bc.iloc[-21]  - 1)
                _a_5ago = float(_sc.iloc[-6]  / _sc.iloc[-26]  - 1) - float(_bc.iloc[-6]  / _bc.iloc[-26]  - 1)
                _diff   = _a_now - _a_5ago
                if _diff > 0.005:
                    rs_momentum = "güç artıyor"
                elif _diff < -0.005:
                    rs_momentum = "güç zayıflıyor"
                else:
                    rs_momentum = "yatay seyrediyor"
    except:
        pass

    if alpha > 1.0:
        _streak_txt   = f" — {rs_streak} gündür endeksten güçlü" if rs_streak > 1 else ""
        _momentum_txt = f", {rs_momentum}" if rs_momentum else ""
        rs_txt  = "GÜÇLÜ MOMENTUM"
        rs_desc = f"Endekse göre %{alpha:.1f} daha güçlü{_streak_txt}{_momentum_txt}."
    elif alpha < -1.0:
        _streak_txt   = f" — {rs_streak} gündür endeksten zayıf" if rs_streak > 1 else ""
        _momentum_txt = f", {rs_momentum}" if rs_momentum else ""
        rs_txt  = "ZAYIF (Endeksin Gerisinde)"
        rs_desc = f"Piyasa giderken gitmiyor (Fark %{alpha:.1f}){_streak_txt}{_momentum_txt}."
    else:
        _momentum_txt = f" — {rs_momentum}" if rs_momentum else ""
        rs_txt  = "NÖTR (Endeks ile Aynı)"
        rs_desc = f"Piyasa rüzgarıyla paralel hareket ediyor{_momentum_txt}."

    # ── RENK DEĞİŞKENLERİ ───────────────
    # S&D rengi: kırılım yönüne göre
    # Arz kırıldı (yukarı) = bullish = yeşil | Arz aktif = direnç = kırmızı
    # Talep kırıldı (aşağı) = bearish = kırmızı | Talep aktif = destek = yeşil
    if sd_data:
        _sd_is_talep  = "Talep" in sd_data.get('Type', '')
        _sd_is_broken = "kırılmış" in _sd_status_txt
        _sd_bullish   = (_sd_is_talep and not _sd_is_broken) or (not _sd_is_talep and _sd_is_broken)
        sd_col = ("#16a34a") if _sd_bullish else ("#f87171")
    else:
        sd_col = "#64748b"
    sfp_color = ("#16a34a") if "Bullish" in pa['sfp']['title'] else \
                ("#f87171") if "Bearish" in pa['sfp']['title'] else \
                ("#475569")
    sq_color  = ("#d97706") if "BOBİN" in pa['sq']['title'] else \
                ("#475569")
    vwap_col  = ("#10b981") if v_diff < -2.0 else \
                ("#4ade80") if v_diff < 0.0 else \
                ("#38bdf8") if v_diff < 8.0 else \
                ("#fbbf24") if v_diff < 15.0 else \
                ("#f87171")
    rs_col    = ("#4ade80") if alpha > 1.0 else \
                ("#f87171") if alpha < -1.0 else \
                ("#94a3b8")
    if div_data['type'] == 'bearish':
        div_col = "#f87171"
        div_bg  = "rgba(248,113,113,0.1)"
        div_brd = "#f87171"
    elif div_data['type'] == 'bullish':
        div_col = "#4ade80"
        div_bg  = "rgba(74,222,128,0.1)"
        div_brd = "#4ade80"
    else:
        div_col = "#94a3b8"
        div_bg  = "transparent"
        div_brd = "transparent"
    text_main  = "#f1f5f9"
    text_muted = "#cbd5e1"
    row_bg     = "#0d1829"
    sep_color  = "#1e3a5f"
    header_col = "#38bdf8"
    card_extra = ""

    # ── ÖNCELİK: en güçlü sinyali bul (özet satır için) ─────────
    sfp_active  = "Bullish" in pa['sfp']['title'] or "Bearish" in pa['sfp']['title']
    div_active  = div_data['type'] != 'neutral'
    vwap_ext    = v_diff < -2.0 or v_diff > 15.0
    rs_active   = abs(alpha) > 1.0
    sq_active   = "BOBİN" in pa['sq']['title']

    top_signals = []
    if div_active:         top_signals.append((10, div_data['title'],                    div_col))
    if sfp_active:         top_signals.append((9,  pa['sfp']['title'],                   sfp_color))
    if pattern_name:       top_signals.append((8,  pattern_name,                         "#38bdf8"))
    if v_diff > 15.0:      top_signals.append((7,  vwap_txt.split("(")[0].strip(),       vwap_col))
    if v_diff < -2.0:      top_signals.append((7,  "Dip Fırsatı — Aşırı İskonto",       vwap_col))
    if rs_active:          top_signals.append((4,  rs_txt.split("(")[0].strip(),         rs_col))
    if sd_data:            top_signals.append((5,  f"Arz-Talep Bölgesi — {_sd_status_txt or 'aktif'}", sd_col))
    if 8.0 <= v_diff < 15.0: top_signals.append((3, "VWAP: Piyasa Isınıyor",            vwap_col))

    if top_signals:
        top_signals.sort(key=lambda x: x[0], reverse=True)
        _, top_name, top_col = top_signals[0]
        top_weight = "800"
    else:
        top_name   = "Belirgin bir sinyal yok — veri izleniyor"
        top_col    = text_muted
        top_weight = "400"

    summary_html = (
        f'<div style="padding:6px 10px;background:{top_col}18;border-left:3px solid {top_col};'
        f'border-radius:0 4px 4px 0;margin-bottom:10px;">'
        f'<div style="font-size:0.72rem;color:{text_muted};font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">En Güçlü Sinyal</div>'
        f'<div style="font-size:0.85rem;font-weight:{top_weight};color:{top_col};">{top_name}</div>'
        f'</div>'
    )

    # ── SEKSİYONLAR (önceliğe göre sıralanır, Mum en tepede sabit) ─
    secs = []

    # Bölümler arası ince kesik çizgi
    _sep = f'<div style="border-top:1px dashed {sep_color};margin:4px 0 6px 0;"></div>'

    # Düşük öncelikli bölümler için kompakt tek satır yardımcısı
    def _compact(label, value_txt, col=None):
        c = col or text_muted
        return (
            f'<div style="padding:2px 6px;opacity:0.55;">'
            f'<span style="font-size:0.74rem;font-weight:600;color:{c};">{label}</span>'
            f'<span style="font-size:0.73rem;color:{text_muted};"> — {value_txt}</span>'
            f'</div>'
        )

    # RSI Uyumsuzluk
    _div_prio = 10 if div_active else 1
    if _div_prio >= 3:
        secs.append((_div_prio,
            f'<div style="padding:8px;border-radius:6px;background:{div_bg};border-left:3px solid {div_brd};">'
            f'<div style="font-weight:800;font-size:0.85rem;color:{div_col};">RSI UYUMSUZLUK: {div_data["title"]}</div>'
            f'<div class="edu-note" style="margin-bottom:0;color:#cbd5e1;">{div_data["desc"]}</div>'
            f'</div>'))
    else:
        secs.append((_div_prio, _compact("RSI UYUMSUZLUK", div_data["title"])))

    # Tuzak (SFP)
    _sfp_prio = 9 if sfp_active else 1
    if _sfp_prio >= 3:
        secs.append((_sfp_prio,
            f'<div style="border-left:3px solid {sfp_color};padding:4px 4px 4px 8px;'
            f'background:{row_bg};border-radius:0 4px 4px 0;">'
            f'<div style="font-weight:700;font-size:0.85rem;color:{sfp_color};">TUZAK DURUMU: {pa["sfp"]["title"]}</div>'
            f'<div class="edu-note" style="color:{text_muted};margin-bottom:0;">{pa["sfp"]["desc"]}</div>'
            f'</div>'))
    else:
        secs.append((_sfp_prio, _compact("TUZAK DURUMU", pa["sfp"]["title"])))

    # Arz-Talep (S&D)
    _sd_prio = 5 if sd_data else 1
    if _sd_prio >= 3:
        secs.append((_sd_prio,
            f'<div style="padding:8px;border-left:3px solid {sd_col};background:{row_bg};border-radius:4px;">'
            f'<div style="font-weight:800;font-size:0.85rem;color:{sd_col};">ARZ-TALEP (S&D) BÖLGELERİ:</div>'
            f'<div style="font-size:0.85rem;font-weight:600;color:{text_main};margin-top:4px;">{sd_txt}</div>'
            + (f'<div class="edu-note" style="margin-top:6px;margin-bottom:0;color:{text_muted};">🐳 <b>Balina Ayak İzi:</b> Kurumsal fonların geçmişte yüklü emir bırakmış olabileceği gizli maliyet bölgesi. Fiyat bu alana girdiğinde tepki ihtimali doğabilir.</div>' if sd_data else '')
            + f'</div>'))
    else:
        secs.append((_sd_prio, _compact("ARZ-TALEP (S&D)", "taze bölge yok")))

    # OBV — dark mode: renk doğrudan metin rengi olarak kullanılır
    _obv_text_col = obv_color
    secs.append((4,
        f'<div style="padding:8px;background:{obv_color+"22"};'
        f'border-radius:6px;border-left:3px solid {obv_color};">'
        f'<div style="font-size:0.8rem;font-weight:700;color:{_obv_text_col};">💰 {obv_title}</div>'
        f'<div style="font-size:0.75rem;color:#cbd5e1;font-style:italic;">{obv_desc}</div>'
        f'</div>'))

    # VWAP (her zaman tam gösterim — min prio 3)
    secs.append((7 if vwap_ext else 3,
        f'<div style="padding:6px 8px;border-left:3px solid {vwap_col};background:{row_bg};border-radius:0 4px 4px 0;">'
        f'<div style="font-weight:700;font-size:0.85rem;color:{vwap_col};">KURUMSAL MALİYET (VWAP): {vwap_txt}</div>'
        f'<div class="edu-note" style="color:{text_muted};">{vwap_desc} '
        f'(Son 20g VWAP: <span style="color:{text_main};font-weight:600;">{vwap_data["val"]:.2f}</span>)</div>'
        f'</div>'))

    # RS
    _rs_prio = 4 if rs_active else 1
    if _rs_prio >= 3:
        secs.append((_rs_prio,
            f'<div style="padding:6px 8px;border-left:3px solid {rs_col};background:{row_bg};border-radius:0 4px 4px 0;">'
            f'<div style="font-weight:700;font-size:0.85rem;color:{rs_col};">RS — PİYASA GÜCÜ: {rs_txt}</div>'
            f'<div class="edu-note" style="color:{text_muted};margin-bottom:0;">{rs_desc}</div>'
            f'</div>'))
    else:
        secs.append((_rs_prio, _compact("RS — PİYASA GÜCÜ", rs_txt)))

    # Hacim & VSA (her zaman prio 3 — tam gösterim)
    secs.append((3,
        f'<div style="padding:6px 8px;border-left:3px solid #38bdf8;background:{row_bg};border-radius:0 4px 4px 0;">'
        f'<div style="font-weight:700;font-size:0.85rem;color:{text_main};">HACİM & VSA: <span style="color:#38bdf8;">{pa["vol"]["title"]}</span></div>'
        f'<div class="edu-note" style="color:{text_muted};">{pa["vol"]["desc"]}</div>'
        f'</div>'))

    # Bağlam & Konum (her zaman prio 2 → kompakt)
    secs.append((2, _compact("BAĞLAM & KONUM", pa["loc"]["title"])))

    # Volatilite (max prio 2 → her zaman kompakt)
    _sq_prio = 2 if sq_active else 1
    secs.append((_sq_prio, _compact("VOLATİLİTE", pa["sq"]["title"], sq_color if sq_active else None)))

    # Önceliğe göre sırala, bölümler arasına ayırıcı ekle
    secs.sort(key=lambda x: x[0], reverse=True)
    sections_html = _sep.join(s for _, s in secs)

    # Mum & Formasyonlar — her zaman en tepede sabit
    candle_html = (
        f'<div style="margin-bottom:8px;">'
        f'<div style="font-weight:700;font-size:0.85rem;color:{text_main};">MUM & FORMASYONLAR: '
        f'<span style="color:#38bdf8;">{pa_candle_title}</span></div>'
        f'<div class="edu-note" style="color:{text_muted};">{pa_candle_desc}</div>'
        f'</div>'
        f'<div style="border-top:1px dashed {sep_color};margin-bottom:8px;"></div>'
    )

    # ── ANA HTML ─────────────────────────────────────────────────
    html_content = (
        f'<div class="info-card" style="border-top:3px solid #6366f1;{card_extra}">'
        f'<div class="info-header" style="color:{header_col};'
        f'{""}">'
        f'🕯️ PRICE ACTION ANALİZİ: {display_ticker}</div>'
        f'{summary_html}'
        f'{candle_html}'
        f'{sections_html}'
        f'</div>'
    )
    st.markdown(html_content.replace("\n", ""), unsafe_allow_html=True)


def calculate_smart_money_score(ticker):
    """
    5 kriterli Akıllı Para Skoru hesaplar. 0-100 arası normalize edilmiş skor döner.
    Hem panel render hem AI prompt için kullanılır.
    Döndürür: dict (score, status, criteria, summary_text) veya None
    """
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
            obv_sma10  = obv.rolling(10).mean()
            obv_above  = bool(obv.iloc[-1] > obv_sma10.iloc[-1])
            obv_rising = bool(obv.iloc[-1] > obv.iloc[-3])
            accum_pass = obv_above and obv_rising
            accum_days = 0
            if accum_pass:
                for i in range(1, min(30, len(df) - 10)):
                    if obv.iloc[-i-1] > obv_sma10.iloc[-i-1]:
                        accum_days += 1
                    else:
                        break
            accum_days = max(1, accum_days) if accum_pass else 0
            price_flat = abs(float(close.iloc[-1] / close.iloc[-6] - 1)) < 0.02
            accum_desc = (f"{accum_days} gündür OBV yükseliyor, fiyat hareketsiz (diverjans)" if (accum_pass and price_flat)
                          else f"{accum_days} gündür OBV 10G ort. üstünde — aktif alım" if accum_pass
                          else "Net birikim sinyali yok")
            accum_edu  = ("OBV (On-Balance Volume) para akışını izler. 10 günlük pencerede OBV "
                          "ortalaması üstündeyse ve son 3 günde yükseliyorsa swing trade için "
                          "aktif kurumsal alım baskısı var demektir.")

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
            trigger_desc = f"RSI {_rsi:.0f} — aşırı satım, dönüş yakın{_rr_txt}"
        elif accum_pass:
            trigger_desc = f"Hazır ama tetik atılmadı{_rr_txt}{_rsi_txt}{_ext_warn}"
        else:
            trigger_desc = f"Tetikleyici oluşmadı{_rsi_txt}{_ext_warn}"
        trigger_edu = ("Kırılım: 20G zirve aşıldı + hacim 1.5x ort. + RSI 35-73 bölgesi. "
                       "SMA50'den %25+ uzaklaşma geç kalınmış sinyalidir. "
                       "R/R 1.5:1 altındaysa hedef yakın — dikkatli ol.")

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

        # [6 — DEĞİŞİKLİK] Senaryo etiketi: kurulumun türünü belirt
        if trigger_pass and not squeeze_pass:
            _scenario = "Trend Devamı"       # hareket başlamış, squeeze olmadan kırılım
        elif squeeze_pass and not trigger_pass:
            _scenario = "Kırılım Öncesi"     # enerji birikmiş, henüz ateşlenmedi
        elif squeeze_pass and trigger_pass:
            _scenario = "Güçlü Kurulum"      # hem squeeze hem tetikleyici
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
                status, status_color, status_bg = f"🎯 FİTİL ÇEKİLİYOR ({pre_launch_days}g){_pl_tag}", "#06b6d4", "#ecfeff"
        elif _overextended and score < 75:
            status, status_color, status_bg = f"⚠️ Geç Kalmış Olabilir (SMA50 +%{_sma50_dist:.0f})", "#f59e0b", "#fffbeb"
        elif score >= 85: status, status_color, status_bg = f"🔥 Harekete geç{trigger_age}" + (f" · {_scenario}" if _scenario else ""), "#10b981", "#ecfdf5"
        elif score >= 65: status, status_color, status_bg = f"⚡ LONG İÇİN HAZIR{trigger_age}" + (f" · {_scenario}" if _scenario else ""), "#3b82f6", "#eff6ff"
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


def render_smart_money_panel(ticker):
    """
    Akıllı Para Skoru paneli — Sidebar: ICT Bottom Line altında, Kurumsal Para İştahı üstünde.
    Edu-notlar CSS hover ile açılır/kaybolur (roadmap panel tekniği).
    """
    data = calculate_smart_money_score(ticker)
    if data is None:
        return

    score         = data["score"]
    score5        = data.get("score5")
    score_trend   = data.get("score_trend", "")
    status        = data["status"]
    s_color       = data["status_color"]
    criteria      = data["criteria"]
    pre_launch    = data.get("pre_launch", False)
    market_score  = data.get("market_score")
    market_note   = data.get("market_note", "")
    display_name  = get_display_name(ticker)

    # Pre-launch: kart kenarlığı ve başlık rengi cyan'a döner (SMR Dark)
    card_bg     = "#060d1a"
    card_border = ("#06b6d4") if pre_launch else ("#1e3a5f")
    head_bg     = ("#0d1f24") if pre_launch else ("#0d1829")
    text_main   = "#f1f5f9"
    text_muted  = "#94a3b8"
    edu_color   = "#cbd5e1"
    row_bg      = "#0d1829"
    row_border  = "#1e3a5f"
    bar_track   = "rgba(255,255,255,0.08)"
    _s_col      = data["status_color"]
    s_bg        = f"rgba({','.join(str(int(_s_col.lstrip('#')[i:i+2],16)) for i in (0,2,4))},0.12)" if _s_col.startswith('#') and len(_s_col)==7 else "rgba(16,185,129,0.12)"

    # Pre-launch pulse animasyonu (tetikleyici satırı bekliyor)
    pulse_css = (
        "@keyframes sms-pulse{0%,100%{box-shadow:0 0 0 0 rgba(6,182,212,0.4);}50%{box-shadow:0 0 0 5px rgba(6,182,212,0);}}"
        ".sms-pre-launch{animation:sms-pulse 2s ease-in-out infinite;}"
    ) if pre_launch else ""

    # CSS hover: her kriter satırı için (roadmap panel ile aynı teknik)
    hover_css = "".join(
        f".sms-row-{i}:hover .sms-tip-{i}{{opacity:1!important;max-height:120px!important;}}"
        for i in range(len(criteria))
    )

    rows_html = ""
    for i, (key, c) in enumerate(criteria.items()):
        if c["pass"] is None:
            dot     = f'<span style="color:#64748b;font-size:0.8rem;flex-shrink:0;">&#8211;</span>'
            desc_clr = text_muted
            lbl_clr  = text_muted
            dot_rgb  = "100,116,139"
        elif c["pass"]:
            dot     = f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#10b981;box-shadow:0 0 4px #10b981;flex-shrink:0;margin-top:3px;"></span>'
            desc_clr = text_main
            lbl_clr  = "#10b981"
            dot_rgb  = "16,185,129"
        else:
            dot     = f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#ef4444;box-shadow:0 0 4px #ef4444;flex-shrink:0;margin-top:3px;"></span>'
            desc_clr = text_muted
            lbl_clr  = "#ef4444"
            dot_rgb  = "239,68,68"

        # Pre-launch: tetikleyici satırı (index 4) özel vurgu — "bekleniyor" hissi
        is_trigger_row = (key == "trigger")
        if pre_launch and is_trigger_row:
            row_extra_cls  = " sms-pre-launch"
            row_extra_sty  = f"border:1px dashed #06b6d4;border-left:3px solid #06b6d4;background:rgba(6,182,212,0.08);"
            lbl_clr        = "#06b6d4"
            dot            = f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#06b6d4;box-shadow:0 0 6px #06b6d4;flex-shrink:0;margin-top:3px;"></span>'
            desc_clr       = "#06b6d4"
            dot_rgb        = "6,182,212"
        else:
            row_extra_cls  = ""
            row_extra_sty  = f"background:{row_bg};border:1px solid {row_border};border-left:3px solid rgba({dot_rgb},0.6);"

        rows_html += (
            f'<div class="sms-row-{i}{row_extra_cls}" style="{row_extra_sty}'
            f'border-radius:7px;padding:7px 9px 5px 9px;margin-bottom:4px;cursor:default;position:relative;">'
            f'<div style="display:flex;align-items:flex-start;gap:7px;">'
            f'{dot}'
            f'<div style="flex:1;min-width:0;">'
            f'<div style="font-size:0.75rem;font-weight:700;color:{lbl_clr};text-transform:uppercase;letter-spacing:0.5px;line-height:1.2;">{c["label"]}</div>'
            f'<div style="font-size:0.80rem;color:{desc_clr};margin-top:2px;line-height:1.3;">{c["desc"]}</div>'
            f'</div>'
            f'<span style="font-size:0.5rem;color:{text_muted};flex-shrink:0;margin-top:2px;opacity:0.6;">&#9432;</span>'
            f'</div>'
            f'<div class="sms-tip-{i}" style="opacity:0;max-height:0;overflow:hidden;transition:opacity 0.22s,max-height 0.22s;'
            f'font-size:0.80rem;color:{edu_color};line-height:1.5;padding-top:0;margin-left:16px;">'
            f'<div style="border-top:1px dashed rgba({dot_rgb},0.25);margin-top:5px;padding-top:4px;">'
            f'&#128161; {c["edu"]}</div></div>'
            f'</div>'
        )

    html = (
        f'<style>{hover_css}{pulse_css}</style>'
        f'<div style="background:{card_bg};border:2px solid {card_border};border-radius:12px;'
        f'overflow:hidden;margin-bottom:10px;font-family:Inter,sans-serif;'
        f'box-shadow:0 4px 16px rgba(0,0,0,0.12);">'
        # ── Başlık bandı: isim sol, skor sağ — tek satır, sarkmaz
        f'<div style="background:{head_bg};padding:9px 14px;display:flex;align-items:center;justify-content:space-between;">'
        f'<div style="min-width:0;">'
        f'<div style="font-size:0.77rem;font-weight:700;color:{text_muted};text-transform:uppercase;letter-spacing:0.8px;white-space:nowrap;">&#128640; LONG RADAR</div>'
        f'<div style="font-size:0.85rem;font-weight:800;color:{text_main};margin-top:2px;">{display_name}</div>'
        f'</div>'
        f'<div style="text-align:right;flex-shrink:0;margin-left:8px;">'
        f'<div style="font-family:JetBrains Mono,monospace;font-size:2.0rem;font-weight:900;color:{s_color};line-height:1;">{score}<span style="font-size:1rem;font-weight:600;color:{text_muted}">/100</span></div>'
        + (f'<div style="font-size:0.70rem;color:{"#10b981" if score_trend.startswith("↑") else "#f87171" if score_trend.startswith("↓") else text_muted};font-weight:600;margin-top:1px;white-space:nowrap;">5g önce: {score5}</div>' if score5 is not None else "")
        + f'</div>'
        f'</div>'
        # ── Status şeridi: tam genişlik, sarkmaz
        f'<div style="background:{s_bg};border-top:1px solid {s_color}30;border-bottom:1px solid {s_color}30;'
        f'padding:5px 14px;text-align:center;">'
        f'<span style="font-size:0.85rem;font-weight:800;color:{s_color};letter-spacing:0.3px;white-space:nowrap;">{status}</span>'
        f'</div>'
        # ── Piyasa notu (varsa)
        + (f'<div style="padding:4px 14px;font-size:0.72rem;font-weight:600;'
           f'color:{"#fbbf24" if market_score is not None and market_score < 40 else "#4ade80" if market_score is not None and market_score >= 65 else text_muted};'
           f'text-align:center;border-bottom:1px solid {s_color}18;">{market_note}</div>'
           if market_note else "")
        # ── Beden
        + f'<div style="padding:10px 12px 8px 12px;">'
        # Progress bar
        f'<div style="position:relative;background:{bar_track};border-radius:99px;height:6px;margin:0 0 2px 0;overflow:hidden;">'
        f'<div style="background:linear-gradient(90deg,{s_color}88,{s_color});width:{score}%;height:100%;border-radius:99px;"></div>'
        f'</div>'
        f'<div style="display:flex;justify-content:space-between;font-size:0.73rem;color:{text_muted};font-family:JetBrains Mono,monospace;margin-bottom:8px;">'
        f'<span>0</span><span>50</span><span>100</span>'
        f'</div>'
        # Kriter satırları
        f'{rows_html}'
        f'</div>'
        f'</div>'
    )

    st.markdown(html, unsafe_allow_html=True)


def render_ict_certification_card(ticker):
    """
    Sadece 5 şartı geçen hisselerde 'Onay Sertifikası' gösterir.
    Görsel: Başlık solda, Sonuç sağda (Yeşil Tikli), Açıklama altta (Edu Note).
    """
    # 1. Teyit Et (Logic Çalıştır)
    df = get_safe_historical_data(ticker, period="1y")
    # Daha önce yazdığımız dedektör fonksiyonunu kullanıyoruz
    res = process_single_ict_setup(ticker, df)
    
    # EĞER HİSSE SETUP'A UYMUYORSA HİÇ GÖSTERME (Sessizce çık)
    if res is None: return 

    # 2. HTML Tasarımı (MARTI Paneli Formatında)
    html_content = f"""
    <div class="info-card" style="border-top: 3px solid #7c3aed; background:rgba(139,92,246,0.07); margin-bottom: 10px;">
        <div class="info-header" style="color:#8b5cf6; display:flex; justify-content:space-between; align-items:center;">
            <span>🦅 ICT Sniper Onay Raporu</span>
            <span style="font-size:0.8rem; background:#7c3aed15; padding:2px 8px; border-radius:10px; font-weight:700;">5/5</span>
        </div>
        
        <div class="info-row" style="margin-top:5px;">
            <div class="label-long" style="width:160px; color:#8b5cf6;">1. Likidite Temizliği (SSL):</div>
            <div class="info-val" style="color:#16a34a; font-weight:800;">GEÇTİ ✅</div>
        </div>
        <div class="edu-note" style="margin-bottom:8px;">
            Son 20-40 günün dibi aşağı kırıldı. Stoplar patlatıldı.
        </div>

        <div class="info-row">
            <div class="label-long" style="width:160px; color:#8b5cf6;">2. Market Yapı Kırılımı:</div>
            <div class="info-val" style="color:#16a34a; font-weight:800;">GEÇTİ ✅</div>
        </div>
        <div class="edu-note" style="margin-bottom:8px;">
            Fiyat ani bir "U" dönüşüyle son tepeyi yukarı kırdı.
        </div>

        <div class="info-row">
            <div class="label-long" style="width:160px; color:#8b5cf6;">3. Enerji / Hacim:</div>
            <div class="info-val" style="color:#16a34a; font-weight:800;">GEÇTİ ✅</div>
        </div>
        <div class="edu-note" style="margin-bottom:8px;">
            Yükseliş cılız mumlarla değil, gövdeli ve iştahlı mumlarla oldu.
        </div>

        <div class="info-row">
            <div class="label-long" style="width:160px; color:#8b5cf6;">4. FVG Bıraktılar (İmza):</div>
            <div class="info-val" style="color:#16a34a; font-weight:800;">VAR (Destek) ✅</div>
        </div>
        <div class="edu-note" style="margin-bottom:8px;">
            Yükselirken arkasında doldurulmamış boşluk bıraktı.
        </div>

        <div class="info-row" style="border-top:1px dashed #d8b4fe; padding-top:6px; margin-top:4px;">
            <div class="label-long" style="width:160px; color:#8b5cf6; font-weight:800;">5. İndirimli Bölge:</div>
            <div class="info-val" style="color:#16a34a; font-weight:800;">OTE (Mükemmel) ✅</div>
        </div>
        <div class="edu-note">
            Fiyat, hareketin %50'sinden fazlasını geri alarak "Toptan Fiyat" bölgesine indi.
        </div>
    </div>
    """
    st.markdown(html_content.replace("\n", " "), unsafe_allow_html=True)

def render_ict_deep_panel(ticker):
    data = calculate_ict_deep_analysis(ticker)
    if not data or data.get("status") == "Error": return st.warning(f"ICT Analiz Bekleniyor... ({data.get('msg', 'Veri Yok')})")
    
    # --- ORİJİNAL MANTIK VE METİNLER (DOKUNULMADI) ---
    struct_title = "MARKET YAPISI"
    struct_desc = "Piyasa kararsız."
    if "MSS" in data['structure']:
        if "🐂" in data['structure']: 
            struct_title = "TREND DÖNÜŞÜ (BULLISH MSS)"
            struct_desc = "Fiyat, düşüş yapısını bozan son önemli tepeyi aştı. Ayı piyasası bitmiş, Boğa dönemi başlıyor olabilir!"
        else: 
            struct_title = "TREND DÖNÜŞÜ (BEARISH MSS)"
            struct_desc = "Fiyat, yükseliş yapısını tutan son önemli dibi kırdı. Boğa piyasası bitmiş, Ayı dönemi başlıyor olabilir!"
    elif "BOS (Yükseliş" in data['structure']: 
        struct_title = "YÜKSELİŞ TRENDİ (BULLISH BOS)"
        struct_desc = "Boğalar kontrolü elinde tutuyor. Eski tepeler aşıldı, bu da yükseliş iştahının devam ettiğini gösterir. Geri çekilmeler alım fırsatı olabilir."
    elif "BOS (Düşüş" in data['structure']: 
        struct_title = "DÜŞÜŞ TRENDİ (BEARISH BOS)"
        struct_desc = "Ayılar piyasaya hakim. Eski dipler kırıldı, düşüş trendi devam ediyor. Yükselişler satış fırsatı olarak görülebilir."
    elif "Zayıf Kırılım" in data['structure']:
        struct_title = "ZAYIF KIRILIM — ONAY BEKLENİYOR"
        struct_desc = "Fiyat son önemli seviyeyi çok az geçti (%0.5'ten az). Bu bir sahte kırılım (fakeout) olabilir. Kırılımın gerçek olduğunu anlamak için hacim artışı ve kapanış onayı beklenmeli."
    elif "Boğa Sıkışması" in data['structure']:
        struct_title = "BOĞA SIKIŞMASI — KIRILIM YUKARI OLABİLİR"
        struct_desc = "Fiyat ne yukarı ne aşağı kırdı ama hem tepeler hem dipler yükseliyor. Bu genellikle yukarı kırılımın habercisidir. Sabırla kırılım onayı beklenebilir."
    elif "Ayı Sıkışması" in data['structure']:
        struct_title = "AYI SIKIŞMASI — DİKKATLİ OL"
        struct_desc = "Fiyat sıkışmış ama hem tepeler hem dipler alçalıyor. Bu genellikle aşağı kırılımın habercisidir. Mevcut alım pozisyonlarında temkinli olunmalı."
    elif "Internal" in data['structure']:
        struct_title = "INTERNAL RANGE (Düşüş/Düzeltme)" if "bearish" in data['bias'] else "INTERNAL RANGE (Yükseliş/Tepki)"
        struct_desc = "Ana trendin tersine bir düzeltme hareketi (Internal Range) yaşanıyor olabilir. Piyasada kararsızlık hakim."

    energy_title = "ENERJİ DURUMU"
    energy_desc = "Zayıf (Hacimsiz Hareket)\nMum gövdeleri küçük, hacimsiz bir hareket. Kurumsal oyuncular henüz oyuna tam girmemiş olabilir. Kırılımlar tuzak olabilir."
    if "Hacim Onaylı" in data['displacement']:
        energy_desc = "Güçlü (Hacim Onaylı)\nFiyat hem güçlü mum gövdesiyle hem de ortalamanın üzerinde hacimle hareket etti. Bu, 'Akıllı Para'nın (Smart Money) gerçek ayak sesidir."
    elif "Hacimsiz Hareket" in data['displacement']:
        energy_desc = "⚠️ Hacimsiz Hareket (Sahte Olabilir)\nMum gövdesi büyük görünse de hacim ortalamayı geçmedi. Kurumsal destek yok, sahte kırılım riski var."
    elif "Güçlü" in data['displacement']:
        energy_desc = "Güçlü (Displacement Var)\nFiyat güçlü ve hacimli mumlarla hareket ediyor. Bu 'Akıllı Para'nın (Smart Money) ayak sesidir."

    _mt = data.get('mean_threshold', 0)
    _cp = data.get('curr_price', 0)
    # Seviyenin kaynağını belirle: OB varsa OB orta noktası, yoksa 60G range equilibrium
    _ob_source = data.get('ob_age', 0) > 0 and data.get('ob_txt', 'Yok') != 'Yok'
    _level_label = "OB Orta Noktası" if _ob_source else "60G Denge Noktası"

    # Mesafe hesabı — başlık bağlamı için
    _mt_dist_pct = abs(_cp - _mt) / _cp * 100 if (_cp > 0 and _mt > 0) else 0
    _mt_dist_str = f"%{_mt_dist_pct:.1f}"
    _ob_h_num = data.get('ob_high_num', 0)
    _ob_l_num = data.get('ob_low_num', 0)
    _in_ob = (_ob_l_num > 0 and _ob_h_num > 0 and _ob_l_num <= _cp <= _ob_h_num)
    _below_ob = (_ob_l_num > 0 and _cp < _ob_l_num)

    mt_title = "Kritik Denge Seviyesi"
    mt_desc  = "Fiyat kritik orta noktanın altına sarktı/üstüne çıktı. Yapı bozulmuş olabilir."
    if "bearish" in data['bias']:
        if _in_ob:
            mt_title = "Arz Bölgesinde! ⚠️"
        elif _cp > _mt:
            mt_title = f"Satıcılar Baskın — Direnç ({_mt_dist_str} aşağıda)"
        else:
            mt_title = f"Satıcılar Baskın — OB Kırıldı ⚠️"
        if _mt > 0 and _mt < _cp:
            mt_desc = (f"⚖️ <b>{_level_label}: {_mt:.2f}</b> — Fiyat bu seviyeyi aşağı kırdı; "
                       f"{_mt:.2f} artık <b>direnç</b> işlevi görüyor. Kurumsal sipariş akışı (Order Flow) satıcılar lehine. "
                       f"Fiyat bu seviyeyi yeniden yukarı kıramadığı sürece yapı bozuk kalır; yeni alım açmak riskli.")
        elif _mt > 0:
            mt_desc = (f"⚖️ <b>{_level_label}: {_mt:.2f}</b> — Fiyat bu denge seviyesinin <b>altında</b> işlem görüyor. "
                       f"Satıcılar baskın; {_mt:.2f} seviyesi kısa vadeli hedef direnç. "
                       f"Fiyatın bu seviyeyi net geçip geçemeyeceği, trendin devamını belirleyecek.")
        else:
            mt_desc = "Kurumsal sipariş akışı (Order Flow) satıcılar lehine. Yeni alım pozisyonları için erken; yapı bozuk kalmaya devam ediyor."
    elif "bullish" in data['bias']:
        if _in_ob:
            mt_title = "OB'de! Destek Test Ediliyor 🎯"
        elif _below_ob:
            mt_title = "OB Kırıldı ⚠️"
        elif _mt > 0 and _mt <= _cp:
            mt_title = f"Alıcılar Baskın — OB Desteği ({_mt_dist_str} aşağıda)"
        else:
            mt_title = f"Alıcılar Baskın — Geri Çekilme Bölgesi ({_mt_dist_str} aşağıda)"
        if _mt > 0 and _mt <= _cp:
            mt_desc = (f"⚖️ <b>{_level_label}: {_mt:.2f}</b> — Fiyat bu seviyenin üzerinde işlem görüyor; "
                       f"{_mt:.2f} <b>destek</b> görevi üstlendi. Olası geri çekilmelerde bu bölgeye yakın alım fırsatı doğabilir. "
                       f"Kapanışlar bu seviyenin altına sarkarsa pozisyon korunmalıdır.")
        elif _mt > 0:
            mt_desc = (f"⚖️ <b>{_level_label}: {_mt:.2f}</b> — Fiyat şu an bu seviyenin <b>altında</b> (iskontolu bölge). "
                       f"Sipariş akışı alıcılar lehine olmakla birlikte, fiyatın önce {_mt:.2f} seviyesine <b>ulaşması gerekiyor</b> — "
                       f"bu aşamalı bir yükseliş hedefidir, mevcut fiyattan destek değil.")
        else:
            mt_desc = "Kurumsal sipariş akışı (Order Flow) alıcılar lehine. Bu seviye güçlü destek görevi görüyor; olası geri çekilmeler alım fırsatı sunabilir."

    # Yapı ↔ Bias çelişki uyarısı
    _struct_bearish = any(w in struct_title for w in ["AYI SIKIŞMASI", "DÜŞÜŞ TRENDİ", "BEARISH"])
    _struct_bullish = any(w in struct_title for w in ["BOĞA SIKIŞMASI", "YÜKSELİŞ TRENDİ", "BULLISH"])
    if ("bullish" in data['bias'] and _struct_bearish) or ("bearish" in data['bias'] and _struct_bullish):
        mt_desc += ("<div style='margin-top:8px; padding:6px 8px; border-top:1px dashed rgba(248,113,113,0.5);'>"
                    "<span style='color:#f87171; font-weight:700; font-size:0.78rem;'>⚠️ Dikkat: </span>"
                    "<span style='font-size:0.75rem;'>Yapısal baskı ile sipariş akışı zıt yönü gösteriyor — "
                    "her iki sinyal aynı tarafta olmadığı için pozisyon daha riskli. Onay beklemek daha sağlıklı.</span></div>")

    zone_desc = "Fiyat 'Ucuzluk' (Discount) bölgesinde. Kurumsal yatırımcılar bu seviyelerden alım yapmayı tercih eder."
    if "PREMIUM" in data['zone']: 
        zone_desc = "Fiyat 'Pahalılık' (Premium) bölgesinde. Kurumsal yatırımcılar bu bölgede satış yapmayı veya kar almayı sever."

    fvg_desc = "Yakınlarda önemli bir dengesizlik boşluğu tespit edilemedi."
    if "Destek" in data['fvg_txt']: fvg_desc = "Fiyatın bu boşluğu doldurup destek alması beklenebilir."
    elif "Direnç" in data['fvg_txt']: fvg_desc = "Fiyatın bu boşluğu doldurup direnç görmesi beklenebilir."

    # OB Dinamik Açıklama — kalite etiketleri + yaş kombinasyonu
    ob_age = data.get('ob_age', 0)   # erken tanım (model skor bloğu henüz gelmedi)
    _ob_worn   = "Yıpranmış" in data['ob_txt']
    _ob_second = "2. Test"   in data['ob_txt']
    _ob_fresh  = "Taze OB"   in data['ob_txt']
    _ob_fvg    = "FVG+OB"    in data['ob_txt']
    _ob_none   = data['ob_txt'] == "Yok"

    if _ob_none:
        ob_desc = "Yakın vadede net bir kurumsal işlem bölgesi (Order Block) tespit edilemedi. Bu, fiyatın şu an belirsiz bir alanda olduğuna işaret eder."
    elif _ob_worn and ob_age >= 16:
        ob_desc = ("⚠️ <b>Tükenmiş Bölge:</b> Bu seviye hem eski (16+ gün) hem de fiyat tarafından defalarca ziyaret edilmiş. "
                   "Oradaki kurumsal sipariş duvarı büyük ölçüde eridi — artık güçlü bir destek/direnç değil, yalnızca bir referans noktası. "
                   "Fiyat bir sonraki gelişte bu bölgeyi kolayca kırabilir.")
    elif _ob_worn and ob_age >= 6:
        ob_desc = ("⚠️ <b>Erimekte:</b> Bu bölgedeki kurumsal emirlerin büyük kısmı tüketildi. "
                   "Fiyat birkaç kez test etti, her seferinde bir miktar 'sipariş duvarı' eridi. "
                   "Güçlü destek/direnç değil; geçirgen bir zemin. Kırılım riski arttı.")
    elif _ob_worn:
        ob_desc = ("🔴 <b>Hızla Tüketildi:</b> Kısa sürede 3+ kez test edilen bu bölgede kurumsal emirler çabuk doldu. "
                   "Bu hızlı tüketim, büyük oyuncuların burada beklediğini ama yeni alım/satım yapmadığını gösterir. "
                   "Güvenilirliği düşük.")
    elif _ob_second and ob_age >= 16:
        ob_desc = ("🟡 <b>Zayıflıyor:</b> 1-2 kez test edilmiş ve artık eski sayılan bir bölge. "
                   "Kurumsal emirlerin bir kısmı hâlâ duruyor olabilir, ama hem yaş hem de testler güveni azaltıyor. ")
    elif _ob_second:
        ob_desc = ("🟡 <b>Kısmi Güç:</b> Bu bölge 1-2 kez test edildi — bir miktar kurumsal emir tüketildi ama henüz bitmedi. "
                   "Taze bir bölge kadar güçlü değil ama tepki hâlâ mümkün. "
                   "Fiyatın burada duraklayıp duraklamamasını izlemek gerekiyor.")
    elif _ob_fresh and ob_age >= 16:
        ob_desc = ("🟡 <b>Uzun Süredir Bekliyor:</b> Hiç test edilmemiş ama eski bir bölge. "
                   "Kurumsal emirler teorik olarak hâlâ orada duruyor — fiyat gelince güçlü tepki verebilir. ")
    elif _ob_fresh:
        ob_desc = ("🟢 <b>Güçlü Duvar:</b> Taze ve henüz hiç test edilmemiş bir kurumsal bölge. "
                   "Büyük oyuncuların emirleri büyük ihtimalle yerli yerinde duruyor. "
                   "Fiyat buraya gelirse tepki beklentisi yüksek.")
    else:
        ob_desc = ("Kurumsal oyuncuların yüklü işlem yaptığı seviye. "
                   "Fiyat buraya dönerse tepki alabilir. ")

    if _ob_fvg and not _ob_none:
        ob_desc += (" Ayrıca bu bölge açık bir fiyat boşluğuyla (GAP/FVG) örtüşüyor — "
                    "iki farklı kurumsal seviyenin aynı noktada buluşması, bölgenin manyetik çekimini güçlendirir.")
    structural_target_val = data.get('structural_target', 0)
    is_bullish_bias = "bullish" in data['bias']
    if is_bullish_bias:
        liq_desc = ("Fiyatın kısa vadede 'bakması' beklenen ilk nokta. Burada çok sayıda stop emri birikmiş olabilir. "
                    "Fiyat bu seviyeye ulaşınca ya yukarı kırar devam eder, ya da geri çekilir — "
                    "kısa vadeli kâr hedefi olarak düşünün.")
        struct_target_label = "🏹 Asıl Hedef"
        struct_target_desc  = ("Geçmişte büyük fonların yoğun satış yaptığı önemli bir tepe. "
                               "Trend devam ederse fiyatın orta-uzun vadede hedefleyeceği seviye.")
    else:
        liq_desc = ("Fiyatın kısa vadede 'bakması' beklenen ilk destek noktası. "
                    "Alıcıların devreye girip giremeyeceğini test edecek seviye. "
                    "Fiyat burada tutunursa düşüş yavaşlar.")
        struct_target_label = "🏹 Asıl Destek"
        struct_target_desc  = ("Geçmişte büyük fonların yoğun alım yaptığı kritik bir dip bölgesi. "
                               "Düşüş devam ederse fiyatın orta-uzun vadede test edeceği seviye.")
                                   
    display_ticker = get_display_name(ticker)
    info = fetch_stock_info(ticker)
    _cp_raw = info.get('price', 0) if info else 0
    current_price_str = (f"{int(_cp_raw)}" if _cp_raw >= 1000 else f"{_cp_raw:.2f}") if info else "0.00"

    # --- MODEL SKORU GÖRSEL DEĞİŞKENLERİ ---
    model_score  = data.get('model_score', 0)
    model_checks = data.get('model_checks', [])
    ob_age   = data.get('ob_age', 0)
    fvg_age  = data.get('fvg_age', 0)
    struct_age = data.get('struct_age', 0)
    _blocks = "■" * model_score + "□" * (5 - model_score)
    if model_score >= 4: _sc = "#10b981"
    elif model_score == 3: _sc = "#f59e0b"
    else: _sc = "#f87171"
    _slabel = ["SETUP YOK", "ÇOK ZAYIF", "ZAYIF", "ORTA", "GÜÇLÜ", "TAM MODEL"][model_score]
    # Kriter ipucu metni (title attribute için)
    _checks_tip = " | ".join([f"{'✅' if ok else '❌'} {name}" for name, ok in model_checks])

    # Yaş renkleri (0-5g taze, 6-15g orta, 16+ eski)
    def _age_clr(age): return ("#10b981","rgba(16,185,129,0.15)") if age<=5 else ("#f59e0b","rgba(245,158,11,0.15)") if age<=15 else ("#f87171","rgba(248,113,113,0.15)")
    ob_age_badge  = ""
    fvg_age_badge = ""
    if ob_age  > 0:
        _c,_b = _age_clr(ob_age);  ob_age_badge  = f'<span style="font-size:0.7rem;color:{_c};background:{_b};padding:1px 5px;border-radius:3px;margin-left:5px;font-weight:600;">{ob_age}g önce</span>'
    if fvg_age > 0:
        _c,_b = _age_clr(fvg_age); fvg_age_badge = f'<span style="font-size:0.7rem;color:{_c};background:{_b};padding:1px 5px;border-radius:3px;margin-left:5px;font-weight:600;">{fvg_age}g önce</span>'
    # Struct age — struct_desc'e ek not
    if struct_age > 0:
        if struct_age <= 5:   _snote = f" <span style='color:#10b981;font-size:0.72rem;'>✅ Taze yapı ({struct_age} gün önce oluştu)</span>"
        elif struct_age <= 15: _snote = f" <span style='color:#f59e0b;font-size:0.72rem;'>⏳ Orta Yapı ({struct_age} gün önce)</span>"
        else:                  _snote = f" <span style='color:#f87171;font-size:0.72rem;'>⚠️ Eski yapı ({struct_age} gün önce)</span>"
        struct_desc = struct_desc + _snote

    # ---------------------------------------------------------------
    # FİYAT HARİTASI — seviyeleri fiyat sırasına göre liste olarak göster
    # ---------------------------------------------------------------
    def _price_ruler_html(d, dark):
        cp   = d.get('curr_price', 0)
        ob_l = d.get('ob_low_num', 0);  ob_h = d.get('ob_high_num', 0)
        fg_l = d.get('fvg_low_num', 0); fg_h = d.get('fvg_high_num', 0)
        mt   = d.get('mean_threshold', 0)
        tgt  = d.get('target', 0)
        is_bull = "bullish" in d.get('bias', '')
        if cp == 0: return ""

        def fmt(p):
            if p >= 10000: return f"{p:,.0f}"
            elif p >= 100: return f"{p:.0f}"
            else:          return f"{p:.2f}"

        # Her seviye: (fiyat, etiket, renk, bant_grubu, band_bg)
        # bant_grubu: aynı gruba alanlar arası dolgu çizgisi çekiliyor
        levels = []
        ob_col  = "#f87171" if not is_bull else "#38bdf8"
        ob_bg   = "rgba(248,113,113,0.08)" if not is_bull else "rgba(56,189,248,0.08)"
        ob_lbl  = "OB Arz" if not is_bull else "OB Talep"
        if ob_h > 0: levels.append((ob_h, f"{ob_lbl} Üst", ob_col, "ob", ob_bg))
        if ob_l > 0: levels.append((ob_l, f"{ob_lbl} Alt", ob_col, "ob", ob_bg))
        if fg_h > 0: levels.append((fg_h, "FVG Üst", "#a78bfa", "fvg", "rgba(139,92,246,0.08)"))
        if fg_l > 0: levels.append((fg_l, "FVG Alt", "#a78bfa", "fvg", "rgba(139,92,246,0.08)"))
        if mt  > 0:  levels.append((mt,   "Denge",   "#f59e0b", None, None))
        if tgt > 0 and abs(tgt - cp) / cp > 0.002:
            tc = "#10b981" if tgt > cp else "#fb923c"
            levels.append((tgt, "Hedef", tc, None, None))

        # Mevcut fiyatı da listeye ekle (işaretçi olarak)
        levels.append((cp, "▶ MEVCUT", "#10b981" if is_bull else "#f87171", "current", None))

        # Fiyata göre azalan sırala (yüksek → düşük)
        levels.sort(key=lambda x: x[0], reverse=True)

        txt_main  = "#e2e8f0" if dark else "#1e293b"
        txt_muted = "#cbd5e1" if dark else "#94a3b8"
        border_c  = "rgba(255,255,255,0.06)" if dark else "rgba(0,0,0,0.06)"

        rows = []
        prev_group = None
        for price, label, color, group, band_bg in levels:
            is_current = group == "current"
            row_bg = ""
            if is_current:
                row_bg = f"background:rgba({'16,185,129' if is_bull else '248,113,113'},0.12);border-radius:4px;border:1px solid {color}40;"
            elif band_bg:
                row_bg = f"background:{band_bg};"

            # Bant değişimlerinde ince ayraç
            sep = ""
            if prev_group and group != prev_group and not is_current:
                sep = f"<div style='height:6px;'></div>"
            prev_group = group

            weight = "800" if is_current else "600"
            size   = "0.75rem" if is_current else "0.7rem"

            rows.append(
                sep +
                f"<div style='display:flex;align-items:center;padding:3px 6px;{row_bg}'>"
                f"<div style='width:3px;height:14px;background:{color};border-radius:2px;margin-right:7px;flex-shrink:0;'></div>"
                f"<span style='flex:1;font-size:{size};color:{color if is_current else txt_muted};font-weight:{weight};'>{label}</span>"
                f"<span style='font-family:monospace;font-size:{size};color:{color if is_current else txt_main};font-weight:{weight};'>{fmt(price)}</span>"
                f"</div>"
            )

        if not rows:
            return ""

        bg_c = "rgba(17,24,39,0.3)" if dark else "rgba(248,250,252,0.8)"
        return (
            f"<div style='background:{bg_c};border-radius:6px;padding:4px 2px;margin-bottom:8px;'>"
            + "".join(rows) +
            "</div>"
        )

    ruler_html = _price_ruler_html(data, True)

    # --- BASİT AÇIKLAMALAR (yeni başlayanlar için, sol sütun) ---
    zone_simple = ("Fiyat şu an 'pahalı bölge'de. Büyük oyuncular bu seviyelerde satış yapmayı veya kâr almayı tercih eder."
                   if "PREMIUM" in data['zone'] else
                   "Fiyat şu an 'ucuz bölge'de. Büyük oyuncular bu seviyelerden alım yapmayı sever.")
    if _ob_none:
        ob_simple = "Yakınlarda net bir kurum bloğu tespit edilemedi — fiyat belirsiz bir alanda."
    elif _ob_worn:
        ob_simple = "Kurum bloğu var ama defalarca test edilmiş. Sipariş duvarı büyük ölçüde eridi — dikkatli ol."
    elif _ob_fresh:
        ob_simple = "Taze kurum bloğu! Büyük fonların emirleri burada duruyor. Fiyat gelirse güçlü tepki beklenir."
    elif _ob_second:
        ob_simple = "Kısmen tüketilmiş kurum bloğu. Hâlâ tepki verebilir ama taze kadar güçlü değil."
    else:
        ob_simple = "Kurumsal işlem bölgesi mevcut. Fiyat buraya gelirse tepki verebilir."
    if "Destek" in data['fvg_txt']:
        fvg_simple = "Doldurulmamış fiyat boşluğu var. Fiyat bu alana geri döndüğünde destek görebilir."
    elif "Direnç" in data['fvg_txt']:
        fvg_simple = "Doldurulmamış fiyat boşluğu var. Fiyat bu alana ulaştığında direnç görebilir."
    else:
        fvg_simple = "Fiyat boşluğu tespit edilemedi — temiz bir alan."
    if is_bullish_bias:
        tgt_simple = f"Kısa vadeli ilk hedef: <b>{data['target']:.2f}</b>. Fiyat buraya yaklaşınca kâr satışları gelebilir."
    else:
        tgt_simple = f"Kısa vadeli ilk destek: <b>{data['target']:.2f}</b>. Fiyat burada alıcı bulup bulamayacağını test edecek."

    mc = "#4ade80" if "bullish" in data['bias'] else "#f87171" if "bearish" in data['bias'] else "#a78bfa"
    bg = "rgba(74,222,128,0.08)" if "bullish" in data['bias'] else "rgba(248,113,113,0.08)" if "bearish" in data['bias'] else "rgba(167,139,250,0.08)"
    st.markdown(f"""
    <div class="info-card" style="border-top: 4px solid {mc}; margin-bottom:10px; border-radius: 8px;">
        <div class="info-header" style="color:#38bdf8; display:flex; justify-content:space-between; align-items:center; padding: 3px 12px;"><span style="font-size:1.15rem; font-weight: 800;">🧠 ICT Smart Money Analizi: {display_ticker}</span><span title="{_checks_tip}" style="cursor:default; font-family:monospace; color:{_sc}; font-size:0.88rem; font-weight:700; letter-spacing:2px; background:#0d1829; padding:3px 10px; border-radius:6px; border:2px solid {_sc};">{_blocks} &nbsp;{model_score}/5 · {_slabel}</span><span style="background:rgba(56,189,248,0.1); color:#38bdf8; padding:2px 10px; border-radius:4px; font-family:'JetBrains Mono',monospace; font-weight:800; font-size:0.9rem; border:1px solid rgba(30,58,138,0.2);">{display_ticker} <span style="opacity:0.6; margin:0 4px; font-weight:400;">—</span> <span style="color:#38bdf8;">{current_price_str}</span></span></div>
    </div>""", unsafe_allow_html=True)

    c1, c2 = st.columns([1.4, 1])
    with c1:
        sc1, sc2 = st.columns(2)
        with sc1: st.markdown(f"""<div style="border:2px solid {mc}; background:{bg}; border-radius:8px; padding:12px; height: 100%;"><div style="font-weight:800; color:{mc}; font-size:0.85rem; text-transform: uppercase; margin-bottom:6px;">{struct_title}</div><div style="font-size:0.8rem; color:#cbd5e1; line-height:1.4;">{struct_desc}</div></div>""", unsafe_allow_html=True)
        with sc2: st.markdown(f"""<div style="border:2px solid #94a3b8; background:#0d1829; border-radius:8px; padding:12px; height: 100%;"><div style="font-weight:800; color:#7c3aed; font-size:0.85rem; text-transform: uppercase; margin-bottom:6px;">{energy_title}</div><div style="font-size:0.8rem; color:#cbd5e1; line-height:1.4;">{energy_desc}</div></div>""", unsafe_allow_html=True)
        hc1, hc2 = st.columns(2)
        with hc1: st.markdown(f"""<div style="background:rgba(245,158,11,0.07); border:2px solid #ea580c; border-left:6px solid #ea580c; padding:12px; margin-top:12px; margin-bottom:12px; border-radius:8px; height: 100%;"><div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:6px;"><div style="font-weight:800; color:#fb923c; font-size:0.9rem;">🛡️ {mt_title}</div><div style="font-family:'JetBrains Mono'; font-weight:800; font-size:1.1rem; color:#fb923c; background:#0d1829; padding: 4px 8px; border-radius: 4px; margin-left: 8px; white-space:nowrap;">{data['mean_threshold']:.2f}</div></div><div style="font-size:0.75rem; color:#cbd5e1; line-height:1.5;">{mt_desc}</div></div>""", unsafe_allow_html=True)
        with hc2: st.markdown(f"""<div style="border:2px solid #f87171; background:rgba(248,113,113,0.06); padding:12px; border-radius:8px; margin-top:12px; margin-bottom:12px; height: 100%;"><div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;"><div style="font-weight:800; color:#f87171; font-size:0.9rem; text-transform: uppercase;">🎯 Yakın Hedef</div><div style="font-weight:800; font-family:'JetBrains Mono'; font-size:1.2rem; color:#f87171; background:#0d1829; padding: 2px 8px; border-radius: 6px;">{data['target']:.2f}</div></div><div style="font-size:0.75rem; color:#cbd5e1; line-height:1.4; margin-bottom:10px;">{liq_desc}</div><div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; border-top:2px solid rgba(248,113,113,0.4); padding-top:6px;"><div style="font-weight:800; color:#fb923c; font-size:0.9rem;">{struct_target_label}</div><div style="font-weight:800; font-family:'JetBrains Mono'; font-size:1.1rem; color:#fb923c; background:#0d1829; padding: 2px 8px; border-radius: 6px;">{structural_target_val:.2f}</div></div><div style="font-size:0.75rem; color:#cbd5e1; line-height:1.4;">{struct_target_desc}</div></div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div style="border:2px solid #cbd5e1; background:#0d1829; border-radius:8px; padding:12px; height:100%;">
            <div style="font-weight:800; color:#f87171; font-size:0.9rem; text-transform:uppercase; border-bottom:2px solid #e2e8f0; padding-bottom:6px; margin-bottom:10px;">📍 FİYAT HARİTASI</div>
            <div style="display:grid; grid-template-columns:54% 44%; gap:10px; align-items:start;">
                <div>
                    <div style="margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid #1e3a5f;">
                        <div style="font-size:0.67rem; font-weight:800; color:#f87171; margin-bottom:3px;">📍 KONUM — {data['zone']}</div>
                        <div style="font-size:0.71rem; color:#cbd5e1; line-height:1.45;">{zone_simple}</div>
                    </div>
                    <div style="margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid #1e3a5f;">
                        <div style="font-size:0.67rem; font-weight:800; color:#38bdf8; margin-bottom:3px;">🧱 KURUM BLOĞU {ob_age_badge}</div>
                        <div style="font-size:0.71rem; color:#cbd5e1; line-height:1.45;">{ob_simple}</div>
                    </div>
                    <div style="margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid #1e3a5f;">
                        <div style="font-size:0.67rem; font-weight:800; color:#8b5cf6; margin-bottom:3px;">⚡ BOŞLUK (FVG) {fvg_age_badge}</div>
                        <div style="font-size:0.71rem; color:#cbd5e1; line-height:1.45;">{fvg_simple}</div>
                    </div>
                    <div>
                        <div style="font-size:0.67rem; font-weight:800; color:#fb923c; margin-bottom:3px;">🎯 HEDEF</div>
                        <div style="font-size:0.71rem; color:#cbd5e1; line-height:1.45;">{tgt_simple}</div>
                    </div>
                </div>
                <div>
                    {ruler_html}
                    <div style="font-size:0.65rem; line-height:1.6; margin-top:4px;">
                      <div><span style="color:#4ade80; font-weight:700;">HAVUZ:</span> <span style="color:#cbd5e1;">{data.get('eqh_eql_txt','-')}</span></div>
                      <div><span style="color:#f87171; font-weight:700;">SWEEP:</span> <span style="color:#cbd5e1;">{data.get('sweep_txt','-')}</span></div>
                    </div>
                </div>
            </div>
        </div>""", unsafe_allow_html=True)

def render_levels_card(ticker):
    data = get_advanced_levels_data(ticker)
    if not data: return
    display_ticker = get_display_name(ticker)
    info = fetch_stock_info(ticker)
    _cp_raw = info.get('price', 0) if info else 0
    current_price_str = (f"{int(_cp_raw)}" if _cp_raw >= 1000 else f"{_cp_raw:.2f}") if info else "0.00"
    
    is_bullish = data['st_dir'] == 1
    st_color = "#10b981" if is_bullish else "#16a34a" if is_bullish else "#f87171"
    st_text = "YÜKSELİŞ (AL)" if is_bullish else "DÜŞÜŞ (SAT)"
    st_icon = "🐂" if is_bullish else "🐻"
    
    # --- ORİJİNAL MANTIK VE METİNLER (DOKUNULMADI) ---
    if is_bullish:
        st_label = "Takip Eden Stop (Stop-Loss)"
        st_desc = "⚠️ Fiyat bu seviyenin <b>altına inerse</b> trend bozulur, stop olunmalıdır."
        gp_desc_text = "Kurumsal alım bölgesi (İdeal Giriş/Destek)."
        gp_desc_color = "#fbbf24"
        res_ui_label = "EN YAKIN DİRENÇ 🚧"
        res_ui_desc = "Zorlu tavan. Geçilirse yükseliş hızlanır."
        sup_ui_label = "EN YAKIN DESTEK 🛡️"
        sup_ui_desc = "İlk savunma hattı. Düşüşü tutmalı."
    else:
        st_label = "Trend Dönüşü (Direnç)"
        st_desc = "🚀 Piyasa yapıcının sipariş akışını (Order Flow) koruduğu son hattır. Yani, Fiyat bu seviyenin <b>üstüne çıkarsa</b> düşüş biter, yükseliş başlar."
        gp_desc_text = "⚠️ Güçlü Direnç / Tepki Satış Bölgesi (Short). Büyük fonların 'Discount' (İndirimli) fiyatlardan maliyetlenmek veya dağıtım yapmak için beklediği en stratejik denge noktasıdır."
        gp_desc_color = "#f87171"
        res_ui_label = "O.T.E. DİRENCİ"
        res_ui_desc = "Akıllı Para short arar. Trend yönünde satış bölgesidir. Fiyatın Fibonacci O.T.E. aralığına girmesi 'pahalı' bölgeye işarettir. Akıllı para, buradaki küçük yatırımcı alımlarını satış likiditesi olarak kullanır."
        sup_ui_label = "AŞAĞIDAKİ LİKİDİTE HEDEFİ"
        sup_ui_desc = "Düşüş trendinde destek aranmaz, kırılması beklenir. Bu seviyeler destek değil, fiyatın stopları patlatmak için çekildiği birer mıknatıstır. Kurumsal çıkış likiditesi bu bölgede aranır."
    
    sup_lbl, sup_val = data['nearest_sup']
    res_lbl, res_val = data['nearest_res']
    
    if res_lbl == "ZİRVE AŞIMI":
        res_display = "---"
        res_desc_final = "🚀 Fiyat tüm dirençleri kırdı (Price Discovery)."
    else:
        res_display = f"{res_val:.2f}"
        res_desc_final = res_ui_desc

    gp_key = next((k for k in data['fibs'].keys() if "Golden" in k), "0.618 (Golden)")
    gp_val = data['fibs'].get(gp_key, 0)
    
    html_content = f"""
    <div class="info-card" style="border-top: 3px solid #8b5cf6; padding:6px 10px 8px 10px;">
        <div class="info-header" style="color:#8b5cf6; margin-bottom:5px; display:flex; justify-content:space-between; align-items:center; padding:0; font-size:0.9rem; font-weight:800;">
        <span>📐 Trend & Seviyeler: {display_ticker}</span>
        <span style="font-family:'JetBrains Mono'; font-weight:800; color:#f1f5f9; font-size:0.9rem; background:#0d1829; padding:1px 6px; border-radius:5px;">{current_price_str}</span>
        </div>

        <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:5px;">

            <div style="background:{st_color}15; padding:5px 6px; border-radius:4px; border:1px solid {st_color};">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div style="font-weight:700; color:{st_color}; font-size:0.75rem;">{st_icon} SuperTrend</div>
                    <div style="font-weight:800; color:{st_color}; font-size:0.75rem;">{st_text}</div>
                </div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-top:3px;">
                    <div style="font-size:0.72rem; color:#cbd5e1;">{st_label}:</div>
                    <div style="font-family:'JetBrains Mono'; font-weight:800; color:#f1f5f9; font-size:0.82rem;">{data['st_val']:.2f}</div>
                </div>
                <div style="font-size:0.72rem; color:#cbd5e1; font-style:italic; margin-top:2px; line-height:1.25;">{st_desc}</div>
            </div>

            <div style="background:rgba(16,185,129,0.07); padding:5px 6px; border-radius:4px; border:1px solid rgba(74,222,128,0.3);">
                <div style="font-size:0.75rem; color:#4ade80; font-weight:700;">{res_ui_label}</div>
                <div style="font-family:'JetBrains Mono'; font-weight:800; color:#4ade80; font-size:0.88rem; margin-top:1px;">{res_display}</div>
                <div style="font-size:0.72rem; color:#4ade80; font-weight:600; margin-top:3px;">Fib {res_lbl}</div>
                <div style="font-size:0.72rem; color:#cbd5e1; font-style:italic; margin-top:2px; line-height:1.25;">{res_desc_final}</div>
            </div>

            <div style="background:rgba(248,113,113,0.08); padding:5px 6px; border-radius:4px; border:1px solid rgba(248,113,113,0.3);">
                <div style="font-size:0.75rem; color:#f87171; font-weight:700;">{sup_ui_label}</div>
                <div style="font-family:'JetBrains Mono'; font-weight:800; color:#f87171; font-size:0.88rem; margin-top:1px;">{sup_val:.2f}</div>
                <div style="font-size:0.72rem; color:#f87171; font-weight:600; margin-top:3px;">Fib {sup_lbl}</div>
                <div style="font-size:0.72rem; color:#cbd5e1; font-style:italic; margin-top:2px; line-height:1.25;">{sup_ui_desc}</div>
            </div>

            <div style="background:rgba(245,158,11,0.07); padding:5px 6px; border-radius:4px; border:1px dashed #f59e0b;">
                <div style="font-size:0.75rem; font-weight:700; color:#fbbf24;">⚜️ GOLDEN POCKET</div>
                <div style="font-family:'JetBrains Mono'; font-size:0.88rem; font-weight:800; color:#fbbf24; margin-top:1px;">{gp_val:.2f}</div>
                <div style="font-size:0.72rem; color:#fbbf24; font-weight:600; margin-top:3px;">Kurumsal Bölge</div>
                <div style="font-size:0.72rem; color:#cbd5e1; font-style:italic; margin-top:2px; line-height:1.25;">{gp_desc_text}</div>
            </div>

        </div>
    </div>
    """
    st.markdown(html_content.replace("\n", " "), unsafe_allow_html=True)

def render_minervini_panel_v2(ticker):
    # 1. Verileri al
    cat = st.session_state.get('category', 'S&P 500')
    bench = "XU100.IS" if "BIST" in cat else "^GSPC"
    
    data = calculate_minervini_sepa(ticker, benchmark_ticker=bench)
    
    if not data: return 

    # --- HİSSE ADINI HAZIRLA ---
    display_ticker = get_display_name(ticker)

    # 2. Görsel öğeleri hazırla
    trend_icon = "✅" if data['trend_ok'] else "❌"
    vcp_icon = "✅" if data['is_vcp'] else "❌"
    vol_icon = "✅" if data['is_dry'] else "❌"
    rs_icon = "✅" if data['rs_val'] > 0 else "❌"
    
    rs_width = min(max(int(data['rs_val'] * 5 + 50), 0), 100)
    rs_color = "#16a34a" if data['rs_val'] > 0 else "#f87171"
    
    # 3. HTML KODU (HİSSE ADI EKLENDİ)
    html_content = f"""
<div class="info-card" style="border-top: 3px solid {data['color']};">
<div class="info-header" style="display:flex; justify-content:space-between; align-items:center; color:{data['color']};">
<span>🦁 Minervini SEPA Analizi</span>
<span style="font-size:0.8rem; font-weight:800; background:{data['color']}15; padding:2px 8px; border-radius:10px;">{data['score']}/100</span>
</div>
<div style="text-align:center; margin-bottom:5px;">
<div style="font-size:0.9rem; font-weight:800; color:{data['color']}; letter-spacing:0.5px;">{display_ticker} | {data['Durum']}</div>
</div>
<div class="edu-note" style="text-align:center; margin-bottom:10px;">
"Aşama 2" yükseliş trendi ve düşük oynaklık (VCP) aranıyor.
</div>
<div style="display:grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap:4px; margin-bottom:5px; text-align:center;">
<div style="background:#0d1829; padding:4px; border-radius:4px; border:1px solid #1e3a5f;">
<div style="font-size:0.6rem; color:#64748B; font-weight:700;">TREND</div>
<div style="font-size:1rem;">{trend_icon}</div>
</div>
<div style="background:#0d1829; padding:4px; border-radius:4px; border:1px solid #1e3a5f;">
<div style="font-size:0.6rem; color:#64748B; font-weight:700;">VCP</div>
<div style="font-size:1rem;">{vcp_icon}</div>
</div>
<div style="background:#0d1829; padding:4px; border-radius:4px; border:1px solid #1e3a5f;">
<div style="font-size:0.6rem; color:#64748B; font-weight:700;">ARZ</div>
<div style="font-size:1rem;">{vol_icon}</div>
</div>
<div style="background:#0d1829; padding:4px; border-radius:4px; border:1px solid #1e3a5f;">
<div style="font-size:0.6rem; color:#64748B; font-weight:700;">RS</div>
<div style="font-size:1rem;">{rs_icon}</div>
</div>
</div>
<div class="edu-note">
1. <b>Trend:</b> Fiyat > SMA200 (Yükseliş Trendinde vs Yatayda-Düşüşte)<br>
2. <b>VCP:</b> Fiyat sıkışıyor mu? (Düşük Oynaklık vs Dalgalı-Dengesiz Yapı)<br>
3. <b>Arz:</b> Düşüş günlerinde hacim daralıyor mu? (Satıcılar yoruldu vs Düşüşlerde hacim yüksek)<br>
4. <b>RS:</b> Endeksten daha mı güçlü? (Endeks düşerken bu hisse duruyor veya yükseliyor vs Endeksle veya daha çok düşüyor)
</div>
<div style="margin-bottom:2px; margin-top:8px;">
<div style="display:flex; justify-content:space-between; font-size:0.7rem; margin-bottom:2px;">
<span style="color:#64748B; font-weight:600;">Endeks Gücü (Mansfield RS)</span>
<span style="font-weight:700; color:{rs_color};">{data['rs_rating']}</span>
</div>
<div style="width:100%; height:6px; background:#0d1829; border-radius:3px; overflow:hidden;">
<div style="width:{rs_width}%; height:100%; background:{rs_color};"></div>
</div>
</div>
<div class="edu-note">Bar yeşil ve doluysa hisse endeksi yeniyor (Lider).</div>
<div style="margin-top:6px; padding-top:4px; border-top:1px dashed #cbd5e1; font-size:0.7rem; color:#475569; display:flex; justify-content:space-between;">
<span>SMA200: {data['sma200']:.2f}</span>
<span>52H Zirve: {data['year_high']:.2f}</span>
</div>
<div class="edu-note">Minervini Kuralı: Fiyat 52 haftalık zirveye %25'ten fazla uzak olmamalı.</div>
</div>
"""
    
    st.markdown(html_content, unsafe_allow_html=True)

# --- MİNİ FORMASYON GRAFİĞİ (tüm pattern tipleri) ---
@st.cache_data(ttl=900, show_spinner=False)
def _mini_pattern_chart_b64(symbol, chart_data, dark_mode):
    """Her formasyon tipine göre mum grafik çizer. Döndürür: base64 PNG string."""
    try:
        from matplotlib.patches import Rectangle
        df = get_safe_historical_data(symbol, period="1y")
        if df is None or df.empty:
            return ""

        close     = df['Close']
        opens_s   = df['Open']
        highs_s   = df['High']
        lows_s    = df['Low']
        date_strs = [str(d.date()) for d in df.index]
        bar_total = len(df)
        pat_type  = chart_data['type']

        def date_to_bar(d_str):
            try:
                return date_strs.index(d_str)
            except ValueError:
                from datetime import date as _date
                tgt = _date.fromisoformat(d_str)
                return min(range(bar_total), key=lambda i: abs((df.index[i].date() - tgt).days))

        # --- Her zaman son 60 bar ---
        bar_start = max(0, bar_total - 60)
        bar_end   = bar_total - 1
        n         = bar_end - bar_start + 1

        sl_open  = list(opens_s.iloc[bar_start:bar_end + 1].values)
        sl_high  = list(highs_s.iloc[bar_start:bar_end + 1].values)
        sl_low   = list(lows_s.iloc[bar_start:bar_end + 1].values)
        sl_close = list(close.iloc[bar_start:bar_end + 1].values)

        y_all   = sl_high + sl_low
        y_range = (max(y_all) - min(y_all)) or 1

        def d2x(d_str):
            return date_to_bar(d_str) - bar_start

        # --- Renkler ---
        bg_c      = "#f0f4f8"
        axis_c    = "#94a3b8"
        up_c      = "#26a69a"   # yeşil mum
        dn_c      = "#ef5350"   # kırmızı mum

        fig, ax = plt.subplots(figsize=(5.4, 2.8), facecolor=bg_c)
        ax.set_facecolor(bg_c)

        # --- Mum grafiği ---
        cw = 0.55  # mum gövde genişliği
        for i, (o, h, l, c_) in enumerate(zip(sl_open, sl_high, sl_low, sl_close)):
            color = up_c if c_ >= o else dn_c
            ax.plot([i, i], [l, h], color=color, linewidth=0.7, zorder=3)
            body_bot = min(o, c_)
            body_h   = abs(c_ - o) or y_range * 0.002
            rect = Rectangle((i - cw / 2, body_bot), cw, body_h,
                              facecolor=color, edgecolor=color, linewidth=0, zorder=4, alpha=0.9)
            ax.add_patch(rect)

        def _lbl(px, py, txt, color, is_high):
            if not (0 <= px < n):
                return
            dy = -y_range * 0.06 if is_high else y_range * 0.06
            ax.text(px, py + dy, txt, color=bg_c, fontsize=6,
                    ha='center', va='top' if is_high else 'bottom', fontweight='bold',
                    bbox=dict(boxstyle='round,pad=0.15', facecolor=color, alpha=0.92, edgecolor='none'))

        def _hline(price, color, label="", ls='--', lw=1.3):
            ax.hlines(price, xmin=0, xmax=n - 1, colors=color, linewidths=lw, linestyles=ls, alpha=0.9)
            if label:
                ax.text(n - 2, price, f" {label}", color=color, fontsize=6,
                        va='bottom', ha='right', fontweight='bold')

        # --- Formasyon overlay'leri ---
        if pat_type == "cup":
            piv_x = [d2x(d) for d in chart_data['pivot_dates']]
            piv_y = chart_data['pivot_prices']
            piv_t = chart_data['pivot_types']
            cup_c, hnd_c = "#38bdf8", "#a78bfa"
            ax.plot(piv_x[:3], piv_y[:3], color=cup_c, lw=2.2, marker='o', ms=5, zorder=5)
            if len(piv_x) >= 4:
                ax.plot(piv_x[2:4], piv_y[2:4], color=hnd_c, lw=1.8, marker='o', ms=5, ls='--', zorder=5)
            _hline(chart_data['neck'], "#f59e0b", f"{chart_data['neck']:.2f}")
            for px, py, pt in zip(piv_x, piv_y, piv_t):
                _lbl(px, py, pt, cup_c, pt == 'H')

        elif pat_type == "tobo":
            piv_x = [d2x(d) for d in chart_data['pivot_dates']]
            piv_y = chart_data['pivot_prices']
            piv_t = chart_data['pivot_types']
            col   = "#fb923c"
            ax.plot(piv_x, piv_y, color=col, lw=2.2, marker='o', ms=5, zorder=5)
            _hline(chart_data['neck'], "#f59e0b", f"{chart_data['neck']:.2f}")
            for px, py, pt in zip(piv_x, piv_y, piv_t):
                _lbl(px, py, pt, col, pt == 'H')

        elif pat_type == "flag":
            fh, fl = chart_data['flag_h'], chart_data['flag_l']
            pe_x   = d2x(chart_data['pole_end_date'])
            ax.axhspan(fl, fh, alpha=0.07, color='#f59e0b')
            _hline(fh, "#ef4444", f"{fh:.2f}")
            _hline(fl, "#10b981", f"{fl:.2f}", ls='-.')
            if 0 <= pe_x < n:
                ax.axvline(x=pe_x, color='#f59e0b', lw=1, ls=':', alpha=0.7)

        elif pat_type == "triangle":
            _hline(chart_data['resistance'], "#ef4444", f"{chart_data['resistance']:.2f}", lw=1.8)
            piv_dates  = chart_data.get('pivot_dates', [])
            piv_prices = chart_data.get('pivot_prices', [])
            piv_types  = chart_data.get('pivot_types', [])
            if piv_dates:
                lows_x = [d2x(d) for d, p, t in zip(piv_dates, piv_prices, piv_types) if t == 'L']
                lows_y = [float(p) for d, p, t in zip(piv_dates, piv_prices, piv_types) if t == 'L']
                if len(lows_x) >= 2:
                    ax.plot(lows_x, lows_y, color="#38bdf8", lw=1.8, marker='o', ms=4, zorder=5)
                    ax.plot([lows_x[0], lows_x[-1]], [lows_y[0], lows_y[-1]],
                            color="#38bdf8", lw=1, ls='--', alpha=0.5)

        elif pat_type == "range":
            res, sup = chart_data['resistance'], chart_data['support']
            ax.axhspan(sup, res, alpha=0.05, color='#94a3b8')
            _hline(res, "#ef4444", f"{res:.2f}", lw=1.5)
            _hline(sup, "#10b981", f"{sup:.2f}", lw=1.5, ls='-.')

        elif pat_type == "saucer":
            _hline(chart_data['right_high'], "#a78bfa", f"{chart_data['right_high']:.2f}", lw=1.5)
            _hline(chart_data['cup_bottom'],  "#38bdf8", f"{chart_data['cup_bottom']:.2f}",  lw=1, ls=':')
            _hline(chart_data['left_high'],   "#64748b", ls=':', lw=0.8)

        elif pat_type == "qml":
            piv_x = [d2x(d) for d in chart_data['pivot_dates']]
            piv_y = chart_data['pivot_prices']
            piv_t = chart_data['pivot_types']
            col   = "#38bdf8"
            ax.plot(piv_x, piv_y, color=col, lw=2, marker='o', ms=5, zorder=5)
            _hline(chart_data['qml_line'], "#f59e0b", f"QML {chart_data['qml_line']:.2f}", lw=1.5)
            for px, py, pt in zip(piv_x, piv_y, piv_t):
                _lbl(px, py, pt, col, pt == 'H')

        elif pat_type == "three_drive":
            piv_x = [d2x(d) for d in chart_data['pivot_dates']]
            piv_y = chart_data['pivot_prices']
            col   = "#fb923c"
            ax.plot(piv_x, piv_y, color=col, lw=2, marker='v', ms=7, zorder=5)
            if len(piv_x) >= 2:
                ax.plot([piv_x[0], piv_x[-1]], [piv_y[0], piv_y[-1]],
                        color=col, lw=1, ls='--', alpha=0.5)
            for i, (px, py) in enumerate(zip(piv_x, piv_y)):
                if 0 <= px < n:
                    ax.text(px, py - y_range * 0.07, f"D{i+1}", color=col,
                            fontsize=6, ha='center', va='top', fontweight='bold')

        elif pat_type == "sr_level":
            lvl = chart_data['level']
            col = "#10b981" if chart_data['is_support'] else "#ef4444"
            lbl = "Destek" if chart_data['is_support'] else "Direnç"
            ax.axhspan(lvl * 0.992, lvl * 1.008, alpha=0.12, color=col)
            _hline(lvl, col, f"{lvl:.2f} ({lbl})", lw=2)

        # --- Tarih ekseni: 4 tick (sol uç, 2 ara, sağ uç) ---
        tick_pos = [0, n // 3, 2 * n // 3, n - 1]
        tick_lbl = []
        for tp in tick_pos:
            actual = bar_start + tp
            actual = min(actual, bar_total - 1)
            tick_lbl.append(df.index[actual].strftime("%d %b '%y"))
        ax.set_xticks(tick_pos)
        ax.set_xticklabels(tick_lbl, fontsize=6.5, color=axis_c)

        # --- Eksen stili ---
        for sp in ['top', 'left', 'right']:
            ax.spines[sp].set_visible(False)
        ax.spines['bottom'].set_color(axis_c)
        ax.spines['bottom'].set_linewidth(0.5)
        ax.tick_params(left=False, right=False, labelleft=False, labelright=False,
                       bottom=False, labelbottom=True, pad=3)
        ax.set_xlim(-0.5, n - 0.5)

        plt.tight_layout(pad=0.15)
        buf_io = io.BytesIO()
        fig.savefig(buf_io, format='png', dpi=120, bbox_inches='tight', pad_inches=0.06, facecolor=bg_c)
        plt.close(fig)
        buf_io.seek(0)
        return base64.b64encode(buf_io.read()).decode()
    except Exception:
        return ""

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
        import yfinance as yf

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


def calculate_8_point_roadmap(ticker):
    """Fiyat davranışı (PA), VSA, Hacim ve Trend algoritmalarını birleştiren sentez model."""
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
            _hint = ('<br><span style="color:#38bdf8;font-weight:600;font-size:11px;">'
                     '🔍 Detaylı grafik ve kilit seviyeleri için aşağıdaki butona tıklayın</span>') if chart_dat else ""
            m2 = (f"<b>Mevcut Formasyon:</b> {pat_name}<br>"
                  f"<b>Ana Yapı:</b> {'İtki (Trend)' if cp > sma50 else 'Düzeltme (Pullback)'}{_hint}")
        else:
            chart_dat = None
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
            pct = (val / max_val) * 100
            c = "139,92,246"
            score_color = "#fff" if val >= max_val else "rgba(76,29,149,0.9)"
            return (
                f"<div style='margin-bottom:3px;'>"
                f"<div style='background:rgba({c},0.12);border-radius:3px;height:15px;position:relative;overflow:hidden;'>"
                f"<div style='width:{pct:.0f}%;background:rgba({c},0.72);height:100%;border-radius:3px;'></div>"
                f"<span style='position:absolute;left:0;top:0;width:100%;height:100%;display:flex;align-items:center;"
                f"justify-content:space-between;padding:0 5px;box-sizing:border-box;'>"
                f"<span style='font-size:0.6rem;font-weight:700;color:#fff;'>{label}</span>"
                f"<span style='font-size:0.6rem;font-weight:800;color:{score_color};'>{val}/{max_val}</span>"
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
            _cat = st.session_state.get('category', 'BIST')
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
                ozet_metin = f"Fiyat {fmt(sup_20)} ile {fmt(res_20)} arasında sıkışmış, yön arayışında olan bir testere (Choppy) piyasasında. Ne alıcılar ne de satıcılar tam kontrol sağlayabilmiş değil. Kırılım yönüne göre (Breakout/Breakdown) pozisyon almak en güvenli stratejidir."
            
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
        if composite_score >= 70:   _comp_decision, _comp_color = "AL", "#16a34a"
        elif composite_score >= 55: _comp_decision, _comp_color = "DİKKAT/İZLE", "#ca8a04"
        elif composite_score >= 40: _comp_decision, _comp_color = "BEKLEMEDE", "#d97706"
        else:                       _comp_decision, _comp_color = "UZAK DUR", "#f87171"

        return {
            "M1": m1, "M2": m2, "M3": m3, "M4": m4, "M5": m5, "M6": m6, "M7": m7, "M8": m8,
            "M2_chart_data": chart_dat,
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

def _pattern_side_info_html(chart_data, curr_price, dark_mode):
    """Geriye dönük uyumluluk için korundu — dialog artık _build_pattern_analysis kullanır."""
    return ""


def _build_pattern_analysis(chart_data, curr_price, ticker):
    """Formasyon için zenginleştirilmiş analiz verisi üretir."""
    import datetime as _dt

    pat_type = chart_data.get('type', '')
    def fp(v):
        try: return f"{v:,.2f}" if v < 1000 else f"{int(v):,}"
        except: return str(v)
    def pct(a, b):
        try: return f"{((a - b) / b * 100):+.1f}%"
        except: return ""

    _LABELS = {
        "cup":         ("☕", "Fincan & Kulp"),
        "tobo":        ("📐", "Ters OBO (TOBO)"),
        "flag":        ("🚩", "Boğa Bayrağı"),
        "triangle":    ("📈", "Yükselen Üçgen"),
        "range":       ("↔️", "Range / Konsolidasyon"),
        "saucer":      ("🥣", "Çanak (Saucer)"),
        "qml":         ("🎯", "Quasimodo (QML)"),
        "three_drive": ("3️⃣", "3 Drive"),
        "sr_level":    ("🧱", "Destek / Direnç"),
    }
    emoji, name = _LABELS.get(pat_type, ("📊", "Formasyon"))

    target = None; invalid = None; levels = []
    stage = 2; stage_total = 4; stage_label = "Gelişiyor"
    story = ""; conclusion = ""

    if pat_type == "cup":
        neck = chart_data['neck']; bottom = chart_data['pivot_prices'][1]
        target = neck + (neck - bottom); invalid = bottom * 0.99
        levels = [("Boyun Çizgisi (Kırılım)", neck, "#f59e0b"), ("Kupa Dibi", bottom, "#38bdf8")]
        stage = 3 if curr_price > neck * 0.97 else 2
        stage_label = "Boyun Yaklaşıyor" if stage == 3 else "Sağ Taraf Oluşuyor"
        story = (f"Fiyat büyük bir düşüşün ardından yavaş yavaş toparlandı ve U şekilli bir kupa oluşturdu. "
                 f"Boyun çizgisi olan <b>{fp(neck)}</b> bölgesine yaklaşılıyor. "
                 f"Burası geçilirse formasyon tamamlanmış sayılır ve ölçü hedefi devreye girer.")
        conclusion = ((f"Fiyat boyun çizgisini kırdı — formasyon aktif. Hedef <b>{fp(target)}</b> ({pct(target, curr_price)}). "
                       f"<b>{fp(invalid)}</b> altına kapanış formasyonu geçersiz kılar.")
                      if curr_price > neck else
                      (f"Fiyat henüz boyun çizgisi <b>{fp(neck)}</b>'i kırmadı — sabırla bekle. "
                       f"Kırılım gelirse hedef <b>{fp(target)}</b>. <b>{fp(invalid)}</b> altına kapanış bozulma sinyali."))

    elif pat_type == "tobo":
        neck = chart_data['neck']; bottom = chart_data['pivot_prices'][2]
        target = neck + (neck - bottom); invalid = bottom * 0.98
        levels = [("Boyun Çizgisi (Kırılım)", neck, "#f59e0b"), ("Orta Dip (Baş)", bottom, "#38bdf8")]
        stage = 3 if curr_price > neck * 0.97 else 2
        stage_label = "Boyun Yaklaşıyor" if stage == 3 else "Sağ Omuz Oluşuyor"
        story = (f"Üç dip noktası oluştu; ortadaki en düşük seviye olan <b>{fp(bottom)}</b> 'baş' konumunda. "
                 f"Bu klasik dip dönüş formasyonunda boyun çizgisi <b>{fp(neck)}</b>. "
                 f"Boyun kırılırsa uzun süredir baskı altında olan satıcılar teslim olmuş demektir.")
        conclusion = (f"Boyun çizgisi <b>{fp(neck)}</b> kırılırsa formasyon tamamlanır, hedef <b>{fp(target)}</b> ({pct(target, curr_price)}). "
                      f"Stop için <b>{fp(invalid)}</b> altı kullanılabilir. "
                      f"Bu formasyon özellikle uzun bir düşüş trendinin sonunda görünce anlamlıdır.")

    elif pat_type == "flag":
        fh, fl = chart_data['flag_h'], chart_data['flag_l']
        target = fh + (fh - fl); invalid = fl * 0.99
        levels = [("Üst Sınır / Kırılım", fh, "#ef4444"), ("Alt Destek", fl, "#10b981")]
        stage = 3 if curr_price > fh * 0.98 else 2
        stage_label = "Kırılıma Hazır" if stage == 3 else "Bayrak Kanalında"
        story = (f"Sert bir yükseliş ('direk') sonrasında fiyat <b>{fp(fl)}–{fp(fh)}</b> aralığında daralarak nefes alıyor. "
                 f"Bu daralma satışın değil, normal bir sindirim sürecinin göstergesi. "
                 f"Üst sınır kırılırsa direk boyu kadar ek yükseliş beklenir.")
        conclusion = (f"<b>{fp(fh)}</b> üzerinde hacimli kapanış kırılım sinyali, hedef <b>{fp(target)}</b> ({pct(target, curr_price)}). "
                      f"Alt destek <b>{fp(fl)}</b> kırılırsa formasyon bozulur. "
                      f"Bant ne kadar dar ve uzun sürerse kırılım o kadar güçlü olur.")

    elif pat_type == "triangle":
        res = chart_data['resistance']; invalid = res * 0.96
        levels = [("Direnç / Kırılım Noktası", res, "#ef4444")]
        stage = 3 if curr_price > res * 0.97 else 2
        stage_label = "Direce Dayandı" if stage == 3 else "Dipler Yükseliyor"
        story = (f"Fiyat giderek yükselen dip noktaları yaparken <b>{fp(res)}</b> direncinde takılmış durumda. "
                 f"Bu sıkışma, alıcıların her seferinde daha pahalıya almaya razı olduğunu gösteriyor. "
                 f"Direnç kırılırsa birikmiş enerji serbest kalır.")
        conclusion = (f"<b>{fp(res)}</b> üzerinde kapanış kırılım sayılır. Stop için <b>{fp(invalid)}</b> altı mantıklı. "
                      f"Kırılım ne kadar hacimli olursa formasyon o kadar güvenilirdir.")

    elif pat_type == "range":
        res, sup = chart_data['resistance'], chart_data['support']
        target = res + (res - sup); invalid = sup * 0.99
        levels = [("Üst Bant / Kırılım", res, "#ef4444"), ("Alt Destek", sup, "#10b981")]
        stage = 3 if curr_price > res * 0.97 else (2 if curr_price > sup * 1.01 else 1)
        stage_label = "Kırılım Eşiğinde" if stage == 3 else "Bant İçinde"
        story = (f"Fiyat <b>{fp(sup)}–{fp(res)}</b> bandında gidip geliyor. "
                 f"Her üst banda gelişte satıcılar baskı uyguluyor, her dipte alıcılar tutuyor. "
                 f"Bu kutu kırılırsa bant genişliği kadar ek hareket beklenir.")
        conclusion = (f"<b>{fp(res)}</b> üzerinde kapanış uzun vadeli kırılım sinyali, hedef <b>{fp(target)}</b> ({pct(target, curr_price)}). "
                      f"Tersine <b>{fp(invalid)}</b> altına kapanış düşüş sinyali. Bant ne kadar uzun sürerse kırılım o kadar güçlüdür.")

    elif pat_type == "saucer":
        boyun = chart_data['right_high']; bottom = chart_data['cup_bottom']
        target = boyun + (boyun - bottom); invalid = bottom * 0.99
        levels = [("Boyun Çizgisi", boyun, "#a78bfa"), ("Çanak Dibi", bottom, "#38bdf8")]
        stage = 3 if curr_price > boyun * 0.97 else 2
        stage_label = "Boyun Yaklaşıyor" if stage == 3 else "Sağ Taraf Toparlanıyor"
        story = (f"Fiyat uzun süre yatay seyrettikten sonra yuvarlak bir dip oluşturdu. "
                 f"Çanak dibi <b>{fp(bottom)}</b>, boyun çizgisi <b>{fp(boyun)}</b>. "
                 f"Bu formasyon kurumsal para birikiminin yavaş yavaş gerçekleştiğini gösterir.")
        conclusion = (f"Boyun çizgisi <b>{fp(boyun)}</b> kırılırsa hedef <b>{fp(target)}</b> ({pct(target, curr_price)}). "
                      f"<b>{fp(invalid)}</b> altına kapanış bozulma sinyali. "
                      f"Fincan-Kulp'a göre daha yavaş ama daha güvenilir bir formasyon olarak bilinir.")

    elif pat_type == "qml":
        qml = chart_data['qml_line']; invalid = qml * 0.995
        levels = [("QML Çizgisi", qml, "#f59e0b")]
        above = curr_price > qml; margin = abs(curr_price - qml) / qml
        stage = (3 if above and margin > 0.03 else 2)
        stage_label = "QML Üstünde Tutunuyor" if (above and margin > 0.03) else ("QML Üstünde — Teyit Bekleniyor" if above else "QML Test Ediliyor")
        target = qml * 1.10
        story = (f"Fiyat önce <b>{fp(qml)}</b> seviyesini aşağı kırdı (sahte kırılım) ve satıcıları tuzağa düşürdü. "
                 f"Ardından hızla geri dönerek QML çizgisinin üstüne çıktı — bu 'tuzak' hareketi Quasimodo formasyonunun özüdür. "
                 f"Şimdi kritik soru: fiyat bu seviyede tutunabilecek mi?")
        conclusion = ((f"Fiyat QML çizgisi <b>{fp(qml)}</b> üzerinde — olumlu işaret. "
                       f"Tutunulursa tahmini hedef <b>{fp(target)}</b> ({pct(target, curr_price)}). "
                       f"Ancak <b>{fp(invalid)}</b> altına kapanış formasyonu geçersiz kılar; stop buraya konulabilir. "
                       f"Hacimle desteklenmiş yeşil mumlar teyit olarak değerlendirilebilir.")
                      if above else
                      (f"Fiyat hâlâ QML çizgisi <b>{fp(qml)}</b> altında. Formasyon henüz tamamlanmadı. "
                       f"QML üzerine çıkış ve orada kapanış beklenmeli. "
                       f"Tutunursa hedef <b>{fp(target)}</b>, tutunmazsa formasyon bozulur."))

    elif pat_type == "three_drive":
        pivots = chart_data['pivot_prices']
        invalid = float(pivots[0]) * 0.99 if pivots else None
        levels = [(f"Dip {i+1}", float(p), "#fb923c") for i, p in enumerate(pivots)]
        stage = min(len(pivots), 3); stage_total = 3
        stage_label = f"Dip {stage}/3 Oluştu"
        story = (f"Üç ardışık dip noktası oluştu; her biri bir öncekinden daha yüksek. "
                 f"Bu, alıcıların giderek artan iştahını ve satıcıların güç kaybettiğini gösteriyor. "
                 f"Üçüncü dipten sonra gelen yükseliş formasyonun tamamlanma sinyalidir.")
        conclusion = (f"Üçüncü dip oluştu ve fiyat yukarı döndüyse alım bölgesindeyiz. "
                      f"Stop için <b>{fp(invalid)}</b> altı kullanılabilir. "
                      f"Her yeni dip bir öncekinden yüksekte olduğu sürece formasyon geçerliliğini korur.")

    elif pat_type == "sr_level":
        lvl = chart_data['level']; is_sup = chart_data['is_support']
        col = "#10b981" if is_sup else "#ef4444"
        levels = [(("Kritik Destek" if is_sup else "Kritik Direnç"), lvl, col)]
        invalid = lvl * (0.985 if is_sup else 1.015); target = lvl * (1.08 if is_sup else 0.92)
        stage = 2; stage_label = "Bölge Test Ediliyor"
        story = (f"<b>{fp(lvl)}</b> seviyesi geçmişte defalarca test edilmiş, her seferinde güçlü tepki vermiş. "
                 f"Bu tür seviyeler piyasanın 'hafızasında' olan yerlerdir — kurumlar burada pozisyon almayı sever. "
                 f"Mevcut test de aynı tepkiyi verebilir.")
        conclusion = (f"{'Destek' if is_sup else 'Direnç'} bölgesi <b>{fp(lvl)}</b> tutunursa hedef <b>{fp(target)}</b> ({pct(target, curr_price)}). "
                      f"<b>{fp(invalid)}</b> {'altına' if is_sup else 'üstüne'} kapanış bölgenin kırıldığını gösterir. "
                      f"Ne kadar çok test edilmişse o kadar güçlüdür — ama çok kez test sonunda kırılma riski de artar.")

    # Hacim teyidi
    vol_dip_ok = None; vol_bounce_ok = None
    try:
        df_vol = get_safe_historical_data(ticker)
        if df_vol is not None and len(df_vol) >= 22:
            volume = df_vol['Volume'].squeeze(); vol20 = volume.rolling(20).mean()
            pivot_dates = chart_data.get('pivot_dates', [])
            if len(pivot_dates) >= 2:
                dip_date = pd.Timestamp(pivot_dates[-2]); bounce_date = pd.Timestamp(pivot_dates[-1])
                di = df_vol.index.get_indexer([dip_date], method='nearest')[0]
                bi = df_vol.index.get_indexer([bounce_date], method='nearest')[0]
                dr = float(volume.iloc[di] / vol20.iloc[di]) if vol20.iloc[di] > 0 else 1.0
                br = float(volume.iloc[bi] / vol20.iloc[bi]) if vol20.iloc[bi] > 0 else 1.0
                vol_dip_ok = dr < 0.85; vol_bounce_ok = br > 1.15
    except: pass

    # Formasyon yaşı
    pat_age_days = 0; pat_start_str = "—"
    try:
        pivot_dates = chart_data.get('pivot_dates', [])
        if pivot_dates:
            start = _dt.date.fromisoformat(str(pivot_dates[0])[:10])
            pat_age_days = (_dt.date.today() - start).days
            _ay = ["Oca","Şub","Mar","Nis","May","Haz","Tem","Ağu","Eyl","Eki","Kas","Ara"]
            pat_start_str = f"{start.day} {_ay[start.month-1]} '{str(start.year)[2:]}"
    except: pass

    # R/R hesabı
    rr_ratio = None; rr_str = "—"
    if target and invalid and curr_price:
        try:
            reward = abs(target - curr_price); risk = abs(curr_price - invalid)
            if risk > 0:
                rr_ratio = reward / risk
                rr_str = f"1 : {rr_ratio:.1f}"
        except: pass

    return {"emoji": emoji, "name": name, "target": target, "invalid": invalid, "levels": levels,
            "stage": stage, "stage_total": stage_total, "stage_label": stage_label,
            "rr_ratio": rr_ratio, "rr_str": rr_str,
            "vol_dip_ok": vol_dip_ok, "vol_bounce_ok": vol_bounce_ok,
            "pat_age_days": pat_age_days, "pat_start_str": pat_start_str,
            "story": story, "conclusion": conclusion, "fp": fp, "pct": pct}


def _mini_harmonic_chart_b64(symbol, harm_res, dark_mode):
    """XABCD Harmonik formasyon için mum grafik — base64 PNG."""
    try:
        from matplotlib.patches import Rectangle as _Rect
        df = get_safe_historical_data(symbol, period="1y")
        if df is None or df.empty:
            return ""

        h_arr = df['High'].values; l_arr = df['Low'].values
        o_arr = df['Open'].values; c_arr = df['Close'].values
        n_total = len(df)

        p_idx     = harm_res.get('pivot_idx', [])
        p_prices  = harm_res.get('pivot_prices', [])
        state     = harm_res.get('state', 'fresh')
        prz       = harm_res.get('prz', 0)
        direction = harm_res.get('direction', 'Bullish')

        valid_idx = [i for i in p_idx if i is not None]
        if not valid_idx:
            return ""
        first_bar = max(0, min(valid_idx) - 5)
        last_bar  = min(n_total - 1, max(valid_idx) + 8 if state == 'fresh' else n_total - 1)
        if last_bar - first_bar < 40:
            first_bar = max(0, last_bar - 40)
        n = last_bar - first_bar + 1

        sl_o = list(o_arr[first_bar:last_bar + 1]); sl_h = list(h_arr[first_bar:last_bar + 1])
        sl_l = list(l_arr[first_bar:last_bar + 1]); sl_c = list(c_arr[first_bar:last_bar + 1])
        y_all = sl_h + sl_l; y_range = (max(y_all) - min(y_all)) or 1

        bg_c  = "#f0f4f8"
        up_c  = "#26a69a"; dn_c = "#ef5350"
        zz_c  = "#a78bfa" if direction == 'Bullish' else "#f472b6"
        prz_c = "#f59e0b"
        axis_c = "#94a3b8"

        fig, ax = plt.subplots(figsize=(5.4, 2.8), facecolor=bg_c)
        ax.set_facecolor(bg_c)
        cw = 0.55
        for i, (o, h, l, c_) in enumerate(zip(sl_o, sl_h, sl_l, sl_c)):
            color = up_c if c_ >= o else dn_c
            ax.plot([i, i], [l, h], color=color, linewidth=0.7, zorder=3)
            body_bot = min(o, c_); body_h = abs(c_ - o) or y_range * 0.002
            rect = _Rect((i - cw / 2, body_bot), cw, body_h,
                         facecolor=color, edgecolor=color, linewidth=0, zorder=4, alpha=0.9)
            ax.add_patch(rect)

        # XABCD zigzag
        labels = ['X', 'A', 'B', 'C', 'D']
        xs = []; ys = []
        for k, (idx, price) in enumerate(zip(p_idx, p_prices)):
            if idx is None or price is None:
                continue
            bar_x = idx - first_bar
            if 0 <= bar_x < n:
                xs.append(bar_x); ys.append(price)
                is_high = (k % 2 == 1) if direction == 'Bullish' else (k % 2 == 0)
                dy = -y_range * 0.07 if is_high else y_range * 0.07
                lbl_col = zz_c if k < 4 else prz_c
                ax.text(bar_x, price + dy, labels[k], color=bg_c, fontsize=7.5,
                        ha='center', va='top' if is_high else 'bottom', fontweight='bold',
                        bbox=dict(boxstyle='round,pad=0.18', facecolor=lbl_col, alpha=0.92, edgecolor='none'))
        if len(xs) >= 2:
            ax.plot(xs, ys, color=zz_c, lw=2.0, marker='o', ms=5, zorder=5, alpha=0.9)

        # PRZ line
        ax.hlines(prz, xmin=0, xmax=n - 1, colors=prz_c, linewidths=1.4, linestyles='--', alpha=0.9)
        ax.text(n - 2, prz, f" PRZ {prz:.2f}", color=prz_c, fontsize=6, va='bottom', ha='right', fontweight='bold')

        if state == 'approaching':
            ax.text(n - 1, prz, " D?", color=bg_c, fontsize=6.5, ha='left', va='center', fontweight='bold',
                    bbox=dict(boxstyle='round,pad=0.15', facecolor=prz_c, alpha=0.95, edgecolor='none'))

        ax.spines[:].set_visible(False)
        ax.tick_params(left=False, bottom=True, colors=axis_c, labelsize=5.5)
        ax.yaxis.set_visible(False)
        step = max(1, n // 4)
        ticks = list(range(0, n, step))
        ax.set_xticks(ticks)
        ax.set_xticklabels([
            pd.Timestamp(df.index[first_bar + t]).strftime("%d %b '%y") if first_bar + t < n_total else ""
            for t in ticks], color=axis_c)
        ax.set_xlim(-0.5, n - 0.5)
        ax.set_ylim(min(y_all) - y_range * 0.05, max(y_all) + y_range * 0.14)

        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=110, bbox_inches='tight', facecolor=bg_c, pad_inches=0.1)
        plt.close(fig); buf.seek(0)
        return base64.b64encode(buf.read()).decode()
    except Exception:
        return ""


def _build_harmonic_analysis(harm_res, curr_price, ticker, df=None):
    """Harmonik formasyon için zenginleştirilmiş analiz verisi üretir."""
    import datetime as _dt

    def fp(v):
        try: return f"{v:,.2f}" if v < 1000 else f"{int(v):,}"
        except: return str(v)
    def pct(a, b):
        try: return f"{((a - b) / b * 100):+.1f}%"
        except: return ""

    pat       = harm_res.get('pattern', 'Harmonik')
    direction = harm_res.get('direction', 'Bullish')
    prz       = harm_res.get('prz', curr_price)
    state     = harm_res.get('state', 'fresh')
    ab_xa     = harm_res.get('AB_XA', 0)
    xd_xa     = harm_res.get('XD_XA', 0)
    bars_ago  = harm_res.get('bars_ago', 0)
    p_prices  = harm_res.get('pivot_prices', [])
    p_idx     = harm_res.get('pivot_idx', [])

    _EMOJI = {'Gartley': '🦋', 'Butterfly': '🦋', 'Bat': '🦇', 'Crab': '🦀', 'Shark': '🦈'}
    emoji  = _EMOJI.get(pat, '🔮')
    is_bull = direction == 'Bullish'

    # Hedef: D'den A'ya mesafenin 0.618'i
    a_price = p_prices[1] if len(p_prices) > 1 and p_prices[1] is not None else None
    target  = None
    if a_price:
        target = (prz + abs(a_price - prz) * 0.618) if is_bull else (prz - abs(a_price - prz) * 0.618)
    invalid = prz * 0.975 if is_bull else prz * 1.025

    # Stage
    if state == 'approaching':
        stage = 3; stage_total = 4; stage_label = "D Noktası Yaklaşıyor"
    else:
        stage = 4; stage_total = 4; stage_label = "PRZ Tamamlandı"

    # R/R
    rr_ratio = None; rr_str = "—"
    if target and invalid and curr_price:
        try:
            reward = abs(target - curr_price); risk = abs(curr_price - invalid)
            if risk > 0:
                rr_ratio = reward / risk; rr_str = f"1 : {rr_ratio:.1f}"
        except: pass

    # Formasyon yaşı
    pat_age_days = 0; pat_start_str = "—"
    try:
        if df is not None and p_idx and p_idx[0] is not None:
            start_date  = df.index[p_idx[0]]
            pat_age_days = (_dt.date.today() - pd.Timestamp(start_date).date()).days
            _ay = ["Oca","Şub","Mar","Nis","May","Haz","Tem","Ağu","Eyl","Eki","Kas","Ara"]
            sd  = pd.Timestamp(start_date).date()
            pat_start_str = f"{sd.day} {_ay[sd.month-1]} '{str(sd.year)[2:]}"
    except: pass

    # Fibonacci hedef tablosu
    _FIB_TARGET = {
        'Gartley':   {'AB/XA': '0.618',        'XD/XA': '0.786'},
        'Butterfly': {'AB/XA': '0.786',        'XD/XA': '1.27–1.618'},
        'Bat':       {'AB/XA': '0.382–0.500',  'XD/XA': '0.886'},
        'Crab':      {'AB/XA': '0.382–0.618',  'XD/XA': '1.618'},
        'Shark':     {'AB/XA': '0.382–0.618',  'XD/XA': '0.886–1.13'},
    }
    fib_table = _FIB_TARGET.get(pat, {})

    # Story
    _STORIES = {
        'Gartley': (
            f"Gartley, harmonik formasyonların klasiğidir. AB dalgası XA'nın %61.8'ini geri çekiyor; "
            f"D noktası XA'nın %78.6'sında tamamlanıyor. "
            f"Bu seviye ({fp(prz)}) kurumsal {'alıcıların' if is_bull else 'satıcıların'} sıklıkla pozisyon açtığı Fibonacci kesişimidir."
        ),
        'Butterfly': (
            f"Butterfly'da D noktası X'in ötesine uzanır — bu 'aşırı uzantı' hareketi yapar. "
            f"Satıcılar ({'' if is_bull else 'alıcılar'}) aşırı bastırmış; "
            f"PRZ bölgesi {fp(prz)} sert bir tersine dönüş için zemin hazırlıyor."
        ),
        'Bat': (
            f"Bat formasyonunda AB, XA'nın %38.2–50'sini geri çeker; D ise XA'nın %88.6'sında oluşur. "
            f"Bu çok derin ama güçlü bir dönüş noktasıdır. "
            f"PRZ {fp(prz)} yakınında {'alıcıların' if is_bull else 'satıcıların'} devreye girmesi beklenir."
        ),
        'Crab': (
            f"Crab, en geniş CD uzantısına sahip harmonik formasyondur — D XA'nın %161.8 uzantısında oluşur. "
            f"Bu 'aşırı uzatılmış' hareket çok sert bir tersine dönüşe zemin hazırlar. "
            f"PRZ {fp(prz)} güçlü bir {'dip' if is_bull else 'zirve'} noktası olabilir."
        ),
        'Shark': (
            f"Shark standart XABCD yapısından ayrışır — C noktası AB'yi aşarak likidite avı yapar. "
            f"Bu agresif hareket genellikle kurumların 'stop tuzağı' kurduğunu gösterir. "
            f"Fiyat {fp(prz)} bölgesine yaklaştığında {'alım' if is_bull else 'satım'} fırsatı doğabilir."
        ),
    }
    story = _STORIES.get(pat, f"{pat} formasyonu PRZ bölgesinde {'dip' if is_bull else 'zirve'} dönüşü sinyali veriyor.")

    # Conclusion
    dir_note = "üzerinde tutunabilirse" if is_bull else "altında kalabilirse"
    stop_dir = "altına" if is_bull else "üstüne"
    if state == 'approaching':
        conclusion = (
            f"D noktası henüz oluşmadı — fiyat tahmini PRZ olan <b>{fp(prz)}</b>'e yaklaşıyor. "
            f"Aceleci olma; D oluştuktan sonra teyit mumunu bekle. "
            f"PRZ onaylanırsa hedef <b>{fp(target) if target else '—'}</b> "
            f"({pct(target, curr_price) if target else ''}). "
            f"Stop için <b>{fp(invalid)}</b> {stop_dir} kapanış kullanılabilir."
        )
    else:
        conclusion = (
            f"D noktası {bars_ago} gün önce tamamlandı. "
            f"Fiyat PRZ <b>{fp(prz)}</b> {dir_note} hedef <b>{fp(target) if target else '—'}</b> "
            f"({pct(target, curr_price) if target else ''}) devreye girer. "
            f"<b>{fp(invalid)}</b> {stop_dir} kapanış formasyonu geçersiz kılar. "
            f"AB/XA: {ab_xa} — Fibonacci oranı {'teyit edildi' if xd_xa > 0 else 'yaklaşık'}."
        )

    return {
        "emoji": emoji, "name": f"{pat} ({direction})", "pat": pat,
        "direction": direction, "is_bull": is_bull,
        "prz": prz, "target": target, "invalid": invalid,
        "stage": stage, "stage_total": stage_total, "stage_label": stage_label,
        "rr_ratio": rr_ratio, "rr_str": rr_str,
        "fib_table": fib_table, "ab_xa": ab_xa, "xd_xa": xd_xa,
        "state": state, "bars_ago": bars_ago,
        "pat_age_days": pat_age_days, "pat_start_str": pat_start_str,
        "story": story, "conclusion": conclusion, "fp": fp, "pct": pct,
    }


@st.dialog("🔮 Harmonik Formasyon", width="large")
def _harmonik_dialog(ticker, harm_res, current_price, display_ticker, is_dark):
    """Harmonik formasyon popup: XABCD grafik + zengin analiz."""
    try:
        df_h = get_safe_historical_data(ticker, period="1y")
    except Exception:
        df_h = None
    _a  = _build_harmonic_analysis(harm_res, current_price, ticker, df_h)
    fp  = _a["fp"]
    txt = "#0f172a"
    sub = "#475569"
    brd = "#e2e8f0"
    lbl = "#64748b"
    card = "#f8fafc"
    acc  = "#a78bfa"

    st.markdown(
        f"<div style='font-size:1.35rem;font-weight:800;color:{acc};margin-bottom:12px;'>"
        f"{_a['emoji']} {display_ticker} — {_a['name']}</div>",
        unsafe_allow_html=True
    )

    _b64 = _mini_harmonic_chart_b64(ticker, harm_res, is_dark)
    _col_chart, _col_info = st.columns([60, 40], gap="medium")

    with _col_chart:
        if _b64:
            st.markdown(f"<img src='data:image/png;base64,{_b64}' style='width:100%;border-radius:8px;display:block;'/>", unsafe_allow_html=True)
        else:
            st.warning("Grafik oluşturulamadı.")

    with _col_info:
        # Aşama
        dots = "".join(
            f'<span style="width:12px;height:12px;border-radius:50%;display:inline-block;margin-right:5px;'
            f'background:{acc if j <= _a["stage"] else ("#e2e8f0")};"></span>'
            for j in range(1, _a["stage_total"] + 1)
        )
        stage_html = (f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:12px;">'
                      f'<div>{dots}</div>'
                      f'<div style="font-size:0.95rem;font-weight:700;color:{acc};">Aşama {_a["stage"]}/{_a["stage_total"]} — {_a["stage_label"]}</div>'
                      f'</div>')

        def _card(label, val_html, bg, border_color):
            return (f'<div style="background:{bg};border-left:3px solid {border_color};'
                    f'border-radius:7px;padding:7px 10px;">'
                    f'<div style="font-size:0.84rem;font-weight:600;color:{border_color};margin-bottom:3px;">{label}</div>'
                    f'<div style="font-size:0.97rem;font-weight:800;color:{txt};font-family:monospace;line-height:1.3;">{val_html}</div>'
                    f'</div>')

        cards_html = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px;">'

        # PRZ
        prz_ac = "#10b981" if current_price >= _a["prz"] else "#ef4444"
        prz_ar = "▲ üstünde ✓" if current_price >= _a["prz"] else "▼ altında"
        cards_html += _card("🎯 PRZ (Dönüş Bölgesi)",
            f'{fp(_a["prz"])} <span style="font-size:0.78rem;color:{prz_ac};">{prz_ar}</span>',
            "rgba(167,139,250,0.10)", acc)

        # Yön
        dir_c = "#10b981" if _a["is_bull"] else "#ef4444"
        cards_html += _card("Beklenti Yönü",
            f'<span style="color:{dir_c};">{"🟢 Bullish" if _a["is_bull"] else "🔴 Bearish"}</span>',
            "rgba(100,116,139,0.06)", dir_c)

        # Hedef
        if _a["target"]:
            t_pct = _a["pct"](_a["target"], current_price)
            cards_html += _card("📈 Tahmini Hedef",
                f'{fp(_a["target"])} <span style="font-size:0.82rem;color:#10b981;font-weight:600;">({t_pct})</span>',
                "rgba(16,185,129,0.10)", "#10b981")

        # Stop
        s_pct = _a["pct"](_a["invalid"], current_price)
        cards_html += _card("🔴 Stop / Geçersizlik",
            f'{fp(_a["invalid"])} <span style="font-size:0.82rem;color:#f87171;font-weight:600;">({s_pct})</span>',
            "rgba(248,113,113,0.08)", "#f87171")

        # R/R
        if _a["rr_ratio"]:
            rr_c = "#10b981" if _a["rr_ratio"] >= 2.0 else ("#f59e0b" if _a["rr_ratio"] >= 1.0 else "#ef4444")
            rr_lbl = "Mükemmel" if _a["rr_ratio"] >= 3.0 else ("İyi" if _a["rr_ratio"] >= 2.0 else ("Kabul" if _a["rr_ratio"] >= 1.0 else "Zayıf"))
            cards_html += _card("📐 Risk / Ödül",
                f'<span style="color:{rr_c};">{_a["rr_str"]}</span> <span style="font-size:0.78rem;color:{rr_c};">({rr_lbl})</span>',
                "rgba(100,116,139,0.08)", rr_c)

        # Formasyon yaşı
        if _a["pat_age_days"] > 0:
            cards_html += _card("📅 Formasyon Yaşı",
                f'{_a["pat_age_days"]} gün <span style="font-size:0.82rem;color:{sub};">({_a["pat_start_str"]})</span>',
                "rgba(100,116,139,0.06)", lbl)

        cards_html += '</div>'

        # Fibonacci oranları
        fib_table = _a.get("fib_table", {})
        fib_rows = ""
        for fib_key, ideal_val in fib_table.items():
            actual_val = str(_a["ab_xa"]) if "AB" in fib_key else str(_a["xd_xa"])
            fib_rows += (f'<div style="display:flex;justify-content:space-between;align-items:center;'
                         f'padding:4px 0;border-bottom:1px solid {brd};">'
                         f'<span style="font-size:0.84rem;color:{sub};">{fib_key}</span>'
                         f'<div><span style="font-family:monospace;font-weight:700;font-size:0.9rem;color:{acc};">{actual_val}</span>'
                         f'<span style="font-size:0.75rem;color:{lbl};margin-left:6px;">(hedef: {ideal_val})</span></div></div>')
        fib_html = ""
        if fib_rows:
            fib_html = (f'<div style="padding:7px 10px;border-radius:7px;background:{card};border:1px solid {brd};margin-bottom:6px;">'
                        f'<div style="font-size:0.84rem;font-weight:700;color:{lbl};text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px;">Fibonacci Oranları</div>'
                        f'{fib_rows}</div>')

        st.markdown(
            f'<div style="padding:2px 0;">{stage_html}'
            f'<div style="font-size:0.84rem;font-weight:700;color:{lbl};text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;">Kilit Seviyeler</div>'
            f'{cards_html}{fib_html}</div>',
            unsafe_allow_html=True
        )

    # Alt bölüm: Sahne Hikayesi + SONUÇ
    st.markdown("<hr style='margin:14px 0 10px 0;border-color:#94a3b8;'>", unsafe_allow_html=True)
    story_bg = "#f1f5f9"
    concl_bg = "#faf5ff"
    st.markdown(
        f'<div style="background:{story_bg};border-radius:10px;padding:14px 18px;margin-bottom:10px;">'
        f'<div style="font-size:0.88rem;font-weight:700;color:{lbl};text-transform:uppercase;letter-spacing:.6px;margin-bottom:7px;">📖 Sahne Hikayesi</div>'
        f'<div style="font-size:1.0rem;color:{txt};line-height:1.7;">{_a["story"]}</div>'
        f'</div>'
        f'<div style="background:{concl_bg};border:1px solid {acc}40;border-left:3px solid {acc};border-radius:10px;padding:14px 18px;">'
        f'<div style="font-size:0.88rem;font-weight:700;color:{acc};text-transform:uppercase;letter-spacing:.6px;margin-bottom:7px;">⚡ SONUÇ — Ne Yapılmalı?</div>'
        f'<div style="font-size:1.0rem;color:{txt};line-height:1.7;">{_a["conclusion"]}</div>'
        f'</div>',
        unsafe_allow_html=True
    )


@st.dialog("📊 Formasyon Grafiği", width="large")
def _formasyon_dialog(ticker, chart_data, current_price, display_ticker, pat_label, is_dark):
    """Zenginleştirilmiş formasyon popup: grafik + tam analiz + sahne hikayesi."""
    _a  = _build_pattern_analysis(chart_data, current_price, ticker)
    fp  = _a["fp"]
    txt = "#0f172a"
    sub = "#475569"
    brd = "#e2e8f0"
    lbl = "#64748b"
    card = "#f8fafc"

    st.markdown(
        f"<div style='font-size:1.35rem;font-weight:800;color:{'#1e3a8a'};margin-bottom:12px;'>"
        f"{_a['emoji']} {display_ticker} — {_a['name']}</div>",
        unsafe_allow_html=True
    )

    _b64 = _mini_pattern_chart_b64(ticker, chart_data, is_dark)
    _col_chart, _col_info = st.columns([60, 40], gap="medium")

    with _col_chart:
        if _b64:
            st.markdown(f"<img src='data:image/png;base64,{_b64}' style='width:100%;border-radius:8px;display:block;'/>", unsafe_allow_html=True)
        else:
            st.warning("Grafik oluşturulamadı.")

    with _col_info:
        # Aşama göstergesi
        dots = "".join(
            f'<span style="width:12px;height:12px;border-radius:50%;display:inline-block;margin-right:5px;'
            f'background:{"#3b82f6" if j <= _a["stage"] else ("#e2e8f0")};"></span>'
            for j in range(1, _a["stage_total"] + 1)
        )
        stage_html = (f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:12px;">'
                      f'<div>{dots}</div>'
                      f'<div style="font-size:0.95rem;font-weight:700;color:#38bdf8;">Aşama {_a["stage"]}/{_a["stage_total"]} — {_a["stage_label"]}</div>'
                      f'</div>')

        # ── Kompakt kart grid: her öğe label üstte, değer altında, 2 kolon ──
        def _info_card(label, val_html, bg, border_color, full_width=False):
            span = "1 / span 2" if full_width else "auto"
            return (f'<div style="grid-column:{span};background:{bg};border-left:3px solid {border_color};'
                    f'border-radius:7px;padding:7px 10px;">'
                    f'<div style="font-size:0.84rem;font-weight:600;color:{border_color};margin-bottom:3px;">{label}</div>'
                    f'<div style="font-size:0.97rem;font-weight:800;color:{txt};font-family:monospace;line-height:1.3;">{val_html}</div>'
                    f'</div>')

        cards_html = f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px;">'

        # Kilit seviye kartları
        for lbl_t, price, color in _a["levels"]:
            arrow = "▲" if current_price >= price else "▼"
            arrow_c = "#10b981" if current_price >= price else "#ef4444"
            note = "üstünde ✓" if current_price >= price else "altında"
            bg_lvl = f"rgba(100,116,139,0.08)"
            cards_html += _info_card(
                lbl_t,
                f'{fp(price)} <span style="font-size:0.78rem;font-weight:600;color:{arrow_c};">{arrow} {note}</span>',
                bg_lvl, color
            )

        # Hedef
        if _a["target"]:
            t_pct = _a["pct"](_a["target"], current_price)
            cards_html += _info_card(
                "🎯 Tahmini Hedef",
                f'{fp(_a["target"])} <span style="font-size:0.82rem;color:#10b981;font-weight:600;">({t_pct})</span>',
                "rgba(16,185,129,0.10)", "#10b981"
            )

        # Stop
        if _a["invalid"]:
            s_pct = _a["pct"](_a["invalid"], current_price)
            cards_html += _info_card(
                "🔴 Stop / Geçersizlik",
                f'{fp(_a["invalid"])} <span style="font-size:0.82rem;color:#f87171;font-weight:600;">({s_pct})</span>',
                "rgba(248,113,113,0.08)", "#f87171"
            )

        # R/R
        if _a["rr_ratio"]:
            rr_c = "#10b981" if _a["rr_ratio"] >= 2.0 else ("#f59e0b" if _a["rr_ratio"] >= 1.0 else "#ef4444")
            rr_lbl = "Mükemmel" if _a["rr_ratio"] >= 3.0 else ("İyi" if _a["rr_ratio"] >= 2.0 else ("Kabul" if _a["rr_ratio"] >= 1.0 else "Zayıf"))
            cards_html += _info_card(
                "📐 Risk / Ödül",
                f'<span style="color:{rr_c};">{_a["rr_str"]}</span> <span style="font-size:0.78rem;font-weight:600;color:{rr_c};">({rr_lbl})</span>',
                f"rgba(100,116,139,0.08)", rr_c
            )

        # Formasyon yaşı
        if _a["pat_age_days"] > 0:
            cards_html += _info_card(
                "📅 Formasyon Yaşı",
                f'{_a["pat_age_days"]} gün <span style="font-size:0.82rem;font-weight:600;color:{sub};">({_a["pat_start_str"]})</span>',
                f"rgba(100,116,139,0.06)", lbl
            )

        cards_html += '</div>'

        # Hacim teyidi (kart içinde tam genişlik)
        vol_html = ""
        if _a["vol_dip_ok"] is not None or _a["vol_bounce_ok"] is not None:
            def _vrow(lbl_v, ok):
                c = "#10b981" if ok else "#f59e0b"
                ic = "✅" if ok else "⚠️"
                note = ("Düşük hacim — tükenme işareti" if ok else "Yüksek hacim — baskı hâlâ var") if "Dip" in lbl_v \
                       else ("Yüksek hacim — güçlü dönüş" if ok else "Zayıf hacim — teyit bekleniyor")
                return (f'<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid {brd};">'
                        f'<span style="font-size:0.88rem;color:{sub};">{ic} {lbl_v}</span>'
                        f'<span style="font-size:0.84rem;font-weight:600;color:{c};">{note}</span></div>')
            vol_inner = ""
            if _a["vol_dip_ok"] is not None:    vol_inner += _vrow("Dip testi hacmi", _a["vol_dip_ok"])
            if _a["vol_bounce_ok"] is not None: vol_inner += _vrow("Dönüş hacmi",     _a["vol_bounce_ok"])
            vol_html = (f'<div style="padding:8px 10px;border-radius:7px;background:{card};border:1px solid {brd};margin-bottom:6px;">'
                        f'<div style="font-size:0.84rem;font-weight:700;color:{lbl};text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px;">Hacim Teyidi</div>'
                        f'{vol_inner}</div>')

        st.markdown(
            f'<div style="padding:2px 0;">{stage_html}'
            f'<div style="font-size:0.84rem;font-weight:700;color:{lbl};text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;">Kilit Seviyeler</div>'
            f'{cards_html}{vol_html}</div>',
            unsafe_allow_html=True
        )

    # Alt bölüm: Sahne Hikayesi + SONUÇ
    st.markdown("<hr style='margin:14px 0 10px 0;border-color:#94a3b8;'>", unsafe_allow_html=True)
    story_bg  = "#0d1829"
    concl_bg  = "rgba(16,185,129,0.07)"
    st.markdown(
        f'<div style="background:{story_bg};border-radius:10px;padding:14px 18px;margin-bottom:10px;">'
        f'<div style="font-size:0.88rem;font-weight:700;color:{lbl};text-transform:uppercase;letter-spacing:.6px;margin-bottom:7px;">📖 Sahne Hikayesi</div>'
        f'<div style="font-size:1.0rem;color:{txt};line-height:1.7;">{_a["story"]}</div>'
        f'</div>'
        f'<div style="background:{concl_bg};border:1px solid #10b98140;border-left:3px solid #10b981;border-radius:10px;padding:14px 18px;">'
        f'<div style="font-size:0.88rem;font-weight:700;color:#10b981;text-transform:uppercase;letter-spacing:.6px;margin-bottom:7px;">⚡ SONUÇ — Ne Yapılmalı?</div>'
        f'<div style="font-size:1.0rem;color:{txt};line-height:1.7;">{_a["conclusion"]}</div>'
        f'</div>',
        unsafe_allow_html=True
    )

# ==============================================================================
# BÖLÜM 31 — ROADMAP VE BİRLEŞİK SİNYAL PANELİ
# 8 maddelik yol haritasını ve tüm sinyalleri tek bir panelde birleştirerek kullanıcıya sunan render fonksiyonları.
# ==============================================================================
def render_roadmap_8_panel(ticker):
    data = calculate_8_point_roadmap(ticker)
    if not data: return

    display_ticker = get_display_name(ticker)
    
    # --- 1. YENİ EKLENEN: FİYAT ÇEKME VE KÜSURAT AYARI ---
    info = fetch_stock_info(ticker)
    current_price = info.get('price', 0) if info else 0
    is_index = "XU" in ticker.upper() or "^" in ticker or current_price > 1000
    display_price = f"{int(current_price)}" if is_index else f"{current_price:.2f}"

    # --- 2. YENİ EKLENEN: ROZET (BADGE) İÇİN TEMA RENKLERİ ---
    title_col = "#38bdf8"
    header_bg = "linear-gradient(90deg,#0d1829,#0f2040)"
    header_border = "#1e3a5f"
    badge_bg = "rgba(56,189,248,0.1)"
    badge_text = "#38bdf8"
    price_color = "#10b981"

    _STATUS_CFG = {
        "bull":    ("74, 222, 128",  "#4ade80"),
        "bear":    ("248, 113, 113", "#f87171"),
        "warning": ("251, 191, 36",  "#fbbf24"),
        "neutral": ("148, 163, 184", "#94a3b8"),
    }

    def make_box(num, title, content, color, edu_text, tf_text, status="neutral", box_idx=0):
        s_rgb, s_hex = _STATUS_CFG.get(status, _STATUS_CFG["neutral"])
        box_cls = f"rm-box-{box_idx}"
        return f"""
        <div class="{box_cls}" style="background:rgba({s_rgb},0.06);border-left:3px solid {s_hex};padding:4px 6px;border-radius:4px;display:flex;flex-direction:column;justify-content:flex-start;height:100%;position:relative;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;border-bottom:1px solid rgba({s_rgb},0.2);padding-bottom:3px;">
                <div style="display:flex;align-items:center;gap:4px;font-size:0.75rem;font-weight:800;color:{s_hex};line-height:1.1;">
                    <span style="width:6px;height:6px;border-radius:50%;background:{s_hex};flex-shrink:0;display:inline-block;box-shadow:0 0 3px {s_hex};"></span>
                    {num}. {title}
                </div>
                <div style="font-size:0.55rem;font-weight:700;color:#64748b;background:rgba(100,116,139,0.1);padding:1px 3px;border-radius:3px;border:1px solid rgba(100,116,139,0.2);">⏱️ {tf_text}</div>
            </div>
            <div style="font-size:0.72rem;font-weight:500;line-height:1.3;flex:1;" class="dark-text-fix">{content}</div>
            <div class="rm-edu-tip-{box_idx}" style="font-size:0.65rem;color:#64748b;font-style:italic;margin-top:3px;border-top:1px dashed rgba({s_rgb},0.25);padding-top:3px;opacity:0;max-height:0;overflow:hidden;transition:opacity 0.25s,max-height 0.25s;">{edu_text}</div>
        </div>
        """

    # Statüleri yeniden eşle: 8 → 5 kart için (Card 6 ve Card 7 yeni Composite/MTF/Trade Plan kartlarına taşındı)
    _statuses_orig = data.get('S', ['neutral'] * 8)
    # Yeni sıra: [s1 (Fiyat+Formasyon birleşik), s3 (VSA), s4 (Trend), s5 (Hacim), s8 (Sentez)]
    # Birleşik kart için s1 ve s2'nin daha güçlü olanını seç
    def _stronger_status(a, b):
        rank = {"bull": 3, "warning": 2, "bear": 2, "neutral": 1}
        return a if rank.get(a, 0) >= rank.get(b, 0) else b
    _statuses = [
        _stronger_status(_statuses_orig[0], _statuses_orig[1]),  # M1+M2 birleşik
        _statuses_orig[2],  # M3 VSA
        _statuses_orig[3],  # M4 Trend
        _statuses_orig[4],  # M5 Hacim
        _statuses_orig[7],  # M8 Sentez
    ]
    _now_str = datetime.now().strftime("%H:%M")

    # M1+M2 birleştirilmiş içerik (Fiyat Davranışı + Formasyon Tespiti)
    _m1_plus_m2 = (
        f'{data["M1"]}'
        f'<div style="margin-top:6px;padding-top:5px;border-top:1px dashed rgba(100,116,139,0.25);">'
        f'<span style="font-size:0.68rem;color:#64748b;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;">Formasyon:</span><br>'
        f'{data["M2"]}'
        f'</div>'
    )

    _box_defs = [
        ("1", "Fiyat & Formasyon",   _m1_plus_m2, "Günlük mum yapısı + Price Action dizilimi + geometrik formasyonlar (kısa-orta vade).", "Kısa-Orta Vade"),
        ("2", "Efor vs Sonuç (VSA)", data['M3'],  "Hacmin fiyata yansıma kalitesi (Churning kontrolü).",                                  "Son 3 Gün"),
        ("3", "Trend Skoru",         data['M4'],  "Sıkışma, hacim daralması ve hareketli ortalama yakınsaması.",                          "1-3 Ay"),
        ("4", "Hacim Algoritması",   data['M5'],  "Kurumsal emilim (Absorption) ve agresif piyasa akışı.",                                "Son 20 Gün"),
        ("5", "Teknik Özet",         data['M8'],  "Tüm verilerin genel sentezi ve piyasa beklentisi.",                                    "Genel Bakış"),
    ]
    # Not: Eski Card 6 (Yön Beklentisi) → üstteki Composite Skor kartında (Momentum alt faktörü)
    #      Eski Card 7 (Ayı/Boğa Senaryoları) → üstteki Trade Plan kartında (Stop/TP1/TP2)

    boxes = [
        make_box(num, title, content, "", edu, tf, status=_statuses[i], box_idx=i)
        for i, (num, title, content, edu, tf) in enumerate(_box_defs)
    ]

    grid_html = "".join(boxes)

    # CSS: hover ile edu tooltip görünür hale gelir
    hover_css = "".join(
        f".rm-box-{i}:hover .rm-edu-tip-{i}{{opacity:1!important;max-height:80px!important;}}"
        for i in range(5)
    )

    # ──────────────────────────────────────────────────────────────────
    # PHASE C — CARD A: COMPOSITE TECHNICAL SCORE (5 alt faktör)
    # ──────────────────────────────────────────────────────────────────
    _comp_score    = data.get('composite_score', 50)
    _comp_decision = data.get('comp_decision', 'BEKLEMEDE')
    _comp_color    = data.get('comp_color', '#d97706')
    _factor_scores = data.get('factor_scores', {})

    def _bar_html(label, score):
        sc = max(0, min(100, score))
        if   sc >= 70: bcol = "#16a34a"
        elif sc >= 50: bcol = "#ca8a04"
        elif sc >= 30: bcol = "#d97706"
        else:          bcol = "#f87171"
        return (f'<div style="margin-bottom:2px;">'
                f'<div style="display:flex;justify-content:space-between;font-size:0.62rem;'
                f'font-weight:600;color:#64748b;line-height:1.1;">'
                f'<span>{label}</span><span style="color:{bcol};">{sc:.0f}</span></div>'
                f'<div style="height:3px;background:rgba(100,116,139,0.18);border-radius:2px;overflow:hidden;">'
                f'<div style="height:100%;width:{sc}%;background:{bcol};"></div></div></div>')

    _factor_bars = (
        _bar_html("Trend",    _factor_scores.get('trend', 50))    +
        _bar_html("Momentum", _factor_scores.get('momentum', 50)) +
        _bar_html("Hacim",    _factor_scores.get('volume', 50))   +
        _bar_html("Yapı",     _factor_scores.get('yapi', 50))     +
        _bar_html("Senaryo",  _factor_scores.get('senaryo', 50))
    )

    composite_card_html = (
        f'<div style="background:rgba({"22,163,74" if _comp_score >= 70 else ("234,179,8" if _comp_score >= 55 else ("245,158,11" if _comp_score >= 40 else "239,68,68"))},0.08);'
        f'border-left:3px solid {_comp_color};border-radius:5px;padding:6px 9px;">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;'
        f'padding-bottom:4px;border-bottom:1px solid rgba(100,116,139,0.18);">'
        f'<span style="font-size:0.7rem;font-weight:800;color:#64748b;letter-spacing:0.04em;'
        f'text-transform:uppercase;">⚡ Composite Skor</span>'
        f'<span style="font-size:0.7rem;font-weight:800;color:{_comp_color};">{_comp_decision}</span>'
        f'</div>'
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:5px;">'
        f'<div style="font-size:1.6rem;font-weight:900;color:{_comp_color};line-height:1;">'
        f'{_comp_score}<span style="font-size:0.7rem;opacity:0.5;font-weight:700;">/100</span></div>'
        f'<div style="flex:1;font-size:0.65rem;color:#94a3b8;line-height:1.25;">'
        f'5 alt faktörün ağırlıklı sentezi'
        f'</div></div>'
        f'<div>{_factor_bars}</div>'
        f'</div>'
    )

    # ──────────────────────────────────────────────────────────────────
    # PHASE D — CARD B: MULTI-TIMEFRAME ALIGNMENT (Şık grid + skor bazlı renk)
    # ──────────────────────────────────────────────────────────────────
    _mtf = calculate_multi_timeframe_alignment(ticker)
    if _mtf and _mtf.get('matrix'):
        # Skor bazlı renk paleti (composite_card ile aynı mantık)
        _mtf_pct = _mtf["overall_pct"]
        _mtf_dom = _mtf['dominant']
        if _mtf_dom == "YUKARI":
            if   _mtf_pct >= 66: _mtf_bg_rgb, _mtf_brd = "22,163,74",  "#16a34a"   # koyu yeşil
            elif _mtf_pct >= 50: _mtf_bg_rgb, _mtf_brd = "132,204,22", "#65a30d"   # lime
            else:                _mtf_bg_rgb, _mtf_brd = "234,179,8",  "#ca8a04"   # amber
        elif _mtf_dom == "AŞAĞI":
            if   _mtf_pct >= 66: _mtf_bg_rgb, _mtf_brd = "248,113,113",  "#f87171"   # kırmızı
            elif _mtf_pct >= 50: _mtf_bg_rgb, _mtf_brd = "249,115,22", "#ea580c"   # turuncu
            else:                _mtf_bg_rgb, _mtf_brd = "234,179,8",  "#ca8a04"   # amber
        else:  # KARARSIZ
            _mtf_bg_rgb, _mtf_brd = "234,179,8", "#ca8a04"  # amber

        # Yön çipi — yuvarlak, soft arkaplanlı, modern görünüm
        def _mtf_chip(sig):
            if sig > 0:
                return ('<div style="display:inline-flex;align-items:center;justify-content:center;'
                        'width:20px;height:20px;border-radius:50%;'
                        'background:rgba(22,163,74,0.16);color:#16a34a;'
                        'font-weight:800;font-size:0.82rem;line-height:1;">↑</div>')
            if sig < 0:
                return ('<div style="display:inline-flex;align-items:center;justify-content:center;'
                        'width:20px;height:20px;border-radius:50%;'
                        'background:rgba(248,113,113,0.16);color:#f87171;'
                        'font-weight:800;font-size:0.82rem;line-height:1;">↓</div>')
            return ('<div style="display:inline-flex;align-items:center;justify-content:center;'
                    'width:20px;height:20px;border-radius:50%;'
                    'background:rgba(148,163,184,0.14);color:#94a3b8;'
                    'font-weight:700;font-size:0.74rem;line-height:1;">≈</div>')

        # Grid: 5 sütun (label + 4 vade)
        _tfs   = _mtf['timeframes']
        _ncol  = len(_tfs)
        _grid_cols = "1.15fr " + " ".join(["1fr"] * _ncol)
        _items = []

        # Üst sıra: boş köşe + timeframe başlıkları (subtle border-bottom)
        _items.append('<div></div>')
        for tf in _tfs:
            _items.append(
                f'<div style="font-size:0.6rem;font-weight:700;color:#64748b;'
                f'text-align:center;padding:2px 0 5px 0;'
                f'letter-spacing:0.05em;text-transform:uppercase;'
                f'border-bottom:1px solid rgba(100,116,139,0.22);">{tf}</div>'
            )

        # 3 indicator satırı (Trend, Momentum, Hacim)
        _ind_labels = {"trend": "Trend", "momentum": "Momentum", "hacim": "Hacim"}
        for i, ind in enumerate(["trend", "momentum", "hacim"]):
            _sep = "border-top:1px dashed rgba(100,116,139,0.12);" if i > 0 else ""
            _items.append(
                f'<div style="font-size:0.66rem;font-weight:700;color:#64748b;'
                f'text-transform:uppercase;letter-spacing:0.03em;'
                f'padding:6px 6px 6px 0;{_sep}">{_ind_labels[ind]}</div>'
            )
            for tf in _tfs:
                _items.append(
                    f'<div style="text-align:center;padding:5px 0;{_sep}">'
                    f'{_mtf_chip(_mtf["matrix"][tf].get(ind, 0))}</div>'
                )

        _grid_html_body = "".join(_items)

        mtf_card_html = (
            f'<div style="background:rgba({_mtf_bg_rgb},0.08);border-left:3px solid {_mtf_brd};'
            f'border-radius:5px;padding:6px 9px;">'
            # Header (Composite ile aynı tarz: uppercase başlık + sağda skor)
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'margin-bottom:4px;padding-bottom:4px;border-bottom:1px solid rgba(100,116,139,0.18);">'
            f'<span style="font-size:0.7rem;font-weight:800;color:#64748b;letter-spacing:0.04em;'
            f'text-transform:uppercase;">📐 Vade Uyumu (MTF)</span>'
            f'<span style="font-size:0.7rem;font-weight:800;color:{_mtf_brd};">'
            f'{_mtf_dom} %{_mtf_pct}</span>'
            f'</div>'
            # Grid body
            f'<div style="display:grid;grid-template-columns:{_grid_cols};gap:0 6px;align-items:center;">'
            f'{_grid_html_body}'
            f'</div>'
            # Footer mini özet
            f'<div style="font-size:0.6rem;color:#94a3b8;margin-top:5px;'
            f'padding-top:4px;border-top:1px dashed rgba(100,116,139,0.18);'
            f'display:flex;gap:8px;justify-content:center;">'
            f'<span style="color:#16a34a;font-weight:700;">{_mtf["bull_cnt"]} ↑</span>'
            f'<span style="opacity:0.4;">·</span>'
            f'<span style="color:#f87171;font-weight:700;">{_mtf["bear_cnt"]} ↓</span>'
            f'<span style="opacity:0.4;">·</span>'
            f'<span style="color:#94a3b8;font-weight:700;">{_mtf["total"] - _mtf["bull_cnt"] - _mtf["bear_cnt"]} ≈</span>'
            f'</div>'
            f'</div>'
        )
    else:
        mtf_card_html = (
            f'<div style="background:rgba(100,116,139,0.06);border-left:3px solid #94a3b8;'
            f'border-radius:5px;padding:6px 9px;">'
            f'<div style="font-size:0.7rem;font-weight:800;color:#64748b;letter-spacing:0.04em;'
            f'text-transform:uppercase;margin-bottom:4px;">📐 Vade Uyumu (MTF)</div>'
            f'<div style="font-size:0.7rem;color:#94a3b8;">Çoklu vade verisi alınamadı</div>'
            f'</div>'
        )

    # ──────────────────────────────────────────────────────────────────
    # PHASE E — CARD C: TRADE PLAN (Entry/Stop/TP1/TP2/R:R)
    # ──────────────────────────────────────────────────────────────────
    _tp_curr   = data.get('tp_curr_price', 0)
    _tp_stop   = data.get('tp_stop_5g', 0)
    _tp_stop20 = data.get('tp_stop_20g', 0)
    _tp_tp1    = data.get('tp_target_atr', 0)
    _tp_tp2    = data.get('tp_target_20g', 0)

    def _fmt_p(v):
        try:
            v = float(v)
            return f"{int(v):,}" if v >= 1000 else f"{v:.2f}"
        except: return "—"

    # Trade plan: composite YUKARI ise long mantığı, AŞAĞI ise sadece "long uygun değil" notu
    if _comp_decision in ("AL", "DİKKAT/İZLE") and _tp_curr > 0 and _tp_stop > 0 and _tp_tp1 > _tp_curr:
        _risk = max(_tp_curr - _tp_stop, 0.001)
        _rew1 = max(_tp_tp1 - _tp_curr, 0)
        _rew2 = max(_tp_tp2 - _tp_curr, 0)
        _rr1  = _rew1 / _risk if _risk > 0 else 0
        _rr2  = _rew2 / _risk if _risk > 0 else 0
        _rr_max = max(_rr1, _rr2)
        if   _rr_max >= 2.5: _rr_lbl, _rr_col = "Mükemmel R/R", "#16a34a"
        elif _rr_max >= 1.5: _rr_lbl, _rr_col = "Sağlam R/R",   "#16a34a"
        elif _rr_max >= 1.0: _rr_lbl, _rr_col = "Sınırda R/R",  "#ca8a04"
        else:                _rr_lbl, _rr_col = "Zayıf R/R",    "#f87171"

        trade_plan_html = (
            f'<div style="background:rgba(56,189,248,0.06);border-left:3px solid #38bdf8;border-radius:5px;padding:8px 10px;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;">'
            f'<span style="font-size:0.75rem;font-weight:800;color:#38bdf8;">🎯 Trade Plan (Long)</span>'
            f'<span style="font-size:0.7rem;font-weight:800;color:{_rr_col};">{_rr_lbl}</span>'
            f'</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:3px 8px;font-size:0.7rem;">'
            f'<span style="color:#64748b;">Giriş:</span><b style="text-align:right;color:#38bdf8;">{_fmt_p(_tp_curr)}</b>'
            f'<span style="color:#64748b;">Stop (5G dip):</span><b style="text-align:right;color:#f87171;">{_fmt_p(_tp_stop)}</b>'
            f'<span style="color:#64748b;">TP1 (ATR×2):</span><b style="text-align:right;color:#16a34a;">{_fmt_p(_tp_tp1)} <span style="opacity:0.7;font-weight:600;">({_rr1:.1f}R)</span></b>'
            f'<span style="color:#64748b;">TP2 (20G zirve):</span><b style="text-align:right;color:#16a34a;">{_fmt_p(_tp_tp2)} <span style="opacity:0.7;font-weight:600;">({_rr2:.1f}R)</span></b>'
            f'</div>'
            f'</div>'
        )
    else:
        trade_plan_html = (
            f'<div style="background:rgba(100,116,139,0.06);border-left:3px solid #94a3b8;border-radius:5px;padding:8px 10px;">'
            f'<div style="font-size:0.75rem;font-weight:800;color:#64748b;margin-bottom:4px;">🎯 Trade Plan</div>'
            f'<div style="font-size:0.7rem;color:#94a3b8;">'
            f'Composite skor düşük ({_comp_score}/100) — long kurulum uygun değil. '
            f'<b>{_comp_decision}</b> durumda kalın.</div>'
            f'</div>'
        )

    # ── SWAP: Trade Plan ↔ Fiyat & Formasyon yer değiştir ──
    # Top row 3. sütun: Fiyat & Formasyon (önceden Trade Plan idi)
    _fp_status = _stronger_status(_statuses_orig[0], _statuses_orig[1])
    _fp_rgb, _fp_hex = _STATUS_CFG.get(_fp_status, _STATUS_CFG["neutral"])
    fp_top_html = (
        f'<div style="background:rgba({_fp_rgb},0.06);border-left:3px solid {_fp_hex};border-radius:5px;padding:6px 9px;">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;'
        f'padding-bottom:4px;border-bottom:1px solid rgba({_fp_rgb},0.18);">'
        f'<span style="font-size:0.7rem;font-weight:800;color:{_fp_hex};letter-spacing:0.04em;">🕯️ Fiyat & Formasyon</span>'
        f'<span style="font-size:0.55rem;font-weight:700;color:#64748b;background:rgba(100,116,139,0.1);padding:1px 4px;border-radius:3px;border:1px solid rgba(100,116,139,0.2);">⏱️ Kısa-Orta Vade</span>'
        f'</div>'
        f'<div style="font-size:0.7rem;line-height:1.3;" class="dark-text-fix">{_m1_plus_m2}</div>'
        f'</div>'
    )

    # Bottom 1. kart: Trade Plan (önceden Fiyat & Formasyon idi)
    if _comp_decision in ("AL", "DİKKAT/İZLE") and _tp_curr > 0 and _tp_stop > 0 and _tp_tp1 > _tp_curr:
        _tp_box_status = "bull" if _rr_max >= 1.5 else ("warning" if _rr_max >= 1 else "bear")
        _tp_box_tf = _rr_lbl
        _tp_box_inner = (
            f'<div style="display:grid;grid-template-columns:auto 1fr;gap:2px 8px;font-size:0.7rem;">'
            f'<span style="color:#64748b;">Giriş:</span><b style="text-align:right;color:#38bdf8;">{_fmt_p(_tp_curr)}</b>'
            f'<span style="color:#64748b;">Stop (5G):</span><b style="text-align:right;color:#f87171;">{_fmt_p(_tp_stop)}</b>'
            f'<span style="color:#64748b;">TP1 (ATR×2):</span><b style="text-align:right;color:#16a34a;">{_fmt_p(_tp_tp1)} ({_rr1:.1f}R)</b>'
            f'<span style="color:#64748b;">TP2 (20G):</span><b style="text-align:right;color:#16a34a;">{_fmt_p(_tp_tp2)} ({_rr2:.1f}R)</b>'
            f'</div>'
        )
    else:
        _tp_box_status = "neutral"
        _tp_box_tf = "Beklemede"
        _tp_box_inner = (
            f'<div style="font-size:0.7rem;color:#94a3b8;">'
            f'Composite skor düşük ({_comp_score}/100) — long kurulum uygun değil.</div>'
        )

    # _box_defs[0] ve _statuses[0]'ı Trade Plan ile değiştir, grid'i yeniden kur
    _box_defs[0] = ("1", "Trade Plan", _tp_box_inner, "Giriş, stop, TP1, TP2 ve R/R kalitesi.", _tp_box_tf)
    _statuses[0] = _tp_box_status
    boxes = [
        make_box(num, title, content, "", edu, tf, status=_statuses[i], box_idx=i)
        for i, (num, title, content, edu, tf) in enumerate(_box_defs)
    ]
    grid_html = "".join(boxes)

    top_section_html = (
        f'<div style="padding:5px 5px 0 5px;">'
        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:5px;">'
        f'{composite_card_html}{mtf_card_html}{fp_top_html}'
        f'</div></div>'
    )

    html_content = f"""
    <style>
    .dark-text-fix {{ color: #cbd5e1 !important; }}
    .dark-text-fix b, .dark-text-fix strong {{ color: #f1f5f9 !important; }}
    .dark-text-fix span {{ color: inherit; }}
    {hover_css}
    </style>
    <div class="info-card" style="border-top:3px solid {title_col};margin-top:5px;margin-bottom:6px;padding:0;">
        <div class="info-header" style="display:flex;justify-content:space-between;align-items:center;color:{title_col};font-size:1rem;padding:4px 8px;border-bottom:1px solid {header_border};background:{header_bg};margin-bottom:0;">
            <div style="display:flex;align-items:center;gap:10px;">
                <span style="font-weight:800;">🗺️ Teknik Yol Haritası</span>
                <span style="font-size:0.6rem;color:#64748b;font-family:'JetBrains Mono',monospace;">güncellendi {_now_str}</span>
            </div>
            <span style="background:{badge_bg};color:{badge_text};padding:2px 10px;border-radius:4px;font-family:'JetBrains Mono',monospace;font-weight:800;font-size:0.9rem;border:1px solid {header_border};">{display_ticker}&nbsp;<span style="opacity:0.6;margin:0 4px;font-weight:400;">—</span>&nbsp;<span style="color:{price_color};">{display_price}</span></span>
        </div>
        {top_section_html}
        <div style="padding:5px;">
            <div style="display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:5px;">
                {grid_html}
            </div>
        </div>
    </div>
    """
    st.markdown(html_content.replace('\n', ''), unsafe_allow_html=True)

    # --- Formasyon mini grafiği — butonu fiyat paneli altında göster (col_right) ---
    _m2_chart = data.get('M2_chart_data')
    _type_labels = {
        "cup": "Fincan-Kulp", "tobo": "TOBO", "flag": "Boğa Bayrağı",
        "triangle": "Yükselen Üçgen", "range": "Range", "saucer": "Çanak",
        "qml": "Quasimodo", "three_drive": "3 Drive", "sr_level": "Destek/Direnç"
    }
    if _m2_chart and isinstance(_m2_chart, dict):
        st.session_state['_formasyon_chart_data']    = _m2_chart
        st.session_state['_formasyon_pat_label']     = _type_labels.get(_m2_chart.get('type', ''), "Formasyon")
        st.session_state['_formasyon_current_price'] = current_price
        st.session_state['_formasyon_ticker']        = ticker
        st.session_state['_formasyon_display']       = display_ticker
    else:
        st.session_state['_formasyon_chart_data'] = None

def render_unified_signals_panel(ticker):
    """
    Canlı Sinyaller + Tarama Sonuçları → tek birleşik panel.
    Olumlu sinyaller üstte, olumsuz sinyaller altta. Her satır hover'da edu-note açar.
    """
    try:
        df = get_safe_historical_data(ticker)
        if df is None or df.empty:
            return

        master_score, _, _ = calculate_master_score(ticker)

        if master_score >= 70:
            karar_icon, karar_txt = "🟢", "AL"
            karar_color = "#4ade80"
            panel_border = "#10b981"
            panel_bg = "#0d1829"
            title_col = "#4ade80"
        elif master_score >= 45:
            karar_icon, karar_txt = "🟡", "İZLE"
            karar_color = "#fbbf24"
            panel_border = "#d97706"
            panel_bg = "#0d1829"
            title_col = "#fbbf24"
        else:
            karar_icon, karar_txt = "🔴", "UZAK DUR"
            karar_color = "#f87171"
            panel_border = "#f87171"
            panel_bg = "#0d1829"
            title_col = "#f87171"

        border_dim = f"#1e3a5f"

        # ── Kısa Vade Uyarısı (AL iken bozulma var mı?) ─────────────
        _kv_warnings = []   # liste: her uyarı için kısa string
        try:
            _stp_tmp = process_single_stock_stp(ticker, df)
            if _stp_tmp and _stp_tmp['type'] in ('cross_down', 'trend_down'):
                _kv_warnings.append("STP ↓")
        except: pass
        try:
            _obv_t, _, _ = get_obv_divergence_status(ticker)
            if any(k in _obv_t.upper() for k in ["DAĞITIM","ÇIKIŞ","NEGATİF","DÜŞÜŞ","ZAYIF SATIŞ"]):
                _kv_warnings.append("OBV Dağıtım")
        except: pass
        try:
            _zd_kv = _z_score_details(df)
            if _zd_kv and _zd_kv["composite"] >= 1.5:
                _kv_warnings.append(f"Z={_zd_kv['composite']:.1f} Tepe")
        except: pass
        try:
            if pa:
                _sv = pa.get('smart_volume', {})
                if _sv.get('climax', 'Yok') != 'Yok':
                    _kv_warnings.append("Climax Hacim")
        except: pass
        try:
            if df is not None and len(df) >= 6:
                _mom5 = float(df['Close'].iloc[-1]) / float(df['Close'].iloc[-6]) - 1
                if _mom5 < -0.02:   # son 5 günde -%2 den fazla düşüş
                    _kv_warnings.append(f"Momentum ↓ %{_mom5*100:.1f}")
        except: pass

        # Kısa vade uyarısı — _kv_note_html ayrı st.markdown ile render edilir
        _kv_show = (karar_txt in ("AL", "İZLE") and bool(_kv_warnings))

        # signals: (icon, text, color, edu_note, is_positive)
        signals = []

        bench_cat = st.session_state.get('category', 'BIST')
        bench_s   = get_benchmark_data(bench_cat)
        bench_tkr = "XU100.IS" if "BIST" in bench_cat else "^GSPC"
        idx_data  = None
        try:
            _idf = get_safe_historical_data(bench_tkr)
            if _idf is not None: idx_data = _idf['Close']
        except: pass

        ict_data  = None
        sent_data = None
        pa        = None
        try: ict_data  = calculate_ict_deep_analysis(ticker)
        except: pass
        try: sent_data = calculate_sentiment_score(ticker)
        except: pass
        try: pa = calculate_price_action_dna(ticker)
        except: pass

        # ── Regime Engine + Conviction Score ────────────────────────
        _regime     = detect_market_regime(df, pa)
        _conviction = calculate_conviction_score(df, pa, ict_data, sent_data, bench_s, ticker)

        # ── Session state'e yaz (AI prompt için) ─────────────────────
        st.session_state["_last_regime"]     = _regime
        st.session_state["_last_conviction"] = _conviction

        # ── 1. STP ──────────────────────────────────────────────────
        try:
            stp = process_single_stock_stp(ticker, df)
            if stp:
                if stp['type'] == 'cross_up':
                    signals.append(("⚡","STP Yukarı Kesişim","#15803d","Kısa vadeli alıcılar iştahlandı. Fiyat denge noktasını yukarı kırdı, taze bir yükseliş ivmesi tetiklendi.",True))
                elif stp['type'] == 'cross_down':
                    signals.append(("🔻","STP Aşağı Kesişim","#f87171","Kısa vadeli satıcı baskısı taze. Denge noktası aşağı kırıldı — likidite çıkışı başladı.",False))
                elif stp['type'] == 'trend_up':
                    g = stp['data'].get('Gun','?')
                    signals.append(("📈",f"STP Yükseliş Trendi ({g} gün)","#0369a1",f"Alıcılar {g} gündür kontrolde. Trend devam ettiği sürece dip alımları geçerli strateji.",True))
                elif stp['type'] == 'trend_down':
                    g = stp['data'].get('Gun','?')
                    signals.append(("📉",f"STP Düşüş Trendi ({g} gün)","#b91c1c",f"Satıcılar {g} gündür baskıda. Tepki rallileri kısa, ana trend aşağı.",False))
        except: pass

        # ── 2. HARSI ────────────────────────────────────────────────
        try:
            harsi = calculate_harsi(df)
            if harsi:
                _ha_val = harsi.get('ha_close', None)
                _ha_str = f" (HA-RSI: {_ha_val:.1f})" if _ha_val is not None else ""
                if harsi['is_green']:
                    signals.append(("🌊",f"HARSI: Boğa Momentumu","#0369a1",
                        f"Heikin Ashi RSI yukarı döndü{_ha_str}. Standart RSI'dan daha az gürültülü; "
                        f"dönüş sinyalini daha erken yakalar. Alıcı baskısı artıyor, trend yukarı.",True))
                else:
                    signals.append(("🌊",f"HARSI: Ayı Momentumu","#b91c1c",
                        f"Heikin Ashi RSI aşağı döndü{_ha_str}. Momentum satıcıların kontrolünde. "
                        f"Tepki rallileri kısa süreli olabilir — dip onaylanmadan alım riskli.",False))
        except: pass


        # ── 4. OBV ──────────────────────────────────────────────────
        try:
            obv_title, obv_color, _ = get_obv_divergence_status(ticker)
            if "ZAYIF" not in obv_title and "Veri Yok" not in obv_title and "Hesaplanamadı" not in obv_title:
                is_obv_pos = any(k in obv_title.upper() for k in ["GİRİŞ","GÜÇLÜ","POZİTİF","ALIŞ","DİRENÇ","EMİLİM","BİRİKİM","SAĞLIKLI","TOPLAMA"])
                if is_obv_pos:
                    _obv_edu = (
                        f"OBV yukarı trendde ve fiyatla uyumlu hareket ediyor. "
                        f"Kurumsal para fiyat düşüşlerinde bile birikim yapmaya devam ediyor — "
                        f"bu güçlü bir akümülasyon sinyali. Mevcut durum: {obv_title}."
                    )
                else:
                    _obv_edu = (
                        f"OBV aşağı trendde — fiyat yükselirken hacim desteği zayıflıyor. "
                        f"Kurumsal para sessizce çıkış yapıyor olabilir (dağıtım). "
                        f"Yükseliş sorgulanabilir. Mevcut durum: {obv_title}."
                    )
                signals.append(("📊",f"OBV: {obv_title}",obv_color,_obv_edu,is_obv_pos))
        except: pass

        # ── 5. ICT Sniper ───────────────────────────────────────────
        try:
            ict_res = process_single_ict_setup(ticker, df)
            if ict_res:
                is_ict_pos = "YÜKSELİŞ" in ict_res.get('Yön','') or "AL" in ict_res.get('Yön','').upper()
                _ict_yon   = ict_res.get('Yön','')
                _ict_durum = ict_res.get('Durum','').split('|')[0].strip()
                _ict_zone  = ict_data.get('zone','') if ict_data else ''
                _ict_struct= ict_data.get('structure','') if ict_data else ''
                if is_ict_pos:
                    _ict_edu = (
                        f"ICT kurulumu aktif: {_ict_yon} yönünde {_ict_durum}. "
                        f"{'DISCOUNT bölgesinde fiyat — kurumsal alım noktası.' if 'DISCOUNT' in _ict_zone else ''} "
                        f"{'Yapı kırılımı (BOS/MSS) yukarı teyit edildi.' if any(x in _ict_struct for x in ['BOS','MSS']) else ''} "
                        f"Order Block / FVG desteği geçerli."
                    ).strip()
                else:
                    _ict_edu = (
                        f"ICT kurulumu aktif: {_ict_yon} yönünde {_ict_durum}. "
                        f"{'PREMIUM bölgesinde fiyat — kurumsal dağıtım riski.' if 'PREMIUM' in _ict_zone else ''} "
                        f"{'Yapı kırılımı aşağı — alıcı yapısı bozuldu.' if any(x in _ict_struct for x in ['BOS','MSS']) else ''} "
                        f"Bearish Order Block / FVG baskısı var."
                    ).strip()
                signals.append((ict_res['İkon'],f"ICT Sniper: {_ict_yon} ({_ict_durum})",ict_res['Renk'],_ict_edu,is_ict_pos))
        except: pass

        # ── 6. Altın Fırsat ─────────────────────────────────────────
        try:
            if ict_data and sent_data:
                rs_t = sent_data.get('rs','').lower()
                c1 = "artıda" in rs_t or "lider" in rs_t or "pozitif" in rs_t or sent_data.get('total',0)>=50 or sent_data.get('raw_rsi',0)>50
                c2 = "DISCOUNT" in ict_data.get('zone','')
                c3 = "Güçlü" in ict_data.get('displacement','') or "Hacim" in sent_data.get('vol','') or sent_data.get('raw_rsi',0)>55
                if c1 and c2 and c3:
                    signals.append(("🏆","Altın Fırsat (Güç+Konum+Enerji)","#a16207","3 bağımsız koşul aynı anda: RS güçlü, fiyat DISCOUNT bölgesinde, hacim momentum destekliyor.",True))
        except: pass

        # ── 7. Royal Flush Nadir Fırsat (4/4) ───────────────────────────
        try:
            if ict_data and sent_data and lor and lor['votes'] >= 7:
                cs = "BOS (Yükseliş" in ict_data.get('structure','') or "MSS" in ict_data.get('structure','')
                ca = lor['signal'] == "YÜKSELİŞ"
                rs2 = sent_data.get('rs','').lower()
                cr = "artıda" in rs2 or "lider" in rs2 or "pozitif" in rs2 or sent_data.get('total',0)>=50
                try:
                    _vw = VolumeWeightedAveragePrice(high=df['High'],low=df['Low'],close=df['Close'],volume=df['Volume'],window=14)
                    _vd = abs((float(df['Close'].iloc[-1]) - float(_vw.volume_weighted_average_price().iloc[-1])) / (float(_vw.volume_weighted_average_price().iloc[-1])+1e-9) * 100)
                    cv = _vd < 12
                except: cv = True
                if cs and ca and cr and cv:
                    signals.append(("♠️","Royal Flush Nadir Fırsat (4/4)","#6d28d9","4 metodoloji aynı anda: ICT yapı kırılımı + RS gücü + VWAP yakınlığı + Hacim canlanması. En seçici kurulum.",True))
        except: pass

        # ── 8. Platin Fırsat (Elit) — önce Altın kriterini geçmeli ──────
        try:
            if len(df) >= 200 and ict_data and sent_data:
                # Altın Fırsat kriterleri (aynı c1/c2/c3 mantığı)
                _rs_t   = sent_data.get('rs','').lower()
                _pc1    = "artıda" in _rs_t or "lider" in _rs_t or "pozitif" in _rs_t or sent_data.get('total',0)>=50 or sent_data.get('raw_rsi',0)>50
                _pc2    = "DISCOUNT" in ict_data.get('zone','')
                _pc3    = "Güçlü" in ict_data.get('displacement','') or "Hacim" in sent_data.get('vol','') or sent_data.get('raw_rsi',0)>55
                if _pc1 and _pc2 and _pc3:  # Altın geçildi → Platin ek kriterleri
                    _c2 = df['Close']; _cp2 = float(_c2.iloc[-1])
                    _s200 = float(_c2.rolling(200).mean().iloc[-1]); _s50 = float(_c2.rolling(50).mean().iloc[-1])
                    _dd = _c2.diff(); _gg = _dd.where(_dd>0,0).rolling(14).mean(); _ll = (-_dd.where(_dd<0,0)).rolling(14).mean()
                    _rsi2 = float((100-(100/(1+_gg/_ll))).iloc[-1])
                    if _cp2>_s200 and _cp2>_s50 and _rsi2<70:
                        _p200_dist = (_cp2-_s200)/_s200*100
                        _p50_dist  = (_cp2-_s50)/_s50*100
                        _platin_edu = (
                            f"Fiyat {_cp2:.2f}: SMA200 üzeri %{_p200_dist:.1f}, SMA50 üzeri %{_p50_dist:.1f}. "
                            f"RSI(14): {_rsi2:.0f} — aşırı alım sınırı (70) altında, hâlâ alan var. "
                            f"Altın kriterleri (RS güçlü + DISCOUNT + Enerji) + yapısal hizalama birlikte sağlandı."
                        )
                        signals.append(("💎","Platin Fırsat (Elit): Yapısal Güç","#1d4ed8",_platin_edu,True))
        except: pass

        # ── 9. Harmonik Confluence ───────────────────────────────────
        try:
            hc = calculate_harmonic_confluence(ticker, df)
            if hc:
                is_hcp = hc.get('direction','') == 'Bullish'
                signals.append(("⚡",f"Harmonik Confluence: {hc['pattern']} + ICT {hc['zone']} | PRZ:{hc['prz']:.2f}","#6d28d9","Fibonacci yapısı, ICT bölgesi ve RSI diverjansının aynı noktada çakışması. Üç metodoloji aynı dönüş seviyesini işaret ediyor.",is_hcp))
        except: pass

        # ── 10. Güçlü Dönüş Adayı ───────────────────────────────────
        try:
            gd = calculate_guclu_donus_adaylari(ticker, df)
            if gd:
                _gd_z     = gd.get('Z-Score', 0)
                _gd_crit  = gd.get('Kriter', '') or gd.get('kriter', '') or gd.get('Detay','')
                _gd_edu   = (
                    f"Z-Score: {_gd_z} — istatistiksel olarak aşırı satım bölgesinde. "
                    f"{'Aktif kriterler: ' + str(_gd_crit)[:100] + '. ' if _gd_crit else ''}"
                    f"Fiyat yapısı dip oluşum bölgesinde — dönüş ihtimali matematiksel olarak yüksek. "
                    f"Kırılım veya hacim artışıyla konfirmasyon gel gelsin, erken giriş riskli."
                )
                signals.append(("🔄",f"Güçlü Dönüş Adayı (Z:{_gd_z})","#15803d",_gd_edu,True))
        except: pass

        # ── 11. RS Momentum ──────────────────────────────────────────
        try:
            is_idx = ticker.startswith("^") or "XU" in ticker or "XBANK" in ticker
            if not is_idx and bench_s is not None and len(df) >= 6:
                s5 = (float(df['Close'].iloc[-1])/float(df['Close'].iloc[-6])-1)*100
                b5 = (float(bench_s.iloc[-1])/float(bench_s.iloc[-6])-1)*100
                alpha = s5-b5
                if abs(alpha) > 1.0:
                    is_rsp = alpha > 0
                    ri = "🔥" if alpha>2 else ("💪" if alpha>0 else ("⚠️" if alpha>-2 else "🐢"))
                    rc = ("#15803d") if is_rsp else ("#f87171")
                    rl = f"Endeksi Eziyor (+%{alpha:.1f})" if alpha>2 else (f"Endeksi Yeniyor (+%{alpha:.1f})" if alpha>0 else (f"Endeksle Paralel (%{alpha:.1f})" if alpha>-2 else f"Endeksin Gerisinde (%{alpha:.1f})"))
                    if is_rsp:
                        _rs_edu = (
                            f"Son 5 günde hisse: %{s5:+.1f}, endeks: %{b5:+.1f}. "
                            f"Göreli fark: {alpha:+.1f}%. "
                            f"Hisse endeksten güçlü ayrışıyor — kurumsal para bu hisseyi tercih ediyor. "
                            f"{'Güçlü momentum: endeksin 2 katından fazla getiri.' if alpha>4 else 'Sağlıklı göreli güç devam ediyor.'}"
                        )
                    else:
                        _rs_edu = (
                            f"Son 5 günde hisse: %{s5:+.1f}, endeks: %{b5:+.1f}. "
                            f"Göreli fark: {alpha:+.1f}%. "
                            f"Hisse endeksin gerisinde kalıyor — göreli zayıflık var. "
                            f"{'Belirgin underperformance: endeks yükselirken hisse geri kaldı.' if alpha<-3 else 'Momentum endeksle kıyasla zayıf — dikkatli ol.'}"
                        )
                    signals.append((ri,f"RS Momentum (5Gün): {rl}",rc,_rs_edu,is_rsp))
        except: pass

        # ── 12. Akıllı Para ──────────────────────────────────────────
        try:
            acc = process_single_accumulation(ticker, df, bench_s)
            if acc:
                is_pp = acc.get('Pocket_Pivot', False)
                signals.append(("⚡" if is_pp else "🤫","Akıllı Para: Pocket Pivot (Patlama)" if is_pp else "Akıllı Para: Sessiz Toplama","#7c3aed","Pocket Pivot: Büyük hacimle fiyat yükseldi — kurumsal alım izleri." if is_pp else "Fiyat yatay veya baskılı görünse de arka planda sinsi fon alımı var. Kırılım hazırlığı.",True))
        except: pass

        # ── 13. Breakout ─────────────────────────────────────────────
        try:
            bo = process_single_breakout(ticker, df)
            if bo:
                prox = str(bo.get('Zirveye Yakınlık','')).split('<')[0].strip()
                is_fired = "TETİKLENDİ" in prox or "Sıkışma" in prox
                bc = ("#15803d") if is_fired else ("#d97706")
                _bo_prx   = bo.get('Zirveye Yakınlık','')
                _bo_direnç = bo.get('Direnç', '') or bo.get('direnç','') or bo.get('Seviye','')
                _bo_edu = (
                    f"{'Kritik direnç hacimle aşıldı — fiyat keşfi modu aktif. ' if is_fired else f'Fiyat tarihi zirveye yaklaşıyor: {_bo_prx}. '}"
                    f"{'Yeni yüksek alan açıldı, üstünde kaldıkça momentum hızlanır.' if is_fired else 'Hacimli bir mum bu seviyeyi geçerse güçlü momentum beklenir. '}"
                    f"{'Kırılım sonrası ilk geri çekilme alım fırsatı olabilir.' if is_fired else 'Haksız kırılım (fakeout) riskine karşı hacim teyidini bekle.'}"
                )
                signals.append(("🔨" if is_fired else "🔥",f"Breakout: {'KIRILIM Onaylandı' if is_fired else prox}",bc,_bo_edu,True))
        except: pass

        # ── 14. Minervini SEPA ───────────────────────────────────────
        try:
            mini = calculate_minervini_sepa(ticker, benchmark_ticker=bench_tkr)
            if mini:
                _mini_sc  = mini.get('Raw_Score', 0)
                _mini_dur = mini.get('Durum', '')
                _mini_det = mini.get('Detay', '') or mini.get('detail', '')
                _mini_edu = (
                    f"8 kriterden {_mini_sc} karşılandı → {_mini_dur}. "
                    f"{'Detay: ' + _mini_det[:120] + '...' if _mini_det and len(_mini_det)>10 else ''} "
                    f"Minervini SEPA: trend yönü, EMA düzeni, 52H yüksek yakınlığı ve RS gücü kontrol edilir. "
                    f"{'Tüm kriterler yeşil — Süper Performans adayı.' if _mini_sc >= 7 else ('Çoğu kriter olumlu — izleme listesine al.' if _mini_sc >= 5 else 'Bazı kriterler eksik — onay bekle.')}"
                ).strip()
                signals.append(("🦁",f"Minervini: {_mini_dur} ({_mini_sc} puan)","#ea580c",_mini_edu,True))
        except: pass

        # ── 15. Formasyon ────────────────────────────────────────────
        try:
            _pdf = pd.DataFrame()
            _pdf = scan_chart_patterns([ticker])
            if not _pdf.empty:
                pn   = _pdf.iloc[0]['Formasyon'].split('(')[0].strip()
                ps   = _pdf.iloc[0]['Skor']
                _pyon = _pdf.iloc[0].get('Yön', '') if 'Yön' in _pdf.columns else ''
                _pdur = _pdf.iloc[0].get('Durum', '') if 'Durum' in _pdf.columns else ''
                _pyon_str = f"Beklenen yön: {_pyon}. " if _pyon else ""
                _pdur_str = f"Durum: {_pdur}. " if _pdur else ""
                _pconf = "Yüksek güven (70+)" if ps>=70 else ("Orta güven (50-70)" if ps>=50 else "Düşük güven")
                _form_edu = (
                    f"'{pn}' formasyonu tespit edildi. {_pyon_str}{_pdur_str}"
                    f"Güven puanı: {ps} ({_pconf}). "
                    f"Kırılım gerçekleşirse hacim teyidini mutlaka bekle — haksız kırılımlar sık görülür."
                )
                signals.append(("📐",f"Formasyon: {pn} (Puan: {ps})","#0f172a",_form_edu,True))
        except: pass

        # ── 16. Radar 1 ──────────────────────────────────────────────
        try:
            r1 = process_single_radar1(ticker, df)
            if r1 and r1['Skor'] >= 4:
                _r1_detay = r1.get('Detay', '') or r1.get('detay', '') or r1.get('detail', '')
                _r1_edu   = (
                    f"7 momentum kriterinden {r1['Skor']}'i yeşil. "
                    f"{'Aktif kriterler: ' + str(_r1_detay)[:100] + '. ' if _r1_detay else ''}"
                    f"RSI yönü, MACD çaprazı, fiyat/EMA ilişkisi ve hacim akışı ölçülüyor. "
                    f"{'Çok güçlü momentum — 6/7 veya üzeri nadir.' if r1['Skor']>=6 else ('İyi momentum — trend devam etme ihtimali yüksek.' if r1['Skor']>=5 else 'Yeterli momentum — ama onay için daha fazla kriter bekle.')}"
                )
                signals.append(("🧠",f"Radar 1: Momentum ({r1['Skor']}/7)","#0369a1",_r1_edu,True))
        except: pass

        # ── 17. Radar 2 ──────────────────────────────────────────────
        try:
            r2 = process_single_radar2(ticker, df, idx_data, 0, 100000, 0)
            if r2 and r2['Skor'] >= 4:
                sn = r2['Setup'] if r2.get('Setup','-') != '-' else 'Trend Takibi'
                _r2_detay = r2.get('Detay', '') or r2.get('detay', '') or r2.get('detail', '')
                _r2_edu   = (
                    f"7 trend kriterinden {r2['Skor']}'i yeşil. Kurulum: {sn}. "
                    f"{'Aktif kriterler: ' + str(_r2_detay)[:100] + '. ' if _r2_detay else ''}"
                    f"SMA hizalaması, EMA düzeni, OBV yönü ve endekse göreli güç ölçülüyor. "
                    f"{'Mükemmel trend yapısı.' if r2['Skor']>=6 else ('Trend güçleniyor — devam etme ihtimali yüksek.' if r2['Skor']>=5 else 'Trend oluşum aşamasında — sabırlı ol.')}"
                )
                signals.append(("🚀",f"Radar 2: {sn} ({r2['Skor']}/7)","#15803d",_r2_edu,True))
        except: pass

        # ── 18. RSI Uyumsuzluk + Smart Volume ───────────────────────
        try:
            if pa:
                div_type = pa.get('div',{}).get('type','neutral')
                if div_type == 'bullish':
                    signals.append(("💎","RSI Uyumsuzluk: POZİTİF (Gizli Güç)","#15803d","Fiyat yeni dip yaparken RSI yüksek dip yapıyor. Satıcıların gücü azalıyor — büyükler dipten topluyor olabilir.",True))
                elif div_type == 'bearish':
                    signals.append(("🐻","RSI Uyumsuzluk: NEGATİF (Yorgun Boğa)","#b91c1c","Fiyat yeni zirve yaparken RSI düşük zirve yapıyor. Yükseliş devam ediyor gibi görünse de içten çürüyor.",False))
                sv = pa.get('smart_volume',{})
                if sv.get('stopping','Yok') != 'Yok':
                    signals.append(("🐋","Balina İzi: Stopping Volume","#15803d","Düşüş yüksek hacimle karşılandı. Kurumsal fren devrede, düşüş durduruluyor olabilir.",True))
                if sv.get('climax','Yok') != 'Yok':
                    signals.append(("🌋","Balina İzi: Climax Volume","#ea580c","Rallinin zirvesinde anormal hacim. Akıllı para malı küçük yatırımcıya boşaltıyor olabilir.",False))
        except: pass

        # ── 19. Z-Score (Professional Multi-Window) ──────────────────
        try:
            _zd = _z_score_details(df)
            if _zd is not None:
                z   = _zd["composite"]
                _z20  = _zd["z20"]
                _z60  = _zd["z60"]
                _z252 = _zd["z252"]
                _atr  = _zd["atr_multiple"]
                _tdir = _zd["trend_dir"]
                _filt = _zd["filtered"]

                # Pencere detayı (kısa özet)
                _win_txt = f"20G:{_z20:+.1f} / 60G:{_z60:+.1f} / 252G:{_z252:+.1f}"
                _atr_txt = f" · ATR katı:{_atr:.1f}x" if _atr > 0 else ""
                _filt_txt = f" · Trend filtresi aktif ({_tdir})" if _filt else f" · Trend: {_tdir}"

                # ATR katına göre sinyal gücü etiketi
                _guc = "Güçlü " if _atr >= 1.5 else ("Zayıf " if _atr < 0.8 else "")

                if z <= -2.0:
                    _desc = (f"Arındırılmış kompozit Z={z:.2f}. "
                             f"3 zaman dilimine göre [{_win_txt}] fiyat trendinden negatif sapmada. "
                             f"{_guc}istatistiksel dönüş bölgesi.{_atr_txt}{_filt_txt}")
                    signals.append(("🔥", f"{_guc}İstatistiksel DİP (Z: {z:.2f})",
                                    "#059669", _desc, True))
                elif z <= -1.5:
                    _desc = (f"Kompozit Z={z:.2f} [{_win_txt}]. "
                             f"Fiyat trendinin altında birikim bölgesine yaklaşıyor.{_atr_txt}{_filt_txt}")
                    signals.append(("⚠️", f"Dibe Yaklaşıyor (Z: {z:.2f})",
                                    "#d97706", _desc, True))
                elif z >= 2.0:
                    _desc = (f"Arındırılmış kompozit Z={z:.2f}. "
                             f"3 zaman dilimine göre [{_win_txt}] fiyat trendinden pozitif sapmada. "
                             f"{_guc}matematiksel aşırı fiyatlanma — düzeltme riski var.{_atr_txt}{_filt_txt}")
                    signals.append(("🚨", f"{_guc}İstatistiksel TEPE (Z: {z:.2f})",
                                    "#f87171", _desc, False))
                elif z >= 1.5:
                    _desc = (f"Kompozit Z={z:.2f} [{_win_txt}]. "
                             f"Trend üstünde gerilim artıyor, düzeltme riski yükseliyor.{_atr_txt}{_filt_txt}")
                    signals.append(("⚠️", f"Tepeye Yaklaşıyor (Z: {z:.2f})",
                                    "#ea580c", _desc, False))
                elif z >= 1.0:
                    _desc = (f"Kompozit Z={z:.2f} [{_win_txt}]. "
                             f"Fiyat trendinin üstünde — yeni alımlarda temkinli ol.{_atr_txt}{_filt_txt}")
                    signals.append(("📈", f"Pahalılanıyor (Z: {z:.2f})",
                                    "#854d0e", _desc, False))
        except: pass

        # ── 20. Harmonik (XABCD) ─────────────────────────────────────
        try:
            hm = calculate_harmonic_patterns(ticker, df)
            if hm:
                is_hmp = hm['direction'] == 'Bullish'
                hmc = ("#15803d") if is_hmp else ("#b91c1c")
                fk = abs(hm['curr_price']-hm['prz'])/(hm['prz']+1e-9)*100
                signals.append(("🔮",f"Harmonik: {hm['pattern']} ({'Bullish' if is_hmp else 'Bearish'}) | PRZ:{hm['prz']:.2f} (%{fk:.1f} uzakta)",hmc,"Fibonacci XABCD oranlarıyla teyit edilmiş dönüş bölgesi. PRZ'ye yaklaşırken yapı ve hacim teyidini bekle.",is_hmp))
        except: pass

        # ── Signal Gating (Regime'e göre karşı-trend etiketleme) ─────
        _rphase = _regime.get("phase", 0)
        gated_signals = []
        for _sig in signals:
            _i, _t, _c, _e, _p = _sig
            _against = False
            if _rphase == 2 and not _p:   # Bull fazında SAT sinyali
                _against = True
            elif _rphase == 4 and _p:     # Bear fazında AL sinyali
                _against = True
            elif _rphase == 3 and _p:     # Dağıtım fazında AL sinyali
                _against = True
            if _against:
                _t = f"{_t} ⚡Karşı Trend"
                _e = f"[Uyarı: {_regime['label']}] " + _e
                # Gri değil — orijinal rengi koru, sadece italik + soluk badge
            gated_signals.append((_i, _t, _c, _e, _p))

        # ── Ayır & Render ────────────────────────────────────────────
        pos_sigs = [(i,t,c,e) for i,t,c,e,p in gated_signals if p]
        neg_sigs = [(i,t,c,e) for i,t,c,e,p in gated_signals if not p]

        if not gated_signals:
            return

        edu_col    = "#cbd5e1"
        def _row(icon, text, color, edu, is_pos, pos_idx=None):
            arr_c = "#4ade80" if is_pos else "#f87171"
            arr   = "↑" if is_pos else "↓"
            uid   = abs(hash(text)) % 999999
            color = "#38bdf8" if is_pos else "#f87171"
            is_ct = "⚡Karşı Trend" in text
            if is_ct:
                _clean_text = text.replace(" ⚡Karşı Trend", "")
                _ct_badge   = (" <span style='font-size:0.65rem;font-style:italic;"
                               "color:#f59e0b;font-weight:700;opacity:0.85;'>⚡ karşı trend</span>")
                _label_html = f"<span style='font-size:0.82rem;font-weight:600;line-height:1.3;flex:1;color:{color};'>{_clean_text}{_ct_badge}</span>"
            else:
                _label_html = f"<span style='font-size:0.82rem;font-weight:600;line-height:1.3;flex:1;color:{color};'>{text}</span>"
            return (
                f"<div class='usp-row' id='ur{uid}' style='border-bottom:1px solid rgba(255,255,255,0.05);cursor:default;'>"
                f"<div style='display:flex;align-items:center;gap:5px;padding:4px 2px;'>"
                f"<span style='font-size:0.95rem;width:18px;text-align:center;flex-shrink:0;line-height:1;'>{icon}</span>"
                f"{_label_html}"
                f"<span style='font-size:0.82rem;font-weight:900;color:{arr_c};flex-shrink:0;'>{arr}</span>"
                f"</div>"
                f"<div style='font-size:0.72rem;color:{edu_col};line-height:1.45;"
                f"padding:0 4px 5px 24px;max-height:0;overflow:hidden;opacity:0;"
                f"transition:max-height 0.22s ease,opacity 0.22s ease;' class='usp-edu'>{edu}</div>"
                f"</div>"
            )

        pos_html = "".join(_row(i,t,c,e,True,idx)  for idx,(i,t,c,e) in enumerate(pos_sigs)) or f"<div style='font-size:0.75rem;color:#64748b;font-style:italic;padding:4px 2px;'>Aktif olumlu sinyal yok.</div>"
        neg_html = "".join(_row(i,t,c,e,False) for i,t,c,e in neg_sigs)

        pc = f"<span style='font-size:0.65rem;padding:0 6px;border-radius:999px;background:rgba(74,222,128,0.15);color:#4ade80;font-weight:800;'>{len(pos_sigs)}</span>" if pos_sigs else ""
        neg_sec = ""
        if neg_sigs:
            neg_sec = (
                f"<div style='display:flex;align-items:center;gap:6px;padding:5px 10px 3px;"
                f"font-size:0.68rem;font-weight:800;text-transform:uppercase;letter-spacing:0.08em;"
                f"color:#f87171;border-top:1px solid rgba(248,113,113,0.2);background:rgba(248,113,113,0.04);'>"
                f"<span style='flex:1;'>Olumsuz</span>"
                f"<span style='font-size:0.65rem;padding:0 6px;border-radius:999px;"
                f"background:rgba(248,113,113,0.15);color:#f87171;font-weight:800;'>{len(neg_sigs)}</span></div>"
                f"<div style='padding:4px 8px;'>{neg_html}</div>"
            )

        # ── Conviction Score HTML ─────────────────────────────────────
        _cv_score = _conviction["score"]
        _cv_label = _conviction["label"]
        _cv_color = _conviction["color"]
        _cv_icon  = _conviction["icon"]
        # Progress bar genişliği
        _cv_bar_w = _cv_score  # 0-100
        # Renk geçiş: düşük → kırmızı, orta → sarı, yüksek → yeşil
        _cv_bg_from = "#ef4444" if _cv_score < 40 else ("#f59e0b" if _cv_score < 55 else "#10b981")
        _cv_bg_to   = "#f87171" if _cv_score < 40 else ("#d97706" if _cv_score < 55 else "#059669")
        _cv_bar_col = f"linear-gradient(90deg, {_cv_bg_from}, {_cv_bg_to})"

        # Skor önce, label sonra (60 → LONG)
        conviction_html = (
            f"<div style='padding:6px 10px 5px;border-bottom:1px solid {border_dim};"
            f"background:rgba(255,255,255,0.02);'>"
            f"<div style='display:flex;align-items:center;gap:6px;margin-bottom:4px;'>"
            f"<span style='font-size:0.72rem;font-weight:700;color:#38bdf8;text-transform:uppercase;"
            f"letter-spacing:0.06em;'>🎯 Kanaat Skoru</span>"
            f"<span style='flex:1;'></span>"
            # Skor sayısı ÖNCE
            f"<span style='font-size:1.0rem;font-weight:900;color:{_cv_color};min-width:28px;"
            f"text-align:right;line-height:1;'>{_cv_score}</span>"
            # Label rozeti SONRA
            f"<span style='font-size:0.75rem;font-weight:900;color:{_cv_color};"
            f"background:{_cv_color}22;padding:1px 9px;border-radius:6px;"
            f"border:1px solid {_cv_color}66;white-space:nowrap;'>{_cv_label}</span>"
            f"</div>"
            f"<div style='height:5px;border-radius:3px;background:rgba(148,163,184,0.15);overflow:hidden;'>"
            f"<div style='height:100%;width:{_cv_bar_w}%;background:{_cv_bar_col};"
            f"border-radius:3px;'></div>"
            f"</div>"
            f"</div>"
        )

        # ── Regime HTML ───────────────────────────────────────────────
        _rg_color = _regime["color"]
        _rg_icon  = _regime["icon"]
        _rg_label = _regime["label"]
        _rg_conf  = int(_regime["confidence"] * 100)
        regime_html = (
            f"<div style='display:flex;align-items:center;gap:6px;padding:4px 10px;"
            f"background:{_rg_color}18;border-bottom:1px solid {border_dim};'>"
            f"<div style='display:flex;flex-direction:column;line-height:1.2;'>"
            f"<span style='font-size:0.72rem;font-weight:700;color:#38bdf8;text-transform:uppercase;"
            f"letter-spacing:0.06em;'>📡 Piyasa Yapısı</span>"
            f"<span style='font-size:0.60rem;font-weight:500;color:#64748b;'>20-200 Gün</span>"
            f"</div>"
            f"<span style='flex:1;font-size:0.78rem;font-weight:800;color:{_rg_color};text-align:right;'>"
            f"{_rg_icon} {_rg_label}</span>"
            f"<span style='font-size:0.68rem;color:{_rg_color};font-weight:800;"
            f"background:{_rg_color}22;padding:1px 6px;border-radius:4px;flex-shrink:0;'>%{_rg_conf}</span>"
            f"</div>"
        )

        # ── Ana panel (kısa vade dahil değil — ayrı render edilecek) ──
        _br_bottom = "0 0 8px 8px" if _kv_show else "8px"
        st.markdown(
            "<style>.usp-row:hover .usp-edu{max-height:100px!important;opacity:1!important;}</style>"
            f'<div style="background:{panel_bg};border:2px solid {panel_border};'
            f'border-radius:8px {_br_bottom};overflow:hidden;'
            f'margin-bottom:{"0" if _kv_show else "8px"};">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;padding:7px 10px;border-bottom:1px solid {border_dim};">'
            f'<span style="font-size:0.85rem;font-weight:800;color:{title_col};">🔔 CANLI SİNYALLER</span>'
            f'<span style="font-size:0.78rem;font-weight:900;color:{karar_color};background:{karar_color}20;padding:2px 8px;border-radius:6px;border:1px solid {karar_color};white-space:nowrap;">{karar_icon} {karar_txt}</span>'
            f'</div>'
            f'{regime_html}'
            f'{conviction_html}'
            f'<div style="display:flex;align-items:center;gap:6px;padding:5px 10px 3px;font-size:0.68rem;font-weight:800;text-transform:uppercase;letter-spacing:0.08em;color:#38bdf8;background:rgba(56,189,248,0.04);">'
            f'<span style="flex:1;">Olumlu</span>{pc}'
            f'</div>'
            f'<div style="padding:4px 8px;">{pos_html}</div>'
            f'{neg_sec}'
            f'</div>',
            unsafe_allow_html=True
        )

        # ── Kısa Vade uyarısı: ayrı st.markdown, panele yapışık ──────
        if _kv_show:
            _kv_str_disp = " · ".join(_kv_warnings)
            _kv_bg   = "rgba(217,119,6,0.12)"
            _kv_bord = "#d97706"
            _kv_titl = "#fbbf24"
            _kv_det  = "#fde68a"
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;'
                f'background:{_kv_bg};border:2px solid {_kv_bord};border-top:none;'
                f'border-radius:0 0 8px 8px;margin-bottom:8px;">'
                f'<span style="font-size:0.9rem;flex-shrink:0;">⚠️</span>'
                f'<div style="display:flex;flex-direction:column;line-height:1.2;flex-shrink:0;">'
                f'<span style="font-size:0.74rem;font-weight:900;color:{_kv_titl};'
                f'text-transform:uppercase;letter-spacing:0.05em;white-space:nowrap;">'
                f'Kısa Vade: İzle</span>'
                f'<span style="font-size:0.60rem;font-weight:500;color:{_kv_det};opacity:0.8;">1-15 Gün</span>'
                f'</div>'
                f'<span style="font-size:0.69rem;color:{_kv_det};font-weight:700;'
                f'flex:1;text-align:right;">{_kv_str_disp}</span>'
                f'</div>',
                unsafe_allow_html=True
            )
    except Exception:
        pass

# ==============================================================================
# BÖLÜM 32 — GENEL ÖZET VE SAĞLIK SİNYALLERİ PANELİ
# Tüm tarama sonuçlarını özetleyen genel özet paneli.
# Piyasa sağlığı göstergeleri ve makro sinyal özeti.
# ==============================================================================
def _render_genel_ozet_panel():
    """GENEL ÖZET — 4 bağımsız sinyal, trend bağlamı, iptal koşulu, LONG RADAR skoru."""
    try:
        if not (st.session_state.get('ticker')):
            return
        _ticker = st.session_state.ticker

        _gs_items_html = ""
        try:
            _gs_dna = calculate_price_action_dna(_ticker)

            # Ardışık mum sayısı
            _gs_consec_label = ""
            _gs_df = None
            try:
                _gs_df = get_safe_historical_data(_ticker, period="3mo")
                if _gs_df is not None and len(_gs_df) >= 3:
                    _gs_cc = _gs_df['Close'].values; _gs_co = _gs_df['Open'].values
                    _gs_bull = _gs_cc[-1] > _gs_co[-1]; _gs_cnt = 1
                    for _gsi in range(2, min(8, len(_gs_df))):
                        if (_gs_cc[-_gsi] > _gs_co[-_gsi]) == _gs_bull: _gs_cnt += 1
                        else: break
                    _gs_consec_label = f"{_gs_cnt} gündür {'yeşil' if _gs_bull else 'kırmızı'} mum"
            except Exception:
                pass

            if _gs_dna:
                # — Veri çek —
                _gs_sv      = _gs_dna.get('smart_volume', {})
                _gs_rvol    = _gs_sv.get('rvol', 1.0)
                _gs_cum5    = _gs_sv.get('cum_delta_5', 0)
                _gs_cdpct   = _gs_sv.get('cum_delta_pct', 0.0)
                _gs_can     = _gs_dna.get('candle', {})
                _gs_vol     = _gs_dna.get('vol', {})
                _gs_obv     = _gs_dna.get('obv', {})
                _gs_sfp     = _gs_dna.get('sfp', {})
                _gs_cdesc   = _gs_can.get('desc', '')
                # Madde 2: sayısal OBV flag (string matching yok)
                _gs_obv_dir = _gs_dna.get('obv_direction', 0)
                # Madde 6: RSI (hacimden tamamen bağımsız)
                _gs_rsi_val = _gs_dna.get('rsi_val', 50.0)

                # Madde 1: Price structure HH+HL — son 3 günde var mı?
                _gs_hh_hl = False; _gs_hh_hl_txt = ""
                try:
                    if _gs_df is not None and len(_gs_df) >= 4:
                        _hs = _gs_df['High'].values; _ls = _gs_df['Low'].values
                        if (_hs[-1] > _hs[-2] > _hs[-3]) and (_ls[-1] > _ls[-2] > _ls[-3]):
                            _gs_hh_hl = True; _gs_hh_hl_txt = "HH+HL yapısı"
                except Exception:
                    pass

                # Madde 3: SMA50 bağlam
                _gs_sma50_txt = ""; _gs_sma50_above = None
                try:
                    if _gs_df is not None and len(_gs_df) >= 52:
                        _cl = _gs_df['Close']; _s50 = _cl.rolling(50).mean()
                        _curr = float(_cl.iloc[-1]); _s50v = float(_s50.iloc[-1])
                        _gs_sma50_above = _curr > _s50v
                        _cnt = sum(1 for i in range(1, min(80, len(_gs_df)))
                                   if not pd.isna(float(_s50.iloc[-i])) and
                                   (_cl.iloc[-i] > _s50.iloc[-i]) == _gs_sma50_above)
                        _side = "üstünde" if _gs_sma50_above else "altında"
                        if _gs_sma50_above:
                            _interp = "Köklü yükseliş" if _cnt >= 20 else ("Yükseliş trendi" if _cnt >= 5 else "Yeni kırılım ↑")
                        else:
                            _interp = "Köklü düşüş" if _cnt >= 20 else ("Düşüş trendi" if _cnt >= 5 else "Yeni kırılım ↓")
                        _gs_sma50_txt = f"SMA50 {_side} · {_cnt} gün · {_interp}"
                except Exception:
                    _gs_sma50_txt = ""

                # Madde 5: Stop seviyesi — 5G dip, ama ATR×2 ile yukarıdan kırpılır
                # (gap-up günleri 5G dip'i çok aşağıya çekiyor — max %8 veya 2×ATR mesafe)
                _gs_low5_txt = ""
                try:
                    if _gs_df is not None and len(_gs_df) >= 5:
                        _curr_close = float(_gs_df['Close'].iloc[-1])
                        _raw_low5   = float(_gs_df['Low'].iloc[-5:].min())
                        # ATR hesapla (14 günlük)
                        try:
                            _atr14 = float(
                                ((_gs_df['High'] - _gs_df['Low']).rolling(14).mean()).iloc[-1]
                            )
                        except Exception:
                            _atr14 = _curr_close * 0.03  # fallback: %3
                        # Stop = max(5G dip, fiyat - 2×ATR)   → dip çok uzaksa ATR ile kırp
                        _atr_floor = _curr_close - 2.0 * _atr14
                        _low5 = max(_raw_low5, _atr_floor)
                        _gs_low5_txt = f"{_low5:.0f}" if _low5 >= 100 else (f"{_low5:.2f}" if _low5 >= 1 else f"{_low5:.4f}")
                except Exception:
                    pass

                # Madde 6: RSI zone sinyal
                _gs_rsi_sig = 0; _gs_rsi_disp = ""
                if _gs_rsi_val < 30:
                    _gs_rsi_sig = +1; _gs_rsi_disp = f"RSI {_gs_rsi_val:.0f} — aşırı satım 💎"
                elif _gs_rsi_val <= 60:
                    _gs_rsi_sig = +1; _gs_rsi_disp = f"RSI {_gs_rsi_val:.0f} — alım bölgesi ✅"
                elif _gs_rsi_val > 70:
                    _gs_rsi_sig = -1; _gs_rsi_disp = f"RSI {_gs_rsi_val:.0f} — aşırı alım ⚠️"
                else:
                    _gs_rsi_disp = f"RSI {_gs_rsi_val:.0f} — nötr"

                # ── DATA-READY FLAG (erken seans: gün içi hacim henüz yok) ──────
                _data_ready = _gs_rvol >= 0.05

                # ── LONG RADAR — verdict band için önceden hesapla ─────────────
                _lr_score = None; _lr_status = ""
                try:
                    _sms = calculate_smart_money_score(_ticker)
                    if _sms and _sms.get('score') is not None:
                        _lr_score  = _sms['score']
                        _lr_status = _sms.get('status', '')
                except Exception:
                    pass

                # ── VOTING: 4 BAĞIMSIZ SİNYAL (hacim oyu data_ready kontrolü) ─
                _sig_hacim = (1 if (_data_ready and _gs_cum5 > 0) else (-1 if (_data_ready and _gs_cum5 < 0) else 0))
                _sig_obv   = (1 if _gs_obv_dir > 0 else (-1 if _gs_obv_dir < 0 else 0))
                _sig_yapi  = (1 if _gs_hh_hl else 0)
                _sig_rsi   = _gs_rsi_sig

                _gs_up = sum(1 for s in (_sig_hacim, _sig_obv, _sig_yapi, _sig_rsi) if s > 0)
                _gs_dn = sum(1 for s in (_sig_hacim, _sig_obv, _sig_yapi, _sig_rsi) if s < 0)

                if   _gs_up >= 3:                  _gs_net_clr = "#4ade80"; _gs_net_txt = "YUKARI"
                elif _gs_dn >= 3:                  _gs_net_clr = "#f87171"; _gs_net_txt = "AŞAĞI"
                elif _gs_up >= 2 and _gs_dn < 2:  _gs_net_clr = "#86efac"; _gs_net_txt = "HAFİF YUKARI"
                elif _gs_dn >= 2 and _gs_up < 2:  _gs_net_clr = "#fca5a5"; _gs_net_txt = "HAFİF AŞAĞI"
                else:                              _gs_net_clr = "#fbbf24"; _gs_net_txt = "KARARSIZ"

                # ── TEMA RENKLERİ (SMR Dark) ──────────────────────────────────
                _gs_txt      = "#cbd5e1"
                _gs_line     = "rgba(56,189,248,0.12)"
                _gs_expl_col = "#cbd5e1"
                _gs_lbl_col  = "#38bdf8"
                _gs_neu      = "#94a3b8"
                _gs_up_clr   = "#4ade80"
                _gs_dn_clr   = "#f87171"
                _lr_clr      = (_gs_up_clr if (_lr_score or 0) >= 60
                                else ("#f59e0b" if (_lr_score or 0) >= 40 else _gs_dn_clr))

                # ── HELPER'LAR ─────────────────────────────────────────────────
                def _arrow(d):
                    if d > 0: return f"<span style='color:{_gs_up_clr};font-weight:800;'>↑</span>"
                    if d < 0: return f"<span style='color:{_gs_dn_clr};font-weight:800;'>↓</span>"
                    return f"<span style='color:{_gs_neu};'>≈</span>"

                def _dir_color(sig):
                    """Sinyale göre etiket rengi: +1 yeşil, -1 kırmızı, 0 gri."""
                    if sig > 0: return _gs_up_clr
                    if sig < 0: return _gs_dn_clr
                    return _gs_neu

                def _gs_section(title):
                    return (f"<div style='margin-top:8px;padding-top:5px;"
                            f"border-top:1px solid {_gs_line};"
                            f"font-size:0.6rem;font-weight:800;letter-spacing:0.09em;"
                            f"color:{_gs_lbl_col};text-transform:uppercase;margin-bottom:1px;'>"
                            f"{title}</div>")

                def _gs_explain(text):
                    return (f"<div style='font-size:0.69rem;color:{_gs_expl_col};"
                            f"margin-top:1px;line-height:1.35;'>↳ {text}</div>")

                def _gs_row(label, value_html, explain=None, stop=False, lc=None):
                    """Etiket sol (lc=label color) — değer sağa hizalı monospace — açıklama altta."""
                    pfx = "⛔ " if stop else ""
                    lbl_col = lc if lc else _gs_txt
                    lbl_weight = "700" if lc else "400"
                    return (
                        f"<div style='padding:4px 0;'>"
                        f"<div style='display:flex;align-items:center;gap:4px;'>"
                        f"<span style='font-size:0.8rem;color:{lbl_col};"
                        f"font-weight:{lbl_weight};flex:1;'>{pfx}{label}</span>"
                        f"<span style='font-family:\"JetBrains Mono\",ui-monospace,Consolas,monospace;"
                        f"font-size:0.81rem;font-weight:700;white-space:nowrap;text-align:right;'>"
                        f"{value_html}</span>"
                        f"</div>"
                        f"{_gs_explain(explain) if explain else ''}"
                        f"</div>"
                    )

                # ── VERDICT BANDI ─────────────────────────────────────────────
                _dom_n      = max(_gs_up, _gs_dn)
                _lr_vhtml   = (f"<span style='color:{_lr_clr};'>{_lr_score}</span>"
                               if _lr_score is not None
                               else f"<span style='color:{_gs_neu};'>—</span>")
                _stop_vhtml = (f"<span style='color:{_gs_dn_clr};'>{_gs_low5_txt}</span>"
                               if _gs_low5_txt
                               else f"<span style='color:{_gs_neu};'>—</span>")
                _sig_bd     = (f"Hacim {_arrow(_sig_hacim)} · OBV {_arrow(_sig_obv)} · "
                               f"Yapı {_arrow(_sig_yapi)} · RSI {_arrow(_sig_rsi)}")
                _mono_s     = '"JetBrains Mono",ui-monospace,Consolas,monospace'

                # LONG değerini "37/100" formatında göster
                _lr_vhtml   = (f"<span style='color:{_lr_clr};font-size:0.72rem;'>{_lr_score}/100</span>"
                               if _lr_score is not None
                               else f"<span style='color:{_gs_neu};font-size:0.72rem;'>—</span>")
                _stop_vhtml = (f"<span style='color:{_gs_dn_clr};font-size:0.72rem;'>{_gs_low5_txt}</span>"
                               if _gs_low5_txt
                               else f"<span style='color:{_gs_neu};font-size:0.72rem;'>—</span>")

                _gs_items_html += (
                    f"<div style='background:rgba(56,189,248,0.07);"
                    f"border:1px solid {_gs_line};border-radius:6px;"
                    f"padding:8px 10px;margin-bottom:2px;'>"
                    f"<div style='display:flex;align-items:center;"
                    f"font-family:{_mono_s};font-weight:800;gap:0;'>"
                    f"<span style='color:{_gs_net_clr};flex:1;font-size:0.83rem;'>{_gs_net_txt} "
                    f"<span style='opacity:0.6;font-size:0.65rem;font-weight:600;'>{_dom_n}/4</span></span>"
                    f"<span style='color:{_gs_neu};padding:0 6px;font-weight:400;font-size:0.72rem;'>|</span>"
                    f"<span style='color:{_gs_neu};font-size:0.62rem;font-weight:600;letter-spacing:0.04em;margin-right:3px;'>LONG</span>{_lr_vhtml}"
                    f"<span style='color:{_gs_neu};padding:0 6px;font-weight:400;font-size:0.72rem;'>|</span>"
                    f"<span style='color:{_gs_neu};font-size:0.62rem;font-weight:600;letter-spacing:0.04em;margin-right:3px;'>STOP</span>{_stop_vhtml}"
                    f"</div>"
                    f"<div style='font-size:0.69rem;color:{_gs_expl_col};margin-top:5px;'>↳ {_sig_bd}</div>"
                    f"</div>"
                )

                # ── GRUP 1: YÖN (önce macro/orta vade, sonra kısa vade) ──────────
                _gs_items_html += _gs_section("Yön")
                _net_sig = (1 if _gs_net_txt in ("YUKARI", "HAFİF YUKARI")
                            else (-1 if _gs_net_txt in ("AŞAĞI", "HAFİF AŞAĞI") else 0))

                # Ana trend önce — orta vade bağlamı (2-3 ay)
                if _gs_sma50_txt:
                    _s50col  = _gs_up_clr if _gs_sma50_above else _gs_dn_clr
                    _s50_dir = "YUKARI" if _gs_sma50_above else "AŞAĞI"
                    _gs_items_html += _gs_row(
                        "Ana trend",
                        f"<span style='color:{_s50col};'>{_s50_dir}</span>",
                        explain=("Orta vade (2–3 ay) · " + _gs_sma50_txt +
                                 (" · sağlam" if _gs_sma50_above else " · baskılı")),
                        lc=_s50col
                    )

                # Kısa Vade görünüm sonra — kısa vade (bugün/bu hafta)
                _gs_items_html += _gs_row(
                    "Kısa Vade Görünüm",
                    f"<span style='color:{_gs_net_clr};'>{_gs_net_txt}</span>",
                    explain=f"Kısa vade (bugün–bu hafta) · 4 sinyal oylaması — {_dom_n}/4 aynı yönde",
                    lc=_dir_color(_net_sig)
                )

                # ── GRUP 2: PARA AKIŞI ────────────────────────────────────────
                _gs_items_html += _gs_section("Para akışı")

                if _gs_cdpct > 0:
                    _bp = 50 + _gs_cdpct / 2; _sp = 50 - _gs_cdpct / 2
                    _dpct = (f"%{_bp:.0f} alım / %{_sp:.0f} satış" if _gs_cum5 >= 0
                             else f"%{_sp:.0f} alım / %{_bp:.0f} satış")
                else:
                    _dpct = "dengede"

                if not _data_ready:
                    _gs_items_html += _gs_row(
                        "Hacim",
                        f"<span style='color:{_gs_neu};'>henüz yok</span>",
                        explain=f"Gün içi hacim oluşmadı · son 5 gün dengesi: {_dpct}",
                        lc=_gs_neu
                    )
                else:
                    _vol_intp = "ortalama üstü" if _gs_rvol >= 1.5 else ("normal" if _gs_rvol >= 0.8 else "zayıf")
                    _vol_clr  = _gs_up_clr if _gs_rvol >= 1.5 else (_gs_neu if _gs_rvol >= 0.8 else _gs_dn_clr)
                    _gs_items_html += _gs_row(
                        "Hacim",
                        (f"<span style='color:{_vol_clr};'>{_gs_rvol:.1f}x</span> "
                         f"<span style='color:{_gs_neu};font-size:0.73rem;'>{_vol_intp}</span>"),
                        explain=f"Son 5 gün alıcı/satıcı dengesi: {_dpct}",
                        lc=_dir_color(_sig_hacim)
                    )

                _obv_t = _gs_obv.get('title', ''); _obv_d = _gs_obv.get('desc', '')
                _obv_map = {
                    "🔥 GÜÇLÜ GİZLİ GİRİŞ":   ("Gizli giriş",    _gs_up_clr, "Fiyat düşerken büyük oyuncular sessizce alıyor"),
                    "👀 Olası Toplama (Zayıf)": ("Zayıf toplama",  _gs_neu,    "Hafif alım emaresi var, henüz güçlü değil"),
                    "⚠️ GİZLİ ÇIKIŞ":          ("Gizli çıkış",   _gs_dn_clr, "Fiyat çıkarken büyük oyuncular sessizce satıyor"),
                    "✅ Hacim Destekli Trend":   ("Trend destekli", _gs_up_clr, "Hacim yönü destekliyor — sağlıklı hareket"),
                }
                _ov_lbl, _ov_clr, _ov_expl = _obv_map.get(
                    _obv_t, ("nötr", _gs_neu, "Akıllı para hareketi belirgin değil"))
                _gs_items_html += _gs_row(
                    "OBV",
                    f"<span style='color:{_ov_clr};'>{_ov_lbl}</span>",
                    explain=_ov_expl,
                    lc=_ov_clr
                )

                _sfp_t = _gs_sfp.get('title', 'Yok')
                if _sfp_t and _sfp_t != 'Yok':
                    import re as _sfp_re
                    _sfp_clean = _sfp_re.sub(
                        r'[^\w\s\(\)\.\,\:\!\?çğıöşüÇĞİÖŞÜ%/-]', '', _sfp_t).strip()
                    _gs_items_html += _gs_row(
                        "SFP",
                        f"<span style='color:{_gs_dn_clr};'>{_sfp_clean}</span>",
                        explain=_gs_sfp.get('desc', 'Sahte kırılım — fiyat seviyeyi geçti ama tutmadı'),
                        lc=_gs_dn_clr
                    )

                # ── GRUP 3: MOMENTUM ──────────────────────────────────────────
                _gs_items_html += _gs_section("Momentum")

                if _gs_rsi_disp:
                    if _gs_rsi_val < 30:
                        _rsi_lbl = "aşırı satım"; _rc = _gs_up_clr
                        _rsi_expl = "Aşırı satım — düşüş hız kaybediyor, tek başına dönüş garantisi değil"
                    elif _gs_rsi_val <= 60:
                        _rsi_lbl = "alım bölgesi"; _rc = _gs_up_clr
                        _rsi_expl = "Sağlıklı alım bölgesi, momentum aktif"
                    elif _gs_rsi_val > 70:
                        _rsi_lbl = "aşırı alım"; _rc = _gs_dn_clr
                        _rsi_expl = "Güçlü trendlerde RSI haftalarca 70+ kalabilir — OBV/Hacim çelişkisi yoksa düzeltme zorunlu değil"
                    else:
                        _rsi_lbl = "nötr"; _rc = _gs_neu
                        _rsi_expl = "Nötr bölge, yön sinyali zayıf"
                    _gs_items_html += _gs_row(
                        "RSI",
                        (f"<span style='color:{_rc};'>{_gs_rsi_val:.0f}</span> "
                         f"<span style='color:{_gs_neu};font-size:0.73rem;'>{_rsi_lbl}</span>"),
                        explain=_rsi_expl,
                        lc=_rc
                    )

                def _strip_emoji(s):
                    import re
                    return re.sub(r'[^\w\s\(\)\.\,\:\!\?çğıöşüÇĞİÖŞÜ%/-]', '', s).strip()

                if _gs_cdesc and _gs_cdesc != 'Belirgin, güçlü bir formasyon yok.':
                    _cand_clean = _gs_cdesc.replace('ALICI:', '').replace('SATICI:', '').strip()
                    _cand_short = _cand_clean.split('|')[0].strip() if '|' in _cand_clean else _cand_clean
                    _cand_short = _strip_emoji(_cand_short)[:40]
                    _bg_parts = []
                    if _gs_consec_label: _bg_parts.append(_gs_consec_label)
                    if _gs_hh_hl_txt:    _bg_parts.append("HH+HL yapısı")
                    _bg_txt = " · ".join(_bg_parts)
                    if _gs_cdesc.startswith('ALICI'):
                        _gs_items_html += _gs_row(
                            "Mum",
                            f"<span style='color:{_gs_up_clr};'>{_cand_short}</span>",
                            explain="Yükseliş sinyali" + (f" · {_bg_txt}" if _bg_txt else ""),
                            lc=_gs_up_clr
                        )
                    elif _gs_cdesc.startswith('SATICI'):
                        _gs_items_html += _gs_row(
                            "Mum",
                            f"<span style='color:{_gs_dn_clr};'>{_cand_short}</span>",
                            explain="Düşüş sinyali" + (f" · {_bg_txt}" if _bg_txt else ""),
                            lc=_gs_dn_clr
                        )
                    else:
                        _gs_items_html += _gs_row(
                            "Mum",
                            f"<span style='color:{_gs_neu};'>{_cand_short}</span>",
                            explain="Yön belirsiz formasyon",
                            lc=_gs_neu
                        )
                elif _gs_consec_label or _gs_hh_hl_txt:
                    _bg = []
                    if _gs_consec_label: _bg.append(_gs_consec_label)
                    if _gs_hh_hl_txt:    _bg.append("HH+HL yapısı")
                    _gs_items_html += _gs_row(
                        "Mum",
                        f"<span style='color:{_gs_neu};'>belirgin yok</span>",
                        explain=" · ".join(_bg),
                        lc=_gs_neu
                    )

                # ── GRUP 4: AKSİYON ──────────────────────────────────────────
                _gs_items_html += _gs_section("Aksiyon")

                if _gs_low5_txt:
                    if _gs_net_txt in ("YUKARI", "HAFİF YUKARI"):
                        _gs_items_html += _gs_row(
                            "Stop seviyesi",
                            f"<span style='color:{_gs_dn_clr};'>{_gs_low5_txt}</span>",
                            explain="Altında kapanış → yükseliş senaryosu iptal",
                            stop=True, lc=_gs_dn_clr
                        )
                    elif _gs_net_txt in ("AŞAĞI", "HAFİF AŞAĞI"):
                        _gs_items_html += _gs_row(
                            "İptal noktası",
                            f"<span style='color:{_gs_up_clr};'>{_gs_low5_txt}</span>",
                            explain="Üstünde kapanış → düşüş senaryosu iptal",
                            stop=True, lc=_gs_dn_clr
                        )
                    else:
                        _gs_items_html += _gs_row(
                            "Kritik seviye",
                            f"<span style='color:{_gs_neu};'>{_gs_low5_txt}</span>",
                            explain="Kırılım yönü senaryoyu belirler",
                            stop=True, lc=_gs_neu
                        )

                if _lr_score is not None:
                    if _lr_score >= 65:
                        _radar_expl = "Tüm kriterler hizalandı — alım kurulumu hazır"
                    elif _lr_score >= 45:
                        _radar_expl = "Bazı kriterler eksik — henüz alım değil, takipte tut"
                    elif _lr_score >= 25:
                        _radar_expl = "Çok az kriter geçti — erken, beklemede"
                    else:
                        _radar_expl = "Kriterler olumsuz — şimdilik radar dışı"
                    _gs_items_html += _gs_row(
                        "LONG RADAR",
                        (f"<span style='color:{_lr_clr};'>{_lr_score}/100</span> "
                         f"<span style='color:{_gs_neu};font-size:0.73rem;'>{_lr_status}</span>"),
                        explain=_radar_expl + " · 5 alım kriterinin birleşik skoru",
                        lc=_lr_clr
                    )

        except Exception:
            _gs_items_html = "<div style='font-size:0.7rem;color:#64748b;padding:6px 2px;font-style:italic;'>Özet hesaplanamadı.</div>"

        _hdr_bg = "linear-gradient(90deg,#0d1829 0%,#0f2040 100%)"
        _hdr_txt = "#38bdf8"; _cnt_bg = "#060d1a"; _border = "#1e3a5f"

        st.markdown(f"""
        <details open style="margin-bottom:7px;border-radius:10px;overflow:hidden;
                        border:1px solid {_border};box-shadow:0 4px 12px rgba(0,0,0,0.4);">
          <summary style="cursor:pointer;padding:8px 13px;background:{_hdr_bg};
                          display:flex;align-items:center;gap:8px;
                          font-size:1rem;font-weight:800;color:{_hdr_txt};
                          letter-spacing:0.05em;list-style:none;user-select:none;
                          border-bottom:1px solid {_border};">
            <span style="font-size:1.1rem;line-height:1;">⚡</span>
            GENEL ÖZET
            <span style="margin-left:auto;font-size:0.7rem;opacity:0.5;font-weight:400;">▾</span>
          </summary>
          <div style="background:{_cnt_bg};padding:8px 12px 10px 12px;">
            {_gs_items_html if _gs_items_html else "<div style='font-size:0.7rem;color:#64748b;padding:6px 2px;font-style:italic;'>Veri bekleniyor...</div>"}
          </div>
        </details>
        """, unsafe_allow_html=True)
    except Exception:
        pass


def _render_health_signals_panel():
    # --- YENİ YERİ: GENEL SAĞLIK PANELİ (SIDEBAR İÇİN OPTİMİZE EDİLDİ) ---
    try:
        if "ticker" in st.session_state and st.session_state.ticker:
            master_score, score_pros, score_cons = calculate_master_score(st.session_state.ticker)

            st.markdown("<div style='text-align:center; font-weight:800; font-size:1rem; color:#38bdf8; margin-bottom:5px; margin-top:5px;'>TEKNİK GÖRÜNÜM</div>", unsafe_allow_html=True)

            # 1. HIZ GÖSTERGESİ (GAUGE)
            render_gauge_chart(master_score)

            # CSS: Özel ve İnce Kaydırma Çubuğu (Custom Scrollbar)
            custom_scrollbar_css = """
            <style>
            .custom-scroll::-webkit-scrollbar { width: 4px; }
            .custom-scroll::-webkit-scrollbar-track { background: transparent; }
            .custom-scroll::-webkit-scrollbar-thumb { background-color: rgba(56,189,248,0.25); border-radius: 10px; }
            .custom-scroll:hover::-webkit-scrollbar-thumb { background-color: rgba(56,189,248,0.5); }
            </style>
            """
            st.markdown(custom_scrollbar_css, unsafe_allow_html=True)

            # 2. POZİTİF ETKENLER
            pos_items_html = ""
            if score_pros:
                _p_txt  = "#cbd5e1"
                _p_line = "rgba(74,222,128,0.12)"
                for p in score_pros:
                    pos_items_html += (
                        f"<div style='display:flex;align-items:flex-start;gap:6px;"
                        f"padding:5px 0;border-bottom:1px solid {_p_line};'>"
                        f"<span style='color:#4ade80;font-size:0.65rem;margin-top:2px;flex-shrink:0;'>●</span>"
                        f"<span style='font-size:0.7rem;line-height:1.4;color:{_p_txt};'>{p}</span>"
                        f"</div>"
                    )
            else:
                _p_txt = "#64748b"
                pos_items_html = f"<div style='font-size:0.7rem;color:{_p_txt};padding:6px 2px;font-style:italic;'>Belirgin pozitif etken yok.</div>"

            _pos_hdr_bg   = "linear-gradient(90deg,#0d1829 0%,rgba(74,222,128,0.08) 100%)"
            _pos_hdr_txt  = "#4ade80"
            _pos_cnt_bg   = "#0a1628"
            _pos_border   = "rgba(74,222,128,0.4)"

            st.markdown(f"""
            <details style="margin-bottom:7px;border-radius:10px;overflow:hidden;
                            border:1px solid {_pos_border};box-shadow:0 1px 4px rgba(0,0,0,0.3);">
              <summary style="cursor:pointer;padding:9px 13px;background:{_pos_hdr_bg};
                              display:flex;align-items:center;gap:8px;
                              font-size:0.72rem;font-weight:700;color:{_pos_hdr_txt};
                              letter-spacing:0.03em;list-style:none;user-select:none;">
                <span style="font-size:1rem;line-height:1;">✅</span>
                POZİTİF ETKENLER
                <span style="margin-left:6px;background:rgba(74,222,128,0.2);color:#4ade80;
                             font-size:0.6rem;font-weight:800;padding:1px 6px;
                             border-radius:999px;border:1px solid rgba(74,222,128,0.4);">{len(score_pros)}</span>
                <span style="margin-left:auto;font-size:0.75rem;color:#4ade80;font-weight:900;">▾</span>
              </summary>
              <div class="custom-scroll"
                   style="background:{_pos_cnt_bg};padding:6px 12px 8px 12px;
                          max-height:160px;overflow-y:auto;">
                {pos_items_html}
              </div>
            </details>
            """, unsafe_allow_html=True)

            # 3. NEGATİF ETKENLER
            neg_items_html = ""
            if score_cons:
                _n_txt  = "#cbd5e1"
                _n_line = "rgba(248,113,113,0.12)"
                for c in score_cons:
                    neg_items_html += (
                        f"<div style='display:flex;align-items:flex-start;gap:6px;"
                        f"padding:5px 0;border-bottom:1px solid {_n_line};'>"
                        f"<span style='color:#f87171;font-size:0.65rem;margin-top:2px;flex-shrink:0;'>●</span>"
                        f"<span style='font-size:0.7rem;line-height:1.4;color:{_n_txt};'>{c}</span>"
                        f"</div>"
                    )
            else:
                _n_txt = "#64748b"
                neg_items_html = f"<div style='font-size:0.7rem;color:{_n_txt};padding:6px 2px;font-style:italic;'>Belirgin negatif etken yok.</div>"

            _neg_hdr_bg   = "linear-gradient(90deg,#0d1829 0%,rgba(248,113,113,0.08) 100%)"
            _neg_hdr_txt  = "#f87171"
            _neg_cnt_bg   = "#0a1628"
            _neg_border   = "rgba(248,113,113,0.4)"

            st.markdown(f"""
            <details style="margin-bottom:7px;border-radius:10px;overflow:hidden;
                            border:1px solid {_neg_border};box-shadow:0 1px 4px rgba(0,0,0,0.3);">
              <summary style="cursor:pointer;padding:9px 13px;background:{_neg_hdr_bg};
                              display:flex;align-items:center;gap:8px;
                              font-size:0.72rem;font-weight:700;color:{_neg_hdr_txt};
                              letter-spacing:0.03em;list-style:none;user-select:none;">
                <span style="font-size:1rem;line-height:1;">❌</span>
                NEGATİF ETKENLER
                <span style="margin-left:6px;background:rgba(248,113,113,0.2);color:#f87171;
                             font-size:0.6rem;font-weight:800;padding:1px 6px;
                             border-radius:999px;border:1px solid rgba(248,113,113,0.4);">{len(score_cons)}</span>
                <span style="margin-left:auto;font-size:0.75rem;color:#f87171;font-weight:900;">▾</span>
              </summary>
              <div class="custom-scroll"
                   style="background:{_neg_cnt_bg};padding:6px 12px 8px 12px;
                          max-height:160px;overflow-y:auto;">
                {neg_items_html}
              </div>
            </details>
            """, unsafe_allow_html=True)

    except Exception as e:
        st.warning(f"Genel Sağlık tablosu oluşturulamadı. Hata: {e}")


    # (Eski Canlı Sinyaller paneli kaldırıldı — render_unified_signals_panel() ile birleştirildi)


#
# ==============================================================================
# 5. SIDEBAR UI
# ==============================================================================
with st.sidebar:
    st.markdown(f"""<div style="font-size:1.5rem; font-weight:800; background: linear-gradient(135deg,#10b981,#38bdf8); -webkit-background-clip:text; -webkit-text-fill-color:transparent; text-align:center; padding-top:10px; padding-bottom:10px; letter-spacing:1px;">SMART MONEY RADAR</div>""", unsafe_allow_html=True)

    # --- GENEL ÖZET — başlığın hemen altında ---
    _render_genel_ozet_panel()

    # --- TEKNİK GÖRÜNÜM (GAUGE) ---
    _render_health_signals_panel()

    # --- ICT BOTTOM LINE (SONUÇ) ---
    try:
        if st.session_state.get('ticker'):
            _bl_data = calculate_ict_deep_analysis(st.session_state.ticker)
            _bl_text = _bl_data.get('bottom_line', '') if _bl_data else ''
            if _bl_text:
                _bl_ticker = get_display_name(st.session_state.ticker)
                _bl_info = fetch_stock_info(st.session_state.ticker)
                _bl_price = _bl_info.get('price', 0) if _bl_info else 0
                _bl_price_str = f"{int(_bl_price):,}" if _bl_price >= 1000 else f"{_bl_price:.2f}"
                st.markdown(f"""<div style="background:linear-gradient(135deg,#0d1829,#0f2040);border:1px solid #1e3a5f;border-radius:8px;padding:10px 12px;margin-bottom:8px;text-align:center;box-shadow:0 4px 12px rgba(0,0,0,0.4);">
<div style="font-weight:700;color:#475569;font-size:0.68rem;margin-bottom:5px;text-transform:uppercase;letter-spacing:0.08em;">🖥️ ICT BOTTOM LINE / SONUÇ</div>
<div style="display:inline-block;background:rgba(56,189,248,0.1);border:1px solid rgba(56,189,248,0.25);border-radius:5px;padding:3px 14px;margin-bottom:7px;font-family:'JetBrains Mono',monospace;font-weight:800;font-size:0.95rem;color:#38bdf8;letter-spacing:0.03em;">{_bl_ticker}&nbsp;&nbsp;—&nbsp;&nbsp;{_bl_price_str}</div>
<div style="font-size:0.78rem;color:#cbd5e1;font-style:italic;line-height:1.55;">"{_bl_text}"</div></div>""", unsafe_allow_html=True)
    except: pass

    # 🎯 AKILLI PARA SKORU — ICT Bottom Line altında, Kurumsal Para İştahı üstünde
    if st.session_state.get('ticker'):
        render_smart_money_panel(st.session_state.ticker)

    # (Genel Sağlık + Canlı Sinyaller artık _render_health_signals_panel() ile sağ sütunda gösteriliyor)

    # --------------------------------------------------
    # --- TEMEL ANALİZ DETAYLARI (DÜZELTİLMİŞ & TEK PARÇA) ---
    sentiment_verisi = calculate_sentiment_score(st.session_state.ticker)

    # 1. PİYASA DUYGUSU (En Üstte)
    sentiment_verisi = calculate_sentiment_score(st.session_state.ticker)
    if sentiment_verisi:
        render_sentiment_card(sentiment_verisi)

    st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)

    st.divider()
    # 4. AI ANALIST PANELİ
    with st.expander("🤖 AI Analist (Prompt)", expanded=True):
        st.caption("Verileri toplayıp Yapay Zeka için hazır metin oluşturur.")
        if st.button("📋 Analiz Metnini Hazırla", type="primary"): 
            st.session_state.generate_prompt = True

    st.divider()
    # MINERVINI PANELİ (Hatasız Versiyon)
    render_minervini_panel_v2(st.session_state.ticker)
   
# ==============================================================================
# BÖLÜM 33 — ELİT TARAMA SİSTEMİ (🏆 ALTIN FIRSAT & 💎 PLATİN FIRSAT (SÜPER TARAMA MOTORU)
# get_golden_trio_batch_scan: Güç + Konum + Enerji üçlüsü.
# Platin seviyesi için SMA200/SMA50 üstü + RSI<70 ek filtresi.
# ==============================================================================
def get_golden_trio_batch_scan(ticker_list):
    # Gerekli tüm kütüphaneleri burada çağırıyoruz
    import yfinance as yf
    import pandas as pd
    import time

    # --- YARDIMCI RSI HESAPLAMA FONKSİYONU ---
    def calc_rsi_manual(series, period=14):
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    golden_candidates = []
    platin_candidates = [] # YENİ: Platin Fırsat adayları
    tekli_altin_candidates = [] # Tekli hisse kriterleri (Altın %65 Discount + Platin bayrağı)

    # 1. BİLGİLENDİRME & HAZIRLIK
    st.toast("Veri Ambari İndiriliyor (1 Yıllık Derinlik)...", icon="⏳")
    progress_text = "📡 Tüm Piyasa Verisi Tek Pakette İndiriliyor (Ban Korumalı Mod)..."
    my_bar = st.progress(10, text=progress_text)

    # 2. ENDEKS VERİSİNİ AL (Hafızadan Çeker)
    index_close = fetch_index_data_cached()

    # 3. TOPLU İNDİRME (Hafızadan Çeker - BAN Korumalı)
    try:
        data = fetch_market_data_cached(tuple(ticker_list))
    except Exception as e:
        st.error(f"Veri çekme hatası: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    my_bar.progress(40, text="⚡ Hafızadaki Veriler İşleniyor (Çift Katmanlı Analiz)...")

    # 4. HIZLI ANALİZ DÖNGÜSÜ
    if isinstance(data.columns, pd.MultiIndex):
        valid_tickers = [t for t in ticker_list if t in data.columns.levels[0]]
    else:
        valid_tickers = ticker_list if not data.empty else []

    total_tickers = len(valid_tickers)

    for i, ticker in enumerate(valid_tickers):
        try:
            # Veriyi al
            if isinstance(data.columns, pd.MultiIndex):
                df = data[ticker].copy()
            else:
                df = data.copy()

            # Veri yetersizse atla (SMA200 için en az 200 bar lazım)
            if df.empty or len(df) < 200: continue

            # --- YENİ: DÜŞEN BIÇAK VE TUZAK KALKANI (5 KURAL) ---
            today_c = df['Close'].iloc[-1]
            today_o = df['Open'].iloc[-1]
            today_h = df['High'].iloc[-1]
            today_l = df['Low'].iloc[-1]
            yest_c = df['Close'].iloc[-2]
            yest_o = df['Open'].iloc[-2]
            day2_c = df['Close'].iloc[-3]

            # 1. Kırmızı Mum — filtre değil, uyarı bayrağı
            has_red_candle = today_c < today_o

            # 2. Son 2 Günlük Mikro RS Kalkanı (Dün kırmızı, bugün yeşilse)
            if yest_c < yest_o and today_c >= today_o:
                if index_close is not None and len(index_close) > 3:
                    stock_2d_ret = (today_c / day2_c) - 1
                    index_2d_ret = (index_close.iloc[-1] / index_close.iloc[-3]) - 1
                    if stock_2d_ret < index_2d_ret:
                        continue # Ölü kedi sıçraması, endeksi yenemedi, ele.

            # 3. %4 Çöküş Koruması
            crash_2d = (today_c - day2_c) / day2_c
            if crash_2d < -0.04:
                continue # 2 günde %4'ten fazla düştüyse şelaledir, ele.

            # UYARI BAYRAKLARI (Shooting Star & Doji)
            has_warning = False
            body = abs(today_c - today_o)
            rng = today_h - today_l
            upper_shadow = today_h - max(today_c, today_o)
            lower_shadow = min(today_c, today_o) - today_l

            # 4. Shooting Star (Kayan Yıldız) Uyarısı
            if upper_shadow >= 2 * body and lower_shadow <= body and body > 0:
                has_warning = True

            # 5. Doji Uyarısı
            if rng > 0 and body <= rng * 0.1:
                has_warning = True

            current_price = today_c
            
            # --- KRİTER 1: GÜÇ (RS) — tekli incelemeyle tutarlı ---
            is_powerful = False
            prev_price_rs = df['Close'].iloc[-10]
            rsi_val = calc_rsi_manual(df['Close']).iloc[-1]

            if index_close is not None and len(index_close) > 10:
                stock_ret = (current_price / prev_price_rs) - 1
                index_ret = (index_close.iloc[-1] / index_close.iloc[-10]) - 1
                # Tekli inceleme: endeksi geçti VEYA sentiment>=50 VEYA RSI>50
                if stock_ret > index_ret or rsi_val > 50:
                    is_powerful = True
            else:
                if rsi_val > 50:
                    is_powerful = True

            # --- KRİTER 2: KONUM — tekli incelemeyle tutarlı (3 alternatif kapı) ---
            high_60 = df['High'].rolling(60).max().iloc[-1]
            low_60 = df['Low'].rolling(60).min().iloc[-1]
            range_diff = high_60 - low_60

            is_discount = False
            # Kapı 1: 3 aylık bandın alt %72'sinde
            if range_diff > 0:
                loc_ratio = (current_price - low_60) / range_diff
                if loc_ratio < 0.72:
                    is_discount = True

            # Kapı 2 & 3: BOS veya MSS yapı kırılımı — tekli incelemede de bu alternatif geçerli
            if not is_discount:
                _sw_highs_scan = []
                _sw_lows_scan  = []
                for _si in range(2, min(len(df) - 2, 30)):
                    try:
                        if df['High'].iloc[-_si] >= max(df['High'].iloc[-_si-2:-_si].max(), df['High'].iloc[-_si+1:-_si+3].max()):
                            _sw_highs_scan.append(float(df['High'].iloc[-_si]))
                        if df['Low'].iloc[-_si] <= min(df['Low'].iloc[-_si-2:-_si].min(), df['Low'].iloc[-_si+1:-_si+3].min()):
                            _sw_lows_scan.append(float(df['Low'].iloc[-_si]))
                    except:
                        pass
                if _sw_highs_scan and current_price > _sw_highs_scan[0]:
                    is_discount = True  # BOS yukarı
                elif _sw_lows_scan and current_price < _sw_lows_scan[0]:
                    is_discount = True  # MSS / BOS aşağı

            # --- KRİTER 3: ENERJİ (HACİM / MOMENTUM) ---
            vol_sma20 = df['Volume'].rolling(20).mean().iloc[-1]
            current_vol = df['Volume'].iloc[-1]
            rsi_now = rsi_val  # zaten hesaplandı
            is_energy = (current_vol > vol_sma20 * 1.05) or (rsi_now > 45)

            # === ALTIN FIRSAT ===
            if is_powerful and is_discount and is_energy:
                # Piyasa Değeri
                try:
                    info = yf.Ticker(ticker).info
                    mcap = info.get('marketCap', 0)
                except:
                    mcap = 0

                # Patlamaya en hazır olanı saptamak için Teknik Skor Üretimi:
                # RSI Momentumu + Hacim Şiddeti Çarpanı + Göreceli Güç (Endeks Farkı)
                vol_ratio = (current_vol / vol_sma20) if vol_sma20 > 0 else 1
                rs_farki = 0
                if index_close is not None and len(index_close) > 10:
                    rs_farki = ((current_price / prev_price_rs - 1) - (index_close.iloc[-1] / index_close.iloc[-10] - 1)) * 100
                
                teknik_skor = round(rsi_now + (vol_ratio * 15) + rs_farki, 2)

                golden_candidates.append({
                    "Hisse": ticker,
                    "Fiyat": current_price,
                    "M.Cap": mcap,
                    "Teknik_Skor": teknik_skor,
                    "Onay": "🏆 RS Gücü + Ucuz Konum + Güçlü Enerji",
                    "Warning": has_warning,
                    "RedCandle": has_red_candle,
                })

            # === PLATİN HAZIR — Bağımsız Filtre ===
            # is_discount şartı YOK. Güçlü Yapı + Uçmamış + 3 Kapıdan 2'si.
            try:
                _c = df['Close']; _v = df['Volume']
                _s200  = float(_c.rolling(200).mean().iloc[-1])
                _s50   = float(_c.rolling(50).mean().iloc[-1])
                _s50_5 = float(_c.rolling(50).mean().iloc[-5])
                _dist  = (current_price / _s50 - 1) * 100 if _s50 > 0 else 999
                _g20   = (current_price / float(_c.iloc[-20]) - 1) * 100 if len(_c) >= 20 else 999

                if (current_price > _s200 and current_price > _s50 and
                        _s50 > _s50_5 and 35 <= rsi_now <= 73 and
                        _dist <= 15 and _g20 <= 20):

                    _kapi = 0; _kapi_list = []

                    # Kapı 1: Hacim Kuruması (son 5g < 20g ort. %80)
                    _v5  = float(_v.iloc[-5:].mean())
                    _v20 = float(_v.rolling(20).mean().iloc[-1])
                    if _v20 > 0 and _v5 < _v20 * 0.80:
                        _kapi += 1; _kapi_list.append("Hacim Kurudu")

                    # Kapı 2: Endeksten Güçlü (son 10g)
                    if index_close is not None and len(index_close) >= 10:
                        _sr = float(_c.iloc[-1]) / float(_c.iloc[-10]) - 1
                        _ir = float(index_close.iloc[-1]) / float(index_close.iloc[-10]) - 1
                        if _sr > _ir:
                            _kapi += 1; _kapi_list.append("Endeksten Güçlü")

                    # Kapı 3: 10 Gün Sıkışma (bant ≤ %8)
                    _h10 = float(df['High'].tail(10).max())
                    _l10 = float(df['Low'].tail(10).min())
                    if _l10 > 0 and (_h10 - _l10) / _l10 * 100 <= 8:
                        _kapi += 1; _kapi_list.append("10g Sıkışma")

                    if _kapi >= 2:
                        _vr  = float(_v.iloc[-1]) / _v20 if _v20 > 0 else 1
                        _rs  = 0.0
                        if index_close is not None and len(index_close) >= 10:
                            _rs = ((float(_c.iloc[-1]) / float(_c.iloc[-10]) - 1) -
                                   (float(index_close.iloc[-1]) / float(index_close.iloc[-10]) - 1)) * 100
                        _skor = round(rsi_now + (_vr * 15) + _rs + (_kapi * 5), 2)
                        try:
                            _mcap_p = yf.Ticker(ticker).fast_info.get('marketCap', 0)
                        except:
                            _mcap_p = 0
                        platin_candidates.append({
                            "Hisse":       ticker,
                            "Fiyat":       round(current_price, 2),
                            "M.Cap":       _mcap_p,
                            "Teknik_Skor": _skor,
                            "Hazırlık":    f"{_kapi}/3",
                            "Kurulum":     " + ".join(_kapi_list),
                            "Onay":        "💎 PLATİN HAZIR: " + " + ".join(_kapi_list),
                            "Warning":     has_warning,
                            "RedCandle":   has_red_candle,
                        })
            except:
                pass

            # === TEKLİ KRİTER TARAMA (Altın %65 Discount + Platin bayrağı) ===
            try:
                _t_loc = (current_price - low_60) / range_diff if range_diff > 0 else 1.0
                _t_is_discount = _t_loc < 0.65
                _t_is_powerful = False
                if index_close is not None and len(index_close) > 10:
                    _t_sr = (current_price / df['Close'].iloc[-10]) - 1
                    _t_ir = (index_close.iloc[-1] / index_close.iloc[-10]) - 1
                    if _t_sr > _t_ir or rsi_now > 45:
                        _t_is_powerful = True
                else:
                    if rsi_now > 45:
                        _t_is_powerful = True
                _t_vol_sma20 = float(df['Volume'].rolling(20).mean().iloc[-1])
                _t_cur_vol   = float(df['Volume'].iloc[-1])
                _t_is_energy = (_t_cur_vol > _t_vol_sma20 * 1.05) or (rsi_now > 45)
                if _t_is_powerful and _t_is_discount and _t_is_energy:
                    _t_sma200    = float(df['Close'].rolling(200).mean().iloc[-1])
                    _t_sma50     = float(df['Close'].rolling(50).mean().iloc[-1])
                    _t_is_platin = (current_price > _t_sma200 and current_price > _t_sma50 and rsi_now < 70)
                    _t_disc_pct  = round(_t_loc * 100, 1)
                    _t_vr        = (_t_cur_vol / _t_vol_sma20) if _t_vol_sma20 > 0 else 1
                    _t_rs        = 0.0
                    if index_close is not None and len(index_close) > 10:
                        _t_rs = ((current_price / df['Close'].iloc[-10] - 1) -
                                 (index_close.iloc[-1] / index_close.iloc[-10] - 1)) * 100
                    _t_skor = round(rsi_now + (_t_vr * 15) + _t_rs + (20 if _t_is_platin else 0), 2)
                    # ── Darvas kutu check ──────────────────────────────
                    _t_dq = _t_ds = _t_dt = _t_db = _t_da = _t_dc = None
                    try:
                        _t_dbox = detect_darvas_box(df)
                        if _t_dbox is not None:
                            _t_dq = _t_dbox['quality']
                            _t_ds = _t_dbox['status']
                            _t_dt = _t_dbox['box_top']
                            _t_db = _t_dbox['box_bottom']
                            _t_da = _t_dbox['box_age']
                            _t_dc = _t_dbox['breakout_class']
                    except:
                        pass
                    tekli_altin_candidates.append({
                        "Hisse":          ticker,
                        "Fiyat":          round(current_price, 2),
                        "Teknik_Skor":    _t_skor,
                        "is_platin":      _t_is_platin,
                        "Discount_Pct":   _t_disc_pct,
                        "RSI":            round(rsi_now, 1),
                        "Warning":        has_warning,
                        "RedCandle":      has_red_candle,
                        "Darvas_Quality": _t_dq,
                        "Darvas_Status":  _t_ds,
                        "Darvas_Top":     _t_dt,
                        "Darvas_Bottom":  _t_db,
                        "Darvas_Age":     _t_da,
                        "Darvas_Class":   _t_dc,
                    })
            except:
                pass

        except:
            continue

        if i % 10 == 0 and total_tickers > 0:
            prog = int((i / total_tickers) * 100)
            my_bar.progress(40 + int(prog/2), text=f"⚡ Analiz: {ticker}...")

    my_bar.progress(100, text="✅ Tarama Tamamlandı! Listeleniyor...")
    time.sleep(0.3)
    my_bar.empty()

    return pd.DataFrame(golden_candidates), pd.DataFrame(platin_candidates), pd.DataFrame(tekli_altin_candidates)

# ==============================================================================
# BÖLÜM 34 — ANA SAYFA PANEL UI (TARAMA SONUÇLARI VE ARAYÜZ)
# Tüm tarama modüllerinin Streamlit arayüzüne bağlandığı bölüm.
# Sekme yapısı, sonuç kartları ve kullanıcı etkileşimleri burada.
# ==============================================================================

# Üst Menü Düzeni: Kategori | Varlık | Master Scan
col_cat, col_ass, col_btn = st.columns([1.0, 1.0, 1.25])

# 2. Kategori Seçimi
try: cat_index = list(ASSET_GROUPS.keys()).index(st.session_state.category)
except ValueError: cat_index = 0
with col_cat:
    st.selectbox("Kategori", list(ASSET_GROUPS.keys()), index=cat_index, key="selected_category_key", on_change=on_category_change, label_visibility="collapsed")

# 3. Varlık Listesi (Dropdown)
with col_ass:
    current_opts = ASSET_GROUPS.get(st.session_state.category, ASSET_GROUPS[INITIAL_CATEGORY]).copy()
    active_ticker = st.session_state.ticker
    if active_ticker not in current_opts:
        current_opts.insert(0, active_ticker)
        asset_idx = 0
    else:
        try: asset_idx = current_opts.index(active_ticker)
        except ValueError: asset_idx = 0
    st.selectbox("Varlık Listesi", current_opts, index=asset_idx, key="selected_asset_key", on_change=on_asset_change, label_visibility="collapsed", format_func=get_display_name)

# 4. MASTER SCAN BUTONU
with col_btn:
    if st.button("🕵️ TÜM PİYASAYI TARA (MASTER SCAN)", type="primary", use_container_width=True):

        _cat      = st.session_state.get('category', 'S&P 500')
        scan_list = ASSET_GROUPS.get(_cat, [])

        # ── AGRESİF CACHE KONTROLÜ (piyasa dışı saatlerde) ─────────────────
        _master_cached = load_scan_result("master_scan", _cat)
        if _master_cached is not None:
            # Diske kaydedilmiş sonuçları session_state'e geri yükle
            for _k, _v in _master_cached.items():
                st.session_state[_k] = _v
            _close_dt = _scan_last_close_dt()
            st.toast(f"📦 Cache yüklendi ({_close_dt.strftime('%d.%m %H:%M')} kapanışından)", icon="⚡")
            st.rerun()
        # ────────────────────────────────────────────────────────────────────

        # --- A. HAZIRLIK ---
        st.toast("Ajanlar göreve çağrılıyor...", icon="🕵️")

        # İlerleme Çubuğu ve Bilgi Mesajı
        progress_text = "Operasyon Başlıyor..."
        my_bar = st.progress(0, text=progress_text)

        try:
            # 1. ÖNCE VERİYİ ÇEK (Yahoo Koruması) - %10
            my_bar.progress(10, text="📡 Veriler İndiriliyor (Batch Download)...%10")
            # st.cache_data TTL'ini atla — master scan her zaman taze veri çekmeli
            get_batch_data_cached.clear()
            get_batch_data_cached(scan_list, period="1y")

            # 3. ICT SNIPER AJANI --- %20
            my_bar.progress(20, text="🦅 ICT Sniper Kurulumları (Liquidity+MSS+FVG) Taranıyor...%20")
            st.session_state.ict_scan_data = scan_ict_batch(scan_list)

            # 3.5 ROYAL FLUSH NADİR FIRSAT AJANI --- %25
            my_bar.progress(25, text="♠️ Royal Flush Nadir Fırsat (BOS/MSS + AI + RS + VWAP) Taranıyor...%25")
            st.session_state.nadir_firsat_scan_data = scan_nadir_firsat_batch(scan_list)

            # 4. ELİTLER (Altın Fırsat + Platin Fırsat) - %30
            my_bar.progress(30, text="💎 ELİTLER Taranıyor (Platin + Altın Fırsat)...%30")
            df_golden, df_nadir, df_tekli = get_golden_trio_batch_scan(scan_list)
            st.session_state.golden_results = (
                df_golden.sort_values(by="Teknik_Skor", ascending=False).reset_index(drop=True)
                if not df_golden.empty else pd.DataFrame()
            )
            st.session_state.platin_results = (
                df_nadir.sort_values(by="Teknik_Skor", ascending=False).reset_index(drop=True)
                if not df_nadir.empty else pd.DataFrame()
            )
            st.session_state.tekli_altin_results = (
                df_tekli.sort_values(by=["is_platin", "Teknik_Skor"], ascending=[False, False]).reset_index(drop=True)
                if not df_tekli.empty else pd.DataFrame()
            )

            # 6. SENTIMENT (AKILLI PARA) AJANI - %40
            my_bar.progress(40, text="🤫 Gizli Toplama (Smart Money) Aranıyor...%40")
            st.session_state.accum_data = scan_hidden_accumulation(scan_list)

            # 8. RADAR 1 & RADAR 2 (GENEL TEKNİK) - %65
            my_bar.progress(65, text="🧠 Radar Sinyalleri İşleniyor...%65")
            st.session_state.scan_data = analyze_market_intelligence(scan_list, _cat)
            st.session_state.radar2_data = radar2_scan(scan_list)

            # 11.7 HARMONİK CONFLUENCE AJANI - %86
            my_bar.progress(86, text="⚡ Harmonik Confluence (3'lü Teyit) Aranıyor...%86")
            st.session_state.harmonic_confluence_data = scan_harmonic_confluence_batch(scan_list)

            # 12. MİNERVİNİ SEPA AJANI - %90
            my_bar.progress(90, text="🦁 Minervini Sepa Taranıyor...%90")
            st.session_state.minervini_data = scan_minervini_batch(scan_list)

            # 13. GÜÇLÜ DÖNÜŞ AJANI - %93
            my_bar.progress(93, text="🔄 Güçlü Dönüş Adayları (RSI Diverjans + Birikim) Taranıyor...%93")
            st.session_state.guclu_donus_data = scan_guclu_donus_batch(scan_list)

            # 14. PRE-LAUNCH BOS AJANI - %96
            my_bar.progress(96, text="🚀 Pre-Launch BOS (Squeeze + Kırılım) Taranıyor...%96")
            st.session_state.prelaunch_bos_data = scan_prelaunch_bos(scan_list)

            # 15. ALTIN FIRSAT + VIP FORMASYON AJANI - %97
            my_bar.progress(97, text="💎 Altın Fırsat + VIP Formasyon (Fincan-Kulp/TOBO/Üçgen) Taranıyor...%97")
            st.session_state.golden_pattern_data = scan_golden_pattern_agent(scan_list, _cat)

            # --- TOP 20 + CONFLUENCE - %99
            my_bar.progress(99, text="🏆 TOP 20 & Confluence Hesaplanıyor...%99")
            st.session_state.top_20_summary  = compile_top_20_summary()
            st.session_state.confluence_hits = compile_confluence_hits()

            # ── TARAMA SONUÇLARINI DİSKE KAYDET (agresif cache) ────────────
            import pickle, logging as _logging
            _master_snapshot = {
                "ict_scan_data":            st.session_state.ict_scan_data,
                "nadir_firsat_scan_data":   st.session_state.nadir_firsat_scan_data,
                "golden_results":           st.session_state.golden_results,
                "platin_results":           st.session_state.platin_results,
                "tekli_altin_results":      st.session_state.tekli_altin_results,
                "accum_data":               st.session_state.accum_data,
                "scan_data":                st.session_state.scan_data,
                "radar2_data":              st.session_state.radar2_data,
                "harmonic_confluence_data": st.session_state.harmonic_confluence_data,
                "minervini_data":           st.session_state.minervini_data,
                "guclu_donus_data":         st.session_state.guclu_donus_data,
                "prelaunch_bos_data":       st.session_state.prelaunch_bos_data,
                "top_20_summary":           st.session_state.top_20_summary,
                "confluence_hits":          st.session_state.confluence_hits,
                "golden_pattern_data":      st.session_state.golden_pattern_data,
            }
            # Önce tüm snapshot'ı bir arada dene
            _save_ok = False
            _skipped_keys = []
            try:
                pickle.dumps(_master_snapshot)   # bellek testi
                _res = save_scan_result("master_scan", _master_snapshot, _cat)
                _save_ok = (_res is True)
            except Exception as _pe:
                # Toplu pickle başarısız — her key'i ayrı ayrı dene, bozuk olanı atla
                _logging.warning(f"[scan_cache] Toplu pickle hatası: {_pe} — key bazlı kayda geçiliyor")
                _clean_snapshot = {}
                for _sk, _sv in _master_snapshot.items():
                    try:
                        pickle.dumps(_sv)
                        _clean_snapshot[_sk] = _sv
                    except Exception as _ke:
                        _skipped_keys.append(_sk)
                        _logging.warning(f"[scan_cache] pickle edilemeyen key atlandı: {_sk} — {_ke}")
                if _clean_snapshot:
                    _res = save_scan_result("master_scan", _clean_snapshot, _cat)
                    _save_ok = (_res is True)
            # ────────────────────────────────────────────────────────────────

            # --- BİTİŞ ---
            my_bar.progress(100, text="✅ TARAMA TAMAMLANDI! Sonuçlar Yükleniyor...%100")

            if _save_ok:
                st.toast("💾 Tarama sonuçları diske kaydedildi.", icon="✅")
            else:
                _skip_str = ", ".join(_skipped_keys[:5]) if _skipped_keys else "bilinmiyor"
                st.warning(f"⚠️ Tarama önbelleği kaydedilemedi. Atlanan keyler: {_skip_str}")

            st.session_state.generate_prompt = False

        except Exception as e:
            # Streamlit'in kendi exception'larını (RerunException, StopException) yeniden fırlat
            _etype = type(e).__name__
            if any(x in _etype for x in ("Rerun", "Stop", "Script")):
                raise
            st.error(f"Tarama sırasında bir hata oluştu: {str(e)}")
            st.stop()

        # st.rerun() try/except DIŞINDA — Streamlit exception'ı artık yakalanmıyor
        st.rerun()

st.markdown("<hr style='margin-top:0.5rem; margin-bottom:0.5rem;'>", unsafe_allow_html=True)

if st.session_state.generate_prompt:
    t = st.session_state.ticker
    clean_ticker = t.replace(".IS", "").replace("-USD", "").replace("=F", "")
    # --- 1. GEREKLİ VERİLERİ TOPLA ---
    info = fetch_stock_info(t)
    df_hist = get_safe_historical_data(t) # Ana veri
    
    # EKSİK OLAN TANIMLAMALAR EKLENDİ (bench_series ve idx_data)
    cat_for_bench = st.session_state.category
    bench_ticker = "XU100.IS" if "BIST" in cat_for_bench else "^GSPC"
    bench_series = get_benchmark_data(cat_for_bench)
    idx_data = get_safe_historical_data(bench_ticker)['Close'] if bench_ticker else None
    # Teknik verileri çeken fonksiyonunuzu çağırıyoruz
    tech_vals = get_tech_card_data(t) 
    # Eğer veri geldiyse değişkenlere atıyoruz, gelmediyse 0 diyoruz
    if tech_vals:
        sma50_val  = tech_vals.get('sma50', 0)
        sma100_val = tech_vals.get('sma100', 0)
        sma200_val = tech_vals.get('sma200', 0)
        ema144_val = tech_vals.get('ema144', 0)
    else:
        sma50_val = 0
        sma100_val = 0
        sma200_val = 0
        ema144_val = 0    
    # Diğer Hesaplamalar
    ict_data = calculate_ict_deep_analysis(t) or {}
    sent_data = calculate_sentiment_score(t) or {}
    tech_data = get_tech_card_data(t) or {}
    pa_data = calculate_price_action_dna(t) or {}
    levels_data = get_advanced_levels_data(t) or {}
    synth_data = calculate_synthetic_sentiment(t)

    # --- TEKNİK YOL HARİTASI VERİSİNİ AI İÇİN HAZIRLA (Composite + MTF + Trade Plan + Alt kartlar) ---
    roadmap_data_ai = calculate_8_point_roadmap(t)
    roadmap_ai_txt = "Veri Yok"

    if roadmap_data_ai:
        import re
        def clean_html(raw_html):
            cleanr = re.compile('<.*?>')
            return re.sub(cleanr, ' ', str(raw_html)).strip()

        # ───── MASTER SENTEZ: Composite Skor + 5 alt faktör ─────
        _comp_score    = roadmap_data_ai.get('composite_score', 50)
        _comp_decision = roadmap_data_ai.get('comp_decision', 'BEKLEMEDE')
        _factor_scores = roadmap_data_ai.get('factor_scores', {})
        _f_trend = _factor_scores.get('trend', 50)
        _f_mom   = _factor_scores.get('momentum', 50)
        _f_vol   = _factor_scores.get('volume', 50)
        _f_yapi  = _factor_scores.get('yapi', 50)
        _f_sen   = _factor_scores.get('senaryo', 50)

        # ───── MULTI-TIMEFRAME ALIGNMENT MATRİSİ ─────
        _mtf_data = calculate_multi_timeframe_alignment(t)
        if _mtf_data and _mtf_data.get('matrix'):
            def _mtf_arr(s): return "↑" if s > 0 else ("↓" if s < 0 else "≈")
            _tfs = _mtf_data['timeframes']
            # Tablo başlığı
            _hdr_line = "                  " + "  ".join(f"{tf:<10}" for tf in _tfs)
            _rows = []
            for ind in ["trend", "momentum", "hacim"]:
                _label = {"trend": "Trend         ", "momentum": "Momentum (RSI)", "hacim": "Hacim         "}[ind]
                _cells = "  ".join(f"{_mtf_arr(_mtf_data['matrix'][tf].get(ind, 0)):<10}" for tf in _tfs)
                _rows.append(f"{_label}    {_cells}")
            _mtf_table = _hdr_line + "\n        " + "\n        ".join(_rows)
            _mtf_neut = _mtf_data['total'] - _mtf_data['bull_cnt'] - _mtf_data['bear_cnt']
            _mtf_summary = (f"Dominant Yön: {_mtf_data['dominant']} (uyum oranı %{_mtf_data['overall_pct']}) | "
                            f"{_mtf_data['bull_cnt']} hücre yukarı, {_mtf_data['bear_cnt']} hücre aşağı, {_mtf_neut} nötr")
        else:
            _mtf_table = "Veri Yok"
            _mtf_summary = "Çoklu vade verisi alınamadı (4H/Haftalık veriler gelmedi olabilir)"

        # ───── TRADE PLAN (Long Setup) ─────
        _tp_curr = roadmap_data_ai.get('tp_curr_price', 0)
        _tp_stop = roadmap_data_ai.get('tp_stop_5g', 0)
        _tp_tp1  = roadmap_data_ai.get('tp_target_atr', 0)
        _tp_tp2  = roadmap_data_ai.get('tp_target_20g', 0)

        def _fmt_p_ai(v):
            try:
                v = float(v)
                return f"{int(v):,}" if v >= 1000 else f"{v:.2f}"
            except: return "—"

        if _comp_decision in ("AL", "DİKKAT/İZLE") and _tp_curr > 0 and _tp_stop > 0 and _tp_tp1 > _tp_curr:
            _risk_pct = (_tp_curr - _tp_stop) / _tp_curr * 100
            _rew1_pct = (_tp_tp1 - _tp_curr) / _tp_curr * 100
            _rew2_pct = (_tp_tp2 - _tp_curr) / _tp_curr * 100
            _rr1_ai   = (_tp_tp1 - _tp_curr) / max(_tp_curr - _tp_stop, 0.001)
            _rr2_ai   = (_tp_tp2 - _tp_curr) / max(_tp_curr - _tp_stop, 0.001)
            _rr_max   = max(_rr1_ai, _rr2_ai)
            _rr_qual  = ("Mükemmel" if _rr_max >= 2.5
                         else ("Sağlam" if _rr_max >= 1.5
                               else ("Sınırda" if _rr_max >= 1.0 else "Zayıf")))
            _trade_plan_block = (
                f"- Entry: {_fmt_p_ai(_tp_curr)} (mevcut fiyat)\n"
                f"        - Stop (5G dip): {_fmt_p_ai(_tp_stop)} → -%{_risk_pct:.1f} risk\n"
                f"        - TP1 (ATR×2): {_fmt_p_ai(_tp_tp1)} → +%{_rew1_pct:.1f} ödül = {_rr1_ai:.1f}R\n"
                f"        - TP2 (20G zirve): {_fmt_p_ai(_tp_tp2)} → +%{_rew2_pct:.1f} ödül = {_rr2_ai:.1f}R\n"
                f"        - R/R Kalitesi: {_rr_qual} (en iyi {_rr_max:.1f}R)"
            )
        else:
            _trade_plan_block = (
                f"- Long kurulum uygun değil (Composite skor: {_comp_score}/100, durum: {_comp_decision})\n"
                f"        - Mevcut fiyat: {_fmt_p_ai(_tp_curr)}\n"
                f"        - Olası destek (5G dip): {_fmt_p_ai(_tp_stop)}\n"
                f"        - Olası direnç (20G zirve): {_fmt_p_ai(_tp_tp2)}\n"
                f"        - Bu seviyelerden teyitli sinyal beklenmeli"
            )

        # ───── PROMPT METNİ — 3 yeni master blok + M1-M5+M8 ham veriler ─────
        roadmap_ai_txt = f"""
        *** COMPOSITE TEKNİK SKOR (5 alt faktörün ağırlıklı sentezi) ***
        Master Skor: {_comp_score}/100 → {_comp_decision}
        Alt Faktörler (her biri 0-100):
          • Trend (SMA50/SMA200/EMA hizalaması, ağırlık 30%): {_f_trend}/100
          • Momentum (RSI + Yön Beklentisi, ağırlık 25%): {_f_mom}/100
          • Hacim (VSA + akış + hacim oranı, ağırlık 20%): {_f_vol}/100
          • Yapı (formasyon + absorption + mum yapısı, ağırlık 15%): {_f_yapi}/100
          • Senaryo (makro+mikro+overheat sentezi, ağırlık 10%): {_f_sen}/100

        *** VADE UYUMU MATRİSİ (Multi-Timeframe Alignment) ***
        {_mtf_summary}

        {_mtf_table}

        *** TRADE PLAN ÖNERİSİ (Long Setup) ***
        {_trade_plan_block}

        *** TEKNİK YOL HARİTASI — ALT KARTLAR (master skorun ham bileşenleri) ***
        1) Fiyat Davranışı ve Yapı: {clean_html(roadmap_data_ai['M1'])}
        2) Formasyon Tespiti: {clean_html(roadmap_data_ai['M2'])}
        3) Efor vs Sonuç (VSA): {clean_html(roadmap_data_ai['M3'])}
        4) Trend Skoru ve Enerji: {clean_html(roadmap_data_ai['M4'])}
        5) Hacim ve Akıllı Para İzi: {clean_html(roadmap_data_ai['M5'])}
        6) Okuma Özeti: {clean_html(roadmap_data_ai['M8'])}
        """

    # Prompt içinde eksik olan slope hesaplaması
    if df_hist is not None and len(df_hist) > 2:
        sma50_hist = df_hist['Close'].rolling(50).mean()
        sma50_slope = 1 if sma50_hist.iloc[-1] > sma50_hist.iloc[-2] else -1
    else:
        sma50_slope = 0

    # --- Arada verileri çekebiliriz Para Akışı İvmesi ---
    if synth_data is not None and not synth_data.empty:
        son_satir = synth_data.iloc[-1]
        guncel_ivme = float(son_satir['MF_Smooth'])
        guncel_stp = float(son_satir['STP'])
        guncel_fiyat = float(son_satir['Price'])
        denge_sapmasi = ((guncel_fiyat / guncel_stp) - 1) * 100
        ivme_yonu = "YÜKSELİŞ (Pozitif)" if guncel_ivme > 0 else "DÜŞÜŞ (Negatif)"
    else:
        # Veri gelmezse AI hata almasın diye varsayılan değerler
        guncel_ivme = 0; guncel_stp = 0; guncel_fiyat = 0; denge_sapmasi = 0; ivme_yonu = "Bilinmiyor"
    mini_data = calculate_minervini_sepa(t) or {} 
    master_score, pros, cons = calculate_master_score(t)
    # --- YENİ: AI İÇİN S&D VE LİKİDİTE VERİLERİ ---
    try: 
        sd_data = detect_supply_demand_zones(df_hist)
        sd_txt_ai = f"{sd_data['Type']} ({sd_data['Bottom']:.2f} - {sd_data['Top']:.2f}) Durum: {sd_data['Status']} olabilir." if sd_data else "Taze bölge görünmüyor."
    except: 
        sd_txt_ai = "Veri Yok"
        
    havuz_ai = ict_data.get('eqh_eql_txt', 'Yok')
    sweep_ai = ict_data.get('sweep_txt', 'Yok')
    # --- ALTIN FIRSAT DURUMU HESAPLAMA (Garantili Versiyon) ---
    rs_text_prompt = sent_data.get('rs', '').lower()
    # 1. Güç Kontrolü
    c_pwr = ("artıda" in rs_text_prompt or "lider" in rs_text_prompt or "pozitif" in rs_text_prompt or 
             sent_data.get('total', 0) >= 50 or sent_data.get('raw_rsi', 0) > 50)
    # 2. Konum Kontrolü
    c_loc = ("DISCOUNT" in ict_data.get('zone', '') or "MSS" in ict_data.get('structure', '') or 
             "BOS" in ict_data.get('structure', ''))
    # 3. Enerji Kontrolü
    c_nrg = ("Güçlü" in ict_data.get('displacement', '') or "Hacim" in sent_data.get('vol', '') or 
             sent_data.get('raw_rsi', 0) > 55)
    # Final Onay Durumu
    is_golden = "✅ EVET (3/3 Kriter Karşılandı)" if (c_pwr and c_loc and c_nrg) else "HAYIR"

    # --- ROYAL FLUSH NADİR FIRSAT DURUMU HESAPLAMA (5/5 Kesişim) ---
    # 1. Yapı: BOS veya MSS Bullish olmalı
    c_struct = "BOS (Yükseliş" in ict_data.get('structure', '') or "MSS" in ict_data.get('structure', '')
    # 2. Güç: RS Alpha > %1.5 (endeksi en az %1.5 geçiyor)
    c_rs = pa_data.get('rs', {}).get('alpha', 0) > 1.5
    # 3. Maliyet: VWAP sapması %10'dan az olmalı (Güvenli Zemin)
    c_vwap = pa_data.get('vwap', {}).get('diff', 0) < 10
    # 4. Hacim canlanması
    try:
        _vol = df_hist['Volume']
        _o20 = _vol.iloc[-22:-2].mean()
        _s3  = _vol.iloc[-3:].mean()
        _s2  = _vol.iloc[-2:].mean()
        _o5  = _vol.iloc[-7:-2].mean()
        c_vol = (_s3 > _o20 * 1.3) or (_s2 > _o5 * 1.3)
    except:
        c_vol = False
    # 5. RSI < 65 (aşırı alım bölgesinde değil)
    try:
        _dd = df_hist['Close'].diff()
        _gg = _dd.where(_dd > 0, 0).rolling(14).mean()
        _ll = (-_dd.where(_dd < 0, 0)).rolling(14).mean()
        c_rsi = float((100 - (100 / (1 + _gg / _ll))).iloc[-1]) < 65
    except:
        c_rsi = False
    # Final Royal Flush Nadir Fırsat Onayı
    is_nadir = "♠️ EVET (5/5 KRALİYET SET-UP - EN YÜKSEK OLASILIK)" if (c_struct and c_rs and c_vwap and c_vol and c_rsi) else "HAYIR"

    # [YENİ EKLENTİ] MOMENTUM DEDEKTİFİ (Yorgun Boğa Analizi)
    momentum_analiz_txt = "Veri Yok"
    if synth_data is not None and not synth_data.empty:
        # Son satırdaki MF_Smooth (Bar Rengi) verisini al
        last_mf = float(synth_data.iloc[-1]['MF_Smooth'])
        # Günlük fiyat değişimini al
        p_change = info.get('change_pct', 0)

        if last_mf > 0:
            momentum_analiz_txt = "✅ GÜÇLÜ (Uyumlu): Momentum barı MAVİ. Para akışı fiyatı destekliyor."
        else:
            # Bar Kırmızı (Negatif) ise şimdi Fiyata bakıyoruz
            if p_change >= 0:
                # SENARYO: Fiyat Yükseliyor AMA Bar Kırmızı -> SENİN CÜMLEN BURADA
                momentum_analiz_txt = "⚠️ UYARI (YORGUN BOĞA mı yoksa DEVAM mı): Fiyat hala tepede görünüyor olabilir ama aldanma. Son 6 günün ortalama hızının altına düştük: Bu yükselişin yakıtını sorgulamak gerekebilir, yakıt bitmiş olabilir, sadece rüzgarla gidiyor olabiliriz. 1) Eğer hacim düşükse bu bir 'Bayrak/Flama' (Güç Toplama) olabilir. 2) Eğer hacim yüksekse bu bir 'Mal Çıkışı' (Yorgun Boğa) olabilir. Stopları yaklaştır ve kırılımı bekle."
            else:
                # SENARYO: Fiyat Düşüyor VE Bar Kırmızı -> NORMAL
                momentum_analiz_txt = "🔻 ZAYIF (Uyumlu): Düşüş trendi momentumla teyit ediliyor."
    # -----------------------------------------------------------    
    # --- 2. AJAN HESAPLAMALARI ---
    stp_res = process_single_stock_stp(t, df_hist)
    acc_res = process_single_accumulation(t, df_hist, bench_series)
    bo_res = process_single_breakout(t, df_hist)
    pat_df = scan_chart_patterns([t])
    r2_res = process_single_radar2(t, df_hist, idx_data, 0, 999999, 0)

    # --- 3. SICAK İSTİHBARAT ÖZETİ (AI SİNYAL KUTUSU - DERİNLEŞTİRİLMİŞ) ---
    scan_box_txt = []

    # A. ELİT KURULUMLAR (Sistemin En Tepesi)
    if is_nadir != "HAYIR": 
        scan_box_txt.append("👑 ELİT KURULUM: ROYAL FLUSH NADİR FIRSAT (4/4 Onay. Algoritmik kusursuzluk! Kurumsal fonların en sevdiği, başarı ihtimali en yüksek asimetrik risk/ödül noktası olabilir.)")
    elif is_golden != "HAYIR": 
        scan_box_txt.append("🏆 ALTIN FIRSAT: Golden Trio Onaylandı (Fiyat ucuz, trend güçlü, hacim destekliyor. Büyük bir hareketin arifesinde olabilir.)")

    # B. ICT & MARKET YAPISI VE MAKRO YÖN (Kurumsal Ayak İzleri)
    if ict_data and ict_data.get('status') != 'Error':
        struct_txt = ict_data.get('structure', '')
        
        # Yapay Zeka İçin Bias (Yön) ve Zone (Bölge) Bilgisi
        ai_bias = ict_data.get('bias', 'Nötr')
        ai_zone = ict_data.get('zone', 'Nötr')
        scan_box_txt.append(f"🧭 MAKRO YÖN VE KONUM: Ana Trend Yönü '{ai_bias}' | Fiyatın Bulunduğu Bölge: '{ai_zone}'. (Yorum yaparken fiyatın ucuzlukta mı pahalılıkta mı olduğunu ve ana trende ters mi düz mü hareket ettiğini mutlaka hesaba kat.)")

        if "MSS" in struct_txt or "BOS" in struct_txt:
            scan_box_txt.append(f"🦅 YAPI KIRILIMI (ICT): {struct_txt} (KRİTİK: Akıllı para piyasa yapısını kırmış görünüyor. Önceki trend bozuldu, yeni bir likidite arayışı başlıyor.)")

    # C. SMART MONEY (Sessiz Toplama / Hacim Patlaması)
    if acc_res:
        if acc_res.get('Pocket_Pivot', False):
            scan_box_txt.append("⚡ AKILLI PARA: Pocket Pivot (Hacimli Kurumsal Alım. Küçük yatırımcı uyurken tahtaya para girişi yapılmış gibi görünüyor.)")
        else:
            scan_box_txt.append("🤫 AKILLI PARA: Sessiz Toplama (Fiyat yatay veya baskılı görünse de arka planda sinsi bir fon alımı var. Kırılım hazırlığı..)")

    # D. STP MOMENTUM (Kısa Vadeli İvme ve Duygu Durumu)
    if stp_res:
        if stp_res['type'] == 'cross_up': 
            scan_box_txt.append("🟢 STP MOMENTUM: Denge Yukarı Kırıldı (Kısa vadeli alıcılar iştahlandı, taze bir yükseliş ivmesi tetiklendi.)")
        elif stp_res['type'] == 'cross_down': 
            scan_box_txt.append("🔴 STP MOMENTUM: Denge Aşağı Kırıldı (Kısa vadeli likidite çıkışı var, satıcı baskısı an itibariyle taze ve tehlikeli.)")
        elif stp_res['type'] == 'trend_up': 
            scan_box_txt.append(f"📈 STP MOMENTUM: Pozitif Trend ({stp_res['data'].get('Gun','?')} Gündür trend alıcıların kontrolünde görünüyor.)")
        elif stp_res['type'] == 'trend_down': 
            scan_box_txt.append(f"📉 STP MOMENTUM: Negatif Trend ({stp_res['data'].get('Gun','?')} Gündür ayılar tahtayı baskılıyor)")

    # D2. ALTIN FIRSAT + VIP FORMASYON (batch scan sonucu — bu hisse tarama listesindeyse)
    try:
        _gp_ai = st.session_state.get('golden_pattern_data')
        if isinstance(_gp_ai, dict):
            _gp_forms = _gp_ai.get('formations', pd.DataFrame())
            if not _gp_forms.empty and 'Sembol' in _gp_forms.columns:
                _gp_row = _gp_forms[_gp_forms['Sembol'] == t]
                if not _gp_row.empty:
                    _gp_detay = _gp_row.iloc[0].get('Detay', '')
                    _gp_puan  = _gp_row.iloc[0].get('Puan', 0)
                    _gp_nadir = _gp_row.iloc[0].get('is_nadir', False)
                    _gp_pfx   = "♠️ PLATİN" if _gp_nadir else "💎 VIP"
                    scan_box_txt.insert(1,
                        f"{_gp_pfx} ALTIN FIRSAT + FORMASYON: Skor {_gp_puan}/100 — {_gp_detay} "
                        f"(Güç + Ucuzluk + Enerji + Geometrik yapı — dört kriter birlikte çakıştı. "
                        f"Batch tarama sonucu: bu hisse en seçkin listede.)"
                    )
    except:
        pass

    # D3. DARVAS BOX (Swing-point bazlı kutu + kalite skoru)
    try:
        if df_hist is not None and len(df_hist) >= 60:
            _dbox_ai = detect_darvas_box(df_hist)
            if _dbox_ai and _dbox_ai['quality'] >= 75:
                _d_st  = _dbox_ai['status']
                _d_q   = _dbox_ai['quality']
                _d_age = _dbox_ai['box_age']
                _d_top = _dbox_ai['box_top']
                _d_bot = _dbox_ai['box_bottom']
                _d_cls = _dbox_ai.get('breakout_class')
                _d_vr  = _dbox_ai.get('vol_ratio', 1.0)
                if _d_st == 'breakout' and _d_cls == 'A':
                    scan_box_txt.append(
                        f"⭐📦 DARVAS A-SINYAL: {_d_age} günlük birikim kutusu 3/3 kapıyla kırıldı! "
                        f"Tavan:{_d_top} → Taban:{_d_bot} · Hacim oranı:{_d_vr:.1f}x · Kalite:{_d_q}/100 "
                        f"(Swing-point bazlı konsolidasyon + hacim patlaması — VCP imzası. "
                        f"Bu kombinasyon tarihsel olarak büyük hareketlerin öncesinde görülür.)"
                    )
                elif _d_st == 'breakout':
                    scan_box_txt.append(
                        f"📦 DARVAS KIRILIM (Kısmi Onay): {_d_age} günlük kutu kırıldı. "
                        f"Tavan:{_d_top} · Kalite:{_d_q}/100 · Hacim:{_d_vr:.1f}x "
                        f"(Fiyat kırılımı var, hacim veya RSI teyidi eksik — izlemede tut.)"
                    )
                else:
                    scan_box_txt.append(
                        f"🟦 DARVAS KUTU OLUŞUYOR: {_d_age} günlük konsolidasyon. "
                        f"Tavan:{_d_top} → Taban:{_d_bot} · Hacim kontraksiyon:{_d_vr:.1f}x · Kalite:{_d_q}/100 "
                        f"(Enerji biriyor, yay gerildi — {_d_top} üstü kapanış kırılım tetikler.)"
                    )
    except:
        pass

    # E. FORMASYON (Geometrik Yapılar)
    if not pat_df.empty:
        scan_box_txt.append(f"📐 GEOMETRİK YAPI: {pat_df.iloc[0]['Formasyon']} (Teknik analistlerin ve algoritmaların ekranına düşecek bir formasyon.)")

    # E2. HARMONİK FORMASYON (Fibonacci XABCD)
    try:
        _harm_res = calculate_harmonic_patterns(t, df_hist)
        if _harm_res:
            _h_dir_tr = "YUKARI DÖNÜŞ" if _harm_res['direction'] == 'Bullish' else "AŞAĞI DÖNÜŞ"
            scan_box_txt.append(
                f"🔮 HARMONİK FORMASYON: {_harm_res['pattern']} ({_h_dir_tr}) | "
                f"PRZ: {_harm_res['prz']:.2f} | AB/XA:{_harm_res['AB_XA']} XD/XA:{_harm_res['XD_XA']} "
                f"(Fibonacci oranlarıyla teyit edilmiş matematiksel dönüş bölgesi. "
                f"Fiyat PRZ'ye yaklaşırken yapı ve hacim teyidini bekle.)"
            )
    except Exception:
        pass

    # G. BREAKOUT (Kırılım Ajanı)
    if bo_res:
        if "TETİKLENDİ" in bo_res['Zirveye Yakınlık']:
            scan_box_txt.append("🔨 TEKNİK YAPI: breakout geldi (yön teyidi için hacim sürekliliği takip edilmeli)")
        elif "Sıkışma" in bo_res['Zirveye Yakınlık']:
            scan_box_txt.append("💣 VOLATİLİTE DARALMASI: Bir Sıkışma (Squeeze) var. (Enerji birikti, yay gerildi. Her an sert bir yön patlaması gelebilir.)")

    # G2. GÜÇLÜ DÖNÜŞ+ SİNYALİ (Kurumsal dip alımı + stop avı teyidi)
    try:
        _gd_data = st.session_state.get('guclu_donus_data')
        if _gd_data is not None and not _gd_data.empty:
            _gd_match = _gd_data[_gd_data['Sembol'] == t]
            if not _gd_match.empty:
                _gd_row = _gd_match.iloc[0]
                _gd_sweep = _gd_row.get('Sweep_Ay', False)
                _gd_h10   = _gd_row.get('Hacim_10g', '-')
                _gd_zs    = _gd_row.get('Z-Score', 0)
                _gd_sweep_txt = " Geçen ay stop avı tespit edildi (C2<C1) —" if _gd_sweep else ""
                scan_box_txt.append(
                    f"🔄 GÜÇLÜ DÖNÜŞ+ SİNYALİ:{_gd_sweep_txt} "
                    f"Z-Score {_gd_zs:.1f} (tarihsel dip bölgesi), son 10 günde {_gd_h10} kez yüksek hacim, "
                    f"OBV birikim ve RSI bullish diverjans aynı anda teyit ediyor. "
                    f"Bu hafta geçen haftadan yüksek kapandı — kurumsal dip alımının ilk somut adımı olabilir."
                )
    except: pass

    # G3. PRE-LAUNCH BOS SİNYALİ (Squeeze + 45g direnç kırılımı)
    try:
        _pb_data = st.session_state.get('prelaunch_bos_data')
        if _pb_data is not None and not _pb_data.empty:
            _pb_match = _pb_data[_pb_data['Sembol'] == t]
            if not _pb_match.empty:
                _pb_row   = _pb_match.iloc[0]
                _pb_day   = int(_pb_row.get('BOS_Day', 0))
                _pb_sq    = int(_pb_row.get('Squeeze_Gun', 0))
                _pb_vol   = float(_pb_row.get('Hacim_Kat', 0))
                _pb_rsi   = float(_pb_row.get('RSI', 0))
                _pb_dist  = float(_pb_row.get('BOS_Dist', 0))
                _pb_skor  = int(_pb_row.get('Skor', 0))
                _pb_day_lbl = ["bugün", "dün", "2 gün önce", "3 gün önce"][_pb_day]
                scan_box_txt.append(
                    f"🚀 PRE-LAUNCH BOS SİNYALİ: 45 günlük direnç {_pb_day_lbl} kırıldı. "
                    f"Kırılım öncesinde {_pb_sq} gün Squeeze (sıkışma) yaşandı — yay gerilmişti. "
                    f"BOS günü hacim {_pb_vol:.1f}x normale çıktı. RSI {_pb_rsi:.0f} — aşırı alım yok. "
                    f"Mevcut fiyat BOS seviyesinin %{_pb_dist:.1f} üzerinde. "
                    f"Tarama skoru: {_pb_skor}/100. "
                    f"Bu hisse büyük hareketin hemen başında yakalanmış olabilir."
                )
    except: pass

    # H. İSTATİSTİKSEL ANOMALİLER (Z-Score Aşırılıkları)
    try:
        z_val = calculate_z_score_live(df_hist)
        if z_val >= 2.0: 
            scan_box_txt.append(f"🚨 İSTATİSTİKSEL ANOMALİ: Z-Score +{z_val:.1f} (DİKKAT: Fiyat ortalamalardan matematiksel olarak saptı. 'Mean Reversion' yani aşağı yönlü düzeltme riski masada!)")
        elif z_val <= -2.0: 
            scan_box_txt.append(f"🚨 İSTATİSTİKSEL ANOMALİ: Z-Score {z_val:.1f} (DİKKAT: Aşırı satım bölgesi. Fiyat o kadar ucuzladı ki, istatistiksel bir yukarı tepki sıçraması ihtimali artıyor.)")
    except: pass

    # I0. AKILLI PARA BİRİKİMİ (Para Akış İvmesi — Yatay Fiyat + Pozitif Force Index)
    try:
        _fi_close = df_hist['Close']
        _fi_vol   = df_hist['Volume']
        _fi_delta = _fi_close.diff()
        _fi_force = (_fi_delta * _fi_vol).ewm(span=5, adjust=False).mean()
        _fi_last10 = _fi_force.tail(10)
        _fi_pos_days = (_fi_last10 > 0).sum()
        _fi_price_now = float(_fi_close.iloc[-1])
        _fi_ma10 = float(_fi_close.tail(10).mean())
        _fi_atr  = float((df_hist['High'] - df_hist['Low']).rolling(14).mean().iloc[-1])
        _fi_band = float(df_hist['High'].tail(10).max() - df_hist['Low'].tail(10).min())
        _fi_price_dist = abs(_fi_price_now - _fi_ma10) / _fi_ma10 * 100
        _fi_band_tight = (_fi_band / _fi_price_now * 100) < 6  # Bant < %6 dar
        if _fi_pos_days >= 7 and _fi_price_dist <= 3.0 and _fi_band_tight:
            scan_box_txt.append(f"💧 AKILLI PARA BİRİKİMİ: Son 10 günün {int(_fi_pos_days)}'inde para akışı pozitif, fiyat ise yatay ({_fi_price_dist:.1f}% değişim). Bu, kurumsal alımın sessizce sürdüğüne ve bir kırılım hazırlığına işaret edebilir.")
    except: pass

    # I. GİZLİ YALANLAR: RSI Uyumsuzluk ve Smart Volume Anomalileri
    if pa_data:
        # Uyumsuzluk
        div_type = pa_data.get('div', {}).get('type', 'neutral')
        if div_type == 'bearish': 
            scan_box_txt.append("⚠️ GİZLİ YALAN (Negatif Uyumsuzluk): Fiyat yeni zirve yapıyor ama RSI (Momentum) düşüyor. (Yorgun boğa! Fiyat çıkarken mal dağıtılıyor olabilir.)")
        elif div_type == 'bullish': 
            scan_box_txt.append("💎 GİZLİ GÜÇ (Pozitif Uyumsuzluk): Fiyat yeni dip yapıyor ama RSI yükseliyor. (Satıcılar yorulmuş görünüyor, büyükler dipten topluyor olabilir.)")
        
        # Hacim Anomalisi (Stopping / Climax)
        sv_data = pa_data.get('smart_volume', {})
        if sv_data.get('stopping') != 'Yok': 
            scan_box_txt.append("🐋 BALİNA İZİ (Stopping Volume): Düşüş nihayet yüksek bir hacimle karşılanmış görünüyor. (Kurumsal fren mekanizması devrede, düşüş durduruluyor olabilir.)")
        if sv_data.get('climax') != 'Yok': 
            scan_box_txt.append("🌋 BALİNA İZİ (Climax Volume): Rallinin zirvesinde anormal bir hacim var. (Müzik durmak üzere ve akıllı para malı küçük yatırımcıya boşaltıyor olabilir!)")

    # E3. HARMONİK CONFLUENCE (3 Metodoloji Çakışması)
    try:
        _hconf = calculate_harmonic_confluence(t, df_hist)
        if _hconf:
            _hc_dir_tr = "YUKARI" if _hconf['direction'] == 'Bullish' else "AŞAĞI"
            _hc_badges = _hconf.get('badge_str', '')
            _hc_aciklama = _hconf.get('Aciklama', 'PRZ teyitli')
            scan_box_txt.insert(0,
                f"⚡ HARMONİK CONFLUENCE: "
                f"{_hconf['pattern']} {_hc_dir_tr} | PRZ: {_hconf['prz']:.2f} | {_hc_aciklama}"
                + (f" | Kriterler: {_hc_badges}" if _hc_badges else "")
                + f" (Fibonacci PRZ teyitli harmonik kurulum. ICT bölge ve RSI diverjans bonus metodoloji olarak eklenirse kalite daha da yükselir.)"
            )
    except Exception:
        pass

    # Eğer hiçbir sıcak sinyal yoksa:
    if not scan_box_txt:
        scan_box_txt.append("⚖️ PİYASA DURUMU NÖTR: An itibariyle sıcak bir kırılım, anomali veya tuzak tespit edilmedi. Standart fiyat hareketi (Konsolidasyon) devam ediyor.")

    scan_summary_str = "\n".join([f"- {s}" for s in scan_box_txt])

    # --- 4. DEĞİŞKEN TANIMLAMA VE GÜN SAYISI (STREAK) HESAPLAMA ---
    curr_price = info.get('price', 0) if info else 0

    if df_hist is not None and not df_hist.empty:
        # Ortalamaları df_hist üzerinde hesaplayalım ki tüm geçmiş seriyi görebilelim
        df_hist['EMA8'] = df_hist['Close'].ewm(span=8, adjust=False).mean()
        df_hist['EMA13'] = df_hist['Close'].ewm(span=13, adjust=False).mean()
        df_hist['SMA50'] = df_hist['Close'].rolling(50).mean()
        df_hist['SMA200'] = df_hist['Close'].rolling(200).mean()
        
        # Gün sayısı hesaplama fonksiyonu (Sistemi yormayan vektörel yöntem)
        def calc_streak(price_s, ma_s):
            is_above = price_s > ma_s
            streak_groups = (is_above != is_above.shift()).cumsum()
            if is_above.iloc[-1]:
                days = int(is_above.groupby(streak_groups).sum().iloc[-1])
                return f"Üzerinde ({days} Gündür)"
            else:
                days = int((~is_above).groupby(streak_groups).sum().iloc[-1])
                return f"Altında ({days} Gündür)"
        
        # Son Değerler
        ema8_val = df_hist['EMA8'].iloc[-1]
        ema13_val = df_hist['EMA13'].iloc[-1]
        sma50_val = df_hist['SMA50'].iloc[-1]
        sma200_val = df_hist['SMA200'].iloc[-1]
        
        # Streak Durumları (Kaç gündür nerede?)
        ema8_status = calc_streak(df_hist['Close'], df_hist['EMA8'])
        ema13_status = calc_streak(df_hist['Close'], df_hist['EMA13'])
        sma50_str = calc_streak(df_hist['Close'], df_hist['SMA50'])
        sma200_str = calc_streak(df_hist['Close'], df_hist['SMA200'])
        
        # Fark Yüzdeleri
        diff_ema8 = ((curr_price / ema8_val) - 1) * 100 if ema8_val > 0 else 0
        diff_ema13 = ((curr_price / ema13_val) - 1) * 100 if ema13_val > 0 else 0
        
        ema_txt = f"EMA8: {ema8_val:.2f} [{ema8_status}, Fiyat Farkı: %{diff_ema8:.1f}] | EMA13: {ema13_val:.2f} [{ema13_status}, Fiyat Farkı: %{diff_ema13:.1f}]"
    else:
        sma50_str = "Veri Yok"
        sma200_str = "Veri Yok"
        ema_txt = "Veri Yok"
        sma50_val = tech_data.get('sma50', 0) if tech_data else 0
        sma200_val = tech_data.get('sma200', 0) if tech_data else 0

    # Destek/Direnç (Levels Data'dan çekme)
    fib_res = "-"
    fib_sup = "-"
    if levels_data:
        # nearest_res bir tuple döner: (Etiket, Fiyat)
        res_tuple = levels_data.get('nearest_res')
        sup_tuple = levels_data.get('nearest_sup')
        if res_tuple: fib_res = f"{res_tuple[1]:.2f} ({res_tuple[0]})"
        if sup_tuple: fib_sup = f"{sup_tuple[1]:.2f} ({sup_tuple[0]})"

    # Likidite Hedefi
    liq_str = f"{ict_data.get('target', 0):.2f}" if ict_data else "-"

    # Price Action Tanımları
    mum_desc = "-"
    pa_div = "-"
    sfp_desc = "-"
    loc_desc = "-"
    if pa_data:
        mum_desc = pa_data.get('candle', {}).get('desc', '-')
        # Güven skoru ve bağlam notlarını candle desc'ten parse et
        candle_raw = pa_data.get('candle', {}).get('desc', '')
        confidence_prompt = ""
        if "Güven:" in candle_raw:
            try:
                guven_part = candle_raw.split("Güven:")[1].split("/100")[0].strip()
                guven_val  = int(guven_part)
                # Neyin katkı sağladığını belirle
                katki = []
                if "📍" in candle_raw:
                    katki.append("S&D bölgesi çakışması")
                if "Ultra Hacim" in candle_raw or "Hacimli" in candle_raw:
                    katki.append("hacim onaylı")
                if "Trend Yönünde" in candle_raw:
                    katki.append("trend uyumlu")
                katki_txt = " + ".join(katki) if katki else "çoklu kriter"
                confidence_prompt = f"Formasyon Güven Skoru: {guven_val}/100 ({katki_txt})"
            except Exception:
                confidence_prompt = ""
        
        sfp_info = pa_data.get('sfp', {})
        sfp_desc = f"{sfp_info.get('title', '-')} ({sfp_info.get('desc', '-')})"
        
        # Ekstra: Konum (Structure) bilgisini de ekleyelim, AI sevinir.
        loc_info = pa_data.get('loc', {})
        loc_desc = f"{loc_info.get('title', '-')} - {loc_info.get('desc', '-')}"

        # --- GÜNCELLENEN RSI KISMI ---
        div_data = pa_data.get('div', {})
        div_title = div_data.get('title', '-')
        div_reason = div_data.get('desc', '-')
        pa_div = f"{div_title} -> DETAY: {div_reason}"
    
    # --- SMART MONEY VERİLERİ (AI İÇİN HAZIRLIK) ---
    # Önce varsayılan değerleri atayalım (Veri yoksa hata vermesin)
    v_val = 0; v_diff = 0; vwap_ai_txt = "Veri Yok"; rs_ai_txt = "Veri Yok"; alpha_val = 0

    if pa_data: # Eğer Price Action verisi varsa hesapla
        # VWAP Verisi
        vwap_info = pa_data.get('vwap', {'val': 0, 'diff': 0})
        v_val = vwap_info['val']
        v_diff = vwap_info['diff']
        
        # VWAP Yorumu — bağlamsal etiket (sinyal değil, seviye konumu)
        # AI POC/VWAP Bağlam Rehberi'ne göre bu etiketleri sadece konum bilgisi olarak kullanır.
        if v_diff < -2.0: vwap_ai_txt = "VWAP ALTINDA (İskonto Bölgesi)"
        elif v_diff < 0.0: vwap_ai_txt = "VWAP TEST (Yakın)"
        elif v_diff < 8.0: vwap_ai_txt = "VWAP ÜSTÜNDE (Trend Aktif)"
        elif v_diff < 15.0: vwap_ai_txt = "VWAP'TAN GERİLDİ"
        else: vwap_ai_txt = "VWAP'TAN AŞIRI UZAK (Momentum Ölçüsü)"

        # RS Verisi
        rs_info = pa_data.get('rs', {'alpha': 0})
        alpha_val = rs_info['alpha']
        
        # RS Yorumu
        if alpha_val > 1.0: rs_ai_txt = "LİDER (Endeksi Yeniyor - Güçlü)"
        elif alpha_val < -1.0: rs_ai_txt = "ZAYIF (Endeksin Gerisinde - İlgi Yok)"
        else: rs_ai_txt = "NÖTR (Endeksle Paralel)"
    # --- HARSI ANALİZİ (AI PROMPT İÇİN) ---
    harsi_prompt_data = calculate_harsi(df_hist)
    harsi_txt = "Veri Yok"
    if harsi_prompt_data:
        harsi_txt = f"{harsi_prompt_data['status']} (HA-RSI Değeri: {harsi_prompt_data['ha_close']:.2f})"
        if harsi_prompt_data['is_green']:
            harsi_txt += " | Görünüm: POZİTİF (Yeşil Bar - Momentum Artıyor)"
        else:
            harsi_txt += " | Görünüm: NEGATİF (Kırmızı Bar - Momentum Kayboluyor)"
    # Diğer Metin Hazırlıkları
    radar_val = "Veri Yok"; radar_setup = "Belirsiz"
    r1_txt = "Veri Yok"
    if st.session_state.radar2_data is not None and not st.session_state.radar2_data.empty:
        if 'Sembol' in st.session_state.radar2_data.columns:
            r_row = st.session_state.radar2_data[st.session_state.radar2_data['Sembol'] == t]
            if not r_row.empty:
                radar_val = f"{r_row.iloc[0]['Skor']}/7"
                radar_setup = r_row.iloc[0]['Setup']
    
    if st.session_state.scan_data is not None and not st.session_state.scan_data.empty:
        col_name = 'Sembol' if 'Sembol' in st.session_state.scan_data.columns else 'Ticker'
        if col_name in st.session_state.scan_data.columns:
            r_row = st.session_state.scan_data[st.session_state.scan_data[col_name] == t]
            if not r_row.empty: r1_txt = f"Skor: {r_row.iloc[0]['Skor']}/7"
            
    r2_txt = f"Skor: {radar_val} | Setup: {radar_setup}"

    # --- GERÇEK PARA AKIŞI (OBV & DIVERGENCE) ---
    para_akisi_txt = "Nötr"

    # df_hist değişkeninin yukarıda tanımlı olduğundan emin ol (genelde prompt başında tanımlıdır)
    if 'df_hist' in locals() and df_hist is not None and len(df_hist) > 20:
        # 1. OBV Hesapla
        change = df_hist['Close'].diff()
        direction = np.sign(change).fillna(0)
        obv = (direction * df_hist['Volume']).cumsum()
        
        # YENİ: AI'ın hacim gücünü anlaması için 20 Günlük Ortalamayı (SMA) ekliyoruz
        obv_sma = obv.rolling(20).mean()

        # 2. Trendleri Kıyasla (Son 10 Gün)
        p_now = df_hist['Close'].iloc[-1]; p_old = df_hist['Close'].iloc[-11]
        obv_now = obv.iloc[-1]; obv_old = obv.iloc[-11]
        obv_sma_now = obv_sma.iloc[-1]

        price_trend = "YUKARI" if p_now > p_old else "AŞAĞI"
        obv_trend_raw = "YUKARI" if obv_now > obv_old else "AŞAĞI"
        
        # YENİ: AI için ekstra karar değişkenleri
        is_obv_strong = obv_now > obv_sma_now
        p_yesterday = df_hist['Close'].iloc[-2]
        
        # --- [YENİ] Prompt İçin RSI Emniyet Kilidi ---
        # AI'ın tepede "Gizli Giriş" diye saçmalamasını engeller.
        delta_p = df_hist['Close'].diff()
        gain_p = (delta_p.where(delta_p > 0, 0)).rolling(14).mean()
        loss_p = (-delta_p.where(delta_p < 0, 0)).rolling(14).mean()
        rsi_val_prompt = 100 - (100 / (1 + gain_p/loss_p)).iloc[-1]

        # 3. Yorumla (Güncellenmiş Profesyonel Mantık)
        if rsi_val_prompt > 60 and price_trend == "AŞAĞI":
             # Fiyat düşüyor ama RSI hala tepedeyse bu giriş değil, "Mal Yedirme" olabilir.
             para_akisi_txt = "⚠️ ZİRVE BASKISI (Dağıtım Riski): Fiyat düşüyor ancak RSI şişkin. Bu bir giriş fırsatı değil, tepeden mal dağıtımı olabilir."
             
        elif price_trend == "AŞAĞI" and obv_trend_raw == "YUKARI":
            if is_obv_strong:
                para_akisi_txt = "🔥 GÜÇLÜ GİZLİ GİRİŞ (Akümülasyon): Son 10 günde fiyat düşmesine rağmen, gerçek hacim (OBV) 20 günlük ortalamasını yukarı kesti. Akıllı para gizlice mal topluyor olabilir!"
            else:
                para_akisi_txt = "👀 OLASI TOPLAMA (Zayıf): Son 10 günde fiyat düşerken OBV hafifçe yükseliyor, ancak henüz 20 günlük ortalamasını aşacak kadar güçlü bir para girişi yok."
                
        elif price_trend == "YUKARI" and obv_trend_raw == "AŞAĞI":
            para_akisi_txt = "⚠️ GİZLİ ÇIKIŞ (Dağıtım): Son 10 günde fiyat yükselmesine rağmen kümülatif hacim (OBV) düşüyor. Yükseliş sahte olabilir, büyük oyuncular mal dağıtıyor (çıkış yapıyor) olabilir."
            
        elif is_obv_strong:
            if p_now < p_yesterday:
                para_akisi_txt = "🛡️ DÜŞÜŞE DİRENÇ (Kurumsal Emilim): Bugün fiyat düşüş eğiliminde olsa da kümülatif hacim (OBV) hala 20 günlük ortalamasının üzerinde gücünü koruyor. Panik satışları büyük oyuncular tarafından karşılanıyor olabilir."
            else:
                para_akisi_txt = "✅ SAĞLIKLI TREND (Hacim Onaylı): Fiyattaki yükseliş, gerçek hacim (OBV) tarafından net bir şekilde destekleniyor. Trendin arkasında akıllı paranın itici gücü var."
                
        else:
            para_akisi_txt = "⚖️ ZAYIF İVME (Hacimsiz Bölge): Kümülatif hacim akışı (OBV) 20 günlük ortalamasının altında süzülüyor. Fiyat hareketlerini destekleyecek net ve iştahlı bir para girişi görünmüyor."
            
    elif synth_data is not None and len(synth_data) > 15:
        # Yedek Plan: df_hist yoksa eski yöntemi kullan
        wma_now = synth_data['MF_Smooth'].tail(10).mean()
        para_akisi_txt = "Pozitif (Giriş Var)" if wma_now > 0 else "Negatif (Çıkış Var)"
        
    mini_txt = "Veri Yok"
    if mini_data:
        mini_txt = f"{mini_data.get('Durum', '-')} | RS Rating: {mini_data.get('rs_rating', '-')}"
        if mini_data.get('is_vcp'): mini_txt += " | VCP Var"
            
    def clean_html_val(key):
            val = sent_data.get(key, '0/0')
            return re.sub(r'<[^>]+>', '', str(val))
    
    sent_yapi = clean_html_val('str')
    sent_trend = clean_html_val('tr')
    sent_hacim = clean_html_val('vol')
    sent_mom = clean_html_val('mom')
    sent_vola = clean_html_val('vola')
    
    fiyat_str = f"{info.get('price', 0):.2f}" if info else "0.00"
    p_change_pct = info.get('change_pct', 0) if info else 0
    degisim_str = f"+%{p_change_pct:.2f}" if p_change_pct > 0 else f"-%{abs(p_change_pct):.2f}"
    master_txt = f"{master_score}/100"
    pros_txt = ", ".join(pros[:5])
    
    st_txt = f"{'YÜKSELİŞ' if levels_data.get('st_dir')==1 else 'DÜŞÜŞ'} | {levels_data.get('st_val',0):.2f}" if levels_data else "-"
    # ==============================================================================
    # 🧠 ALGORİTMİK KARAR MATRİSİ V3.0 (FULL-CYCLE SCENARIO DETECTOR)
    # ==============================================================================
    # 1. TEMEL METRİKLERİN HESAPLANMASI
    # ---------------------------------------------------------
    try:
        # Fiyat & Değişim
        p_now = info.get('price', 0)
        p_change_pct = info.get('change_pct', 0) # Günlük Yüzde Değişim
        
        # Trend Gücü (SMA50 Referansı) ve Yönü
        sma50_val = tech_data.get('sma50', 0)
        trend_ratio = (p_now / sma50_val) if sma50_val > 0 else 1.0
        # Trendin Eğimi (Pozitif: Yukarı yönlü, Negatif: Aşağı yönlü)
        sma50_slope = tech_data.get('sma50_slope', 1) 
        
        # Hacim Oranı (20 Günlük Ortalamaya Göre)
        vol_ratio = 1.0
        if df_hist is not None and len(df_hist) > 20:
            v_curr = float(df_hist['Volume'].iloc[-1])
            v_avg = float(df_hist['Volume'].rolling(20).mean().iloc[-1])
            if v_avg > 0: vol_ratio = v_curr / v_avg

        # STP Durumu (Momentum)
        is_stp_broken = False
        if synth_data is not None and not synth_data.empty:
            l_p = float(synth_data.iloc[-1]['Price'])
            l_s = float(synth_data.iloc[-1]['STP'])
            if l_p < l_s: is_stp_broken = True
            
        # RSI Durumu (Negatif ve Pozitif Uyumsuzluklar)
        rsi_val_now = sent_data.get('raw_rsi', 50)
        is_rsi_div_neg = "NEGATİF" in str(pa_data.get('div', {}).get('title', '')).upper()
        is_rsi_div_pos = "POZİTİF" in str(pa_data.get('div', {}).get('title', '')).upper()
        
        # Mum Durumu (İyi ve Kötü Formasyonlar)
        mum_str = str(mum_desc)
        bad_candles = ["Black Crows", "Bearish Engulfing", "Shooting Star", "Marubozu 🔻"]
        has_bad_candle = any(x in mum_str for x in bad_candles)
        good_candles = ["Hammer", "Bullish Engulfing", "Morning Star", "Marubozu 🔺", "Doji 🟢"]
        has_good_candle = any(x in mum_str for x in good_candles)

    except:
        # Veri hatası olursa çökmemesi için tüm varsayılan değerler
        p_change_pct = 0; trend_ratio = 1.0; vol_ratio = 1.0; is_stp_broken = False; rsi_val_now = 50
        is_rsi_div_neg = False; is_rsi_div_pos = False; has_bad_candle = False; has_good_candle = False; sma50_slope = 1

    # ==============================================================================
    # 2. SENARYO TESPİT MOTORU (YENİ NESİL - ICT ve PA ODAKLI)
    # ==============================================================================
    ai_scenario_title = "PİYASA OKUMASI BEKLENİYOR"
    ai_mood_instruction = ""
    
    # --- DEĞİŞKENLERİ HESAPLA (Eksik olan kısım burasıydı!) ---
    pa_signal, pa_context = detect_price_action_with_context(df_hist)
    reversal_signal = detect_ict_reversal(df_hist)
    bias = str(ict_data.get('bias', 'Nötr')) if isinstance(ict_data, dict) else 'Nötr'
    zone = str(ict_data.get('zone', 'Nötr')) if isinstance(ict_data, dict) else 'Nötr'
    
    try:
        z_score_val = round(calculate_z_score_live(df_hist), 2)
    except:
        z_score_val = 0.0
    # ----------------------------------------------------------

    # 1. ÖNCELİK: PRICE ACTION (Anlık Dönüşler en kritiktir)
    if pa_signal == "PA_BULLISH":
        ai_scenario_title = "⚡ DİPTEN V-DÖNÜŞ (LİKİDİTE AVI)"
        ai_mood_instruction = f"Fiyat {pa_context} seviyesinden agresif bir alıcı tepkisi verdi. Düşüş trendi olsa bile kısa vadeli yukarı yönlü dönüş senaryosuna ağırlık ver."
    elif pa_signal == "PA_BEARISH":
        ai_scenario_title = "⚡ TEPEDEN RET (BOĞA TUZAĞI)"
        ai_mood_instruction = f"Fiyat {pa_context} seviyesinden sert şekilde reddedildi. Satıcılar (Ayılar) kontrolü ele alıyor. Yükseliş trendi olsa bile kısa vadeli düşüş ve düzeltme senaryosuna ağırlık ver."

    # 2. ÖNCELİK: UÇ DURUMLAR (Z-SCORE & KOPUŞLAR)
    elif z_score_val >= 2.0:
        ai_scenario_title = "🔥 AŞIRI ISINMA (PARABOLİK RİSK)"
        ai_mood_instruction = "Fiyat istatistiksel olarak çok şişmiş durumda (+2 Z-Score). Kar satışları ve sert düzeltme riski çok yüksek. Yeni alımların riskli olduğunu ve stopların yaklaştırılması gerektiğini vurgula."
    elif z_score_val <= -2.0:
        ai_scenario_title = "🩸 KAPİTÜLASYON (AŞIRI SATIM)"
        ai_mood_instruction = "Fiyat istatistiksel olarak dibe vurmuş durumda (-2 Z-Score). Panik satışları bitmek üzere olabilir, Akıllı Para'nın (Smart Money) dipten toplama ihtimalini ve dönüş fırsatlarını değerlendir."

    # 3. ÖNCELİK: ICT (SMART MONEY) BAĞLAMI
    elif "bearish" in bias.lower():
        if "PREMIUM" in zone.upper():
            ai_scenario_title = "📉 DAĞITIM BÖLGESİ (BEARISH PREMIUM)"
            ai_mood_instruction = "Makro trend aşağı ve fiyat şu an 'Pahalı' (Premium) bölgede. Kurumsalların satış (dağıtım) yaptığı bir alandayız. 'Normal piyasa' demek yerine, aşağıdaki likidite hedeflerine (SSL) doğru düşüş senaryosuna net bir şekilde odaklan."
        else:
            ai_scenario_title = "🐻 DÜŞÜŞ TRENDİ (İSKONTOLU ALAN)"
            ai_mood_instruction = "Trend aşağı yönlü ancak fiyat şu an 'Ucuz' (Discount) bölgede. Düşüş devam etse de, aşağıdaki likidite (stoplar) temizlendikten sonra sert bir yukarı tepki gelebileceğini uyar. Çift yönlü düşün."
    elif "bullish" in bias.lower():
        if "DISCOUNT" in zone.upper():
            ai_scenario_title = "🚀 TOPLAMA BÖLGESİ (BULLISH DISCOUNT)"
            ai_mood_instruction = "Makro trend yukarı ve fiyat şu an 'Ucuz' (Discount) bölgede. Akıllı paranın (Smart Money) alım için pusuda beklediği en ideal seviyeler. Yukarı yönlü hedeflere (BSL) ve yükseliş senaryosuna net bir şekilde odaklan."
        else:
            ai_scenario_title = "🐂 YÜKSELİŞ TRENDİ (PAHALI ALAN)"
            ai_mood_instruction = "Trend yukarı yönlü ancak fiyat 'Pahalı' (Premium) bölgede. Yükseliş sürse de yeni alım için FOMO riski taşıyan bir bölge. Kar realizasyonlarına (pullback) karşı temkinli bir yükseliş senaryosu çiz."
    else:
        # Gerçekten Nötr ise
        ai_scenario_title = "⚖️ KONSOLİDASYON (YATAY BANT)"
        ai_mood_instruction = "Piyasa şu an net bir trende sahip değil ve yön arayışında (Range). Destek ve dirençler arasında sıkışma var. Her iki yönü de dengeli bir şekilde anlatarak kırılım şartlarını belirt."

    # === HOOK BAŞLIĞI ve DİNAMİK BÖLÜM BAŞLIĞI ===
    # En güçlü sinyal etiketi (Python tarafı)
    if is_nadir != "HAYIR":
        _hook_sinyal = "♠️ Royal Flush Nadir Fırsat"
    elif "EVET" in str(is_golden):
        _hook_sinyal = "🏆 Altın Fırsat Aktif"
    elif "TOPLAMA" in ai_scenario_title.upper():
        _hook_sinyal = "🐳 Kurumsal Toplama Sinyali"
    elif "DAĞITIM" in ai_scenario_title.upper():
        _hook_sinyal = "📉 Dağıtım Riski"
    elif "KAPİTÜLASYON" in ai_scenario_title.upper():
        _hook_sinyal = "🩸 Kapitülasyon Bölgesi"
    elif "ISINMA" in ai_scenario_title.upper():
        _hook_sinyal = "🔥 Parabolik Risk"
    elif "DÖNÜŞ" in ai_scenario_title.upper() or "V-DÖNÜŞ" in ai_scenario_title.upper():
        _hook_sinyal = "⚡ Dönüş Sinyali"
    elif "TUZAK" in ai_scenario_title.upper():
        _hook_sinyal = "🧟 Boğa Tuzağı"
    else:
        _hook_sinyal = ""
    hook_baslik = (
        f"#{clean_ticker} {fiyat_str} ({degisim_str}) | {ai_scenario_title}"
        + (f" — {_hook_sinyal}" if _hook_sinyal else "")
        + " 👇📸"
    )

    # Dinamik bölüm başlığı — senaryoya göre değişir
    if "bullish" in bias.lower() and "DISCOUNT" in zone.upper():
        genel_analiz_baslik = "1. GENEL ANALİZ — Neden Fırsat Var? (Önem derecesine göre)"
    elif "bearish" in bias.lower() and "PREMIUM" in zone.upper():
        genel_analiz_baslik = "1. GENEL ANALİZ — Neden Tehlikeli? (Önem derecesine göre)"
    elif z_score_val >= 2.0:
        genel_analiz_baslik = "1. GENEL ANALİZ — Aşırı Isınma: Risk Nerede? (Önem derecesine göre)"
    elif z_score_val <= -2.0:
        genel_analiz_baslik = "1. GENEL ANALİZ — Dip mi, Yoksa Devam mı? (Önem derecesine göre)"
    elif pa_signal == "PA_BULLISH":
        genel_analiz_baslik = "1. GENEL ANALİZ — Dönüş Sinyali Gerçek mi? (Önem derecesine göre)"
    elif pa_signal == "PA_BEARISH":
        genel_analiz_baslik = "1. GENEL ANALİZ — Tuzak mı, Gerçek Ret mi? (Önem derecesine göre)"
    elif "bullish" in bias.lower():
        genel_analiz_baslik = "1. GENEL ANALİZ — Yükseliş Devam Edebilir mi? (Önem derecesine göre)"
    elif "bearish" in bias.lower():
        genel_analiz_baslik = "1. GENEL ANALİZ — Düşüş Nereye Kadar? (Önem derecesine göre)"
    else:
        genel_analiz_baslik = "1. GENEL ANALİZ — İki Taraf da Konuşuyor (Önem derecesine göre)"

    # --- YENİ: AI İÇİN S&D VE LİKİDİTE VERİLERİ ÇEKİMİ ---
    try: 
        sd_data = detect_supply_demand_zones(df_hist)
        sd_txt_ai = f"{sd_data['Type']} ({sd_data['Bottom']:.2f} - {sd_data['Top']:.2f}) Durum: {sd_data['Status']} olabilir." if sd_data else "Taze bölge görünmüyor."
    except: 
        sd_txt_ai = "Veri Yok"
        
    havuz_ai = ict_data.get('eqh_eql_txt', 'Yok') if isinstance(ict_data, dict) else 'Yok'
    sweep_ai = ict_data.get('sweep_txt', 'Yok') if isinstance(ict_data, dict) else 'Yok'
    
    # --- 🚨 PROMPT'TAN HEMEN ÖNCE PAKETİ AÇIYORUZ ---
    # calculate_price_action_dna'dan dönen veriyi (örneğin dna değişkeni) kontrol ediyoruz:
    df = get_safe_historical_data(t, period="6mo") 
    dna = calculate_price_action_dna(t)
    # Prompt oluşturulmadan hemen önce bu verileri çekiyoruz
    sv_extra = pa_data.get('smart_volume', {})
    rvol_val           = sv_extra.get('rvol', 1.0)
    _vol_missing_flag  = sv_extra.get('vol_data_missing', False)
    stop_vol_val       = sv_extra.get('stopping', 'Yok')
    climax_vol_val     = sv_extra.get('climax', 'Yok')
    # --- PROMPT İÇİN POC VERİLERİNİ HAZIRLAMA ---
    if dna and "smart_volume" in dna:
        sv = dna["smart_volume"]
        poc_price = f"{sv['poc']:.2f}"
        delta_val = sv.get("delta", 0)
        delta_yuzde = sv.get("delta_yuzde", 0)

        if delta_val < 0:
            if delta_yuzde >= 60.0:
                baskinlik = f"-%{delta_yuzde:.1f} (Agresif Satıcılar Baskın)"
            else:
                baskinlik = f"-%{delta_yuzde:.1f} (Nötr/Gürültü - Sığ Satış)"
        elif delta_val > 0:
            if delta_yuzde >= 60.0:
                baskinlik = f"+%{delta_yuzde:.1f} (Agresif Alıcılar Baskın)"
            else:
                baskinlik = f"+%{delta_yuzde:.1f} (Nötr/Gürültü - Pasif Limit Emirler)"
        else:
            baskinlik = "Kusursuz Denge (%0)"

        delta_durumu = f"{sv['title']} | Net Baskınlık: {baskinlik}"

        # Değer bölgesi konumu ve sınırları
        va_pos_txt   = sv.get("va_pos", "Veri Yok")
        vah_txt      = f"{sv.get('vah', 0):.2f}"
        val_txt      = f"{sv.get('val', 0):.2f}"

        # 5 seans kümülatif delta
        cum5_val     = sv.get("cum_delta_5", 0)
        cum5_pct_val = sv.get("cum_delta_pct", 0)
        if cum5_val > 0:
            cum5_txt = f"+%{cum5_pct_val:.1f} (5 günde net alım ağırlığı — Kurumsal Birikim sinyali)"
        elif cum5_val < 0:
            cum5_txt = f"-%{cum5_pct_val:.1f} (5 günde net satış ağırlığı — Dağıtım baskısı)"
        else:
            cum5_txt = "Dengede (%0) — Net yön yok"
    else:
        delta_durumu = "Veri Yok"
        poc_price    = "Veri Yok"
        va_pos_txt   = "Veri Yok"
        vah_txt      = "Veri Yok"
        val_txt      = "Veri Yok"
        cum5_txt     = "Veri Yok"
        # -----------------------------------------------------

    # Güncel fiyatı DataFrame'den veya mevcut bir fiyattan çekiyoruz
    try:
        guncel_fiyat = f"{df['Close'].iloc[-1]:.2f}"
    except:
        guncel_fiyat = "Bilinmiyor"
    # ------------------------------------------------

    # --- PRE-PROMPT VERİ HAZIRLIĞI ---
    # Harmonik formasyon
    try:
        _harm = calculate_harmonic_patterns(t, df_hist)
        if _harm:
            _hdir = _harm.get('direction', 'Bullish')
            _hpat = _harm.get('pattern', '?')
            _hprz = _harm.get('prz', 0)
            _hst  = _harm.get('state', 'fresh')
            _hfark = abs(_harm.get('curr_price', _hprz) - _hprz) / (_hprz + 1e-9) * 100
            harm_txt = (f"{_hpat} ({_hdir}) | PRZ: {_hprz:.2f} | "
                        f"Uzaklık: %{_hfark:.1f} | Durum: {'Tamamlandı' if _hst == 'fresh' else 'Yaklaşıyor'}")
        else:
            harm_txt = "Yok"
    except Exception:
        harm_txt = "Yok"

    # KALKIŞ RADARI pre_launch durumu
    try:
        _sms = calculate_smart_money_score(t)
        _sms_str = (f"Skor: {_sms['score']}/100 — Durum: {_sms['status']}\n{_sms['summary_text']}"
                    + ("\n⚡ ÖN HAZIRLIK UYARISI: 4 temel kriter karşılandı, tetik (trigger) henüz ateşlenmedi — "
                       "fiyat henüz hareket etmeden önce ideal pencere olabilir." if _sms.get('pre_launch') else "")
                    ) if _sms else "Hesaplanamadı"
    except Exception:
        _sms_str = "Hesaplanamadı"

    # ── Piyasa Fazı + Kanaat Skoru (session_state'ten) ───────────────
    try:
        _ss_regime = st.session_state.get("_last_regime", {})
        _rg_phase  = _ss_regime.get("phase", 0)
        _rg_label  = _ss_regime.get("label", "Belirsiz")
        _rg_conf   = int(_ss_regime.get("confidence", 0) * 100)
        _rg_desc   = _ss_regime.get("desc", "")
        _rg_bull   = _ss_regime.get("bull_bias", None)
        _rg_warn   = ""
        if _rg_phase == 3:
            _rg_warn = "⚠️ Dağıtım fazında AL sinyalleri karşı-trend sayılır — dikkatli yorumla."
        elif _rg_phase == 4:
            _rg_warn = "⚠️ Düşüş fazında. Olumlu sinyalleri karşı-trend olarak değerlendir."
        elif _rg_phase == 1:
            _rg_warn = "ℹ️ Birikim fazı — kırılım onaylanmadan erken giriş riski var."
        _regime_prompt_str = (
            f"Faz {_rg_phase} — {_rg_label} | Güven: %{_rg_conf}\n"
            f"Açıklama: {_rg_desc}\n"
            + (f"{_rg_warn}\n" if _rg_warn else "")
        ) if _rg_phase else "Hesaplanamadı"
    except Exception:
        _regime_prompt_str = "Hesaplanamadı"

    try:
        _ss_conv   = st.session_state.get("_last_conviction", {})
        _cv_score  = _ss_conv.get("score", 50)
        _cv_label  = _ss_conv.get("label", "NÖTR")
        _cv_raw    = _ss_conv.get("raw", 0)
        _cv_factors = _ss_conv.get("factors", [])
        # factors = list of (açıklama_str, pts_int) tuple
        _cv_pos = [f for f in _cv_factors if f[1] > 0]
        _cv_neg = [f for f in _cv_factors if f[1] < 0]
        _cv_pos_str = ", ".join(f[0] for f in _cv_pos) if _cv_pos else "Yok"
        _cv_neg_str = ", ".join(f[0] for f in _cv_neg) if _cv_neg else "Yok"
        _conviction_prompt_str = (
            f"Skor: {_cv_score}/100 — {_cv_label}\n"
            f"Olumlu faktörler: {_cv_pos_str}\n"
            f"Olumsuz faktörler: {_cv_neg_str}"
        )
    except Exception:
        _conviction_prompt_str = "Hesaplanamadı"

# ==============================================================================
# BÖLÜM 35 — AI PROMPT SİSTEMİ
# Senaryo bazlı dinamik kimlik seçimi. Contradiction-hunting talimatları
# ve görev bazlı (Görev 1 ELITE / Görev 3 PRO) prompt üreticileri.
# ==============================================================================
# --- PERSONA SEÇİMİ (Senaryo bazlı dinamik kimlik) ---
    # Öncelik sırası: Royal Flush Nadir Fırsat > Z-Score aşırılık > Formasyon > Nötr
    try:
        _z = z_score_val
    except:
        _z = 0.0

    _has_nadir   = is_nadir != "HAYIR"
    _has_pat     = not pat_df.empty
    _pat_name    = pat_df.iloc[0]['Formasyon'] if _has_pat else ""
    _is_tobo_flag = "TOBO" in _pat_name or "FİNCAN" in _pat_name or "YÜKSELEN" in _pat_name
    _is_qml      = "QUASIMODO" in _pat_name or "3 DRIVE" in _pat_name
    _bearish_ict = "bearish" in str(ict_data.get('bias', '')).lower() if ict_data else False
    _bullish_ict = "bullish" in str(ict_data.get('bias', '')).lower() if ict_data else False

    if _has_nadir:
        persona_kimlik = (
            "Sen yılda belki 3-4 kez gördüğün nadir kurumsal setup'ları sabırla bekleyen, "
            "pozisyon büyüten ve asimetrik risk/ödül fırsatlarında devreye giren agresif bir "
            "momentum yatırımcısısın. Bu tür sinyaller gördüğünde sesini yükselt, "
            "ama hukuki dili ve ihtiyatlı tonu asla bırakma."
        )
        persona_ton = (
            "Bugün nadir bir setup var. Analizinin tonu heyecanlı ama kontrollü olsun — "
            "tecrübeli bir avcı avını bulduğunda nasıl sakin kalırsa öyle. "
            "Veriyi raporlama, hikayeyi anlat."
        )
    elif _is_qml:
        persona_kimlik = (
            "Sen kurumların küçük yatırımcıların stoplarını patlatıp mal topladığı "
            "anlara odaklanan, likidite avcısı bir ICT trader kimliğindesin. "
            "Stop-hunt, likidite süpürmesi ve dipten toplama senin uzmanlık alanın. "
            "Piyasanın 'görünmez elini' halkın anlayacağı dille deşifre etmek için buradasın."
        )
        persona_ton = (
            "Bugün piyasada kurumsal bir oyun oynandığına dair izler var. "
            "Analizini o oyunun senaryosu üzerine kur — kim kimi tuzağa düşürdü, "
            "nerede mal toplandı, bir sonraki hamle ne olabilir?"
        )
    elif _z >= 2.0:
        persona_kimlik = (
            "Sen kariyerinde onlarca piyasa balonu ve çöküşü yaşamış, "
            "aşırı ısınmış piyasalarda defansif pozisyon alan şüpheci bir risk yöneticisisin. "
            "Herkes alırken satan, herkes satarken alan tarafın insanısın. "
            "İstatistiksel anomalileri erken gören ve bunları net bir dille aktaran "
            "bir risk masası uzmanısın."
        )
        persona_ton = (
            "Bugün sana verilen veriler seni tedirgin ediyor. Analizin temkinli, "
            "uyarı odaklı ve defansif olsun. 'Herkes aynı tarafta mı?' sorusunu sor. "
            "Risk/ödül dengesizliğini net fiyatlarla göster."
        )
    elif _z <= -2.0:
        persona_kimlik = (
            "Sen kapitülasyon anlarını — paniğin doruk noktasını, son satıcının da "
            "teslim olduğu o anı — avlayan, derin değer odaklı bir kontrarian yatırımcısın. "
            "İstatistiksel diplerin nasıl göründüğünü, dipten toplamayı ve panik satışlarını "
            "25 yılda onlarca kez gördün."
        )
        persona_ton = (
            "Bugünkü veri aşırı satım bölgesine işaret ediyor. "
            "Analizin umut verici ama temkinli olsun — dip yakalamak cesaret ister, "
            "ama 'daha da dip olabilir mi?' sorusu her zaman masada. "
            "Panik ile fırsat arasındaki ince çizgiyi göster."
        )
    elif _is_tobo_flag:
        persona_kimlik = (
            "Sen büyük yapısal formasyonların — TOBO, Fincan-Kulp, Yükselen Üçgen gibi — "
            "tamamlanma anlarını sabırla bekleyen, uzun vadeli düşünen bir swing trader kimliğindesin. "
            "Bu formasyonlar aylarca, bazen yıllarca oluşur; sen onların kırılım anını "
            "diğerlerinden önce tespit etmek için buradasın."
        )
        persona_ton = (
            "Bugün aylarca veya çeyrekler boyunca oluşan bir yapının kırılım eşiğine "
            "gelinmiş olabilir. Analizini o büyük yapıyı anlatarak başlat — "
            "fiyat nereye gitti, nerede döndü, şimdi nerede? "
            "Okuyucu grafiği görmese de kafasında çizebilmeli."
        )
    elif _bearish_ict and _z > 0.5:
        persona_kimlik = (
            "Sen dağıtım bölgelerini — kurumların küçük yatırımcılara mal sattığı "
            "pahalı bölgeleri — erken fark eden, gizli satışları takip eden "
            "defansif bir portföy yöneticisisin. 'Herkes alırken ben neden dikkatli oluyorum?' "
            "sorusunun cevabını veri ile açıklayan bir analistsin."
        )
        persona_ton = (
            "Bugün tabloda bir şeyler rahatsız edici. Görünürde iyi olan her şeyin altında "
            "ne saklı? Analizini o şüphe üzerine kur. "
            "Ama şüpheni de veriye dayandır, havasından değil."
        )
    else:
        persona_kimlik = (
            "Sen net bir sinyal olmayan, piyasanın yön arayışında olduğu konsolidasyon dönemlerinde "
            "bekleyebilen, sabırlı ve disiplinli bir portföy yöneticisisin. "
            "Pozisyon almak için acele etmezsin — en iyi işlem bazen hiç işlem yapmamaktır. "
            "Risk/ödül dengesini her zaman önce sorgularsın."
        )
        persona_ton = (
            "Bugün net bir yön yok. Analizin dengeli, her iki senaryoyu da gösteren "
            "ve 'bekle, izle' mesajı veren bir yapıda olsun. "
            "Kırılım şartlarını somut fiyatlarla belirt."
        )

    # --- KANCA: Çelişki tespiti ---
    kanca_talimat = """
*** ANALİZİN ODAK NOKTASI ***
Analize başlamadan önce şu soruyu kendine sor:
"Bu verideki en baskın hikaye nedir?"

Eğer veride gerçek bir çelişki varsa — onu vurgula. Gerçek çelişki örnekleri:
- Fiyat yukarı gidiyor ama OBV düşüyor → gizli dağıtım olabilir
- Hacim patlıyor ama fiyat hareket etmiyor (Churning) → enerji boşa mı gidiyor?
- Kurumsal para akışı pozitif ama ICT yapısı bearish → akıllı para ne biliyor?
- Akıllı para toplama sinyali var ama fiyat düşüşte → sabırlı toplama mı, düşen bıçak mı?
- Formasyon güven skoru düşük ama Royal Flush tetiklendi → yapı zayıf ama setup güçlü
- Fiyat SMA200 üstünde ama SMA200 eğimi aşağı → üstte olmak yeterli mi?

Eğer veride belirgin bir çelişki yoksa — bunu zorla arama. Bunun yerine en güçlü sinyali bul ve analizini onun üzerine kur. Ralli yapan bir hissede Z-Score yükselmesi veya VWAP sapması çelişki değildir — trendin doğal sonucudur, dipnot geç.

KURAL: Belirgin bir çelişki varsa analizini o çelişkinin etrafında kur. Çelişki yoksa en baskın sinyali merkeze al. Her iki durumda da tek bir hikaye anlat — her veriyi eşit ağırlıkta sıralama.
"""

    # ── PROMPT İÇİN AKTİF PA SİNYALİ ÖNCELİK LİSTESİ ─────────────
    _pa_prio = []
    _div_active_ai   = pa_div not in ("-", "Yok", "Bilinmiyor") and "Nötr" not in pa_div
    _sfp_active_ai   = "Bullish SFP" in sfp_desc or "Bearish SFP" in sfp_desc
    _sd_active_ai    = sd_txt_ai not in ("Taze bölge görünmüyor.", "Veri Yok")
    _vwap_ext_ai     = v_diff < -2.0 or v_diff > 15.0
    _vwap_warm_ai    = 8.0 <= v_diff < 15.0
    _rs_active_ai    = abs(alpha_val) > 1.0
    _harm_active_ai  = harm_txt not in ("Yok", "")
    _pa_sig_active   = pa_signal in ("PA_BULLISH", "PA_BEARISH")

    if _div_active_ai:   _pa_prio.append((10, f"RSI UYUMSUZLUK → {pa_div.split(' -> ')[0]}"))
    if _sfp_active_ai:   _pa_prio.append((9,  f"TUZAK (SFP) → {sfp_desc.split('(')[0].strip()}"))
    if _pa_sig_active:   _pa_prio.append((8,  f"ANLİK DÖNÜŞ → {pa_signal} ({pa_context})"))
    if _harm_active_ai:  _pa_prio.append((7,  f"HARMONİK FORMASYON → {harm_txt.split('|')[0].strip()}"))
    if _sd_active_ai:    _pa_prio.append((5,  f"ARZ-TALEP BÖLGESİ → {sd_txt_ai.split('.')[0]}"))
    if _vwap_ext_ai:     _pa_prio.append((7,  f"VWAP EKSTREMİ → {vwap_ai_txt} (%{v_diff:.1f} sapma)"))
    if _vwap_warm_ai:    _pa_prio.append((3,  f"VWAP ISINMA → {vwap_ai_txt} (%{v_diff:.1f} sapma)"))
    if _rs_active_ai:    _pa_prio.append((4,  f"RS GÜCÜ → {rs_ai_txt} (Alpha: {alpha_val:.1f})"))

    _pa_prio.sort(key=lambda x: x[0], reverse=True)
    if _pa_prio:
        _pa_priority_str = "\n".join(f"  [{p}/10] {lbl}" for p, lbl in _pa_prio)
        _pa_priority_str = (
            "⚡ BU HİSSE İÇİN AKTİF PA SİNYALLERİ (öncelik sırasına göre — sadece ateşlenenler):\n"
            + _pa_priority_str
            + "\n  — Yukarıdaki sinyalleri analiz merkezine al; diğer verileri bu çerçeve içinde yorumla.\n"
        )
    else:
        _pa_priority_str = "⚡ AKTİF PA SİNYALİ: Belirgin tetikleyici yok — nötr izleme modu. Veriyi dengeli değerlendir.\n"
    # ────────────────────────────────────────────────────────────────

    prompt = f"""*** SİSTEM ROLLERİ VE BUGÜNKÜ KİMLİĞİN ***
Sen 25 yılını finansın risk masasında geçirmiş, "Smart Money Radar" projesinin yaşayan ruhusun. Price Action, ICT (Akıllı Para), VWAP ve momentum yatırımcılığı konularında derin deneyim sahibisin — ama bu deneyimi o günün verisine göre farklı bir mercekten kullanırsın.
Unutma, karşındaki kitle ortalama üzeri zekaya sahip ve hafızası güçlü bir topluluk. Bugün söylediğin bir şeyi yarın veri değişmeden inkar edersen güven kaybederiz. Bu yüzden 'kesinlik' satma, 'olasılık ve risk yönetimi' sat. Analizlerin bir 'kumarbazın heyecanı' değil, bir 'satranç ustasının soğukkanlılığı' tınısında olsun.

Bugün sana verilen veri ve sinyaller incelendiğinde, analizini şu kimlikle yapman gerekiyor:
{persona_kimlik}

Analiz tonun için özel talimat:
{persona_ton}

Sana ekte sunduğum GRAFİK GÖRSELİNİ (Röntgen) kendi görsel zekanla derinlemesine incele. Aynı zamanda aşağıdaki algoritmik verileri kullanarak profesyonel bir analiz/işlem planı oluştur.
Bu iki veriyi (grafikte gördüklerini ve aşağıda okuduklarını) birleştirerek o kusursuz analizi çıkar. Grafiği okuyamıyorsan analizinin en altına "Grafik görünmemektedir" yaz, ama teknik verilerle analiz yap. Grafik görünüyorsa analizinin merkezine Price Action'ı koy; algoritmik veriler bu analizi destekleyen veya sorgulayan kanıtlar olarak kullan.
Aşağıdaki herhangi bir veri noktası 'Bilinmiyor' veya 'Yok' olarak gelmişse, o alanı yorumlamaya zorlama — mevcut diğer verilerle sentezini yap.

*** ENDEKSLERİ ANALİZ EDERKEN HACİM VERİSİ KULLANMA ***
Analiz ettiğin sembol bir endeks ise (XU100, XU030, S&P500, Nasdaq, DAX, vb.) — hacim verisini HİÇBİR ŞEKİLDE analize dahil etme ve hacim bazlı yorum yapma. Sebebi teknik: Endeksler bizzat alınıp satılan enstrümanlar değil, hesaplanan değerlerdir. Yahoo Finance ve benzeri veri sağlayıcılar endeksler için güvenilir hacim verisi sunmaz — dönen rakam ya 0'dır ya da anlamsız bir toplamdan ibarettir. Bu yüzden endeks analizlerinde OBV, hacim trendi, hacim momentumu, "hacim destekli hareket", "hacim kuruyor/artıyor" gibi ifadeler kullanma. Bunların yerine fiyat momentumunu, EMA hizalamasını, RSI'ı, Fibonacci seviyelerini ve price action yapısını (BOS, CHoCH, HH/HL döngüleri) ön plana çıkar.

Senin gizli gücün, bu kurumsal derinliği Twitter'daki @SMRadar_2026 topluluğu için vurucu, merak uyandırıcı ve etkileşim odaklı bir hikayeye dönüştürebilmendir. Sen sadece veri okumuyorsun; o verinin içindeki Akıllı Para niyetini deşifre edip halkın anlayacağı dille bir "Piyasa Pusulası" sunuyorsun.
Görevin veriyi sadece raporlamak değil, içindeki insani ve kurumsal niyetleri deşifre etmektir. Bir makine gibi steril değil; masanın öbür tarafında oturan, biraz şüpheci, sezgileri kuvvetli ve tecrübeli bir stratejist gibi konuş. Analizlerin içine "Açıkçası bu tablo beni biraz rahatsız ediyor", "bu noktada temkinli olmamız gerektiğini söylüyor", "Piyasa burada bir bit yeniği saklıyor olabilir", "Bu kadar uyum beni düşündürüyor — gerçekten bu kadar temiz mi?" gibi insani, samimi ve tecrübe odaklı cümleler serpiştir. Arada cümlelere "Dostlar" diyerek  başla.
Yapacağın analizin vadesi 3 gün ile 3 hafta arasında değişebilir — bu bir "anlık röntgen" değil, "görünmez elin niyetini deşifre eden bir piyasa pusulası" olacak. 
{kanca_talimat}
    
*** EN ÖNEMLİ KURAL: VERİ ODAK NOKTASI VE AĞIRLIKLANDIRMA KURALI ***
1. ANALİZİN MERKEZİ: Her zaman "Akıllı Para ne yapıyor?", "Senaryo Çerçevesi (Bias+Zone)" ve "Fitil Çekiliyor mu?" soruları olmalıdır.
2. Z-SCORE SINIRLANDIRMASI: Z-Score veya ortalamalardan uzaklaşma verilerini analizin merkezine KOYMA. Yüksek Z-Score değerlerini bir "çöküş", "bit yeniği" veya "kesin dönüş" sinyali olarak YORUMLAMA.
3. Güçlü kurumsal alımların olduğu yerlerde yüksek Z-Score, tehlike değil "güçlü momentumun" kanıtıdır. Z-Score'a sadece risk yönetimi paragrafında "kısa bir kâr al/izleyen stop uyarısı" olarak kısaca değin ve geç. Hikayeni bu istatistik üzerine kurma.
Objektiflik Kuralı: Piyasaya asla sadece korkuyla veya sadece coşkuyla bakma. Her analizinde masadaki 'Kurumsal İştahı (Alıcı Gücü)' ve 'Karşılaşılabilecek Duvarları (Satış İhtimali)' aynı terazide tart. Örneğin; fiyat çok yükselmiş olsa bile hemen düşüş senaryosu yazma. 'Trend çok güçlü ilerliyor, mevcut rüzgar alıcılardan yana, sadece şu seviyelere yaklaşıldığında kâr satışları gelebilir' şeklinde nötr ve profesyonel bir dil kullan.
BEARISH BIAS (KÖTÜMSER ÖNYARGI) YASAĞI: Olaylara sürekli pesimist bir açıyla yaklaşma. Her verinin altında bir çöküş, tuzak veya felaket arayan aşırı defansif bir tutum sergileme. Piyasaya sürekli şüpheyle bakmak yerine; yükseliş ivmesini ve alıcı gücünü, masadaki düşüş riskleriyle tamamen aynı terazide tart. Sen bir felaket tellalı değil, soğukkanlı bir stratejistsin.

*** KESİN DİL KURALLARI VE HUKUKİ GÜVENLİK PROTOKOLÜ ***
Bu bir finansal analizdir ve HUKUKİ RİSKLER barındırır. Bu yüzden aşağıdaki kurallara HARFİYEN uyacaksın:
HALKÇI ANALİST KİMLİĞİ: Analizlerini 'okumuşun halinden anlamayan' bir profesör gibi değil, 'en karmaşık riski kahvehanedeki adama anlatabilen' dahi bir stratejist gibi hazırla.
1. YASAKLI KELİMELER LİSTESİ:
   — Kesinlik bildiren: "kesin, kesinlikle, %100, garanti, tartışmasız, hiç şüphesiz, açıkça, mutlaka"
   — Abartılı/duygusal: "inanılmaz, devasa, muazzam, olağanüstü, mükemmel, felaket, yıkıcı, eşi benzeri yok, benzeri görülmemiş, tarihi, rekor kıran, nadir"
   — Piyasayı kişileştiren edebi mecazlar: "fısıldıyor, fısıldıyor olabilir, bağırıyor, haykırıyor, çığlık atıyor, alarm veriyor"
   — Yönlendirici fiiller: "uçacak, kaçacak, çökecek, patlayacak, dibe vuracak"
   — Yasak kelimeler: "kanıtlar, kanıtlıyor, kanıtlamaktadır, belgeliyor, belgeler, belgelemektedir"
   — Tehlike/korku sıfatları: "tehlikeli, korkutucu, endişe verici, uyarı niteliğinde"
   Bunları ASLA KULLANMAYACAKSIN.

2. YASAKLI SIFAT VE ZARF KULLANIMI:
   — Yoğunluk zarfları YASAKTIR: "çok, oldukça, son derece, aşırı derecede, fazlasıyla, inanılmaz derecede" — bunları sıfatın önüne KOYMA.
   — Drama sıfatları YASAKTIR: "sert, fena, ciddi, dramatik, şiddetli, ağır, derin, yıkıcı, kritik" — bunları kullanma.
   — Tarihi/eşsizlik iddiaları YASAKTIR: "tarihi, rekor, benzeri görülmemiş, nadir, olağanüstü, eşi benzeri yok"
   — KURAL: Sıfat kullanmak zorundaysan, veriyle karşılaştır. "Sert düşüş" değil → "önceki 5 güne göre daha belirgin bir düşüş". "Çok ciddi" değil → "geçmişte bu seviyelerde büyük hareketler görüldü".

3. ROBOT DİLİ ASLA KULLANMA: Filleri asla "..mektedir" "...maktadır" gibi robot diliyle kullanma. İnsan dili kullan: "...yor" "...labilir" şeklinde anlat.
YASAKLI CÜMLE KALIPLARI — Aşağıdaki kalıpları ASLA kullanma, bunları kullandığında fark edilebilir bir yapay zeka gibi görünürsün:
   YASAKLI: "perakende yatırımcı", onun yerine "küçük yatırımcı"
   YASAKLI: "dır, dir, tir, tır" ile biten kelimeleri kullanma. Orneğin: "görünmektedir", "değerlendirilebilir", "tespit edilmiştir", "anlaşılmaktadır" gibi. → YERİNE: "...gibi görünüyor", "...gibi duruyor", "...olabilir", "...gibi olabilir" "olumludur" yerine "olumlu"..yani daha çok konuşma dili gibi konuş.
   YASAKLI: asla kelimeleri "mektedir" maktadır" gibi robotik bir şekilde yazma
   YASAKLI: "...olarak değerlendirilebilir" deme → YERİNE: "Bu tablo bana şunu gösteriyor")
   YASAKLI: "...göze çarpmaktadır" deme → YERİNE: Ne gördüğünü söyle ("Dikkat çeken şu:")
   YASAKLI: "...dikkat çekmektedir" deme → YERİNE: Neden önemli olduğunu açıkla
   YASAKLI: "...söylemek mümkündür" deme → YERİNE: Söyle, izin istemene gerek yok
   YASAKLI: "...kanıtlıyor" asla deme → YERİNE: "gösteriyor olabilir", "gibi görünüyor", "gibi duruyor"
   YASAKLI: "Bu bağlamda..." → YERİNE: Cümleyi direkt başlat
   YASAKLI: "Öte yandan..." → YERİNE: "Ama", "Bununla birlikte", "Şu da var ki"
   YASAKLI: "Sonuç itibarıyla..." → YERİNE: "Kısacası", "Uzun lafın kısası" "Özetle"
   YASAKLI: "...önem arz etmektedir" deme → YERİNE: Neden önemli olduğunu bir cümleyle açıkla
   YASAKLI: "Bu veriler ışığında..." → YERİNE: Direkt veriye gönderme yap
   YASAKLI: "...olduğu görülmektedir" deme → YERİNE: "...olabileceği görünüyor", "...gibi"
   YASAKLI: "...tespit edilmiştir" → YERİNE: "...görülüyor", "...çıkıyor"
   YASAKLI: "İncelendiğinde..." → YERİNE: Doğrudan bulgunu yaz
   YASAKLI: "kanıtlıyor" asla deme→ YERİNE "gösteriyor olabilir"
   YASAKLI: "Genel itibarıyla..." → YERİNE: "Tablonun özü şu:", "Kısaca:"
   YASAKLI: "...olduğu anlaşılmaktadır" → YERİNE: "...olabileceği anlaşılıyor", "...görünüyor"
   YASAKLI: Her paragrafı "X tespit edilmiştir, bu durum Y anlamına gelmektedir" yapısıyla bitirmek
   YASAKLI: Her bölümü "Bu veriler ışığında şunu söyleyebiliriz ki..." ile açmak
   YASAKLI: Sonuç paragrafını her zaman "Genel itibarıyla değerlendirildiğinde..." ile başlatmak
   ━━━ MEAN REVERSION FALLACY YASAKLI KALIPLARI (kritik — ASLA kullanma) ━━━
   YASAKLI: "düzeltme ihtiyacı" / "düzeltme gelebilir" / "düzeltme zorunlu" → uzaklık tek başına argüman değil
   YASAKLI: "geri gelmesi lazım" / "ortalamaya dönmeli" / "geri çekilme kaçınılmaz" → bağımsız kanıt yoksa yazma
   YASAKLI: "sürdürülemez hareket" / "bu hızda yükseliş normal değil" → trend ivmesi normaldir
   YASAKLI: "pahalı bölgeye girdi" / "aşırı kopmuş, geri gelmeli" → POC/VWAP "fair value" değil
   YASAKLI: "kurumsal maliyetten %X uzaklaşması düzeltme ihtiyacını fısıldıyor" → bu kalıbı KESİNLİKLE yazma
   YASAKLI: "Adil değerden saptı" → VWAP adil değer değildir, sadece referanstır
   YASAKLI: "RSI 70+ olduğu için satış geliyor" → güçlü trendde RSI haftalarca 70+ kalır, çelişki olmadıkça yazma
   YASAKLI: "Z-Score yüksek, çöküş yakın" → Z-Score sadece uzaklık ölçüsü, kehanet değil
   YASAKLI: "kâr almak düşünülebilir (yüksek uzaklık nedeniyle)" → bu öneriyi sadece OBV/Hacim/Delta divergence VARSA ver
   ━━━ DOĞRU DİL — ŞU KALIPLARI KULLAN ━━━
   DOĞRU: "VWAP geri çekilmede destek olabilir" (uzaklık → seviye fonksiyonu)
   DOĞRU: "Trend ivmesinin doğal sonucu" (uzaklık → momentum açıklaması)
   DOĞRU: "İzleyen stop yükseltme noktası" (uzaklık → risk yönetimi)
   DOĞRU: "OBV uyumsuzluğu olmadıkça düzeltme zorunluluğu yok" (uzaklık + çelişki testi)
3. HALKÇI STRATEJİST: En karmaşık kurumsal riski, kahvehanedeki adamın "Ha, şimdi anladım!" diyeceği kadar sade ama bir banka müdürünün ciddiyetini bozmadan anlat. Parantez içinde İngilizce terim bırakma, hepsini Türkçe'ye çevir.
4. TAVSİYE VERMEK YASAKTIR: "Alın, satın, tutun, kaçın, ekleyin" gibi yatırımcıyı doğrudan yönlendiren fiiller KULLANILAMAZ. 
5. ALGORİTMA REFERANSI: Algoritmadan gelen bulguları aktarırken "Sistemin ürettiği veriler" ifadesini kullanabilirsin — bu ifade algoritmamızın gücünü yansıtır ve abonelerde güven oluşturur. Ama her cümleyi bu kalıpla başlatma; analizin geri kalanı insan diliyle akmalı. YASAK: Her cümleyi "Sistemin ürettiği veriler gösteriyor ki..." ile açmak. OLMASI GEREKEN: Algoritmaya atıfta bulunduğun yerlerde kullan, diğer yorumlarında doğal konuş. "İstatistiksel durum", "Matematiksel sapma" gibi steril kalıpları kullanma — bunların yerine direkt veriyi söyle. ASLA parantez içinde İngilizce terim koyma, Türkçe terimler kullanarak sadeleştir. (mean reversion, accumulation, distribution, liquidity sweep gibi tüm ICT, Price Action, Teknik analiz terimlerini Türkçe'ye çevirerek kullan)
6. GELECEĞİ TAHMİN ETME: Gelecekte ne olacağını söyleme. Sadece "Mevcut verinin tarihsel olarak ne anlama geldiğini" ve "Risk/Ödül dengesinin nerede olduğunu" belirt.
Örnek Doğru Cümle: "Z-Score +2 seviyesinin aşıldığını gösteriyor. Algoritmik olarak bu bölgeler aşırı fiyatlanma alanları, yani düzeltme riski taşıyabilir."
Örnek Yanlış Cümle: "Z-Score +2 seviyesinin aşıldığını göstermektedir. Algoritmik olarak bu bölgeler aşırı fiyatlanma alanlarıdır ve düzeltme riski taşıyabilmektedir."
Özetle; Twitter için atılacak bi twit tarzında, aşırıya kaçmadan ve basit bir dilde yaz. Yatırımcıyı korkutmadan, umutlandırmadan, sadece mevcut durumun ne olduğunu ve hangi risklerin nerede olduğunu anlat.

*** Z-SCORE BAĞLAM REHBERİ (ZORUNLU OKUMA — SCAN KUTUSU "🚨 Z-SCORE ANOMALİSİ" GÖRSEN DAHİ) ***
Z-Score tek başına ne anlam taşır?
- Z > +2 = "Fiyat son 20 günlük ortalamasından 2 standart sapma uzakta" demektir. Sadece bir uzaklık ölçüsüdür, kehanete çevrilmez.
- Trend başlangıçlarında, güçlü kırılımlarda, kurumsal giriş anlarında Z > +2 BEKLENEN VE NORMAL bir olgudur.
  Örnek: Hisse 3 gündür yükseli̇yor → Z = +2.7 → Bu "tehlike" değil, "ivme" sinyalidir.

Z-Score'u SADECE şu iki koşulda öne al:
  a) OBV düşüyor VEYA hacim zayıf IKEN Z > +2 → Gerçek "Zayıf El Yükselişi" riski. Kısaca değin.
  b) Fiyat 30+ gündür durmadan yükseliyor VE kurumsal satış işaretleri de varsa → Yorgunluk notu düş.

Aksi tüm durumlarda: Scan kutusunda 🚨 Z-Score uyarısı görsen bile bunu analizinin ana teması yapma. Sadece "uzaklık verisi" olarak son paragrafa göm. Analizin hikayesi akıllı para, senaryo ve price action üzerine kurulu kalsın.

PRE-LAUNCH / FİTİL ÇEKİLİYOR durumu: Eğer KALKIŞ RADARI "FİTİL ÇEKİLİYOR" veya "⚡ LONG İÇİN HAZIR" statüsündeyse, bu analizin birincil hikayesi olmalıdır. Z-Score ne olursa olsun, birikim süreci tamamlanmış ve tetik bekleniyor demektir — bu bulguyu analizin en başına koy, Z-Score yorumunu ise ancak risk yönetimi notunda kısaca kullan.

ALTIN FIRSAT (Golden Trio) + Yüksek Z-Score bir arada: Bu durum "tehlike" değil "güçlü momentum + uzama" kombinasyonudur. Analizin tonu olumlu kalmalı; Z-Score'u "stop seviyesini yukarı taşı" notu olarak kullan, "dikkat et, çöküş gelebilir" panikâr diline çevirme.

*** POC / VWAP BAĞLAM REHBERİ (ZORUNLU OKUMA — Z-SCORE İLE AYNI MANTIK) ***
POC ve VWAP "fair value" (adil değer) DEĞİLDİR. Geçmiş hacim merkezi ve kurumsal execution ortalamasıdır. Tek başına alım/satım sinyali olarak ASLA kullanılmaz.

POC tek başına ne anlam taşır?
- POC = "Son 20 günde en çok hacim gören fiyat" — geçmiş arz/talep dengesinin tepe noktası
- Fiyatın POC üstünde olması = piyasa yeni denge arıyor (bullish auction, NORMAL bir durumdur)
- Fiyatın POC altında olması = eski denge çöküyor (bearish auction)
- POC'tan uzaklık MOMENTUM ölçer, "overvaluation" (aşırı pahalılık) DEĞİLDİR

VWAP tek başına ne anlam taşır?
- VWAP = Kurumsal execution benchmark (algo trading referansı)
- Trendde fiyatın VWAP üstünde kalması BEKLENEN durumdur, "pahalı" değildir
- VWAP'tan sapma = trend ivmesi göstergesi, "düzeltme ihtiyacı" değildir

YANLIŞ kullanım örnekleri (BU TARZ CÜMLELER KESİNLİKLE YASAK):
× "Fiyat POC'un %5 üstünde, pahalı, düzeltme gelebilir"
× "VWAP'tan koptu, mean reversion bekleniyor"
× "Kurumsal maliyet merkezinden %X uzaklaşması düzeltme ihtiyacı fısıldıyor"
× "Pahalı bölgeye girdi, geri gelme zorunluluğu var"

DOĞRU kullanım örnekleri:
✓ "Fiyat POC üzerinde — POC seviyesi olası geri çekilmede destek olabilir"
✓ "VWAP üstünde momentum sağlam — VWAP altına düşmedikçe trend yapısı bozulmaz"
✓ "VAH üstünde kapanış var, kurumsal alıcılar yeni denge arıyor"
✓ "Fiyat VWAP'tan %X sapmış — bu trend ivmesinin doğal sonucu, çelişki değil"

POC/VWAP uzaklığını "düzeltme tezini" ANCAK şu durumlarda kur (yani çelişki varsa):
  a) OBV düşüyor + RSI uyumsuzluk + POC üstünde → "yorgunluk emaresi" (kısa not, son paragraf)
  b) Yatay piyasa (range) içinde POC'tan +2 std sapma → mean reversion ihtimali konuşulabilir
  c) Stopping/Climax Volume + POC üstünde → kurumsal kar satışı sinyali olabilir
  d) Trend zaten çökmüş + fiyat POC'a dönüyor → eski denge testi

Aksi tüm durumlarda POC/VWAP'ı sadece SEVİYE olarak kullan, yön sinyali olarak değil.
"%5 uzak", "%10 uzak" gibi yüzdesel uzaklık TEK BAŞINA analiz argümanı OLAMAZ.
Bunlar ancak diğer çelişkilerle (OBV/RSI/Hacim divergence) BİRLİKTE değerlendirilirse anlamlıdır.

Trade plan oluştururken POC/VWAP'ı ŞÖYLE kullan:
- Giriş bölgesi: POC veya VWAP geri çekilmesinde re-test (level olarak)
- Stop seviyesi: VAL (Value Area Low) altı
- Hedef: VAH (Value Area High) veya bir önceki POC
Yön kararı için POC/VWAP DEĞİL → akıllı para hareketi (OBV, delta, kurumsal hacim) kullanılır.

═══════════════════════════════════════════════════════════════════════
🚫 KESİN YASAK CÜMLE KALIPLARI — MEAN REVERSION FALLACY (BU LİSTEYİ EZBERLE)
═══════════════════════════════════════════════════════════════════════
AŞAĞIDAKİ KALIPLARI HİÇBİR KOŞULDA KULLANMAYACAKSIN. Bu cümleleri yazarsan
analizin reddedilir. Eğer kullanmak üzereysen DUR ve şu kontrolü yap:
"Bu çıkarımı POC/VWAP/RSI/Z-Score uzaklığı ÜZERİNE Mİ kuruyorum, yoksa
OBV/Hacim/Delta çelişkisi gibi BAĞIMSIZ bir kanıt var mı?"
Bağımsız kanıt YOKSA → bu cümleyi yazma.

🚫 YASAK 1 — VWAP/POC distance'ı tek başına dönüş tetikleyicisi yapmak:
   × "Fiyat VWAP'tan %X uzaklaştı, düzeltme gelebilir"
   × "POC'un %X üstüne çıktı, pahalı bölgeye girdi"
   × "Kurumsal maliyetten uzaklaşması düzeltme ihtiyacı fısıldıyor"
   × "Adil değerden saptı, geri dönüş kaçınılmaz"
   × "Parabolik hareket sürdürülemez"
   × "Bu hızda yükseliş normal değil, kâr satışı yakın"
   ✓ DOĞRUSU: "Fiyat VWAP üzerinde — VWAP geri çekilmede destek seviyesi olabilir"
   ✓ DOĞRUSU: "POC'tan %X uzakta — bu trend ivmesinin doğal sonucu"

🚫 YASAK 2 — RSI overbought/oversold'u tek başına dönüş tetikleyicisi yapmak:
   × "RSI 75'te, aşırı alım, düzeltme yakın"
   × "RSI 25'te, aşırı satım, dönüş zamanı"
   × "Momentum tepe yapmış, satış geliyor"
   ✓ DOĞRUSU: "RSI 75 — güçlü trendde RSI haftalarca 70+ kalabilir, OBV/hacim çelişkisi olmadıkça düzeltme zorunluluğu yok"
   ✓ DOĞRUSU: "RSI 25 — düşüş hız kaybediyor olabilir; pozitif divergence + hacim teyidi ile alım fırsatı dönüşebilir"

🚫 YASAK 3 — Z-Score'u tek başına çöküş/dönüş tetikleyicisi yapmak:
   × "Z-Score +2.5'te, çöküş geliyor"
   × "Standart sapmalardan kopmuş, geri gelmeli"
   ✓ DOĞRUSU: "Z-Score +2.5 — trend ivmesinin doğal ölçüsü; izleyen stop yükseltme noktası, ama tek başına çıkış sinyali değil"

🚫 YASAK 4 — "Pahalı/Ucuz" yargısını yüzdelik uzaklık üzerine kurmak:
   × "%5 yukarıda → pahalı bölge"
   × "%3 altında → ucuz, alım fırsatı"
   ✓ DOĞRUSU: Konum bilgisi olarak "fiyat X seviyesinin üzerinde/altında" — yargı yok, seviye var.

🚫 YASAK 5 — Mean reversion'ı bağımsız kanıt olmadan kullanmak:
   × "Geri gelmesi lazım", "düzeltmesi gerekiyor", "ortalamaya dönmeli"
   × "Bu hareket kalıcı değil"
   × "Geri çekilme kaçınılmaz"
   Mean reversion'dan SADECE şu durumlarda bahset (ve "kesin" değil "ihtimal" diliyle):
     a) OBV/Delta divergence + uzaklık birlikte → "yorgunluk emaresi olabilir"
     b) Yatay piyasa içinde +2 std → "range içinde mean reversion ihtimali artıyor"
     c) Stopping/Climax Volume + uzaklık → "kurumsal kar satışı belirebilir"
     d) Trend zaten kırılmış + uzaklık daralıyor → "eski denge testi"

⚠️ ÖZ-DENETLEME — ANALİZİ TAMAMLAMADAN ÖNCE ŞU 3 SORUYU CEVAPLA:
   1. "Düzeltme yakın / pahalı / aşırı uzak" tarzı cümle yazdım mı? → Evet ise SİL veya bağımsız kanıt ekle.
   2. POC/VWAP/RSI/Z-Score uzaklığını TEK BAŞINA argüman olarak kullandım mı? → Evet ise OBV/Hacim/Delta ile teyit et veya konum bilgisine indir.
   3. "Sürdürülemez", "geri gelmeli", "ortalamaya döner" gibi mean reversion ifadesi kullandım mı? → Evet ise yukarıdaki 4 koşuldan birinin sağlandığını doğrula.

Bu kuralların ihlali = analizinin profesyonelliğinin sıfırlanması demektir.
═══════════════════════════════════════════════════════════════════════

*** YANILTICI VERİ TUZAKLARI — BUNLARI YANLIŞ OKUMA ***
Aşağıdaki veriler trendin yan ürünüdür, trendin kendisi değildir. Hisse yükseliyorsa bu verileri tehlike olarak çerçeveleme:
→ Z-Score yüksekliği → Yükselen bir hissede Z-Score'un +2 veya üzerine çıkması normaldir. "Yükseldi ama Z-Score tehlikeli" deme. Sadece "bu seviyede izleyen stop mantıklı olabilir" diyebilirsin.
→ VWAP sapması → Ralli yapan hissede fiyatın VWAP'tan uzaklaşması ivmenin sonucudur. "VWAP'tan çok koptu, düzeltme gelebilir" yerine "VWAP bu noktada olası bir geri çekilmede destek olabilir" de.
→ RSI aşırı alım → Güçlü trendlerde RSI haftalarca 70 üzerinde kalabilir. RSI'ı tek başına uyarı olarak öne çıkarma; OBV veya hacimle çelişmiyorsa dipnot geç.

Gerçek çelişki bunlardır — bunları mutlaka belirt ama "yükseldik, şuna da dikkat edelim" tonuyla:
→ Fiyat yukarı giderken OBV aşağı (gizli dağıtım olabilir)
→ Hacim düşerken fiyat yükseliyor (zayıf el yükselişi)
→ HARSI kırmızıyken fiyat tavan yapıyor (momentum tükenebilir)
→ Stopping Volume veya Climax Volume tespit edilmişse (dönüş ihtimali artar)
Bu çelişkiler varsa tek bir paragrafta "yükseliş devam ederken şunu da gözden kaçırmayalım" şeklinde sun — analizin merkezine alma.

Şimdi, kendi kimliğini iyice tanıdığına göre, analizinin için gerekli veriler aşağıda.  Verileri yorumlarken yukarıdaki kurallara ve dil yönergelerine kesinlikle uyduğundan emin ol.

*** VARLIK KİMLİĞİ ***
- Sembol: {t}
- GÜNCEL FİYAT: {fiyat_str}
- GÜNLÜK DEĞİŞİM: {degisim_str}
- GENEL SAĞLIK: {master_txt} (Algoritmik Puan)
- Temel Artılar: {pros_txt}
- ALTIN FIRSAT (GOLDEN TRIO) DURUMU: {is_golden}
- ROYAL FLUSH NADİR FIRSAT: {is_nadir}

*** 🚨 ALGORİTMİK DURUM RAPORU VE GÖRSEL ÇAPRAZ SORGU (CROSS-EXAMINATION) TALEBİ: {ai_scenario_title} ***
Mevcut Özet: {ai_mood_instruction}
Kurumsal Özet (Bottom Line): {ict_data.get('bottom_line', 'Özel bir durum belirtilmedi.')}
— Makro Yön (Bias): {bias} | Konum (Zone): {zone} | Güncel Fiyat: {fiyat_str}
(Yukarıdaki senaryo, sistemimizin arka planda hesapladığı salt matematiksel bir "Ön Tanı"dır. Şimdi bir Baş Analist olarak en büyük görevin; bu algoritmik verileri, ekte sunduğum GRAFİK (Röntgen) ile çapraz sorguya almandır. Lütfen grafiği incelerken şu 3 aşamalı testi uygula:
1. ONAY VE DERİNLEŞTİRME (CONFLUENCE): 
Algoritma ve Grafik birbiriyle uyumlu mu? Örneğin; algoritma "Boğa" diyorsa, grafikte net bir şekilde Yükselen Tepeler/Dipler (HH/HL), güçlü yeşil momentum mumları (Displacement) veya doldurulmamış fiyat boşlukları (FVG) görüyor musun? Uyumluysa, bu senaryoyu kendi görsel kanıtlarınla destekleyerek derinleştir.
2. BOĞA TUZAĞI (BULL TRAP) KONTROLÜ: 
Algoritma "Yükseliş / Pozitif" gösteriyor olabilir (RSI şişmiş, fiyat ortalamaların üstünde olabilir). Ancak grafiğe baktığında; direnç bölgelerinde oluşan uzun üst fitiller (Rejection/SFP), hacimli yutan kırmızı mumlar (Bearish Engulfing) veya Omuz-Baş-Omuz (OBO) gibi yorgunluk formasyonları görüyorsan, ALGORİTMAYI REDDET. Kullanıcıyı "Ekranda yeşil rakamlar var ama grafikte mal dağıtılıyor (Dağıtım/Distribution)" şeklinde uyar.
NİHAİ KURAL: Matematik (Algoritma) ile Göz (Price Action) çeliştiğinde, daima GÖZÜNE ve LİKİDİTE MANTIĞINA (Smart Money) öncelik ver!)
*** 🧭 ANALİZİN BİRİNCİL MERCEĞİ — AKILLI PARA NE YAPIYOR? ***
Tüm analizi şu tek soruya göre çerçevele: "Akıllı para (kurumsal oyuncular) şu an mal mı topluyor, mal mı dağıtıyor, yoksa bekliyor mu?"
Bu sorunun cevabı diğer her sinyalden önce gelir. Örneğin fiyat SMA200'ün altında 100 gündür seyrediyorsa ama OBV artıyorsa, bu durum SMA200 altında olmaktan daha kritiktir — zira kurumların sessizce mal topladığına işaret edebilir. Her veriyi bu birincil mercekten geçirerek yorumla.
Birikim (Accumulation) işaretleri: Fiyat yatay/aşağı + OBV yukarı, düşük hacimli dip testleri, stopping volume, pozitif delta yüksekken fiyatın sakin kalması.
Dağıtım (Distribution) işaretleri: Fiyat yukarı + OBV aşağı, yüksek hacimli tepki satışları, climax volume, negatif delta yüksekken fiyatın sahte yükselmesi.

*** PİYASA FAZI VE ALGORİTMİK KANAAT (REJİM + CONVICTION) ***
(Bu iki veri, sistemin tüm sinyalleri sentezleyerek ürettiği makro çerçevedir. Analizinde bu çerçeveyi arka plan bağlamı olarak kullan; sinyalleri bu faza göre ağırlıklandır.)
- Piyasa Yapısı (20-200 Gün — SMA50/200 bazlı): {_regime_prompt_str}
- Algoritmik Kanaat Skoru: {_conviction_prompt_str}
Kural: Faz 3 (Dağıtım) veya Faz 4 (Düşüş) iken gelen AL sinyallerini "karşı-trend" olarak işaretle ve riskini vurgula. Faz 2 (Yükseliş) iken SAT sinyali varsa aynı şekilde dikkat çek. Kanaat skoru 45 altında (SHORT/GÜÇLÜ SHORT) iken pozitif senaryo çiziyorsan bunu açıkça "algoritmanın genel eğilimiyle çelişiyor" diye belirt.

*** AKILLI PARA HAZIRLIK SKORU (5 KRİTERLİ ALGORİTMİK ANALİZ) ***
{_sms_str}

*** CANLI TARAMA SONUÇLARI (SİNYAL KUTUSU) ***
(Burası sistemin tespit ettiği en sıcak sinyallerdir, )
{scan_summary_str}
Eğer sinyal kutusunda "AKILLI PARA BİRİKİMİ" sinyali varsa: Bu sinyalin ne anlama geldiğini aboneye düz dille açıkla (Force Index, fiyat yataylığı), ardından ICT bölgesi (OB/FVG/bias) ile bağla — "kurumsal birikim + ICT alım bölgesi çakışması" varsa bunu öne çıkar.
Eğer Akıllı Para Hazırlık Skoru 65 veya üzerindeyse, bunu analizinin içine doğal bir şekilde eriştir — "sistem bu hissenin kırılım hazırlığında olduğuna dair güçlü sinyaller veriyor" gibi bir bağlam kur.

*** KURUMSAL PARA İŞTAHI KARNESİ (Detaylı Puanlar) Ama bunların GECİKMELİ VERİLER olduğunu unutma. Analize ekleyeceksen 'son kaç günün verileri' olduğunu muhakkak belirt***
- YAPI (Structure): {sent_yapi} (Market yapısı puanları şöyle: Son 20 günün %97-100 zirvesinde (12). Son 5 günün en düşük seviyesi, önceki 20 günün en düşük seviyesinden yukarıdaysa: HL (8))
- HACİM (Volume): {sent_hacim} (Hacmin 20G ortalamaya oranını ve On-Balance Volume (OBV) denetler. Bugünün hacmi son 20G ort.üstünde (12) Para girişi var: 10G ortalamanın üstünde (8))
- TREND: {sent_trend} (Ortalamalara bakar. Hisse fiyatı SMA200 üstünde (8). EMA20 üstünde (8). Kısa vadeli ortalama, orta vadeli ortalamanın üzerinde, yani EMA20 > SMA50 (4))
- MOMENTUM: {sent_mom} (RSI ve MACD ile itki gücünü ölçer. 50 üstü RSI (5) RSI ivmesi artıyor (5). MACD sinyal çizgisi üstünde (5))
- VOLATİLİTE: {sent_vola} (Bollinger Bant genişliğini inceler. Bant genişliği son 20G ortalamasından dar (10))
- MOMENTUM DURUMU (Özel Sinyal): {momentum_analiz_txt} (Hissenin Endekse göre relatif gücünü (RS) ölçer. Mansfield RS göstergesi 0'ın üzerinde (5). RS trendi son 5 güne göre yükselişte (5). Endeks düşerken hisse artıda (Alpha) (5))
*** GELİŞMİŞ MOMENTUM VE FİYAT DENGESİ (GRAFİK VERİLERİ) ***
- Para Akış İvmesi Değeri: {guncel_ivme:.4f} ({ivme_yonu}) -> (Not: Bu değer 0'ın ne kadar üzerindeyse kurumsal momentum o kadar tazedir.. Tam tersi durum için ise durum kötüdür)
- Fiyat Dengesi (Denge Seviyesi): {guncel_stp:.2f}
- Fiyat/Denge Sapması: %{denge_sapmasi:.2f} -> (Not: Eğer fiyat sarı denge çizgisinden (STP) %5'ten fazla uzaklaşmışsa 'anormalleşme' uyarısı yap.)
*** ALGORİTMİK 8 MADDELİK LABORATUVAR VERİSİ ***
(Aşağıdaki 8 madde, sistemin fiyat, hacim ve volatiliteyi matematiksel olarak hesapladığı ham verilerdir. 3. Görevindeki Teknik Kartı doldururken BİREBİR bu verileri kullan.)
{roadmap_ai_txt}
*** 1. TREND VE GÜÇ ***
KISA VADELİ TREND GÖSTERGELERİ:
- HARSI Durumu (Heikin Ashi RSI): {harsi_txt} (Bu veri, son 14 günlük hafızayı kullanır; RSI üzerindeki gürültüyü temizleyerek, momentumun mevcut trend yönünü ve kalıcılığını ölçer.)
- EMA Durumu (8/13): {ema_txt} (Gün sayısına çok dikkat et! Fiyat günlerdir bu kısa vadeli EMA'ların altındaysa bu devam eden bir 'Kurumsal Satış Baskısı' olabilir. Üstündeyse güçlü bir 'Momentum Rallisi' gibi görünmektedir. HARSI de negatifse kısa vadeli trend tamamen kırılmış olabilir.)
ORTA VADELİ TEKNİK GÖSTERGELER ve KURUMSAL SEVİYELER:
- SuperTrend (son 60 günlük Yön): {st_txt}
- Minervini Durumu: {mini_txt}
HAREKETLİ ORTALAMALAR VE KURUMSAL SEVİYELER (ZAMAN ANALİZİ):
- SMA50 Durumu (Orta Vade): {sma50_str} (Seviye: {sma50_val:.2f}) -> (Fiyatın kaç gündür üstünde/altında olduğu trendin olgunluğunu gösterir.)
- SMA200 Durumu (Makro Trend): {sma200_str} (Seviye: {sma200_val:.2f})
- SMA 100 (Ara Destek/Direnç): {sma100_val:.2f}
- EMA 144 (Fibonacci/Robotik Seviye): {ema144_val:.2f}
Son birkaç gündür bu hareketli ortalamalardan en az birinden tepki alıp almadığını incele. Bu desteklerin/dirençlerin tamamı Kurumsal yatırımcıların yakından takip ettiği kritik seviyelerdir. Eğer fiyat bu seviyelerden tepki alıyorsa, ya da bu hareketli ortalamaların civarında bir süredir takılıyorsa, bu seviyelerin geçerliliği ve gücü hakkında yorum yap.
- RADAR 1 (Momentum/Hacim): {r1_txt}
- RADAR 2 (Trend/Setup): {r2_txt}
Kısa vadeli momentumun (HARSI/EMA8), ana trend (SMA200/SuperTrend) ile uyumunu kontrol et. Eğer kısa vadeli sinyal ana trendin tersineyse, bunu bir 'Trend Dönüş Başlangıcı' mı yoksa 'Yüksek Riskli Bir Tepki Yükselişi' mi olduğunu netleştir.
*** 2. PRICE ACTION / ARZ-TALEP BÖLGELERİ / SMART MONEY LİKİDİTE & ICT YAPISI ***
{_pa_priority_str}
- ⚡ ANLIK DÖNÜŞ SİNYALİ (Price Action V-Dönüşü): {pa_signal} -> (Eğer Bullish ise dipten, Bearish ise tepeden anlık dönüş var demektir. Nötr ise hareket yoktur.)
- 🎯 DÖNÜŞÜN GELDİĞİ YER (Confluence): {pa_context} -> (Yukarıdaki dönüş sinyali Nötr değilse, fiyatın tam olarak hangi kurumsal destek/dirençten döndüğünü gösterir. Analizinde bu seviyenin gücünü mutlaka vurgula!)
- 🔄 ICT YAPI KIRILIMI (Trend Dönüşü - MSS): {reversal_signal} -> (Eğer Bullish_MSS veya Bearish_MSS yazıyorsa, piyasanın ana yönü az önce kırıldı demektir. Bu en güçlü trend dönüş sinyallerinden biridir, analizinin en başına koy!)
- MAKRO YÖN (Bias): {bias} -> (AI DİKKAT: Eğilimin Boğa mı Ayı mı olduğunu gösterir. Analizlerini her zaman bu ana trend yönüyle uyumlu yap.)
- KONUM (Zone): {zone} -> (AI DİKKAT: Eğer konum 'Discount' ise fiyatın ucuzladığını ve Smart Money alım bölgesi olabileceğini; 'Premium' ise fiyatın şiştiğini ve kar satışı / dağıtım (Distribution) riski taşıdığını mutlaka yorumlarına kat.)
- Market Yapısı (Structure): {ict_data.get('structure', 'Bilinmiyor')}
- LİKİDİTE HAVUZLARI (Mıknatıs): {havuz_ai} (Eğer veri varsa, bu likidite havuzlarının fiyatın hangi seviyelerinde olduğunu ve bu seviyelerin neden önemli olduğunu yorumla. Akıllı Para'nın bu havuzları nasıl kullanabileceğini, örneğin stopları temizleyip (Sweep) yukarı yönlü hareket için bir sıçrama tahtası olarak kullanabileceğini açıklamaya çalış.)
- LİKİDİTE AVI (Sweep/Silkeleme): {sweep_ai}
Likidite havuzlarına bakarak, küçük yatırımcıların nerede 'terste kalmış' olabileceğini ve Akıllı Para'nın bu likiditeyi nasıl kullanmak isteyebileceğini yorumla
- Balina Ayak İzi (Taze Arz-Talep Bölgesi): {sd_txt_ai}
- Kısa Vadeli Trend Hassasiyeti (10G WMA): {para_akisi_txt} (Son günlerin fiyat hareketine daha fazla ağırlık vererek, trenddeki taze değişimleri ölçer.)
- Aktif FVG: {ict_data.get('fvg_txt', 'Yok')}
- Aktif Order Block: {ict_data.get('ob_txt', 'Yok')}
- HEDEF LİKİDİTE (Mıknatıs): {ict_data.get('target', 0)}
- Mum Formasyonu: {mum_desc}
- Formasyon Güvenilirliği: {confidence_prompt if confidence_prompt else "Skor hesaplanamadı (nötr veya belirsiz formasyon)"}
- RSI Uyumsuzluğu: {pa_div} (Varsa çok dikkat et!)
- TUZAK DURUMU (SFP): {sfp_desc}
- HARMONİK FORMASYON (XABCD Fibonacci): {harm_txt} (Varsa: harmonik PRZ + ICT bölgesi çakışmasını özellikle vurgula)
- NİHAİ KARAR VE AKSİYON PLANI (THE BOTTOM LINE): {ict_data.get('bottom_line', 'Veri Yok')}
*** 3. ALGORİTMİK ANALİZ STANDARTLARI (BİLGİ NOTU) ***
Analiz yaparken algoritmamızın şu katı kuralları uyguladığını bil ve yorumlarını bu temel üzerine inşa et:
1. MUM FORMASYONLARI (H1/L1 Hassasiyeti): 
   - "Three Inside Up/Down" tespit edilmişse; bu, 3. mumun 1. mumun tepesini (H1) veya dibini (L1) resmen kırdığı ve dönüşün onaylandığı anlamına gelir.
   - "Morning Star" tespit edilmişse; ortadaki yıldızın 1. mumun kapanış seviyesinin (C1) altında oluştuğu (Gap/Aşağı sarkma) ve 3. mumun güçlü bir geri dönüş yaptığı teyit edilmiştir.
2. DOW TEORİSİ VE TREND ZİNCİRİ:
   - Eğer "Yükselen Dip (HL)" etiketi varsa; fiyatın son 15 günlük en düşük seviyesini koruduğu ve yapısal olarak güçlendiği kesinleşmiştir.
   - Eğer "Yeni Dip (LL)" uyarısı varsa; dönüş formasyonu gelse bile fiyatın yapısal olarak yeni bir düşük yaptığı, bu yüzden bu dönüşün "Riskli bir tepki" olduğu vurgulanmalıdır.
3. SMART MONEY (ICT) KESİŞİMİ:
   - "Confluence (Kesişim)" alanı; PA formasyonunun tesadüfen değil, tam olarak SMA, Fibonacci OTE veya PDH/PDL gibi kurumsal bir seviyeden sektiğini gösterir. Bu durum sinyalin güvenilirliğini %80 artırır.
*** 4. HEDEFLER VE RİSK ***
- Direnç (Hedef): {fib_res}
- Destek (Stop): {fib_sup}
- Hedef Likidite: {liq_str}
- İptal Seviyesi (Invalidation Point): Bu teknik tezin (Boğa/Ayı) tamamen çökeceği, piyasanın 'yanıldık' diyeceği o kritik likidite seviyesi veya yapı kırılımı (BOS) noktası neresidir? Tüm verilere bakarak net bir fiyat seviyesi olarak belirle.
*** 5. EK TEKNİK VERİLER (SMART MONEY METRİKLERİ) ***
- Bugüne ait Smart Money Hacim Durumu: {delta_durumu}
- POC Alanı — Son 20 Günlük Hacim Ağırlıklı Kontrol Noktası: {poc_price}
- POC Alanı Konumu (VAH Üst Sınır / VAL Alt Sınır): {va_pos_txt} | VAH: {vah_txt} | VAL: {val_txt}
- 5 Seans Kümülatif Delta: {cum5_txt}
- Güncel Fiyat: {guncel_fiyat}
- Fiyat POC Alanının altındaysa bunun bir "Ucuzluk" (Discount) bölgesi mi yoksa "Düşüş Trendi" onayı mı olduğunu yorumla. Fiyat POC üzerindeyse bir "Pahalı" (Premium) bölge riski var mı, değerlendir.
- Bugüne ait Smart Money Hacim Durumundaki "Bugüne ait Net Baskınlık" yüzdesine dikkat et! Eğer bu oran %40'ın üzerindeyse, tahtada bugün için ciddi bir "Smart Money (Balina/Kurumsal)" müdahalesi olabileceğini belirt.
-"Net Baskınlık" sadece bugüne ait veridir, bunu unutma. Fiyat hareketi arasında bir uyumsuzluk var mı kontrol et. Fiyat artarken bugüne ait Net Baskınlık EKSİ (-) yönde yüksekse, "Tepeden mal dağıtımı (Distribution) yapılıyor olabilir, Boğa Tuzağı riski yüksek!" şeklinde kullanıcıyı uyar. Ama bu durumum bugün için geçerli olabileceğini, yarın her şeyin değişebileceğini unutmadan yorumla. Verininsadece bugünün durumunu yansıttığını hatırlat.
Veriler arasındaki uyumu (Confluence) ve çelişkiyi (Divergence) sorgula. Eğer Momentum (RSI/MACD) yükselirken Akıllı Para Hacmi (Delta) düşüyorsa, bunu 'Zayıf El Alımı' olarak işaretleyebilirsin. Fiyat VWAP'tan çok uzaksa (Parabolik), Golden Trio olsa bile kurumsalın küçük yatırımcıyı 'Çıkış Likiditesi' (Exit Liquidity) olarak kullanıp kullanmadığını dürüstçe değerlendir.
*** AKILLI PARA HACİM ANOMALİLERİ ***
- 20 Günlük Ortalamaya Göre Hacim (RVOL): {"VERİ EKSİK — bu dönem için kaynak hacim verisi yok, RVOL hesaplanamadı; hacim bazlı yorum yapma." if _vol_missing_flag else f"{rvol_val}x (1.0 = normal, 2.0+ = kurumsal aktivite, 0.5 altı = ilgisiz piyasa)"}
- Stopping Volume (Frenleme): {stop_vol_val}
- Climax Volume (Tahliye): {climax_vol_val}
{"" if _vol_missing_flag else "RVOL 2.0x üzerindeyken fiyatın hareket etmemesi (Churning) bir dağıtım (Distribution) sinyali olması ihtimalini gösterir; RVOL yüksekken bir kırılım gelmesi ise gerçek bir kurumsal katılımdır. Bu ikisi arasındaki farkı mutlaka analiz et.\nHacim artarken (RVOL > 1.5x) fiyatın dar bir bantta kalması 'Sessiz Birikim' veya 'Dağıtım' olabilir. Hacim düşerken (RVOL < 0.8x) fiyatın yükselmesi 'Zayıf El Yükselişi'dir. Bu uyumsuzlukları mutlaka vurgula."}
*** 6. KURUMSAL REFERANS MALİYETİ VE ALPHA GÜCÜ ***
- VWAP: {v_val:.2f} (Hacim ağırlıklı ortalama fiyat — kurumsal execution benchmark'ı. "Adil değer" DEĞİL, sadece bir referans seviyedir; trendde fiyatın bu seviyeden uzaklaşması beklenen durumdur.)
- Fiyat Konumu: VWAP'ın %{v_diff:.1f} üzerinde/altında (Bu sadece konum bilgisi — yön sinyali değildir; tek başına alım/satım kararına çevirme).
- VWAP DURUMU: {vwap_ai_txt} (Bağlamsal etiket — fiyatın VWAP'a göre konumunu gösterir, sinyal değil. POC/VWAP Bağlam Rehberi'ne göre yorumla.)
- RS (Piyasa Gücü): {rs_ai_txt} (Alpha: {alpha_val:.1f}) (Hissenin endeksten ayrışma gücü; pozitif Alpha, piyasa düşerken bile ayakta kalan lider hisseyi gösterir.)
(NOT: VWAP'tan uzaklığı TEK BAŞINA "düzeltme/dönüş" tezi olarak ASLA kullanma. "VWAP'tan AŞIRI UZAK" etiketi sadece momentum ölçüsüdür; OBV/Hacim çelişkisi olmadıkça yorgunluk demek değildir. "VWAP ÜSTÜNDE" ise trendin sağlam olduğunu gösterir, "Ralli sürer mi?" değerlendirmesi için akıllı para hareketine bak.)

⚠️ KRİTİK EMİR — VWAP/POC YORUMLAMA KURALI ⚠️
Bu blokta verilen "{vwap_ai_txt}" etiketini ve %{v_diff:.1f} uzaklığı bir analiz cümlesinin TEK GEREKÇESİ olarak ASLA kullanmayacaksın. Aşağıdaki cümleler KESİNLİKLE YASAKTIR — yazarsan kuralı ihlal etmiş olursun:
   ❌ "Fiyat VWAP'tan %X uzaklaştığı için düzeltme yakın"
   ❌ "VWAP'a göre pahalı bölgede"
   ❌ "Kurumsal maliyetten kopması düzeltme ihtiyacı doğuruyor"
   ❌ "Adil değerden saptı, geri gelmeli"

VWAP/POC uzaklığını ANCAK şu durumlarda mean-reversion bağlamında yorumlayabilirsin:
   ✓ OBV düşüyor + uzaklık birlikte → "yorgunluk emaresi olabilir" (kesin değil, ihtimal)
   ✓ Stopping/Climax Volume + uzaklık → "kurumsal kar satışı belirebilir"
   ✓ Yatay piyasada +2 std → "range içinde mean reversion ihtimali"
Bunlar dışında VWAP/POC sadece SEVİYE bilgisidir; analizin diğer bölümlerinde "destek/direnç", "stop seviyesi", "geri çekilme bölgesi" olarak kullan.

EĞER analizin sonunda yukarıdaki yasak cümlelerden BİRİNİ yazdıysan, geri dön ve sil — yerine OBV/Delta/Hacim çelişkisi VARsa onunla birlikte yaz, YOKsa cümleyi tamamen çıkar.

*** SIFIRINCI GÖREV (ZORUNLU — EN BAŞA YAZ, SONRA DİĞERLERİNE GEÇ) ***
"KURAL: Kancayı (Hook) bir tahmine değil, bir çelişkiyi teşhis etmeye dayandır. Zeki takipçi tahmine değil, analitik tespite güvenir.
MESELA YASAK: 'X hissesi buradan dönebilir' (Bu ucuzdur).
MESELA OLMASI GEREKEN: 'Fiyat %3 düşerken kurumsal alış hacmi neden zirve yapıyor? Birileri sessizce mal mı topluyor?' (Bu bir mantık bilmecesidir)."
Algoritmamızın senaryo tespitinden üretilen temel başlık: {hook_baslik}
Bu başlığı esas al. Analizindeki EN KRİTİK veya EN ŞOK EDİCİ TEK BULGUYA dayanan özelleştirilmiş bir hook başlığı üret.
Format: [EMOJİ] #{clean_ticker} {fiyat_str} ({degisim_str}) | [SENARYO]: [GERİLİM CÜMLESİ — max 8 kelime] 👇📸
Kural: "ANCAK", "ama", "oysa", "peki" veya "?" kelimelerinden en az biri cümlede olmalı. "ANALİZİ", "RAPORU" gibi jenerik kelimeler yasak.
Örnekler:
  🐳 #THYAO 327.50 (-1.2%) | TOPLAMA BÖLGESİ: OBV yükseliyor, fiyat neden düşüyor? 👇📸
  🔥 #SISE 48.20 (+3.1%) | AŞIRI ISINMA: Z-Score +2.7 — ama kurumsal alım devam ediyor? 👇📸
  🎯 #EREGL 140.00 (+0.5%) | KONSOLİDASYON: 3 aydır aynı bant — kırılım bu sefer gerçek mi? 👇📸
  🐳 #THYAO 327.50 | HERKES ALIYOR: Fiyat yükseliyor ama para akışı neden zayıf? 👇📸
  🔥 #SISE 48.20 | ANALİZ: Kurumlar mal mı boşaltıyor yoksa silkeleme mi yapılıyor? 👇📸
  🎯 #EREGL 140.00 | KRİTİK SEVİYE: Robotlar burada neyi bekliyor olabilir? 👇📸

⚡ ALGORİTMA UYUM ZORUNLULUĞU (X / Twitter):
Bu başlık X algoritması tarafından ilk 30 dakikada engagement velocity ile değerlendirilir.
Yaşaması için SAVE (kaydet=25 puan) + REPLY (yorum=13.5 puan) tetiklemesi şart.
Like (1 puan) tek başına tweet'i gömer. Hook'u yazarken "Birisi bunu kaydetmek/yorum yapmak ister mi?" sorusunu kendine sor.
Detaylı algoritma kuralları için aşağıda Dördüncü Görev'deki "X / TWITTER ALGORİTMA STRATEJİSİ" bölümüne uy:
   - Somut SEVİYE (save tetikler) + AÇIK UÇLU çelişki/soru (reply tetikler) birlikte olsun
   - Closed-end soru ("yükselir mi?") YASAK → açık-uçlu ("hangi senaryo?") OK
   - Max 2 hashtag, ilk satırda URL yok, "ALACAĞIM!" gibi promo dili yok
   - 180-220 karakter ideal, max 280

Bu başlığı "📌 " ile işaretle. Sonra diğer görevlere geç.

*** BEŞ GÖREVİN VAR ***

* Birinci Görevin;
Tüm bu teknik verileri Linda Raschke’nin profesyonel soğukkanlılığıyla sentezleyip, Lance Beggs’in ‘Stratejik Price Action’ ve ‘Yatırımcı Psikolojisi’ odaklı bakış açısıyla yorumlamaktır. Asla tavsiye verme (bekle, al, sat, tut vs deme), sadece olasılıkları belirt. "etmeli" "yapmalı" gibi emir kipleri ile konuşma. "edilebilir" "yapılabilir" gibi konuş. Asla keskin konuşma. "en yüksek", "en kötü", "en sert", "çok", "büyük", "küçük", "dev", "keskin", "sert" gibi aşırılık ifade eden kelimelerden uzak dur. Bizim işimiz basitçe olasılıkları sıralamak.
Analizini yaparken karmaşık finans jargonundan kaçın; mümkün olduğunca Türkçe terimler kullanarak sade ve anlaşılır bir dille konuş. Verilerin neden önemli olduğunu, birbirleriyle nasıl etkileşime girebileceğini ve bu durumun yatırımcı psikolojisi üzerinde nasıl bir etkisi olabileceğini açıklamaya çalış. Unutma, geleceği kimse bilemez, bu sadece olasılıkların bir değerlendirmesidir.
Teknik terimleri zorunda kalırsan sadece ilk geçtiği yerde kısaltmasıyla ver, sonraki anlatımlarda akıcılığı bozmamak için sadeleştir.
Analizinde küçük yatırımcı psikolojisi ile kurumsal niyet arasındaki farka odaklan. Verilerdeki anormallikleri birer ipucu olarak kabul et ve bu ipuçlarını birleştirerek piyasa yapıcının olası hamlesini değerlendir.
Bir veri noktası ‘Bilinmiyor’ gelirse onu yok say, ancak eldeki verilerle bir olasılık tablosu kur. Asla tek yönlü (sadece olumlu) bir tablo çizme; madalyonun öte yüzünü her zaman göster. Savunma mekanizman ‘analizi haklı çıkarmak’ değil, ‘riski bulmak’ olsun.
Herhangi bir veri alanı boş veya süslü parantez içinde {...} şeklinde ham halde gelmişse, o verinin teknik bir arıza nedeniyle okunamadığını varsay ve mevcut diğer verilerle analizi tamamla. Asla "Veri Yok" veya "Bilinmiyor" yazan bir alanı yorumlamaya zorlama, sadece mevcut verilerle en iyi sentezi yapmaya çalış.
En başa "{hook_baslik}" başlığı at. Sonra analizine o günün en baskın bulgusuyla başla — başlık değil, direkt cümle. Okuyucu ilk satırda ne olduğunu anlasın. Bu giriş 4-5 cümlelik akıcı bir paragraf olsun; "YÖNETİCİ ÖZETİ" gibi bir etiket koyma.
Referans ton — YASAK: "Söz konusu teknik tablo incelendiğinde, momentumun zayıfladığı görülmektedir." OLMASI GEREKEN: "OBV yükselirken fiyat aynı yerde sayıyor — bu tablo genelde kurumsal toplama öncesi görülür, ama dikkatli olmak gerek."
{genel_analiz_baslik}:
   - Yukarıdaki verilerden SADECE EN KRİTİK OLANLARI seçerek maksimum 6 maddelik bir liste oluştur. Zorlama madde ekleme! 2 kritik sinyal varsa 2 madde yaz.
   - SIRALAMA KURALI (BU KURAL ÖNEMLİ): Maddeleri "Önem Derecesine" göre azalan şekilde sırala. Düzyazı halinde yapma; Her madde için paragraf aç. Önce olumlu olanları sırala; en çok olumlu’dan en az olumlu’ya doğru sırala. Sonra da olumsuz olanları sırala; en çok olumsuz’dan en az olumsuz’a doğru sırala. Olumsuz olanları sıralamadan evvel şu geçişi kullan: "Tablonun parlak tarafı bu. Ama sahneyi tamamlamak için arka plandaki ağırlıklara da bakmak gerekiyor:" — "Öte Yandan;" gibi sert bir kopuş değil, okuyucuyu doğal olarak oraya taşı. Otoriter yazma. Geleceği kimse bilemez.
   - SIRALAMA KURALI DEVAMI: Her maddeyi 3 cümle ile yorumla ve yorumlarken; o verinin neden önemli olduğunu (8/10) gibi puanla ve finansal bir dille açıkla. Olumlu maddelerin başına "✅" ve verdiğin puanı, olumsuz/nötr maddelerin başına " 📍 " ve verdiğin puanı koy. (Örnek Başlık: "📍 (8/10) Momentum Kaybı ve HARSI Zayıflığı:") Olumlu maddeleri alt alta, Olumsuz maddeleri de alt alta yaz. Sırayı asla karıştırma. (Yani bir olumlu bir olumsuz madde yazma)
   - AKIŞ KURALI (BU KURAL KRİTİK): Her maddeyi birbirinden kopuk bağımsız bir kutu gibi yazma. Her madde bir öncekinin üzerine inşa edilsin ve bir sonrakine köprü kursun. Bunun için her maddenin 3 cümlesi şu işlevi taşısın:
     · 1. cümle: Veriyi söyle — net, sade, doğrudan.
     · 2. cümle: Ne anlama geldiğini söyle — okuyucu için, teknik jargon değil.
     · 3. cümle: Köprü kur — ya bir soru bırak ("Peki bunu teyit eden var mı?"), ya bir sonraki maddenin cevabını ima et ("Cevap bir sonraki sinyalde gizli."), ya da önceki maddeyle bağlantı kur ("Bu da BOS sinyalini güçlendiriyor.").
   Okuyucu her maddeyi okuyunca bir sonrakini okumak zorunda hissetmeli. Analizin bir hikayesi olsun — başı, gerilimi ve çözümü.
   Ayrıca, yorumları bir robot gibi değil, tecrübeli ve sezgileri kuvvetli bir stratejist gibi yap.
     a) Listenin en başına; "Kırılım (Breakout)", "Akıllı Para (Smart Money)", "Trend Dönüşü" veya "BOS" içeren EN GÜÇLÜ sinyalleri koy ve bunlara (8/10) ile (10/10) arasında puan ver.
        - Eğer ALTIN FIRSAT durumu ‘EVET’ ise, bu hissenin piyasadan pozitif ayrıştığını (RS Gücü), kurumsal toplama bölgesinde olduğunu (ICT) ve ivme kazandığını vurgula. Analizinde bu 3/3 onayın neden kritik bir ‘alım penceresi’ sunduğunu belirt.
        - Eğer ROYAL FLUSH NADİR FIRSAT durumu ‘EVET’ ise, bu nadir görülen 4/4’lük onayı analizin en başında vurgula ve bu kurulumun neden en yüksek kazanma oranına sahip olduğunu finansal gerekçeleriyle açıkla.
     b) Listenin devamına; trendi destekleyen ama daha zayıf olan yan sinyalleri (örneğin: "Hareketli ortalama üzerinde", "RSI 50 üstü" vb.) ekle. Ancak bunlara DÜRÜSTÇE (1/10) ile (7/10) arasında puan ver.
   - NOT: Listeyi 6 maddeye tamamlamak için zayıf sinyallere asla yapay olarak yüksek puan (8+) verme! Sinyal gücü neyse onu yaz.
2. SENARYO A: ELİNDE OLANLAR İÇİN
   - Yöntem: [TUTULABİLİR / EKLENEBİLİR / SATILABİLİR / KAR ALINABİLİR]
   - Strateji: Trend bozulmadığı sürece taşınabilir mi? Kar realizasyonu için hangi (BOS/Fibonacci/EMA8/EMA13) seviyesi beklenebilir? Emir kipi kullanmadan ("edilebilir", "beklenebilir") Trend/Destek kırılımına göre risk yönetimi çiz. İzsüren stop seviyesi öner.
   - İzsüren Stop: Stop seviyesi nereye yükseltilebilir?
3. SENARYO B: ELİNDE OLMAYANLAR İÇİN
   - Yöntem: [ALINABİLİR / GERİ ÇEKİLME BEKLENEBİLİR / UZAK DURULMASI İYİ OLUR]
   - Risk/Ödül Analizi: Şu an girmek finansal açıdan olumlu mu? yoksa "FOMO" (Tepeden alma) riski taşıyabilir mi? Fiyat çok mu şişkin yoksa çok mu ucuz?
   - İdeal Giriş: Güvenli alım için fiyatın hangi seviyeye (FVG/Destek/EMA8/EMA13/SMA20) gelmesi beklenebilir? Mümkünse bu girişin önümüzdeki kaç günde oluşabileceğini de tahmin et. "etmeli" "yapmalı" gibi emir kipleri ile konuşma. "edilebilir" "yapılabilir" gibi konuş. Sadece olasılıkları belirt.
   - Tezin İptal Noktası (sadece Senaryo B için geçerli): Analizdeki yükseliş/düşüş beklentisinin hangi seviyede tamamen geçersiz kalacağını (Invalidation) net fiyatla belirt. Bu seviyeye gelinirse, mevcut teknik yapının çökmüş olabileceği ve yeni bir analiz yapılması gerektiği yorumunu yap.
4. SONUÇ VE UYARI: "SONUÇ:" başlığı aç — tüm analizin 3-4 cümlelik vurucu, stratejik ve psikolojik bir özeti olsun.
Eğer RSI uyumsuzluğu, hacim düşüklüğü, stopping volume, trend tersliği, ayı/boğa tuzağı veya gizli satış işaretleri varsa bunları "UYARI:" başlığı altında normal cümle tonuyla yaz — büyük harf kullanma, uyarıyı da insani bir dille aktar.
Analizinde HARSI (Heikin Ashi RSI) verilerini kullanacaksan bunun son 14 günlük olduğunu unutma ve son gün mumu için şu şartlar sağlanıyorsa dikkati çek: 1) Eğer ‘Yeşil Bar’ ise bunu "gürültüden arınmış gerçek bir yükseliş ivmesi" olarak yorumla. 2) Eğer ‘Kırmızı Bar’ ise fiyat yükselse bile momentumun (RSI bazında) düştüğünü ve bunun bir yorgunluk sinyali olabileceğini belirt.
Analizin sonuna "Eğitim amaçlıdır. Yatırım tavsiyesi değildir." yaz ve altına " #SmartMoneyRadar #{clean_ticker} #BIST100" yaz.

* İkinci Görevin;
Birinci görevinin "okuyunca her şeyi anlamış gibi hissettiren" sıkıştırılmış özetidir. Uzun analizi okumayan aboneler için birinci görevin HER ÖNEMLİ NOKTASINI kapsayan, gereksiz tekrar içermeyen, akıcı ve "to the point" bir özet çıkaracaksın. Jenerik ifadeler yasak — her maddede mutlaka somut fiyat seviyesi, yüzde değeri veya metrik adı geçmeli.

Format şu şekilde olacak — bölüm sırası sabit değil, o günün en baskın sinyali hangi bölümse onu öne al:
#{clean_ticker} {fiyat_str} ({degisim_str}) | {ai_scenario_title} 👇📸

⚡ ÖZET YORUM:
[Birinci görevindeki açılış paragrafının en kritik 2 cümlelik özü. Büyük resmi ve genel tonu ver.]

📊 TEKNİK TABLO:
🔹[En güçlü sinyal — somut seviye veya formasyon adıyla]
🔹[Yapı ve trend durumu — nerede duruyoruz, hangi seviye kritik]
🔹[Sadece gerçek bir risk veya yapısal uyumsuzluk varsa ekle — Z-Score tek başına yüksekse bu satırı koyma]

🏦 AKILLI PARA:
🔹[Smart Money: delta durumu, VA konumu (ÜSTÜNDE/ALTINDA/İÇİNDE), kurumsal iz — 1 cümle, somut veriyle]
🔹[Hacim anomalisi: RVOL, VSA, OBV veya kümülatif delta — 1 cümle]

🎯 SENARYO VE STRATEJİ:
🔹ELİNDE VARSA: [ne yapılabilir — stop seviyesini mutlaka yaz]
🔹ELİNDE YOKSA: [ideal giriş bölgesi veya bekleme nedeni — somut seviyeyle]
🔹İPTAL NOKTASI: [tezin tamamen çökeceği fiyat seviyesi]

⚠️ SONUÇ ve UYARI:
[Birinci görevindeki sonucu ve varsa en kritik uyarıyı 1 cümleyle — normal cümle tonuyla, büyük harf kullanma]
Eğitim amaçlıdır. Yatırım tavsiyesi değildir.
#BIST100 #SmartMoneyRadar #{clean_ticker}

* Üçüncü Görevin:
Yukarıdaki saf matematiksel verileri (Özellikle "Algoritmik 8 Maddelik Laboratuvar Verisi" bölümünü) kullanarak ve grafiği okuyarak aşağıdaki şablonu doldur. Her madde alt başlıklardan oluşmalı ve okuması keyifli, profesyonel bir tonda olmalıdır. Başlık "{hook_baslik}" olmalıdır.
Önemli: Veri yoksa veya grafik o maddeyi desteklemiyorsa o maddeyi atlayabilirsin — boş doldurmak zorunda değilsin. Veri varsa yaz, yoksa geç.
Formatın şu şekilde olmalıdır (Alt başlıkları aynen kullan):
TEKNİK KART:
1⃣🔹) Genel Sentez (Composite Skor + Vade Uyumu)
- Master Skor: (Algoritmik Composite Skoru ve karar etiketini yaz; en güçlü ve en zayıf alt faktörü vurgula — örn. "Trend 100 mükemmel ama Hacim 50 zayıf")
- Vade Uyumu (MTF): (4H/Günlük/Haftalık/Aylık matrisinden dominant yön ve uyum oranı; vadelerin uyumlu mu yoksa çelişkili mi olduğu)
2⃣🔹) Fiyat Davranışı ve Formasyon
- Mum Yapısı: (Gövde ve fitillere göre görsel okuma + algoritmik PA sinyali)
- Formasyon Durumu: (Grafikte gördüğün OBO, TOBO, Bayrak vs. formasyon ve ikili/üçlü mum yapıları — formasyon yoksa bu satırı atla)
3⃣🔹) Hacim, Efor ve Akıllı Para İzi
- Hacim/Fiyat Uyumu: (Hacmin fiyat hareketini destekleyip desteklemediği, 'Churning' olup olmadığı)
- Kurumsal Akış: (Grafikteki fitillere ve algoritmaya göre emilim veya agresif çıkış)
4⃣🔹) Trend Skoru ve Enerji
- Enerji Puanı: (Algoritmadan gelen Skoru yaz ve grafikteki sıkışmayı/momentumu yorumla)
5⃣🔹) Olası Trade Plan ve Risk Yönetimi
- Giriş ve Stop: (Algoritmik trade plan'daki Entry seviyesi ve 5G dip stop seviyesi — net rakamlarla)
- Hedefler ve R/R: (TP1 ve TP2 seviyelerini, R/R kalitesini değerlendir; hedefler yakın ya da R/R 1.5 altındaysa "kar al noktası yakın" gibi yorumla)
🔹🔹 Teknik Okuma Özeti
(Tüm analizin 3-4 cümlelik vurucu, stratejik ve psikolojik özeti — Composite Skor ve Vade Uyumunu mutlaka özetin çerçevesine koy.)
Bu 5 maddelik TEKNİK KART Algoritmamın çıktısıdır.Eğitim amaçlıdır. Yatırım tavsiyesi değildir.
#BIST100 #SmartMoneyRadar #{clean_ticker}

* Dördüncü Görevin:
Yukarıdaki ilk 3 görevini tamaladıktan sonra bu ilk 3 görevi buraya özetleyen ve abonelere yollanacak bir değerlendirme yapacaksın.
Bu değerlendirme, abonelerin hızlıca anlayabileceği şekilde, ilk 3 görevin en kritik noktalarını ve sonuçlarını içermelidir. Twitter için SEO'luk ve etkileşimlik açısından çekici, vurucu ve net bir şekilde özetini çıkaracaksın.

Değerlendirmeye BAŞLAMADAN ÖNCE, aşağıdaki formatta bir SOSYAL MEDYA KANCASI (HOOK) yaz.
Bu kanca Twitter'da thread'in önüne yapıştırılacak ilk tweet olacak — dikkat çekmeli, merak uyandırmalı, ama analizi ele vermemeli.

─── [HOOK FORMATI] ───────────────────────────────────────────
Bugünkü en baskın sinyale göre aşağıdaki hook tiplerinden birini seç — hepsini kullanma, sadece en uygun olanı:

TİP A — Zıtlık/Gerilim (kurumsal iz varken fiyat zayıfsa veya tam tersi):
Format: [EMOJİ] #{clean_ticker} {fiyat_str} ({degisim_str}) | [SENARYO]: [GERİLİM CÜMLESİ — max 8 kelime] 👇📸
Kural: "ama", "ancak", "oysa" — biri cümlede olmalı.
Örnek: ⚡ #SASA 2.60 (-%0.76) | TEPEDEN RET: Kurumsal sinyaller güçlü ancak tepeden sert ret var 👇📸

TİP B — Soru (okuyucuyu meraklandır, cevabı içeride bırak):
Format: [EMOJİ] #{clean_ticker} {fiyat_str} ({degisim_str}) | [tek cümlelik soru — max 10 kelime] 👇📸
Kural: Sorunun cevabı hook'ta olmasın, analizi oku diye çeksin.
Örnek: 🐳 #THYAO 327.50 (-1.2%) | Kurumlar 3 gündür topluyor — kimse fark etmedi mi? 👇📸

TİP C — Rakam/Tespit (çarpıcı bir veriyi direkt söyle):
Format: [EMOJİ] #{clean_ticker} {fiyat_str} ({degisim_str}) | [çarpıcı veri — max 8 kelime] 👇📸
Kural: Somut sayı veya sinyal adı geçsin, jenerik olmasın.
Örnek: 🎯 #EREGL 140.00 (+0.5%) | OBV 5 gündür yukarı, fiyat hâlâ yatay. 👇📸

TİP D — İddia (net bir tespiti cesurca söyle):
Format: [EMOJİ] #{clean_ticker} {fiyat_str} ({degisim_str}) | [cesur ama temkinli iddia — max 8 kelime] 👇📸
Kural: "olabilir", "görünüyor" gibi ihtiyatlı kelimelerle sar.
Örnek: 🔥 #KONTR 10.85 (+9.93%) | Kurumlar içeri girmiş gibi görünüyor — ama ralli sorgulanabilir. 👇📸

Kapanış: Uyarı baskınsa "SONUÇ ve UYARI kısmına dikkat👇", değilse "UYARI kısmına dikkat👇"

═══════════════════════════════════════════════════════════════════════
🎯 X / TWITTER ALGORİTMA STRATEJİSİ — HOOK'UN İLK 30 DAKİKADA YAŞAMASI İÇİN
═══════════════════════════════════════════════════════════════════════
X'in (eski Twitter) açık kaynaklı algoritması ilk 30 dakikadaki ETKİLEŞİM
HIZINI ölçer. Ağırlıklar (twitter/the-algorithm repo'dan):
- Like = 1 puan (en zayıf — TEK BAŞINA tweet'i yaşatmaz, like döneminin sonu)
- Quote Tweet = 6 puan (tweet'i yeniden bağlam ile paylaşma)
- Reply / Yorum = 13.5 puan (algoritma için "tartışma" sinyali)
- Save / Bookmark = 25 puan (EN GÜÇLÜ sinyal — "saklamaya değer")

Hook bu üç davranıştan en az BİRİNİ tetiklemek ZORUNDA, yoksa ilk
saatte gömülür ve hiç kimse görmez. Hook'un yaşaması = içeriğin yaşaması.

🔥 SAVE TETİKLEYEN HOOK — "Bunu sonra okurum/incelerim"
   - Somut SEVİYE (ama spoiler değil): Hook'ta kritik fiyat görünsün,
     ama analizi ele verme. Okuyucu "bu seviyeyi takip etmem lazım" diyerek save'lesin.
   - Örnek: "#THYAO 327.50 | KURUMSAL BÖLGE TESTİ: Bu seviye kırılırsa hikaye değişir 👇📸"
   - Örnek: "#SISE 48.20 | 3 KRİTİK SEVİYE: Hangi sırayla teste tabi olacaklar? 👇📸"

💬 REPLY TETİKLEYEN HOOK — "Bu konuda yorum yapmak istiyorum"
   - AÇIK UÇLU SORU: Cevabı tek değil, çeşitli yorumlara açık olsun.
     Evet/Hayır kapanması olmasın. "Yükselecek mi?" değil "Hangi senaryo daha güçlü?".
   - Profesyonel-meraklı ton: "Sizin gözünüze nasıl çarpıyor?" tarzı topluluk daveti.
   - Örnek: "#EREGL 140 | 3 farklı senaryo görüyorum — sizinki hangisi? 👇📸"
   - Örnek: "#KONTR 10.85 | OBV bu kadar pozitif olduğunda fiyat genelde ne yapardı? 👇📸"

🔄 QUOTE TWEET TETİKLEYEN HOOK — "Bunu kendi yorumumla paylaşmak istiyorum"
   - PROFESYONEL ÇELİŞKİ: Yaygın görüşle ters bir tespit (ama veriye dayalı).
     Okuyucu "ben farklı yorumluyorum" diye QT atmak istesin.
   - Örnek: "#SASA 2.60 | Herkes düşüş diyor ama tabloda farklı bir şey var 👇📸"
   - Örnek: "#KCHOL 220 | Bu konsolidasyon sıkılma değil — başka bir şey 👇📸"

⛔ ALGORİTMA CEZASI — HOOK'TA ASLA KULLANMA:
   × URL/link ilk satırda → tweet görünürlüğü %50+ düşer
   × 2'den fazla hashtag → engagement reach düşer (max 2 hashtag — örn. #THYAO + #BIST100)
   × Açıkça promosyon dili: "ALACAĞIM!", "KAÇIRMAYIN!", "MUTLAKA İZLEYİN!"
   × Tüm CAPS LOCK kelime serisi (1-2 emfazi OK, ama yarım cümle CAPS yasak)
   × Closed-end soru: "Yükselecek mi?", "Düşer mi?" → kapalı cevap, yorum gelmez
   × Click-bait abartı: "İNANILMAZ KEŞİF!", "KİMSENİN GÖRMEDİĞİ!" → algoritma cezalı
   × Tek başına emoji yığını: "🚀🔥💎" → spam sinyali

✅ ALGORİTMA DOSTU YAPI:
   ✓ İlk satırda: SEMBOL + FIYAT + SENARYO ETİKETİ (görsel hiyerarşi net)
   ✓ Tek "ama/ancak/oysa" → çelişki kurar (engagement çekecek)
   ✓ Spesifik VERİ (sayı, seviye, gün sayısı) → "bu önemli" sinyali
   ✓ "👇📸" sonu → "thread var, kaydır" sinyali (save'i tetikler)
   ✓ Max 280 karakter, ideal 180-220 karakter (mobile-first okunaklı)
   ✓ "olabilir", "görünüyor" gibi ihtiyat ifadeleri (cesur ama temkinli ton)

🎯 EN GÜÇLÜ HOOK = SAVE + REPLY birlikte tetiklenecek yapı:
   "[EMOJİ] #SEMBOL [FIYAT] | [ETİKET]: [Somut veri] [açık uçlu çelişki/soru] 👇📸"

   Örnek (SAVE+REPLY birlikte):
   🐳 #THYAO 327.50 (-1.2%) | KURUMSAL ALIM: OBV 5 gündür yukarı, fiyat aşağı —
       3 senaryo var, hangisi sizin? 👇📸

   Bu hook hem kritik seviyeyi (327.50) verir → save tetikler,
   hem açık uçlu soru sorar → reply tetikler,
   hem de "3 senaryo var" diyerek thread'e davet eder → tıklanma artar.

⚠️ ZORUNLU ÖZ-KONTROL — HOOK'U YAZDIKTAN SONRA SOR:
   1. "Birinin bunu kaydetmek (save) isteyeceği somut bir veri var mı?" → Yoksa ekle.
   2. "Birinin yorum yapmak isteyeceği açık bir soru/çelişki var mı?" → Yoksa ekle.
   3. "Hashtag sayısı 2'yi geçti mi?" → Geçtiyse fazlasını sil.
   4. "Closed-end soru ('Yükselir mi?') var mı?" → Varsa açık-uçluya çevir.
   5. "İlk satırda URL var mı?" → Varsa kaldır.
═══════════════════════════════════════════════════════════════════════
────────────────────────────────────────────────────────────

─── HOOK BİTTİ, DEVAM: ABONE ÖZETİ ───────────────────────
Değerlendirme şu formatta olmalıdır. Başlıkları aynen kullan ama her bölümün içini sıfırdan, o hisseye özel yaz — başka görevden cümle alma.

İlk Başlık daima "{hook_baslik}" formatında olmalıdır. Asla tarih ve saat yazma.

GENEL YORUM: Bugünkü en baskın bulgudan başla. En güçlü sinyali (10/10) (9/10) ilk cümlede söyle. Hisse yükseliyorsa rallinin hikayesini anlat, düşüyorsa neden düştüğünü. 4-5 cümle — ama her cümle o hisseye özel olsun. "fısıldıyor", "kanıtlar nitelikte", "işaret ediyor olsa da" gibi kalıpları kullanma. Bir arkadaşına piyasayı anlatır gibi yaz — ama rakamları ve seviyeleri doğal akışta ver.
Referans ton — YASAK: "Algoritmik veriler incelendiğinde, hissenin güçlü momentum sergilediği görülmektedir." OLMASI GEREKEN: "BTC 97K'da dirençle karşılaştı ama çekilme henüz başlamadı — kurumlar hâlâ tutunuyor gibi görünüyor."

Teknik Görünüm: Fiyat nerede, hangi seviyeyle boğuşuyor, momentum ne diyor — 2-3 cümle. Somut seviye ver, jenerik kalıp kullanma. Eğer rallide iyi görünüyorsa öyle yaz, zorla "ama" ekleme.

Smart Money İzi: Kurumsal tarafta ne görünüyor — delta, OBV, hacim — 2 cümle. Sadece gerçek bir anomali varsa vurgula. Hacim normalse "normal seyrediyor" de, tehlike üretme. mesela, Eğer "bugünkü net baskınlık" ile "OBV trendi" ters yönde 
gidiyorsa — bunu analiz et.

SONUÇ: Tüm tablonun 2-3 cümlelik özü. En önemli seviyeyi ve o seviyenin ne anlama geldiğini söyle. "Uzun lafın kısası" tonunda yaz.

UYARI: Sadece gerçek bir risk varsa yaz — RSI uyumsuzluğu, stopping volume, gizli satış gibi. Yoksa bu bölümü "Belirgin bir uyarı sinyali yok, ana seviyeleri izlemek yeterli." diye kapat. Büyük harf kullanma, normal cümle tonu.

Analizin sonuna "Eğitim amaçlıdır. Yatırım tavsiyesi değildir." yaz (küçük harf, noktalı) ve altına "#SmartMoneyRadar #BIST100" yaz.

*****GÖREVLERİN SUNUŞ SIRALAMASI (DİNAMİK)*****
Görevlerin sunuş sırası bugünkü en baskın sinyale göre değişiyor:

EĞER Royal Flush Nadir Fırsat sinyali tetiklendiyse:
→ Sıralama: Dördüncü (Abone özeti) → İkinci (Twitter) → Birinci (Detaylı analiz) → Üçüncü (Teknik kart)
→ Tüm analizi o nadir sinyal üzerine kurgula. Diğer veriler destekleyici.

EĞER Quasimodo tetiklendiyse:
→ Sıralama: Dördüncü → Birinci → Üçüncü → İkinci
→ Analizi likidite avı hikayesi üzerine kur. Kurumsal oyun anlatısını öne çıkar.

EĞER Z-Score >= 2.0 VE aynı zamanda OBV düşüyorsa VEYA hacim zayıfsa (gerçek zayıf el yükselişi):
→ Sıralama: Birinci (Detaylı analiz, risk odaklı) → Dördüncü → Üçüncü → İkinci
→ Analizi risk yönetimi ve ihtiyat üzerine kur. Uyarıları öne çıkar.
EĞER Z-Score >= 2.0 ama kurumsal alım devam ediyorsa (OBV yükseliyor, hacim güçlü):
→ Z-Score'u analizin merkezine koyma. Normal sırayı koru, Z-Score'u sadece risk yönetimi notunda kısaca geç.
EĞER Z-Score <= -2.0 (Kapitülasyon):
→ Sıralama: Birinci (Detaylı analiz, risk odaklı) → Dördüncü → Üçüncü → İkinci
→ Analizi dip arayışı ve olası toparlanma şartları üzerine kur.

EĞER TOBO, Fincan-Kulp veya Yükselen Üçgen kırılımı varsa:
→ Sıralama: Dördüncü → Birinci → İkinci → Üçüncü
→ Analizi büyük yapısal formasyonun tamamlanması üzerine kur.

EĞER yukarıdakilerin hiçbiri yoksa (Nötr/Konsolidasyon):
→ Sıralama: Dördüncü → Üçüncü → Birinci → İkinci
→ Analizi "neden beklemek gerekir" ve kırılım şartları üzerine kur.
NOT: Hangi sırayı seçersen seç, tüm 5 görevi eksiksiz tamamla.
Sadece sunum sırası değişiyor. Beşinci Görev her zaman en sona yazılır.

* Beşinci Görevin:
Dördüncü görevinde yazdığın abone özetini al ve TAMAMEN YENİDEN YAZ. Aynı bilgiler, ama farklı bir insan gibi. Bu sefer hiçbir sabit başlık yok, hiçbir bölüm adı yok — sadece akıcı paragraflar.
Referans ton — YASAK: "GENEL YORUM: Teknik tablo güçlü görünmektedir. UYARI: Z-Score yüksek seyrediyor." OLMASI GEREKEN: "97K direnç gibi duruyordu, ama bugün satıcılar isteksiz. OBV bunu zaten söylüyordu."
ZORUNLU: 'Dostlar' kelimesini sadece ve sadece 'Rakamların bittiği, tecrübenin konuştuğu' o kritik risk uyarısında kullan.

YASAK: "GENEL YORUM:", "Teknik Görünüm:", "Smart Money İzi:", "SONUÇ:", "UYARI:" başlıklarını KULLANMA.
YASAK: "fısıldıyor", "fısıldıyor olabilir", "kanıtlar nitelikte", "işaret ediyor olsa da" gibi kalıplaşmış köprü cümlelerini KULLANMA.
YASAK: UYARIYI BÜYÜK HARFLE YAZMA. Uyarıyı normal cümle gibi, son paragrafın içine göm.
YASAK: Her paragrafı "Dostlar" ile başlatma — sadece bir kez ve beklenmedik bir yerde kullan.
YASAK: Hook formatını birebir Dördüncü Görevle aynı yapma — farklı bir açıdan, farklı bir gerilimle yaz.

ZORUNLU: En önemli tek bulguyla başla — başlık değil, direkt cümle. Okuyucu ilk satırda "bu beni ilgilendiriyor" desin.
ZORUNLU: Verideki en baskın hikayeyi bul ve analizini onun üzerine kur. Eğer hisse ralli yapıyorsa rallinin hikayesini anlat — Z-Score yüksek ya da VWAP sapması varsa bunları "şunu da gözden kaçırma" olarak doğal akışta geç, analizin merkezine koyma. Eğer gerçek bir çelişki varsa (örn: hacim patlamış ama fiyat hareket etmiyorsa) o zaman onu merkeze al. Hikaye ne ise onu anlat — yapay gerilim üretme.
ZORUNLU: Kritik fiyat seviyelerini doğal konuşma akışı içinde ver — ayrı madde olarak değil.
ZORUNLU: Son cümle bir uyarı veya soru olsun, büyük harf olmadan.
ZORUNLU: En sona "Eğitim amaçlıdır. Yatırım tavsiyesi değildir." yaz (küçük harf, noktalı) ve altına "#SmartMoneyRadar #BIST100 #{clean_ticker}" yaz.

Uzunluk: Dördüncü görevden daha kısa. 4-5 paragraf yeterli.
"""
    with st.sidebar:
        st.code(prompt, language="text")
        st.success("Prompt Güncellendi")
    st.session_state.generate_prompt = False

info = fetch_stock_info(st.session_state.ticker)

# ==============================================================================
# BÖLÜM 36 — ANA STREAMLIT UYGULAMA GİRİŞ NOKTASI
# Sayfa yapısı, sol/sağ sütun düzeni, tam ekran grafik modu.
# Tüm Streamlit widget'larının nihai render edildiği yer.
# ==============================================================================
# --- CACHE TOAST BİLDİRİMİ ---
if st.session_state.get('_cache_toast_msg'):
    st.toast(st.session_state.pop('_cache_toast_msg'), icon="📦")
    st.session_state['_cache_toast_shown'] = False  # sonraki tarama için sıfırla

# ── TAM EKRAN GRAFİK DIALOG ──────────────────────────────────────────────────
@st.dialog("🧠 SMC Derin Yapı Analizi", width="large")
def _show_fullscreen_chart():
    _ticker = st.session_state.ticker
    _disp   = get_display_name(_ticker)

    # ── Grafik + özet verilerini önceden hesapla (başlıkta göstermek için) ──
    _fig, _smc_sum = _main_price_chart_plotly(_ticker, False)

    # ── Chip HTML'i oluştur ───────────────────────────────────────────────────
    def _chip(label, color, mono=True):
        _ff = "font-family:monospace;" if mono else ""
        return (
            f"<span style='background:#0d1117;border:2px solid {color};"
            f"padding:3px 10px;border-radius:5px;font-size:11.5px;font-weight:700;"
            f"color:{color};white-space:nowrap;{_ff}'>{label}</span>"
        )

    _chips_html = ""
    if _smc_sum:
        _parts = []
        if 'zone' in _smc_sum:
            _z_lbl, _z_col, _z_note = _smc_sum['zone']
            _parts.append(_chip(f"📍 {_z_lbl} {_z_note}", _z_col, mono=False))
        if 'poc' in _smc_sum:
            _pv, _pd = _smc_sum['poc']
            _pf = f"{int(_pv):,}" if _pv >= 1000 else f"{_pv:.2f}"
            _ps = '+' if _pd >= 0 else ''
            _parts.append(_chip(f"◈ POC {_pf} ({_ps}{_pd:.1f}%)", "#fbbf24"))
        if 'vwap' in _smc_sum:
            _vv, _vd = _smc_sum['vwap']
            _vf = f"{int(_vv):,}" if _vv >= 1000 else f"{_vv:.2f}"
            _vs = '+' if _vd >= 0 else ''
            _parts.append(_chip(f"〜 VWAP(Y) {_vf} ({_vs}{_vd:.1f}%)", "#e879f9"))
        if 'bos' in _smc_sum:
            _bkind, _bprice, _bdist = _smc_sum['bos']
            _bf = f"{int(_bprice):,}" if _bprice >= 1000 else f"{_bprice:.2f}"
            _bsg = '+' if _bdist >= 0 else ''
            _parts.append(_chip(f"⚡ {_bkind} {_bf} ({_bsg}{_bdist:.1f}%)", "#38bdf8"))
        if 'fvg' in _smc_sum:
            _fdir, _fmid, _fdist = _smc_sum['fvg']
            _ff2 = f"{int(_fmid):,}" if _fmid >= 1000 else f"{_fmid:.2f}"
            _fsg = '+' if _fdist >= 0 else ''
            _farr = '↑' if _fdir == 'bull' else '↓'
            _fcl  = '#4ade80' if _fdir == 'bull' else '#f87171'
            _parts.append(_chip(f"FVG{_farr} {_ff2} ({_fsg}{_fdist:.1f}%)", _fcl))
        if _parts:
            _chips_html = (
                "<div style='display:flex;gap:6px;flex-wrap:wrap;align-items:center;'>"
                + "".join(_parts) + "</div>"
            )

    # ── Başlık satırı: sol=isim+fiyat  sağ=chip şeridi ───────────────────────
    _info   = fetch_stock_info(_ticker)
    _px     = _info.get('price', 0) if _info else 0
    _px_str = f"{int(_px):,}" if _px > 1000 else f"{_px:.2f}"
    st.markdown(
        f"<div style='display:flex;justify-content:space-between;"
        f"align-items:center;margin-bottom:8px;gap:12px;'>"
        f"<div style='display:flex;align-items:baseline;gap:10px;flex-shrink:0;'>"
        f"<span style='font-weight:900;font-size:1.05rem;'>📊 {_disp} — SMC Fiyat Yapısı</span>"
        f"<span style='font-family:monospace;font-size:1.1rem;font-weight:800;"
        f"color:#10b981;'>{_px_str}</span></div>"
        f"{_chips_html}"
        f"</div>",
        unsafe_allow_html=True,
    )

    # ── Tab sistemi — scroll çakışması yok ───────────────────────────────────

    # Dinamik tab: bu ticker için taranmış formasyon / harmonik var mı?
    _pat_row  = None
    _harm_row = None

    # Formasyon: anlık bireysel tarama yap
    try:
        _live_pat_df = scan_chart_patterns([_ticker])
        if _live_pat_df is not None and not _live_pat_df.empty:
            _pat_row = _live_pat_df.iloc[0]
    except Exception:
        pass

    # Harmonik: confluence session state'e bak
    _hconf_df = st.session_state.get('harmonic_confluence_data')
    if _hconf_df is not None and hasattr(_hconf_df, 'empty') and not _hconf_df.empty and 'Sembol' in _hconf_df.columns:
        _hconf_matches = _hconf_df[_hconf_df['Sembol'] == _ticker]
        if not _hconf_matches.empty:
            _harm_row = _hconf_matches.iloc[0]

    _tab_labels = ["📊 SMC Grafik", "🌊 Para Akış İvmesi", "💰 Smart Money Hacim"]
    if _pat_row is not None:
        _tab_labels.append("📐 Formasyon")
    if _harm_row is not None:
        _tab_labels.append("🔮 Harmonik")

    _tabs = st.tabs(_tab_labels)
    _tab1 = _tabs[0]
    _tab2 = _tabs[1]
    _tab3 = _tabs[2]
    _tab_pat  = _tabs[3] if _pat_row is not None else None
    _tab_harm = _tabs[4] if (_pat_row is not None and _harm_row is not None) else (_tabs[3] if (_pat_row is None and _harm_row is not None) else None)

    with _tab1:
        if _fig is None or isinstance(_fig, str):
            st.warning(f"Grafik oluşturulamadı. {_fig or ''}")
        else:
            _fig.update_layout(height=640)
            st.plotly_chart(
                _fig,
                use_container_width=True,
                config={
                    'scrollZoom'             : True,
                    'displayModeBar'         : True,
                    'modeBarButtonsToRemove' : ['select2d', 'lasso2d'],
                    'displaylogo'            : False,
                },
            )

    with _tab2:
        _synth = calculate_synthetic_sentiment(_ticker)
        if _synth is not None and not _synth.empty:
            render_synthetic_sentiment_panel(_synth)
        else:
            st.info("Para Akış verisi hesaplanamadı.")

    with _tab3:
        render_smart_volume_panel(_ticker)

    # ── Formasyon Tab ────────────────────────────────────────────────────────
    if _tab_pat is not None and _pat_row is not None:
        with _tab_pat:
            _chart_dat = _pat_row.get('ChartData', None)
            _curr_px   = _px  # fetch_stock_info ile çekilen mevcut fiyat

            if _chart_dat and isinstance(_chart_dat, dict):
                # ── _formasyon_dialog ile tamamen aynı tam analiz ──────────
                try:
                    _a   = _build_pattern_analysis(_chart_dat, _curr_px, _ticker)
                    _fp  = _a["fp"]
                    _txt = "#0f172a"
                    _sub = "#475569"
                    _brd = "#e2e8f0"
                    _lbl = "#64748b"
                    _crd = "#f8fafc"

                    # Başlık
                    _ttl_col = "#1e3a8a"
                    st.markdown(
                        f"<div style='font-size:1.2rem;font-weight:800;color:{_ttl_col};margin-bottom:10px;'>"
                        f"{_a['emoji']} {_disp} — {_a['name']}</div>",
                        unsafe_allow_html=True
                    )

                    _col_ch, _col_inf = st.columns([60, 40], gap="medium")

                    with _col_ch:
                        _b64 = _mini_pattern_chart_b64(_ticker, _chart_dat, False)
                        if _b64:
                            st.markdown(
                                f"<img src='data:image/png;base64,{_b64}' "
                                f"style='width:100%;border-radius:8px;display:block;'/>",
                                unsafe_allow_html=True,
                            )
                        else:
                            st.caption("Grafik oluşturulamadı.")

                    with _col_inf:
                        # Aşama göstergesi
                        _dots = "".join(
                            f'<span style="width:12px;height:12px;border-radius:50%;display:inline-block;margin-right:5px;'
                            f'background:{"#3b82f6" if j <= _a["stage"] else ("#e2e8f0")};"></span>'
                            for j in range(1, _a["stage_total"] + 1)
                        )
                        st.markdown(
                            f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:10px;">'
                            f'<div>{_dots}</div>'
                            f'<div style="font-size:0.92rem;font-weight:700;color:#38bdf8;">'
                            f'Aşama {_a["stage"]}/{_a["stage_total"]} — {_a["stage_label"]}</div></div>',
                            unsafe_allow_html=True
                        )

                        # Kilit seviye kartları
                        def _ic(label, val_html, bg, bc, full=False):
                            span = "1 / span 2" if full else "auto"
                            return (f'<div style="grid-column:{span};background:{bg};border-left:3px solid {bc};'
                                    f'border-radius:7px;padding:7px 10px;">'
                                    f'<div style="font-size:0.82rem;font-weight:600;color:{bc};margin-bottom:3px;">{label}</div>'
                                    f'<div style="font-size:0.94rem;font-weight:800;color:{_txt};font-family:monospace;line-height:1.3;">{val_html}</div>'
                                    f'</div>')

                        _gh = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px;">'
                        st.markdown(
                            f'<div style="font-size:0.82rem;font-weight:700;color:{_lbl};text-transform:uppercase;'
                            f'letter-spacing:.5px;margin-bottom:6px;">Kilit Seviyeler</div>',
                            unsafe_allow_html=True
                        )

                        for _lv_lbl, _lv_px, _lv_col in _a["levels"]:
                            _arr = "▲" if _curr_px >= _lv_px else "▼"
                            _arc = "#10b981" if _curr_px >= _lv_px else "#ef4444"
                            _nte = "üstünde ✓" if _curr_px >= _lv_px else "altında"
                            _gh += _ic(_lv_lbl,
                                       f'{_fp(_lv_px)} <span style="font-size:0.76rem;color:{_arc};font-weight:600;">{_arr} {_nte}</span>',
                                       "rgba(100,116,139,0.08)", _lv_col)
                        if _a["target"]:
                            _t_pct = _a["pct"](_a["target"], _curr_px)
                            _gh += _ic("🎯 Tahmini Hedef",
                                       f'{_fp(_a["target"])} <span style="font-size:0.8rem;color:#10b981;font-weight:600;">({_t_pct})</span>',
                                       "rgba(16,185,129,0.10)", "#10b981")
                        if _a["invalid"]:
                            _s_pct = _a["pct"](_a["invalid"], _curr_px)
                            _gh += _ic("🔴 Stop / Geçersizlik",
                                       f'{_fp(_a["invalid"])} <span style="font-size:0.8rem;color:#f87171;font-weight:600;">({_s_pct})</span>',
                                       "rgba(248,113,113,0.08)", "#f87171")
                        if _a["rr_ratio"]:
                            _rrc = "#10b981" if _a["rr_ratio"] >= 2.0 else ("#f59e0b" if _a["rr_ratio"] >= 1.0 else "#f87171")
                            _rrl = "Mükemmel" if _a["rr_ratio"] >= 3.0 else ("İyi" if _a["rr_ratio"] >= 2.0 else ("Kabul" if _a["rr_ratio"] >= 1.0 else "Zayıf"))
                            _gh += _ic("📐 Risk / Ödül",
                                       f'<span style="color:{_rrc};">{_a["rr_str"]}</span> <span style="font-size:0.76rem;color:{_rrc};font-weight:600;">({_rrl})</span>',
                                       "rgba(100,116,139,0.08)", _rrc)
                        if _a["pat_age_days"] > 0:
                            _gh += _ic("📅 Formasyon Yaşı",
                                       f'{_a["pat_age_days"]} gün <span style="font-size:0.8rem;color:{_sub};font-weight:600;">({_a["pat_start_str"]})</span>',
                                       "rgba(100,116,139,0.06)", _lbl)
                        _gh += '</div>'

                        # Hacim teyidi
                        _vol_h = ""
                        if _a["vol_dip_ok"] is not None or _a["vol_bounce_ok"] is not None:
                            def _vr(lbl_v, ok):
                                _c = "#10b981" if ok else "#f59e0b"; _ic2 = "✅" if ok else "⚠️"
                                _nt = ("Düşük hacim — tükenme işareti" if ok else "Yüksek hacim — baskı hâlâ var") if "Dip" in lbl_v \
                                      else ("Yüksek hacim — güçlü dönüş" if ok else "Zayıf hacim — teyit bekleniyor")
                                return (f'<div style="display:flex;justify-content:space-between;align-items:center;'
                                        f'padding:4px 0;border-bottom:1px solid {_brd};">'
                                        f'<span style="font-size:0.86rem;color:{_sub};">{_ic2} {lbl_v}</span>'
                                        f'<span style="font-size:0.82rem;font-weight:600;color:{_c};">{_nt}</span></div>')
                            _vi = ""
                            if _a["vol_dip_ok"]    is not None: _vi += _vr("Dip testi hacmi", _a["vol_dip_ok"])
                            if _a["vol_bounce_ok"] is not None: _vi += _vr("Dönüş hacmi",     _a["vol_bounce_ok"])
                            _vol_h = (f'<div style="padding:8px 10px;border-radius:7px;background:{_crd};'
                                      f'border:1px solid {_brd};margin-bottom:6px;">'
                                      f'<div style="font-size:0.82rem;font-weight:700;color:{_lbl};'
                                      f'text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px;">Hacim Teyidi</div>'
                                      f'{_vi}</div>')

                        st.markdown(f'{_gh}{_vol_h}', unsafe_allow_html=True)

                    # Alt bölüm: Sahne Hikayesi + Sonuç
                    st.markdown("<hr style='margin:12px 0 8px 0;border-color:#94a3b8;'>", unsafe_allow_html=True)
                    _sbg = "#0d1829"
                    _cbg = "rgba(16,185,129,0.07)"
                    st.markdown(
                        f'<div style="background:{_sbg};border-radius:10px;padding:12px 16px;margin-bottom:8px;">'
                        f'<div style="font-size:0.85rem;font-weight:700;color:{_lbl};text-transform:uppercase;'
                        f'letter-spacing:.6px;margin-bottom:6px;">📖 Sahne Hikayesi</div>'
                        f'<div style="font-size:0.95rem;color:{_txt};line-height:1.7;">{_a["story"]}</div>'
                        f'</div>'
                        f'<div style="background:{_cbg};border:1px solid #10b98140;border-left:3px solid #10b981;'
                        f'border-radius:10px;padding:12px 16px;">'
                        f'<div style="font-size:0.85rem;font-weight:700;color:#10b981;text-transform:uppercase;'
                        f'letter-spacing:.6px;margin-bottom:6px;">⚡ SONUÇ — Ne Yapılmalı?</div>'
                        f'<div style="font-size:0.95rem;color:{_txt};line-height:1.7;">{_a["conclusion"]}</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )

                except Exception as _fe:
                    st.caption(f"Formasyon analizi yüklenemedi: {_fe}")
            else:
                _pat_name  = _pat_row.get('Formasyon', _pat_row.get('Detay', 'Formasyon'))
                _pat_detay = _pat_row.get('Detay', '')
                _pat_skor  = _pat_row.get('Skor', _pat_row.get('Puan', '—'))
                st.markdown(
                    f"<div style='background:rgba(56,189,248,0.08);border-left:3px solid #38bdf8;"
                    f"border-radius:0 6px 6px 0;padding:10px 14px;margin-bottom:10px;'>"
                    f"<span style='color:#38bdf8;font-weight:900;font-size:1rem;'>📐 {_pat_name}</span>"
                    f"{'  <span style=\"color:#94a3b8;font-size:0.8rem;\">Skor: ' + str(_pat_skor) + '</span>' if _pat_skor != '—' else ''}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if _pat_detay:
                    st.markdown(f"<div style='font-size:0.82rem;color:#94a3b8;line-height:1.6;'>{_pat_detay}</div>",
                                unsafe_allow_html=True)
                st.caption("Bu formasyon için grafik verisi mevcut değil.")

    # ── Harmonik Tab ─────────────────────────────────────────────────────────
    if _tab_harm is not None and _harm_row is not None:
        with _tab_harm:
            _h_pattern = _harm_row.get('Pattern', _harm_row.get('Formasyon', '—'))
            _h_yon     = _harm_row.get('Yön', _harm_row.get('Yön', '—'))
            _h_prz     = _harm_row.get('PRZ', '—')
            _h_fark    = _harm_row.get('PRZ_Fark%', _harm_row.get('Fark%', '—'))
            _h_durum   = _harm_row.get('Durum', '—')
            _h_bar_once = _harm_row.get('Bar_Önce', '—')

            # Renk yön'e göre
            _h_col = "#10b981" if "Yük" in str(_h_yon) or "bull" in str(_h_yon).lower() or "AL" in str(_h_yon).upper() else "#f87171"
            _h_icon = "🟢" if _h_col == "#10b981" else "🔴"

            st.markdown(
                f"<div style='background:rgba(167,139,250,0.08);border-left:3px solid #a78bfa;"
                f"border-radius:0 6px 6px 0;padding:10px 14px;margin-bottom:12px;'>"
                f"<span style='color:#a78bfa;font-weight:900;font-size:1rem;'>🔮 {_h_pattern}</span>"
                f"  <span style='color:{_h_col};font-weight:700;font-size:0.9rem;'>{_h_icon} {_h_yon}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # PRZ bilgi kartları
            _hc1, _hc2, _hc3 = st.columns(3)
            with _hc1:
                st.markdown(
                    f"<div style='text-align:center;padding:10px;background:rgba(167,139,250,0.1);"
                    f"border-radius:8px;border:1px solid rgba(167,139,250,0.3);'>"
                    f"<div style='color:#94a3b8;font-size:0.72rem;'>PRZ Seviyesi</div>"
                    f"<div style='color:#a78bfa;font-family:monospace;font-size:1rem;font-weight:800;'>{_h_prz}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with _hc2:
                st.markdown(
                    f"<div style='text-align:center;padding:10px;background:rgba(251,191,36,0.1);"
                    f"border-radius:8px;border:1px solid rgba(251,191,36,0.3);'>"
                    f"<div style='color:#94a3b8;font-size:0.72rem;'>PRZ Farkı</div>"
                    f"<div style='color:#fbbf24;font-family:monospace;font-size:1rem;font-weight:800;'>%{_h_fark}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with _hc3:
                st.markdown(
                    f"<div style='text-align:center;padding:10px;background:rgba(16,185,129,0.1);"
                    f"border-radius:8px;border:1px solid rgba(16,185,129,0.3);'>"
                    f"<div style='color:#94a3b8;font-size:0.72rem;'>Durum</div>"
                    f"<div style='color:#10b981;font-size:0.85rem;font-weight:700;'>{_h_durum}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)

            # Ek satırlar varsa (confluence vs)
            _h_aciklama = _harm_row.get('Aciklama', _harm_row.get('Detay', ''))
            if _h_aciklama:
                st.markdown(
                    f"<div style='font-size:0.82rem;color:#94a3b8;line-height:1.6;"
                    f"padding:8px 12px;background:rgba(255,255,255,0.03);"
                    f"border-radius:6px;margin-top:4px;'>{_h_aciklama}</div>",
                    unsafe_allow_html=True,
                )

            # Bar önce bilgisi
            if _h_bar_once != '—' and str(_h_bar_once).strip():
                st.caption(f"D noktası {_h_bar_once} gün önce oluştu." if str(_h_bar_once) != 'bekleniyor' else "D noktası henüz bekleniyor — PRZ yaklaşıyor.")

col_left, col_right = st.columns([4, 1])

# --- SOL SÜTUN ---
with col_left:
    # ── ANA FİYAT GRAFİĞİ (buton → popup) ───────────────────────────────────
    _disp_name = get_display_name(st.session_state.ticker)

    if st.button(
        f"🧠  {_disp_name}  ·  AKILLI PARA İZİ  ·  SMC Derin Yapı Grafiğini İncelemek için TIKLA",
        key="btn_fullscreen_chart",
        use_container_width=True,
        type="primary",
    ):
        _show_fullscreen_chart()

    # ── Stale data uyarısı (veri güncellenemediğinde) ─────────────────
    _stale = st.session_state.pop('_data_stale', None)
    if _stale and _stale.get('ticker') == st.session_state.ticker:
        _sd = _stale['days']; _sl = _stale['last']
        _sc = "rgba(248,113,113,0.08)"
        _sb = "#f87171"
        st.markdown(
            f'<div style="background:{_sc};border:1px solid {_sb};border-radius:6px;'
            f'padding:6px 12px;margin-bottom:6px;font-size:0.78rem;color:{_sb};font-weight:700;">'
            f'⚠️ Veri güncellenemedi — gösterilen veriler <b>{_sd} gün eski</b> '
            f'(son güncelleme: {_sl}). Bağlantıyı kontrol edin veya sayfayı yenileyin.'
            f'</div>',
            unsafe_allow_html=True
        )
    # ─────────────────────────────────────────────────────────────────────────

    # 1. PARA AKIŞ İVMESİ & FİYAT DENGESİ (EN TEPE)
    synth_data = calculate_synthetic_sentiment(st.session_state.ticker)
    if synth_data is None:
        # Cache'de bozuk/None sonuç olabilir — temizle ve tekrar dene
        calculate_synthetic_sentiment.clear()
        synth_data = calculate_synthetic_sentiment(st.session_state.ticker)
    if synth_data is not None and not synth_data.empty:
        render_synthetic_sentiment_panel(synth_data)
    
    # --- YENİ YERİ: TEKNİK SEVİYELER (MA) PANELİ (TEK SATIR ŞERİT GÖRÜNÜMÜ) ---
    try:
        if "ticker" in st.session_state and st.session_state.ticker:
            df_ma = get_safe_historical_data(st.session_state.ticker, period="1y") 
            
            if df_ma is not None and not df_ma.empty:
                if 'Close' in df_ma.columns: c_col = 'Close'
                elif 'close' in df_ma.columns: c_col = 'close'
                elif 'Fiyat' in df_ma.columns: c_col = 'Fiyat'
                else: c_col = df_ma.columns[0]

                # 🛡️ YENİ KORUMA: Eğer eski bozuk veriden dolayı 'Close' iki tane gelmişse, sadece ilkini al
                if isinstance(df_ma[c_col], pd.DataFrame):
                    price_series = df_ma[c_col].iloc[:, 0]
                else:
                    price_series = df_ma[c_col]

                # DEĞERLERİ GÜVENLİ SERİ ÜZERİNDEN HESAPLA
                current_price = info.get('price', price_series.iloc[-1]) if info else price_series.iloc[-1]

                ema5 = price_series.ewm(span=5, adjust=False).mean().iloc[-1]
                ema8 = price_series.ewm(span=8, adjust=False).mean().iloc[-1]
                ema13 = price_series.ewm(span=13, adjust=False).mean().iloc[-1]
                ema144 = price_series.ewm(span=144, adjust=False).mean().iloc[-1]

                sma50 = price_series.rolling(window=50).mean().iloc[-1]
                sma100 = price_series.rolling(window=100).mean().iloc[-1]
                sma200 = price_series.rolling(window=200).mean().iloc[-1]

                is_index = "XU" in st.session_state.ticker.upper() or "^" in st.session_state.ticker or current_price > 1000

                def ma_status(ma_value, price):
                    if pd.isna(ma_value): return "⏳ -"
                    if is_index: val_str = f"{int(ma_value)}" 
                    else: val_str = f"{ma_value:.2f}"  
                        
                    if price > ma_value: return f"🟢 <b>{val_str}</b>"
                    else: return f"🔴 <b>{val_str}</b>"

                clean_ticker = get_display_name(st.session_state.ticker)
                
                # Fiyat formatlaması (Endeks ise EMA'lar gibi küsuratsız)
                if is_index:
                    display_price = f"{int(current_price)}"
                else:
                    display_price = f"{current_price:.2f}"
                
                # SMR Dark Tema Renkleri
                text_col   = "#f1f5f9"
                lbl_col    = "#94a3b8"
                border_col = "#1e3a5f"
                bg_col     = "#0d1829"
                badge_bg   = "rgba(16,185,129,0.15)"
                badge_text = "#10b981"
                price_color = "#10b981"

                def ma_cell(label, val, price):
                    return (f'<div style="display:flex;flex-direction:column;align-items:center;gap:1px;padding:0 8px;border-right:1px solid {border_col};">'
                            f'<span style="font-size:0.66rem;color:{lbl_col};font-weight:600;white-space:nowrap;">{label}</span>'
                            f'<span style="font-size:0.8rem;color:{text_col};font-family:\'JetBrains Mono\',monospace;font-weight:700;">{ma_status(val, price)}</span>'
                            f'</div>')

                def ma_cell_last(label, val, price):
                    return (f'<div style="display:flex;flex-direction:column;align-items:center;gap:1px;padding:0 8px;">'
                            f'<span style="font-size:0.66rem;color:{lbl_col};font-weight:600;white-space:nowrap;">{label}</span>'
                            f'<span style="font-size:0.8rem;color:{text_col};font-family:\'JetBrains Mono\',monospace;font-weight:700;">{ma_status(val, price)}</span>'
                            f'</div>')

                def ma_cell(label, val, price, sep=True):
                    border = f"border-right:1px solid {border_col};" if sep else ""
                    return (f'<div style="display:flex;flex-direction:column;align-items:center;gap:2px;flex:1;{border}padding:4px 0;">'
                            f'<span style="font-size:0.67rem;color:{lbl_col};font-weight:600;">{label}</span>'
                            f'<span style="font-size:0.82rem;color:{text_col};font-family:\'JetBrains Mono\',monospace;font-weight:700;">{ma_status(val, price)}</span>'
                            f'</div>')

                def grp_label(icon, line1, line2, color):
                    return (f'<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;flex:0 0 72px;padding:4px 0;border-right:1px solid {border_col};">'
                            f'<span style="font-size:0.75rem;">{icon}</span>'
                            f'<span style="font-size:0.65rem;color:{color};font-weight:700;line-height:1.3;">{line1}</span>'
                            f'<span style="font-size:0.65rem;color:{color};font-weight:700;line-height:1.3;">{line2}</span>'
                            f'</div>')

                st.markdown(f"""
<div style="border:1px solid #1e3a5f;border-radius:8px;overflow:hidden;margin-bottom:8px;box-shadow:0 4px 12px rgba(0,0,0,0.4);">
  <div style="background:linear-gradient(90deg,#0d1829,#0f2040);padding:5px 12px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #1e3a5f;">
    <span style="font-weight:700;font-size:0.82rem;color:#38bdf8;letter-spacing:0.5px;">📊 TEKNİK SEVİYELER</span>
    <span style="background:{badge_bg};color:{badge_text};padding:2px 10px;border-radius:4px;font-family:'JetBrains Mono',monospace;font-weight:800;font-size:0.82rem;border:1px solid rgba(16,185,129,0.3);">{clean_ticker} — <span style="color:{price_color};">{display_price}</span></span>
  </div>
  <div style="display:flex;align-items:stretch;padding:0;background:{bg_col};width:100%;">
    {grp_label("📉", "KISA", "VADE", "#38bdf8")}
    {ma_cell("EMA 5",   ema5,   current_price)}
    {ma_cell("EMA 8",   ema8,   current_price)}
    {ma_cell("EMA 13",  ema13,  current_price)}
    {grp_label("🔭", "ORTA", "UZUN",  "#8b5cf6")}
    {ma_cell("SMA 50",  sma50,  current_price)}
    {ma_cell("SMA 100", sma100, current_price)}
    {ma_cell("SMA 200", sma200, current_price)}
    {ma_cell("EMA 144", ema144, current_price, sep=False)}
  </div>
</div>
""", unsafe_allow_html=True)
                
    except Exception as e:
        st.warning(f"Teknik tablo oluşturulamadı. Hata: {e}")
    # --------------------------------------------------
    # 1--- SMART MONEY HACİM ANALİZİ ---
    st.markdown("<div style='margin-top: 0px;'></div>", unsafe_allow_html=True)
    render_smart_volume_panel(st.session_state.ticker)

    # 2. TEKNİK YOL HARİTASI PANELİ
    render_roadmap_8_panel(st.session_state.ticker)

    # 3.--- ICT SMART MONEY ANALİZİ ---
    render_ict_deep_panel(st.session_state.ticker)

    # 4. Kritik Seviyeler
    render_levels_card(st.session_state.ticker)

    # ---------------------------------------------------------
    # 🏆 TARAMA MERKEZİ — TİERELİ DÜZEN (YENİ)
    # ---------------------------------------------------------

    # Session state başlatmaları
    for _k in ['ict_scan_data','nadir_firsat_scan_data','guclu_donus_data',
                'harmonic_confluence_data','accum_data','minervini_data',
                'golden_results','platin_results','tekli_altin_results','prelaunch_bos_data',
                'golden_pattern_data']:
        if _k not in st.session_state: st.session_state[_k] = None

    # ── STARTUP CACHE RESTORE (piyasa dışı saatlerde otomatik yükle) ─────────
    # Sayfa ilk açıldığında veya yenilendiğinde, son kapanış sonrası yapılmış
    # master scan cache varsa tüm sonuçları session_state'e geri yükler.
    if '_scan_cache_restored' not in st.session_state:
        st.session_state['_scan_cache_restored'] = True   # Bu oturumda bir kez çalış
        _cat_restore = st.session_state.get('category', 'S&P 500')
        _master_restore = load_scan_result("master_scan", _cat_restore)
        if _master_restore is not None:
            for _rk, _rv in _master_restore.items():
                # Sadece henüz None olan key'leri doldur (elle değiştirilen kalıcı olsun)
                if st.session_state.get(_rk) is None or st.session_state.get(_rk) == [] or st.session_state.get(_rk) is False:
                    st.session_state[_rk] = _rv
            _close_str = _scan_last_close_dt().strftime('%d.%m %H:%M')
            st.session_state['_cache_toast_msg'] = f"📦 {_close_str} kapanışı tarama sonuçları yüklendi"
    # ─────────────────────────────────────────────────────────────────────────

    # Skor kartı başlığı — gradient progress bar + puan badge
    def _darken(hex_color, factor=0.65):
        h = hex_color.lstrip('#')
        r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
        return f"#{int(r*factor):02x}{int(g*factor):02x}{int(b*factor):02x}"

    def _scan_card_header(icon, title, score, subtitle, color="#3b82f6", desc=""):
        pct   = score
        stars = "⭐" * (score // 20)
        if desc:
            bottom = (f"<span style='color:#cbd5e1;font-weight:600;font-size:0.72rem;font-style:italic;'>{desc}</span> "
                      f"<span style='color:#cbd5e1;font-size:0.70rem;font-weight:500;opacity:0.8;'>({subtitle})</span>")
        else:
            bottom = f"<span style='color:#cbd5e1;font-size:0.7rem;font-weight:500;font-style:italic;'>{stars} {subtitle}</span>"
        return (f"<div style='background:linear-gradient(135deg,{color}18,{color}06);"
                f"border:1px solid {color}50;border-radius:10px;padding:9px 13px;margin-bottom:7px;'>"
                f"<div style='display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:5px;'>"
                f"<span style='font-weight:900;font-size:0.92rem;'>{icon} {title}</span>"
                f"<span style='background:{color};color:white;border-radius:11px;padding:2px 9px;font-size:0.73rem;font-weight:800;'>{score}/100</span>"
                f"</div>"
                f"<div style='background:rgba(0,0,0,0.12);border-radius:3px;height:4px;margin-bottom:5px;'>"
                f"<div style='background:{color};height:4px;border-radius:3px;width:{pct}%;'></div></div>"
                f"<div style='margin-top:3px;'>{stars} {bottom}</div></div>")

    # ══════════════════════════════════════════════════════════
    # ① CONFLUENCE & ELİTLER — Her Zaman Görünür
    # ══════════════════════════════════════════════════════════
    st.markdown("<div style='border-left:4px solid #7c3aed;padding-left:10px;margin-top:22px;margin-bottom:8px;"
                "font-weight:900;font-size:1.05rem;'>🎯 CONFLUENCE & ELİTLER</div>", unsafe_allow_html=True)

    _c_left, _c_right = st.columns(2)

    with _c_left:
        st.markdown(_scan_card_header("🔥", "CONFLUENCE", 95,
            "2-3 Metodoloji Kesişimi", "#1d4ed8",
            desc="2-3 bağımsız metodoloji aynı hisseyi işaret ettiğinde — en güvenilir sinyal"
        ), unsafe_allow_html=True)
        _hits  = st.session_state.get('confluence_hits')
        _hc_df = st.session_state.get('harmonic_confluence_data')
        _cbg   = "#eff6ff"

        # ── İki sub-kolon: sol=CONFLUENCE, sağ=HARMONİK CONF ──
        _sub_cf, _sub_hc = st.columns(2)

        with _sub_cf:
            _conf_count = len(_hits) if _hits else 0
            st.markdown(f"<div style='background:linear-gradient(135deg,#7c3aed18,#7c3aed06);"
                        f"border:1px solid #7c3aed50;border-radius:8px;padding:6px 10px;margin-bottom:5px;'>"
                        f"<span style='font-size:0.78rem;font-weight:900;color:#7c3aed;'>"
                        f"🔥 CONFLUENCE — {_conf_count} Hisse</span></div>", unsafe_allow_html=True)
            if _hits:
                _hits_33 = [h for h in _hits if h['group_count'] == 3]
                _hits_23 = [h for h in _hits if h['group_count'] == 2]
                _conf_all = _hits_33 + _hits_23   # limit yok
                with st.container(height=320, border=False):
                    for _ci, _ch in enumerate(_conf_all):
                        _cs = _ch['Sembol'].replace('.IS','')
                        _cp = _ch.get('price', 0)
                        _cp_s = f"{int(_cp)}" if _cp >= 1000 else f"{_cp:.2f}"
                        _icon = "🔥" if _ch.get('group_count',0) == 3 else "⚡"
                        # Açıklama: hangi metodoloji grupları ve scanner'lar buldu
                        _hg = _ch.get('hit_groups', [])
                        _conf_desc_parts = []
                        for _hg_item in _hg:
                            _scanners_str = " + ".join(_hg_item.get('scanners', []))
                            _conf_desc_parts.append(f"{_hg_item['label']}: {_scanners_str}")
                        _conf_desc = " | ".join(_conf_desc_parts)
                        if st.button(f"{_icon} {_cs} ({_cp_s})", key=f"conf_top_{_cs}_{_ci}", use_container_width=True):
                            st.session_state.ticker = _ch['Sembol']
                            on_scan_result_click(_ch['Sembol']); st.rerun()
                        if _conf_desc:
                            st.markdown(
                                f"<div style='font-size:0.72rem;color:#cbd5e1;font-weight:500;"
                                f"margin:-4px 0 6px 4px;line-height:1.4;'>{_conf_desc}</div>",
                                unsafe_allow_html=True
                            )
            else:
                st.markdown(f"<div style='border:1px dashed #7c3aed50;border-radius:7px;"
                            f"padding:18px 10px;text-align:center;color:#94a3b8;font-size:0.8rem;'>"
                            f"Master Scan çalıştırın</div>", unsafe_allow_html=True)

        with _sub_hc:
            _hc_count = len(_hc_df) if (_hc_df is not None and not (hasattr(_hc_df,'empty') and _hc_df.empty)) else 0
            st.markdown(f"<div style='background:linear-gradient(135deg,#7c3aed18,#7c3aed06);"
                        f"border:1px solid #7c3aed50;border-radius:8px;padding:6px 10px;margin-bottom:5px;'>"
                        f"<span style='font-size:0.78rem;font-weight:900;color:#7c3aed;'>"
                        f"⚡ HARMONİK CONF — {_hc_count} Hisse</span></div>", unsafe_allow_html=True)
            if _hc_count > 0:
                with st.container(height=320, border=False):
                    for _hci, _hcr in _hc_df.iterrows():
                        _hcs = str(_hcr.get('Sembol','')).replace('.IS','')
                        _hcp = _hcr.get('Fiyat', 0)
                        _hcp_s = f"{int(_hcp)}" if _hcp >= 1000 else f"{_hcp:.2f}"
                        _cyon = str(_hcr.get('Yön',''))
                        _cyon_lbl = "🟢" if ('Bullish' in _cyon or 'LONG' in _cyon) else "🔴"
                        _hc_badges = _hcr.get('Badges', '')
                        _hc_badge_suffix = f" | {_hc_badges}" if _hc_badges else ""
                        _hc_tooltip = _hcr.get('Aciklama', 'Harmonik PRZ teyitli')
                        _hc_durum = str(_hcr.get('Durum', ''))
                        if st.button(f"⚡{_cyon_lbl} {_hcs} ({_hcp_s}) | {_hcr.get('Pattern','')}{_hc_badge_suffix}", key=f"hctop_{_hcs}_{_hci}", use_container_width=True):
                            st.session_state.ticker = _hcr.get('Sembol', _hcs)
                            on_scan_result_click(_hcr.get('Sembol', _hcs)); st.rerun()
                        _hc_inline = f"{_hc_tooltip}" + (f" · {_hc_durum}" if _hc_durum else "")
                        if _hc_inline:
                            st.markdown(
                                f"<div style='font-size:0.72rem;color:#cbd5e1;font-weight:500;"
                                f"margin:-4px 0 6px 4px;line-height:1.4;'>{_hc_inline}</div>",
                                unsafe_allow_html=True
                            )
            else:
                st.markdown(f"<div style='border:1px dashed #7c3aed50;border-radius:7px;"
                            f"padding:18px 10px;text-align:center;color:#94a3b8;font-size:0.8rem;'>"
                            f"Master Scan çalıştırın</div>", unsafe_allow_html=True)

    # ── ELİT ↔ Pre-Launch kesişim seti ──────────────────────────────────────
    _pb_scan_syms   = set()
    _elit_scan_syms = set()
    if st.session_state.get('prelaunch_bos_data') is not None and not st.session_state.prelaunch_bos_data.empty:
        _pb_scan_syms = set(st.session_state.prelaunch_bos_data['Sembol'].values)
    if st.session_state.get('platin_results') is not None and not st.session_state.platin_results.empty:
        _elit_scan_syms.update(set(st.session_state.platin_results['Hisse'].values))
    if st.session_state.get('golden_results') is not None and not st.session_state.golden_results.empty:
        _elit_scan_syms.update(set(st.session_state.golden_results['Hisse'].values))
    _double_hit_syms = _elit_scan_syms & _pb_scan_syms
    # ─────────────────────────────────────────────────────────────────────────

    with _c_right:
        st.markdown(_scan_card_header("💎", "ELİTLER", 88,
            "Güçlü Yapı + Uçmamış + Harekete Hazır",
            "#1d4ed8",
            desc="Güçlü trendde, henüz fırlamamış, 3 kapıdan 2'si açık hisseler"
        ), unsafe_allow_html=True)
        if st.button("💎 ELİT TARAMA (Platin + Altın)", use_container_width=True, key="btn_elit_tara_main",
                     help="Piyasanın en kaliteli hisselerini iki kategoride listeler.\n\n💎 Platin Fırsat: Fiyat hem 200 hem 50 günlük ortalamanın üstünde, RS endeksten güçlü, Discount bölgede ve hacim artıyor — tüm kriterler aynı anda sağlanmalı.\n\n🦁 Altın Fırsat: Son 10 günde endeksi geçmiş, son 60 güne göre hâlâ ucuz, enerji/hacim yükseliyor. Büyük oyunculara yakın ama henüz fazla yükselmemiş hisseler."):
            with st.spinner("Elit hisseler aranıyor..."):
                _scan_list = ASSET_GROUPS.get(st.session_state.category, [])
                if _scan_list:
                    st.session_state.radar2_data = radar2_scan(_scan_list)
                    _df_g, _df_r, _df_t = get_golden_trio_batch_scan(_scan_list)
                    st.session_state.golden_results = _df_g.sort_values(by="Teknik_Skor", ascending=False).reset_index(drop=True) if not _df_g.empty else pd.DataFrame()
                    st.session_state.platin_results  = _df_r.sort_values(by="Teknik_Skor", ascending=False).reset_index(drop=True) if not _df_r.empty else pd.DataFrame()
                    st.session_state.tekli_altin_results = _df_t.sort_values(by=["is_platin", "Teknik_Skor"], ascending=[False, False]).reset_index(drop=True) if not _df_t.empty else pd.DataFrame()
                    st.rerun()
        _has_elite = (
            (st.session_state.platin_results is not None and not st.session_state.platin_results.empty) or
            (st.session_state.golden_results is not None and not st.session_state.golden_results.empty) or
            (st.session_state.tekli_altin_results is not None and not st.session_state.tekli_altin_results.empty)
        )
        # Tarama çalıştı ama 0 sonuç döndü mü? (boş DataFrame, None değil)
        _scan_ran_empty = (
            (isinstance(st.session_state.platin_results, pd.DataFrame) and st.session_state.platin_results.empty) and
            (isinstance(st.session_state.golden_results, pd.DataFrame) and st.session_state.golden_results.empty) and
            (isinstance(st.session_state.tekli_altin_results, pd.DataFrame) and st.session_state.tekli_altin_results.empty)
        )
        if _has_elite:
            _platin = st.session_state.platin_results
            _tekli  = st.session_state.tekli_altin_results

            # ── İKİ SÜTUN ────────────────────────────────────────────
            _col_l, _col_r = st.columns(2)

            # ── SOL: HAREKETE HAZIR (3-kapı Platin tarama) ──────────
            with _col_l:
                st.markdown(
                    "<div style='font-size:0.72rem;font-weight:700;color:#a78bfa;"
                    "margin-bottom:4px;'>⚡ HAREKETE HAZIR</div>",
                    unsafe_allow_html=True
                )
                with st.container(height=145, border=True):
                    if _platin is not None and not _platin.empty:
                        # 3/3 kapı açık olanları önce göster, sonra Teknik_Skor
                        _plt_s = _platin.copy()
                        _plt_s['_gate_n'] = _plt_s['Hazırlık'].apply(
                            lambda x: int(str(x).split('/')[0]) if '/' in str(x) else 0)
                        _plt_s = _plt_s.sort_values(by=['_gate_n', 'Teknik_Skor'], ascending=[False, False])
                        for _pi, _pr in _plt_s.head(8).iterrows():
                            _psym = _pr['Hisse']
                            _pd_  = get_display_name(_psym)
                            _pfv  = _pr['Fiyat']; _pfs = f"{int(_pfv)}" if _pfv >= 1000 else f"{_pfv:.2f}"
                            _pred = _pr.get('RedCandle', False)
                            _pkur = _pr.get('Kurulum', '')
                            _phaz = _pr.get('Hazırlık', '')
                            _pg_n = int(str(_phaz).split('/')[0]) if '/' in str(_phaz) else 0
                            _pdbl = _psym in _double_hit_syms
                            _plbl = f"{'🔥' if _pg_n == 3 else '💎'} {_pd_} ({_pfs}) {_phaz}" + (" 🟠" if _pred else "") + (" 🚀" if _pdbl else "")
                            _ptip = f"Kurulum: {_pkur} · {_phaz} kapı açık" + (" | ÇİFT TEYİT — Pre-Launch BOS'ta da var!" if _pdbl else "")
                            if st.button(_plbl, key=f"elit_plt2_{_pi}", use_container_width=True, help=_ptip):
                                on_scan_result_click(_psym); st.rerun()
                    else:
                        st.markdown(
                            "<div style='color:#64748b;font-size:0.72rem;text-align:center;"
                            "padding-top:22px;'>Bu kategoride Platin bulunamadı</div>",
                            unsafe_allow_html=True
                        )

            # ── SAĞ: ALTIN & PLATİN (tekli hisse kriterleri) ────────
            with _col_r:
                st.markdown(
                    "<div style='font-size:0.72rem;font-weight:700;color:#f59e0b;"
                    "margin-bottom:4px;'>💎 ALTIN & PLATİN</div>",
                    unsafe_allow_html=True
                )
                with st.container(height=145, border=True):
                    if _tekli is not None and not _tekli.empty:
                        for _ti, _tr in _tekli.head(12).iterrows():
                            _tsym  = _tr['Hisse']
                            _td_   = get_display_name(_tsym)
                            _tfv   = _tr['Fiyat']; _tfs = f"{int(_tfv)}" if _tfv >= 1000 else f"{_tfv:.2f}"
                            _tred  = _tr.get('RedCandle', False)
                            _tisp  = _tr.get('is_platin', False)
                            _tdisc = _tr.get('Discount_Pct', 0)
                            _trsi  = _tr.get('RSI', 0)
                            _tdbl  = _tsym in _double_hit_syms
                            # ── Darvas badge (yıldızlı pekiyi) ─────────────
                            _tdq  = _tr.get('Darvas_Quality', None)
                            _tds  = _tr.get('Darvas_Status',  None)
                            _tdc  = _tr.get('Darvas_Class',   None)
                            _tda  = _tr.get('Darvas_Age',     None)
                            _tdt  = _tr.get('Darvas_Top',     None)
                            _tdb  = _tr.get('Darvas_Bottom',  None)
                            _darvas_lbl = ""
                            _darvas_tip = ""
                            if _tdq is not None and _tdq >= 75:
                                if _tds == 'breakout' and _tdc == 'A':
                                    _darvas_lbl = " ⭐📦"
                                    _darvas_tip = (f" | ⭐ DARVAS A-SINYAL — {_tda} günlük kutu kırıldı!"
                                                   f" ({_tdt}→{_tdb}) · 3/3 Kapı · Kalite:{_tdq}/100")
                                elif _tds == 'breakout':
                                    _darvas_lbl = " 📦"
                                    _darvas_tip = (f" | 📦 Darvas Kırılım — {_tda}g kutu"
                                                   f" ({_tdt}) · Kalite:{_tdq}/100")
                                else:
                                    _darvas_lbl = " 🟦"
                                    _darvas_tip = (f" | 🟦 Darvas Kutu Oluşuyor — {_tda}g"
                                                   f" ({_tdt}→{_tdb}) · Kalite:{_tdq}/100")
                            _tlbl  = f"{'💎 Platin' if _tisp else '🏆 Altın'} · {_td_} ({_tfs}){_darvas_lbl}" + (" 🟠" if _tred else "") + (" 🚀" if _tdbl else "")
                            _ttip  = (f"{'💎 Platin' if _tisp else '🏆 Altın'} · Discount %{_tdisc} · RSI {_trsi}"
                                      + _darvas_tip
                                      + (" | ÇİFT TEYİT — Pre-Launch BOS'ta da var!" if _tdbl else ""))
                            if st.button(_tlbl, key=f"elit_tekli_{_ti}", use_container_width=True, help=_ttip):
                                on_scan_result_click(_tsym); st.rerun()
                    else:
                        st.markdown(
                            "<div style='color:#64748b;font-size:0.72rem;text-align:center;"
                            "padding-top:22px;'>Bu kategoride Altın/Platin bulunamadı</div>",
                            unsafe_allow_html=True
                        )
        elif _scan_ran_empty:
            # Master Scan çalıştı ama bu kategoride ELİT yok
            st.markdown(
                "<div style='border:1px dashed #1d4ed850;border-radius:7px;"
                "padding:14px 10px;text-align:center;color:#94a3b8;font-size:0.78rem;line-height:1.4;'>"
                "💤 <b>Bu kategoride ELİT bulunamadı</b><br>"
                "<span style='font-size:0.7rem;opacity:0.85;'>Master Scan tamamlandı ancak Platin/Altın kriterlerini geçen hisse yok.</span>"
                "</div>", unsafe_allow_html=True
            )
        else:
            # Henüz hiç scan yapılmamış
            st.markdown(
                "<div style='border:1px dashed #1d4ed850;border-radius:7px;"
                "padding:14px 10px;text-align:center;color:#94a3b8;font-size:0.78rem;'>"
                "Master Scan çalıştırın veya yukarıdaki butona basın</div>",
                unsafe_allow_html=True
            )

    st.markdown("<hr style='margin:12px 0;border-color:rgba(150,150,150,0.2);'>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════
    # ② TIER 1 — Kanıtlanmış Yöntemler
    # ══════════════════════════════════════════════════════════
    st.markdown("<div style='border-left:5px solid #16a34a;padding-left:10px;margin-bottom:8px;"
                "font-weight:900;font-size:1rem;color:#16a34a;'>🥇 TIER 1 — Kanıtlanmış Yöntemler</div>",
                unsafe_allow_html=True)

    # ── ICT Sniper & Royal Flush Nadir Fırsat ──
    if True:
        st.markdown(_scan_card_header(
            "🦅", "ICT Sniper  &  ♠️ Royal Flush", 90,
            "SMA50+Sweep+Hacim×2+RRR≥2  |  BOS/MSS+RS>1.5+VWAP<10+Hacim×1.3+RSI<65",
            "#16a34a",
            desc="🦅 ICT Sniper: Hacimsiz stop avı + toparlanma = likidite sonrası toparlanma yapısı &nbsp;·&nbsp; ♠️ Royal Flush: 5 kriterin aynı anda kesiştiği nadir kraliyet kurulumu"
        ), unsafe_allow_html=True)

        # İki iç sütun: ICT | Royal Flush
        _ict_sub, _rf_sub = st.columns(2)

        with _ict_sub:
            st.markdown("<div style='text-align:center;color:#16a34a;font-weight:800;font-size:0.7rem;"
                        "padding:2px;border-radius:4px;border:1px solid #86efac;margin-bottom:4px;'>"
                        "🦅 ICT SNIPER</div>", unsafe_allow_html=True)
            if st.button(f"🦅 ICT SNIPER TARA ({st.session_state.category})", type="secondary",
                         use_container_width=True, key="btn_scan_ict",
                         help="Kurumsal yatırımcıların (bankalar, fonlar) piyasaya giriş izlerini takip eder.\n\n"
                              "✅ Zorunlu: Stop avı (sweep) + Yapı kırılımı (MSS) + Hacimli itme (displacement) + RRR ≥ 2.0\n\n"
                              "🎯 FVG CE rozeti: Fiyat aynı zamanda kurumsal boşluk bölgesinin ortasındaysa — daha güçlü giriş.\n\n"
                              "⭐ OTE rozeti: Fibonacci %61.8–78.6 ile örtüşüyorsa — kurumların en çok tercih ettiği aralık.\n\n"
                              "Rozetsiz sinyal de geçerlidir, rozetler ek teyit sağlar."):
                with st.spinner("Kurumsal ayak izleri (MSS + Displacement + FVG) taranıyor..."):
                    current_assets = ASSET_GROUPS.get(st.session_state.category, [])
                    st.session_state.ict_scan_data = scan_ict_batch(current_assets)
                    save_scan_result("ict_scan_data", st.session_state.ict_scan_data, st.session_state.category)
            if st.session_state.ict_scan_data is not None:
                if st.session_state.ict_scan_data.empty:
                    st.warning("ICT kurulumu bulunamadı.")
                else:
                    df_res = st.session_state.ict_scan_data
                    longs  = df_res[df_res['Yön'] == 'LONG']
                    shorts = df_res[df_res['Yön'] == 'SHORT']
                    st.markdown(f"<div style='text-align:center;color:#16a34a;font-size:0.65rem;"
                                f"font-weight:700;margin-bottom:2px;'>🐂 LONG ({len(longs)}) · 🐻 SHORT ({len(shorts)})</div>",
                                unsafe_allow_html=True)
                    with st.container(height=150, border=False):
                        for i, row in longs.iterrows():
                            sym = row['Sembol']
                            _aciklama = row.get('Aciklama', '')
                            if st.button(f"🐂 {sym.replace('.IS','')} ({row['Fiyat']:.2f}) | {row['Durum']}",
                                         key=f"ict_long_{sym}_{i}", use_container_width=True):
                                on_scan_result_click(sym); st.rerun()
                            if _aciklama:
                                st.markdown(f"<div style='font-size:0.72rem;color:#cbd5e1;font-weight:500;margin:-6px 0 4px 4px;"
                                            f"line-height:1.3;'>{_aciklama}</div>", unsafe_allow_html=True)
                        for i, row in shorts.iterrows():
                            sym = row['Sembol']
                            if st.button(f"🐻 {sym.replace('.IS','')} ({row['Fiyat']:.2f}) | {row['Durum']}",
                                         key=f"ict_short_{sym}_{i}", use_container_width=True):
                                on_scan_result_click(sym); st.rerun()

        with _rf_sub:
            st.markdown("<div style='text-align:center;color:#7c3aed;font-weight:800;font-size:0.7rem;"
                        "padding:2px;border-radius:4px;border:1px solid #c4b5fd;margin-bottom:4px;'>"
                        "♠️ ROYAL FLUSH NADİR SET-UP</div>", unsafe_allow_html=True)
            if st.button(f"♠️ ROYAL FLUSH TARA ({st.session_state.category})", type="secondary",
                         use_container_width=True, key="btn_scan_nadir_firsat",
                         help="4 kriter aynı anda sağlanmalı:\n\n"
                              "♠️ BOS / MSS (Bullish yapı kırılımı)\n"
                              "📈 RS Alpha > %1.5 (endeksi en az %1.5 geçiyor)\n"
                              "⚖️ VWAP sapması < %10 (aşırı şişmemiş)\n"
                              "📊 Hacim canlanması (son 3 gün > 20 gün ort×1.3 VEYA son 2 gün > önceki 5 gün×1.3)\n"
                              "💡 RSI < 65 (aşırı alım bölgesinde değil)\n\n"
                              "Yılda nadiren görülen en seçici kurulum. Tüm kriterlerin aynı anda kesişmesi gerekir."):
                with st.spinner("♠️ 4/4 Kraliyet kurulumu taranıyor (BOS/MSS + AI + RS + VWAP)..."):
                    current_assets = ASSET_GROUPS.get(st.session_state.category, [])
                    st.session_state.nadir_firsat_scan_data = scan_nadir_firsat_batch(current_assets)
                    save_scan_result("nadir_firsat_scan_data", st.session_state.nadir_firsat_scan_data, st.session_state.category)
            if st.session_state.nadir_firsat_scan_data is not None:
                _nf = st.session_state.nadir_firsat_scan_data
                if _nf.empty:
                    st.warning("4/4 Royal Flush bulunamadı.")
                else:
                    st.markdown(f"<div style='text-align:center;color:#7c3aed;font-size:0.65rem;"
                                f"font-weight:700;margin-bottom:2px;'>♠️ {len(_nf)} Hisse</div>",
                                unsafe_allow_html=True)
                    with st.container(height=150, border=False):
                        for i, row in _nf.iterrows():
                            sym   = row['Sembol']
                            fv    = row['Fiyat']
                            fs    = f"{int(fv)}" if fv >= 1000 else f"{fv:.2f}"
                            icon  = "♠️♠️" if row.get('Votes', 0) >= 7 else "♠️"
                            if st.button(f"{icon} {sym.replace('.IS','')} ({fs}) | {row['Durum']}",
                                         key=f"nadir_firsat_{sym}_{i}", use_container_width=True):
                                on_scan_result_click(sym); st.rerun()
                            _rf_ac = row.get('Aciklama', '')
                            if _rf_ac:
                                st.markdown(f"<div style='font-size:0.72rem;color:#cbd5e1;font-weight:500;margin:-6px 0 4px 4px;"
                                            f"line-height:1.3;'>{_rf_ac}</div>", unsafe_allow_html=True)


    st.markdown("<hr style='margin:10px 0;border-color:rgba(150,150,150,0.2);'>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════
    # ③ TIER 2 — Yüksek Güven (2×2 Grid)
    # ══════════════════════════════════════════════════════════
    st.markdown("<div style='border-left:5px solid #3b82f6;padding-left:10px;margin-bottom:8px;"
                "font-weight:900;font-size:1rem;color:#38bdf8;'>🥈 TIER 2 — Yüksek Güven</div>",
                unsafe_allow_html=True)

    _t2c1, _t2c2 = st.columns(2)

    with _t2c1:
        st.markdown(_scan_card_header("🔄", "Güçlü Dönüş+", 75,
            "RSI 50-65 + EMA13↑ + RS/BIST100 + VWAP + OBV · min 5/7", "#3b82f6",
            desc="Dipten döndükten sonra momentumu teyit eden hisseler — ne çok erken ne çok geç"
        ), unsafe_allow_html=True)
        if st.button(f"🔄 GÜÇLÜ DÖNÜŞ TARA ({st.session_state.category})", type="secondary", use_container_width=True, key="btn_scan_guclu_donus",
                     help="7 bağımsız kriter, min 5/7. Zorunlu: RSI 50-65. Puanlanan: EMA13↑ · EMA eğimi↑ · RS>BIST100 · RSI ivme · OBV↑ · Hacim 5+/10g · Yıllık VWAP↑. Bonus: 🔍 stop avı · ↑ haftalık."):
            with st.spinner("RSI Diverjans, Gizli Birikim ve 20-Bar Dip taranıyor..."):
                current_assets = ASSET_GROUPS.get(st.session_state.category, [])
                st.session_state.guclu_donus_data = scan_guclu_donus_batch(current_assets)
                save_scan_result("guclu_donus_data", st.session_state.guclu_donus_data, st.session_state.category)
        if st.session_state.guclu_donus_data is not None:
            df_gd = st.session_state.guclu_donus_data
            if not df_gd.empty:
                _sweep_cnt  = int(df_gd['Sweep_Ay'].sum()) if 'Sweep_Ay' in df_gd.columns else 0
                _weekly_cnt = int(df_gd['Weekly_Up'].sum()) if 'Weekly_Up' in df_gd.columns else 0
                _tam_7      = int((df_gd['Skor'] == 7).sum()) if 'Skor' in df_gd.columns else 0
                _alti_7     = int((df_gd['Skor'] == 6).sum()) if 'Skor' in df_gd.columns else 0
                _skor_str   = ""
                if _tam_7:   _skor_str += f"  ·  ⭐ {_tam_7} tam 7/7"
                if _alti_7:  _skor_str += f"  ·  ✦ {_alti_7} adet 6/7"
                st.markdown(
                    f"<div style='text-align:center;font-size:0.65rem;font-weight:700;"
                    f"color:#38bdf8;margin-bottom:3px;'>"
                    f"🔄 {len(df_gd)} Hisse{_skor_str}"
                    f"{'  ·  🔍 ' + str(_sweep_cnt) + ' Stop Avı' if _sweep_cnt else ''}"
                    f"{'  ·  ↑ ' + str(_weekly_cnt) + ' Haftalık' if _weekly_cnt else ''}"
                    f"</div>", unsafe_allow_html=True
                )
                with st.container(height=150, border=True):
                    for i, (_, row) in enumerate(df_gd.iterrows()):
                        sym    = row["Sembol"]
                        _zs    = row.get('Z-Score', 0)
                        _zs_str = f"{_zs:.1f}" if isinstance(_zs, float) else str(_zs)
                        _h10   = row.get('Hacim_10g', '-')
                        _sweep = row.get('Sweep_Ay', False)
                        _sweep_icon  = "🔍" if _sweep else ""
                        _weekly_icon = "↑" if row.get('Weekly_Up', False) else ""
                        _rsi_v   = row.get('RSI', 0)
                        _rsi_str = f"{_rsi_v:.0f}" if isinstance(_rsi_v, float) else str(_rsi_v)
                        _skor_v  = row.get('Skor', 0)
                        _rs_v    = row.get('RS_Pct', 0.0)
                        _rs_str  = f"RS:{_rs_v:+.1f}%" if isinstance(_rs_v, float) else ""
                        _lbl     = f"🔄{_sweep_icon}{_weekly_icon} {sym.replace('.IS','')} | RSI:{_rsi_str} | {_skor_v}/7 | {_rs_str}"
                        if st.button(_lbl, key=f"gd_res_btn_{sym}", use_container_width=True):
                            on_scan_result_click(sym); st.rerun()
                        _gd_ac = row.get('Aciklama', '')
                        if _gd_ac:
                            st.markdown(f"<div style='font-size:0.72rem;color:#cbd5e1;font-weight:500;margin:-6px 0 4px 4px;"
                                        f"line-height:1.3;'>{_gd_ac}</div>", unsafe_allow_html=True)
            else:
                st.warning("OBV birikim + RSI diverjans + hacim frekansı kriterlerini karşılayan hisse bulunamadı.")

    with _t2c2:
        st.markdown(_scan_card_header("🚀", "Pre-Launch BOS", 82,
            "Squeeze (≥5g) + 45g Direnç Kırılımı + Hacim + RSI<70", "#3b82f6",
            desc="Sıkışma sonrası kurumsal kırılım — hareket başlar başlamaz, kalabalık girmeden önce"
        ), unsafe_allow_html=True)
        if st.button(f"🚀 PRE-LAUNCH BOS TARA ({st.session_state.category})", type="secondary",
                     use_container_width=True, key="btn_scan_prelaunch",
                     help="İki aşamalı sert eleme:\n\n"
                          "1️⃣ Squeeze: BOS öncesi 15-25 günde en az 5 gün Bollinger/Keltner sıkışması — yay gerilmiş olmalı.\n\n"
                          "2️⃣ BOS: Son 3 gün içinde 45 günlük swing high kırıldı — direnç aşıldı.\n\n"
                          "Puanlama: Hacim (1.5x→+25), RS>BIST100→+20, RSI 50-65→+20, BOS seviyesine yakınlık→+20, SMA50 üzeri→+15. RSI>70 kesin elenme. Min 55/100."):
            with st.spinner("Squeeze tarihi + BOS kırılımı + hacim teyidi taranıyor..."):
                current_assets = ASSET_GROUPS.get(st.session_state.category, [])
                st.session_state.prelaunch_bos_data = scan_prelaunch_bos(current_assets)
                save_scan_result("prelaunch_bos_data", st.session_state.prelaunch_bos_data, st.session_state.category)
        if st.session_state.prelaunch_bos_data is not None:
            df_pb = st.session_state.prelaunch_bos_data
            if not df_pb.empty:
                _d0 = int((df_pb['BOS_Day'] == 0).sum())
                _d1 = int((df_pb['BOS_Day'] == 1).sum())
                _d2p = int((df_pb['BOS_Day'] >= 2).sum())
                _day_str = ""
                if _d0: _day_str += f"  ·  ⚡ {_d0} bugün"
                if _d1: _day_str += f"  ·  🕐 {_d1} dün"
                if _d2p: _day_str += f"  ·  ⏳ {_d2p} eski"
                st.markdown(
                    f"<div style='text-align:center;font-size:0.65rem;font-weight:700;"
                    f"color:#38bdf8;margin-bottom:3px;'>"
                    f"🚀 {len(df_pb)} Hisse{_day_str}"
                    f"</div>", unsafe_allow_html=True
                )
                with st.container(height=150, border=True):
                    for i, (_, row) in enumerate(df_pb.iterrows()):
                        sym        = row['Sembol']
                        day_icon   = ["⚡", "🕐", "⏳", "⏳"][int(row.get('BOS_Day', 0))]
                        skor_v     = row.get('Skor', 0)
                        rsi_v      = row.get('RSI', 0)
                        vol_k      = row.get('Hacim_Kat', 0)
                        rs_v       = row.get('RS_Pct', 0.0)
                        rs_str     = f"RS:{rs_v:+.1f}%" if isinstance(rs_v, float) else ""
                        _pb_double = sym in _double_hit_syms
                        lbl = (f"{day_icon}{'💎' if _pb_double else ''} {sym.replace('.IS','')} | "
                               f"Skor:{skor_v} | RSI:{rsi_v:.0f} | Vol:{vol_k:.1f}x | {rs_str}")
                        if st.button(lbl, key=f"pb_btn_{sym}_{i}", use_container_width=True):
                            on_scan_result_click(sym); st.rerun()
                        if _pb_double:
                            st.markdown("<div style='font-size:0.68rem;color:#818cf8;font-weight:700;"
                                        "margin:-6px 0 2px 4px;'>💎 ELİTLER'de de var</div>",
                                        unsafe_allow_html=True)
                        _pb_ac = row.get('Aciklama', '')
                        if _pb_ac:
                            st.markdown(f"<div style='font-size:0.72rem;color:#cbd5e1;font-weight:600;"
                                        f"margin:-4px 0 4px 4px;line-height:1.3;'>{_pb_ac}</div>",
                                        unsafe_allow_html=True)
            else:
                st.warning("Squeeze + BOS kriterlerini aynı anda karşılayan hisse bulunamadı.")

    _t2c3, _t2c4 = st.columns(2)

    with _t2c3:
        st.markdown(_scan_card_header("🦁", "Minervini SEPA", 76,
            "VCP + SMA hizalama + RS güç", "#3b82f6",
            desc="Dünya şampiyonunun metodolojisi: dar bant, güçlü trend, doğru an"
        ), unsafe_allow_html=True)
        if st.button(f"🦁 SEPA TARAMASI ({st.session_state.category})", type="secondary", use_container_width=True, key="btn_scan_sepa",
                     help="Mark Minervini'nin onlarca yıllık şampiyon hisse araştırmasına dayanan tarama yöntemi.\n\nKriterler: Fiyat 50, 150 ve 200 günlük ortalamalarının hepsinin üstünde ve bu ortalamalar doğru sırada hizalanmış olmalı. Hisse piyasadan güçlü (RS > 70) ve son 52 haftanın dibinden en az %25 yukarıda olmalı.\n\nEk bonus: VCP (Volatility Contraction Pattern) — hisse giderek daralan bir sıkışma içindeyse ve hacimli kırılım yaşandıysa 'Süper' etiketiyle çıkar."):
            with st.spinner("Aslan avda... Trend şablonu, VCP ve RS taranıyor..."):
                current_assets = ASSET_GROUPS.get(st.session_state.category, [])
                st.session_state.minervini_data = scan_minervini_batch(current_assets)
        if st.session_state.minervini_data is not None:
            if len(st.session_state.minervini_data) > 0:
                with st.container(height=150, border=True):
                    for i, row in st.session_state.minervini_data.iterrows():
                        sym = row['Sembol']
                        icon = "💎💎" if "SÜPER" in row['Durum'] else "🔥"
                        if st.button(f"{icon} {sym} ({row['Fiyat']}) | {row['Durum']}", key=f"sepa_{sym}_{i}", use_container_width=True):
                            on_scan_result_click(sym); st.rerun()
                        _sepa_detay = row.get('Detay', '')
                        if _sepa_detay:
                            st.markdown(f"<div style='font-size:0.72rem;color:#cbd5e1;font-weight:500;margin:-6px 0 4px 4px;"
                                        f"line-height:1.3;'>{_sepa_detay}</div>", unsafe_allow_html=True)
            else:
                st.warning("Bu zorlu kriterlere uyan hisse bulunamadı.")

    with _t2c4:
        st.markdown(_scan_card_header("💎", "Altın Fırsat & VIP Formasyon", 78,
            "RS güçlü + Discount + Hacim + Formasyon", "#16a34a",
            desc="Güçlü ama henüz pahalılaşmamış — büyük oyuncuya yakın erken konum"
        ), unsafe_allow_html=True)
        if st.button(f"💎 ALTIN SET-UP & VIP FORMASYON TARA ({st.session_state.category})", type="secondary", use_container_width=True, key="btn_scan_golden_vip",
                     help="İki aşamalı eleme: önce Altın Fırsat kriterleri (RS güçlü + Discount + Hacim), sonra formasyon (Fincan-Kulp, TOBO, Üçgen, Range). Formasyon bulunamazsa Hazırlık listesine girer."):
            with st.spinner("Fincan-Kulp, TOBO ve Üçgenlerde Altın Fırsat aranıyor..."):
                current_assets = ASSET_GROUPS.get(st.session_state.category, [])
                st.session_state.golden_pattern_data = scan_golden_pattern_agent(current_assets, st.session_state.get('category', 'S&P 500'))
                save_scan_result("golden_pattern_data", st.session_state.golden_pattern_data, st.session_state.category)
                st.rerun()
        _gp_data = st.session_state.get('golden_pattern_data')
        if _gp_data is not None:
            _formations = _gp_data.get("formations", pd.DataFrame()) if isinstance(_gp_data, dict) else _gp_data
            _hazirlik   = _gp_data.get("hazirlik",   pd.DataFrame()) if isinstance(_gp_data, dict) else pd.DataFrame()
            if not _formations.empty:
                with st.container(height=150, border=True):
                    for _gpi, _gpr in _formations.head(12).iterrows():
                        _gps = _gpr['Sembol']
                        _prefix = "♠️" if _gpr.get('is_nadir', False) else "🚀"
                        _mf_v = _gpr.get('Mansfield', '-')
                        _mf_icon = "📈" if (isinstance(_mf_v, float) and _mf_v > 0) else "📉"
                        if st.button(f"{_prefix} {_gps.replace('.IS','')} | Skor:{_gpr['Puan']} | RS:{_mf_icon}{_mf_v}", key=f"gvip_btn_{_gps}_{_gpi}", use_container_width=True):
                            on_scan_result_click(_gps); st.rerun()
                        _gp_detay = _gpr.get('Detay', '')
                        if _gp_detay:
                            st.markdown(f"<div style='font-size:0.72rem;color:#1e3a5f;font-weight:600;margin:-6px 0 4px 4px;"
                                        f"line-height:1.3;'>{_gp_detay}</div>", unsafe_allow_html=True)
            elif not _hazirlik.empty:
                with st.container(height=120, border=True):
                    for _hzi, _hzr in _hazirlik.head(10).iterrows():
                        _hzs = _hzr['Sembol']
                        if st.button(f"⏳ {_hzs.replace('.IS','')} — {_hzr['Durum']}", key=f"gvip_hz_{_hzs}_{_hzi}", use_container_width=True):
                            on_scan_result_click(_hzs); st.rerun()
            else:
                st.caption("Altın Fırsat & VIP Formasyon bulunamadı.")



    # 5. GELİŞMİŞ TEKNİK KART
    render_detail_card_advanced(st.session_state.ticker)

    st.markdown(f"<div style='font-size:0.9rem;font-weight:600;margin-bottom:4px; margin-top:20px;'>📡 {st.session_state.ticker} hakkında haberler ve analizler</div>", unsafe_allow_html=True)
    symbol_raw = st.session_state.ticker; base_symbol = (symbol_raw.replace(".IS", "").replace("=F", "").replace("-USD", "")); lower_symbol = base_symbol.lower()
    st.markdown(f"""<div class="news-card" style="display:flex; flex-wrap:wrap; align-items:center; gap:8px; border-left:none;"><a href="https://seekingalpha.com/symbol/{base_symbol}/news" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.8rem; font-weight:600;">SeekingAlpha</div></a><a href="https://finance.yahoo.com/quote/{base_symbol}/news" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.8rem; font-weight:600;">Yahoo Finance</div></a><a href="https://www.nasdaq.com/market-activity/stocks/{lower_symbol}/news-headlines" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.8rem; font-weight:600;">Nasdaq</div></a><a href="https://stockanalysis.com/stocks/{lower_symbol}/" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.8rem; font-weight:600;">StockAnalysis</div></a><a href="https://finviz.com/quote.ashx?t={base_symbol}&p=d" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.8rem; font-weight:600;">Finviz</div></a><a href="https://unusualwhales.com/stock/{base_symbol}/overview" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.7rem; font-weight:600;">UnusualWhales</div></a></div>""", unsafe_allow_html=True)

    # --- GİZLİ TEMETTÜ / BÖLÜNME SIFIRLAMA + VERİ TAZELE BUTONLARI ---
    col_reset, col_refresh, _ = st.columns([1, 1, 2])
    with col_reset:
        with st.expander("⚙️ Veriyi Onar (Temettü/Bölünme)"):
            if st.button("🔄 Sıfırla ve İndir", use_container_width=True, key="reset_data_btn"):
                t_clean = st.session_state.ticker.replace(".IS", "")
                if "BIST" in st.session_state.category or ".IS" in st.session_state.ticker:
                    t_clean = st.session_state.ticker if st.session_state.ticker.endswith(".IS") else f"{st.session_state.ticker}.IS"
                
                file_path = os.path.join(CACHE_DIR, f"{t_clean}_1d.parquet")
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        get_safe_historical_data.clear()
                        get_batch_data_cached.clear()
                        st.toast(f"✅ {st.session_state.ticker} verileri silindi! Yeniden indiriliyor...", icon="🔄")
                    except Exception as e:
                        st.error(f"Silme hatası: {e}")
                else:
                    get_safe_historical_data.clear()
                    get_batch_data_cached.clear()
                    st.toast("⚠️ Dosya bulunamadı ama önbellek temizlendi.", icon="⚠️")
                
                st.rerun()

    with col_refresh:
        with st.expander("🔄 Veriyi Tazele (Kategorideki Tüm hisseler)"):
            st.caption("Seçili kategorideki tüm eski parquet verilerini yeniden indirir.")
            if st.button("▶ Güncellemeyi Başlat", use_container_width=True, key="refresh_all_btn"):
                _ref_cat   = st.session_state.get('category', 'BIST 500 ')
                _ref_list  = ASSET_GROUPS.get(_ref_cat, [])
                _ref_total = len(_ref_list)
                _ref_bar   = st.progress(0, text="Başlatılıyor...")
                _ref_ok = 0; _ref_fail = 0
                get_safe_historical_data.clear()
                get_batch_data_cached.clear()
                for _ri, _rt in enumerate(_ref_list):
                    try:
                        _df_r = get_safe_historical_data(_rt, period="1y")
                        if _df_r is not None and not _df_r.empty:
                            _ref_ok += 1
                        else:
                            _ref_fail += 1
                    except:
                        _ref_fail += 1
                    _ref_bar.progress(
                        (_ri + 1) / _ref_total,
                        text=f"{_rt} ({_ri+1}/{_ref_total})"
                    )
                _ref_bar.empty()
                st.toast(f"✅ {_ref_ok} güncellendi, {_ref_fail} başarısız.", icon="🔄")
                st.rerun()

# --- SAĞ SÜTUN ---
with col_right:
    if not info: info = fetch_stock_info(st.session_state.ticker)

    # 1. Fiyat (YENİ TERMİNAL GÖRÜNÜMÜ)
    if info and info.get('price'):
        display_ticker = get_display_name(st.session_state.ticker)
        price_val = info.get('price', 0)
        change_val = info.get('change_pct', 0)

        # Rengi Belirle
        if change_val >= 0:
            bg_color = "#81bb96"  # Yeşil
            arrow = "▲"
            shadow_color = "rgba(22, 163, 74, 0.4)"
        else:
            bg_color = "#9B7C99"  # Kırmızı
            arrow = "▼"
            shadow_color = "rgba(220, 38, 38, 0.4)"

        # HTML Kodları
        st.markdown(f"""<div style="background-color:{bg_color}; border-radius:12px; padding:15px; color:white; text-align:center; box-shadow: 0 10px 15px -3px {shadow_color}, 0 4px 6px -2px rgba(0,0,0,0.05); margin-bottom:15px; border: 1px solid rgba(255,255,255,0.2);">
<div style="font-size:1.1rem; font-weight:600; opacity:0.9; letter-spacing:1px; margin-bottom:5px; text-transform:uppercase;">FİYAT: {display_ticker}</div>
<div style="font-family:'JetBrains Mono', monospace; font-size:2.4rem; font-weight:800; line-height:1; text-shadow: 0 2px 4px rgba(0,0,0,0.2);">{price_val:.2f}</div>
<div style="margin-top:10px;">
<span style="background:rgba(255,255,255,0.25); color:white; font-weight:700; font-size:1.1rem; padding:4px 12px; border-radius:20px; backdrop-filter: blur(4px);">
{arrow} %{change_val:.2f}
</span>
</div>
</div>""", unsafe_allow_html=True)
    
    else:
        st.warning("Fiyat verisi alınamadı.")

    # --- FORMASYON BUTONU — fiyat paneli hemen altında ---
    _fcd = st.session_state.get('_formasyon_chart_data')
    if _fcd and isinstance(_fcd, dict):
        _fpl  = st.session_state.get('_formasyon_pat_label', 'Formasyon')
        _fpr  = st.session_state.get('_formasyon_current_price', 0)
        _ftk  = st.session_state.get('_formasyon_ticker', st.session_state.ticker)
        _fdsp = st.session_state.get('_formasyon_display', get_display_name(st.session_state.ticker))
        _fdrk = False
        # Yön: sr_level'de is_support=False → Short, diğer tüm formasyon tipleri → Long
        _f_bull = not (_fcd.get('type') == 'sr_level' and not _fcd.get('is_support', True))
        _fbtn_bg  = "#81bb96" if _f_bull else "#9B7C99"
        _fbtn_brd = "#3d8c5a" if _f_bull else "#6b3a5c"
        _fbtn_hov = "#6aaa82" if _f_bull else "#876a85"
        st.markdown(f"""<style>
            div.st-key-btn_formasyon_dialog button {{
                background:{_fbtn_bg} !important;
                border:2px solid {_fbtn_brd} !important;
                color:white !important;
                font-weight:700 !important;
            }}
            div.st-key-btn_formasyon_dialog button:hover {{
                background:{_fbtn_hov} !important;
                border-color:{_fbtn_brd} !important;
            }}
        </style>""", unsafe_allow_html=True)
        if st.button(f"📊 {_fdsp}-{_fpl}", use_container_width=True, key="btn_formasyon_dialog"):
            _formasyon_dialog(_ftk, _fcd, _fpr, _fdsp, _fpl, _fdrk)

    # --- HARMONİK FORMASYON BUTONU — formasyon butonunun hemen altında ---
    try:
        _hdf = get_safe_historical_data(st.session_state.ticker, period="1y")
        _hres = calculate_harmonic_patterns(st.session_state.ticker, _hdf) if _hdf is not None else None
    except Exception:
        _hres = None
    if _hres and isinstance(_hres, dict):
        _hdsp   = get_display_name(st.session_state.ticker)
        _hpat   = _hres.get('pattern', 'Harmonik')
        _hprice = float(_hres.get('curr_price', info.get('price', 0) if info else 0))
        _hdrk   = False
        _h_bull   = _hres.get('direction', 'Bullish') == 'Bullish'
        _hbtn_bg  = "#81bb96" if _h_bull else "#9B7C99"
        _hbtn_brd = "#3d8c5a" if _h_bull else "#6b3a5c"
        _hbtn_hov = "#6aaa82" if _h_bull else "#876a85"
        st.markdown(f"""<style>
            div.st-key-btn_harmonik_dialog button {{
                background:{_hbtn_bg} !important;
                border:2px solid {_hbtn_brd} !important;
                color:white !important;
                font-weight:700 !important;
            }}
            div.st-key-btn_harmonik_dialog button:hover {{
                background:{_hbtn_hov} !important;
                border-color:{_hbtn_brd} !important;
            }}
        </style>""", unsafe_allow_html=True)
        if st.button(f"🔮 {_hdsp}-{_hpat}", use_container_width=True, key="btn_harmonik_dialog"):
            _harmonik_dialog(st.session_state.ticker, _hres, _hprice, _hdsp, _hdrk)

    # --- BİRLEŞİK SİNYAL PANELİ (Canlı Sinyaller + Tarama Sonuçları) ---
    render_unified_signals_panel(st.session_state.ticker)

    # 2. Price Action Paneli
    render_price_action_panel(st.session_state.ticker)

    # 🦅 YENİ: ICT SNIPER ONAY RAPORU (Sadece Setup Varsa Çıkar)
    render_ict_certification_card(st.session_state.ticker)

    # --- YENİ EKLEME: ALTIN ÜÇLÜ KONTROL PANELİ ---
    # Verileri taze çekelim ki hata olmasın
    try:
        ict_data_check = calculate_ict_deep_analysis(st.session_state.ticker)
        # DÜZELTME: Resimdeki doğru fonksiyon ismini kullandık:
        sent_data_check = calculate_sentiment_score(st.session_state.ticker) 
        # 2. Fonksiyonu çağır (Sadece 3/3 ise ekrana basacak, yoksa boş geçecek)
        render_golden_trio_banner(ict_data_check, sent_data_check, ticker=st.session_state.ticker)
    except Exception as e:
        pass # Bir hata olursa sessizce geç, ekranı bozma.

    # Platin Fırsat (Elit) — tarama yapmadan canlı hesaplar (AF + SMA200 + SMA50 + RSI < 70)
    render_platin_live_banner(st.session_state.ticker, ict_data_check, sent_data_check)

    # ÇİFT TEYİT — ELİT + Pre-Launch BOS (önce göster, en önemli)
    render_double_hit_banner(st.session_state.ticker, ict_data_check, sent_data_check)

    # Güçlü Dönüş Adayları — bireysel hisse banner'ı
    render_guclu_donus_banner(st.session_state.ticker)

    # Pre-Launch BOS — bireysel hisse banner'ı
    render_prelaunch_bos_banner(st.session_state.ticker)

    # Harmonik Formasyon — bireysel hisse banner'ı
    render_harmonic_banner(st.session_state.ticker)

    # Harmonik Confluence (3'lü teyit) — varsa Royal Flush Nadir Fırsat/Altın Fırsat seviyesinde rozet
    render_harmonic_confluence_banner(st.session_state.ticker)

    # 💎 VIP FORMASYON — Altın Fırsat + Geometrik Yapı batch tarama sonucu
    try:
        _gp_live = st.session_state.get('golden_pattern_data')
        if isinstance(_gp_live, dict):
            _gp_lf = _gp_live.get('formations', pd.DataFrame())
            if not _gp_lf.empty and 'Sembol' in _gp_lf.columns:
                _gp_lr = _gp_lf[_gp_lf['Sembol'] == st.session_state.ticker]
                if not _gp_lr.empty:
                    _gp_lrow   = _gp_lr.iloc[0]
                    _gp_ldetay = _gp_lrow.get('Detay', '')
                    _gp_lpuan  = _gp_lrow.get('Puan', 0)
                    _gp_lnadir = _gp_lrow.get('is_nadir', False)
                    _gp_border = "#7c3aed" if _gp_lnadir else "#16a34a"
                    _gp_bg     = "rgba(124,58,237,0.08)" if _gp_lnadir else "rgba(22,163,74,0.08)"
                    _gp_ikon   = "♠️ PLATİN VIP FORMASYON" if _gp_lnadir else "💎 ALTIN SET-UP + VIP FORMASYON"
                    st.markdown(
                        f"<div style='border:2px solid {_gp_border};border-radius:10px;"
                        f"padding:10px 14px;background:{_gp_bg};margin-bottom:8px;'>"
                        f"<div style='font-size:0.8rem;font-weight:800;color:{_gp_border};"
                        f"margin-bottom:4px;'>{_gp_ikon} — Skor: {_gp_lpuan}/100</div>"
                        f"<div style='font-size:0.78rem;color:#cbd5e1;line-height:1.4;'>{_gp_ldetay}</div>"
                        f"<div style='font-size:0.68rem;color:#94a3b8;margin-top:4px;'>"
                        f"Güç + Ucuzluk + Enerji + Geometrik yapı — dört kriter aynı anda çakıştı</div>"
                        f"</div>",
                        unsafe_allow_html=True
                    )
    except:
        pass

    # 📦 DARVAS BOX — Bireysel hisse banner'ı (kalite ≥ 75 ise göster)
    try:
        _df_darvas_live = get_safe_historical_data(st.session_state.ticker, period="6mo")
        if _df_darvas_live is not None and len(_df_darvas_live) >= 60:
            _dbox_live = detect_darvas_box(_df_darvas_live)
            if _dbox_live and _dbox_live['quality'] >= 75:
                _dl_st  = _dbox_live['status']
                _dl_q   = _dbox_live['quality']
                _dl_age = _dbox_live['box_age']
                _dl_top = _dbox_live['box_top']
                _dl_bot = _dbox_live['box_bottom']
                _dl_cls = _dbox_live.get('breakout_class')
                _dl_vr  = _dbox_live.get('vol_ratio', 1.0)
                if _dl_st == 'breakout' and _dl_cls == 'A':
                    _dl_border = "#f59e0b"; _dl_bg = "rgba(245,158,11,0.08)"
                    _dl_title  = "⭐📦 DARVAS A-SINYAL — 3/3 Kapı Açık"
                    _dl_desc   = f"{_dl_age} günlük birikim kutusu hacimle kırıldı · Tavan:{_dl_top} → Taban:{_dl_bot} · Hacim:{_dl_vr:.1f}x"
                elif _dl_st == 'breakout':
                    _dl_border = "#38bdf8"; _dl_bg = "rgba(56,189,248,0.07)"
                    _dl_title  = "📦 Darvas Kırılım (Kısmi Onay)"
                    _dl_desc   = f"{_dl_age} günlük kutu kırıldı · Tavan:{_dl_top} · Hacim teyidi eksik"
                else:
                    _dl_border = "#6366f1"; _dl_bg = "rgba(99,102,241,0.07)"
                    _dl_title  = "🟦 Darvas Kutu Oluşuyor"
                    _dl_desc   = f"{_dl_age} günlük konsolidasyon · Tavan:{_dl_top} → Taban:{_dl_bot} · {_dl_top} üstü kapanış kırılım tetikler"
                st.markdown(
                    f"<div style='border:2px solid {_dl_border};border-radius:10px;"
                    f"padding:10px 14px;background:{_dl_bg};margin-bottom:8px;'>"
                    f"<div style='font-size:0.8rem;font-weight:800;color:{_dl_border};"
                    f"margin-bottom:4px;'>{_dl_title} · Kalite: {_dl_q}/100</div>"
                    f"<div style='font-size:0.78rem;color:#cbd5e1;line-height:1.4;'>{_dl_desc}</div>"
                    f"</div>",
                    unsafe_allow_html=True
                )
    except:
        pass

    st.markdown("<hr style='margin-top:15px; margin-bottom:10px;'>", unsafe_allow_html=True)


    st.markdown("<hr style='margin-top:15px; margin-bottom:10px; border-color: rgba(150,150,150,0.2);'>", unsafe_allow_html=True)

    # ESKİ DÜZ ÇİZGİYİ SİLDİK, YERİNE MODERN BAŞLIK EKLİYORUZ
    header_html_bottom = """
    <div style="
        background: linear-gradient(135deg, rgba(59, 130, 246, 0.1) 0%, rgba(16, 185, 129, 0.05) 100%);
        border: 1px solid rgba(59, 130, 246, 0.3);
        border-radius: 12px;
        padding: 16px 20px;
        margin-top: 30px;
        margin-bottom: 20px;
        box-shadow: 0 4px 10px rgba(0, 0, 0, 0.05);
        display: flex;
        align-items: center;
        justify-content: center;
    ">
        <div style="
            margin: 0; 
            padding: 0; 
            color: var(--text-color); 
            font-weight: 800; 
            font-size: 1.3rem; 
            letter-spacing: 0.5px;
            text-align: center;
        ">
            <span style="color:#38bdf8;">⚡ GELİŞMİŞ RADARLAR</span> VE SİNYALLER
        </div>
    </div>
    """
    st.markdown(header_html_bottom, unsafe_allow_html=True)

    # ALT SEKMELERİ YİNE ŞIK BİR ÇERÇEVE (CONTAINER) İÇİNE ALIYORUZ
    with st.container(border=True):
        
        tab_radar, tab_top20, tab_perf = st.tabs([
            "📡 RADARLAR",
            "👑 TOP 20 MASTER",
            "📊 SİNYAL PERFORMANSI"
        ])
    # ---------------------------------------------------------
    # SEKME: 📡 RADARLAR VE KESİŞİMLER (R1 + R2)
    # ---------------------------------------------------------
    with tab_radar:
        if st.button("🔄 Radar 1 & 2'yi Tara", use_container_width=True, key="btn_r1_r2_scan"):
            with st.spinner("Momentum ve Trend verileri taranıyor..."):
                curr_assets = ASSET_GROUPS.get(st.session_state.category, [])
                st.session_state.scan_data = analyze_market_intelligence(curr_assets, st.session_state.get('category', 'S&P 500'))
                st.session_state.radar2_data = radar2_scan(curr_assets)
                st.rerun()

        df1 = st.session_state.get('scan_data')
        df2 = st.session_state.get('radar2_data')

        # ORTAK FIRSATLAR KISMI
        st.markdown(f"<div style='font-size:0.9rem;font-weight:bold; margin-bottom:8px; color:#38bdf8; background-color:rgba(30, 64, 175, 0.05); padding:5px; border-radius:5px; border:1px solid #1e40af; text-align:center;'>🎯 Ortak Set-Up'lar (R1 + R2 Kesişim)</div>", unsafe_allow_html=True)
        if df1 is not None and df2 is not None and not df1.empty and not df2.empty:
            commons = []
            symbols = set(df1["Sembol"]).intersection(set(df2["Sembol"]))
            for sym in symbols:
                r1_s = float(df1[df1["Sembol"]==sym].iloc[0]["Skor"])
                r2_s = float(df2[df2["Sembol"]==sym].iloc[0]["Skor"])
                if r1_s + r2_s >= 11: commons.append({"sym": sym, "r1": r1_s, "r2": r2_s, "tot": r1_s+r2_s})
            
            if commons:
                sorted_commons = sorted(commons, key=lambda x: x["tot"], reverse=True)
                cols_c = st.columns(2) 
                for i, item in enumerate(sorted_commons):
                    temiz_sembol_ortak = item['sym'].replace('.IS', '').replace('-USD', '')
                    if cols_c[i % 2].button(f"🎯 {temiz_sembol_ortak}\nSkor: {int(item['tot'])}", key=f"com_tab_{item['sym']}", use_container_width=True, help=f"R1: {int(item['r1'])} | R2: {int(item['r2'])}"):
                        on_scan_result_click(item['sym']); st.rerun()
            else:
                st.caption("Kesişim bulunamadı.")
        else:
            st.caption("Tarama yapıldığında burada kesişen hisseler görünür.")

        st.markdown("<hr style='margin:10px 0; border-color: rgba(150,150,150,0.1);'>", unsafe_allow_html=True)
        
        # RADAR 1 LİSTESİ
        st.markdown("<div style='font-size:0.8rem; font-weight:bold; color:#38bdf8; margin-bottom:5px;'>🧠 Radar 1 (Momentum)</div>", unsafe_allow_html=True)
        with st.container(height=150, border=False):
            if df1 is not None and not df1.empty:
                cols_r1 = st.columns(3)
                for i, row in df1.head(15).iterrows():
                    sym = row["Sembol"]
                    if cols_r1[i % 3].button(f"🔥 {int(row['Skor'])}/7\n{sym.replace('.IS','')}", key=f"r1_tab_{sym}_{i}", use_container_width=True):
                        on_scan_result_click(sym); st.rerun()
            else: st.caption("Veri yok.")

        st.markdown("<hr style='margin:10px 0; border-color: rgba(150,150,150,0.1);'>", unsafe_allow_html=True)
        
        # RADAR 2 LİSTESİ
        st.markdown("<div style='font-size:0.8rem; font-weight:bold; color:#4ade80; margin-bottom:5px;'>🚀 Radar 2 (Trend Setup)</div>", unsafe_allow_html=True)
        with st.container(height=150, border=False):
            if df2 is not None and not df2.empty:
                cols_r2 = st.columns(3)
                for i, row in df2.head(15).iterrows():
                    sym   = row["Sembol"]
                    setup = row['Setup'] if row['Setup'] != "-" else "Trend"
                    _dq   = row['Darvas_Quality'] if 'Darvas_Quality' in row.index and row['Darvas_Quality'] is not None else None
                    _ds   = row['Darvas_Status']  if 'Darvas_Status'  in row.index else None
                    _dc   = row['Darvas_Class']   if 'Darvas_Class'   in row.index else None
                    _da   = row['Darvas_Age']      if 'Darvas_Age'     in row.index else None
                    # Darvas badge — sadece kalite ≥ 75 ise göster
                    _dbadge = ""
                    _dtip   = ""
                    if _dq is not None and _dq >= 75:
                        if _ds == 'breakout' and _dc == 'A':
                            _dbadge = " ⭐📦"
                            _dtip   = f" · ⭐ DARVAS A-SINYAL: {_da}g kutu kırıldı, 3/3 kapı açık (Kalite:{_dq}/100)"
                        elif _ds == 'breakout':
                            _dbadge = " 📦"
                            _dtip   = f" · 📦 Darvas Kırılım: {_da}g kutu (Kalite:{_dq}/100)"
                        else:
                            _dbadge = " 🟦"
                            _dtip   = f" · 🟦 Darvas Kutu Oluşuyor: {_da}g (Kalite:{_dq}/100)"
                    _help = f"Setup: {setup}{_dtip}"
                    if cols_r2[i % 3].button(f"🚀 {int(row['Skor'])}/7\n{sym.replace('.IS','')}{_dbadge}", key=f"r2_tab_{sym}_{i}", use_container_width=True, help=_help):
                        on_scan_result_click(sym); st.rerun()
            else: st.caption("Veri yok.")

    # ---------------------------------------------------------
    # SEKME: 👑 TOP 20 MASTER LİSTE
    # ---------------------------------------------------------
    with tab_top20:
        if 'top_20_summary' in st.session_state and st.session_state.top_20_summary:
            st.markdown('<div style="font-size:0.85rem; color:#64748b; margin-bottom:10px; text-align:center;">Tüm algoritmalardan en çok onay alan elit hisseler</div>', unsafe_allow_html=True)
            # --- Clickable card CSS overlay ---
            st.markdown("""<style>
.t20wrap { position:relative; margin-bottom:18px; cursor:pointer; }
.t20wrap:hover .t20card { opacity:0.88; transition:opacity 0.15s; }
/* Pull the Streamlit button immediately after .t20wrap up to cover the card */
.t20wrap + div[data-testid="element-container"] {
    position:relative; z-index:10; margin-top:-120px; height:120px;
}
.t20wrap + div[data-testid="element-container"] button {
    width:100% !important; height:120px !important;
    background:transparent !important; border:none !important;
    box-shadow:none !important; color:transparent !important;
    cursor:pointer !important; position:absolute; top:0; left:0;
}
</style>""", unsafe_allow_html=True)
            with st.container(height=500, border=True):
                for i, item in enumerate(st.session_state.top_20_summary):
                    sym = item['Sembol'].replace('.IS', '')
                    score = int(item['score'])
                    onay_sayisi = item.get('onay_sayisi', 0)
                    price_val = float(item['price'])
                    price_str = f"{int(price_val)}" if price_val >= 1000 else f"{price_val:.2f}"
                    sources_str = ", ".join(item['sources'][:3])
                    if len(item['sources']) > 3: sources_str += "..."
                    if score >= 80:
                        bg_color = "#fffbeb"; border_color = "#f59e0b"
                        score_bg = "linear-gradient(90deg, #f59e0b 0%, #d97706 100%)"
                    elif score >= 50:
                        bg_color = "#f8fafc"; border_color = "#3b82f6"
                        score_bg = "#3b82f6"
                    else:
                        bg_color = "#ffffff"; border_color = "#475569"
                        score_bg = "#64748b"
                    sym_clr    = "#0f172a"
                    detail_clr = "#0f172a"
                    onay_badge = (f'<span style="background:linear-gradient(90deg,#7c3aed,#4f46e5);color:white;padding:2px 8px;border-radius:10px;font-size:0.72rem;font-weight:800;margin-left:6px;">🎖️ {onay_sayisi} Onay</span>'
                                  if onay_sayisi >= 4 else
                                  f'<span style="background:#0d1829;color:#cbd5e1;padding:2px 8px;border-radius:10px;font-size:0.72rem;font-weight:700;margin-left:6px;">✅ {onay_sayisi} Onay</span>')
                    st.markdown(f"""
<div class="t20wrap">
  <div class="t20card" style="background-color:{bg_color};padding:12px;border-radius:8px;border:2px solid {border_color};">
    <div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid {border_color}44;padding-bottom:6px;margin-bottom:8px;">
      <span style="font-weight:900;font-size:1.2rem;color:{sym_clr};">{sym} {onay_badge}</span>
      <span style="background:{score_bg};color:white;padding:2px 10px;border-radius:12px;font-weight:800;font-size:0.8rem;">Skor: {score}/100</span>
    </div>
    <div style="font-size:0.8rem;color:{detail_clr};font-weight:600;line-height:1.4;padding:8px;background:rgba(255,255,255,0.07);border-radius:5px;border-left:4px solid {border_color};">{item['katalizor']}</div>
    <div style="font-size:0.65rem;color:#94a3b8;margin-top:8px;font-weight:700;"><span style="color:#94a3b8;">Kesişen Sinyaller:</span> {sources_str}</div>
  </div>
</div>""", unsafe_allow_html=True)
                    if st.button(sym, key=f"top20b_btn_{sym}_{i}", use_container_width=True):
                        st.session_state.ticker = item['Sembol']; st.rerun()
        else:
            st.info("Lütfen sol menüdeki 'TÜM PİYASAYI TARA' butonunu kullanarak listeyi oluşturun.")

    # ---------------------------------------------------------
    # SEKME: 📊 SİNYAL PERFORMANSI
    # ---------------------------------------------------------
    with tab_perf:
        st.markdown(
            "<div style='font-size:0.85rem; color:#64748b; margin-bottom:12px; text-align:center;'>"
            "Scan sinyallerinin gerçekleşmiş fiyat performansı — her gün otomatik güncellenir"
            "</div>",
            unsafe_allow_html=True
        )

        lookback = st.selectbox(
            "Değerlendirme Penceresi",
            options=[30, 60, 90],
            index=2,
            format_func=lambda x: f"Son {x} gün",
            key="perf_lookback"
        )

        with st.spinner("Sinyal performansı hesaplanıyor..."):
            df_summary = get_signal_performance_summary(lookback_days=lookback)

        if df_summary.empty:
            st.info(
                "Henüz yeterli sinyal verisi yok. "
                "Scan'ler çalıştıkça ve en az 5 iş günü geçtikçe burada performans görünmeye başlar."
            )
        else:
            # Özet tablo
            st.dataframe(
                df_summary,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Tarama":     st.column_config.TextColumn("Tarama Türü", width="medium"),
                    "Sinyal":     st.column_config.NumberColumn("Sinyal #",   width="small"),
                    "Hit 5G":     st.column_config.TextColumn("Hit Rate 5G",  width="small"),
                    "Hit 10G":    st.column_config.TextColumn("Hit Rate 10G", width="small"),
                    "Hit 20G":    st.column_config.TextColumn("Hit Rate 20G", width="small"),
                    "Ort +5G":    st.column_config.TextColumn("Ort. +5G",     width="small"),
                    "Ort +10G":   st.column_config.TextColumn("Ort. +10G",    width="small"),
                    "Ort +20G":   st.column_config.TextColumn("Ort. +20G",    width="small"),
                }
            )

            # Ham sinyal detay tablosu (opsiyonel genişletme)
            with st.expander("📋 Ham Sinyal Detayları", expanded=False):
                df_raw = evaluate_signals(lookback_days=lookback)
                if not df_raw.empty:
                    show_cols = ['Sembol', 'Tarama', 'Sinyal Tarihi', 'Giriş',
                                 'Getiri_5G', 'Getiri_10G', 'Getiri_20G', 'Geçen Gün']
                    show_cols = [c for c in show_cols if c in df_raw.columns]

                    def _color_ret(val):
                        if val is None or (isinstance(val, float) and pd.isna(val)):
                            return ''
                        return 'color: #16a34a; font-weight:bold' if float(val) > 0 else 'color:#f87171; font-weight:bold'

                    styled = df_raw[show_cols].style.applymap(
                        _color_ret,
                        subset=[c for c in ['Getiri_5G', 'Getiri_10G', 'Getiri_20G'] if c in show_cols]
                    )
                    st.dataframe(styled, use_container_width=True, hide_index=True)
                else:
                    st.caption("Değerlendirilebilir sinyal bulunamadı.")