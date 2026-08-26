#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TARAMA KARNE — measure→filter döngüsünü tek komuta indirir.
Standalone (canlı app'e dokunmaz). Çalıştır:  python scanner_karne.py

Ne yapar (19 Haz 2026 oturumunda elle yapılan analizin otomatiği):
  A) TIER SAĞLIK     — her scan_type'ın GERÇEK tam-veri 10g hit/ret'i vs SCANNER_TIER_MAP
                       iddiası. "Şişik/bayat" (boğa penceresinden kalma) tier'ları işaretler.
  B) REJİM KIRILIMI  — ay bazında hit/ret. Bir tarama sadece boğa ayında mı parlıyor?
  C) FEATURE AYRIŞTIRICI — her tarama için HER İKİ rejimde de tutan en güçlü giriş şartı
                       (52H/RSI/VP/CMF/Sıkışma). Yeni filtre adayları.

Çıktı: konsol + scanner_karne_report.md
Feature'lar veriler/*_1d.parquet'ten point-in-time hesaplanır (app.py formülleriyle birebir).
"""
import sqlite3, os, re, glob, sys, numpy as np, pandas as pd, warnings
from datetime import datetime
warnings.filterwarnings('ignore')
# 2 Tem 2026 — Windows konsolu (cp1254) emoji'leri kodlayamıyordu → --bugscan
# UnicodeEncodeError ile çöküyordu. stdout'u utf-8'e al (feature_karne deseni).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DB = 'patron.db'
VERILER = 'veriler'
CACHE = '_backfill_feats.parquet'
MIN_N = 30          # bir dilim/tarama için minimum örnek
MIN_SYM = 6         # minimum farklı hisse (konsantrasyon koruması)
SPREAD = 10         # anlamlı feature ayrışması (puan)

# ───────────────────────── FEATURE HESAPLAMA (app.py birebir) ─────────────────────────
def _cmf(df, n):
    rng = (df['High'] - df['Low']).replace(0, np.nan)
    mfv = (((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / rng * df['Volume']).fillna(0)
    v = df['Volume'].rolling(n).sum().iloc[-1]
    return float(mfv.rolling(n).sum().iloc[-1] / v) if v else 0.0

def _feats(df):
    o = {}; c = df['Close']
    seg = df.tail(252); h, l, cv = float(seg['High'].max()), float(seg['Low'].min()), float(c.iloc[-1])
    o['p52'] = (cv - l) / (h - l) * 100 if h > l else None
    d = c.diff(); g = d.where(d > 0, 0).rolling(14).mean(); ls = (-d.where(d < 0, 0)).rolling(14).mean()
    o['rsi'] = float((100 - 100 / (1 + g / ls)).iloc[-1])
    try:
        c5, c20 = _cmf(df, 5), _cmf(df, 20)
        o['cmf'] = ('turning_up' if c5 > 0 and c20 < 0 else 'turning_down' if c5 < 0 and c20 > 0
                    else 'strong_pos' if c5 > .05 and c20 > .05 else 'strong_neg' if c5 < -.05 and c20 < -.05
                    else 'pos' if c20 > .05 else 'neg' if c20 < -.05 else 'neutral')
    except Exception:
        o['cmf'] = None
    try:
        ma = c.rolling(20).mean(); sd = c.rolling(20).std(); atr = (df['High'] - df['Low']).rolling(20).mean()
        sq = ((ma + 2 * sd) < (ma + 1.5 * atr)) & ((ma - 2 * sd) > (ma - 1.5 * atr)); cnt = 0
        for v in sq.iloc[::-1]:
            if bool(v): cnt += 1
            else: break
        o['sq'] = cnt
    except Exception:
        o['sq'] = None
    try:
        s = df.tail(60); pr = ((s['High'] + s['Low']) / 2).values; vo = s['Volume'].values
        hist, edges = np.histogram(pr, bins=30, weights=vo); tot = hist.sum()
        poc = (edges[int(np.argmax(hist))] + edges[int(np.argmax(hist)) + 1]) / 2
        order = np.argsort(hist)[::-1]; cum = 0; inc = set()
        for i in order:
            inc.add(int(i)); cum += hist[i]
            if cum / tot >= .70: break
        il = sorted(inc); val, vah = edges[il[0]], edges[il[-1] + 1]
        pos = (poc - val) / (vah - val) if vah > val else .5
        o['vp'] = 'akumulasyon' if pos < .4 else 'dagitim' if pos > .6 else 'denge'
    except Exception:
        o['vp'] = None
    return o

_PQ = {}
def _load(sym):
    if sym in _PQ: return _PQ[sym]
    base = sym.replace('.IS', ''); df = None
    for cand in (f"{VERILER}/{sym}_1d.parquet", f"{VERILER}/{base}.IS_1d.parquet", f"{VERILER}/{base}_1d.parquet"):
        if os.path.exists(cand):
            df = pd.read_parquet(cand); break
    _PQ[sym] = df; return df

def build_backfill(force=False):
    """signal_results → point-in-time feature snapshot. Parquet cache; DB değişince yeniler."""
    if (not force) and os.path.exists(CACHE) and os.path.getmtime(CACHE) >= os.path.getmtime(DB):
        return pd.read_parquet(CACHE)
    print("» Feature backfill hesaplanıyor (veriler/*.parquet)...")
    c = sqlite3.connect(DB)
    rows = c.execute("SELECT symbol,signal_date,scan_type,ret_5g,hit_5g,ret_10g,hit_10g,ret_20g,hit_20g "
                     "FROM signal_results WHERE ret_5g IS NOT NULL").fetchall()
    c.close()
    recs = []
    for sym, sd, st, r5, h5, r10, h10, r20, h20 in rows:
        df = _load(sym)
        if df is None: continue
        sub = df[df.index <= pd.Timestamp(sd)]
        if len(sub) < 60: continue
        recs.append({'sym': sym, 'date': sd, 'month': sd[:7], 'st': st,
                     'r5': r5, 'h5': h5, 'r10': r10, 'h10': h10, 'r20': r20, 'h20': h20, **_feats(sub)})
    D = pd.DataFrame(recs)
    D.to_parquet(CACHE)
    print(f"  {len(D)} sinyal feature'landı → {CACHE}")
    return D

def parse_tier_map():
    """app.py SCANNER_TIER_MAP'i regex ile {scan_type: (tier, hit10, ret10)} olarak çıkar."""
    try:
        src = open('app.py', encoding='utf-8').read()
    except Exception:
        return {}
    m = re.search(r"SCANNER_TIER_MAP\s*=\s*\{(.*?)\n\}", src, re.S)
    if not m: return {}
    out = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"\s*'([^']+)':\s*\('([^']+)',\s*([\d.]+),\s*([\d.]+)", line)
        if mm:
            out[mm.group(1)] = (mm.group(2), float(mm.group(3)), float(mm.group(4)))
    return out

# ───────────────────────── BÖLÜM A: TIER SAĞLIK ─────────────────────────
def section_a(D, tmap):
    L = ["## A) TIER SAĞLIK — iddia vs gerçek (tam veri, 10g)", ""]
    L.append(f"{'tarama':16}{'tier(iddia)':18}{'iddia_hit':>10}{'gercek_hit':>11}{'gercek_ret':>11}{'n':>6}  DURUM")
    flags = []
    for st, g in D.groupby('st'):
        gg = g[g['h10'].notna()]
        if len(gg) < MIN_N: continue
        real_h = gg['h10'].mean() * 100; real_r = gg['r10'].mean(); n = len(gg)
        tier, claim_h, claim_r = tmap.get(st, ('—', None, None))
        durum = ''
        if claim_h is not None:
            if real_h < claim_h - 12:
                durum = f'⚠ ŞİŞİK (iddia %{claim_h:.0f} → gerçek %{real_h:.0f})'; flags.append((st, durum))
            if tier.startswith('TIER_1') and real_r <= 1:
                durum += ' ⚠ TIER_1 ama ret zayıf'; flags.append((st, 'TIER_1 ret zayıf'))
            if tier.startswith(('TIER_1', 'TIER_2')) and real_h < 50:
                durum += ' ⚠ hit<%50'; flags.append((st, 'hit<50'))
        ch = f"%{claim_h:.0f}" if claim_h is not None else '—'
        L.append(f"{st:16}{tier:18}{ch:>10}{('%'+format(real_h,'.0f')):>11}{('%'+format(real_r,'.1f')):>11}{n:>6}  {durum}")
    L.append("")
    L.append(f"**Özet:** {len(flags)} tarama dikkat istiyor (şişik tier / zayıf getiri / düşük hit)." if flags
             else "**Özet:** belirgin şişik/bayat tier yok.")
    L.append("")
    return "\n".join(L)

# ───────────────────────── BÖLÜM B: REJİM KIRILIMI ─────────────────────────
def section_b(D):
    months = sorted(m for m in D['month'].unique() if D[D.month == m].shape[0] >= 50)
    L = [f"## B) REJİM KIRILIMI — ay bazında 5g hit/ret  (aylar: {', '.join(months)})", ""]
    # genel piyasa havası
    L.append("**Piyasa havası (tüm sinyaller):** " +
             " · ".join(f"{m}: %{D[D.month==m]['h5'].mean()*100:.0f}/{D[D.month==m]['r5'].mean():+.1f}" for m in months))
    L.append("")
    L.append(f"{'tarama':16}" + "".join(f"{m[5:]+'_hit':>10}{m[5:]+'_ret':>10}" for m in months) + "  NOT")
    for st, g in D.groupby('st'):
        if len(g) < MIN_N: continue
        cells = ''; vals = []
        for m in months:
            gm = g[g.month == m]
            if len(gm) >= 12:
                h = gm['h5'].mean() * 100; r = gm['r5'].mean(); vals.append(r)
                cells += f"{('%'+format(h,'.0f')):>10}{('%'+format(r,'.1f')):>10}"
            else:
                cells += f"{'-':>10}{'-':>10}"; vals.append(None)
        note = ''
        clean = [v for v in vals if v is not None]
        if len(clean) >= 2:
            if all(v > 0 for v in clean): note = '✅ tüm rejimlerde +'
            elif clean[0] is not None and len(clean) >= 2 and clean[0] > 0 and clean[-1] <= 0: note = '⚠ sadece erken/boğa ayında'
        L.append(f"{st:16}{cells}  {note}")
    L.append("")
    return "\n".join(L)

# ───────────────────────── BÖLÜM C: FEATURE AYRIŞTIRICI ─────────────────────────
def _bucket(col):
    if col == 'rsi': return lambda v: 'a<40' if v < 40 else 'b40-55' if v < 55 else 'c55-70' if v < 70 else 'd>70'
    if col == 'p52': return lambda v: 'a:dip' if v < 25 else 'b25-50' if v < 50 else 'c50-75' if v < 75 else 'd:tepe'
    if col == 'sq':  return lambda v: 'a:yok' if v == 0 else 'b1-9g' if v < 10 else 'c10g+'
    return None

def section_c(D):
    months = sorted(m for m in D['month'].unique() if D[D.month == m].shape[0] >= 50)
    L = ["## C) FEATURE AYRIŞTIRICI — her iki rejimde tutan giriş şartı (filtre adayları)", ""]
    if len(months) < 2:
        L.append("_(İki rejim için yeterli ay yok — tek pencere; ayrıştırıcı atlandı.)_\n")
        return "\n".join(L)
    feats = [('rsi', 'RSI'), ('p52', '52H'), ('sq', 'Sıkışma'), ('vp', 'VP'), ('cmf', 'CMF')]
    any_found = False
    for st, S in D.groupby('st'):
        if len(S) < 60: continue
        hits = []
        for col, nm in feats:
            SS = S[S[col].notna()].copy()
            if SS.empty: continue
            bk = _bucket(col)
            SS['b'] = SS[col].apply(bk) if bk else SS[col]
            res = {}; ok = True
            for m in months[-2:]:
                gg = SS[SS.month == m].groupby('b').agg(n=('h5', 'size'), h=('h5', 'mean'), ns=('sym', 'nunique'))
                gg = gg[(gg.n >= 20) & (gg.ns >= MIN_SYM)]
                if len(gg) < 2: ok = False; break
                res[m] = gg
            if not ok: continue
            common = set(res[months[-2]].index) & set(res[months[-1]].index)
            if len(common) < 2: continue
            comb = {b: (res[months[-2]].loc[b, 'h'] + res[months[-1]].loc[b, 'h']) / 2 * 100 for b in common}
            best = max(comb, key=comb.get); worst = min(comb, key=comb.get); sp = comb[best] - comb[worst]
            b1 = res[months[-2]].loc[best, 'h'] > res[months[-2]].loc[worst, 'h']
            b2 = res[months[-1]].loc[best, 'h'] > res[months[-1]].loc[worst, 'h']
            if sp >= SPREAD and b1 and b2:
                hits.append(f"  - [{nm}] **'{best}'** > '{worst}' (+{sp:.0f}p, iki rejimde de)")
        if hits:
            any_found = True
            L.append(f"### {st}"); L.extend(hits); L.append("")
    if not any_found:
        L.append("_(Bu turda iki-rejimde tutan belirgin ayrıştırıcı çıkmadı.)_\n")
    return "\n".join(L)

# ───────────────────────── BÖLÜM D: MASTER SKOR GÖZLEM ─────────────────────────
def section_d_master():
    """Master Skor + 4 bileşeni → vade-vade getiri (19 Haz 2026 — GÖZLEM, Temmuz sonu kararı).
    patron.db scan_signals (f_master_score, f_ms_*) × signal_results. Inversiyon var mı izle."""
    L = ["## D) MASTER SKOR GÖZLEM — Temmuz sonu kararı (inversiyon takibi)", ""]
    try:
        con = sqlite3.connect(DB); cur = con.cursor()
        cols = [('f_master_score', 'MASTER SKOR'), ('f_ms_trend', 'Trend'),
                ('f_ms_momentum', 'Momentum'), ('f_ms_ict', 'ICT'), ('f_ms_radar2', 'Radar2')]
        bucket = ("CASE WHEN s.{c}<25 THEN '1 <25' WHEN s.{c}<50 THEN '2 25-50' "
                  "WHEN s.{c}<75 THEN '3 50-75' ELSE '4 >75' END")
        for col, lbl in cols:
            rows_any = False
            seg = [f"### {lbl}"]
            for g in ('5g', '10g', '20g'):
                cur.execute(f"""SELECT {bucket.format(c=col)} b, COUNT(*) n,
                    ROUND(AVG(r.hit_{g})*100,1) hit, ROUND(AVG(r.ret_{g}),2) ret
                    FROM signal_results r JOIN scan_signals s ON s.id=r.signal_id
                    WHERE s.{col} IS NOT NULL AND r.ret_{g} IS NOT NULL
                    GROUP BY b ORDER BY b""")
                rr = cur.fetchall()
                if len(rr) < 2:
                    continue
                rows_any = True
                lo = next((x for x in rr if x[0].startswith('1')), None)
                hi = next((x for x in rr if x[0].startswith('4')), None)
                inv = ""
                if lo and hi and lo[3] is not None and hi[3] is not None:
                    inv = "  ⚠ TERS (düşük>yüksek)" if lo[3] > hi[3] else "  ✓ düz"
                cells = " | ".join(f"{x[0]}:n{x[1]} %{x[2]}/r{x[3]}" for x in rr)
                seg.append(f"  {g}: {cells}{inv}")
            if rows_any:
                L += seg + [""]
        con.close()
        L.append("**Not:** Master Skor = 'şu anki güç' (52H ile korele). Kısa vadede TERS = kısa-vade reversal. "
                 "20g olgunlaşınca (Temmuz) karar: uzun vadede pozitife dönüyor mu (momentum) yoksa gerçekten zayıf mı.")
    except Exception as e:
        L.append(f"_(Master gözlem hatası: {e})_")
    L.append("")
    return "\n".join(L)


# ───────────────────────── BUG-TARAMA MODU (haftalık, hızlı) ─────────────────────────
def bugscan():
    """HAFTALIK BUG TARAMASI — sadece anomali/kırık flag uyarısı, KARAR metriği YOK (19 Haz 2026).
    Çalıştır: python scanner_karne.py --bugscan
    Amaç: kırık/ölü/donmuş flag, veri hijyeni, .IS, tazelik — hızlı yakala. Hızlı (backfill yok)."""
    import datetime as _dt
    # BİLİNEN ölü/nadir/alt-küme/yeni flag'ler — bunlar "kırık" değil, beklenen. Yanlış alarm verme.
    KNOWN = {
        'f_tefas_yeni_giris': 'TEFAS kaldırıldı (O21)', 'f_buyback_aktif': 'KAP kaldırıldı',
        'f_buyback_dip_aliyor': 'KAP kaldırıldı', 'f_threshold_asildi': 'KAP kaldırıldı',
        'f_insider_first_buy': 'KAP kaldırıldı', 'f_kurumsal_anchor': 'TEFAS/KAP kaldırıldı',
        'f_tefas_konsensus_alim': 'TEFAS kaldırıldı', 'f_tefas_konsensus_satim': 'TEFAS kaldırıldı',
        'f_yabanci_anchor': 'nadir tetikler', 'f_udvr_climax': 'nadir olay (52H extrem)',
        'f_near_ifvg': 'AI dışı (gürültü, kapatıldı)', 'f_breaker_block_active': 'AI dışı',
        'f_tavan_skor': 'sadece tavan alt-kümesi', 'f_tavan_kat': 'sadece tavan',
        'f_tavan_confluence_n': 'sadece tavan',
        'f_sentiment_score': '19 Haz YENİ — ilk Master Scan bekliyor',
        'f_ict_model': '19 Haz YENİ — ilk Master Scan bekliyor',
        'f_smart_money_score': '19 Haz YENİ — ilk Master Scan bekliyor',
        'f_adv_tl': '19 Haz Faz1 YENİ — ilk Master Scan bekliyor',
        'f_liquidity_tier': '19 Haz Faz1 YENİ — ilk Master Scan bekliyor',
        'f_manip_risk': '19 Haz Faz1 YENİ — ilk Master Scan bekliyor',
    }
    print(f"\n🐞 BUG TARAMASI — {datetime.now():%d.%m.%Y %H:%M}  (karar değil, sadece anomali)")
    con = sqlite3.connect(DB); cur = con.cursor()
    alerts = []   # SADECE bilinmeyen (gerçekten yeni) anomaliler
    known_hits = []

    # 1) Tazelik
    last = cur.execute("SELECT MAX(scan_date) FROM scan_signals").fetchone()[0]
    if last:
        gun = (_dt.date.today() - _dt.datetime.strptime(last, "%Y-%m-%d").date()).days
        print(f"\n  Son tarama: {last} ({gun} gün önce)" + ("  ⚠ BAYAT (Master Scan çalışmıyor?)" if gun > 4 else "  ✓"))
        if gun > 4: alerts.append("scan_signals bayat")

    # 2) Flag doluluk + anomali (son 14 gün)
    cutoff = (_dt.date.today() - _dt.timedelta(days=14)).strftime("%Y-%m-%d")
    cols = [r[1] for r in cur.execute("PRAGMA table_info(scan_signals)").fetchall() if r[1].startswith('f_')]
    ntot = cur.execute("SELECT COUNT(*) FROM scan_signals WHERE scan_date>=?", (cutoff,)).fetchone()[0]
    print(f"\n  Son 14g sinyal: {ntot}")
    if ntot >= 30:
        print(f"  {'flag':24}{'doluluk':>9}  durum")
        for col in cols:
            nf = cur.execute(f"SELECT COUNT({col}) FROM scan_signals WHERE scan_date>=?", (cutoff,)).fetchone()[0]
            fill = nf / ntot * 100 if ntot else 0
            note = ""
            if fill < 5:
                note = "⚠ neredeyse BOŞ (ölü/kırık?)"
            else:
                # numerik varyans / donmuş kontrol
                try:
                    vals = [r[0] for r in cur.execute(
                        f"SELECT DISTINCT {col} FROM scan_signals WHERE scan_date>=? AND {col} IS NOT NULL LIMIT 5",
                        (cutoff,)).fetchall()]
                    if len(vals) == 1:
                        note = f"⚠ TEK DEĞER ({vals[0]}) — donmuş?"
                except Exception:
                    pass
            if note:
                if col in KNOWN:
                    known_hits.append(f"{col}: {note} → bilinen: {KNOWN[col]}")
                else:
                    alerts.append(f"{col}: {note}")
                    print(f"  {col:24}{fill:>7.0f}%  🚨 {note}  (BEKLENMEDİK!)")
        if not alerts:
            print("  (beklenmedik flag anomalisi YOK ✓ — gerçekten yeni bir kırık yok)")
        if known_hits:
            print(f"  ℹ️  {len(known_hits)} bilinen ölü/nadir/yeni flag (alarm değil — beklenen)")
    else:
        print("  ⚠ son 14g yetersiz sinyal — Master Scan çalıştır")

    # 3) .IS tutarsızlık
    wis, wos = cur.execute("SELECT SUM(CASE WHEN symbol LIKE '%.IS' THEN 1 ELSE 0 END), "
                           "SUM(CASE WHEN symbol NOT LIKE '%.IS' THEN 1 ELSE 0 END) FROM scan_signals "
                           "WHERE scan_date>=?", (cutoff,)).fetchone()
    print(f"\n  .IS (son 14g): {wis or 0} '.IS'li · {wos or 0} '.IS'siz" +
          ("  ⚠ hâlâ karışık (yeni yazımlar .IS'siz olmalı)" if (wis and wos) else "  ✓ tutarlı"))
    con.close()

    # 4) Veri hijyeni (hızlı örnek — 120 parquet)
    import glob, pandas as pd, numpy as np
    files = glob.glob(f"{VERILER}/*_1d.parquet")[:120]
    dj = zv = stale = 0
    today = pd.Timestamp(_dt.date.today())
    for f in files:
        try:
            df = pd.read_parquet(f)
            if len(df) < 30: continue
            l20 = df.tail(20)
            if (l20['Open'] == l20['Close']).mean() > 0.4: dj += 1
            if (l20['Volume'] == 0).mean() > 0.3: zv += 1
            if (today - pd.Timestamp(df.index[-1])).days > 7: stale += 1
        except Exception:
            pass
    print(f"\n  Veri hijyeni (120 örnek): doji-ağır {dj} · 0-hacim {zv} · bayat-parquet {stale}" +
          ("  ⚠" if (dj > 5 or zv > 5 or stale > 10) else "  ✓"))

    print(f"\n{'='*50}")
    if alerts:
        print(f"🚨 SONUÇ: {len(alerts)} BEKLENMEDİK anomali — incele:")
        for a in alerts: print(f"  • {a}")
    else:
        print("🐞 SONUÇ: TEMİZ ✓ — beklenmedik kırık/donmuş flag yok")
    if known_hits:
        print(f"\n  ℹ️ Bilinen (alarm değil, beklenen):")
        for a in known_hits: print(f"     - {a}")
    print("\n(Bu mod KARAR vermez — sadece bug avı. Karar için: python scanner_karne.py)")


# ───────────────────────── CONTROLS CHECK (heartbeat için, sonuç döndürür) ─────────────────────────
def controls_check():
    """Heartbeat'in çağırdığı KONTROL — bugscan anomali mantığını çalıştırır, YAZMAZ döndürür.
    Dönüş: dict(ran [bool: kontrol yapıldı mı], new_anomalies [list], known_n [int], data_fresh [bool])."""
    import datetime as _dt
    KNOWN = {
        'f_tefas_yeni_giris', 'f_buyback_aktif', 'f_buyback_dip_aliyor', 'f_threshold_asildi',
        'f_insider_first_buy', 'f_kurumsal_anchor', 'f_tefas_konsensus_alim', 'f_tefas_konsensus_satim',
        'f_yabanci_anchor', 'f_udvr_climax', 'f_near_ifvg', 'f_breaker_block_active',
        'f_tavan_skor', 'f_tavan_kat', 'f_tavan_confluence_n',
        'f_sentiment_score', 'f_ict_model', 'f_smart_money_score',
        'f_adv_tl', 'f_liquidity_tier', 'f_manip_risk',
    }
    res = {'ran': False, 'new_anomalies': [], 'known_n': 0, 'data_fresh': False}
    con = sqlite3.connect(DB); cur = con.cursor()
    cutoff = (_dt.date.today() - _dt.timedelta(days=14)).strftime("%Y-%m-%d")
    cols = [r[1] for r in cur.execute("PRAGMA table_info(scan_signals)").fetchall() if r[1].startswith('f_')]
    ntot = cur.execute("SELECT COUNT(*) FROM scan_signals WHERE scan_date>=?", (cutoff,)).fetchone()[0]
    last = cur.execute("SELECT MAX(scan_date) FROM scan_signals").fetchone()[0]
    if last:
        res['data_fresh'] = (_dt.date.today() - _dt.datetime.strptime(last, "%Y-%m-%d").date()).days <= 7
    if ntot >= 30:
        for col in cols:
            nf = cur.execute(f"SELECT COUNT({col}) FROM scan_signals WHERE scan_date>=?", (cutoff,)).fetchone()[0]
            fill = nf / ntot * 100 if ntot else 0
            note = ""
            if fill < 5:
                note = "neredeyse boş"
            else:
                try:
                    vals = [r[0] for r in cur.execute(
                        f"SELECT DISTINCT {col} FROM scan_signals WHERE scan_date>=? AND {col} IS NOT NULL LIMIT 5",
                        (cutoff,)).fetchall()]
                    if len(vals) == 1:
                        note = f"tek değer ({vals[0]})"
                except Exception:
                    pass
            if note:
                if col in KNOWN:
                    res['known_n'] += 1
                else:
                    res['new_anomalies'].append(f"{col}: {note}")
        res['ran'] = True   # kontrol başarıyla yapıldı
    con.close()
    return res


# ───────────────────────── DATA INTEGRITY (girdi-veri doğruluğu — en temel katman) ─────────────────────────
def data_integrity_check():
    """GİRDİ-VERİ DOĞRULUĞU ('çöp girer çöp çıkar'). XU100 benchmark sağlığı + hisse fiyat gap
    anomalisi (split/bad tick) + likidite eşiği bayatlığı (enflasyon). Dönüş: dict(problems, ran)."""
    import datetime as _dt
    today = _dt.date.today()
    out = {'problems': [], 'ran': False}
    try:
        # 1) XU100 — beta/RS/alpha HEPSİ buna bağlı; bozuksa her relative ölçüm yanlış
        xf = glob.glob(f'{VERILER}/XU100*.parquet')
        if not xf:
            out['problems'].append("XU100 parquet YOK — beta/RS/alpha bozuk olur")
        else:
            xu = pd.read_parquet(xf[0])
            if len(xu) < 60:
                out['problems'].append("XU100 verisi kısa (<60 bar)")
            else:
                sd = (today - pd.Timestamp(xu.index[-1]).date()).days
                if sd > 5:
                    out['problems'].append(f"XU100 BAYAT ({sd}g) — relative ölçümler eski")
                ret = xu['Close'].pct_change().dropna().tail(60)
                if (ret.abs() > 0.08).sum() > 3:
                    out['problems'].append("XU100'de anormal sıçramalar (veri hatası?) — endeks nadiren >%8 oynar")
                # XU100 Volume=0 endeks için NORMAL — flag ETME
        # 2) Hisse fiyat gap anomalisi — gerçekçi olmayan sıçrama (split-adjusted değil / bad tick)
        sample = glob.glob(f'{VERILER}/*.IS_1d.parquet')[:200]
        anom = 0; advs = []; open_bad = 0
        for f in sample:
            try:
                df = pd.read_parquet(f)
                if len(df) < 60:
                    continue
                ret = df['Close'].pct_change().dropna().tail(120)
                if (ret.abs() > 0.25).sum() > 1:   # BIST limit ~%10; >%25 2+ kez = veri hatası şüphesi
                    anom += 1
                # 2 Tem 2026 — OPEN/CLOSE ÖLÇEK BOZULMASI: Open, Close'dan farklı düzeltme
                # bazında (auto_adjust karışması: fetcher=False vs app=True). Aynı gün Open/Close
                # oranı 0.7-1.4 dışı olamaz (imkansız bar) → parquet bozuk imzası.
                # SADECE SON 30 bar: bizim aktif bozulmamız yeni bar'larda; Yahoo'nun eski
                # bozuk Open tick'leri (illikit hisse) kalıcıdır → yanlış alarm yapmasın.
                _r = (df['Open'].tail(30) / df['Close'].tail(30)).replace([float('inf')], 0).dropna()
                if ((_r < 0.7) | (_r > 1.4)).sum() >= 3:
                    open_bad += 1
                if len(df) >= 20:
                    advs.append(float((df['Close'] * df['Volume']).tail(20).mean()) / 1e6)
            except Exception:
                pass
        if sample and anom / len(sample) > 0.05:
            out['problems'].append(f"{anom}/{len(sample)} hissede aşırı fiyat sıçraması (SİSTEMİK split/veri hatası?)")
        if sample and open_bad / len(sample) > 0.02:
            out['problems'].append(f"{open_bad}/{len(sample)} hissede OPEN/CLOSE ÖLÇEK UYUMSUZ "
                                   f"(auto_adjust karışması → parquet bozuk) → repair_parquets.py çalıştır")
        # 3) Likidite eşiği bayatlığı (enflasyon — TL eşiği kayar). Kalibrasyon medyanı 89mn (19 Haz).
        if advs:
            med = sorted(advs)[len(advs) // 2]
            if med > 89 * 1.8 or med < 89 * 0.55:
                out['problems'].append(f"LİKİDİTE EŞİĞİ BAYAT — medyan ADV {med:.0f}mn (kalibrasyon 89mn) → yeniden kalibre")
        # 4) TAKVİM-EKSİK GÜN — serinin ORTASINDA eksik işlem günü (tazelik DEĞİL — delik!).
        # XU100 benchmark KRİTİK: eksik gün → % değişim yanlış (n-gün vs 1-gün) + beta/RS hizalaması kayar.
        try:
            import bist_calendar as _bc
            import datetime as _dt2
            if xf and len(xu) >= 10:
                _last = pd.Timestamp(xu.index[-1]).date()
                _xd = set(pd.Timestamp(d).date() for d in xu.index[-25:])
                _exp = [d for d in (_last - _dt2.timedelta(days=i) for i in range(0, 14))
                        if _bc.is_trading_day(d) and d <= _last]
                _miss = sorted(d.isoformat() for d in _exp if d not in _xd)
                if _miss:
                    out['problems'].append(f"XU100'de EKSİK İŞLEM GÜNÜ {_miss[-3:]} (orta-seri delik) — "
                                            f"% değişim + beta/RS YANLIŞ. Yahoo kaynağı eksikse İsyatirim "
                                            f"endeks-fallback gerek (yeniden-çek çözmeyebilir)")
            # Hisseler (sample) — sistemik eksik gün var mı
            _smiss = 0
            for f in sample[:100]:
                try:
                    d2 = pd.read_parquet(f)
                    if len(d2) < 10:
                        continue
                    _l2 = pd.Timestamp(d2.index[-1]).date()
                    _d2 = set(pd.Timestamp(d).date() for d in d2.index[-25:])
                    _e2 = [d for d in (_l2 - _dt2.timedelta(days=i) for i in range(0, 10))
                           if _bc.is_trading_day(d) and d <= _l2]
                    if any(d not in _d2 for d in _e2):
                        _smiss += 1
                except Exception:
                    pass
            if _smiss > 10:
                out['problems'].append(f"{_smiss}/100 hissede eksik işlem günü (SİSTEMİK veri çekme sorunu)")
        except Exception:
            pass
        # 5) DOĞRULAMA KAPISI reddleri — gap-fill şüpheli değer reddetmiş mi (son 7g)?
        try:
            import json as _json, datetime as _dt3
            _rf = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'gapfill_rejects.json')
            if os.path.exists(_rf):
                _rej = _json.load(open(_rf, encoding='utf-8'))
                _cut = (_dt3.date.today() - _dt3.timedelta(days=7)).isoformat()
                _recent = [r for r in _rej if str(r.get('logged_at', ''))[:10] >= _cut]
                if _recent:
                    _ex = _recent[-1]
                    out['problems'].append(
                        f"DOĞRULAMA KAPISI: {len(_recent)} şüpheli endeks değeri REDDEDİLDİ, doldurulMADI "
                        f"(örn {_ex.get('ticker')} {_ex.get('date')}={_ex.get('value')}: {_ex.get('reason')}) — manuel bak")
        except Exception:
            pass
        out['ran'] = True
    except Exception as e:
        out['problems'].append(f"veri denetimi çalışamadı: {str(e)[:50]}")
    return out


# ───────────────────────── GÜÇ-FİLTRE DENETİMİ (sürekli özdenetim) ─────────────────────────
def guc_filter_audit():
    """SÜREKLİ GÜÇ-FİLTRE DENETİMİ — her güç-kullanan tarama için 🟢 vs 🔴 getiri.
    Güç etiketi dürüst mü (🟢>🔴) yoksa ters mi? KULLANILAN bir filtre ters dönerse ALARM (drift):
    bugün kanıtlı (Harmonik) yarın bozulabilir → makine kendi kendini denetler. Kaldırılan
    filtreler (VIP/Pre-Launch) de izlenir (sessizce geri gelmesin / aday doğrulansın diye).
    Dönüş: dict(lines[list], alerts[list])."""
    out = {'lines': [], 'alerts': []}
    try:
        c = sqlite3.connect(DB); cur = c.cursor()
        # (etiket, scan_type, feature, 🟢 koşul, 🔴 koşul, HÂLÂ_KULLANILIYOR mu)
        checks = [
            ('Harmonik (KULLANILIYOR)', 'harmonik_confluence', 'f_52h_pos', '>=60', '<40', True),
            ('VIP (kaldırıldı)',         'vip_formasyon',       'f_52h_pos', '>=60', '<40', False),
            ('Pre-Launch (kaldırıldı)',  'prelaunch_bos',       'f_52h_pos', '>=60', '<40', False),
        ]
        for name, st_, col, g_op, r_op, in_use in checks:
            def grp(op):
                q = (f"SELECT COUNT(*), AVG(r.ret_5g) FROM signal_results r "
                     f"JOIN scan_signals s ON r.signal_id=s.id "
                     f"WHERE r.scan_type='{st_}' AND r.ret_5g IS NOT NULL AND s.{col}{op}")
                return cur.execute(q).fetchone()
            ng, rg = grp(g_op); nr, rr = grp(r_op)
            if (ng or 0) < 15 or (nr or 0) < 15:
                out['lines'].append(f"  {name}: yetersiz veri (🟢{ng or 0}/🔴{nr or 0})")
                continue
            honest = (rg or 0) > (rr or 0)
            tag = '✅ dürüst' if honest else '🔴 TERS'
            out['lines'].append(f"  {name}: 🟢%{(rg or 0):+.2f} vs 🔴%{(rr or 0):+.2f} → {tag}")
            # KULLANILAN bir filtre ters dönerse → ALARM (drift yakalama)
            if in_use and not honest:
                out['alerts'].append(f"{name} 52H güç TERS DÖNDÜ (🟢%{(rg or 0):+.2f} ≤ 🔴%{(rr or 0):+.2f}) — tek-rejim etkisi olabilir (düşüşte zirve yakını en sert düşer); KALDIRMA, izle. Rejim dönünce makas hâlâ tersse o zaman kaldır.")
        c.close()
    except Exception as e:
        out['lines'].append(f"guc_filter_audit hata: {str(e)[:50]}")
    return out


# ───────────────────────── OBSERVATION WATCH (izlemedeki aday filtreler) ─────────────────────────
def observation_watch():
    """İZLEMEDEKİ aday filtreler — henüz hard-kural YAPILMADI, cross-rejim doğrulama bekliyor.
    20 Haz 2026: VIP Formasyon RSI 55-70. Haziran-only kanıt (5g +%2.29 vs diğer −%0.27); feature
    loglama 3 Haz'da başladı → Mayıs boş. Bu watch: aday hâlâ tutuyor mu + cross-rejim veri geldi mi?
    Dönüş: rapor satırları list[str]."""
    out = []
    try:
        c = sqlite3.connect(DB); cur = c.cursor()
        # VIP RSI 55-70 — aday filtre
        def _vip(where):
            q = f"""SELECT COUNT(*), AVG(r.ret_5g), AVG(r.hit_5g)
            FROM signal_results r JOIN scan_signals s ON r.signal_id=s.id
            WHERE r.scan_type='vip_formasyon' AND r.ret_5g IS NOT NULL AND s.f_rsi IS NOT NULL {where}"""
            return cur.execute(q).fetchone()
        n1, r1, h1 = _vip("AND s.f_rsi>=55 AND s.f_rsi<70")
        n0, r0, h0 = _vip("AND (s.f_rsi<55 OR s.f_rsi>=70)")
        # cross-rejim: feature'lı VIP sinyallerinin tarih aralığı (Haziran dışı geldi mi?)
        span = cur.execute("""SELECT MIN(s.scan_date), MAX(s.scan_date)
            FROM scan_signals s WHERE s.scan_type='vip_formasyon' AND s.f_rsi IS NOT NULL""").fetchone()
        mn, mx = span if span else (None, None)
        out.append("👁 İZLEMEDE — VIP Formasyon RSI 55-70 (aday filtre, hard-kural değil):")
        if n1 and n1 >= 10:
            out.append(f"   🟢 RSI 55-70: n={n1} 5g+%{(r1 or 0)*100/100:.2f} hit%{(h1 or 0)*100:.0f}  ⟷  diğer: n={n0} 5g%{(r0 or 0):+.2f} hit%{(h0 or 0)*100:.0f}")
        else:
            out.append(f"   yeterli feature'lı sinyal yok (n={n1 or 0})")
        # cross-rejim kapısı: Mayıs/öncesi feature'lı veri var mı
        pre_jun = cur.execute("""SELECT COUNT(*) FROM scan_signals s
            WHERE s.scan_type='vip_formasyon' AND s.f_rsi IS NOT NULL AND s.scan_date<'2026-06-01'""").fetchone()[0]
        if pre_jun >= 20:
            out.append(f"   ✅ CROSS-REJİM VERİ GELDİ ({pre_jun} Haziran-öncesi) → artık doğrulanabilir, KARAR ZAMANI")
        else:
            out.append(f"   ⏳ hâlâ tek-rejim (Haziran-only); cross-rejim için Temmuz+ bekleniyor (feature loglama 3 Haz başladı)")
        c.close()
    except Exception as e:
        out.append(f"observation_watch hata: {str(e)[:50]}")
    return out


# ───────────────────────── MAIN ─────────────────────────
def main():
    if not os.path.exists(DB):
        print("patron.db bulunamadı — proje kökünden çalıştır."); return
    if '--bugscan' in sys.argv:   # haftalık hızlı bug taraması (karar değil)
        bugscan(); return
    force = '--rebuild' in sys.argv
    D = build_backfill(force=force)
    D = D[D['month'].str.match(r'2026-\d2') | D['month'].notna()]
    tmap = parse_tier_map()
    head = [f"# TARAMA KARNE — {datetime.now():%d.%m.%Y %H:%M}",
            f"Toplam ölçülebilir sinyal: **{len(D)}** · tarama tipi: **{D['st'].nunique()}** · "
            f"aylar: {', '.join(sorted(D['month'].unique()))}", ""]
    report = ("\n".join(head) + "\n" + section_a(D, tmap) + "\n" + section_b(D)
              + "\n" + section_c(D) + "\n" + section_d_master())
    open('scanner_karne_report.md', 'w', encoding='utf-8').write(report)
    # konsola sadeleştirilmiş çıktı
    print("\n" + report)
    print("\n✅ Rapor yazıldı → scanner_karne_report.md")

if __name__ == '__main__':
    main()
