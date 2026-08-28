"""
SMR — Backtest Runner v2 (standalone)

Çıktı: backtest_results.json + patron.db → signal_results tablosu
Kaynak: patron.db (scan_signals) + veriler/*.parquet

Kullanım:
    python backtest_runner.py
    veya: run_backtest.bat (double-click)
    veya: Windows Task Scheduler (her gün 03:00)

Yeni metrikler (v2):
    - Bias-corrected hit (bearish sinyal → aşağı giderse ✅)
    - std_dev: tutarlılık ölçüsü
    - avg_win / avg_loss: kazanç ve kayıp ortalamaları
    - stop_hit: pencere boyunca stop seviyesine değdi mi?
    - expectancy: (hit% × avg_win) + (miss% × avg_loss)
    - signal_results: bireysel sonuçlar patron.db'ye yazılır
"""

import sqlite3
import json
import sys
import os
import io
import time
from pathlib import Path
from datetime import datetime, date, timedelta
from collections import defaultdict
import pandas as pd
import pytz
from signal_policy import ensure_event_schema, resolve_next_open_entry

# UTF-8 stdout zorunlu (Windows cp1254 emoji uyumsuzluğunu çöz)
try:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

# ─── KONFİG ──────────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
DB_FILE         = BASE_DIR / "patron.db"
PARQUET_DIR     = BASE_DIR / "veriler"
OUTPUT_FILE     = BASE_DIR / "backtest_results.json"
LOOKBACK_DAYS   = 90
FORWARD_WINDOWS = [5, 10, 20]
TZ_ISTANBUL     = pytz.timezone("Europe/Istanbul")

# ── İDEAL VADE (alpha-zirvesi) taraması ──────────────────────────────────────
# Her scan_type için gün 1..MAXD_VADE getiri+alpha biriktirilir; sinyalin piyasaya
# göre ÜSTÜN olduğu (alpha en yüksek) gün = ideal vade. Tek "şampiyon gün" yerine
# alpha platosu bir ARALIK olarak verilir (aşırı-uyuma karşı). UI'da alt-satır rozeti.
MAXD_VADE       = 28
MIN_N_VADE      = 15    # bir günün güvenilir sayılması için min sinyal (mutlak taban)
EDGE_MIN_ALPHA  = 0.5   # "piyasayı geçiyor" demek için min α — altı gürültü sayılır
# ÖRNEK-DESTEK KAPISI: uzun vadede örnek erir (90g lookback → geç sinyaller d-gün ileriye
# ulaşamaz); eriyen kuyrukta 30-70 survivor α'yı şişirir = "kuyruğa-yapışma" / aşırı-uyum.
# Gözlem (2 Tem 2026): sahte-zirve günleri HEP n/nmax ≤ 0.30, dürüst iç zirveler HEP ≥ 0.51
# → aradaki temiz boşluğa 0.50 eşik. Bir gün ancak sinyallerin çoğu o vadeye ULAŞTIYSA güvenilir.
NFRAC_VADE      = 0.50  # peak adayı: n >= NFRAC_VADE * nmax (o günün örnek-desteği)

# ─── SENARYO ETİKETLERİ ──────────────────────────────────────────────────────
SCENARIO_NAMES = {
    'A1': 'Dipte Sessizlik',          'A2': 'Hacimli Tepki',
    'A3': 'Çift Dip / İkinci Test',   'A4': 'Yön Değiştiriyor',
    'A5': 'Toparlanma Başlıyor',      'A6': 'Dipte Sıkışma',
    'A7': 'Hacimli Toparlanma',       'A8': 'Ucuz + Hacimli Atak',
    'A9': 'Ucuz Bölgede Bekleyiş',
    'B1': 'Mükemmel Sıkışma',         'B2': 'Sessiz Birikim',
    'B3': 'Klasik Sıkışma',           'B4': 'Yatayda Hacim Patlaması',
    'B5': 'Üçgen Daralma',            'B6': 'Yatay + Endekse Direnç',
    'B7': 'Yatay + Hafif Pullback',   'B8': 'Sıkışma Sonu',
    'B9': 'Alt Sınır Testi',          'B10': 'Yatay + Momentum Çelişkisi',
    'B11': 'Tepe Yakını Sıkışma',
    'C1': 'İdeal Pullback',           'C2': 'Ortalama Testi',
    'C3': 'Pullback + Hacimli Alım',  'C4': 'Soluklanma',
    'C5': 'Bayrak Formasyonu',        'C6': 'Piyasa Lideri',
    'C7': 'Sağlam Trend Yapısı',      'C8': 'Yukarı Kanal Testi',
    'C9': 'Pullback Başlıyor',        'C10': 'Trendde Sıkışma',
    'C11': 'Trendde Momentum Sağlam',
    'D1': 'Tek Güçlü Sinyal',         'D2': 'Karışık Sinyal',
    'D3': 'Erken Aşama',              'D4': 'Kurumsal Satış Riski',
    'D5': 'Trend Bozuldu',
}
CAT_ICONS = {'A': '🔄', 'B': '📐', 'C': '🚀', 'D': '⚠'}

CLASSIC_LABELS = {
    'guclu_donus':    '💪 Güçlü Dönüş',
    'nadir_firsat':   '🔥 Royal Flush (Nadir Fırsat)',
    'minervini':      '🦁 Minervini SEPA',
    'rs_leaders':     '🚀 RS Momentum',
    'golden_pattern': '⭐ Altın Formasyon',
    'prelaunch_bos':  '🎯 Pre-Launch BOS',
    'ict_sniper':     '🎯 ICT Sniper',
    'gizli_birikim':  '🔍 Gizli Birikim',
    'radar1':         '🧠 Radar 1',
    'radar2':         '🚀 Radar 2',
    'harmonik_confluence': '🎵 Harmonik Confluence',
    'vip_formasyon':  '💎 VIP Formasyon',
    'para_akisi_lider': '💧 Para Akışı Liderleri',
    'tavan_alarm':    '🚀 Tavan Alarm (Skor≥150)',
    'tavan_top30':    '🚀 Tavan TOP 30',
    'rsi_pozitif_uyumsuzluk': '📈 RSI Pozitif Uyumsuzluk',
    'liderlik_aday':     '🔎 Liderlik Adayı (Radar2)',
    'liderlik_yeni':     '🟢 Yeni Lider (Radar2+C6)',
    'liderlik_teyitli':  '✅ Liderlik Teyitli',
    'liderlik_c6':       '🏁 C6 Liderlik Teyidi',
    'liderlik_gec':      '⏳ Geç Liderlik Sinyali',
}


def label_for_scan_type(scan_type: str) -> str:
    if scan_type in CLASSIC_LABELS:
        return CLASSIC_LABELS[scan_type]
    if scan_type.startswith('er_'):
        sid = scan_type[3:]
        nm  = SCENARIO_NAMES.get(sid, sid)
        cat = sid[0] if sid and sid[0] in CAT_ICONS else '•'
        return f"{CAT_ICONS.get(cat, '•')} ER {sid} · {nm}"
    return scan_type


def category_for_scan_type(scan_type: str) -> str:
    if scan_type.startswith('er_'):
        sid = scan_type[3:]
        return sid[0] if sid and sid[0] in 'ABCD' else 'X'
    return 'CLASSIC'


def load_parquet(symbol: str):
    for suffix in ('.IS_1d.parquet', '_1d.parquet'):
        p = PARQUET_DIR / f"{symbol}{suffix}"
        if p.exists():
            try:
                df = pd.read_parquet(p)
                df.index = pd.to_datetime(df.index)
                return df.sort_index()
            except Exception:
                return None
    return None


def load_xu100():
    """XU100 benchmark verisini yükle (alpha hesabı için)."""
    df = load_parquet("XU100.IS")
    return df


# ── KURUMSAL İŞLEM (BÖLÜNME) BEKÇİSİ — 15 Tem 2026 ──────────────────────────
# Sorun: data_policy.AUTO_ADJUST=False → parquet'lerde bölünme/bedelsiz
# DÜZELTİLMEMİŞ (seviyeler aracıyla tutsun diye, bilinçli karar). Seviye ölçmek
# için doğru ama GETİRİ ölçmek için zehir: TRHOL 12 Ara 2024'te 17.33 → 1733.00
# (×100) görünüyor → o pencereye düşen sinyal "+%9900 getiri" yazıyordu. Tek
# böyle satır bir taramanın ortalamasını tek başına "elit" yapmaya yeter
# (ölçüldü: Güçlü Dönüş "+%1.43 alfa"sının tamamı bu tek satırdandı).
#
# Zehir ret_*'ı değil, max_gain/max_loss ve STOP_HIT'i de bozuyor (bölünmede
# fiyat düşer → sahte stop tetiklemesi).
#
# Eşik %15: BIST günlük limiti ±%10. Tüm evrende (156.598 günlük hareket)
# ölçüldü → %10-11.5 arası 612 meşru limit hareketi var, %13-15 arası SIFIR
# gözlem (temiz uçurum), %15 üstü 15 olayın hepsi kurumsal işlem. %25 eşiği
# %15-25 bandındaki bedelsizleri kaçırırdı.
#
# Politika: bölünme içeren pencere ÖLÇÜLEMEZ → None (uydurma değil, dürüst
# boşluk). Etkilenen ~%0.5 sinyal; kısa pencere temizse o korunur.
KURUMSAL_ESIK = 0.15

# ── VERİ TUTARSIZLIĞI BEKÇİSİ — 15 Tem 2026 ─────────────────────────────────
# İkinci (ve daha sinsi) zehir yolu: entry_price scan anında CANLI kaydedilir
# (scan_signals), çıkış fiyatı ise parquet'ten okunur. İkisi AYNI gün AYNI
# hisseyi gösterir → ayrışamaz. Ayrışıyorsa parquet bozuktur.
#   Gerçek vaka: AKYHO 21 May 2026 — DB entry 2.60 (İsyatirim doğruluyor: 2.65),
#   parquet 13163.90 (5294x şişik) → "5 günde +%546.061 getiri". Bu TEK satır
#   er_A9'un 5g ortalamasını -%1.11'den +%177.34'e çıkarıyordu.
# Bölünme bekçisi bunu YAKALAMAZ (pencerede bölünme yok, parquet'in TAMAMI
# yanlış ölçekte). Eşik veri_bekcisi.TOL_REF_ORAN ile aynı: 1.25 (BIST günlük
# limit ±%10 → aynı günün canlı fiyatı ile kapanışı %25'ten fazla ayrışamaz).
TUTARSIZLIK_ORAN = 1.25


def kurumsal_islem_indeksleri(df):
    """|günlük değişim| > %15 olan barların POZİSYONEL indeksleri (set).
    BIST'te ±%10 limit → %15+ tek-gün hareketi ancak bölünme/bedelsiz olabilir."""
    try:
        r = df['Close'].astype(float).pct_change()
        return {i for i, v in enumerate((r.abs() > KURUMSAL_ESIK).tolist()) if v}
    except Exception:
        return set()


def ilk_kurumsal_islem(kurumsal_idx, bas, son):
    """(bas, son] aralığındaki İLK kurumsal işlem pozisyonu — yoksa None."""
    if not kurumsal_idx:
        return None
    aday = [i for i in kurumsal_idx if bas < i <= son]
    return min(aday) if aday else None


def ensure_signal_results_table(conn):
    """signal_results tablosunu oluştur (yoksa)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_results (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id      INTEGER,
            symbol         TEXT,
            scan_type      TEXT,
            signal_date    TEXT,
            bias           TEXT,
            entry_price    REAL,
            entry_date     TEXT,
            entry_gap_pct  REAL,
            entry_delay    INTEGER,
            entry_status   TEXT,
            stop_level     REAL,
            ret_5g         REAL,
            ret_10g        REAL,
            ret_20g        REAL,
            hit_5g         INTEGER,
            hit_10g        INTEGER,
            hit_20g        INTEGER,
            stop_hit_5g    INTEGER,
            stop_hit_10g   INTEGER,
            stop_hit_20g   INTEGER,
            max_gain_20g   REAL,
            max_loss_20g   REAL,
            evaluated_at   TEXT,
            UNIQUE(signal_id)
        )
    """)
    for column, kind in (
        ("entry_date", "TEXT"),
        ("entry_gap_pct", "REAL"),
        ("entry_delay", "INTEGER"),
        ("entry_status", "TEXT"),
    ):
        try:
            conn.execute(f"ALTER TABLE signal_results ADD COLUMN {column} {kind}")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def ensure_signal_returns_table(conn):
    """Bağımsız olayların girişten sonraki 1-20 seans yolunu saklar."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_returns (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id   INTEGER NOT NULL,
            scan_type   TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            entry_price REAL,
            day_offset  INTEGER NOT NULL,
            close_price REAL,
            return_pct  REAL,
            category    TEXT,
            UNIQUE(signal_id, day_offset)
        )
    """)
    conn.commit()


def upsert_result(conn, row: dict):
    """Bireysel sinyal sonucunu yaz (varsa güncelle)."""
    conn.execute("""
        INSERT INTO signal_results (
            signal_id, symbol, scan_type, signal_date, bias,
            entry_price, entry_date, entry_gap_pct, entry_delay, entry_status, stop_level,
            ret_5g, ret_10g, ret_20g,
            hit_5g, hit_10g, hit_20g,
            stop_hit_5g, stop_hit_10g, stop_hit_20g,
            max_gain_20g, max_loss_20g, evaluated_at
        ) VALUES (
            :signal_id, :symbol, :scan_type, :signal_date, :bias,
            :entry_price, :entry_date, :entry_gap_pct, :entry_delay, :entry_status, :stop_level,
            :ret_5g, :ret_10g, :ret_20g,
            :hit_5g, :hit_10g, :hit_20g,
            :stop_hit_5g, :stop_hit_10g, :stop_hit_20g,
            :max_gain_20g, :max_loss_20g, :evaluated_at
        )
        ON CONFLICT(signal_id) DO UPDATE SET
            entry_price=excluded.entry_price, entry_date=excluded.entry_date,
            entry_gap_pct=excluded.entry_gap_pct, entry_delay=excluded.entry_delay,
            entry_status=excluded.entry_status, stop_level=excluded.stop_level,
            ret_5g=excluded.ret_5g, ret_10g=excluded.ret_10g, ret_20g=excluded.ret_20g,
            hit_5g=excluded.hit_5g, hit_10g=excluded.hit_10g, hit_20g=excluded.hit_20g,
            stop_hit_5g=excluded.stop_hit_5g, stop_hit_10g=excluded.stop_hit_10g,
            stop_hit_20g=excluded.stop_hit_20g,
            max_gain_20g=excluded.max_gain_20g, max_loss_20g=excluded.max_loss_20g,
            evaluated_at=excluded.evaluated_at
    """, row)


def evaluate_signals(lookback_days=LOOKBACK_DAYS, forward_windows=None):
    """
    scan_signals'ı tarayıp her sinyalin forward getirilerini hesapla.
    Yeni: bias-corrected hit, stop tracking, max_gain/loss, bireysel kayıt.
    """
    if forward_windows is None:
        forward_windows = FORWARD_WINDOWS

    # OneDrive senkronu patron.db'yi anlık kilitleyebiliyor → hemen çökme, 30sn bekle.
    conn = sqlite3.connect(DB_FILE, timeout=30)
    ensure_event_schema(conn)
    ensure_signal_results_table(conn)
    ensure_signal_returns_table(conn)

    signals = pd.read_sql(
        "SELECT * FROM scan_signals WHERE scan_date >= date('now', ?) "
        "AND COALESCE(is_event_start, 1)=1",
        conn,
        params=(f'-{lookback_days} days',)
    )

    today   = datetime.now(TZ_ISTANBUL).date()
    min_fwd = min(forward_windows)
    max_fwd = max(forward_windows)
    results = []
    db_rows = []   # 21 Tem 2026 — yazmalar döngü SONUNA ertelenir (kilit süresi kök çözümü, aşağı bak)
    daily_return_rows = []
    pending = 0
    total   = len(signals)

    # İdeal vade birikimi: {scan_type: {day: {'rets':[], 'alphas':[]}}}
    vade_accum = defaultdict(lambda: defaultdict(lambda: {'rets': [], 'alphas': []}))

    # ── XU100 benchmark — alpha hesabı için ─────────────────────────────────
    df_xu100 = load_xu100()
    print(f"  → XU100 benchmark: {'yüklendi' if df_xu100 is not None else 'bulunamadı'}")

    print(f"  → {total} sinyal değerlendiriliyor (min {min_fwd}g geçmiş gerekir)...")
    last_pct = -1
    kurumsal_atlanan = 0   # 15 Tem 2026 — bölünme penceresine düşen sinyal sayısı
    tutarsiz_bazlanan = 0  # 15 Tem 2026 — girişi parquet bazına taşınan (bölünme)
    entry_status_counts = defaultdict(int)
    entry_gaps = []

    # ── İş #8 (28 Tem 2026): KURUMSAL TAKVİM — TEMETTÜ getiri düzeltmesi ──────────
    # Yukarıdaki %15 sıçrama bekçisi (kurumsal_islem_indeksleri) sadece BÖLÜNMEYİ
    # yakalar; temettü (~%2) eşiğin altında kaçıp getiriyi mekanik ~%2 EKSİK ölçtürür.
    # Oran-yöntemi takvimi (kurumsal_takvim, İsyatirim÷Yahoo, kırmızı çizgi: fiyat
    # değil sadece olay) temettü penceresinde getiriye düşüşü GERİ EKLER — sinyali
    # ATMADAN dürüstleştirir. Cache TEK okumada (per-sinyal dosya okuma YOK).
    try:
        from kurumsal_takvim import load_all_cached as _kt_load_all
        _ca_events = _kt_load_all()
    except Exception:
        _ca_events = {}
    temettu_ayarlanan = 0

    for i, (_, sig) in enumerate(signals.iterrows()):
        pct = int((i + 1) / total * 100)
        if pct != last_pct and pct % 10 == 0:
            print(f"    [{pct}%] {i+1}/{total}")
            last_pct = pct

        try:
            sig_date = pd.to_datetime(sig['scan_date']).date()

            df_hist = load_parquet(sig['symbol'])
            if df_hist is None or df_hist.empty:
                continue

            sig_ts = pd.Timestamp(sig['scan_date'])
            _hist_dates = pd.to_datetime(df_hist.index)
            if getattr(_hist_dates, 'tz', None) is not None:
                _hist_dates = _hist_dates.tz_localize(None)
            signal_idx = int(_hist_dates.normalize().searchsorted(sig_ts.normalize()))
            if signal_idx >= len(df_hist):
                continue

            # ── Bias (bullish varsayılan) ─────────────────────────────────────
            bias = str(sig.get('bias', 'bullish') or 'bullish').lower()
            is_bullish = 'bear' not in bias

            # ── Gerçekçi giriş: sinyal kapanışından sonraki işlem yapılabilir açılış ──
            _entry_info = resolve_next_open_entry(
                df_hist, sig['scan_date'], bias=bias,
                apply_bist_limit=(
                    not str(sig.get('category', '') or '').strip()
                    or 'BIST' in str(sig.get('category', '') or '').upper()
                    or str(sig['symbol']).upper().endswith('.IS')
                ),
                max_locked_sessions=3,
            )
            _entry_status = str(_entry_info.get('status', 'unknown'))
            entry_status_counts[_entry_status] += 1
            if _entry_info.get('entry_gap_pct') is not None:
                entry_gaps.append(float(_entry_info['entry_gap_pct']))
            _blank_db_row = {
                'signal_id': int(sig['id']), 'symbol': sig['symbol'],
                'scan_type': sig['scan_type'], 'signal_date': sig['scan_date'],
                'bias': bias, 'entry_price': _entry_info.get('entry_price'),
                'entry_date': _entry_info.get('entry_date'),
                'entry_gap_pct': _entry_info.get('entry_gap_pct'),
                'entry_delay': _entry_info.get('entry_delay'),
                'entry_status': _entry_status, 'stop_level': None,
                'ret_5g': None, 'ret_10g': None, 'ret_20g': None,
                'hit_5g': None, 'hit_10g': None, 'hit_20g': None,
                'stop_hit_5g': None, 'stop_hit_10g': None, 'stop_hit_20g': None,
                'max_gain_20g': None, 'max_loss_20g': None,
                'evaluated_at': datetime.now(TZ_ISTANBUL).strftime("%Y-%m-%d %H:%M:%S"),
            }
            if not _entry_status.startswith('filled'):
                db_rows.append(_blank_db_row)
                if _entry_status.startswith('pending'):
                    pending += 1
                continue

            entry = float(_entry_info['entry_price'])
            idx = int(_entry_info['entry_pos'])
            _entry_date_obj = pd.to_datetime(_entry_info['entry_date']).date()
            _ca_sym = str(sig['symbol']).replace('.IS', '').replace('.is', '').upper()
            # Son ~4 işlem gününde signal_returns yalnız T+1 taşıyabilir; bu normaldir.
            # Sinyal 5 seansa ulaşınca gece betiği geri dönerek derin vadeleri de yazar.
            if idx + min_fwd - 1 >= len(df_hist):
                pending += 1
                db_rows.append(_blank_db_row)
                continue

            # ── Stop seviyesi + geçmiş bölünme bazını koruma ─────────────────
            stop = sig.get('stop_level')
            stop = float(stop) if stop and not pd.isna(stop) and float(stop) > 0 else None
            _stored_entry = sig.get('entry_price')
            _stored_entry = (
                float(_stored_entry)
                if _stored_entry is not None and not pd.isna(_stored_entry) and float(_stored_entry) > 0
                else None
            )
            if _stored_entry and stop:
                _pq_kapanis = float(df_hist['Close'].iloc[signal_idx])
                _ayrisma = max(_stored_entry, _pq_kapanis) / min(_stored_entry, _pq_kapanis)
                if _ayrisma > TUTARSIZLIK_ORAN:
                    stop *= _pq_kapanis / _stored_entry
                    tutarsiz_bazlanan += 1

            # ── KURUMSAL İŞLEM BEKÇİSİ (15 Tem 2026) ─────────────────────────
            # Bölünme/bedelsiz penceredeyse fiyat serisi süreksiz → o pencerenin
            # getirisi/stop'u ÖLÇÜLEMEZ. Aşağıda etkilenen alanlar None bırakılır.
            _kur_idx = kurumsal_islem_indeksleri(df_hist)
            _split_i = ilk_kurumsal_islem(_kur_idx, idx - 1, idx + max_fwd - 1)
            if _split_i is not None:
                kurumsal_atlanan += 1
            if _split_i == idx:
                # Giriş seansındaki %15+ fiyat kırılması BIST'te işlem hareketi değil,
                # bölünme/bedelsiz baz değişimidir. "Alındı" etiketi vermek yanıltıcı olur.
                entry_status_counts[_entry_status] = max(
                    0, entry_status_counts[_entry_status] - 1
                )
                entry_status_counts['excluded_corporate_action'] += 1
                _blank_db_row['entry_status'] = 'excluded_corporate_action'
                db_rows.append(_blank_db_row)
                continue

            # ── Pencere boyunca fiyat serisi ─────────────────────────────────
            window_slice = df_hist.iloc[idx: idx + max_fwd]
            has_low      = 'Low'  in window_slice.columns
            has_high     = 'High' in window_slice.columns

            # Max gain / max loss (tüm 20g penceresi) — bölünme varsa ölçülemez
            if _split_i is not None:
                max_gain_20g = None
                max_loss_20g = None
            elif has_high and has_low and len(window_slice) > 1:
                max_high   = float(window_slice['High'].max())
                min_low    = float(window_slice['Low'].min())
                max_gain_20g = round((max_high - entry) / entry * 100, 2)
                max_loss_20g = round((min_low  - entry) / entry * 100, 2)
            else:
                max_gain_20g = None
                max_loss_20g = None

            db_row = dict(_blank_db_row)
            db_row.update({
                'entry_price': round(entry, 4),
                'entry_date': _entry_info['entry_date'],
                'entry_gap_pct': _entry_info.get('entry_gap_pct'),
                'entry_delay': _entry_info.get('entry_delay'),
                'entry_status': _entry_status,
                'stop_level': round(stop, 4) if stop else None,
                'max_gain_20g': max_gain_20g,
                'max_loss_20g': max_loss_20g,
            })

            result_row = {
                'symbol':      sig['symbol'],
                'scan_type':   sig['scan_type'],
                'signal_date': sig['scan_date'],
                'entry_date':  _entry_info['entry_date'],
                'entry_gap_pct': _entry_info.get('entry_gap_pct'),
                'entry_delay': _entry_info.get('entry_delay'),
                'entry_status': _entry_status,
                'bias':        bias,
                'entry':       round(entry, 4),
                'stop':        stop,
                'max_gain_20g': max_gain_20g,
                'max_loss_20g': max_loss_20g,
            }

            # ── XU100 index satırını bir kez bul (tüm pencereler için) ─────────
            xu100_idx = None
            if df_xu100 is not None and not df_xu100.empty:
                xu100_entry_ts = pd.Timestamp(_entry_info['entry_date'])
                xu100_idx = int(df_xu100.index.searchsorted(xu100_entry_ts))
                if xu100_idx >= len(df_xu100):
                    xu100_idx = None

            for fwd in forward_windows:
                # 5 seans tutma = giriş seansı dahil beşinci kapanış.
                f_idx = idx + fwd - 1
                if f_idx >= len(df_hist):
                    continue

                # Bölünme bu pencerenin İÇİNDE mi? (kısa pencere temizse korunur —
                # 15. günde bölünme varsa 5g/10g geçerli, 20g ölçülemez)
                if _split_i is not None and _split_i <= f_idx:
                    continue

                f_price = float(df_hist['Close'].iloc[f_idx])
                ret     = round((f_price - entry) / entry * 100, 2)

                # İş #8: TEMETTÜ (scan_date, forward] penceresindeyse ham getiri mekanik
                # düşüşle eksik → temettü oranını GERİ EKLE (holder nakit temettüyü aldı).
                # Bölünme (type='bolunme') buraya girmez — o zaten %15 bekçisiyle NULL'lanır.
                _fwd_date = df_hist.index[f_idx].date()
                for _ev in _ca_events.get(_ca_sym, []):
                    if _ev.get('type') != 'temettu':
                        continue
                    try:
                        _ed = datetime.strptime(str(_ev['date'])[:10], '%Y-%m-%d').date()
                    except Exception:
                        continue
                    if _entry_date_obj < _ed <= _fwd_date:
                        ret = round(ret + (float(_ev['factor']) - 1.0) * 100, 2)
                        temettu_ayarlanan += 1

                # Bias-corrected hit
                if is_bullish:
                    hit = 1 if ret > 0 else 0
                else:
                    hit = 1 if ret < 0 else 0  # bearish sinyal → aşağı = başarı

                # Stop hit: pencere boyunca Low, stop'a değdi mi?
                stop_hit = None
                if stop and has_low:
                    fwd_slice = df_hist.iloc[idx: f_idx + 1]
                    if is_bullish:
                        stop_hit = 1 if float(fwd_slice['Low'].min()) <= stop else 0
                    else:
                        # Bearish stop = stop yukarıda
                        stop_hit = 1 if float(fwd_slice['High'].max()) >= stop else 0

                # ── XU100 aynı pencere getirisi + alpha ─────────────────────
                xu100_ret = None
                alpha     = None
                if xu100_idx is not None:
                    xu100_f = xu100_idx + fwd - 1
                    if xu100_f < len(df_xu100):
                        xu100_entry_p = float(df_xu100['Open'].iloc[xu100_idx])
                        xu100_exit_p  = float(df_xu100['Close'].iloc[xu100_f])
                        if xu100_entry_p > 0:
                            xu100_ret = round((xu100_exit_p - xu100_entry_p) / xu100_entry_p * 100, 2)
                            alpha     = round(ret - xu100_ret, 2)

                result_row[f'ret_{fwd}g']      = ret
                result_row[f'hit_{fwd}g']       = hit
                result_row[f'stop_hit_{fwd}g']  = stop_hit
                result_row[f'xu100_{fwd}g']     = xu100_ret
                result_row[f'alpha_{fwd}g']     = alpha

                db_row[f'ret_{fwd}g']  = ret
                db_row[f'hit_{fwd}g']  = hit
                db_row[f'stop_hit_{fwd}g'] = stop_hit

            results.append(result_row)
            db_rows.append(db_row)   # döngüde YAZMA YOK → yavaş parquet okumaları boyunca DB kilidi tutulmaz

            # ── İDEAL VADE: gün 1..MAXD_VADE getiri + alpha (bias-düzeltmeli) ──
            # Kurumsal işlem bekçisi: bölünme gününden İTİBAREN seri süreksiz →
            # o günden sonraki vadeler ölçülemez, döngü kırılır (öncesi geçerli).
            _split_vade = ilk_kurumsal_islem(_kur_idx, idx - 1, idx + MAXD_VADE - 1)
            for dd in range(1, MAXD_VADE + 1):
                v_idx = idx + dd - 1
                if v_idx >= len(df_hist):
                    break
                if _split_vade is not None and _split_vade <= v_idx:
                    break
                v_price = float(df_hist['Close'].iloc[v_idx])
                v_raw_ret = (v_price - entry) / entry * 100
                _v_date = df_hist.index[v_idx].date()
                for _ev in _ca_events.get(_ca_sym, []):
                    if _ev.get('type') != 'temettu':
                        continue
                    try:
                        _ed = datetime.strptime(str(_ev['date'])[:10], '%Y-%m-%d').date()
                    except Exception:
                        continue
                    if _entry_date_obj < _ed <= _v_date:
                        v_raw_ret += (float(_ev['factor']) - 1.0) * 100
                if dd <= 20:
                    daily_return_rows.append((
                        int(sig['id']), sig['scan_type'], sig['symbol'], sig['scan_date'],
                        round(entry, 4), dd, v_price, round(v_raw_ret, 4),
                        sig.get('category', ''),
                    ))
                v_ret = v_raw_ret
                if not is_bullish:
                    v_ret = -v_ret  # bearish sinyal → düşüş = kâr
                v_alpha = None
                if xu100_idx is not None:
                    v_xf = xu100_idx + dd - 1
                    if v_xf < len(df_xu100):
                        v_xe = float(df_xu100['Open'].iloc[xu100_idx])
                        v_xx = float(df_xu100['Close'].iloc[v_xf])
                        if v_xe > 0:
                            v_xr = (v_xx - v_xe) / v_xe * 100
                            if not is_bullish:
                                v_xr = -v_xr
                            v_alpha = v_ret - v_xr
                bucket = vade_accum[sig['scan_type']][dd]
                bucket['rets'].append(v_ret)
                if v_alpha is not None:
                    bucket['alphas'].append(v_alpha)

        except Exception:
            continue

    # 21 Tem 2026 — KİLİT KÖK ÇÖZÜMÜ. Eskiden upsert_result döngü İÇİNDE çağrılıyor, tek commit döngü
    # SONUNDA yapılıyordu → binlerce sinyallik TEK yazma işlemi, yavaş parquet okumaları boyunca (dakikalar)
    # SQLite yazma kilidini elde tutuyordu. patron.db WAL değil (scp/OneDrive gerekçesi) → yazan kilit TÜM
    # okumaları bloke eder → 19:30 backtest'i çalışırken uygulama ilk açılışta DB okumalarında bekleyip BOŞ
    # kalıyordu. Çözüm: hesap döngüsü hiç yazmaz (kilit yok); yazma burada, parquet I/O'suz HIZLI fazda +
    # her 500 satırda commit → kilit yalnız kısa aralıklarla, aralarda uygulama rahatça okur.
    for _wi, _r in enumerate(db_rows):
        try:
            upsert_result(conn, _r)
        except Exception:
            continue
        if (_wi + 1) % 500 == 0:
            conn.commit()   # kilidi periyodik bırak → uygulamanın okuma penceresi
    conn.commit()
    _daily_sql = """
        INSERT INTO signal_returns(
            signal_id, scan_type, symbol, signal_date, entry_price,
            day_offset, close_price, return_pct, category
        ) VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(signal_id, day_offset) DO UPDATE SET
            entry_price=excluded.entry_price,
            close_price=excluded.close_price,
            return_pct=excluded.return_pct,
            category=excluded.category
    """
    for _di in range(0, len(daily_return_rows), 5000):
        conn.executemany(_daily_sql, daily_return_rows[_di:_di + 5000])
        conn.commit()
    conn.close()

    if kurumsal_atlanan:
        print(f"  → 🛡 Kurumsal işlem bekçisi: {kurumsal_atlanan} sinyalin penceresinde "
              f"bölünme/bedelsiz var → etkilenen vadeler ölçülmedi (NULL)")
    if tutarsiz_bazlanan:
        print(f"  → 🛡 Baz uyumu bekçisi: {tutarsiz_bazlanan} sinyalin girişi parquet "
              f"bazına taşındı (DB girişi bölünme öncesi = bayat) → getiri artık ölçülüyor")
    if temettu_ayarlanan:
        print(f"  → 💰 Temettü düzeltmesi (İş #8): {temettu_ayarlanan} vade-getirisine "
              f"pencereye düşen temettü geri eklendi (oran-yöntemi takvimi)")
    elif not _ca_events:
        print(f"  → 💰 Temettü düzeltmesi: kurumsal_takvim cache BOŞ — önce "
              f"`python kurumsal_takvim.py --precompute` koş (aksi halde temettü düzeltmesi devre dışı)")

    df = pd.DataFrame(results)
    if entry_status_counts:
        _status_text = ", ".join(f"{k}={v}" for k, v in sorted(entry_status_counts.items()))
        print(f"  → Gerçek giriş karnesi: {_status_text}")
    return df, {'pending': pending, 'evaluated': len(df),
                'kurumsal_atlanan': kurumsal_atlanan,
                'tutarsiz_bazlanan': tutarsiz_bazlanan,
                'entry_status_counts': dict(entry_status_counts),
                'daily_return_rows': len(daily_return_rows),
                'avg_entry_gap_pct': (
                    round(sum(entry_gaps) / len(entry_gaps), 3) if entry_gaps else None
                )}, vade_accum


def summarize(df):
    """scan_type bazında zengin özet.

    Ham fiyat getirisi raporda aynen korunur. Ayı yönlü uyarılarda kazanma/kaybetme,
    beklenti ve kâr faktörü ise sinyal yönüne çevrilerek hesaplanır; böylece D4/D5
    düşüş başarısı hiçbir zaman boğa getirisi gibi görünmez.
    """
    if df is None or df.empty:
        return []

    out = []
    for scan_type, grp in df.groupby('scan_type'):
        _bias_values = (grp['bias'].fillna('bullish').astype(str).str.lower()
                        if 'bias' in grp.columns else pd.Series(dtype=str))
        _bearish_votes = int(_bias_values.str.contains('bear', na=False).sum())
        is_bearish = scan_type in ('er_D4', 'er_D5') or (
            len(_bias_values) > 0 and _bearish_votes > len(_bias_values) / 2
        )
        row = {
            'scan_type':     scan_type,
            'label':         label_for_scan_type(scan_type),
            'category':      category_for_scan_type(scan_type),
            'bias':          'bearish' if is_bearish else 'bullish',
            'total_signals': len(grp),
        }

        for fwd in FORWARD_WINDOWS:
            hcol  = f'hit_{fwd}g'
            rcol  = f'ret_{fwd}g'
            scol  = f'stop_hit_{fwd}g'

            hits  = grp[hcol].dropna() if hcol in grp.columns else pd.Series(dtype=float)
            rets  = grp[rcol].dropna() if rcol in grp.columns else pd.Series(dtype=float)
            stops = grp[scol].dropna() if scol in grp.columns else pd.Series(dtype=float)

            if len(hits) >= 3:
                hit_pct     = round(float(hits.mean()) * 100, 1)
                avg_ret     = round(float(rets.mean()), 2)
                std_dev     = round(float(rets.std()), 2) if len(rets) >= 2 else None

                # Yönsel getiri: boğada yükseliş, ayıda düşüş kazançtır.
                directional_rets = -rets if is_bearish else rets
                directional_avg  = round(float(directional_rets.mean()), 2)
                pos_rets    = directional_rets[directional_rets > 0]
                neg_rets    = directional_rets[directional_rets <= 0]
                avg_win     = round(float(pos_rets.mean()), 2) if len(pos_rets) > 0 else 0.0
                avg_loss    = round(float(neg_rets.mean()), 2) if len(neg_rets) > 0 else 0.0

                # Beklenti doğrudan yönsel getirinin ortalamasıdır. Bu, hit oranı ile
                # kazanç/kayıp kümelerinin aynı yön tanımını kullanmasını garanti eder.
                expectancy  = directional_avg

                # Profit Factor: toplam kazanç / toplam kayıp (abs)
                gross_profit   = float(pos_rets.sum()) if len(pos_rets) > 0 else 0.0
                gross_loss_abs = float(abs(neg_rets.sum())) if len(neg_rets) > 0 else 0.0
                profit_factor  = round(gross_profit / gross_loss_abs, 2) if gross_loss_abs > 0 else None

                stop_hit_pct = round(float(stops.mean()) * 100, 1) if len(stops) >= 3 else None

                # Alpha vs XU100
                acol   = f'alpha_{fwd}g'
                alphas = grp[acol].dropna() if acol in grp.columns else pd.Series(dtype=float)
                alpha_avg = round(float(alphas.mean()), 2) if len(alphas) >= 3 else None
                directional_alpha = (-alphas if is_bearish else alphas)
                directional_alpha_avg = (
                    round(float(directional_alpha.mean()), 2)
                    if len(directional_alpha) >= 3 else None
                )

                xu100col  = f'xu100_{fwd}g'
                xu100s    = grp[xu100col].dropna() if xu100col in grp.columns else pd.Series(dtype=float)
                xu100_avg = round(float(xu100s.mean()), 2) if len(xu100s) >= 3 else None

                row[f'hit_{fwd}g_pct']       = hit_pct
                row[f'avg_{fwd}g_ret']       = avg_ret
                row[f'directional_avg_{fwd}g_ret'] = directional_avg
                row[f'std_{fwd}g']           = std_dev
                row[f'avg_win_{fwd}g']       = avg_win
                row[f'avg_loss_{fwd}g']      = avg_loss
                row[f'expectancy_{fwd}g']    = expectancy
                row[f'profit_factor_{fwd}g'] = profit_factor
                row[f'stop_hit_{fwd}g_pct']  = stop_hit_pct
                row[f'eval_{fwd}g']          = int(len(hits))
                row[f'alpha_{fwd}g']         = alpha_avg
                row[f'directional_alpha_{fwd}g'] = directional_alpha_avg
                row[f'xu100_avg_{fwd}g']     = xu100_avg
            else:
                row[f'hit_{fwd}g_pct']       = None
                row[f'avg_{fwd}g_ret']       = None
                row[f'directional_avg_{fwd}g_ret'] = None
                row[f'std_{fwd}g']           = None
                row[f'avg_win_{fwd}g']       = None
                row[f'avg_loss_{fwd}g']      = None
                row[f'expectancy_{fwd}g']    = None
                row[f'profit_factor_{fwd}g'] = None
                row[f'stop_hit_{fwd}g_pct']  = None
                row[f'eval_{fwd}g']          = int(len(hits))
                row[f'alpha_{fwd}g']         = None
                row[f'directional_alpha_{fwd}g'] = None
                row[f'xu100_avg_{fwd}g']     = None

        out.append(row)

    # Expectancy 10G'ye göre sırala (en güvenilir pencere)
    out.sort(
        key=lambda r: (r['expectancy_10g'] if r['expectancy_10g'] is not None else -999),
        reverse=True
    )
    return out


def compute_ideal_vade(vade_accum):
    """
    Her scan_type için alpha-zirvesi gününü (sinyalin piyasaya üstünlüğü en yüksek)
    bulup ideal vade ARALIĞI + getirisi + edge bayrağını döner.
    Döner: {scan_type: {ideal_lo, ideal_hi, ideal_ret, ideal_alpha, ideal_hit, ideal_n, has_edge}}
    """
    out = {}
    for scan_type, days in vade_accum.items():
        per_day = []
        for d in sorted(days.keys()):
            rets = days[d]['rets']
            if len(rets) < MIN_N_VADE:
                continue
            s = pd.Series(rets, dtype=float)
            pos = s[s > 0]; neg = s[s <= 0]
            hit = round(len(pos) / len(s) * 100, 1)
            avg = round(float(s.mean()), 2)
            win = round(float(pos.mean()), 2) if len(pos) else 0.0
            loss = round(float(neg.mean()), 2) if len(neg) else 0.0
            alphas = days[d]['alphas']
            a = round(float(pd.Series(alphas).mean()), 2) if alphas else 0.0
            per_day.append({'d': d, 'n': len(s), 'hit': hit, 'avg': avg,
                            'a': a, 'win': win, 'loss': loss})
        if not per_day:
            continue

        # ── ÖRNEK-DESTEK KAPISI: kuyruğa-yapışmayı önle ─────────────────────────────
        # Sadece sinyallerin çoğunun ULAŞTIĞI vadeleri peak adayı say. Eriyen kuyruktaki
        # (n/nmax düşük) birkaç survivor'ın şişirdiği sahte α-zirvesi ideal vadeyi çalamaz.
        _nmax = max(p['n'] for p in per_day)
        _support = [p for p in per_day if p['n'] >= NFRAC_VADE * _nmax]
        if _support:                      # destekli gün varsa onlarla çalış (yoksa eski davranış)
            per_day = _support

        # ── KARARLI ZİRVE: α eğrisini 3g pencerede yumuşat → gün-gün argmax sıçramasını önle ──
        # Sonra zirveye yakın (tolerans içi) EN ERKEN günü seç: "aynı piyasa farkını en kısa
        # sürede yakaladığın vade". Uzun vadede örnek azalıp α şiştiği için saf argmax kararsız.
        per_day.sort(key=lambda x: x['d'])
        _aseq = [p['a'] for p in per_day]
        for i, p in enumerate(per_day):
            _l = max(0, i - 1); _h = min(len(per_day), i + 2)
            p['a_s'] = sum(_aseq[_l:_h]) / (_h - _l)   # 3g merkezli yumuşatma
        peak_s = max(p['a_s'] for p in per_day)
        TOL = 0.3
        _cand = [p for p in per_day if p['a_s'] >= peak_s - TOL]
        peak = min(_cand, key=lambda x: x['d'])        # tolerans içi en erken gün
        has_edge = peak_s >= EDGE_MIN_ALPHA            # +%0.1 gibi gürültü "geçiyor" sayılmasın

        # Aralık: peak çevresinde yumuşak-α platosu kaldığı sürece genişlet
        thr = max(0.0, peak_s - TOL)
        by_d = {p['d']: p for p in per_day}
        lo = hi = peak['d']
        while (lo - 1) in by_d and by_d[lo - 1]['a_s'] >= thr:
            lo -= 1
        while (hi + 1) in by_d and by_d[hi + 1]['a_s'] >= thr:
            hi += 1

        out[scan_type] = {
            'ideal_lo':    lo,
            'ideal_hi':    hi,
            'ideal_day':   peak['d'],      # α-zirvesi tek gün (sütun alt-satırı için)
            'ideal_ret':   peak['avg'],
            'ideal_win':   peak['win'],    # o vadede ort. kazanç
            'ideal_loss':  peak['loss'],   # o vadede ort. kayıp
            'ideal_alpha': peak['a'],      # o vadede piyasa farkı
            'ideal_hit':   peak['hit'],
            'ideal_n':     peak['n'],
            'has_edge':    has_edge,
        }
    return out


def db_stats():
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30)
        c    = conn.cursor()
        total   = c.execute("SELECT COUNT(*) FROM scan_signals").fetchone()[0]
        first   = c.execute("SELECT MIN(scan_date) FROM scan_signals").fetchone()[0]
        last    = c.execute("SELECT MAX(scan_date) FROM scan_signals").fetchone()[0]
        types   = c.execute("SELECT COUNT(DISTINCT scan_type) FROM scan_signals").fetchone()[0]
        symbols = c.execute("SELECT COUNT(DISTINCT symbol) FROM scan_signals").fetchone()[0]

        # signal_results tablosu
        try:
            res_count = c.execute("SELECT COUNT(*) FROM signal_results").fetchone()[0]
        except Exception:
            res_count = 0

        conn.close()
        return {
            'total_signals':    total,
            'unique_scan_types': types,
            'unique_symbols':   symbols,
            'first_date':       first,
            'last_date':        last,
            'evaluated_stored': res_count,
        }
    except Exception as e:
        return {'error': str(e)}


def main():
    t0      = time.time()
    now_str = datetime.now(TZ_ISTANBUL).strftime("%Y-%m-%d %H:%M:%S")
    print("╔════════════════════════════════════════════════════════════╗")
    print(f"║  SMR Backtest Runner v2 — {now_str}           ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print()

    if not DB_FILE.exists():
        print(f"❌ HATA: patron.db bulunamadı ({DB_FILE})")
        sys.exit(1)
    if not PARQUET_DIR.exists():
        print(f"❌ HATA: veriler/ klasörü bulunamadı ({PARQUET_DIR})")
        sys.exit(1)

    print("📊 DB istatistikleri...")
    stats = db_stats()
    print(f"   Toplam sinyal    : {stats.get('total_signals', '?')}")
    print(f"   Tarama tipi      : {stats.get('unique_scan_types', '?')}")
    print(f"   Sembol           : {stats.get('unique_symbols', '?')}")
    print(f"   Tarih aralığı    : {stats.get('first_date', '—')} → {stats.get('last_date', '—')}")
    print(f"   Kayıtlı sonuç    : {stats.get('evaluated_stored', 0)}")
    print()

    print(f"🔍 Değerlendirme başlıyor (lookback={LOOKBACK_DAYS}g)...")
    df, eval_meta, vade_accum = evaluate_signals(LOOKBACK_DAYS)
    print(f"   Değerlendirildi  : {eval_meta['evaluated']}")
    print(f"   Bekleyen         : {eval_meta['pending']}")
    print()

    print("📈 Özet çıkarılıyor...")
    summary = summarize(df)
    print(f"   {len(summary)} tarama tipinde sonuç var.")

    # İdeal vade (alpha-zirvesi) → her özet satırına işle
    ideal = compute_ideal_vade(vade_accum)
    _iv_n = 0
    for r in summary:
        iv = ideal.get(r['scan_type'])
        if iv:
            r.update(iv)
            _iv_n += 1
    print(f"   {_iv_n} taramada ideal vade hesaplandı.")
    print()

    # Boğa taramaları ve düşüş uyarıları ayrı liglerde sıralanır.
    bullish_summary = [r for r in summary if r.get('bias') != 'bearish']
    bearish_warnings = [
        r for r in summary
        if r.get('bias') == 'bearish' and r.get('expectancy_10g') is not None
    ]
    bearish_warnings.sort(key=lambda r: r['expectancy_10g'], reverse=True)

    # Top 5 — yalnız boğa taramaları, expectancy 10G
    top5  = [r for r in bullish_summary if r.get('expectancy_10g') is not None and r['expectancy_10g'] > 0][:5]
    # Worst 5 — yalnız boğa taramaları
    worst = [r for r in bullish_summary if r.get('expectancy_10g') is not None and r['expectancy_10g'] < 0][-5:]
    worst.reverse()
    # Kategori bazlı en iyi
    best_per_cat = {}
    for cat in ('A', 'B', 'C'):
        cat_items = [r for r in summary
                     if r['category'] == cat and r.get('expectancy_10g') is not None]
        if cat_items:
            best_per_cat[cat] = {
                'scan_type':   cat_items[0]['scan_type'],
                'label':       cat_items[0]['label'],
                'hit_10g':     cat_items[0]['hit_10g_pct'],
                'expectancy':  cat_items[0]['expectancy_10g'],
                'std':         cat_items[0]['std_10g'],
            }

    duration = round(time.time() - t0, 2)

    payload = {
        'generated_at':      now_str,
        'duration_sec':      duration,
        'lookback_days':     LOOKBACK_DAYS,
        'forward_windows':   FORWARD_WINDOWS,
        'db_stats':          stats,
        'eval_meta':         eval_meta,
        'summary':           summary,
        'top5_by_expectancy': top5,
        'worst_5':           worst,
        'bearish_warnings':   bearish_warnings,
        'best_per_category': best_per_cat,
    }

    OUTPUT_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding='utf-8'
    )
    print(f"✅ Yazıldı  : {OUTPUT_FILE}")
    print(f"✅ DB kayıt : patron.db → signal_results")
    print(f"⏱  Süre    : {duration} saniye")
    print()

    if top5:
        print("🥇 EN BAŞARILI (Expectancy 10G):")
        for r in top5[:5]:
            print(
                f"   • {r['label']}: "
                f"hit %{r['hit_10g_pct']} | "
                f"exp {r['expectancy_10g']:+.2f}% | "
                f"std ±{r['std_10g']}% | "
                f"stop %{r.get('stop_hit_10g_pct', '?')} | "
                f"{r['eval_10g']} sinyal"
            )
    print()
    if worst:
        print("⚠  EN ZAYIF (Expectancy 10G):")
        for r in worst[:3]:
            print(f"   • {r['label']}: exp {r['expectancy_10g']:+.2f}%")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⛔ İptal edildi.")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ HATA: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
