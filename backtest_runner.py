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
import pandas as pd
import pytz

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
    conn.commit()


def upsert_result(conn, row: dict):
    """Bireysel sinyal sonucunu yaz (varsa güncelle)."""
    conn.execute("""
        INSERT INTO signal_results (
            signal_id, symbol, scan_type, signal_date, bias,
            entry_price, stop_level,
            ret_5g, ret_10g, ret_20g,
            hit_5g, hit_10g, hit_20g,
            stop_hit_5g, stop_hit_10g, stop_hit_20g,
            max_gain_20g, max_loss_20g, evaluated_at
        ) VALUES (
            :signal_id, :symbol, :scan_type, :signal_date, :bias,
            :entry_price, :stop_level,
            :ret_5g, :ret_10g, :ret_20g,
            :hit_5g, :hit_10g, :hit_20g,
            :stop_hit_5g, :stop_hit_10g, :stop_hit_20g,
            :max_gain_20g, :max_loss_20g, :evaluated_at
        )
        ON CONFLICT(signal_id) DO UPDATE SET
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

    conn = sqlite3.connect(DB_FILE)
    ensure_signal_results_table(conn)

    signals = pd.read_sql(
        "SELECT * FROM scan_signals WHERE scan_date >= date('now', ?)",
        conn,
        params=(f'-{lookback_days} days',)
    )

    today   = datetime.now(TZ_ISTANBUL).date()
    min_fwd = min(forward_windows)
    max_fwd = max(forward_windows)
    results = []
    pending = 0
    total   = len(signals)

    # ── XU100 benchmark — alpha hesabı için ─────────────────────────────────
    df_xu100 = load_xu100()
    print(f"  → XU100 benchmark: {'yüklendi' if df_xu100 is not None else 'bulunamadı'}")

    print(f"  → {total} sinyal değerlendiriliyor (min {min_fwd}g geçmiş gerekir)...")
    last_pct = -1

    for i, (_, sig) in enumerate(signals.iterrows()):
        pct = int((i + 1) / total * 100)
        if pct != last_pct and pct % 10 == 0:
            print(f"    [{pct}%] {i+1}/{total}")
            last_pct = pct

        try:
            sig_date     = pd.to_datetime(sig['scan_date']).date()
            days_elapsed = (today - sig_date).days
            if days_elapsed < min_fwd:
                pending += 1
                continue

            df_hist = load_parquet(sig['symbol'])
            if df_hist is None or df_hist.empty:
                continue

            sig_ts = pd.Timestamp(sig['scan_date'])
            idx    = df_hist.index.searchsorted(sig_ts)
            if idx >= len(df_hist):
                continue

            # ── Entry fiyatı ────────────────────────────────────────────────
            entry = sig.get('entry_price')
            if pd.isna(entry) or not entry:
                entry = float(df_hist['Close'].iloc[idx])
            else:
                entry = float(entry)
            if entry == 0:
                continue

            # ── Stop seviyesi ────────────────────────────────────────────────
            stop = sig.get('stop_level')
            stop = float(stop) if stop and not pd.isna(stop) and float(stop) > 0 else None

            # ── Bias (bullish varsayılan) ─────────────────────────────────────
            bias = str(sig.get('bias', 'bullish') or 'bullish').lower()
            is_bullish = 'bear' not in bias

            # ── Pencere boyunca fiyat serisi ─────────────────────────────────
            window_slice = df_hist.iloc[idx: idx + max_fwd + 1]
            has_low      = 'Low'  in window_slice.columns
            has_high     = 'High' in window_slice.columns

            # Max gain / max loss (tüm 20g penceresi)
            if has_high and has_low and len(window_slice) > 1:
                max_high   = float(window_slice['High'].max())
                min_low    = float(window_slice['Low'].min())
                max_gain_20g = round((max_high - entry) / entry * 100, 2)
                max_loss_20g = round((min_low  - entry) / entry * 100, 2)
            else:
                max_gain_20g = None
                max_loss_20g = None

            db_row = {
                'signal_id':   int(sig['id']),
                'symbol':      sig['symbol'],
                'scan_type':   sig['scan_type'],
                'signal_date': sig['scan_date'],
                'bias':        bias,
                'entry_price': round(entry, 4),
                'stop_level':  round(stop, 4) if stop else None,
                'ret_5g':      None, 'ret_10g': None, 'ret_20g': None,
                'hit_5g':      None, 'hit_10g': None, 'hit_20g': None,
                'stop_hit_5g': None, 'stop_hit_10g': None, 'stop_hit_20g': None,
                'max_gain_20g': max_gain_20g,
                'max_loss_20g': max_loss_20g,
                'evaluated_at': datetime.now(TZ_ISTANBUL).strftime("%Y-%m-%d %H:%M:%S"),
            }

            result_row = {
                'symbol':      sig['symbol'],
                'scan_type':   sig['scan_type'],
                'signal_date': sig['scan_date'],
                'bias':        bias,
                'entry':       round(entry, 4),
                'stop':        stop,
                'max_gain_20g': max_gain_20g,
                'max_loss_20g': max_loss_20g,
            }

            # ── XU100 index satırını bir kez bul (tüm pencereler için) ─────────
            xu100_idx = None
            if df_xu100 is not None and not df_xu100.empty:
                xu100_sig_ts = pd.Timestamp(sig['scan_date'])
                xu100_idx    = df_xu100.index.searchsorted(xu100_sig_ts)
                if xu100_idx >= len(df_xu100):
                    xu100_idx = None

            for fwd in forward_windows:
                f_idx = idx + fwd
                if f_idx >= len(df_hist):
                    continue

                f_price = float(df_hist['Close'].iloc[f_idx])
                ret     = round((f_price - entry) / entry * 100, 2)

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
                    xu100_f = xu100_idx + fwd
                    if xu100_f < len(df_xu100):
                        xu100_entry_p = float(df_xu100['Close'].iloc[xu100_idx])
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
            upsert_result(conn, db_row)

        except Exception:
            continue

    conn.commit()
    conn.close()

    df = pd.DataFrame(results)
    return df, {'pending': pending, 'evaluated': len(df)}


def summarize(df):
    """scan_type bazında zengin özet."""
    if df is None or df.empty:
        return []

    out = []
    for scan_type, grp in df.groupby('scan_type'):
        row = {
            'scan_type':     scan_type,
            'label':         label_for_scan_type(scan_type),
            'category':      category_for_scan_type(scan_type),
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

                pos_rets    = rets[rets > 0]
                neg_rets    = rets[rets <= 0]
                avg_win     = round(float(pos_rets.mean()), 2) if len(pos_rets) > 0 else 0.0
                avg_loss    = round(float(neg_rets.mean()), 2) if len(neg_rets) > 0 else 0.0

                # Expectancy: (hit% × avg_win) + (miss% × avg_loss)
                miss_pct    = 1.0 - (hit_pct / 100)
                expectancy  = round((hit_pct / 100) * avg_win + miss_pct * avg_loss, 2)

                stop_hit_pct = round(float(stops.mean()) * 100, 1) if len(stops) >= 3 else None

                # Alpha vs XU100
                acol   = f'alpha_{fwd}g'
                alphas = grp[acol].dropna() if acol in grp.columns else pd.Series(dtype=float)
                alpha_avg = round(float(alphas.mean()), 2) if len(alphas) >= 3 else None

                xu100col  = f'xu100_{fwd}g'
                xu100s    = grp[xu100col].dropna() if xu100col in grp.columns else pd.Series(dtype=float)
                xu100_avg = round(float(xu100s.mean()), 2) if len(xu100s) >= 3 else None

                row[f'hit_{fwd}g_pct']      = hit_pct
                row[f'avg_{fwd}g_ret']      = avg_ret
                row[f'std_{fwd}g']          = std_dev
                row[f'avg_win_{fwd}g']      = avg_win
                row[f'avg_loss_{fwd}g']     = avg_loss
                row[f'expectancy_{fwd}g']   = expectancy
                row[f'stop_hit_{fwd}g_pct'] = stop_hit_pct
                row[f'eval_{fwd}g']         = int(len(hits))
                row[f'alpha_{fwd}g']        = alpha_avg
                row[f'xu100_avg_{fwd}g']    = xu100_avg
            else:
                row[f'hit_{fwd}g_pct']      = None
                row[f'avg_{fwd}g_ret']      = None
                row[f'std_{fwd}g']          = None
                row[f'avg_win_{fwd}g']      = None
                row[f'avg_loss_{fwd}g']     = None
                row[f'expectancy_{fwd}g']   = None
                row[f'stop_hit_{fwd}g_pct'] = None
                row[f'eval_{fwd}g']         = int(len(hits))
                row[f'alpha_{fwd}g']        = None
                row[f'xu100_avg_{fwd}g']    = None

        out.append(row)

    # Expectancy 10G'ye göre sırala (en güvenilir pencere)
    out.sort(
        key=lambda r: (r['expectancy_10g'] if r['expectancy_10g'] is not None else -999),
        reverse=True
    )
    return out


def db_stats():
    try:
        conn = sqlite3.connect(DB_FILE)
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
    df, eval_meta = evaluate_signals(LOOKBACK_DAYS)
    print(f"   Değerlendirildi  : {eval_meta['evaluated']}")
    print(f"   Bekleyen         : {eval_meta['pending']}")
    print()

    print("📈 Özet çıkarılıyor...")
    summary = summarize(df)
    print(f"   {len(summary)} tarama tipinde sonuç var.")
    print()

    # Top 5 — expectancy 10G
    top5  = [r for r in summary if r.get('expectancy_10g') is not None and r['expectancy_10g'] > 0][:5]
    # Worst 5
    worst = [r for r in summary if r.get('expectancy_10g') is not None and r['expectancy_10g'] < 0][-5:]
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
