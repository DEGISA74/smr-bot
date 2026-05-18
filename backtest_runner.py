"""
SMR — Backtest Runner (standalone)

Çıktı: backtest_results.json
Kaynak: signals.db (scan_signals) + veriler/*.parquet

Kullanım:
    python backtest_runner.py
    veya: run_backtest.bat (double-click)
    veya: Windows Task Scheduler (her gün 03:00)

app.py'den import etmez — kırılmaz. Streamlit bağımlılığı yok.
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
BASE_DIR     = Path(__file__).parent
DB_FILE      = BASE_DIR / "patron.db"   # app.py ile aynı DB
PARQUET_DIR  = BASE_DIR / "veriler"
OUTPUT_FILE  = BASE_DIR / "backtest_results.json"
LOOKBACK_DAYS    = 90
FORWARD_WINDOWS  = [5, 10, 20]
TZ_ISTANBUL  = pytz.timezone("Europe/Istanbul")

# ─── SENARYO ETİKETLERİ (app.py ile aynı kalmalı) ────────────────────────────
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
}


def label_for_scan_type(scan_type: str) -> str:
    """scan_type → kullanıcı dostu etiket"""
    if scan_type in CLASSIC_LABELS:
        return CLASSIC_LABELS[scan_type]
    if scan_type.startswith('er_'):
        sid = scan_type[3:]
        nm = SCENARIO_NAMES.get(sid, sid)
        cat = sid[0] if sid and sid[0] in CAT_ICONS else '•'
        return f"{CAT_ICONS.get(cat, '•')} ER {sid} · {nm}"
    return scan_type


def category_for_scan_type(scan_type: str) -> str:
    """A / B / C / D / classic"""
    if scan_type.startswith('er_'):
        sid = scan_type[3:]
        return sid[0] if sid and sid[0] in 'ABCD' else 'X'
    return 'CLASSIC'


def load_parquet(symbol: str):
    """veriler/{symbol}.IS_1d.parquet veya {symbol}_1d.parquet oku"""
    # BIST hisseleri .IS uzantılı
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


def evaluate_signals(lookback_days=LOOKBACK_DAYS, forward_windows=None):
    """scan_signals'ı tarayıp her sinyalin +N gün getirisini hesapla."""
    if forward_windows is None:
        forward_windows = FORWARD_WINDOWS

    conn = sqlite3.connect(DB_FILE)
    signals = pd.read_sql(
        "SELECT * FROM scan_signals WHERE scan_date >= date('now', ?)",
        conn,
        params=(f'-{lookback_days} days',)
    )
    conn.close()

    if signals.empty:
        return pd.DataFrame(), {'pending': 0, 'evaluated': 0}

    today    = datetime.now(TZ_ISTANBUL).date()
    min_fwd  = min(forward_windows)
    results  = []
    pending  = 0
    total    = len(signals)

    print(f"  → {total} sinyal değerlendiriliyor (min {min_fwd}g geçmiş gerekir)...")
    last_pct = -1
    for i, (_, sig) in enumerate(signals.iterrows()):
        # Progress (her %10'da bir)
        pct = int((i + 1) / total * 100)
        if pct != last_pct and pct % 10 == 0:
            print(f"    [{pct}%] {i+1}/{total}")
            last_pct = pct

        try:
            sig_date = pd.to_datetime(sig['scan_date']).date()
            days_elapsed = (today - sig_date).days
            if days_elapsed < min_fwd:
                pending += 1
                continue

            df_hist = load_parquet(sig['symbol'])
            if df_hist is None or df_hist.empty:
                continue

            sig_ts = pd.Timestamp(sig['scan_date'])
            idx = df_hist.index.searchsorted(sig_ts)
            if idx >= len(df_hist):
                continue

            entry = sig.get('entry_price')
            if pd.isna(entry) or not entry:
                entry = float(df_hist['Close'].iloc[idx])
            else:
                entry = float(entry)
            if entry == 0:
                continue

            row = {
                'symbol':    sig['symbol'],
                'scan_type': sig['scan_type'],
                'signal_date': sig['scan_date'],
                'entry':     round(entry, 4),
            }
            for fwd in forward_windows:
                f_idx = idx + fwd
                if f_idx < len(df_hist):
                    f_price = float(df_hist['Close'].iloc[f_idx])
                    ret = (f_price - entry) / entry * 100
                    row[f'ret_{fwd}g']  = round(ret, 2)
                    row[f'hit_{fwd}g']  = 1 if ret > 0 else 0
                else:
                    row[f'ret_{fwd}g']  = None
                    row[f'hit_{fwd}g']  = None
            results.append(row)
        except Exception as e:
            continue

    df = pd.DataFrame(results)
    return df, {'pending': pending, 'evaluated': len(df)}


def summarize(df):
    """scan_type bazında özet (hit rate, ort. getiri, sinyal sayısı)"""
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
            hits = grp[f'hit_{fwd}g'].dropna()
            rets = grp[f'ret_{fwd}g'].dropna()
            if len(hits) >= 3:
                row[f'hit_{fwd}g_pct']  = round(float(hits.mean()) * 100, 1)
                row[f'avg_{fwd}g_ret']  = round(float(rets.mean()), 2)
                row[f'eval_{fwd}g']     = int(len(hits))
            else:
                row[f'hit_{fwd}g_pct']  = None
                row[f'avg_{fwd}g_ret']  = None
                row[f'eval_{fwd}g']     = int(len(hits))
        out.append(row)
    # Hit 20G'ye göre sırala (None'lar sona)
    out.sort(key=lambda r: (r['hit_20g_pct'] if r['hit_20g_pct'] is not None else -1), reverse=True)
    return out


def db_stats():
    """signals.db sağlık göstergesi"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        total = c.execute("SELECT COUNT(*) FROM scan_signals").fetchone()[0]
        first = c.execute("SELECT MIN(scan_date) FROM scan_signals").fetchone()[0]
        last  = c.execute("SELECT MAX(scan_date) FROM scan_signals").fetchone()[0]
        types = c.execute("SELECT COUNT(DISTINCT scan_type) FROM scan_signals").fetchone()[0]
        symbols = c.execute("SELECT COUNT(DISTINCT symbol) FROM scan_signals").fetchone()[0]
        conn.close()
        return {
            'total_signals': total,
            'unique_scan_types': types,
            'unique_symbols': symbols,
            'first_date': first,
            'last_date': last,
        }
    except Exception as e:
        return {'error': str(e)}


def main():
    t0 = time.time()
    now_str = datetime.now(TZ_ISTANBUL).strftime("%Y-%m-%d %H:%M:%S")
    print(f"╔════════════════════════════════════════════════════════════╗")
    print(f"║  SMR Backtest Runner — {now_str}              ║")
    print(f"╚════════════════════════════════════════════════════════════╝")
    print()

    if not DB_FILE.exists():
        print(f"❌ HATA: signals.db bulunamadı ({DB_FILE})")
        sys.exit(1)
    if not PARQUET_DIR.exists():
        print(f"❌ HATA: veriler/ klasörü bulunamadı ({PARQUET_DIR})")
        sys.exit(1)

    print(f"📊 DB stats çekiliyor...")
    stats = db_stats()
    print(f"   Toplam sinyal: {stats.get('total_signals', '?')}")
    print(f"   Tarama tipi:   {stats.get('unique_scan_types', '?')}")
    print(f"   Sembol:        {stats.get('unique_symbols', '?')}")
    print(f"   Tarih aralığı: {stats.get('first_date', '—')} → {stats.get('last_date', '—')}")
    print()

    print(f"🔍 Sinyaller değerlendiriliyor (lookback={LOOKBACK_DAYS}g)...")
    df, eval_meta = evaluate_signals(LOOKBACK_DAYS)
    print(f"   Değerlendirildi: {eval_meta['evaluated']}, Bekleyen: {eval_meta['pending']}")
    print()

    print(f"📈 scan_type bazında özet çıkarılıyor...")
    summary = summarize(df)
    print(f"   {len(summary)} tarama tipinde sonuç var.")
    print()

    # En başarılı 5 (Hit 20G > 0)
    top5 = [r for r in summary if r.get('hit_20g_pct') is not None and r['hit_20g_pct'] > 0][:5]
    # En başarısız (Hit 20G < 50)
    worst = [r for r in summary
             if r.get('hit_20g_pct') is not None and r['hit_20g_pct'] < 50][-5:]
    worst.reverse()
    # Kategori bazlı en iyi (sadece ER kategorileri)
    best_per_cat = {}
    for cat in ('A', 'B', 'C'):
        cat_items = [r for r in summary if r['category'] == cat and r.get('hit_20g_pct') is not None]
        if cat_items:
            best_per_cat[cat] = {
                'scan_type': cat_items[0]['scan_type'],
                'label':     cat_items[0]['label'],
                'hit_20g':   cat_items[0]['hit_20g_pct'],
                'avg_20g':   cat_items[0]['avg_20g_ret'],
            }

    duration = round(time.time() - t0, 2)

    payload = {
        'generated_at':  now_str,
        'duration_sec':  duration,
        'lookback_days': LOOKBACK_DAYS,
        'forward_windows': FORWARD_WINDOWS,
        'db_stats':      stats,
        'eval_meta':     eval_meta,
        'summary':       summary,
        'top5_by_hit20': top5,
        'worst_5':       worst,
        'best_per_category': best_per_cat,
    }

    OUTPUT_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding='utf-8'
    )
    print(f"✅ Yazıldı: {OUTPUT_FILE}")
    print(f"⏱  Süre: {duration} saniye")
    print()
    if top5:
        print(f"🥇 EN BAŞARILI 3 (20G hit rate):")
        for r in top5[:3]:
            print(f"   • {r['label']}: hit %{r['hit_20g_pct']} · ort +%{r['avg_20g_ret']} · {r['total_signals']} sinyal")


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
