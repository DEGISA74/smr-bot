import streamlit as st
import yfinance as yf
import pandas as pd
import feedparser
import urllib.parse
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

# ==============================================================================
# 1. AYARLAR VE STİL
# ==============================================================================
st.set_page_config(
    page_title="SMART MONEY RADAR", 
    layout="wide",
    page_icon="💸"
)

# Tema seçeneği kaldırıldı, varsayılan "Buz Mavisi" olarak sabitlendi.
if 'theme' not in st.session_state:
    st.session_state.theme = "Buz Mavisi"

THEMES = {
    "Beyaz": {"bg": "#FFFFFF", "box_bg": "#F8F9FA", "text": "#000000", "border": "#DEE2E6", "news_bg": "#FFFFFF"},
    "Kirli Beyaz": {"bg": "#FAF9F6", "box_bg": "#FFFFFF", "text": "#2C3E50", "border": "#E5E7EB", "news_bg": "#FFFFFF"},
    "Buz Mavisi": {"bg": "#F0F8FF", "box_bg": "#FFFFFF", "text": "#0F172A", "border": "#BFDBFE", "news_bg": "#FFFFFF"}
}
current_theme = THEMES[st.session_state.theme]

st.markdown(f"""
<style>
    section[data-testid="stSidebar"] {{ width: 350px !important; }}

    /* --- METRIC (SONUÇ KUTULARI) YAZI BOYUTU AYARI --- */
    div[data-testid="stMetricValue"] {{ font-size: 0.7rem !important; }}
    div[data-testid="stMetricLabel"] {{ font-size: 0.7rem !important; font-weight: 700; }}
    div[data-testid="stMetricDelta"] {{ font-size: 0.7rem !important; }}
    /* ------------------------------------------------ */

    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=JetBrains+Mono:wght+400;700&display=swap');
    
    html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; color: {current_theme['text']}; }}
    .stApp {{ background-color: {current_theme['bg']}; }}
    
    section.main > div.block-container {{ padding-top: 1rem; padding-bottom: 1rem; }}
    
    .stMetricValue, .money-text {{ font-family: 'JetBrains Mono', monospace !important; }}
    
    .stat-box-small {{
        background: {current_theme['box_bg']}; border: 1px solid {current_theme['border']};
        border-radius: 4px; padding: 8px; text-align: center; margin-bottom: 10px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }}
    .stat-label-small {{ font-size: 0.6rem; color: #64748B; text-transform: uppercase; margin: 0; font-weight: 700; letter-spacing: 0.5px; }}
    .stat-value-small {{ font-size: 1.1rem; font-weight: 700; color: {current_theme['text']}; margin: 2px 0 0 0; }}
    .stat-delta-small {{ font-size: 0.8rem; margin-left: 6px; font-weight: 600; }}
    
    hr {{ margin-top: 0.2rem; margin-bottom: 0.5rem; }}
    .stSelectbox, .stTextInput {{ margin-bottom: -10px; }}
    
    .delta-pos {{ color: #16A34A; }} .delta-neg {{ color: #DC2626; }}
    .news-card {{ background: {current_theme['news_bg']}; border-left: 3px solid {current_theme['border']}; padding: 6px; margin-bottom: 6px; font-size: 0.78rem; }}
    
    /* --- GÜÇLENDİRİLMİŞ MAVİ BUTON (KESİN) --- */
    /* Araya > koymadık, böylece buton kutunun dibinde de olsa bulur */
    div.stButton button[data-testid="baseButton-primary"] {{
        background-color: #2563EB !important;
        border-color: #2563EB !important;
        color: white !important;
    }}

    div.stButton button[data-testid="baseButton-primary"]:hover {{
        background-color: #1D4ED8 !important;
        border-color: #1D4ED8 !important;
        color: white !important;
    }}
    
    .stButton button {{ 
        width: 100%; border-radius: 4px;
        font-size: 0.75rem;
        padding: 0.1rem 0.4rem;
    }}
    
    .info-card {{
        background: {current_theme['box_bg']}; border: 1px solid {current_theme['border']};
        border-radius: 6px; 
        padding: 6px;
        margin-top: 5px; 
        margin-bottom: 5px;
        font-size: 0.8rem;
        font-family: 'Inter', sans-serif;
    }}
    .info-header {{ font-weight: 700; color: #1e3a8a; border-bottom: 1px solid {current_theme['border']}; padding-bottom: 4px; margin-bottom: 4px; }}
    .info-row {{ display: flex; align-items: flex-start; margin-bottom: 2px; }}
    
    .label-short {{ font-weight: 600; color: #64748B; width: 80px; flex-shrink: 0; }}
    .label-long {{ font-weight: 600; color: #64748B; width: 100px; flex-shrink: 0; }} 
    
    .info-val {{ color: {current_theme['text']}; font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; }}
    
    .edu-note {{
        font-size: 0.75rem;
        color: #64748B;
        font-style: italic;
        margin-top: 2px;
        margin-bottom: 6px;
        line-height: 1.3;
        padding-left: 0px;
    }}

    .tech-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 4px; }}
    .tech-item {{ display: flex; align-items: center; font-size: 0.8rem; }}
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
priority_sp = ["^GSPC", "^DJI", "^NDX", "^IXIC","QQQI", "AGNC", "ARCC", "TSPY", "JEPI", "MO", "JEPQ"]

# S&P 500'ün Tamamı (503 Hisse - Güncel)
raw_sp500_rest = [
    "A", "AAL", "AAPL", "ABBV", "ABNB", "ABT", "ACGL", "ACN", "ADBE", "ADI", "ADM", "ADP", "ADSK", "AEE", "AEP", "AES", "AFL", "AIG", "AIZ", "AJG", 
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
    "ILMN", "INCY", "INTC", "INTU", "INVH", "IP", "IPG", "IQV", "IR", "IRM", "ISRG", "IT", "ITW", "IVZ", "J", "JBHT", "JBL", "JCI", "JKHY", "JNJ", 
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
    "BNB-USD", "SOL-USD", "XRP-USD", "ADA-USD", "DOGE-USD", "AVAX-USD", "TRX-USD", 
    "LINK-USD", "DOT-USD", "MATIC-USD", "LTC-USD", "BCH-USD", "UNI-USD", "ATOM-USD", 
    "XLM-USD", "ETC-USD", "FIL-USD", "HBAR-USD", "APT-USD", "NEAR-USD", "VET-USD", 
    "QNT-USD", "AAVE-USD", "ALGO-USD"
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
    "QRVO", "AVTR", "FTNT", "ENPH", "SEDG", "BIIB", "CSGP"
]
raw_nasdaq = sorted(list(set(raw_nasdaq)))

# --- BIST LİSTESİ (GENİŞLETİLMİŞ - BIST 200+ Adayları) ---
priority_bist_indices = ["XU100.IS", "XU030.IS", "XBANK.IS", "EREGL.IS", "SISE.IS", "TUPRS.IS"]

# Buraya BIST 100 + BIST TUM'den seçme hisseleri ekliyoruz
raw_bist_stocks = [
    # BIST 30 & 100 DEMİRBAŞLARI
    "AEFES.IS", "AGHOL.IS", "AKBNK.IS", "AKCNS.IS", "AKSA.IS", "AKSEN.IS", "ALARK.IS", "ALBRK.IS", 
    "ARCLK.IS", "ASELS.IS", "ASTOR.IS", "BIMAS.IS", "BRSAN.IS", "CCOLA.IS", "CIMSA.IS", "CWENE.IS", 
    "DOAS.IS", "DOHOL.IS", "EGEEN.IS", "EKGYO.IS", "ENJSA.IS", "ENKAI.IS", "EREGL.IS", "EUPWR.IS", 
    "FROTO.IS", "GARAN.IS", "GESAN.IS", "GUBRF.IS", "HALKB.IS", "HEKTS.IS", "ISCTR.IS", "ISGYO.IS", 
    "KCHOL.IS", "KONTR.IS", "KOZAA.IS", "KOZAL.IS", "KRDMD.IS", "MGROS.IS", "MIATK.IS", "ODAS.IS", 
    "OYAKC.IS", "PETKM.IS", "PGSUS.IS", "SAHOL.IS", "SASA.IS", "SISE.IS", "SMRTG.IS", "SOKM.IS", 
    "TAVHL.IS", "TCELL.IS", "THYAO.IS", "TKFEN.IS", "TOASO.IS", "TSKB.IS", "TTKOM.IS", "TUPRS.IS", 
    "VAKBNK.IS", "VESTL.IS", "YKBNK.IS", "YYLGD.IS",
    
    # YAN TAHTALAR VE POPÜLER HİSSELER (BIST 200 Adayları)
    "ADEL.IS", "AGESA.IS", "AGROT.IS", "AKFGY.IS", "AKGRT.IS", "AKYHO.IS", "ALFAS.IS", "ALGYO.IS", 
    "ALKIM.IS", "ANELE.IS", "ANGEN.IS", "ANSGR.IS", "ARZUM.IS", "ASGYO.IS", "ATAKP.IS", "AVOD.IS", 
    "AVPGY.IS", "AYDEM.IS", "AYGAZ.IS", "AZTEK.IS", "BAGFS.IS", "BANVT.IS", "BARMA.IS", "BASGZ.IS", 
    "BAYRK.IS", "BERA.IS", "BFREN.IS", "BIENY.IS", "BIGCH.IS", "BIOEN.IS", "BOBET.IS", "BOSSA.IS", 
    "BRISA.IS", "BRKO.IS", "BRKVY.IS", "BRMEN.IS", "BRYAT.IS", "BSOKE.IS", "BTCIM.IS", "BUCIM.IS", 
    "BURCE.IS", "BURVA.IS", "BVSAN.IS", "CANTE.IS", "CATES.IS", "CELHA.IS", "CEMAS.IS", "CEMTS.IS", 
    "CEOEM.IS", "CVSENERGY.IS", "DAPGM.IS", "DEVA.IS", "DGATE.IS", "DGNMO.IS", "DIRIT.IS", "DITAS.IS", 
    "DMSAS.IS", "DNISI.IS", "DOKTA.IS", "DURDO.IS", "DYOBY.IS", "ECILC.IS", "ECZYT.IS", "EDATA.IS", 
    "EGGUB.IS", "EPLAS.IS", "ERBOS.IS", "ERCB.IS", "ESCAR.IS", "ESCOM.IS", "ESEN.IS", "ETILR.IS", 
    "EUREN.IS", "EYGYO.IS", "FADE.IS", "FENER.IS", "FLAP.IS", "FMIZP.IS", "FONET.IS", "FORMT.IS", 
    "FRIGO.IS", "FZCMI.IS", "GARFA.IS", "GENIL.IS", "GEREL.IS", "GLBMD.IS", "GLRYH.IS", "GLYHO.IS", 
    "GOKNR.IS", "GOLTS.IS", "GOODY.IS", "GOZDE.IS", "GRSEL.IS", "GSDHO.IS", "GSRAY.IS", "GWIND.IS", 
    "GZNMI.IS", "HATEK.IS", "HDFGS.IS", "HEDEF.IS", "HLGYO.IS", "HUBVC.IS", "HUNER.IS", "ICBCT.IS", 
    "IEYHO.IS", "IHAAS.IS", "IHEVA.IS", "IHGZT.IS", "IMASM.IS", "INDES.IS", "INFO.IS", "INTEM.IS", 
    "IPEKE.IS", "ISDMR.IS", "ISFIN.IS", "ISGSY.IS", "ISKPL.IS", "ISMEN.IS", "ITTFH.IS", "IZENR.IS", 
    "IZFAS.IS", "IZMDC.IS", "JANTS.IS", "KAPLM.IS", "KAREL.IS", "KARYE.IS", "KATMR.IS", "KAYSE.IS", 
    "KCAER.IS", "KERVT.IS", "KRGYO.IS", "KFEIN.IS", "KGYO.IS", "KLGYO.IS", "KLKIM.IS", "KLMSN.IS", 
    "KLSER.IS", "KMPUR.IS", "KNFRT.IS", "KONYA.IS", "KORDS.IS", "KOZAA.IS", "KRONT.IS", "KRPLS.IS", 
    "KRTEK.IS", "KRVGD.IS", "KSTUR.IS", "KTLEV.IS", "KTSKR.IS", "KUTPO.IS", "KUVVA.IS", "KUYAS.IS", 
    "KZBGY.IS", "LIDER.IS", "LIDFA.IS", "LINK.IS", "LKMNH.IS", "LOGO.IS", "LUKSK.IS", "MAALT.IS", 
    "MACKO.IS", "MAGEN.IS", "MAKIM.IS", "MAKTK.IS", "MANAS.IS", "MARKA.IS", "MARTI.IS", "MAVI.IS", 
    "MEDTR.IS", "MEGAP.IS", "MEPET.IS", "MERCN.IS", "MERKO.IS", "METRO.IS", "METUR.IS", "MIPAZ.IS", 
    "MMCAS.IS", "MNDRS.IS", "MOBTL.IS", "MPARK.IS", "MRGYO.IS", "MRSHL.IS", "MSGYO.IS", "MTRKS.IS", 
    "MTRYO.IS", "MZHLD.IS", "NATEN.IS", "NETAS.IS", "NIBAS.IS", "NTGAZ.IS", "NUHCM.IS", "OBASE.IS", 
    "ODAS.IS", "ONCSM.IS", "ORCA.IS", "ORGE.IS", "ORMA.IS", "OSMEN.IS", "OSTIM.IS", "OTKAR.IS", 
    "OTTO.IS", "OYAYO.IS", "OYLUM.IS", "OZGYO.IS", "OZKGY.IS", "OZRDN.IS", "OZSUB.IS", "PAGYO.IS", 
    "PAMEL.IS", "PAPIL.IS", "PARSN.IS", "PASEU.IS", "PCILT.IS", "PEGYO.IS", "PEKGY.IS", "PENGD.IS", 
    "PENTA.IS", "PETUN.IS", "PGSUS.IS", "PINSU.IS", "PKART.IS", "PKENT.IS", "PLAT.IS", "PNLSN.IS", 
    "POLHO.IS", "POLTK.IS", "PRDGS.IS", "PRKAB.IS", "PRKME.IS", "PRZMA.IS", "PSDTC.IS", "PSGYO.IS", 
    "QNBFB.IS", "QUAGR.IS", "RALYH.IS", "RAYSG.IS", "REEDR.IS", "RNPOL.IS", "RODRG.IS", "ROYAL.IS", 
    "RTALB.IS", "RUBNS.IS", "RYGYO.IS", "RYSAS.IS", "SAFKR.IS", "SAMAT.IS", "SANEL.IS", "SANFM.IS", 
    "SANKO.IS", "SARKY.IS", "SAYAS.IS", "SDTTR.IS", "SEKFK.IS", "SEKUR.IS", "SELEC.IS", "SELGD.IS", 
    "SELVA.IS", "SEYKM.IS", "SILVR.IS", "SKBNK.IS", "SKTAS.IS", "SMART.IS", "SNGYO.IS", "SNKRN.IS", 
    "SNPAM.IS", "SODSN.IS", "SOKE.IS", "SONME.IS", "SRVGY.IS", "SUMAS.IS", "SUNTK.IS", "SURGY.IS", 
    "SUWEN.IS", "TABGD.IS", "TARAF.IS", "TATGD.IS", "TAVHL.IS", "TBORG.IS", "TCELL.IS", "TDGYO.IS", 
    "TEKTU.IS", "TERA.IS", "TETMT.IS", "TEZOL.IS", "TGSAS.IS", "THYAO.IS", "TKFEN.IS", "TKNSA.IS", 
    "TLMAN.IS", "TMPOL.IS", "TMSN.IS", "TNZTP.IS", "TOASO.IS", "TRCAS.IS", "TRGYO.IS", "TRILC.IS", 
    "TSGYO.IS", "TSKB.IS", "TSPOR.IS", "TTKOM.IS", "TTRAK.IS", "TUCLK.IS", "TUKAS.IS", "TUPRS.IS", 
    "TUREX.IS", "TURGG.IS", "TURSG.IS", "UFUK.IS", "ULAS.IS", "ULKER.IS", "ULUFA.IS", "ULUSE.IS", 
    "ULUUN.IS", "UMPAS.IS", "UNLU.IS", "USAK.IS", "VAKBN.IS", "VAKFN.IS", "VAKKO.IS", "VANGD.IS", 
    "VBTYZ.IS", "VERUS.IS", "VESBE.IS", "VESTL.IS", "VKFYO.IS", "VKGYO.IS", "VKING.IS", "YAPRK.IS", 
    "YATAS.IS", "YAYLA.IS", "YEOTK.IS", "YESIL.IS", "YGGYO.IS", "YGYO.IS", "YKBNK.IS", "YKSLN.IS", 
    "YONGA.IS", "YUNSA.IS", "YYAPI.IS", "ZEDUR.IS", "ZOREN.IS", "ZRGYO.IS"
]

# Kopyaları Temizle ve Birleştir
raw_bist_stocks = list(set(raw_bist_stocks) - set(priority_bist_indices))
raw_bist_stocks.sort()
final_bist100_list = priority_bist_indices + raw_bist_stocks

ASSET_GROUPS = {
    "S&P 500": final_sp500_list,
    "NASDAQ-100": raw_nasdaq,
    "BIST İLK 300": final_bist100_list,
    "KRİPTO-TOP 25": final_crypto_list
}
INITIAL_CATEGORY = "S&P 500"

# --- STATE YÖNETİMİ ---
if 'category' not in st.session_state: st.session_state.category = INITIAL_CATEGORY
if 'ticker' not in st.session_state: st.session_state.ticker = "^GSPC"
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
        df = yf.download(ticker, period="1y", progress=False)
        
        if df.empty: return None
        return df['Close']
    except:
        return None

@st.cache_data(ttl=3600)
def get_fundamental_score(ticker):
    """
    GLOBAL STANDART (IBD/Stockopedia Mantığı) - Kademeli Puanlama
    """
    # Endeks veya Kripto kontrolü
    if ticker.startswith("^") or "XU" in ticker or "-USD" in ticker:
        return {"score": 50, "details": [], "valid": False} # Nötr dön

    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        if not info: return {"score": 50, "details": ["Veri Yok"], "valid": False}
        
        score = 0
        details = []
        
        # --- YARDIMCI FONKSİYON: Kademeli Puanlama ---
        def rate_metric(val, thresholds, max_pts):
            """Değeri eşiklere göre puanlar. Örn: val=15, thresh=[5, 10, 20], max=20"""
            if not val: return 0
            val = val * 100 if val < 10 else val # Yüzde dönüşümü
            step = max_pts / len(thresholds)
            earned = 0
            for t in thresholds:
                if val > t: earned += step
            return earned

        # 1. BÜYÜME (GROWTH) - Max 40 Puan
        # Ciro Büyümesi (Eşikler: %5, %15, %25) -> Max 20p
        rev_g = info.get('revenueGrowth', 0)
        s_rev = rate_metric(rev_g, [5, 15, 25], 20)
        score += s_rev
        if s_rev >= 10: details.append(f"Ciro Büyümesi: %{rev_g*100:.1f}")

        # Kâr Büyümesi (Eşikler: %5, %15, %25) -> Max 20p
        earn_g = info.get('earningsGrowth', 0)
        s_earn = rate_metric(earn_g, [5, 15, 25], 20)
        score += s_earn
        if s_earn >= 10: details.append(f"Kâr Büyümesi: %{earn_g*100:.1f}")

        # 2. KALİTE (QUALITY) - Max 40 Puan
        # ROE (Eşikler: %5, %10, %15, %20) -> Max 20p (Daha hassas)
        roe = info.get('returnOnEquity', 0)
        s_roe = rate_metric(roe, [5, 10, 15, 20], 20)
        score += s_roe
        if s_roe >= 15: details.append(f"Güçlü ROE: %{roe*100:.1f}")

        # Net Marj (Eşikler: %5, %10, %20) -> Max 20p
        margin = info.get('profitMargins', 0)
        s_marg = rate_metric(margin, [5, 10, 20], 20)
        score += s_marg
        if s_marg >= 10: details.append(f"Net Marj: %{margin*100:.1f}")

        # 3. SMART MONEY (SAHİPLİK) - Max 20 Puan
        inst = info.get('heldPercentInstitutions', 0)
        s_inst = rate_metric(inst, [10, 30, 50, 70], 20)
        score += s_inst
        if s_inst >= 10: details.append(f"Kurumsal: %{inst*100:.0f}")

        return {"score": min(score, 100), "details": details, "valid": True}
        
    except Exception:
        return {"score": 50, "details": [], "valid": False}

# --- GLOBAL DATA CACHE KATMANI ---
@st.cache_data(ttl=3600, show_spinner=False)
def get_batch_data_cached(asset_list, period="1y"):
    """
    Tüm listenin verisini tek seferde çeker ve önbellekte tutar.
    Tarama fonksiyonları internete değil, buraya başvurur.
    """
    if not asset_list:
        return pd.DataFrame()
    
    try:
        # Tickers listesini string'e çevir
        tickers_str = " ".join(asset_list)
        
        # Tek seferde devasa indirme (Batch Download)
        data = yf.download(
            tickers_str, 
            period=period, 
            group_by='ticker', 
            threads=True, 
            progress=False,
            auto_adjust=False 
        )
        return data
    except Exception:
        return pd.DataFrame()

# --- SINGLE STOCK CACHE (DETAY SAYFASI İÇİN) ---
@st.cache_data(ttl=300)
def get_safe_historical_data(ticker, period="1y", interval="1d"):
    try:
        clean_ticker = ticker.replace(".IS", "").replace("=F", "")
        if "BIST" in ticker or ".IS" in ticker:
            clean_ticker = ticker if ticker.endswith(".IS") else f"{ticker}.IS"
        
        df = yf.download(clean_ticker, period=period, interval=interval, progress=False)
        
        if df.empty: return None
            
        if isinstance(df.columns, pd.MultiIndex):
            try:
                if clean_ticker in df.columns.levels[1]: df = df.xs(clean_ticker, axis=1, level=1)
                else: df.columns = df.columns.get_level_values(0)
            except: df.columns = df.columns.get_level_values(0)
                
        df.columns = [c.capitalize() for c in df.columns]
        required = ['Close', 'High', 'Low', 'Open']
        if not all(col in df.columns for col in required): return None

        if 'Volume' not in df.columns: df['Volume'] = 1
        df['Volume'] = df['Volume'].replace(0, 1)
        return df

    except Exception: return None

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
        close = df['Close']; high = df['High']; low = df['Low']
        
        sma50 = close.rolling(50).mean().iloc[-1] if len(close) > 50 else 0
        sma100 = close.rolling(100).mean().iloc[-1] if len(close) > 100 else 0
        sma200 = close.rolling(200).mean().iloc[-1] if len(close) > 200 else 0
        ema144 = close.ewm(span=144, adjust=False).mean().iloc[-1]
        atr = (high-low).rolling(14).mean().iloc[-1]
        
        return {
            "sma50": sma50, "sma100": sma100, "sma200": sma200, "ema144": ema144,
            "stop_level": close.iloc[-1] - (2 * atr), "risk_pct": (2 * atr) / close.iloc[-1] * 100,
            "atr": atr, "close_last": close.iloc[-1]
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
                "type": "cross",
                "data": {"Sembol": symbol, "Fiyat": c_last, "STP": s_last, "Fark": ((c_last/s_last)-1)*100}
            }
            sma_val = float(sma200.iloc[-1])
            rsi_val = float(rsi.iloc[-1])
            if (c_last > sma_val) and (20 < rsi_val < 70):
                result["is_filtered"] = True
            else:
                result["is_filtered"] = False

        elif c_prev > s_prev and c_last > s_last:
            above = close > stp
            streak = (above != above.shift()).cumsum()
            streak_count = above.groupby(streak).sum().iloc[-1]
            
            result = {
                "type": "trend",
                "data": {
                    "Sembol": symbol, 
                    "Fiyat": c_last, 
                    "STP": s_last, 
                    "Fark": ((c_last/s_last)-1)*100,
                    "Gun": int(streak_count)
                }
            }
        return result
    except Exception: return None

@st.cache_data(ttl=900)
def scan_chart_patterns(asset_list):
    """
    V4 FİNAL: ERKEN SİNYALLERİ ELEYEN, SADECE 'OLMUŞ' FORMASYONLARI BULAN TARAMA
    """
    data = get_batch_data_cached(asset_list, period="1y") 
    if data.empty: return pd.DataFrame()

    results = []
    
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol not in data.columns.levels[0]: continue
                df = data[symbol].dropna()
            else:
                df = data.dropna()
            
            # Daha güvenilir analiz için en az 150 gün veri
            if len(df) < 150: continue 

            close = df['Close']; high = df['High']; low = df['Low']; open_ = df['Open']
            volume = df['Volume']
            curr_price = float(close.iloc[-1])
            
            # --- ACIMASIZ ANA TREND FİLTRESİ ---
            # Fiyat 200 günlük ortalamanın altındaysa HİÇ BAKMA.
            sma200 = close.rolling(200).mean().iloc[-1]
            if curr_price < sma200: continue 

            pattern_found = False; pattern_name = ""; desc = ""; base_score = 0
            
            # --- 1. BOĞA BAYRAĞI (BULL FLAG) ---
            # Direk en az %15, Bayrak en fazla %6 genişlikte
            p20 = float(close.iloc[-20]); p5 = float(close.iloc[-5])
            pole = (p5 - p20) / p20
            flag_h = high.iloc[-5:].max(); flag_l = low.iloc[-5:].min()
            tight = (flag_h - flag_l) / flag_l
            
            if pole > 0.15 and tight < 0.06 and curr_price > flag_h * 0.99:
                pattern_found = True; pattern_name = "🚩 BOĞA BAYRAĞI"; base_score = 85
                desc = f"Direk: %{pole*100:.1f} | Sıkışma: %{tight*100:.1f}"

            # --- 2. FİNCAN KULP (CUP & HANDLE) - APTV DÜZELTMESİ ---
            if not pattern_found:
                # Daha geniş bak: Sol Tepe (4-6 ay önce), Sağ Tepe (Son 1 ay)
                rim_l = high.iloc[-150:-40].max() 
                cup_b = low.iloc[-60:-20].min()
                rim_r = high.iloc[-25:-5].max() 
                
                # Kulp Dibi
                handle_low = low.iloc[-10:].min()
                
                # 1. Simetri: Sol ve Sağ tepe birbirine çok yakın olmalı (%5)
                # APTV burada %10 farkla elenecek veya "Henüz kulp yapmadı" diyecek.
                aligned = abs(rim_l - rim_r) / rim_l < 0.05
                
                # 2. Derinlik: Çanak belirgin olmalı
                deep = cup_b < rim_l * 0.85
                
                # 3. KULP ŞARTI: Fiyat, Sağ Tepeden sonra biraz düşmüş (Kulp yapmış) ama çok da çökmemiş olmalı.
                # APTV şu an sağ tepede olduğu için "pullback" yapmadı, elenecek.
                handle_exists = (handle_low < rim_r * 0.97) and (handle_low > cup_b + (rim_r - cup_b)*0.5)
                
                # 4. KIRILIM ŞARTI: Fiyat şu an TAM DİRENÇTE veya GEÇMİŞ olmalı.
                # 0.96 yerine 0.99 yaptık. Yani tam sınıra dayanmalı.
                breaking = curr_price >= rim_r * 0.99
                
                if aligned and deep and handle_exists and breaking:
                    pattern_found = True; pattern_name = "☕ FİNCAN KULP"; base_score = 95
                    desc = "Kulp tamamlandı, boyun çizgisi kırılıyor."

            # --- 3. TOBO (Inverse Head & Shoulders) - GÜNCELLENDİ ---
            if not pattern_found:
                # Periyotlar
                ml = low.iloc[-60:-40].min()
                mh = low.iloc[-40:-15].min()
                mr = low.iloc[-15:].min()
                
                # Boyun Çizgisi (Direnç)
                neck = high.iloc[-60:-10].max()
                
                # KURALLAR:
                # 1. Baş en altta mı?
                head_deep = mh < ml * 0.98 and mh < mr * 0.98
                
                # 2. Simetri: Omuzlar arası fark %8'i geçmesin
                sym = abs(ml - mr) / ml < 0.08
                
                # 3. YENİ FİLTRE: "CHASE FILTER" (Peşinden Koşma)
                # Fiyat boyun çizgisini kırmış olmalı AMA %3'ten fazla uzaklaşmamış olmalı.
                # SCHW $97 boynunu kırmış ama $101 olmuş (%4+). Bu filtre onu eler.
                is_breakout_fresh = (curr_price >= neck * 0.98) and (curr_price <= neck * 1.03)
                
                if head_deep and sym and is_breakout_fresh:
                    pattern_found = True; pattern_name = "🧛 TOBO"; base_score = 90
                    desc = "Dönüş Formasyonu. Kırılım taze."

            # --- 4. YÜKSELEN ÜÇGEN ---
            if not pattern_found:
                h_peaks = high.iloc[-45:].nlargest(3).values
                if len(h_peaks) > 0:
                    avg_res = h_peaks.mean()
                    flat = all(abs(p - avg_res)/avg_res < 0.02 for p in h_peaks)
                    
                    l3=low.iloc[-15:].min(); l2=low.iloc[-30:-15].min(); l1=low.iloc[-45:-30].min()
                    rising = l3 > l2 and l2 > l1
                    
                    if flat and rising and curr_price >= avg_res * 0.99:
                        pattern_found = True; pattern_name = "📐 YÜKSELEN ÜÇGEN"; base_score = 88
                        desc = "Direnç zorlanıyor"

            # --- KALİTE PUANLAMASI ---
            if pattern_found:
                q_score = base_score
                
                # Hacim Desteği (+15 Puan)
                avg_vol = volume.iloc[-20:].mean()
                if volume.iloc[-1] > avg_vol * 1.5: q_score += 15
                
                # Son 2 Gün Kırmızıysa AĞIR CEZA (-30 Puan)
                # Senin istediğin özellik: Düşen formasyonları en alta atar.
                if close.iloc[-1] < open_.iloc[-1] and close.iloc[-2] < open_.iloc[-2]:
                    q_score -= 30
                    desc += " (⚠️ Düşüşte)"
                
                results.append({
                    "Sembol": symbol,
                    "Fiyat": curr_price,
                    "Formasyon": pattern_name,
                    "Detay": desc,
                    "Skor": q_score
                })

        except Exception: continue
            
    if results:
        # En yüksek puanlılar en üstte
        return pd.DataFrame(results).sort_values(by="Skor", ascending=False)
    
    return pd.DataFrame()

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

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(process_single_stock_stp, sym, df) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                if res["type"] == "cross":
                    cross_signals.append(res["data"])
                    if res.get("is_filtered"):
                        filtered_signals.append(res["data"])
                elif res["type"] == "trend":
                    trend_signals.append(res["data"])

    trend_signals.sort(key=lambda x: x["Gun"], reverse=False)
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

        # --- 2. ZAMAN AYARLI HACİM HESABI (PRO-RATA) ---
        last_date = df.index[-1].date()
        today_date = datetime.now().date()
        is_live = (last_date == today_date)
        
        volume_for_check = float(volume.iloc[-1])
        
        if is_live:
            now = datetime.now() + timedelta(hours=3) # TR Saati
            current_hour = now.hour
            current_minute = now.minute
            
            if current_hour < 10: progress = 0.1
            elif current_hour >= 18: progress = 1.0
            else:
                progress = ((current_hour - 10) * 60 + current_minute) / 480.0
                progress = max(0.1, min(progress, 1.0))
            
            if progress > 0:
                volume_for_check = float(volume.iloc[-1]) / progress

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
            "Kalite": quality_label # Yeni alan eklendi
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
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        # benchmark serisini her fonksiyona argüman olarak geçiyoruz
        futures = [executor.submit(process_single_accumulation, sym, df, benchmark) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)

    if results: 
        df_res = pd.DataFrame(results)
        # Önce Pocket Pivot olanları, sonra Skoru yüksek olanları üste al
        return df_res.sort_values(by=["Pocket_Pivot", "Kalite", "Skor"], ascending=[False, True, False])
    
    return pd.DataFrame()

def process_single_radar1(symbol, df):
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

        return { "Sembol": symbol, "Fiyat": f"{curr_c:.2f}", "Skor": score, "Nedenler": " | ".join(reasons), "Detaylar": details }
    except: return None

@st.cache_data(ttl=3600)
def analyze_market_intelligence(asset_list):
    data = get_batch_data_cached(asset_list, period="6mo")
    if data.empty: return pd.DataFrame()

    signals = []
    stock_dfs = []
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]: stock_dfs.append((symbol, data[symbol]))
            else:
                if len(asset_list) == 1: stock_dfs.append((symbol, data))
        except: continue

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(process_single_radar1, sym, df) for sym, df in stock_dfs]
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
        
        # RS Skoru (Endeks)
        rs_score = 0.0
        if idx is not None and len(close) > 60 and len(idx) > 60:
            common_index = close.index.intersection(idx.index)
            if len(common_index) > 60:
                cs = close.reindex(common_index); isx = idx.reindex(common_index)
                rs_score = float((cs.iloc[-1]/cs.iloc[-60]-1) - (isx.iloc[-1]/isx.iloc[-60]-1))
        
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

@st.cache_data(ttl=3600)
def radar2_scan(asset_list, min_price=5, max_price=5000, min_avg_vol_m=0.5):
    data = get_batch_data_cached(asset_list, period="1y")
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
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
        
        # --- 1. ZAMAN AYARLI HACİM (SABAH KORUMASI) ---
        last_date = df.index[-1].date()
        today_date = datetime.now().date()
        is_live = (last_date == today_date)
        
        # Varsayılan: Gün bitti (%100)
        progress = 1.0 

        if is_live:
            now = datetime.now() + timedelta(hours=3) # TR Saati
            current_hour = now.hour
            current_minute = now.minute
            
            # BIST Seans Mantığı (10:00 - 18:00)
            if current_hour < 10: progress = 0.1
            elif current_hour >= 18: progress = 1.0
            else:
                progress = ((current_hour - 10) * 60 + current_minute) / 480.0
                progress = max(0.1, min(progress, 1.0))

        # Mevcut Hacim
        curr_vol_raw = float(volume.iloc[-1])
        # Yansıtılmış (Projected) Hacim: "Bu hızla giderse gün sonu ne olur?"
        curr_vol_projected = curr_vol_raw / progress
        
        # Hacim Ortalaması (Bugün hariç son 20 gün)
        vol_20 = volume.iloc[:-1].tail(20).mean()
        if pd.isna(vol_20) or vol_20 == 0: vol_20 = 1

        # Relative Volume (RVOL) - Projeksiyon kullanılarak hesaplanır
        rvol = curr_vol_projected / vol_20
        
        # --- TEKNİK HESAPLAMALAR ---
        # Ortalamalar
        ema5 = close.ewm(span=5, adjust=False).mean()
        ema20 = close.ewm(span=20, adjust=False).mean()
        sma20 = close.rolling(20).mean(); sma50 = close.rolling(50).mean()
        
        # Zirve Hesabı (Bugün hariç son 45 gün - Taze Zirve)
        high_val = high.iloc[:-1].tail(45).max()
        curr_price = close.iloc[-1]
        
        # RSI
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]
        
        # --- ŞARTLAR (HAVUZ DARALTMAMAK İÇİN MEVCUT KRİTERLER KORUNDU) ---
        cond_ema = ema5.iloc[-1] > ema20.iloc[-1]
        
        # DÜZELTME: Artık "Projected" hacme bakıyoruz, sabah da çalışır.
        cond_vol = rvol > 1.2 
        
        cond_prox = curr_price > (high_val * 0.90) # %10 Yakınlık
        cond_rsi = rsi < 70
        sma_ok = sma20.iloc[-1] > sma50.iloc[-1]
        
        if cond_ema and cond_vol and cond_prox and cond_rsi:
            
            # --- 2. LAZYBEAR PATLAMA KONTROLÜ (YENİ) ---
            sq_now, sq_prev = check_lazybear_squeeze_breakout(df)
            
            # Patlama Tanımı: Dün Sıkışık (True) VE Bugün Değil (False)
            is_firing = sq_prev and not sq_now
            
            # --- 3. SIRALAMA VE ÇIKTI ---
            
            # Sıralama: Tetiklenenler en üste, diğerleri hacim hızına göre
            # +1000 puan vererek listenin en tepesine çiviliyoruz.
            sort_score = rvol + (1000 if is_firing else 0)

            # Görsel Metin
            prox_pct = (curr_price / high_val) * 100
            
            if is_firing:
                prox_str = f"🚀 TETİKLENDİ (Triggered)"
            elif sq_now:
                prox_str = f"💣 Sıkışma Var (Squeeze)"
            else:
                prox_str = f"%{prox_pct:.1f}" + (" (Sınırda)" if prox_pct >= 98 else " (Hazırlık)")
            
            # Fitil Uyarısı (Satış baskısı var mı?)
            body_size = abs(close.iloc[-1] - open_.iloc[-1])
            upper_wick = high.iloc[-1] - max(open_.iloc[-1], close.iloc[-1])
            is_wick_rejected = (upper_wick > body_size * 1.5) and (upper_wick > 0)
            wick_warning = " ⚠️ Satış Baskısı" if is_wick_rejected else ""
            
            # Hacim Metni (Eğer gerçek hacim düşükse ama hız yüksekse belirtelim)
            if (curr_vol_raw < vol_20) and (rvol > 1.2):
                rvol_text = "Hız Yüksek (Proj.) 📈"
            else:
                rvol_text = "Olağanüstü 🐳" if rvol > 2.0 else "İlgi Artıyor 📈"

            display_symbol = symbol
            trend_display = f"✅EMA | {'✅SMA' if sma_ok else '❌SMA'}"
            
            return { 
                "Sembol_Raw": symbol, 
                "Sembol_Display": display_symbol, 
                "Fiyat": f"{curr_price:.2f}", 
                "Zirveye Yakınlık": prox_str + wick_warning, 
                "Hacim Durumu": rvol_text, 
                "Trend Durumu": trend_display, 
                "RSI": f"{rsi:.0f}", 
                "SortKey": sort_score 
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(process_single_breakout, sym, df) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)
    
    return pd.DataFrame(results).sort_values(by="SortKey", ascending=False) if results else pd.DataFrame()

def process_single_confirmed(symbol, df):
    try:
        if df.empty or 'Close' not in df.columns: return None
        df = df.dropna(subset=['Close'])
        if len(df) < 200: return None 

        close = df['Close']; high = df['High']; volume = df['Volume'] if 'Volume' in df.columns else pd.Series([1]*len(df))
        
        # --- 1. ADIM: ZİRVE KONTROLÜ (Son 30 İş Günü) ---
        # Bugünü (son satırı) hesaba katmadan, düne kadarki 30 günün zirvesi
        high_val = high.iloc[:-1].tail(30).max()
        curr_close = float(close.iloc[-1])
        
        # Eğer bugünkü fiyat, geçmiş 30 günün zirvesini geçmediyse ELE.
        if curr_close <= high_val: return None 

        # --- 2. ADIM: GÜVENLİ HACİM HESABI (TIME-BASED) ---
        
        # Önce Tarih Kontrolü: Elimizdeki son veri (df.index[-1]) BUGÜNE mi ait?
        last_data_date = df.index[-1].date()
        today_date = datetime.now().date()
        
        # Eğer son veri bugüne aitse "Canlı Seans" mantığı çalışsın.
        # Eğer veri eskiyse (akşam olduysa veya hafta sonuysa), gün bitmiş sayılır (Progress = 1.0)
        is_live_today = (last_data_date == today_date)
        
        day_progress = 1.0 # Varsayılan: Gün bitti (%100)

        if is_live_today:
            # Sadece veri "Bugün" ise saat hesabına gir.
            now = datetime.now()
            current_hour = now.hour
            current_minute = now.minute
            
            # BIST Seans: 10:00 - 18:00 (480 dk)
            if current_hour < 10:
                day_progress = 0.1 # Seans öncesi veri gelirse sapıtmasın
            elif current_hour >= 18:
                day_progress = 1.0 # Seans bitti
            else:
                minutes_passed = (current_hour - 10) * 60 + current_minute
                day_progress = minutes_passed / 480.0
                day_progress = max(0.1, min(day_progress, 1.0)) # 0.1 ile 1.0 arasına sıkıştır

        # Geçmiş 20 günün ortalama hacmi (Bugün hariç)
        avg_vol_20 = volume.rolling(20).mean().shift(1).iloc[-1]
        
        # BEKLENEN HACİM
        expected_vol_now = avg_vol_20 * day_progress
        curr_vol = float(volume.iloc[-1])
        
        # PERFORMANS ORANI
        # Eğer günün yarısı bittiyse ve hacim de ortalamanın yarısıysa oran 1.0 olur.
        # Biz biraz 'hareket' istiyoruz, o yüzden 0.9 (Normalin %90'ı) alt sınır olsun.
        if avg_vol_20 > 0:
            performance_ratio = curr_vol / expected_vol_now
        else:
            performance_ratio = 0
            
        # Filtre: Eğer o saate kadar yapması gereken hacmi yapmadıysa ELE.
        if performance_ratio < 0.9: return None 
        
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
        
        if rsi > 75: return None

        return {
            "Sembol": symbol,
            "Fiyat": f"{curr_close:.2f}",
            "Kirim_Turu": breakout_type,
            "Hacim_Kati": vol_display,
            "RSI": int(rsi),
            # Sıralamayı hacim "hızına" göre yapıyoruz
            "SortKey": performance_ratio 
        }
    except: return None

@st.cache_data(ttl=3600)
def scan_confirmed_breakouts(asset_list):
    data = get_batch_data_cached(asset_list, period="1y")
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(process_single_confirmed, sym, df) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)
    
    return pd.DataFrame(results).sort_values(by="SortKey", ascending=False).head(20) if results else pd.DataFrame()

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

@st.cache_data(ttl=3600)
def calculate_master_score(ticker):
    """
    FİNAL MASTER SKOR (Gelişmiş Raporlu):
    Puanı hesaplarken nedenlerini (Artı/Eksi) kaydeder.
    """
    # 1. VERİLERİ TOPLA
    mini_data = calculate_minervini_sepa(ticker)
    fund_data = get_fundamental_score(ticker)
    sent_data = calculate_sentiment_score(ticker)
    ict_data = calculate_ict_deep_analysis(ticker)
    tech = get_tech_card_data(ticker)
    
    # Radar Puanlarını Al
    r1_score = 0; r2_score = 0
    scan_df = st.session_state.get('scan_data')
    if scan_df is not None and not scan_df.empty and 'Sembol' in scan_df.columns:
        row = scan_df[scan_df['Sembol'] == ticker]
        if not row.empty: r1_score = float(row.iloc[0]['Skor'])
    
    radar2_df = st.session_state.get('radar2_data')
    if radar2_df is not None and not radar2_df.empty and 'Sembol' in radar2_df.columns:
        row = radar2_df[radar2_df['Sembol'] == ticker]
        if not row.empty: r2_score = float(row.iloc[0]['Skor'])

    # RAPOR LİSTELERİ
    pros = [] # Artılar
    cons = [] # Eksiler (Puan kırılan yerler)

    # ---------------------------------------------------
    # A. TREND (%30)
    # ---------------------------------------------------
    s_trend = 0
    if tech:
        close = tech['close_last']
        sma200 = tech['sma200']; sma50 = tech['sma50']
        
        # Ana Trend (SMA200)
        if close > sma200: 
            s_trend += 50
            pros.append("Fiyat SMA200 üzerinde (Ana Trend Boğa)")
        elif close > sma200 * 0.95: 
            s_trend += 30
            cons.append("Fiyat SMA200 altında ama yakın (Tolerans)")
        else:
            cons.append("Ana Trend Zayıf (Fiyat < SMA200)")
        
        # Orta Vade (SMA50)
        if close > sma50: 
            s_trend += 30
            pros.append("Fiyat SMA50 üzerinde (Orta Vade Güçlü)")
        else:
            cons.append("Orta Vade Zayıf (Fiyat < SMA50)")
        
        # Minervini Onayı
        if mini_data and mini_data.get('score', 0) > 50: 
            s_trend += 20
            pros.append("Minervini Trend Şablonuna Uygun")
    
    s_trend = min(s_trend, 100)

    # ---------------------------------------------------
    # B. MOMENTUM (%20)
    # ---------------------------------------------------
    sent_raw = sent_data.get('total', 50) if sent_data else 50
    rsi_val = sent_data.get('raw_rsi', 50) if sent_data else 50
    
    s_mom = (sent_raw * 0.6) + (rsi_val * 0.4)
    
    if sent_raw >= 60: pros.append(f"Genel Duygu Güçlü ({sent_raw}/100)")
    elif sent_raw <= 40: cons.append(f"Genel Duygu Zayıf ({sent_raw}/100)")
    
    if rsi_val > 50: pros.append(f"RSI Pozitif Bölgede ({int(rsi_val)})")
    else: cons.append(f"RSI Negatif Bölgede ({int(rsi_val)})")

    # ---------------------------------------------------
    # C. TEMEL (%30) - Endeks değilse
    # ---------------------------------------------------
    s_fund = fund_data.get('score', 50)
    is_index = ticker.startswith("^") or "XU" in ticker or "-USD" in ticker
    
    if not is_index:
        if s_fund >= 60: pros.append("Temel Veriler Güçlü (Büyüme/Kalite)")
        elif s_fund <= 40: cons.append("Temel Veriler Zayıf/Yetersiz")
        
        # Detaylardan gelenleri ekle
        for d in fund_data.get('details', []):
            pros.append(f"Temel: {d}")

    # ---------------------------------------------------
    # D. SMART / TEKNİK (%20)
    # ---------------------------------------------------
    # ICT (%10)
    s_ict = 50
    if ict_data:
        if "bullish" in ict_data.get('bias', ''): 
            s_ict += 20; pros.append("ICT Yapısı: Bullish (Boğa)")
        elif "bearish" in ict_data.get('bias', ''):
            cons.append("ICT Yapısı: Bearish (Ayı)")
            
        if "Güçlü" in ict_data.get('displacement', ''): 
            s_ict += 20; pros.append("Güçlü Hacim/Enerji (Displacement)")
        else:
            cons.append("Hacim/Enerji Zayıf")
            
        if "Ucuz" in ict_data.get('zone', ''): 
            s_ict += 10; pros.append("Fiyat Ucuzluk (Discount) Bölgesinde")
    s_ict = min(s_ict, 100)

    # Radar 2 (%10)
    s_r2_norm = (r2_score / 7) * 100
    if r2_score >= 4: pros.append("Radar-2 Setup Onayı Mevcut")
    else: cons.append("Net bir Radar-2 Setup Formasyonu Yok")

    # ---------------------------------------------------
    # FİNAL HESAPLAMA
    # ---------------------------------------------------
    if is_index:
        final = (s_trend * 0.40) + (s_mom * 0.30) + (s_ict * 0.15) + (s_r2_norm * 0.15)
    else:
        final = (s_trend * 0.30) + (s_fund * 0.30) + (s_mom * 0.20) + (s_ict * 0.10) + (s_r2_norm * 0.10)

    # Mavi Çip Koruması
    if not is_index and s_fund >= 80 and final < 50:
        final = 50
        pros.append("🛡️ Mavi Çip Koruması (Temel çok güçlü olduğu için puan yükseltildi)")

    return int(final), pros, cons
    
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
        sma200_up = sma200 > sma200_prev
        
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
        if is_vcp and in_pivot: status = "💎 SÜPER BOĞA (VCP)"
        elif in_pivot: status = "🚀 KIRILIM EŞİĞİNDE"
        
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
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
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
    
@st.cache_data(ttl=600)
def calculate_sentiment_score(ticker):
    try:
        df = get_safe_historical_data(ticker, period="6mo")
        if df is None: return None
        
        close = df['Close']; high = df['High']; low = df['Low']; volume = df['Volume']
        
        # --- VERİ HESAPLAMALARI ---
        
        # 1. YAPI (STRUCTURE) - 25 PUAN
        score_str = 0; reasons_str = []
        recent_high = high.rolling(20).max().shift(1).iloc[-1]
        recent_low = low.rolling(20).min().shift(1).iloc[-1]
        
        if close.iloc[-1] > recent_high: 
            score_str += 15; reasons_str.append("BOS: Kırılım")
        if low.iloc[-1] > recent_low:
            score_str += 10; reasons_str.append("HL: Yükselen Dip")

        # 2. TREND - 25 PUAN
        score_tr = 0; reasons_tr = []
        sma50 = close.rolling(50).mean(); sma200 = close.rolling(200).mean()
        ema20 = close.ewm(span=20, adjust=False).mean()
        
        if close.iloc[-1] > sma200.iloc[-1]: score_tr += 10; reasons_tr.append("Ana Trend+")
        if close.iloc[-1] > ema20.iloc[-1]: score_tr += 10; reasons_tr.append("Kısa Vade+")
        if ema20.iloc[-1] > sma50.iloc[-1]: score_tr += 5; reasons_tr.append("Hizalı")

        # 3. HACİM - 25 PUAN
        score_vol = 0; reasons_vol = []
        vol_ma = volume.rolling(20).mean()
        if volume.iloc[-1] > vol_ma.iloc[-1]: score_vol += 15; reasons_vol.append("Hacim Artışı")
        
        obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
        obv_ma = obv.rolling(10).mean()
        if obv.iloc[-1] > obv_ma.iloc[-1]: score_vol += 10; reasons_vol.append("OBV+")

        # 4. MOMENTUM - 15 PUAN
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

        # 5. VOLATİLİTE - 10 PUAN
        score_vola = 0; reasons_vola = []
        std = close.rolling(20).std()
        upper = close.rolling(20).mean() + (2 * std)
        lower = close.rolling(20).mean() - (2 * std)
        bb_width = (upper - lower) / close.rolling(20).mean()
        
        if bb_width.iloc[-1] < bb_width.rolling(20).mean().iloc[-1]:
            score_vola += 10; reasons_vola.append("Sıkışma")
        
        total = score_str + score_tr + score_vol + score_mom + score_vola
        
        # --- GÖRSEL AYARLAR (BAR VE YAZI TİPİ DÜZELTİLDİ) ---
        bars = int(total / 5)
        # Bar: Kare bloklar
        bar_str = "【" + "█" * bars + "░" * (20 - bars) + "】"
        
        def fmt(lst): 
            if not lst: return ""
            # Her bir sebebin arasına ' + ' koyup birleştiriyoruz
            content = " + ".join(lst)
            # HTML string olarak döndürüyoruz. CSS stillerine dikkat et.
            return f"<span style='font-size:0.7rem; color:#334155; font-style:italic; font-weight:300;'>({content})</span>"
        
        return {
            "total": total, "bar": bar_str, 
            # fmt() fonksiyonunu çağırarak formatlanmış HTML stringi alıyoruz
            "mom": f"{score_mom}/15 {fmt(reasons_mom)}",
            "vol": f"{score_vol}/25 {fmt(reasons_vol)}", 
            "tr": f"{score_tr}/25 {fmt(reasons_tr)}",
            "vola": f"{score_vola}/10 {fmt(reasons_vola)}", 
            "str": f"{score_str}/25 {fmt(reasons_str)}",
            "raw_rsi": rsi.iloc[-1], "raw_macd": (macd-signal).iloc[-1], "raw_obv": obv.iloc[-1], "raw_atr": 0
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

# --- ICT MODÜLÜ (GÜNCELLENMİŞ: Hata Korumalı) ---
@st.cache_data(ttl=600)
def calculate_ict_deep_analysis(ticker):
    error_ret = {"status": "Error", "msg": "Veri Yok", "structure": "-", "bias": "-", "entry": 0, "target": 0, "stop": 0, "rr": 0, "desc": "Veri bekleniyor", "displacement": "-", "fvg_txt": "-", "ob_txt": "-", "zone": "-", "mean_threshold": 0, "curr_price": 0, "setup_type": "BEKLE"}
    
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
                    sw_highs.append((df.index[i], high.iloc[i])) 
                if low.iloc[i] <= min(low.iloc[i-2:i]) and low.iloc[i] <= min(low.iloc[i+1:i+3]):
                    sw_lows.append((df.index[i], low.iloc[i]))
            except: continue

        if not sw_highs or not sw_lows: return error_ret

        curr_price = close.iloc[-1]
        last_sh = sw_highs[-1][1] 
        last_sl = sw_lows[-1][1]  
        
        structure = "YATAY / KONSOLİDE"
        bias = "neutral"
        displacement_txt = "Zayıf (Hacimsiz Hareket)"
        
        last_candle_body = abs(open_.iloc[-1] - close.iloc[-1])
        if last_candle_body > avg_body_size.iloc[-1] * 1.2:
             displacement_txt = "🔥 Güçlü Displacement (Hacimli Kırılım)"
        
        if curr_price > last_sh:
            structure = "BOS (Yükseliş Kırılımı) 🐂"
            bias = "bullish"
        elif curr_price < last_sl:
            structure = "BOS (Düşüş Kırılımı) 🐻"
            bias = "bearish"
        else:
            structure = "Internal Range (Düşüş/Düzeltme)"
            if close.iloc[-1] > open_.iloc[-1]: bias = "bullish_retrace" 
            else: bias = "bearish_retrace"

        next_bsl = min([h[1] for h in sw_highs if h[1] > curr_price], default=high.max())
        next_ssl = max([l[1] for l in sw_lows if l[1] < curr_price], default=low.min())

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
        
        if bias == "bullish" or bias == "bullish_retrace":
            if bullish_fvgs:
                f = bullish_fvgs[-1]
                active_fvg_txt = f"Açık FVG var (Destek): {f['bot']:.2f} - {f['top']:.2f}"
            lowest_idx = df['Low'].iloc[start_idx:].idxmin()
            if isinstance(lowest_idx, pd.Timestamp): lowest_idx = df.index.get_loc(lowest_idx)
            for i in range(lowest_idx, max(0, lowest_idx-5), -1):
                if df['Close'].iloc[i] < df['Open'].iloc[i]:
                    ob_low = df['Low'].iloc[i]
                    ob_high = df['High'].iloc[i]
                    active_ob_txt = f"{ob_low:.2f} - {ob_high:.2f} (Talep Bölgesi)"
                    mean_threshold = (ob_low + ob_high) / 2
                    break
                    
        elif bias == "bearish" or bias == "bearish_retrace":
            if bearish_fvgs:
                f = bearish_fvgs[-1]
                active_fvg_txt = f"Açık FVG var (Direnç): {f['bot']:.2f} - {f['top']:.2f}"
            highest_idx = df['High'].iloc[start_idx:].idxmax()
            if isinstance(highest_idx, pd.Timestamp): highest_idx = df.index.get_loc(highest_idx)
            for i in range(highest_idx, max(0, highest_idx-5), -1):
                if df['Close'].iloc[i] > df['Open'].iloc[i]:
                    ob_low = df['Low'].iloc[i]
                    ob_high = df['High'].iloc[i]
                    active_ob_txt = f"{ob_low:.2f} - {ob_high:.2f} (Arz Bölgesi)"
                    mean_threshold = (ob_low + ob_high) / 2
                    break

        range_high = max(high.tail(60)); range_low = min(low.tail(60))
        range_loc = (curr_price - range_low) / (range_high - range_low)
        zone = "PREMIUM (Pahalı)" if range_loc > 0.5 else "DISCOUNT (Ucuz)"

        setup_type = "BEKLE"
        entry_price = 0.0; stop_loss = 0.0; take_profit = 0.0; rr_ratio = 0.0
        setup_desc = "Mantıklı bir R/R kurulumu veya Bölge uyumu bekleniyor."
        
        if bias in ["bullish", "bullish_retrace"] and zone == "DISCOUNT (Ucuz)":
            valid_fvgs = [f for f in bullish_fvgs if f['top'] < curr_price]
            if valid_fvgs and next_bsl > curr_price:
                best_fvg = valid_fvgs[-1]
                temp_entry = best_fvg['top']
                if next_bsl > temp_entry:
                    entry_price = temp_entry
                    take_profit = next_bsl
                    stop_loss = last_sl if last_sl < entry_price else best_fvg['bot'] - atr * 0.5
                    risk = entry_price - stop_loss
                    reward = take_profit - entry_price
                    if risk > 0:
                        rr_ratio = reward / risk
                        setup_type = "LONG"
                        setup_desc = "Fiyat ucuzluk bölgesinde. FVG desteğinden yukarıdaki likidite (BSL) hedefleniyor."

        elif bias in ["bearish", "bearish_retrace"] and zone == "PREMIUM (Pahalı)":
            valid_fvgs = [f for f in bearish_fvgs if f['bot'] > curr_price]
            if valid_fvgs and next_ssl < curr_price:
                best_fvg = valid_fvgs[-1]
                temp_entry = best_fvg['bot']
                if next_ssl < temp_entry:
                    entry_price = temp_entry
                    take_profit = next_ssl
                    stop_loss = last_sh if last_sh > entry_price else best_fvg['top'] + atr * 0.5
                    risk = stop_loss - entry_price
                    reward = entry_price - take_profit
                    if risk > 0:
                        rr_ratio = reward / risk
                        setup_type = "SHORT"
                        setup_desc = "Fiyat pahalılık bölgesinde. Direnç bloğundan aşağıdaki likidite (SSL) hedefleniyor."

        return {
            "status": "OK", "structure": structure, "bias": bias, "zone": zone,
            "setup_type": setup_type, "entry": entry_price, "stop": stop_loss, "target": take_profit,
            "rr": rr_ratio, "desc": setup_desc, "last_sl": last_sl, "last_sh": last_sh,
            "displacement": displacement_txt, "fvg_txt": active_fvg_txt, "ob_txt": active_ob_txt,
            "mean_threshold": mean_threshold, "curr_price": curr_price
        }

    except Exception: return error_ret
        
@st.cache_data(ttl=600)
def calculate_price_action_dna(ticker):
    try:
        df = get_safe_historical_data(ticker, period="6mo") 
        if df is None or len(df) < 50: return None
        
        o = df['Open']; h = df['High']; l = df['Low']; c = df['Close']; v = df['Volume']
        
        # --- VERİ HAZIRLIĞI (SON 3 GÜN) ---
        c1_o, c1_h, c1_l, c1_c = float(o.iloc[-1]), float(h.iloc[-1]), float(l.iloc[-1]), float(c.iloc[-1]) # Bugün
        c2_o, c2_h, c2_l, c2_c = float(o.iloc[-2]), float(h.iloc[-2]), float(l.iloc[-2]), float(c.iloc[-2]) # Dün
        c3_o, c3_h, c3_l, c3_c = float(o.iloc[-3]), float(h.iloc[-3]), float(l.iloc[-3]), float(c.iloc[-3]) # Önceki Gün
        
        c1_v = float(v.iloc[-1])
        avg_v = float(v.rolling(20).mean().iloc[-1]) 
        sma50 = c.rolling(50).mean().iloc[-1]
        
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
        # 1. TEKLİ MUM FORMASYONLARI
        # ======================================================
        if total_len > 0:
            # Hammer
            if l_wick > body * wick_ratio and u_wick < body * 0.5: 
                if trend_dir == "DÜŞÜŞ" or is_oversold: add_signal(bulls, "Hammer 🔨", True)
                else: neutrals.append("Hanging Man Potansiyeli")
            
            # Shooting Star
            if u_wick > body * wick_ratio and l_wick < body * 0.5: 
                if trend_dir == "YÜKSELİŞ" or is_overbought: add_signal(bears, "Shooting Star 🔫", False)
            
            # Stopping Volume (Smart Money İmzası)
            if (l_wick > body * 2.0) and (c1_v > avg_v * 1.5) and (c1_l < c2_l):
                bulls.append("🛑 STOPPING VOLUME (Kurumsal Alım)")
            
            # Marubozu
            if body > total_len * 0.85: 
                if is_green: add_signal(bulls, "Marubozu 🚀", True)
                else: add_signal(bears, "Marubozu 🔻", False)
            
            # Doji
            if body < total_len * doji_threshold: neutrals.append("Doji (Kararsızlık) ⚖️")

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
        if (c1_h < c2_h) and (c1_l > c2_l): neutrals.append("Harami (Inside Bar) 🤰")

        # ======================================================
        # 3. ÜÇLÜ MUM FORMASYONLARI
        # ======================================================
        
        # Morning Star (Sabah Yıldızı - Dipten Dönüş)
        # 1. Kırmızı, 2. Küçük Gövde, 3. Yeşil (ilk mumun yarısını geçen)
        if (c3_c < c3_o) and (abs(c2_c - c2_o) < total_len * 0.3) and is_green and (c1_c > (c3_o + c3_c)/2):
             if is_oversold or trend_dir == "DÜŞÜŞ": add_signal(bulls, "Morning Star ⭐", True)

        # [EKLENEN EKSİK PARÇA] Evening Star (Akşam Yıldızı - Tepeden Dönüş)
        # 1. Yeşil, 2. Küçük Gövde, 3. Kırmızı (ilk mumun yarısını aşağı geçen)
        if (c3_c > c3_o) and (abs(c2_c - c2_o) < total_len * 0.3) and is_red and (c1_c < (c3_o + c3_c)/2):
             if is_overbought or trend_dir == "YÜKSELİŞ": add_signal(bears, "Evening Star 🌆", False)

        # 3 White Soldiers
        if (c1_c > c1_o) and (c2_c > c2_o) and (c3_c > c3_o) and (c1_c > c2_c > c3_c):
             if c1_c > c1_h * 0.95: add_signal(bulls, "3 White Soldiers ⚔️", True)

        # 3 Black Crows
        if (c1_c < c1_o) and (c2_c < c2_o) and (c3_c < c3_o) and (c1_c < c2_c < c3_c):
             if c1_c < c1_l * 1.05: add_signal(bears, "3 Black Crows 🦅", False)

        # --- ÇIKTI FORMATLAMA ---
        signal_summary = ""
        priorities = ["Bullish Kicker", "Stopping Volume", "3 White Soldiers"]
        for p in priorities:
            for b in bulls:
                if p in b: bulls.remove(b); bulls.insert(0, b); break

        if bulls: signal_summary += f"ALICI: {', '.join(bulls)} "
        if bears: signal_summary += f"SATICI: {', '.join(bears)} "
        if neutrals: signal_summary += f"NÖTR: {', '.join(neutrals)}"
        
        candle_desc = signal_summary if signal_summary else "Belirgin, güçlü bir formasyon yok."
        candle_title = "Formasyon Tespiti"

        # ======================================================
        # DİĞER GÖSTERGELER (SFP, VSA, KONUM, SIKIŞMA)
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
        # RSI UYUMSUZLUK (DIVERGENCE)
        # ======================================================
        div_txt, div_desc, div_type = "Uyumlu", "RSI ve Fiyat paralel.", "neutral"
        try:
            # Son 5 gün vs Önceki 15 gün
            current_window = c.iloc[-5:]
            prev_window = c.iloc[-20:-5]
            
            # Negatif Uyumsuzluk (Fiyat Tepe, RSI Düşük)
            p_curr_max = current_window.max(); p_prev_max = prev_window.max()
            r_curr_max = rsi_series.iloc[-5:].max(); r_prev_max = rsi_series.iloc[-20:-5].max()
            
            if (p_curr_max > p_prev_max) and (r_curr_max < r_prev_max) and (r_prev_max > 60):
                div_txt = "🐻 NEGATİF UYUMSUZLUK (Tepe Zayıflığı)"
                div_desc = "Fiyat yeni tepe yaptı ama RSI desteklemiyor. Düşüş riski!"
                div_type = "bearish"
                
            # Pozitif Uyumsuzluk (Fiyat Dip, RSI Yüksek)
            p_curr_min = current_window.min(); p_prev_min = prev_window.min()
            r_curr_min = rsi_series.iloc[-5:].min(); r_prev_min = rsi_series.iloc[-20:-5].min()
            
            if (p_curr_min < p_prev_min) and (r_curr_min > r_prev_min) and (r_prev_min < 45):
                div_txt = "💎 POZİTİF UYUMSUZLUK (Gizli Güç)"
                div_desc = "Fiyat yeni dip yaptı ama RSI yükseliyor. Toplama sinyali!"
                div_type = "bullish"     
        except: pass

        return {
            "candle": {"title": candle_title, "desc": candle_desc},
            "sfp": {"title": sfp_txt, "desc": sfp_desc},
            "vol": {"title": vol_txt, "desc": vol_desc},
            "loc": {"title": loc_txt, "desc": loc_desc},
            "sq": {"title": sq_txt, "desc": sq_desc},
            "div": {"title": div_txt, "desc": div_desc, "type": div_type}
        }
    except Exception: return None

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

def calculate_fib_levels(df, period=144):
    """
    Son N periyodun en yüksek ve en düşüğüne göre Fibonacci seviyelerini hesaplar.
    """
    try:
        if len(df) < period: period = len(df)
        recent_data = df.tail(period)
        
        max_h = recent_data['High'].max()
        min_l = recent_data['Low'].min()
        diff = max_h - min_l
        
        levels = {
            "1.618 (Ext)": max_h + (diff * 0.618),
            "1.272 (Ext)": max_h + (diff * 0.272),
            "0 (Tepe)": max_h,
            "0.236": max_h - (diff * 0.236),
            "0.382": max_h - (diff * 0.382),
            "0.5 (Orta)": max_h - (diff * 0.5),
            "0.618 (Golden)": max_h - (diff * 0.618),
            "0.786": max_h - (diff * 0.786),
            "1 (Dip)": min_l
        }
        return levels
    except:
        return {}

@st.cache_data(ttl=600)
def get_advanced_levels_data(ticker):
    """
    Arayüz için verileri paketler.
    """
    df = get_safe_historical_data(ticker, period="1y")
    if df is None: return None
    
    # 1. SuperTrend
    st_val, st_dir = calculate_supertrend(df)
    
    # 2. Fibonacci (Son 6 ay ~120 gün baz alınarak)
    fibs = calculate_fib_levels(df, period=120)
    
    curr_price = df['Close'].iloc[-1]
    
    # En yakın destek ve direnci bulma
    sorted_fibs = sorted(fibs.items(), key=lambda x: float(x[1]))
    support = (None, -999999)
    resistance = (None, 999999)
    
    for label, val in sorted_fibs:
        if val < curr_price and val > support[1]:
            support = (label, val)
        if val > curr_price and val < resistance[1]:
            resistance = (label, val)
    if resistance[1] == 999999:
        resistance = ("ZİRVE AŞIMI", curr_price * 1.10) # Sembolik %10 yukarı koy veya boş bırak

    return {
        "st_val": st_val,
        "st_dir": st_dir,
        "fibs": fibs,
        "nearest_sup": support,
        "nearest_res": resistance,
        "curr_price": curr_price
    }

# ==============================================================================
# 4. GÖRSELLEŞTİRME FONKSİYONLARI (EKSİK OLAN KISIM)
# ==============================================================================

def render_sentiment_card(sent):
    if not sent: return
    display_ticker = st.session_state.ticker.replace(".IS", "").replace("=F", "")
    
    # 1. SKOR RENKLERİ VE İKONLARI
    score = sent['total']
    if score >= 70: color = "#16a34a"; icon = "🔥"; status = "GÜÇLÜ BOĞA"
    elif score >= 50: color = "#d97706"; icon = "↔️"; status = "NÖTR / POZİTİF" # Tahteravalli (Denge)
    elif score >= 30: color = "#b91c1c"; icon = "🐻"; status = "ZAYIF / AYI"
    else: color = "#7f1d1d"; icon = "❄️"; status = "ÇÖKÜŞ"
    
    html_content = f"""
    <div class="info-card">
        <div class="info-header">🎭 Smart Money Sentiment: {display_ticker}</div>
        
        <div class="info-row" style="border-bottom: 2px solid {color}; padding-bottom:6px; margin-bottom:8px; background-color:{color}10; border-radius:4px; padding:6px;">
            <div style="font-weight:500; color:{color}; font-size:1rem;">{score}/100 {icon} {status}</div>
        </div>
        
        <div style="font-family:'Arial', sans-serif; font-size:0.8rem; color:#1e3a8a; margin-bottom:8px; text-align:center; letter-spacing:1px;">{sent['bar']}</div>
        
        <div class="info-row" style="background:#f0f9ff; padding:2px; border-radius:4px;">
            <div class="label-long" style="width:120px; color:#0369a1;">1. YAPI (25p):</div>
            <div class="info-val" style="font-weight:700;">{sent['str']}</div>
        </div>
        <div class="edu-note">Market Yapısı- Son 20 günün zirvesini yukarı kırarsa (15). Son 5 günün en düşük seviyesi, önceki 20 günün en düşük seviyesinden yukarıdaysa: HL (10)</div>

        <div class="info-row">
            <div class="label-long" style="width:120px;">2. TREND (25p):</div>
            <div class="info-val">{sent['tr']}</div>
        </div>
        <div class="edu-note">Ortalamalara bakar. Hisse fiyatı SMA200 üstünde (10). EMA20 üstünde (10). Kısa vadeli ortalama, orta vadeli ortalamanın üzerinde, yani EMA20 > SMA50 (5)</div>
        
        <div class="info-row">
            <div class="label-long" style="width:120px;">3. HACİM (25p):</div>
            <div class="info-val">{sent['vol']}</div>
        </div>
        <div class="edu-note">Hacmin 20G ortalamaya oranını ve On-Balance Volume (OBV) denetler. Bugünün hacmi son 20G ort.üstünde (15) Para girişi var: 10G ortalamanın üstünde (10)</div>

        <div class="info-row">
            <div class="label-long" style="width:120px;">4. MOMENTUM (15p):</div>
            <div class="info-val">{sent['mom']}</div>
        </div>
        <div class="edu-note">RSI ve MACD ile itki gücünü ölçer. 50 üstü RSI (5) RSI ivmesi artıyor (5). MACD sinyal çizgisi üstünde (5)</div>
        
        <div class="info-row">
            <div class="label-long" style="width:120px;">5. SIKIŞMA (10p):</div>
            <div class="info-val">{sent['vola']}</div>
        </div>
        <div class="edu-note">Bollinger Bant genişliğini inceler. Bant genişliği son 20G ortalamasından dar (10)</div>
    </div>
    """.replace("\n", "")
    
    st.markdown(html_content, unsafe_allow_html=True)

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
        temp_df = analyze_market_intelligence([ticker])
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
    st.markdown(f"""<div class="info-card" style="margin-bottom:10px;"><div class="info-header">🌊 Para Akış İvmesi & Fiyat Dengesi: {display_ticker}</div></div>""", unsafe_allow_html=True)
    c1, c2 = st.columns([1, 1]); x_axis = alt.X('Date_Str', axis=alt.Axis(title=None, labelAngle=-45), sort=None)
    with c1:
        base = alt.Chart(data).encode(x=x_axis)
        color_condition = alt.condition(
            alt.datum.MF_Smooth > 0,
            alt.value("#3b82f6"), 
            alt.value("#ef4444")
        )
        bars = base.mark_bar(size=15, opacity=0.9).encode(
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
        st.altair_chart(alt.layer(area, line_stp, line_price).properties(height=280, title=alt.TitleParams("EMA6 Analizi: Mavi (Fiyat) Sarıyı (STP) Yukarı Keserse AL", fontSize=14, color="#1e40af")), use_container_width=True)

def render_price_action_panel(ticker):
    pa = calculate_price_action_dna(ticker)
    if not pa:
        st.info("PA verisi bekleniyor...")
        return

    display_ticker = ticker.replace(".IS", "").replace("=F", "")

    sfp_color = "#16a34a" if "Bullish" in pa['sfp']['title'] else "#dc2626" if "Bearish" in pa['sfp']['title'] else "#475569"
    sq_color = "#d97706" if "BOBİN" in pa['sq']['title'] else "#475569"
    
    # RSI DIV RENKLENDİRME
    div_data = pa.get('div', {'type': 'neutral', 'title': '-', 'desc': '-'})
    if div_data['type'] == 'bearish':
        div_style = "background:#fef2f2; border-left:3px solid #dc2626; color:#991b1b;"
    elif div_data['type'] == 'bullish':
        div_style = "background:#f0fdf4; border-left:3px solid #16a34a; color:#166534;"
    else:
        div_style = "color:#475569;"
    
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
        
    </div>
    """
    st.markdown(html_content.replace("\n", " "), unsafe_allow_html=True)
    

def render_ict_deep_panel(ticker):
    data = calculate_ict_deep_analysis(ticker)
    
    if not data or data.get("status") == "Error":
        st.warning(f"ICT Analiz Bekleniyor... ({data.get('msg', 'Veri Yok')})")
        return
    
    struct_desc = "Piyasa kararsız."
    if "BOS (Yükseliş" in data['structure']: struct_desc = "Boğalar kontrolü elinde tutuyor. Eski tepeler aşıldı, bu da yükseliş iştahının devam ettiğini gösterir. Geri çekilmeler alım fırsatı olabilir."
    elif "BOS (Düşüş" in data['structure']: struct_desc = "Ayılar piyasaya hakim. Eski dipler kırıldı, düşüş trendi devam ediyor. Yükselişler satış fırsatı olarak görülebilir."
    elif "Internal" in data['structure']: struct_desc = "Ana trendin tersine bir düzeltme hareketi (Internal Range) yaşanıyor olabilir. Piyasada kararsızlık hakim."

    energy_desc = "Mum gövdeleri küçük, hacimsiz bir hareket. Kurumsal oyuncular henüz oyuna tam girmemiş olabilir. Kırılımlar tuzak olabilir."
    if "Güçlü" in data['displacement']: energy_desc = "Fiyat güçlü ve hacimli mumlarla hareket ediyor. Bu 'Akıllı Para'nın (Smart Money) ayak sesidir."

    zone_desc = "Fiyat 'Ucuzluk' (Discount) bölgesinde. Kurumsal yatırımcılar bu seviyelerden alım yapmayı tercih eder."
    if "PREMIUM" in data['zone']: zone_desc = "Fiyat 'Pahalılık' (Premium) bölgesinde. Kurumsal yatırımcılar bu bölgede satış yapmayı veya kar almayı sever."

    fvg_desc = "Dengesizlik Boşluğu: Yani, Fiyatın denge bulmak için bu aralığı doldurması (rebalance) beklenir. Mıknatıs etkisi yapar."
    if "Yok" in data['fvg_txt']: fvg_desc = "Yakınlarda önemli bir dengesizlik boşluğu tespit edilemedi."

    ob_desc = "Order Block: Yani Kurumsal oyuncuların son yüklü işlem yaptığı seviye. Fiyat buraya dönerse güçlü tepki alabilir: Eğer bu bölge fiyatı yeni bir tepeye (BOS) götürdüyse 'Kaliteli'dir. Götürmediyse zayıftır."
    
    liq_desc = "Yani Fiyatın bir sonraki durağı. Stop emirlerinin (Likiditenin) biriktiği, fiyatın çekildiği hedef seviye."

    bias_color = "#16a34a" if "bullish" in data['bias'] else "#dc2626" if "bearish" in data['bias'] else "#475569"
    bg_color_old = "#f0fdf4" if "bullish" in data['bias'] else "#fef2f2" if "bearish" in data['bias'] else "#f8fafc"

    mt_html = "" 
    mt_val = data.get('mean_threshold', 0)
    curr = data.get('curr_price', 0)
    
    if mt_val > 0 and curr > 0:
        diff_pct = (curr - mt_val) / mt_val
        if abs(diff_pct) < 0.003: 
            mt_status = "⚠️ KARAR ANI (BIÇAK SIRTI)"
            mt_desc = "Fiyat, yapının tam %50 denge noktasını test ediyor. Kırılım yönü beklenmeli."
            mt_color = "#d97706"; mt_bg = "#fffbeb" 
        elif diff_pct > 0:
            mt_status = "🛡️ Alıcılar Korumada" if "bullish" in data['bias'] else "Fiyat Dengenin Üzerinde"
            mt_desc = "Fiyat kritik orta noktanın üzerinde tutunuyor. Yapı korunuyor."
            mt_color = "#15803d"; mt_bg = "#f0fdf4" 
        else:
            mt_status = "🛡️ Satıcılar Baskın" if "bearish" in data['bias'] else "💀 Savunma Çöktü"
            mt_desc = "Fiyat kritik orta noktanın altına sarktı. Yapı bozulmuş olabilir."
            mt_color = "#b91c1c"; mt_bg = "#fef2f2" 
            
        mt_html = f"""
        <div style="background:{mt_bg}; padding:6px; border-radius:5px; border-left:3px solid {mt_color}; margin-bottom:8px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:700; color:{mt_color}; font-size:0.8rem;">⚖️ {mt_status}</span>
                <span style="font-family:'JetBrains Mono'; font-size:0.8rem; font-weight:700;">{mt_val:.2f}</span>
            </div>
            <div class="edu-note" style="margin-bottom:0;">{mt_desc}</div>
        </div>
        """

    if data['setup_type'] == "LONG":
        header_color = "#166534"; bg_color = "#f0fdf4"; border_color = "#16a34a"; icon = "🚀"
    elif data['setup_type'] == "SHORT":
        header_color = "#991b1b"; bg_color = "#fef2f2"; border_color = "#ef4444"; icon = "🔻"
    else:
        header_color = "#475569"; bg_color = "#f8fafc"; border_color = "#cbd5e1"; icon = "⏳"

    rr_display = f"{data['rr']:.2f}R" if data['rr'] > 0 else "-"
    
    html_content = f"""
    <div class="info-card" style="margin-bottom:8px;">
        <div class="info-header">🧠 ICT Smart Money Analizi: {display_ticker}</div>
        
        <div style="background:{bg_color_old}; padding:6px; border-radius:5px; border-left:3px solid {bias_color}; margin-bottom:8px;">
            <div style="font-weight:700; color:{bias_color}; font-size:0.8rem; margin-bottom:2px;">{data['structure']}</div>
            <div class="edu-note">{struct_desc}</div>
            
            <div class="info-row"><div class="label-long">Enerji:</div><div class="info-val">{data['displacement']}</div></div>
            <div class="edu-note">{energy_desc}</div>
        </div>

        {mt_html}

        <div style="margin-bottom:8px;">
            <div style="font-size:0.8rem; font-weight:700; color:#1e3a8a; border-bottom:1px dashed #cbd5e1; margin-bottom:4px;">📍 Ucuz Pahalı Okları (Giriş/Çıkış Referansları)</div>
            
            <div class="info-row"><div class="label-long">Konum:</div><div class="info-val" style="font-weight:700;">{data['zone']}</div></div>
            <div class="edu-note">{zone_desc}</div>
            
            <div class="info-row"><div class="label-long">GAP (FVG):</div><div class="info-val">{data['fvg_txt']}</div></div>
            <div class="edu-note">{fvg_desc}</div>
            
            <div class="info-row"><div class="label-long">Aktif OB:</div><div class="info-val">{data['ob_txt']}</div></div>
            <div class="edu-note">{ob_desc}</div>
        </div>

        <div style="background:#f1f5f9; padding:5px; border-radius:4px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-size:0.8rem; font-weight:600; color:#475569;">🧲 Hedef Likidite</span>
                <span style="font-family:'JetBrains Mono'; font-weight:700; font-size:0.8rem; color:#0f172a;">{data['target']:.2f}</span>
            </div>
            <div class="edu-note" style="margin-bottom:0;">{liq_desc}</div>
        </div>
    </div>

    <div class="info-card" style="border: 2px solid {border_color}; margin-top:5px;">
        <div style="background-color:{header_color}; color:white; padding:5px 10px; font-weight:700; border-radius:3px 3px 0 0; display:flex; justify-content:space-between; align-items:center;">
            <span>{icon} ICT TRADE SET-UP</span>
            <span style="font-family:'JetBrains Mono'; background:rgba(255,255,255,0.2); padding:2px 6px; border-radius:4px;">{data['setup_type']}</span>
        </div>
        <div style="padding:10px; background-color:{bg_color};">
            <div style="font-size:0.85rem; margin-bottom:10px; font-style:italic; color:#374151;">"{data['desc']}"</div>
            <div style="display:grid; grid-template-columns: 1fr 1fr; gap:10px; margin-bottom:10px;">
                <div style="background:white; padding:5px; border:1px solid #e5e7eb; border-radius:4px; text-align:center;">
                    <div style="font-size:0.7rem; color:#6b7280; font-weight:600;">GİRİŞ (ENTRY)</div>
                    <div style="font-family:'JetBrains Mono'; font-weight:700; color:{header_color};">{data['entry']:.2f}</div>
                </div>
                <div style="background:white; padding:5px; border:1px solid #e5e7eb; border-radius:4px; text-align:center;">
                    <div style="font-size:0.7rem; color:#6b7280; font-weight:600;">HEDEF (TP)</div>
                    <div style="font-family:'JetBrains Mono'; font-weight:700; color:#16a34a;">{data['target']:.2f}</div>
                </div>
                <div style="background:white; padding:5px; border:1px solid #e5e7eb; border-radius:4px; text-align:center;">
                    <div style="font-size:0.7rem; color:#6b7280; font-weight:600;">STOP (SL)</div>
                    <div style="font-family:'JetBrains Mono'; font-weight:700; color:#dc2626;">{data['stop']:.2f}</div>
                </div>
                <div style="background:white; padding:5px; border:1px solid #e5e7eb; border-radius:4px; text-align:center;">
                    <div style="font-size:0.7rem; color:#6b7280; font-weight:600;">RİSK/GETİRİ</div>
                    <div style="font-family:'JetBrains Mono'; font-weight:800; color:#0f172a;">{rr_display}</div>
                </div>
            </div>
        </div>
    </div>
    """
    
    st.markdown(html_content.replace("\n", " "), unsafe_allow_html=True)

def render_levels_card(ticker):
    data = get_advanced_levels_data(ticker)
    if not data: return

    # Renk ve İkon Ayarları
    is_bullish = data['st_dir'] == 1
    
    st_color = "#16a34a" if is_bullish else "#dc2626"
    st_text = "YÜKSELİŞ (AL)" if is_bullish else "DÜŞÜŞ (SAT)"
    st_icon = "🐂" if is_bullish else "🐻"
    
    # --- DİNAMİK METİN AYARLARI (YENİ KISIM) ---
    if is_bullish:
        # Yükseliş Senaryosu
        st_label = "Takip Eden Stop (Stop-Loss)"
        st_desc = "⚠️ Fiyat bu seviyenin <b>altına inerse</b> trend bozulur, stop olunmalıdır."
    else:
        # Düşüş Senaryosu
        st_label = "Trend Dönüşü (Direnç)"
        st_desc = "🚀 Fiyat bu seviyenin <b>üstüne çıkarsa</b> düşüş biter, yükseliş başlar."
    # -------------------------------------------
    
    # Fibonacci Formatlama
    sup_lbl, sup_val = data['nearest_sup']
    res_lbl, res_val = data['nearest_res']
    
    # --- GÖRSEL DÜZELTME ---
    if res_lbl == "ZİRVE AŞIMI":
        res_display = "---"
        res_desc = "🚀 Fiyat tüm dirençleri kırdı (Price Discovery)."
    else:
        res_display = f"{res_val:.2f}"
        res_desc = "Zorlu tavan. Geçilirse yükseliş hızlanır."
    
    html_content = f"""
    <div class="info-card" style="border-top: 3px solid #8b5cf6;">
        <div class="info-header" style="color:#4c1d95;">📐 Kritik Seviyeler & Trend</div>
        
        <div style="background:{st_color}15; padding:8px; border-radius:5px; border:1px solid {st_color}; margin-bottom:8px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:700; color:{st_color}; font-size:0.8rem;">{st_icon} SuperTrend (10,3)</span>
                <span style="font-weight:500; color:{st_color}; font-size:0.9rem;">{st_text}</span>
            </div>
            <div style="font-size:0.75rem; color:#64748B; margin-top:2px;">
                {st_label}: <strong style="color:#0f172a;">{data['st_val']:.2f}</strong>
            </div>
            <div style="font-size:0.65rem; color:#6b7280; font-style:italic; margin-top:4px; border-top:1px dashed {st_color}40; padding-top:2px;">
                {st_desc}
            </div>
        </div>

        <div style="display:grid; grid-template-columns: 1fr 1fr; gap:6px;">
            <div style="background:#f0fdf4; padding:6px; border-radius:4px; border:1px solid #bbf7d0;">
                <div style="font-size:0.65rem; color:#166534; font-weight:700;">EN YAKIN DİRENÇ 🚧</div>
                <div style="font-family:'JetBrains Mono'; font-weight:700; color:#15803d; font-size:0.85rem;">{res_val:.2f}</div>
                <div style="font-size:0.6rem; color:#166534; margin-bottom:2px;">Fib {res_lbl}</div>
                <div style="font-size:0.6rem; color:#64748B; font-style:italic; line-height:1.1;">Zorlu tavan. Geçilirse yükseliş hızlanır.</div>
            </div>
            
            <div style="background:#fef2f2; padding:6px; border-radius:4px; border:1px solid #fecaca;">
                <div style="font-size:0.65rem; color:#991b1b; font-weight:700;">EN YAKIN DESTEK 🛡️</div>
                <div style="font-family:'JetBrains Mono'; font-weight:700; color:#b91c1c; font-size:0.85rem;">{sup_val:.2f}</div>
                <div style="font-size:0.6rem; color:#991b1b; margin-bottom:2px;">Fib {sup_lbl}</div>
                <div style="font-size:0.6rem; color:#64748B; font-style:italic; line-height:1.1;">İlk savunma hattı. Düşüşü tutmalı.</div>
            </div>
        </div>
        
        <div style="margin-top:6px;">
            <div style="font-size:0.7rem; font-weight:700; color:#6b7280; margin-bottom:2px;">⚜️ Golden Pocket (0.618 - 0.65):</div>
            <div style="display:flex; align-items:center; gap:6px;">
                <div style="font-family:'JetBrains Mono'; font-size:0.8rem; background:#fffbeb; padding:2px 6px; border-radius:4px; border:1px dashed #f59e0b;">
                    {data['fibs'].get('0.618 (Golden)', 0):.2f}
                </div>
                <div style="font-size:0.65rem; color:#92400e; font-style:italic;">
                    Kurumsal alım bölgesi (İdeal Giriş).
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
    
# ==============================================================================
# 5. SIDEBAR UI
# ==============================================================================
with st.sidebar:
    st.markdown(f"""<div style="font-size:1.5rem; font-weight:700; color:#1e3a8a; text-align:center; padding-top: 10px; padding-bottom: 10px;">SMART MONEY RADAR</div><hr style="border:0; border-top: 1px solid #e5e7eb; margin-top:5px; margin-bottom:10px;">""", unsafe_allow_html=True)
    
# 1. SKORU VE NEDENLERİ HESAPLA (GÜNCELLENDİ)
    master_score, score_pros, score_cons = calculate_master_score(st.session_state.ticker)

    # 2. DERECELENDİRME VE RENKLER
    if master_score >= 85:    
        grade="A+ (MÜKEMMEL)"; score_color="#15803d"; icon="🏆"
    elif master_score >= 70:  
        grade="B (GÜÇLÜ)"; score_color="#0369a1"; icon="💎"
    elif master_score >= 50:  
        grade="C (NÖTR)"; score_color="#b45309"; icon="⚖️"
    else:                     
        grade="D (ZAYIF)"; score_color="#b91c1c"; icon="⚠️"

    # 3. YÜZDELERİ AYARLA
    is_asset_crypto_or_index = (st.session_state.ticker.startswith("^") or "-USD" in st.session_state.ticker or "XU" in st.session_state.ticker)
    
    if is_asset_crypto_or_index:
        trend_pct, fund_pct, mom_pct, smart_pct = "40", "0", "30", "30"
    else:
        trend_pct, fund_pct, mom_pct, smart_pct = "30", "30", "20", "20"

    # 4. SKOR KARTINI ÇİZ (HTML)
    st.markdown(f"""<div class="info-card" style="border-top: 3px solid {score_color};">
    <div class="info-header" style="display:flex; justify-content:space-between; align-items:center; color:{score_color};">
    <span>{icon} ANA SKOR (MASTER)</span>
    <span style="font-weight:800; font-size:1.2rem; background:{score_color}15; padding:2px 8px; border-radius:10px;">
    {master_score} - {grade.split(' ')[0]}
    </span>
    </div>
    <div style="display:grid; grid-template-columns: 1fr 1fr; gap:5px; margin-top:8px; text-align:center;">
    <div style="background:#f8fafc; padding:4px; border-radius:4px; border:1px solid #e2e8f0;">
    <div style="font-size:0.65rem; color:#64748B; font-weight:700;">TREND</div>
    <div style="font-size:0.8rem; font-weight:700; color:#334155;">📈 %{trend_pct}</div>
    </div>
    <div style="background:#f8fafc; padding:4px; border-radius:4px; border:1px solid #e2e8f0;">
    <div style="font-size:0.65rem; color:#64748B; font-weight:700;">TEMEL</div>
    <div style="font-size:0.8rem; font-weight:700; color:#334155;">📊 %{fund_pct}</div>
    </div>
    <div style="background:#f8fafc; padding:4px; border-radius:4px; border:1px solid #e2e8f0;">
    <div style="font-size:0.65rem; color:#64748B; font-weight:700;">MOMENTUM</div>
    <div style="font-size:0.8rem; font-weight:700; color:#334155;">🚀 %{mom_pct}</div>
    </div>
    <div style="background:#f8fafc; padding:4px; border-radius:4px; border:1px solid #e2e8f0;">
    <div style="font-size:0.65rem; color:#64748B; font-weight:700;">SMART</div>
    <div style="font-size:0.8rem; font-weight:700; color:#334155;">🧠 %{smart_pct}</div>
    </div>
    </div>
    </div>""", unsafe_allow_html=True)

    # 5. DETAYLI KARNE (EXPANDER İÇİNDE)
    with st.expander("📝 Puan Detayları (Neden?)", expanded=True):
        # Artılar
        if score_pros:
            st.markdown('<div style="font-size:0.75rem; font-weight:700; color:#166534; margin-bottom:2px;">✅ POZİTİF ETKENLER (+):</div>', unsafe_allow_html=True)
            for p in score_pros:
                st.markdown(f'<div style="font-size:0.7rem; color:#14532d; margin-left:5px; margin-bottom:2px;">• {p}</div>', unsafe_allow_html=True)
        
        st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)
        
        # Eksiler
        if score_cons:
            st.markdown('<div style="font-size:0.75rem; font-weight:700; color:#991b1b; margin-bottom:2px;">❌ NEGATİF / EKSİKLER (-):</div>', unsafe_allow_html=True)
            for c in score_cons:
                st.markdown(f'<div style="font-size:0.7rem; color:#7f1d1d; margin-left:5px; margin-bottom:2px;">• {c}</div>', unsafe_allow_html=True)
        
        if not score_pros and not score_cons:
            st.caption("Yeterli veri yok.")

    # --- TEMEL ANALİZ DETAYLARI (DÜZELTİLMİŞ & TEK PARÇA) ---
        sentiment_verisi = calculate_sentiment_score(st.session_state.ticker)
    
    # 1. PİYASA DUYGUSU (En Üstte)
    sentiment_verisi = calculate_sentiment_score(st.session_state.ticker)
    if sentiment_verisi:
        render_sentiment_card(sentiment_verisi)

    st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)

    # YENİ MINERVINI PANELİ (Hatasız Versiyon)
    render_minervini_panel_v2(st.session_state.ticker)
    
    # --- YILDIZ ADAYLARI (KESİŞİM PANELİ) ---
    st.markdown(f"""
    <div style="background: linear-gradient(45deg, #4f46e5, #7c3aed); color: white; padding: 8px; border-radius: 6px; text-align: center; font-weight: 700; font-size: 0.9rem; margin-bottom: 10px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
        🌟 YILDIZ ADAYLARI
    </div>
    """, unsafe_allow_html=True)
    
    # Kesişim Mantığı
    stars_found = False
    
    # Scroll Alanı Başlatıyoruz
    with st.container(height=150):
        
        # Verilerin varlığını kontrol et
        has_accum = st.session_state.accum_data is not None and not st.session_state.accum_data.empty
        has_warm = st.session_state.breakout_left is not None and not st.session_state.breakout_left.empty
        has_break = st.session_state.breakout_right is not None and not st.session_state.breakout_right.empty
        
        if has_accum:
            # Akıllı Para listesindeki sembolleri al
            acc_df = st.session_state.accum_data
            acc_symbols = set(acc_df['Sembol'].values)
            
            # 1. SENARYO: HAREKET (Kıranlar + Akıllı Para)
            if has_break:
                bo_df = st.session_state.breakout_right
                bo_symbols = set(bo_df['Sembol'].values)
                # Kesişim Bul
                move_stars = acc_symbols.intersection(bo_symbols)
                
                for sym in move_stars:
                    stars_found = True
                    # Fiyatı Accumulation listesinden çekelim
                    price = acc_df[acc_df['Sembol'] == sym]['Fiyat'].values[0]
                    
                    # Buton Formatı: 🚀 THYAO (305.50) | HAREKET
                    label = f"🚀 {sym} ({price}) | HAREKET"
                    if st.button(label, key=f"star_mov_{sym}", use_container_width=True):
                        on_scan_result_click(sym)
                        st.rerun()

            # 2. SENARYO: HAZIRLIK (Isınanlar + Akıllı Para)
            if has_warm:
                warm_df = st.session_state.breakout_left
                # Isınanlar listesinde bazen 'Sembol_Raw' bazen 'Sembol' olabilir, kontrol edelim
                col_name = 'Sembol_Raw' if 'Sembol_Raw' in warm_df.columns else 'Sembol'
                warm_symbols = set(warm_df[col_name].values)
                # Kesişim Bul
                prep_stars = acc_symbols.intersection(warm_symbols)
                
                for sym in prep_stars:
                    stars_found = True
                    price = acc_df[acc_df['Sembol'] == sym]['Fiyat'].values[0]
                    
                    # Buton Formatı: ⏳ ASELS (60.20) | HAZIRLIK
                    label = f"⏳ {sym} ({price}) | HAZIRLIK"
                    if st.button(label, key=f"star_prep_{sym}", use_container_width=True):
                        on_scan_result_click(sym)
                        st.rerun()
        
        if not stars_found:
            if not has_accum:
                st.info("'Sentiment Ajanı-Akıllı Para Topluyor' ile 'Breakout Ajanı' taramasının ortak sonuçları gösterilir.")
            elif not (has_warm or has_break):
                st.info("'Sentiment Ajanı-Akıllı Para Topluyor' ile 'Breakout Ajanı' taramasının ortak sonuçları gösterilir.")
            else:
                st.warning("Şu an toplanan ORTAK bir hisse yok.")

    st.divider()

    # 3. AI ANALIST (En Altta)
    with st.expander("🤖 AI Analist (Prompt)", expanded=True):
        st.caption("Verileri toplayıp ChatGPT için hazır metin oluşturur.")
        if st.button("📋 Analiz Metnini Hazırla", type="primary"): 
            st.session_state.generate_prompt = True

# ==============================================================================
# 6. ANA SAYFA (MAIN UI)
# ==============================================================================
col_cat, col_ass, col_search_in, col_search_btn = st.columns([1.5, 2, 2, 0.7])
try: cat_index = list(ASSET_GROUPS.keys()).index(st.session_state.category)
except ValueError: cat_index = 0
with col_cat: st.selectbox("Kategori", list(ASSET_GROUPS.keys()), index=cat_index, key="selected_category_key", on_change=on_category_change, label_visibility="collapsed")

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

with col_search_in: st.text_input("Manuel", placeholder="Kod", key="manual_input_key", label_visibility="collapsed")
with col_search_btn: st.button("Ara", on_click=on_manual_button_click)
st.markdown("<hr style='margin-top:0.5rem; margin-bottom:0.5rem;'>", unsafe_allow_html=True)

if st.session_state.generate_prompt:
    t = st.session_state.ticker
    
    # --- 1. TÜM VERİLERİ TOPLA (Eksikler Eklendi) ---
    info = fetch_stock_info(t)
    ict_data = calculate_ict_deep_analysis(t) or {}
    sent_data = calculate_sentiment_score(t) or {}
    tech_data = get_tech_card_data(t) or {}
    pa_data = calculate_price_action_dna(t) or {}
    levels_data = get_advanced_levels_data(t) or {}
    synth_data = calculate_synthetic_sentiment(t) 
    
    # EKLENEN YENİ VERİLER:
    mini_data = calculate_minervini_sepa(t) or {} # Minervini
    fund_data = get_fundamental_score(t) or {}    # Temel
    master_score, pros, cons = calculate_master_score(t) # Master Skor

    # Radar verisi kontrolü
    radar_val = "Veri Yok"; radar_setup = "Belirsiz"
    if st.session_state.radar2_data is not None:
        r_row = st.session_state.radar2_data[st.session_state.radar2_data['Sembol'] == t]
        if not r_row.empty:
            radar_val = f"{r_row.iloc[0]['Skor']}/7"
            radar_setup = r_row.iloc[0]['Setup']
    
    # --- 2. GİZLİ PARA AKIŞI ANALİZİ (WMA) ---
    para_akisi_txt = "Veri Yetersiz"
    if synth_data is not None and len(synth_data) > 15:
        window = 10
        recent_mf = synth_data['MF_Smooth'].tail(window).values
        weights = np.arange(1, window + 1)
        wma_now = np.sum(recent_mf * weights) / np.sum(weights)
        prev_mf_slice = synth_data['MF_Smooth'].iloc[-(window+1):-1].values
        wma_prev = np.sum(prev_mf_slice * weights) / np.sum(weights)
        
        ana_renk = "MAVİ (Pozitif)" if wma_now > 0 else "KIRMIZI (Negatif)"
        momentum_durumu = ""
        if wma_now > 0:
            if wma_now > wma_prev: momentum_durumu = "GÜÇLENİYOR 🚀 (İştah Artıyor)"
            else: momentum_durumu = "ZAYIFLIYOR ⚠️ (Alıcılar Yoruldu)"
        else:
            if wma_now < wma_prev: momentum_durumu = "DERİNLEŞİYOR 🔻 (Satış Baskısı Artıyor)" 
            else: momentum_durumu = "ZAYIFLIYOR ✅ (Satışlar Kuruyor/Dönüş Sinyali)" 

        para_akisi_txt = f"{ana_renk} | Momentum: {momentum_durumu} (10 Günlük Ağırlıklı Analiz)"

    # --- 3. METİN HAZIRLIKLARI ---
    def clean_text(text): return re.sub(r'<[^>]+>', '', str(text))
    
    # Minervini Metni
    mini_txt = "Trend Zayıf / Veri Yok"
    if mini_data:
        mini_txt = f"{mini_data.get('Durum', '-')} | RS Rating: {mini_data.get('rs_rating', '-')}"
        if mini_data.get('is_vcp'): mini_txt += " | ✅ VCP (Sıkışma) Var"
        if mini_data.get('is_dry'): mini_txt += " | ✅ Arz Kurumuş"

    # Temel Analiz Metni
    fund_txt = "Veri Yok / Önemsiz"
    if fund_data and fund_data.get('details'):
        fund_txt = " | ".join(fund_data['details'])

    # Master Skor Özeti
    master_txt = f"{master_score}/100"
    pros_txt = ", ".join(pros[:5]) # İlk 5 artı maddeyi al

    st_txt = "Veri Yok"
    if levels_data:
        st_dir_txt = "YÜKSELİŞ (AL)" if levels_data.get('st_dir') == 1 else "DÜŞÜŞ (SAT)"
        st_txt = f"{st_dir_txt} | Seviye: {levels_data.get('st_val', 0):.2f}"
        sup_l, sup_v = levels_data.get('nearest_sup', (None, 0))
        res_l, res_v = levels_data.get('nearest_res', (None, 0))
        fib_sup = f"{sup_v:.2f} (Fib {sup_l})" if sup_l else "Bilinmiyor"
        fib_res = f"{res_v:.2f} (Fib {res_l})" if res_l else "Bilinmiyor"

    fiyat_str = f"{info.get('price', 0):.2f}" if info else "0.00"
    sma50_str = f"{tech_data.get('sma50', 0):.2f}"
    liq_str = f"{ict_data.get('target', 0):.2f}" if ict_data.get('target', 0) > 0 else "Belirsiz / Yok"
    mum_desc = pa_data.get('candle', {}).get('desc', 'Belirgin formasyon yok')
    pa_div = pa_data.get('div', {}).get('title', 'Yok')

    # --- 4. FİNAL PROMPT (GÜNCELLENDİ) ---
    prompt = f"""*** SİSTEM ROLLERİ ***
Sen Price Action, ICT (Smart Money) ve Mark Minervini (SEPA) stratejilerinde uzmanlaşmış kıdemli bir Fon Yöneticisisin.
Aşağıdaki TEKNİK ve TEMEL verilere dayanarak profesyonel bir işlem planı oluştur.

*** VARLIK KİMLİĞİ ***
- Sembol: {t}
- GÜNCEL FİYAT: {fiyat_str}
- ANA SKOR: {master_txt} (Algoritmik Puan)
- Temel Artılar: {pros_txt}

*** 1. TREND VE GÜÇ (Minervini & SuperTrend) ***
- SuperTrend (Yön): {st_txt}
- Minervini Durumu: {mini_txt}
- SMA50 Durumu: {sma50_str}

*** 2. SMART MONEY & ICT YAPISI ***
- Market Yapısı: {ict_data.get('structure', 'Bilinmiyor')} ({ict_data.get('bias', 'Nötr')})
- Konum (Zone): {ict_data.get('zone', 'Bilinmiyor')}
- Gizli Para Akışı (10G WMA): {para_akisi_txt}
- Aktif FVG: {ict_data.get('fvg_txt', 'Yok')}

*** 3. ŞİRKET TEMEL KALİTESİ ***
- Öne Çıkanlar: {fund_txt}

*** 4. HEDEFLER VE RİSK ***
- Direnç (Hedef): {fib_res}
- Destek (Stop): {fib_sup}
- Hedef Likidite: {liq_str}

*** 5. PRICE ACTION & UYARILAR ***
- Mum Formasyonu: {mum_desc}
- RSI Uyumsuzluğu: {pa_div} (Varsa çok dikkat et!)

*** GÖREVİN ***
Verileri sentezle ve bir "Sniper" gibi işlem kurgula:
1. ANALİZ: Fiyat trendini (Minervini) ve Smart Money niyetini (Para Akışı) birleştirerek yorumla. Şirket temel olarak bu yükselişi destekliyor mu?
2. KARAR: [Long / Short / İzle]
3. STRATEJİ: Giriş, Stop ve Kar Al seviyelerini net rakamlarla belirt.
4. UYARI: Eğer RSI uyumsuzluğu, Hacim düşüklüğü veya Trend tersliği varsa büyük harflerle uyar.
"""
    with st.sidebar:
        st.code(prompt, language="text")
        st.success("Prompt Güncellendi: Temel Analiz + Minervini + Master Skor eklendi! 🧠")
    
    st.session_state.generate_prompt = False

info = fetch_stock_info(st.session_state.ticker)

col_left, col_right = st.columns([4, 1])

# --- SOL SÜTUN ---
with col_left:
    synth_data = calculate_synthetic_sentiment(st.session_state.ticker)
    if synth_data is not None and not synth_data.empty: render_synthetic_sentiment_panel(synth_data)
    render_detail_card_advanced(st.session_state.ticker)

    st.markdown('<div class="info-header" style="margin-top: 15px; margin-bottom: 10px;">🕵️ Sentiment Ajanı (Akıllı Para Topluyor: 60/100)</div>', unsafe_allow_html=True)
    
    if 'accum_data' not in st.session_state: st.session_state.accum_data = None
    if 'stp_scanned' not in st.session_state: st.session_state.stp_scanned = False
    if 'stp_crosses' not in st.session_state: st.session_state.stp_crosses = []
    if 'stp_trends' not in st.session_state: st.session_state.stp_trends = []
    if 'stp_filtered' not in st.session_state: st.session_state.stp_filtered = []

    if st.button(f"🕵️ SENTIMENT & MOMENTUM TARAMASI BAŞLAT ({st.session_state.category})", type="primary", use_container_width=True):
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
            st.markdown("<div style='text-align:center; color:#1e40af; font-weight:700; font-size:0.9rem; margin-bottom:5px;'>⚡ STP KESİŞİM</div>", unsafe_allow_html=True)
            with st.container(height=200, border=True):
                if st.session_state.stp_crosses:
                    for item in st.session_state.stp_crosses:
                        if st.button(f"🚀 {item['Sembol']} ({item['Fiyat']:.2f})", key=f"stp_c_{item['Sembol']}", use_container_width=True): 
                            st.session_state.ticker = item['Sembol']
                            st.rerun()
                else:
                    st.caption("Kesişim yok.")
        
        with c2:
            st.markdown("<div style='text-align:center; color:#b91c1c; font-weight:700; font-size:0.8rem; margin-bottom:5px;'>🎯 MOMENTUM BAŞLANGICI?</div>", unsafe_allow_html=True)
            with st.container(height=200, border=True):
                if st.session_state.stp_filtered:
                    for item in st.session_state.stp_filtered:
                        if st.button(f"🔥 {item['Sembol']} ({item['Fiyat']:.2f})", key=f"stp_f_{item['Sembol']}", use_container_width=True): 
                            st.session_state.ticker = item['Sembol']
                            st.rerun()
                else:
                    st.caption("Tam eşleşme yok.")

        with c3:
            st.markdown("<div style='text-align:center; color:#15803d; font-weight:700; font-size:0.8rem; margin-bottom:5px;'>✅ STP TREND</div>", unsafe_allow_html=True)
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
                        btn_label = f"{icon} {row['Sembol']} ({row['Fiyat']}) | {q_tag} | {rs_short}"
                        
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

    
    if st.button(f"⚡ {st.session_state.category} İÇİN BREAK-OUT TARAMASI BAŞLAT", type="primary", key="dual_breakout_btn", use_container_width=True):
        with st.spinner("Ajanlar sahaya indi: Hem ısınanlar hem kıranlar taranıyor..."):
            curr_list = ASSET_GROUPS.get(st.session_state.category, [])
            # Paralel tarama simülasyonu (Sırayla çalışır ama hızlıdır)
            st.session_state.breakout_left = agent3_breakout_scan(curr_list) # Mevcut Isınanlar
            st.session_state.breakout_right = scan_confirmed_breakouts(curr_list) # Yeni Kıranlar
            st.rerun()

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
    # 🦁 YENİ: MINERVINI SEPA AJANI (SOL TARAF - TARAYICI)
    # ---------------------------------------------------------
    if 'minervini_data' not in st.session_state: st.session_state.minervini_data = None

    st.markdown('<div class="info-header" style="margin-top: 20px; margin-bottom: 5px;">🦁 Minervini SEPA Ajanı (85/100)</div>', unsafe_allow_html=True)
    
    # 1. TARAMA BUTONU
    if st.button(f"🦁 SEPA TARAMASI BAŞLAT ({st.session_state.category})", type="primary", use_container_width=True, key="btn_scan_sepa"):
        with st.spinner("Aslan avda... Trend şablonu, VCP ve RS taranıyor..."):
            current_assets = ASSET_GROUPS.get(st.session_state.category, [])
            st.session_state.minervini_data = scan_minervini_batch(current_assets)
            
    # 2. SONUÇ EKRANI (Scroll Bar - 300px)
    if st.session_state.minervini_data is not None:
        count = len(st.session_state.minervini_data)
        if count > 0:
            st.success(f"🎯 Kriterlere uyan {count} hisse bulundu!")
            with st.container(height=300, border=True):
                for i, row in st.session_state.minervini_data.iterrows():
                    sym = row['Sembol']
                    icon = "💎" if "SÜPER" in row['Durum'] else "🔥"
                    label = f"{icon} {sym} ({row['Fiyat']}) | {row['Durum']} | {row['Detay']}"
                    
                    if st.button(label, key=f"sepa_{sym}_{i}", use_container_width=True):
                        on_scan_result_click(sym)
                        st.rerun()
        else:
            st.warning("Bu zorlu kriterlere uyan hisse bulunamadı.")

    # ---------------------------------------------------------
    # 📐 YENİ: FORMASYON AJANI (TOBO, BAYRAK, RANGE)
    # ---------------------------------------------------------
    if 'pattern_data' not in st.session_state: st.session_state.pattern_data = None

    st.markdown('<div class="info-header" style="margin-top: 20px; margin-bottom: 5px;">📐 Formasyon Ajanı (TOBO, Bayrak, Range, Fincan-Kulp, Yükselen Üçgen)</div>', unsafe_allow_html=True)
    
    # TARAMA BUTONU
    if st.button(f"📐 FORMASYONLARI TARA ({st.session_state.category})", type="primary", use_container_width=True, key="btn_scan_pattern"):
        with st.spinner("Cetveller çekiliyor... Bayraklar ve TOBO'lar aranıyor..."):
            current_assets = ASSET_GROUPS.get(st.session_state.category, [])
            st.session_state.pattern_data = scan_chart_patterns(current_assets)
            
    # SONUÇ EKRANI
    if st.session_state.pattern_data is not None:
        count = len(st.session_state.pattern_data)
        if count > 0:
            st.success(f"🧩 {count} adet formasyon yapısı tespit edildi!")
            with st.container(height=300, border=True):
                for i, row in st.session_state.pattern_data.iterrows():
                    sym = row['Sembol']
                    pat = row['Formasyon']
                    
                    # Renkler
                    icon = "🚩" if "BAYRAK" in pat else "📦" if "RANGE" in pat else "🧛"
                    
                    label = f"{icon} {sym} ({row['Fiyat']:.2f}) | {pat}"
                    
                    if st.button(label, key=f"pat_{sym}_{i}", use_container_width=True, help=row['Detay']):
                        on_scan_result_click(sym)
                        st.rerun()
        else:
            st.info("Şu an belirgin bir 'Kitabi Formasyon' (TOBO, Bayrak vb.) oluşumu bulunamadı.")
    # ---------------------------------------------------------
    
    st.markdown(f"<div style='font-size:0.9rem;font-weight:600;margin-bottom:4px; margin-top:20px;'>📡 {st.session_state.ticker} hakkında haberler ve analizler</div>", unsafe_allow_html=True)
    symbol_raw = st.session_state.ticker; base_symbol = (symbol_raw.replace(".IS", "").replace("=F", "").replace("-USD", "")); lower_symbol = base_symbol.lower()
    st.markdown(f"""<div class="news-card" style="display:flex; flex-wrap:wrap; align-items:center; gap:8px; border-left:none;"><a href="https://seekingalpha.com/symbol/{base_symbol}/news" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.8rem; font-weight:600;">SeekingAlpha</div></a><a href="https://finance.yahoo.com/quote/{base_symbol}/news" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.8rem; font-weight:600;">Yahoo Finance</div></a><a href="https://www.nasdaq.com/market-activity/stocks/{lower_symbol}/news-headlines" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.8rem; font-weight:600;">Nasdaq</div></a><a href="https://stockanalysis.com/stocks/{lower_symbol}/" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.8rem; font-weight:600;">StockAnalysis</div></a><a href="https://finviz.com/quote.ashx?t={base_symbol}&p=d" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.8rem; font-weight:600;">Finviz</div></a><a href="https://unusualwhales.com/stock/{base_symbol}/overview" target="_blank" style="text-decoration:none;"><div style="padding:4px 8px; border-radius:4px; border:1px solid #e5e7eb; font-size:0.7rem; font-weight:600;">UnusualWhales</div></a></div>""", unsafe_allow_html=True)

# --- SAĞ SÜTUN ---
with col_right:
    if not info: info = fetch_stock_info(st.session_state.ticker)
    
    # 1. Fiyat
    if info and info.get('price'):
        display_ticker = st.session_state.ticker.replace(".IS", "").replace("=F", "")
        cls = "delta-pos" if info['change_pct'] >= 0 else "delta-neg"
        st.markdown(f'<div class="stat-box-small" style="margin-bottom:10px;"><p class="stat-label-small">FİYAT: {display_ticker}</p><p class="stat-value-small money-text">{info["price"]:.2f}<span class="stat-delta-small {cls}">{"+" if info["change_pct"]>=0 else ""}{info["change_pct"]:.2f}%</span></p></div>', unsafe_allow_html=True)
    else: st.warning("Fiyat verisi alınamadı.")

    # 2. Price Action Paneli
    render_price_action_panel(st.session_state.ticker)
    
    # 3. Kritik Seviyeler
    render_levels_card(st.session_state.ticker)
    
    # 4. ICT Paneli
    render_ict_deep_panel(st.session_state.ticker)
    
    # 5. Ortak Fırsatlar Başlığı
    st.markdown(f"<div style='font-size:0.9rem;font-weight:600;margin-bottom:4px; margin-top:10px; color:#1e40af; background-color:{current_theme['box_bg']}; padding:5px; border-radius:5px; border:1px solid #1e40af;'>🎯 Ortak Fırsatlar</div>", unsafe_allow_html=True)
    
    # 6. Ortak Fırsatlar Listesi
    with st.container(height=250):
        df1 = st.session_state.scan_data; df2 = st.session_state.radar2_data
        if df1 is not None and df2 is not None and not df1.empty and not df2.empty:
            commons = []; symbols = set(df1["Sembol"]).intersection(set(df2["Sembol"]))
            if symbols:
                for sym in symbols:
                    row1 = df1[df1["Sembol"] == sym].iloc[0]; row2 = df2[df2["Sembol"] == sym].iloc[0]
                    r1_score = float(row1["Skor"]); r2_score = float(row2["Skor"]); combined_score = r1_score + r2_score
                    commons.append({"symbol": sym, "r1_score": r1_score, "r2_score": r2_score, "combined": combined_score, "r1_max": 7, "r2_max": 7})
                sorted_commons = sorted(commons, key=lambda x: x["combined"], reverse=True)
                cols = st.columns(2) 
                for i, item in enumerate(sorted_commons):
                    sym = item["symbol"]
                    score_text_safe = f"{i+1}. {sym} ({int(item['combined'])})"
                    with cols[i % 2]:
                        if st.button(f"{score_text_safe} | R1:{int(item['r1_score'])} R2:{int(item['r2_score'])}", key=f"c_select_{sym}", help="Detaylar için seç", use_container_width=True): 
                            on_scan_result_click(sym); st.rerun()
            else: st.info("Kesişim yok.")
        else: st.caption("İki radar da çalıştırılmalı.")
   
    tab1, tab2 = st.tabs(["🧠 RADAR 1", "🚀 RADAR 2"])
    with tab1:
        if st.button(f"⚡ {st.session_state.category} Tara", type="primary", key="r1_main_scan_btn"):
            with st.spinner("Taranıyor..."): st.session_state.scan_data = analyze_market_intelligence(ASSET_GROUPS.get(st.session_state.category, []))
        if st.session_state.scan_data is not None:
            with st.container(height=250):
                cols = st.columns(2)
                for i, (index, row) in enumerate(st.session_state.scan_data.iterrows()):
                    sym = row["Sembol"]
                    with cols[i % 2]:
                        if st.button(f"🔥 {row['Skor']}/7 | {row['Sembol']}", key=f"r1_b_{i}", use_container_width=True): on_scan_result_click(row['Sembol']); st.rerun()
    with tab2:
        if st.button(f"🚀 RADAR 2 Tara", type="primary", key="r2_main_scan_btn"):
            with st.spinner("Taranıyor..."): st.session_state.radar2_data = radar2_scan(ASSET_GROUPS.get(st.session_state.category, []))
        if st.session_state.radar2_data is not None:
            with st.container(height=250):
                cols = st.columns(2)
                for i, (index, row) in enumerate(st.session_state.radar2_data.iterrows()):
                    sym = row["Sembol"]
                    with cols[i % 2]:
                        if st.button(f"🚀 {row['Skor']}/7 | {row['Sembol']} | {row['Setup']}", key=f"r2_b_{i}", use_container_width=True): on_scan_result_click(row['Sembol']); st.rerun()

