# -*- coding: utf-8 -*-
"""8502 ABD terminalinin dondurulmuş S&P 500 ilk-200 evreni.

Bu liste resmi bir "S&P 200" endeksi değildir. S&P 500 bileşenlerinin piyasa
değeri sıralamasındaki ilk 200 hisse kodunun 6 Eylül 2026 fotoğrafıdır. Evren
aylık olarak kontrollü güncellenir; geçmiş tarama kayıtlarının hangi sembol
kümeyle üretildiği böylece yeniden kurulabilir.
"""
from __future__ import annotations


US200_CATEGORY = "S&P 200"
US200_AS_OF = "2026-09-06"
US200_SOURCE = "https://www.slickcharts.com/sp500"

# Yahoo sembol biçimi kullanılır: Berkshire Hathaway B sınıfı BRK-B'dir.
US200_TICKERS = (
    "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "AVGO", "META", "TSLA", "MU",
    "BRK-B", "LLY", "JPM", "WMT", "AMD", "V", "JNJ", "XOM", "MA", "INTC",
    "ORCL", "ABBV", "BAC", "CSCO", "PLTR", "CVX", "COST", "LRCX", "KO", "CAT",
    "MRK", "AMAT", "UNH", "GE", "MS", "PG", "DELL", "NFLX", "HD", "GS",
    "PM", "WFC", "PANW", "RTX", "SNDK", "GEV", "ANET", "KLAC", "AMGN", "TXN",
    "C", "TMO", "IBM", "AXP", "LIN", "CRWD", "CRM", "VZ", "APH", "MRVL",
    "TMUS", "STX", "SCHW", "PEP", "ABT", "GILD", "DE", "DIS", "MCD", "QCOM",
    "T", "ADI", "NEE", "BLK", "UNP", "WELL", "WDC", "BA", "PFE", "COP",
    "ETN", "UBER", "NOW", "DHR", "TJX", "BKNG", "VRTX", "BMY", "NEM", "COF",
    "GLW", "CB", "SPGI", "PLD", "ISRG", "PGR", "CVS", "PH", "LMT", "MDT",
    "SBUX", "SYK", "MO", "LOW", "FTNT", "ACN", "BNY", "ADP", "HOOD", "MPC",
    "VRT", "APP", "ABNB", "VLO", "ADBE", "CEG", "MCK", "FCX", "HWM", "EQIX",
    "BX", "PSX", "SO", "CME", "USB", "TT", "PNC", "GD", "KKR", "CMCSA",
    "PWR", "DUK", "DASH", "CSX", "INTU", "WMB", "ICE", "MRSH", "ELV", "JCI",
    "MAR", "HCA", "WM", "UPS", "MMM", "MNST", "MCO", "SLB", "EMR", "REGN",
    "AMT", "SHW", "CDNS", "CTAS", "LITE", "APO", "ECL", "MDLZ", "MSI", "CMI",
    "TRV", "GM", "ITW", "DDOG", "FDX", "EOG", "SNPS", "TGT", "CI", "NSC",
    "ROST", "NOC", "ORLY", "RCL", "WBD", "CL", "HLT", "KMI", "DLR", "BSX",
    "HPE", "AON", "RSG", "AEP", "SPG", "AJG", "APD", "HON", "PCAR", "ALL",
    "TDG", "COR", "TFC", "BKR", "URI", "CRH", "GWW", "TRGP", "MET", "TEL",
)


def validate_us200_universe() -> None:
    """Evren kaynağındaki sessiz kopya/biçim hatalarını açılışta reddeder."""
    if len(US200_TICKERS) != 200:
        raise ValueError(f"ABD evreni 200 sembol olmalı; bulunan={len(US200_TICKERS)}")
    if len(set(US200_TICKERS)) != len(US200_TICKERS):
        raise ValueError("ABD evreninde yinelenen sembol var")
    invalid = [
        symbol for symbol in US200_TICKERS
        if not symbol or symbol != symbol.upper() or symbol.endswith(".IS")
    ]
    if invalid:
        raise ValueError(f"ABD evreninde geçersiz sembol biçimi: {invalid}")


validate_us200_universe()
