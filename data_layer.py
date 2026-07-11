# -*- coding: utf-8 -*-
"""
data_layer.py — VERİ KATMANI (Yahoo+İsyatirim hibrit fetch + parquet cache + evren)
===================================================================================
app.py bölme projesi Adım 6c (9 Tem 2026) — BİREBİR taşındı (davranış aynı).

İçerik: get_batch_data_cached / get_safe_historical_data ailesi (İsyatirim volume
override + borsapy gap-fill + binance + futures hacim + split/tatil temizliği +
canlı fiyat yaması) · fetch_stock_info (saçma-değer sigortalı) · endeks fetch ·
BIST evren listeleri + _normalize_bist_ticker · CACHE_DIR/_DEAD_SYMBOLS sabitleri.

NOT: Streamlit'e bilinçli bağımlı (@st.cache_data) — bu katmanın kimliği bu.
Toplu tarama döngüleri (scan_*_batch) app.py'de kaldı: log_scan_signal +
_compute_signal_features'a bağımlılar (Adım 7'de motorla birlikte gelirler).
"""
import os
import re
import io
import json
import time
import math
import streamlit as st
import numpy as np
import pandas as pd
import yfinance as yf
import concurrent.futures
from datetime import datetime, timedelta
import pytz

_TZ_ISTANBUL = pytz.timezone("Europe/Istanbul")

from data_policy import AUTO_ADJUST, drop_adj_close
from db_layer import log_error

# ── BIST Takvim Modülü (app.py ile AYNI blok) ────────────────────────────────
try:
    from bist_calendar import (
        is_trading_day   as _bist_is_trading_day,
        is_half_day      as _bist_is_half_day,
        is_closed        as _bist_is_closed,
        get_rvol_day_factor as _bist_rvol_factor,
        get_session_hours   as _bist_session_hours,
        get_day_label       as _bist_day_label,
        get_day_status      as _bist_day_status,
        AREFE_SESSION_RATIO as _AREFE_RATIO,
    )
    _BIST_CAL_OK = True
except ImportError:
    def _bist_is_trading_day(_dt=None): return True
    def _bist_is_half_day(_dt=None):    return False
    def _bist_is_closed(_dt=None):      return False
    def _bist_rvol_factor(_dt=None):    return 1.0
    def _bist_session_hours(_dt=None):  return ("10:00", "18:00")
    def _bist_day_label(_dt=None):      return "✅ Normal Seans"
    def _bist_day_status(_dt=None):     return ("open", "Normal Seans")
    _AREFE_RATIO = 0.3125
    _BIST_CAL_OK = False

# app.py ile ortak ek bağımlılıklar (otomatik tespit):
from data_policy import AUTO_ADJUST, drop_adj_close
from datetime import datetime, timedelta
from db_layer import DB_FILE, log_error, init_db, _fetch_mkk_yabanci_rss, _compute_mkk_yabanci_signals, load_watchlist_db, add_watchlist_db, remove_watchlist_db, get_scanner_optimal_windows, get_scenario_ages_batch
import os
import pandas as pd
import yfinance as yf

# CACHE_DIR — environment variable ile override edilebilir (VPS deploy için portable)
# Varsayılan: app.py'nin yanındaki "veriler" klasörü
_DEFAULT_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veriler")
CACHE_DIR = os.environ.get("SMR_CACHE_DIR", _DEFAULT_CACHE)
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)



_CACHE_STATS = {'hit': 0, 'miss': 0, 'split_detected': 0}


def _volume_is_stale(df, ticker):
    """
    Son işlem verisinde hacim bozukluğu var mı kontrol eder.
    Endeks/kripto hariç: son non-zero Volume tarihi 5+ takvim günü eskiyse
    (bugün hariç) → parquet bozuk/eksik demektir.
    """
    if df is None or df.empty:
        return False
    if ticker.startswith(("XU", "^")) or "-USD" in ticker:
        return False
    try:
        v = df['Volume']
        v_hist = v.iloc[:-1]           # bugünü hariç tut
        nonzero = v_hist[v_hist > 0]
        if len(nonzero) == 0:
            return True
        last_nz = nonzero.index[-1]
        if hasattr(last_nz, 'date'):
            last_nz = last_nz.date()
        import datetime as _dt_sv
        days_gap = (_dt_sv.date.today() - last_nz).days
        # 5+ takvim günü açık varsa bozuk say (hafta sonu = max 3 gün normal boşluk)
        return days_gap > 5
    except Exception:
        return False


def _fetch_bist_ohlcv_borsapy(symbol, period="1y", interval="1d"):
    """
    borsapy library — TradingView WebSocket üzerinden gerçek-zamanlı OHLCV.
    yfinance + İsyatirim ikisi de başarısız olduğunda son fallback.

    Avantajları:
      - Real-time tick data (TradingView)
      - VIOP destekli (vadeli işlemler — yfinance vermez)
      - BIST hisse + endeks + forex + crypto + fon hepsi tek lib

    Riskleri:
      - TradingView ToS, IP block riski
      - Tek maintainer, downtime riski

    Bu yüzden SADECE primary kaynaklar başarısızken devreye girer.

    Returns: DataFrame (Open, High, Low, Close, Volume) veya None
    """
    try:
        import borsapy as _bp
    except ImportError:
        return None
    try:
        _sym = symbol.replace(".IS", "").upper()
        _t = _bp.Ticker(_sym)
        _df = _t.history(period=period, interval=interval)
        if _df is None or len(_df) == 0:
            return None
        # Sütun normalize
        _df.columns = [str(c).capitalize() for c in _df.columns]
        _need = {'Open', 'High', 'Low', 'Close', 'Volume'}
        if not _need.issubset(_df.columns):
            return None
        # Index tz temizliği
        if _df.index.tz is not None:
            _df.index = _df.index.tz_localize(None)
        return _df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    except Exception as _ex:
        import logging
        logging.warning(f"[borsapy] {symbol} hata: {_ex}")
        return None


def _fetch_bist_ohlcv_isyatirim(symbol, start_date, end_date):
    """
    İş Yatırım API'sinden BIST hisse tam OHLCV verisi çeker.
    yfinance'in bölünme/temettü adjusted price hatalarını giderir.
    Volume = HGDG_HACIM (TL) / HGDG_AOF (ağırlıklı ort. fiyat) → hisse adedi
    Returns: DataFrame (Open, High, Low, Close, Volume, DatetimeIndex) veya None
    """
    try:
        from isyatirimhisse import fetch_stock_data
        _sym = symbol.replace(".IS", "").replace(".is", "").upper()
        from datetime import datetime as _dtime
        _s = _dtime.strptime(start_date, "%Y-%m-%d").strftime("%d-%m-%Y")
        _e = _dtime.strptime(end_date,   "%Y-%m-%d").strftime("%d-%m-%Y")
        df_isy = fetch_stock_data(symbols=_sym, start_date=_s, end_date=_e)
        if df_isy is None or df_isy.empty:
            return None
        # 4 Haz 2026 — İsyatirim API'sinde HGDG_ACILIS sütunu artık YOK.
        # Eski zorunlu kontrol her zaman False döndürüyordu → fallback hiç çalışmıyordu.
        # YENİ MANTIK: Volume için sadece HGDG_HACIM + HGDG_AOF + HGDG_TARIH yeter.
        # Açılış (Open) opsiyonel — yoksa Close ile doldur (Yahoo'nun OHLC zaten doğru).
        _minimal = {'HGDG_TARIH', 'HGDG_AOF', 'HGDG_HACIM', 'HGDG_KAPANIS'}
        if not _minimal.issubset(df_isy.columns):
            return None
        df_isy = df_isy[df_isy['HGDG_AOF'] > 0].copy()
        if df_isy.empty:
            return None
        idx = pd.to_datetime(df_isy['HGDG_TARIH'])
        idx = idx.dt.tz_localize(None) if idx.dt.tz is not None else idx
        # Açılış varsa kullan, yoksa Close ile fallback (override edici fonksiyonlar Open'i
        # genelde Yahoo'dan korur — sadece Volume + Close gerçekten önemli).
        _open_col = df_isy['HGDG_ACILIS'].values if 'HGDG_ACILIS' in df_isy.columns else df_isy['HGDG_KAPANIS'].values
        _high_col = df_isy['HGDG_MAX'].values if 'HGDG_MAX' in df_isy.columns else df_isy['HGDG_KAPANIS'].values
        _low_col  = df_isy['HGDG_MIN'].values if 'HGDG_MIN' in df_isy.columns else df_isy['HGDG_KAPANIS'].values
        df_out = pd.DataFrame({
            'Open':   _open_col,
            'High':   _high_col,
            'Low':    _low_col,
            'Close':  df_isy['HGDG_KAPANIS'].values,
            'Volume': (df_isy['HGDG_HACIM'] / df_isy['HGDG_AOF']).values,
        }, index=idx)
        df_out = df_out[df_out['Close'] > 0].dropna(subset=['Close', 'Volume'])
        return df_out if not df_out.empty else None
    except ImportError:
        return None
    except Exception as _e:
        import logging as _lg
        _lg.warning(f"[_fetch_bist_ohlcv_isyatirim] {symbol} hata: {_e}")
        return None


_FUT_CONTRACT_CANDIDATES = {
    # =F sembolü → aday CME kontrat sembolleri (yfinance formatında)
    # Ay kodları: F=Oca G=Sub H=Mar J=Nis K=May M=Haz N=Tem Q=Agu U=Eyl V=Eki X=Kas Z=Ara
    # Cari + bir sonraki + bir ileri ayı kapsa; max-hacim kuralı doğruyu seçer.
    # Bu liste yılda 1-2 kez (12 ay rollover'a yaklaşırken) güncellenebilir.
    "GC=F": ["GCM26.CMX", "GCQ26.CMX", "GCZ26.CMX", "GCG27.CMX"],   # Gold
    "SI=F": ["SIN26.CMX", "SIU26.CMX", "SIZ26.CMX", "SIH27.CMX"],   # Silver
    "CL=F": ["CLN26.NYM", "CLQ26.NYM", "CLU26.NYM", "CLZ26.NYM"],   # Crude
    "NG=F": ["NGN26.NYM", "NGQ26.NYM", "NGU26.NYM", "NGZ26.NYM"],   # NatGas
    "HG=F": ["HGN26.CMX", "HGU26.CMX", "HGZ26.CMX"],                # Copper
    "PL=F": ["PLN26.NYM", "PLV26.NYM", "PLF27.NYM"],                # Platinum
    "PA=F": ["PAM26.NYM", "PAU26.NYM", "PAZ26.NYM"],                # Palladium
}


def _fetch_futures_volume_yahoo_active(symbol, period="6mo"):
    """
    Emtia futures (=F) için Yahoo'nun aktif CME kontratlarından birleştirilmiş
    gerçek hacim serisi döndürür. Her gün için en yüksek hacimli aday kontrat
    seçilir → kontrat rollover'ı otomatik handle edilir.
    Returns: pd.Series (DatetimeIndex, Volume) veya None
    """
    candidates = _FUT_CONTRACT_CANDIDATES.get(symbol)
    if not candidates:
        return None
    try:
        import yfinance as _yf
        import logging as _lg0
        # 2 Tem 2026 — Aday kontratların bir kısmı TASARIM GEREĞİ expired (GCM26 Haziran
        # geçince boş döner). Fonksiyon bunu zaten `continue` ile atlıyor ama yfinance'in
        # KENDİ logger'ı her expired probe'ta ERROR basıyordu (log spam). Probe boyunca sustur.
        _yf_log = _lg0.getLogger("yfinance")
        _yf_prev = _yf_log.level
        _yf_log.setLevel(_lg0.CRITICAL)
        frames = []
        try:
            for _c in candidates:
                try:
                    _df = _yf.download(_c, period=period, progress=False, auto_adjust=False, threads=False)
                    if _df is None or _df.empty:
                        continue
                    if isinstance(_df.columns, pd.MultiIndex):
                        _df.columns = _df.columns.get_level_values(0)
                    if "Volume" not in _df.columns:
                        continue
                    _v = pd.to_numeric(_df["Volume"], errors="coerce").fillna(0)
                    _v.name = _c
                    frames.append(_v)
                except Exception:
                    continue
        finally:
            _yf_log.setLevel(_yf_prev)      # yfinance logger seviyesini geri yükle
        if not frames:
            return None
        merged = pd.concat(frames, axis=1).fillna(0)
        # Her gün için adaylar arasında max-hacim → o günkü aktif kontrat
        active_volume = merged.max(axis=1)
        # tz-naive normalize (parquet cache + Yahoo OHLCV ile eşleşsin)
        if active_volume.index.tz is not None:
            active_volume.index = active_volume.index.tz_localize(None)
        return active_volume[active_volume > 0]
    except Exception as _e:
        import logging as _lg
        _lg.warning(f"[_fetch_futures_volume_yahoo_active] {symbol} hata: {_e}")
        return None


def _apply_futures_volume_override(df, symbol):
    """
    =F sembolünde df'in Volume kolonunu aktif kontrat hacmiyle override eder.
    Tarih-eşleşmeli (df.index ile kontrat tarihi). Eşleşmeyen günler (eski tarih)
    NaN'la doldurulmaz; orijinal =F değeri korunur (geri uyumluluk).
    """
    if df is None or df.empty or "Volume" not in df.columns:
        return df
    if not symbol or not symbol.endswith("=F"):
        return df
    if symbol not in _FUT_CONTRACT_CANDIDATES:
        return df
    vol_series = _fetch_futures_volume_yahoo_active(symbol)
    if vol_series is None or vol_series.empty:
        return df
    df = df.copy()
    # tz-naive normalize (df tarafı)
    _idx = df.index
    if hasattr(_idx, "tz") and _idx.tz is not None:
        df.index = _idx.tz_localize(None)
    # Tarih-eşleşmeli override (date'e göre, saat farklarını ignore et)
    _df_dates = pd.to_datetime(df.index).normalize()
    _vol_dates = pd.to_datetime(vol_series.index).normalize()
    _vol_map = pd.Series(vol_series.values, index=_vol_dates)
    _vol_map = _vol_map[~_vol_map.index.duplicated(keep="last")]
    _new_vol = pd.Series(_df_dates.map(_vol_map), index=df.index)
    # Sadece eşleşme bulunduğu yerde override; bulunamayan eski tarihler =F kalır
    _mask = _new_vol.notna()
    if _mask.any():
        df.loc[_mask, "Volume"] = _new_vol[_mask].astype(float).values
    return df


_DEAD_SYMBOLS = frozenset({
    "UMPAS", "ISATR", "ROYAL", "RTALB", "FRIGO", "GOKNR", "YYLGD", "LKMNH",
})


def _has_recent_gap(df, lookback_days=45):
    """2 Tem 2026 — borsapy gap-fill'i SADECE gerçekten eksik işlem günü olan
    sembollerde çağırmak için ucuz kapı. Önceden gap-fill HER BIST sembolü için
    borsapy'a gidiyordu (yüzlerce çağrı → TradingView throttle → 'No data received').
    Burada bist_calendar ile df'in son 45 gününde tamamlanmış (bugün HARİÇ) bir işlem
    günü eksikse True döner → sadece o zaman borsapy. Şüphede False (borsapy'ı yorma)."""
    try:
        import bist_calendar as _bc
        import datetime as _dt
        if df is None or df.empty:
            return False
        have = set(pd.DatetimeIndex(df.index).normalize().date)
        last = df.index[-1].date()
        today = _dt.date.today()
        d = last - _dt.timedelta(days=lookback_days)
        while d < today:                       # bugünü hariç tut (oturmamış bar tetiklemesin)
            if _bc.is_trading_day(d) and d not in have:
                return True                     # tamamlanmış bir işlem günü eksik = delik
            d += _dt.timedelta(days=1)
        return False
    except Exception:
        return False


def _apply_split_adjustments(df):
    """
    BIST bedelsiz artırım / bölünme tespiti ve geriye dönük fiyat düzeltmesi.
    BIST günlük fiyat limiti ±%10 → tek günde >%20 düşüş kesinlikle bölünme.
    Birden fazla bölünme varsa hepsini kronolojik sırayla yakalar.
    Volume ters yönde düzeltilir (daha fazla hisse = daha fazla volume).
    """
    if df is None or df.empty or len(df) < 5:
        return df
    df = df.copy().sort_index()
    price_cols = [c for c in ['Open', 'High', 'Low', 'Close'] if c in df.columns]
    if 'Close' not in df.columns:
        return df
    # 24 Haz 2026 — Volume int64'e float ratio atanınca her bölünmede FutureWarning
    # üretiyordu (yüz binlerce konsol satırı → Master Scan boğuluyordu). Önce float64'e
    # çevir ki tip uyumsuzluğu uyarısı çıkmasın.
    for _c in price_cols + (['Volume'] if 'Volume' in df.columns else []):
        if df[_c].dtype != 'float64':
            df[_c] = df[_c].astype('float64')
    import logging as _lg
    for _ in range(10):  # max 10 bölünme — sonsuz döngü koruması
        closes = df['Close'].ffill().values
        split_found = False
        for i in range(1, len(closes)):
            prev, curr = closes[i - 1], closes[i]
            if prev <= 0 or curr <= 0:
                continue
            ratio = prev / curr
            if ratio >= 1.20:  # >%20 tek günlük düşüş → bölünme/bedelsiz artırım
                for col in price_cols:
                    df.iloc[:i, df.columns.get_loc(col)] = (
                        df.iloc[:i][col].values / ratio
                    )
                if 'Volume' in df.columns:
                    df.iloc[:i, df.columns.get_loc('Volume')] = (
                        df.iloc[:i]['Volume'].values * ratio
                    )
                _lg.info(f"[split_adj] Bölünme düzeltildi: satır={i} oran=x{ratio:.2f}")
                split_found = True
                break  # closes güncellendi, dışarı çık ve tekrar tara
        if not split_found:
            break
    return df


def _fix_stale_volume(df_base, clean_ticker, interval):
    """
    start/end ile gelen veride Volume=0 (bozuk) günler varsa,
    period='1mo' ile kısa vadeli çekiş yapıp son 30 günü override eder.
    Eski tarihsel veri korunur, sadece bozuk günler düzeltilir.
    """
    try:
        df_short = _yf_download_with_retry(clean_ticker, period="1mo", interval=interval)
        if df_short.empty:
            return df_base
        # Sütun temizleme
        if isinstance(df_short.columns, pd.MultiIndex):
            df_short.columns = (df_short.columns.get_level_values(0)
                                if 'Close' in df_short.columns.get_level_values(0)
                                else df_short.columns.get_level_values(1))
        df_short = df_short.loc[:, ~df_short.columns.duplicated()].copy()
        df_short.columns = [str(c).capitalize() for c in df_short.columns]
        if df_short.index.tz is not None:
            df_short.index = df_short.index.tz_convert(None)
        if 'Volume' not in df_short.columns or df_short['Volume'].isna().all():
            return df_base
        # Merge: df_base'den short'un başladığı tarihten öncesini al, geri kalanı short ile yaz
        cutoff = df_short.index[0]
        df_old = df_base[df_base.index < cutoff].copy()
        df_merged = pd.concat([df_old, df_short])
        df_merged = df_merged[~df_merged.index.duplicated(keep='last')].sort_index()
        return df_merged
    except Exception:
        return df_base


def is_yahoo_update_needed(ticker, local_last_date):
    now = datetime.now(_TZ_ISTANBUL)
    weekday = now.weekday()
    hour_min = now.hour * 100 + now.minute
    local_date = local_last_date.date()

    if ".IS" in ticker or ticker.startswith("XU"):
        if weekday >= 5:
            return local_date < (now - timedelta(days=(weekday - 4))).date()
        if hour_min < 1000:
            return local_date < (now - timedelta(days=1)).date()
        if hour_min > 1830:
            return local_date < now.date()
        return True

    elif "-USD" not in ticker and "=F" not in ticker:
        if weekday >= 5:
            return local_date < (now - timedelta(days=(weekday - 4))).date()
        if hour_min > 2330 or hour_min < 1630:
            target_date = now.date() if hour_min > 2330 else (now - timedelta(days=1)).date()
            return local_date < target_date
        return True

    return True


# --- BIST LİSTESİ (GENİŞLETİLMİŞ - BIST 500) ---
priority_bist_indices = [
    "XU100.IS", "XU030.IS", "XBANK.IS", "XTUMY.IS", "XUSIN.IS", "EREGL.IS", "SISE.IS", "TUPRS.IS",
    # BIST30 (alfabetik, yukarıdakiler hariç)
    "AKBNK.IS", "ARCLK.IS", "ASELS.IS", "BIMAS.IS", "CCOLA.IS", "EKGYO.IS", "ENKAI.IS",
    "FROTO.IS", "GARAN.IS", "GUBRF.IS", "HALKB.IS", "ISCTR.IS", "KCHOL.IS",
    "KRDMD.IS", "PETKM.IS", "PGSUS.IS", "SAHOL.IS", "SASA.IS", "TCELL.IS", "THYAO.IS",
    "TKFEN.IS", "TOASO.IS", "TTKOM.IS", "TTRAK.IS", "VAKBN.IS", "YKBNK.IS",
]

# Buraya BIST TUM'deki hisseleri ekliyoruz
raw_bist_stocks = [
    "A1CAP.IS", "ACSEL.IS", "ADEL.IS", "ADESE.IS", "ADGYO.IS", "AEFES.IS", "AFYON.IS", "AGESA.IS", "AGHOL.IS", "AGROT.IS", "AGYO.IS", "AHGAZ.IS", "AKBNK.IS", "AKCNS.IS", "AKENR.IS", "AKFGY.IS", "AKGRT.IS", "AKMGY.IS", "AKSA.IS", "AKSEN.IS", "AKSGY.IS", "AKSUE.IS", "AKYHO.IS", "ALARK.IS", "ALBRK.IS", "ALCAR.IS", "ALCTL.IS", "ALFAS.IS", "ALGYO.IS", "ALKA.IS", "ALKIM.IS", "ALTNY.IS", "ALVES.IS", "ANELE.IS", "ANGEN.IS", "ANHYT.IS", "ANSGR.IS", "ARASE.IS", "ARCLK.IS", "ARDYZ.IS", "ARENA.IS", "ARSAN.IS", "ARTMS.IS", "ARZUM.IS", "ASELS.IS", "ASGYO.IS", "ASTOR.IS", "ASUZU.IS", "ATAGY.IS", "ATAKP.IS", "ATATP.IS", "ATEKS.IS", "ATLAS.IS", "ATSYH.IS", "AVGYO.IS", "AVHOL.IS", "AVOD.IS", "AVPGY.IS", "AVTUR.IS", "AYCES.IS", "AYDEM.IS", "AYEN.IS", "AYES.IS", "AYGAZ.IS", "AZTEK.IS",
    "BAGFS.IS", "BAKAB.IS", "BALAT.IS", "BANVT.IS", "BARMA.IS", "BASCM.IS", "BASGZ.IS", "BAYRK.IS", "BEGYO.IS", "BERA.IS", "BEYAZ.IS", "BFREN.IS", "BIENY.IS", "BIGCH.IS", "BIMAS.IS", "BINBN.IS", "BINHO.IS", "BIOEN.IS", "BIZIM.IS", "BJKAS.IS", "BLCYT.IS", "BMSCH.IS", "BMSTL.IS", "BNTAS.IS", "BOBET.IS", "BORLS.IS", "BOSSA.IS", "BRISA.IS", "BRKO.IS", "BRKSN.IS", "BRKVY.IS", "BRLSM.IS", "BRMEN.IS", "BRSAN.IS", "BRYAT.IS", "BSOKE.IS", "BTCIM.IS", "BUCIM.IS", "BURCE.IS", "BURVA.IS", "BVSAN.IS", "BYDNR.IS",
    "CANTE.IS", "CATES.IS", "CCOLA.IS", "CELHA.IS", "CEMAS.IS", "CEMTS.IS", "CEOEM.IS", "CIMSA.IS", "CLEBI.IS", "CMBTN.IS", "CMENT.IS", "CONSE.IS", "COSMO.IS", "CRDFA.IS", "CRFSA.IS", "CUSAN.IS", "CVKMD.IS", "CWENE.IS",
    "DAGI.IS", "DAPGM.IS", "DARDL.IS", "DENGE.IS", "DERHL.IS", "DERIM.IS", "DESA.IS", "DESPC.IS", "DEVA.IS", "DGATE.IS", "DGGYO.IS", "DGNMO.IS", "DIRIT.IS", "DITAS.IS", "DMSAS.IS", "DNISI.IS", "DOAS.IS", "DOCO.IS", "DOFER.IS", "DOGUB.IS", "DOHOL.IS", "DOKTA.IS", "DURDO.IS", "DYOBY.IS", "DZGYO.IS",
    "EBEBK.IS", "ECILC.IS", "ECZYT.IS", "EDATA.IS", "EDIP.IS", "EGEEN.IS", "EGEPO.IS", "EGGUB.IS", "EGPRO.IS", "EGSER.IS", "EKGYO.IS", "EKIZ.IS", "EKSUN.IS", "ELITE.IS", "EMKEL.IS", "EMNIS.IS", "ENJSA.IS", "ENKAI.IS", "ENSRI.IS", "ENTRA.IS", "EPLAS.IS", "ERBOS.IS", "ERCB.IS", "EREGL.IS", "ERSU.IS", "ESCAR.IS", "ESCOM.IS", "ESEN.IS", "ETILR.IS", "ETYAT.IS", "EUHOL.IS", "EUKYO.IS", "EUPWR.IS", "EUREN.IS", "EUYO.IS", "EYGYO.IS",
    "FADE.IS", "FENER.IS", "FLAP.IS", "FMIZP.IS", "FONET.IS", "FORMT.IS", "FORTE.IS", "FRIGO.IS", "FROTO.IS", "FZLGY.IS",
    "GARAN.IS", "GARFA.IS", "GEDIK.IS", "GEDZA.IS", "GENIL.IS", "GENTS.IS", "GEREL.IS", "GESAN.IS", "GLBMD.IS", "GLCVY.IS", "GLRYH.IS", "GLYHO.IS", "GMTAS.IS", "GOKNR.IS", "GOLTS.IS", "GOODY.IS", "GOZDE.IS", "GRNYO.IS", "GRSEL.IS", "GSDDE.IS", "GSDHO.IS", "GSRAY.IS", "GUBRF.IS", "GWIND.IS", "GZNMI.IS",
    "HALKB.IS", "HATEK.IS", "HATSN.IS", "HDFGS.IS", "HEDEF.IS", "HEKTS.IS", "HKTM.IS", "HLGYO.IS", "HRKET.IS", "HTTBT.IS", "HUBVC.IS", "HUNER.IS", "HURGZ.IS",
    "ICBCT.IS", "ICUGS.IS", "IDGYO.IS", "IEYHO.IS", "IHAAS.IS", "IHEVA.IS", "IHGZT.IS", "IMASM.IS", "INDES.IS", "INFO.IS", "INGRM.IS", "INTEM.IS", "INVEO.IS", "INVES.IS", "ISATR.IS", "ISBIR.IS", "ISBTR.IS", "ISCTR.IS", "ISDMR.IS", "ISFIN.IS", "ISGSY.IS", "ISGYO.IS", "ISKPL.IS", "ISKUR.IS", "ISMEN.IS", "ISSEN.IS", "ISYAT.IS", "IZENR.IS", "IZFAS.IS", "IZINV.IS", "IZMDC.IS",
    "JANTS.IS", "TRALT.IS", "ONRYT.IS", "EFOR.IS", "OZATD.IS", "EKDMR.IS",
    "KAPLM.IS", "KAREL.IS", "KARSN.IS", "KATMR.IS", "KAYSE.IS", "KCAER.IS", "KCHOL.IS", "KENT.IS", "KERVN.IS", "KFEIN.IS", "KGYO.IS", "KIMMR.IS", "KLGYO.IS", "KLKIM.IS", "KLMSN.IS", "KLNMA.IS", "KLSER.IS", "KLRHO.IS", "KMPUR.IS", "KNFRT.IS", "KOCMT.IS", "KONKA.IS", "KONTR.IS", "KONYA.IS", "KOPOL.IS", "KORDS.IS", "KOTON.IS", "KRDMA.IS", "KRDMB.IS", "KRDMD.IS", "KRGYO.IS", "KRONT.IS", "KRPLS.IS", "KRSTL.IS", "KRTEK.IS", "KRVGD.IS", "KSTUR.IS", "KTLEV.IS", "KTSKR.IS", "KUTPO.IS", "KUVVA.IS", "KUYAS.IS", "KZBGY.IS", "KZGYO.IS",
    "LIDER.IS", "LIDFA.IS", "LILAK.IS", "LINK.IS", "LKMNH.IS", "LMKDC.IS", "LOGO.IS", "LUKSK.IS",
    "MAALT.IS", "MACKO.IS", "MAGEN.IS", "MAKIM.IS", "MAKTK.IS", "MANAS.IS", "MARBL.IS", "MARKA.IS", "MARTI.IS", "MAVI.IS", "MEDTR.IS", "MEGAP.IS", "MEGMT.IS", "MEKAG.IS", "MEPET.IS", "MERCN.IS", "MERIT.IS", "MERKO.IS", "METRO.IS", "MGROS.IS", "MIATK.IS", "MMCAS.IS", "MNDRS.IS", "MNDTR.IS", "MOBTL.IS", "MOGAN.IS", "MPARK.IS", "MRGYO.IS", "MRSHL.IS", "MSGYO.IS", "MTRKS.IS", "MTRYO.IS", "MZHLD.IS",
    "NATEN.IS", "NETAS.IS", "NIBAS.IS", "NTGAZ.IS", "NUGYO.IS", "NUHCM.IS", "ARFYE.IS", 
    "OBASE.IS", "OBAMS.IS", "ODAS.IS", "ODINE.IS", "OFSYM.IS", "ONCSM.IS", "ORGE.IS", "ORMA.IS", "OSMEN.IS", "OSTIM.IS", "OTKAR.IS", "OTTO.IS", "OYAKC.IS", "OYAYO.IS", "OYLUM.IS", "OYYAT.IS", "OZGYO.IS", "OZKGY.IS", "OZRDN.IS", "OZSUB.IS",
    "PAGYO.IS", "PAMEL.IS", "PAPIL.IS", "PARSN.IS", "PASEU.IS", "PCILT.IS", "PEKGY.IS", "PENGD.IS", "PENTA.IS", "PETKM.IS", "PETUN.IS", "PGSUS.IS", "PINSU.IS", "PKART.IS", "PKENT.IS", "PNLSN.IS", "POLHO.IS", "POLTK.IS", "PRDGS.IS", "PRKAB.IS", "PRKME.IS", "PRZMA.IS", "PSDTC.IS", "PSGYO.IS",
    "QUAGR.IS", "PLTUR.IS", "PATEK.IS", "NETCD.IS",
    "RALYH.IS", "RAYSG.IS", "REEDR.IS", "RGYAS.IS", "RNPOL.IS", "RODRG.IS", "RTALB.IS", "RUBNS.IS", "RYGYO.IS", "RYSAS.IS",
    "SAFKR.IS", "SAHOL.IS", "SAMAT.IS", "SANEL.IS", "SANFM.IS", "SANKO.IS", "SARKY.IS", "SASA.IS", "SAYAS.IS", "SDTTR.IS", "SEGYO.IS", "SEKFK.IS", "SEKUR.IS", "SELEC.IS", "SELVA.IS", "SEYKM.IS", "SILVR.IS", "SISE.IS", "SKBNK.IS", "SKTAS.IS", "SKYMD.IS", "SMART.IS", "SMRTG.IS", "SNGYO.IS", "SNICA.IS", "SNPAM.IS", "SODSN.IS", "SOKE.IS", "SOKM.IS", "SONME.IS", "SRVGY.IS", "SUMAS.IS", "SUNTK.IS", "SURGY.IS", "SUWEN.IS",
    "TABGD.IS", "TATGD.IS", "TAVHL.IS", "TBORG.IS", "TCELL.IS", "TDGYO.IS", "TEKTU.IS", "TERA.IS", "TEZOL.IS", "TGSAS.IS", "THYAO.IS", "TKFEN.IS", "TKNSA.IS", "TLMAN.IS", "TMPOL.IS", "TMSN.IS", "TNZTP.IS", "TOASO.IS", "TRCAS.IS", "TRGYO.IS", "TRILC.IS", "TSGYO.IS", "TSKB.IS", "TSPOR.IS", "TTKOM.IS", "TTRAK.IS", "TUCLK.IS", "TUKAS.IS", "TUPRS.IS", "TUREX.IS", "TURGG.IS", "TURSG.IS",
    "UFUK.IS", "ULAS.IS", "ULKER.IS", "ULUFA.IS", "ULUSE.IS", "ULUUN.IS", "UNLU.IS", "USAK.IS", "TATEN.IS",
    "VAKBN.IS", "VAKFN.IS", "VAKKO.IS", "VANGD.IS", "VBTYZ.IS", "VERUS.IS", "VESBE.IS", "VESTL.IS", "VKFYO.IS", "VKGYO.IS", "VKING.IS", "VRGYO.IS",
    "YAPRK.IS", "YATAS.IS", "YAYLA.IS", "YBTAS.IS", "YEOTK.IS", "YESIL.IS", "YGGYO.IS", "YKBNK.IS", "YKSLN.IS", "YONGA.IS", "YUNSA.IS", "YYAPI.IS", "YYLGD.IS",
    "ZEDUR.IS", "ZOREN.IS", "ZRGYO.IS", "GIPTA.IS", "TEHOL.IS", "PAHOL.IS", "MARMR.IS", "BIGEN.IS", "GLRMK.IS", "TRHOL.IS", "AAGYO.IS", "FRMPL.IS", "MCARD.IS",
    # --- 2025-05 EKLENENler (resmi BIST listesinden eksik bulunanlar) ---
    "AHSGY.IS", "AKFIS.IS", "AKFYE.IS", "AKHAN.IS", "ALKLC.IS", "ARMGD.IS", "ATATR.IS", "BAHKM.IS", "BALSU.IS", "BESLR.IS",
    "BESTE.IS", "BIGTK.IS", "BLUME.IS", "BORSK.IS", "BULGS.IS", "CEMZY.IS", "CGCAM.IS", "DCTTR.IS", "DMRGD.IS", "DOFRB.IS",
    "DSTKF.IS", "DUNYH.IS", "DURKN.IS", "ECOGR.IS", "EGEGY.IS", "EKOS.IS", "EMPAE.IS", "ENDAE.IS", "ENERY.IS", "ENPRA.IS",
    "FRMPL.IS", "GATEG.IS", "GENKM.IS", "GRTHO.IS", "GUNDG.IS", "HOROZ.IS", "IHLAS.IS", "KARTN.IS", "KBORU.IS", "KLSYN.IS",
    "KLYPV.IS", "LRSHO.IS", "LXGYO.IS", "LYDHO.IS", "LYDYE.IS", "MCARD.IS", "MEYSU.IS", "MHRGY.IS", "MOPAS.IS", "NTHOL.IS",
    "OZYSR.IS", "PNSUT.IS", "QNBFK.IS", "QNBTR.IS", "RUZYE.IS", "SEGMN.IS", "SERNT.IS", "SKYLP.IS", "SMRVA.IS", "SVGYO.IS",
    "TARKM.IS", "TCKRC.IS", "TRENJ.IS", "TRMET.IS", "UCAYM.IS", "VAKFA.IS", "VSNMD.IS", "YIGIT.IS", "ZERGY.IS", "ZGYO.IS",
    # --- 6 Tem 2026 EKLENENler (yeni/IPO watchlist — veri gelince fetcher doldurur) ---
    "SSAAT.IS", "EKIM.IS", "ISVEA.IS", "GOLDA.IS", "SOHOE.IS", "ORZAX.IS", "BETAE.IS"
]

# Kopyaları Temizle ve Birleştir
raw_bist_stocks = list(set(raw_bist_stocks) - set(priority_bist_indices))
raw_bist_stocks.sort()
final_bist100_list = priority_bist_indices + raw_bist_stocks

# 16 Haz 2026 — BIST whitelist (`.IS`'siz çağrıları normalize etmek için).
# Bug: bazı çağıranlar bare "BERA" geçiyor → cache "BERA_1d.parquet" yazıyor
# (yanlış ad) + İsyatirim/borsapy override yolları kapanıyor (Yahoo-ham veri).
_BIST_TICKER_SET = {t.replace(".IS", "") for t in (priority_bist_indices + raw_bist_stocks)}

def _normalize_bist_ticker(ticker: str) -> str:
    """Bare BIST ticker'a `.IS` ekler. Diğer her şeyi olduğu gibi döner."""
    if not isinstance(ticker, str) or not ticker:
        return ticker
    if ticker.endswith(".IS"):
        return ticker
    if ticker in _BIST_TICKER_SET:
        return f"{ticker}.IS"
    return ticker


def apply_volume_projection(df, ticker=""):
    if df is None or df.empty or 'Volume' not in df.columns:
        return df

    # FIX (30 May 2026): Merkezi tatil 'hayalet bar' temizliği — TÜM okuma yolları
    # (get_safe_historical_data + get_batch_data_cached) buradan geçer. Sondaki
    # O=H=L=C, V=0 kapalı-gün barlarını söker → panellerin/taramaların hepsi
    # otomatik düzelir. Detay: _strip_holiday_bars docstring.
    df = _strip_holiday_bars(df, ticker)
    if df is None or df.empty:
        return df

    # FIX (21 Haz 2026): İşlenmemiş BIST bedelli/bedelsiz (split) düzeltmesi — MERKEZİ.
    # Bozuk parquet bir kez oluşunca cache-hit serve yolu (4291/4335/4440) split-adj'i
    # ATLIYORDU → FENER gibi çok-bölünmüş hisselerde 52H zirvesi eski ölçekte kalıp
    # sahte -%90 düşüş + master_score çöküşü + AI "veri tutarsızlığı" üretiyordu.
    # _apply_split_adjustments idempotent (>%20 tek-gün düşüş kalmadıysa no-op). SADECE
    # BIST HİSSE — endeks/kripto/FX/futures'ta >%20 günlük düşüş gerçek olabilir → hariç.
    try:
        if (".IS" in ticker) and not ticker.startswith(("XU", "XB", "XT", "XY")):
            df = _apply_split_adjustments(df)
    except Exception:
        pass

    # Türkiye saatini güvenli şekilde al
    try:
        from datetime import timezone, timedelta
        _tz_tr = timezone(timedelta(hours=3))
        now = datetime.now(_tz_tr).replace(tzinfo=None)
    except Exception:
        now = datetime.now()

    # Hafta sonu veya tam tatil günüyse projeksiyon yapma
    if now.weekday() >= 5 or _bist_is_closed():
        return df

    # Elimizdeki son veri BUGÜNE mi ait? Değilse dokunma.
    last_date = df.index[-1].date()
    if last_date != now.date():
        return df

    now_min = now.hour * 60 + now.minute
    current_volume = float(df['Volume'].iloc[-1])

    # KARANTİNA KONTROLÜ: Hacim sıfır veya anlamsız küçükse projeksiyon yapma
    if current_volume < 100:
        return df

    progress = 1.0

    # PİYASA TÜRÜNE GÖRE DİNAMİK AĞIRLIK HARİTASI (U-Şekilli)
    if "-USD" in ticker:
        # Kripto 24 saat kesintisiz (doğrusal akışa daha uygundur)
        progress = max(0.01, now_min / 1440)

    elif "^" in ticker or (not ".IS" in ticker and not ticker.startswith("XU")):
        # ABD Piyasası 16:30-23:00 TR Saati (Toplam 390 dakika)
        open_min = 16 * 60 + 30
        close_min = 23 * 60
        if now_min < open_min:
            return df
        if now_min >= close_min:
            progress = 1.0
        else:
            elapsed = now_min - open_min
            # İlk 60 dk: çarpan >4x — güvenilmez, projeksiyon yapma
            if elapsed < 60:
                return df
            # U-Şeklinde Ağırlık: İlk 60 dk %25 | Orta 270 dk %40 | Son 60 dk %35
            if elapsed <= 60:
                progress = (elapsed / 60) * 0.25
            elif elapsed <= 330:
                progress = 0.25 + ((elapsed - 60) / 270) * 0.40
            else:
                progress = 0.65 + ((elapsed - 330) / 60) * 0.35

    elif _bist_is_half_day():
        # BIST Arefe Günü — 09:55-12:30 TR Saati (Toplam 155 dakika)
        open_min  = 9 * 60 + 55
        close_min = 12 * 60 + 30
        if now_min < open_min:
            return df
        if now_min >= close_min:
            progress = 1.0
        else:
            elapsed = now_min - open_min
            # İlk 30 dk: çarpan çok büyük — güvenilmez, projeksiyon yapma
            if elapsed < 30:
                return df
            # Arefe kısa seans → lineer ilerleme yeterli (U-shape gerek yok)
            progress = elapsed / (close_min - open_min)

    else:
        # BIST Normal Gün — 09:55-18:15 TR Saati (Toplam 500 dakika)
        open_min = 9 * 60 + 55
        close_min = 18 * 60 + 15
        if now_min < open_min:
            return df
        if now_min >= close_min:
            progress = 1.0
        else:
            elapsed = now_min - open_min
            # İlk 60 dk: çarpan >5x — güvenilmez, projeksiyon yapma
            if elapsed < 60:
                return df
            # U-Şeklinde Ağırlık: İlk 120 dk %40 | Orta 260 dk %20 | Son 120 dk %40
            if elapsed <= 120:
                progress = (elapsed / 120) * 0.40
            elif elapsed <= 380:
                progress = 0.40 + ((elapsed - 120) / 260) * 0.20
            else:
                progress = 0.60 + ((elapsed - 380) / 120) * 0.40

    # Güvenlik kilidi: çarpan çok uçuk çıkmasın
    progress = max(0.05, min(progress, 1.0))

    df_proj = df.copy()
    projected_volume = current_volume / progress
    # 24 Haz 2026 — int64 Volume'a float atama FutureWarning üretiyordu; önce float64'e çevir.
    if 'Volume' in df_proj.columns and df_proj['Volume'].dtype != 'float64':
        df_proj['Volume'] = df_proj['Volume'].astype('float64')
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
        df = yf.download(ticker, period="1y", progress=False, auto_adjust=AUTO_ADJUST, prepost=False)

        if df.empty: return None
        df = drop_adj_close(df)
        return df['Close']
    except:
        return None


@st.cache_data(ttl=900, show_spinner=False)
def get_batch_data_cached(asset_list, period="1y"):
    """
    GATEKEEPER DESTEKLİ TOPLU TARAMA MOTORU - MULTIINDEX HATASI GİDERİLDİ
    """
    if not asset_list:
        return pd.DataFrame()
    # 24 Haz 2026 — çıplak BIST sembol (EREGL) → EREGL.IS. get_safe_historical_data zaten
    # normalize ediyordu; burada YOKTU → çıplak semboller Yahoo'ya gidip 404 seli + DONMA yapıyordu.
    asset_list = [_normalize_bist_ticker(t) for t in asset_list]

    missing_assets = []
    combined_dict = {}
    
    for sym in asset_list:
        clean_sym = sym.replace(".IS", "")
        if "BIST" in sym or ".IS" in sym or sym.startswith("XU"):
            clean_sym = sym if sym.endswith(".IS") else f"{sym}.IS"
        file_path = os.path.join(CACHE_DIR, f"{clean_sym}_1d.parquet")

        needs_download = True
        if os.path.exists(file_path):
            try:
                df_cached = pd.read_parquet(file_path)
                if not df_cached.empty:
                    if not is_yahoo_update_needed(sym, df_cached.index[-1]):
                        # Bölünme tespiti: son kapanıştaki ani sıçrama (>35% tek günde)
                        # → cache stale, yeniden indir
                        _rc = df_cached['Close'].dropna()
                        _split_flag = (
                            len(_rc) >= 2 and
                            abs(_rc.iloc[-1] / _rc.iloc[-2] - 1) > 0.35
                        )
                        if _split_flag:
                            os.remove(file_path)
                            _CACHE_STATS['split_detected'] += 1
                        else:
                            df_ready = df_cached.tail(500).copy()
                            combined_dict[sym] = apply_volume_projection(df_ready, sym)
                            needs_download = False
                            _CACHE_STATS['hit'] += 1
            except: pass
        if needs_download and clean_sym.replace(".IS", "") in _DEAD_SYMBOLS:
            # ölü/delisted → ağ denemesi YOK (cache varsa yukarıda zaten sunuldu)
            needs_download = False
        if needs_download:
            missing_assets.append(sym)
            _CACHE_STATS['miss'] += 1

    if missing_assets:
        df_new = _yf_download_with_retry(
            " ".join(missing_assets),
            period="1y", group_by='ticker', threads=True, prepost=False
        )

        # ─── AŞAMA A: Yahoo verisini temizle (serial, hızlı — ~5sn) ──────
        # Bu döngü sadece DataFrame cleanup yapar — isyatirim çağrısı YOK.
        # İsyatirim fallback aşağıda PARALEL yapılır (max_workers=10).
        _isy_candidates = []   # (sym, clean_sym, df_sym_new) — V=0 > %20 olanlar
        _df_pending = {}        # sym → df_sym_new (henüz parquet'e yazılmamış)
        for sym in missing_assets:
            clean_sym = sym.replace(".IS", "")
            if "BIST" in sym or ".IS" in sym or sym.startswith("XU"):
                clean_sym = sym if sym.endswith(".IS") else f"{sym}.IS"

            try:
                # MULTIINDEX TEMİZLİĞİ
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
                if 'Volume' not in df_sym_new.columns or df_sym_new['Volume'].isna().all():
                    df_sym_new['Volume'] = 0.0  # KARANTİNA: Sahte hacim yerine sıfır

                df_sym_new = df_sym_new.dropna(subset=['Close'])
                df_sym_new.index = df_sym_new.index.tz_localize(None)

                _df_pending[sym] = (clean_sym, df_sym_new)

                # İsyatirim adayı mı? (BIST hissesi + son 30g V=0 oranı > %20)
                _is_bist_b43 = sym.endswith(".IS") and not sym.startswith(("XU", "XB", "XT"))
                if _is_bist_b43 and len(df_sym_new) >= 30:
                    _v0_ratio = (df_sym_new['Volume'].tail(30) == 0).sum() / 30
                    if _v0_ratio > 0.20:
                        _isy_candidates.append((sym, clean_sym))
            except Exception:
                continue

        # ─── AŞAMA B: PARALEL İSYATIRIM FALLBACK (max_workers=10) ────────
        # B4-3 v2 (4 Haz 2026): Serial → Paralel.
        # Önceki sürümde her hisse için döngüde serial isyatirim çağrısı vardı:
        # 600 hisse × 0.5sn ≈ 5dk. Şimdi ThreadPoolExecutor ile ~30sn'ye iner.
        # Streamlit context gerekmiyor (isyatirim fonksiyonu salt requests+pandas).
        if _isy_candidates:
            import threading as _th_b43
            from concurrent.futures import ThreadPoolExecutor as _TPE_b43, as_completed as _ac_b43
            _isy_lock = _th_b43.Lock()

            def _isy_fetch_one(item):
                _sym_x, _clean_x = item
                try:
                    _clean_isy_x, _df_x = _df_pending[_sym_x]
                    from datetime import timedelta as _td_x
                    _end_x = _df_x.index[-1] + _td_x(days=1)
                    _start_x = _df_x.index[0]
                    _isy_x = _fetch_bist_ohlcv_isyatirim(
                        _clean_x,
                        _start_x.strftime("%Y-%m-%d"),
                        _end_x.strftime("%Y-%m-%d")
                    )
                    if _isy_x is not None and len(_isy_x) > 5:
                        _common_x = _df_x.index.intersection(_isy_x.index)
                        if len(_common_x) > 0:
                            _zero_x = _df_x.loc[_common_x, 'Volume'] == 0
                            _isynz_x = _isy_x.loc[_common_x, 'Volume'] > 0
                            _fix_x = _common_x[_zero_x & _isynz_x]
                            if len(_fix_x) > 0:
                                _df_x.loc[_fix_x, 'Volume'] = _isy_x.loc[_fix_x, 'Volume']
                                with _isy_lock:
                                    _CACHE_STATS['isy_volume_fix'] = _CACHE_STATS.get('isy_volume_fix', 0) + 1
                                return _sym_x, True, len(_fix_x)
                    return _sym_x, False, 0
                except Exception as _e_x:
                    return _sym_x, False, 0

            with _TPE_b43(max_workers=10) as _ex_b43:
                _futs_b43 = [_ex_b43.submit(_isy_fetch_one, _item) for _item in _isy_candidates]
                for _fut_b43 in _ac_b43(_futs_b43):
                    try: _fut_b43.result(timeout=30)
                    except Exception: pass

        # ─── AŞAMA C: Parquet'e yaz + combined_dict doldur (serial, hızlı) ──
        for sym, (clean_sym, df_sym_new) in _df_pending.items():
            try:
                file_path = os.path.join(CACHE_DIR, f"{clean_sym}_1d.parquet")
                if df_sym_new.empty:
                    continue  # boş indirme mevcut parquet'i ezmesin
                # ── TARİHÇE KORUMASI (9 Tem 2026) ── Yahoo bazen sembolün TÜM
                # geçmişini kesiyor (örn. XTUMY 1y istekte tek bar döndü) →
                # eski parquet'i ezerse aylarca veri kaybolur. Yeni indirme
                # eskisinin yarısından kısaysa eski barları koru (yeni kazanır).
                try:
                    if os.path.exists(file_path):
                        _old_pq = pd.read_parquet(file_path)
                        if len(df_sym_new) < len(_old_pq) // 2:
                            _older_pq = _old_pq[_old_pq.index < df_sym_new.index.min()]
                            if len(_older_pq) > 0:
                                df_sym_new = pd.concat([_older_pq, df_sym_new]).sort_index()
                                df_sym_new = df_sym_new[~df_sym_new.index.duplicated(keep='last')]
                except Exception:
                    pass
                df_sym_new.to_parquet(file_path)
                df_ready = df_sym_new.tail(500).copy()
                combined_dict[sym] = apply_volume_projection(df_ready, sym)
            except Exception:
                continue

    if combined_dict:
        return pd.concat(combined_dict.values(), axis=1, keys=combined_dict.keys())
    return pd.DataFrame()


def _fetch_from_binance(ticker, limit=730):
    """
    Kripto tickerları (-USD) için Binance API'den günlük mum verisi çeker.
    Dönen DataFrame yfinance formatıyla birebir uyumludur (Open/High/Low/Close/Volume + Date index).
    Binance'de coin yoksa veya hata olursa None döner → yfinance'e fallback yapılır.
    """
    import requests as _req
    symbol = ticker.replace("-USD", "USDT").upper()   # BTC-USD → BTCUSDT
    url = (
        f"https://api.binance.com/api/v3/klines"
        f"?symbol={symbol}&interval=1d&limit={limit}"
    )
    try:
        resp = _req.get(url, timeout=8)
        data = resp.json()
        # Binance coin bulamazsa {'code': -1121, 'msg': ...} döndürür
        if isinstance(data, dict) and "code" in data:
            return None
        if not data:
            return None

        cols = [
            "timestamp", "Open", "High", "Low", "Close", "Volume",
            "close_time", "quote_vol", "trades",
            "taker_base", "taker_quote", "ignore"
        ]
        df = pd.DataFrame(data, columns=cols)
        for c in ["Open", "High", "Low", "Close", "Volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["Date"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("Date", inplace=True)
        df.index = df.index.tz_localize(None)
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        return df

    except Exception:
        return None


def _yf_download_with_retry(ticker, max_tries=3, base_delay=1.5, **kwargs):
    """
    yf.download() için retry sarmalayıcı.
    max_tries kez dener; her başarısızlıkta bekleme süresi katlanır.
    Tüm denemeler başarısızsa boş DataFrame döner.
    """
    import time
    last_exc = None
    for attempt in range(max_tries):
        try:
            df = yf.download(ticker, progress=False, auto_adjust=AUTO_ADJUST, **kwargs)
            if not df.empty:
                return drop_adj_close(df)  # ham modda 'Adj Close' fazlalığını at (şema=OHLCV)
        except Exception as e:
            last_exc = e
        if attempt < max_tries - 1:
            time.sleep(base_delay * (attempt + 1))   # 1.5s, 3s, 4.5s
    if last_exc:
        import logging
        logging.warning(f"[yf_retry] {ticker} — {max_tries} denemede veri alınamadı: {last_exc}")
    return pd.DataFrame()


def _flatten_columns(df):
    """MultiIndex kolonları flatten et. Zaten flat ise no-op."""
    if df is None or not hasattr(df, 'columns'):
        return df
    try:
        if isinstance(df.columns, pd.MultiIndex):
            if 'Close' in df.columns.get_level_values(0):
                df.columns = df.columns.get_level_values(0)
            else:
                df.columns = df.columns.get_level_values(1)
            df = df.loc[:, ~df.columns.duplicated()]
            df.columns = [str(c).capitalize() for c in df.columns]
    except Exception:
        pass
    return df


LIVE_PRICE_PATCH_ENABLED = False


@st.cache_data(ttl=60)
def get_live_price(ticker: str) -> float:
    """fast_info üzerinden canlı fiyat çeker. 60 sn cache'li.
    Not: fast_info bir dict değil, object — .get() yerine attribute erişimi kullanılıyor."""
    try:
        fi = yf.Ticker(ticker).fast_info
        # Önce last_price, yoksa regular_market_price dene (attribute, dict değil)
        for _attr in ("last_price", "regular_market_price", "previousClose"):
            try:
                _v = getattr(fi, _attr, None)
                if _v is not None and float(_v) > 0:
                    return float(_v)
            except Exception:
                continue
        # Fallback: info dict'ten dene (daha yavaş ama güvenli)
        _info2 = yf.Ticker(ticker).info
        for _key in ("currentPrice", "regularMarketPrice", "previousClose"):
            _v = _info2.get(_key)
            if _v and float(_v) > 0:
                return float(_v)
        return 0.0
    except Exception:
        return 0.0


def _strip_holiday_bars(df: pd.DataFrame, ticker: str = "") -> pd.DataFrame:
    """SONDAKİ tatil/kapalı-gün 'hayalet barları'nı söker (30 May 2026).

    Sorun: BIST resmi tatil/bayram günlerinde (hafta içine denk gelse bile)
    veri katmanı O=H=L=C=önceki kapanış, Volume=0 olan sahte barlar üretip
    parquet'e yazıyordu (bkz. _patch_live_price hafta-içi dalı + holiday padding).
    Sonuç: 610 BIST hissesinin ~%17'si günlerce %0 değişimle 'donmuş' görünüyordu
    (her panel ve taramaya yayılan veri katmanı hatası).

    Çözüm: serinin SONUNDAN başlayarak, hacmi 0 OLAN ve OHLC'si düz (O==H==L==C)
    barları sök. İki koşul birlikte → gerçek 'işlem olmayan kapalı gün' imzası.
    Sadece düz-trailing barlara dokunur; geçmişteki gerçek 0-hacim tek barına veya
    gerçek seans barına dokunmaz. Tüm okuma yollarında merkezi çağrılır.

    Not: gömülü (ortadaki) tatil barları nadirdir ve sonraki gerçek seans onları
    'düz değil' yapar; bu fonksiyon kasıtlı olarak yalnız trailing'i hedefler ki
    geçmiş seri bütünlüğü bozulmasın.
    """
    try:
        if df is None or df.empty or 'Volume' not in df.columns:
            return df
        o = df['Open'].values; h = df['High'].values
        l = df['Low'].values;  c = df['Close'].values
        v = df['Volume'].values
        i = len(df) - 1
        cut = len(df)
        while i >= 1:   # en az 1 gerçek bar kalsın
            _flat = (abs(o[i] - c[i]) < 1e-9 and abs(h[i] - c[i]) < 1e-9
                     and abs(l[i] - c[i]) < 1e-9)
            _zero = (not (v[i] > 0))   # 0, NaN veya negatif → işlem yok
            if _flat and _zero:
                cut = i; i -= 1
            else:
                break
        if cut < len(df):
            return df.iloc[:cut]
        return df
    except Exception as _e:
        log_error("_strip_holiday_bars", _e, ticker)
        return df


def _patch_live_price(df: pd.DataFrame, ticker: str, interval: str = "1d") -> pd.DataFrame:
    """
    Günlük veri için son satırın Close fiyatını canlı fiyatla günceller.
    Grafik ile fiyat kutusu arasındaki cache kaynaklı uyuşmazlığı giderir.
    Sadece interval='1d' için çalışır; intraday verilere dokunmaz.

    ⚠️ BAN KORUMASI: Master Scan çalışırken (session_state['_master_scan_running']==True)
    yfinance live API çağrılarını ATLA — 500 ticker × 1 API call = ban riski.
    """
    if interval != "1d" or df is None or df.empty:
        return df
    # 1 Tem 2026 — TEK KAYNAK: bayrak kapalıysa canlı patch YOK, ham veriler/ kapanışı döner
    # (infografik + AI ile birebir tutarlı; seans-içi Yahoo çelişkisi ortadan kalkar).
    if not LIVE_PRICE_PATCH_ENABLED:
        return df
    # Master Scan içinde live patch atla (parquet'teki kapanış zaten yeterli)
    try:
        if st.session_state.get('_master_scan_running', False):
            return df
    except Exception:
        pass
    try:
        _live = get_live_price(ticker)
        if _live <= 0:
            return df

        _now   = datetime.now(_TZ_ISTANBUL)
        _today = _now.date()
        # Cumartesi=5, Pazar=6 → hafta sonu
        _is_weekend = (_now.weekday() >= 5)
        # FIX (30 May 2026): BIST resmi tatil/bayram günü de "kapalı gün" sayılır.
        # Eskiden sadece hafta sonu kontrol ediliyordu → bayram hafta-içine
        # denk gelince (örn. Kurban Bayramı 27-28-29 May) "yeni bar ekle" dalı
        # ateşlenip O=H=L=C, V=0 hayalet bar üretiyordu. Sadece BIST için.
        _is_bist = (".IS" in ticker or "BIST" in ticker or ticker.startswith("XU"))
        _is_holiday = False
        if _is_bist:
            try:
                _is_holiday = _bist_is_closed(_today)
            except Exception:
                _is_holiday = False
        _is_closed_day = _is_weekend or _is_holiday

        _last_date = df.index[-1]
        if hasattr(_last_date, "date"):
            _last_date = _last_date.date()

        if _last_date == _today:
            # Bugünün satırı var → Close/High/Low'u canlı fiyatla güncelle
            df = df.copy()
            df.loc[df.index[-1], "Close"] = _live
            df.loc[df.index[-1], "High"]  = max(float(df.loc[df.index[-1], "High"]),  _live)
            df.loc[df.index[-1], "Low"]   = min(float(df.loc[df.index[-1], "Low"]),   _live)

        elif _is_closed_day:
            # ── KAPALI GÜN (hafta sonu VEYA BIST tatil/bayram): Sahte bar EKLEME ──
            # Piyasa kapalı; son gerçek seansın kapanışını canlı fiyatla güncelle.
            # Bu sayede parquet gün içinde yazılmışsa eksik kapanış düzelir.
            df = df.copy()
            df.loc[df.index[-1], "Close"] = _live
            # High'ı ancak _live daha büyükse yukarı çek (aşağı çekme)
            df.loc[df.index[-1], "High"]  = max(float(df.loc[df.index[-1], "High"]), _live)

        else:
            # Hafta içi + bugünün satırı yok → yeni satır ekle (seans açık, veri henüz gelmemiş)
            df = df.copy()
            new_row = df.iloc[-1].copy()
            new_row["Close"]  = _live
            new_row["Open"]   = _live
            new_row["High"]   = _live
            new_row["Low"]    = _live
            new_row["Volume"] = 0.0
            try:
                _tz = df.index.tz if df.index.tz is not None else None
                _new_idx = pd.Timestamp(_today)
                if _tz is not None:
                    _new_idx = _new_idx.tz_localize(_tz)
            except Exception:
                _new_idx = pd.Timestamp(_today)
            # ÖNEMLİ: index name'i koru — yoksa reset_index() 'Date' kolonunu kaybeder
            _orig_name = df.index.name
            _new_df = pd.DataFrame([new_row], index=[_new_idx])
            _new_df.index.name = _orig_name
            df = pd.concat([df, _new_df])

    except Exception as _pe:
        import logging
        logging.warning(f"[patch_live_price] {ticker} — {_pe}")
        log_error("patch_live_price", _pe, ticker)
    return df


@st.cache_data(ttl=300)
def _get_safe_historical_data_cached(ticker, period="1y", interval="1d"):
    try:
        # ── KRİPTO YÖNLENDİRMESİ: -USD tickerları Binance'den çek ──
        if "-USD" in ticker and interval == "1d":
            clean_ticker = ticker.upper()
            file_path = os.path.join(CACHE_DIR, f"{clean_ticker}_1d.parquet")

            # Cache varsa ve güncel ise doğrudan kullan
            if os.path.exists(file_path):
                df_cached = pd.read_parquet(file_path)
                if not is_yahoo_update_needed(ticker, df_cached.index[-1]):
                    return apply_volume_projection(df_cached.tail(500).copy(), ticker)

            # Binance'den taze veri çek
            df_bnc = _fetch_from_binance(ticker, limit=730)
            if df_bnc is not None and not df_bnc.empty:
                df_bnc.to_parquet(file_path)
                return apply_volume_projection(df_bnc.tail(500).copy(), ticker)

            # Binance başarısız → yfinance'e fallback (coin bazı API'lerde farklı formatta olabilir)
            # (aşağıdaki genel yfinance akışına düşer)
        # ─────────────────────────────────────────────────────────────

        clean_ticker = ticker.replace(".IS", "")
        if "BIST" in ticker or ".IS" in ticker or ticker.startswith("XU"):
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
            if 'Volume' not in df.columns or df['Volume'].isna().all():
                df['Volume'] = 0.0  # KARANTİNA: Sahte 1.0 atamasını iptal ettik
            return df

        import datetime as _dt
        _start = str(_dt.date.today() - _dt.timedelta(days=380))
        _end   = str(_dt.date.today() + _dt.timedelta(days=1))

        if os.path.exists(file_path):
            df_cached = pd.read_parquet(file_path)
            df_cached = safe_clean_columns(df_cached)
            # tz-aware ise convert, tz-naive ise dokunma (her iki durumda güvenli)
            if df_cached.index.tz is not None:
                df_cached.index = df_cached.index.tz_convert(None)

            # Hacim bozukluğu kontrolü: son non-zero hacim 5+ gün eskiyse yenile
            _vol_stale = _volume_is_stale(df_cached, ticker)

            if not is_yahoo_update_needed(ticker, df_cached.index[-1]) and not _vol_stale:
                return apply_volume_projection(df_cached.tail(500).copy(), ticker)

            # Güncelleme gerekiyor — retry ile dene
            df_new = _yf_download_with_retry(
                clean_ticker, start=_start, end=_end, interval=interval
            )
            if not df_new.empty:
                df_new = safe_clean_columns(df_new)
                if df_new.index.tz is not None:
                    df_new.index = df_new.index.tz_convert(None)
                # İSYATIRIM OHLCV ENTEGRASYONU (BIST hisseleri, endeks hariç)
                # yfinance'in bölünme/temettü adjusted price + Volume=0 hatalarını giderir.
                _is_bist_stock = (".IS" in ticker or "BIST" in ticker) and not ticker.startswith(("XU", "XB", "XT"))
                if _is_bist_stock and interval == "1d":
                    _isy_ohlcv = _fetch_bist_ohlcv_isyatirim(clean_ticker, _start, _end)
                    if _isy_ohlcv is not None and len(_isy_ohlcv) > 5:
                        _common = df_new.index.intersection(_isy_ohlcv.index)
                        if len(_common) > 0:
                            # 6 Tem 2026 — SADECE Volume override! İsyatirim'in MAX/MIN/KAPANIS'i
                            # temettü-DÜZELTİLMİŞ (Yahoo auto_adjust=True ile birebir eşleşir), Yahoo
                            # Open ise HAM (data_policy.AUTO_ADJUST=False). HLC'yi İsyatirim'den alınca
                            # ham Open + düzeltilmiş HLC karışıyordu → Open>High "Frankenstein" bar
                            # (baktığın her hisse bozuluyordu). İsyatirim'in gerçek katkısı HACİM
                            # (Yahoo BIST hacmi 0/bozuk). Fiyat TAMAMEN Yahoo ham kalır — Master Scan
                            # (get_batch_data_cached) ve smr_core zaten böyle yapıyor; bu yol da hizalandı.
                            if 'Volume' in df_new.columns and df_new['Volume'].dtype != 'float64':
                                df_new['Volume'] = df_new['Volume'].astype('float64')
                            df_new.loc[_common, 'Volume'] = \
                                _isy_ohlcv.loc[_common, 'Volume'].values
                    else:
                        # isyatirimhisse başarısız → eski parquet hacim koruması
                        if 'Volume' in df_cached.columns:
                            _ci = df_new.index.intersection(df_cached.index)
                            if len(_ci) > 0:
                                _nz = df_new.loc[_ci, 'Volume'] == 0
                                _ov = df_cached.loc[_ci, 'Volume'] > 0
                                _rx = _ci[_nz & _ov]
                                if len(_rx) > 0:
                                    df_new.loc[_rx, 'Volume'] = df_cached.loc[_rx, 'Volume']
                if _is_bist_stock and interval == "1d":
                    df_new = _apply_split_adjustments(df_new)

                # ── EMTIA FUTURES HACIM OVERRIDE (15 Haz 2026) ──
                # Yahoo =F continuous Volume bozuk → aktif CME kontratından override
                if ticker.endswith("=F") and interval == "1d":
                    df_new = _apply_futures_volume_override(df_new, ticker)

                # ── TARİHÇE KORUMASI (9 Tem 2026) ──
                # Yahoo bazen sembolün TÜM geçmişini vermeyi keser (örn. XTUMY
                # 1y istekte tek bar döndü) → df_new cache'i ezerse aylarca veri
                # kaybolur. Yeni indirme eskisinin yarısından kısaysa, eski
                # parquet'in yeni indirmeden önceki barlarını koru (yeni kazanır).
                if len(df_new) < len(df_cached) // 2:
                    _older = df_cached[df_cached.index < df_new.index.min()]
                    if len(_older) > 0:
                        df_new = pd.concat([_older, df_new]).sort_index()
                        df_new = df_new[~df_new.index.duplicated(keep='last')]

                # ── BORSAPY GAP-FILL (10 Haz 2026) ──
                # Yahoo bazen iş gününü atlar (örn. XU100 9 Haz Pazartesi yok).
                # BIST endeks/hisse için borsapy'den son 30g'i kontrol et,
                # eksik tarihler varsa SATIRLARI EKLE (mevcut barlara dokunma).
                _is_bist_any = (".IS" in ticker or "BIST" in ticker or ticker.startswith(("XU", "XB", "XT")))
                # 2 Tem 2026 — SADECE gerçekten eksik işlem günü varsa borsapy'a git
                # (önceden her sembolde → yüzlerce çağrı → TradingView throttle/'No data received').
                if _is_bist_any and interval == "1d" and _has_recent_gap(df_new):
                    try:
                        import borsapy as _bp_gf
                        _sym_gf = ticker.replace(".IS", "").upper()
                        # Veri ciddi kısaysa (Yahoo geçmişi kesmiş) 3mo yerine 1y iste
                        _gf_period = "1y" if len(df_new) < 100 else "3mo"
                        # BIST'te X-prefix endekslere ayrılmıştır (XTUMY/XBANK/XUSIN...)
                        # Index.history auto_adjust almaz (endekste bölünme/temettü yok)
                        if _sym_gf.startswith("X") or ticker.startswith("^"):
                            _df_gf = _bp_gf.Index(_sym_gf).history(period=_gf_period, interval="1d")
                        else:
                            _df_gf = _bp_gf.Ticker(_sym_gf).history(period=_gf_period, interval="1d", auto_adjust=AUTO_ADJUST)
                        if _df_gf is not None and len(_df_gf) > 5:
                            if _df_gf.index.tz is not None:
                                _df_gf.index = _df_gf.index.tz_localize(None)
                            _df_gf.index = pd.DatetimeIndex([d.normalize() for d in _df_gf.index])
                            _df_gf.index.name = df_new.index.name
                            _df_gf = _df_gf[['Open','High','Low','Close','Volume']].copy()
                            # Sadece df_new'da OLMAYAN tarihleri ekle
                            _missing = _df_gf.index.difference(df_new.index)
                            if len(_missing) > 0:
                                import logging
                                logging.info(f"[borsapy gap-fill] {ticker}: {len(_missing)} eksik bar → "
                                             f"{[d.strftime('%Y-%m-%d') for d in _missing[-5:]]}")
                                _add_rows = _df_gf.loc[_missing]
                                df_new = pd.concat([df_new, _add_rows]).sort_index()
                                df_new = df_new[~df_new.index.duplicated(keep='first')]
                    except Exception as _gf_ex:
                        import logging
                        logging.warning(f"[borsapy gap-fill] {ticker} hata: {_gf_ex}")

                df_new.to_parquet(file_path)
                return apply_volume_projection(df_new.tail(500).copy(), ticker)

            # ── KATMAN 2 FALLBACK: borsapy (TradingView) ──
            # Yahoo retry başarısız + İsyatirim yok → borsapy son şans.
            # Sadece veri ≥1 gün eskiyse devreye gir (intraday başarısızlığında atla).
            _stale_days = (datetime.now(_TZ_ISTANBUL).date() - df_cached.index[-1].date())
            _is_bist_stock_u = (".IS" in ticker or "BIST" in ticker) and not ticker.startswith(("XU", "XB", "XT"))
            if _stale_days.days >= 1 and _is_bist_stock_u and interval == "1d":
                _bp_df = _fetch_bist_ohlcv_borsapy(ticker, period="1y", interval=interval)
                if _bp_df is not None and len(_bp_df) > 5:
                    _bp_df = _apply_split_adjustments(_bp_df)
                    try: _bp_df.to_parquet(file_path)
                    except Exception: pass
                    import logging
                    logging.info(f"[borsapy] {ticker} cache update fallback — {len(_bp_df)} bar")
                    return apply_volume_projection(_bp_df.tail(500).copy(), ticker)

            # borsapy de başarısız → eski cache'i döndür + staleness işareti
            if _stale_days.days >= 1:
                st.session_state['_data_stale'] = {
                    'ticker': ticker,
                    'days':   _stale_days.days,
                    'last':   df_cached.index[-1].strftime('%d.%m.%Y'),
                }
            return apply_volume_projection(df_cached.tail(500).copy(), ticker)

        else:
            # Parquet yok — önce start/end ile dene, başarısız olursa period ile dene
            df_full = _yf_download_with_retry(
                clean_ticker, start=_start, end=_end, interval=interval
            )
            if df_full.empty:
                # start/end başarısız → period="1y" ile dene (Yahoo farklı davranıyor)
                import logging as _log2
                _log2.warning(f"[get_hist] {clean_ticker} start/end başarısız → period='1y' fallback")
                df_full = _yf_download_with_retry(
                    clean_ticker, period="1y", interval=interval
                )
            if not df_full.empty:
                df_full = safe_clean_columns(df_full)
                # tz-aware ise convert, tz-naive ise dokunma
                if df_full.index.tz is not None:
                    df_full.index = df_full.index.tz_convert(None)
                # İSYATIRIM OHLCV ENTEGRASYONU (BIST hisseleri, endeks hariç)
                # yfinance'in bölünme/temettü adjusted price + Volume=0 hatalarını giderir.
                _is_bist_stock = (".IS" in ticker or "BIST" in ticker) and not ticker.startswith(("XU", "XB", "XT"))
                if _is_bist_stock and interval == "1d":
                    _isy_ohlcv = _fetch_bist_ohlcv_isyatirim(clean_ticker, _start, _end)
                    if _isy_ohlcv is not None and len(_isy_ohlcv) > 5:
                        _common = df_full.index.intersection(_isy_ohlcv.index)
                        if len(_common) > 0:
                            # 6 Tem 2026 — SADECE Volume override (yukarıdaki cache-hit yoluyla aynı
                            # gerekçe): İsyatirim HLC düzeltilmiş, Yahoo Open ham → karışım Open>High
                            # üretiyordu. Fiyat Yahoo ham kalır, İsyatirim'den yalnız hacim alınır.
                            if 'Volume' in df_full.columns and df_full['Volume'].dtype != 'float64':
                                df_full['Volume'] = df_full['Volume'].astype('float64')
                            df_full.loc[_common, 'Volume'] = \
                                _isy_ohlcv.loc[_common, 'Volume'].values
                    # isyatirimhisse başarısız → _fix_stale_volume fallback
                    elif _volume_is_stale(df_full, ticker):
                        df_full = _fix_stale_volume(df_full, clean_ticker, interval)
                if _is_bist_stock and interval == "1d":
                    df_full = _apply_split_adjustments(df_full)

                # ── EMTIA FUTURES HACIM OVERRIDE (15 Haz 2026) — fresh download path ──
                if ticker.endswith("=F") and interval == "1d":
                    df_full = _apply_futures_volume_override(df_full, ticker)

                # ── BORSAPY GAP-FILL (10 Haz 2026) — fresh download path ──
                # Aynı bar atlama tespiti, parquet ilk oluşturulduğunda da uygula
                _is_bist_any2 = (".IS" in ticker or "BIST" in ticker or ticker.startswith(("XU", "XB", "XT")))
                # 2 Tem 2026 — SADECE gerçek boşlukta borsapy (throttle önleme, yukarıdaki gibi)
                if _is_bist_any2 and interval == "1d" and _has_recent_gap(df_full):
                    try:
                        import borsapy as _bp_gf2
                        _sym_gf2 = ticker.replace(".IS", "").upper()
                        # Veri ciddi kısaysa (Yahoo geçmişi kesmiş) 3mo yerine 1y iste
                        _gf_period2 = "1y" if len(df_full) < 100 else "3mo"
                        # BIST'te X-prefix endekslere ayrılmıştır (XTUMY/XBANK/XUSIN...)
                        # Index.history auto_adjust almaz (endekste bölünme/temettü yok)
                        if _sym_gf2.startswith("X") or ticker.startswith("^"):
                            _df_gf2 = _bp_gf2.Index(_sym_gf2).history(period=_gf_period2, interval="1d")
                        else:
                            _df_gf2 = _bp_gf2.Ticker(_sym_gf2).history(period=_gf_period2, interval="1d", auto_adjust=AUTO_ADJUST)
                        if _df_gf2 is not None and len(_df_gf2) > 5:
                            if _df_gf2.index.tz is not None:
                                _df_gf2.index = _df_gf2.index.tz_localize(None)
                            _df_gf2.index = pd.DatetimeIndex([d.normalize() for d in _df_gf2.index])
                            _df_gf2.index.name = df_full.index.name
                            _df_gf2 = _df_gf2[['Open','High','Low','Close','Volume']].copy()
                            _missing2 = _df_gf2.index.difference(df_full.index)
                            if len(_missing2) > 0:
                                import logging
                                logging.info(f"[borsapy gap-fill fresh] {ticker}: {len(_missing2)} eksik bar")
                                _add_rows2 = _df_gf2.loc[_missing2]
                                df_full = pd.concat([df_full, _add_rows2]).sort_index()
                                df_full = df_full[~df_full.index.duplicated(keep='first')]
                    except Exception: pass

                df_full.to_parquet(file_path)
                return apply_volume_projection(df_full.tail(500).copy(), ticker)
            else:
                # ── KATMAN 2 FALLBACK: borsapy (TradingView WebSocket) ──
                # yfinance her iki yöntemde de başarısız. BIST hisse ise borsapy dene.
                # Son çare — primary çalışmadığında.
                _is_bist_stock_fb = (".IS" in ticker or "BIST" in ticker) and not ticker.startswith(("XU", "XB", "XT"))
                if _is_bist_stock_fb and interval == "1d":
                    _bp_df = _fetch_bist_ohlcv_borsapy(ticker, period="1y", interval=interval)
                    if _bp_df is not None and len(_bp_df) > 5:
                        # Bölünme düzeltmesi + cache yaz
                        _bp_df = _apply_split_adjustments(_bp_df)
                        try: _bp_df.to_parquet(file_path)
                        except Exception: pass
                        import logging
                        logging.info(f"[borsapy] {ticker} fallback başarılı — {len(_bp_df)} bar")
                        return apply_volume_projection(_bp_df.tail(500).copy(), ticker)

                # Her iki yöntem de başarısız — session_state'e hata işareti bırak
                st.session_state['_data_stale'] = {
                    'ticker': ticker,
                    'days':   999,
                    'last':   'Hiç çekilemedi',
                }

        return None

    except Exception as e:
        import logging
        logging.warning(f"[get_safe_historical_data] {ticker} hata: {e}")
        log_error("get_safe_historical_data", e, ticker)
        return None


def _ensure_parquet_on_disk(ticker: str, interval: str = "1d") -> None:
    """
    @st.cache_data YAN ETKİ SORUNU İÇİN GÜVENLİK NETI:
    Cached fonksiyon bellekten döndüğünde parquet yazılmayabilir.
    Bu fonksiyon cache DIŞINDA çalışır — her çağrıda parquet var mı kontrol eder,
    yoksa indirir ve kaydeder. Crypto ve intraday atlanır.
    """
    # Sadece günlük ve kripto olmayan tickerlar için
    if interval != "1d" or "-USD" in ticker:
        return
    try:
        # file_path'i hesapla (cached fonksiyonla aynı mantık)
        _ct = ticker.replace(".IS", "")
        if "BIST" in ticker or ".IS" in ticker or ticker.startswith("XU"):
            _ct = ticker if ticker.endswith(".IS") else f"{ticker}.IS"
        _fp = os.path.join(CACHE_DIR, f"{_ct}_{interval}.parquet")

        if os.path.exists(_fp):
            return  # Zaten var → işlem yok

        import datetime as _dt2
        _s = str(_dt2.date.today() - _dt2.timedelta(days=380))
        _e = str(_dt2.date.today() + _dt2.timedelta(days=1))

        _df = _yf_download_with_retry(_ct, start=_s, end=_e, interval=interval)
        if _df.empty:
            _df = _yf_download_with_retry(_ct, period="1y", interval=interval)
        if _df.empty:
            return

        # Sütunları temizle
        if isinstance(_df.columns, pd.MultiIndex):
            _lvl = _df.columns.get_level_values(0)
            _df.columns = _lvl if "Close" in _lvl else _df.columns.get_level_values(1)
        _df = _df.loc[:, ~_df.columns.duplicated()].copy()
        _df.columns = [str(c).capitalize() for c in _df.columns]
        if "Volume" not in _df.columns or _df["Volume"].isna().all():
            _df["Volume"] = 0.0
        # Timezone temizle
        if _df.index.tz is not None:
            _df.index = _df.index.tz_convert(None)
        # Kaydet
        _df.to_parquet(_fp)
        import logging as _lg2
        _lg2.info(f"[ensure_parquet] {_ct} yazildi: {len(_df)} satir")
    except Exception as _ep:
        import logging as _lg3
        _lg3.warning(f"[ensure_parquet] {ticker} hata: {_ep}")


@st.cache_data(ttl=3600)
def _fetch_index_isyatirim_cached(symbol):
    """20 Haz 2026 — İsyatirim endeks kapanış serisi (XU100 vb. gap-fill için). Cache 1sa.
    Yahoo endeksi ara işlem günü atlayabiliyor; İsyatirim VALUE tam → delik doldurma kaynağı.
    Dönüş: {Timestamp(normalize): close_float}."""
    try:
        import isyatirimhisse as _iy
        from datetime import datetime as _dtt, timedelta as _tdd
        _e = _dtt.now(); _s = _e - _tdd(days=400)
        ix = _iy.fetch_index_data(indices=symbol.replace('.IS', '').upper(),
                                  start_date=_s.strftime('%d-%m-%Y'), end_date=_e.strftime('%d-%m-%Y'))
        if ix is None or ix.empty or 'DATE' not in ix.columns or 'VALUE' not in ix.columns:
            return {}
        ix = ix.copy(); ix['DATE'] = pd.to_datetime(ix['DATE'])
        return {pd.Timestamp(d).normalize(): float(v) for d, v in zip(ix['DATE'], ix['VALUE']) if v and float(v) > 0}
    except Exception:
        return {}


def _validate_index_fill(v, prev_close, next_close, limit=0.12):
    """20 Haz 2026 — DOĞRULAMA KAPISI. Doldurulacak endeks değeri makul mü?
    Kaynağa GÜVENMEK yerine DEĞERİ komşu gerçek barlarla kıyasla: endeks günlük ~%10 oynar,
    eklenen değerin komşulara implike ettiği hareket ±%12 üstüyse → çöp/typo/ölçek hatası, REDDET.
    Bu HER kaynaktan (İsyatirim dahil) gelen bozuk değeri yakalar. Dönüş: (ok: bool, reason: str)."""
    try:
        v = float(v)
        if v <= 0:
            return False, "değer ≤0"
        if prev_close and float(prev_close) > 0:
            mv_in = v / float(prev_close) - 1
            if abs(mv_in) > limit:
                return False, f"önceki güne göre %{mv_in*100:+.1f} (limit ±%{limit*100:.0f})"
        if next_close and float(next_close) > 0:
            mv_out = float(next_close) / v - 1
            if abs(mv_out) > limit:
                return False, f"sonraki güne göre %{mv_out*100:+.1f} (limit ±%{limit*100:.0f})"
        return True, "ok"
    except Exception as e:
        return False, f"doğrulama hatası: {str(e)[:30]}"


def _log_gapfill_rejects(ticker, rejects):
    """Reddedilen (şüpheli) endeks dolum değerlerini logs/gapfill_rejects.json'a yaz.
    Heartbeat/data_integrity bunu okuyup 'değer şüpheli, doldurulMADI' diye admin'e bildirir."""
    try:
        import json as _json
        _base = os.path.dirname(os.path.abspath(__file__))
        _ld = os.path.join(_base, 'logs')
        os.makedirs(_ld, exist_ok=True)
        _fp = os.path.join(_ld, 'gapfill_rejects.json')
        _cur = []
        if os.path.exists(_fp):
            try:
                _cur = _json.load(open(_fp, encoding='utf-8'))
            except Exception:
                _cur = []
        _now = pd.Timestamp.now().isoformat()
        for (d, v, pc, nc, reason) in rejects:
            _cur.append({'ticker': str(ticker), 'date': pd.Timestamp(d).date().isoformat(),
                         'value': float(v), 'prev': pc, 'next': nc, 'reason': reason, 'logged_at': _now})
        _cur = _cur[-50:]   # son 50 kayıt yeter
        _json.dump(_cur, open(_fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    except Exception:
        pass


def _gapfill_index_isyatirim(df, ticker):
    """20 Haz 2026 — Yahoo endeks (XU100 vb.) ARA işlem günü deliklerini İsyatirim VALUE ile doldur.
    Sadece eksik işlem günlerini (bist_calendar) ekler; mevcut Yahoo barlarına DOKUNMAZ.
    Endeks olduğu için eklenen barda OHLC=kapanış, Volume=0 (sistem endeks 0-hacmi zaten yönetiyor).
    DOĞRULAMA KAPISI: her dolum değeri _validate_index_fill'den geçer; şüpheliyi enjekte ETMEZ, loglar."""
    try:
        if df is None or getattr(df, 'empty', True) or len(df) < 5:
            return df
        import bist_calendar as _bc
        _last = pd.Timestamp(df.index[-1]).normalize()
        _have = set(pd.Timestamp(d).normalize() for d in df.index)
        _exp = []
        _d = _last - pd.Timedelta(days=35)
        while _d <= _last:
            if _bc.is_trading_day(_d.date()):
                _exp.append(_d.normalize())
            _d += pd.Timedelta(days=1)
        _miss = [d for d in _exp if d not in _have]
        if not _miss:
            return df
        _imap = _fetch_index_isyatirim_cached(ticker)
        if not _imap:
            return df
        # DOĞRULAMA KAPISI: her aday değeri komşu gerçek barlarla sına; şüpheliyi enjekte etme.
        _close = df['Close']
        _rows = []; _rejects = []
        for d in _miss:
            if d not in _imap:
                continue
            v = _imap[d]
            _pv = _close[_close.index < d]
            _nx = _close[_close.index > d]
            pc = float(_pv.iloc[-1]) if len(_pv) else None
            nc = float(_nx.iloc[0]) if len(_nx) else None
            ok, reason = _validate_index_fill(v, pc, nc)
            if ok:
                _rows.append((d, v))
            else:
                _rejects.append((d, v, pc, nc, reason))
        if _rejects:
            _log_gapfill_rejects(ticker, _rejects)
        if not _rows:
            return df
        # OHLC: İsyatirim sadece kapanış (VALUE) verir → O=C=kapanış. H/L'ye MİNİK spread (±%0.01)
        # ekliyoruz ki "düz bar" (O=H=L=C) sayılıp HAYALET-BAR temizliğine TAKILMASIN. Endeks V=0 +
        # düz = tatil hayaleti sanılıp siliniyordu → grafik 18 Haz'ı göstermiyordu (kapanış-tabanlı
        # %değişim doğruydu ama grafik bara kalıyordu). Spread görünmez (close-tabanlı hesaplara etkisiz).
        _add = pd.DataFrame(
            {'Open':   [v for _, v in _rows],
             'High':   [round(v * 1.0001, 4) for _, v in _rows],
             'Low':    [round(v * 0.9999, 4) for _, v in _rows],
             'Close':  [v for _, v in _rows],
             'Volume': [0] * len(_rows)},
            index=[d for d, _ in _rows])
        out = pd.concat([df, _add]).sort_index()
        out = out[~out.index.duplicated(keep='first')]
        return out
    except Exception:
        return df


def get_safe_historical_data(ticker, period="1y", interval="1d"):
    """
    Public wrapper — önce cache'li OHLCV'yi alır, sonra
    cache dışında canlı fiyat patch'ini uygular.
    Bu sayede fiyat her render'da güncellenir, OHLCV cache'i bozulmaz.

    _ensure_parquet_on_disk: cache'in bellek sonucu döndürdüğü durumlarda
    parquet'in diske yazılmasını garanti eder.
    """
    # 16 Haz 2026 — bare BIST ticker (örn. "BERA") gelirse `.IS` ekle.
    # Yoksa cache yanlış isimle yazılır (BERA_1d.parquet) + İsyatirim/borsapy bypass olur.
    ticker = _normalize_bist_ticker(ticker)
    # 2 Tem 2026 — ÖLÜ/DELISTED sembol → ağ denemesi YOK, diskteki son bilinen veriyi döndür.
    if ticker.replace(".IS", "") in _DEAD_SYMBOLS:
        try:
            _dc = ticker if ticker.endswith(".IS") else (
                f"{ticker}.IS" if (".IS" in ticker or ticker.startswith("XU")) else ticker)
            _dp = os.path.join(CACHE_DIR, f"{_dc}_1d.parquet")
            if os.path.exists(_dp):
                return pd.read_parquet(_dp)
        except Exception:
            pass
        return pd.DataFrame()
    _ensure_parquet_on_disk(ticker, interval)

    # ── BÖLÜNME / TEMETTÜ TESPİTİ ────────────────────────────────────────────
    # Parquet'teki son kapanış ile canlı fiyat arasında 2x'ten fazla fark varsa
    # bölünme/büyük temettü sonrası düzeltme yapılmamış demektir.
    # Parquet + Streamlit in-memory cache temizlenerek taze veri çekilir.
    if interval == "1d":
        try:
            _ct_s = ticker.replace(".IS", "")
            if ".IS" in ticker or ticker.startswith("XU"):
                _ct_s = ticker if ticker.endswith(".IS") else f"{ticker}.IS"
            _fp_s = os.path.join(CACHE_DIR, f"{_ct_s}_1d.parquet")
            if os.path.exists(_fp_s):
                _df_p   = pd.read_parquet(_fp_s)
                _last_p = float(_df_p['Close'].dropna().iloc[-1])
                _live_p = get_live_price(ticker)
                if _live_p > 0 and _last_p > 0:
                    _ratio = max(_last_p, _live_p) / min(_last_p, _live_p)
                    if _ratio > 2.0:
                        os.remove(_fp_s)
                        _get_safe_historical_data_cached.clear()
        except Exception:
            pass
    # ─────────────────────────────────────────────────────────────────────────

    df = _get_safe_historical_data_cached(ticker, period=period, interval=interval)
    # 20 Haz 2026 — ENDEKS GAP-FILL: Yahoo XU100 vb. ara işlem günü atlayabiliyor (% değişim +
    # beta/RS bozuluyor). İsyatirim'den (cache'li) eksik günleri doldur. Sadece endeks ticker'larda.
    if interval == "1d" and ticker.upper().startswith(("XU", "XB", "XT", "XY")):
        _df_gf = _gapfill_index_isyatirim(df, ticker)
        if _df_gf is not df:   # delik dolduruldu → parquet'e de yaz (karne/backfill/data_integrity için)
            try:
                _ct_gf = ticker if ticker.endswith(".IS") else f"{ticker}.IS"
                _df_gf.to_parquet(os.path.join(CACHE_DIR, f"{_ct_gf}_1d.parquet"))
            except Exception:
                pass
        df = _df_gf
    return _patch_live_price(df, ticker, interval)


@st.cache_data(ttl=60)
def fetch_stock_info(ticker):
    # 1 Tem 2026 — TEK KAYNAK: bayrak kapalıyken fiyat SADECE veriler/ günlük kapanışından
    # okunur (canlı Yahoo fast_info/intraday bypass edilir). _patch_live_price de kapalı olduğu
    # için get_safe_historical_data ham kapanış döner → sağ kart = infografik = AI, HEPSİ AYNI.
    if not LIVE_PRICE_PATCH_ENABLED:
        try:
            _vt  = _normalize_bist_ticker(ticker)
            _vdf = get_safe_historical_data(_vt, period="3mo")
            if _vdf is None or _vdf.empty or 'Close' not in _vdf.columns:
                return None
            _vc = _vdf['Close'].dropna()
            if len(_vc) < 1:
                return None
            _vprice = float(_vc.iloc[-1])
            _vprev  = float(_vc.iloc[-2]) if len(_vc) >= 2 else _vprice
            # 8 Tem 2026 — SAÇMA-DEĞER SİGORTASI: çekim anına denk gelen bozuk Yahoo
            # yanıtı cache'e girip fiyat kartını zehirleyebiliyor (XU100 "11.54" vakası;
            # 6 Tem TUPRS paneli aynı aile). Son fiyat, önceki 5 kapanış medyanından
            # %40+ sapıyorsa güvenme → diskteki parquet kapanışına düş.
            try:
                if len(_vc) >= 6:
                    _vmed = float(_vc.iloc[-6:-1].median())
                    if _vmed > 0 and abs(_vprice - _vmed) / _vmed > 0.40:
                        import os as _sv_os
                        _pq = _sv_os.path.join('veriler', f'{_vt}_1d.parquet')
                        if _sv_os.path.exists(_pq):
                            _pqc = pd.read_parquet(_pq, columns=['Close'])['Close'].dropna()
                            if len(_pqc):
                                _vprice = float(_pqc.iloc[-1])
                                _vprev = float(_pqc.iloc[-2]) if len(_pqc) >= 2 else _vprice
            except Exception:
                pass
            _vchg   = ((_vprice - _vprev) / _vprev * 100) if _vprev > 0 else 0.0
            _vvol   = float(_vdf['Volume'].dropna().iloc[-1]) if ('Volume' in _vdf.columns and _vdf['Volume'].dropna().size) else 0.0
            return {"price": _vprice, "change_pct": _vchg, "volume": _vvol, "sector": "-", "target": "-"}
        except Exception:
            return None
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
            try:
                # Yahoo quirk: period="5d" gives older close (good for prev_close),
                # period="1d" gives the most recent close (good for current price).
                h1 = yf.Ticker(ticker).history(period="1d")
                h5 = yf.Ticker(ticker).history(period="5d")
            except Exception:
                h1 = h5 = None
            if h1 is not None and not h1.empty:
                price  = float(h1["Close"].iloc[-1])
                volume = float(h1["Volume"].iloc[-1])
                if h5 is not None and not h5.empty:
                    # h1 ve h5 farklı son tarihte ise, h5'in son satırı önceki kapanış
                    if h1.index[-1].date() != h5.index[-1].date():
                        prev_close = float(h5["Close"].iloc[-1])
                    elif len(h5) > 1:
                        prev_close = float(h5["Close"].iloc[-2])
                    else:
                        prev_close = price
                else:
                    prev_close = price
            elif h5 is not None and not h5.empty:
                price      = float(h5["Close"].iloc[-1])
                prev_close = float(h5["Close"].iloc[-2]) if len(h5) > 1 else price
                volume     = float(h5["Volume"].iloc[-1])
            else:
                return None

        # ── YAHOO 1m INTRADAY TAZELEMESİ (10 Haz 2026) ──
        # BIST + seans açık ise Yahoo'nun 1m bar endpoint'inden son tick'i çek.
        # Bu ~10 dk delayed (Yahoo'nun BIST için yasal limiti) ama günlük
        # kapanışa göre daha taze. fast_info veya günlük h1 gün başı kapanışı
        # gösterdiği için intraday hareket kaybediliyordu — şimdi yakalanıyor.
        try:
            _is_bist_intraday = (".IS" in ticker or "BIST" in ticker or ticker.startswith("XU"))
            _now_tr_intr = datetime.now(_TZ_ISTANBUL)
            _seans_acik = (
                _now_tr_intr.weekday() < 5
                and 1000 <= (_now_tr_intr.hour * 100 + _now_tr_intr.minute) <= 1830
            )
            if _is_bist_intraday and _seans_acik:
                _h1m = yf.Ticker(ticker).history(period="1d", interval="1m")
                if _h1m is not None and not _h1m.empty:
                    _last_tick = float(_h1m["Close"].iloc[-1])
                    if _last_tick > 0:
                        price = _last_tick  # intraday son fiyat üzerine yaz
        except Exception: pass

        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0

        # FIX (30 May 2026): Kapalı günde (hafta sonu/BIST tatil) Yahoo fast_info
        # last_price == previous_close veriyor → değişim %0.00 görünüyordu.
        # Bu durumda günlük değişimi TEMİZLENMİŞ cache'in son iki GERÇEK seansından
        # hesapla (hayalet barlar zaten _strip_holiday_bars ile sökülmüş olur).
        _is_bist = (".IS" in ticker or "BIST" in ticker or ticker.startswith("XU"))
        _closed_now = False
        try:
            _now_tr = datetime.now(_TZ_ISTANBUL)
            _closed_now = (_now_tr.weekday() >= 5) or (_is_bist and _bist_is_closed(_now_tr.date()))
        except Exception:
            _closed_now = False
        if abs(change_pct) < 1e-6 or _closed_now:
            try:
                _hist = get_safe_historical_data(ticker, period="1mo")
                if _hist is not None and len(_hist) >= 2:
                    _rc = _hist['Close'].dropna()
                    if len(_rc) >= 2 and float(_rc.iloc[-2]) > 0:
                        _last = float(_rc.iloc[-1]); _prev = float(_rc.iloc[-2])
                        change_pct = (_last - _prev) / _prev * 100
                        # Fiyat kutusu kapalı günde son gerçek kapanışı göstersin
                        if _closed_now:
                            price = _last
            except Exception:
                pass

        # FIX (10 Haz 2026 Oturum 20): BIST endeks / hisse — Yahoo eksik bar
        # detection. Yahoo bazen bir önceki seansı ATLIYOR (örn. 10 Haz'da Yahoo
        # 9 Haz Pazartesi kapanışını veremedi, 8→10 atladı). prev_close=8 Haz
        # geliyor, gerçek dün=9 Haz → %değişim YANLIŞ.
        # Çözüm: borsapy ile dünün gerçek kapanışını DOĞRULA. Yahoo prev_close
        # ile borsapy'nin dünkü close değeri arasında >%0.2 fark varsa
        # borsapy'yi kullan (eşik düşük tutuldu — fark genelde küçük ama
        # gerçek; %0.5 üstü zaten ciddi bar atlamadır).
        # PLUS: borsapy'nin TARİH index'ini de kullan — Yahoo'nun atladığı
        # gün borsapy'de varsa bu kesin kanıttır.
        if _is_bist and not _closed_now and price and prev_close:
            try:
                import borsapy as _bp
                _sym_b = ticker.replace(".IS", "").upper()
                # XU100 için Index, hisse için Ticker
                if ticker.startswith("XU") or ticker.startswith("^"):
                    _obj_b = _bp.Index(_sym_b.replace(".IS", ""))
                else:
                    _obj_b = _bp.Ticker(_sym_b)
                _df_b = _obj_b.history(period="5d", interval="1d")
                if _df_b is not None and len(_df_b) >= 2:
                    _bp_prev = float(_df_b['Close'].iloc[-2])
                    _bp_today = float(_df_b['Close'].iloc[-1])
                    # ÖNEMLİ — borsapy'nin "dün"ü Yahoo'nun "dün"ünden farklı mı?
                    # Eğer >%0.2 fark VEYA |prev_close - bp_today| < |prev_close - bp_prev|
                    # (yani Yahoo'nun prev_close'u borsapy'nin BUGÜNÜNE daha yakın
                    # = bar atlamış olduğu güne karışmış)
                    _diff_pct = abs(_bp_prev - prev_close) / _bp_prev if _bp_prev > 0 else 0
                    _bar_skipped = abs(prev_close - _bp_today) < abs(prev_close - _bp_prev)
                    if _bp_prev > 0 and (_diff_pct > 0.002 or _bar_skipped):
                        import logging
                        logging.info(f"[fetch_stock_info] {ticker}: Yahoo prev_close "
                                     f"{prev_close:.2f} → borsapy düzeltme {_bp_prev:.2f} "
                                     f"(diff %{_diff_pct*100:.2f})")
                        prev_close = _bp_prev
                        change_pct = ((price - prev_close) / prev_close * 100)
            except Exception: pass

        return { "price": price, "change_pct": change_pct, "volume": volume or 0, "sector": "-", "target": "-" }
    except: return None


@st.cache_data(ttl=900, show_spinner=False)
def fetch_index_data_cached():
    import yfinance as yf
    import pandas as pd
    try:
        index_df = drop_adj_close(yf.download("XU100.IS", period="1y", progress=False, auto_adjust=AUTO_ADJUST))
        if not index_df.empty:
            if isinstance(index_df.columns, pd.MultiIndex):
                return index_df['Close'].iloc[:, 0] if not index_df['Close'].empty else None
            else:
                return index_df['Close']
    except:
        pass
    return None


def _data_integrity_check(df, ticker):
    """Veriyi denetler, güven damgası döndürür (ONARMAZ).
    {'clean': bool, 'durum': 'TEMİZ'|'ŞÜPHELİ', 'issues': [insan-okur], 'distrust': [metrik]}"""
    issues = []
    distrust = []
    try:
        if df is None or len(df) < 20 or 'Close' not in df.columns:
            return {'clean': False, 'durum': 'ŞÜPHELİ',
                    'issues': ['veri yetersiz (<20 bar)'], 'distrust': ['hepsi']}

        is_index = str(ticker).upper().startswith(("XU", "XB", "XT", "XY"))
        c = df['Close']; h = df.get('High'); l = df.get('Low'); v = df.get('Volume')

        # 1) NaN / sıfır kapanış (son 60 bar)
        if c.tail(60).isna().any() or (c.tail(60) <= 0).any():
            issues.append('son 60 günde NaN/sıfır kapanış')
            distrust.append('fiyat')

        # 2) OHLC bütünlüğü (son 60 bar)
        if h is not None and l is not None:
            if int((h.tail(60) < l.tail(60)).sum()) > 0:
                issues.append('Yüksek < Düşük olan bar(lar)')
                distrust.append('mum')
            try:
                _out = int(((c.tail(60) > h.tail(60) + 1e-6) |
                            (c.tail(60) < l.tail(60) - 1e-6)).sum())
                if _out > 2:
                    issues.append(f'{_out} barda kapanış mum aralığı dışında')
                    distrust.append('mum')
            except Exception:
                pass

        # 3) Ölçek / işlenmemiş bölünme kalıntısı (hisse)
        if not is_index and len(df) >= 60:
            win = df.tail(252)
            hi = float(win['High'].max()); last = float(c.iloc[-1])
            if hi > 0 and 0 < last < hi * 0.04:
                issues.append(f"son fiyat 52H zirvenin %{last/hi*100:.0f}'i — işlenmemiş bölünme şüphesi")
                distrust.extend(['52h_zirve', 'master_score'])
            if bool((c.pct_change().tail(252) < -0.35).any()):
                issues.append('son 1 yılda düzeltilmemiş >%35 tek-gün düşüş')
                distrust.extend(['52h_zirve', 'getiri'])

        # 4) Tekrar / büyük veri deliği
        if bool(df.index.duplicated().any()):
            issues.append('tekrarlı tarih (duplicate gün)')
        try:
            if not is_index and int((df.index.to_series().diff().dt.days.tail(60) > 6).sum()) > 0:
                issues.append('son 60 günde >6 günlük veri deliği')
        except Exception:
            pass

        # 5) Sıfır-hacim serisi (endeks hariç)
        if v is not None and not is_index:
            if int((v.tail(10) <= 0).sum()) >= 5:
                issues.append('son 10 günde 5+ sıfır-hacim bar')
                distrust.append('hacim')
    except Exception as _e:
        return {'clean': False, 'durum': 'ŞÜPHELİ',
                'issues': [f'denetim hatası: {_e}'], 'distrust': []}

    clean = (len(issues) == 0)
    return {'clean': clean, 'durum': 'TEMİZ' if clean else 'ŞÜPHELİ',
            'issues': issues, 'distrust': sorted(set(distrust))}

# ── GORUNTU ADI SOZLUGU (Adim 8'de app.py'den tasindi — varlik alani) ──
# --- GÖRÜNTÜ ADI SÖZLÜĞÜ ---
# Yahoo ticker kodu → Kullanıcıya gösterilecek isim
TICKER_DISPLAY_NAMES = {
    "GC=F":    "ONS ALTIN",
    "SI=F":    "GÜMÜŞ",
    "CL=F":    "WTI PETROL",
    "BZ=F":    "BRENT PETROL",
    "NG=F":    "DOĞAL GAZ",
    "HG=F":    "BAKIR",
    "ZW=F":    "BUĞDAY",
    "ZC=F":    "MISIR",
    "BTC-USD": "BITCOIN",
    "ETH-USD": "ETHEREUM",
    "BNB-USD": "BNB",
    "SOL-USD": "SOLANA",
    "XRP-USD": "XRP",
    "DOGE-USD":"DOGECOIN",
    "AVAX-USD":"AVALANCHE",
    "^GSPC":   "S&P 500",
    "^IXIC":   "NASDAQ",
    "^DJI":    "DOW JONES",
}


def get_display_name(ticker):
    """Ticker kodunu görüntü adına çevirir. Bilinmiyorsa temiz kodu döner."""
    if ticker in TICKER_DISPLAY_NAMES:
        return TICKER_DISPLAY_NAMES[ticker]
    return ticker.split('.')[0].replace("=F", "").replace("-USD", "")
