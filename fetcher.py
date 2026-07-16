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
from data_policy import AUTO_ADJUST  # 3 Tem 2026 — veri politikası TEK KAYNAK (app.py ile aynı)

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

PERIOD_DAYS = 365              # 1 yıl (SEED: parquet yok/kısaysa tam çek)
INCREMENTAL_DAYS = 15          # 1 Tem 2026 — parquet DOLUYSA sadece son ~15g çek. Merge eski
                               # geçmişi korur. Eskiden her tur 365g çekiyordu → İsyatirim turu
                               # saatlerce sürüp HİÇ bitmiyordu (9 START/1 DONE). Bu asıl hız fix'i.
MAX_WORKERS_YFINANCE  = 5      # yfinance thread-safe
MAX_WORKERS_ISYATIRIM = 1      # isyatirimhisse thread-safe DEĞİL (sıralı şart)
MAX_WORKERS_BORSAPY   = 5      # 10 Tem 2026 test: 20 sembol / 5 thread / 11.8sn, 0 hata

# KAPANIŞ PENCERESİ (10 Tem 2026) — 18:15-18:45 arası 5 dk'da bir tur,
# kaynak rotasyonu yfinance → isyatirim → borsapy. Ölçülen hızlar:
# yf tam tur ~27sn · borsapy ~6dk · isyatirim ~11dk → her tur SÜRE KUTULU
# (sonraki 5dk işaretinde kesilir), eksikler sonraki turda en-bayat-önce telafi.
KAPANIS_END      = (18, 45)    # son tur başlangıcı
KAPANIS_SKIP     = (18, 35)    # SMR_Finalize_Volume (İsyatirim hacim) slotu — çakışma yasak
KAPANIS_INTERVAL = 300         # 5 dk

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
    """Evren listelerini data_layer.py'den (yoksa app.py'den) çıkarır.
    4 Tem 2026 bölme projesi Adım 6c: raw_bist_stocks + priority_bist_indices
    app.py'den data_layer.py'ye taşındı — fetcher app.py'de bulamayınca
    '0 ticker' ile boş tur atıyordu."""
    tickers = set()
    for src_name in ["data_layer.py", "app.py"]:
        src = ROOT / src_name
        if not src.exists():
            continue
        content = src.read_text(encoding='utf-8')
        _found = _parse_ticker_blocks(content, tickers)
        if _found:
            break
    # Endeksler → sondaki ., normal hisseler → alfabetik
    indices = sorted(t for t in tickers if t.startswith('X'))
    stocks  = sorted(t for t in tickers if not t.startswith('X'))
    return indices + stocks


def _parse_ticker_blocks(content: str, tickers: set) -> bool:
    found = False
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
        new = re.findall(r'"([A-Z0-9]+\.IS)"', block)
        if new:
            found = True
            tickers.update(new)
    return found


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
        df = t.history(period=f"{period_days}d", auto_adjust=AUTO_ADJUST)
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


def fetch_borsapy(symbol: str, period_days: int = PERIOD_DAYS):
    """borsapy (İş Yatırım tabanlı) — sadece .IS hisseleri, endeks yfinance'a düşer."""
    if not symbol.endswith('.IS') or symbol.startswith('X'):
        return None
    try:
        import borsapy
        period = '1mo' if period_days <= 31 else '1y'
        df = borsapy.Ticker(symbol.replace('.IS', '')).history(period=period)
        if df is None or df.empty:
            return None
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        df.index.name = 'Date'
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df.index = df.index.normalize()  # 09:00 damgasını güne indir (yf/isy ile hizalı)
        df = df[df['Close'] > 0].dropna()
        return df if not df.empty else None
    except Exception as e:
        log.debug(f"[bp]  {symbol}: {e}")
        return None


FETCHERS = {
    'yfinance':  fetch_yfinance,
    'isyatirim': fetch_isyatirim,
    'borsapy':   fetch_borsapy,
}


# --------------------------------------------------------------------
# Tek hisse işlemi (atomic write)
# --------------------------------------------------------------------
def process_one(symbol: str, source: str):
    """Çek + atomic write. Başarısızsa eski parquet'e dokunma.
    Yeni veri her zaman MEVCUT parquet ile birleştirilir (overwrite değil) —
    iki kaynak (yfinance/isyatirim) farklı tarihlerde güncellendiği için
    biri eksik dönerse diğerinin yazdığı en taze bar asla silinmez."""
    is_index = symbol.startswith('X')
    use_src  = "yfinance" if (source != "yfinance" and is_index) else source
    fetcher  = FETCHERS[use_src]
    target = VERILER / f"{symbol}_1d.parquet"
    tmp    = target.with_suffix(".parquet.tmp")
    # INCREMENTAL: parquet zaten dolu (≥200 bar) ise sadece son ~INCREMENTAL_DAYS günü çek
    # (hızlı → tur tamamlanır). Merge aşağıda eski geçmişi korur. Yok/kısa ise tam PERIOD_DAYS.
    _days = PERIOD_DAYS
    if target.exists():
        try:
            if len(pd.read_parquet(target, columns=['Close'])) >= 200:
                _days = INCREMENTAL_DAYS
        except Exception:
            pass
    df = fetcher(symbol, period_days=_days)
    if df is None or df.empty:
        return symbol, 'fail', 0, use_src
    try:
        if target.exists():
            try:
                old = pd.read_parquet(target)
                old = old[~old.index.duplicated(keep='last')]
                df = pd.concat([old, df])
                df = df[~df.index.duplicated(keep='last')].sort_index()
                # ── HACİM KORUMASI (15 Tem 2026) ──────────────────────────────
                # Kaynak rotasyonu birbirini eziyordu: Yahoo bazı hisselerde V=0
                # döner (SASA 7-14 Tem: gerçek hacim ~4.7 milyar, Yahoo 0), aynı
                # tarihi İsyatirim/borsapy doğru verir. keep='last' sıfırı gerçek
                # hacmin üstüne yazıyordu → veri her turda (~10 dk) gidip geliyordu.
                # Bir işlem gününün hacmi sonradan 0'a dönmez; 0 = kaynak hatası.
                # Endeksler etkilenmez (eski de yeni de 0 → koşul tutmaz).
                if 'Volume' in df.columns and 'Volume' in old.columns:
                    _ort = old.index.intersection(df.index)
                    if len(_ort):
                        _yeni_bozuk = ~(pd.to_numeric(df.loc[_ort, 'Volume'], errors='coerce') > 0)
                        _eski_saglam = pd.to_numeric(old.loc[_ort, 'Volume'], errors='coerce') > 0
                        _koru = _ort[(_yeni_bozuk & _eski_saglam).values]
                        if len(_koru):
                            df.loc[_koru, 'Volume'] = old.loc[_koru, 'Volume']
                            log.warning(f"[hacim-koruma] {symbol}: {use_src} {len(_koru)} barda "
                                        f"V=0 döndü → eski gerçek hacim korundu")
            except Exception as e:
                log.warning(f"[merge] {symbol}: eski parquet okunamadı, sadece yeni veri yazılıyor: {e}")
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


# --------------------------------------------------------------------
# KAPANIŞ PENCERESİ — 18:15-18:45, 5 dk'da bir, 3 kaynak rotasyonlu
# --------------------------------------------------------------------
def _stalest_first(tickers: list[str]) -> list[str]:
    """Parquet'i en eski yazılmış (veya hiç olmayan) ticker'lar öne —
    süre kutusuna sığmayan turların açığını sonraki tur kapatır."""
    def mtime(t):
        p = VERILER / f"{t}_1d.parquet"
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0
    return sorted(tickers, key=mtime)


def _round_timeboxed(source: str, tickers: list[str], deadline: float):
    """Tek tur: deadline'a (epoch sn) kadar işleyebildiğini işler, kalanı iptal."""
    workers = {'yfinance': MAX_WORKERS_YFINANCE,
               'isyatirim': MAX_WORKERS_ISYATIRIM,
               'borsapy': MAX_WORKERS_BORSAPY}[source]
    tickers = _stalest_first(tickers)
    done = ok = 0
    t0 = time.time()
    ex = ThreadPoolExecutor(max_workers=workers)
    try:
        futs = [ex.submit(process_one, t, source) for t in tickers]
        for f in as_completed(futs, timeout=max(1.0, deadline - time.time())):
            _, status, _, _ = f.result()
            done += 1
            ok += (status == 'ok')
            if time.time() >= deadline:
                break
    except TimeoutError:
        pass
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    log.info(f"  [KAPANIS] {source:9s} tur bitti: {done}/{len(tickers)} işlendi, ok={ok} ({time.time()-t0:.0f}sn)")
    return done, ok


def run_kapanis():
    """18:15'te Task Scheduler başlatır. 18:45'e kadar her 5 dk işaretinde bir tur,
    kaynak sırası yfinance → isyatirim → borsapy. 18:35 slotu SMR_Finalize_Volume'a
    bırakılır (İsyatirim hacim kesinleştirme ile yazma yarışı olmasın).
    Pencere dışında elle çalıştırılırsa test modu: 3 kaynak art arda 1'er tur."""
    from itertools import cycle
    tickers = load_bist_tickers()
    sources = cycle(['yfinance', 'isyatirim', 'borsapy'])
    now = datetime.now()
    end = now.replace(hour=KAPANIS_END[0], minute=KAPANIS_END[1], second=0, microsecond=0)

    if now > end:
        log.info(f"=== KAPANIS TEST MODU === (pencere dışı, 3 kaynak art arda) | {len(tickers)} ticker")
        for _ in range(3):
            _round_timeboxed(next(sources), tickers, time.time() + KAPANIS_INTERVAL)
        log.info("=== KAPANIS TEST DONE ===")
        return

    log.info(f"=== KAPANIS PENCERESİ === {now:%H:%M} → {end:%H:%M} | {len(tickers)} ticker")
    while True:
        now = datetime.now()
        if now > end + timedelta(minutes=2):   # 18:45 turu dahil (saniye kayması pencereyi yemesin)
            break
        if (now.hour, now.minute) >= KAPANIS_SKIP and (now.hour, now.minute) < (KAPANIS_SKIP[0], KAPANIS_SKIP[1] + 5):
            log.info("  [KAPANIS] 18:35 slotu finalize_volume'a bırakıldı, bekleniyor")
            time.sleep(60)
            continue
        # Bu turun süre kutusu: bir sonraki 5 dk duvar-saati işareti
        next_mark = (int(time.time()) // KAPANIS_INTERVAL + 1) * KAPANIS_INTERVAL
        _round_timeboxed(next(sources), tickers, float(next_mark))
        # Sonraki işarete kadar bekle
        wait = next_mark - time.time()
        if wait > 0:
            time.sleep(wait)
    log.info("=== KAPANIS PENCERESİ DONE ===")


if __name__ == "__main__":
    if 'kapanis' in sys.argv[1:]:
        run_kapanis()
    else:
        run()
