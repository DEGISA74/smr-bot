"""
TAVAN MOTORU TAM BACKTEST
60 iş günü × ~600 hisse — her gün motoru çalıştır, ertesi günün gerçek tavan
listesiyle karşılaştır. Recall + precision + rejim/kalıp/eşik analizi.

Skorlama mantığı tavan_engine.py'de (TEK KAYNAK) — bu backtest artık CANLI app.py
motorunu ölçer (confluence eşiği 30, aynı manipülasyon filtresi). Eskiden bu dosya
conf=50 ile kendi kopyasını ölçüyordu → "ölçülen ≠ yayınlanan" idi; düzeltildi.
"""
import pandas as pd
import glob, os, sys, io, warnings
import tavan_engine as te
warnings.filterwarnings('ignore')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

VERILER = 'veriler'
LOOKBACK_DAYS = 60                      # Backtest penceresi
TAVAN_ESIK = te.TAVAN_ESIK             # +%9.5+ = tavan
BUYUK_HAREKET_ESIK = te.BUYUK_HAREKET_ESIK  # +%5+ = büyük hareket
MIN_VOL_TL = te.MIN_VOL_TL

# ─── Veri yükle (bellekte tut) ───
print('Parquetler yükleniyor...')
ALL = {}
for f in glob.glob(f'{VERILER}/*.IS_1d.parquet'):
    tk = os.path.basename(f).replace('.IS_1d.parquet', '')
    if tk in ('XU100', 'XU030', 'XU050', 'XBANK', 'XUSIN', 'XUMAL'):
        continue
    try:
        df = pd.read_parquet(f)
        if len(df) >= 100:
            ALL[tk] = df
    except Exception:
        pass
xu = pd.read_parquet(f'{VERILER}/XU100.IS_1d.parquet')
print(f'{len(ALL)} hisse + XU100 yüklendi.')

# Hedef günler (T+1'i hesaplayabilmek için son günü ATLA)
ref = next(iter(ALL.values()))
all_dates = ref.index.tolist()
target_dates = all_dates[-LOOKBACK_DAYS - 1:-1]
print(f'Backtest pencere: {str(target_dates[0].date())} → {str(target_dates[-1].date())} ({len(target_dates)} gün)')


# ─── Backtest ───
def scan_one_day(target):
    """T günü kapanış → skor DataFrame'i (T+1 için aday). Motor = tavan_engine."""
    if target not in xu.index:
        return None, 'BILINMEZ', 0
    i_xu = xu.index.get_loc(target)
    rejim, chg = te.detect_rejim(xu['Close'], i_xu, lookback=10)
    agr = te.REJIM_AGIRLIK[rejim]
    rows = []
    for tk, df in ALL.items():
        if target not in df.index:
            continue
        i = df.index.get_loc(target)
        feat = te.features(df, i)
        if feat is None:
            continue
        if feat['vol_tl'] < MIN_VOL_TL:
            continue
        sc = te.score_row(feat, agr)
        rows.append({'tk': tk, 'skor': round(sc['skor'], 1), 'kat': sc['kat'],
                     'confluence_n': sc['confluence_n']})
    return pd.DataFrame(rows).sort_values('skor', ascending=False).reset_index(drop=True), rejim, round(chg, 2)


def get_real_movers(target_date, target_idx):
    """T+1 günündeki gerçek tavanlar ve büyük hareketler."""
    tavanlar = set()
    buyuk = set()
    next_date = all_dates[target_idx + 1]
    for tk, df in ALL.items():
        if next_date not in df.index:
            continue
        i = df.index.get_loc(next_date)
        if i < 1:
            continue
        pct = (df.iloc[i]['Close'] / df.iloc[i - 1]['Close'] - 1) * 100
        if pct >= TAVAN_ESIK:
            tavanlar.add(tk)
        if pct >= BUYUK_HAREKET_ESIK:
            buyuk.add(tk)
    return tavanlar, buyuk


# Ana döngü
print('\nBacktest çalışıyor...')
results = []
for ti, target in enumerate(target_dates):
    idx_in_ref = all_dates.index(target)
    df_scan, rejim, chg = scan_one_day(target)
    if df_scan is None or df_scan.empty:
        continue
    tavanlar, buyuk = get_real_movers(target, idx_in_ref)
    if not buyuk:
        continue   # tatil günleri vs.
    pool = len(df_scan)
    row = {
        'tarih': str(target.date()),
        'next_date': str(all_dates[idx_in_ref + 1].date()),
        'rejim': rejim, 'xu_chg': chg, 'pool': pool,
        'tavan_n': len(tavanlar), 'buyuk_n': len(buyuk),
    }
    for N in [10, 20, 30, 50, 100]:
        top = set(df_scan.head(N)['tk'].tolist())
        row[f'top{N}_tavan'] = len(top & tavanlar)
        row[f'top{N}_buyuk'] = len(top & buyuk)
    for esik in [100, 130, 150, 180, 200]:
        sub = set(df_scan[df_scan['skor'] >= esik]['tk'].tolist())
        row[f'esik{esik}_n'] = len(sub)
        row[f'esik{esik}_tavan'] = len(sub & tavanlar)
        row[f'esik{esik}_buyuk'] = len(sub & buyuk)
    conf3 = set(df_scan[df_scan['confluence_n'] >= 3]['tk'].tolist())
    row['conf3_n'] = len(conf3)
    row['conf3_tavan'] = len(conf3 & tavanlar)
    row['conf3_buyuk'] = len(conf3 & buyuk)
    for kat in ['A', 'C', 'E', 'D']:
        kat_top = set(df_scan[df_scan['kat'] == kat].head(50)['tk'].tolist())
        row[f'{kat}_n_top50'] = len(kat_top)
        row[f'{kat}_tavan'] = len(kat_top & tavanlar)
        row[f'{kat}_buyuk'] = len(kat_top & buyuk)
    results.append(row)
    if (ti + 1) % 10 == 0:
        print(f'  {ti+1}/{len(target_dates)} tamamlandı...')

R = pd.DataFrame(results)
R.to_csv('tavan_backtest_60g.csv', index=False)
print(f'\nBacktest bitti — {len(R)} gün × {R["pool"].mean():.0f} hisse ort. havuz')
print(f'Toplam gerçek tavan: {R["tavan_n"].sum()} · büyük hareket: {R["buyuk_n"].sum()}')

# ════════════════════════════════════════════════════════════
# RAPOR — TOPLU
# ════════════════════════════════════════════════════════════
print('\n' + '=' * 78)
print('1) GENEL TOP-N PERFORMANS (60 gün toplam)')
print('=' * 78)
tot_tavan = R['tavan_n'].sum()
tot_buyuk = R['buyuk_n'].sum()
avg_pool = R['pool'].mean()
print(f'\n{"BÖLGE":<10} {"TAVAN HIT":>12} {"REC%":>6} {"RAND":>6} {"x":>5}  {"BÜYÜK HIT":>12} {"REC%":>6} {"RAND":>6} {"x":>5}')
print('-' * 78)
for N in [10, 20, 30, 50, 100]:
    t = R[f'top{N}_tavan'].sum()
    b = R[f'top{N}_buyuk'].sum()
    rt = 100 * t / tot_tavan
    rb = 100 * b / tot_buyuk
    rand_t = tot_tavan * N / avg_pool
    rand_b = tot_buyuk * N / avg_pool
    print(f'TOP {N:<6} {t:>5}/{tot_tavan:<5} {rt:>5.1f}% {rand_t:>6.1f} {t/rand_t:>4.2f}x  {b:>5}/{tot_buyuk:<5} {rb:>5.1f}% {rand_b:>6.1f} {b/rand_b:>4.2f}x')

# ════════════════════════════════════════════════════════════
print('\n' + '=' * 78)
print('2) SKOR EŞİĞİ ANALİZİ — precision yükselir mi?')
print('=' * 78)
print(f'\n{"EŞİK":<8} {"ORT N":>7} {"TAVAN HIT":>12} {"PRECISION%":>11} {"REC%":>6}  {"x RAND":>7}')
print('-' * 78)
for esik in [100, 130, 150, 180, 200]:
    n_total = R[f'esik{esik}_n'].sum()
    t = R[f'esik{esik}_tavan'].sum()
    b = R[f'esik{esik}_buyuk'].sum()
    avg_n = R[f'esik{esik}_n'].mean()
    if n_total == 0:
        print(f'≥{esik:<7} {avg_n:>7.1f} {0:>5}/{0:<5}        —        —      —')
        continue
    prec = 100 * t / n_total
    rec = 100 * t / tot_tavan
    rand_t = tot_tavan * n_total / (avg_pool * len(R))
    multi = t / rand_t if rand_t > 0 else 0
    print(f'≥{esik:<7} {avg_n:>7.1f} {t:>5}/{n_total:<5} {prec:>10.2f}% {rec:>5.1f}% {multi:>6.2f}x')

# ════════════════════════════════════════════════════════════
print('\n' + '=' * 78)
print('3) CONFLUENCE 3+ — özel altın grup mu?')
print('=' * 78)
n_conf = R['conf3_n'].sum()
t_conf = R['conf3_tavan'].sum()
b_conf = R['conf3_buyuk'].sum()
avg_conf = R['conf3_n'].mean()
prec_conf = 100 * t_conf / n_conf if n_conf else 0
rand_t = tot_tavan * n_conf / (avg_pool * len(R))
print(f'\nGünlük ortalama: {avg_conf:.1f} hisse')
print(f'Tavan hit: {t_conf}/{n_conf} ({prec_conf:.2f}% precision)')
print(f'Büyük hareket hit: {b_conf}/{n_conf} ({100*b_conf/n_conf:.2f}% precision)' if n_conf else '')
print(f'Random çarpan: {t_conf/rand_t:.2f}x' if rand_t > 0 else '')

# ════════════════════════════════════════════════════════════
print('\n' + '=' * 78)
print('4) KALIP BAZLI PERFORMANS — A vs C vs E vs D (TOP 50)')
print('=' * 78)
print(f'\n{"KALIP":<8} {"ORT N":>7} {"TAVAN HIT":>12} {"PRECISION%":>11} {"REC%":>6}  {"BÜYÜK %":>9}')
print('-' * 78)
for k in ['A', 'C', 'E', 'D']:
    n_total = R[f'{k}_n_top50'].sum()
    t = R[f'{k}_tavan'].sum()
    b = R[f'{k}_buyuk'].sum()
    avg_n = R[f'{k}_n_top50'].mean()
    prec = 100 * t / n_total if n_total else 0
    rec = 100 * t / tot_tavan
    bprec = 100 * b / n_total if n_total else 0
    print(f'{k:<8} {avg_n:>7.1f} {t:>5}/{n_total:<5} {prec:>10.2f}% {rec:>5.1f}% {bprec:>8.2f}%')

# ════════════════════════════════════════════════════════════
print('\n' + '=' * 78)
print('5) REJİM BAZLI PERFORMANS — TOP 30')
print('=' * 78)
print(f'\n{"REJİM":<16} {"GÜN":>4} {"ORT TAVAN":>10} {"TOP30 HIT":>11} {"REC%":>6}  {"x RAND":>7}')
print('-' * 78)
for rej in ['HIZLI_RALLI', 'ILIMLI_YUKARI', 'YATAY', 'ZAYIF', 'DUSUS']:
    sub = R[R['rejim'] == rej]
    if sub.empty:
        print(f'{rej:<16} {"-":>4} {"-":>10} {"-":>11} {"-":>6} {"-":>7}')
        continue
    n_days = len(sub)
    avg_tavan = sub['tavan_n'].mean()
    hit = sub['top30_tavan'].sum()
    total_tavan = sub['tavan_n'].sum()
    rec = 100 * hit / total_tavan if total_tavan else 0
    rand = total_tavan * 30 / sub['pool'].mean()
    multi = hit / rand if rand > 0 else 0
    print(f'{rej:<16} {n_days:>4} {avg_tavan:>10.1f} {hit:>5}/{total_tavan:<5} {rec:>5.1f}% {multi:>6.2f}x')

# ════════════════════════════════════════════════════════════
print('\n' + '=' * 78)
print('6) GÜN BAZLI EN İYİ 5 + EN KÖTÜ 5 (TOP 30 hit)')
print('=' * 78)
R['top30_hit_pct'] = R.apply(lambda r: 100 * r['top30_tavan'] / r['tavan_n'] if r['tavan_n'] > 0 else 0, axis=1)
print('\n🏆 EN İYİ 5 GÜN:')
print(f'{"TARİH":<12} {"REJİM":<14} {"TAVAN":>6} {"TOP30":>6} {"REC%":>6}')
for _, r in R.nlargest(5, 'top30_hit_pct').iterrows():
    print(f'{r["tarih"]:<12} {r["rejim"]:<14} {r["tavan_n"]:>6} {r["top30_tavan"]:>6} {r["top30_hit_pct"]:>5.1f}%')
print('\n💀 EN KÖTÜ 5 GÜN (tavan vardı, motor sıfır yakaladı):')
worst = R[R['tavan_n'] >= 10].nsmallest(5, 'top30_hit_pct')
print(f'{"TARİH":<12} {"REJİM":<14} {"TAVAN":>6} {"TOP30":>6} {"REC%":>6}')
for _, r in worst.iterrows():
    print(f'{r["tarih"]:<12} {r["rejim"]:<14} {r["tavan_n"]:>6} {r["top30_tavan"]:>6} {r["top30_hit_pct"]:>5.1f}%')

print(f'\n\n✅ Tam tablo: tavan_backtest_60g.csv ({len(R)} satır)')
