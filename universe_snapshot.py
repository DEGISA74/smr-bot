#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UNIVERSE SNAPSHOT — tüm BIST evreninin günlük feature fotoğrafı + ileri getiri.

Tarama-tetik backtest'inin (TARAMA PERFORMANSI paneli) kör noktasını kapatır:
HER hisse HER gün ölçülür — tarama seçsin ya da seçmesin. Böylece seçim yanlılığı
olmadan "hangi skor gerçekten öngörüyor" sorusu tüm popülasyonda yanıtlanır.

Standalone — canlı app.py'ye DOKUNMAZ. veriler/*.parquet'ten okur, patron.db'ye yazar.
Feature formülleri scanner_karne._feats ile BİREBİR aynı (vektörel hızlandırılmış;
--verify ile eşitlik kanıtlanır). Tüm hesaplar point-in-time (lookahead YOK).

Çıktı : patron.db → universe_snapshot tablosu  +  universe_report.md (feature güç sırası)
Çalıştır:
  python universe_snapshot.py --backfill-days 120   # geçmiş fotoğraf + ileri getiri
  python universe_snapshot.py --daily               # bugünü ekle + bekleyen getirileri doldur
  python universe_snapshot.py --report-only         # sadece güç sıralama raporu
  python universe_snapshot.py --verify              # _feats ile eşitlik testi
"""
import sqlite3, os, glob, sys, argparse, time, warnings
import numpy as np, pandas as pd
from datetime import datetime
warnings.filterwarnings('ignore')
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # Windows cp1254 konsol koruması
except Exception:
    pass

DB = 'patron.db'
VERILER = 'veriler'
FWD = [5, 10, 20]
MIN_BARS = 80          # feature güvenilirliği için min geçmiş

# ───────────────────────── FEATURE MOTORU (scanner_karne._feats vektörel ikizi) ─────────
def _cmf_series(df, n):
    rng = (df['High'] - df['Low']).replace(0, np.nan)
    mfv = (((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / rng * df['Volume']).fillna(0)
    vol = df['Volume'].rolling(n).sum()
    return (mfv.rolling(n).sum() / vol).replace([np.inf, -np.inf], np.nan)


def _cmf_state(c5, c20):
    if pd.isna(c5) or pd.isna(c20):
        return None
    if c5 > 0 and c20 < 0:   return 'turning_up'
    if c5 < 0 and c20 > 0:   return 'turning_down'
    if c5 > .05 and c20 > .05:   return 'strong_pos'
    if c5 < -.05 and c20 < -.05: return 'strong_neg'
    if c20 > .05:  return 'pos'
    if c20 < -.05: return 'neg'
    return 'neutral'


def _vp_shape(seg):
    """60 barlık hacim profili şekli — akumulasyon/dagitim/denge (scanner_karne ile birebir)."""
    try:
        pr = ((seg['High'] + seg['Low']) / 2).values
        vo = seg['Volume'].values
        hist, edges = np.histogram(pr, bins=30, weights=vo)
        tot = hist.sum()
        if tot <= 0:
            return None
        poc = (edges[int(np.argmax(hist))] + edges[int(np.argmax(hist)) + 1]) / 2
        order = np.argsort(hist)[::-1]; cum = 0; inc = set()
        for i in order:
            inc.add(int(i)); cum += hist[i]
            if cum / tot >= .70:
                break
        il = sorted(inc); val, vah = edges[il[0]], edges[il[-1] + 1]
        pos = (poc - val) / (vah - val) if vah > val else .5
        return 'akumulasyon' if pos < .4 else 'dagitim' if pos > .6 else 'denge'
    except Exception:
        return None


def _rsi_win(c, n):
    d = c.diff()
    g = d.where(d > 0, 0).rolling(n).mean()
    ls = (-d.where(d < 0, 0)).rolling(n).mean()
    return 100 - 100 / (1 + g / ls)


def _mfi_win(df, n):
    """Money Flow Index — hacim ağırlıklı RSI."""
    tp = (df['High'] + df['Low'] + df['Close']) / 3
    rmf = tp * df['Volume']
    pos = rmf.where(tp > tp.shift(1), 0.0).rolling(n).sum()
    neg = rmf.where(tp < tp.shift(1), 0.0).rolling(n).sum()
    return (100 - 100 / (1 + pos / neg)).replace([np.inf, -np.inf], np.nan)


def _dual_state(short, long_, ob=70, os_=30, gap=3):
    """RSI/MFI gibi 0-100 göstergeler için kısa-vs-uzun pencere momentum durumu."""
    if pd.isna(short) or pd.isna(long_):
        return None
    if long_ >= ob:           return 'asiri_alim'
    if long_ <= os_:          return 'asiri_satim'
    if short > long_ + gap:   return 'guclenen'   # kısa pencere uzunu geçti → momentum doğuyor
    if short < long_ - gap:   return 'zayiflayan'  # kısa pencere altta → momentum sönüyor
    return 'notr'


def _obv_div_state(pchg, ochg):
    """OBV–fiyat uyumu (20g): birikim/dağıtım izi."""
    if pd.isna(pchg) or pd.isna(ochg):
        return None
    if pchg > 0 and ochg < 0:  return 'ayi_uyumsuzluk'    # fiyat yukarı, OBV aşağı → dağıtım
    if pchg < 0 and ochg > 0:  return 'boga_uyumsuzluk'   # fiyat aşağı, OBV yukarı → birikim
    if pchg > 0 and ochg > 0:  return 'teyit_yukari'
    if pchg < 0 and ochg < 0:  return 'teyit_asagi'
    return 'notr'


def feature_frame(df):
    """Tüm feature SERİLERİNİ vektörel üretir (her satır = o güne kadar point-in-time).
    Seviye + DİNAMİK (dual-window state + slope) + MFI + OBV."""
    c = df['Close']
    out = pd.DataFrame(index=df.index)
    out['close'] = c
    # ── SEVİYE ──
    roll_hi = df['High'].rolling(252, min_periods=60).max()
    roll_lo = df['Low'].rolling(252, min_periods=60).min()
    out['p52'] = ((c - roll_lo) / (roll_hi - roll_lo) * 100).where(roll_hi > roll_lo)
    out['rsi'] = _rsi_win(c, 14)
    out['mfi'] = _mfi_win(df, 14)
    c5 = _cmf_series(df, 5); c20 = _cmf_series(df, 20)
    out['cmf'] = [_cmf_state(a, b) for a, b in zip(c5, c20)]
    out['cmf20'] = c20   # numerik (birleşik çapraz-kesitsel skor için)
    ma = c.rolling(20).mean(); sd = c.rolling(20).std()
    atr = (df['High'] - df['Low']).rolling(20).mean()
    sqb = (((ma + 2 * sd) < (ma + 1.5 * atr)) & ((ma - 2 * sd) > (ma - 1.5 * atr))).fillna(False)
    out['sq'] = sqb.groupby((~sqb).cumsum()).cumsum().astype(int)
    # ── DİNAMİK: dual-window state (kısa vs uzun) ──
    rsi5 = _rsi_win(c, 5); mfi5 = _mfi_win(df, 5)
    out['rsi_dual'] = [_dual_state(s, l) for s, l in zip(rsi5, out['rsi'])]
    out['mfi_dual'] = [_dual_state(s, l) for s, l in zip(mfi5, out['mfi'])]
    # ── DİNAMİK: slope (5g değişim) ──
    out['rsi_slope'] = out['rsi'] - out['rsi'].shift(5)
    out['mfi_slope'] = out['mfi'] - out['mfi'].shift(5)
    out['cmf_slope'] = c20 - c20.shift(5)
    out['p52_slope'] = out['p52'] - out['p52'].shift(5)
    # ── OBV: slope (avg-hacme normalize) + fiyat uyumsuzluğu ──
    obv = (np.sign(c.diff()).fillna(0) * df['Volume']).cumsum()
    avgvol20 = df['Volume'].rolling(20).mean()
    out['obv_slope'] = ((obv - obv.shift(20)) / (avgvol20 * 20)).replace([np.inf, -np.inf], np.nan)
    pchg = c - c.shift(20); ochg = obv - obv.shift(20)
    out['obv_div'] = [_obv_div_state(p, o) for p, o in zip(pchg, ochg)]
    # ── KÜRESEL FAKTÖRLER (fiyat+hacim, çapraz-kesitsel — quintile sıralamayla ölçülür) ──
    ret1 = c.pct_change()
    out['mom_12_1'] = c.shift(21) / c.shift(252) - 1            # 12-1 ay momentum (son ay hariç)
    out['lowvol_60'] = ret1.rolling(60).std()                  # düşük-vol anomalisi (düşük dilim iyi beklenir)
    out['rev_21'] = c / c.shift(21) - 1                        # kısa-vade reversal (düşük dilim iyi beklenir)
    amihud = (ret1.abs() / (df['Volume'] * c)).replace([np.inf, -np.inf], np.nan)
    out['illiq_21'] = amihud.rolling(21).mean()                # Amihud illikidite (yüksek dilim prim beklenir)
    return out


# ───────────────────────── EVREN + DB ─────────────────────────
def universe_symbols():
    syms = []
    for fp in sorted(glob.glob(f"{VERILER}/*.IS_1d.parquet")):
        base = os.path.basename(fp).replace('_1d.parquet', '')
        if base.upper().startswith(('XU', 'XB', 'XT', 'XY')):
            continue  # endeks değil, hisse istiyoruz
        syms.append(base)
    return syms


_SCHEMA = """
        CREATE TABLE universe_snapshot (
            snap_date TEXT, symbol TEXT, close REAL,
            p52 REAL, rsi REAL, mfi REAL, cmf TEXT, cmf20 REAL, sq INTEGER, vp TEXT,
            rsi_dual TEXT, mfi_dual TEXT, obv_div TEXT,
            rsi_slope REAL, mfi_slope REAL, cmf_slope REAL, p52_slope REAL, obv_slope REAL,
            mom_12_1 REAL, lowvol_60 REAL, rev_21 REAL, illiq_21 REAL,
            fwd_ret_5g REAL, fwd_ret_10g REAL, fwd_ret_20g REAL,
            fwd_hit_5g INTEGER, fwd_hit_10g INTEGER, fwd_hit_20g INTEGER,
            PRIMARY KEY (snap_date, symbol)
        )"""
_COLS = ['snap_date', 'symbol', 'close', 'p52', 'rsi', 'mfi', 'cmf', 'cmf20', 'sq', 'vp',
         'rsi_dual', 'mfi_dual', 'obv_div', 'rsi_slope', 'mfi_slope', 'cmf_slope',
         'p52_slope', 'obv_slope', 'mom_12_1', 'lowvol_60', 'rev_21', 'illiq_21',
         'fwd_ret_5g', 'fwd_ret_10g', 'fwd_ret_20g',
         'fwd_hit_5g', 'fwd_hit_10g', 'fwd_hit_20g']


def ensure_table(conn):
    have = [r[1] for r in conn.execute("PRAGMA table_info(universe_snapshot)").fetchall()]
    if have and 'cmf20' not in have:   # eski şema → temiz yeniden kur (türev cache, ucuz)
        print("  (şema güncellendi → eski tablo düşürülüyor, yeniden kurulacak)")
        conn.execute("DROP TABLE universe_snapshot")
        have = []
    if not have:
        conn.execute(_SCHEMA)
        conn.execute("CREATE INDEX IF NOT EXISTS ix_us_date ON universe_snapshot(snap_date)")
    conn.commit()


def _fwd(closes, i, n):
    j = i + n
    if j < len(closes) and closes[i] > 0:
        r = closes[j] / closes[i] - 1.0
        return float(r), int(r > 0)
    return None, None


def _n(x):
    return None if pd.isna(x) else float(x)


# ───────────────────────── BACKFILL / DAILY ─────────────────────────
def run_snapshot(days=120, daily_only=False):
    t0 = time.time()
    conn = sqlite3.connect(DB)
    ensure_table(conn)
    syms = universe_symbols()
    print(f"» Evren: {len(syms)} hisse · pencere: {'sadece bugün' if daily_only else f'son {days} işlem günü'}")
    rows = []; nsym = 0
    for sym in syms:
        fp = f"{VERILER}/{sym}_1d.parquet"
        try:
            df = pd.read_parquet(fp).sort_index()
        except Exception:
            continue
        if len(df) < MIN_BARS + 5:
            continue
        feats = feature_frame(df)
        closes = df['Close'].values
        nlen = len(df)
        if daily_only:
            positions = [nlen - 1]                       # bugün (forward NULL kalır)
        else:
            hi = nlen - 1 - 20                            # 20g forward'ı olan en son gün
            lo = max(MIN_BARS, hi - days + 1)
            positions = range(lo, hi + 1)
        for i in positions:
            if i < MIN_BARS:
                continue
            r5, h5 = _fwd(closes, i, 5); r10, h10 = _fwd(closes, i, 10); r20, h20 = _fwd(closes, i, 20)
            fr = feats.iloc[i]
            rows.append((
                df.index[i].strftime('%Y-%m-%d'), sym, float(closes[i]),
                _n(fr['p52']), _n(fr['rsi']), _n(fr['mfi']), fr['cmf'],
                _n(fr['cmf20']),
                None if pd.isna(fr['sq']) else int(fr['sq']), _vp_shape(df.iloc[max(0, i - 59):i + 1]),
                fr['rsi_dual'], fr['mfi_dual'], fr['obv_div'],
                _n(fr['rsi_slope']), _n(fr['mfi_slope']), _n(fr['cmf_slope']),
                _n(fr['p52_slope']), _n(fr['obv_slope']),
                _n(fr['mom_12_1']), _n(fr['lowvol_60']), _n(fr['rev_21']), _n(fr['illiq_21']),
                r5, r10, r20, h5, h10, h20,
            ))
        nsym += 1
    cur = conn.cursor()
    _ph = ",".join("?" * len(_COLS))
    cur.executemany(f"INSERT OR IGNORE INTO universe_snapshot ({','.join(_COLS)}) VALUES ({_ph})", rows)
    conn.commit()
    written = cur.rowcount
    total = conn.execute("SELECT COUNT(*) FROM universe_snapshot").fetchone()[0]
    conn.close()
    print(f"  {nsym} hisse işlendi · {len(rows)} satır üretildi · {written} yeni · tablo toplam {total} · {time.time()-t0:.1f}s")


def fill_forward():
    """forward getirisi NULL olan eski satırları, artık geleceği oluştuysa doldur."""
    conn = sqlite3.connect(DB)
    miss = pd.read_sql("SELECT rowid,symbol,snap_date FROM universe_snapshot WHERE fwd_ret_20g IS NULL", conn)
    if miss.empty:
        print("  doldurulacak (NULL forward) satır yok."); conn.close(); return
    upd = []
    for sym, g in miss.groupby('symbol'):
        fp = f"{VERILER}/{sym}_1d.parquet"
        if not os.path.exists(fp):
            continue
        df = pd.read_parquet(fp).sort_index()
        closes = df['Close'].values
        idx = {d.strftime('%Y-%m-%d'): k for k, d in enumerate(df.index)}
        for _, r in g.iterrows():
            i = idx.get(r['snap_date'])
            if i is None:
                continue
            r5, h5 = _fwd(closes, i, 5); r10, h10 = _fwd(closes, i, 10); r20, h20 = _fwd(closes, i, 20)
            if r20 is not None:
                upd.append((r5, r10, r20, h5, h10, h20, int(r['rowid'])))
    if upd:
        conn.executemany("""UPDATE universe_snapshot SET fwd_ret_5g=?,fwd_ret_10g=?,fwd_ret_20g=?,
            fwd_hit_5g=?,fwd_hit_10g=?,fwd_hit_20g=? WHERE rowid=?""", upd)
        conn.commit()
    print(f"  {len(upd)} satırın forward getirisi dolduruldu.")
    conn.close()


# ───────────────────────── RAPOR: FEATURE GÜÇ SIRASI ─────────────────────────
def _quintile_edge(d, col, ret='fwd_ret_10g'):
    s = d[[col, ret]].dropna()
    if len(s) < 500:
        return None
    try:
        s['q'] = pd.qcut(s[col], 5, labels=False, duplicates='drop')
    except Exception:
        return None
    grp = s.groupby('q')[ret].agg(['mean', 'count'])
    if len(grp) < 3:
        return None
    top = grp['mean'].iloc[-1] * 100; bot = grp['mean'].iloc[0] * 100
    # monotonluk: dilim ortalamaları sıralı mı
    means = grp['mean'].values
    mono = bool(np.all(np.diff(means) >= 0) or np.all(np.diff(means) <= 0))
    return {'feature': col, 'spread': top - bot, 'top': top, 'bot': bot,
            'n': int(grp['count'].sum()), 'mono': mono}


def _cat_edge(d, col, ret='fwd_ret_10g'):
    s = d[[col, ret]].dropna()
    if len(s) < 500:
        return None
    grp = s.groupby(col)[ret].agg(['mean', 'count'])
    grp = grp[grp['count'] >= 50]
    if len(grp) < 2:
        return None
    best = grp['mean'].idxmax(); worst = grp['mean'].idxmin()
    return {'feature': col, 'spread': (grp['mean'].max() - grp['mean'].min()) * 100,
            'best': f"{best} ({grp['mean'].max()*100:+.1f}%)",
            'worst': f"{worst} ({grp['mean'].min()*100:+.1f}%)",
            'n': int(grp['count'].sum())}


def _xu100_regime():
    """Her tarih için piyasa rejimi: XU100 close > SMA50 → 'boğa', değilse 'ayı'."""
    for cand in (f"{VERILER}/XU100.IS_1d.parquet", f"{VERILER}/XU100_1d.parquet"):
        if os.path.exists(cand):
            x = pd.read_parquet(cand).sort_index()
            sma = x['Close'].rolling(50).mean()
            reg = pd.Series(np.where(x['Close'] > sma, 'boğa', 'ayı'), index=x.index.strftime('%Y-%m-%d'))
            return reg[~reg.index.duplicated()]
    return None


def _sp_q(g, col):
    r = _quintile_edge(g, col)
    return r['spread'] if r else float('nan')


def _sp_c(g, col):
    r = _cat_edge(g, col)
    return r['spread'] if r else float('nan')


def report():
    conn = sqlite3.connect(DB)
    d = pd.read_sql("SELECT * FROM universe_snapshot WHERE fwd_ret_10g IS NOT NULL", conn)
    conn.close()
    L = []
    L.append("# UNIVERSE SNAPSHOT — FEATURE GÜÇ RAPORU")
    L.append(f"Üretim: {datetime.now():%Y-%m-%d %H:%M} · değerlendirilen satır: {len(d):,} · "
             f"hisse: {d['symbol'].nunique()} · gün: {d['snap_date'].nunique()}")
    if len(d) < 500:
        L.append("\n⚠ Yeterli veri yok (≥500 satır gerekli). Önce --backfill-days çalıştır.")
        open('universe_report.md', 'w', encoding='utf-8').write("\n".join(L))
        print("\n".join(L)); return
    base10 = d['fwd_ret_10g'].mean() * 100
    base_hit = d['fwd_hit_10g'].mean() * 100
    L.append(f"Evren tabanı (10g): ort getiri %{base10:+.2f} · yukarı oranı %{base_hit:.1f}\n")

    # Sürekli feature'lar — quintile spread
    _cont_feats = ['p52', 'rsi', 'mfi', 'sq', 'rsi_slope', 'mfi_slope', 'cmf_slope', 'p52_slope',
                   'obv_slope', 'mom_12_1', 'lowvol_60', 'rev_21', 'illiq_21']
    cont = [r for r in (_quintile_edge(d, c) for c in _cont_feats) if r]
    cont.sort(key=lambda x: -abs(x['spread']))
    L.append("## Sürekli feature gücü (10g ileri getiri, en güçlü → zayıf)")
    L.append(f"{'feature':12}{'spread':>9}{'üst%5':>9}{'alt%5':>9}{'monoton':>9}{'n':>9}")
    for r in cont:
        L.append(f"{r['feature']:12}{r['spread']:>+8.2f} {r['top']:>+8.2f} {r['bot']:>+8.2f} "
                 f"{('evet' if r['mono'] else 'hayır'):>9}{r['n']:>9,}")

    # Kategorik feature'lar
    L.append("\n## Kategorik feature gücü (10g)")
    cat = [r for r in (_cat_edge(d, c) for c in ['cmf', 'vp', 'rsi_dual', 'mfi_dual', 'obv_div']) if r]
    cat.sort(key=lambda x: -x['spread'])
    for r in cat:
        L.append(f"- {r['feature']}: spread %{r['spread']:.2f} · en iyi {r['best']} · en kötü {r['worst']} · n={r['n']:,}")

    # Korelasyon-budama — birbirinin kopyası sürekli feature'lar (|r|>0.85)
    L.append("\n## Korelasyon (kopya/ölü ağırlık avı)")
    try:
        cm = d[_cont_feats].corr()
        dup = []
        for ii in range(len(_cont_feats)):
            for jj in range(ii + 1, len(_cont_feats)):
                rr = cm.iloc[ii, jj]
                if pd.notna(rr) and abs(rr) > 0.85:
                    dup.append(f"{_cont_feats[ii]} ↔ {_cont_feats[jj]} (r={rr:+.2f})")
        L.append("- Yüksek korele çift (biri elenebilir): " + ("; ".join(dup) if dup else "yok — hepsi ayrı bilgi taşıyor"))
    except Exception:
        pass

    # ── REJİM AYRIMI — akış sinyali ayıda da tutuyor mu? ──
    reg = _xu100_regime()
    if reg is not None:
        d['regime'] = d['snap_date'].map(reg)
        dr = d.dropna(subset=['regime'])
        # ay bazında taban (rejimlerin gerçekten var olduğunu göster)
        L.append("\n## Ay bazında evren tabanı (10g) — rejim var mı")
        mm = dr.assign(ay=dr['snap_date'].str[:7]).groupby('ay')['fwd_ret_10g'].agg(['mean', 'count'])
        for ay, row in mm.iterrows():
            L.append(f"  {ay}: %{row['mean']*100:>+6.2f}  (n={int(row['count']):,})")
        L.append("\n## REJİM AYRIMI (XU100 SMA50: boğa vs ayı) — spread her rejimde de tutuyor mu?")
        # (q)=quintile sürekli, (c)=kategorik. mom/lowvol/rev/illiq = küresel faktörler.
        _keyf = [('cmf', 'c'), ('mom_12_1', 'q'), ('lowvol_60', 'q'), ('rev_21', 'q'),
                 ('illiq_21', 'q'), ('obv_div', 'c'), ('p52', 'q')]
        L.append(f"{'rejim':6}{'n':>8}{'taban':>8}" + "".join(f"{nm[:8]:>10}" for nm, _ in _keyf))
        for rname, gg in dr.groupby('regime'):
            if len(gg) < 1000:
                continue
            cells = "".join(f"{(_sp_c(gg, nm) if t == 'c' else _sp_q(gg, nm)):>+10.2f}" for nm, t in _keyf)
            L.append(f"{rname:6}{len(gg):>8,}{gg['fwd_ret_10g'].mean()*100:>+8.2f}{cells}")
        L.append("→ Spread her iki rejimde de pozitif/yüksekse REJİM-DAYANIKLI; sadece boğada yüksekse serap.")
        L.append("  NOT: lowvol/rev'de NEGATİF spread anomaliyi DOĞRULAR (düşük dilim kazanır).")

    # Yorum
    L.append("\n## Okuma")
    if cont:
        top = cont[0]
        L.append(f"- En güçlü ayrıştırıcı: **{top['feature']}** (üst %5 vs alt %5 farkı %{top['spread']:+.2f}, "
                 f"{'monoton' if top['mono'] else 'monoton DEĞİL'}).")
    L.append("- 'spread' ≈ 0 olan feature ölü ağırlıktır → AI/skordan eleme adayı.")
    L.append("- Bu yalnızca tek-değişkenli güç; eşik/rejim kalibrasyonu ayrı segment-backtest ister (extrapolation-yasak).")

    txt = "\n".join(L)
    open('universe_report.md', 'w', encoding='utf-8').write(txt)
    print(txt)
    print("\n✅ Rapor → universe_report.md")


# ───────────────────────── EŞİTLİK TESTİ ─────────────────────────
def verify():
    """Vektörel feature'lar scanner_karne._feats ile son bar'da aynı mı?"""
    from scanner_karne import _feats
    syms = universe_symbols()[:8]
    print(f"{'sym':10}{'p52':>14}{'rsi':>12}{'cmf':>16}{'sq':>8}{'vp':>14}")
    for sym in syms:
        fp = f"{VERILER}/{sym}_1d.parquet"
        df = pd.read_parquet(fp).sort_index()
        if len(df) < MIN_BARS:
            continue
        a = _feats(df)
        b = feature_frame(df).iloc[-1]
        vp_b = _vp_shape(df.tail(60))
        p_ok = (a['p52'] is None and pd.isna(b['p52'])) or abs((a['p52'] or 0) - (b['p52'] or 0)) < 0.5
        r_ok = abs(a['rsi'] - b['rsi']) < 0.5
        print(f"{sym:10}{'OK' if p_ok else 'FARK':>14}{'OK' if r_ok else 'FARK':>12}"
              f"{str(a['cmf']==b['cmf']):>16}{str(a['sq']==int(b['sq'])):>8}{str(a['vp']==vp_b):>14}")


# ───────────────────────── STRATEJİ BACKTEST (winrate yükseltme) ─────────────────────────
def _xu100_fwd10():
    """Her tarih için XU100'ün ileri 10g getirisi (%) — beat-rate için."""
    for cand in (f"{VERILER}/XU100.IS_1d.parquet", f"{VERILER}/XU100_1d.parquet"):
        if os.path.exists(cand):
            x = pd.read_parquet(cand).sort_index()
            f = (x['Close'].shift(-10) / x['Close'] - 1) * 100
            return pd.Series(f.values, index=x.index.strftime('%Y-%m-%d'))
    return None


def strategy_report(topn=10):
    """Haftalık CMF-top-N stratejisini geçmişe koş + yanına 2. koşul ekleyince winrate ne olur."""
    conn = sqlite3.connect(DB)
    d = pd.read_sql("SELECT snap_date,symbol,cmf20,p52,rsi,lowvol_60,mom_12_1,fwd_ret_10g "
                    "FROM universe_snapshot WHERE fwd_ret_10g IS NOT NULL AND cmf20 IS NOT NULL", conn)
    conn.close()
    d['ret'] = d['fwd_ret_10g'] * 100
    reg = _xu100_regime()
    if reg is not None:
        d['regime'] = d['snap_date'].map(reg)
    xf = _xu100_fwd10()
    d['xu'] = d['snap_date'].map(xf) if xf is not None else float('nan')

    def basket(df, filt=None):
        picks = []
        for _, g in df.groupby('snap_date'):
            gg = g if filt is None else g[filt(g)]
            if len(gg):
                picks.append(gg.nlargest(topn, 'cmf20'))
        if not picks:
            return None
        P = pd.concat(picks)
        return {'n': len(P), 'ret': P['ret'].mean(),
                'win': (P['ret'] > 0).mean() * 100,
                'beat': (P['ret'] > P['xu']).mean() * 100 if P['xu'].notna().any() else float('nan')}

    filters = [
        ('CMF-top (ham, yan koşulsuz)', None),
        ('+ trend (p52≥50)', lambda g: g['p52'] >= 50),
        ('+ aşırı-alım değil (rsi<70)', lambda g: g['rsi'] < 70),
        ('+ uzamamış (p52 30-75)', lambda g: (g['p52'] >= 30) & (g['p52'] <= 75)),
        ('+ düşük-vol (gün-içi alt yarı)', lambda g: g['lowvol_60'] <= g['lowvol_60'].median()),
        ('+ momentum>0 (12-1ay)', lambda g: g['mom_12_1'] > 0),
        ('+ trend & aşırı-alım değil', lambda g: (g['p52'] >= 50) & (g['rsi'] < 70)),
    ]
    L = ["# STRATEJİ BACKTEST — Haftalık CMF-top-%d (10g)" % topn]
    L.append(f"gün {d['snap_date'].nunique()} · evren-taban 10g %{d['ret'].mean():+.2f} · "
             f"taban winrate %{(d['ret']>0).mean()*100:.0f}\n")
    L.append(f"{'strateji':34}{'n':>7}{'ort.ret':>9}{'winrate':>9}{'XU100 geç':>11}")
    base = None
    for name, f in filters:
        r = basket(d, f)
        if not r:
            continue
        if base is None:
            base = r
        L.append(f"{name:34}{r['n']:>7,}{r['ret']:>+8.2f}{r['win']:>8.0f}%{r['beat']:>10.0f}%")

    # rejim ayrımı — ham CMF-top
    if 'regime' in d.columns:
        L.append("\n## Rejim ayrımı (ham CMF-top)")
        for rn, g in d.dropna(subset=['regime']).groupby('regime'):
            r = basket(g)
            if r:
                L.append(f"  {rn}: winrate %{r['win']:.0f} · ort %{r['ret']:+.2f} · XU100 geç %{r['beat']:.0f} (n={r['n']:,})")

    L.append("\n## Okuma")
    L.append("- 'winrate' = pozitif çıkan pay; 'XU100 geç' = endeksi yenen pay (asıl ölçü).")
    L.append("- Bir yan-koşul winrate'i belirgin artırıyorsa → canlı listeye o filtreyi ekleriz.")
    txt = "\n".join(L)
    open('strategy_report.md', 'w', encoding='utf-8').write(txt)
    print(txt)
    print("\n✅ Rapor → strategy_report.md")


# ─────────── CMF + UDVR KOMBİNASYON — ÖRNEKLEM-DIŞI ───────────
def cmf_udvr_oos():
    """CMF tek başına vs UDVR tek başına vs CMF+UDVR kombine — train'de kur, test'te doğrula.
    Karar: kombine, TEST döneminde CMF-tek-başınayı geçiyor mu (katkı) yoksa seyreltiyor mu."""
    conn = sqlite3.connect(DB)
    snap = pd.read_sql("SELECT snap_date,symbol,cmf20,fwd_ret_10g FROM universe_snapshot "
                       "WHERE fwd_ret_10g IS NOT NULL", conn)
    conn.close()
    snap['ret'] = snap['fwd_ret_10g'] * 100
    xf = _xu_fwd(10)
    parts = []
    for sym, g in snap.groupby('symbol'):
        for cand in (f"{VERILER}/{sym}_1d.parquet", f"{VERILER}/{sym}.IS_1d.parquet"):
            if os.path.exists(cand):
                df = _split_adj(pd.read_parquet(cand)); break
        else:
            continue
        if df is None or len(df) < 40:
            continue
        c = df['Close']; v = df['Volume']
        us = v.where(c > c.shift(1), 0.0).rolling(20).sum()
        ds = v.where(c < c.shift(1), 0.0).rolling(20).sum()
        F = pd.DataFrame({'udvr': (us - ds) / (us + ds)}, index=df.index)
        F.index = pd.Index(F.index).strftime('%Y-%m-%d')
        parts.append(g.set_index('snap_date').join(F).reset_index())
    D = pd.concat(parts, ignore_index=True)
    D['xu'] = D['snap_date'].map(xf) if xf is not None else np.nan
    D = D.dropna(subset=['cmf20', 'udvr', 'ret'])
    D['r_cmf'] = D.groupby('snap_date')['cmf20'].rank(pct=True)
    D['r_udvr'] = D.groupby('snap_date')['udvr'].rank(pct=True)
    D['combo'] = (D['r_cmf'] + D['r_udvr']) / 2
    dates = sorted(D['snap_date'].unique()); split = dates[len(dates) // 2]

    def basket(d, rank, topn=10):
        picks = [gg.nlargest(topn, rank) for _, gg in d.groupby('snap_date') if len(gg)]
        P = pd.concat(picks)
        win = (P['ret'] > 0).mean() * 100
        beat = (P['ret'] > P['xu']).mean() * 100 if P['xu'].notna().any() else float('nan')
        return win, P['ret'].mean(), beat

    L = ["# CMF + UDVR KOMBİNASYON — ÖRNEKLEM-DIŞI (top 10, 10g)"]
    L.append(f"train ≤{split} · test >{split}\n")
    L.append(f"{'dönem':7}{'sıralama':10}{'winrate':>9}{'beklenti':>10}{'XU geç':>8}")
    for tag, d in [('TRAIN', D[D['snap_date'] <= split]), ('TEST', D[D['snap_date'] > split])]:
        for rk, nm in [('r_cmf', 'CMF'), ('r_udvr', 'UDVR'), ('combo', 'CMF+UDVR')]:
            w, e, b = basket(d, rk)
            L.append(f"{tag:7}{nm:10}{w:>8.0f}%{e:>+9.2f}{b:>7.0f}%")
        L.append("")
    L.append("## Karar")
    L.append("- TEST döneminde CMF+UDVR > CMF ise → UDVR gerçek katkı, motora ekle.")
    L.append("- TEST'te eşit/düşükse → seyreltiyor, CMF tek başına yeterli (flow için eksiksiz).")
    txt = "\n".join(L)
    open('cmf_udvr_report.md', 'w', encoding='utf-8').write(txt)
    print(txt)
    print("\n✅ Rapor → cmf_udvr_report.md")


# ─────────── BAŞKA FLOW SİNYALLERİ — ÖRNEKLEM-DIŞI + CMF korelasyon ───────────
def flow_oos():
    """CMF'in kuzeni akış sinyallerini OOS test eder + CMF ile korelasyon (kopya mı, ortogonal mi)."""
    conn = sqlite3.connect(DB)
    snap = pd.read_sql("SELECT snap_date,symbol,cmf20,fwd_ret_10g FROM universe_snapshot "
                       "WHERE fwd_ret_10g IS NOT NULL", conn)
    conn.close()
    snap['ret'] = snap['fwd_ret_10g'] * 100
    parts = []
    for sym, g in snap.groupby('symbol'):
        for cand in (f"{VERILER}/{sym}_1d.parquet", f"{VERILER}/{sym}.IS_1d.parquet"):
            if os.path.exists(cand):
                df = _split_adj(pd.read_parquet(cand)); break
        else:
            continue
        if df is None or len(df) < 60:
            continue
        c = df['Close']; h = df['High']; l = df['Low']; v = df['Volume']
        rng = (h - l).replace(0, np.nan); avgv20 = v.rolling(20).mean()
        clv = ((c - l) - (h - c)) / rng
        ad = (clv * v).fillna(0).cumsum()
        upv = v.where(c > c.shift(1), 0.0); dnv = v.where(c < c.shift(1), 0.0)
        us = upv.rolling(20).sum(); ds = dnv.rolling(20).sum()
        fi = ((c - c.shift(1)) * v).ewm(span=13, adjust=False).mean()
        vpt = (v * c.pct_change()).fillna(0).cumsum()
        F = pd.DataFrame({
            'ad_slope': (ad - ad.shift(20)) / (avgv20 * 20),          # Chaikin A/D eğimi
            'udvr': (us - ds) / (us + ds),                            # Up/Down Volume Ratio (Wyckoff)
            'force': fi / (c * avgv20),                               # Force Index (Elder)
            'vpt_slope': (vpt - vpt.shift(20)) / (avgv20 * 20),       # Volume Price Trend eğimi
            'cmf10': _cmf_series(df, 10), 'cmf40': _cmf_series(df, 40),  # CMF pencere varyantı
            'vol_exp': v.rolling(10).mean() / v.rolling(60).mean(),   # hacim genişlemesi
        }, index=df.index)
        F.index = pd.Index(F.index).strftime('%Y-%m-%d')
        parts.append(g.set_index('snap_date').join(F).reset_index())
    D = pd.concat(parts, ignore_index=True)
    dates = sorted(D['snap_date'].dropna().unique()); split = dates[len(dates) // 2]
    tr = D[D['snap_date'] <= split]; te = D[D['snap_date'] > split]

    def q_spread(d, col):
        s = d[[col, 'ret']].dropna()
        if len(s) < 500:
            return None
        try:
            s['b'] = pd.qcut(s[col], 5, labels=False, duplicates='drop')
        except Exception:
            return None
        gp = s.groupby('b')['ret'].mean()
        return float(gp.iloc[-1] - gp.iloc[0]) if len(gp) >= 3 else None

    L = ["# BAŞKA FLOW SİNYALLERİ — ÖRNEKLEM-DIŞI + CMF korelasyon"]
    L.append(f"train {tr['snap_date'].min()}→{split} (n={len(tr):,}) · test >{split} (n={len(te):,})\n")
    L.append(f"{'sinyal':12}{'train':>8}{'test':>8}  {'CMF-kor':>8}  durum")
    L.append(f"{'cmf20 (çıta)':12}{q_spread(tr,'cmf20'):>+8.2f}{q_spread(te,'cmf20'):>+8.2f}  {'1.00':>8}  👑")
    for f in ['ad_slope', 'udvr', 'force', 'vpt_slope', 'cmf10', 'cmf40', 'vol_exp']:
        ts = q_spread(tr, f); es = q_spread(te, f)
        cor = D[[f, 'cmf20']].dropna().corr().iloc[0, 1]
        if ts is None or es is None:
            L.append(f"{f:12}  (yetersiz veri)"); continue
        robust = (ts * es > 0) and abs(ts) >= 0.4 and abs(es) >= 0.4
        ortho = abs(cor) < 0.6
        durum = ("✅ DAYANIKLI" if robust else ("⚠ zayıf" if ts * es > 0 else "❌ ters")) + \
                ("" if ortho or not robust else " (ama CMF kopyası)") + \
                (" · ORTOGONAL" if (robust and ortho) else "")
        L.append(f"{f:12}{ts:>+8.2f}{es:>+8.2f}  {cor:>+8.2f}  {durum}")
    L.append("\n## Okuma")
    L.append("- Değerli sinyal = DAYANIKLI (iki dönem +) VE ORTOGONAL (CMF-kor < 0.6 → yeni bilgi).")
    L.append("- Dayanıklı ama CMF-kor yüksek = CMF'i tekrarlıyor, katkı yok.")
    txt = "\n".join(L)
    open('flow_report.md', 'w', encoding='utf-8').write(txt)
    print(txt)
    print("\n✅ Rapor → flow_report.md")


# ─────────── CROSSOVER / ÖNCÜ MA SİNYALLERİ — ÖRNEKLEM-DIŞI ───────────
def _split_adj(df):
    if df is None or df.empty or len(df) < 5 or 'Close' not in df.columns:
        return df
    df = df.copy().sort_index(); pc = [c for c in ['Open', 'High', 'Low', 'Close'] if c in df.columns]
    for _ in range(10):
        cl = df['Close'].ffill().values; found = False
        for i in range(1, len(cl)):
            if cl[i - 1] <= 0 or cl[i] <= 0:
                continue
            r = cl[i - 1] / cl[i]
            if r >= 1.20:
                for col in pc:
                    df.iloc[:i, df.columns.get_loc(col)] = df.iloc[:i][col].values / r
                found = True; break
        if not found:
            break
    return df


def crossover_oos():
    """Öncü MA sinyalleri + baseline crossover'lar, train/test OOS. CMF baseline ile kıyas."""
    conn = sqlite3.connect(DB)
    snap = pd.read_sql("SELECT snap_date,symbol,cmf20,fwd_ret_10g FROM universe_snapshot "
                       "WHERE fwd_ret_10g IS NOT NULL", conn)
    conn.close()
    snap['ret'] = snap['fwd_ret_10g'] * 100
    parts = []
    for sym, g in snap.groupby('symbol'):
        for cand in (f"{VERILER}/{sym}_1d.parquet", f"{VERILER}/{sym}.IS_1d.parquet"):
            if os.path.exists(cand):
                df = _split_adj(pd.read_parquet(cand)); break
        else:
            continue
        if df is None or len(df) < 60:
            continue
        c = df['Close']
        e5 = c.ewm(span=5, adjust=False).mean(); e13 = c.ewm(span=13, adjust=False).mean()
        e9 = c.ewm(span=9, adjust=False).mean(); e20 = c.ewm(span=20, adjust=False).mean()
        e21 = c.ewm(span=21, adjust=False).mean()
        s50 = c.rolling(50).mean(); s200 = c.rolling(200).mean()
        cr = (e5 > e13).astype(int)
        F = pd.DataFrame({
            # ÖNCÜ (leading)
            'sikisma_5_20': -((e5 - e20).abs() / c * 100),       # yüksek = daha sıkışık (setup)
            'egim_e20': (e20 / e20.shift(5) - 1) * 100,           # MA eğim dönüşü
            'fiyat_vs_e20': (c - e20) / c * 100,                  # stretch/pullback
            'taze_kesisim_5_13': (cr.diff() == 1).rolling(5).max().fillna(0),  # son 5g golden cross
            # BASELINE (lagging)
            'dizilim_9_21': (e9 > e21).astype(float),
            'golden_50_200': (s50 > s200).astype(float),
        }, index=df.index)
        F.index = pd.Index(F.index).strftime('%Y-%m-%d')
        parts.append(g.set_index('snap_date').join(F).reset_index())
    D = pd.concat(parts, ignore_index=True)
    dates = sorted(D['snap_date'].dropna().unique()); split = dates[len(dates) // 2]
    tr = D[D['snap_date'] <= split]; te = D[D['snap_date'] > split]

    def q_spread(d, col):
        s = d[[col, 'ret']].dropna()
        if len(s) < 500:
            return None
        try:
            s['b'] = pd.qcut(s[col], 5, labels=False, duplicates='drop')
        except Exception:
            return None
        gp = s.groupby('b')['ret'].mean()
        return float(gp.iloc[-1] - gp.iloc[0]) if len(gp) >= 3 else None

    def bin_spread(d, col):
        s = d[[col, 'ret']].dropna()
        if s[col].nunique() < 2 or len(s) < 500:
            return None
        m1 = s.loc[s[col] >= 0.5, 'ret'].mean(); m0 = s.loc[s[col] < 0.5, 'ret'].mean()
        return float(m1 - m0)

    L = ["# CROSSOVER / ÖNCÜ MA SİNYALLERİ — ÖRNEKLEM-DIŞI"]
    L.append(f"train {tr['snap_date'].min()}→{split} (n={len(tr):,}) · test >{split} (n={len(te):,})")
    L.append("spread = sinyalin 10g getiri ayırma gücü; her iki dönemde + ve anlamlı = DAYANIKLI\n")

    def line(name, ts, es):
        if ts is None or es is None:
            return f"  {name:20} (yetersiz veri)"
        robust = (ts * es > 0) and abs(ts) >= 0.4 and abs(es) >= 0.4
        flag = "✅ DAYANIKLI" if robust else ("⚠ zayıf" if ts * es > 0 else "❌ TERS (curve-fit)")
        return f"  {name:20} train %{ts:>+5.2f} · test %{es:>+5.2f}   {flag}"

    L.append("## BASELINE — CMF (kıyas çıtası)")
    L.append(line("cmf20", q_spread(tr, 'cmf20'), q_spread(te, 'cmf20')))
    L.append("\n## ÖNCÜ (leading) MA sinyalleri")
    for f in ['sikisma_5_20', 'egim_e20', 'fiyat_vs_e20']:
        L.append(line(f, q_spread(tr, f), q_spread(te, f)))
    L.append(line("taze_kesisim_5_13", bin_spread(tr, 'taze_kesisim_5_13'), bin_spread(te, 'taze_kesisim_5_13')))
    L.append("\n## BASELINE crossover'lar (lagging)")
    L.append(line("dizilim_9_21", bin_spread(tr, 'dizilim_9_21'), bin_spread(te, 'dizilim_9_21')))
    L.append(line("golden_50_200", bin_spread(tr, 'golden_50_200'), bin_spread(te, 'golden_50_200')))

    L.append("\n## Okuma")
    L.append("- CMF çıtasını GEÇEN + iki dönemde tutan bir sinyal varsa değerli; yoksa CMF zaten yeterli.")
    L.append("- Öncü sinyaller gecikmeli crossover'ları geçmeli (hipotez); veri ne diyor bak.")
    txt = "\n".join(L)
    open('crossover_report.md', 'w', encoding='utf-8').write(txt)
    print(txt)
    print("\n✅ Rapor → crossover_report.md")


# ─────────── KAZANANLARIN ORTAK NOKTASI — ÖRNEKLEM-DIŞI (train/test) ───────────
def winners_oos():
    """Veriyi zamanla ikiye böl: ilk yarı (train) kazanan-özelliklerini bul, ikinci
    yarı (test) DOĞRULA. Sadece İKİ yarıda da ayıran özellik gerçek; biri = curve-fit."""
    conn = sqlite3.connect(DB)
    d = pd.read_sql("SELECT * FROM universe_snapshot WHERE fwd_ret_10g IS NOT NULL", conn)
    conn.close()
    dates = sorted(d['snap_date'].unique())
    split = dates[len(dates) // 2]
    tr = d[d['snap_date'] <= split]
    te = d[d['snap_date'] > split]
    cont = ['cmf20', 'p52', 'rsi', 'mfi', 'sq', 'obv_slope', 'mom_12_1', 'lowvol_60',
            'rev_21', 'illiq_21', 'rsi_slope', 'mfi_slope', 'cmf_slope', 'p52_slope']
    cat = ['cmf', 'vp', 'rsi_dual', 'mfi_dual', 'obv_div']
    L = ["# KAZANANLARIN ORTAK NOKTASI — ÖRNEKLEM-DIŞI DOĞRULAMA"]
    L.append(f"train: {tr['snap_date'].min()}→{split} (n={len(tr):,}, taban %{tr['fwd_ret_10g'].mean()*100:+.2f}) · "
             f"test: >{split}→{te['snap_date'].max()} (n={len(te):,}, taban %{te['fwd_ret_10g'].mean()*100:+.2f})\n")
    L.append("Spread = üst dilim − alt dilim 10g getiri farkı (kazananları ayırma gücü).\n")

    def _row(name, tr_sp, te_sp):
        if tr_sp is None or te_sp is None:
            return f"  {name:12} (yetersiz veri)"
        robust = (tr_sp * te_sp > 0) and abs(tr_sp) >= 0.4 and abs(te_sp) >= 0.4
        flag = "✅ DAYANIKLI" if robust else ("⚠ zayıf/karışık" if tr_sp * te_sp > 0 else "❌ TERS DÖNDÜ (curve-fit)")
        return f"  {name:12} train %{tr_sp:>+5.2f} · test %{te_sp:>+5.2f}   {flag}"

    L.append("## Sürekli özellikler")
    res = []
    for f in cont:
        a = _quintile_edge(tr, f); b = _quintile_edge(te, f)
        res.append((f, a['spread'] if a else None, b['spread'] if b else None))
    res.sort(key=lambda x: -(min(abs(x[1]), abs(x[2])) if (x[1] and x[2]) else -1))
    for f, ts, es in res:
        L.append(_row(f, ts, es))

    L.append("\n## Kategorik özellikler (en iyi−en kötü spread)")
    for f in cat:
        a = _cat_edge(tr, f); b = _cat_edge(te, f)
        L.append(_row(f, a['spread'] if a else None, b['spread'] if b else None))

    L.append("\n## Okuma")
    L.append("- ✅ DAYANIKLI = her iki yarıda da aynı yönde + anlamlı → GERÇEK kazanan-özelliği.")
    L.append("- ❌ TERS DÖNDÜ = bir yarıda artı, diğerinde eksi → o pencereye uydurulmuş, GÜVENME.")
    L.append("- Motoru sadece ✅ olanlarla güçlendir; gerisi gürültü.")
    txt = "\n".join(L)
    open('winners_report.md', 'w', encoding='utf-8').write(txt)
    print(txt)
    print("\n✅ Rapor → winners_report.md")


# ───────────────────────── STRATEJİ LAB (winrate gelistirme testleri) ─────────────────────────
def _xu_fwd(w):
    for cand in (f"{VERILER}/XU100.IS_1d.parquet", f"{VERILER}/XU100_1d.parquet"):
        if os.path.exists(cand):
            x = pd.read_parquet(cand).sort_index()
            f = (x['Close'].shift(-w) / x['Close'] - 1) * 100
            return pd.Series(f.values, index=x.index.strftime('%Y-%m-%d'))
    return None


def strategy_lab():
    """3 test: (A) sepet büyüklüğü, (B) tutma süresi, (C) ham vs risk-ayarlı momentum.
    Her birinde winrate + beklenti (ort getiri) + kazanç/kayıp + XU100 geç."""
    conn = sqlite3.connect(DB)
    d = pd.read_sql("SELECT snap_date,symbol,cmf20,mom_12_1,lowvol_60,"
                    "fwd_ret_5g,fwd_ret_10g,fwd_ret_20g FROM universe_snapshot "
                    "WHERE cmf20 IS NOT NULL AND mom_12_1 IS NOT NULL", conn)
    conn.close()
    for w in (5, 10, 20):
        d[f'r{w}'] = d[f'fwd_ret_{w}g'] * 100
        xf = _xu_fwd(w)
        d[f'xu{w}'] = d['snap_date'].map(xf) if xf is not None else np.nan
    d['riskmom'] = d['mom_12_1'] / d['lowvol_60'].replace(0, np.nan)

    def basket(gate, topn, w):
        rc, xc = f'r{w}', f'xu{w}'
        picks = []
        for _, g in d.groupby('snap_date'):
            gg = g[gate(g)] if gate else g
            gg = gg.dropna(subset=[rc])
            if len(gg):
                picks.append(gg.nlargest(topn, 'cmf20'))
        if not picks:
            return None
        P = pd.concat(picks)
        win = (P[rc] > 0).mean() * 100
        kaz = P.loc[P[rc] > 0, rc].mean()
        kay = P.loc[P[rc] <= 0, rc].mean()
        beat = (P[rc] > P[xc]).mean() * 100 if P[xc].notna().any() else float('nan')
        return {'n': len(P), 'win': win, 'exp': P[rc].mean(), 'kaz': kaz, 'kay': kay, 'beat': beat}

    momgate = lambda g: g['mom_12_1'] > 0
    L = ["# STRATEJİ LAB — CMF+momentum winrate geliştirme"]
    L.append(f"örneklem {len(d):,} satır · gün {d['snap_date'].nunique()}\n")

    def _line(tag, r):
        if not r:
            return f"{tag:24}(veri yok)"
        return (f"{tag:24} win %{r['win']:>4.0f} · beklenti %{r['exp']:>+5.2f} · "
                f"kazanç %{r['kaz']:>+5.1f}/kayıp %{r['kay']:>+5.1f} · XU geç %{r['beat']:>4.0f} · n={r['n']:,}")

    L.append("## A) SEPET BÜYÜKLÜĞÜ (mom>0 gate, CMF rank, 10g)")
    for n in (5, 10, 15, 20):
        L.append(_line(f"top {n}", basket(momgate, n, 10)))

    L.append("\n## B) TUTMA SÜRESİ (top 10, mom>0, CMF rank)")
    for w in (5, 10, 20):
        L.append(_line(f"{w} gün", basket(momgate, 10, w)))

    L.append("\n## C) GATE: ham momentum vs risk-ayarlı (mom/vol), top10, 10g")
    L.append(_line("ham mom>0", basket(momgate, 10, 10)))
    L.append(_line("risk-ayarlı (üst yarı)", basket(lambda g: g['riskmom'] >= g['riskmom'].median(), 10, 10)))

    L.append("\n## Okuma")
    L.append("- Winrate TEK başına yetmez: beklenti (+) ve XU geç birlikte yükselmeli.")
    L.append("- 'kazanç/kayıp' asimetrisi: yüksek winrate ama büyük kayıp = tuzak.")
    txt = "\n".join(L)
    open('lab_report.md', 'w', encoding='utf-8').write(txt)
    print(txt)
    print("\n✅ Rapor → lab_report.md")


# ───────────────────────── CANLI YAYIN: factor_rank ─────────────────────────
def publish_factor_rank():
    """Bugünün CMF çapraz-kesitsel yüzdeliğini factor_rank tablosuna yazar (canlı app okur).
    Tek dayanıklı faktör (CMF) → her hissenin tüm BIST içindeki para-akışı sıralaması."""
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS factor_rank (
        rank_date TEXT, symbol TEXT, cmf20 REAL, cmf_pct REAL,
        PRIMARY KEY (rank_date, symbol))""")
    recs = []
    for sym in universe_symbols():
        try:
            df = pd.read_parquet(f"{VERILER}/{sym}_1d.parquet").sort_index()
        except Exception:
            continue
        if len(df) < 30:
            continue
        c20 = _cmf_series(df, 20).iloc[-1]
        if pd.isna(c20):
            continue
        recs.append((sym, float(c20), df.index[-1].strftime('%Y-%m-%d')))
    if not recs:
        print("  factor_rank: veri yok"); conn.close(); return
    R = pd.DataFrame(recs, columns=['symbol', 'cmf20', 'date'])
    rdate = R['date'].max()
    R['cmf_pct'] = R['cmf20'].rank(pct=True) * 100
    conn.executemany("INSERT OR REPLACE INTO factor_rank (rank_date,symbol,cmf20,cmf_pct) VALUES (?,?,?,?)",
                     [(rdate, r.symbol, r.cmf20, r.cmf_pct) for r in R.itertuples()])
    conn.commit()
    print(f"  factor_rank yazıldı: {len(R)} hisse · tarih {rdate} · üst dilim örnek: "
          + ", ".join(R.nlargest(5, 'cmf_pct')['symbol'].str.replace('.IS', '', regex=False)))
    conn.close()


# ───────────────────────── BİRLEŞİK SKOR TESTİ ─────────────────────────
def combo_report():
    """Çapraz-kesitsel BİRLEŞİK skor (CMF + düşük-vol) tek başınadan iyi mi?"""
    conn = sqlite3.connect(DB)
    d = pd.read_sql("SELECT snap_date,symbol,cmf20,lowvol_60,fwd_ret_10g FROM universe_snapshot "
                    "WHERE fwd_ret_10g IS NOT NULL", conn)
    conn.close()
    d = d.dropna(subset=['cmf20', 'lowvol_60', 'fwd_ret_10g']).copy()
    if len(d) < 1000:
        print("yetersiz veri — önce --backfill-days çalıştır."); return
    d['ret'] = d['fwd_ret_10g'] * 100
    # günlük çapraz-kesitsel yüzdelik rank (1.0 = o günün en iyisi)
    d['r_cmf'] = d.groupby('snap_date')['cmf20'].rank(pct=True)            # yüksek CMF = iyi
    d['r_lv'] = 1 - d.groupby('snap_date')['lowvol_60'].rank(pct=True)     # düşük vol = iyi
    d['combo'] = (d['r_cmf'] + d['r_lv']) / 2

    def q5(col):
        s = d[[col, 'ret']].dropna().copy()
        s['b'] = pd.qcut(s[col], 5, labels=False, duplicates='drop')
        return s.groupby('b')['ret'].mean()

    gc, gl, gb = q5('r_cmf'), q5('r_lv'), q5('combo')
    L = ["# BİRLEŞİK SKOR TESTİ — CMF + Düşük-Vol (çapraz-kesitsel)"]
    L.append(f"satır {len(d):,} · gün {d['snap_date'].nunique()} · taban10g %{d['ret'].mean():+.2f}\n")
    L.append("## Quintile ortalama 10g getiri (Q1 zayıf skor → Q5 güçlü skor)")
    L.append(f"{'dilim':6}{'CMF':>10}{'düşük-vol':>12}{'BİRLEŞİK':>12}")
    for b in range(5):
        L.append(f"Q{b+1:<5}{gc.get(b, float('nan')):>+9.2f} {gl.get(b, float('nan')):>+11.2f} {gb.get(b, float('nan')):>+11.2f}")
    sp_c = gc.iloc[-1] - gc.iloc[0]; sp_l = gl.iloc[-1] - gl.iloc[0]; sp_b = gb.iloc[-1] - gb.iloc[0]
    best = max(sp_c, sp_l)
    L.append(f"\nSpread (Q5−Q1): CMF %{sp_c:+.2f} · düşük-vol %{sp_l:+.2f} · BİRLEŞİK %{sp_b:+.2f}")
    L.append(f"→ BİRLEŞİK tek-başınanın en iyisini GEÇTİ ✅ ({sp_b:+.2f} > {best:+.2f}) — çok-faktör tezi tutuyor."
             if sp_b > best else
             f"→ Birleşik ({sp_b:+.2f}) tek-başınayı ({best:+.2f}) geçemedi — birleştirme katkı vermedi.")

    reg = _xu100_regime()
    if reg is not None:
        d['regime'] = d['snap_date'].map(reg)
        L.append("\n## Rejim ayrımı — birleşik skor Q5−Q1")
        for rn, g in d.dropna(subset=['regime']).groupby('regime'):
            s = g[['combo', 'ret']].dropna().copy()
            if len(s) < 500:
                continue
            s['b'] = pd.qcut(s['combo'], 5, labels=False, duplicates='drop')
            gg = s.groupby('b')['ret'].mean()
            L.append(f"  {rn}: %{gg.iloc[-1]-gg.iloc[0]:+.2f}  (taban %{g['ret'].mean():+.2f}, n={len(s):,})")

    last = d['snap_date'].max()
    top = d[d['snap_date'] == last].nlargest(12, 'combo')[['symbol', 'combo']]
    L.append(f"\n## {last} — birleşik skor TOP 12 (örnek 'kanıtlı liste' vitrini)")
    L.append("  " + ", ".join(f"{r.symbol.replace('.IS','')}({r.combo:.2f})" for r in top.itertuples()))

    txt = "\n".join(L)
    open('combo_report.md', 'w', encoding='utf-8').write(txt)
    print(txt)
    print("\n✅ Rapor → combo_report.md")


# ───────────────────────── TREND × FLOW ÇELİŞKİ BACKTEST ─────────────────────────
def conflict_report():
    """gorev4 'çelişki/teyit' mekanizmasının testi: TREND (yavaş, mom_12_1) vs
    FLOW (hızlı, cmf20) çakışınca gelecek getiri ne oluyor? 4 kova × forward × rejim × OOS.
    Period kovalamıyoruz — sadece 'çelişki bilgi taşıyor mu' hipotezini ölçüyoruz."""
    conn = sqlite3.connect(DB)
    d = pd.read_sql("SELECT snap_date,symbol,mom_12_1,cmf20,fwd_ret_5g,fwd_ret_10g,fwd_ret_20g "
                    "FROM universe_snapshot WHERE fwd_ret_10g IS NOT NULL AND mom_12_1 IS NOT NULL "
                    "AND cmf20 IS NOT NULL", conn)
    conn.close()
    d['tu'] = d['mom_12_1'] > 0           # trend up (yavaş)
    d['fp'] = d['cmf20'] > 0              # flow positive (hızlı)
    _order = ['T↑F↑ uyumlu-yukarı', 'T↑F↓ güce dağıtım*', 'T↓F↑ gizli birikim*', 'T↓F↓ uyumlu-aşağı']
    def _bk(t, f): return _order[0] if (t and f) else _order[1] if (t and not f) else _order[2] if (f and not t) else _order[3]
    d['bucket'] = [_bk(t, f) for t, f in zip(d['tu'], d['fp'])]
    reg = _xu100_regime()
    if reg is not None: d['regime'] = d['snap_date'].map(reg)
    dates = sorted(d['snap_date'].unique()); split = dates[len(dates) // 2]
    d['oos'] = np.where(d['snap_date'] < split, 'train', 'test')

    def _tbl(df, title):
        L = [title, f"{'kova':20}{'N':>7}{'ret5%':>8}{'ret10%':>8}{'ret20%':>8}{'hit10%':>8}"]
        for b in _order:
            g = df[df['bucket'] == b]
            if len(g) == 0: continue
            L.append(f"{b:20}{len(g):>7,}{g['fwd_ret_5g'].mean()*100:>+8.2f}"
                     f"{g['fwd_ret_10g'].mean()*100:>+8.2f}{g['fwd_ret_20g'].mean()*100:>+8.2f}"
                     f"{(g['fwd_ret_10g'] > 0).mean()*100:>8.1f}")
        return L

    L = ["# TREND × FLOW ÇELİŞKİ BACKTEST (gorev4 mekanizma testi)"]
    L.append(f"Üretim: {datetime.now():%Y-%m-%d %H:%M} · satır: {len(d):,} · "
             f"tarih: {d['snap_date'].min()} → {d['snap_date'].max()}")
    L.append("Trend(yavaş)=mom_12_1>0 · Flow(hızlı)=cmf20>0 · * = çelişki (divergence) kovaları\n")
    L += _tbl(d, "## TÜM DÖNEM")
    L.append(f"(evren tabanı 10g: {d['fwd_ret_10g'].mean()*100:+.2f}%)")
    if 'regime' in d.columns:
        for rn, gg in d.dropna(subset=['regime']).groupby('regime'):
            L.append(""); L += _tbl(gg, f"## REJİM: {rn}")
    for sp in ('train', 'test'):
        L.append(""); L += _tbl(d[d['oos'] == sp], f"## OOS: {sp} ({'<' if sp == 'train' else '>='} {split})")
    L.append("\n## OKUMA")
    L.append("- Hipotez 1: T↑F↓ (güce dağıtım) < T↑F↑ → uptrend'de para çıkışı KÖTÜ habercisi.")
    L.append("- Hipotez 2: T↓F↑ (gizli birikim) > T↓F↓ → downtrend'de para girişi İYİ habercisi (dönüş).")
    L.append("- KARAR: fark hem İKİ REJİMDE hem TEST'te (OOS) tutuyorsa mekanizma GERÇEK; sadece train/boğada ise serap.")
    txt = "\n".join(L)
    open('conflict_report.md', 'w', encoding='utf-8').write(txt)
    print(txt); print("\n✅ Rapor → conflict_report.md")


# ───────────────────────── CLI ─────────────────────────
if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--backfill-days', type=int, default=0)
    ap.add_argument('--daily', action='store_true')
    ap.add_argument('--report-only', action='store_true')
    ap.add_argument('--combo', action='store_true')
    ap.add_argument('--strategy', action='store_true')
    ap.add_argument('--lab', action='store_true')
    ap.add_argument('--winners', action='store_true')
    ap.add_argument('--crossover', action='store_true')
    ap.add_argument('--flow', action='store_true')
    ap.add_argument('--udvr', action='store_true')
    ap.add_argument('--conflict', action='store_true')
    ap.add_argument('--publish', action='store_true')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args()
    if a.verify:
        verify()
    elif a.publish:
        publish_factor_rank()
    elif a.combo:
        combo_report()
    elif a.strategy:
        strategy_report()
    elif a.lab:
        strategy_lab()
    elif a.winners:
        winners_oos()
    elif a.crossover:
        crossover_oos()
    elif a.flow:
        flow_oos()
    elif a.udvr:
        cmf_udvr_oos()
    elif a.conflict:
        conflict_report()
    elif a.report_only:
        report()
    elif a.daily:
        run_snapshot(daily_only=True); fill_forward(); report()
    else:
        run_snapshot(days=a.backfill_days or 120); report()
