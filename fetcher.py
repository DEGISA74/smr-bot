"""
fetcher.py — BIST verisi dönüşümlü kaynaktan çekip parquet'e yazar.

Çalışma mantığı:
  - Her çalıştırmada bir kaynak seçilir: yfinance ↔ isyatirim (.last_source ile takip)
  - Tüm BIST ticker'ları paralel olarak çekilir (ThreadPoolExecutor)
  - Başarılı çekme:    veriler/SYMBOL_1d.parquet üzerine atomic yazar
  - Başarısız çekme:   eski parquet KORUNUR (dokunulmaz)
  - Endeksler (X*):    sadece yfinance kullanır (isyatirim endeks vermiyor)

Production: cron / systemd ile 10dk'da bir tetiklenir.
"""

from __future__ import annotations

import re
import sys
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf

# --------------------------------------------------------------------
# Paths & config
# --------------------------------------------------------------------
ROOT       = Path(__file__).parent
VERILER    = ROOT / "veriler"
LOGS_DIR   = ROOT / "logs"
VERILER.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
STATE_FILE   = VERILER / ".last_source"
LOG_FILE     = LOGS_DIR / "fetcher.log"
HISTORY_FILE = LOGS_DIR / "fetcher_history.jsonl"

PERIOD_DAYS = 365              # 1 yıl
MAX_WORKERS_YFINANCE  = 5      # yfinance thread-safe
MAX_WORKERS_ISYATIRIM = 1      # isyatirimhisse thread-safe DEĞİL (sıralı şart)

# Monitoring eşikleri
FAIL_RATE_WARN = 0.05          # %5 üstü fail → WARN
FAIL_RATE_ALERT = 0.10         # %10 üstü fail → ALERT (sistem sorunu)

# --------------------------------------------------------------------
# Logging — hem konsola hem dosyaya
# --------------------------------------------------------------------
_fmt = logging.Formatter('%(asctime)s %(levelname)-7s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
log = logging.getLogger("fetcher")
log.setLevel(logging.INFO)
# Konsol
_ch = logging.StreamHandler()
_ch.setFormatter(_fmt)
log.addHandler(_ch)
# Dosya — günlük rotasyon (basit)
_fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
_fh.setFormatter(_fmt)
log.addHandler(_fh)
# yfinance gürültüsünü sustur
logging.getLogger("yfinance").setLevel(logging.ERROR)


# --------------------------------------------------------------------
# Ticker yükleme — app.py'den parse
# --------------------------------------------------------------------
def load_bist_tickers() -> list[str]:
    """app.py içinden tüm BIST ticker'larını çıkarır."""
    app_py = ROOT / "app.py"
    content = app_py.read_text(encoding='utf-8')
    tickers = set()
    for block_name in ['raw_bist_stocks', 'priority_bist_indices']:
        m = re.search(rf'{block_name}\s*=\s*\[', content)
        if not m:
            continue
        start = m.end()
        # Bracket sayarak doğru kapanışı bul
        depth = 1
        i = start
        while depth and i < len(content):
            if content[i] == '[':   depth += 1
            elif content[i] == ']': depth -= 1
            i += 1
        block = content[start:i-1]
        tickers.update(re.findall(r'"([A-Z0-9]+\.IS)"', block))
    # Endeksler → sondaki ., normal hisseler → alfabetik
    indices = sorted(t for t in tickers if t.startswith('X'))
    stocks  = sorted(t for t in tickers if not t.startswith('X'))
    return indices + stocks


# --------------------------------------------------------------------
# Kaynak rotasyonu
# --------------------------------------------------------------------
def get_next_source() -> str:
    try:
        last = STATE_FILE.read_text().strip()
    except FileNotFoundError:
        last = "isyatirim"
    return "isyatirim" if last == "yfinance" else "yfinance"


def save_source(source: str) -> None:
    STATE_FILE.write_text(source)


# --------------------------------------------------------------------
# Fetchers
# --------------------------------------------------------------------
def fetch_yfinance(symbol: str, period_days: int = PERIOD_DAYS):
    try:
        t  = yf.Ticker(symbol)
        df = t.history(period=f"{period_days}d", auto_adjust=False)
        if df is None or df.empty:
            return None
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        df.index.name = 'Date'
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df if len(df) > 0 else None
    except Exception as e:
        log.debug(f"[yf]  {symbol}: {e}")
        return None


def fetch_isyatirim(symbol: str, period_days: int = PERIOD_DAYS):
    """Sadece .IS hisseleri için (endeks vermiyor)."""
    if not symbol.endswith('.IS') or symbol.startswith('X'):
        return None
    try:
        from isyatirimhisse import fetch_stock_data
        sym = symbol.replace('.IS', '')
        end_dt   = datetime.now()
        start_dt = end_dt - timedelta(days=period_days)
        s = start_dt.strftime("%d-%m-%Y")
        e = end_dt.strftime("%d-%m-%Y")
        df_isy = fetch_stock_data(symbols=sym, start_date=s, end_date=e)
        if df_isy is None or df_isy.empty:
            return None
        # isyatirim API'si HGDG_ACILIS dönmüyor — Open için AOF (ağırlıklı ort.) kullan
        required = {'HGDG_TARIH', 'HGDG_MAX', 'HGDG_MIN',
                    'HGDG_KAPANIS', 'HGDG_AOF', 'HGDG_HACIM'}
        if not required.issubset(df_isy.columns):
            return None
        df_isy = df_isy[df_isy['HGDG_AOF'] > 0].copy()
        idx = pd.to_datetime(df_isy['HGDG_TARIH'])
        if idx.dt.tz is not None:
            idx = idx.dt.tz_localize(None)
        # Open: HGDG_ACILIS varsa onu, yoksa AOF kullan
        if 'HGDG_ACILIS' in df_isy.columns:
            open_vals = df_isy['HGDG_ACILIS'].values
        else:
            open_vals = df_isy['HGDG_AOF'].values
        df_out = pd.DataFrame({
            'Open':   open_vals,
            'High':   df_isy['HGDG_MAX'].values,
            'Low':    df_isy['HGDG_MIN'].values,
            'Close':  df_isy['HGDG_KAPANIS'].values,
            'Volume': (df_isy['HGDG_HACIM'] / df_isy['HGDG_AOF']).values,
        }, index=idx)
        df_out.index.name = 'Date'
        df_out = df_out[df_out['Close'] > 0].dropna()
        return df_out if not df_out.empty else None
    except Exception as ex:
        log.debug(f"[isy] {symbol}: {ex}")
        return None


# --------------------------------------------------------------------
# Tek hisse işlemi (atomic write)
# --------------------------------------------------------------------
def process_one(symbol: str, source: str):
    """Çek + atomic write. Başarısızsa eski parquet'e dokunma."""
    is_index = symbol.startswith('X')
    use_src  = "yfinance" if (source == "isyatirim" and is_index) else source
    fetcher  = fetch_yfinance if use_src == "yfinance" else fetch_isyatirim
    df = fetcher(symbol)
    if df is None or df.empty:
        return symbol, 'fail', 0, use_src
    target = VERILER / f"{symbol}_1d.parquet"
    tmp    = target.with_suffix(".parquet.tmp")
    try:
        df.to_parquet(tmp, compression='snappy')
        tmp.replace(target)  # atomic rename
        return symbol, 'ok', len(df), use_src
    except Exception as e:
        log.warning(f"[write] {symbol}: {e}")
        if tmp.exists():
            try: tmp.unlink()
            except: pass
        return symbol, 'write_fail', 0, use_src


# --------------------------------------------------------------------
# Ana akış
# --------------------------------------------------------------------
def run():
    source  = get_next_source()
    tickers = load_bist_tickers()
    log.info(f"=== FETCHER START ===  Kaynak: {source}  |  {len(tickers)} ticker")

    start   = time.time()
    results = {'ok': 0, 'fail': 0, 'write_fail': 0, 'rows': 0}
    failed  = []
    src_breakdown = {'yfinance': 0, 'isyatirim': 0}

    workers = MAX_WORKERS_ISYATIRIM if source == "isyatirim" else MAX_WORKERS_YFINANCE
    log.info(f"  Worker sayısı: {workers}")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process_one, t, source): t for t in tickers}
        for i, f in enumerate(as_completed(futs), 1):
            sym, status, rows, used_src = f.result()
            results[status] += 1
            src_breakdown[used_src] = src_breakdown.get(used_src, 0) + 1
            if status == 'ok':
                results['rows'] += rows
            else:
                failed.append(sym)
            if i % 100 == 0:
                log.info(f"  Progress: {i}/{len(tickers)}  ok={results['ok']} fail={results['fail']}")

    dur = time.time() - start
    save_source(source)

    # Fail rate hesabı (delisted hisseleri hariç tutmak için %5 baseline kabul ediyoruz)
    total      = len(tickers)
    fail_rate  = results['fail'] / total if total else 0
    rate_level = "OK"
    if   fail_rate >= FAIL_RATE_ALERT: rate_level = "ALERT"
    elif fail_rate >= FAIL_RATE_WARN:  rate_level = "WARN"

    log.info(f"=== FETCHER DONE === ({dur:.1f} sn)")
    log.info(f"  Başarılı     : {results['ok']}")
    log.info(f"  Başarısız    : {results['fail']}  (rate %{fail_rate*100:.1f} → {rate_level})")
    log.info(f"  Yazma fail   : {results['write_fail']}")
    log.info(f"  Toplam bar   : {results['rows']:,}")
    log.info(f"  Kaynak dağ.  : yfinance={src_breakdown.get('yfinance',0)}, isyatirim={src_breakdown.get('isyatirim',0)}")
    if failed:
        sample = failed[:15]
        log.info(f"  Fail örnek   : {sample}{' ...' if len(failed) > 15 else ''}")

    # ALERT — gerçek sorun (delisted normalden fazla)
    if rate_level == "ALERT":
        log.error(f"⚠️  YÜKSEK FAIL ORANI: %{fail_rate*100:.1f} — kaynak '{source}' problemli olabilir!")
    elif rate_level == "WARN":
        log.warning(f"⚠️  Fail oranı normalin üstünde: %{fail_rate*100:.1f}")

    # History — her run için JSONL satırı
    history_record = {
        'ts':           datetime.now().isoformat(timespec='seconds'),
        'source':       source,
        'duration_sec': round(dur, 1),
        'total':        total,
        'ok':           results['ok'],
        'fail':         results['fail'],
        'write_fail':   results['write_fail'],
        'fail_rate':    round(fail_rate, 4),
        'rate_level':   rate_level,
        'rows':         results['rows'],
        'fail_samples': failed[:30],
    }
    try:
        with HISTORY_FILE.open('a', encoding='utf-8') as f:
            f.write(json.dumps(history_record, ensure_ascii=False) + '\n')
    except Exception as e:
        log.warning(f"History yazılamadı: {e}")

    return results, failed


if __name__ == "__main__":
    run()
