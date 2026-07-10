#!/usr/bin/env python3
"""
produce_light_stock.py  —  Light site (frontend_light) tek-hisse JSON üretici.

Mevcut produce_stock_json.py'den farkı:
  - Çıktı ŞEKLİ app.js'in beklediği latest.json yapısına birebir uyumlu
    ({meta, xu100:{...}, xu100_grafik:[...], piyasa_ozeti:{...}}) — render
    fonksiyonları hiç değişmeden çalışır.
  - xu100 bloğuna "ticker" alanı eklenir (etiketler dinamik olsun diye).
  - piyasa_ozeti mevcut latest.json'dan taşınır (piyasa-geneli bağlam, ortak).
  - Lokal public/frontend_light/data/ klasörüne yazar.
  - Windows-uyumlu tarih formatı.

Kullanım:
  python produce_light_stock.py ASTOR            # tek hisse
  python produce_light_stock.py ASTOR THYAO SISE # birkaç hisse
  python produce_light_stock.py --all            # tüm liste (600'e genelleme noktası)
"""

import json
import os
import sys
import datetime
import traceback

try:
    import yfinance as yf
except ImportError:
    sys.exit("yfinance yüklü değil. Çalıştır: pip install yfinance")

# 6 Tem 2026 — veri politikası TEK KAYNAK (app.py/fetcher/smr_core ile aynı HAM fiyat)
try:
    from data_policy import AUTO_ADJUST as _AUTO_ADJUST, drop_adj_close as _drop_adj_close
except Exception:
    _AUTO_ADJUST = False
    def _drop_adj_close(_d): return _d

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "public", "frontend_light")
OUT_DIR = os.environ.get("SMR_LIGHT_OUT", os.path.join(FRONTEND_DIR, "data"))
LATEST_JSON = os.environ.get("SMR_LIGHT_LATEST", os.path.join(FRONTEND_DIR, "latest.json"))

# Genelleme noktası — --all ile tüm liste işlenir. Şimdilik döngü kanıtı.
FULL_LIST = [
    "ASELS", "THYAO", "GARAN", "AKBNK", "ISCTR", "SAHOL", "KCHOL", "EREGL",
    "TCELL", "SISE", "TUPRS", "BIMAS", "FROTO", "TOASO", "HALKB", "VAKBN",
    "YKBNK", "ENKAI", "TKFEN", "PGSUS", "PETKM", "CCOLA", "EKGYO", "ARCLK",
    "GUBRF", "KRDMD", "SASA", "TTKOM", "TTRAK", "KOZAL", "ASTOR", "ALFAS",
]


# ── Teknik hesaplamalar (produce_stock_json.py'den, sağlam) ─────────────────────

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    return 100 - (100 / (1 + rs))


def calc_mf_smooth(df, span=10):
    # 9 Tem 2026 — TEK KAYNAK: indicators.compute_flow_momentum
    # (%50 STP eğimi + %50 kompozit puan değişimi; app/bot/infografik ile aynı).
    # `span` geriye uyumluluk için duruyor, artık kullanılmıyor.
    from indicators import compute_flow_momentum
    mf, _stp = compute_flow_momentum(df)
    if mf is None:
        return df['Close'] * 0.0   # hata: nötr (sıfır) barlar
    return mf


def calc_stp(df, span=6):
    tp = (df['High'] + df['Low'] + df['Close']) / 3.0
    return tp.ewm(span=span, adjust=False).mean()


def get_regime(close, sma50, sma200, rsi):
    if close > sma200 and close > sma50 and rsi > 55:
        return "Boğa Trendi", "green"
    elif close > sma200 and rsi >= 50:
        return "Hafif Yükseliş", "green"
    elif close < sma200 and close < sma50 and rsi < 45:
        return "Düşüş Trendi", "red"
    elif close < sma200:
        return "Zayıf", "red"
    else:
        return "Nötr", "orange"


def calc_genel_skor(close, sma50, sma200, rsi, degisim_pct, pozisyon_pct):
    skor = 0
    if close > sma200:    skor += 25
    if close > sma50:     skor += 20
    if rsi > 50:          skor += 15
    if rsi > 60:          skor += 10
    if degisim_pct > 0:   skor += 10
    if pozisyon_pct > 50: skor += 10
    if pozisyon_pct > 75: skor += 10
    return min(skor, 100)


def _fmt_date(idx):
    """Windows-uyumlu '22 May' formatı (%-d yok)."""
    try:
        return f"{idx.day} {idx.strftime('%b')}"
    except Exception:
        return str(idx)[:10]


# ── Tek hisse işleme ────────────────────────────────────────────────────────────

def process_ticker(ticker):
    yf_ticker = ticker.replace(".IS", "") + ".IS"
    df = yf.download(yf_ticker, period="1y", interval="1d",
                     progress=False, auto_adjust=_AUTO_ADJUST)   # 6 Tem 2026 — HAM (data_policy)
    df = _drop_adj_close(df)

    if df is None or len(df) < 30:
        print(f"  {ticker}: yetersiz veri ({0 if df is None else len(df)} bar)")
        return None

    if hasattr(df.columns, 'levels') and len(df.columns.levels) > 1:
        df.columns = df.columns.droplevel(1)

    df = df.dropna(subset=['Close', 'High', 'Low', 'Volume'])
    if len(df) < 30:
        print(f"  {ticker}: NaN temizleme sonrası yetersiz ({len(df)} bar)")
        return None

    closes, highs, lows = df['Close'], df['High'], df['Low']

    kapanis = float(closes.iloc[-1])
    prev = float(closes.iloc[-2])
    degisim_pct = round((kapanis - prev) / prev * 100, 2)

    sma50 = float(closes.rolling(50, min_periods=30).mean().iloc[-1])
    sma200 = float(closes.rolling(200, min_periods=100).mean().iloc[-1])
    rsi = float(calc_rsi(closes).iloc[-1])

    yil_yuksek = float(highs.iloc[-252:].max())
    yil_dusuk = float(lows.iloc[-252:].min())
    rng = yil_yuksek - yil_dusuk
    pozisyon = round((kapanis - yil_dusuk) / (rng + 1e-9) * 100, 1)

    rejim, rejim_renk = get_regime(kapanis, sma50, sma200, rsi)
    genel_skor = calc_genel_skor(kapanis, sma50, sma200, rsi, degisim_pct, pozisyon)

    # Grafik — son 30 iş günü (app.js xu100_grafik formatı)
    df30 = df.iloc[-30:]
    mf_ser = calc_mf_smooth(df)
    stp_ser = calc_stp(df)

    grafik = []
    for idx, row in df30.iterrows():
        mf_val = float(mf_ser.reindex([idx]).iloc[0]) if idx in mf_ser.index else 0.0
        stp_val = float(stp_ser.reindex([idx]).iloc[0]) if idx in stp_ser.index else kapanis
        price_v = float(row['Close'])
        if any(v != v for v in [mf_val, stp_val, price_v]):  # NaN
            continue
        grafik.append({"date": _fmt_date(idx), "mf": round(mf_val, 4),
                       "stp": round(stp_val, 4), "price": round(price_v, 2)})

    if not grafik:
        print(f"  {ticker}: grafik üretilemedi")
        return None

    # app.js'in beklediği "xu100" bloğu — ama bu hissenin verisi
    xu100_block = {
        "ticker":        ticker,
        "kapanis":       round(kapanis, 2),
        "degisim_pct":   degisim_pct,
        "sma50":         round(sma50, 2),
        "sma200":        round(sma200, 2),
        "rsi":           round(rsi, 1),
        "rejim":         rejim,
        "rejim_renk":    rejim_renk,
        "yillik_yuksek": round(yil_yuksek, 2),
        "yillik_dusuk":  round(yil_dusuk, 2),
        "pozisyon_pct":  pozisyon,
        "genel_skor":    genel_skor,
    }
    return xu100_block, grafik


def load_market_context():
    """piyasa_ozeti + meta'yı mevcut latest.json'dan taşı (piyasa-geneli, ortak)."""
    try:
        with open(LATEST_JSON, "r", encoding="utf-8") as f:
            latest = json.load(f)
        return latest.get("piyasa_ozeti", {}), latest.get("kilitli", {})
    except Exception:
        return {}, {}


def main():
    args = [a for a in sys.argv[1:] if a]
    if not args:
        print("Kullanım: python produce_light_stock.py ASTOR [THYAO ...] | --all")
        return
    tickers = FULL_LIST if args[0] == "--all" else [a.upper() for a in args]

    os.makedirs(OUT_DIR, exist_ok=True)
    piyasa_ozeti, kilitli = load_market_context()
    now_tr = datetime.datetime.utcnow() + datetime.timedelta(hours=3)

    ok = 0
    print(f"[produce_light_stock] {len(tickers)} hisse → {OUT_DIR}")
    for ticker in tickers:
        try:
            res = process_ticker(ticker)
        except Exception:
            traceback.print_exc()
            res = None
        if not res:
            print(f"   ✗ {ticker}")
            continue
        xu100_block, grafik = res
        payload = {
            "meta": {
                "tarih":      now_tr.strftime("%Y-%m-%d"),
                "guncelleme": now_tr.strftime("%H:%M"),
                "versiyon":   "light-1.0",
                "kaynak":     "Önceki kapanış verisi · Eğitim / veri analitiği amaçlıdır",
            },
            "xu100":        xu100_block,
            "xu100_grafik": grafik,
            "piyasa_ozeti": piyasa_ozeti,
            "kilitli":      kilitli,
        }
        out_path = os.path.join(OUT_DIR, f"{ticker}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"   ✓ {ticker}  fiyat={xu100_block['kapanis']} rsi={xu100_block['rsi']} → {ticker}.json")
        ok += 1

    print(f"[produce_light_stock] Tamamlandı: {ok}/{len(tickers)}")


if __name__ == "__main__":
    main()
