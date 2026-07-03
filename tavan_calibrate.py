"""
TAVAN AĞIRLIK KALİBRASYONU — veriyle öneri üretir (auto-apply YOK)
==================================================================
REJIM_AGIRLIK çarpanlarını (rejim × kalıp = 5×4) geçmiş veriye göre optimize eder.
Elle "Tur 2" ayarının yerine veriye dayalı bir ÖNERİ çıkarır.

DOKTRIN (CLAUDE.md): kalibrasyon ÖLÇER, insan karar verir. Bu script canlı motoru
değiştirmez — sadece `tavan_weights_onerilen.json` + karşılaştırma raporu üretir.
Beğenirsen ağırlıkları HEM tavan_engine.py HEM app.py'de elle güncellersin;
tavan_drift_guard.py ikisinin senkron kaldığını doğrular.

Overfit koruması: walk-forward — ağırlıklar TRAIN (ilk %70) günlerde optimize edilir,
metrik VALIDATION (son %30) günlerde raporlanır. Val'de kazanım yoksa uygulama.

Metrik: TOP-30 listedeki toplam gerçek tavan yakalama (recall proxy).

Çalıştırma:  python tavan_calibrate.py            # varsayılan
             python tavan_calibrate.py --top 30   # metrik penceresi
Gerekir: veriler/*.IS_1d.parquet (VPS'te).
"""
import pandas as pd
import numpy as np
import glob, os, json, argparse, warnings
import tavan_engine as te
warnings.filterwarnings('ignore')

VERILER = 'veriler'
LOOKBACK_DAYS = 60
TRAIN_FRAC = 0.70
INDEKSLER = ('XU100', 'XU030', 'XU050', 'XBANK', 'XUSIN', 'XUMAL')


def load_data():
    ALL = {}
    for f in glob.glob(f'{VERILER}/*.IS_1d.parquet'):
        tk = os.path.basename(f).replace('.IS_1d.parquet', '')
        if tk in INDEKSLER:
            continue
        try:
            df = pd.read_parquet(f)
            if len(df) >= 100:
                ALL[tk] = df
        except Exception:
            pass
    xu = pd.read_parquet(f'{VERILER}/XU100.IS_1d.parquet')
    return ALL, xu


def precompute(ALL, xu, top_n):
    """Her hedef gün için: rejim + [(feat, tavan_next?)] + havuz. Feature'lar bir kez
    hesaplanır (pahalı kısım); sonra farklı ağırlıklar ucuza denenir."""
    ref = next(iter(ALL.values()))
    all_dates = ref.index.tolist()
    target_dates = all_dates[-LOOKBACK_DAYS - 1:-1]
    days = []
    for target in target_dates:
        if target not in xu.index:
            continue
        i_xu = xu.index.get_loc(target)
        rejim, _ = te.detect_rejim(xu['Close'], i_xu, lookback=10)
        idx_in_ref = all_dates.index(target)
        next_date = all_dates[idx_in_ref + 1]
        rows = []
        n_tavan = 0
        for tk, df in ALL.items():
            if target not in df.index:
                continue
            i = df.index.get_loc(target)
            feat = te.features(df, i)
            if feat is None or feat['vol_tl'] < te.MIN_VOL_TL:
                continue
            # T+1 gerçek getiri
            tavan_next = False
            if next_date in df.index:
                j = df.index.get_loc(next_date)
                if j >= 1:
                    pct = (df.iloc[j]['Close'] / df.iloc[j - 1]['Close'] - 1) * 100
                    tavan_next = pct >= te.TAVAN_ESIK
            rows.append((feat, tavan_next))
            if tavan_next:
                n_tavan += 1
        if rows and n_tavan >= 0:
            days.append({'rejim': rejim, 'rows': rows, 'top_n': top_n})
    return days


def metric(days, weights):
    """TOP-N listedeki toplam gerçek tavan sayısı (tüm günler)."""
    hits = 0
    total_tavan = 0
    for d in days:
        agr = weights[d['rejim']]
        scored = []
        for feat, tav in d['rows']:
            sc = te.score_row(feat, agr)
            scored.append((sc['skor'], tav))
            if tav:
                total_tavan += 1
        scored.sort(key=lambda x: x[0], reverse=True)
        hits += sum(1 for _, tav in scored[:d['top_n']] if tav)
    return hits, total_tavan


def coordinate_descent(train, base_weights, passes=2):
    """Her (rejim, kalıp) çarpanını sabit bir aday kümesinde tek tek en iyiye çeker."""
    import copy
    weights = copy.deepcopy(base_weights)
    best_hits, _ = metric(train, weights)
    CANDS = [0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3]
    for _ in range(passes):
        improved = False
        for rejim in ['HIZLI_RALLI', 'ILIMLI_YUKARI', 'YATAY', 'ZAYIF', 'DUSUS']:
            for kat in ['A', 'C', 'E', 'D']:
                cur = weights[rejim][kat]
                best_val = cur
                for cand in CANDS:
                    if cand == cur:
                        continue
                    weights[rejim][kat] = cand
                    h, _ = metric(train, weights)
                    if h > best_hits:
                        best_hits = h
                        best_val = cand
                        improved = True
                weights[rejim][kat] = best_val
        if not improved:
            break
    return weights, best_hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--top', type=int, default=30, help='Metrik TOP-N penceresi')
    args = ap.parse_args()

    print('Veriler yükleniyor...')
    ALL, xu = load_data()
    print(f'{len(ALL)} hisse + XU100.')
    print('Feature ön-hesap (bir kez)...')
    days = precompute(ALL, xu, args.top)
    if len(days) < 6:
        print(f'Yetersiz gün ({len(days)}). Çıkılıyor.')
        return
    split = int(len(days) * TRAIN_FRAC)
    train, val = days[:split], days[split:]
    print(f'{len(days)} gün · train {len(train)} · val {len(val)}\n')

    base = te.REJIM_AGIRLIK
    tr0, tr_tot = metric(train, base)
    va0, va_tot = metric(val, base)
    print(f'MEVCUT ağırlıklar → train {tr0}/{tr_tot} tavan · val {va0}/{va_tot} tavan (TOP {args.top})')

    print('Kalibrasyon (coordinate descent)...')
    new_w, tr1 = coordinate_descent(train, base)
    va1, _ = metric(val, new_w)
    print(f'ÖNERİLEN ağırlıklar → train {tr1}/{tr_tot} tavan · val {va1}/{va_tot} tavan\n')

    # Rapor: rejim × kalıp değişim tablosu
    print('=== ÖNERİLEN DEĞİŞİKLİKLER (mevcut → öneri) ===')
    changed = False
    for rejim in ['HIZLI_RALLI', 'ILIMLI_YUKARI', 'YATAY', 'ZAYIF', 'DUSUS']:
        diffs = []
        for kat in ['A', 'C', 'E', 'D']:
            a, b = base[rejim][kat], new_w[rejim][kat]
            if abs(a - b) > 1e-9:
                diffs.append(f'{kat} {a}→{b}')
                changed = True
        if diffs:
            print(f'  {rejim:<14} {" · ".join(diffs)}')
    if not changed:
        print('  (mevcut ağırlıklar zaten en iyisi — değişiklik yok)')

    verdict = 'UYGULA' if va1 > va0 else ('NÖTR' if va1 == va0 else 'UYGULAMA')
    print(f'\nVALIDATION KARARI: {verdict}  (val {va0} → {va1})')
    if verdict != 'UYGULA':
        print('  ⚠️ Val setinde kazanım yok — overfit riski. Ağırlıkları DEĞİŞTİRME.')

    out = {
        'generated_top_n': args.top,
        'train_gun': len(train), 'val_gun': len(val),
        'mevcut': {'train_hit': tr0, 'val_hit': va0},
        'onerilen': {'train_hit': tr1, 'val_hit': va1},
        'verdict': verdict,
        'REJIM_AGIRLIK_onerilen': new_w,
    }
    with open('tavan_weights_onerilen.json', 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print('\n✅ Öneri yazıldı: tavan_weights_onerilen.json')
    print('   Uygulamak istersen: REJIM_AGIRLIK\'ı HEM tavan_engine.py HEM app.py\'de')
    print('   güncelle, sonra `python tavan_drift_guard.py` ile senkronu doğrula.')


if __name__ == '__main__':
    main()
