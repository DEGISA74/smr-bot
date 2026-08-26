import pandas as pd, numpy as np, glob, os, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
VER = 'veriler'
ENDEKS = {'XU100','XU030','XU050','XBANK','XUSIN','XUMAL','XU100D','XGIDA','XBANA'}
data = {}; liq = []
for f in glob.glob(f'{VER}/*.IS_1d.parquet'):
    tk = os.path.basename(f).replace('.IS_1d.parquet', '')
    if tk in ENDEKS: continue
    try:
        d = pd.read_parquet(f); d = d[~d.index.duplicated()].sort_index()
        if len(d) < 220: continue
        data[tk] = d; liq.append((tk, float((d['Close'] * d['Volume']).tail(120).median())))
    except Exception: continue
liq.sort(key=lambda x: -x[1]); bist100 = [t for t, _ in liq[:100]]
xu = pd.read_parquet(f'{VER}/XU100.IS_1d.parquet'); xu = xu[~xu.index.duplicated()].sort_index(); xuc = xu['Close']


def find_swings(arr, lookback=8):
    highs, lows = [], []; n = len(arr)
    for i in range(lookback, n - lookback):
        w = arr[i - lookback:i + lookback + 1]
        if arr[i] >= w.max() - 1e-9: highs.append((i, arr[i]))
        if arr[i] <= w.min() + 1e-9: lows.append((i, arr[i]))
    return highs, lows


def _val_cup(cup_arr, left_i, dip_i, right_i, r2, min_r2=0.78):
    if r2 < min_r2: return False
    span = right_i - left_i
    if span <= 0: return False
    cent = (dip_i - left_i) / span
    if not (0.30 <= cent <= 0.70): return False
    rel = dip_i - left_i; lh = cup_arr[:max(2, rel + 1)]; rh = cup_arr[rel:]
    if len(lh) >= 3 and len(rh) >= 3:
        ls = np.polyfit(np.arange(len(lh)), lh, 1)[0]; rs = np.polyfit(np.arange(len(rh)), rh, 1)[0]
        if not (ls < 0 and rs > 0): return False
    return True


def _val_tobo(sl1_i, sl1_v, sl2_i, sl2_v, sl3_i, sl3_v, sh1_i, sh1_v, sh2_i, sh2_v, bar_total):
    if not (sl2_v < sl1_v * 0.92 and sl2_v < sl3_v * 0.92): return False, None
    tl = sl2_i - sl1_i; tr = sl3_i - sl2_i
    if tl <= 0 or tr <= 0: return False, None
    if not (0.4 <= tl / tr <= 2.5): return False, None
    if abs(sh1_v - sh2_v) / sh1_v > 0.15: return False, None
    nslope = (sh2_v - sh1_v) / (sh2_i - sh1_i) if sh2_i != sh1_i else 0.0
    neck = sh2_v + nslope * (bar_total - 1 - sh2_i)
    neck = max(min(sh1_v, sh2_v) * 0.90, min(neck, max(sh1_v, sh2_v) * 1.12))
    return True, neck


def _dbl_bottom(sw_l_y, sw_h_y, curr, bar_total, eq=0.04, mind=0.12, maxd=0.40, mindur=25, maxdur=180, mfd=12.0):
    if len(sw_l_y) < 2 or len(sw_h_y) < 1: return None
    for j in range(len(sw_l_y) - 1, 0, -1):
        d2_i, d2_v = sw_l_y[j]
        if bar_total - d2_i > 60: continue
        for k in range(j - 1, -1, -1):
            d1_i, d1_v = sw_l_y[k]; dur = d2_i - d1_i
            if not (mindur <= dur <= maxdur): continue
            if abs(d1_v - d2_v) / d1_v > eq: continue
            mh = [(i, v) for i, v in sw_h_y if d1_i < i < d2_i]
            if not mh: continue
            neck_i, neck_v = max(mh, key=lambda x: x[1]); base = min(d1_v, d2_v); depth = (neck_v - base) / base
            if not (mind <= depth <= maxd): continue
            if curr < d2_v * 0.98: continue
            tgt = neck_v + (neck_v - base); risk = max(curr - d2_v * 0.98, 0.01)
            if (tgt - curr) / risk < 1.0: continue
            dist = ((neck_v - curr) / neck_v * 100) if curr < neck_v else 0
            brk = neck_v * 0.97 <= curr <= neck_v * 1.10
            frm = (curr > d2_v * 1.01 and curr < neck_v * 0.97 and dist <= mfd)
            if not (brk or frm): continue
            return dict(type='W', neck=neck_v, state='break' if brk else 'form', dist=dist, depth=depth)
    return None


def detect(close, high, low, open_, i, sw_h, sw_l):
    bar_total = i + 1; curr = float(close[i])
    sw_h_y = [(k, v) for k, v in sw_h if i - 252 <= k <= i - 8]
    sw_l_y = [(k, v) for k, v in sw_l if i - 252 <= k <= i - 8]

    def clean(s, e):
        s = max(0, s); e = min(bar_total, e + 1)
        if e - s < 5: return True
        b = np.abs(close[s:e] - open_[s:e]); w = (high[s:e] - low[s:e]) - b
        mb = np.median(b)
        if mb < 1e-9: return False
        return np.median(w) <= 2.0 * mb

    # FINCAN-KULP
    if len(sw_h_y) >= 2 and len(sw_l_y) >= 1:
        for ri in range(len(sw_h_y) - 1, 0, -1):
            sh2_i, sh2_v = sw_h_y[ri]
            if bar_total - sh2_i > 60: continue
            for li in range(ri - 1, max(ri - 12, -1), -1):
                sh1_i, sh1_v = sw_h_y[li]; cd = sh2_i - sh1_i
                if not (40 <= cd <= 252): continue
                cl = [(k, v) for k, v in sw_l_y if sh1_i < k < sh2_i]
                if not cl: continue
                sl_i, sl_v = min(cl, key=lambda x: x[1]); depth = (sh1_v - sl_v) / sh1_v
                if not (0.12 <= depth <= 0.55): continue
                if abs(sh1_v - sh2_v) / sh1_v > 0.06: continue
                try:
                    ca = close[sh1_i:sh2_i + 1].astype(float)
                    if len(ca) < 10: continue
                    cs = pd.Series(ca).ewm(span=5, adjust=False).mean().values
                    xf = np.linspace(0, 1, len(ca)); cf = np.polyfit(xf, cs, 2); yp = np.polyval(cf, xf)
                    sr = np.sum((cs - yp) ** 2); st_ = np.sum((cs - cs.mean()) ** 2); r2 = 1 - sr / st_ if st_ > 0 else 0
                    if cf[0] <= 0: continue
                except Exception: continue
                if not _val_cup(ca, sh1_i, sl_i, sh2_i, r2): continue
                if not clean(sh1_i, sh2_i): continue
                hl = [(k, v) for k, v in sw_l_y if k > sh2_i]
                if hl: hl_i, hl_v = hl[0]
                else:
                    aft = close[sh2_i:]
                    if len(aft) < 3: continue
                    rel = int(np.argmin(aft)); hl_v = float(aft[rel])
                if not (hl_v > sl_v + (sh2_v - sl_v) * 0.35): continue
                if not (hl_v > sh2_v * 0.82): continue
                tgt = sh2_v + (sh2_v - sl_v); risk = max(curr - hl_v * 0.98, 0.01)
                if (tgt - curr) / risk < 1.0: continue
                brk = sh2_v * 0.97 <= curr <= sh2_v * 1.10; frm = curr >= hl_v * 0.98 and not brk
                if not (brk or frm): continue
                dist = ((sh2_v - curr) / sh2_v * 100) if curr < sh2_v else 0
                return dict(type='CUP', neck=sh2_v, state='break' if brk else 'form', dist=dist, depth=depth)

    # TOBO
    if len(sw_h_y) >= 2 and len(sw_l_y) >= 3:
        for i_rs in range(len(sw_l_y) - 1, 1, -1):
            sl3_i, sl3_v = sw_l_y[i_rs]
            if bar_total - sl3_i > 60: continue
            for i_hd in range(i_rs - 1, 0, -1):
                sl2_i, sl2_v = sw_l_y[i_hd]
                for i_ls in range(i_hd - 1, max(i_hd - 8, -1), -1):
                    sl1_i, sl1_v = sw_l_y[i_ls]; dur = sl3_i - sl1_i
                    if not (40 <= dur <= 252): continue
                    if not (sl2_v < sl1_v * 0.95 and sl2_v < sl3_v * 0.95): continue
                    c1 = [(k, v) for k, v in sw_h_y if sl1_i < k < sl2_i]
                    c2 = [(k, v) for k, v in sw_h_y if sl2_i < k < sl3_i]
                    if not c1 or not c2: continue
                    sh1_i, sh1_v = max(c1, key=lambda x: x[1]); sh2_i, sh2_v = max(c2, key=lambda x: x[1])
                    ok, neck = _val_tobo(sl1_i, sl1_v, sl2_i, sl2_v, sl3_i, sl3_v, sh1_i, sh1_v, sh2_i, sh2_v, bar_total)
                    if not ok: continue
                    if abs(sl1_v - sl3_v) / sl1_v > 0.15: continue
                    rec = (sl3_v - sl2_v) / (neck - sl2_v) if (neck - sl2_v) > 0 else 0
                    if rec < 0.45: continue
                    if not clean(sl1_i, sl3_i): continue
                    tgt = neck + (neck - sl2_v); risk = max(curr - sl3_v * 0.98, 0.01)
                    if (tgt - curr) / risk < 1.0: continue
                    brk = neck * 0.97 <= curr <= neck * 1.08; frm = curr > sl3_v * 1.01 and curr < neck * 0.96
                    if not (brk or frm): continue
                    dist = ((neck - curr) / neck * 100) if curr < neck else 0
                    return dict(type='TOBO', neck=neck, state='break' if brk else 'form', dist=dist, depth=0)

    # W (cift dip)
    return _dbl_bottom(sw_l_y, sw_h_y, curr, bar_total)


allbreak = []; formstats = []
for tk in bist100:
    d = data[tk]; close = d['Close'].values; high = d['High'].values; low = d['Low'].values; open_ = d['Open'].values; vol = d['Volume'].values
    n = len(d); idx = d.index
    sw_h, sw_l = find_swings(close, 8)
    cs = d['Close']; ma = cs.rolling(20).mean(); sd = cs.rolling(20).std()
    trv = pd.concat([(d['High'] - d['Low']), (d['High'] - cs.shift()).abs(), (d['Low'] - cs.shift()).abs()], axis=1).max(axis=1)
    atr = trv.rolling(20).mean()
    sq = ((ma + 2 * sd < ma + 1.5 * atr) & (ma - 2 * sd > ma - 1.5 * atr)).fillna(False).values
    sma50 = cs.rolling(50).mean().values; sma200 = cs.rolling(200).mean().values; v20 = d['Volume'].rolling(20).mean().values
    last_brk = -999
    for i in range(150, n):
        r = detect(close, high, low, open_, i, sw_h, sw_l)
        if r is None: continue
        if r['state'] == 'form':
            formstats.append(dict(tk=tk, date=idx[i].date(), type=r['type'], dist=round(r['dist'], 1)))
        elif r['state'] == 'break' and i - last_brk > 20:
            last_brk = i; j = min(i + 40, n - 1); k20 = min(i + 20, n - 1)
            sqd = int(sq[max(0, i - 15):i].sum())
            vs = vol[i] / v20[i] if v20[i] > 0 else 0
            look = min(250, i); lo = np.nanmin(low[i - look:i + 1]); hi = np.nanmax(high[i - look:i + 1])
            pos52 = (close[i] - lo) / (hi - lo) * 100 if hi > lo else 50
            a200 = 1 if (sma200[i] == sma200[i] and close[i] > sma200[i]) else 0
            try:
                xp = xuc.index.get_indexer([idx[i]], method='ffill')[0]
                rs = (close[i] / close[i - 20] - 1) - (float(xuc.iloc[xp]) / float(xuc.iloc[xp - 20]) - 1) if xp >= 20 else 0
            except Exception: rs = 0
            Q = int(sqd >= 4) + int(vs >= 1.8) + int(pos52 >= 85) + int(a200) + int(rs > 0)
            allbreak.append(dict(tk=tk, date=idx[i].date(), type=r['type'], neck=round(r['neck'], 2),
                                 full=(i + 40 < n), Q=Q, sqd=sqd, vs=round(vs, 1), pos52=round(pos52), rs=round(rs * 100, 1),
                                 max20=round((float(np.nanmax(high[i + 1:k20 + 1])) / close[i] - 1) * 100, 1) if i + 1 < n else None,
                                 max40=round((float(np.nanmax(high[i + 1:j + 1])) / close[i] - 1) * 100, 1) if i + 1 < n else None,
                                 cl40=round((float(close[j]) / close[i] - 1) * 100, 1) if i + 1 < n else None))

B = pd.DataFrame(allbreak)
print('=== FORMASYON BOYUN KIRILIMLARI (BIST100) ===')
print('Toplam kirilim sinyali:', len(B), '| pattern dagilimi:', dict(B.type.value_counts()) if len(B) else {})
F = B[B.full].dropna(subset=['max40']) if len(B) else B
print(f'Tam-forward: {len(F)}')
if len(F):
    print('--- GENEL win-rate ---')
    print('max40>=20%%: %.0f%% | max40>=25%%: %.0f%% | cl40>=15%%: %.0f%% | ort max40 %.1f%% | ort cl40 %.1f%%' % (
        (F.max40 >= 20).mean() * 100, (F.max40 >= 25).mean() * 100, (F.cl40 >= 15).mean() * 100, F.max40.mean(), F.cl40.mean()))
    print('--- pattern bazinda ---')
    print(F.groupby('type').agg(N=('max40', 'size'), w20=('max40', lambda x: round((x >= 20).mean() * 100)),
                                ortmax=('max40', 'mean'), ortcl=('cl40', 'mean')).round(1).to_string())
if len(F):
    print('--- KALITE SKORU (formasyon + isaretler) bazinda win-rate ---')
    g = F.groupby('Q').agg(N=('max40', 'size'), w20=('max40', lambda x: round((x >= 20).mean() * 100)),
                           w25=('max40', lambda x: round((x >= 25).mean() * 100)), ortmax=('max40', 'mean')).round(1)
    print(g.to_string())
    for thr in (3, 4):
        s = F[F.Q >= thr]
        print('Q>=%d: %d sinyal | max40>=20%%: %.0f%% | max40>=25%%: %.0f%% | ort max40 %.1f%%' % (
            thr, len(s), (s.max40 >= 20).mean() * 100, (s.max40 >= 25).mean() * 100, s.max40.mean()))
print('--- TOASO/EREGL kirilimlari ---')
print(B[B.tk.isin(['TOASO', 'EREGL'])].to_string() if len(B) else 'yok')
print('--- en buyuk 12 ---')
print(B.sort_values('max40', ascending=False).head(12).to_string() if len(B) else 'yok')
print('\n=== OLUSAN (form) durum:', len(formstats), '| son 5 ===')
print(pd.DataFrame(formstats).tail(5).to_string() if formstats else 'yok')

print('\n\n################ ALARM ZAMANLAMASI (DEV HAREKETLER) ################')
TEST = ['EREGL', 'TOASO', 'HDFGS', 'MANAS', 'TCKRC', 'SARKY', 'TRALT', 'RALYH', 'KRDMD', 'SOKM']
for tk in TEST:
    if tk not in data:
        print(f'\n----- {tk} ----- (parquet yok)'); continue
    d = data[tk]; close = d['Close'].values; high = d['High'].values; low = d['Low'].values; open_ = d['Open'].values
    n = len(d); idx = d.index
    sw_h, sw_l = find_swings(close, 8)
    cs = d['Close']; ma = cs.rolling(20).mean(); sd = cs.rolling(20).std()
    trv = pd.concat([(d['High'] - d['Low']), (d['High'] - cs.shift()).abs(), (d['Low'] - cs.shift()).abs()], axis=1).max(axis=1)
    atr = trv.rolling(20).mean()
    sq = ((ma + 2 * sd < ma + 1.5 * atr) & (ma - 2 * sd > ma - 1.5 * atr)).fillna(False).values
    watches = []; breaks = []; last_b = -999
    for i in range(150, n):
        r = detect(close, high, low, open_, i, sw_h, sw_l)
        if r is None: continue
        sqd = int(sq[max(0, i - 15):i].sum())
        if r['state'] == 'form' and r['dist'] <= 6 and sqd >= 2:
            watches.append((i, idx[i].date(), float(close[i]), r['type'], round(r['dist'], 1)))
        elif r['state'] == 'break' and i - last_b > 20:
            last_b = i; j = min(i + 40, n - 1)
            peak = float(np.nanmax(high[i + 1:j + 1])) if i + 1 < n else float(close[i])
            breaks.append((i, idx[i].date(), float(close[i]), r['type'], peak))
    print(f'\n----- {tk} ----- (bugun {round(float(close[-1]),2)})')
    if not breaks:
        print('  ❌ Temiz formasyon (fincan/TOBO/W) bulunamadi — bu hisseyi formasyon-radari KACIRIR.')
        continue
    bb = max(breaks, key=lambda x: x[4] / x[2])  # en buyuk hareketli kirilim
    bi, bdate, bprice, btype, bpeak = bb
    pw = [w for w in watches if w[0] < bi and bi - w[0] <= 40]
    gain_brk = (bpeak / bprice - 1) * 100
    if pw:
        wi, wdate, wprice, wtype, wdist = pw[0]
        gain_w = (bpeak / wprice - 1) * 100
        print(f'  🤏 1.ALARM (takibe al): {wdate} @ {round(wprice,2)}  ({wtype}, boyna %{wdist})')
        print(f'  🚀 2.ALARM (kirildi):   {bdate} @ {round(bprice,2)}  ({btype})')
        print(f'  📈 Sonra zirve: {round(bpeak,2)}  → takibe-al fiyatindan +%{gain_w:.0f} | kirilimdan +%{gain_brk:.0f}')
    else:
        print(f'  🚀 SADECE kirilim alarmi (oncesinde yaklasma yakalanmadi): {bdate} @ {round(bprice,2)} ({btype}) → zirve +%{gain_brk:.0f}')
