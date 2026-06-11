"""
SMR — Finalize Volume v1 (standalone)

Amaç: BIST seans kapanışından (18:15) sonra İsyatirim resmi API'sinden günün
finalize hacim verisini çekip parquet cache'lerini override eder. Yahoo'nun
geç dolan/yanlış olan Volume verisini kesin doğruyla değiştirir.

Çalışma:
    Windows Task Scheduler her iş günü 18:35'te tetikler.
    finalize_volume.py → ~120 BIST ticker (BIST100 + BIST30) için
    İsyatirim'den son 5 günü çeker → mevcut parquet'lerin son barlarındaki
    Volume'ünü override eder → CACHE_DIR/.finalize_marker dosyasına bugünün
    tarihini yazar.

    app.py içindeki `_compute_volume_quality_label` bu marker'ı görünce
    "FINAL_ISYATIRIM" döner → AI prompt YAML'a kesin güven sinyali enjekte eder.

Manuel çalıştırma:
    python finalize_volume.py
    veya:    run_finalize_volume.bat (Task Scheduler bunu çağırır)

Çıktı:
    logs/finalize_volume.log
"""

import sys
import os
import io
import time
from pathlib import Path
from datetime import datetime, timedelta
import logging

# UTF-8 zorla (Windows cp1254 emoji uyumsuzluğu)
try:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import pandas as pd

BASE_DIR    = Path(__file__).parent
CACHE_DIR   = Path(os.environ.get("SMR_CACHE_DIR", BASE_DIR / "veriler"))
LOG_DIR     = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
MARKER_FILE = CACHE_DIR / ".finalize_marker"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "finalize_volume.log", encoding='utf-8'),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ─── BIST30 + BIST100 LİSTESİ ────────────────────────────────────────────────
# Manuel bakım — BIST endeks bileşimi değiştiğinde güncellenir. Liste fazla
# olsa sorun değil; eksik olursa o hisse "FINAL_ISYATIRIM" rozetini almaz.

BIST30 = [
    "AKBNK.IS", "ARCLK.IS", "ASELS.IS", "ASTOR.IS", "BIMAS.IS", "DOAS.IS",
    "EKGYO.IS", "ENKAI.IS", "EREGL.IS", "FROTO.IS", "GARAN.IS", "GUBRF.IS",
    "HEKTS.IS", "ISCTR.IS", "KCHOL.IS", "KOZAA.IS", "KOZAL.IS", "KRDMD.IS",
    "ODAS.IS", "PETKM.IS", "PGSUS.IS", "SAHOL.IS", "SASA.IS", "SISE.IS",
    "TAVHL.IS", "TCELL.IS", "THYAO.IS", "TOASO.IS", "TUPRS.IS", "YKBNK.IS",
]

# BIST100 ek hisseler (BIST30 hariç) — 2025 sonu yaklaşık bileşim
BIST100_EXTRA = [
    "AEFES.IS", "AGHOL.IS", "AGROT.IS", "AHGAZ.IS", "AKCNS.IS", "AKFGY.IS",
    "AKFYE.IS", "AKSA.IS", "AKSEN.IS", "ALARK.IS", "ALBRK.IS", "ALFAS.IS",
    "ANSGR.IS", "ARDYZ.IS", "BERA.IS", "BIENY.IS", "BRSAN.IS", "BRYAT.IS",
    "BTCIM.IS", "CANTE.IS", "CCOLA.IS", "CIMSA.IS", "CLEBI.IS", "CWENE.IS",
    "DOHOL.IS", "ECILC.IS", "ECZYT.IS", "EGEEN.IS", "ENERY.IS", "ENJSA.IS",
    "EUPWR.IS", "EUREN.IS", "GESAN.IS", "GLYHO.IS", "GOLTS.IS", "GSDHO.IS",
    "GUBRE.IS", "HALKB.IS", "ISMEN.IS", "IZENR.IS", "IZMDC.IS", "KARSN.IS",
    "KAYSE.IS", "KCAER.IS", "KLSER.IS", "KMPUR.IS", "KONTR.IS", "KONYA.IS",
    "KORDS.IS", "KOTON.IS", "KZBGY.IS", "LMKDC.IS", "MAVI.IS", "MGROS.IS",
    "MIATK.IS", "MPARK.IS", "OBASE.IS", "OBAMS.IS", "ONCSM.IS", "OTKAR.IS",
    "OYAKC.IS", "PENTA.IS", "QUAGR.IS", "REEDR.IS", "SDTTR.IS", "SELEC.IS",
    "SKBNK.IS", "SMRTG.IS", "SOKM.IS", "TABGD.IS", "TKFEN.IS", "TKNSA.IS",
    "TMSN.IS", "TSKB.IS", "TTKOM.IS", "TTRAK.IS", "TUKAS.IS", "ULKER.IS",
    "VAKBN.IS", "VESBE.IS", "VESTL.IS", "YEOTK.IS", "ZOREN.IS",
]

TICKERS = sorted(set(BIST30 + BIST100_EXTRA))


def fetch_isyatirim_volumes(ticker, days_back=5):
    """İsyatirim API'sinden son N günü çek. Volume = HGDG_HACIM / HGDG_AOF."""
    try:
        from isyatirimhisse import fetch_stock_data
    except ImportError:
        log.error("isyatirimhisse paketi kurulu değil: pip install isyatirimhisse")
        return None
    try:
        _sym = ticker.replace(".IS", "").replace(".is", "").upper()
        _end = datetime.now()
        _start = _end - timedelta(days=days_back + 7)  # hafta sonu/tatil için marj
        _s = _start.strftime("%d-%m-%Y")
        _e = _end.strftime("%d-%m-%Y")
        df_isy = fetch_stock_data(symbols=_sym, start_date=_s, end_date=_e)
        if df_isy is None or df_isy.empty:
            return None
        _minimal = {'HGDG_TARIH', 'HGDG_AOF', 'HGDG_HACIM', 'HGDG_KAPANIS'}
        if not _minimal.issubset(df_isy.columns):
            return None
        df_isy = df_isy[df_isy['HGDG_AOF'] > 0].copy()
        if df_isy.empty:
            return None
        idx = pd.to_datetime(df_isy['HGDG_TARIH'])
        if idx.dt.tz is not None:
            idx = idx.dt.tz_localize(None)
        df_out = pd.DataFrame({
            'Close':  df_isy['HGDG_KAPANIS'].values,
            'Volume': (df_isy['HGDG_HACIM'] / df_isy['HGDG_AOF']).values,
        }, index=idx)
        df_out = df_out[df_out['Close'] > 0].dropna()
        return df_out.tail(days_back) if not df_out.empty else None
    except Exception as e:
        log.warning(f"[{ticker}] İsyatirim hata: {e}")
        return None


def override_parquet_volume(ticker, df_isy):
    """Parquet'in son N gününün Volume'ünü İsyatirim verisiyle override eder."""
    # app.py naming: ticker zaten ".IS" ile bitiyor → "SASA.IS_1d.parquet"
    fp = CACHE_DIR / f"{ticker}_1d.parquet"
    if not fp.exists():
        return False, "parquet_yok"
    try:
        df_cache = pd.read_parquet(fp)
        if df_cache.empty or 'Volume' not in df_cache.columns:
            return False, "cache_bos"
        if df_cache.index.tz is not None:
            df_cache.index = df_cache.index.tz_localize(None)
        # Her İsyatirim barı için cache'te eşleşen tarih varsa Volume'ünü override et
        overridden = 0
        for _dt, _row in df_isy.iterrows():
            _dt_match = _dt.normalize()
            _mask = df_cache.index.normalize() == _dt_match
            if _mask.any():
                df_cache.loc[_mask, 'Volume'] = float(_row['Volume'])
                overridden += 1
        if overridden > 0:
            df_cache.to_parquet(fp)
        return True, f"override_{overridden}_gun"
    except Exception as e:
        return False, f"hata_{type(e).__name__}"


def main():
    t_start = time.time()
    log.info("=" * 70)
    log.info(f"Finalize Volume başlıyor — {len(TICKERS)} BIST ticker")
    log.info(f"CACHE_DIR: {CACHE_DIR}")
    log.info("=" * 70)

    stats = {'ok': 0, 'fail': 0, 'no_data': 0, 'no_parquet': 0}

    for i, ticker in enumerate(TICKERS, 1):
        df_isy = fetch_isyatirim_volumes(ticker, days_back=5)
        if df_isy is None or df_isy.empty:
            stats['no_data'] += 1
            if i % 20 == 0:
                log.info(f"  ... [{i}/{len(TICKERS)}] {ticker} → İsyatirim veri yok")
            continue
        ok, status = override_parquet_volume(ticker, df_isy)
        if ok:
            stats['ok'] += 1
        elif status == "parquet_yok":
            stats['no_parquet'] += 1
        else:
            stats['fail'] += 1
            log.warning(f"[{ticker}] {status}")
        # Hız sınırlaması — İsyatirim'i boğmamak için
        time.sleep(0.15)

    # Marker dosyası yaz — app.py bunu görünce volume_quality=FINAL_ISYATIRIM döner
    try:
        _today = datetime.now().strftime("%Y-%m-%d")
        with open(MARKER_FILE, "w", encoding="utf-8") as f:
            f.write(_today)
        log.info(f"Marker yazıldı: {MARKER_FILE} ({_today})")
    except Exception as e:
        log.error(f"Marker yazılamadı: {e}")

    elapsed = time.time() - t_start
    log.info("=" * 70)
    log.info(f"BİTTİ — {elapsed:.1f}sn | OK={stats['ok']} FAIL={stats['fail']} "
             f"NO_DATA={stats['no_data']} NO_PARQUET={stats['no_parquet']}")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
