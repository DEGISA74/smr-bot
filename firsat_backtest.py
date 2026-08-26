#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIRSAT RADARI — sürekli backtest.
firsat_radar_log.jsonl'deki her sinyalin forward getirisini (parquet'ten) ölçer,
durum / formasyon / Q bazında GERÇEK win-rate çıkarır. İstediğinde çalıştır:
  python firsat_backtest.py
"""
import pandas as pd, numpy as np, json, os, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE = os.path.dirname(os.path.abspath(__file__))
VER = os.path.join(BASE, 'veriler')
LOG = os.path.join(BASE, 'firsat_radar_log.jsonl')

if not os.path.exists(LOG):
    print('Henüz log yok — radar canlı çalışıp sinyal biriktirince backtest yapılabilir.')
    sys.exit(0)

rows = []
with open(LOG, encoding='utf-8') as fh:
    for line in fh:
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
if not rows:
    print('Log boş.'); sys.exit(0)

_cache = {}
def _load(tk):
    if tk not in _cache:
        p = os.path.join(VER, f'{tk}.IS_1d.parquet')
        try:
            d = pd.read_parquet(p); d = d[~d.index.duplicated()].sort_index()
            _cache[tk] = d
        except Exception:
            _cache[tk] = None
    return _cache[tk]

out = []
for r in rows:
    d = _load(r['tk'])
    if d is None:
        continue
    try:
        pos = d.index.get_indexer([pd.Timestamp(r['date'])], method='ffill')[0]
    except Exception:
        continue
    if pos < 0:
        continue
    close = d['Close'].values; high = d['High'].values; n = len(d)
    entry = float(close[pos])
    rec = dict(r)
    for H in (10, 20, 40):
        if pos + H < n:
            rec[f'max{H}'] = round((float(np.nanmax(high[pos + 1:pos + H + 1])) / entry - 1) * 100, 1)
            rec[f'cl{H}'] = round((float(close[pos + H]) / entry - 1) * 100, 1)
            rec[f'full{H}'] = True
        else:
            rec[f'max{H}'] = round((float(np.nanmax(high[pos + 1:n])) / entry - 1) * 100, 1) if pos + 1 < n else None
            rec[f'cl{H}'] = round((float(close[-1]) / entry - 1) * 100, 1) if pos + 1 < n else None
            rec[f'full{H}'] = False
    out.append(rec)

S = pd.DataFrame(out)
print(f'=== FIRSAT RADARI SÜREKLİ BACKTEST ===')
print(f'Toplam loglanmış sinyal: {len(S)} | tarih aralığı: {S.date.min()} → {S.date.max()}')
F = S[S.get('full20', False)].copy() if 'full20' in S else S.iloc[0:0]
print(f'20 gün forward dolmuş: {len(F)}')
if len(F) == 0:
    print('\nHenüz forward dolan sinyal yok (en yeni sinyaller ~20 iş günü beklemeli). Birkaç hafta sonra tekrar çalıştır.')
    sys.exit(0)


def seg(col, win_metric='max20', win_thr=10):
    F['_w'] = (F[win_metric] >= win_thr).astype(int)
    g = F.groupby(col).agg(N=('_w', 'size'), winrate=('_w', lambda x: round(x.mean() * 100)),
                           ort_max20=('max20', 'mean'), ort_max40=('max40', 'mean')).round(1)
    print(f'\n--- {col} bazında ({win_metric}>=%{win_thr} = kazanç) ---')
    print(g.to_string())


print(f'\nGENEL: {len(F)} sinyal · max20>=%10 hit: {round((F.max20 >= 10).mean() * 100)}% · ort max20 {F.max20.mean():.1f}% · ort max40 {F.max40.mean():.1f}%')
seg('durum')
seg('kutu')
seg('type')
if 'Q' in F:
    seg('Q')
print('\n--- en iyi 10 sinyal (max40) ---')
print(S.sort_values('max40', ascending=False).head(10)[['date', 'tk', 'kutu', 'durum', 'type', 'Q', 'max20', 'max40']].to_string())
