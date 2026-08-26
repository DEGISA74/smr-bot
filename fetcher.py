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

import os
import re
import sys
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from data_policy import AUTO_ADJUST  # 3 Tem 2026 — veri politikası TEK KAYNAK (app.py ile aynı)
from bist_data_store import promote_batch, read_active
from isyatirim_gateway import robust_isyatirim

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
KAPANIS_END      = (18, 40)    # kesinleşen kapanış için İstanbul saatiyle son tur başlangıcı
KAPANIS_INTERVAL = 300         # 5 dk
ISTANBUL_TZ      = ZoneInfo("Europe/Istanbul")

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
        from provider_traffic import acquire_slot, record_success, record_failure
        acquire_slot("yahoo", priority="regular_fetch", max_wait=120.0)
        t  = yf.Ticker(symbol)
        df = t.history(period=f"{period_days}d", auto_adjust=AUTO_ADJUST)
        if df is None or df.empty:
            record_failure("yahoo", kind="empty", error="empty")
            return None
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        df.index.name = 'Date'
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        record_success("yahoo")
        return df if len(df) > 0 else None
    except Exception as e:
        try:
            from provider_traffic import record_failure
            record_failure("yahoo", kind="error", error=str(e))
        except Exception:
            pass
        log.debug(f"[yf]  {symbol}: {e}")
        return None


def fetch_isyatirim(symbol: str, period_days: int = PERIOD_DAYS):
    """Sadece .IS hisseleri için ortak bütçeli İş Yatırım hacim adayı."""
    if not symbol.endswith('.IS') or symbol.startswith('X'):
        return None
    try:
        df_out, source = robust_isyatirim(
            symbol, period_days=period_days, tries=1, allow_stale=False,
            priority="regular_fetch", max_wait=120.0)
        return df_out if source == "canli" and df_out is not None else None
    except Exception as ex:
        log.debug(f"[isy] {symbol}: {ex}")
        return None


def fetch_borsapy(symbol: str, period_days: int = PERIOD_DAYS):
    """borsapy (İş Yatırım tabanlı) — sadece .IS hisseleri, endeks yfinance'a düşer."""
    if not symbol.endswith('.IS') or symbol.startswith('X'):
        return None
    try:
        from provider_traffic import acquire_slot, record_success, record_failure
        acquire_slot("borsapy", priority="repair", max_wait=120)
        import borsapy
        period = '1mo' if period_days <= 31 else '1y'
        df = borsapy.Ticker(symbol.replace('.IS', '')).history(period=period)
        if df is None or df.empty:
            record_failure("borsapy", kind="empty", error=f"{symbol} empty")
            return None
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        df.index.name = 'Date'
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df.index = df.index.normalize()  # 09:00 damgasını güne indir (yf/isy ile hizalı)
        df = df[df['Close'] > 0].dropna()
        record_success("borsapy")
        return df if not df.empty else None
    except Exception as e:
        try:
            record_failure("borsapy", kind="error", error=str(e))
        except Exception:
            pass
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
    """Sağlayıcıdan veri çeker; ana dosyaya değil, sürüm kapısına aday üretir."""
    is_index = symbol.startswith('X')
    use_src = "yfinance" if (source != "yfinance" and is_index) else source
    fetcher = FETCHERS[use_src]
    target = VERILER / f"{symbol}_1d.parquet"
    days = PERIOD_DAYS
    if target.exists():
        try:
            if len(pd.read_parquet(target, columns=['Close'])) >= 200:
                days = INCREMENTAL_DAYS
        except Exception:
            pass
    df = fetcher(symbol, period_days=days)
    if df is None or df.empty:
        return symbol, 'fail', 0, use_src, None
    try:
        df = df[~df.index.duplicated(keep='last')].sort_index()
        if use_src == "yfinance":
            # Yahoo yalnız fiyatın patronudur. Bugünkü Yahoo hacmi sadece İş
            # Yatırım gelene kadar geçici katmanda kullanılabilir.
            spec = {
                "price_df": df[['Open', 'High', 'Low', 'Close']].copy(),
                "price_source": "yahoo",
            }
            if 'Volume' in df.columns:
                spec["volume_df"] = df[['Volume']].tail(1).copy()
                spec["volume_source"] = "yahoo_provisional"
        elif use_src == "isyatirim":
            # İş Yatırım fiyat alanlarına dokunamaz; yalnız hacim teklif eder.
            spec = {"volume_df": df[['Volume']].copy(),
                    "volume_source": "isyatirim"}
        else:
            # borsapy normal tur kaynağı değildir; yalnız açıkça kanıtlı gapfill
            # ve kapanış referansı süreçleri kullanır.
            return symbol, 'fail', 0, use_src, None
        return symbol, 'ok', len(df), use_src, spec
    except Exception as exc:
        log.warning(f"[candidate] {symbol}: {exc}")
        return symbol, 'write_fail', 0, use_src, None


# --------------------------------------------------------------------
# ENDEKS TL CİRO OVERRIDE (18 Tem 2026)
# Yahoo/borsapy endeks "Volume"u ADETTİR (XU100 ~7,5 mlr lot), TL ciro DEĞİL.
# İş Yatırım endeks endpoint'i hiç hacim vermiyor. Çözüm: endeks bileşenlerinin
# TL cirosunu (Σ Volume×Close, parquet'ten) toplayıp endeks parquet'inin Volume
# kolonunu bununla EZ. Böylece aşağıdaki tüm tüketiciler (OBV/CMF/paneller)
# otomatik gerçek TL ciroyu kullanır. Fetch turu SONUNDA çalışır (idempotent:
# her tur Yahoo adedini çeker, sonra biz TL ciroyla üzerine yazarız).
# --------------------------------------------------------------------
# Endeks TL ciro çekirdeği data_layer'da (TEK KAYNAK — app da oradan kullanır, drift yok).
# fetcher burada parquet'e YAZAR (at-rest); app apply_volume_projection'da canlı uygular.
from data_layer import (INDEX_CIRO_TARGETS, compute_index_tl_ciro_series,
                        load_index_components)


def override_index_ciro():
    """Hedef endekslerin parquet Volume kolonunu bileşen-toplamı TL ciroyla EZER.
    Fetch turu sonunda çağrılır (bileşenler bu turda tazelendi). borsapy'ye izinli
    (haftalık bileşen listesi tazelemesi fetcher'ın işi)."""
    candidates = {}
    for ix in INDEX_CIRO_TARGETS:
        try:
            # Memo'yu atla: fetcher taze parquet'ten yeniden hesaplasın
            from data_layer import _index_ciro_memo
            _index_ciro_memo.pop(ix, None)
            load_index_components(ix, allow_network=True)   # haftalık cache tazele
            ser = compute_index_tl_ciro_series(ix, allow_network=True)
            if ser is None or ser.empty:
                log.warning(f"[ciro] {ix}: hesaplanamadı (bileşen/parquet eksik)")
                continue
            target = VERILER / f"{ix}.IS_1d.parquet"
            if not target.exists():
                log.warning(f"[ciro] {ix} parquet yok, override atlandı")
                continue
            idx_df = pd.read_parquet(target)
            _ort = idx_df.index.intersection(ser.index)
            if len(_ort) == 0:
                log.warning(f"[ciro] {ix} tarih kesişimi boş, override atlandı")
                continue
            vol = pd.DataFrame({'Volume': ser.reindex(_ort).astype(float)}, index=_ort).tail(4)
            candidates[f"{ix}.IS"] = {
                "volume_df": vol,
                "volume_source": "index_ciro",
                "evidence": {"component_sum": True},
            }
            log.info(f"[ciro] {ix}: son gün TL ciro {float(ser.reindex(_ort).iloc[-1]):,.0f} "
                     f"(~{ser.reindex(_ort).iloc[-1]/1e9:.0f} mlr) sürüm adayı hazır")
        except Exception as e:
            log.warning(f"[ciro] {ix} override hatası: {e}")
    if candidates:
        result = promote_batch(candidates, reason="index_component_turnover",
                               source_run={"source": "component_parquets"},
                               max_reject_ratio=0.50)
        if not result.get("ok"):
            log.warning("[ciro] endeks ciro sürümü reddedildi; önceki sağlam veri korundu")


# --------------------------------------------------------------------
# Ana akış
# --------------------------------------------------------------------
def run(source_override: str | None = None, candidate_filter=None):
    source  = source_override or get_next_source()
    tickers = load_bist_tickers()
    log.info(f"=== FETCHER START ===  Kaynak: {source}  |  {len(tickers)} ticker")

    start   = time.time()
    results = {'ok': 0, 'fail': 0, 'write_fail': 0, 'merge_skip': 0, 'regress_skip': 0, 'rows': 0}
    failed  = []
    candidates = {}
    src_breakdown = {'yfinance': 0, 'isyatirim': 0}

    workers = MAX_WORKERS_ISYATIRIM if source == "isyatirim" else MAX_WORKERS_YFINANCE
    log.info(f"  Worker sayısı: {workers}")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process_one, t, source): t for t in tickers}
        for i, f in enumerate(as_completed(futs), 1):
            sym, status, rows, used_src, spec = f.result()
            results[status] = results.get(status, 0) + 1
            src_breakdown[used_src] = src_breakdown.get(used_src, 0) + 1
            if status == 'ok':
                results['rows'] += rows
                candidates[sym] = spec
            else:
                failed.append(sym)
                # Kaynak genel olarak çöktüyse sürüm kapısı oranı görüp turu durdursun.
                candidates[sym] = {}
            if i % 100 == 0:
                log.info(f"  Progress: {i}/{len(tickers)}  ok={results['ok']} fail={results['fail']}")

    dur = time.time() - start
    # Süzgeç kancası: adaylar sürüm kapısına GİTMEDEN önce son bir denetim.
    # Şu an yalnız kapanis_final kullanıyor (borsapy konsensüs kapısı).
    if candidate_filter is not None:
        try:
            candidates = candidate_filter(candidates)
        except Exception as _exc:
            log.warning(f"[konsensus] süzgeç hatası, adaylar dokunulmadan geçti: {_exc}")
    promotion = promote_batch(
        candidates, reason=f"fetcher_{source}",
        source_run={"source": source, "total": len(tickers),
                    "fetch_ok": results['ok'], "fetch_fail": results['fail'],
                    "duration_sec": round(dur, 1)},
        max_reject_ratio=0.20)
    if promotion.get("ok"):
        save_source(source)
        log.info(f"  Sürüm         : {promotion.get('version_id')} · değişen={promotion.get('changed')}")
    else:
        log.error(f"  SÜRÜM REDDEDİLDİ — aktif veri korundu: {promotion.get('active_version')}")

    # ENDEKS TL CİRO OVERRIDE (18 Tem 2026) — bileşenler bu turda tazelendi,
    # şimdi endeks parquet'inin adet-Volume'ünü TL ciroyla ez (idempotent).
    override_index_ciro()

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
        'promotion_ok': bool(promotion.get('ok')),
        'version_id': promotion.get('version_id'),
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
    candidates = {}
    t0 = time.time()
    ex = ThreadPoolExecutor(max_workers=workers)
    try:
        futs = [ex.submit(process_one, t, source) for t in tickers]
        for f in as_completed(futs, timeout=max(1.0, deadline - time.time())):
            sym, status, _, _, spec = f.result()
            done += 1
            ok += (status == 'ok')
            candidates[sym] = spec if status == 'ok' else {}
            if time.time() >= deadline:
                break
    except TimeoutError:
        pass
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    if candidates:
        result = promote_batch(candidates, reason=f"closing_{source}",
                               source_run={"source": source, "timeboxed": True,
                                           "processed": done, "universe": len(tickers)},
                               max_reject_ratio=0.25)
        if not result.get("ok"):
            log.warning(f"  [KAPANIS] {source} adayları reddedildi; aktif sürüm korundu")
    log.info(f"  [KAPANIS] {source:9s} tur bitti: {done}/{len(tickers)} işlendi, ok={ok} ({time.time()-t0:.0f}sn)")
    return done, ok


def run_yahoo_refresh():
    """Gün içi Yahoo turu: sonraki 10 dakikalık cron turuna taşmadan yayın yapar."""
    tickers = load_bist_tickers()
    # Cron her 10 dakikada bir başlar. Sonraki turun kilide takılmaması için
    # mevcut tura 9 dakika ver; yetişmeyen semboller sonraki turda en başa gelir.
    deadline = time.time() + 9 * 60
    log.info(f"=== YAHOO GÜN İÇİ TUR === | {len(tickers)} ticker | süre sınırı 9 dk")
    _round_timeboxed('yfinance', tickers, deadline)


def _build_hot_list(universe: list[str]) -> list[str]:
    """ACİL LİSTE (6 Ağu 2026): her turda çekilecek küçük öncelik kümesi —
    endeksler + BIST30 (priority_bist_indices) + kullanıcı favorileri (patron.db
    watchlist). Yalnız BIST evreninde olanlar; GC=F / SPCX gibi BIST-dışı favoriler
    elenir. Sıra korunur (endeks/BIST30 önce, favoriler sonra), tekrar yok."""
    uni = set(universe)
    hot: list[str] = []
    try:
        from data_layer import priority_bist_indices
        hot = [t for t in priority_bist_indices if t in uni]
    except Exception as e:
        log.debug(f"[acil] priority_bist_indices okunamadi: {e}")
    try:
        from db_layer import load_watchlist_db
        for f in (load_watchlist_db() or []):
            if (f.endswith('.IS') or f.startswith('X')) and f in uni and f not in hot:
                hot.append(f)
    except Exception as e:
        log.debug(f"[acil] favoriler okunamadi: {e}")
    return hot


def run_acil_liste(cold_chunk: int = 60):
    """2 KATMANLI GÜN İÇİ TUR (6 Ağu 2026) — Yahoo throttle'ını önlemek için tüm
    615'i HER TURDA değil, KÜÇÜK + YAYILI çek:
      • ACİL (her tur): endeks + BIST30 + favoriler → izlenen hisseler hep taze.
      • EVREN (dönüşümlü): en bayat `cold_chunk` hisse → evren ~50 dk'da tam döner.
    Yük ≈ (acil+dilim)/tur ≈ 100 istek / 5 dk = ~20/dk (throttle bölgesinden uzak).
    Cron: */5 seans içi. Ölçülen kanıt: 615'i tek seferde istemek Yahoo'yu kısıyordu."""
    universe = load_bist_tickers()
    hot = _build_hot_list(universe)
    hot_set = set(hot)
    cold = [t for t in universe if t not in hot_set]
    cold_stale = _stalest_first(cold)[:max(0, cold_chunk)]
    batch = hot + cold_stale
    deadline = time.time() + 4 * 60   # küçük parti; süre kutusu geniş, taşma beklenmez
    log.info(f"=== ACİL LİSTE TURU === | acil={len(hot)} + evren_dilimi={len(cold_stale)}"
             f" = {len(batch)} ticker (evren={len(universe)}, chunk={cold_chunk})")
    _round_timeboxed('yfinance', batch, deadline)


def run_kapanis():
    """18:15'te Task Scheduler başlatır. 18:45'e kadar her 5 dk işaretinde bir tur,
    kaynak sırası yfinance → isyatirim → borsapy. 18:35 slotu SMR_Finalize_Volume'a
    bırakılır (İsyatirim hacim kesinleştirme ile yazma yarışı olmasın).
    Pencere dışında elle çalıştırılırsa test modu: 3 kaynak art arda 1'er tur."""
    from itertools import cycle
    tickers = load_bist_tickers()
    # Bu pencerede fiyat güncelliği önceliklidir: her 5 dakikada Yahoo fiyat
    # turu. İş Yatırım hacim kesinleştirmesi ayrı kapanış işinde kalır.
    sources = cycle(['yfinance'])
    now = datetime.now(ISTANBUL_TZ)
    end = now.replace(hour=KAPANIS_END[0], minute=KAPANIS_END[1], second=0, microsecond=0)

    if now > end:
        log.info(f"=== KAPANIS TEST MODU === (pencere dışı, 3 kaynak art arda) | {len(tickers)} ticker")
        for _ in range(3):
            _round_timeboxed(next(sources), tickers, time.time() + KAPANIS_INTERVAL)
        log.info("=== KAPANIS TEST DONE ===")
        return

    log.info(f"=== KAPANIS PENCERESİ === {now:%H:%M} → {end:%H:%M} | {len(tickers)} ticker")
    while True:
        now = datetime.now(ISTANBUL_TZ)
        if now > end + timedelta(minutes=2):   # son tur dahil (saniye kayması pencereyi yemesin)
            break
        # Bu turun süre kutusu: bir sonraki 5 dk duvar-saati işareti
        next_mark = (int(time.time()) // KAPANIS_INTERVAL + 1) * KAPANIS_INTERVAL
        _round_timeboxed(next(sources), tickers, float(next_mark))
        # Sonraki işarete kadar bekle
        wait = next_mark - time.time()
        if wait > 0:
            time.sleep(wait)
    log.info("=== KAPANIS PENCERESİ DONE ===")


# ── borsapy KONSENSÜS KAPISI — kapanis_final turu (26 Ağu 2026) ──────────────
# Ölçüldü (settle_report, 21 gün): borsapy ile İş Yatırım'ın NİHAİ kapanışı 73/73
# (%100) aynı; borsapy 71 kez ERKEN oturuyor, HİÇ geç kalmıyor. Kapanış ise medyan
# 18:36'da, %95 gün 19:49'da, en kötü gün 20:58'de oturuyor — bu tur 19:05'te
# koştuğu için Yahoo'nun son barı PROVİZYON olabilir ve depodaki satırı ezerdi.
#
# Kural — YALNIZ EZME VETO EDİLİR, yeni gün engellenmez:
#   depoda o gün YOKSA           → dokunma (bar olmaması provizyon bardan kötü;
#                                  21:30/22:15 turları ve sabah incremental düzeltir)
#   depodaki kapanış AYNI ise    → ezme yok, borsapy'ye SORULMAZ (asıl tasarruf)
#   FARKLI + borsapy doğruluyor  → aday kalır
#   FARKLI + borsapy çelişiyor   → yalnız SON BAR düşer, önceki günler kalır
#   borsapy veri vermiyor/hata   → eski davranış (Yahoo'ya güven)
#
# ⚠ price_source etiketi DEĞİŞTİRİLMEZ: bist_data_store.PRICE_SOURCES bir BEYAZ
# LİSTE; listede olmayan ad adayı BÜTÜNÜYLE reddettirir. Konsensüs log'a yazılır.
# ⚠ Sembol aday sözlüğünden SİLİNMEZ / spec boş bırakılmaz: promote_batch boş
# spec'i fail sayıp max_reject_ratio'yu tetikleyebilir. Veto satır siler, sembol değil.
# Kapatma: SMR_KAPANIS_KONSENSUS=0
KAPANIS_KONSENSUS = os.environ.get("SMR_KAPANIS_KONSENSUS", "1") != "0"
KONSENSUS_TOLERANS = 0.0015        # %0,15 — ölçümde birebir uyuyorlar, pay yuvarlama için
KONSENSUS_SURE_BUTCESI = 900.0     # sn (5 thread × ~0,6 sn/sembol → geniş pay)


def _borsapy_kapanis(symbol: str, gun):
    """Verilen günün borsapy kapanışı (yoksa None). borsapy WebSocket'i arada bir
    boş dönüyor → tek tekrar. Tekrar da tutmazsa çağıran eski davranışa düşer."""
    for _k in range(2):
        try:
            from provider_traffic import acquire_slot, record_success, record_failure
            acquire_slot("borsapy", priority="post_close", max_wait=30)
            import borsapy as _bp
            h = _bp.Ticker(symbol.replace('.IS', '')).history(period="3d")
            if h is not None and not h.empty and "Close" in h.columns:
                idx = pd.DatetimeIndex([
                    pd.Timestamp(t).tz_localize(None).normalize()
                    if pd.Timestamp(t).tz else pd.Timestamp(t).normalize()
                    for t in h.index])
                h = h.set_axis(idx)
                if gun in h.index:
                    record_success("borsapy")
                    return float(h.at[gun, "Close"])
            record_failure("borsapy", kind="empty", error=f"{symbol} empty")
        except Exception:
            pass
        if _k == 0:
            time.sleep(0.6)
    return None


def _kapanis_konsensus_suz(candidates: dict) -> dict:
    """Adayları borsapy ile teyit eder; teyit edilemeyen EZME satırını düşürür."""
    if not KAPANIS_KONSENSUS or not candidates:
        return candidates

    sorulacak = []                      # (sym, gun, yahoo_close)
    for sym, spec in candidates.items():
        pdf = (spec or {}).get("price_df")
        if pdf is None or getattr(pdf, "empty", True):
            continue
        if sym.startswith('X'):
            continue                    # endeks fiyatı borsapy referansı değil
        try:
            gun = pd.Timestamp(pdf.index[-1]).normalize()
            yc = float(pdf["Close"].iloc[-1])
            old = read_active(sym)
            if old is None or old.empty or gun not in old.index:
                continue                # yeni gün → veto yok
            oc = float(old.at[gun, "Close"])
            if abs(oc - yc) <= max(1e-8, abs(oc) * 1e-6):
                continue                # depodakiyle aynı → ezme yok, sorma
            sorulacak.append((sym, gun, yc))
        except Exception:
            continue

    if not sorulacak:
        log.info("[konsensus] ezilecek kapanış yok — borsapy'ye sorulmadı")
        return candidates

    baslangic = time.time()
    teyitli = veto = sorulmayan = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_BORSAPY) as ex:
        futs = {ex.submit(_borsapy_kapanis, sym, gun): (sym, gun, yc)
                for sym, gun, yc in sorulacak}
        for f in as_completed(futs):
            sym, gun, yc = futs[f]
            if time.time() - baslangic > KONSENSUS_SURE_BUTCESI:
                sorulmayan += 1
                continue
            try:
                bp = f.result()
            except Exception:
                bp = None
            if bp is None:
                sorulmayan += 1
                continue
            if abs(bp - yc) <= max(0.005, abs(bp) * KONSENSUS_TOLERANS):
                teyitli += 1
                continue
            try:                        # ÇELİŞKİ → yalnız son bar düşer
                kirpik = candidates[sym]["price_df"].iloc[:-1]
                if kirpik.empty:
                    continue            # tek satırlık aday → dokunma
                candidates[sym]["price_df"] = kirpik
                veto += 1
            except Exception:
                pass

    log.info(f"[konsensus] ezilecek {len(sorulacak)} kapanış -> borsapy teyitli {teyitli}"
             f" · veto {veto} (son bar düşürüldü) · sorulmayan {sorulmayan}"
             f" · {time.time() - baslangic:.0f}sn")
    return candidates


def run_kapanis_final():
    """KAPANIŞ SONRASI KESİN FİYAT TURU (18 Ağu 2026).

    Neden: `kapanis` penceresi 17:40'ta başlıyor, tam tur ~12 dk sürüyor → tur
    borsa kapanmadan (18:10 seans sonu) bitiyordu. 18 Ağu'da 192 hissenin
    "kapanışı" gün-içi fiyat olarak kalmıştı ve kimse fark etmedi.
    Bu tur pencere kapandıktan SONRA tek sefer tüm evreni süre kutusuz çeker;
    Yahoo günlük barı o saatte kesinleşmiş olur."""
    log.info("=== KAPANIS FINAL (kesin fiyat turu) ===")
    run('yfinance', candidate_filter=_kapanis_konsensus_suz)


if __name__ == "__main__":
    if 'kapanis_final' in sys.argv[1:]:
        run_kapanis_final()
    elif 'kapanis' in sys.argv[1:]:
        run_kapanis()
    elif 'isyatirim' in sys.argv[1:]:
        run("isyatirim")
    elif 'acil' in sys.argv[1:]:
        run_acil_liste()
    elif 'yahoo' in sys.argv[1:]:
        run_yahoo_refresh()
    else:
        run()
