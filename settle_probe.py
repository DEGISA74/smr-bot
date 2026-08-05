# -*- coding: utf-8 -*-
"""
settle_probe.py — BIST KAPANIŞ OTURMA ZAMANI ÖLÇÜM PROBU
=======================================================
Amaç: "İsyatirim'in resmi kapanışı gün içinde tam olarak NE ZAMAN oturuyor?" sorusunu
KANITLA cevaplamak. Her iş günü 18:05-21:00 arası likit bir sepeti 5 dk'da bir sorar,
her ölçümü CSV'ye yazar. ≥20 iş günü sonra `settle_report.py` kesin saati çıkarır.

Neden gerek: 1 Tem 2026'da fetcher 19:14'te İsyatirim'den provizyon kapanış (EREGL 40.54)
çekti, gerçek 40.44'e sonra oturdu. Fetcher İsyatirim turları HİÇ tamamlanmıyordu. Settle-pass'ı
doğru saate koymak için oturma zamanını ölçüyoruz.

Zamanlama: Windows Task Scheduler → SMR_Settle_Probe, hafta içi 18:05.
Çıktı: logs/settle_probe.csv  (date, poll_time, ticker, isy_close, yahoo_close)
"""
import sys, os, time, csv
from datetime import datetime, date, timedelta
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import pandas as pd

BASKET = ["EREGL", "KONTR", "AKBNK", "THYAO", "GARAN",
          "SISE", "ASELS", "TUPRS", "BIMAS", "KCHOL"]
LOG = os.path.join("logs", "settle_probe.csv")
POLL_EVERY = 300        # 5 dk
END_HHMM   = 2100       # 21:00'de dur (bugün 20:40'ta bile taze oturma görüldü → geniş tut)

def _isy_close(tk, ds, de, today):
    try:
        from isyatirim_gateway import robust_isyatirim
        d, source = robust_isyatirim(
            f"{tk}.IS", period_days=7, want_dates=[today], tries=1,
            allow_stale=False, priority="probe", max_wait=60)
        if d is None or d.empty or "Close" not in d.columns:
            return None
        row = d.loc[d.index.astype(str).str.slice(0, 10) == today]
        return float(row["Close"].iloc[-1]) if not row.empty else None
    except Exception:
        return None

def _yahoo_close(tk, today):
    try:
        from provider_traffic import acquire_slot, record_success, record_failure
        acquire_slot("yahoo", priority="probe", max_wait=60)
        import yfinance as yf
        h = yf.Ticker(f"{tk}.IS").history(period="2d")
        if h is None or h.empty:
            record_failure("yahoo", kind="empty", error="empty_probe")
            return None
        record_success("yahoo")
        return float(h["Close"].iloc[-1]) if str(h.index[-1])[:10] == today else None
    except Exception:
        return None

def _borsapy_close(tk, today):
    # borsapy = TradingView WebSocket; İsyatirim'e bağımsız 3. kaynak (konsensüs testi)
    try:
        from provider_traffic import acquire_slot, record_success, record_failure
        acquire_slot("borsapy", priority="probe", max_wait=60)
        import borsapy as bp
        h = bp.Ticker(tk).history(period="3d")
        if h is None or h.empty:
            record_failure("borsapy", kind="empty", error="empty_probe")
            return None
        record_success("borsapy")
        return float(h["Close"].iloc[-1]) if str(h.index[-1])[:10] == today else None
    except Exception:
        return None

def main():
    # Ölçüm hedefi dolduysa probu otomatik emekliye ayır; sağlayıcıyı sonsuza
    # kadar sorgulamaz. SMR_SETTLE_PROBE_FORCE=1 yalnız kontrollü araştırma içindir.
    if os.path.exists(LOG) and os.environ.get("SMR_SETTLE_PROBE_FORCE", "0") != "1":
        try:
            old = pd.read_csv(LOG, usecols=["date"])
            if old["date"].nunique() >= 20:
                print("settle_probe: 20 işlem günü tamamlandı — otomatik durdu")
                return
        except Exception:
            pass
    today = date.today()
    if today.weekday() >= 5:
        print("hafta sonu — probe atlandı"); return
    tstr = today.strftime("%Y-%m-%d")
    ds = (today - timedelta(days=4)).strftime("%d-%m-%Y")
    de = (today + timedelta(days=1)).strftime("%d-%m-%Y")
    os.makedirs("logs", exist_ok=True)
    newfile = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if newfile:
            w.writerow(["date", "poll_time", "ticker", "isy_close", "yahoo_close", "borsapy_close"])
        print(f"[settle_probe] {tstr} · 5dk aralık · {END_HHMM} kadar · başladı {datetime.now():%H:%M}", flush=True)
        while True:
            now = datetime.now()
            pt = now.strftime("%H:%M:%S")
            for tk in BASKET:
                ic = _isy_close(tk, ds, de, tstr)
                yc = _yahoo_close(tk, tstr)
                bc = _borsapy_close(tk, tstr)
                w.writerow([tstr, pt, tk, "" if ic is None else ic,
                            "" if yc is None else yc, "" if bc is None else bc])
            f.flush()
            print(f"  {pt} · {len(BASKET)} hisse yazıldı", flush=True)
            if now.hour * 100 + now.minute >= END_HHMM:
                break
            time.sleep(POLL_EVERY)
    print("[settle_probe] bitti", flush=True)

if __name__ == "__main__":
    main()
