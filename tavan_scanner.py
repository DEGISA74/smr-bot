"""
TAVAN YAKALAMA MOTORU
1109 tavan örnekli 60g backtest sonuçlarına dayalı filtre.
T günü kapanışına bakar, T+1 için "tavan riski" skoru üretir.

Kullanım:
  python tavan_scanner.py              # bugünün son verisi
  python tavan_scanner.py 2026-06-12   # belirli bir gün (validation)
  python tavan_scanner.py --top 50     # ilk 50 hisse
"""
import pandas as pd
import numpy as np
import glob, os, sys, argparse, warnings
warnings.filterwarnings('ignore')

VERILER = 'veriler'

# ───────────────────── Yardımcı hesaplamalar ─────────────────────
def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100/(1 + up/dn.replace(0,np.nan))

def features(df, i):
    """T gününde teknik resmi çıkar. T = en son kapanış.
       5 günlük dizilim katmanı dahil (60g 1149 örnek backtest sonucu)."""
    if i < 60: return None
    t = df.iloc[i]      # T (bugünkü kapanış — yarın için bakacağımız gün)
    t1 = df.iloc[i-1]
    hist = df.iloc[:i+1]   # T dahil
    close = hist['Close']; high = hist['High']; low = hist['Low']; vol = hist['Volume']

    rsi14 = rsi(close).iloc[-1]
    look = min(252, len(hist))
    hh = high.tail(look).max(); ll = low.tail(look).min()
    pos_52h = (t['Close']-ll)/(hh-ll)*100 if hh>ll else np.nan

    bb_w = (close.tail(20).std()/close.tail(20).mean())*100
    bb_60 = close.rolling(20).std()/close.rolling(20).mean()*100
    bb_pct_rank = (bb_60.tail(60) <= bb_w).mean()*100

    vol20 = vol.tail(20).mean()
    vr_t = t['Volume']/vol20 if vol20>0 else np.nan
    vr_t1 = t1['Volume']/vol20 if vol20>0 else np.nan

    ret_10g = (t['Close']/df.iloc[i-10]['Close']-1)*100 if i>=10 else np.nan
    ret_5g = (t['Close']/df.iloc[i-5]['Close']-1)*100 if i>=5 else np.nan

    high20 = close.tail(20).max()
    near_high20 = (t['Close']/high20)*100

    # ── 5 GÜNLÜK DİZİLİM KATMANI ──
    # T günü = "yarın için bakıyoruz". Yani T'nin kendisi T-1'in yerine geçer (1 gün ileride bakıyoruz).
    # Burada T = scanner çağrı zamanı; ertesi gün için "T-1 = bugün, T-2 = dün..."

    # Son 5 günün getiri ve hacim oranı (T-4'ten T'ye = 5 gün)
    pct_seq = []   # günlük yüzde değişim
    vol_seq = []   # vol_ratio
    for k in range(5):  # T-4, T-3, T-2, T-1, T
        idx = i - (4 - k)   # i-4, i-3, i-2, i-1, i
        if idx < 1:
            pct_seq.append(np.nan); vol_seq.append(np.nan); continue
        prev = df.iloc[idx-1]['Close']
        cur = df.iloc[idx]['Close']
        pct_seq.append((cur/prev - 1)*100 if prev>0 else np.nan)
        v20 = df.iloc[max(0,idx-20):idx]['Volume'].mean()
        vol_seq.append(df.iloc[idx]['Volume']/v20 if v20>0 else np.nan)

    # Hacim eğimi (T-4 → T): yavaş ısınma var mı?
    vs = [v for v in vol_seq if pd.notna(v)]
    vol_5g_slope = (vs[-1] - vs[0]) if len(vs) >= 2 else 0   # >0 = artan, <0 = düşen

    # T-1 (yani df.iloc[i-1] = bizim T-1 günümüz — bir gün önceki kapanış) güç sinyalleri
    pct_T_minus_1 = pct_seq[-1] if pct_seq else np.nan   # son tamamlanmış günün getirisi
    vol_T_minus_1 = vol_seq[-1] if vol_seq else np.nan

    # Mum tipi (T günü = bugünün mumu)
    rng = t['High'] - t['Low']
    body = abs(t['Close'] - t['Open'])
    upper_wick = t['High'] - max(t['Close'], t['Open'])
    lower_wick = min(t['Close'], t['Open']) - t['Low']
    if rng > 0:
        body_pct = body/rng*100
        uw_pct = upper_wick/rng*100
        lw_pct = lower_wick/rng*100
    else:
        body_pct = uw_pct = lw_pct = 0

    is_doji = body_pct < 10
    is_green = t['Close'] > t['Open']
    is_hammer = (lw_pct > 40) and body_pct > 10   # alt fitilli mum
    is_shooting = (uw_pct > 40) and (not is_green) and body_pct > 10  # üst fitilli kırmızı

    return dict(
        close=t['Close'], pct_t=(t['Close']/t1['Close']-1)*100,
        rsi=rsi14, pos_52h=pos_52h, bb_rank=bb_pct_rank,
        vr_t=vr_t, vr_t1=vr_t1, near_h20=near_high20,
        ret_5g=ret_5g, ret_10g=ret_10g,
        vol_tl=t['Close']*t['Volume'],
        # 5 günlük katman
        pct_T=pct_T_minus_1,
        vol_T=vol_T_minus_1,
        vol_5g_slope=vol_5g_slope,
        pct_seq=pct_seq,
        is_doji=is_doji, is_green=is_green,
        is_hammer=is_hammer, is_shooting=is_shooting,
        body_pct=body_pct,
    )

# ───────────────────── Kalıp skorları (0-100) ─────────────────────
# Her kalıbın 60g'deki gerçek medyanlarına yakın olan hisseye yüksek skor.

def score_A_momentum(f):
    """A: Momentum süren — RSI 75, 52H 90, BB geniş, NearH20 100, Ret10g +25
       Strict + ham skor: tipik tavan profili 90-110, ortalama 50-70."""
    s = 0
    # Ret10g — A'nın en ayırt edici özelliği (medyan +%26)
    if f['ret_10g'] >= 20: s += 35
    elif f['ret_10g'] >= 15: s += 25
    elif f['ret_10g'] >= 10: s += 15
    elif f['ret_10g'] >= 5: s += 5
    # 52H pozisyonu (medyan %90)
    if f['pos_52h'] >= 90: s += 25
    elif f['pos_52h'] >= 75: s += 15
    elif f['pos_52h'] >= 60: s += 6
    # NearH20 — direnç dibi (medyan %100)
    if f['near_h20'] >= 99: s += 20
    elif f['near_h20'] >= 95: s += 8
    # RSI yüksek (medyan 75)
    if 70 <= f['rsi'] <= 85: s += 12
    elif 60 <= f['rsi'] <= 90: s += 5
    # Hacim hafif yüksek (medyan 1.27x)
    if 1.0 <= f['vr_t'] <= 2.5: s += 8
    elif 0.7 <= f['vr_t'] <= 3.5: s += 3
    return s   # ham skor — clip yok

def score_C_sikisma(f):
    """C: Sıkışma kırılımı — BBrank 12, NearH20 95, RSI 51, 52H 48"""
    s = 0
    # En kritik — BBrank (medyan 10-12)
    if f['bb_rank'] <= 10: s += 40
    elif f['bb_rank'] <= 20: s += 28
    elif f['bb_rank'] <= 30: s += 15
    elif f['bb_rank'] <= 40: s += 5
    # NearH20 (medyan %95)
    if f['near_h20'] >= 97: s += 25
    elif f['near_h20'] >= 93: s += 15
    elif f['near_h20'] >= 88: s += 5
    # RSI orta (medyan 51)
    if 45 <= f['rsi'] <= 60: s += 15
    elif 35 <= f['rsi'] <= 68: s += 7
    # 52H orta (medyan 48)
    if 35 <= f['pos_52h'] <= 65: s += 12
    elif 25 <= f['pos_52h'] <= 75: s += 5
    # Hacim normal — patlama önceden parlama olmaz (0.7-1.4)
    if 0.8 <= f['vr_t'] <= 1.3: s += 8
    return s

def score_E_direnc(f):
    """E: 20g direnci kırma — NearH20 100, pos_52h 73, RSI 66, Vol 1.27x"""
    s = 0
    # NearH20 en kritik (medyan %100)
    if f['near_h20'] >= 99.5: s += 35
    elif f['near_h20'] >= 97: s += 22
    elif f['near_h20'] >= 94: s += 8
    # 52H pozisyon (medyan 73)
    if 65 <= f['pos_52h'] <= 85: s += 22
    elif 55 <= f['pos_52h'] <= 90: s += 10
    # RSI (medyan 66)
    if 60 <= f['rsi'] <= 75: s += 15
    elif 50 <= f['rsi'] <= 80: s += 6
    # Hacim hafif arttı (medyan 1.27x)
    if f['vr_t'] >= 1.2: s += 15
    elif f['vr_t'] >= 0.9: s += 6
    # Az pozitif 10g (medyan +8)
    if 3 <= f['ret_10g'] <= 14: s += 10
    elif 0 <= f['ret_10g'] <= 20: s += 4
    return s

def score_D_dipdonus(f):
    """D: Dipten dönüş — pos_52h 9, RSI 33, vol 0.6x, Ret10g -10"""
    s = 0
    # 52H çok düşük (medyan %12)
    if f['pos_52h'] <= 10: s += 35
    elif f['pos_52h'] <= 18: s += 22
    elif f['pos_52h'] <= 28: s += 8
    # RSI çok düşük (medyan 33)
    if f['rsi'] <= 28: s += 25
    elif f['rsi'] <= 38: s += 15
    elif f['rsi'] <= 45: s += 5
    # Hacim çok düşük — sessizlik (medyan 0.59x)
    if f['vr_t'] <= 0.65: s += 20
    elif f['vr_t'] <= 0.9: s += 8
    # 10g eksi (medyan -10)
    if f['ret_10g'] <= -10: s += 15
    elif f['ret_10g'] <= -4: s += 8
    return s

KATEGORI_ACK = {
    'A': 'Momentum süren — 10g zaten +%15+, 52H zirvede',
    'C': 'Sıkışma kırılımı — bantlar dar, direnç dibinde',
    'E': '20g direnci kırma — zirvenin nefesinde, hacim hafif arttı',
    'D': 'Dipten dönüş — RSI 30, sessizlik, fiyat 52H dibinde',
}

# ───────────────────── Rejim tespit ─────────────────────
def detect_rejim(xu100_df, i, lookback=10):
    """XU100'ün son N günlük değişimine göre rejim. Yoksa 'BILINMEZ'."""
    if xu100_df is None or i < lookback: return 'BILINMEZ', 0
    start = xu100_df.iloc[i-lookback]['Close']
    end = xu100_df.iloc[i]['Close']
    chg = (end/start-1)*100
    if chg >= 5: return 'HIZLI_RALLI', chg
    if chg >= 2: return 'ILIMLI_YUKARI', chg
    if chg >= -2: return 'YATAY', chg
    if chg >= -5: return 'ZAYIF', chg
    return 'DUSUS', chg

REJIM_AGIRLIK = {
    # rejim → her kalıp için çarpan (60g segment yoğunluğuna göre)
    'HIZLI_RALLI':   {'A':1.0, 'C':0.7, 'E':0.9, 'D':0.6},   # F büyük, teknik motor zayıf
    'ILIMLI_YUKARI': {'A':1.0, 'C':1.3, 'E':1.0, 'D':0.9},   # C öne çıkar
    'YATAY':         {'A':1.4, 'C':1.0, 'E':1.1, 'D':0.9},   # A yıldız (S3 %47)
    'ZAYIF':         {'A':1.3, 'C':1.1, 'E':1.0, 'D':1.0},   # A liderler korur
    'DUSUS':         {'A':1.2, 'C':1.0, 'E':0.9, 'D':1.2},   # D oranı artar
    'BILINMEZ':      {'A':1.0, 'C':1.0, 'E':1.0, 'D':1.0},
}

# ───────────────────── Ana motor ─────────────────────
def run(target_date=None, top_n=30, min_vol_tl=5_000_000):
    print('Veriler yükleniyor...')
    ALL = {}
    for f in glob.glob(f'{VERILER}/*.IS_1d.parquet'):
        tk = os.path.basename(f).replace('.IS_1d.parquet','')
        try:
            df = pd.read_parquet(f)
            if len(df) >= 80: ALL[tk] = df
        except: pass
    print(f'{len(ALL)} hisse yüklendi.')

    # Hedef gün
    ref = ALL.get('AKBNK')
    if target_date is None:
        target = ref.index[-1]
    else:
        target = pd.Timestamp(target_date)
        if target not in ref.index:
            # En yakın geriyi al
            target = ref.index[ref.index <= target][-1]
    print(f'\n=== HEDEF GÜN: {target.date()} ({target.strftime("%A")}) ===')

    # Rejim
    xu = ALL.get('XU100')
    if xu is not None and target in xu.index:
        i_xu = xu.index.get_loc(target)
        rejim, chg = detect_rejim(xu, i_xu, lookback=10)
        print(f'Rejim: {rejim} (XU100 10g {chg:+.2f}%)')
    else:
        rejim, chg = 'BILINMEZ', 0
        print('Rejim: BİLİNMEZ (XU100 verisi yok)')

    agirlik = REJIM_AGIRLIK[rejim]

    # Tarama
    rows = []
    for tk, df in ALL.items():
        if target not in df.index: continue
        i = df.index.get_loc(target)
        f = features(df, i)
        if f is None: continue
        # Likidite filtresi
        if f['vol_tl'] < min_vol_tl: continue

        sA = score_A_momentum(f) * agirlik['A']
        sC = score_C_sikisma(f) * agirlik['C']
        sE = score_E_direnc(f) * agirlik['E']
        sD = score_D_dipdonus(f) * agirlik['D']

        # ── 5 GÜNLÜK DİZİLİM BOOSTERLAR ──
        # Backtest kanıtı: T-1 yeşil + vol > 1.2x → tavanların %34'ünde
        # Hacim yavaş yükseliyor (5g slope >0): tavanların yarısında görünür
        # Doji + sıkışma kombinasyonu C kalıbının özel sinyali

        boost_A = 0; boost_C = 0; boost_E = 0; boost_D = 0
        # B1: T-1 güçlü yeşil + hacim
        if pd.notna(f['pct_T']) and pd.notna(f['vol_T']):
            if f['pct_T'] > 2 and f['vol_T'] > 1.2:    # ↑ + Yüksek hacim — en güçlü tek sinyal
                boost_A += 12; boost_E += 18; boost_C += 6
            elif f['pct_T'] > 1:
                boost_A += 6; boost_E += 9; boost_C += 3
            elif f['pct_T'] < -3 and f['vol_T'] < 0.7:  # Sessiz düşüş — D karakteri
                boost_D += 15
        # B2: 5 günlük hacim ısınması (slope > 0.3)
        if pd.notna(f['vol_5g_slope']):
            if f['vol_5g_slope'] > 0.5:
                boost_A += 8; boost_E += 10; boost_C += 8
            elif f['vol_5g_slope'] > 0.2:
                boost_A += 4; boost_E += 5; boost_C += 4
        # B3: T mum tipi — bugünün mumu
        if f['is_doji']:
            boost_C += 12   # doji + sıkışma → klasik kırılım hazırlığı
        if f['is_green'] and f['body_pct'] > 60:
            boost_A += 8; boost_E += 10
        if f['is_hammer']:
            boost_D += 10   # alt fitilli mum dipte tepki sinyali
        # B4: 5 günlük getiri sınıfı
        if pd.notna(f['ret_5g']):
            if f['ret_5g'] > 10:
                boost_A += 8
            elif f['ret_5g'] < -8:
                boost_D += 8

        sA += boost_A; sC += boost_C; sE += boost_E; sD += boost_D
        scores = {'A': sA, 'C': sC, 'E': sE, 'D': sD}
        best_kat = max(scores, key=scores.get)
        best_score = scores[best_kat]

        # Confluence: ikinci skor 30+ ise ağırlıklı eklenir (kalıp uyumu en güçlü sinyal)
        sorted_scores = sorted(scores.values(), reverse=True)
        confluence_bonus = max(0, (sorted_scores[1] - 30)) * 0.6 if len(sorted_scores)>1 else 0
        # Triple confluence — 3 kalıp 30+ ise ek bonus
        if len(sorted_scores)>=3 and sorted_scores[2] > 30:
            confluence_bonus += (sorted_scores[2] - 30) * 0.3
        total = best_score + confluence_bonus    # ham skor, clip yok

        rows.append({
            'tk': tk,
            'fiyat': round(f['close'], 2),
            'kat': best_kat,
            'skor': round(total, 1),
            'A': round(sA, 0), 'C': round(sC, 0), 'E': round(sE, 0), 'D': round(sD, 0),
            'RSI': round(f['rsi'], 0),
            '52H%': round(f['pos_52h'], 0),
            'BBrank': round(f['bb_rank'], 0),
            'VolT': round(f['vr_t'], 2),
            'NearH20': round(f['near_h20'], 0),
            'Ret10g': round(f['ret_10g'], 1),
            'vol_mTL': round(f['vol_tl']/1e6, 1),
        })

    df = pd.DataFrame(rows).sort_values('skor', ascending=False)
    df.to_csv(f'tavan_skoru_{target.date()}.csv', index=False)

    # Rapor
    print(f'\n{len(df)} hisse tarandı (likidite > {min_vol_tl/1e6:.1f}M TL).')
    print(f'Rejim çarpanı: {agirlik}\n')

    print(f'\n=== TOP {top_n} TAVAN ADAYI ({target.date()} kapanışı → ertesi gün için) ===')
    cols = ['tk','fiyat','skor','kat','A','C','E','D','RSI','52H%','BBrank','VolT','NearH20','Ret10g','vol_mTL']
    print(df.head(top_n)[cols].to_string(index=False))

    # Kalıp bazlı top
    print('\n\n=== HER KALIPTA EN GÜÇLÜ 5 ===')
    for k in ['A','C','E','D']:
        sub = df[df['kat']==k].head(5)
        print(f'\n--- {k} — {KATEGORI_ACK[k]} ---')
        if len(sub):
            print(sub[['tk','fiyat','skor','RSI','52H%','BBrank','VolT','NearH20','Ret10g','vol_mTL']].to_string(index=False))
        else:
            print('  (uygun hisse yok)')

    # Confluence (2+ kalıp 60+)
    conf = df[((df['A']>=55).astype(int) + (df['C']>=55).astype(int) +
              (df['E']>=55).astype(int) + (df['D']>=55).astype(int)) >= 2]
    print(f'\n\n=== CONFLUENCE (2+ kalıp uyumu, 55+ skor) — {len(conf)} hisse ===')
    if len(conf):
        print(conf.head(15)[cols].to_string(index=False))

    print(f'\nTam tablo: tavan_skoru_{target.date()}.csv')
    return df

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('date', nargs='?', help='Hedef tarih YYYY-MM-DD (boş = en son gün)')
    parser.add_argument('--top', type=int, default=30, help='Kaç hisse listelensin')
    parser.add_argument('--min-vol', type=float, default=5_000_000, help='Min günlük hacim (TL)')
    args = parser.parse_args()
    run(args.date, top_n=args.top, min_vol_tl=args.min_vol)
