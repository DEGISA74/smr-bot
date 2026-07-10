#!/usr/bin/env python3
"""
produce_stock_json.py
BIST100 hisseleri için bireysel JSON dosyaları üretir (100 hisse sabit liste).

Mantık:
  - 100 hisseyi işle → stocks/TICKER.json olarak kaydet (veriler bekler)
  - Erken Radar hangi hisseyi seçerse app.js o hissenin JSON'unu açar
  - Radar dışındaki hisseler için JSON disk'te var ama frontend göstermez

Her hisse için çıktı:
  /var/www/smr/frontend/stocks/TICKER.json

Veri yapısı latest.json'daki xu100 formatıyla birebir uyumlu —
app.js'teki tüm render fonksiyonları değişiklik gerektirmeden çalışır.

Kullanım:
  python3 /var/www/smr/backend/produce_stock_json.py

Crontab (iş günü 16:00 UTC = 19:00 TR):
  0 16 * * 1-5 cd ~/smr && source venv/bin/activate && python3 /var/www/smr/backend/produce_stock_json.py >> /var/www/smr/backend/stock_json.log 2>&1
"""

import json
import os
import sys
import datetime
import traceback

import numpy as np

# 6 Tem 2026 — veri politikası TEK KAYNAK (app.py/fetcher/smr_core ile aynı HAM fiyat)
try:
    from data_policy import AUTO_ADJUST as _AUTO_ADJUST, drop_adj_close as _drop_adj_close
except Exception:
    _AUTO_ADJUST = False
    def _drop_adj_close(_d): return _d

try:
    import yfinance as yf
except ImportError:
    sys.exit("yfinance yüklü değil. Çalıştır: pip install yfinance")

OUT_DIR = "/var/www/smr/frontend/stocks"

# BIST100 sabit listesi — Erken Radar bu 100 hisseden seçim yapar.
# JSON'lar hazırda bekler; frontend sadece Erken Radar public_items'ındakileri gösterir.
# Güncel BIST100 (XU100) bileşenleri — Mynet Finans / Borsa İstanbul verisi
# Erken Radar bu liste içinden seçim yapar; JSON'lar hazırda bekler.
BIST100_TICKERS = [
    "AKBNK", "AKSA",  "AKSEN", "AGHOL", "AEFES", "ALARK", "ALTNY", "ANSGR",
    "ARCLK", "ASELS", "ASTOR", "BALSU", "BIMAS", "BRSAN", "BRYAT", "BSOKE",
    "BTCIM", "CANTE", "CCOLA", "CIMSA", "CVKMD", "CWENE", "DAPGM", "DOHOL",
    "DOAS",  "DSTKF", "ECILC", "EFOR",  "EKGYO", "ENKAI", "ENERY", "ENJSA",
    "EREGL", "EUREN", "EUPWR", "FENER", "FROTO", "GARAN", "GENIL", "GESAN",
    "GLRMK", "GRSEL", "GRTHO", "GSRAY", "GUBRF", "HALKB", "HEKTS", "ISCTR",
    "IZENR", "KLRHO", "KCHOL", "KONTR", "KRDMD", "KTLEV", "KUYAS", "MAGEN",
    "MAVI",  "MGROS", "MIATK", "MPARK", "OBAMS", "ODAS",  "OTKAR", "OYAKC",
    "PAHOL", "PASEU", "PATEK", "PGSUS", "PETKM", "PSGYO", "QUAGR", "RALYH",
    "REEDR", "SAHOL", "SARKY", "SASA",  "SISE",  "SKBNK", "SOKM",  "TABGD",
    "TAVHL", "TCELL", "THYAO", "TKFEN", "TOASO", "TRENJ", "TRALT", "TRMET",
    "TSKB",  "TTKOM", "TUKAS", "TUPRS", "TUREX", "TURSG", "ULKER", "VAKBN",
    "VESTL", "YKBNK", "ZOREN",
    # Erken Radar demo — BIST500 hisseleri (radar'a girince JSON hazır olsun)
    "AYES",  "ISDMR", "ALFAS", "ASTOR", "ERBOS", "KARSN", "ECZYT",
]
BIST100_TICKERS = list(dict.fromkeys(BIST100_TICKERS))  # tekrarları kaldır


# ── Teknik Hesaplamalar ────────────────────────────────────────────────────────

def calc_rsi(series, period=14):
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / (avg_loss + 1e-9)
    return 100 - (100 / (1 + rs))


def calc_mf_smooth(df, span=10):
    """
    Para Akışı İvmesi (MF_Smooth) — 9 Tem 2026: TEK KAYNAK
    indicators.compute_flow_momentum (%50 STP eğimi + %50 kompozit puan değişimi).
    app paneli / bot / infografik / scan_core ile birebir aynı barlar.
    `span` parametresi geriye uyumluluk için duruyor, artık kullanılmıyor.
    """
    from indicators import compute_flow_momentum
    mf, _stp = compute_flow_momentum(df)
    if mf is None:
        return df['Close'] * 0.0   # hata: nötr (sıfır) barlar — pd import'suz
    return mf


def calc_stp(df, span=6):
    """
    STP (Sentiment Trend Price):
    EMA(span) of Typical Price — app.js Sentiment grafiğinin sarı çizgisi.
    """
    tp = (df['High'] + df['Low'] + df['Close']) / 3.0
    return tp.ewm(span=span, adjust=False).mean()


def get_regime(close, sma50, sma200, rsi):
    if close > sma200 and close > sma50 and rsi > 55:
        return "Boğa Trendi",  "green"
    elif close > sma200 and rsi >= 50:
        return "Hafif Yükseliş", "green"
    elif close < sma200 and close < sma50 and rsi < 45:
        return "Düşüş Trendi", "red"
    elif close < sma200:
        return "Zayıf",        "red"
    else:
        return "Nötr",         "orange"


def calc_genel_skor(close, sma50, sma200, rsi, degisim_pct, pozisyon_pct):
    skor = 0
    if close > sma200:        skor += 25
    if close > sma50:         skor += 20
    if rsi > 50:              skor += 15
    if rsi > 60:              skor += 10
    if degisim_pct > 0:       skor += 10
    if pozisyon_pct > 50:     skor += 10
    if pozisyon_pct > 75:     skor += 10
    return min(skor, 100)


# ── Tek Hisse İşleme ──────────────────────────────────────────────────────────

def process_ticker(ticker):
    yf_ticker = ticker.replace(".IS", "") + ".IS"
    try:
        df = yf.download(
            yf_ticker,
            period="1y",
            interval="1d",
            progress=False,
            auto_adjust=_AUTO_ADJUST   # 6 Tem 2026 — HAM fiyat (data_policy TEK KAYNAK, app ile aynı)
        )
        df = _drop_adj_close(df)

        if df is None or len(df) < 30:
            n = len(df) if df is not None else 0
            print(f"  {ticker}: yetersiz veri ({n} bar)")
            return None

        # yfinance bazen çok sütunlu MultiIndex döndürür — düzelt
        if hasattr(df.columns, 'levels') and len(df.columns.levels) > 1:
            df.columns = df.columns.droplevel(1)

        df = df.dropna(subset=['Close', 'High', 'Low', 'Volume'])
        if len(df) < 30:
            print(f"  {ticker}: NaN temizleme sonrası yetersiz ({len(df)} bar)")
            return None

        closes = df['Close']
        highs  = df['High']
        lows   = df['Low']

        # Temel metrikler
        kapanis     = float(closes.iloc[-1])
        prev        = float(closes.iloc[-2])
        degisim_pct = round((kapanis - prev) / prev * 100, 2)

        sma50  = float(closes.rolling(50,  min_periods=30).mean().iloc[-1])
        sma200 = float(closes.rolling(200, min_periods=100).mean().iloc[-1])
        rsi    = float(calc_rsi(closes).iloc[-1])

        yil_yuksek = float(highs.iloc[-252:].max())
        yil_dusuk  = float(lows.iloc[-252:].min())
        rng        = yil_yuksek - yil_dusuk
        pozisyon   = round((kapanis - yil_dusuk) / (rng + 1e-9) * 100, 1)

        rejim, rejim_renk = get_regime(kapanis, sma50, sma200, rsi)
        genel_skor        = calc_genel_skor(
            kapanis, sma50, sma200, rsi, degisim_pct, pozisyon
        )

        # Grafik verisi — son 30 iş günü (app.js xu100_grafik formatı)
        df30   = df.iloc[-30:]
        mf_ser = calc_mf_smooth(df)
        stp_ser= calc_stp(df)

        grafik = []
        for idx, row in df30.iterrows():
            try:
                date_str = idx.strftime("%-d %b")          # "22 May"
            except Exception:
                date_str = str(idx)[:10]

            mf_val  = float(mf_ser.reindex([idx]).iloc[0])  if idx in mf_ser.index  else 0.0
            stp_val = float(stp_ser.reindex([idx]).iloc[0]) if idx in stp_ser.index else kapanis
            price_v = float(row['Close'])

            if any(v != v for v in [mf_val, stp_val, price_v]):   # NaN check
                continue
            grafik.append({
                "date":  date_str,
                "mf":    round(mf_val,  4),
                "stp":   round(stp_val, 4),
                "price": round(price_v, 2)
            })

        if not grafik:
            print(f"  {ticker}: grafik verisi oluşturulamadı")
            return None

        # Güncelleme saati (TR, UTC+3)
        now_tr = datetime.datetime.utcnow() + datetime.timedelta(hours=3)

        return {
            "ticker":         ticker,
            "tarih":          now_tr.strftime("%Y-%m-%d"),
            "guncelleme":     now_tr.strftime("%H:%M"),
            "kapanis":        round(kapanis, 2),
            "degisim_pct":    degisim_pct,
            "sma50":          round(sma50,  2),
            "sma200":         round(sma200, 2),
            "rsi":            round(rsi, 1),
            "rejim":          rejim,
            "rejim_renk":     rejim_renk,
            "yillik_yuksek":  round(yil_yuksek, 2),
            "yillik_dusuk":   round(yil_dusuk,  2),
            "pozisyon_pct":   pozisyon,
            "genel_skor":     genel_skor,
            "grafik":         grafik
        }

    except Exception:
        traceback.print_exc()
        return None


# ── Ana Akış ──────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    tickers  = BIST100_TICKERS
    now_str  = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    ok_count = 0
    print(f"[produce_stock_json] {now_str} — {len(tickers)} hisse işlenecek")

    for ticker in tickers:
        print(f"  → {ticker} işleniyor...")
        data = process_ticker(ticker)
        if data:
            out_path = os.path.join(OUT_DIR, f"{ticker}.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"     ✓ {out_path}  (fiyat: {data['kapanis']}, RSI: {data['rsi']})")
            ok_count += 1
        else:
            print(f"     ✗ {ticker} — veri alınamadı")

    print(f"[produce_stock_json] Tamamlandı: {ok_count}/{len(tickers)}")


if __name__ == "__main__":
    main()
