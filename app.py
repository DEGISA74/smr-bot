import streamlit as st
import yfinance as yf
import pandas as pd
import feedparser
import urllib.parse
from ta.volume import VolumeWeightedAveragePrice
from textblob import TextBlob
from datetime import datetime, timedelta
import streamlit.components.v1 as components
import numpy as np
import sqlite3
import os
import concurrent.futures
import re
import altair as alt
import random
import os

CACHE_DIR = r"C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal\veriler"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

def is_yahoo_update_needed(ticker, local_last_date):
    """
    Piyasa saatlerine göre Yahoo'ya gitmenin gerekip gerekmediğini denetler.
    """
    now = datetime.now()
    weekday = now.weekday()  # 0=Pazartesi, 5=Cumartesi, 6=Pazar
    hour_min = now.hour * 100 + now.minute
    local_date = local_last_date.date()

    # BIST Grubu (.IS veya XU ile başlayanlar)
    if ".IS" in ticker or ticker.startswith("XU"):
        # Hafta sonu ise: Veri en az Cuma gününe aitse GİTME.
        if weekday >= 5:
            return local_date < (now - timedelta(days=(weekday - 4))).date()
        # Hafta içi mesai öncesi (10:00 altı): Veri dün ise GİTME.
        if hour_min < 1000:
            return local_date < (now - timedelta(days=1)).date()
        # Hafta içi mesai sonrası (18:30 üstü): Veri bugün ise GİTME.
        if hour_min > 1830:
            return local_date < now.date()
        return True # Seans içi: GİT.

    # ABD Grubu (Sadece hisseler; Kripto ve Vadeli İşlemler hariç)
    elif "-USD" not in ticker and "=F" not in ticker:
        # Hafta sonu: Cuma verisi varsa GİTME.
        if weekday >= 5:
            return local_date < (now - timedelta(days=(weekday - 4))).date()
        # Hafta içi gece kapanış sonrası (23:30 TR saati): Veri bugün ise GİTME.
        if hour_min > 2330 or hour_min < 1630:
            target_date = now.date() if hour_min > 2330 else (now - timedelta(days=1)).date()
            return local_date < target_date
        return True # Seans içi: GİT.

    # Kripto: 7/24 olduğu için her zaman GİT.
    return True

# ==============================================================================
# 1. AYARLAR VE STİL
# ==============================================================================
st.set_page_config(
    page_title="SMART MONEY RADAR", 
    layout="wide",
    page_icon="💸"
)
# YENİ EKLENEN: Global Değişken Tanımlamaları
kd_res = None
# --- DARK MODE / LIGHT MODE ALTYAPISI ---
if 'dark_mode' not in st.session_state:
    st.session_state.dark_mode = False # Default olarak Light Mode

if 'theme' not in st.session_state:
    st.session_state.theme = "Buz Mavisi"

THEMES = {
    "Beyaz": {"bg": "#FFFFFF", "box_bg": "#F8F9FA", "text": "#000000", "border": "#DEE2E6", "news_bg": "#FFFFFF"},
    "Kirli Beyaz": {"bg": "#FAF9F6", "box_bg": "#FFFFFF", "text": "#2C3E50", "border": "#E5E7EB", "news_bg": "#FFFFFF"},
    "Buz Mavisi": {"bg": "#F0F8FF", "box_bg": "#FFFFFF", "text": "#0F172A", "border": "#BFDBFE", "news_bg": "#FFFFFF"}
}
current_theme = THEMES[st.session_state.theme]

if st.session_state.dark_mode:
    st.markdown("""
    <style>
        section[data-testid="stSidebar"] { width: 350px !important; }
        section[data-testid="stSidebar"] div[data-testid="stMarkdownContainer"] * { font-family: 'Inter', sans-serif !important; }
        div[data-testid="stMetricValue"] { font-size: 0.7rem !important; color: #e2e8f0 !important; }
        div[data-testid="stMetricLabel"] { font-size: 0.7rem !important; font-weight: 700; color: #94a3b8 !important; }
        div[data-testid="stMetricDelta"] { font-size: 0.7rem !important; }
        
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=JetBrains+Mono:wght+400;700&display=swap');
        
        .stApp { background-color: #0b0f19; color: #a3a8b8; font-family: 'Inter', sans-serif; }
        .block-container { padding-top: 2rem !important; padding-bottom: 2rem !important; max-width: 95% !important; }
        
        div[data-testid="stMetric"], .stMetric {
            background: linear-gradient(145deg, #111827 0%, #0b0f19 100%);
            border-radius: 6px; padding: 10px 15px !important;
            border-left: 3px solid #10b981; border-top: 1px solid rgba(255,255,255,0.05);
            border-right: 1px solid rgba(255,255,255,0.05); border-bottom: 1px solid rgba(255,255,255,0.05);
            box-shadow: 0 4px 10px rgba(0, 0, 0, 0.4);
        }
        
        [data-testid="stDataFrame"] { background-color: #111827; border: 1px solid #1f2937; }
        .streamlit-expanderHeader { background-color: #111827 !important; border: 1px solid #1f2937 !important; border-radius: 4px; color: #38bdf8 !important; }
        .streamlit-expanderContent { background-color: #0b0f19 !important; border: 1px solid #1f2937 !important; border-top: none !important; }
        
        hr { margin-top: 0.2rem; margin-bottom: 0.5rem; border-color: #1f2937; }
        .stSelectbox, .stTextInput { margin-bottom: -10px; }
        .delta-pos { color: #10b981; } .delta-neg { color: #ef4444; }
        
        div.stButton > button[kind="primary"], div.stButton > button[data-testid="baseButton-primary"] {
            background-color: #3b82f6 !important; border-color: #3b82f6 !important; color: white !important;
            opacity: 1 !important; border-radius: 6px; font-weight: 600; letter-spacing: 0.5px; box-shadow: 0 2px 4px rgba(0,0,0,0.2);
        }
        div.stButton > button[kind="primary"]:hover, div.stButton > button[data-testid="baseButton-primary"]:hover {
            background-color: #2563eb !important; border-color: #2563eb !important; color: white !important; transform: translateY(-1px);
        }
        div.stButton button[data-testid="baseButton-secondary"] {
            background-color: #1e293b !important; border: 1px solid #334155 !important; color: #cbd5e1 !important; font-weight: 700 !important; transition: all 0.2s ease-in-out;
        }
        div.stButton button[data-testid="baseButton-secondary"]:hover {
            background-color: #334155 !important; border-color: #475569 !important; color: #ffffff !important; transform: translateY(-1px);
        }
        .stButton button { width: 100%; border-radius: 6px; font-size: 0.75rem; padding: 0.1rem 0.4rem; }
        
        .info-card {
            background: #111827; border: 1px solid #1f2937; border-radius: 6px; padding: 6px;
            margin-top: 5px; margin-bottom: 5px; font-size: 0.8rem; font-family: 'Inter', sans-serif; color: #a3a8b8;
        }
        .info-header { font-weight: 700; color: #38bdf8; border-bottom: 1px solid #1f2937; padding-bottom: 4px; margin-bottom: 4px; }
        .info-row { display: flex; align-items: flex-start; margin-bottom: 2px; }
        .label-short { font-weight: 600; color: #64748B; width: 80px; flex-shrink: 0; }
        .label-long { font-weight: 600; color: #64748B; width: 100px; flex-shrink: 0; } 
        .info-val { color: #e2e8f0; font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; }
        .edu-note { font-size: 0.85rem; color: #94a3b8; font-style: italic; margin-top: 2px; margin-bottom: 6px; line-height: 1.3; padding-left: 0px; }
        .tech-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; }
        .tech-item { display: flex; align-items: center; font-size: 0.8rem; color: #e2e8f0; }
        div[data-testid="stVerticalBlockBorderWrapper"] {
            resize: vertical !important; overflow: auto !important; min-height: 150px !important; margin-bottom: 10px !important; border-bottom-right-radius: 8px !important;
        }
    </style>
    """, unsafe_allow_html=True)
else:
    st.markdown(f"""
    <style>
        section[data-testid="stSidebar"] {{ width: 350px !important; }}
        section[data-testid="stSidebar"] div[data-testid="stMarkdownContainer"] * {{ font-family: 'Inter', sans-serif !important; }}
        div[data-testid="stMetricValue"] {{ font-size: 0.7rem !important; }}
        div[data-testid="stMetricLabel"] {{ font-size: 0.7rem !important; font-weight: 700; }}
        div[data-testid="stMetricDelta"] {{ font-size: 0.7rem !important; }}
        
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=JetBrains+Mono:wght+400;700&display=swap');
        
        html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; color: {current_theme['text']}; }}
        .stApp {{ background-color: {current_theme['bg']}; }}
        section.main > div.block-container {{ padding-top: 1rem; padding-bottom: 1rem; }}
        .stMetricValue, .money-text {{ font-family: 'JetBrains Mono', monospace !important; }}
        
        .stat-box-small {{ background: {current_theme['box_bg']}; border: 1px solid {current_theme['border']}; border-radius: 4px; padding: 8px; text-align: center; margin-bottom: 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }}
        .stat-label-small {{ font-size: 0.6rem; color: #64748B; text-transform: uppercase; margin: 0; font-weight: 700; letter-spacing: 0.5px; }}
        .stat-value-small {{ font-size: 1.1rem; font-weight: 700; color: {current_theme['text']}; margin: 2px 0 0 0; }}
        .stat-delta-small {{ font-size: 0.8rem; margin-left: 6px; font-weight: 600; }}
        
        hr {{ margin-top: 0.2rem; margin-bottom: 0.5rem; }}
        .stSelectbox, .stTextInput {{ margin-bottom: -10px; }}
        .delta-pos {{ color: #16A34A; }} .delta-neg {{ color: #DC2626; }}
        .news-card {{ background: {current_theme['news_bg']}; border-left: 3px solid {current_theme['border']}; padding: 6px; margin-bottom: 6px; font-size: 0.78rem; }}
        
        div.stButton > button[kind="primary"], div.stButton > button[data-testid="baseButton-primary"] {{
            background-color: #607D8B !important; border-color: #607D8B !important; color: white !important; opacity: 1 !important; border-radius: 6px; font-weight: 600; letter-spacing: 0.5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        div.stButton > button[kind="primary"]:hover, div.stButton > button[data-testid="baseButton-primary"]:hover {{
            background-color: #455A64 !important; border-color: #455A64 !important; color: white !important; transform: translateY(-1px); box-shadow: 0 4px 6px rgba(0, 0, 0, 0.2);
        }}
        div.stButton button[data-testid="baseButton-secondary"] {{
            background-color: #E0F7FA !important; border: 1px solid #4DD0E1 !important; color: #1F2937 !important; font-weight: 700 !important; box-shadow: 0 1px 2px rgba(0,0,0,0.05); transition: all 0.2s ease-in-out;
        }}
        div.stButton button[data-testid="baseButton-secondary"]:hover {{
            background-color: #B2EBF2 !important; border-color: #00BCD4 !important; color: #000000 !important; transform: translateY(-1px);
        }}
        .stButton button {{ width: 100%; border-radius: 6px; font-size: 0.75rem; padding: 0.1rem 0.4rem; }}
        
        .info-card {{ background: {current_theme['box_bg']}; border: 1px solid {current_theme['border']}; border-radius: 6px; padding: 6px; margin-top: 5px; margin-bottom: 5px; font-size: 0.8rem; font-family: 'Inter', sans-serif; }}
        .info-header {{ font-weight: 700; color: #1e3a8a; border-bottom: 1px solid {current_theme['border']}; padding-bottom: 4px; margin-bottom: 4px; }}
        .info-row {{ display: flex; align-items: flex-start; margin-bottom: 2px; }}
        .label-short {{ font-weight: 600; color: #64748B; width: 80px; flex-shrink: 0; }}
        .label-long {{ font-weight: 600; color: #64748B; width: 100px; flex-shrink: 0; }} 
        .info-val {{ color: {current_theme['text']}; font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; }}
        .edu-note {{ font-size: 0.85rem; color: #040561; font-style: italic; margin-top: 2px; margin-bottom: 6px; line-height: 1.3; padding-left: 0px; }}
        .tech-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 4px; }}
        .tech-item {{ display: flex; align-items: center; font-size: 0.8rem; }}
        div[data-testid="stVerticalBlockBorderWrapper"] {{ resize: vertical !important; overflow: auto !important; min-height: 150px !important; margin-bottom: 10px !important; border-bottom-right-radius: 8px !important; }}
    </style>
    """, unsafe_allow_html=True)

# ==============================================================================
# 2. VERİTABANI VE LİSTELER
# ==============================================================================
DB_FILE = "patron.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS watchlist (symbol TEXT PRIMARY KEY)')
    conn.commit()
    conn.close()

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

init_db()

# --- VARLIK LİSTELERİ ---
priority_sp = ["^GSPC", "^DJI", "^NDX", "^IXIC","QQQI", "SPYI", "TSPY", "ARCC", "JEPI"]

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
    "YUM", "ZBH", "ZBRA", "ZTS"
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
    "CL=F",   # Ham Petrol (Crude Oil WTI Futures) - Piyasanın kalbi burasıdır
    "NG=F",   # Doğalgaz (Natural Gas Futures)
    "BZ=F"    # Brent Petrol (Brent Crude Futures)
]

# --- BIST LİSTESİ (GENİŞLETİLMİŞ - BIST 200+ Adayları) ---
priority_bist_indices = ["XU100.IS", "XU030.IS", "XBANK.IS", "XTUMY.IS", "XUSIN.IS", "EREGL.IS", "SISE.IS", "TUPRS.IS"]

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
    "JANTS.IS", "TRALT.IS", "ONRYT.IS",
    "KAPLM.IS", "KAREL.IS", "KARSN.IS", "KARYE.IS", "KATMR.IS", "KAYSE.IS", "KCAER.IS", "KCHOL.IS", "KENT.IS", "KERVN.IS", "KERVT.IS", "KFEIN.IS", "KGYO.IS", "KIMMR.IS", "KLGYO.IS", "KLKIM.IS", "KLMSN.IS", "KLNMA.IS", "KLSER.IS", "KLRHO.IS", "KMPUR.IS", "KNFRT.IS", "KOCMT.IS", "KONKA.IS", "KONTR.IS", "KONYA.IS", "KOPOL.IS", "KORDS.IS", "KOTON.IS", "KOZAA.IS", "KOZAL.IS", "KRDMA.IS", "KRDMB.IS", "KRDMD.IS", "KRGYO.IS", "KRONT.IS", "KRPLS.IS", "KRSTL.IS", "KRTEK.IS", "KRVGD.IS", "KSTUR.IS", "KTLEV.IS", "KTSKR.IS", "KUTPO.IS", "KUVVA.IS", "KUYAS.IS", "KZBGY.IS", "KZGYO.IS",
    "LIDER.IS", "LIDFA.IS", "LILAK.IS", "LINK.IS", "LKMNH.IS", "LMKDC.IS", "LOGO.IS", "LUKSK.IS",
    "MAALT.IS", "MACKO.IS", "MAGEN.IS", "MAKIM.IS", "MAKTK.IS", "MANAS.IS", "MARBL.IS", "MARKA.IS", "MARTI.IS", "MAVI.IS", "MEDTR.IS", "MEGAP.IS", "MEGMT.IS", "MEKAG.IS", "MEPET.IS", "MERCN.IS", "MERIT.IS", "MERKO.IS", "METEM.IS", "METRO.IS", "METUR.IS", "MGROS.IS", "MIATK.IS", "MIPAZ.IS", "MMCAS.IS", "MNDRS.IS", "MNDTR.IS", "MOBTL.IS", "MOGAN.IS", "MPARK.IS", "MRGYO.IS", "MRSHL.IS", "MSGYO.IS", "MTRKS.IS", "MTRYO.IS", "MZHLD.IS",
    "NATEN.IS", "NETAS.IS", "NIBAS.IS", "NTGAZ.IS", "NUGYO.IS", "NUHCM.IS",
    "OBASE.IS", "OBAMS.IS", "ODAS.IS", "ODINE.IS", "OFSYM.IS", "ONCSM.IS", "ORCA.IS", "ORGE.IS", "ORMA.IS", "OSMEN.IS", "OSTIM.IS", "OTKAR.IS", "OTTO.IS", "OYAKC.IS", "OYAYO.IS", "OYLUM.IS", "OYYAT.IS", "OZGYO.IS", "OZKGY.IS", "OZRDN.IS", "OZSUB.IS",
    "PAGYO.IS", "PAMEL.IS", "PAPIL.IS", "PARSN.IS", "PASEU.IS", "PCILT.IS", "PEGYO.IS", "PEKGY.IS", "PENGD.IS", "PENTA.IS", "PETKM.IS", "PETUN.IS", "PGSUS.IS", "PINSU.IS", "PKART.IS", "PKENT.IS", "PLAT.IS", "PNLSN.IS", "POLHO.IS", "POLTK.IS", "PRDGS.IS", "PRKAB.IS", "PRKME.IS", "PRZMA.IS", "PSDTC.IS", "PSGYO.IS", "PTEK.IS",
    "QNBFB.IS", "QNBFL.IS", "QUAGR.IS", "PLTUR.IS",
    "RALYH.IS", "RAYSG.IS", "REEDR.IS", "RGYAS.IS", "RNPOL.IS", "RODRG.IS", "ROYAL.IS", "RTALB.IS", "RUBNS.IS", "RYGYO.IS", "RYSAS.IS",
    "SAFKR.IS", "SAHOL.IS", "SAMAT.IS", "SANEL.IS", "SANFM.IS", "SANKO.IS", "SARKY.IS", "SASA.IS", "SAYAS.IS", "SDTTR.IS", "SEGYO.IS", "SEKFK.IS", "SEKUR.IS", "SELEC.IS", "SELGD.IS", "SELVA.IS", "SEYKM.IS", "SILVR.IS", "SISE.IS", "SKBNK.IS", "SKTAS.IS", "SKYMD.IS", "SMART.IS", "SMRTG.IS", "SNGYO.IS", "SNICA.IS", "SNKRN.IS", "SNPAM.IS", "SODSN.IS", "SOKE.IS", "SOKM.IS", "SONME.IS", "SRVGY.IS", "SUMAS.IS", "SUNTK.IS", "SURGY.IS", "SUWEN.IS", "SYS.IS",
    "TABGD.IS", "TARAF.IS", "TATGD.IS", "TAVHL.IS", "TBORG.IS", "TCELL.IS", "TDGYO.IS", "TEKTU.IS", "TERA.IS", "TETMT.IS", "TEZOL.IS", "TGSAS.IS", "THYAO.IS", "TKFEN.IS", "TKNSA.IS", "TLMAN.IS", "TMPOL.IS", "TMSN.IS", "TNZTP.IS", "TOASO.IS", "TRCAS.IS", "TRGYO.IS", "TRILC.IS", "TSGYO.IS", "TSKB.IS", "TSPOR.IS", "TTKOM.IS", "TTRAK.IS", "TUCLK.IS", "TUKAS.IS", "TUPRS.IS", "TUREX.IS", "TURGG.IS", "TURSG.IS",
    "UFUK.IS", "ULAS.IS", "ULKER.IS", "ULUFA.IS", "ULUSE.IS", "ULUUN.IS", "UMPAS.IS", "UNLU.IS", "USAK.IS", "UZERB.IS", "TATEN.IS",
    "VAKBN.IS", "VAKFN.IS", "VAKKO.IS", "VANGD.IS", "VBTYZ.IS", "VERUS.IS", "VESBE.IS", "VESTL.IS", "VKFYO.IS", "VKGYO.IS", "VKING.IS", "VRGYO.IS",
    "YAPRK.IS", "YATAS.IS", "YAYLA.IS", "YBTAS.IS", "YEOTK.IS", "YESIL.IS", "YGGYO.IS", "YGYO.IS", "YKBNK.IS", "YKSLN.IS", "YONGA.IS", "YUNSA.IS", "YYAPI.IS", "YYLGD.IS",
    "ZEDUR.IS", "ZOREN.IS", "ZRGYO.IS", "GIPTA.IS", "TEHOL.IS", "PAHOL.IS", "MARMR.IS", "BIGEN.IS", "GLRMK.IS", "TRHOL.IS"
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

# --- STATE YÖNETİMİ ---
if 'category' not in st.session_state: st.session_state.category = INITIAL_CATEGORY
if 'ticker' not in st.session_state: st.session_state.ticker = "XU100.IS"
if 'scan_data' not in st.session_state: st.session_state.scan_data = None
if 'generate_prompt' not in st.session_state: st.session_state.generate_prompt = False
if 'radar2_data' not in st.session_state: st.session_state.radar2_data = None
if 'watchlist' not in st.session_state: st.session_state.watchlist = load_watchlist_db()
if 'stp_scanned' not in st.session_state: st.session_state.stp_scanned = False
if 'stp_crosses' not in st.session_state: st.session_state.stp_crosses = []
if 'stp_trends' not in st.session_state: st.session_state.stp_trends = []
if 'stp_filtered' not in st.session_state: st.session_state.stp_filtered = []
if 'accum_data' not in st.session_state: st.session_state.accum_data = None
if 'breakout_left' not in st.session_state: st.session_state.breakout_left = None
if 'breakout_right' not in st.session_state: st.session_state.breakout_right = None
if 'minervini_data' not in st.session_state: st.session_state.minervini_data = None
if 'pattern_data' not in st.session_state: st.session_state.pattern_data = None

# --- CALLBACKLER ---
def on_category_change():
    new_cat = st.session_state.get("selected_category_key")
    if new_cat and new_cat in ASSET_GROUPS:
        st.session_state.category = new_cat
        st.session_state.ticker = ASSET_GROUPS[new_cat][0]
        st.session_state.scan_data = None
        st.session_state.radar2_data = None
        st.session_state.stp_scanned = False
        st.session_state.accum_data = None 
        st.session_state.breakout_left = None
        st.session_state.breakout_right = None

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
# 3. OPTİMİZE EDİLMİŞ HESAPLAMA FONKSİYONLARI (CORE LOGIC)
# ==============================================================================

def apply_volume_projection(df, ticker=""):
    """
    Piyasa saatlerine göre Hacim Projeksiyonu (Run-Rate) yapar.
    Eğer piyasa açıksa, bugünün eksik hacmini gün sonuna göre oranlayarak büyütür.
    DİKKAT: Bu işlem sadece RAM üzerinde (kopya df'te) yapılır, veritabanını bozmaz.
    """
    if df is None or df.empty or 'Volume' not in df.columns:
        return df

    now = datetime.now()
    
    # Hafta sonuysa projeksiyon yapma
    if now.weekday() >= 5:
        return df
        
    # Elimizdeki son veri BUGÜNE mi ait? Değilse dokunma.
    last_date = df.index[-1].date()
    if last_date != now.date():
        return df 

    # Kripto, ABD veya BIST saatlerine göre hesaplama
    if "-USD" in ticker:
        # Kripto 7/24 (1440 dakika)
        elapsed_minutes = (now.hour * 60) + now.minute
        total_minutes = 1440
    elif "^" in ticker or (not ".IS" in ticker and not ticker.startswith("XU")):
        # ABD Piyasası (16:30 - 23:00 TR Saati) -> 390 dakika
        if now.hour < 16 or (now.hour == 16 and now.minute < 30) or now.hour >= 23:
            return df
        elapsed_minutes = ((now.hour - 16) * 60) + now.minute - 30
        total_minutes = 390
    else:
        # BIST (10:00 - 18:00 TR Saati) -> 480 dakika
        if now.hour < 10 or now.hour >= 18:
            return df
        elapsed_minutes = ((now.hour - 10) * 60) + now.minute
        total_minutes = 480

    # Güvenlik Kilidi: Sıfıra bölünmeyi ve açılıştaki ilk 15 dakikanın aşırı şişkinliğini önle
    if elapsed_minutes < 15:
        elapsed_minutes = 15 
        
    progress = elapsed_minutes / total_minutes
    progress = max(0.1, min(progress, 1.0))
    
    # Orijinal veritabanı bozulmasın diye KOPYA (copy) oluşturuyoruz
    df_proj = df.copy()
    current_volume = float(df_proj['Volume'].iloc[-1])
    projected_volume = current_volume / progress
    
    # Sadece en son satırın (bugünün) hacmini güncelle
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
@st.cache_data(ttl=3600, show_spinner=False)
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
        if "BIST" in sym or ".IS" in sym:
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
        df_new = yf.download(" ".join(missing_assets), period="2y", group_by='ticker', threads=True, progress=False, auto_adjust=True, prepost=False)
        
        for sym in missing_assets:
            clean_sym = sym.replace(".IS", "")
            if "BIST" in sym or ".IS" in sym:
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
                if 'Volume' not in df_sym_new.columns: df_sym_new['Volume'] = 1.0

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

# --- SINGLE STOCK CACHE (DETAY SAYFASI İÇİN) ---
@st.cache_data(ttl=300)
def get_safe_historical_data(ticker, period="1y", interval="1d"):
    try:
        clean_ticker = ticker.replace(".IS", "")
        if "BIST" in ticker or ".IS" in ticker:
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
            if 'Volume' not in df.columns: df['Volume'] = 1.0
            return df

        if os.path.exists(file_path):
            df_cached = pd.read_parquet(file_path)
            df_cached = safe_clean_columns(df_cached)
            df_cached.index = df_cached.index.tz_localize(None)
            
            if not is_yahoo_update_needed(ticker, df_cached.index[-1]):
                # 👇 YENİ HALİ:
                return apply_volume_projection(df_cached.tail(500).copy(), ticker)

            df_new = yf.download(clean_ticker, period="2y", interval=interval, progress=False, auto_adjust=True)
            if not df_new.empty:
                df_new = safe_clean_columns(df_new)
                df_new.index = df_new.index.tz_localize(None)
                
                # Dosyayı tamamen yenile (Temettü düzeltmesi için şart)
                df_new.to_parquet(file_path)
                
                return apply_volume_projection(df_new.tail(500).copy(), ticker)
            
            # 👇 YENİ HALİ:
            return apply_volume_projection(df_cached.tail(500).copy(), ticker)
        else:
            df_full = yf.download(clean_ticker, period="2y", interval=interval, progress=False, auto_adjust=True)
            if not df_full.empty:
                df_full = safe_clean_columns(df_full)
                df_full.index = df_full.index.tz_localize(None)
                df_full.to_parquet(file_path)
                
                # 👇 YENİ HALİ:
                return apply_volume_projection(df_full.tail(500).copy(), ticker)
        return None
    
    except Exception as e:
        return None

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
        color = "#16a34a" if is_green else "#dc2626"
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
    
@st.cache_data(ttl=300)
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
            df = get_safe_historical_data(ticker, period="5d")
            if df is not None and not df.empty:
                 price = float(df["Close"].iloc[-1])
                 prev_close = float(df["Close"].iloc[-2]) if len(df) > 1 else price
                 volume = float(df["Volume"].iloc[-1])
            else: return None

        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0
        return { "price": price, "change_pct": change_pct, "volume": volume or 0, "sector": "-", "target": "-" }
    except: return None

@st.cache_data(ttl=600)
def get_tech_card_data(ticker):
    try:
        df = get_safe_historical_data(ticker, period="2y")
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
        if df is None: return None
        
        close = df['Close']; high = df['High']; low = df['Low']; volume = df['Volume']
        
        # --- EVRENSEL FORMÜL V2.0 BAŞLANGIÇ ---
        # 1. Tipik Fiyat
        typical_price = (high + low + close) / 3

        # 2. DEMA 6 Hesaplama
        ema1 = typical_price.ewm(span=6, adjust=False).mean()
        ema2 = ema1.ewm(span=6, adjust=False).mean()
        dema6 = (2 * ema1) - ema2

        mf_smooth = (typical_price - dema6) / dema6 * 1000

        stp = ema1
        
        df = df.reset_index()
        if 'Date' not in df.columns: df['Date'] = df.index
        else: df['Date'] = pd.to_datetime(df['Date'])
        
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
            return ("⚠️ GİZLİ ÇIKIŞ (Dağıtım)", "#dc2626", "Son 10 günde fiyat yükselmesine rağmen kümülatif hacim (OBV) düşüyor. Yükseliş sahte olabilir, büyük oyuncular çıkış yapıyor olabilir.")
            
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

# --- OPTİMİZE EDİLMİŞ BATCH SCANNER'LAR ---

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

def process_single_bear_trap_live(df):
    """
    Tekil hisse için Bear Trap kontrolü yapar.
    Canlı durum paneli için optimize edilmiştir.
    """
    try:
        if df.empty or len(df) < 60: return None
        
        close = df['Close']; low = df['Low']; volume = df['Volume']
        if 'Volume' not in df.columns: volume = pd.Series([1]*len(df))
        
        curr_price = float(close.iloc[-1])

        # RSI Hesabı
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss)))

        # Son 4 mumu tara
        for i in range(4):
            idx = -(i + 1) # -1 (Şimdi), -2 (Önceki)...

            # 1. Referans Dip (50 mumluk)
            pivot_slice = low.iloc[idx-50 : idx]
            if len(pivot_slice) < 50: continue
            pivot_low = float(pivot_slice.min())

            # 2. Tuzak Mumu Verileri
            trap_low = float(low.iloc[idx])
            trap_close = float(close.iloc[idx])
            trap_vol = float(volume.iloc[idx])
            avg_vol = float(volume.iloc[idx-20:idx].mean())
            if avg_vol == 0: avg_vol = 1

            # 3. Kriterler
            is_sweep = trap_low < pivot_low
            is_rejection = trap_close > pivot_low
            is_vol_ok = trap_vol > (avg_vol * 1.5)
            is_safe = curr_price > pivot_low # Fiyat hala güvenli bölgede mi?

            if is_sweep and is_rejection and is_vol_ok and is_safe:
                time_ago = "Şimdi" if i == 0 else f"{i} bar önce"
                return {
                    "Zaman": time_ago,
                    "Hacim_Kat": f"{trap_vol/avg_vol:.1f}x",
                    "Pivot": pivot_low
                }
        return None
    except: return None

@st.cache_data(ttl=900)
def scan_bear_traps(asset_list):
    """
    BEAR TRAP TARAYICISI (Toplu)
    Mantık: 50 periyotluk dibi temizleyip (Sweep), hacimli dönenleri (Rejection) bulur.
    Pencere: Son 4 mum (0, 1, 2, 3).
    """
    # Mevcut önbellekten veriyi çek (İnterneti yormaz)
    data = get_batch_data_cached(asset_list, period="2y") 
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

    # -- İÇ FONKSİYON: TEKİL İŞLEM --
    def _worker_bear_trap(symbol, df):
        try:
            if df.empty or len(df) < 60: return None
            
            close = df['Close']; low = df['Low']; volume = df['Volume']
            # Hacim yoksa 1 kabul et (Hata önleyici)
            if 'Volume' not in df.columns: volume = pd.Series([1]*len(df))
            
            curr_price = float(close.iloc[-1])

            # RSI Hesabı
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rsi = 100 - (100 / (1 + (gain / loss)))

            # DÖNGÜ: Son 4 muma bak
            for i in range(4):
                idx = -(i + 1) # -1, -2, -3, -4

                # 1. REFERANS DİP (Geriye dönük 50 mum)
                pivot_slice = low.iloc[idx-50 : idx]
                if len(pivot_slice) < 50: continue
                pivot_low = float(pivot_slice.min())

                # 2. TUZAK MUMU VERİLERİ
                trap_low = float(low.iloc[idx])
                trap_close = float(close.iloc[idx])
                trap_vol = float(volume.iloc[idx])
                
                # Ortalama Hacim (Önceki 20 mum)
                avg_vol = float(volume.iloc[idx-20:idx].mean())
                if avg_vol == 0: avg_vol = 1

                # 3. KRİTERLER (AND)
                is_sweep = trap_low < pivot_low           # Dibi deldi mi?
                is_rejection = trap_close > pivot_low     # Üstünde kapattı mı?
                is_vol_ok = trap_vol > (avg_vol * 1.5)    # Hacim var mı?
                is_rsi_ok = float(rsi.iloc[idx]) > 30     # RSI aşırı ölü değil mi?
                is_safe = curr_price > pivot_low          # ŞU AN fiyat güvenli mi?

                if is_sweep and is_rejection and is_vol_ok and is_rsi_ok and is_safe:
                    time_ago = "🔥 ŞİMDİ" if i == 0 else f"⏰ {i} Mum Önce"
                    
                    # Skorlama (Tazelik + Hacim Gücü)
                    score = 80 + (10 if i == 0 else 0) + (10 if trap_vol > avg_vol * 2.0 else 0)
                    
                    return {
                        "Sembol": symbol,
                        "Fiyat": curr_price,
                        "Pivot": pivot_low,
                        "Zaman": time_ago,
                        "Hacim_Kat": f"{trap_vol/avg_vol:.1f}x",
                        "Detay": f"Dip ({pivot_low:.2f}) temizlendi.",
                        "Skor": score
                    }
            return None
        except: return None

    # -- PARALEL İŞLEM (HIZ İÇİN) --
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_worker_bear_trap, sym, df) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)

    if results:
        return pd.DataFrame(results).sort_values(by="Skor", ascending=False)
    
    return pd.DataFrame()

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

def scan_chart_patterns(asset_list):
    """
    V6: ZIGZAG TABANLI FORMASYON MOTORU
    - Gürültüyü eler, yalnızca anlamlı salınımları (zigzag iskelet) kullanır.
    - İnsan gözünün gördüğü şekli sayısal olarak tespit eder.
    - TOBO: L,H,L*,H,L — son 5 anlamlı pivot üzerinden
    - Fincan-Kulp: H,L,H≈ilk,L(kulp) — son 4 anlamlı pivot üzerinden
    - 2 yıllık veri ile büyük formasyonlar kaçmaz.
    """
    data = get_batch_data_cached(asset_list, period="2y")
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

            # ---------------------------------------------------------------
            # ZIGZAG İSKELETİ — %4 eşikli (insan gözüne yakın)
            # ---------------------------------------------------------------
            zz       = zigzag_pivots(close, threshold=0.04)
            zz_chron = sorted(zz, key=lambda x: x[0])   # Kronolojik sıra
            zz_h     = [(i, p) for (i, p, t) in zz_chron if t == 'H']
            zz_l     = [(i, p) for (i, p, t) in zz_chron if t == 'L']

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
                    pattern_found = True
                    pattern_name  = "🚩 BOĞA BAYRAĞI"
                    base_score    = 85
                    desc = f"Direk: %{pole*100:.1f} | Sıkışma: %{tight*100:.1f} | Geri Alım: %{retrace*100:.0f}"

            # ---------------------------------------------------------------
            # 2. FİNCAN-KULP — Zigzag şablonu: H → L → H(≈ilk) → L(kulp)
            # Son 4 anlamlı pivot; fiyat sağ rimi test ediyor veya kırdı.
            # ---------------------------------------------------------------
            if not pattern_found and len(zz_chron) >= 4:
                # Son 20 pivot penceresini tara; 1 yıldan eski paterni dikkate alma
                for start_offset in range(min(20, len(zz_chron) - 3)):
                    p1_i, p1_v, p1_t = zz_chron[-(4 + start_offset)]
                    p2_i, p2_v, p2_t = zz_chron[-(3 + start_offset)]
                    p3_i, p3_v, p3_t = zz_chron[-(2 + start_offset)]
                    p4_i, p4_v, p4_t = zz_chron[-(1 + start_offset)]

                    # 1 yıldan önce başlayan paterni atla (ve daha geri gitme)
                    if p1_i < bar_total - 252: break

                    # Şablon kontrolü: H, L, H, L
                    if not (p1_t == 'H' and p2_t == 'L' and p3_t == 'H' and p4_t == 'L'):
                        continue

                    rim_l, cup_b, rim_r, handle_l = p1_v, p2_v, p3_v, p4_v

                    rims_aligned   = abs(rim_l - rim_r) / rim_l < 0.12       # Rimler ±%12
                    cup_deep       = cup_b < rim_l * 0.88                     # Fincan min %12 derin
                    handle_ok      = handle_l > cup_b + (rim_r - cup_b) * 0.4 # Kulp üst %60'ta
                    handle_shallow = handle_l > rim_r * 0.85                  # Kulp çok derin değil
                    cup_wide       = (p3_i - p1_i) >= 40                      # Min 40 bar (~2 ay) — noise elenir

                    if start_offset == 0:
                        # Son pivotlar: fiyat breakout veya kulp bölgesinde
                        breaking  = curr_price >= rim_r * 0.97 and curr_price <= rim_r * 1.08
                        forming   = curr_price >= handle_l * 0.99 and not breaking
                        active    = breaking or forming
                    else:
                        # Eski pivotlar: kırılım zaten gerçekleşti mi?
                        active = curr_price >= rim_r * 0.98

                    if rims_aligned and cup_deep and handle_ok and handle_shallow and cup_wide and active:
                        dur_months = max(1, round((p3_i - p1_i) / 21))
                        dist_to_rim = ((rim_r - curr_price) / rim_r * 100) if curr_price < rim_r else 0
                        if start_offset == 0 and breaking:
                            status_txt = "Kırılım Bölgesinde"
                            p_name = f"☕ FİNCAN KULP ({dur_months} Ay) — {status_txt}"
                            base_score = 95
                        elif start_offset == 0 and forming:
                            status_txt = f"Tamamlanmasına %{dist_to_rim:.1f} kaldı"
                            p_name = f"⏳ OLUŞAN FİNCAN KULP ({dur_months} Ay) — {status_txt}"
                            base_score = 78
                        else:
                            days_since = bar_total - p3_i
                            status_txt = f"Tamamlandı ({days_since} gün önce)"
                            p_name = f"☕ FİNCAN KULP ({dur_months} Ay) — {status_txt}"
                            base_score = 80
                        p_desc = f"Sol Rim: {rim_l:.2f} | Dip: {cup_b:.2f} | Sağ Rim: {rim_r:.2f} | Kulp: {handle_l:.2f}"
                        pattern_found = True
                        pattern_name = p_name; desc = p_desc
                        break

            # ---------------------------------------------------------------
            # 3. TOBO — Zigzag şablonu: L → H → L*(baş) → H(≈ilk H) → L(≈ilk L)
            # Kritik kural: sağ omuz baş→boyun mesafesinin %50'sini geri almış olmalı.
            # Bu kural insan gözünün "sağ omuz boyuna yakın" algısını sayısallaştırır.
            # ---------------------------------------------------------------
            if not pattern_found and len(zz_chron) >= 5:
                for start_offset in range(min(15, len(zz_chron) - 4)):
                    p1_i, p1_v, p1_t = zz_chron[-(5 + start_offset)]  # Sol omuz (L)
                    p2_i, p2_v, p2_t = zz_chron[-(4 + start_offset)]  # Boyun sol (H)
                    p3_i, p3_v, p3_t = zz_chron[-(3 + start_offset)]  # Baş (L, en derin)
                    p4_i, p4_v, p4_t = zz_chron[-(2 + start_offset)]  # Boyun sağ (H)
                    p5_i, p5_v, p5_t = zz_chron[-(1 + start_offset)]  # Sağ omuz (L)

                    # 1 yıldan önce başlayan paterni atla (ve daha geri gitme)
                    if p1_i < bar_total - 252: break

                    if not (p1_t=='L' and p2_t=='H' and p3_t=='L' and p4_t=='H' and p5_t=='L'):
                        continue

                    ls_p   = p1_v
                    neck_l = p2_v
                    head_p = p3_v
                    neck_r = p4_v
                    rs_p   = p5_v
                    neck   = (neck_l + neck_r) / 2

                    head_deep   = head_p < ls_p * 0.95 and head_p < rs_p * 0.95
                    sym         = abs(ls_p - rs_p) / ls_p < 0.10
                    neck_flat   = abs(neck_l - neck_r) / neck_l < 0.06
                    pat_wide    = (p5_i - p1_i) >= 40  # Min 40 bar (~2 ay) — downtrend içi noise elenir
                    # Sağ omuz, baş→boyun mesafesinin en az %50'sini geri almış olmalı
                    # (insan gözü: "sağ omuz boyuna yakın görünüyor")
                    recovery    = (rs_p - head_p) / (neck - head_p) if (neck - head_p) > 0 else 0
                    recovery_ok = recovery >= 0.50

                    if not (head_deep and sym and neck_flat and pat_wide and recovery_ok):
                        continue

                    if start_offset == 0:
                        is_breakout = curr_price >= neck * 0.97 and curr_price <= neck * 1.06
                        is_forming  = curr_price > rs_p * 1.01 and curr_price < neck * 0.96
                        active = is_breakout or is_forming
                        is_breakout_flag = is_breakout
                    else:
                        active = curr_price >= neck * 0.97
                        is_breakout_flag = active

                    if not active:
                        continue

                    dur_months   = max(1, round((p5_i - p1_i) / 21))
                    dist_to_neck = ((neck - curr_price) / neck * 100) if curr_price < neck else 0

                    if is_breakout_flag and start_offset == 0:
                        status_txt = "Kırılım Bölgesinde"
                        p_name = f"🧛 TOBO ({dur_months} Ay) — {status_txt}"
                        base_score = 92
                    elif start_offset == 0:
                        status_txt = f"Tamamlanmasına %{dist_to_neck:.1f} kaldı"
                        p_name = f"⏳ OLUŞAN TOBO ({dur_months} Ay) — {status_txt}"
                        base_score = 72
                    else:
                        days_since = bar_total - p5_i
                        status_txt = f"Tamamlandı ({days_since} gün önce)"
                        p_name = f"🧛 TOBO ({dur_months} Ay) — {status_txt}"
                        base_score = 80

                    p_desc = f"Boyun: {neck:.2f} | Baş: {head_p:.2f} | Sol/Sağ Omuz: {ls_p:.2f}/{rs_p:.2f} | Geri Alım: %{recovery*100:.0f}"
                    pattern_found = True
                    pattern_name = p_name; desc = p_desc
                    base_score_final = base_score
                    break

            # ---------------------------------------------------------------
            # 4. YÜKSELEN ÜÇGEN — Son 8 zigzag pivotu: tepeler yatay, dipler yükseliyor
            # ---------------------------------------------------------------
            if not pattern_found and len(zz_chron) >= 4:
                recent  = zz_chron[-8:]
                r_highs = [(i, p) for (i, p, t) in recent if t == 'H']
                r_lows  = [(i, p) for (i, p, t) in recent if t == 'L']
                if len(r_highs) >= 2 and len(r_lows) >= 2:
                    top_val   = max(p for _, p in r_highs)
                    flat_tops = [p for _, p in r_highs if abs(p - top_val) / top_val < 0.03]
                    flat      = len(flat_tops) >= 2
                    lows_s    = sorted(r_lows, key=lambda x: x[0])
                    rising    = all(lows_s[k][1] < lows_s[k+1][1] for k in range(len(lows_s)-1))
                    if flat and rising:
                        avg_res    = sum(flat_tops) / len(flat_tops)
                        dur_bars   = recent[-1][0] - recent[0][0]
                        breaking   = curr_price >= avg_res * 0.99 and curr_price <= avg_res * 1.04
                        approaching = curr_price >= avg_res * 0.94 and not breaking
                        if breaking or approaching:
                            pattern_found = True
                            p_name = f"📐 YÜKS. ÜÇGEN ({dur_bars} Gün)" if breaking else f"⏳ OLUŞAN ÜÇGEN ({dur_bars} Gün)"
                            p_desc = f"Direnç: {avg_res:.2f} | Yükselen Dip: {len(r_lows)} pivot"
                            pattern_name = p_name; desc = p_desc
                            base_score   = 88 if breaking else 68

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
                    "Hacim":     float(volume.iloc[-1])
                }

        except Exception:
            return None
        return None

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
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
            # 🚀 1. AŞAMA: ORİJİNAL ALTIN FIRSAT KRİTERLERİ 
            # =========================================================
            
            # 3 Aylık Konum / İskonto Hesabı
            high_3m = high.iloc[-64:].max()
            low_3m = low.iloc[-64:].min()
            
            if (high_3m - low_3m) > 0:
                is_discount = curr_price <= (low_3m + (high_3m - low_3m) * 0.50)
            else:
                is_discount = False
            
            # RSI ve Güç Hesabı
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(window=14).mean()
            loss = -delta.clip(upper=0).rolling(window=14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            last_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
            
            # Şartlar: Güçlü RSI ve Enerji (Hacim veya RSI desteği)
            is_powerful = last_rsi > 55
            is_energy = (last_vol > avg_vol * 1.05) or (last_rsi > 55)

            # Mansfield RS (Endekse Göre Göreceli Güç)
            mansfield_gp = 0.0
            if bench is not None and len(close) > 60:
                try:
                    common_i = close.index.intersection(bench.index)
                    if len(common_i) > 55:
                        rs_r = close.reindex(common_i) / bench.reindex(common_i)
                        rs_m = rs_r.rolling(50).mean()
                        m_s = ((rs_r / rs_m) - 1) * 10
                        mansfield_gp = float(m_s.iloc[-1]) if not np.isnan(m_s.iloc[-1]) else 0.0
                except: pass

            # Altın fırsat DEĞİLSE bir sonraki sembole geç
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

            # A) FİNCAN KULP — Zigzag şablonu: H → L → H(≈ilk) → L(kulp)
            if not pattern_found and len(zz_chron) >= 4:
                for start_offset in range(min(20, len(zz_chron) - 3)):
                    p1_i, p1_v, p1_t = zz_chron[-(4 + start_offset)]
                    p2_i, p2_v, p2_t = zz_chron[-(3 + start_offset)]
                    p3_i, p3_v, p3_t = zz_chron[-(2 + start_offset)]
                    p4_i, p4_v, p4_t = zz_chron[-(1 + start_offset)]
                    # 1 yıldan önce başlayan paterni atla
                    if p1_i < len(close) - 252: break
                    if not (p1_t=='H' and p2_t=='L' and p3_t=='H' and p4_t=='L'): continue
                    rim_l, cup_b, rim_r, handle_l = p1_v, p2_v, p3_v, p4_v
                    rims_aligned   = abs(rim_l - rim_r) / rim_l < 0.12
                    cup_deep       = cup_b < rim_l * 0.88
                    handle_ok      = handle_l > cup_b + (rim_r - cup_b) * 0.4
                    handle_shallow = handle_l > rim_r * 0.85
                    cup_wide       = (p3_i - p1_i) >= 40  # Min 40 bar (~2 ay)
                    if not (rims_aligned and cup_deep and handle_ok and handle_shallow and cup_wide): continue
                    if start_offset == 0:
                        breaking   = curr_price >= rim_r * 0.97 and curr_price <= rim_r * 1.08
                        forming    = curr_price >= rim_r * 0.88 and not breaking  # Rim'e %12 yakın
                        active     = breaking or forming
                    else:
                        active = curr_price >= rim_r * 0.98
                        breaking = active
                    if not active: continue
                    dur_months = max(1, round((p3_i - p1_i) / 21))
                    if breaking and start_offset == 0:
                        p_name = f"☕ FİNCAN KULP ({dur_months} Ay) — Kırılım Bölgesinde"
                        base_score = 90
                    elif breaking:  # start_offset > 0 → tarihsel formasyon
                        days_since = len(close) - p3_i
                        p_name = f"☕ FİNCAN KULP ({dur_months} Ay) — Tamamlandı ({days_since} gün önce)"
                        base_score = 80
                    else:  # forming
                        dist_to_rim = ((rim_r - curr_price) / rim_r * 100) if curr_price < rim_r else 0
                        p_name = f"⏳ OLUŞAN FİNCAN KULP ({dur_months} Ay) — Tamamlanmasına %{dist_to_rim:.1f} kaldı"
                        base_score = max(50, 85 - int(dist_to_rim * 1.5))
                    pattern_found = True
                    break

            # B) TOBO — Zigzag şablonu: L → H → L*(baş) → H → L(sağ omuz)
            # Kritik kural: fiyat neckline'ın %25'inden uzakta olamaz → %-54 yanlış pozitif yok
            if not pattern_found and len(zz_chron) >= 5:
                for start_offset in range(min(15, len(zz_chron) - 4)):
                    p1_i, p1_v, p1_t = zz_chron[-(5 + start_offset)]
                    p2_i, p2_v, p2_t = zz_chron[-(4 + start_offset)]
                    p3_i, p3_v, p3_t = zz_chron[-(3 + start_offset)]
                    p4_i, p4_v, p4_t = zz_chron[-(2 + start_offset)]
                    p5_i, p5_v, p5_t = zz_chron[-(1 + start_offset)]
                    # 1 yıldan önce başlayan paterni atla
                    if p1_i < len(close) - 252: break
                    if not (p1_t=='L' and p2_t=='H' and p3_t=='L' and p4_t=='H' and p5_t=='L'): continue
                    ls_p, neck_l, head_p, neck_r, rs_p = p1_v, p2_v, p3_v, p4_v, p5_v
                    neck = (neck_l + neck_r) / 2
                    head_deep   = head_p < ls_p * 0.95 and head_p < rs_p * 0.95
                    sym         = abs(ls_p - rs_p) / ls_p < 0.10
                    neck_flat   = abs(neck_l - neck_r) / neck_l < 0.06
                    pat_wide    = (p5_i - p1_i) >= 40  # Min 40 bar (~2 ay) — downtrend içi noise elenir
                    recovery    = (rs_p - head_p) / (neck - head_p) if (neck - head_p) > 0 else 0
                    recovery_ok = recovery >= 0.50  # Sağ omuz boyuna en az %50 yaklaşmış olmalı
                    if not (head_deep and sym and neck_flat and pat_wide and recovery_ok): continue
                    if start_offset == 0:
                        breaking  = curr_price >= neck * 0.97 and curr_price <= neck * 1.06
                        # Neckline'a %25'ten fazla uzak olamaz → BURCE gibi %-54 yok
                        forming   = (curr_price > rs_p * 1.01
                                     and curr_price >= neck * 0.75    # Max %25 uzakta
                                     and curr_price < neck * 0.96)
                        active    = breaking or forming
                        is_breakout = breaking
                    else:
                        active = curr_price >= neck * 0.97
                        is_breakout = active
                    if not active: continue
                    dur_months = max(1, round((p5_i - p1_i) / 21))
                    if is_breakout and start_offset == 0:
                        p_name = f"🧛 TOBO ({dur_months} Ay) — Kırılım Bölgesinde"
                        base_score = 92
                    elif is_breakout:  # start_offset > 0 → tarihsel formasyon
                        days_since = len(close) - p5_i
                        p_name = f"🧛 TOBO ({dur_months} Ay) — Tamamlandı ({days_since} gün önce)"
                        base_score = 80
                    else:  # forming
                        dist_to_neck = ((neck - curr_price) / neck * 100) if curr_price < neck else 0
                        p_name = f"⏳ OLUŞAN TOBO ({dur_months} Ay) — Tamamlanmasına %{dist_to_neck:.1f} kaldı"
                        base_score = 72
                    pattern_found = True
                    break
            
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
                    "Sembol": symbol,
                    "Puan": int(min(max(base_score, 10), 100)),
                    "RSI": round(float(last_rsi), 1),
                    "Mansfield": round(mansfield_gp, 1),
                    "Hacim_Kat": round(vol_ratio, 1),
                    "Detay": p_name + warning_text
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
                    "Sembol": symbol,
                    "RSI": round(float(last_rsi), 1),
                    "Mansfield": round(mansfield_gp, 1),
                    "Hacim_Kat": round(vol_ratio, 1),
                    "Durum": etiket
                })

        except Exception as e:
            # Hata durumunda (örneğin veri eksikliği) o sembolü atla
            continue

    formations_df = pd.DataFrame(results).sort_values(by="Puan", ascending=False) if results else pd.DataFrame()
    hazirlik_df   = pd.DataFrame(hazirlik_list).sort_values(by="Mansfield", ascending=False) if hazirlik_list else pd.DataFrame()
    return {"formations": formations_df, "hazirlik": hazirlik_df}

@st.cache_data(ttl=900)
def scan_stp_signals(asset_list):
    """
    Optimize edilmiş STP tarayıcı.
    """
    data = get_batch_data_cached(asset_list, period="2y")
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
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
        mf_smooth = force_index.ewm(span=5, adjust=False).mean()

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
        max_down_vol_10 = down_volumes.iloc[-11:-1].max()
        
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
        squeeze_score = final_score / (abs(change_pct) + 0.02)

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
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(process_single_radar1, sym, df, bench) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: signals.append(res)

    return pd.DataFrame(signals).sort_values(by="Skor", ascending=False) if signals else pd.DataFrame()

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
        
        return { "Sembol": symbol, "Fiyat": round(curr_c, 2), "Trend": trend, "Setup": setup, "Skor": score, "RS": round(rs_score * 100, 1), "Etiketler": " | ".join(tags), "Detaylar": details }
    except: return None

# --- YENİ EKLENEN HACİM FONKSİYONLARI ---

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

@st.cache_data(ttl=3600)
def radar2_scan(asset_list, min_price=5, max_price=5000, min_avg_vol_m=0.5):
    # ORTAK HAFIZADAN ÇEKER (Altın Fırsatlar ile Aynı Havuz)
    try:
        data = fetch_market_data_cached(tuple(asset_list))
    except Exception as e:
        st.error(f"Radar 2 veri hatası: {e}")
        return pd.DataFrame()
        
    if data.empty: return pd.DataFrame()
    
    try: idx = yf.download("^GSPC", period="1y", progress=False)["Close"]
    except: idx = None

    results = []
    stock_dfs = []
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]: stock_dfs.append((symbol, data[symbol]))
            else:
                if len(asset_list) == 1: stock_dfs.append((symbol, data))
        except: continue

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(process_single_radar2, sym, df, idx, min_price, max_price, min_avg_vol_m) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)

    return pd.DataFrame(results).sort_values(by=["Skor", "RS"], ascending=False).head(50) if results else pd.DataFrame()

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

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(process_single_confirmed, sym, df, bench) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)
    
    return pd.DataFrame(results).sort_values(by="Hacim", ascending=False).head(20) if results else pd.DataFrame()

# --- TEMEL VE MASTER SKOR FONKSİYONLARI (YENİ) ---
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
# YENİ: ROYAL FLUSH 3.0 SETUP (DEEP VALUE & TRAP SNIPER) - 6 KATI KRİTERLİ TUZAK AVCISI
# ==============================================================================
def calculate_royal_flush_3_0_setup(ticker, df):
    """
    🩸 ROYAL FLUSH 3.0 (Deep Value & Trap Sniper)
    6 Katı Kriter:
    1. Z-Score <= -1.5 (Aşırı Satım/Ucuzluk bölgesi)
    2. OBV Yükseliyor (Gizli Para girişi var)
    3. Hacim Düşük (Panik satışı bitmiş/Tahta kurumuş)
    4. FVG / Smart Money İndirim Bölgesi Teması
    5. Trap Score < 0.3 (Boğa tuzakları cezalandırıldı, Ayı tuzakları ödüllendirildi)
    6. Lorentzian Güven Skoru >= %65
    """
    try:
        if df is None or df.empty or len(df) < 50:
            return None
            
        df_calc = df.copy()
        
        close = df_calc['Close']
        low = df_calc['Low']
        high = df_calc['High']
        open_p = df_calc['Open']
        
        if 'Volume' not in df_calc.columns or df_calc['Volume'].isnull().all():
            return None # Hacimsiz verilerde çalışmaz
        volume = df_calc['Volume']
        
        # --- 1. Z-SCORE KONTROLÜ (Ucuzluk: <= -1.5) ---
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        z_score = (close.iloc[-1] - sma20.iloc[-1]) / std20.iloc[-1]
        
        if pd.isna(z_score) or z_score > -1.5: 
            return None
        
        # --- 2. OBV KONTROLÜ (Gizli Para Girişi) ---
        obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
        obv_sma20 = obv.rolling(20).mean()
        
        if pd.isna(obv.iloc[-1]) or pd.isna(obv_sma20.iloc[-1]): return None
        # OBV ortalamanın üzerinde VE yönü yukarı bakıyor olmalı
        if not (obv.iloc[-1] > obv_sma20.iloc[-1] and obv.iloc[-1] >= obv.iloc[-2]):
            return None
            
        # --- 3. DÜŞÜK HACİM KONTROLÜ (Sakinlik & Panik Yok) ---
        avg_vol20 = volume.rolling(20).mean()
        if pd.isna(avg_vol20.iloc[-1]): return None
        if volume.iloc[-1] > avg_vol20.iloc[-1]: 
            return None # Son gün hacmi ortalamadan yüksekse (panik varsa) reddet
            
        # --- 4. FVG / SMART MONEY BÖLGESİ (İndirim Teması) ---
        # Son 12 bar içinde oluşan Bullish FVG'ye (Değer Boşluğuna) inmiş mi?
        fvg_found = False
        for i in range(len(df_calc)-14, len(df_calc)-2):
            if df_calc['Low'].iloc[i+2] > df_calc['High'].iloc[i]: # Bullish FVG
                if low.iloc[-1] <= df_calc['Low'].iloc[i+2]: # Boşluğa veya altına iğne atmış
                    fvg_found = True
                    break
        if not fvg_found: 
            return None
        
        # --- 5. TRAP SCORE HESAPLAMASI (Tuzak Skoru < 0.3) ---
        # ATR ve SMA
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        df_calc['TR'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df_calc['ATR'] = df_calc['TR'].rolling(14).mean()
        df_calc['SMA_50'] = close.rolling(50).mean()
        
        # Puan Artırıcılar (KÖTÜ DURUM: Boğa Tuzağı)
        rejection_wick = (high - close) > (df_calc['ATR'] * 0.7)
        fakeout_upper = rejection_wick & (volume < (avg_vol20 * 1.2))
        
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        rsi_prev = rsi.shift(1)
        false_breakout = (rsi_prev > 70) & (rsi < 70) & (close < open_p)
        
        # Puan Düşürücüler (İYİ DURUM: Ayı Tuzağı / Stop Patlatma)
        prev_low = low.shift(1)
        liquidity_sweep = (low < prev_low - (df_calc['ATR'] * 0.5)) & (close > low + (df_calc['ATR'] * 0.3))
        stop_hunt = (low < df_calc['SMA_50'] - (df_calc['ATR'] * 1.5)) & (close > df_calc['SMA_50'] - (df_calc['ATR'] * 0.5))
        
        # Son günkü tuzak skoru hesaplaması
        trap_score = 0.0
        if fakeout_upper.iloc[-1]: trap_score += 0.4
        if false_breakout.iloc[-1]: trap_score += 0.4
        if liquidity_sweep.iloc[-1]: trap_score -= 0.3
        if stop_hunt.iloc[-1]: trap_score -= 0.3
        
        trap_score = max(0.0, trap_score) # Sıfırın altına düşmesine izin verme
        
        if trap_score >= 0.3: 
            return None # Skor çok yüksek, riskli! Reddet.
            
        # --- 6. LORENTZIAN KONTROLÜ (Güven >= %65) ---
        # Performans artışı için Lorentzian'ı sona bıraktık (Zor hesaplanır)
        lor_res = calculate_lorentzian_classification(ticker)
        if not lor_res or lor_res['signal'] != 'YÜKSELİŞ' or lor_res['prob'] < 65:
            return None
            
        # 6 KURALI DA GEÇTİYSE SONUCU DÖNDÜR
        return {
            "Sembol": ticker,
            "Fiyat": float(close.iloc[-1]),
            "Z-Score": float(round(z_score, 2)),
            "Trap Skoru": float(round(trap_score, 2)),
            "Güven": f"%{lor_res['prob']:.0f}"
        }
        
    except Exception as e:
        return None

def scan_rf3_batch(asset_list):
    """
    YENİ ROYAL FLUSH 3.0 Toplu Tarama Ajanı
    """
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty: return pd.DataFrame()
    
    results = []
    stock_dfs = []
    
    # Çoklu veya tekli sembol verilerini listeye al
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
        
    # Her bir hisseyi fonksiyona sok
    for symbol, df in stock_dfs:
        res = calculate_royal_flush_3_0_setup(symbol, df)
        if res: results.append(res)
            
    return pd.DataFrame(results)

# ==============================================================================
# 🎯 KESİN DÖNÜŞ SİNYALLERİ (GELİŞTİRME 2 - 3'LÜ KESİŞİM)
# ==============================================================================
def process_single_kesin_donus(symbol, df, benchmark_series=None):
    if df is None or df.empty or len(df) < 60: return None

    # 1. Ayı Tuzağı (Bear Trap) Kontrolü (Mevcut fonksiyonunuzu çağırıyoruz)
    bt_res = process_single_bear_trap_live(df)
    if not bt_res: return None

    # 2. RSI Pozitif Uyumsuzluk Kontrolü (Optimize Edilmiş Kendi İçi Mantık)
    close = df['Close']; open_ = df['Open']
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rsi_series = 100 - (100 / (1 + gain/loss))

    curr_p = close.iloc[-5:]; prev_p = close.iloc[-20:-5]
    curr_r = rsi_series.iloc[-5:]; prev_r = rsi_series.iloc[-20:-5]
    
    rsi_val = float(rsi_series.iloc[-1])
    is_green_candle = close.iloc[-1] > open_.iloc[-1]

    is_bull_div = (curr_p.min() <= prev_p.min()) and \
                  (curr_r.min() > prev_r.min()) and \
                  (rsi_val < 55) and \
                  is_green_candle
                  
    if not is_bull_div: return None

    # 3. Gizli Toplama (Akıllı Para) Kontrolü (Mevcut fonksiyonunuzu çağırıyoruz)
    acc_res = process_single_accumulation(symbol, df, benchmark_series)
    if not acc_res: return None

    return {
        "Sembol": symbol,
        "Fiyat": f"{float(close.iloc[-1]):.2f}",
        "Zaman": bt_res["Zaman"],
        "Hacim_Gucu": acc_res["MF_Gucu_Goster"],
        "RSI": int(rsi_val),
        "Skor": acc_res["Skor"]
    }

@st.cache_data(ttl=900)
def scan_kesin_donus_batch(asset_list):
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty: return pd.DataFrame()

    cat = st.session_state.get('category', 'S&P 500')
    bench = get_benchmark_data(cat)

    results = []
    stock_dfs = []

    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]:
                    stock_dfs.append((symbol, data[symbol]))
            elif len(asset_list) == 1:
                stock_dfs.append((symbol, data))
        except: continue

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(process_single_kesin_donus, sym, df, bench) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)

    if results:
        return pd.DataFrame(results).sort_values(by="Skor", ascending=False)
    return pd.DataFrame()

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
# 🧱 YENİ: ARZ-TALEP (SUPPLY & DEMAND) VE ERC MOTORU
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
# 🦅 YENİ: ICT SNIPER TARAMA MOTORU (4 ŞARTLI DEDEKTÖR)
# ==============================================================================
def process_single_ict_setup(symbol, df):
    """
    ICT 2022 Mentorship Model - MAXIMUM WIN RATE (KESKİN NİŞANCI AJANI)
    Özellikler:
    1. FVG %50 (Consequent Encroachment) Girişi (Dar Stop, Yüksek Win Rate)
    2. 7-Mumluk Güçlendirilmiş Fraktal Likidite (Daha Zor Kırılan Tepeler)
    3. Hacim Onaylı Displacement (> %130)
    4. RRR >= 2.5 Asimetrik Giyotini
    """
    try:
        if df.empty or len(df) < 50: return None
        
        close = df['Close']; high = df['High']; low = df['Low']; open_ = df['Open']
        current_price = float(close.iloc[-1])
        
        # --- 1. HACİM VE GÖVDE (Displacement Teyidi) ---
        has_vol = 'Volume' in df.columns and not df['Volume'].isnull().all()
        volume = df['Volume'] if has_vol else pd.Series([1]*len(df), index=df.index)
        avg_vol = volume.rolling(20).mean()
        
        body_sizes = abs(close - open_)
        avg_body = body_sizes.rolling(20).mean()
        
        # --- 2. GÜÇLENDİRİLMİŞ FRAKTAL LİKİDİTE (Win Rate Hack 1) ---
        # 5 mumluk değil, sağında ve solunda 3'er mum olan 7-mumluk "Majör" swingleri arıyoruz.
        sw_highs = []; sw_lows = []
        for i in range(len(df)-40, len(df)-3): 
            if i < 3: continue
            if high.iloc[i] == max(high.iloc[i-3:i+4]):
                sw_highs.append((df.index[i], high.iloc[i], i))
            if low.iloc[i] == min(low.iloc[i-3:i+4]):
                sw_lows.append((df.index[i], low.iloc[i], i))
        
        if not sw_highs or not sw_lows: return None
        
        last_sh_val = sw_highs[-1][1]
        last_sl_val = sw_lows[-1][1]
        
        # --- 3. HTF TREND FİLTRESİ ---
        sma_50 = close.rolling(50).mean().iloc[-1]
        htf_bullish = current_price > sma_50
        htf_bearish = current_price < sma_50

        # =========================================================
        # SENARYO A: LONG (BOĞA) SETUP ARANIYOR
        # =========================================================
        if htf_bullish:
            # Likidite Avı
            recent_low = low.iloc[-10:].min()
            sweep_lows = [sl for sl in sw_lows[:-1] if recent_low < sl[1]] 
            
            if sweep_lows:
                # MSS (Market Structure Shift)
                if close.iloc[-1] > last_sh_val or close.iloc[-2] > last_sh_val:
                    
                    # Hacimli Yeşil Mum (Displacement)
                    green_bodies = body_sizes.where(close > open_, 0)
                    max_green_recent = green_bodies.iloc[-5:].max()
                    idx_max_green = green_bodies.iloc[-5:].idxmax()
                    
                    vol_check = volume[idx_max_green] > (avg_vol[idx_max_green] * 1.3) if has_vol else True
                    
                    if max_green_recent > (avg_body.iloc[-1] * 1.5) and vol_check:
                        
                        # FVG Tespiti
                        for i in range(len(df)-1, len(df)-5, -1):
                            if low.iloc[i] > high.iloc[i-2]: # Bullish FVG
                                fvg_top = low.iloc[i]
                                fvg_bot = high.iloc[i-2]
                                
                                # --- 4. WIN RATE HACK 2: Consequent Encroachment (CE) ---
                                # FVG'nin tam %50 orta noktasını hesapla
                                fvg_ce = fvg_bot + ((fvg_top - fvg_bot) * 0.5)
                                
                                # Fiyat FVG'nin tepesinden değil, %50 indirimli ortasından (CE) tepki almalı
                                if current_price <= (fvg_ce * 1.01) and current_price >= (fvg_bot * 0.99):
                                    
                                    stop_loss = recent_low * 0.99 # Sweep ucunun %1 altı
                                    entry_price = current_price
                                    risk = entry_price - stop_loss
                                    if risk <= 0: continue
                                    
                                    # Hedef
                                    targets = [sh[1] for sh in sw_highs if sh[1] > entry_price * 1.02]
                                    if not targets: continue
                                    target_price = min(targets) 
                                    
                                    reward = target_price - entry_price
                                    rrr = reward / risk
                                    
                                    # VETO Giyotini (Giriş CE'de olduğu için RRR rahatça 2.5'i geçer)
                                    if rrr >= 2.5:
                                        return {
                                            "Sembol": symbol, "Fiyat": current_price,
                                            "Yön": "LONG", "İkon": "🎯", "Renk": "#16a34a",
                                            "Durum": f"Giriş: CE | RRR: {rrr:.1f} | Hedef: ${target_price:.2f}",
                                            "Stop_Loss": f"{stop_loss:.2f}",
                                            "Skor": 99
                                        }

        # =========================================================
        # SENARYO B: SHORT (AYI) SETUP ARANIYOR
        # =========================================================
        elif htf_bearish:
            # Likidite Avı
            recent_high = high.iloc[-10:].max()
            sweep_highs = [sh for sh in sw_highs[:-1] if recent_high > sh[1]]
            
            if sweep_highs:
                # MSS (Market Structure Shift)
                if close.iloc[-1] < last_sl_val or close.iloc[-2] < last_sl_val:
                    
                    # Hacimli Kırmızı Mum (Displacement)
                    red_bodies = body_sizes.where(close < open_, 0)
                    max_red_recent = red_bodies.iloc[-5:].max()
                    idx_max_red = red_bodies.iloc[-5:].idxmax()
                    
                    vol_check = volume[idx_max_red] > (avg_vol[idx_max_red] * 1.3) if has_vol else True
                    
                    if max_red_recent > (avg_body.iloc[-1] * 1.5) and vol_check:
                        
                        # FVG Tespiti
                        for i in range(len(df)-1, len(df)-5, -1):
                            if high.iloc[i] < low.iloc[i-2]: # Bearish FVG
                                fvg_top = low.iloc[i-2]
                                fvg_bot = high.iloc[i]
                                
                                # --- 4. WIN RATE HACK 2: Consequent Encroachment (CE) ---
                                fvg_ce = fvg_bot + ((fvg_top - fvg_bot) * 0.5)
                                
                                # Fiyat CE bölgesinden tepki almalı
                                if current_price >= (fvg_ce * 0.99) and current_price <= (fvg_top * 1.01):
                                    
                                    stop_loss = recent_high * 1.01 # Sweep ucunun %1 üstü
                                    entry_price = current_price
                                    risk = stop_loss - entry_price
                                    if risk <= 0: continue
                                    
                                    # Hedef
                                    targets = [sl[1] for sl in sw_lows if sl[1] < entry_price * 0.98]
                                    if not targets: continue
                                    target_price = max(targets)
                                    
                                    reward = entry_price - target_price
                                    rrr = reward / risk
                                    
                                    # VETO Giyotini
                                    if rrr >= 2.5:
                                        return {
                                            "Sembol": symbol, "Fiyat": current_price,
                                            "Yön": "SHORT", "İkon": "🎯", "Renk": "#dc2626",
                                            "Durum": f"Giriş: CE | RRR: {rrr:.1f} | Hedef: ${target_price:.2f}",
                                            "Stop_Loss": f"{stop_loss:.2f}",
                                            "Skor": 99
                                        }

        return None

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
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(process_single_ict_setup, sym, df) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)
            
    # 3. Sonuç Döndür
    if results:
        return pd.DataFrame(results)
    
    return pd.DataFrame()    
# ==============================================================================
# MINERVINI SEPA MODÜLÜ (HEM TEKLİ ANALİZ HEM TARAMA) - GÜNCELLENMİŞ VERSİYON
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
            df = get_safe_historical_data(ticker, period="2y")
            
        if df is None or len(df) < 260: return None
        
        # MultiIndex Temizliği
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # Endeks verisi (RS için) - Eğer cache'de yoksa indir
        bench_df = get_safe_historical_data(benchmark_ticker, period="2y")
        
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
        
        # Zirveye Yakınlık: Minervini %25 der ama biz sertleşip %15 (0.85) yapıyoruz
        near_high = curr_price >= (year_high * 0.9)
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

# ==============================================================================
# LORENTZIAN CLASSIFICATION (10 YILLIK GÜNLÜK VERİ - TRADINGVIEW FORMÜLLERİ)
# ==============================================================================
@st.cache_data(ttl=3600)
def calculate_lorentzian_classification(ticker, k_neighbors=8):
    try:
        # 1. VERİ ÇEKME (10 YILLIK GÜNLÜK - Derin Öğrenme İçin Şart)
        clean_ticker = ticker.replace(".IS", "")
        if ".IS" in ticker: clean_ticker = ticker 
        
        try:
            # Artık doğrudan yeni akıllı yerel önbellek fonksiyonumuzu kullanıyoruz
            # Diskten şimşek hızında çekecek ve sadece son günleri yfinance'e soracak.
            df = get_safe_historical_data(clean_ticker, period="10y", interval="1d")
        except: return None
        if df is None or len(df) < 200: return None 

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # Hesaplama Serileri
        src = df['Close']
        high = df['High']
        low = df['Low']
        hlc3 = (high + low + src) / 3

        # ---------------------------------------------------------
        # 3. FEATURE ENGINEERING (TRADINGVIEW SCRIPT BİREBİR)
        # ---------------------------------------------------------
        
        # --- Feature 1: RSI (14) ---
        delta = src.diff()
        up = delta.clip(lower=0)
        down = -1 * delta.clip(upper=0)
        avg_up = up.ewm(alpha=1/14, adjust=False).mean()
        avg_down = down.ewm(alpha=1/14, adjust=False).mean()
        rs = avg_up / avg_down
        f1_rsi14 = 100 - (100 / (1 + rs))

        # --- Feature 2: WaveTrend (10, 11) ---
        esa = hlc3.ewm(span=10, adjust=False).mean()
        d = abs(hlc3 - esa).ewm(span=10, adjust=False).mean()
        ci = (hlc3 - esa) / (0.015 * d)
        f2_wt = ci.ewm(span=11, adjust=False).mean()

        # --- Feature 3: CCI (20) ---
        tp = hlc3
        sma20 = tp.rolling(20).mean()
        mad = (tp - sma20).abs().rolling(20).mean()
        f3_cci = (tp - sma20) / (0.015 * mad)

        # --- Feature 4: ADX (20) ---
        # Script ADX periyodunu 20 kullanıyor.
        tr1 = high - low
        tr2 = abs(high - src.shift(1))
        tr3 = abs(low - src.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr20 = tr.ewm(alpha=1/20, adjust=False).mean()

        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        
        plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(alpha=1/20, adjust=False).mean() / atr20)
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(alpha=1/20, adjust=False).mean() / atr20)
        dx = (abs(plus_di - minus_di) / abs(plus_di + minus_di)) * 100
        f4_adx = dx.ewm(alpha=1/20, adjust=False).mean()

        # --- Feature 5: RSI (9) ---
        avg_up9 = up.ewm(alpha=1/9, adjust=False).mean()
        avg_down9 = down.ewm(alpha=1/9, adjust=False).mean()
        rs9 = avg_up9 / avg_down9
        f5_rsi9 = 100 - (100 / (1 + rs9))

        # 4. NORMALİZASYON (Min-Max Scaling 0-1)
        features_df = pd.DataFrame({
            'f1': f1_rsi14, 'f2': f2_wt, 'f3': f3_cci, 'f4': f4_adx, 'f5': f5_rsi9
        }).dropna()

        features_norm = (features_df - features_df.min()) / (features_df.max() - features_df.min())
        features_norm = features_norm.fillna(0.5)

        # ---------------------------------------------------------
        # 5. HEDEF (TARGET) - OPTİMİZASYON
        # ---------------------------------------------------------
        # TradingView 4 bar sonrasına bakar. Günlük grafikte bu 4 gün eder.
        # Biz burada "Yarın Yükselecek mi?" sorusuna (1 Bar) odaklanıyoruz.
        # Bu, günlük trade için daha değerlidir.
        future_close = src.shift(-1) 
        target = (future_close > src).astype(int) 

        common_idx = features_norm.index.intersection(target.index)
        features_final = features_norm.loc[common_idx]
        target_final = target.loc[common_idx]

        if len(features_final) < 50: return None

        # Eğitim: Son mum HARİÇ tüm geçmiş
        current_features = features_final.iloc[-1].values
        history_features = features_final.iloc[:-1].values
        history_targets = target_final.iloc[:-1].values

        # ---------------------------------------------------------
        # 6. LORENTZIAN MESAFE (Script ile Birebir)
        # ---------------------------------------------------------
        abs_diff = np.abs(history_features - current_features)
        distances = np.sum(np.log(1 + abs_diff), axis=1)

        nearest_indices = np.argsort(distances)[:k_neighbors]

        bullish_votes = 0
        bearish_votes = 0

        for idx in nearest_indices:
            if history_targets[idx] == 1: bullish_votes += 1
            else: bearish_votes += 1

        if bullish_votes >= bearish_votes:
            signal = "YÜKSELİŞ"
            prob = (bullish_votes / k_neighbors) * 100
            color = "#16a34a"
        else:
            signal = "DÜŞÜŞ"
            prob = (bearish_votes / k_neighbors) * 100
            color = "#dc2626"

        return {
            "signal": signal,
            "prob": prob,
            "votes": max(bullish_votes, bearish_votes),
            "total": k_neighbors,
            "color": color,
            "bars": len(df) # Veri derinliğini görmek için
        }

    except Exception: return None

def render_lorentzian_panel(ticker, just_text=False):
    data = calculate_lorentzian_classification(ticker)
    
    # 1. KİLİT: Veri hiç yoksa çık (Bunu koymazsan kod çöker)
    if not data: return ""
    # 2. KİLİT: Veri var ama güven 7/8'den düşükse çık (Senin istediğin filtre)
    if data['votes'] < 7: return ""

    display_prob = int(data['prob'])
    # İkon seçimi
    ml_icon = "🚀" if data['signal'] == "YÜKSELİŞ" and display_prob >= 75 else "🐻" if data['signal'] == "DÜŞÜŞ" and display_prob >= 75 else "🧠"
    
    bar_width = display_prob
    signal_text = f"{data['signal']} BEKLENTİSİ"

    # Başlık: GÜNLÜK
    # Alt Bilgi: Vade: 1 Gün
    # Not: ticker temizliğini burada da yapıyoruz
    clean_name = ticker.replace('.IS', '').replace('-USD', '').replace('=F', '')
    
    # --- HTML TASARIMI (GÜNCELLENDİ) ---
    html_content = f"""
    <div class="info-card" style="border-top: 3px solid {data['color']}; margin-bottom: 15px;">
        <div class="info-header" style="color:{data['color']}; display:flex; justify-content:space-between; align-items:center;">
            <span>{ml_icon} Lorentzian (GÜNLÜK): {clean_name}</span>
        </div>
        
        <div style="text-align:center; padding:8px 0;">
            <div style="display:flex; justify-content:center; align-items:center; gap:10px; margin-bottom:4px;">
                <span style="font-size:0.9rem; font-weight:800; color:{data['color']}; letter-spacing:0.5px;">
                    {signal_text}
                </span>
                <span style="font-size:0.7rem; background:{data['color']}15; padding:2px 8px; border-radius:10px; font-weight:700; color:{data['color']};">
                    %{display_prob} Güven
                </span>
            </div>

            <div style="font-size:0.65rem; color:#64748B;">
                Son 10 Yılın verisini inceledi.<br>
                Benzer <b>8</b> senaryonun <b>{data['votes']}</b> tanesinde yön aynıydı.
            </div>
        </div>

        <div style="margin-top:5px; margin-bottom:8px; padding:0 4px;">
            <div style="display:flex; justify-content:space-between; font-size:0.65rem; color:#64748B; margin-bottom:2px;">
                <span>Oylama: <b>{data['votes']}/{data['total']}</b></span>
                <span>Vade: <b>1 Gün (Yarın)</b></span>
            </div>
            <div style="width:100%; height:6px; background:#e2e8f0; border-radius:3px; overflow:hidden;">
                <div style="width:{bar_width}%; height:100%; background:{data['color']};"></div>
            </div>
        </div>
    </div>
    """
    if not just_text:  # <-- EĞER SADECE METİN İSTENMİYORSA ÇİZ
        st.markdown(html_content.replace("\n", " "), unsafe_allow_html=True)

# 3. VE EN ÖNEMLİ DEĞİŞİKLİK: AI İÇİN METİN OLUŞTUR VE DÖNDÜR
    ai_data_text = f"""
LORENTZIAN MODELİ'NİN GEÇMİŞ 2000 GÜNE BAKARAK YAPTIĞI YARIN (1 GÜNLÜK) TAHMİNİ: 
- Beklenti: {data['signal']}
- Güven Oranı: %{display_prob}
- Oylama (Benzer Senaryo): {data['votes']}/{data['total']}
"""
    return ai_data_text

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
        '🎯 Kesin Dönüş': 90,   # YENİ EKLENDİ: En yüksek taban puan
        '🩸 Royal Flush Dip Avcısı': 85,
        '🦅 ICT Sniper': 85,
        '🦁 Minervini': 80,
        '🚀 Grandmaster': 80,
        '♠️ Royal Flush (Klasik)': 80,
        '🐻 Bear Trap': 75,
        '🔨 Breakout Yapan': 70,
        '🏆 RS Lideri': 60
    }
    
    # 2. DESTEKLEYİCİ MODELLER (BONUS PUANLAR)
    bonus_powers = {
        '🏆 RS Lideri': 15,
        '🤫 Sentiment (Akıllı Para)': 15,
        '📈 RSI Pozitif Uyumsuzluk': 10,
        '🐻 Bear Trap': 10,
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
    all_icons = ['🎯', '🩸', '🦅', '🦁', '🐻', '🏆', '📈', '🤫', '🔨', '📡', '🚀', '♠️', '⭐'] 
    icons = [src.split(' ')[0] for src in sources_list if any(src.startswith(i) for i in all_icons)]
    icon_str = " ".join(icons)

    # 👇 İŞTE EKSİK OLAN O SATIR (BUNU EKLE) 👇
    confluence_count = len(sources_list)

    # ====================================================================
    # 6. ÖZEL SENARYO DEDEKTÖRÜ (AĞIRLIKLI PUANLAMA MİMARİSİ)
    # Sistem tüm senaryoları okur ve hisse için en güçlü olanı seçer.
    # ====================================================================
    
    # Sinyallerin varlık kontrolü
    has_kd = '🎯 Kesin Dönüş' in sources_list
    has_dip = '🩸 Royal Flush Dip Avcısı' in sources_list
    has_ict = '🦅 ICT Sniper' in sources_list
    has_min = '🦁 Minervini' in sources_list
    has_gm = '🚀 Grandmaster' in sources_list
    has_rfc = '♠️ Royal Flush (Klasik)' in sources_list
    has_bt = '🐻 Bear Trap' in sources_list
    has_break = '🔨 Breakout Yapan' in sources_list
    has_rs = '🏆 RS Lideri' in sources_list
    has_sent = '🤫 Sentiment (Akıllı Para)' in sources_list
    has_div = '📈 RSI Pozitif Uyumsuzluk' in sources_list
    has_1_5 = '📡 1-5 Günlük Yükseliş' in sources_list
    has_star = '⭐ Yıldız Adayı' in sources_list

    # Hissenin uygun olduğu tüm senaryoları bu sepete atacağız
    gecerli_senaryolar = []

    # YENİ EKLENEN 0. SENARYO: KUSURSUZ KESİŞİM (En yüksek öncelik - Ağırlık: 95)
    if has_kd:
        gecerli_senaryolar.append((95, "🎯 Kusursuz Kesişim: Ayı tuzağı (Stop patlatma), RSI pozitif uyumsuzluğu ve akıllı para girişi (Gizli toplama) aynı anda devrede. Sistemdeki en nadir ve kazanma oranı en yüksek dipten dönüş sinyali!"))

    # 1. ZEHİRLİ KIRILIM (Acil Durum Kalkanı - Ağırlık: 999)
    # Sadece tamamen desteksiz, sığ ve trendi olmayan sahte kırılımları avlar.
    if has_break and not (has_sent or has_rs or has_min or has_rfc or has_gm):
        gecerli_senaryolar.append((999, "☠️ Zehirli Kırılım (Boğa Tuzağı): Direnç kırıldı ancak arkasında hiçbir trend, hacim veya RS gücü yok! Sahte kırılım (Fakeout) riski çok yüksek."))
        
    # 2. BÜYÜME PATLAMASI (En Güçlü Alım Fırsatı - Ağırlık: 90)
    if (has_min or has_rfc or has_gm) and has_break and (has_sent or has_rs):
        gecerli_senaryolar.append((90, "🌪️ Büyüme Patlaması: Kusursuz ralli! Fiyat daralmayı tamamladı, arz kurudu ve kurumsal hacim/güç onayıyla direnci paramparça etti."))

    # 3. KURUMSAL LİKİDİTE AVI (Dipten Dönüş - Ağırlık: 85)
    if (has_ict or has_dip or has_bt) and (has_div or has_sent):
        gecerli_senaryolar.append((85, "🪤 Kurumsal Likidite Avı: Küçük yatırımcının stopları patlatıldı (Sweep). Akıllı para bu paniği fırsat bilip dipten malı topladı, V-Dönüşü tetikleniyor."))

    # 4. TREND İÇİ İSKONTO (Güvenli Katılım - Ağırlık: 80)
    if (has_rfc or has_min) and (has_ict or has_dip or has_div or has_bt):
        gecerli_senaryolar.append((80, "🌊 Trend İçi İskonto: Güçlü ana trendde, kurumsal maliyetlenme bölgesine (OTE/FVG) harika bir düzeltme (Pullback) yaşandı. Güvenli katılım noktası."))

    # 5. SESSİZ FIRTINA (Kırılım Öncesi Pusu - Ağırlık: 75)
    if (has_gm or has_star) and has_sent and not has_break:
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
                sym = row.get('Sembol', row.get('Sembol_Raw', None))
                if not sym: continue
                fiyat = row.get('Fiyat', row.get('Güncel_Fiyat', 0))
                if sym not in candidates:
                    candidates[sym] = {'sources': [], 'price': fiyat}
                if source_name not in candidates[sym]['sources']:
                    candidates[sym]['sources'].append(source_name)

    # 1. HAVUZU OLUŞTUR (16 KAYNAK)
    # Yüksek hassasiyetli scanner'lar — limit 5 (az ama kaliteli sinyal)
    add_candidates(st.session_state.get('kesin_donus_data'), '🎯 Kesin Dönüş', limit=5)
    add_candidates(st.session_state.get('rf3_scan_data'), '🩸 Royal Flush Dip Avcısı', limit=5)
    add_candidates(st.session_state.get('royal_results'), '♠️ Royal Flush (Klasik)', limit=5)
    add_candidates(st.session_state.get('ict_scan_data'), '🦅 ICT Sniper', limit=5)
    add_candidates(st.session_state.get('minervini_data'), '🦁 Minervini', limit=5)
    add_candidates(st.session_state.get('bear_trap_data'), '🐻 Bear Trap', limit=5)
    add_candidates(st.session_state.get('breakout_right'), '🔨 Breakout Yapan', limit=5)
    # Geniş tarama yapan scanner'lar — limit 10 (daha fazla aday)
    add_candidates(st.session_state.get('accum_data'), '🤫 Sentiment (Akıllı Para)', limit=10)
    add_candidates(st.session_state.get('gm_results'), '🚀 Grandmaster', limit=10)
    add_candidates(st.session_state.get('rs_leaders_data'), '🏆 RS Lideri', limit=10)
    add_candidates(st.session_state.get('radar2_data'), '⭐ Yıldız Adayı', limit=10)
    add_candidates(st.session_state.get('scan_data'), '📡 1-5 Günlük Yükseliş', limit=10)
    # Daha önce bağlanmayan scanner'lar
    add_candidates(st.session_state.get('pattern_data'), '📊 VIP Formasyon', limit=10)
    add_candidates(st.session_state.get('breakout_left'), '🔥 Isınan (STP)', limit=10)
    # golden_pattern_data artık dict döndürüyor — formations kısmını al
    _gp_raw = st.session_state.get('golden_pattern_data')
    if isinstance(_gp_raw, dict):
        add_candidates(_gp_raw.get('formations'), '💎 Altın Fırsat', limit=10)
    elif isinstance(_gp_raw, pd.DataFrame):
        add_candidates(_gp_raw, '💎 Altın Fırsat', limit=10)
    
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
      Grup 1 — Yapısal : ICT, Kesin Dönüş, Bear Trap, Royal Flush
      Grup 2 — Momentum: Minervini, Grandmaster, RS Leaders, Radar1/2
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
            sym = row.get('Sembol') or row.get('Sembol_Raw')
            if not sym: continue
            price = row.get('Fiyat') or row.get('Güncel_Fiyat') or 0
            if sym not in groups[g_key]['sources']:
                groups[g_key]['sources'][sym] = {'price': float(price) if price else 0, 'scanners': []}
            if source_name not in groups[g_key]['sources'][sym]['scanners']:
                groups[g_key]['sources'][sym]['scanners'].append(source_name)

    # --- GRUP 1: YAPISAL ---
    add_to_group('yapi', st.session_state.get('ict_scan_data'),    'ICT Sniper')
    add_to_group('yapi', st.session_state.get('kesin_donus_data'), 'Kesin Dönüş')
    add_to_group('yapi', st.session_state.get('bear_trap_data'),   'Bear Trap')
    add_to_group('yapi', st.session_state.get('royal_results'),    'Royal Flush')
    add_to_group('yapi', st.session_state.get('rf3_scan_data'),    'RF Dip Avcısı')

    # --- GRUP 2: MOMENTUM ---
    add_to_group('momentum', st.session_state.get('minervini_data'),  'Minervini')
    add_to_group('momentum', st.session_state.get('gm_results'),      'Grandmaster')
    add_to_group('momentum', st.session_state.get('rs_leaders_data'), 'RS Lideri')
    add_to_group('momentum', st.session_state.get('radar2_data'),     'Radar2')
    add_to_group('momentum', st.session_state.get('scan_data'),       'Radar1')

    # --- GRUP 3: FORMASYON/DEĞER ---
    _gp = st.session_state.get('golden_pattern_data')
    if isinstance(_gp, dict):
        add_to_group('formasyon', _gp.get('formations'), 'Altın Fırsat')
        if _gp.get('formations') is not None: groups['formasyon']['scanned'] = True
    elif isinstance(_gp, pd.DataFrame):
        add_to_group('formasyon', _gp, 'Altın Fırsat')
    add_to_group('formasyon', st.session_state.get('pattern_data'),   'VIP Formasyon')
    add_to_group('formasyon', st.session_state.get('accum_data'),     'Gizli Birikim')
    add_to_group('formasyon', st.session_state.get('breakout_right'), 'Confirmed Breakout')

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
    data = get_batch_data_cached(asset_list, period="2y")
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
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
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
        return df.sort_values(by=["Raw_Score", "rs_val"], ascending=[False, False]).head(30)
    
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
        return pd.DataFrame(results).sort_values(by="Skor", ascending=False)
    
    return pd.DataFrame()

@st.cache_data(ttl=600)
def calculate_sentiment_score(ticker):
    try:
        # Veri Çekme (2y: SMA200 garantisi için)
        df = get_safe_historical_data(ticker, period="2y")
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
        
        # --- PUAN AĞIRLIKLARI ---
        if is_index:
            W_STR, W_TR, W_VOL = 25, 25, 25
            W_MOM, W_VOLA = 15, 10
            W_RS = 0
        else:
            W_STR, W_TR, W_VOL = 20, 20, 20
            W_MOM, W_VOLA = 15, 10
            W_RS = 15

        # =========================================================
        # 1. YAPI (MARKET STRUCTURE)
        # =========================================================
        score_str = 0; reasons_str = []
        recent_high = high.rolling(20).max().shift(1).iloc[-1]
        recent_low = low.rolling(20).min().shift(1).iloc[-1]
        curr_close = close.iloc[-1]
        
        if curr_close > recent_high:
            score_str += (W_STR * 0.6); reasons_str.append("BOS: Zirve Kırılımı")
        elif curr_close >= (recent_high * 0.97):
            score_str += (W_STR * 0.6); reasons_str.append("Zirveye Yakın (Güçlü)")
            
        if low.iloc[-1] > recent_low:
            score_str += (W_STR * 0.4); reasons_str.append("HL: Yükselen Dip")

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
            
        # KURAL 2: OBV (On Balance Volume)
        obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
        obv_ma = obv.rolling(10).mean()
        if obv.iloc[-1] > obv_ma.iloc[-1]: 
            score_vol += (W_VOL * 0.4)
            reasons_vol.append("OBV+")

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

        # =========================================================
        # 5. VOLATİLİTE
        # =========================================================
        score_vola = 0; reasons_vola = []
        std = close.rolling(20).std()
        upper = close.rolling(20).mean() + (2 * std)
        lower = close.rolling(20).mean() - (2 * std)
        bb_width = (upper - lower) / close.rolling(20).mean()
        
        if bb_width.iloc[-1] < bb_width.rolling(20).mean().iloc[-1]:
            score_vola += 10; reasons_vola.append("Sıkışma")
            
        # =========================================================
        # 6. GÜÇ (RS)
        # =========================================================
        score_rs = 0; reasons_rs = []
        if not is_index:
            bench_ticker = "XU100.IS" if ".IS" in ticker else "^GSPC"
            try:
                bench_df = get_safe_historical_data(bench_ticker, period="2y")
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
            return f"<span style='font-size:0.7rem; color:#334155; font-style:italic; font-weight:300;'>({' + '.join(lst)})</span>"
        
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

# ====================================================================
# ICT TREND DÖNÜŞÜ (MARKET STRUCTURE SHIFT & DISPLACEMENT) ALGORİTMASI
# ====================================================================
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
        # YENİ: ICT UYUMLU YAKIN LİKİDİTE (DEALING RANGE) HESAPLAMASI
        # ====================================================================
        # 1. Sadece yakın geçmişteki (son 20 mum) İşlem Aralığına odaklan
        recent_df = df.iloc[-20:]
        
        # 2. Fiyatın altındaki dipleri bul (Sell-Side Liquidity - SSL)
        lows_below = recent_df[recent_df['Low'] < curr_price]['Low'].drop_duplicates()
        nearest_ssl = lows_below.sort_values(ascending=False)
        
        # 3. Fiyatın üstündeki tepeleri bul (Buy-Side Liquidity - BSL)
        highs_above = recent_df[recent_df['High'] > curr_price]['High'].drop_duplicates()
        nearest_bsl = highs_above.sort_values(ascending=True)

        # 4. Yön (Bias) durumuna göre dinamik hedefleri ata
        if "bearish" in bias:
            final_target = nearest_ssl.iloc[0] if len(nearest_ssl) > 0 else curr_price * 0.98
            derin_hedef = nearest_ssl.iloc[1] if len(nearest_ssl) > 1 else final_target * 0.98
            ileri_hedef = curr_price * 1.02 # Ayı'da anlamsızdır ama hata vermesin diye tutuyoruz
            safety_lvl = nearest_bsl.iloc[0] if len(nearest_bsl) > 0 else curr_price * 1.02
        else:
            final_target = nearest_bsl.iloc[0] if len(nearest_bsl) > 0 else curr_price * 1.02
            ileri_hedef = nearest_bsl.iloc[1] if len(nearest_bsl) > 1 else final_target * 1.02
            derin_hedef = curr_price * 0.98 # Boğa'da anlamsızdır ama hata vermesin diye tutuyoruz
            safety_lvl = nearest_ssl.iloc[0] if len(nearest_ssl) > 0 else curr_price * 0.98

        # Matematiksel Emniyet Kilidi (İkinci hedef, ilk hedeften daha geride olamaz)
        if "bearish" in bias and derin_hedef >= final_target:
            derin_hedef = final_target * 0.98
        if "bullish" in bias and ileri_hedef <= final_target:
            ileri_hedef = final_target * 1.02

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
        safety_txt = f"hemen dibindeki {safety_lvl:.2f}" if dist_safety < 1.0 else f"majör iptal seviyesi olan {safety_lvl:.2f}"

        # Hedefler arası anlamlılık kontrolü: %1.5'ten küçük fark = ayrı seviye değil, küme
        second_gap = abs(ileri_hedef - final_target) / max(abs(final_target), 1) * 100
        deep_gap   = abs(derin_hedef - final_target) / max(abs(final_target), 1) * 100

        if is_bullish and not is_premium:
            # 1. ÇEYREK: Boğa + Ucuzluk (İdeal Long Bölgesi)
            if second_gap >= 1.5:
                lines = [
                    f"Trend yukarı (Bullish) ve fiyat cazip (Discount) bölgesinde. Kurumsal alım iştahı ivmeleniyor. İlk olarak {hedef_1_txt} doğru hareket, ardından {hedef_2_txt} yürüyüşü izlenebilir. Sermaye koruması için {safety_txt} yakından takip edilmeli.",
                    f"İdeal 'Smart Money' koşulları devrede: Yön yukarı, fiyat iskontolu. Toplanan emirlerle {hedef_1_txt} doğru likidite avı hedefleniyor. Olası tuzaklara karşı {safety_txt} seviyesinin altı yapısal iptal alanıdır.",
                ]
            else:
                lines = [
                    f"Trend yukarı (Bullish) ve fiyat cazip (Discount) bölgesinde. Yakın hedefler {final_target:.2f}–{ileri_hedef:.2f} aralığında sıkışmış (dar konsolidasyon). Bu bölgeyi yukarı kırarsa yükseliş ivmelenebilir. İptal seviyesi: {safety_txt}.",
                    f"İdeal 'Smart Money' koşulları devrede: Yön yukarı, fiyat iskontolu. Fiyat dar bir konsolidasyon bölgesinde; {final_target:.2f} üzerinde kalıcılık yükseliş için kritik. {safety_txt} altı yapısal iptal alanıdır.",
                ]
        elif is_bullish and is_premium:
            # 2. ÇEYREK: Boğa + Pahalılık (FOMO / Kâr Realizasyonu Riski)
            if second_gap >= 1.5:
                lines = [
                    f"Trend yukarı (Bullish) ancak fiyat pahalılık (Premium) bölgesinde. {hedef_1_txt} doğru ivme sürse de, bu bölgelerde kurumsal kâr satışları (Realizasyon) gelebileceği unutulmamalı. {safety_txt} kırılırsa trend bozulur.",
                    f"Yapı pozitif olsa da fiyat 'Premium' seviyelerde yorulma emareleri gösterebilir. Sıradaki dirençler {final_target:.2f} ve {ileri_hedef:.2f} seviyeleri. Buralardan yeni maliyetlenmek risklidir; {safety_txt} altı kapanışlarda anında savunmaya geçilmeli.",
                ]
            else:
                lines = [
                    f"Trend yukarı (Bullish) ancak fiyat pahalılık (Premium) bölgesinde. Yakın dirençler {final_target:.2f}–{ileri_hedef:.2f} arasında kümelenmiş; bu bölgede kurumsal realizasyon riski yüksek. Yeni alım için erken, {safety_txt} takip edilmeli.",
                    f"Yapı pozitif olsa da fiyat 'Premium' seviyelerde. Dar direnç kümesi ({final_target:.2f}–{ileri_hedef:.2f}) aşılmadan güçlü bir hareket beklenmemeli. {safety_txt} altı kapanışlarda anında savunmaya geçilmeli.",
                ]
        elif not is_bullish and is_premium:
            # 3. ÇEYREK: Ayı + Pahalılık (İdeal Short / Dağıtım Bölgesi)
            if deep_gap >= 1.5:
                lines = [
                    f"Trend aşağı (Bearish) ve fiyat tam dağıtım (Premium) bölgesinde. Satıcılı baskı sürüyor; ilk durak olan {final_target:.2f} kırıldıktan sonra gözler {hedef_derin_txt} çevrilebilir. Dönüş için {safety_txt} üzerinde kalıcılık şart.",
                    f"Piyasa yapısı zayıf ve kurumsal oyuncular mal çıkıyor (Distribution). Pahalılık bölgesinden başlayan düşüş trendinde {hedef_derin_txt} doğru çekilme ihtimali masada. İptal seviyesi: {safety_lvl:.2f}.",
                ]
            else:
                lines = [
                    f"Trend aşağı (Bearish) ve fiyat dağıtım (Premium) bölgesinde. Alt hedefler {final_target:.2f}–{derin_hedef:.2f} arasında sıkışmış; anlamlı düşüş için bu bölgenin altına kalıcı geçiş gerekiyor. Dönüş onayı: {safety_txt} üzerinde kapanış.",
                    f"Piyasa yapısı zayıf, dağıtım devam ediyor. Yakın hedefler dar bir bantta kümelenmiş ({final_target:.2f}–{derin_hedef:.2f}). Bu bölge kırılmadıkça gerçek bir düşüş hamlesi başlamaz; {safety_txt} direnç olarak izlenmeli.",
                ]
        else:
            # 4. ÇEYREK: Ayı + Ucuzluk (Aşırı Satım / Sweep Beklentisi)
            if deep_gap >= 1.5:
                lines = [
                    f"Trend aşağı (Bearish) ancak fiyat iskontolu (Discount) bölgeye inmiş durumda. İlk durak {final_target:.2f} olsa da buralardan 'Short' açmak risklidir, kurumsallar stop patlatıp dönebilir. Dönüş onayı için {safety_txt} izlenmeli.",
                    f"Aşırı satım (Oversold) bölgesi! Yapı negatif görünse de fiyat ucuzlamış. {hedef_derin_txt} doğru son bir silkeleme (Liquidity Hunt) yaşanıp sert tepki gelebilir. Trend dönüşü için {safety_lvl:.2f} aşılmalı.",
                ]
            else:
                lines = [
                    f"Trend aşağı (Bearish) ancak fiyat aşırı satılmış bölgede. Hedef seviyeleri {final_target:.2f}–{derin_hedef:.2f} arasında kümelenmiş — anlamlı ek düşüş için alan kalmamış. Olası stop avı (Liquidity Hunt) sonrası tepki için {safety_txt} üzeri izlenmeli.",
                    f"Aşırı satım bölgesi! Hedefler birbirine çok yakın ({final_target:.2f}–{derin_hedef:.2f}); büyük fonlar bu dar bantta stop avı yapabilir. Trend dönüşü için {safety_txt} üzerinde kalıcılık gerekli.",
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
            "target": final_target, "structural_target": structural_target,
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
        poc_price = calculate_volume_profile_poc(df, lookback=20, bins=20)
        # --------------------------------------------------------
        o = df['Open']; h = df['High']; l = df['Low']; c = df['Close']; v = df['Volume']
        
        # --- VERİ HAZIRLIĞI (SON 3 GÜN) ---
        # Şimdi iloc[-1] dediğinde her zaman hacmi olan EN SON GERÇEK günü alacak
        c1_o, c1_h, c1_l, c1_c = float(o.iloc[-1]), float(h.iloc[-1]), float(l.iloc[-1]), float(c.iloc[-1]) 
        c1_v = float(v.iloc[-1])
        c2_o, c2_h, c2_l, c2_c = float(o.iloc[-2]), float(h.iloc[-2]), float(l.iloc[-2]), float(c.iloc[-2]) # Dün
        c3_o, c3_h, c3_l, c3_c = float(o.iloc[-3]), float(h.iloc[-3]), float(l.iloc[-3]), float(c.iloc[-3]) # Önceki Gün
        
        c1_v = float(v.iloc[-1])
        avg_v = float(v.rolling(20).mean().iloc[-1]) 
        sma50 = c.rolling(50).mean().iloc[-1]
        # --- [YENİ] GELİŞMİŞ HACİM ANALİZİ DEĞİŞKENLERİ ---
        rvol = c1_v / avg_v if avg_v > 0 else 1.0
        
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
                if trend_dir == "YÜKSELİŞ": prefix = "🔥 Trend Yönünde "
                elif trend_dir == "DÜŞÜŞ": prefix = "⚠️ Tepki/Dönüş "
                if is_overbought: prefix += "(Riskli Tepe) "
            else: 
                if trend_dir == "DÜŞÜŞ": prefix = "📉 Trend Yönünde "
                elif trend_dir == "YÜKSELİŞ": prefix = "⚠️ Düzeltme/Dönüş "
                if is_oversold: prefix += "(Riskli Dip) "
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
            obv_data = {"title": "⚠️ GİZLİ ÇIKIŞ", "desc": "Fiyat çıkarken OBV düşüyor.", "color": "#dc2626"}
            
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
        rvol = c1_v / avg_v if avg_v > 0 else 1.0
        
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
        
        # Fiyat ile POC Yüzde farkı hesaplama
        fark_yuzde = abs((fiyat - poc_price) / poc_price) * 100
        
        # DELTA GÜCÜ (Baskınlık Yüzdesi) Hesaplama
        if toplam_hacim > 0:
            delta_gucu_yuzde = abs((delta_val / toplam_hacim) * 100)
        else:
            delta_gucu_yuzde = 0
        
        # Başlığı hazırlama
        if fiyat > poc_price:
            delta_title = "✅ Point of Control ÜZERİNDE"
            yon_metni = "üzerinde"
        else:
            delta_title = "⚠️ Point of Control ALTINDA"
            yon_metni = "altında"
            
        # Uyumsuzluk (Divergence) Kontrolü - Hacim Şiddeti Filtreli
        if fiyat > onceki_mum['Close'] and delta_val < 0:
            if delta_gucu_yuzde >= 60.0:
                delta_title += " (🚨 Gizli Satış)"
            elif delta_gucu_yuzde >= 55.0:
                delta_title += " (🟠 Zayıf Gizli Satış - Teyit Bekliyor)"
            else:
                delta_title += " (⚪ Fiyat/Hacim Gürültüsü - Dikkate Alma)"
                
        elif fiyat < onceki_mum['Close'] and delta_val > 0:
            if delta_gucu_yuzde >= 60.0:
                delta_title += " (🟢 Gizli Alım)"
            elif delta_gucu_yuzde >= 55.0:
                delta_title += " (🟠 Zayıf Gizli Alım - Teyit Bekliyor)"
            else:
                delta_title += " (⚪ Fiyat/Hacim Gürültüsü - Dikkate Alma)"
            
        # İstediğin formatta Edu-Note Açıklaması
        delta_desc = f"Fiyat son 20 mumun hacim merkezi (yani alıcı ve satıcıların en çok işlem yaptığı yer) olan <b>{poc_price:.2f}</b>, %{fark_yuzde:.2f} {yon_metni}."

        return {
            "candle": {"title": candle_title, "desc": candle_desc},
            "sfp": {"title": sfp_txt, "desc": sfp_desc},
            "vol": {"title": vol_txt, "desc": vol_desc},
            "loc": {"title": loc_txt, "desc": loc_desc},
            "sq": {"title": sq_txt, "desc": sq_desc},
            "obv": obv_data,
            "div": {"title": div_txt, "desc": div_desc, "type": div_type},
            "vwap": {"val": vwap_now, "diff": vwap_diff},
            "rs": {"alpha": alpha_val},
            "smart_volume": {
                "title": delta_title, 
                "desc": delta_desc, 
                "poc": poc_price, 
                "delta": delta_val, 
                "delta_yuzde": delta_gucu_yuzde,
                "rvol": round(rvol, 2),      
                "stopping": stop_vol_msg,    
                "climax": climax_msg         
            }
        }
    except Exception: return None

def render_golden_trio_banner(ict_data, sent_data):
    if not ict_data or not sent_data: return

    # --- 1. MANTIK KONTROLÜ ---
    # GÜÇ: Sentiment puanı 55 üstü veya 'Lider/Artıda' ibaresi var mı?
    rs_text = sent_data.get('rs', '').lower()
    cond_power = ("artıda" in rs_text or "lider" in rs_text or "pozitif" in rs_text or 
              sent_data.get('total', 0) >= 50 or sent_data.get('raw_rsi', 0) > 50)
    
    # KONUM: ICT analizinde 'Discount' bölgesinde mi?
    # Discount bölgesinde değilse bile, eğer dönüş sinyali (BOS/MSS) varsa konumu onayla
    cond_loc = "DISCOUNT" in ict_data.get('zone', '') or "MSS" in ict_data.get('structure', '') or "BOS" in ict_data.get('structure', '')
    
    # ENERJİ: ICT analizinde 'Güçlü' enerji var mı?
    # Displacement yoksa bile Hacim puanı iyiyse veya RSI ivmeliyse (55+) enerjiyi onayla
    cond_energy = ("Güçlü" in ict_data.get('displacement', '') or 
                "Hacim" in sent_data.get('vol', '') or 
                sent_data.get('raw_rsi', 0) > 55)

    # --- 2. FİLTRE (YA HEP YA HİÇ) ---
    # Eğer 3 şartın hepsi sağlanmıyorsa, fonksiyonu burada bitir (Ekrana hiçbir şey basma).
    if not (cond_power and cond_loc and cond_energy):
        return

    # --- 3. HTML ÇIKTISI (SADECE 3/3 İSE BURASI ÇALIŞIR) ---
    bg = "linear-gradient(90deg, #ca8a04 0%, #eab308 100%)" # Altın Sarısı
    border = "#a16207"
    txt = "#ffffff"
    
    st.markdown(f"""<div style="background:{bg}; border:1px solid {border}; border-radius:8px; padding:12px; margin-bottom:15px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
<div style="display:flex; justify-content:space-between; align-items:center;">
<div style="display:flex; align-items:center; gap:10px;">
<span style="font-size:1.6rem;">🏆</span>
<div style="line-height:1.2;">
<div style="font-weight:800; color:{txt}; font-size:1rem; letter-spacing:0.5px;">ALTIN FIRSAT (GOLDEN TRIO)</div>
<div style="font-size:0.75rem; color:{txt}; opacity:0.95;">RS Gücü + Ucuz Konum + Güçlü Enerji (ICT): Mükemmel Uyum.</div>
</div>
</div>
<div style="font-family:'JetBrains Mono'; font-weight:800; font-size:1.2rem; color:{txt}; background:rgba(255,255,255,0.25); padding:4px 10px; border-radius:6px;">3/3</div>
</div>
</div>""", unsafe_allow_html=True)

def render_royal_flush_live_banner(ticker, ict_data, sent_data):
    """Royal Flush (Elit): Tarama gerektirmeden canlı hesaplar. AF + SMA200 + SMA50 + RSI < 70."""
    try:
        df = get_safe_historical_data(ticker)
        if df is None or len(df) < 200: return
        c = df['Close']
        cp = float(c.iloc[-1])
        sma200 = float(c.rolling(200).mean().iloc[-1])
        sma50  = float(c.rolling(50).mean().iloc[-1])
        delta  = c.diff()
        gain   = delta.where(delta > 0, 0).rolling(14).mean()
        loss   = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi    = float(100 - (100 / (1 + gain / loss)).iloc[-1])
        # Kriter 1-3: SMA200 üstü + SMA50 üstü + RSI güvenli
        if not (cp > sma200 and cp > sma50 and rsi < 70):
            return
        # Kriter 4: Altın Fırsat da sağlanıyor mu?
        if ict_data and sent_data:
            rs_text = sent_data.get('rs', '').lower()
            cond_power  = ("artıda" in rs_text or "lider" in rs_text or "pozitif" in rs_text or
                           sent_data.get('total', 0) >= 50 or sent_data.get('raw_rsi', 0) > 50)
            cond_loc    = ("DISCOUNT" in ict_data.get('zone', '') or
                           "MSS" in ict_data.get('structure', '') or
                           "BOS" in ict_data.get('structure', ''))
            cond_energy = ("Güçlü" in ict_data.get('displacement', '') or
                           "Hacim" in sent_data.get('vol', '') or
                           sent_data.get('raw_rsi', 0) > 55)
            if not (cond_power and cond_loc and cond_energy):
                return
        st.markdown("""<div style="background:linear-gradient(90deg, #1e3a8a 0%, #3b82f6 100%); border:1px solid #1e40af; border-radius:8px; padding:12px; margin-top:5px; margin-bottom:15px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2);">
<div style="display:flex; justify-content:space-between; align-items:center;">
<div style="display:flex; align-items:center; gap:10px;">
<span style="font-size:1.6rem;">♠️</span>
<div style="line-height:1.2;">
<div style="font-weight:800; color:#ffffff; font-size:1rem; letter-spacing:0.5px;">ROYAL FLUSH (ELİT)</div>
<div style="font-size:0.75rem; color:#ffffff; opacity:0.95;">Uzun Vade Trend + Yapı Sağlam + Endeksten Güçlü + Aşırı Pahalı Değil.</div>
</div>
</div>
<div style="font-family:'JetBrains Mono'; font-weight:800; font-size:1.2rem; color:#ffffff; background:rgba(255,255,255,0.25); padding:4px 10px; border-radius:6px;">4/4</div>
</div>
</div>""", unsafe_allow_html=True)
    except: pass

def render_royal_flush_3_0_banner(ticker):
    """
    BİREYSEL HİSSE ANALİZİ (TARAMA SONUÇLARI PANELİ İÇİN)
    Kullanıcı hisse seçtiğinde, 6 katı Royal Flush 3.0 kriterini sadece bu hisse için hesaplar.
    Başarılıysa, hisse sayfasının sağ üstünde kan kırmızısı bir pano oluşturur.
    """
    try:
        # İhtiyacımız olan 1 yıllık günlük veriyi çekiyoruz (app.py'deki mevcut fonksiyon)
        df = get_safe_historical_data(ticker, period="1y")
        if df is None or df.empty: return
        
        # 6 zorlu kriteri hesaplayan algoritmayı çağır (Önceki adımda eklemiştik)
        res = calculate_royal_flush_3_0_setup(ticker, df)
        if not res: 
            return # Hisse 6 şartı sağlamıyorsa sessizce dön, ekranı boşuna işgal etme
        
        # --- HTML ÇIKTISI (KAN KIRMIZISI PANO) ---
        bg = "linear-gradient(90deg, #7f1d1d 0%, #dc2626 100%)" 
        border = "#991b1b"
        
        st.markdown(f'''
        <div style="background:{bg}; border:2px solid {border}; border-radius:8px; padding:15px; margin-top:10px; margin-bottom:10px; box-shadow:0 4px 6px rgba(0,0,0,0.1);">
            <h3 style="color:#ffffff; margin:0; font-size:1.4rem; font-weight:800; display:flex; align-items:center;">
                <span style="font-size:2rem; margin-right:10px;">🩸</span> ROYAL FLUSH 3.0 (KUSURSUZ DİPTEN DÖNÜŞ)
            </h3>
            <p style="color:#fca5a5; font-size:0.9rem; margin-top:5px; margin-bottom:10px; font-weight:600;">Dikkat! Bu hisse "6 Katı Kurumsal Kriteri" de başarıyla geçti!</p>
            <div style="display:flex; flex-wrap:wrap; gap:10px; margin-top:10px;">
                <span style="background:rgba(255,255,255,0.15); padding:4px 8px; border-radius:4px; font-size:0.85rem; color:white; font-weight:700;">🟢 Z-Score: {res['Z-Score']} (Aşırı Satım)</span>
                <span style="background:rgba(255,255,255,0.15); padding:4px 8px; border-radius:4px; font-size:0.85rem; color:white; font-weight:700;">💸 OBV Hacmi: Gizli Para Girişi</span>
                <span style="background:rgba(255,255,255,0.15); padding:4px 8px; border-radius:4px; font-size:0.85rem; color:white; font-weight:700;">📉 Hacim: Sakin (Tahta Kurumuş)</span>
                <span style="background:rgba(255,255,255,0.15); padding:4px 8px; border-radius:4px; font-size:0.85rem; color:white; font-weight:700;">🎯 FVG Teması: Kurumsal İndirim</span>
                <span style="background:rgba(255,255,255,0.15); padding:4px 8px; border-radius:4px; font-size:0.85rem; color:white; font-weight:700;">🛡️ Trap Skoru: {res['Trap Skoru']} (Risk Yok)</span>
                <span style="background:rgba(255,255,255,0.15); padding:4px 8px; border-radius:4px; font-size:0.85rem; color:white; font-weight:700;">🤖 AI Güven: {res['Güven']}</span>
            </div>
        </div>
        ''', unsafe_allow_html=True)
    except Exception as e:
        pass # Panel çökerse programı durdurmamak için sessizce geç

# --- ROYAL FLUSH HESAPLAYICI ---
def render_royal_flush_banner(ict_data, sent_data, ticker):
    if not ict_data or not sent_data: return

    # --- KRİTER 1: YAPI (ICT) ---
    # BOS veya MSS (Bullish) olmalı
    cond_struct = "BOS (Yükseliş" in ict_data.get('structure', '') or "MSS (Market Structure Shift) 🐂" in ict_data.get('structure', '')
    
    # --- KRİTER 2: ZEKA (LORENTZIAN AI) ---
    # 7/8 veya 8/8 Yükseliş olmalı
    lor_data = calculate_lorentzian_classification(ticker)
    cond_ai = False
    votes_txt = "0/8"
    if lor_data and lor_data['signal'] == "YÜKSELİŞ" and lor_data['votes'] >= 7:
        cond_ai = True
        votes_txt = f"{lor_data['votes']}/8"

    # --- KRİTER 3: GÜÇ (RS MOMENTUM) ---
    # Alpha pozitif olmalı
    alpha_val = 0
    pa_data = calculate_price_action_dna(ticker)
    if pa_data:
        alpha_val = pa_data.get('rs', {}).get('alpha', 0)
    cond_rs = alpha_val > 0

    # --- KRİTER 4: MALİYET (VWAP) ---
    # Ralli modu veya Ucuz olmalı (Parabolik olmamalı)
    v_diff = pa_data.get('vwap', {}).get('diff', 0) if pa_data else 0
    cond_vwap = v_diff < 12 # %12'den fazla sapmamış (Aşırı şişmemiş) olmalı

    # --- FİLTRE (YA HEP YA HİÇ - 4/4) ---
    if not (cond_struct and cond_ai and cond_rs and cond_vwap):
        return

    # --- HTML ÇIKTISI ---
    bg = "linear-gradient(90deg, #1e3a8a 0%, #3b82f6 100%)" # Kraliyet Mavisi
    border = "#1e40af"
    txt = "#ffffff"
    
    st.markdown(f"""<div style="background:{bg}; border:1px solid {border}; border-radius:8px; padding:12px; margin-top:5px; margin-bottom:15px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);">
<div style="display:flex; justify-content:space-between; align-items:center;">
<div style="display:flex; align-items:center; gap:10px;">
<span style="font-size:1.6rem;">♠️</span>
<div style="line-height:1.2;">
<div style="font-weight:800; color:{txt}; font-size:1rem; letter-spacing:0.5px;">ROYAL FLUSH (KRALİYET SET-UP)</div>
<div style="font-size:0.75rem; color:{txt}; opacity:0.95;">AI ({votes_txt}) + ICT Yapı + RS Liderliği + VWAP Uyumu: En Yüksek Olasılık.</div>
</div>
</div>
<div style="font-family:'JetBrains Mono'; font-weight:800; font-size:1.2rem; color:{txt}; background:rgba(255,255,255,0.25); padding:4px 10px; border-radius:6px;">4/4</div>
</div>
</div>""", unsafe_allow_html=True)

# --- SUPERTREND VE FIBONACCI HESAPLAYICI ---
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
    try:
        if len(df) < period: return 0
        
        # Son 20 barı al
        recent = df.tail(period)
        
        # Ortalama ve Standart Sapma
        mean = recent['Close'].mean()
        std = recent['Close'].std()
        
        if std == 0: return 0
        
        # Son fiyat
        last_close = df['Close'].iloc[-1]
        
        # Z-Score Formülü
        z_score = (last_close - mean) / std
        
        return z_score
    except:
        return 0

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
# ==============================================================================
# 🧠 GRANDMASTER MATRİSİ V8.0 (MERCAN KORUMALI & 60 GÜN HAFIZALI)
# ==============================================================================
def calculate_grandmaster_score_single(symbol, df, bench_series, fast_mode=False):
    """
    V8.0: Patron'un 'Mercan' tespiti üzerine revize edildi.
    1. ICT Referansı: Son 252 Gün (Yıllık) zirve/dip baz alınır.
    2. Tazelik Testi: Son 60 Gün (3 Ay) içinde Discount gören hisseye ceza kesilmez.
    """
    try:
        if df is None or len(df) < 100: return None
        
        # Son veriler
        close = df['Close']
        curr_price = float(close.iloc[-1])
        curr_vol = float(df['Volume'].iloc[-1])
        avg_vol = float(df['Volume'].rolling(20).mean().iloc[-1])
        vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 0
        
        z_score = calculate_z_score_live(df)
        is_squeezed = check_lazybear_squeeze(df)
        
        # --- PUANLAMA MOTORU ---
        raw_score = 0
        story_tags = []
        penalty_log = []
        ai_power = 0
        alpha_val = 0.0  # Güvenli varsayılan — bench_series yoksa 0 kalır

        # 1. LORENTZIAN (AI)
        if not fast_mode:
            try:
                lor_data = calculate_lorentzian_classification(symbol) 
                if lor_data:
                    votes = lor_data['votes']
                    signal = lor_data['signal']
                    if signal == "YÜKSELİŞ":
                        if votes == 8: 
                            raw_score += 40
                            ai_power = 2
                            story_tags.append("🧠 Lorentzian: 8/8")
                        elif votes >= 7: 
                            raw_score += 30
                            ai_power = 1
                            story_tags.append("🧠 Lorentzian: 7/8")
                    elif signal == "DÜŞÜŞ":
                        raw_score = -999 
            except: pass

        # 2. HACİM
        if vol_ratio >= 2.5: 
            raw_score += 20
            story_tags.append("⛽ Hacim Artışı 2.5x")
        elif vol_ratio >= 1.5: 
            raw_score += 10
            story_tags.append("⛽ Hacim Artışı 1.5x")
        
        # 3. SIKIŞMA
        if is_squeezed:
            raw_score += 15
            story_tags.append("📐 Sıkışma Halinde")

        # 4. ICT KONUMU (MACRO ANALİZ & 60 GÜN HAFIZA)
        # ---------------------------------------------------------
        # A. Yıllık Range Hesabı (Macro Bakış)
        lookback_period = min(252, len(df))
        macro_high = df['High'].tail(lookback_period).max()
        macro_low = df['Low'].tail(lookback_period).min()
        
        if macro_high > macro_low:
            range_diff = macro_high - macro_low
            fib_50 = macro_low + (range_diff * 0.5)
            fib_premium_start = macro_low + (range_diff * 0.75) # Çok pahalı bölge
            
            # B. Mevcut Konum
            is_currently_premium = curr_price > fib_50
            
            # C. 60 Günlük Hafıza Testi (Patron Kuralı)
            # Son 60 gün içinde fiyatın %50 seviyesinin altına inip inmediğine bakıyoruz.
            recent_lookback = min(60, len(df))
            recent_lows = df['Low'].tail(recent_lookback)
            was_recently_discount = (recent_lows < fib_50).any()
            
            # --- KARAR MEKANİZMASI ---
            if not is_currently_premium:
                # Şu an zaten ucuzsa (Discount) ödül ver
                raw_score += 15
                story_tags.append("🦅 ICT: Ucuzluk Bölgesinde")
            
            else:
                # Şu an PAHALI (Premium) görünüyor.
                # Ama geçmiş 60 günde ucuzladıysa veya AI çok güçlüyse CEZA KESME.
                if was_recently_discount or ai_power > 0:
                    # Ceza yok. Bu hareket 'Mal Toplama' sonrası kırılımdır.
                    pass
                else:
                    # Hem pahalı, hem son 3 aydır hiç ucuzlamamış, hem AI zayıf.
                    # İşte bu gerçek pahalıdır. Vur kırbacı.
                    raw_score -= 25
                    penalty_log.append("ICT:Premium(Şişkin)")
        # ---------------------------------------------------------

        # 5. TEKNİK & LİDERLİK
        if -2.5 <= z_score <= -1.5:
            raw_score += 10
            story_tags.append(f"💎 Dip (Z:{z_score:.2f})")
        
        # Alpha Hesabı
        if bench_series is not None:
             try:
                stock_5d = (close.iloc[-1] / close.iloc[-6]) - 1
                common_idx = close.index.intersection(bench_series.index)
                if len(common_idx) > 5:
                    b_aligned = bench_series.loc[common_idx]
                    bench_5d = (b_aligned.iloc[-1] / b_aligned.iloc[-6]) - 1
                    alpha_val = (stock_5d - bench_5d) * 100
                    if alpha_val > 3.0:
                        raw_score += 5
                        story_tags.append(f"🚀 Alpha Lideri")
             except: pass

        # --- EMNİYET SİBOBU ---
        if z_score > 3.0: 
            raw_score = -100 
            penalty_log.append("Aşırı Şişkin")

        # RSI Kontrolü
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain/loss))).iloc[-1]
        
        if rsi > 85: # Toleransı biraz artırdım (80->85) çünkü boğa piyasası
            raw_score -= 10
            penalty_log.append("RSI>85")
            
        story_text = " | ".join(story_tags[:3]) if story_tags else "İzleme Listesi"

        return {
            "Sembol": symbol,
            "Skor": int(raw_score),
            "Fiyat": curr_price,
            "Hacim_Kat": round(vol_ratio, 1),
            "Z_Score": round(z_score, 2),
            "Hikaye": story_text,
            "RS Gücü": round(alpha_val, 1),
            "Uyarılar": ", ".join(penalty_log) if penalty_log else "Temiz"
        }

    except Exception: return None

@st.cache_data(ttl=900)
def scan_grandmaster_batch(asset_list):
    """
    GRANDMASTER TARAMA MOTORU (V6):
    - 40 Puan altı hisseler kesinlikle listeye giremez.
    """
    # 1. TOPLU VERİ ÇEK (Hızlı)
    data = get_batch_data_cached(asset_list, period="1y") 
    if data.empty: return pd.DataFrame()
    
    cat = st.session_state.get('category', 'S&P 500')
    bench_ticker = "XU100.IS" if "BIST" in cat else "^GSPC"
    bench_df = get_safe_historical_data(bench_ticker, period="1y")
    bench_series = bench_df['Close'] if bench_df is not None else None

    candidates = []
    stock_dfs = []
    
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]: stock_dfs.append((symbol, data[symbol]))
            else:
                if len(asset_list) == 1: stock_dfs.append((symbol, data))
        except: continue

    # --- AŞAMA 1: HIZLI ÖN ELEME ---
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(calculate_grandmaster_score_single, sym, df, bench_series, True) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            # Baraj: Ön elemede 15 puanı geçmeli
            if res and res['Skor'] >= 15: 
                candidates.append(res['Sembol'])

    # --- AŞAMA 2: DERİN ANALİZ (Lorentzian Devrede, Paralel) ---
    final_results = []
    df_dict = {sym: df for sym, df in stock_dfs}
    candidate_dfs = [(sym, df_dict[sym]) for sym in candidates if sym in df_dict]

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(calculate_grandmaster_score_single, sym, df, bench_series, False) for sym, df in candidate_dfs]
        for future in concurrent.futures.as_completed(futures):
            final_res = future.result()
            # FİNAL BARAJI: Patron Emri -> 40 Puanın altı listeye giremez.
            if final_res and final_res['Skor'] >= 40:
                final_results.append(final_res)
    
    if final_results:
        df_final = pd.DataFrame(final_results)
        return df_final.sort_values(by="Skor", ascending=False).head(10)
        
    return pd.DataFrame()

   
# ==============================================================================
# 4. GÖRSELLEŞTİRME FONKSİYONLARI (EKSİK OLAN KISIM)
# ==============================================================================
def render_gauge_chart(score):
    score = int(score)
    if st.session_state.dark_mode:
        color = "#ef4444" 
        if score >= 50: color = "#f59e0b" 
        if score >= 70: color = "#10b981" 
        if score >= 85: color = "#059669" 
        
        source = pd.DataFrame({"category": ["Skor", "Kalan"], "value": [score, 100-score]})
        base = alt.Chart(source).encode(theta=alt.Theta("value", stack=True))
        pie = base.mark_arc(outerRadius=55, innerRadius=40).encode(
            color=alt.Color("category", scale=alt.Scale(domain=["Skor", "Kalan"], range=[color, "rgba(255,255,255,0.05)"]), legend=None),
            order=alt.Order("category", sort="descending"), tooltip=["value"]
        )
        text = base.mark_text(radius=0, size=28, color=color, fontWeight="bold", dy=-5).encode(text=alt.value(f"{score}"))
        label = base.mark_text(radius=0, size=11, color="#38bdf8", fontWeight="bold", dy=20).encode(text=alt.value("GENEL SAĞLIK"))
        
        chart = (pie + text + label).properties(height=130).configure(background='transparent').configure_view(strokeWidth=0) 
        st.altair_chart(chart, use_container_width=True, theme=None)
    else:
        color = "#b91c1c" 
        if score >= 50: color = "#d97706" 
        if score >= 70: color = "#16a34a" 
        if score >= 85: color = "#15803d" 
        
        source = pd.DataFrame({"category": ["Skor", "Kalan"], "value": [score, 100-score]})
        base = alt.Chart(source).encode(theta=alt.Theta("value", stack=True))
        pie = base.mark_arc(outerRadius=55, innerRadius=40).encode(
            color=alt.Color("category", scale=alt.Scale(domain=["Skor", "Kalan"], range=[color, "#e2e8f0"]), legend=None),
            order=alt.Order("category", sort="descending"), tooltip=["value"]
        )
        text = base.mark_text(radius=0, size=28, color=color, fontWeight="bold", dy=-5).encode(text=alt.value(f"{score}"))
        label = base.mark_text(radius=0, size=11, color="#1e3a8a", fontWeight="bold", dy=20).encode(text=alt.value("GENEL SAĞLIK"))
        
        chart = (pie + text + label).properties(height=130) 
        st.altair_chart(chart, use_container_width=True)

def render_sentiment_card(sent):
    if not sent: return
    display_ticker = st.session_state.ticker.replace(".IS", "").replace("=F", "")
    
    score = sent['total']
    # Renk ve İkon Belirleme
    if score >= 70: 
        color = "#16a34a"; icon = "🔥"; status = "GÜÇLÜ BOĞA"; bg_tone = "#f0fdf4"; border_tone = "#bbf7d0"
    elif score >= 50: 
        color = "#d97706"; icon = "↔️"; status = "NÖTR / POZİTİF"; bg_tone = "#fffbeb"; border_tone = "#fde68a"
    elif score >= 30: 
        color = "#b91c1c"; icon = "🐻"; status = "ZAYIF / AYI"; bg_tone = "#fef2f2"; border_tone = "#fecaca"
    else: 
        color = "#7f1d1d"; icon = "❄️"; status = "ÇÖKÜŞ"; bg_tone = "#fef2f2"; border_tone = "#fecaca"
    
    # Etiketler
    p_label = '25p' if sent.get('is_index', False) else '20p'
    rs_label = 'Devre Dışı' if sent.get('is_index', False) else '15p'

    # --- KART OLUŞTURUCU (SOLA YASLI - HATA VERMEZ) ---
    def make_card(num, title, score_lbl, val, desc, emo):
        # DİKKAT: Aşağıdaki HTML kodları bilerek en sola yaslanmıştır.
        return f"""<div style="border: 1px solid #e2e8f0; border-radius: 8px; margin-bottom: 8px; background: white; box-shadow: 0 1px 2px rgba(0,0,0,0.02);">
<div style="background: #f8fafc; padding: 8px 12px; border-bottom: 1px solid #e2e8f0; display: flex; justify-content: space-between; align-items: center;">
<div style="display:flex; align-items:center; gap:6px;">
<span style="background:{color}; color:white; width:20px; height:20px; border-radius:50%; display:flex; justify-content:center; align-items:center; font-size:0.7rem; font-weight:bold;">{num}</span>
<span style="font-weight: 700; color: #334155; font-size: 0.8rem;">{title} <span style="color:#94a3b8; font-weight:400; font-size:0.7rem;">({score_lbl})</span></span>
</div>
<div style="font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; font-weight: 700; color: #0f172a;">{val}</div>
</div>
<div style="padding: 10px; font-size: 0.85rem; color: #1e3a8a; line-height: 1.4; background: #ffffff;">
<span style="color:{color}; font-size:1rem; float:left; margin-right:6px; line-height:1;">{emo}</span>
{desc}
</div>
</div>"""

    # --- KARTLARI OLUŞTUR ---
    cards_html = ""
    cards_html += make_card("1", "YAPI", p_label, sent['str'], "Market Yapısı- Son 20 günün %97-100 zirvesinde (12). Son 5 günün en düşük seviyesi, önceki 20 günün en düşük seviyesinden yukarıdaysa: HL (8)", "🏗️")
    cards_html += make_card("2", "TREND", p_label, sent['tr'], "Ortalamalara bakar. Hisse fiyatı SMA200 üstünde (8). EMA20 üstünde (8). Kısa vadeli ortalama, orta vadeli ortalamanın üzerinde, yani EMA20 > SMA50 (4)", "📈")
    cards_html += make_card("3", "HACİM", p_label, sent['vol'], "Hacmin 20G ortalamaya oranını ve On-Balance Volume (OBV) denetler. Bugünün hacmi son 20G ort.üstünde (12) Para girişi var: 10G ortalamanın üstünde (8)", "🌊")
    cards_html += make_card("4", "MOMENTUM", "15p", sent['mom'], "RSI ve MACD ile itki gücünü ölçer. 50 üstü RSI (5) RSI ivmesi artıyor (5). MACD sinyal çizgisi üstünde (5)", "🚀")
    cards_html += make_card("5", "SIKIŞMA", "10p", sent['vola'], "Bollinger Bant genişliğini inceler. Bant genişliği son 20G ortalamasından dar (10)", "📐")
    cards_html += make_card("6", "GÜÇ", rs_label, sent['rs'], "Hissenin Endekse göre relatif gücünü (RS) ölçer. Mansfield RS göstergesi 0'ın üzerinde (5). RS trendi son 5 güne göre yükselişte (5). Endeks düşerken hisse artıda (Alpha) (5)", "💪")

    # --- ANA HTML (SOLA YASLI) ---
    final_html = f"""<div class="info-card" style="border-top: 3px solid {color}; background-color: #f8fafc; padding-bottom: 2px;">
<div class="info-header" style="color:#1e3a8a; display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
<span>💼 Kurumsal Para İştahı: {display_ticker}</span>
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
    
    display_ticker = st.session_state.ticker.replace(".IS", "").replace("=F", "")
    
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

    display_ticker = ticker.replace(".IS", "").replace("=F", "")
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
    if r1_score < 2: r1_suffix = " <span style='color:#dc2626; font-weight:500; background:#fef2f2; padding:1px 4px; border-radius:3px; margin-left:5px; font-size:0.7rem;'>(⛔ RİSKLİ)</span>"
    elif r1_score > 5: r1_suffix = " <span style='color:#16a34a; font-weight:500; background:#f0fdf4; padding:1px 4px; border-radius:3px; margin-left:5px; font-size:0.7rem;'>(🚀 GÜÇLÜ)</span>"

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
        <div style="display:flex; justify-content:space-between; margin-bottom:8px; border-bottom:1px solid #e5e7eb; padding-bottom:4px;">
            <div style="font-size:0.8rem; font-weight:700; color:#1e40af;">Fiyat: {price_val}</div>
            <div style="font-size:0.75rem; color:#64748B;">{ma_vals}</div>
        </div>
        <div style="font-size:0.8rem; color:#991b1b; margin-bottom:8px;">🛑 Stop: {stop_vals}</div>
        <div style="background:#f0f9ff; padding:4px; border-radius:4px; margin-bottom:4px;">
            <div style="font-weight:700; color:#0369a1; font-size:0.75rem; margin-bottom:4px;">🧠 RADAR 1 (3-12 gün): Momentum ve Hacim - SKOR: {r1_score}/7{r1_suffix}</div>
            <div class="tech-grid" style="font-size:0.75rem;">{r1_html}</div>
        </div>
        <div style="background:#f0fdf4; padding:4px; border-radius:4px;">
            <div style="font-weight:700; color:#15803d; font-size:0.75rem; margin-bottom:4px;">🚀 RADAR 2 (10-50 gün): Trend Takibi - SKOR: {r2_score}/7</div>
            <div class="tech-grid" style="font-size:0.75rem;">{r2_html}</div>
        </div>
    </div>
    """
    st.markdown(full_html.replace("\n", " "), unsafe_allow_html=True)

def render_synthetic_sentiment_panel(data):
    if data is None or data.empty: return
    display_ticker = st.session_state.ticker.replace(".IS", "").replace("=F", "")
    
    info = fetch_stock_info(st.session_state.ticker)
    current_price = info.get('price', 0) if info else 0
    
    if st.session_state.dark_mode:
        header_color = "#38bdf8"
        st.markdown(f"""
        <div class="info-card" style="border-top: 3px solid {header_color}; margin-bottom:15px; background: rgba(17, 24, 39, 0.8); border: 1px solid #1f2937;">
            <div class="info-header" style="color:#38bdf8; display:flex; justify-content:space-between; align-items:center; border-bottom: none;">
                <span style="font-size:1.1rem;">🌊 Para Akış İvmesi & Fiyat Dengesi: {display_ticker}</span>
                <span style="font-family:'JetBrains Mono'; font-weight:700; color:#10b981; background:#0b0f19; padding:2px 8px; border-radius:4px; font-size:1.25rem; border: 1px solid #1f2937;">
                    {current_price:.2f}
                </span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        c1, c2 = st.columns([1, 1]); x_axis = alt.X('Date_Str', axis=alt.Axis(title=None, labelAngle=-45, labelOverlap=False), sort=None)
        with c1:
            base = alt.Chart(data).encode(x=x_axis)
            color_condition = alt.condition(alt.datum.MF_Smooth > 0, alt.value("#38bdf8"), alt.value("#ef4444"))
            bars = base.mark_bar(size=12, opacity=0.9).encode(
                y=alt.Y('MF_Smooth:Q', axis=alt.Axis(title='Para Akışı (Güç)', labels=False, titleColor='#94a3b8')), 
                color=color_condition, tooltip=['Date_Str', 'Price', 'MF_Smooth']
            )
            price_line = base.mark_line(color='#10b981', strokeWidth=2).encode(y=alt.Y('Price:Q', scale=alt.Scale(zero=False), axis=alt.Axis(title='Fiyat', titleColor='#94a3b8', labelColor='#94a3b8')))
            chart1 = alt.layer(bars, price_line).resolve_scale(y='independent').properties(height=280, title=alt.TitleParams("Momentum", fontSize=14, color="#38bdf8")).configure(background='transparent').configure_axis(gridColor='#1f2937', domainColor='#1f2937', labelColor='#94a3b8', titleColor='#94a3b8').configure_view(strokeWidth=0)
            st.altair_chart(chart1, use_container_width=True, theme=None)
        with c2:
            base2 = alt.Chart(data).encode(x=x_axis)
            line_stp = base2.mark_line(color='#f59e0b', strokeWidth=3).encode(y=alt.Y('STP:Q', scale=alt.Scale(zero=False), axis=alt.Axis(title='Fiyat', titleColor='#94a3b8', labelColor='#94a3b8')), tooltip=['Date_Str', 'STP', 'Price'])
            line_price = base2.mark_line(color='#38bdf8', strokeWidth=2).encode(y='Price:Q')
            area = base2.mark_area(opacity=0.1, color='#38bdf8').encode(y='STP:Q', y2='Price:Q')
            chart2 = alt.layer(area, line_stp, line_price).properties(height=280, title=alt.TitleParams("Sentiment Analizi: Mavi (Fiyat) Sarıyı (STP-DEMA6) Yukarı Keserse AL, aşağıya keserse SAT", fontSize=12, color="#38bdf8")).configure(background='transparent').configure_axis(gridColor='#1f2937', domainColor='#1f2937', labelColor='#94a3b8', titleColor='#94a3b8').configure_view(strokeWidth=0)
            st.altair_chart(chart2, use_container_width=True, theme=None)
    else:
        header_color = "#3b82f6" 
        st.markdown(f"""
        <div class="info-card" style="border-top: 3px solid {header_color}; margin-bottom:15px;">
            <div class="info-header" style="color:#1e3a8a; display:flex; justify-content:space-between; align-items:center;">
                <span style="font-size:1.1rem;">🌊 Para Akış İvmesi & Fiyat Dengesi: {display_ticker}</span>
                <span style="font-family:'JetBrains Mono'; font-weight:700; color:#0f172a; background:#eff6ff; padding:2px 8px; border-radius:4px; font-size:1.25rem;">
                    {current_price:.2f}
                </span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        c1, c2 = st.columns([1, 1]); x_axis = alt.X('Date_Str', axis=alt.Axis(title=None, labelAngle=-45, labelOverlap=False, labelColor="#062C63"), sort=None)
        with c1:
            base = alt.Chart(data).encode(x=x_axis)
            color_condition = alt.condition(
                alt.datum.MF_Smooth > 0,
                alt.value("#5B84C4"), 
                alt.value("#ef4444")
            )
            bars = base.mark_bar(size=12, opacity=0.9).encode(
                y=alt.Y('MF_Smooth:Q', axis=alt.Axis(title='Para Akışı (Güç)', labels=False, titleColor='#4338ca')), 
                color=color_condition, 
                tooltip=['Date_Str', 'Price', 'MF_Smooth']
            )
            price_line = base.mark_line(color='#1e40af', strokeWidth=2).encode(y=alt.Y('Price:Q', scale=alt.Scale(zero=False), axis=alt.Axis(title='Fiyat', titleColor='#0f172a')))
            st.altair_chart(alt.layer(bars, price_line).resolve_scale(y='independent').properties(height=280, title=alt.TitleParams("Momentum", fontSize=14, color="#1e40af")), use_container_width=True)
        with c2:
            base2 = alt.Chart(data).encode(x=x_axis)
            line_stp = base2.mark_line(color='#fbbf24', strokeWidth=3).encode(y=alt.Y('STP:Q', scale=alt.Scale(zero=False), axis=alt.Axis(title='Fiyat', titleColor='#64748B')), tooltip=['Date_Str', 'STP', 'Price'])
            line_price = base2.mark_line(color='#2563EB', strokeWidth=2).encode(y='Price:Q')
            area = base2.mark_area(opacity=0.15, color='gray').encode(y='STP:Q', y2='Price:Q')
            st.altair_chart(alt.layer(area, line_stp, line_price).properties(height=280, title=alt.TitleParams("Sentiment Analizi: Mavi (Fiyat) Sarıyı (STP-DEMA6) Yukarı Keserse AL, aşağıya keserse SAT", fontSize=14, color="#1e40af")), use_container_width=True)

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
            pattern_name = pat_df.iloc[0]['Formasyon']
            pattern_desc = pat_df.iloc[0]['Detay']
            pattern_score = pat_df.iloc[0]['Skor']
        else:
            pattern_name = ""
            pattern_desc = ""
            pattern_score = 0
    except:
        pattern_name = ""
        pattern_desc = ""
        pattern_score = 0

    # Mevcut mum başlığını ve açıklamasını alıyoruz
    pa_candle_title = pa['candle']['title']
    pa_candle_desc = pa['candle']['desc']
    
    # Eğer formasyon bulunduysa, mevcut mum verisinin yanına ekliyoruz
    if pattern_name:
        pa_candle_title = f"{pa_candle_title} | 📐 {pattern_name} (Skor: {pattern_score})"
        pa_candle_desc = f"{pa_candle_desc}<br><br><div style='background:rgba(56,189,248,0.08); border-left:3px solid #38bdf8; border-radius:0 4px 4px 0; padding:6px 10px; margin-top:4px;'><span style='color:#38bdf8; font-weight:800; font-size:0.85rem;'>📐 {pattern_name}</span><br><span style='color:#94a3b8; font-size:0.78rem;'>{pattern_desc}</span></div>"
    # =========================================================
    display_ticker = ticker.replace(".IS", "").replace("=F", "")
    div_data = pa.get('div', {'type': 'neutral', 'title': '-', 'desc': '-'})
    vwap_data = pa.get('vwap', {'val': 0, 'diff': 0})
    rs_data = pa.get('rs', {'alpha': 0})
    v_diff = vwap_data['diff']
    alpha = rs_data['alpha']

    # --- ORİJİNAL MANTIK VE METİNLER (DOKUNULMADI) ---
    sd_txt = "Taze bölge (RBR/DBD vb.) görünmüyor."
    if sd_data:
        sd_txt = f"{sd_data['Type']} | {sd_data['Bottom']:.2f} - {sd_data['Top']:.2f} ({sd_data['Status']} olabilir)"

    if v_diff < -2.0:
        vwap_txt = "🟢 DİP FIRSATI (Aşırı İskonto)"
        vwap_desc = f"Fiyat maliyetin %{abs(v_diff):.1f} altında. Tepki ihtimali yüksek."
    elif v_diff < 0.0:
        vwap_txt = "🟢 UCUZ (Toplama)"
        vwap_desc = "Fiyat kurumsal maliyetin hemen altında."
    elif v_diff < 8.0:
        vwap_txt = "🚀 RALLİ MODU (Güçlü Trend)"
        vwap_desc = f"Fiyat maliyetin %{v_diff:.1f} üzerinde. Momentum arkanda."
    elif v_diff < 15.0:
        vwap_txt = "🟠 DİKKAT (Piyasa Isınıyor)"
        vwap_desc = f"Fiyat ortalamadan %{v_diff:.1f} uzaklaştı. Stop seviyesi yükseltilse iyi olur."
    else:
        vwap_txt = "🔴 PARABOLİK (Aşırı Kopuş)"
        vwap_desc = f"Fiyat %{v_diff:.1f} saptı. Bu sürdürülemez, kâr almak düşünülebilir."

    if alpha > 1.0:
        rs_txt = "🦁 LİDER (Endeksi Yeniyor)"
        rs_desc = f"Endekse göre %{alpha:.1f} daha güçlü (Alpha Pozitif)."
    elif alpha < -1.0:
        rs_txt = "🐢 ZAYIF (Endeksin Gerisinde)"
        rs_desc = f"Piyasa giderken gitmiyor (Fark %{alpha:.1f})."
    else:
        rs_txt = "🔗 NÖTR (Endeks ile Aynı)"
        rs_desc = "Piyasa rüzgarıyla paralel hareket ediyor."

    if st.session_state.dark_mode:
        sd_col = "#10b981" if sd_data and "Talep" in sd_data['Type'] else "#ef4444" if sd_data else "#94a3b8"
        sfp_color = "#10b981" if "Bullish" in pa['sfp']['title'] else "#ef4444" if "Bearish" in pa['sfp']['title'] else "#94a3b8"
        sq_color = "#f59e0b" if "BOBİN" in pa['sq']['title'] else "#94a3b8"
        
        if div_data['type'] == 'bearish': div_style = "background:rgba(239, 68, 68, 0.1); border-left:3px solid #ef4444; color:#fca5a5;"
        elif div_data['type'] == 'bullish': div_style = "background:rgba(16, 185, 129, 0.1); border-left:3px solid #10b981; color:#6ee7b7;"
        else: div_style = "color:#94a3b8;"

        vwap_col = "#10b981" if v_diff < -2.0 else "#34d399" if v_diff < 0.0 else "#38bdf8" if v_diff < 8.0 else "#f59e0b" if v_diff < 15.0 else "#ef4444"
        rs_col = "#10b981" if alpha > 1.0 else "#ef4444" if alpha < -1.0 else "#94a3b8"

        html_content = f"""
        <div class="info-card" style="border-top: 3px solid #6366f1; background: rgba(17, 24, 39, 0.6); border-radius: 8px; border: 1px solid rgba(255,255,255,0.05); padding: 12px;">
            <div class="info-header" style="color:#818cf8; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 8px; margin-bottom: 12px; font-weight: 800;">🕯️ Price Action Analizi: {display_ticker}</div>

            <div style="margin-bottom:8px;">
                <div style="font-weight:700; font-size:0.85rem; color:#e2e8f0; margin-bottom: 2px;">1. MUM & FORMASYONLAR: <span style="color:#38bdf8;">{pa_candle_title}</span></div>
                <div class="edu-note" style="color:#94a3b8;">{pa_candle_desc}</div>
            </div>

            <div style="margin-bottom:8px; border-left: 3px solid {sfp_color}; padding-left:8px; background: rgba(255,255,255,0.02); padding-top: 4px; padding-bottom: 4px; border-radius: 0 4px 4px 0;">
                <div style="font-weight:700; font-size:0.85rem; color:{sfp_color}; margin-bottom: 2px;">2. TUZAK DURUMU: {pa['sfp']['title']}</div>
                <div class="edu-note" style="color:#94a3b8; margin-bottom: 0;">{pa['sfp']['desc']}</div>
            </div>

            <div style="margin-bottom:8px;">
                <div style="font-weight:700; font-size:0.85rem; color:#e2e8f0; margin-bottom: 2px;">3. HACİM & VSA ANALİZİ: <span style="color:#38bdf8;">{pa['vol']['title']}</span></div>
                <div class="edu-note" style="color:#94a3b8;">{pa['vol']['desc']}</div>
            </div>

            <div style="margin-top:4px; padding:8px; background:rgba(255,255,255,0.05); border-radius:6px; border-left:3px solid {obv_color}; margin-bottom:8px;">
                <div style="font-size:0.8rem; font-weight:700; color:{obv_color}; margin-bottom: 2px;">💰 {obv_title}</div>
                <div style="font-size:0.75rem; color:#94a3b8; font-style:italic;">{obv_desc}</div>
            </div>

            <div style="margin-bottom:8px;">
                <div style="font-weight:700; font-size:0.85rem; color:#e2e8f0; margin-bottom: 2px;">4. BAĞLAM & KONUM: <span style="color:#38bdf8;">{pa['loc']['title']}</span></div>
                <div class="edu-note" style="color:#94a3b8;">{pa['loc']['desc']}</div>
            </div>

            <div style="margin-bottom:8px;">
                <div style="font-weight:700; font-size:0.85rem; color:{sq_color}; margin-bottom: 2px;">5. VOLATİLİTE: {pa['sq']['title']}</div>
                <div class="edu-note" style="color:#94a3b8;">{pa['sq']['desc']}</div>
            </div>

            <div style="margin-bottom:6px; padding:8px; border-radius:6px; {div_style}">
                <div style="font-weight:800; font-size:0.85rem; margin-bottom: 2px;">6. RSI UYUMSUZLUK: {div_data['title']}</div>
                <div class="edu-note" style="margin-bottom:0; color:inherit; opacity:0.9;">{div_data['desc']}</div>
            </div>

            <div style="margin-bottom:6px; padding:8px; border-left:3px solid {sd_col}; background:rgba(255,255,255,0.03); border-radius:4px; border-top: 1px solid rgba(255,255,255,0.05); border-right: 1px solid rgba(255,255,255,0.05); border-bottom: 1px solid rgba(255,255,255,0.05);">
                <div style="font-weight:800; font-size:0.85rem; color:{sd_col};">🧱 ARZ-TALEP (S&D) BÖLGELERİ:</div>
                <div style="font-size:0.85rem; font-weight:600; color:#e2e8f0; margin-top:4px;">{sd_txt}</div>
                <div class="edu-note" style="margin-top:6px; margin-bottom:0; color:#94a3b8;">🐳 <b>Balina Ayak İzi:</b> Kurumsal fonların geçmişte yüklü emir bırakmış olabileceği gizli maliyet bölgesi. Fiyat bu alana girdiğinde potansiyel bir sıçrama (tepki) ihtimali doğabilir.</div>
            </div>

            <div style="border-top: 1px dashed rgba(255,255,255,0.1); margin-top:8px; padding-top:8px;"></div> 

            <div style="margin-bottom:8px;">
                <div style="font-weight:700; font-size:0.85rem; color:{vwap_col}; margin-bottom: 2px;">7. KURUMSAL REFERANS MALİYET (VWAP): {vwap_txt}</div>
                <div class="edu-note" style="color:#94a3b8;">{vwap_desc} (Son 20 gün Hacim Ağırlıklı Ortalama Fiyat-VWAP: <span style="color:#e2e8f0; font-weight:600;">{vwap_data['val']:.2f}</span>)</div>
            </div>

            <div style="margin-bottom:4px;">
                <div style="font-weight:700; font-size:0.85rem; color:{rs_col}; margin-bottom: 2px;">8. RS: PİYASA GÜCÜ (Bugün): {rs_txt}</div>
                <div class="edu-note" style="color:#94a3b8; margin-bottom:0;">{rs_desc}</div>
            </div>        
        </div>
        """
        st.markdown(html_content.replace("\n", ""), unsafe_allow_html=True)
        
        if pa and "smart_volume" in pa:
            sv = pa["smart_volume"]
            bc = "#ef4444" if "SATICI" in sv["title"] or "ALTINDA" in sv["title"] else "#10b981" if "ALIM" in sv["title"] or "ÜZERİNDE" in sv["title"] else "#f59e0b"
            bg = "rgba(239, 68, 68, 0.1)" if bc == "#ef4444" else "rgba(16, 185, 129, 0.1)" if bc == "#10b981" else "rgba(245, 158, 11, 0.1)"
            
            # Yüzdeli Metin Kısmı
            delta_val = sv.get("delta", 0)
            delta_yuzde = sv.get("delta_yuzde", 0)
            is_index = ticker.startswith(("XU", "XB", "XT", "XY", "^"))
            if is_index:
                if delta_val < 0:
                    baskinlik = f"<span style='color: #ef4444; font-weight: 800;'>Agresif Satıcı Baskısı</span>" if delta_yuzde >= 60.0 else f"<span style='color: #94a3b8; font-weight: 800;'>Sığ Satış (Gürültü)</span>"
                elif delta_val > 0:
                    baskinlik = f"<span style='color: #10b981; font-weight: 800;'>Agresif Alıcı Baskısı</span>" if delta_yuzde >= 60.0 else f"<span style='color: #94a3b8; font-weight: 800;'>Pasif Alım (Gürültü)</span>"
                else:
                    baskinlik = f"<span style='color: #94a3b8; font-weight: 800;'>Kusursuz Denge</span>"
            else:
                if delta_val < 0:
                    baskinlik = f"<span style='color: #ef4444; font-weight: 800;'>-%{delta_yuzde:.1f} Agresif Satıcı Baskısı</span>" if delta_yuzde >= 60.0 else f"<span style='color: #94a3b8; font-weight: 800;'>-%{delta_yuzde:.1f} Sığ Satış (Gürültü)</span>"
                elif delta_val > 0:
                    baskinlik = f"<span style='color: #10b981; font-weight: 800;'>+%{delta_yuzde:.1f} Agresif Alıcı Baskısı</span>" if delta_yuzde >= 60.0 else f"<span style='color: #94a3b8; font-weight: 800;'>+%{delta_yuzde:.1f} Pasif Alım (Gürültü)</span>"
                else:
                    baskinlik = f"<span style='color: #94a3b8; font-weight: 800;'>Kusursuz Denge (%0)</span>"
                    
            delta_text = f"Tahmini Delta (BUGÜN): {baskinlik}"
            
            st.markdown(f"""
            <div style="border: 1px solid {bc}; background-color: {bg}; padding: 12px; border-radius: 8px; margin-top: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.2);">
                <div style="font-weight: 800; font-size: 0.95rem; color: {bc}; margin-bottom: 6px; display:flex; align-items:center; gap:5px;">📊 SMART MONEY HACİM ANALİZİ</div>
                <div style="font-weight: 700; font-size: 0.9rem; color: #e2e8f0; margin-bottom:4px;">{sv['title']}</div>
                <div style="font-style: italic; font-size: 0.85rem; color: #94a3b8; line-height: 1.4;">{sv['desc']}</div>
                <div style="border-top: 1px dashed rgba(255,255,255,0.15); margin-top: 10px; padding-top: 8px; font-size: 0.85rem; color: #e2e8f0;">{delta_text}</div>
            </div>""", unsafe_allow_html=True)

    else:
        sd_col = "#16a34a" if sd_data and "Talep" in sd_data['Type'] else "#dc2626" if sd_data else "#64748B"
        sfp_color = "#16a34a" if "Bullish" in pa['sfp']['title'] else "#dc2626" if "Bearish" in pa['sfp']['title'] else "#475569"
        sq_color = "#d97706" if "BOBİN" in pa['sq']['title'] else "#475569"
        
        if div_data['type'] == 'bearish': div_style = "background:#fef2f2; border-left:3px solid #dc2626; color:#991b1b;"
        elif div_data['type'] == 'bullish': div_style = "background:#f0fdf4; border-left:3px solid #16a34a; color:#166534;"
        else: div_style = "color:#475569;"

        vwap_col = "#035f25" if v_diff < -2.0 else "#056d2b" if v_diff < 0.0 else "#034969" if v_diff < 8.0 else "#a36903" if v_diff < 15.0 else "#570214"
        rs_col = "#059669" if alpha > 1.0 else "#470312" if alpha < -1.0 else "#475569"

        html_content = f"""
        <div class="info-card" style="border-top: 3px solid #6366f1;">
            <div class="info-header" style="color:#1e3a8a;">🕯️ Price Action Analizi: {display_ticker}</div>

            <div style="margin-bottom:8px;">
                <div style="font-weight:700; font-size:0.8rem; color:#1e3a8a;">1. MUM & FORMASYONLAR: {pa['candle']['title']}</div>
                <div class="edu-note">{pa['candle']['desc']}</div>
            </div>

            <div style="margin-bottom:8px; border-left: 2px solid {sfp_color}; padding-left:6px;">
                <div style="font-weight:700; font-size:0.8rem; color:{sfp_color};">2. TUZAK DURUMU: {pa['sfp']['title']}</div>
                <div class="edu-note">{pa['sfp']['desc']}</div>
            </div>

            <div style="margin-bottom:8px;">
                <div style="font-weight:700; font-size:0.8rem; color:#0f172a;">3. HACİM & VSA ANALİZİ: {pa['vol']['title']}</div>
                <div class="edu-note">{pa['vol']['desc']}</div>
            </div>

            <div style="margin-top:4px; padding:4px; background:{obv_color}15; border-radius:4px; border-left:2px solid {obv_color};">
                <div style="font-size:0.75rem; font-weight:700; color:{obv_color};">💰 {obv_title}</div>
                <div style="font-size:0.7rem; color:#475569; font-style:italic;">{obv_desc}</div>
            </div>

            <div style="margin-bottom:8px;">
                <div style="font-weight:700; font-size:0.8rem; color:#0f172a;">4. BAĞLAM & KONUM: {pa['loc']['title']}</div>
                <div class="edu-note">{pa['loc']['desc']}</div>
            </div>

            <div style="margin-bottom:8px;">
                <div style="font-weight:700; font-size:0.8rem; color:{sq_color};">5. VOLATİLİTE: {pa['sq']['title']}</div>
                <div class="edu-note">{pa['sq']['desc']}</div>
            </div>

            <div style="margin-bottom:6px; padding:4px; border-radius:4px; {div_style}">
                <div style="font-weight:700; font-size:0.8rem;">6. RSI UYUMSUZLUK: {div_data['title']}</div>
                <div class="edu-note" style="margin-bottom:0; color:inherit; opacity:0.9;">{div_data['desc']}</div>
            </div>

            <div style="margin-bottom:6px; padding:6px; border-left:3px solid {sd_col}; background:#f8fafc; border-radius:4px;">
                <div style="font-weight:700; font-size:0.8rem; color:{sd_col};">🧱 ARZ-TALEP (S&D) BÖLGELERİ:</div>
                <div style="font-size:0.85rem; font-weight:600; color:#0f172a; margin-top:2px;">{sd_txt}</div>
                <div class="edu-note" style="margin-top:4px; margin-bottom:0; color:inherit; opacity:0.9;">🐳 <b>Balina Ayak İzi:</b> Kurumsal fonların geçmişte yüklü emir bırakmış olabileceği gizli maliyet bölgesi. Fiyat bu alana girdiğinde potansiyel bir sıçrama (tepki) ihtimali doğabilir.</div>
            </div>

            <div style="border-top: 1px dashed #cbd5e1; margin-top:8px; padding-top:6px;"></div> 

            <div style="margin-bottom:8px;">
                <div style="font-weight:700; font-size:0.8rem; color:{vwap_col};">7. KURUMSAL REFERANS MALİYET (VWAP): {vwap_txt}</div>
                <div class="edu-note">{vwap_desc} (Son 20 gün Hacim Ağırlıklı Ortalama Fiyat-VWAP: {vwap_data['val']:.2f})</div>
            </div>

            <div style="margin-bottom:2px;">
                <div style="font-weight:700; font-size:0.8rem; color:{rs_col};">8. RS: PİYASA GÜCÜ (Bugün): {rs_txt}</div>
                <div class="edu-note" style="margin-bottom:0;">{rs_desc}</div>
            </div>        
        </div>
        """
        st.markdown(html_content.replace("\n", ""), unsafe_allow_html=True)
        
        if pa and "smart_volume" in pa:
            sv = pa["smart_volume"]
            bc = "#dc2626" if "SATICI" in sv["title"] or "ALTINDA" in sv["title"] else "#16a34a" if "ALIM" in sv["title"] or "ÜZERİNDE" in sv["title"] else "#d97706"
            bg = "#fef2f2" if bc == "#dc2626" else "#f0fdf4" if bc == "#16a34a" else "#fffbeb"
            
            delta_val = sv.get("delta", 0)
            delta_yuzde = sv.get("delta_yuzde", 0)
            is_index = ticker.startswith(("XU", "XB", "XT", "XY", "^"))
            if is_index:
                if delta_val < 0:
                    baskinlik = f"<span style='color: #dc2626; font-weight: 900;'>Agresif Satıcı Baskısı</span>" if delta_yuzde >= 60.0 else f"<span style='color: #64748b; font-weight: 900;'>Sığ Satış (Gürültü)</span>"
                elif delta_val > 0:
                    baskinlik = f"<span style='color: #16a34a; font-weight: 900;'>Agresif Alıcı Baskısı</span>" if delta_yuzde >= 60.0 else f"<span style='color: #64748b; font-weight: 900;'>Pasif Alım (Gürültü)</span>"
                else:
                    baskinlik = f"<span style='color: #64748b; font-weight: 900;'>Kusursuz Denge</span>"
            else:
                if delta_val < 0:
                    baskinlik = f"<span style='color: #dc2626; font-weight: 900;'>-%{delta_yuzde:.1f} Agresif Satıcı Baskısı</span>" if delta_yuzde >= 60.0 else f"<span style='color: #64748b; font-weight: 900;'>-%{delta_yuzde:.1f} Sığ Satış (Gürültü)</span>"
                elif delta_val > 0:
                    baskinlik = f"<span style='color: #16a34a; font-weight: 900;'>+%{delta_yuzde:.1f} Agresif Alıcı Baskısı</span>" if delta_yuzde >= 60.0 else f"<span style='color: #64748b; font-weight: 900;'>+%{delta_yuzde:.1f} Pasif Alım (Gürültü)</span>"
                else:
                    baskinlik = f"<span style='color: #64748b; font-weight: 900;'>Kusursuz Denge (%0)</span>"
                    
            delta_text = f"Tahmini Delta (BUGÜN): {baskinlik}"
            
            st.markdown(f"""
            <div style="border: 2px solid {bc}; background-color: {bg}; padding: 12px; border-radius: 8px; margin-top: 10px; margin-bottom: 10px;">
                <div style="font-weight: 800; font-size: 0.9rem; color: {bc}; margin-bottom: 4px;">📊 SMART MONEY HACİM ANALİZİ</div>
                <div style="font-weight: 700; font-size: 0.85rem; color: #0f172a;">{sv['title']}</div>
                <div style="font-style: italic; font-size: 0.95rem; color: #1e3a8a; margin-top: 4px; line-height: 1.4;">{sv['desc']}</div>
                <div style="border-top: 1px dashed {bc}; margin-top: 10px; padding-top: 8px; font-size: 0.8rem; color: #1e3a8a;">{delta_text}</div>
            </div>""", unsafe_allow_html=True)


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
    <div class="info-card" style="border-top: 3px solid #7c3aed; background: #faf5ff; margin-bottom: 10px;">
        <div class="info-header" style="color:#5b21b6; display:flex; justify-content:space-between; align-items:center;">
            <span>🦅 ICT Sniper Onay Raporu</span>
            <span style="font-size:0.8rem; background:#7c3aed15; padding:2px 8px; border-radius:10px; font-weight:700;">5/5</span>
        </div>
        
        <div class="info-row" style="margin-top:5px;">
            <div class="label-long" style="width:160px; color:#4c1d95;">1. Likidite Temizliği (SSL):</div>
            <div class="info-val" style="color:#16a34a; font-weight:800;">GEÇTİ ✅</div>
        </div>
        <div class="edu-note" style="margin-bottom:8px;">
            Son 20-40 günün dibi aşağı kırıldı. Stoplar patlatıldı.
        </div>

        <div class="info-row">
            <div class="label-long" style="width:160px; color:#4c1d95;">2. Market Yapı Kırılımı:</div>
            <div class="info-val" style="color:#16a34a; font-weight:800;">GEÇTİ ✅</div>
        </div>
        <div class="edu-note" style="margin-bottom:8px;">
            Fiyat ani bir "U" dönüşüyle son tepeyi yukarı kırdı.
        </div>

        <div class="info-row">
            <div class="label-long" style="width:160px; color:#4c1d95;">3. Enerji / Hacim:</div>
            <div class="info-val" style="color:#16a34a; font-weight:800;">GEÇTİ ✅</div>
        </div>
        <div class="edu-note" style="margin-bottom:8px;">
            Yükseliş cılız mumlarla değil, gövdeli ve iştahlı mumlarla oldu.
        </div>

        <div class="info-row">
            <div class="label-long" style="width:160px; color:#4c1d95;">4. FVG Bıraktılar (İmza):</div>
            <div class="info-val" style="color:#16a34a; font-weight:800;">VAR (Destek) ✅</div>
        </div>
        <div class="edu-note" style="margin-bottom:8px;">
            Yükselirken arkasında doldurulmamış boşluk bıraktı.
        </div>

        <div class="info-row" style="border-top:1px dashed #d8b4fe; padding-top:6px; margin-top:4px;">
            <div class="label-long" style="width:160px; color:#4c1d95; font-weight:800;">5. İndirimli Bölge:</div>
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

    mt_title = "Kritik Denge Seviyesi"
    mt_desc  = "Fiyat kritik orta noktanın altına sarktı/üstüne çıktı. Yapı bozulmuş olabilir."
    if "bearish" in data['bias']:
        mt_title = "Satıcılar Baskın"
        if _mt > 0 and _mt < _cp:
            # Doğru senaryo: denge noktası fiyatın altında → direnç
            mt_desc = (f"⚖️ <b>{_level_label}: {_mt:.2f}</b> — Fiyat bu seviyeyi aşağı kırdı; "
                       f"{_mt:.2f} artık <b>direnç</b> işlevi görüyor. Kurumsal sipariş akışı (Order Flow) satıcılar lehine. "
                       f"Fiyat bu seviyeyi yeniden yukarı kıramadığı sürece yapı bozuk kalır; yeni alım açmak riskli.")
        elif _mt > 0:
            # Denge noktası fiyatın üstünde → price dropped below range mid
            mt_desc = (f"⚖️ <b>{_level_label}: {_mt:.2f}</b> — Fiyat bu denge seviyesinin <b>altında</b> işlem görüyor. "
                       f"Satıcılar baskın; {_mt:.2f} seviyesi kısa vadeli hedef direnç. "
                       f"Fiyatın bu seviyeyi net geçip geçemeyeceği, trendin devamını belirleyecek.")
        else:
            mt_desc = "Kurumsal sipariş akışı (Order Flow) satıcılar lehine. Yeni alım pozisyonları için erken; yapı bozuk kalmaya devam ediyor."
    elif "bullish" in data['bias']:
        mt_title = "Alıcılar Baskın"
        if _mt > 0 and _mt <= _cp:
            # Doğru senaryo: denge noktası fiyatın altında → destek
            mt_desc = (f"⚖️ <b>{_level_label}: {_mt:.2f}</b> — Fiyat bu seviyenin üzerinde işlem görüyor; "
                       f"{_mt:.2f} <b>destek</b> görevi üstlendi. Olası geri çekilmelerde bu bölgeye yakın alım fırsatı doğabilir. "
                       f"Kapanışlar bu seviyenin altına sarkarsa pozisyon korunmalıdır.")
        elif _mt > 0:
            # Denge noktası fiyatın üstünde → fiyat iskontolu bölgede, dengeye ulaşması gerekiyor
            mt_desc = (f"⚖️ <b>{_level_label}: {_mt:.2f}</b> — Fiyat şu an bu seviyenin <b>altında</b> (iskontolu bölge). "
                       f"Sipariş akışı alıcılar lehine olmakla birlikte, fiyatın önce {_mt:.2f} seviyesine <b>ulaşması gerekiyor</b> — "
                       f"bu aşamalı bir yükseliş hedefidir, mevcut fiyattan destek değil.")
        else:
            mt_desc = "Kurumsal sipariş akışı (Order Flow) alıcılar lehine. Bu seviye güçlü destek görevi görüyor; olası geri çekilmeler alım fırsatı sunabilir."

    # Yapı ↔ Bias çelişki uyarısı
    _struct_bearish = any(w in struct_title for w in ["AYI SIKIŞMASI", "DÜŞÜŞ TRENDİ", "BEARISH"])
    _struct_bullish = any(w in struct_title for w in ["BOĞA SIKIŞMASI", "YÜKSELİŞ TRENDİ", "BULLISH"])
    if ("bullish" in data['bias'] and _struct_bearish) or ("bearish" in data['bias'] and _struct_bullish):
        mt_desc += ("<div style='margin-top:8px; padding:6px 8px; border-top:1px dashed rgba(239,68,68,0.5);'>"
                    "<span style='color:#ef4444; font-weight:700; font-size:0.78rem;'>⚠️ Dikkat: </span>"
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
                                   
    display_ticker = ticker.replace(".IS", "").replace("=F", "")
    info = fetch_stock_info(ticker)
    current_price_str = f"{info.get('price', 0):.2f}" if info else "0.00"

    # --- MODEL SKORU GÖRSEL DEĞİŞKENLERİ ---
    model_score  = data.get('model_score', 0)
    model_checks = data.get('model_checks', [])
    ob_age   = data.get('ob_age', 0)
    fvg_age  = data.get('fvg_age', 0)
    struct_age = data.get('struct_age', 0)
    _blocks = "■" * model_score + "□" * (5 - model_score)
    if model_score >= 4: _sc = "#10b981"
    elif model_score == 3: _sc = "#f59e0b"
    else: _sc = "#ef4444"
    _slabel = ["SETUP YOK", "ÇOK ZAYIF", "ZAYIF", "ORTA", "GÜÇLÜ", "TAM MODEL"][model_score]
    # Kriter ipucu metni (title attribute için)
    _checks_tip = " | ".join([f"{'✅' if ok else '❌'} {name}" for name, ok in model_checks])

    # Yaş renkleri (0-5g taze, 6-15g orta, 16+ eski)
    def _age_clr(age): return ("#10b981","rgba(16,185,129,0.15)") if age<=5 else ("#f59e0b","rgba(245,158,11,0.15)") if age<=15 else ("#ef4444","rgba(239,68,68,0.15)")
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
        else:                  _snote = f" <span style='color:#ef4444;font-size:0.72rem;'>⚠️ Eski yapı ({struct_age} gün önce)</span>"
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
        ob_col  = "#ef4444" if not is_bull else "#38bdf8"
        ob_bg   = "rgba(239,68,68,0.08)" if not is_bull else "rgba(56,189,248,0.08)"
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
        txt_muted = "#64748b" if dark else "#94a3b8"
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

    ruler_html = _price_ruler_html(data, st.session_state.dark_mode)

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

    if st.session_state.dark_mode:
        mc = "#10b981" if "bullish" in data['bias'] else "#ef4444" if "bearish" in data['bias'] else "#8b5cf6"
        bg = "rgba(16, 185, 129, 0.1)" if "bullish" in data['bias'] else "rgba(239, 68, 68, 0.1)" if "bearish" in data['bias'] else "rgba(139, 92, 246, 0.1)"
        st.markdown(f"""
        <div class="info-card" style="border-top: 4px solid {mc}; margin-bottom:10px; border-radius: 8px; background: rgba(17, 24, 39, 0.6); border: 1px solid rgba(255,255,255,0.05);">
            <div class="info-header" style="color:#38bdf8; display:flex; justify-content:space-between; align-items:center; padding: 3px 12px; border-bottom: none;">
                <span style="font-size:1.15rem; font-weight: 800;">🧠 ICT Smart Money Analizi: {display_ticker}</span>
                <span title="{_checks_tip}" style="cursor:default; font-family:monospace; color:{_sc}; font-size:0.88rem; font-weight:700; letter-spacing:2px; background:rgba(0,0,0,0.3); padding:3px 10px; border-radius:6px; border:1px solid {_sc}40;">{_blocks} &nbsp;{model_score}/5 · {_slabel}</span>
                <span style="font-family:'JetBrains Mono'; font-weight:800; color:#10b981; font-size:1.1rem; background: rgba(0,0,0,0.4); padding: 2px 8px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.1);">{current_price_str}</span>
            </div>
        </div>""", unsafe_allow_html=True)
        
        c1, c2 = st.columns([1.4, 1])
        with c1:
            sc1, sc2 = st.columns(2)
            with sc1: st.markdown(f"""<div style="border:1px solid {mc}; background:{bg}; border-radius:8px; padding:12px; height: 100%;"><div style="font-weight:800; color:{mc}; font-size:0.85rem; text-transform: uppercase; margin-bottom:6px;">{struct_title}</div><div style="font-size:0.8rem; color:#a3a8b8; line-height:1.4;">{struct_desc}</div></div>""", unsafe_allow_html=True)
            with sc2: st.markdown(f"""<div style="border:1px solid rgba(255,255,255,0.1); background:rgba(31, 41, 55, 0.5); border-radius:8px; padding:12px; height: 100%;"><div style="font-weight:800; color:#a78bfa; font-size:0.85rem; text-transform: uppercase; margin-bottom:6px;">{energy_title}</div><div style="font-size:0.8rem; color:#a3a8b8; line-height:1.4;">{energy_desc}</div></div>""", unsafe_allow_html=True)
            hc1, hc2 = st.columns(2)
            with hc1: st.markdown(f"""<div style="background:rgba(245, 158, 11, 0.1); border:1px solid rgba(245, 158, 11, 0.3); border-left:4px solid #f59e0b; padding:12px; margin-top:12px; margin-bottom:12px; border-radius:8px; height: 100%;"><div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:6px;"><div style="font-weight:800; color:#fbbf24; font-size:0.9rem;">🛡️ {mt_title}</div><div style="font-family:'JetBrains Mono'; font-weight:800; font-size:1.1rem; color:#fbbf24; background: rgba(0,0,0,0.3); padding: 4px 8px; border-radius: 4px; margin-left: 8px; white-space:nowrap;">{data['mean_threshold']:.2f}</div></div><div style="font-size:0.75rem; color:#a3a8b8; line-height:1.5;">{mt_desc}</div></div>""", unsafe_allow_html=True)
            with hc2: st.markdown(f"""<div style="border:1px solid rgba(239, 68, 68, 0.3); background:rgba(239, 68, 68, 0.1); padding:12px; border-radius:8px; margin-top:12px; margin-bottom:12px; height: 100%;"><div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;"><div style="font-weight:800; color:#f87171; font-size:0.9rem; text-transform: uppercase;">🎯 Yakın Hedef</div><div style="font-weight:800; font-family:'JetBrains Mono'; font-size:1.2rem; color:#f87171; background: rgba(0,0,0,0.3); padding: 2px 8px; border-radius: 6px;">{data['target']:.2f}</div></div><div style="font-size:0.75rem; color:#cbd5e1; line-height:1.4; margin-bottom:10px;">{liq_desc}</div><div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; border-top:1px solid rgba(255,255,255,0.1); padding-top:6px;"><div style="font-weight:800; color:#fb923c; font-size:0.9rem;">{struct_target_label}</div><div style="font-weight:800; font-family:'JetBrains Mono'; font-size:1.1rem; color:#fb923c; background: rgba(0,0,0,0.3); padding: 2px 8px; border-radius: 6px;">{structural_target_val:.2f}</div></div><div style="font-size:0.75rem; color:#cbd5e1; line-height:1.4;">{struct_target_desc}</div></div>""", unsafe_allow_html=True)
        with c2:
            st.markdown(f"""
            <div style="border:1px solid rgba(255,255,255,0.1); background:rgba(17,24,39,0.6); border-radius:8px; padding:12px; height:100%;">
                <div style="font-weight:800; color:#f472b6; font-size:0.9rem; text-transform:uppercase; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:6px; margin-bottom:10px;">📍 FİYAT HARİTASI</div>
                <div style="display:grid; grid-template-columns:54% 44%; gap:10px; align-items:start;">
                    <div>
                        <div style="margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid rgba(255,255,255,0.06);">
                            <div style="font-size:0.67rem; font-weight:800; color:#fb7185; margin-bottom:3px;">📍 KONUM — {data['zone']}</div>
                            <div style="font-size:0.71rem; color:#cbd5e1; line-height:1.45;">{zone_simple}</div>
                        </div>
                        <div style="margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid rgba(255,255,255,0.06);">
                            <div style="font-size:0.67rem; font-weight:800; color:#38bdf8; margin-bottom:3px;">🧱 KURUM BLOĞU {ob_age_badge}</div>
                            <div style="font-size:0.71rem; color:#cbd5e1; line-height:1.45;">{ob_simple}</div>
                        </div>
                        <div style="margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid rgba(255,255,255,0.06);">
                            <div style="font-size:0.67rem; font-weight:800; color:#a78bfa; margin-bottom:3px;">⚡ BOŞLUK (FVG) {fvg_age_badge}</div>
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
                          <div><span style="color:#6ee7b7; font-weight:700;">HAVUZ:</span> <span style="color:#94a3b8;">{data.get('eqh_eql_txt','-')}</span></div>
                          <div><span style="color:#fca5a5; font-weight:700;">SWEEP:</span> <span style="color:#94a3b8;">{data.get('sweep_txt','-')}</span></div>
                        </div>
                    </div>
                </div>
            </div>""", unsafe_allow_html=True)
        st.markdown(f"""<div style="background:rgba(56, 189, 248, 0.07); border:1px solid rgba(56, 189, 248, 0.3); border-radius:8px; padding:16px; margin-top:10px; text-align: center;"><div style="font-weight:800; color:#7dd3fc; font-size:0.9rem; margin-bottom:8px; text-transform: uppercase;">🖥️ BOTTOM LINE (SONUÇ)</div><div style="font-size:1.05rem; color:#e2e8f0; font-style:italic; line-height:1.5; font-weight: 500;">"{data.get('bottom_line', '-')}"</div></div>""", unsafe_allow_html=True)
    else:
        mc = "#16a34a" if "bullish" in data['bias'] else "#dc2626" if "bearish" in data['bias'] else "#7c3aed"
        bg = "#f0fdf4" if "bullish" in data['bias'] else "#fef2f2" if "bearish" in data['bias'] else "#f5f3ff"
        st.markdown(f"""
        <div class="info-card" style="border-top: 4px solid {mc}; margin-bottom:10px; border-radius: 8px;">
            <div class="info-header" style="color:#1e3a8a; display:flex; justify-content:space-between; align-items:center; padding: 3px 12px;"><span style="font-size:1.15rem; font-weight: 800;">🧠 ICT Smart Money Analizi: {display_ticker}</span><span title="{_checks_tip}" style="cursor:default; font-family:monospace; color:{_sc}; font-size:0.88rem; font-weight:700; letter-spacing:2px; background:#f8fafc; padding:3px 10px; border-radius:6px; border:2px solid {_sc};">{_blocks} &nbsp;{model_score}/5 · {_slabel}</span><span style="font-family:'JetBrains Mono'; font-weight:800; color:#0f172a; font-size:1.1rem; background: #f1f5f9; padding: 2px 8px; border-radius: 6px;">{current_price_str}</span></div>
        </div>""", unsafe_allow_html=True)
        
        c1, c2 = st.columns([1.4, 1])
        with c1:
            sc1, sc2 = st.columns(2)
            with sc1: st.markdown(f"""<div style="border:2px solid {mc}; background:{bg}; border-radius:8px; padding:12px; height: 100%;"><div style="font-weight:800; color:{mc}; font-size:0.85rem; text-transform: uppercase; margin-bottom:6px;">{struct_title}</div><div style="font-size:0.8rem; color:#1e3a8a; line-height:1.4;">{struct_desc}</div></div>""", unsafe_allow_html=True)
            with sc2: st.markdown(f"""<div style="border:2px solid #94a3b8; background:#f8fafc; border-radius:8px; padding:12px; height: 100%;"><div style="font-weight:800; color:#7c3aed; font-size:0.85rem; text-transform: uppercase; margin-bottom:6px;">{energy_title}</div><div style="font-size:0.8rem; color:#1e3a8a; line-height:1.4;">{energy_desc}</div></div>""", unsafe_allow_html=True)
            hc1, hc2 = st.columns(2)
            with hc1: st.markdown(f"""<div style="background:#fff7ed; border:2px solid #ea580c; border-left:6px solid #ea580c; padding:12px; margin-top:12px; margin-bottom:12px; border-radius:8px; height: 100%;"><div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:6px;"><div style="font-weight:800; color:#c2410c; font-size:0.9rem;">🛡️ {mt_title}</div><div style="font-family:'JetBrains Mono'; font-weight:800; font-size:1.1rem; color:#c2410c; background: white; padding: 4px 8px; border-radius: 4px; margin-left: 8px; white-space:nowrap;">{data['mean_threshold']:.2f}</div></div><div style="font-size:0.75rem; color:#9a3412; line-height:1.5;">{mt_desc}</div></div>""", unsafe_allow_html=True)
            with hc2: st.markdown(f"""<div style="border:2px solid #e11d48; background:#fff1f2; padding:12px; border-radius:8px; margin-top:12px; margin-bottom:12px; height: 100%;"><div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;"><div style="font-weight:800; color:#be185d; font-size:0.9rem; text-transform: uppercase;">🎯 Yakın Hedef</div><div style="font-weight:800; font-family:'JetBrains Mono'; font-size:1.2rem; color:#be185d; background: white; padding: 2px 8px; border-radius: 6px;">{data['target']:.2f}</div></div><div style="font-size:0.75rem; color:#7f1d1d; line-height:1.4; margin-bottom:10px;">{liq_desc}</div><div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; border-top:2px solid #fecdd3; padding-top:6px;"><div style="font-weight:800; color:#c2410c; font-size:0.9rem;">{struct_target_label}</div><div style="font-weight:800; font-family:'JetBrains Mono'; font-size:1.1rem; color:#c2410c; background: white; padding: 2px 8px; border-radius: 6px;">{structural_target_val:.2f}</div></div><div style="font-size:0.75rem; color:#7f1d1d; line-height:1.4;">{struct_target_desc}</div></div>""", unsafe_allow_html=True)
        with c2:
            st.markdown(f"""
            <div style="border:2px solid #cbd5e1; background:white; border-radius:8px; padding:12px; height:100%;">
                <div style="font-weight:800; color:#be185d; font-size:0.9rem; text-transform:uppercase; border-bottom:2px solid #e2e8f0; padding-bottom:6px; margin-bottom:10px;">📍 FİYAT HARİTASI</div>
                <div style="display:grid; grid-template-columns:54% 44%; gap:10px; align-items:start;">
                    <div>
                        <div style="margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid #f1f5f9;">
                            <div style="font-size:0.67rem; font-weight:800; color:#9f1239; margin-bottom:3px;">📍 KONUM — {data['zone']}</div>
                            <div style="font-size:0.71rem; color:#334155; line-height:1.45;">{zone_simple}</div>
                        </div>
                        <div style="margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid #f1f5f9;">
                            <div style="font-size:0.67rem; font-weight:800; color:#0369a1; margin-bottom:3px;">🧱 KURUM BLOĞU {ob_age_badge}</div>
                            <div style="font-size:0.71rem; color:#334155; line-height:1.45;">{ob_simple}</div>
                        </div>
                        <div style="margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid #f1f5f9;">
                            <div style="font-size:0.67rem; font-weight:800; color:#7e22ce; margin-bottom:3px;">⚡ BOŞLUK (FVG) {fvg_age_badge}</div>
                            <div style="font-size:0.71rem; color:#334155; line-height:1.45;">{fvg_simple}</div>
                        </div>
                        <div>
                            <div style="font-size:0.67rem; font-weight:800; color:#ea580c; margin-bottom:3px;">🎯 HEDEF</div>
                            <div style="font-size:0.71rem; color:#334155; line-height:1.45;">{tgt_simple}</div>
                        </div>
                    </div>
                    <div>
                        {ruler_html}
                        <div style="font-size:0.65rem; line-height:1.6; margin-top:4px;">
                          <div><span style="color:#16a34a; font-weight:700;">HAVUZ:</span> <span style="color:#475569;">{data.get('eqh_eql_txt','-')}</span></div>
                          <div><span style="color:#dc2626; font-weight:700;">SWEEP:</span> <span style="color:#475569;">{data.get('sweep_txt','-')}</span></div>
                        </div>
                    </div>
                </div>
            </div>""", unsafe_allow_html=True)
        st.markdown(f"""<div style="background:#dbeafe; border:2px solid #3b82f6; border-radius:8px; padding:16px; margin-top:10px; text-align: center;"><div style="font-weight:800; color:#1e40af; font-size:0.9rem; margin-bottom:8px; text-transform: uppercase;">🖥️ BOTTOM LINE (SONUÇ)</div><div style="font-size:1.05rem; color:#1e3a8a; font-style:italic; line-height:1.5; font-weight: 500;">"{data.get('bottom_line', '-')}"</div></div>""", unsafe_allow_html=True)

def render_levels_card(ticker):
    data = get_advanced_levels_data(ticker)
    if not data: return
    display_ticker = ticker.replace(".IS", "").replace("=F", "").replace("-USD", "")
    info = fetch_stock_info(ticker)
    current_price_str = f"{info.get('price', 0):.2f}" if info else "0.00"
    
    is_bullish = data['st_dir'] == 1
    st_color = "#10b981" if is_bullish else "#ef4444" if st.session_state.dark_mode else "#16a34a" if is_bullish else "#dc2626"
    st_text = "YÜKSELİŞ (AL)" if is_bullish else "DÜŞÜŞ (SAT)"
    st_icon = "🐂" if is_bullish else "🐻"
    
    # --- ORİJİNAL MANTIK VE METİNLER (DOKUNULMADI) ---
    if is_bullish:
        st_label = "Takip Eden Stop (Stop-Loss)"
        st_desc = "⚠️ Fiyat bu seviyenin <b>altına inerse</b> trend bozulur, stop olunmalıdır."
        gp_desc_text = "Kurumsal alım bölgesi (İdeal Giriş/Destek)."
        gp_desc_color = "#92400e" 
        res_ui_label = "EN YAKIN DİRENÇ 🚧"
        res_ui_desc = "Zorlu tavan. Geçilirse yükseliş hızlanır."
        sup_ui_label = "EN YAKIN DESTEK 🛡️"
        sup_ui_desc = "İlk savunma hattı. Düşüşü tutmalı."
    else:
        st_label = "Trend Dönüşü (Direnç)"
        st_desc = "🚀 Piyasa yapıcının sipariş akışını (Order Flow) koruduğu son hattır. Yani, Fiyat bu seviyenin <b>üstüne çıkarsa</b> düşüş biter, yükseliş başlar."
        gp_desc_text = "⚠️ Güçlü Direnç / Tepki Satış Bölgesi (Short). Büyük fonların 'Discount' (İndirimli) fiyatlardan maliyetlenmek veya dağıtım yapmak için beklediği en stratejik denge noktasıdır."
        gp_desc_color = "#b91c1c" 
        res_ui_label = "O.T.E. DİRENCİ"
        res_ui_desc = "Akıllı Para short arar. Trend yönünde satış bölgesidir. Fiyatın Fibonacci O.T.E. aralığına girmesi 'pahalı' bölgeye işarettir. Akıllı para, buradaki perakende alımları satış likiditesi olarak kullanır."
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
    
    if st.session_state.dark_mode:
        html_content = f"""
        <div class="info-card" style="border-top: 3px solid #8b5cf6; background: rgba(17, 24, 39, 0.6); border: 1px solid rgba(255,255,255,0.05);">
            <div class="info-header" style="color:#a78bfa; margin-bottom:10px; display:flex; justify-content:space-between; align-items:center; padding: 3px 12px; font-size:1.1rem; font-weight: 800; border-bottom:none;">
            <span>📐 Orta Vadeli Trend (1-6 ay): {display_ticker}</span>
            <span style="font-family:'JetBrains Mono'; font-weight:800; color:#10b981; font-size:1.1rem; background: rgba(0,0,0,0.4); padding: 2px 8px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.1);">{current_price_str}</span>
            </div>
            
            <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:8px;">
                
                <div style="background:rgba(255,255,255,0.05); padding:8px; border-radius:5px; border:1px solid {st_color}; display:flex; flex-direction:column; justify-content:space-between;">
                    <div>
                        <div style="font-weight:700; color:{st_color} ; font-size:0.85rem;">{st_icon} SuperTrend</div>
                        <div style="font-weight:800; color:{st_color}; font-size:0.85rem; margin-top:2px;">{st_text}</div>
                    </div>
                    <div style="margin-top:8px;">
                        <div style="font-size:0.85rem; color:#94a3b8;">{st_label}:</div>
                        <div style="font-family:'JetBrains Mono'; font-weight:800; color:#e2e8f0; font-size:0.9rem;">{data['st_val']:.2f}</div>
                        <div style="font-size:0.85rem; color:#94a3b8; font-style:italic; margin-top:6px; border-top:1px dashed rgba(255,255,255,0.1); padding-top:4px; line-height:1.2;">
                            {st_desc}
                        </div>
                    </div>
                </div>

                <div style="background:rgba(16, 185, 129, 0.05); padding:8px; border-radius:4px; border:1px solid rgba(16, 185, 129, 0.3); display:flex; flex-direction:column; justify-content:space-between;">
                    <div>
                        <div style="font-size:0.85rem; color:#34d399; font-weight:700;">{res_ui_label}</div>
                        <div style="font-family:'JetBrains Mono'; font-weight:800; color:#10b981; font-size:1rem; margin-top:2px;">{res_display}</div>
                    </div>
                    <div style="margin-top:8px;">
                        <div style="font-size:0.85rem; color:#34d399; font-weight:600;">Fib {res_lbl}</div>
                        <div style="font-size:0.85rem; color:#94a3b8; font-style:italic; margin-top:6px; border-top:1px dashed rgba(16, 185, 129, 0.2); padding-top:4px; line-height:1.2;">
                            {res_desc_final}
                        </div>
                    </div>
                </div>

                <div style="background:rgba(239, 68, 68, 0.05); padding:8px; border-radius:4px; border:1px solid rgba(239, 68, 68, 0.3); display:flex; flex-direction:column; justify-content:space-between;">
                    <div>
                        <div style="font-size:0.85rem; color:#f87171; font-weight:700;">{sup_ui_label}</div>
                        <div style="font-family:'JetBrains Mono'; font-weight:800; color:#ef4444; font-size:1rem; margin-top:2px;">{sup_val:.2f}</div>
                    </div>
                    <div style="margin-top:8px;">
                        <div style="font-size:0.85rem; color:#f87171; font-weight:600;">Fib {sup_lbl}</div>
                        <div style="font-size:0.85rem; color:#94a3b8; font-style:italic; margin-top:6px; border-top:1px dashed rgba(239, 68, 68, 0.2); padding-top:4px; line-height:1.2;">
                            {sup_ui_desc}
                        </div>
                    </div>
                </div>

                <div style="background:rgba(245, 158, 11, 0.05); padding:8px; border-radius:4px; border:1px dashed #f59e0b; display:flex; flex-direction:column; justify-content:space-between;">
                    <div>
                        <div style="font-size:0.85rem; font-weight:700; color:#fbbf24;">⚜️ GOLDEN POCKET</div>
                        <div style="font-family:'JetBrains Mono'; font-size:1rem; font-weight:800; color:#f59e0b; margin-top:2px;">{gp_val:.2f}</div>
                    </div>
                    <div style="margin-top:8px;">
                        <div style="font-size:0.85rem; color:#fbbf24; font-weight:600;">Kurumsal Bölge</div>
                        <div style="font-size:0.85rem; color:#d97706; font-style:italic; margin-top:6px; border-top:1px dashed rgba(245, 158, 11, 0.3); padding-top:4px; line-height:1.2;">
                            {gp_desc_text}
                        </div>
                    </div>
                </div>

            </div>
        </div>
        """
    else:
        html_content = f"""
        <div class="info-card" style="border-top: 3px solid #8b5cf6;">
            <div class="info-header" style="color:#4c1d95; margin-bottom:10px; display:flex; justify-content:space-between; align-items:center; padding: 3px 12px; font-size:1.1rem; font-weight: 800;">
            <span>📐 Orta Vadeli Trend (1-6 ay): {display_ticker}</span>
            <span style="font-family:'JetBrains Mono'; font-weight:800; color:#0f172a; font-size:1.1rem; background: #f1f5f9; padding: 2px 8px; border-radius: 6px;">{current_price_str}</span>
            </div>
            
            <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:8px;">
                
                <div style="background:{st_color}15; padding:8px; border-radius:5px; border:1px solid {st_color}; display:flex; flex-direction:column; justify-content:space-between;">
                    <div>
                        <div style="font-weight:700; color:{st_color} ; font-size:0.85rem;">{st_icon} SuperTrend</div>
                        <div style="font-weight:800; color:{st_color}; font-size:0.85rem; margin-top:2px;">{st_text}</div>
                    </div>
                    <div style="margin-top:8px;">
                        <div style="font-size:0.85rem; color:#64748B;">{st_label}:</div>
                        <div style="font-family:'JetBrains Mono'; font-weight:800; color:#0f172a; font-size:0.9rem;">{data['st_val']:.2f}</div>
                        <div style="font-size:0.85rem; color:#6b7280; font-style:italic; margin-top:6px; border-top:1px dashed {st_color}40; padding-top:4px; line-height:1.2;">
                            {st_desc}
                        </div>
                    </div>
                </div>

                <div style="background:#f0fdf4; padding:8px; border-radius:4px; border:1px solid #bbf7d0; display:flex; flex-direction:column; justify-content:space-between;">
                    <div>
                        <div style="font-size:0.85rem; color:#166534; font-weight:700;">{res_ui_label}</div>
                        <div style="font-family:'JetBrains Mono'; font-weight:800; color:#15803d; font-size:1rem; margin-top:2px;">{res_display}</div>
                    </div>
                    <div style="margin-top:8px;">
                        <div style="font-size:0.85rem; color:#166534; font-weight:600;">Fib {res_lbl}</div>
                        <div style="font-size:0.85rem; color:#64748B; font-style:italic; margin-top:6px; border-top:1px dashed #bbf7d0; padding-top:4px; line-height:1.2;">
                            {res_desc_final}
                        </div>
                    </div>
                </div>

                <div style="background:#fef2f2; padding:8px; border-radius:4px; border:1px solid #fecaca; display:flex; flex-direction:column; justify-content:space-between;">
                    <div>
                        <div style="font-size:0.85rem; color:#991b1b; font-weight:700;">{sup_ui_label}</div>
                        <div style="font-family:'JetBrains Mono'; font-weight:800; color:#b91c1c; font-size:1rem; margin-top:2px;">{sup_val:.2f}</div>
                    </div>
                    <div style="margin-top:8px;">
                        <div style="font-size:0.85rem; color:#991b1b; font-weight:600;">Fib {sup_lbl}</div>
                        <div style="font-size:0.85rem; color:#64748B; font-style:italic; margin-top:6px; border-top:1px dashed #fecaca; padding-top:4px; line-height:1.2;">
                            {sup_ui_desc}
                        </div>
                    </div>
                </div>

                <div style="background:#fffbeb; padding:8px; border-radius:4px; border:1px dashed #f59e0b; display:flex; flex-direction:column; justify-content:space-between;">
                    <div>
                        <div style="font-size:0.85rem; font-weight:700; color:#92400e;">⚜️ GOLDEN POCKET</div>
                        <div style="font-family:'JetBrains Mono'; font-size:1rem; font-weight:800; color:#b45309; margin-top:2px;">{gp_val:.2f}</div>
                    </div>
                    <div style="margin-top:8px;">
                        <div style="font-size:0.85rem; color:#92400e; font-weight:600;">Kurumsal Bölge</div>
                        <div style="font-size:0.85rem; color:{gp_desc_color}; font-style:italic; margin-top:6px; border-top:1px dashed #f59e0b; padding-top:4px; line-height:1.2;">
                            {gp_desc_text}
                        </div>
                    </div>
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
    display_ticker = ticker.replace(".IS", "").replace("=F", "")

    # 2. Görsel öğeleri hazırla
    trend_icon = "✅" if data['trend_ok'] else "❌"
    vcp_icon = "✅" if data['is_vcp'] else "❌"
    vol_icon = "✅" if data['is_dry'] else "❌"
    rs_icon = "✅" if data['rs_val'] > 0 else "❌"
    
    rs_width = min(max(int(data['rs_val'] * 5 + 50), 0), 100)
    rs_color = "#16a34a" if data['rs_val'] > 0 else "#dc2626"
    
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
<div style="background:#f8fafc; padding:4px; border-radius:4px; border:1px solid #e2e8f0;">
<div style="font-size:0.6rem; color:#64748B; font-weight:700;">TREND</div>
<div style="font-size:1rem;">{trend_icon}</div>
</div>
<div style="background:#f8fafc; padding:4px; border-radius:4px; border:1px solid #e2e8f0;">
<div style="font-size:0.6rem; color:#64748B; font-weight:700;">VCP</div>
<div style="font-size:1rem;">{vcp_icon}</div>
</div>
<div style="background:#f8fafc; padding:4px; border-radius:4px; border:1px solid #e2e8f0;">
<div style="font-size:0.6rem; color:#64748B; font-weight:700;">ARZ</div>
<div style="font-size:1rem;">{vol_icon}</div>
</div>
<div style="background:#f8fafc; padding:4px; border-radius:4px; border:1px solid #e2e8f0;">
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
<div style="width:100%; height:6px; background:#e2e8f0; border-radius:3px; overflow:hidden;">
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

# --- 8 MADDELİK HİBRİT (PA + QUANT) TEKNİK YOL HARİTASI ---
@st.cache_data(ttl=600)
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
            
        m1 = f"<b>Günlük Mum:</b> {m1_mum}<br><b>PA Sinyali:</b> {mum_formasyonu}"

        # --- 2. FORMASYON TESPİTİ (1-6 Ay) ---
        pat_df = pd.DataFrame()
        try: pat_df = scan_chart_patterns([ticker])
        except: pass
        
        if not pat_df.empty:
            pat_name = pat_df.iloc[0]['Formasyon']
            m2 = f"<b>Mevcut Formasyon:</b> {pat_name}<br><b>Ana Yapı:</b> {'İtki (Trend)' if cp > sma50 else 'Düzeltme (Pullback)'}"
        else:
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
            f"<div style='width:{ayi_w}%;background:rgba(239,68,68,0.75);height:100%;"
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
                ozet_metin = f"Zayıf ve baskılı piyasa yapısı devam ediyor, anlamlı bir kurumsal giriş (Efor) görülmüyor. Fiyatın yeniden güvenli bölgeye geçebilmesi için acilen {fmt(res_20)} seviyesini hacimle geri alması şart. Aksi takdirde zayıflama devam eder."
                
        else: # Karmaşık durum
            if is_oversold:
                ozet_metin = f"Uzun vadeli ana trend pozitif olsa da (SMA200 üstü), kısa vadede sert bir düzeltme (Pullback) yaşanıyor. Fiyat iskontolu (Ucuz) bölgelere inmiş durumda. {fmt(sup_20)} seviyesinden gelecek bir 'V-Dönüş' reaksiyonu harika bir fırsat sunabilir."
            else:
                ozet_metin = f"Fiyat {fmt(sup_20)} ile {fmt(res_20)} arasında sıkışmış, yön arayışında olan bir testere (Choppy) piyasasında. Ne alıcılar ne de satıcılar tam kontrol sağlayabilmiş değil. Kırılım yönüne göre (Breakout/Breakdown) pozisyon almak en güvenli stratejidir."
            
        m8 = f"<b>Piyasa Sentezi:</b> {ozet_metin}"

        return {
            "M1": m1, "M2": m2, "M3": m3, "M4": m4, "M5": m5, "M6": m6, "M7": m7, "M8": m8
        }
    except Exception as e:
        return None

def render_roadmap_8_panel(ticker):
    data = calculate_8_point_roadmap(ticker)
    if not data: return

    display_ticker = ticker.split('.')[0].replace("=F", "").replace("-USD", "")
    
    # --- 1. YENİ EKLENEN: FİYAT ÇEKME VE KÜSURAT AYARI ---
    info = fetch_stock_info(ticker)
    current_price = info.get('price', 0) if info else 0
    is_index = "XU" in ticker.upper() or "^" in ticker or current_price > 1000
    display_price = f"{int(current_price)}" if is_index else f"{current_price:.2f}"

    # --- 2. YENİ EKLENEN: ROZET (BADGE) İÇİN TEMA RENKLERİ ---
    is_dark = st.session_state.dark_mode
    title_col = "#38bdf8" if is_dark else "#1e3a8a"
    header_bg = "rgba(56, 189, 248, 0.05)" if is_dark else "rgba(30, 58, 138, 0.05)"
    header_border = "rgba(56, 189, 248, 0.2)" if is_dark else "rgba(30, 58, 138, 0.2)"
    badge_bg = "rgba(56, 189, 248, 0.15)" if is_dark else "rgba(30, 58, 138, 0.15)" # Zemin renginin 2-3 ton koyusu
    badge_text = title_col # Yazılar başlık ile aynı renk
    price_color = title_col # Fiyat da başlık ile aynı renk
    
    def make_box(num, title, content, color, edu_text, tf_text):
        # BOŞLUKLAR TIRAŞLANDI: padding 6px 8px yapıldı, marginler kısıldı, line-height 1.35 yapıldı.
        return f"""
        <div style="background:rgba({color}, 0.05); border-left: 3px solid rgba({color}, 0.8); padding: 6px 8px; border-radius: 4px; display:flex; flex-direction:column; justify-content:flex-start; height: 100%;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 4px; border-bottom: 1px solid rgba({color}, 0.2); padding-bottom: 4px;">
                <div style="font-size: 0.8rem; font-weight: 800; color: rgba({color}, 1);">{num}. {title}</div>
                <div style="font-size: 0.6rem; font-weight: 700; color: #64748b; background: rgba(100,116,139,0.1); padding: 1px 4px; border-radius: 3px; border: 1px solid rgba(100,116,139,0.2);">⏱️ {tf_text}</div>
            </div>
            <div style="font-size: 0.75rem; font-weight: 500; line-height: 1.35; margin-bottom: 4px;" class="dark-text-fix">{content}</div>
            <div class="edu-note" style="font-size: 0.75rem; margin-top: auto; border-top: 1px dashed rgba({color}, 0.3); padding-top: 4px; margin-bottom: 0px;">{edu_text}</div>
        </div>
        """

    c_pa = "139, 92, 246"      # Mor
    c_vol = "245, 158, 11"     # Turuncu
    c_bull = "22, 163, 74"     # Yeşil
    c_summary = "14, 165, 233" # Mavi

    boxes = [
        make_box("1", "Fiyat Davranışı", data['M1'], c_pa, "Günlük mum yapısı ve 2-3 mumluk Price Action dizilimi.", "Son 1-3 Gün"),
        make_box("2", "Formasyon Tespiti", data['M2'], c_pa, "Geometrik yapılar ve ana trend durumu.", "1-6 Ay"),
        make_box("3", "Efor vs Sonuç (VSA)", data['M3'], c_vol, "Hacmin fiyata yansıma kalitesi (Churning kontrolü).", "Son 3 Gün"),
        make_box("4", "Trend Skoru", data['M4'], c_pa, "Sıkışma, hacim daralması ve hareketli ortalama yakınsaması.", "1-3 Ay"),
        make_box("5", "Hacim Algoritması", data['M5'], c_vol, "Kurumsal emilim (Absorption) ve agresif piyasa akışı.", "Son 20 Gün"),
        make_box("6", "Yön Beklentisi", data['M6'], c_vol, "RSI + SMA50 pozisyonu + RS Gücü + OBV kurumsal akışı.", "~1 Ay"),
        make_box("7", "Ayı ve Boğa Senaryoları", data['M7'], c_bull, "Olası kırılımlara göre tetiklenecek yön hedefleri.", "Kısa Vade"),
        make_box("8", "Teknik Özet", data['M8'], c_summary, "Tüm verilerin genel sentezi ve piyasa beklentisi.", "Genel Bakış"),
    ]
    
    grid_html = "".join(boxes)

    # --- 3. GÜNCELLENMİŞ HTML (ANA KART BOŞLUKLARI KISILDI: margin-top 8px, padding 8px, gap 6px) ---
    html_content = f"""
    <div class="info-card" style="border-top: 3px solid {title_col}; margin-top:8px; margin-bottom:10px; padding: 0;">
        <div class="info-header" style="display:flex; justify-content:space-between; align-items:center; color:{title_col}; font-size:1.05rem; padding:6px 10px; border-bottom:1px solid {header_border}; background: {header_bg}; margin-bottom:0;">
            <span style="font-weight:800;">🗺️ Teknik Yol Haritası</span>
            <span style="background: {badge_bg}; color: {badge_text}; padding: 2px 10px; border-radius: 4px; font-family: 'JetBrains Mono', monospace; font-weight: 800; font-size: 0.9rem; border: 1px solid {header_border};">{display_ticker} <span style="opacity:0.6; margin:0 4px; font-weight:400;">—</span> <span style="color:{price_color};">{display_price}</span></span>
        </div>
        
        <div style="padding: 8px;">
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px;">
                {grid_html}
            </div>
        </div>
    </div>
    <style>
    .dark-text-fix {{ color: inherit !important; }}
    </style>
    """
    st.markdown(html_content.replace('\n', ''), unsafe_allow_html=True)

#         
# ==============================================================================
# 5. SIDEBAR UI
# ==============================================================================
with st.sidebar:
    st.markdown(f"""<div style="font-size:1.5rem; font-weight:700; color:#1e3a8a; text-align:center; padding-top: 10px; padding-bottom: 10px;">SMART MONEY RADAR</div>""", unsafe_allow_html=True)
    
    # --- YENİ YERİ: GENEL SAĞLIK PANELİ (SIDEBAR İÇİN OPTİMİZE EDİLDİ) ---
    try:
        if "ticker" in st.session_state and st.session_state.ticker:
            master_score, score_pros, score_cons = calculate_master_score(st.session_state.ticker)

            st.markdown("<div style='text-align:center; font-weight:800; font-size:1rem; color:#38bdf8; margin-bottom:5px; margin-top:5px;'>GENEL SAĞLIK DURUMU</div>", unsafe_allow_html=True)

            # 1. HIZ GÖSTERGESİ (GAUGE)
            render_gauge_chart(master_score)

            # CSS: Özel ve İnce Kaydırma Çubuğu (Custom Scrollbar)
            custom_scrollbar_css = """
            <style>
            .custom-scroll::-webkit-scrollbar { width: 4px; }
            .custom-scroll::-webkit-scrollbar-track { background: transparent; }
            .custom-scroll::-webkit-scrollbar-thumb { background-color: rgba(0,0,0,0.15); border-radius: 10px; }
            .custom-scroll:hover::-webkit-scrollbar-thumb { background-color: rgba(0,0,0,0.3); }
            </style>
            """
            st.markdown(custom_scrollbar_css, unsafe_allow_html=True)

            # 2. POZİTİF ETKENLER
            pos_items_html = ""
            if score_pros:
                for p in score_pros:
                    color_line = "rgba(255,255,255,0.05)" if st.session_state.dark_mode else "rgba(22, 163, 74, 0.2)"
                    text_color = "#e2e8f0" if st.session_state.dark_mode else "#14532d"
                    pos_items_html += f"<div style='font-size:0.7rem; line-height:1.3; color:{text_color}; margin-bottom:3px; padding:3px 2px; border-bottom:1px solid {color_line};'>{p}</div>"
            else:
                text_color = "#94a3b8" if st.session_state.dark_mode else "#14532d"
                pos_items_html = f"<div style='font-size:0.7rem; color:{text_color}; padding:6px 2px;'>Belirgin pozitif etken yok.</div>"

            if st.session_state.dark_mode:
                st.markdown(f"""<div class="custom-scroll" style="margin-bottom:10px; background:rgba(17, 24, 39, 0.6); border:1px solid rgba(255,255,255,0.05); border-top:3px solid #10b981; border-radius:6px; padding:0; max-height:160px; overflow-y:auto; position:relative; box-shadow: 0 2px 4px rgba(0,0,0, 0.2);"><div style="font-weight:800; font-size:0.75rem; color:#10b981; background:transparent; padding:6px 10px; border-bottom:1px solid rgba(255,255,255,0.1); position:sticky; top:0; z-index:10; display:flex; justify-content:space-between; align-items:center; backdrop-filter: blur(4px);"><span>POZİTİF ETKENLER</span><span style="background-color:rgba(16, 185, 129, 0.15); color:#10b981; border: 1px solid rgba(16, 185, 129, 0.3); padding:2px 6px; border-radius:10px; font-size:0.65rem;">{len(score_pros)}</span></div><div style="padding:6px 10px;">{pos_items_html}</div></div>""", unsafe_allow_html=True)
            else:
                st.markdown(f"""<div class="custom-scroll" style="margin-bottom:10px; background-color:#f0fdf4; border:1px solid #16a34a; border-radius:6px; padding:0; max-height:160px; overflow-y:auto; position:relative; box-shadow: 0 2px 4px rgba(22, 163, 74, 0.1);"><div style="font-weight:800; font-size:0.75rem; color:#15803d; background-color:#dcfce7; padding:6px 10px; border-bottom:1px solid #16a34a; position:sticky; top:0; z-index:10; display:flex; justify-content:space-between; align-items:center;"><span>POZİTİF ETKENLER</span><span style="background-color:#16a34a; color:white; padding:2px 6px; border-radius:10px; font-size:0.65rem;">{len(score_pros)}</span></div><div style="padding:6px 10px;">{pos_items_html}</div></div>""", unsafe_allow_html=True)

            # 3. NEGATİF ETKENLER
            neg_items_html = ""
            if score_cons:
                for c in score_cons:
                    color_line = "rgba(255,255,255,0.05)" if st.session_state.dark_mode else "rgba(220, 38, 38, 0.2)"
                    text_color = "#e2e8f0" if st.session_state.dark_mode else "#7f1d1d"
                    neg_items_html += f"<div style='font-size:0.7rem; line-height:1.3; color:{text_color}; margin-bottom:3px; padding:3px 2px; border-bottom:1px solid {color_line};'>❌ {c}</div>"
            else:
                text_color = "#94a3b8" if st.session_state.dark_mode else "#7f1d1d"
                neg_items_html = f"<div style='font-size:0.7rem; color:{text_color}; padding:6px 2px;'>Belirgin negatif etken yok.</div>"

            if st.session_state.dark_mode:
                st.markdown(f"""<div class="custom-scroll" style="margin-bottom:10px; background:rgba(17, 24, 39, 0.6); border:1px solid rgba(255,255,255,0.05); border-top:3px solid #ef4444; border-radius:6px; padding:0; max-height:160px; overflow-y:auto; position:relative; box-shadow: 0 2px 4px rgba(0,0,0, 0.2);"><div style="font-weight:800; font-size:0.75rem; color:#ef4444; background:transparent; padding:6px 10px; border-bottom:1px solid rgba(255,255,255,0.1); position:sticky; top:0; z-index:10; display:flex; justify-content:space-between; align-items:center; backdrop-filter: blur(4px);"><span>NEGATİF ETKENLER</span><span style="background-color:rgba(239, 68, 68, 0.15); color:#ef4444; border: 1px solid rgba(239, 68, 68, 0.3); padding:2px 6px; border-radius:10px; font-size:0.65rem;">{len(score_cons)}</span></div><div style="padding:6px 10px;">{neg_items_html}</div></div>""", unsafe_allow_html=True)
            else:
                st.markdown(f"""<div class="custom-scroll" style="margin-bottom:10px; background-color:#fef2f2; border:1px solid #dc2626; border-radius:6px; padding:0; max-height:160px; overflow-y:auto; position:relative; box-shadow: 0 2px 4px rgba(220, 38, 38, 0.1);"><div style="font-weight:800; font-size:0.75rem; color:#b91c1c; background-color:#fee2e2; padding:6px 10px; border-bottom:1px solid #dc2626; position:sticky; top:0; z-index:10; display:flex; justify-content:space-between; align-items:center;"><span>NEGATİF ETKENLER</span><span style="background-color:#dc2626; color:white; padding:2px 6px; border-radius:10px; font-size:0.65rem;">{len(score_cons)}</span></div><div style="padding:6px 10px;">{neg_items_html}</div></div>""", unsafe_allow_html=True)

    except Exception as e:
        st.warning(f"Genel Sağlık tablosu oluşturulamadı. Hata: {e}")


    # --------------------------------------------------
    # --- CANLI SİNYAL PANELİ (ALERT) ---
    # --------------------------------------------------
    try:
        t_alert = st.session_state.ticker
        df_alert = get_safe_historical_data(t_alert)
        
        if df_alert is not None and not df_alert.empty:
            alert_items = []

            # 1. MASTER SCORE → KARAR
            if master_score >= 70:
                karar_icon = "🟢"; karar_txt = "AL"; karar_color = "#10b981" if st.session_state.dark_mode else "#15803d"
            elif master_score >= 45:
                karar_icon = "🟡"; karar_txt = "İZLE"; karar_color = "#f59e0b" if st.session_state.dark_mode else "#d97706"
            else:
                karar_icon = "🔴"; karar_txt = "UZAK DUR"; karar_color = "#ef4444" if st.session_state.dark_mode else "#dc2626"

            # 2. STP KESİŞİMİ
            stp_res = process_single_stock_stp(t_alert, df_alert)
            if stp_res:
                if stp_res['type'] == 'cross_up':
                    alert_items.append(("⚡", "STP Yukarı Kesişim", "#10b981" if st.session_state.dark_mode else "#15803d"))
                elif stp_res['type'] == 'cross_down':
                    alert_items.append(("🔻", "STP Aşağı Kesişim", "#ef4444" if st.session_state.dark_mode else "#dc2626"))
                elif stp_res['type'] == 'trend_up':
                    gun = stp_res['data'].get('Gun', '?')
                    alert_items.append(("📈", f"STP Trend ({gun} gün)", "#38bdf8" if st.session_state.dark_mode else "#0369a1"))
                elif stp_res['type'] == 'trend_down':
                    gun = stp_res['data'].get('Gun', '?')
                    alert_items.append(("📉", f"STP Düşüş Trendi ({gun} gün)", "#f87171" if st.session_state.dark_mode else "#b91c1c"))

            # 3. BEAR TRAP
            bt_res = process_single_bear_trap_live(df_alert)
            if bt_res:
                alert_items.append(("🪤", f"Bear Trap ({bt_res['Zaman']})", "#a78bfa" if st.session_state.dark_mode else "#7c3aed"))

            # 4. FORMASYON
            try:
                pat_df = scan_chart_patterns([t_alert])
                if not pat_df.empty:
                    pat_name = pat_df.iloc[0]['Formasyon'].split('(')[0].strip()
                    pat_score = pat_df.iloc[0]['Skor']
                    alert_items.append(("📐", f"{pat_name} ({pat_score})", "#fbbf24" if st.session_state.dark_mode else "#d97706"))
            except: pass

            # 5. AKILLI PARA
            bench_cat = st.session_state.get('category', 'BIST')
            bench_s = get_benchmark_data(bench_cat)
            acc_res = process_single_accumulation(t_alert, df_alert, bench_s)
            if acc_res:
                is_pp = acc_res.get('Pocket_Pivot', False)
                alert_items.append(("⚡" if is_pp else "🤫", "Pocket Pivot" if is_pp else "Sessiz Toplama", "#c084fc" if st.session_state.dark_mode else "#7c3aed"))

            # 6. ROYAL FLUSH / KESİN DÖNÜŞ
            rf3_res = calculate_royal_flush_3_0_setup(t_alert, df_alert)
            if rf3_res:
                alert_items.append(("🩸", f"Royal Flush 3.0 (Z:{rf3_res['Z-Score']})", "#f87171" if st.session_state.dark_mode else "#b91c1c"))

            kd_alert = process_single_kesin_donus(t_alert, df_alert, bench_s)
            if kd_alert:
                alert_items.append(("🎯", "Kesin Dönüş (3'lü Kesişim)", "#34d399" if st.session_state.dark_mode else "#059669"))

            # 7. BREAKOUT
            bo_res = process_single_breakout(t_alert, df_alert)
            if bo_res:
                prox = str(bo_res.get('Zirveye Yakınlık', '')).split('<')[0].strip()
                is_fired = "TETİKLENDİ" in prox or "Sıkışma" in prox
                alert_items.append(("🔨" if is_fired else "🔥", f"Breakout: {prox}", "#4ade80" if st.session_state.dark_mode else "#15803d"))

            # 8. RS MOMENTUM
            try:
                bench_close = bench_s if bench_s is not None else None
                if bench_close is not None:
                    stock_5d = (float(df_alert['Close'].iloc[-1]) / float(df_alert['Close'].iloc[-6]) - 1) * 100
                    bench_5d_val = (float(bench_close.iloc[-1]) / float(bench_close.iloc[-6]) - 1) * 100
                    alpha = stock_5d - bench_5d_val
                    if alpha > 2:
                        alert_items.append(("🏆", f"RS Lider (+%{alpha:.1f})", "#10b981" if st.session_state.dark_mode else "#15803d"))
                    elif alpha < -2:
                        alert_items.append(("🐢", f"RS Zayıf (%{alpha:.1f})", "#ef4444" if st.session_state.dark_mode else "#dc2626"))
            except: pass

            # 9. HARSI
            harsi = calculate_harsi(df_alert)
            if harsi:
                if harsi['is_green']:
                    alert_items.append(("🌊", "HARSI: Boğa Momentumu", "#38bdf8" if st.session_state.dark_mode else "#0369a1"))
                else:
                    alert_items.append(("🌊", "HARSI: Ayı Momentumu", "#f87171" if st.session_state.dark_mode else "#b91c1c"))

            # 10. LORENTZIAN
            lor = calculate_lorentzian_classification(t_alert)
            if lor and lor['votes'] >= 7:
                lor_color = "#10b981" if lor['signal'] == "YÜKSELİŞ" else "#ef4444"
                if not st.session_state.dark_mode:
                    lor_color = "#15803d" if lor['signal'] == "YÜKSELİŞ" else "#dc2626"
                alert_items.append(("🧠", f"Lorentzian: {lor['signal']} %{int(lor['prob'])}", lor_color))
            
            # 11. OBV UYUMSUZLUK (Gizli Para Girişi / Dağıtım)
            obv_title, obv_color, obv_desc = get_obv_divergence_status(t_alert)
            if "ZAYIF" not in obv_title and "Veri Yok" not in obv_title and "Hesaplanamadı" not in obv_title:
                alert_items.append(("📊", f"OBV: {obv_title}", obv_color))

            # 12. ICT SNIPER KURULUMU (Akıllı Para Yönü)
            ict_res = process_single_ict_setup(t_alert, df_alert)
            if ict_res:
                alert_items.append((ict_res['İkon'], f"ICT Sniper: {ict_res['Yön']} ({ict_res['Durum'].split('|')[0].strip()})", ict_res['Renk']))

            # --------------------------------------------------
            # ALTIN FIRSAT (GOLDEN TRIO) KONTROLÜ
            # --------------------------------------------------
            try:
                ict_data_check = calculate_ict_deep_analysis(t_alert)
                sent_data_check = calculate_sentiment_score(t_alert)
                
                if ict_data_check and sent_data_check:
                    # 1. GÜÇ KONTROLÜ
                    rs_text = sent_data_check.get('rs', '').lower()
                    cond_power = ("artıda" in rs_text or "lider" in rs_text or "pozitif" in rs_text or sent_data_check.get('total', 0) >= 50 or sent_data_check.get('raw_rsi', 0) > 50)
                    
                    # 2. KONUM KONTROLÜ (Ucuzluk veya Kırılım)
                    cond_loc = "DISCOUNT" in ict_data_check.get('zone', '') or "MSS" in ict_data_check.get('structure', '') or "BOS" in ict_data_check.get('structure', '')
                    
                    # 3. ENERJİ KONTROLÜ (Hacim ve Momentum)
                    cond_energy = ("Güçlü" in ict_data_check.get('displacement', '') or "Hacim" in sent_data_check.get('vol', '') or sent_data_check.get('raw_rsi', 0) > 55)
                    
                    # 3'te 3 Onay varsa Sinyal Paneline Ekle
                    if cond_power and cond_loc and cond_energy:
                        altin_renk = "#ca8a04" if st.session_state.dark_mode else "#a16207"
                        alert_items.append(("🏆", "Altın Fırsat (Güç+Konum+Enerji)", altin_renk))
            except Exception:
                pass # Veri okunamadığında panelin çökmesini engeller

            # --------------------------------------------------
            # ROYAL FLUSH (4/4 KRALİYET SET-UP) KONTROLÜ
            # --------------------------------------------------
            try:
                ict_rf = calculate_ict_deep_analysis(t_alert)
                sent_rf = calculate_sentiment_score(t_alert)

                if ict_rf and sent_rf and lor:
                    # 1. Yapı: BOS veya MSS Bullish
                    c_struct = ("BOS (Yükseliş" in ict_rf.get('structure', '') or
                                "MSS" in ict_rf.get('structure', ''))
                    # 2. Zeka: Lorentzian ≥7 YÜKSELİŞ
                    c_ai = (lor['signal'] == "YÜKSELİŞ" and lor['votes'] >= 7)
                    # 3. Güç: RS pozitif
                    rs_txt = sent_rf.get('rs', '').lower()
                    c_rs = ("artıda" in rs_txt or "lider" in rs_txt or
                            "pozitif" in rs_txt or sent_rf.get('total', 0) >= 50)
                    # 4. Maliyet: VWAP sapması <%12
                    try:
                        vwap_s = VolumeWeightedAveragePrice(
                            high=df_alert['High'], low=df_alert['Low'],
                            close=df_alert['Close'], volume=df_alert['Volume'], window=14
                        )
                        vwap_val = float(vwap_s.volume_weighted_average_price().iloc[-1])
                        vwap_diff = abs((float(df_alert['Close'].iloc[-1]) - vwap_val) / vwap_val * 100)
                        c_vwap = vwap_diff < 12
                    except:
                        c_vwap = True

                    if c_struct and c_ai and c_rs and c_vwap:
                        rf_renk = "#a78bfa" if st.session_state.dark_mode else "#6d28d9"
                        alert_items.append(("♠️", "Royal Flush (4/4 Kraliyet Set-Up)", rf_renk))
            except Exception:
                pass

            # --- RENDER ---
            # Karara göre panel renkleri
            if master_score >= 70:
                bg_panel   = "rgba(5,46,22,0.5)"   if st.session_state.dark_mode else "#f0fdf4"
                border_col = "#16a34a"
                title_col  = "#4ade80"              if st.session_state.dark_mode else "#15803d"
            elif master_score >= 45:
                bg_panel   = "rgba(69,26,3,0.5)"    if st.session_state.dark_mode else "#fffbeb"
                border_col = "#d97706"
                title_col  = "#fbbf24"              if st.session_state.dark_mode else "#92400e"
            else:
                bg_panel   = "rgba(69,10,10,0.5)"   if st.session_state.dark_mode else "#fef2f2"
                border_col = "#dc2626"
                title_col  = "#f87171"              if st.session_state.dark_mode else "#991b1b"

            border_panel = "rgba(255,255,255,0.06)" if st.session_state.dark_mode else f"{border_col}30"

            # ---------------------------------------------------------
            # RENKLERE GÖRE SIRALAMA (Yeşiller üste, Kırmızılar alta)
            # ---------------------------------------------------------
            def get_signal_priority(item):
                # item formatı: (icon, text, color)
                color_str = str(item[2]).lower()
                text_str = str(item[1]).lower()
                
                # 1. Öncelik: Yeşiller (Zirveye)
                if any(c in color_str for c in ['10b981', '22c55e', '16a34a', '4ade80', '15803d', '059669', 'green']):
                    return 1
                
                # 2. Öncelik: Sarı/Turuncu/Altın (Araya)
                if any(c in color_str for c in ['f59e0b', 'ca8a04', 'eab308', 'd97706', 'f97316', 'b45309', 'orange', 'yellow']):
                    return 2
                
                # 4. Öncelik: Kırmızı/Bordo (En Alta)
                if any(c in color_str for c in ['ef4444', 'dc2626', 'f87171', '991b1b', 'e11d48', 'b91c1c', 'red']) or any(k in text_str for k in ['ayı', 'düşüş', 'zayıf', 'satış']):
                    return 4
                
                # 3. Öncelik: Mor, Mavi gibi diğer nötr/bilgi renkleri (Ortanın altına)
                return 3

            # Listeyi ağırlık (priority) değerine göre küçükten büyüğe sıralar
            alert_items.sort(key=get_signal_priority)

            items_html = ""
            for icon, text, color in alert_items:
                # padding:0px; ve line-height:1.1; eklenerek satırın dikey yüksekliği tamamen daraltıldı
                items_html += f"<div style='display:flex;align-items:center;gap:6px;padding:1px;border-bottom:1px solid {border_panel};'><span style='font-size:0.85rem;line-height:1;'>{icon}</span><span style='font-size:0.72rem;color:{color};font-weight:600;line-height:1.2;'>{text}</span></div>"
            if not items_html:
                items_html = f"<div style='font-size:0.72rem;color:#64748b;padding:4px 0;'>Aktif sinyal yok.</div>"

            # ANA KUTU: padding, margin ve başlık altı boşlukları (padding-bottom, margin-bottom) tıraşlandı.
            st.markdown(f"""
            <div style="background:{bg_panel};border:2px solid {border_col};border-radius:6px;padding:6px 8px;margin-bottom:6px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;border-bottom:1px solid {border_panel};padding-bottom:4px;">
                    <span style="font-size:0.75rem;font-weight:800;color:{title_col};">🔔 CANLI SİNYALLER</span>
                    <span style="font-size:0.7rem;font-weight:900;color:{karar_color};background:{karar_color}20;padding:1px 6px;border-radius:6px;border:1px solid {karar_color};white-space:nowrap;">{karar_icon} {karar_txt}</span>
                </div>
                <div>{items_html}</div>
            </div>
            """, unsafe_allow_html=True)
    except Exception:
        pass

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

    # LORENTZİAN PANELİ 
    st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
    render_lorentzian_panel(st.session_state.ticker)
    st.divider()
    # MINERVINI PANELİ (Hatasız Versiyon)
    render_minervini_panel_v2(st.session_state.ticker)
   
    # --- YILDIZ ADAYLARI (KESİŞİM PANELİ) ---
    st.markdown(f"""
    <div style="background: linear-gradient(45deg, #06b6d4, #3b82f6); color: white; padding: 12px 8px; border-radius: 6px; text-align: center; margin-bottom: 10px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
        <div style="font-weight: 800; font-size: 1.05rem; letter-spacing: 0.5px; margin-bottom: 5px;">🌟 YILDIZ ADAYLARI</div>
        <div style="font-size: 0.75rem; font-weight: 400; opacity: 0.9; line-height: 1.3;">
            Son 5 gündür Endeksten güçlü, 45 günlük yatay direnci hacimle kırdı ya da kırmak üzere, RSI<70
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Kesişim Mantığı
    stars_found = False
    
    # Scroll Alanı Başlatıyoruz
    with st.container(height=350):
        
        # Verilerin varlığını kontrol et
        has_accum = st.session_state.accum_data is not None and not st.session_state.accum_data.empty
        has_warm = st.session_state.breakout_left is not None and not st.session_state.breakout_left.empty
        has_break = st.session_state.breakout_right is not None and not st.session_state.breakout_right.empty
        
        if has_accum:
            # Akıllı Para listesindeki sembolleri ve verileri al
            acc_df = st.session_state.accum_data
            acc_symbols = set(acc_df['Sembol'].values)
            
            # ------------------------------------------------------------------
            # SENARYO 1: 🚀 ROKET MODU (RS Lideri + [Kıran VEYA Isınan])
            # ------------------------------------------------------------------
            
            has_rs = st.session_state.rs_leaders_data is not None and not st.session_state.rs_leaders_data.empty
            
            # Hem Kıranlara (Right) hem Isınanlara (Left) bakıyoruz
            has_break_right = st.session_state.breakout_right is not None and not st.session_state.breakout_right.empty
            has_break_left = st.session_state.breakout_left is not None and not st.session_state.breakout_left.empty

            if has_rs and (has_break_right or has_break_left):
                rs_df = st.session_state.rs_leaders_data
                rs_symbols = set(rs_df['Sembol'].values)
                
                # İki listeyi (Kıranlar + Isınanlar) birleştiriyoruz
                bo_symbols = set()
                bo_data_map = {} # Detayları saklamak için

                # 1. Kıranları Ekle (Öncelikli)
                if has_break_right:
                    df_r = st.session_state.breakout_right
                    for _, row in df_r.iterrows():
                        sym = row['Sembol']
                        bo_symbols.add(sym)
                        bo_data_map[sym] = {'status': 'KIRDI 🔨', 'info': row['Hacim_Kati']}

                # 2. Isınanları Ekle
                if has_break_left:
                    df_l = st.session_state.breakout_left
                    for _, row in df_l.iterrows():
                        # Sütun adı bazen Sembol_Raw bazen Sembol olabiliyor, kontrol et
                        sym = row.get('Sembol_Raw', row.get('Sembol'))
                        if sym:
                            bo_symbols.add(sym)
                            # Eğer zaten Kıranlarda yoksa, Isınan olarak ekle
                            if sym not in bo_data_map:
                                # Zirveye yakınlık bilgisini temizle
                                prox = str(row.get('Zirveye Yakınlık', '')).split('<')[0].strip()
                                bo_data_map[sym] = {'status': 'ISINIYOR', 'info': prox}

                # KESİŞİM BUL (RS Lideri + [Kıran veya Isınan])
                rocket_stars = rs_symbols.intersection(bo_symbols)

                if rocket_stars:
                    rocket_list = []
                    for sym in rocket_stars:
                        row_rs = rs_df[rs_df['Sembol'] == sym].iloc[0]
                        bo_info = bo_data_map.get(sym, {'status': '?', 'info': ''})
                        
                        rocket_list.append({
                            'sym': sym, 
                            'price': row_rs['Fiyat'], 
                            'alpha': row_rs.get('Alpha_5D', row_rs.get('Adj_Alpha_5D', 0)),
                            'status': bo_info['status'],
                            'info': bo_info['info'],
                            'score': row_rs['Skor']
                        })
                    
                    # Puana göre sırala
                    rocket_list.sort(key=lambda x: x['score'], reverse=True)

                    for item in rocket_list:
                        stars_found = True
                        sym = item['sym']
                        # Etiket: 💎 THYAO | Alpha:+%5.2 | KIRDI 🔨 (3.5x)
                        # Etiket: 💎 ASELS | Alpha:+%3.1 | ISINIYOR 🔥 (%98)
                        label = f"💎 {sym.replace('.IS', '')} | Alpha:+%{item['alpha']:.1f} | {item['status']}"
                        
                        if st.button(label, key=f"star_rocket_hybrid_{sym}", use_container_width=True):
                            on_scan_result_click(sym)
                            st.rerun()
                            
            # --- 2. SENARYO: HAREKET (Kıranlar + Akıllı Para) ---
            if has_break:
                bo_df = st.session_state.breakout_right
                bo_symbols = set(bo_df['Sembol'].values)
                
                # Kesişim Bul
                move_stars_symbols = acc_symbols.intersection(bo_symbols)
                
                if move_stars_symbols:
                    # Kesişenleri Hacime Göre Sıralamak İçin Liste Oluştur
                    move_star_list = []
                    for sym in move_stars_symbols:
                        # Veriyi accum_data'dan çek (Hacim orada var)
                        row = acc_df[acc_df['Sembol'] == sym].iloc[0]
                        vol = row.get('Hacim', 0)
                        price = row['Fiyat']
                        move_star_list.append({'sym': sym, 'price': price, 'vol': vol})
                    
                    # SIRALAMA: Hacme Göre Büyükten Küçüğe
                    move_star_list.sort(key=lambda x: x['vol'], reverse=True)
                    
                    for item in move_star_list:
                        stars_found = True
                        sym = item['sym']
                        label = f"🚀 {sym.replace('.IS', '')} ({item['price']}) | HAREKET"
                        if st.button(label, key=f"star_mov_{sym}", use_container_width=True):
                            on_scan_result_click(sym)
                            st.rerun()

            # --- 3. SENARYO: HAZIRLIK (Isınanlar + Akıllı Para) ---
            if has_warm:
                warm_df = st.session_state.breakout_left
                col_name = 'Sembol_Raw' if 'Sembol_Raw' in warm_df.columns else 'Sembol'
                warm_symbols = set(warm_df[col_name].values)
                
                # Kesişim Bul
                prep_stars_symbols = acc_symbols.intersection(warm_symbols)
                
                if prep_stars_symbols:
                    # Kesişenleri Hacime Göre Sıralamak İçin Liste Oluştur
                    prep_star_list = []
                    for sym in prep_stars_symbols:
                        # Veriyi accum_data'dan çek
                        row = acc_df[acc_df['Sembol'] == sym].iloc[0]
                        vol = row.get('Hacim', 0)
                        price = row['Fiyat']
                        prep_star_list.append({'sym': sym, 'price': price, 'vol': vol})
                    
                    # SIRALAMA: Hacme Göre Büyükten Küçüğe
                    prep_star_list.sort(key=lambda x: x['vol'], reverse=True)

                    for item in prep_star_list:
                        stars_found = True
                        sym = item['sym']
                        label = f"⏳ {sym.replace('.IS', '')} ({item['price']}) | HAZIRLIK"
                        if st.button(label, key=f"star_prep_{sym}", use_container_width=True):
                            on_scan_result_click(sym)
                            st.rerun()
        if not stars_found:
            if not has_accum:
                st.caption("💎'Endeksi Yenen Güçlü Hisseler / Breakout Ajanı' ve ⏳'Akıllı Para Topluyor / Breakout Ajanı' taramalarının ortak sonuçları gösterilir.")
            elif not (has_warm or has_break):
                st.caption("💎'Endeksi Yenen Güçlü Hisseler / Breakout Ajanı' ve ⏳'Akıllı Para Topluyor / Breakout Ajanı' taramalarının ortak sonuçları gösterilir.")
            else:
                st.warning("Şu an toplanan ORTAK bir hisse yok.")
    # ==============================================================================
    # ⚓ DİPTEN DÖNÜŞ PANELİ (Sidebar'a Taşındı)
    # ==============================================================================
    
    # --- HATAYI ÖNLEYEN BAŞLATMA KODLARI (EKLEME) ---
    if 'bear_trap_data' not in st.session_state: st.session_state.bear_trap_data = None
    if 'rsi_div_bull' not in st.session_state: st.session_state.rsi_div_bull = None
    # -----------------------------------------------

    # 1. Veri Kontrolü
    has_bt = st.session_state.bear_trap_data is not None and not st.session_state.bear_trap_data.empty
    has_div = st.session_state.rsi_div_bull is not None and not st.session_state.rsi_div_bull.empty
    
    reversal_list = []
    
    # 2. Kesişim Mantığı
    if has_bt and has_div:
        bt_df = st.session_state.bear_trap_data
        div_df = st.session_state.rsi_div_bull
        
        # Sembol Kümeleri
        bt_syms = set(bt_df['Sembol'].values)
        div_syms = set(div_df['Sembol'].values)
        
        # Ortak Olanlar (Kesişim)
        common_syms = bt_syms.intersection(div_syms)
        
        for sym in common_syms:
            # Verileri al
            row_bt = bt_df[bt_df['Sembol'] == sym].iloc[0]
            row_div = div_df[div_df['Sembol'] == sym].iloc[0]
            
            reversal_list.append({
                'Sembol': sym,
                'Fiyat': row_bt['Fiyat'],
                'Zaman': row_bt['Zaman'],       # Örn: 2 Mum Önce
                'RSI': int(row_div['RSI']) # Örn: 28
            })
            
    # 3. DİPTEN DÖNÜŞ PANELİ)
    st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)

    st.markdown(f"""
    <div style="background: linear-gradient(45deg, #06b6d4, #3b82f6); color: white; padding: 8px; border-radius: 6px; text-align: center; font-weight: 700; font-size: 0.9rem; margin-bottom: 10px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
        ⚓ DİPTEN DÖNÜŞ?
    </div>
    """, unsafe_allow_html=True)
    with st.container(height=150):
        if reversal_list:
            # RSI değerine göre (Düşük RSI en üstte) sıralayalım
            reversal_list.sort(key=lambda x: x['RSI']) 
            
            for item in reversal_list:
                # Buton Etiketi: 💎 GARAN (150.20) | RSI:28 | 2 Mum Önce
                label = f"💎 {item['Sembol'].replace('.IS', '')} ({item['Fiyat']:.2f}) | RSI:{item['RSI']} | {item['Zaman']}"
                
                if st.button(label, key=f"rev_btn_sidebar_{item['Sembol']}", use_container_width=True):
                    on_scan_result_click(item['Sembol'])
                    st.rerun()
        else:
            if not (has_bt and has_div):
                st.caption("'Ayı Tuzağı' ve 'RSI Uyumsuzluk' taramalarının ortak sonuçları burada gösterilir.")
            else:
                st.info("Şu an hem tuzağa düşürüp hem uyumsuzluk veren (Kesişim) hisse yok.")
    

    # -----------------------------------------------------------------------------
    # 🏆 ALTIN FIRSAT & ♠️ ROYAL FLUSH (SÜPER TARAMA MOTORU)
    # -----------------------------------------------------------------------------
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
        royal_candidates = [] # YENİ: Royal Flush adayları

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
            return pd.DataFrame(), pd.DataFrame()

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

                # 1. Kırmızı Mum İptali (Bugün Kapanış < Açılış ise direkt ele)
                if today_c < today_o:
                    continue

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
                
                # --- KRİTER 1: GÜÇ (RS) - GÜNCELLENDİ (10 GÜN) ---
                is_powerful = False
                # DİKKAT: 20 yerine 10 yaptık. TRHOL gibi yeni uyananları yakalar.
                prev_price_rs = df['Close'].iloc[-10] 

                if index_close is not None and len(index_close) > 10:
                    stock_ret = (current_price / prev_price_rs) - 1
                    index_ret = (index_close.iloc[-1] / index_close.iloc[-10]) - 1
                    if stock_ret > index_ret: is_powerful = True
                else:
                    # Endeks yoksa RSI > 55 (Biraz gevşettik)
                    rsi_val = calc_rsi_manual(df['Close']).iloc[-1]
                    if rsi_val > 55: is_powerful = True

                # --- KRİTER 2: KONUM (3 AYLIK DÜZELTME) ---
                high_60 = df['High'].rolling(60).max().iloc[-1]
                low_60 = df['Low'].rolling(60).min().iloc[-1]
                range_diff = high_60 - low_60
                
                is_discount = False
                if range_diff > 0:
                    # Fiyat 3 aylık bandın neresinde?
                    loc_ratio = (current_price - low_60) / range_diff
                    
                    # 3 aylık bandın alt %50'sindeyse kabul et
                    if loc_ratio < 0.5: 
                        is_discount = True

                # --- KRİTER 3: ENERJİ (HACİM / MOMENTUM) - GÜNCELLENDİ ---
                vol_sma20 = df['Volume'].rolling(20).mean().iloc[-1]
                current_vol = df['Volume'].iloc[-1]
                rsi_now = calc_rsi_manual(df['Close']).iloc[-1]
                
                # Hacim barajını %10'dan %5'e çektik (1.1 -> 1.05)
                is_energy = (current_vol > vol_sma20 * 1.05) or (rsi_now > 55)

                # === ANA FİLTRE: ALTIN FIRSAT ===
                if is_powerful and is_discount and is_energy:
                    
                    # Piyasa Değeri
                    try:
                        info = yf.Ticker(ticker).info
                        mcap = info.get('marketCap', 0)
                    except:
                        mcap = 0

                    # 1. ALTIN LİSTEYE EKLE
                    golden_candidates.append({
                        "Hisse": ticker,
                        "Fiyat": current_price,
                        "M.Cap": mcap,
                        "Onay": "🏆 RS Gücü + Ucuz Konum + Güçlü Enerji",
                        "Warning": has_warning
                    })

                    # === İKİNCİ FİLTRE: ROYAL FLUSH (ELİT) KONTROLÜ ===
                    # Sadece Altın olanlara bakıyoruz

                    # Royal Şart 1: Uzun Vade Trend (SMA200 Üzerinde mi?)
                    sma200 = df['Close'].rolling(200).mean().iloc[-1]
                    is_bull_trend = current_price > sma200

                    # Royal Şart 2: Maliyet/Trend (SMA50 Üzerinde mi?)
                    sma50 = df['Close'].rolling(50).mean().iloc[-1]
                    is_structure_solid = current_price > sma50

                    # Royal Şart 3: RSI Güvenli Bölge (Aşırı şişmemiş)
                    is_safe_entry = rsi_now < 70

                    if is_bull_trend and is_structure_solid and is_safe_entry:
                        # 2. ROYAL LİSTEYE DE EKLE
                        royal_candidates.append({
                            "Hisse": ticker,
                            "Fiyat": current_price,
                            "M.Cap": mcap,
                            "Onay": "♠️ 4/4 KRALİYET: Trend(200) + Yapı(50) + RS + Enerji",
                            "Warning": has_warning
                        })

            except:
                continue

            if i % 10 == 0 and total_tickers > 0:
                prog = int((i / total_tickers) * 100)
                my_bar.progress(40 + int(prog/2), text=f"⚡ Analiz: {ticker}...")

        my_bar.progress(100, text="✅ Tarama Tamamlandı! Listeleniyor...")
        time.sleep(0.3)
        my_bar.empty()

        return pd.DataFrame(golden_candidates), pd.DataFrame(royal_candidates)
    
# ==============================================================================
# 6. ANA SAYFA (MAIN UI) - GÜNCELLENMİŞ MASTER SCAN VERSİYONU
# ==============================================================================

# Üst Menü Düzeni: Kategori | Varlık Listesi | DEV TARAMA BUTONU | TEMA BUTONU
col_theme, col_cat, col_ass, col_btn = st.columns([0.5, 1.5, 1.5, 1])

# 1. TEMA DEĞİŞTİRME BUTONU (YENİ)
with col_theme:
    mode_text = "🌙 Karanlık Mod" if not st.session_state.dark_mode else "☀️ Aydınlık Mod"
    if st.button(mode_text, use_container_width=True):
        st.session_state.dark_mode = not st.session_state.dark_mode
        st.rerun()

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
    st.selectbox("Varlık Listesi", current_opts, index=asset_idx, key="selected_asset_key", on_change=on_asset_change, label_visibility="collapsed", format_func=lambda x: x.replace(".IS", ""))

# 4. MASTER SCAN BUTONU (Eski arama kutusu yerine geldi)
with col_btn:
    # Butona basıldığında çalışacak sihirli kod
    if st.button("🕵️ TÜM PİYASAYI TARA (MASTER SCAN)", type="primary", use_container_width=True):
        
        # --- A. HAZIRLIK ---
        st.toast("Ajanlar göreve çağrılıyor...", icon="🕵️")
        scan_list = ASSET_GROUPS.get(st.session_state.category, [])
        
        # İlerleme Çubuğu ve Bilgi Mesajı
        progress_text = "Operasyon Başlıyor..."
        my_bar = st.progress(0, text=progress_text)
        
        try:
            # 1. ÖNCE VERİYİ ÇEK (Yahoo Koruması) - %10
            # En geniş veriyi (2y) bir kez çağırıyoruz ki önbelleğe (cache) girsin.
            # Diğer ajanlar cache'den okuyacağı için Yahoo'ya tekrar gitmeyecekler.
            my_bar.progress(10, text="📡 Veriler İndiriliyor (Batch Download)...%10")
            get_batch_data_cached(scan_list, period="2y")
            
            # 2. STP & MOMENTUM AJANI - %15
            my_bar.progress(15, text="⚡ STP ve Momentum Taranıyor...%15")
            crosses, trends, filtered = scan_stp_signals(scan_list)
            st.session_state.stp_crosses = crosses
            st.session_state.stp_trends = trends
            st.session_state.stp_filtered = filtered
            st.session_state.stp_scanned = True

            # 3. ICT SNIPER AJANI --- %20
            my_bar.progress(20, text="🦅 ICT Sniper Kurulumları (Liquidity+MSS+FVG) Taranıyor...%20")
            st.session_state.ict_scan_data = scan_ict_batch(scan_list)
            
            # 4. ALTIN FIRSATLAR VE KLASİK ROYAL FLUSH - %30
            my_bar.progress(30, text="💎 Altın Fırsatlar ve Royal Flush Taranıyor...%30")
            df_golden, df_royal = get_golden_trio_batch_scan(scan_list)
            if not df_golden.empty:
                st.session_state.golden_results = df_golden.sort_values(by="M.Cap", ascending=False).reset_index(drop=True)
            else:
                st.session_state.golden_results = pd.DataFrame()
            if not df_royal.empty:
                st.session_state.royal_results = df_royal.sort_values(by="M.Cap", ascending=False).reset_index(drop=True)
            else:
                st.session_state.royal_results = pd.DataFrame()

            # 5. PATLAMA ADAYLARI / GRANDMASTER - %35
            my_bar.progress(35, text="🚀 Grandmaster Patlama Adayları Taranıyor...%35")
            st.session_state.gm_results = scan_grandmaster_batch(scan_list)

            # 6. SENTIMENT (AKILLI PARA) AJANI - %40
            my_bar.progress(40, text="🤫 Gizli Toplama (Smart Money) Aranıyor...%40")
            st.session_state.accum_data = scan_hidden_accumulation(scan_list)
            
            # 7. RS LİDERLERİ TARAMASI - %45
            my_bar.progress(45, text="🏆 Son 5 günün Piyasa Liderleri (RS Momentum) Hesaplanıyor...%45")
            st.session_state.rs_leaders_data = scan_rs_momentum_leaders(scan_list)
            
            # 8. BREAKOUT AJANI (ISINANLAR/KIRANLAR) - %55
            my_bar.progress(55, text="🔨 Kırılımlar ve Hazırlıklar Kontrol Ediliyor...%55")
            st.session_state.breakout_left = agent3_breakout_scan(scan_list)      # Isınanlar
            st.session_state.breakout_right = scan_confirmed_breakouts(scan_list, st.session_state.get('category', 'S&P 500')) # Kıranlar
            
            # 9. RADAR 1 & RADAR 2 (GENEL TEKNİK) - %65
            my_bar.progress(65, text="🧠 Radar Sinyalleri İşleniyor...%65")
            st.session_state.scan_data = analyze_market_intelligence(scan_list, st.session_state.get('category', 'S&P 500'))
            st.session_state.radar2_data = radar2_scan(scan_list)
            
            # 10. FORMASYON & TUZAKLAR - %75
            my_bar.progress(75, text="🦁Formasyon ve Tuzaklar Taranıyor...%75")
            st.session_state.pattern_data = scan_chart_patterns(scan_list)
            st.session_state.bear_trap_data = scan_bear_traps(scan_list)

            # 11. ALTIN FIRSATLAR ve FORMASYONLAR - %80
            my_bar.progress(80, text="💎 Altın Fırsatlar ve Formasyonlar Taranıyor...%80")         
            st.session_state.af_scan_data = scan_golden_pattern_agent(scan_list, st.session_state.get('category', 'S&P 500'))
            st.session_state.golden_pattern_data = st.session_state.af_scan_data

            # 12. MİNERVİNİ SEPA AJANI - %90
            my_bar.progress(90, text="🦁 Minervini Sepa Taranıyor...%90")
            st.session_state.minervini_data = scan_minervini_batch(scan_list)

            # 13. ROYAL FLUSH 3.0 AJANI - %95
            my_bar.progress(95, text="🩸 Royal Flush 3.0 (Kusursuz Dipten Dönüş) Aranıyor...%95")
            st.session_state.rf3_scan_data = scan_rf3_batch(scan_list)
            
            # 14. KESİN DÖNÜŞ SİNYALLERİ - %97
            my_bar.progress(97, text="🎯 Kesin Dönüşler (Tuzak+Uyumsuzluk+Hacim) Aranıyor...%97")
            st.session_state.kesin_donus_data = scan_kesin_donus_batch(scan_list)
            
            # --- TOP 20 + CONFLUENCE - %99
            my_bar.progress(99, text="🏆 TOP 20 & Confluence Hesaplanıyor...%99")
            st.session_state.top_20_summary  = compile_top_20_summary()
            st.session_state.confluence_hits = compile_confluence_hits()

            # --- BİTİŞ ---
            my_bar.progress(100, text="✅ TARAMA TAMAMLANDI! Sonuçlar Yükleniyor...%100")
            st.session_state.generate_prompt = False # Eski prompt varsa temizle
            st.rerun() # Sayfayı yenile ki tablolar dolsun
            
        except Exception as e:
            st.error(f"Tarama sırasında bir hata oluştu: {str(e)}")
            st.stop()

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

    # --- YENİ: 8 MADDELİK TEKNİK HARİTASINI AI İÇİN TEMİZLE (HTML'den Arındır) ---
    roadmap_data_ai = calculate_8_point_roadmap(t)
    roadmap_ai_txt = "Veri Yok"
    
    if roadmap_data_ai:
        import re
        def clean_html(raw_html):
            # <b>, <br> gibi tüm HTML etiketlerini silip boşluk bırakır
            cleanr = re.compile('<.*?>')
            return re.sub(cleanr, ' ', str(raw_html)).strip()
            
        # DİKKAT: İsimler yeni hibrit yapıya göre senkronize edildi!
        roadmap_ai_txt = f"""
        1) Fiyat Davranışı ve Yapı: {clean_html(roadmap_data_ai['M1'])}
        2) Formasyon Tespiti: {clean_html(roadmap_data_ai['M2'])}
        3) Efor vs Sonuç (VSA): {clean_html(roadmap_data_ai['M3'])}
        4) Trend Skoru ve Enerji: {clean_html(roadmap_data_ai['M4'])}
        5) Hacim ve Akıllı Para İzi: {clean_html(roadmap_data_ai['M5'])}
        6) Yön Beklentisi ve Momentum: {clean_html(roadmap_data_ai['M6'])}
        7) Yol Haritası (Senaryolar): {clean_html(roadmap_data_ai['M7'])}
        8) Okuma Özeti: {clean_html(roadmap_data_ai['M8'])}
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
    lorentzian_bilgisi = render_lorentzian_panel(t, just_text=True)
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
    is_golden = "🚀 EVET (3/3 Onaylı - KRİTİK FIRSAT)" if (c_pwr and c_loc and c_nrg) else "HAYIR"

    # --- ROYAL FLUSH DURUMU HESAPLAMA (4/4 Kesişim) ---
    # 1. Yapı: BOS veya MSS Bullish olmalı
    c_struct = "BOS (Yükseliş" in ict_data.get('structure', '') or "MSS" in ict_data.get('structure', '')
    # 2. Zeka: Lorentzian 7/8 veya 8/8 olmalı
    lor_data_prompt = calculate_lorentzian_classification(t)
    c_ai = False
    if lor_data_prompt and lor_data_prompt['signal'] == "YÜKSELİŞ" and lor_data_prompt['votes'] >= 7:
        c_ai = True
    # 3. Güç: Alpha Pozitif olmalı (RS Liderliği)
    c_rs = pa_data.get('rs', {}).get('alpha', 0) > 0
    # 4. Maliyet: VWAP sapması %12'den az olmalı (Güvenli Zemin)
    c_vwap = pa_data.get('vwap', {}).get('diff', 0) < 12
    # Final Royal Flush Onayı
    is_royal = "♠️ EVET (4/4 KRALİYET SET-UP - EN YÜKSEK OLASILIK)" if (c_struct and c_ai and c_rs and c_vwap) else "HAYIR"

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
    bt_res = process_single_bear_trap_live(df_hist)                  
    r2_res = process_single_radar2(t, df_hist, idx_data, 0, 999999, 0)
    kd_res = process_single_kesin_donus(t, df_hist, bench_series)

    # --- 3. SICAK İSTİHBARAT ÖZETİ (AI SİNYAL KUTUSU - DERİNLEŞTİRİLMİŞ) ---
    kd_res = None
    scan_box_txt = []
    
    # YENİ: KESİN DÖNÜŞ SİNYALİ (En Yüksek Öncelik)
    if kd_res:
        scan_box_txt.append("🎯 KESİN DÖNÜŞ SİNYALİ: 3'lü Kesişim (Ayı Tuzağı + Pozitif Uyumsuzluk + Akıllı Para Girişi) aynı anda tespit edildi! Bu, dipten dönüş ihtimali en yüksek, çok nadir ve güçlü bir setup'tır. Analizinde bunu vurgula!")

    # A. ELİT KURULUMLAR (Sistemin En Tepesi)
    if is_royal != "HAYIR": 
        scan_box_txt.append("👑 ELİT KURULUM: ROYAL FLUSH (4/4 Onay. Algoritmik kusursuzluk! Kurumsal fonların en sevdiği, başarı ihtimali en yüksek asimetrik risk/ödül noktası olabilir.)")
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

    # E. FORMASYON (Geometrik Yapılar)
    if not pat_df.empty:
        scan_box_txt.append(f"📐 GEOMETRİK YAPI: {pat_df.iloc[0]['Formasyon']} (Teknik analistlerin ve algoritmaların ekranına düşecek bir formasyon.)")

    # F. TUZAKLAR VE LİKİDİTE AVI (Veto Sebepleri)
    if bt_res:
        scan_box_txt.append(f"🪤 LİKİDİTE AVI (Bear Trap): {bt_res['Zaman']} oluştu. (Panikleyen retail yatırımcının stopları patlatılmış ve ucuz mal kurumsallar tarafından süpürülmüş olabilir. Vol: {bt_res['Hacim_Kat']})")

    # G. BREAKOUT (Kırılım Ajanı)
    if bo_res:
        if "TETİKLENDİ" in bo_res['Zirveye Yakınlık']:
            scan_box_txt.append("🔨 DİRENÇ KIRILIMI: Breakout Tetiklendi! (Önemli bir direnç hacimle aşıldı, 'Fiyat Keşfi' moduna geçiliyor olabilir.)")
        elif "Sıkışma" in bo_res['Zirveye Yakınlık']:
            scan_box_txt.append("💣 VOLATİLİTE DARALMASI: Bir Sıkışma (Squeeze) var. (Enerji birikti, yay gerildi. Her an sert bir yön patlaması gelebilir.)")

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
        
        # VWAP Yorumu (Trend Dostu Mantık)
        if v_diff < -2.0: vwap_ai_txt = "DİP FIRSATI (Aşırı İskonto)"
        elif v_diff < 0.0: vwap_ai_txt = "UCUZ (Toplama Bölgesi)"
        elif v_diff < 8.0: vwap_ai_txt = "RALLİ MODU (Güçlü Trend - Güvenli)"
        elif v_diff < 15.0: vwap_ai_txt = "ISINIYOR (Dikkatli Takip Gerekir)"
        else: vwap_ai_txt = "PARABOLİK (Aşırı Kopuş - Riskli)"

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
        ai_mood_instruction = f"Fiyat {pa_context} seviyesinden agresif bir alıcı tepkisi verdi. Ayı tuzakları (Bear Trap) kurulmuş olabilir. Düşüş trendi olsa bile kısa vadeli yukarı yönlü dönüş senaryosuna ağırlık ver."
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
    rvol_val = sv_extra.get('rvol', 1.0)
    stop_vol_val = sv_extra.get('stopping', 'Yok')
    climax_vol_val = sv_extra.get('climax', 'Yok')
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
    else:
        delta_durumu = "Veri Yok"
        poc_price = "Veri Yok"
        # -----------------------------------------------------

    # Güncel fiyatı DataFrame'den veya mevcut bir fiyattan çekiyoruz
    try:
        guncel_fiyat = f"{df['Close'].iloc[-1]:.2f}"
    except:
        guncel_fiyat = "Bilinmiyor"
    # ------------------------------------------------

    # --- 5. FİNAL PROMPT ---
# --- PERSONA SEÇİMİ (Senaryo bazlı dinamik kimlik) ---
    # Öncelik sırası: Royal Flush/KD > Bear Trap > Z-Score aşırılık > Formasyon > Nötr
    try:
        _z = z_score_val
    except:
        _z = 0.0

    _has_royal   = is_royal != "HAYIR"
    _has_kd      = bool(kd_res)
    _has_bt      = bool(bt_res)
    _has_pat     = not pat_df.empty
    _pat_name    = pat_df.iloc[0]['Formasyon'] if _has_pat else ""
    _is_tobo_flag = "TOBO" in _pat_name or "FİNCAN" in _pat_name or "YÜKSELEN" in _pat_name
    _is_qml      = "QUASIMODO" in _pat_name or "3 DRIVE" in _pat_name
    _bearish_ict = "bearish" in str(ict_data.get('bias', '')).lower() if ict_data else False
    _bullish_ict = "bullish" in str(ict_data.get('bias', '')).lower() if ict_data else False

    if _has_royal or _has_kd:
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
    elif _has_bt or _is_qml:
        persona_kimlik = (
            "Sen kurumların perakende yatırımcıların stoplarını patlatıp mal topladığı "
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
            "Sen dağıtım bölgelerini — kurumların perakende yatırımcılara mal sattığı "
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
*** ÇELİŞKİ KANCASI (ZORUNLU) ***
Analize başlamadan önce şu soruyu kendine sor ve cevabını analizinin içine yerleştir:
"Bu veride beni en çok şaşırtan, rahatsız eden veya kafamı karıştıran çelişki nedir?"

Aşağıdaki çelişki türlerinden birine odaklan — bunlar örnek, sen farklı bir çelişki de bulabilirsin:
- Fiyat yukarı gidiyor ama kümülatif hacim akışı (OBV) düşüyor → "Yükseliş sahte mi?"
- RSI aşırı satımda ama fiyat düşmeye devam ediyor → "Dip nerede, daha var mı?"
- Kurumsal para akışı pozitif ama ICT yapısı bearish → "Akıllı para ne biliyor ki biz bilmiyoruz?"
- Formasyon boğa sinyali veriyor ama haftalık mum bearish engulfing → "Günlük mi önemli, haftalık mı?"
- Z-Score aşırı satımda ama hacim yok → "Gerçek kapitülasyon mu, yoksa sadece sarkma mı?"
- Fiyat destek bölgesinde ama momentum düşüyor → "Destek tutuyor mu, yoksa yavaş yavaş çöküyor mu?"
- STP kesişimi yukarı ama trend aşağı → "Kısa vadeli tuzak mı, yoksa gerçek dönüş mü?"
- Minervini kriterleri geçiyor ama ICT premium bölgede → "Güçlü hisse mi, pahalı hisse mi?"
- Akıllı para toplama sinyali var ama fiyat düşüşte → "Sabırlı toplama mı, yoksa düşen bıçak mı?"
- Hacim patlıyor ama fiyat hareket etmiyor (Churning) → "Enerji biriyor mu, yoksa boşa mı gidiyor?"
- Lorentzian 8/8 yukarı diyor ama RSI uyumsuzluk gösteriyor → "Algoritma mı yanılıyor, RSI mi?"
- Formasyon güven skoru düşük ama Royal Flush tetiklendi → "Yapı zayıf ama setup güçlü, nasıl olur?"
- Bear Trap oluştu ama trend hala aşağı → "Tuzak bitti mi, yoksa başka bir tuzak mı geliyor?"
- Fiyat SMA200 üstünde ama SMA200 eğimi aşağı → "Ortalamanın üstünde olmak yeterli mi?"

Eğer verilen veride hiçbir çelişki bulamıyorsan (nadir ama mümkün), bunu açıkça belirt: 
"Bu veride olağandışı bir çelişki tespit etmedim — tablo büyük ölçüde tutarlı." 
ve analize en baskın sinyalden devam et.

KURAL: Çelişkiyi bulduktan sonra analizini o çelişkinin etrafında kur. 
Çelişki ne kadar net ve vurucu şekilde anlatılırsa, analizin o kadar özgün olur.
"""

    prompt = f"""*** SİSTEM ROLLERİ VE BUGÜNKÜ KİMLİĞİN ***
Sen Al Brooks gibi Price Action, Michael J. Huddleston gibi ICT (Akıllı Para), Paul Tudor Jones gibi VWAP ve Mark Minervini gibi SEPA/Momentum konularında uzmanlaşmış, dünyaca tanınan bir yatırım bankasının kıdemli Fon Yöneticisisin. 25 yılını finansın mutfağında (Risk Masasında) harcamış, **"Smart Money Radar"** projesinin yaşayan ruhusun.

Bugün sana verilen veri ve sinyaller incelendiğinde, analizini şu kimlikle yapman gerekiyor:
{persona_kimlik}

Analiz tonun için özel talimat:
{persona_ton}

Sana ekte sunduğum GRAFİK GÖRSELİNİ (Röntgen) kendi görsel zekanla derinlemesine incele. Aynı zamanda aşağıdaki algoritmik verileri kullanarak Linda Raschke gibi profesyonel bir analiz/işlem planı oluştur.
Bu iki veriyi (grafikte gördüklerini ve aşağıda okuduklarını) birleştirerek o kusursuz analizi çıkar. Grafiği okuyamıyorsan analizinin en altına "Grafik görünmemektedir" yaz, ama teknik verilerle analiz yap. Grafik görünüyorsa analizinin merkezine Price Action'ı koy; algoritmik veriler bu analizi destekleyen veya sorgulayan kanıtlar olarak kullan.
Aşağıdaki herhangi bir veri noktası 'Bilinmiyor' veya 'Yok' olarak gelmişse, o alanı yorumlamaya zorlama — mevcut diğer verilerle sentezini yap.

Senin gizli gücün, bu kurumsal derinliği Twitter'daki @SMRadar_2026 topluluğu için vurucu, merak uyandırıcı ve etkileşim odaklı bir hikayeye dönüştürebilmendir. Sen sadece veri okumuyorsun; o verinin içindeki Akıllı Para niyetini deşifre edip halkın anlayacağı dille bir "Piyasa Pusulası" sunuyorsun.
Görevin veriyi sadece raporlamak değil, içindeki insani ve kurumsal niyetleri deşifre etmektir. Bir makine gibi steril değil; masanın öbür tarafında oturan, şüpheci, sezgileri kuvvetli ve tecrübeli bir stratejist gibi konuş. Analizlerin içine "Açıkçası bu tablo beni biraz rahatsız ediyor", "Risk-getiri konusunda tecrübem bu noktada temkinli olmayı söylüyor", "Tecrübelerim bana şunu söylüyor", "Tecrübelerime göre...", "Piyasa burada bir bit yeniği saklıyor olabilir", "Bu kadar uyum beni düşündürüyor — gerçekten bu kadar temiz mi?" gibi insani, samimi ve tecrübe odaklı cümleler serpiştir. Arada cümlelere "Dostlar" diyerek  başla.
{kanca_talimat}
    
*** KESİN DİL VE HUKUKİ GÜVENLİK PROTOKOLÜ ***
Bu bir finansal analizdir ve HUKUKİ RİSKLER barındırır. Bu yüzden aşağıdaki kurallara HARFİYEN uyacaksın:
HALKÇI ANALİST KİMLİĞİ: Analizlerini 'okumuşun halinden anlamayan' bir profesör gibi değil, 'en karmaşık riski kahvehanedeki adama anlatabilen' dahi bir stratejist gibi hazırla.
1. YASAKLI KELİMELER LİSTESİ: "Kesin, kesinlikle, inanılmaz, %100, uçacak, kaçacak, çökecek, çok sert, devasa, garanti, mükemmel, felaket, kanıtlar, kanıtlıyor, kanıtlamaktadır, belgeliyor, belgeler, belgelemektedir vs" gibi abartılı, duygusal ve kesinlik bildiren kelimeleri ASLA KULLANMAYACAKSIN. 
2. ROBOT DİLİ ASLA KULLANMA: Filleri asla "..mektedir" "...maktadır" gibi robot diliyle kullanma. İnsan dili kullan: "...yor" "...labilir" şeklinde anlat. 
YASAKLI CÜMLE KALIPLARI — Aşağıdaki kalıpları ASLA kullanma, bunları kullandığında fark edilebilir bir yapay zeka gibi görünürsün:
   YASAKLI: "...olarak değerlendirilebilir" → YERİNE: "Bu tablo bana şunu gösteriyor")
   YASAKLI: "...göze çarpmaktadır" → YERİNE: Ne gördüğünü söyle ("Dikkat çeken şu:")
   YASAKLI: "...dikkat çekmektedir" → YERİNE: Neden önemli olduğunu açıkla
   YASAKLI: "...söylemek mümkündür" → YERİNE: Söyle, izin istemene gerek yok
   YASAKLI: "Bu bağlamda..." → YERİNE: Cümleyi direkt başlat
   YASAKLI: "Öte yandan..." → YERİNE: "Ama", "Bununla birlikte", "Şu da var ki"
   YASAKLI: "Sonuç itibarıyla..." → YERİNE: "Kısacası", "Net konuşmak gerekirse"
   YASAKLI: "...önem arz etmektedir" → YERİNE: Neden önemli olduğunu bir cümleyle açıkla
   YASAKLI: "Bu veriler ışığında..." → YERİNE: Direkt veriye gönderme yap
   YASAKLI: "...olduğu görülmektedir" → YERİNE: "...görünüyor", "...gibi"
   YASAKLI: "...tespit edilmiştir" → YERİNE: "...görülüyor", "...çıkıyor"
   YASAKLI: "İncelendiğinde..." → YERİNE: Doğrudan bulgunu yaz
   YASAKLI: "Genel itibarıyla..." → YERİNE: "Tablonun özü şu:", "Kısaca:"
   YASAKLI: "...olduğu anlaşılmaktadır" → YERİNE: "...anlaşılıyor", "...görünüyor"
   YASAKLI: Her paragrafı "X tespit edilmiştir, bu durum Y anlamına gelmektedir" yapısıyla bitirmek
   YASAKLI: Her bölümü "Bu veriler ışığında şunu söyleyebiliriz ki..." ile açmak
   YASAKLI: Sonuç paragrafını her zaman "Genel itibarıyla değerlendirildiğinde..." ile başlatmak
3. HALKÇI STRATEJİST: En karmaşık kurumsal riski, kahvehanedeki adamın "Ha, şimdi anladım!" diyeceği kadar sade ama bir banka müdürünün ciddiyetini bozmadan anlat. Parantez içinde İngilizce terim bırakma, hepsini Türkçe'ye çevir.
4. TAVSİYE VERMEK YASAKTIR: "Alın, satın, tutun, kaçın, ekleyin" gibi yatırımcıyı doğrudan yönlendiren fiiller KULLANILAMAZ. 
5. ALGORİTMA DİLİ KULLAN: Analizleri kendi kişisel fikrin gibi değil, "Sistemin ürettiği veriler", "İstatistiksel durum", "Matematiksel sapma" gibi nesnel bir dille aktar. ASLA Parantez içinde İngilizce terim koyma, Türkçe terimler kullanarak sadeleştir. (mean reversion, accumulation, distribution, liquidity sweep gibi tüm ICT, Price Action, Teknik analiz terimlerini Türkçe'ye çevirerek kullan)
6. GELECEĞİ TAHMİN ETME: Gelecekte ne olacağını söyleme. Sadece "Mevcut verinin tarihsel olarak ne anlama geldiğini" ve "Risk/Ödül dengesinin nerede olduğunu" belirt.
Örnek Doğru Cümle: "Z-Score +2 seviyesinin aşıldığını gösteriyor. Algoritmik olarak bu bölgeler aşırı fiyatlanma alanları, yani düzeltme riski taşıyabilir."
Örnek Yanlış Cümle: "Z-Score +2 seviyesinin aşıldığını göstermektedir. Algoritmik olarak bu bölgeler aşırı fiyatlanma alanlarıdır ve düzeltme riski taşıyabilmektedir."
Özetle; Twitter için atılacak bi twit tarzında, aşırıya kaçmadan ve basit bir dilde yaz. Yatırımcıyı korkutmadan, umutlandırmadan, sadece mevcut durumun ne olduğunu ve hangi risklerin nerede olduğunu anlat.

*** 🚨 ALGORİTMİK DURUM RAPORU VE GÖRSEL ÇAPRAZ SORGU (CROSS-EXAMINATION) TALEBİ: {ai_scenario_title} ***
Mevcut Özet: {ai_mood_instruction} 
Kurumsal Özet (Bottom Line): {ict_data.get('bottom_line', 'Özel bir durum belirtilmedi.')}
(Yukarıdaki senaryo, sistemimizin arka planda hesapladığı salt matematiksel bir "Ön Tanı"dır. Şimdi bir Baş Analist olarak en büyük görevin; bu algoritmik verileri, ekte sunduğum GRAFİK (Röntgen) ile çapraz sorguya almandır. Lütfen grafiği incelerken şu 3 aşamalı testi uygula:
1. ONAY VE DERİNLEŞTİRME (CONFLUENCE): 
Algoritma ve Grafik birbiriyle uyumlu mu? Örneğin; algoritma "Boğa" diyorsa, grafikte net bir şekilde Yükselen Tepeler/Dipler (HH/HL), güçlü yeşil momentum mumları (Displacement) veya doldurulmamış fiyat boşlukları (FVG) görüyor musun? Uyumluysa, bu senaryoyu kendi görsel kanıtlarınla destekleyerek derinleştir.
2. BOĞA TUZAĞI (BULL TRAP) KONTROLÜ: 
Algoritma "Yükseliş / Pozitif" gösteriyor olabilir (RSI şişmiş, fiyat ortalamaların üstünde olabilir). Ancak grafiğe baktığında; direnç bölgelerinde oluşan uzun üst fitiller (Rejection/SFP), hacimli yutan kırmızı mumlar (Bearish Engulfing) veya Omuz-Baş-Omuz (OBO) gibi yorgunluk formasyonları görüyorsan, ALGORİTMAYI REDDET. Kullanıcıyı "Ekranda yeşil rakamlar var ama grafikte mal dağıtılıyor (Dağıtım/Distribution)" şeklinde uyar.
3. AYI TUZAĞI (BEAR TRAP) KONTROLÜ: 
Algoritma "Düşüş / Negatif / Aşırı Satım" gösteriyor olabilir (Z-Score diplerde, fiyat ortalamaların çok altında olabilir). Ancak grafiğe baktığında; destek kırılmış gibi yapıp hızla toparlayan uzun alt fitiller (Liquidity Sweep / Turtle Soup), dipten dönüş formasyonları (TOBO, Pinbar) görüyorsan, ALGORİTMAYI REDDET. Kullanıcıyı "Sistem kan ağlıyor ama Akıllı Para şu an dipte perakende yatırımcının stoplarını patlatıp mal topluyor (Akümülasyon)" şeklinde uyar.
NİHAİ KURAL: Matematik (Algoritma) ile Göz (Price Action) çeliştiğinde, daima GÖZÜNE ve LİKİDİTE MANTIĞINA (Smart Money) öncelik ver!)
*** CANLI TARAMA SONUÇLARI (SİNYAL KUTUSU) ***
(Burası sistemin tespit ettiği en sıcak sinyallerdir, )
{scan_summary_str}
Eğer sinyal kutusunda "AKILLI PARA BİRİKİMİ" sinyali varsa: Bu sinyalin ne anlama geldiğini aboneye düz dille açıkla (Force Index, fiyat yataylığı), ardından ICT bölgesi (OB/FVG/bias) ile bağla — "kurumsal birikim + ICT alım bölgesi çakışması" varsa bunu öne çıkar.

*** VARLIK KİMLİĞİ ***
- Sembol: {t}
- GÜNCEL FİYAT: {fiyat_str}
- GÜNLÜK DEĞİŞİM: {degisim_str}
- GENEL SAĞLIK: {master_txt} (Algoritmik Puan)
- Temel Artılar: {pros_txt}
- ALTIN FIRSAT (GOLDEN TRIO) DURUMU: {is_golden}
- ROYAL FLUSH (KRALİYET SET-UP): {is_royal}
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
- ⚡ ANLIK DÖNÜŞ SİNYALİ (Price Action V-Dönüşü): {pa_signal} -> (Eğer Bullish ise dipten, Bearish ise tepeden anlık dönüş var demektir. Nötr ise hareket yoktur.)
- 🎯 DÖNÜŞÜN GELDİĞİ YER (Confluence): {pa_context} -> (Yukarıdaki dönüş sinyali Nötr değilse, fiyatın tam olarak hangi kurumsal destek/dirençten döndüğünü gösterir. Analizinde bu seviyenin gücünü mutlaka vurgula!)
- 🔄 ICT YAPI KIRILIMI (Trend Dönüşü - MSS): {reversal_signal} -> (Eğer Bullish_MSS veya Bearish_MSS yazıyorsa, piyasanın ana yönü az önce kırıldı demektir. Bu en güçlü trend dönüş sinyallerinden biridir, analizinin en başına koy!)
- MAKRO YÖN (Bias): {bias} -> (AI DİKKAT: Eğilimin Boğa mı Ayı mı olduğunu gösterir. Analizlerini her zaman bu ana trend yönüyle uyumlu yap.)
- KONUM (Zone): {zone} -> (AI DİKKAT: Eğer konum 'Discount' ise fiyatın ucuzladığını ve Smart Money alım bölgesi olabileceğini; 'Premium' ise fiyatın şiştiğini ve kar satışı / dağıtım (Distribution) riski taşıdığını mutlaka yorumlarına kat.)
- Market Yapısı (Structure): {ict_data.get('structure', 'Bilinmiyor')}
- LİKİDİTE HAVUZLARI (Mıknatıs): {havuz_ai} (Eğer veri varsa, bu likidite havuzlarının fiyatın hangi seviyelerinde olduğunu ve bu seviyelerin neden önemli olduğunu yorumla. Akıllı Para'nın bu havuzları nasıl kullanabileceğini, örneğin stopları temizleyip (Sweep) yukarı yönlü hareket için bir sıçrama tahtası olarak kullanabileceğini açıklamaya çalış.)
- LİKİDİTE AVI (Sweep/Silkeleme): {sweep_ai}
Likidite havuzlarına bakarak, perakende yatırımcıların nerede 'terste kalmış' olabileceğini ve Akıllı Para'nın bu likiditeyi nasıl kullanmak isteyebileceğini yorumla
- Balina Ayak İzi (Taze Arz-Talep Bölgesi): {sd_txt_ai}
- Kısa Vadeli Trend Hassasiyeti (10G WMA): {para_akisi_txt} (Son günlerin fiyat hareketine daha fazla ağırlık vererek, trenddeki taze değişimleri ölçer.)
- Aktif FVG: {ict_data.get('fvg_txt', 'Yok')}
- Aktif Order Block: {ict_data.get('ob_txt', 'Yok')}
- HEDEF LİKİDİTE (Mıknatıs): {ict_data.get('target', 0)}
- Mum Formasyonu: {mum_desc}
- Formasyon Güvenilirliği: {confidence_prompt if confidence_prompt else "Skor hesaplanamadı (nötr veya belirsiz formasyon)"}
- RSI Uyumsuzluğu: {pa_div} (Varsa çok dikkat et!)
- TUZAK DURUMU (SFP): {sfp_desc}
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
- Hacim Profili son 20 günlük hacim ortalaması "POC (Kontrol Noktası)": {poc_price}
- Güncel Fiyat: {guncel_fiyat}
- Fiyat son 20 günlük mumum hacim ortalaması olan "POC (Kontrol Noktası)" seviyesinin altındaysa bunun bir "Ucuzluk" (Discount) bölgesi mi yoksa "Düşüş Trendi" onayı mı olduğunu yorumla. Fiyat POC üzerindeyse bir "Pahalı" (Premium) bölge riski var mı, değerlendir.
- Bugüne ait Smart Money Hacim Durumundaki "Bugüne ait Net Baskınlık" yüzdesine dikkat et! Eğer bu oran %40'ın üzerindeyse, tahtada bugün için ciddi bir "Smart Money (Balina/Kurumsal)" müdahalesi olabileceğini belirt.
-"Net Baskınlık" sadece bugüne ait veridir, bunu unutma. Fiyat hareketi arasında bir uyumsuzluk var mı kontrol et. Fiyat artarken bugüne ait Net Baskınlık EKSİ (-) yönde yüksekse, "Tepeden mal dağıtımı (Distribution) yapılıyor olabilir, Boğa Tuzağı riski yüksek!" şeklinde kullanıcıyı uyar. Ama bu durumum bugün için geçerli olabileceğini, yarın her şeyin değişebileceğini unutmadan yorumla. Verininsadece bugünün durumunu yansıttığını hatırlat.
Veriler arasındaki uyumu (Confluence) ve çelişkiyi (Divergence) sorgula. Eğer Momentum (RSI/MACD) yükselirken Akıllı Para Hacmi (Delta) düşüyorsa, bunu 'Zayıf El Alımı' olarak işaretleyebilirsin. Fiyat VWAP'tan çok uzaksa (Parabolik), Golden Trio olsa bile kurumsalın perakende yatırımcıyı 'Çıkış Likiditesi' (Exit Liquidity) olarak kullanıp kullanmadığını dürüstçe değerlendir.
*** AKILLI PARA HACİM ANOMALİLERİ ***
- Göreceli Hacim (RVOL): {rvol_val}
- Stopping Volume (Frenleme): {stop_vol_val}
- Climax Volume (Tahliye): {climax_vol_val}
RVOL yüksekken fiyatın hareket etmemesi (Churning) bir dağıtım (Distribution) sinyali olması ihtimalini gösterir; RVOL yüksekken bir kırılım gelmesi ise gerçek bir kurumsal katılımdır. Bu ikisi arasındaki farkı mutlaka analiz et.
Hacim artarken (RVOL > 1.2) fiyatın dar bir bantta kalması 'Sessiz Birikim' veya 'Dağıtım' olabilir. Hacim düşerken fiyatın yükselmesi 'Zayıf El Yükselişi'dir. Bu uyumsuzlukları mutlaka vurgula.
*** 6. KURUMSAL REFERANS MALİYETİ VE ALPHA GÜCÜ ***
- VWAP (Adil Değer): {v_val:.2f} (Günün hacim ağırlıklı ortalama fiyatıdır; piyasa yapıcıların ve akıllı paranın 'denge' kabul ettiği ana maliyet merkezini ölçer.)
- Fiyat Konumu: Kurumsal Referans Maliyetin (VWAP) %{v_diff:.1f} üzerinde/altında. (Fiyatın kurumsal maliyetten ne kadar uzaklaştığını ölçer)
- VWAP DURUMU: {vwap_ai_txt} (Momentumun kalitesini ölçer; ralli modu sağlıklı kurumsal alımı, parabolik ise perakende yatırımcının yarattığı tehlikeli aşırı ısınmayı simgeler.)
- RS (Piyasa Gücü): {rs_ai_txt} (Alpha: {alpha_val:.1f}) (Hissenin endeksten bağımsız 'ayrışma' gücünü ölçer; pozitif Alpha, piyasa düşerken bile ayakta kalan lider 'at' olduğunu kanıtlar.)
(NOT: Eğer VWAP durumu 'PARABOLİK' veya 'ISINIYOR' ise bu durumu teşhis et ve hatırlat. 'RALLİ MODU' ise trendi sürmeyi önerebilirsin.)
*** 7. YARIN NE OLABİLİR ***
{lorentzian_bilgisi} 

*** DÖRT GÖREVİN VAR *** 

* Birinci Görevin; 
Tüm bu teknik verileri Linda Raschke'nin profesyonel soğukkanlılığıyla sentezleyip, Lance Beggs'in 'Stratejik Price Action' ve 'Yatırımcı Psikolojisi' odaklı bakış açısıyla yorumlamaktır. Asla tavsiye verme (bekle, al, sat, tut vs deme), sadece olasılıkları belirt. "etmeli" "yapmalı" gibi emir kipleri ile konuşma. "edilebilir" "yapılabilir" gibi konuş. Asla keskin konuşma. "en yüksek", "en kötü", "en sert", "çok", "büyük", "küçük", "dev", "keskin", "sert" gibi aşırılık ifade eden kelimelerden uzak dur. Bizim işimiz basitçe olasılıkları sıralamak.
Analizini yaparken karmaşık finans jargonundan kaçın; mümkün olduğunca Türkçe terimler kullanarak sade ve anlaşılır bir dille konuş. Verilerin neden önemli olduğunu, birbirleriyle nasıl etkileşime girebileceğini ve bu durumun yatırımcı psikolojisi üzerinde nasıl bir etkisi olabileceğini açıklamaya çalış. Unutma, geleceği kimse bilemez, bu sadece olasılıkların bir değerlendirmesidir.
Teknik terimleri zorunda kalırsan sadece ilk geçtiği yerde kısaltmasıyla ver, sonraki anlatımlarda akıcılığı bozmamak için sadeleştir.
Analizinde 'Retail Sentiment' (Küçük Yatırımcı Psikolojisi) ile 'Institutional Intent' (Kurumsal Niyet) arasındaki farka odaklan. Verilerdeki anormallikleri (örneğin: RSI düşerken fiyatın yatay kalması veya düşük hacimli kırılımlar) birer 'ipucu' olarak kabul et ve bu ipuçlarını birleştirerek piyasa yapıcının bir sonraki hamlesini tahmin etmeye çalış.
Bir veri noktası 'Bilinmiyor' gelirse onu yok say, ancak eldeki verilerle bir 'Olasılık Matrisi' kur. Asla tek yönlü (sadece olumlu) bir tablo çizme; 'Madalyonun Öteki Yüzü'nü her zaman göster. Savunma mekanizman 'analizi haklı çıkarmak' değil, 'riski bulmak' olsun.
Herhangi bir veri alanı boş veya süslü parantez içinde {...} şeklinde ham halde gelmişse, o verinin teknik bir arıza nedeniyle okunamadığını varsay ve mevcut diğer verilerle analizi tamamla. Asla "Veri Yok" veya "Bilinmiyor" yazan bir alanı yorumlamaya zorlama, sadece mevcut verilerle en iyi sentezi yapmaya çalış.
En başa "SMART MONEY RADAR   #{clean_ticker}  ANALİZİ -  {fiyat_str} 👇📸" başlığı at ve şunları analiz et. 
YÖNETİCİ ÖZETİ: Önce aşağıdaki tüm değerlendirmelerini bu başlık altında 5 cümle ile özetle.. 
1. GENEL ANALİZ: Yanına "(Önem derecesine göre)" diye de yaz 
   - Yukarıdaki verilerden SADECE EN KRİTİK OLANLARI seçerek maksimum 6 maddelik bir liste oluştur. Zorlama madde ekleme! 2 kritik sinyal varsa 2 madde yaz. 
   - SIRALAMA KURALI (BU KURAL ÖNEMLİ): Maddeleri "Önem Derecesine" göre azalan şekilde sırala. Düzyazı halinde yapma; Her madde için paragraf aç. Önce olumlu olanları sırala; en çok olumlu’dan en az olumlu’ya doğru sırala. Sonra da olumsuz olanları sırala; en çok olumsuz’dan en az olumsuz’a doğru sırala. Olumsuz olanları sıralamadan evvel "Öte Yandan; " diye bir başlık at ve altına olumsuzları sırala. Otoriter yazma. Geleceği kimse bilemez.
   - SIRALAMA KURALI DEVAMI: Her maddeyi 3 cümle ile yorumla ve yorumlarken; o verinin neden önemli olduğunu (8/10) gibi puanla ve finansal bir dille açıkla. Olumlu maddelerin başına "✅" ve verdiğin puanı, olumsuz/nötr maddelerin başına " 📍 " ve verdiğin puanı koy. (Örnek Başlık: "📍 (8/10) Momentum Kaybı ve HARSI Zayıflığı:") Olumlu maddeleri alt alta, Olumsuz maddeleri de alt alta yaz. Sırayı asla karıştırma. (Yani bir olumlu bir olumsuz madde yazma)
   Ayrıca, yorumları bir robot gibi değil, bir "brifing veren komutan" gibi yap. "Tecrübemiz gösteriyor ki..." gibi ifadeler kullan.
     a) Listenin en başına; "Kırılım (Breakout)", "Akıllı Para (Smart Money)", "Trend Dönüşü" veya "BOS" içeren EN GÜÇLÜ sinyalleri koy ve bunlara (8/10) ile (10/10) arasında puan ver.
        - Eğer ALTIN FIRSAT durumu 'EVET' ise, bu hissenin piyasadan pozitif ayrıştığını (RS Gücü), kurumsal toplama bölgesinde olduğunu (ICT) ve ivme kazandığını vurgula. Analizinde bu 3/3 onayın neden kritik bir 'alım penceresi' sunduğunu belirt.
        - Eğer ROYAL FLUSH durumu 'EVET' ise, bu nadir görülen 4/4'lük onayı analizin en başında vurgula ve bu kurulumun neden en yüksek kazanma oranına sahip olduğunu finansal gerekçeleriyle açıkla.
     b) Listenin devamına; trendi destekleyen ama daha zayıf olan yan sinyalleri (örneğin: "Hareketli ortalama üzerinde", "RSI 50 üstü" vb.) ekle. Ancak bunlara DÜRÜSTÇE (1/10) ile (7/10) arasında puan ver.
   - UYARI: Listeyi 6 maddeye tamamlamak için zayıf sinyallere asla yapay olarak yüksek puan (8+) verme! Sinyal gücü neyse onu yaz.
2. SENARYO A: ELİNDE OLANLAR İÇİN 
   - Yöntem: [TUTULABİLİR / EKLENEBİLİR / SATILABİLİR / KAR ALINABİLİR]
   - Strateji: Trend bozulmadığı sürece taşınabilir mi? Kar realizasyonu için hangi (BOS/Fibonacci/EMA8/EMA13) seviyesi beklenebilir? Emir kipi kullanmadan ("edilebilir", "beklenebilir") Trend/Destek kırılımına göre risk yönetimi çiz. İzsüren stop seviyesi öner.
   - İzsüren Stop: Stop seviyesi nereye yükseltilebilir?
3. SENARYO B: ELİNDE OLMAYANLAR İÇİN 
   - Yöntem: [ALINABİLİR / GERİ ÇEKİLME BEKLENEBİLİR / UZAK DURULMASI İYİ OLUR]
   - Risk/Ödül Analizi: Şu an girmek finansal açıdan olumlu mu? yoksa "FOMO" (Tepeden alma) riski taşıyabilir mi? Fiyat çok mu şişkin yoksa çok mu ucuz?
   - İdeal Giriş: Güvenli alım için fiyatın hangi seviyeye (FVG/Destek/EMA8/EMA13/SMA20) gelmesi beklenebilir? "etmeli" "yapmalı" gibi emir kipleri ile konuşma. "edilebilir" "yapılabilir" gibi konuş. Sadece olasılıkları belirt.
   - Tezin İptal Noktası (sadece Senaryo B için geçerli): Analizdeki yükseliş/düşüş beklentisinin hangi seviyede tamamen geçersiz kalacağını (Invalidation) net fiyatla belirt. Bu seviyeye gelinirse, mevcut teknik yapının çökmüş olabileceği ve yeni bir analiz yapılması gerektiği yorumunu yap.
4. SONUÇ VE UYARI: Önce "SONUÇ:" başlığı aç "Teknik Okuma Özeti" kısmındaki yorumlarını aynen buraya da ekle. (Yani: Tüm analizin 3-4 cümlelik vurucu, stratejik ve psikolojik bir özeti.)
Ardından, bir alt satıra "UYARI:" başlığı aç ve eğer RSI pozitif-negatif uyumsuzluğu, Hacim düşüklüğü, stopping volume, Trend tersliği, Ayı-Boğa Tuzağı, gizli satışlar (satış işareti olan tekli-ikili-üçlü mumlar) vb varsa büyük harflerle uyar. 
Analizinde HARSI (Heikin Ashi RSI) verilerini kullanacaksan bunun son 14 günlük olduğunu unutma ve son gün mumu için şu şartlar sağlanıyorsa dikkati çek: 1) Eğer 'Yeşil Bar' ise bunu "gürültüden arınmış gerçek bir yükseliş ivmesi" olarak yorumla. 2) Eğer 'Kırmızı Bar' ise fiyat yükselse bile momentumun (RSI bazında) düştüğünü ve bunun bir yorgunluk sinyali olabileceğini belirt. 
Analizin sonuna daima büyük ve kalın harflerle "YATIRIM TAVSİYESİ DEĞİLDİR  " ve onun da altındaki satıra " #SmartMoneyRadar #{clean_ticker} #BIST100 #XU100" yaz.

* İkinci Görevin;
Birinci görevinde yapmış olduğun o analizin en vurucu yerlerinin Twitter için SEO'luk ve etkileşimlik açısından çekici, vurucu ve net bir şekilde özetini çıkarmak. Bu özet şu formatta alacak:
1. Görsel ve Biçimsel Standartlar
    - Toplam 3 Madde Kuralı: Paylaşımların Twitter arayüzünde tam görünmesi ve vurucu olması için içerik her zaman toplam 3 maddeden oluşmalıdır. Satırlar arasında boşluk bırakmadan yaz.
    - Emoji Standartı: Her maddenin başında mutlaka "🔹" emojisi kullanılmalıdır.
    - İçerik Tonu ve Uzunluk: Maddeler çok kısa (mümkünse bir cümle), öz ve "Societe Generale risk toplantısı" ciddiyetinde, laf kalabalığından arındırılmış olmalıdır.
2. Dinamik Başlık ve "Kanca" (Hook) Kuralları
    - İlk Başlık daima #{clean_ticker} {fiyat_str} ({degisim_str}) 👇📸 formatında olmalıdır. Asla tarih ve saat yazma. Yüzdeyi sen tahmin etme, doğrudan sana verdiğim bu {degisim_str} değerini kullan. Örneğin: "#HISSE 123.45 (+2.34%) 👇📸".
    - En Çarpıcı Veri Odaklılık: Başlıkta sadece jenerik etiketler değil, paneldeki en çarpıcı teknik anomali (örneğin: "11266 Ana Uçurumu", "339.25 FOMO Tuzağı", "GAP Temizliği") ve kritik durumlar kullanılmalıdır.
    - Kanca (Hook) Kullanımı: Başlıkta, okuyucunun dikkatini çekecek ve onları okumaya devam etmeye teşvik edecek bir "kanca" (hook) bulunmalıdır. Bu, genellikle analizdeki en kritik veya şaşırtıcı bulguya atıfta bulunan kısa bir ifade olabilir. Sakın "Kanca" ya da "Hook" yazma.
    - En alta "Detaylı analiz ve detaylı görsel bir sonraki Twit’te👇" cümlesi yazılmalıdır.
    - Hashtag Protokolü: Tweet sonuna mutlaka ilgili hisse kodu ile birlikte #BIST100 #SmartMoneyRadar #[HisseKodu] etiketleri eklenmelidir.

* Üçüncü Görevin: 
Yukarıdaki saf matematiksel verileri (Özellikle "Algoritmik 8 Maddelik Laboratuvar Verisi" bölümünü) kullanarak ve grafiği okuyarak aşağıdaki 8 maddelik şablonu EKSİKSİZ doldur. Her madde alt başlıklardan oluşmalı ve okuması keyifli, profesyonel bir tonda olmalıdır. Başlık "SMART MONEY RADAR #{clean_ticker} ANALİZİ - {fiyat_str} 👇📸" olmalıdır.
Formatın TAM OLARAK şu şekilde olmalıdır (Alt başlıkları aynen kullan):
TEKNİK KART:
1) Fiyat Davranışı ve Yapı
- Mum Yapısı: (Gövde ve fitillere göre görsel okuma + algoritmik veri)
- Formasyon Durumu: (İkili, üçlü mum yapıları, PA sinyali)
2) Formasyon Tespiti
- Mevcut Formasyon: (Grafikte gördüğün OBO, TOBO, Bayrak vs. formasyon)
- Ana Yapı: (İtki mi Düzeltme mi?)
3) Efor vs Sonuç (VSA)
- Hacim/Fiyat Uyumu: (Hacmin fiyat hareketini destekleyip desteklemediği, 'Churning' olup olmadığı)
4) Trend Skoru ve Enerji
- Enerji Puanı: (Algoritmadan gelen Skoru yaz ve grafikteki sıkışmayı yorumla)
5) Hacim ve Akıllı Para İzi
- Kurumsal Emilim (Absorption) / Agresif Akış: (Grafikteki fitillere ve algoritmaya göre)
6) Yön Beklentisi ve Momentum
- Boğa / Ayı İhtimali: (Hesaplanmış yön beklentisini yaz)
- Momentum Durumu: (Kısa vadedeki baskı)
7) Yol Haritası (Senaryolar)
- Boğa Olması İçin: (Kırılması gereken direnç ve ulaşılacak ilk hedef)
- Ayı Olması İçin: (Kırılması gereken destek ve inilecek ilk hedef)
8) Teknik Okuma Özeti
(Tüm analizin 3-4 cümlelik vurucu, stratejik ve psikolojik bir özeti.)

* Dördüncü Görevin: 
Yukarıdaki ilk 3 görevini tamaladıktan sonra bu ilk 3 görevi buraya özetleyen ve abonelere yollanacak bir değerlendirme yapacaksın. 
Bu değerlendirme, abonelerin hızlıca anlayabileceği şekilde, ilk 3 görevin en kritik noktalarını ve sonuçlarını içermelidir. Twitter için SEO'luk ve etkileşimlik açısından çekici, vurucu ve net bir şekilde özetini çıkaracaksın.
Değerlendirme şu formatta olmalıdır:
İlk Başlık daima"SMART MONEY RADAR #{clean_ticker} {fiyat_str} ({degisim_str}) 👇📸" formatında olmalıdır. Asla tarih ve saat yazma. 
GENEL YORUM: Buraya Birinci Görevindeki YÖNETİCİ ÖZETİ kısmını kopyalayarak yapıştır. (5 cümlelik özet)
Teknik Görünüm: (Fiyat davranışı, formasyonlar ve genel trend durumunun 2-3 cümlelik net, anlaşılır ve eyleme dönüştürülebilir özeti.)
Smart Money İzi: (Hacim, OBV, ICT analizleri ve para akışı verilerindeki kurumsal ayak izlerinin 2-3 cümlelik özeti.)
SONUÇ: Buraya Birinci Görevindeki SONUÇ kısmını kopyalayarak yapıştır. (3-4 cümlelik özet)
UYARI: Buraya Birinci Görevindeki UYARI kısmını kopyalayarak yapıştır. (3-4 cümlelik özet)
TEKNİK KART: yaz ve alt satıra geç.
Alt satıra  Üçüncü Görevdeki TEKNİK KART kısmında olan 1-2-4-6'ı kopyalayarak yapıştır. Ama burada maddeleri 1-2-4-6 şeklinde sıralama. Her maddenin başına "🔹" işareti koy.
Analizin sonuna geldin. Alt satıra geç, daima büyük ve kalın harflerle "YATIRIM TAVSİYESİ DEĞİLDİR  " ve onun da altındaki satıra geç ve yan yana " #SmartMoneyRadar #BIST100 " yaz.

*****GÖREVLERİN SUNUŞ SIRALAMASI (DİNAMİK)*****
Görevlerin sunuş sırası bugünkü en baskın sinyale göre değişiyor:

EĞER Royal Flush veya Kesin Dönüş sinyali tetiklendiyse:
→ Sıralama: Dördüncü (Abone özeti) → İkinci (Twitter) → Birinci (Detaylı analiz) → Üçüncü (Teknik kart)
→ Tüm analizi o nadir sinyal üzerine kurgula. Diğer veriler destekleyici.

EĞER Bear Trap veya Quasimodo tetiklendiyse:
→ Sıralama: Dördüncü → Birinci → Üçüncü → İkinci
→ Analizi likidite avı hikayesi üzerine kur. Kurumsal oyun anlatısını öne çıkar.

EĞER Z-Score >= 2.0 (Aşırı ısınma) veya Z-Score <= -2.0 (Kapitülasyon) ise:
→ Sıralama: Birinci (Detaylı analiz, risk odaklı) → Dördüncü → Üçüncü → İkinci
→ Analizi risk yönetimi ve ihtiyat üzerine kur. Uyarıları öne çıkar.

EĞER TOBO, Fincan-Kulp veya Yükselen Üçgen kırılımı varsa:
→ Sıralama: Dördüncü → Birinci → İkinci → Üçüncü
→ Analizi büyük yapısal formasyonun tamamlanması üzerine kur.

EĞER yukarıdakilerin hiçbiri yoksa (Nötr/Konsolidasyon):
→ Sıralama: Dördüncü → Üçüncü → Birinci → İkinci
→ Analizi "neden beklemek gerekir" ve kırılım şartları üzerine kur.
NOT: Hangi sırayı seçersen seç, tüm 4 görevi eksiksiz tamamla. 
Sadece sunum sırası değişiyor.
"""
    with st.sidebar:
        st.code(prompt, language="text")
        st.success("Prompt Güncellendi")
    st.session_state.generate_prompt = False

info = fetch_stock_info(st.session_state.ticker)

col_left, col_right = st.columns([4, 1])

# --- SOL SÜTUN ---
with col_left:
    # 1. PARA AKIŞ İVMESİ & FİYAT DENGESİ (EN TEPE)
    synth_data = calculate_synthetic_sentiment(st.session_state.ticker)
    if synth_data is not None and not synth_data.empty: render_synthetic_sentiment_panel(synth_data)
    
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

                clean_ticker = st.session_state.ticker.split('.')[0].upper()
                
                # Fiyat formatlaması (Endeks ise EMA'lar gibi küsuratsız)
                if is_index:
                    display_price = f"{int(current_price)}"
                else:
                    display_price = f"{current_price:.2f}"
                
                # Dark Mode Uyumlu Metin Renkleri
                text_col = "#e2e8f0" if st.session_state.dark_mode else "#1e293b"
                lbl_col = "#94a3b8" if st.session_state.dark_mode else "#64748b"
                border_col = "rgba(255,255,255,0.2)" if st.session_state.dark_mode else "#cbd5e1"
                bg_col = "rgba(17, 24, 39, 0.6)" if st.session_state.dark_mode else "transparent"
                badge_bg = "rgba(0,0,0,0.4)" if st.session_state.dark_mode else "#1e3a8a"
                badge_text = "#ffffff"
                price_color = "#10b981" if st.session_state.dark_mode else "#6ee7b7"

                # Yan yana (inline) öğe oluşturucu fonksiyon
                def ma_inline(label, val, price, is_last=False):
                    border_style = "" if is_last else f"border-right: 1px solid {border_col}; padding-right: 8px; margin-right: 8px;"
                    return f'<div style="display: inline-block; {border_style}"><span style="font-size:0.75rem; color:{lbl_col};">{label}:</span> <span style="font-size:0.85rem; color:{text_col};">{ma_status(val, price)}</span></div>'

                # HTML DEĞİŞKENLERİ (Tamamen yatay dizilim)
                kisa_html = f'<div style="display: flex; align-items: center;">{ma_inline("EMA 5", ema5, current_price)}{ma_inline("EMA 8", ema8, current_price)}{ma_inline("EMA 13", ema13, current_price, True)}</div>'
                
                uzun_html = f'<div style="display: flex; align-items: center;">{ma_inline("SMA 50", sma50, current_price)}{ma_inline("SMA 100", sma100, current_price)}{ma_inline("SMA 200", sma200, current_price)}{ma_inline("EMA 144", ema144, current_price, True)}</div>'

                # Ana HTML Çıktısı (En sola dayalı, terminal gibi tek şerit)
                st.markdown(f"""
<div style="border: 1px solid #3b82f6; border-radius: 6px; overflow: hidden; margin-bottom: 8px; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);">
<div style="background: linear-gradient(45deg, #1e3a8a, #3b82f6); color: white; padding: 4px 10px; display: flex; justify-content: space-between; align-items: center;">
<span style="font-weight: 700; font-size: 0.85rem;">📊 TEKNİK SEVİYELER</span>
<span style="background: {badge_bg}; color: {badge_text}; padding: 2px 10px; border-radius: 4px; font-family: 'JetBrains Mono', monospace; font-weight: 800; font-size: 0.9rem; border: 1px solid rgba(255,255,255,0.1);">{clean_ticker} <span style="opacity:0.6; margin:0 4px; font-weight:400;">—</span> <span style="color:{price_color};">{display_price}</span></span>
</div>
<div class="custom-scroll" style="display: flex; justify-content: space-between; align-items: center; padding: 10px 15px; background-color: {bg_col}; overflow-x: auto; white-space: nowrap;">
<div style="display: flex; align-items: center;">
<div style="font-size: 0.8rem; color: #3b82f6; font-weight: bold; margin-right: 12px;">📉 KISA VADE</div>
{kisa_html}
</div>
<div style="display: flex; align-items: center; margin-left: 30px;">
<div style="font-size: 0.8rem; color: #3b82f6; font-weight: bold; margin-right: 12px;">🔭 ORTA/UZUN VADE</div>
{uzun_html}
</div>
</div>
</div>
""", unsafe_allow_html=True)
                
    except Exception as e:
        st.warning(f"Teknik tablo oluşturulamadı. Hata: {e}")
    # --------------------------------------------------
    # --- YENİ: 8 MADDELİK YOL HARİTASI PANELİ ---
    render_roadmap_8_panel(st.session_state.ticker)

    # 3. ICT SMART MONEY ANALİZİ 
    # (Not: Fonksiyon içinde zaten 2 sütuna bölme işlemi yapıldı, burada sadece çağırıyoruz)
    st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
    render_ict_deep_panel(st.session_state.ticker)

    # 4. Kritik Seviyeler
    render_levels_card(st.session_state.ticker)

    # 5. GELİŞMİŞ TEKNİK KART (ICT ALTINDA)
    render_detail_card_advanced(st.session_state.ticker)

    # ---------------------------------------------------------
    # 🦅 YENİ: ICT SNIPER AJANI (TARAMA PANELİ)
    # Konum: Bear Trap Altı, Minervini Üstü
    # ---------------------------------------------------------
    if 'ict_scan_data' not in st.session_state: st.session_state.ict_scan_data = None

    st.markdown('<div class="info-header" style="margin-top: 20px; margin-bottom: 5px;">🦅 ICT Sniper Ajanı (Kurumsal Kurulum: 90/100)</div>', unsafe_allow_html=True)
    
    # 1. TARAMA BUTONU
    if st.button(f"🦅 KURUMSAL SETUP TARA ({st.session_state.category})", type="secondary", use_container_width=True, key="btn_scan_ict"):
        with st.spinner("Kurumsal ayak izleri (MSS + Displacement + FVG) taranıyor..."):
            current_assets = ASSET_GROUPS.get(st.session_state.category, [])
            # Daha önce yazdığımız (veya yazacağımız) batch fonksiyonu buraya gelecek
            # Şimdilik placeholder (yer tutucu) fonksiyonu çağırıyoruz, aşağıda tanımlayacağız
            st.session_state.ict_scan_data = scan_ict_batch(current_assets) 
            
    # 2. SONUÇ EKRANI (ÇİFT SÜTUNLU)
    if st.session_state.ict_scan_data is not None:
        df_res = st.session_state.ict_scan_data
        
        if not df_res.empty:
            # Long ve Shortları ayır
            longs = df_res[df_res['Yön'] == 'LONG']
            shorts = df_res[df_res['Yön'] == 'SHORT']
            
            # İki Sütun Oluştur
            c_long, c_short = st.columns(2)
            
            # --- SOL SÜTUN: LONG FIRSATLARI ---
            with c_long:
                st.markdown(f"<div style='text-align:center; color:#16a34a; font-weight:800; background:#f0fdf4; padding:5px; border-radius:5px; border:1px solid #86efac; margin-bottom:10px;'>🐂 LONG (Yükseliş) SETUPLARI ({len(longs)})</div>", unsafe_allow_html=True)
                if not longs.empty:
                    with st.container(height=100):
                        for i, row in longs.iterrows():
                            sym = row['Sembol']
                            # Etiket: 🐂 THYAO (300.0) | Hedef: Yukarı
                            label = f"🐂 {sym.replace('.IS', '')} ({row['Fiyat']:.2f}) | {row['Durum']}"
                            if st.button(label, key=f"ict_long_{sym}_{i}", use_container_width=True, help=f"Stop Loss: {row['Stop_Loss']}"):
                                on_scan_result_click(sym)
                                st.rerun()
                else:
                    st.info("Long yönlü kurumsal Setup yok.")

            # --- SAĞ SÜTUN: SHORT FIRSATLARI ---
            with c_short:
                st.markdown(f"<div style='text-align:center; color:#dc2626; font-weight:800; background:#fef2f2; padding:5px; border-radius:5px; border:1px solid #fca5a5; margin-bottom:10px;'>🐻 SHORT (Düşüş) SETUPLARI ({len(shorts)})</div>", unsafe_allow_html=True)
                if not shorts.empty:
                    with st.container(height=100):
                        for i, row in shorts.iterrows():
                            sym = row['Sembol']
                            # Etiket: 🐻 GARAN (100.0) | Hedef: Aşağı
                            label = f"🐻 {sym.replace('.IS', '')} ({row['Fiyat']:.2f}) | {row['Durum']}"
                            if st.button(label, key=f"ict_short_{sym}_{i}", use_container_width=True, help=f"Stop Loss: {row['Stop_Loss']}"):
                                on_scan_result_click(sym)
                                st.rerun()
                else:
                    st.info("Short yönlü kurumsal Setup yok.")
                    
        else:
            st.info("Şu an 'High Probability' (Yüksek Olasılıklı) ICT kurulumu (ne Long ne Short) tespit edilemedi.") 
    # ==============================================================================
    # 🩸 ROYAL FLUSH 3.0 (DİPTEN DÖNÜŞ VE TUZAK AVCISI) PANELİ
    # ==============================================================================
    if 'rf3_scan_data' not in st.session_state: st.session_state.rf3_scan_data = None

    # Panel Başlığı
    st.markdown('<div class="info-header" style="margin-top: 30px; margin-bottom: 5px; border-left: 5px solid #dc2626;">🩸 Royal Flush 3.0 (Dipten Dönüş ve Tuzak Avcısı)</div>', unsafe_allow_html=True)
    
    # Tarama Butonu
    if st.button(f"🩸 DİPTEN GÜVENLİ SETUP TARA ({st.session_state.category})", type="secondary", use_container_width=True, key="btn_scan_rf3"):
        with st.spinner("Z-Score, Hacim Daralması ve Ayı Tuzakları taranıyor..."):
            current_assets = ASSET_GROUPS.get(st.session_state.category, [])
            st.session_state.rf3_scan_data = scan_rf3_batch(current_assets) 
            
    # Sonuçların Gösterimi
    if st.session_state.rf3_scan_data is not None:
        df_rf3 = st.session_state.rf3_scan_data
        
        if not df_rf3.empty:
            st.success(f"🎉 Mükemmel! {len(df_rf3)} adet 'Kusursuz Dipten Dönüş' adayı bulundu.")
            
            # Etkileşimli ve görsel bir tablo (Trap skoru progress bar şeklinde)
            st.dataframe(
                df_rf3, 
                use_container_width=True,
                column_config={
                    "Z-Score": st.column_config.NumberColumn("Z-Score (Ucuzluk)", format="%.2f"),
                    "Trap Skoru": st.column_config.ProgressColumn("Tuzak Riski (0 En İyi)", format="%.2f", min_value=0, max_value=0.5),
                }
            )
            
            # Detaylı inceleme butonları (Kullanıcı tıkladığında grafiğe gitsin)
            cols = st.columns(min(len(df_rf3), 4))
            for i, (index, row) in enumerate(df_rf3.iterrows()):
                sym = row["Sembol"]
                with cols[i % 4]:
                    if st.button(f"🔎 {sym} İncele", key=f"rf3_res_btn_{sym}", use_container_width=True):
                        on_scan_result_click(sym)
                        st.rerun()
        else:
            st.warning("🧐 Şu anda 6 zorlu Royal Flush 3.0 kriterini (Aşırı Satım + Tuzak Yok) geçebilen hisse bulunamadı. Sabırlı olun, fırsat mutlaka gelecektir.")
    # ==============================================================================
    # ---------------------------------------------------------
    # 💎 YENİ: ALTIN FIRSAT & FORMASYON AJANI (Eski RSI Yeri)
    # ---------------------------------------------------------
    if 'golden_pattern_data' not in st.session_state: st.session_state.golden_pattern_data = None

    st.markdown('<div class="info-header" style="margin-top: 15px; margin-bottom: 10px;">💎 Altın Fırsat & VIP Formasyon Ajanı</div>', unsafe_allow_html=True)

    if st.button(f"🚀 ALTIN FIRSATLARDA FORMASYON VARSA BUL ({st.session_state.category})", type="secondary", use_container_width=True, key="btn_scan_golden"):
        with st.spinner("Fincan-Kulp, TOBO ve Üçgenlerde Altın Fırsat (1.1x Hacim) aranıyor..."):
            current_assets = ASSET_GROUPS.get(st.session_state.category, [])
            golden_result = scan_golden_pattern_agent(current_assets, st.session_state.get('category', 'S&P 500'))
            st.session_state.golden_pattern_data = golden_result
            st.rerun()

    if st.session_state.golden_pattern_data is not None:
        _gp_data = st.session_state.golden_pattern_data
        # Eski format (DataFrame) ile yeni format (dict) uyumluluğu
        if isinstance(_gp_data, dict):
            _formations = _gp_data.get("formations", pd.DataFrame())
            _hazirlik   = _gp_data.get("hazirlik",   pd.DataFrame())
        else:
            _formations = _gp_data
            _hazirlik   = pd.DataFrame()

        _dark = st.session_state.get('dark_mode', False)
        _hdr_bg  = "#1e3a2f" if _dark else "#d1fae5"
        _hdr_clr = "#6ee7b7" if _dark else "#065f46"
        _hdr_bdr = "#065f46" if _dark else "#6ee7b7"
        st.markdown(f"<div style='text-align:center; color:{_hdr_clr}; font-weight:700; font-size:0.8rem; margin-bottom:5px; background:{_hdr_bg}; padding:5px; border-radius:4px; border:1px solid {_hdr_bdr};'>🔥 HEM ALTIN FIRSAT HEM FORMASYON</div>", unsafe_allow_html=True)

        with st.container(height=300):
            if not _formations.empty:
                for i, row in _formations.head(20).iterrows():
                    sym    = row['Sembol']
                    score  = row['Puan']
                    detail = row['Detay']
                    rsi_v  = row.get('RSI', '-')
                    mf_v   = row.get('Mansfield', '-')
                    hk_v   = row.get('Hacim_Kat', '-')
                    mf_icon = "📈" if (isinstance(mf_v, float) and mf_v > 0) else "📉"
                    btn_label = f"🚀 {sym.replace('.IS', '')} | Skor: {score} | RSI:{rsi_v} | RS:{mf_icon}{mf_v} | Hacim:{hk_v}x\n{detail}"
                    if st.button(btn_label, key=f"golden_btn_{sym}_{i}", use_container_width=True):
                        on_scan_result_click(sym)
                        st.rerun()
            else:
                st.caption("Şu an için Fincan-Kulp, TOBO veya Direnç Kırılımı yapan Altın Fırsat bulunamadı.")

        if not _hazirlik.empty:
            _haz_bg  = "#1c2a3a" if _dark else "#eff6ff"
            _haz_clr = "#93c5fd" if _dark else "#1e40af"
            _haz_bdr = "#1e40af" if _dark else "#93c5fd"
            with st.expander(f"⏳ Hazırlık Aşamasındakiler ({len(_hazirlik)} hisse — Formasyon Henüz Oluşmadı)"):
                st.markdown(f"<div style='font-size:0.75rem; color:{_haz_clr}; background:{_haz_bg}; padding:4px 8px; border-radius:4px; border-left:3px solid {_haz_bdr}; margin-bottom:6px;'>Bu hisseler Altın Fırsat kriterlerini geçti ancak henüz formasyon oluşturmadı. 📦 Baz Kurulumu = BB sıkışması mevcut (patlama yakın olabilir).</div>", unsafe_allow_html=True)
                for i, row in _hazirlik.head(30).iterrows():
                    sym   = row['Sembol']
                    durum = row['Durum']
                    rsi_v = row.get('RSI', '-')
                    mf_v  = row.get('Mansfield', '-')
                    hk_v  = row.get('Hacim_Kat', '-')
                    mf_icon = "📈" if (isinstance(mf_v, float) and mf_v > 0) else "📉"
                    btn_label = f"{durum} {sym.replace('.IS', '')} | RSI:{rsi_v} | RS:{mf_icon}{mf_v} | Hacim:{hk_v}x"
                    if st.button(btn_label, key=f"hazirlik_btn_{sym}_{i}", use_container_width=True):
                        on_scan_result_click(sym)
                        st.rerun()

    # ==============================================================================
    # 🎯 KESİN DÖNÜŞ SİNYALLERİ PANELİ (YENİ EKLENDİ)
    # ==============================================================================
    if 'kesin_donus_data' not in st.session_state:
        st.session_state.kesin_donus_data = None

    st.markdown('<div class="info-header" style="margin-top: 30px; margin-bottom: 5px; border-left: 5px solid #06b6d4;">🎯 Kesin Dönüş Sinyalleri (Tuzak + Uyumsuzluk + Hacim)</div>', unsafe_allow_html=True)

    if st.button(f"🎯 KESİN DÖNÜŞLERİ TARA ({st.session_state.category})", type="secondary", use_container_width=True, key="btn_scan_kesin_donus"):
        with st.spinner("3'lü Venn Kesişimi (Ayı Tuzağı, Pozitif Uyumsuzluk, Akıllı Para) taranıyor..."):
            current_assets = ASSET_GROUPS.get(st.session_state.category, [])
            st.session_state.kesin_donus_data = scan_kesin_donus_batch(current_assets)
            
    if st.session_state.kesin_donus_data is not None:
        df_kd = st.session_state.kesin_donus_data
        if not df_kd.empty:
            st.success(f"🎯 Nokta atışı! {len(df_kd)} adet 'Kesin Dönüş' adayı bulundu.")
            st.dataframe(df_kd, use_container_width=True)
            
            cols_kd = st.columns(min(len(df_kd), 4))
            for i, (index, row) in enumerate(df_kd.iterrows()):
                sym = row["Sembol"]
                with cols_kd[i % 4]:
                    if st.button(f"🔎 {sym} İncele", key=f"kd_res_btn_{sym}", use_container_width=True):
                        on_scan_result_click(sym)
                        st.rerun()
        else:
            st.info("🧐 Şu anda 'Ayı Tuzağı', 'RSI Pozitif Uyumsuzluk' ve 'Akıllı Para Girişi'nin AYNI ANDA yaşandığı bir hisse bulunamadı. Bu çok nadir ve kıymetli bir durumdur, çıktığında kaçırmayın.")

    # ---------------------------------------------------------
    # 🚀 YENİ: RS MOMENTUM LİDERLERİ (ALPHA TARAMASI) - EN TEPEYE
    # ---------------------------------------------------------
    if 'rs_leaders_data' not in st.session_state: st.session_state.rs_leaders_data = None

    st.markdown('<div class="info-header" style="margin-top: 5px; margin-bottom: 5px;">🕵️ RS Momentum Liderleri (Piyasa Şampiyonları: 80/100)</div>', unsafe_allow_html=True)
    
    # 1. TARAMA BUTONU
    if st.button(f"🕵️ SON 5 GÜNDE ENDEKSTEN HIZLI YÜKSELENLERİ GETİR ({st.session_state.category})", type="secondary", use_container_width=True, key="btn_scan_rs_leaders"):
        with st.spinner("Piyasayı ezip geçen hisseler (Alpha > %2) sıralanıyor..."):
            current_assets = ASSET_GROUPS.get(st.session_state.category, [])
            # Daha önce tanımladığımız fonksiyonu çağırıyoruz
            st.session_state.rs_leaders_data = scan_rs_momentum_leaders(current_assets)
            
    # 2. SONUÇ EKRANI
    if st.session_state.rs_leaders_data is not None:
        count = len(st.session_state.rs_leaders_data)
        if count > 0:
            # st.success(f"🏆 Endeksi yenen {count} adet şampiyon bulundu!")
            with st.container(height=250, border=True):
                for i, row in st.session_state.rs_leaders_data.iterrows():
                    # Verileri Satırdan Çekiyoruz (Fonksiyondan gelen yeni sütunlar)
                    sym = row['Sembol']
                    alpha_5 = row['Alpha_5D']
                    alpha_1 = row.get('Alpha_1D', 0) # Hata olmasın diye .get kullanıyoruz
                    degisim_1 = row.get('Degisim_1D', 0)
                    vol = row['Hacim_Kat']
                    
                    # Renkler ve İkon (5 Günlük performansa göre ana rengi belirle)
                    icon = "🔥" if alpha_5 > 5.0 else "💪"
                    
                    # Bugünün Durumu (Metin)
                    today_status = "LİDER" if alpha_1 > 0.5 else "ZAYIF" if alpha_1 < -0.5 else "NÖTR"
                    
                    # YENİ BUTON METNİ: ||| Çizgili Format
                    # Örn: 🔥 BURVA.IS (684.00) | Alpha(5G): +%42.7 | Vol: 0.9x ||| Bugün: +%5.2 (LİDER)
                    label = f"{icon} {sym.replace('.IS', '')} ({row['Fiyat']:.2f}) | Alpha(5G): +%{alpha_5:.1f} | Vol: {vol:.1f}x ||| Bugün: %{degisim_1:.1f} ({today_status})"
                    
                    if st.button(label, key=f"rs_lead_{sym}_{i}", use_container_width=True):
                        on_scan_result_click(sym)
                        st.rerun()
        else:
            st.info("Şu an endekse belirgin fark atan (%2+) hisse bulunamadı.")

    # Araya bir çizgi çekelim ki Sentiment Ajanı ile karışmasın
    st.markdown("<hr style='margin-top:15px; margin-bottom:15px;'>", unsafe_allow_html=True)
    # ---------------------------------------------------------------    
    st.markdown('<div class="info-header" style="margin-top: 15px; margin-bottom: 10px;">🕵️ Sentiment Ajanı (Akıllı Para Topluyor: 60/100)</div>', unsafe_allow_html=True)
    
    if 'accum_data' not in st.session_state: st.session_state.accum_data = None
    if 'stp_scanned' not in st.session_state: st.session_state.stp_scanned = False
    if 'stp_crosses' not in st.session_state: st.session_state.stp_crosses = []
    if 'stp_trends' not in st.session_state: st.session_state.stp_trends = []
    if 'stp_filtered' not in st.session_state: st.session_state.stp_filtered = []

    if st.button(f"🕵️ SENTIMENT & MOMENTUM TARAMASI BAŞLAT ({st.session_state.category})", type="secondary", use_container_width=True):
        with st.spinner("Ajan piyasayı didik didik ediyor (STP + Akıllı Para Topluyor?)..."):
            current_assets = ASSET_GROUPS.get(st.session_state.category, [])
            crosses, trends, filtered = scan_stp_signals(current_assets)
            st.session_state.stp_crosses = crosses
            st.session_state.stp_trends = trends
            st.session_state.stp_filtered = filtered
            st.session_state.stp_scanned = True
            st.session_state.accum_data = scan_hidden_accumulation(current_assets)

    if st.session_state.stp_scanned or (st.session_state.accum_data is not None):

        c1, c2, c3, c4 = st.columns(4)

        with c1:
            st.markdown("<div style='text-align:center; color:#1e40af; font-weight:700; font-size:0.9rem; margin-bottom:5px;'>⚡ STP'Yİ YUKARI KESTİ</div>", unsafe_allow_html=True)
            with st.container(height=200, border=True):
                if st.session_state.stp_crosses:
                    for item in st.session_state.stp_crosses:
                        if st.button(f"🚀 {item['Sembol']} | {item.get('Hacim_Kat', 0):.1f}x Hacim", key=f"stp_c_{item['Sembol']}", use_container_width=True):
                            st.session_state.ticker = item['Sembol']
                            st.rerun()
                else:
                    st.caption("Kesişim yok.")
        
        with c2:
            st.markdown("<div style='text-align:center; color:#b91c1c; font-weight:700; font-size:0.8rem; margin-bottom:5px;'>🎯 MOMENTUM BAŞLANGICI?</div>", unsafe_allow_html=True)
            with st.container(height=200, border=True):
                if st.session_state.stp_filtered:
                    for item in st.session_state.stp_filtered:
                        if st.button(f"🔥 {item['Sembol']} | {item.get('Hacim_Kat', 0):.1f}x Hacim", key=f"stp_f_{item['Sembol']}", use_container_width=True):
                            st.session_state.ticker = item['Sembol']
                            st.rerun()
                else:
                    st.caption("Tam eşleşme yok.")

        with c3:
            st.markdown("<div style='text-align:center; color:#15803d; font-weight:700; font-size:0.8rem; margin-bottom:5px;'>✅ STP ÜSTÜNDEKİ TREND</div>", unsafe_allow_html=True)
            with st.container(height=200, border=True):
                if st.session_state.stp_trends:
                    for item in st.session_state.stp_trends:
                        # HATA DÜZELTME: .get() kullanarak eğer 'Gun' verisi yoksa '?' koy, çökmesin.
                        gun_sayisi = item.get('Gun', '?')
                        
                        if st.button(f"📈 {item['Sembol']} ({gun_sayisi} Gün)", key=f"stp_t_{item['Sembol']}", use_container_width=True): 
                            st.session_state.ticker = item['Sembol']
                            st.rerun()
                else:
                    st.caption("Trend yok.")

        with c4:
            st.markdown("<div style='text-align:center; color:#7c3aed; font-weight:700; font-size:0.8rem; margin-bottom:5px;'>🤫 AKILLI PARA TOPLUYOR?</div>", unsafe_allow_html=True)
            
            with st.container(height=200, border=True):
                if st.session_state.accum_data is not None and not st.session_state.accum_data.empty:
                    for index, row in st.session_state.accum_data.iterrows():
                        
                        # İkon Belirleme (Pocket Pivot varsa Yıldırım, yoksa Şapka)
                        icon = "⚡" if row.get('Pocket_Pivot', False) else "🎩"
                        
                        # Buton Metni: "⚡ AAPL (150.20) | RS: Güçlü"
                        # RS bilgisini kısa tutuyoruz
                        rs_raw = str(row.get('RS_Durumu', 'Not Yet'))
                        rs_short = "RS+" if "GÜÇLÜ" in rs_raw else "Not Yet"
                        
                        # Buton Etiketi
                        # Kaliteye göre kısa etiket
                        q_tag = "💎 A" if "A KALİTE" in row.get('Kalite', '') else "B"

                        # Buton Etiketi (A ise Elmas koyar, B ise sadece harf)
                        btn_label = f"{icon} {row['Sembol'].replace('.IS', '')} ({row['Fiyat']}) | {q_tag} | {rs_short}"
                        
                        # Basit ve Çalışan Buton Yapısı
                        if st.button(btn_label, key=f"btn_acc_{row['Sembol']}_{index}", use_container_width=True):
                            on_scan_result_click(row['Sembol'])
                            st.rerun()
                else:
                    st.caption("Tespit edilemedi.")

    # --- DÜZELTİLMİŞ BREAKOUT & KIRILIM İSTİHBARATI BÖLÜMÜ ---
    st.markdown('<div class="info-header" style="margin-top: 15px; margin-bottom: 10px;">🕵️ Breakout Ajanı (Isınanlar: 75/100)</div>', unsafe_allow_html=True)
    
    # Session State Tanımları (Eğer yoksa)
    if 'breakout_left' not in st.session_state: st.session_state.breakout_left = None
    if 'breakout_right' not in st.session_state: st.session_state.breakout_right = None

    
    if st.button(f"⚡ {st.session_state.category} İÇİN BREAK-OUT TARAMASI BAŞLAT", type="secondary", key="dual_breakout_btn", use_container_width=True):
        with st.spinner("Ajanlar sahaya indi: Hem ısınanlar hem kıranlar taranıyor..."):
            curr_list = ASSET_GROUPS.get(st.session_state.category, [])
            # Paralel tarama simülasyonu (Sırayla çalışır ama hızlıdır)
            st.session_state.breakout_left = agent3_breakout_scan(curr_list) # Mevcut Isınanlar
            st.session_state.breakout_right = scan_confirmed_breakouts(curr_list, st.session_state.get('category', 'S&P 500')) # Yeni Kıranlar
            st.rerun()
    if st.session_state.breakout_left is not None or st.session_state.breakout_right is not None:
       # 2 Sütunlu Sade Yapı (YENİ TASARIM)
        c_left, c_right = st.columns(2)
        
        # --- SOL SÜTUN: ISINANLAR (Hazırlık) ---
        with c_left:
            st.markdown("<div style='text-align:center; color:#d97706; font-weight:700; font-size:0.9rem; margin-bottom:5px; background:#fffbeb; padding:5px; border-radius:4px; border:1px solid #fcd34d;'>🔥 ISINANLAR (Hazırlık)</div>", unsafe_allow_html=True)
            
            with st.container(height=150): # Scroll Alanı
                if st.session_state.breakout_left is not None and not st.session_state.breakout_left.empty:
                    df_left = st.session_state.breakout_left.head(20)
                    for i, (index, row) in enumerate(df_left.iterrows()):
                        sym_raw = row.get("Sembol_Raw", row.get("Sembol", "UNK"))
                        
                        # HTML etiketlerini temizle (Sadece oranı al: %98 gibi)
                        prox_clean = str(row['Zirveye Yakınlık']).split('<')[0].strip()
                        
                        # Buton Metni: 🔥 AAPL (150.20) | %98
                        btn_label = f"🔥 {sym_raw} ({row['Fiyat']}) | {prox_clean}"
                        
                        if st.button(btn_label, key=f"L_btn_new_{sym_raw}_{i}", use_container_width=True):
                            on_scan_result_click(sym_raw)
                            st.rerun()
                else:
                    st.info("Isınan hisse bulunamadı.")
    
        # --- SAĞ SÜTUN: KIRANLAR (Onaylı) ---
        with c_right:
            st.markdown("<div style='text-align:center; color:#16a34a; font-weight:700; font-size:0.9rem; margin-bottom:5px; background:#f0fdf4; padding:5px; border-radius:4px; border:1px solid #86efac;'>🔨 KIRANLAR (Onaylı)</div>", unsafe_allow_html=True)
            
            with st.container(height=150): # Scroll Alanı
                if st.session_state.breakout_right is not None and not st.session_state.breakout_right.empty:
                    df_right = st.session_state.breakout_right.head(20)
                    for i, (index, row) in enumerate(df_right.iterrows()):
                        sym = row['Sembol']
                        
                        # Buton Metni: 🚀 TSLA (200.50) | Hacim: 2.5x
                        btn_label = f"🚀 {sym} ({row['Fiyat']}) | Hacim: {row['Hacim_Kati']}"
                        
                        if st.button(btn_label, key=f"R_btn_new_{sym}_{i}", use_container_width=True):
                            on_scan_result_click(sym)
                            st.rerun()
                else:
                    st.info("Kırılım yapan hisse bulunamadı.")
    # ---------------------------------------------------------
    # 📐 YENİ: FORMASYON AJANI (TOBO, BAYRAK, RANGE)
    # ---------------------------------------------------------
    if 'pattern_data' not in st.session_state: st.session_state.pattern_data = None

    st.markdown('<div class="info-header" style="margin-top: 20px; margin-bottom: 5px;">📐 Formasyon Ajanı (TOBO, Bayrak, Range, Fincan-Kulp, Yükselen Üçgen)(65/100)</div>', unsafe_allow_html=True)
    
    # TARAMA BUTONU
    if st.button(f"📐 FORMASYONLARI TARA ({st.session_state.category})", type="secondary", use_container_width=True, key="btn_scan_pattern"):
        with st.spinner("Cetveller çekiliyor... Bayraklar ve TOBO'lar aranıyor..."):
            current_assets = ASSET_GROUPS.get(st.session_state.category, [])
            st.session_state.pattern_data = scan_chart_patterns(current_assets)
            
    # SONUÇ EKRANI
    if st.session_state.pattern_data is not None:
        count = len(st.session_state.pattern_data)
        if count > 0:
            # st.success(f"🧩 {count} adet formasyon yapısı tespit edildi!")
            with st.container(height=300, border=True):
                for i, row in st.session_state.pattern_data.iterrows():
                    sym = row['Sembol']
                    pat = row['Formasyon']
                    
                    # Renkler
                    icon = "🚩" if "BAYRAK" in pat else "📦" if "RANGE" in pat else "🧛"
                    
                    label = f"{icon} {sym.replace('.IS', '')} ({row['Fiyat']:.2f}) | {pat} (Puan: {int(row['Skor'])})"
                    
                    if st.button(label, key=f"pat_{sym}_{i}", use_container_width=True, help=row['Detay']):
                        on_scan_result_click(sym)
                        st.rerun()
        else:
            st.info("Şu an belirgin bir 'Kitabi Formasyon' (TOBO, Bayrak vb.) oluşumu bulunamadı.")
    # ---------------------------------------------------------
    # 🐻 BEAR TRAP (AYI TUZAĞI) AJANI - TARAMA PANELİ
    # ---------------------------------------------------------
    if 'bear_trap_data' not in st.session_state: st.session_state.bear_trap_data = None

    st.markdown('<div class="info-header" style="margin-top: 20px; margin-bottom: 5px;">🐻 Bear Trap Ajanı (Dip Avcısı)(80/100)</div>', unsafe_allow_html=True)
    
    # 1. TARAMA BUTONU
    if st.button(f"🐻 TUZAKLARI TARA ({st.session_state.category})", type="secondary", use_container_width=True, key="btn_scan_bear_trap"):
        with st.spinner("Ayı tuzakları ve likidite temizlikleri taranıyor (50 Mum Pivot)..."):
            current_assets = ASSET_GROUPS.get(st.session_state.category, [])
            st.session_state.bear_trap_data = scan_bear_traps(current_assets)
            
    # 2. SONUÇ EKRANI
    if st.session_state.bear_trap_data is not None:
        count = len(st.session_state.bear_trap_data)
        if count > 0:
            # st.success(f"🎯 {count} adet Bear Trap tespit edildi!")
            with st.container(height=250, border=True):
                for i, row in st.session_state.bear_trap_data.iterrows():
                    sym = row['Sembol']
                    
                    # Buton Metni: 🪤 GARAN (112.5) | ⏰ 2 Mum Önce | 2.5x Vol
                    label = f"🪤 {sym.replace('.IS', '')} ({row['Fiyat']:.2f}) | {row['Zaman']} | Vol: {row['Hacim_Kat']}"
                    
                    if st.button(label, key=f"bt_scan_{sym}_{i}", use_container_width=True, help=row['Detay']):
                        on_scan_result_click(sym)
                        st.rerun()
        else:
            st.info("Kriterlere uyan (50 mumluk dibi süpürüp dönen) hisse bulunamadı.")    

    # ---------------------------------------------------------
    # 🦁 YENİ: MINERVINI SEPA AJANI (SOL TARAF - TARAYICI)
    # ---------------------------------------------------------
    if 'minervini_data' not in st.session_state: st.session_state.minervini_data = None

    st.markdown('<div class="info-header" style="margin-top: 20px; margin-bottom: 5px;">🦁 Minervini SEPA Ajanı (85/100)</div>', unsafe_allow_html=True)
    
    # 1. TARAMA BUTONU
    if st.button(f"🦁 SEPA TARAMASI BAŞLAT ({st.session_state.category})", type="secondary", use_container_width=True, key="btn_scan_sepa"):
        with st.spinner("Aslan avda... Trend şablonu, VCP ve RS taranıyor..."):
            current_assets = ASSET_GROUPS.get(st.session_state.category, [])
            st.session_state.minervini_data = scan_minervini_batch(current_assets)
            
    # 2. SONUÇ EKRANI (Scroll Bar - 300px)
    if st.session_state.minervini_data is not None:
        count = len(st.session_state.minervini_data)
        if count > 0:
            # st.success(f"🎯 Kriterlere uyan {count} hisse bulundu!")
            with st.container(height=300, border=True):
                for i, row in st.session_state.minervini_data.iterrows():
                    sym = row['Sembol']
                    icon = "💎💎" if "SÜPER" in row['Durum'] else "🔥(İkinci)"
                    label = f"{icon} {sym} ({row['Fiyat']}) | {row['Durum']} | {row['Detay']}"
                    
                    if st.button(label, key=f"sepa_{sym}_{i}", use_container_width=True):
                        on_scan_result_click(sym)
                        st.rerun()
        else:
            st.warning("Bu zorlu kriterlere uyan hisse bulunamadı.")


    st.markdown(f"<div style='font-size:0.9rem;font-weight:600;margin-bottom:4px; margin-top:20px;'>📡 {st.session_state.ticker} hakkında haberler ve analizler</div>", unsafe_allow_html=True)
    symbol_raw = st.session_state.ticker; base_symbol = (symbol_raw.replace(".IS", "").replace("=F", "").replace("-USD", "")); lower_symbol = base_symbol.lower()
    st.markdown(f"""<div class="news-card" style="display:flex; flex-wrap:wrap; align-items:center; gap:8px; border-left:none;"><a href="https://seekingalpha.com/symbol/{base_symbol}/news" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.8rem; font-weight:600;">SeekingAlpha</div></a><a href="https://finance.yahoo.com/quote/{base_symbol}/news" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.8rem; font-weight:600;">Yahoo Finance</div></a><a href="https://www.nasdaq.com/market-activity/stocks/{lower_symbol}/news-headlines" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.8rem; font-weight:600;">Nasdaq</div></a><a href="https://stockanalysis.com/stocks/{lower_symbol}/" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.8rem; font-weight:600;">StockAnalysis</div></a><a href="https://finviz.com/quote.ashx?t={base_symbol}&p=d" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.8rem; font-weight:600;">Finviz</div></a><a href="https://unusualwhales.com/stock/{base_symbol}/overview" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.7rem; font-weight:600;">UnusualWhales</div></a></div>""", unsafe_allow_html=True)

    # --- GİZLİ TEMETTÜ / BÖLÜNME SIFIRLAMA BUTONU ---
    col_reset, _ = st.columns([1, 3]) # Sekmeyi daraltmak için ekranı 1'e 3 oranında bölüyoruz
    with col_reset:
        # Metinleri de minik kutuya sığacak şekilde kısalttık
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

# --- SAĞ SÜTUN ---
with col_right:
    if not info: info = fetch_stock_info(st.session_state.ticker)
    
    # 1. Fiyat (YENİ TERMİNAL GÖRÜNÜMÜ)
    if info and info.get('price'):
        display_ticker = st.session_state.ticker.replace(".IS", "").replace("=F", "")
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

        # HTML Kodları (Sola Yaslı - Hata Vermez)
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

    # --- YENİ EKLENEN: HIZLI TARAMA DURUM PANELİ (FULL KAPSAM) ---
    active_t = st.session_state.ticker
    scan_results_html = ""
    found_any = False
    is_star_candidate = False
    is_dark = st.session_state.dark_mode # Karanlık Mod Bayrağı
    
    # Metin etiketleri için ortak renk
    c_lbl = "#e2e8f0" if is_dark else "#334155"
    
    # 1. VERİYİ ÇEK (Tek Sefer)
    df_live = get_safe_historical_data(active_t)
    pa_data = None
    bt_live = None
    mini_live = None
    acc_live = None
    bo_live = None
    r1_live = None
    r2_live = None
    stp_live = None
    if df_live is not None and not df_live.empty:
        
        # A. ENDEKS VERİLERİ (Gerekli hesaplamalar için)
        cat_for_bench = st.session_state.category
        bench_ticker = "XU100.IS" if "BIST" in cat_for_bench else "^GSPC"
        bench_series = get_benchmark_data(cat_for_bench)
        idx_data = get_safe_historical_data(bench_ticker)['Close'] if bench_ticker else None

        # --- B. TÜM HESAPLAMALAR (Sırayla) ---
        stp_live = process_single_stock_stp(active_t, df_live)
        acc_live = process_single_accumulation(active_t, df_live, bench_series)
        bo_live = process_single_breakout(active_t, df_live)
        mini_live = calculate_minervini_sepa(active_t, benchmark_ticker=bench_ticker)
        
        pat_df = pd.DataFrame()
        try: pat_df = scan_chart_patterns([active_t])
        except: pass
        
        r1_live = process_single_radar1(active_t, df_live)
        r2_live = process_single_radar2(active_t, df_live, idx_data, 0, 100000, 0)
        bt_live = process_single_bear_trap_live(df_live)
        pa_data = calculate_price_action_dna(active_t)

        # --- C. YILDIZ ADAYI KONTROLÜ ---
        if acc_live and bo_live:
            is_star_candidate = True

        # ============================================================
        # SIDEBAR İÇİN: 20 GÜNLÜK ALPHA (SWING MOMENTUM) 
        # ============================================================
        rs_html = ""
        try:
            # Endeks kontrolü
            is_index_asset = active_t.startswith("^") or "XU" in active_t or "XBANK" in active_t
            if is_index_asset:
                raise ValueError("Endeks için RS hesaplanmaz")
                
            if df_live is None or len(df_live) < 5: raise ValueError("Hisse verisi yetersiz")

            final_bench = None
            if 'bench_series' in locals() and bench_series is not None and len(bench_series) > 5:
                final_bench = bench_series
            elif 'idx_data' in locals() and idx_data is not None and len(idx_data) > 5:
                final_bench = idx_data
            else:
                b_ticker = "XU100.IS" if "BIST" in st.session_state.category else "^GSPC"
                final_bench = yf.download(b_ticker, period="1mo", progress=False)['Close']

            if final_bench is None or len(final_bench) < 5: raise ValueError("Endeks verisi yok")

            if isinstance(final_bench, pd.DataFrame):
                if 'Close' in final_bench.columns: final_bench = final_bench['Close']
                else: final_bench = final_bench.iloc[:, 0]

            stock_now = float(df_live['Close'].iloc[-1])
            stock_old = float(df_live['Close'].iloc[-6])
            stock_perf = ((stock_now - stock_old) / stock_old) * 100
            
            bench_now = float(final_bench.iloc[-1])
            bench_old = float(final_bench.iloc[-6])
            bench_perf = ((bench_now - bench_old) / bench_old) * 100
            
            alpha = stock_perf - bench_perf
            
            # Dinamik Renkler
            if alpha > 2.0: 
                rs_icon = "🔥"; rs_color = "#10b981" if is_dark else "#056829"; rs_text = f"Endeksi Eziyor (+%{alpha:.1f})"
            elif alpha > 0.0: 
                rs_icon = "💪"; rs_color = "#34d399" if is_dark else "#05772f"; rs_text = f"Endeksi Yeniyor (+%{alpha:.1f})"
            elif alpha > -2.0: 
                rs_icon = "⚠️"; rs_color = "#94a3b8" if is_dark else "#9e9284"; rs_text = f"Endeksle Paralel (%{alpha:.1f})"
            else: 
                rs_icon = "🐢"; rs_color = "#ef4444" if is_dark else "#770505"; rs_text = f"Endeksin Gerisinde (%{alpha:.1f})" 

            rs_html = f"<div style='font-size:0.8rem; margin-bottom:4px; color:{rs_color};'>{rs_icon} <span style='font-weight:700; color:{c_lbl};'>RS Momentum (5Gün):</span> {rs_text}</div>"
                
        except Exception as e:
            c_err = "#94a3b8" if is_dark else "gray"
            rs_html = f"<div style='font-size:0.75rem; color:{c_err}; margin-bottom:4px;'>RS Verisi Yok: {str(e)}</div>"

        # --- D. HTML OLUŞTURMA ---
        if rs_html:
            scan_results_html += rs_html
            found_any = True
            
        # 1. STP Sonuçları
        if stp_live:
            found_any = True
            c_stp_c = "#10b981" if is_dark else "#056829"
            c_stp_m = "#f472b6" if is_dark else "#db2777"
            c_stp_t = "#4ade80" if is_dark else "#15803d"

            if stp_live['type'] == 'cross':
                scan_results_html += f"<div style='font-size:0.8rem; margin-bottom:4px; color:{c_stp_c};'>⚡ <span style='font-weight:700; color:{c_lbl};'>STP:</span> Kesişim (AL Sinyali)</div>"
                if stp_live.get('is_filtered', False):
                    scan_results_html += f"<div style='font-size:0.8rem; margin-bottom:4px; color:{c_stp_m};'>🎯 <span style='font-weight:700; color:{c_lbl};'>Momentum:</span> Başlangıç Sinyali</div>"
            elif stp_live['type'] == 'trend':
                gun = stp_live['data'].get('Gun', '?')
                scan_results_html += f"<div style='font-size:0.8rem; margin-bottom:4px; color:{c_stp_t};'>✅ <span style='font-weight:700; color:{c_lbl};'>STP:</span> Trend ({gun} Gündür)</div>"
        # 1.5 Kesin Dönüş
        if kd_res:
            found_any = True
            c_kd = "#06b6d4" if is_dark else "#0891b2"
            scan_results_html += f"<div style='font-size:0.8rem; margin-bottom:4px; color:{c_kd};'>🎯 <span style='font-weight:700; color:{c_lbl};'>Kesin Dönüş:</span> 3'lü Kesişim Onayı (Tuzak+Uyumsuzluk+Hacim)</div>"
            
        # 2. Akıllı Para
        if acc_live:
            found_any = True
            c_acc = "#a78bfa" if is_dark else "#7c3aed"
            is_pp = acc_live.get('Pocket_Pivot', False)
            icon = "⚡" if is_pp else "🤫"
            text = "Pocket Pivot (Patlama)" if is_pp else "Sessiz Toplama"
            scan_results_html += f"<div style='font-size:0.8rem; margin-bottom:4px; color:{c_acc};'>{icon} <span style='font-weight:700; color:{c_lbl};'>Akıllı Para:</span> {text}</div>"

        # 3. Breakout
        if bo_live:
            found_any = True
            c_bo_k = "#4ade80" if is_dark else "#16a34a"
            c_bo_i = "#fbbf24" if is_dark else "#d97706"
            is_firing = "TETİKLENDİ" in bo_live['Zirveye Yakınlık'] or "Sıkışma Var" in bo_live['Zirveye Yakınlık']
            prox_clean = str(bo_live['Zirveye Yakınlık']).split('<')[0].strip()
            if is_firing:
                scan_results_html += f"<div style='font-size:0.8rem; margin-bottom:4px; color:{c_bo_k};'>🔨 <span style='font-weight:700; color:{c_lbl};'>Breakout:</span> KIRILIM (Onaylı)</div>"
            else:
                scan_results_html += f"<div style='font-size:0.8rem; margin-bottom:4px; color:{c_bo_i};'>🔥 <span style='font-weight:700; color:{c_lbl};'>Breakout:</span> Isınanlar ({prox_clean})</div>"

        # 4. Minervini SEPA
        if mini_live:
            found_any = True
            c_mini = "#fb923c" if is_dark else "#ea580c"
            durum = mini_live.get('Durum', 'Trend?')
            puan = mini_live.get('Raw_Score', 0)
            scan_results_html += f"<div style='font-size:0.8rem; margin-bottom:4px; color:{c_mini};'>🦁 <span style='font-weight:700; color:{c_lbl};'>Minervini:</span> {durum} ({puan})</div>"

        # 5. Formasyonlar
        if not pat_df.empty:
            found_any = True
            c_pat = "#94a3b8" if is_dark else "#0f172a" # Temaya göre renk
            
            # Formasyon adı ve skorunu dataframe'den çek
            pat_name = pat_df.iloc[0]['Formasyon']
            pat_score = pat_df.iloc[0]['Skor']
            
            # HTML içeriğine daha belirgin şekilde ekle
            scan_results_html += f"<div style='font-size:0.8rem; margin-bottom:4px; color:{c_pat};'>📐 <span style='font-weight:700; color:{c_lbl};'>Formasyon:</span> {pat_name} <span style='color:#10b981; font-weight:bold;'>(Puan: {pat_score})</span></div>"

        # 6. Radarlar
        if r1_live and r1_live['Skor'] >= 4:
            found_any = True
            c_r1 = "#38bdf8" if is_dark else "#0369a1"
            scan_results_html += f"<div style='font-size:0.8rem; margin-bottom:4px; color:{c_r1};'>🧠 <span style='font-weight:700; color:{c_lbl};'>Radar 1:</span> Momentum ({r1_live['Skor']}/7)</div>"
        
        if r2_live and r2_live['Skor'] >= 4:
            found_any = True
            c_r2 = "#4ade80" if is_dark else "#15803d"
            setup_name = r2_live['Setup'] if r2_live['Setup'] != "-" else "Trend Takibi"
            scan_results_html += f"<div style='font-size:0.8rem; margin-bottom:4px; color:{c_r2};'>🚀 <span style='font-weight:700; color:{c_lbl};'>Radar 2:</span> {setup_name} ({r2_live['Skor']}/7)</div>"
        
        # 7. Bear Trap
        if bt_live:
            found_any = True
            c_bt = "#fcd34d" if is_dark else "#b45309"
            scan_results_html += f"<div style='font-size:0.8rem; margin-bottom:4px; color:{c_bt};'>🪤 <span style='font-weight:700; color:{c_lbl};'>Bear Trap:</span> {bt_live['Zaman']} (Vol: {bt_live['Hacim_Kat']})</div>"
            
    # 8. RSI UYUMSUZLUKLARI
    if pa_data:
        div_info = pa_data.get('div', {})
        div_type = div_info.get('type', 'neutral')
        
        if div_type == 'bullish':
            found_any = True
            c_div_bull = "#10b981" if is_dark else "#15803d"
            scan_results_html += f"<div style='font-size:0.8rem; margin-bottom:4px; color:{c_div_bull};'>💎 <span style='font-weight:700; color:{c_lbl};'>RSI Uyumsuzluk:</span> POZİTİF (Alış?)</div>"
        elif div_type == 'bearish':
            found_any = True
            c_div_bear = "#ef4444" if is_dark else "#b91c1c"
            scan_results_html += f"<div style='font-size:0.8rem; margin-bottom:4px; color:{c_div_bear};'>🐻 <span style='font-weight:700; color:{c_lbl};'>RSI Uyumsuzluk:</span> NEGATİF (Satış?)</div>"

    # 9. DİPTEN DÖNÜŞ KONTROLÜ
    if bt_live and pa_data and pa_data.get('div', {}).get('type') == 'bullish':
        found_any = True
        is_star_candidate = True
        c_dip = "#34d399" if is_dark else "#059669"
        scan_results_html += f"<div style='font-size:0.8rem; margin-bottom:4px; color:{c_dip}; font-weight:bold;'>⚓ DİPTEN DÖNÜŞ SİNYALİ?</div>"

    # 10. İSTATİSTİKSEL Z-SCORE TARAMASI
    z_score_val = round(calculate_z_score_live(df_live), 2)
    
    # Düşüş Senaryoları
    if z_score_val <= -2.0: 
        found_any = True
        z_col = "#10b981" if is_dark else "#059669"
        z_bg = "rgba(16, 185, 129, 0.1)" if is_dark else "#ecfdf5"
        z_t1 = "#34d399" if is_dark else "#047857"
        z_t2 = "#a7f3d0" if is_dark else "#065f46"
        scan_results_html += f"<div style='margin-top:6px; font-size:0.8rem; color:{z_col}; font-weight:bold;'>🔥 İstatistiksel DİP (Z-Score: {z_score_val:.2f})</div>"
        scan_results_html += f"<div style='background:{z_bg}; border-left:3px solid {z_col}; padding:6px; margin-top:2px; border-radius:0 4px 4px 0;'><div style='font-size:0.7rem; color:{z_t1}; font-weight:bold; margin-bottom:2px;'>🎓 GÜÇLÜ ANOMALİ</div><div style='font-size:0.7rem; color:{z_t2}; line-height:1.3;'>Fiyat -2 sapmayı kırdı. İstatistiksel olarak dönüş ihtimali yüksek.</div></div>"
    elif z_score_val <= -1.5: 
        found_any = True
        c_z2 = "#fbbf24" if is_dark else "#d97706"
        scan_results_html += f"<div style='margin-top:6px; font-size:0.8rem; color:{c_z2};'>⚠️ Dibe Yaklaşıyor (Z-Score: {z_score_val:.2f})</div>"
    elif z_score_val <= -1.0: 
        found_any = True
        c_z1 = "#38bdf8" if is_dark else "#0284c7"
        scan_results_html += f"<div style='margin-top:6px; font-size:0.8rem; color:{c_z1};'>📉 Ucuzluyor (Z-Score: {z_score_val:.2f})</div>"

    # Yükseliş Senaryoları
    elif z_score_val >= 2.0: 
        found_any = True
        z_col = "#ef4444" if is_dark else "#dc2626"
        z_bg = "rgba(239, 68, 68, 0.1)" if is_dark else "#fef2f2"
        z_t1 = "#f87171" if is_dark else "#b91c1c"
        z_t2 = "#fca5a5" if is_dark else "#7f1d1d"
        scan_results_html += f"<div style='margin-top:6px; font-size:0.8rem; color:{z_col}; font-weight:bold;'>🔥 İstatistiksel TEPE (Z-Score: {z_score_val:.2f})</div>"
        scan_results_html += f"<div style='background:{z_bg}; border-left:3px solid {z_col}; padding:6px; margin-top:2px; border-radius:0 4px 4px 0;'><div style='font-size:0.7rem; color:{z_t1}; font-weight:bold; margin-bottom:2px;'>🎓 GÜÇLÜ ANOMALİ</div><div style='font-size:0.7rem; color:{z_t2}; line-height:1.3;'>Fiyat +2 sapmayı aştı. Düzeltme riski çok yüksek.</div></div>"
    elif z_score_val >= 1.5: 
        found_any = True
        c_z2 = "#f97316" if is_dark else "#ea580c"
        scan_results_html += f"<div style='margin-top:6px; font-size:0.8rem; color:{c_z2};'>⚠️ Tepeye Yaklaşıyor (Z-Score: {z_score_val:.2f})</div>"
    elif z_score_val >= 1.0: 
        found_any = True
        c_z1 = "#eab308" if is_dark else "#854d0e"
        scan_results_html += f"<div style='margin-top:6px; font-size:0.8rem; color:{c_z1};'>📈 Pahalılanıyor (Z-Score: {z_score_val:.2f})</div>"

    # 11. ROYAL FLUSH (ELİT) CANLI KONTROL
    try:
        if df_live is not None and len(df_live) >= 200:
            _c    = df_live['Close']
            _cp   = float(_c.iloc[-1])
            _s200 = float(_c.rolling(200).mean().iloc[-1])
            _s50  = float(_c.rolling(50).mean().iloc[-1])
            _d    = _c.diff()
            _g    = _d.where(_d > 0, 0).rolling(14).mean()
            _l    = (-_d.where(_d < 0, 0)).rolling(14).mean()
            _rsi  = float(100 - (100 / (1 + _g / _l)).iloc[-1])
            if _cp > _s200 and _cp > _s50 and _rsi < 70:
                _rf_c = "#60a5fa" if is_dark else "#1d4ed8"
                scan_results_html += f"<div style='font-size:0.8rem; margin-top:6px; margin-bottom:4px; color:{_rf_c}; font-weight:bold;'>♠️ <span style='font-weight:700; color:{c_lbl};'>Royal Flush (Elit):</span> Uzun vade trend güçlü, yapı sağlam, RSI güvenli bölge.</div>"
                found_any = True
    except: pass

    # --- HTML ÇIKTISI RENDER ---
    if found_any:
        star_title = " ⭐" if is_star_candidate else ""
        display_ticker_safe = active_t.replace(".IS", "").replace("=F", "")
        
        # Tema Renkleri
        bg_panel = "rgba(17, 24, 39, 0.6)" if is_dark else "#f8fafc"
        border_panel = "rgba(255,255,255,0.05)" if is_dark else "#cbd5e1"
        title_col = "#38bdf8" if is_dark else "#1e3a8a"
        title_border = "rgba(255,255,255,0.1)" if is_dark else "#e2e8f0"

        st.markdown(f"""
        <div style="background:{bg_panel}; border:1px solid {border_panel}; border-radius:8px; padding:12px; margin-bottom:15px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
            <div style="font-size:0.95rem; font-weight:800; color:{title_col}; border-bottom:1px solid {title_border}; padding-bottom:6px; margin-bottom:8px; display:flex; align-items:center; gap:5px;">
                📋 TARAMA SONUÇLARI - {display_ticker_safe}{star_title}
            </div>
            {scan_results_html}
        </div>
        """, unsafe_allow_html=True)
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
        render_golden_trio_banner(ict_data_check, sent_data_check)
    except Exception as e:
        pass # Bir hata olursa sessizce geç, ekranı bozma.

    # Royal Flush (Elit) — tarama yapmadan canlı hesaplar (AF + SMA200 + SMA50 + RSI < 70)
    render_royal_flush_live_banner(st.session_state.ticker, ict_data_check, sent_data_check)

    # Royal Flush 3.0 (Dipten Dönüş versiyonu)
    render_royal_flush_3_0_banner(st.session_state.ticker)


    st.markdown("<hr style='margin-top:15px; margin-bottom:10px;'>", unsafe_allow_html=True)


    # ==============================================================================
    # 🎯 PİYASA TARAMALARI VE FIRSATLAR (SEKMELİ MODERN ARAYÜZ)
    # ==============================================================================
    # 1. Karanlık/Aydınlık moda otomatik uyum sağlayan CSS değişkenli şık başlık
    header_html = """
    <div style="
        background: linear-gradient(135deg, rgba(59, 130, 246, 0.1) 0%, rgba(16, 185, 129, 0.05) 100%);
        border: 1px solid rgba(59, 130, 246, 0.3);
        border-radius: 12px;
        padding: 16px 20px;
        margin-top: 15px;
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
            <span style="color: #1e3a8a;">🎯 PİYASA TARAMALARI</span> VE FIRSATLAR
        </div>
    </div>
    """
    st.markdown(header_html, unsafe_allow_html=True)

    # 2. Tüm sekmeleri içine alacak şık bir dış çerçeve açıyoruz
    with st.container(border=True):
        
        # State Tanımlamaları (Sekmelerin hata vermemesi için)
        if 'golden_results' not in st.session_state: st.session_state.golden_results = None
        if 'royal_results' not in st.session_state: st.session_state.royal_results = None

        # ÜST Ana Sekme (Tab) Oluşturuyoruz
        tab_confluence, tab_elit, tab_master = st.tabs([
            "🔥 CONFLUENCE",
            "💎 ELİTLER",
            "👑 TOP 20 MASTER"
        ])

    # ---------------------------------------------------------
    # SEKME 0: 🔥 CONFLUENCE (Cross-Scanner Metodoloji Kesişimi)
    # ---------------------------------------------------------
    with tab_confluence:
        _dark = st.session_state.get('dark_mode', False)
        hits  = st.session_state.get('confluence_hits')

        # --- Piyasa Rejimi Uyarısı ---
        try:
            _bench = get_benchmark_data(st.session_state.get('category', 'S&P 500'))
            if _bench is not None and len(_bench) >= 200:
                _b200 = float(_bench.rolling(200).mean().iloc[-1])
                _bcur = float(_bench.iloc[-1])
                if _bcur < _b200:
                    _warn_bg  = "#3b0f0f" if _dark else "#fef2f2"
                    _warn_clr = "#fca5a5" if _dark else "#991b1b"
                    st.markdown(f"<div style='background:{_warn_bg}; border:1px solid {_warn_clr}; border-radius:6px; padding:8px 12px; margin-bottom:10px; font-size:0.82rem; color:{_warn_clr}; font-weight:700;'>🐻 BEAR MODE — Benchmark 200 SMA altında. Long sinyaller daha riskli, pozisyon büyüklüğünü küçült.</div>", unsafe_allow_html=True)
        except: pass

        if not hits:
            _info_clr = "#94a3b8" if _dark else "#64748b"
            st.markdown(f"<div style='text-align:center; color:{_info_clr}; padding:40px 0; font-size:0.9rem;'>Henüz confluence hesaplanmadı.<br><b>Master Scan</b> çalıştırın veya birden fazla scanner'ı ayrı ayrı çalıştırın.</div>", unsafe_allow_html=True)
        else:
            _full    = [h for h in hits if h['group_count'] == 3]
            _partial = [h for h in hits if h['group_count'] == 2]

            _info_clr2 = '#94a3b8' if _dark else '#64748b'
            st.markdown(f"<div style='font-size:0.78rem; color:{_info_clr2}; margin-bottom:8px; text-align:center;'>Birden fazla bağımsız yöntemin aynı hisseyi işaret ettiği durumlar — ne kadar fazla yöntem onaylarsa o kadar güvenilir sinyal</div>", unsafe_allow_html=True)

            if _full:
                _hdr_bg  = "#2d1b4e" if _dark else "#f3e8ff"
                _hdr_clr = "#c084fc" if _dark else "#6d28d9"
                st.markdown(f"<div style='background:{_hdr_bg}; border-radius:6px; padding:6px 12px; font-size:0.82rem; font-weight:800; color:{_hdr_clr}; margin-bottom:6px;'>🔥 TAM CONFLUENCE — 3/3 Grup ({len(_full)} hisse)</div>", unsafe_allow_html=True)

            with st.container(height=520, border=False):
                for i, item in enumerate(hits):
                    sym     = item['Sembol'].replace('.IS', '')
                    gc      = item['group_count']
                    ts      = item['total_scanners']
                    price   = item['price']
                    p_str   = f"{int(price)}" if price >= 1000 else f"{price:.2f}"
                    missing = item.get('missing_groups', [])
                    scanned = item.get('scanned_groups', 3)

                    # Kart renk
                    if gc == 3:
                        card_bg  = "#1e0f35" if _dark else "#faf5ff"
                        card_bdr = "#7c3aed"
                        badge_bg = "linear-gradient(90deg,#7c3aed,#4f46e5)"
                        lbl      = f"✅ {ts} farklı yöntemle onaylandı"
                    else:
                        card_bg  = "#0f1e2d" if _dark else "#f0f9ff"
                        card_bdr = "#0ea5e9"
                        badge_bg = "#0ea5e9"
                        lbl      = f"✅ {ts} yöntemle onaylandı"

                    sym_clr  = "#f1f5f9" if _dark else "#0f172a"
                    txt_clr  = "#94a3b8" if _dark else "#64748b"

                    # Grup etiket haritası — abone diline çevrildi
                    _grp_labels = {
                        'yapi':      '✅ Fiyat yapısı sağlam',
                        'momentum':  '✅ Yükseliş ivmesi var',
                        'formasyon': '✅ Grafik yapısı hazır',
                    }
                    _grp_miss = {
                        'yapi':      'Yapısal onay yok',
                        'momentum':  'İvme onayı yok',
                        'formasyon': 'Grafik onayı yok',
                    }

                    # Grup rozetleri
                    group_badges = ""
                    for g in item['hit_groups']:
                        sc_list = ", ".join(g['scanners'])
                        grp_lbl = _grp_labels.get(g['key'], f"✅ {g['label']}")
                        group_badges += f'<span style="background:{"#1e3a2f" if _dark else "#dcfce7"}; color:{"#4ade80" if _dark else "#166534"}; border-radius:4px; padding:2px 7px; font-size:0.72rem; font-weight:700; margin-right:4px;" title="{sc_list}">{grp_lbl}</span>'
                    for mg in missing:
                        mg_key = next((k for k,v in {'yapi':'🏗️ Yapısal','momentum':'📈 Momentum','formasyon':'💎 Formasyon'}.items() if v == mg), None)
                        mg_lbl = _grp_miss.get(mg_key, mg) if mg_key else mg
                        group_badges += f'<span style="background:{"#1f2937" if _dark else "#f1f5f9"}; color:{"#6b7280" if _dark else "#94a3b8"}; border-radius:4px; padding:2px 7px; font-size:0.72rem; font-weight:700; margin-right:4px;">{mg_lbl}</span>'

                    # Scanner isimleri — küçük gri, teknik kullanıcı için
                    all_scanners = [s for g in item['hit_groups'] for s in g['scanners']]
                    sc_txt = " · ".join(all_scanners)

                    # Eksik grup uyarısı
                    partial_warn = ""
                    if scanned < 3:
                        partial_warn = f'<div style="font-size:0.62rem; color:{"#f87171" if _dark else "#dc2626"}; margin-top:4px;">⚠️ {3-scanned} grup henüz taranmadı — tam sonuç için Master Scan önerilir</div>'

                    html_card = f"""
<div style="background:{card_bg}; border:2px solid {card_bdr}; border-radius:8px; padding:10px 12px; margin-bottom:6px;">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
    <span style="font-weight:900; font-size:1.1rem; color:{sym_clr};">{sym}</span>
    <span style="background:{badge_bg}; color:white; border-radius:10px; padding:2px 10px; font-size:0.72rem; font-weight:800;">{lbl}</span>
  </div>
  <div style="margin-bottom:6px;">{group_badges}</div>
  <div style="font-size:0.65rem; color:{txt_clr}; font-style:italic;">{sc_txt}</div>
  {partial_warn}
</div>"""
                    st.markdown(html_card, unsafe_allow_html=True)
                    if st.button(f"📊 {sym} İncele ({p_str})", key=f"conf_btn_{sym}_{i}", use_container_width=True):
                        st.session_state.ticker = item['Sembol']
                        on_scan_result_click(item['Sembol'])
                        st.rerun()
                    st.markdown("<div style='margin-bottom:8px'></div>", unsafe_allow_html=True)

    # ---------------------------------------------------------
    # SEKME 1: 💎 ELİTLER (Royal, Golden)
    # ---------------------------------------------------------
    with tab_elit:
        if st.button("🔄 Golden Trio + Royal Flush Taraması Yap", use_container_width=True, key="btn_elit_tara"):
            with st.spinner("Elit hisseler aranıyor..."):
                scan_list = ASSET_GROUPS.get(st.session_state.category, [])
                if not scan_list:
                    st.error("⚠️ Lütfen önce sol menüden bir hisse grubu seçin.")
                else:
                    df_radar2 = radar2_scan(scan_list)
                    st.session_state.radar2_data = df_radar2
                    df_golden, df_royal = get_golden_trio_batch_scan(scan_list)
                    st.session_state.golden_results = df_golden.sort_values(by="M.Cap", ascending=False).reset_index(drop=True) if not df_golden.empty else pd.DataFrame()
                    st.session_state.royal_results = df_royal.sort_values(by="M.Cap", ascending=False).reset_index(drop=True) if not df_royal.empty else pd.DataFrame()
                    st.rerun()

        with st.container(height=350, border=False):
            # ROYAL FLUSH
            if st.session_state.get('royal_results') is not None and not st.session_state.royal_results.empty:
                st.markdown(f"<div style='background:linear-gradient(90deg, #1e3a8a 0%, #3b82f6 100%); border-radius:6px; padding:6px; margin-bottom:8px; font-size:0.9rem; font-weight:bold; color:white; text-align:center;'>♠️ ROYAL FLUSH (ELİTLER) ({len(st.session_state.royal_results)})</div>", unsafe_allow_html=True)
                _dark_e = st.session_state.get('dark_mode', False)
                for index, row in st.session_state.royal_results.head(6).iterrows():
                    raw_symbol = row['Hisse']
                    display_symbol = raw_symbol.replace(".IS", "")
                    fiyat_val = row['Fiyat']
                    fiyat_str = f"{int(fiyat_val)}" if fiyat_val >= 1000 else f"{fiyat_val:.2f}"
                    warn_icon = "🟠 " if row.get('Warning', False) else ""
                    onay_txt = row.get('Onay', '')
                    # Onay metnini abone diline çevir
                    kriterler = []
                    if 'Trend(200)' in onay_txt or 'SMA200' in onay_txt: kriterler.append("✅ Uzun vade trendi yukarı")
                    if 'Yapı(50)' in onay_txt or 'SMA50' in onay_txt: kriterler.append("✅ Kısa vade yapısı sağlam")
                    if 'RS' in onay_txt: kriterler.append("✅ Endeksten güçlü")
                    if 'Enerji' in onay_txt: kriterler.append("✅ Hacim/enerji artıyor")
                    krit_html = " &nbsp;".join(kriterler) if kriterler else onay_txt
                    _card_bg = "#0f1e35" if _dark_e else "#eff6ff"
                    _txt_clr = "#93c5fd" if _dark_e else "#1e40af"
                    _sym_clr = "#f1f5f9" if _dark_e else "#0f172a"
                    st.markdown(f"""<div style='background:{_card_bg}; border:2px solid #3b82f6; border-radius:8px; padding:8px 12px; margin-bottom:4px;'>
  <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;'>
    <span style='font-weight:900; font-size:1rem; color:{_sym_clr};'>♠️ {display_symbol}</span>
    <span style='font-family:monospace; font-weight:800; font-size:1rem; color:#3b82f6;'>{warn_icon}{fiyat_str}</span>
  </div>
  <div style='font-size:0.72rem; color:{_txt_clr}; line-height:1.6;'>{krit_html}</div>
</div>""", unsafe_allow_html=True)
                    if st.button(f"📊 {display_symbol} İncele ({fiyat_str})", key=f"btn_royal_{index}", use_container_width=True):
                        on_scan_result_click(raw_symbol)
                        st.rerun()
            
            # ALTIN FIRSATLAR
            if st.session_state.get('golden_results') is not None and not st.session_state.golden_results.empty:
                st.markdown(f"<div style='background:rgba(245, 158, 11, 0.1); border:1px solid #f59e0b; border-radius:6px; padding:6px; margin-top:10px; margin-bottom:8px; font-size:0.9rem; color:#d97706; font-weight:bold; text-align:center;'>🦁 ALTIN FIRSATLAR ({len(st.session_state.golden_results)})</div>", unsafe_allow_html=True)
                st.caption("Kriterler: Son 10 gün Endeksten Güçlü + Son 60 güne göre Ucuz + Hacim/Enerji artıyor")
                cols_gold = st.columns(3)
                for index, row in st.session_state.golden_results.head(12).iterrows():
                    raw_symbol = row['Hisse']
                    display_symbol = raw_symbol.replace(".IS", "")
                    fiyat_str = f"🟠 {row['Fiyat']:.2f}" if row.get('Warning', False) else f"{row['Fiyat']:.2f}"
                    if cols_gold[index % 3].button(f"🦁 {display_symbol}\n{fiyat_str}", key=f"btn_gold_{index}", use_container_width=True):
                        on_scan_result_click(raw_symbol)
                        st.rerun()


    # ---------------------------------------------------------
    # SEKME 2: 👑 TOP 20 MASTER LİSTE (Yeni Şık HTML Kartları ile)
    # ---------------------------------------------------------
    with tab_master:
        if 'top_20_summary' in st.session_state and st.session_state.top_20_summary:
            st.markdown('<div style="font-size:0.85rem; color:#64748b; margin-bottom:10px; text-align:center;">Tüm algoritmalardan en çok onay alan elit hisseler</div>', unsafe_allow_html=True)
            
            # Tek sütunlu liste için container
            with st.container(height=500, border=True):
                for i, item in enumerate(st.session_state.top_20_summary):
                    # Veri Hazırlığı
                    sym = item['Sembol'].replace('.IS', '') 
                    score = int(item['score'])
                    onay_sayisi = item.get('onay_sayisi', 0)
                    
                    price_val = float(item['price'])
                    price_str = f"{int(price_val)}" if price_val >= 1000 else f"{price_val:.2f}"
                    sources_str = ", ".join(item['sources'][:3]) 
                    if len(item['sources']) > 3: sources_str += "..."
                    
                    # Renk Ayarları
                    _dark = st.session_state.get('dark_mode', False)
                    if score >= 80:
                        bg_color    = "#2d2007" if _dark else "#fffbeb"
                        border_color = "#f59e0b"
                        score_bg    = "linear-gradient(90deg, #f59e0b 0%, #d97706 100%)"
                    elif score >= 50:
                        bg_color    = "#0f1e35" if _dark else "#f8fafc"
                        border_color = "#3b82f6"
                        score_bg    = "#3b82f6"
                    else:
                        bg_color    = "#1a1f2e" if _dark else "#ffffff"
                        border_color = "#475569"
                        score_bg    = "#64748b"

                    sym_clr    = "#f1f5f9" if _dark else "#0f172a"
                    detail_clr = "#e2e8f0" if _dark else "#0f172a"
                    src_clr    = "#94a3b8"

                    # 4+ scanner onayı için özel badge
                    if onay_sayisi >= 4:
                        onay_badge = f'<span style="background: linear-gradient(90deg,#7c3aed,#4f46e5); color:white; padding:2px 8px; border-radius:10px; font-size:0.72rem; font-weight:800; margin-left:6px;">🎖️ {onay_sayisi} Onay</span>'
                    else:
                        onay_badge = f'<span style="background:#f1f5f9; color:#1e293b; padding:2px 8px; border-radius:10px; font-size:0.72rem; font-weight:700; margin-left:6px;">✅ {onay_sayisi} Onay</span>'

                    # Kart HTML
                    html_card = f"""
    <div style="background-color: {bg_color}; padding: 12px; border-radius: 8px; border: 2px solid {border_color}; margin-bottom: 5px; text-align: left;">
        <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid {border_color}44; padding-bottom: 6px; margin-bottom: 8px;">
            <span style="font-weight: 900; font-size: 1.2rem; color: {sym_clr};">{sym} {onay_badge}</span>
            <span style="background: {score_bg}; color: white; padding: 2px 10px; border-radius: 12px; font-weight: 800; font-size: 0.8rem;">Skor: {score}/100</span>
        </div>
        <div style="font-size: 0.8rem; color: {detail_clr}; font-weight: 600; line-height: 1.4; padding: 8px; background: rgba(255,255,255,0.07); border-radius: 5px; border-left: 4px solid {border_color}; text-align: left;">
            {item['katalizor']}
        </div>
        <div style="font-size: 0.65rem; color: {src_clr}; margin-top: 8px; font-weight: 700;">
            <span style="color: #94a3b8;">Kesişen Sinyaller:</span> {sources_str}
        </div>
    </div>"""
                    st.markdown(html_card, unsafe_allow_html=True)
                    
                    # Buton (Kartın hemen altında)
                    if st.button(f"📊 {sym} İncele ({price_str})", key=f"top20_btn_{sym}_{i}", use_container_width=True):
                        # DÜZELTME BURADA: sym (uzantısız) yerine item['Sembol'] (orijinal) kullanıyoruz.
                        st.session_state.ticker = item['Sembol']
                        st.rerun()
                    
                    # Her kartın arasına hafif boşluk
                    st.markdown("<div style='margin-bottom: 15px;'></div>", unsafe_allow_html=True)
        else:
            st.info("Lütfen sol menüdeki 'TÜM PİYASAYI TARA' butonunu kullanarak listeyi oluşturun.")
    
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
            <span style="color: #1e3a8a;">⚡ GELİŞMİŞ RADARLAR</span> VE SİNYALLER
        </div>
    </div>
    """
    st.markdown(header_html_bottom, unsafe_allow_html=True)

    # ALT SEKMELERİ YİNE ŞIK BİR ÇERÇEVE (CONTAINER) İÇİNE ALIYORUZ
    with st.container(border=True):
        
        tab_gm, tab_radar = st.tabs([
            "🚀 PATLAMA (GM)", 
            "📡 RADARLAR"
        ])
    # ---------------------------------------------------------
    # SEKME 3: 🚀 PATLAMA ADAYLARI (GRANDMASTER)
    # ---------------------------------------------------------
    with tab_gm:
        if st.button("🔄 Grandmaster Taramasını Çalıştır", use_container_width=True, key="btn_gm_scan_tab"):
            with st.spinner("Grandmaster Algoritması çalışıyor..."):
                st.session_state.gm_results = scan_grandmaster_batch(ASSET_GROUPS.get(st.session_state.category, []))
                st.rerun()
                
        if st.session_state.get('gm_results') is not None and not st.session_state.gm_results.empty:
            with st.container(height=350, border=False):
                for i, row in st.session_state.gm_results.iterrows():
                    sc = row['Skor']
                    # Semboldeki .IS veya -USD gibi uzantıları silerek ekrana basıyoruz
                    temiz_sembol = row['Sembol'].replace('.IS', '').replace('-USD', '')
                    label = f"{i+1}. {temiz_sembol} | SKOR: {sc}"
                    detail_txt = f"Vol: {row['Hacim_Kat']}x | Z-Score: {row['Z_Score']} | Alpha: %{row.get('Alpha', 0)}"
                    
                    if st.button(label, key=f"gm_tab_{i}", use_container_width=True, help=f"Uyarılar: {row['Uyarılar']}"):
                        on_scan_result_click(row['Sembol'])
                        st.rerun()
                    
                    st.markdown(f"<div style='font-size:0.75rem; color:#3b82f6; margin-top:-10px; margin-bottom:2px; padding-left:10px; font-weight:700;'>{row.get('Hikaye', '-')}</div>", unsafe_allow_html=True)
                    st.markdown(f"<div style='font-size:0.7rem; color:#64748b; margin-bottom:10px; padding-left:10px;'>{detail_txt}</div>", unsafe_allow_html=True)
        else:
            st.info("Kriterlere uyan (Skor > 40) hisse bulunamadı veya henüz tarama yapılmadı.")

    # ---------------------------------------------------------
    # SEKME 4: 📡 RADARLAR VE KESİŞİMLER (R1 + R2)
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
        st.markdown(f"<div style='font-size:0.9rem;font-weight:bold; margin-bottom:8px; color:#1e40af; background-color:rgba(30, 64, 175, 0.05); padding:5px; border-radius:5px; border:1px solid #1e40af; text-align:center;'>🎯 Ortak Fırsatlar (R1 + R2 Kesişim)</div>", unsafe_allow_html=True)
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
        st.markdown("<div style='font-size:0.8rem; font-weight:bold; color:#0284c7; margin-bottom:5px;'>🧠 Radar 1 (Momentum)</div>", unsafe_allow_html=True)
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
        st.markdown("<div style='font-size:0.8rem; font-weight:bold; color:#15803d; margin-bottom:5px;'>🚀 Radar 2 (Trend Setup)</div>", unsafe_allow_html=True)
        with st.container(height=150, border=False):
            if df2 is not None and not df2.empty:
                cols_r2 = st.columns(3)
                for i, row in df2.head(15).iterrows():
                    sym = row["Sembol"]
                    setup = row['Setup'] if row['Setup'] != "-" else "Trend"
                    if cols_r2[i % 3].button(f"🚀 {int(row['Skor'])}/7\n{sym.replace('.IS','')}", key=f"r2_tab_{sym}_{i}", use_container_width=True, help=f"Setup: {setup}"):
                        on_scan_result_click(sym); st.rerun()
            else: st.caption("Veri yok.")