#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIRSAT RADARI — formasyon motoru (app.py fincan/TOBO/cift-dip detektorlerinin
standalone cekirdegi). Hem backtest hem canli radar bunu import eder (tek kaynak).

detect_formation(close, high, low, open_, i, sw_h, sw_l) -> dict|None
  dict: type ('CUP'/'TOBO'/'W'), neck, state ('break'/'form'), dist, depth
"""
import numpy as np, pandas as pd, glob, os

import pattern_core  # 4 Tem 2026 — formasyon paketi senkronu (ana motor ile AYNI kurallar)

ENDEKS = {'XU100', 'XU030', 'XU050', 'XBANK', 'XUSIN', 'XUMAL', 'XU100D', 'XGIDA', 'XBANA'}


def find_swings(arr, lookback=8):
    highs, lows = [], []; n = len(arr)
    for i in range(lookback, n - lookback):
        w = arr[i - lookback:i + lookback + 1]
        if arr[i] >= w.max() - 1e-9: highs.append((i, arr[i]))
        if arr[i] <= w.min() + 1e-9: lows.append((i, arr[i]))
    return highs, lows


def find_swings_hl(high, low, close, lookback=8):
    """4 Tem 2026 senkron: pivotlar fitil uclarindan (High/Low) + hissenin
    volatilitesine uyarlanan esikle budama (pattern_core). Ana formasyon
    motoruyla ayni pivot kalitesi. Eski find_swings geriye uyum icin durur."""
    sw_h, _ = find_swings(np.asarray(high, dtype=float), lookback)
    _, sw_l = find_swings(np.asarray(low, dtype=float), lookback)
    thr = pattern_core.adaptive_threshold(np.asarray(close, dtype=float))
    return pattern_core.prune_pivots(sw_h, sw_l, thr)


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


def detect_formation(close, high, low, open_, i, sw_h, sw_l):
    """Tek bardaki (i) formasyon durumunu dondurur. Swing'ler i-8'e kadar onayli (nedensel)."""
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
                # 4 Tem senkron: on-trend — kupa oncesi yukselis sarti
                if not pattern_core.cup_pretrend_ok(close, sh1_i): continue
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
                # 4 Tem senkron: kulp suresi — kupaya oranla kisa olmali
                if not pattern_core.handle_dur_ok(sh2_i, bar_total, cd): continue
                tgt = sh2_v + (sh2_v - sl_v); risk = max(curr - hl_v * 0.98, 0.01)
                if (tgt - curr) / risk < 1.0: continue
                dist = ((sh2_v - curr) / sh2_v * 100) if curr < sh2_v else 0
                brk = sh2_v * 0.97 <= curr <= sh2_v * 1.10
                # 4 Tem senkron: OLUSAN icin boyuna max %12 uzaklik
                frm = curr >= hl_v * 0.98 and not brk and dist <= pattern_core.PC['form_max_dist']
                if not (brk or frm): continue
                return dict(type='CUP', neck=float(sh2_v), state='break' if brk else 'form', dist=dist, depth=depth)

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
                    # 4 Tem senkron: on-trend — TOBO oncesi dusus sarti
                    if not pattern_core.tobo_pretrend_ok(close, sl1_i, sl2_v): continue
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
                    dist = ((neck - curr) / neck * 100) if curr < neck else 0
                    brk = neck * 0.97 <= curr <= neck * 1.08
                    # 4 Tem senkron: OLUSAN icin boyuna max %12 uzaklik
                    frm = (curr > sl3_v * 1.01 and curr < neck * 0.96
                           and dist <= pattern_core.PC['form_max_dist'])
                    if not (brk or frm): continue
                    return dict(type='TOBO', neck=float(neck), state='break' if brk else 'form', dist=dist, depth=0)

    # W (cift dip)
    return _dbl_bottom(sw_l_y, sw_h_y, curr, bar_total)


def quality_signals(d, i, xuc):
    """5 kalite isareti + Q skoru. d=DataFrame, i=bar index, xuc=XU100 close serisi."""
    close = d['Close'].values; high = d['High'].values; low = d['Low'].values; vol = d['Volume'].values
    cs = d['Close']; ma = cs.rolling(20).mean(); sd = cs.rolling(20).std()
    trv = pd.concat([(d['High'] - d['Low']), (d['High'] - cs.shift()).abs(), (d['Low'] - cs.shift()).abs()], axis=1).max(axis=1)
    atr = trv.rolling(20).mean()
    sq = ((ma + 2 * sd < ma + 1.5 * atr) & (ma - 2 * sd > ma - 1.5 * atr)).fillna(False).values
    sma200 = cs.rolling(200).mean().values; sma50v = cs.rolling(50).mean().values; v20 = d['Volume'].rolling(20).mean().values
    ext = float(close[i] / sma50v[i]) if (sma50v[i] == sma50v[i] and sma50v[i] > 0) else 1.0
    sqd = int(sq[max(0, i - 15):i].sum())
    vs = vol[i] / v20[i] if v20[i] > 0 else 0
    look = min(250, i); lo = np.nanmin(low[i - look:i + 1]); hi = np.nanmax(high[i - look:i + 1])
    pos52 = (close[i] - lo) / (hi - lo) * 100 if hi > lo else 50
    a200 = (sma200[i] == sma200[i] and close[i] > sma200[i])
    rs = 0.0
    try:
        xp = xuc.index.get_indexer([d.index[i]], method='ffill')[0]
        if xp >= 20: rs = (close[i] / close[i - 20] - 1) - (float(xuc.iloc[xp]) / float(xuc.iloc[xp - 20]) - 1)
    except Exception: pass
    Q = int(sqd >= 4) + int(vs >= 1.8) + int(pos52 >= 85) + int(a200) + int(rs > 0)
    return dict(sqd=sqd, vsurge=round(vs, 1), pos52=round(pos52), a200=int(a200), rs=round(rs * 100, 1), ext=round(ext, 2), Q=Q)


def classify_state(r, q, close, high, i):
    """Bir formasyon sonucunu kutuya ayirir (scan_universe + tek-hisse ortak).
    Doner: (kutu, durum). kutu: 'kirdi'/'sikis'/'hazir'.
    durum (sadece kirdi): taze/retest/extended/fail/retest_wait, digerlerinde ''."""
    if r['state'] == 'break':
        neck = r['neck']; rh = float(np.nanmax(high[max(0, i - 10):i + 1]))
        ran = rh >= neck * 1.05
        if close[i] < neck * 0.98:
            durum = 'fail'
        elif close[i] > neck * 1.05:
            durum = 'extended'
        elif close[i] >= neck and ran:
            durum = 'retest'
        elif close[i] >= neck:
            durum = 'taze'
        else:
            durum = 'retest_wait'
        return 'kirdi', durum
    elif r['dist'] <= 6 and q.get('sqd', 0) >= 2 and q.get('a200') == 1:
        return 'sikis', ''   # trend yukari (a200) + boyna yakin + sikisma
    else:
        return 'hazir', ''


def scan_universe(veriler_dir, lik_taban=500_000_000):
    """Tum likit (>=lik_taban TL/gun) parquet'leri tarar, 3 kutuya ayirir.
    Doner: (hazir, sikis, kirdi, target_date). kirdi satirlarinda 'durum':
      taze (yeni kirdi, ustunde) / retest (boyna dondu, ustunde tutuyor) /
      extended (ucmus) / fail (altina kapandi) / retest_wait (hemen altinda)."""
    xu = pd.read_parquet(os.path.join(veriler_dir, 'XU100.IS_1d.parquet'))
    xu = xu[~xu.index.duplicated()].sort_index(); xuc = xu['Close']
    today = pd.Timestamp.now(tz='UTC').date()
    xt = xu[xu.index.date >= today]
    drop_today = (not xt.empty) and float(xt.iloc[-1]['Volume']) <= 0   # seans-ici yarim bar korumasi
    if drop_today:
        xuc = xuc[xuc.index.date < today]
    hazir, sikis, kirdi = [], [], []
    target_date = None
    for f in glob.glob(os.path.join(veriler_dir, '*.IS_1d.parquet')):
        tk = os.path.basename(f).replace('.IS_1d.parquet', '')
        if tk in ENDEKS:
            continue
        try:
            d = pd.read_parquet(f); d = d[~d.index.duplicated()].sort_index()
            if drop_today:
                d = d[d.index.date < today]
            if len(d) < 200:
                continue
            if float((d['Close'] * d['Volume']).tail(20).median()) < lik_taban:
                continue
            close = d['Close'].values; high = d['High'].values; low = d['Low'].values; open_ = d['Open'].values
            i = len(d) - 1
            # 4 Tem senkron: fitil pivotlari + adaptif budama (ana motor ile ayni)
            sw_h, sw_l = find_swings_hl(high, low, close, 8)
            r = detect_formation(close, high, low, open_, i, sw_h, sw_l)
            if r is None:
                continue
            target_date = d.index[-1]
            q = quality_signals(d, i, xuc)
            row = dict(tk=tk, type=r['type'], neck=round(r['neck'], 2), fiyat=round(float(close[i]), 2),
                       dist=round(r['dist'], 1), Q=q['Q'], sqd=q['sqd'], pos52=q['pos52'],
                       a200=q['a200'], rs=q['rs'], ext=q['ext'])
            kutu, durum = classify_state(r, q, close, high, i)
            if durum:
                row['durum'] = durum
            (kirdi if kutu == 'kirdi' else sikis if kutu == 'sikis' else hazir).append(row)
        except Exception:
            continue
    kirdi.sort(key=lambda x: -x['Q']); sikis.sort(key=lambda x: x['dist']); hazir.sort(key=lambda x: x['dist'])
    return hazir, sikis, kirdi, target_date
