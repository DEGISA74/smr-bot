"""
TAVAN YAKALAMA MOTORU — CLI
T günü kapanışına bakar, T+1 için "tavan riski" skoru üretir.

Skorlama mantığı tavan_engine.py'de (TEK KAYNAK). Bu dosya sadece:
  parquet yükle → engine ile skorla → raporla + CSV.

Kullanım:
  python tavan_scanner.py              # bugünün son verisi
  python tavan_scanner.py 2026-06-12   # belirli bir gün (validation)
  python tavan_scanner.py --top 50     # ilk 50 hisse
"""
import pandas as pd
import glob, os, argparse, warnings
import tavan_engine as te
warnings.filterwarnings('ignore')

VERILER = 'veriler'


# ───────────────────── Ana motor ─────────────────────
def run(target_date=None, top_n=30, min_vol_tl=te.MIN_VOL_TL):
    print('Veriler yükleniyor...')
    ALL = {}
    for f in glob.glob(f'{VERILER}/*.IS_1d.parquet'):
        tk = os.path.basename(f).replace('.IS_1d.parquet', '')
        try:
            df = pd.read_parquet(f)
            if len(df) >= 80:
                ALL[tk] = df
        except Exception:
            pass
    print(f'{len(ALL)} hisse yüklendi.')

    # Hedef gün
    ref = ALL['AKBNK'] if 'AKBNK' in ALL else next(iter(ALL.values()))
    if target_date is None:
        target = ref.index[-1]
    else:
        target = pd.Timestamp(target_date)
        if target not in ref.index:
            target = ref.index[ref.index <= target][-1]
    print(f'\n=== HEDEF GÜN: {target.date()} ({target.strftime("%A")}) ===')

    # Rejim (XU100 son 10g)
    xu = ALL.get('XU100')
    if xu is not None and target in xu.index:
        i_xu = xu.index.get_loc(target)
        rejim, chg = te.detect_rejim(xu['Close'], i_xu, lookback=10)
        print(f'Rejim: {rejim} (XU100 10g {chg:+.2f}%)')
    else:
        rejim, chg = 'BILINMEZ', 0
        print('Rejim: BİLİNMEZ (XU100 verisi yok)')

    agirlik = te.REJIM_AGIRLIK[rejim]

    # Tarama
    rows = []
    for tk, df in ALL.items():
        if target not in df.index:
            continue
        i = df.index.get_loc(target)
        f = te.features(df, i)
        if f is None:
            continue
        if f['vol_tl'] < min_vol_tl:
            continue

        sc = te.score_row(f, agirlik)
        rows.append({
            'tk': tk,
            'fiyat': round(f['close'], 2),
            'kat': sc['kat'],
            'skor': round(sc['skor'], 1),
            'A': round(sc['A'], 0), 'C': round(sc['C'], 0),
            'E': round(sc['E'], 0), 'D': round(sc['D'], 0),
            'RSI': round(f['rsi'], 0),
            '52H%': round(f['pos_52h'], 0),
            'BBrank': round(f['bb_rank'], 0),
            'VolT': round(f['vr_t'], 2),
            'NearH20': round(f['near_h20'], 0),
            'Ret10g': round(f['ret_10g'], 1),
            'vol_mTL': round(f['vol_tl'] / 1e6, 1),
        })

    df = pd.DataFrame(rows).sort_values('skor', ascending=False)
    df.to_csv(f'tavan_skoru_{target.date()}.csv', index=False)

    # Rapor
    print(f'\n{len(df)} hisse tarandı (likidite > {min_vol_tl/1e6:.1f}M TL).')
    print(f'Rejim çarpanı: {agirlik}\n')

    print(f'\n=== TOP {top_n} TAVAN ADAYI ({target.date()} kapanışı → ertesi gün için) ===')
    cols = ['tk', 'fiyat', 'skor', 'kat', 'A', 'C', 'E', 'D', 'RSI', '52H%',
            'BBrank', 'VolT', 'NearH20', 'Ret10g', 'vol_mTL']
    print(df.head(top_n)[cols].to_string(index=False))

    # Kalıp bazlı top
    print('\n\n=== HER KALIPTA EN GÜÇLÜ 5 ===')
    for k in ['A', 'C', 'E', 'D']:
        sub = df[df['kat'] == k].head(5)
        print(f'\n--- {k} — {te.KATEGORI_ACK[k]} ---')
        if len(sub):
            print(sub[['tk', 'fiyat', 'skor', 'RSI', '52H%', 'BBrank', 'VolT',
                       'NearH20', 'Ret10g', 'vol_mTL']].to_string(index=False))
        else:
            print('  (uygun hisse yok)')

    # Confluence (2+ kalıp 55+)
    conf = df[((df['A'] >= 55).astype(int) + (df['C'] >= 55).astype(int) +
               (df['E'] >= 55).astype(int) + (df['D'] >= 55).astype(int)) >= 2]
    print(f'\n\n=== CONFLUENCE (2+ kalıp uyumu, 55+ skor) — {len(conf)} hisse ===')
    if len(conf):
        print(conf.head(15)[cols].to_string(index=False))

    print(f'\nTam tablo: tavan_skoru_{target.date()}.csv')
    return df


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('date', nargs='?', help='Hedef tarih YYYY-MM-DD (boş = en son gün)')
    parser.add_argument('--top', type=int, default=30, help='Kaç hisse listelensin')
    parser.add_argument('--min-vol', type=float, default=te.MIN_VOL_TL, help='Min günlük hacim (TL)')
    args = parser.parse_args()
    run(args.date, top_n=args.top, min_vol_tl=args.min_vol)
