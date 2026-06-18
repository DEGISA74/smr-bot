"""
TAVAN MOTORU TAM BACKTEST
60 iş günü × 600 hisse — her gün motoru çalıştır, ertesi günün gerçek
tavan listesiyle karşılaştır. Recall + precision + rejim/kalıp/eşik analizi.
"""
import pandas as pd
import numpy as np
import glob, os, sys, io, warnings
warnings.filterwarnings('ignore')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

VERILER = 'veriler'
LOOKBACK_DAYS = 60   # Backtest penceresi
TAVAN_ESIK = 9.5     # +%9.5+ = tavan
BUYUK_HAREKET_ESIK = 5.0   # +%5+ = büyük hareket
MIN_VOL_TL = 2_000_000

# ─── Helpers (tavan_scanner.py'den birebir) ───
def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100/(1 + up/dn.replace(0, np.nan))

def is_manipulated(df, i):
    """Manipülasyon şüphesi: fitilsiz mum çok, tavan/taban açılış çok, range çöküşü çok.
    En az 2 ölçüt kırmızıysa True döner — hisse motordan elenir."""
    if i < 60: return False
    # Son 30 günde fitilsiz mum oranı (body/range > 85%)
    last30 = df.iloc[i-30:i]
    rng = last30['High'] - last30['Low']
    body = (last30['Close'] - last30['Open']).abs()
    body_ratio = (body / rng.replace(0, np.nan)).fillna(1.0)
    fitilsiz_oran = (body_ratio > 0.85).mean()

    # Son 60 günde tavan/taban açılış sıklığı (|günlük %| > 9.5)
    last60 = df.iloc[max(0, i-60):i]
    pct_chg = last60['Close'].pct_change().abs() * 100
    tavan_taban_oran = (pct_chg > 9.5).mean()

    # Son 20 günde range çöküşü ((high-low)/close < 1%)
    last20 = df.iloc[i-20:i]
    rel_range = ((last20['High'] - last20['Low']) / last20['Close']) * 100
    range_collapse = (rel_range < 1.0).mean()

    kirmizi = 0
    if fitilsiz_oran > 0.45: kirmizi += 1   # son 30g'de %45+ mum fitilsiz
    if tavan_taban_oran > 0.12: kirmizi += 1   # son 60g'de %12+ tavan/taban (7+/60)
    if range_collapse > 0.30: kirmizi += 1   # son 20g'de %30+ range çöküşü

    return kirmizi >= 2

def features(df, i):
    if i < 60: return None
    if is_manipulated(df, i): return None   # Manipülasyon şüpheli — ele
    t = df.iloc[i]; t1 = df.iloc[i-1]
    hist = df.iloc[:i+1]
    close = hist['Close']; high = hist['High']; low = hist['Low']; vol = hist['Volume']
    rsi14 = rsi(close).iloc[-1]
    look = min(252, len(hist))
    hh = high.tail(look).max(); ll = low.tail(look).min()
    pos_52h = (t['Close']-ll)/(hh-ll)*100 if hh > ll else np.nan
    bb_w = (close.tail(20).std()/close.tail(20).mean())*100
    bb_60 = close.rolling(20).std()/close.rolling(20).mean()*100
    bb_pct_rank = (bb_60.tail(60) <= bb_w).mean()*100
    vol20 = vol.tail(20).mean()
    vr_t = t['Volume']/vol20 if vol20 > 0 else np.nan
    ret_10g = (t['Close']/df.iloc[i-10]['Close']-1)*100 if i >= 10 else np.nan
    ret_5g = (t['Close']/df.iloc[i-5]['Close']-1)*100 if i >= 5 else np.nan
    near_h20 = (t['Close']/close.tail(20).max())*100
    pct_seq, vol_seq = [], []
    for k in range(5):
        idx = i - (4 - k)
        if idx < 1:
            pct_seq.append(np.nan); vol_seq.append(np.nan); continue
        prev = df.iloc[idx-1]['Close']; cur = df.iloc[idx]['Close']
        pct_seq.append((cur/prev-1)*100 if prev > 0 else np.nan)
        v20 = df.iloc[max(0, idx-20):idx]['Volume'].mean()
        vol_seq.append(df.iloc[idx]['Volume']/v20 if v20 > 0 else np.nan)
    vs = [v for v in vol_seq if pd.notna(v)]
    vol_5g_slope = (vs[-1]-vs[0]) if len(vs) >= 2 else 0
    pct_T = pct_seq[-1] if pct_seq else np.nan
    vol_T = vol_seq[-1] if vol_seq else np.nan
    rng = t['High']-t['Low']; body = abs(t['Close']-t['Open'])
    body_pct = body/rng*100 if rng > 0 else 0
    is_doji = body_pct < 10
    is_green = t['Close'] > t['Open']
    lower_wick = min(t['Close'], t['Open']) - t['Low']
    lw_pct = lower_wick/rng*100 if rng > 0 else 0
    is_hammer = (lw_pct > 40) and (body_pct > 10)
    return dict(close=t['Close'], rsi=rsi14, pos_52h=pos_52h, bb_rank=bb_pct_rank,
                vr_t=vr_t, near_h20=near_h20, ret_5g=ret_5g, ret_10g=ret_10g,
                vol_tl=t['Close']*t['Volume'], pct_T=pct_T, vol_T=vol_T,
                vol_5g_slope=vol_5g_slope, is_doji=is_doji, is_green=is_green,
                is_hammer=is_hammer, body_pct=body_pct)

def score_A(f):
    s = 0
    if f['ret_10g'] >= 20: s += 35
    elif f['ret_10g'] >= 15: s += 25
    elif f['ret_10g'] >= 10: s += 15
    elif f['ret_10g'] >= 5: s += 5
    if f['pos_52h'] >= 90: s += 25
    elif f['pos_52h'] >= 75: s += 15
    elif f['pos_52h'] >= 60: s += 6
    if f['near_h20'] >= 99: s += 20
    elif f['near_h20'] >= 95: s += 8
    if 70 <= f['rsi'] <= 85: s += 12
    elif 60 <= f['rsi'] <= 90: s += 5
    if 1.0 <= f['vr_t'] <= 2.5: s += 8
    elif 0.7 <= f['vr_t'] <= 3.5: s += 3
    return s

def score_C(f):
    s = 0
    if f['bb_rank'] <= 10: s += 40
    elif f['bb_rank'] <= 20: s += 28
    elif f['bb_rank'] <= 30: s += 15
    elif f['bb_rank'] <= 40: s += 5
    if f['near_h20'] >= 97: s += 25
    elif f['near_h20'] >= 93: s += 15
    elif f['near_h20'] >= 88: s += 5
    if 45 <= f['rsi'] <= 60: s += 15
    elif 35 <= f['rsi'] <= 68: s += 7
    if 35 <= f['pos_52h'] <= 65: s += 12
    elif 25 <= f['pos_52h'] <= 75: s += 5
    if 0.8 <= f['vr_t'] <= 1.3: s += 8
    return s

def score_E(f):
    s = 0
    if f['near_h20'] >= 99.5: s += 35
    elif f['near_h20'] >= 97: s += 22
    elif f['near_h20'] >= 94: s += 8
    if 65 <= f['pos_52h'] <= 85: s += 22
    elif 55 <= f['pos_52h'] <= 90: s += 10
    if 60 <= f['rsi'] <= 75: s += 15
    elif 50 <= f['rsi'] <= 80: s += 6
    if f['vr_t'] >= 1.2: s += 15
    elif f['vr_t'] >= 0.9: s += 6
    if 3 <= f['ret_10g'] <= 14: s += 10
    elif 0 <= f['ret_10g'] <= 20: s += 4
    return s

def score_D(f):
    s = 0
    if f['pos_52h'] <= 10: s += 35
    elif f['pos_52h'] <= 18: s += 22
    elif f['pos_52h'] <= 28: s += 8
    if f['rsi'] <= 28: s += 25
    elif f['rsi'] <= 38: s += 15
    elif f['rsi'] <= 45: s += 5
    if f['vr_t'] <= 0.65: s += 20
    elif f['vr_t'] <= 0.9: s += 8
    if f['ret_10g'] <= -10: s += 15
    elif f['ret_10g'] <= -4: s += 8
    return s

REJIM_AGIRLIK = {
    # KALİBRE TUR 2 (1. tur agresif kalibrasyon TOP 30'u düşürdü):
    # Daha ılımlı çarpanlar — A öne ama abartmadan, C cezalandırılmış ama dengeli
    # DUSUS'ta D çarpanı 1.3 BÜYÜK HATAYDI (3.79x→1.38x) — D 1.0'a, A 0.9 (motor zayıflar düşüşte)
    'HIZLI_RALLI':   {'A':1.1, 'C':0.8, 'E':1.0, 'D':0.7},
    'ILIMLI_YUKARI': {'A':1.1, 'C':0.9, 'E':1.0, 'D':0.9},
    'YATAY':         {'A':1.2, 'C':0.9, 'E':1.1, 'D':0.9},
    'ZAYIF':         {'A':1.1, 'C':0.8, 'E':1.0, 'D':1.0},
    'DUSUS':         {'A':0.9, 'C':0.7, 'E':0.8, 'D':1.0},
    'BILINMEZ':      {'A':1.0, 'C':1.0, 'E':1.0, 'D':1.0},
}

def detect_rejim(xu_df, i):
    if i < 10: return 'BILINMEZ', 0.0
    start = xu_df.iloc[i-10]['Close']; end = xu_df.iloc[i]['Close']
    chg = (end/start-1)*100
    if chg >= 5: return 'HIZLI_RALLI', chg
    if chg >= 2: return 'ILIMLI_YUKARI', chg
    if chg >= -2: return 'YATAY', chg
    if chg >= -5: return 'ZAYIF', chg
    return 'DUSUS', chg

# ─── Veri yükle (bellekte tut) ───
print('Parquetler yükleniyor...')
ALL = {}
for f in glob.glob(f'{VERILER}/*.IS_1d.parquet'):
    tk = os.path.basename(f).replace('.IS_1d.parquet', '')
    if tk in ('XU100','XU030','XU050','XBANK','XUSIN','XUMAL'):
        continue
    try:
        df = pd.read_parquet(f)
        if len(df) >= 100: ALL[tk] = df
    except: pass
xu = pd.read_parquet(f'{VERILER}/XU100.IS_1d.parquet')
print(f'{len(ALL)} hisse + XU100 yüklendi.')

# Hedef günler (T+1'i hesaplayabilmek için son günü ATLA)
ref = next(iter(ALL.values()))
all_dates = ref.index.tolist()
target_dates = all_dates[-LOOKBACK_DAYS-1:-1]   # son 60 iş günü, T+1 için son günü atla
print(f'Backtest pencere: {str(target_dates[0].date())} → {str(target_dates[-1].date())} ({len(target_dates)} gün)')

# ─── Backtest ───
def scan_one_day(target):
    """T günü kapanış → skor DataFrame'i (T+1 için aday)."""
    if target not in xu.index: return None, 'BILINMEZ', 0
    i_xu = xu.index.get_loc(target)
    rejim, chg = detect_rejim(xu, i_xu)
    agr = REJIM_AGIRLIK[rejim]
    rows = []
    for tk, df in ALL.items():
        if target not in df.index: continue
        i = df.index.get_loc(target)
        feat = features(df, i)
        if feat is None: continue
        if feat['vol_tl'] < MIN_VOL_TL: continue
        sA = score_A(feat) * agr['A']
        sC = score_C(feat) * agr['C']
        sE = score_E(feat) * agr['E']
        sD = score_D(feat) * agr['D']
        # Booster
        if pd.notna(feat['pct_T']) and pd.notna(feat['vol_T']):
            if feat['pct_T'] > 2 and feat['vol_T'] > 1.2:
                sA += 12; sE += 18; sC += 6
            elif feat['pct_T'] > 1:
                sA += 6; sE += 9; sC += 3
            elif feat['pct_T'] < -3 and feat['vol_T'] < 0.7:
                sD += 15
        if pd.notna(feat['vol_5g_slope']):
            if feat['vol_5g_slope'] > 0.5:
                sA += 8; sE += 10; sC += 8
            elif feat['vol_5g_slope'] > 0.2:
                sA += 4; sE += 5; sC += 4
        if feat['is_doji']: sC += 12
        if feat['is_green'] and feat['body_pct'] > 60:
            sA += 8; sE += 10
        if feat['is_hammer']: sD += 10
        if pd.notna(feat['ret_5g']):
            if feat['ret_5g'] > 10: sA += 8
            elif feat['ret_5g'] < -8: sD += 8
        sc = {'A': sA, 'C': sC, 'E': sE, 'D': sD}
        kat = max(sc, key=sc.get)
        best = sc[kat]
        srt = sorted(sc.values(), reverse=True)
        # Confluence eşiği 30 → 50 (eski eşik çok geniş, 177 hisse/gün
        # confluence sayılıyordu — gerçek özel grup değildi)
        conf = max(0, (srt[1]-50))*0.6
        if len(srt)>=3 and srt[2]>50:
            conf += (srt[2]-50)*0.3
        total = best + conf
        rows.append({'tk':tk, 'skor':round(total,1), 'kat':kat,
                     'confluence_n':sum(1 for v in srt if v>50)})
    return pd.DataFrame(rows).sort_values('skor', ascending=False).reset_index(drop=True), rejim, round(chg,2)

def get_real_movers(target_date, target_idx):
    """T+1 günündeki gerçek tavanlar ve büyük hareketler."""
    tavanlar = set(); buyuk = set()
    next_date = all_dates[target_idx+1]
    for tk, df in ALL.items():
        if next_date not in df.index: continue
        i = df.index.get_loc(next_date)
        if i < 1: continue
        pct = (df.iloc[i]['Close']/df.iloc[i-1]['Close']-1)*100
        if pct >= TAVAN_ESIK: tavanlar.add(tk)
        if pct >= BUYUK_HAREKET_ESIK: buyuk.add(tk)
    return tavanlar, buyuk

# Ana döngü
print('\nBacktest çalışıyor...')
results = []
for ti, target in enumerate(target_dates):
    idx_in_ref = all_dates.index(target)
    df_scan, rejim, chg = scan_one_day(target)
    if df_scan is None or df_scan.empty: continue
    tavanlar, buyuk = get_real_movers(target, idx_in_ref)
    if not buyuk: continue   # tatil günleri vs.
    pool = len(df_scan)
    row = {
        'tarih': str(target.date()),
        'next_date': str(all_dates[idx_in_ref+1].date()),
        'rejim': rejim, 'xu_chg': chg, 'pool': pool,
        'tavan_n': len(tavanlar), 'buyuk_n': len(buyuk),
    }
    # TOP N analizi (recall + precision için tavan ve büyük hareket)
    for N in [10, 20, 30, 50, 100]:
        top = set(df_scan.head(N)['tk'].tolist())
        row[f'top{N}_tavan'] = len(top & tavanlar)
        row[f'top{N}_buyuk'] = len(top & buyuk)
    # Skor eşiği analizi
    for esik in [100, 130, 150, 180, 200]:
        sub = set(df_scan[df_scan['skor']>=esik]['tk'].tolist())
        row[f'esik{esik}_n'] = len(sub)
        row[f'esik{esik}_tavan'] = len(sub & tavanlar)
        row[f'esik{esik}_buyuk'] = len(sub & buyuk)
    # Confluence 3+
    conf3 = set(df_scan[df_scan['confluence_n']>=3]['tk'].tolist())
    row['conf3_n'] = len(conf3)
    row['conf3_tavan'] = len(conf3 & tavanlar)
    row['conf3_buyuk'] = len(conf3 & buyuk)
    # Kalıp bazlı TOP 50
    for kat in ['A','C','E','D']:
        kat_top = set(df_scan[df_scan['kat']==kat].head(50)['tk'].tolist())
        row[f'{kat}_n_top50'] = len(kat_top)
        row[f'{kat}_tavan'] = len(kat_top & tavanlar)
        row[f'{kat}_buyuk'] = len(kat_top & buyuk)
    results.append(row)
    if (ti+1) % 10 == 0:
        print(f'  {ti+1}/{len(target_dates)} tamamlandı...')

R = pd.DataFrame(results)
R.to_csv('tavan_backtest_60g.csv', index=False)
print(f'\nBacktest bitti — {len(R)} gün × {R["pool"].mean():.0f} hisse ort. havuz')
print(f'Toplam gerçek tavan: {R["tavan_n"].sum()} · büyük hareket: {R["buyuk_n"].sum()}')

# ════════════════════════════════════════════════════════════
# RAPOR — TOPLU
# ════════════════════════════════════════════════════════════
print('\n' + '='*78)
print('1) GENEL TOP-N PERFORMANS (60 gün toplam)')
print('='*78)
tot_tavan = R['tavan_n'].sum()
tot_buyuk = R['buyuk_n'].sum()
avg_pool = R['pool'].mean()
print(f'\n{"BÖLGE":<10} {"TAVAN HIT":>12} {"REC%":>6} {"RAND":>6} {"x":>5}  {"BÜYÜK HIT":>12} {"REC%":>6} {"RAND":>6} {"x":>5}')
print('-'*78)
for N in [10,20,30,50,100]:
    t = R[f'top{N}_tavan'].sum(); b = R[f'top{N}_buyuk'].sum()
    rt = 100*t/tot_tavan; rb = 100*b/tot_buyuk
    rand_t = tot_tavan * N / avg_pool
    rand_b = tot_buyuk * N / avg_pool
    print(f'TOP {N:<6} {t:>5}/{tot_tavan:<5} {rt:>5.1f}% {rand_t:>6.1f} {t/rand_t:>4.2f}x  {b:>5}/{tot_buyuk:<5} {rb:>5.1f}% {rand_b:>6.1f} {b/rand_b:>4.2f}x')

# ════════════════════════════════════════════════════════════
print('\n' + '='*78)
print('2) SKOR EŞİĞİ ANALİZİ — precision yükselir mi?')
print('='*78)
print(f'\n{"EŞİK":<8} {"ORT N":>7} {"TAVAN HIT":>12} {"PRECISION%":>11} {"REC%":>6}  {"x RAND":>7}')
print('-'*78)
for esik in [100,130,150,180,200]:
    n_total = R[f'esik{esik}_n'].sum()
    t = R[f'esik{esik}_tavan'].sum()
    b = R[f'esik{esik}_buyuk'].sum()
    avg_n = R[f'esik{esik}_n'].mean()
    if n_total == 0:
        print(f'≥{esik:<7} {avg_n:>7.1f} {0:>5}/{0:<5}        —        —      —')
        continue
    prec = 100*t/n_total
    rec = 100*t/tot_tavan
    rand_t = tot_tavan * n_total / (avg_pool * len(R))
    multi = t/rand_t if rand_t > 0 else 0
    print(f'≥{esik:<7} {avg_n:>7.1f} {t:>5}/{n_total:<5} {prec:>10.2f}% {rec:>5.1f}% {multi:>6.2f}x')

# ════════════════════════════════════════════════════════════
print('\n' + '='*78)
print('3) CONFLUENCE 3+ — özel altın grup mu?')
print('='*78)
n_conf = R['conf3_n'].sum()
t_conf = R['conf3_tavan'].sum()
b_conf = R['conf3_buyuk'].sum()
avg_conf = R['conf3_n'].mean()
prec_conf = 100*t_conf/n_conf if n_conf else 0
rand_t = tot_tavan * n_conf / (avg_pool * len(R))
print(f'\nGünlük ortalama: {avg_conf:.1f} hisse')
print(f'Tavan hit: {t_conf}/{n_conf} ({prec_conf:.2f}% precision)')
print(f'Büyük hareket hit: {b_conf}/{n_conf} ({100*b_conf/n_conf:.2f}% precision)' if n_conf else '')
print(f'Random çarpan: {t_conf/rand_t:.2f}x' if rand_t > 0 else '')

# ════════════════════════════════════════════════════════════
print('\n' + '='*78)
print('4) KALIP BAZLI PERFORMANS — A vs C vs E vs D (TOP 50)')
print('='*78)
print(f'\n{"KALIP":<8} {"ORT N":>7} {"TAVAN HIT":>12} {"PRECISION%":>11} {"REC%":>6}  {"BÜYÜK %":>9}')
print('-'*78)
for k in ['A','C','E','D']:
    n_total = R[f'{k}_n_top50'].sum()
    t = R[f'{k}_tavan'].sum()
    b = R[f'{k}_buyuk'].sum()
    avg_n = R[f'{k}_n_top50'].mean()
    prec = 100*t/n_total if n_total else 0
    rec = 100*t/tot_tavan
    bprec = 100*b/n_total if n_total else 0
    print(f'{k:<8} {avg_n:>7.1f} {t:>5}/{n_total:<5} {prec:>10.2f}% {rec:>5.1f}% {bprec:>8.2f}%')

# ════════════════════════════════════════════════════════════
print('\n' + '='*78)
print('5) REJİM BAZLI PERFORMANS — TOP 30')
print('='*78)
print(f'\n{"REJİM":<16} {"GÜN":>4} {"ORT TAVAN":>10} {"TOP30 HIT":>11} {"REC%":>6}  {"x RAND":>7}')
print('-'*78)
for rej in ['HIZLI_RALLI','ILIMLI_YUKARI','YATAY','ZAYIF','DUSUS']:
    sub = R[R['rejim']==rej]
    if sub.empty:
        print(f'{rej:<16} {"-":>4} {"-":>10} {"-":>11} {"-":>6} {"-":>7}')
        continue
    n_days = len(sub)
    avg_tavan = sub['tavan_n'].mean()
    hit = sub['top30_tavan'].sum()
    total_tavan = sub['tavan_n'].sum()
    rec = 100*hit/total_tavan if total_tavan else 0
    rand = total_tavan * 30 / sub['pool'].mean()
    multi = hit/rand if rand > 0 else 0
    print(f'{rej:<16} {n_days:>4} {avg_tavan:>10.1f} {hit:>5}/{total_tavan:<5} {rec:>5.1f}% {multi:>6.2f}x')

# ════════════════════════════════════════════════════════════
print('\n' + '='*78)
print('6) GÜN BAZLI EN İYİ 5 + EN KÖTÜ 5 (TOP 30 hit)')
print('='*78)
R['top30_hit_pct'] = R.apply(lambda r: 100*r['top30_tavan']/r['tavan_n'] if r['tavan_n']>0 else 0, axis=1)
print('\n🏆 EN İYİ 5 GÜN:')
print(f'{"TARİH":<12} {"REJİM":<14} {"TAVAN":>6} {"TOP30":>6} {"REC%":>6}')
for _, r in R.nlargest(5, 'top30_hit_pct').iterrows():
    print(f'{r["tarih"]:<12} {r["rejim"]:<14} {r["tavan_n"]:>6} {r["top30_tavan"]:>6} {r["top30_hit_pct"]:>5.1f}%')
print('\n💀 EN KÖTÜ 5 GÜN (tavan vardı, motor sıfır yakaladı):')
worst = R[R['tavan_n']>=10].nsmallest(5, 'top30_hit_pct')
print(f'{"TARİH":<12} {"REJİM":<14} {"TAVAN":>6} {"TOP30":>6} {"REC%":>6}')
for _, r in worst.iterrows():
    print(f'{r["tarih"]:<12} {r["rejim"]:<14} {r["tavan_n"]:>6} {r["top30_tavan"]:>6} {r["top30_hit_pct"]:>5.1f}%')

print(f'\n\n✅ Tam tablo: tavan_backtest_60g.csv ({len(R)} satır)')
