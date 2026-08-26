#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
APP SKOR DENETİMİ — app'in KENDİ kaydettiği kompozit skorları (drift yok, gerçek
rakam) getiriye karşı + rejim ayrımında ölçer. Kopyalama yok: scan_signals'a
canlı app'in yazdığı f_* kolonları × signal_results (backtest_runner getirileri).

Soru: master_score CMF gibi rejim-DAYANIKLI mı, yoksa obv_slope gibi boğa serabı mı?
Çıktı: konsol + app_score_report.md
"""
import sqlite3, os, sys, numpy as np, pandas as pd
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

DB = 'patron.db'; VERILER = 'veriler'
BADGE_TYPES = ('altin_setup', 'platin_setup', 'vip_formasyon')


def xu100_regime():
    for cand in (f"{VERILER}/XU100.IS_1d.parquet", f"{VERILER}/XU100_1d.parquet"):
        if os.path.exists(cand):
            x = pd.read_parquet(cand).sort_index()
            sma = x['Close'].rolling(50).mean()
            reg = pd.Series(np.where(x['Close'] > sma, 'boğa', 'ayı'), index=x.index.strftime('%Y-%m-%d'))
            return reg[~reg.index.duplicated()]
    return None


def quintile(d, col, ret='ret_10g', q=5):
    s = d[[col, ret]].dropna()
    if len(s) < 250:
        return None
    try:
        s['b'] = pd.qcut(s[col], q, labels=False, duplicates='drop')
    except Exception:
        return None
    g = s.groupby('b')[ret].agg(['mean', 'count'])
    if len(g) < 3:
        return None
    means = g['mean'].values
    mono = bool(np.all(np.diff(means) >= 0) or np.all(np.diff(means) <= 0))
    return {'spread': g['mean'].iloc[-1] - g['mean'].iloc[0], 'top': g['mean'].iloc[-1],
            'bot': g['mean'].iloc[0], 'mono': mono, 'n': int(g['count'].sum())}


def half_split(d, col, ret='ret_10g'):
    s = d[[col, ret]].dropna()
    if len(s) < 200:
        return None
    med = s[col].median()
    hi = s[s[col] > med][ret].mean(); lo = s[s[col] <= med][ret].mean()
    return {'hi': hi, 'lo': lo, 'spread': hi - lo, 'n': len(s)}


def badge_contribution_audit(conn):
    """Rozetleri bağımsız olay ve aynı-gün kontrol grubuyla ölçer."""
    q = """
        SELECT ss.id, ss.symbol, ss.scan_date, ss.scan_type,
               ss.f_liquidity_tier AS liquidity_tier, r.ret_10g
        FROM scan_signals ss
        JOIN signal_results r ON r.signal_id=ss.id
        WHERE COALESCE(ss.is_event_start, 1)=1
          AND r.ret_10g IS NOT NULL
          AND COALESCE(r.entry_status, 'filled_legacy') LIKE 'filled%'
    """
    x = pd.read_sql(q, conn)
    if x.empty:
        return ["## Altın / Platin / VIP rozet katkısı", "Ölçülebilir bağımsız olay yok."]
    badge = x[x['scan_type'].isin(BADGE_TYPES)].copy()
    control = x[~x['scan_type'].isin(BADGE_TYPES)].copy()
    # Aynı sembol-tarih birden çok taramaya yakalandıysa kontrolü tek hisse getirisine indir.
    control = control.groupby(
        ['symbol', 'scan_date', 'liquidity_tier'], dropna=False, as_index=False
    )['ret_10g'].mean()
    lines = [
        "## Altın / Platin / VIP → rozet katkı denetimi",
        "Her satır yalnız bağımsız olay başlangıcıdır. “Aynı-gün farkı”, rozetli hissenin "
        "10 seans getirisinden o gün diğer taramaların bulduğu hisselerin ortalamasını çıkarır.",
        "",
        "| Rozet | Bağımsız olay | 10 seans ort. | Pozitif oran | Aynı-gün eşleşme | Aynı-gün farkı | Yalnız rozet |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for scan_type in BADGE_TYPES:
        g = badge[badge['scan_type'] == scan_type].drop_duplicates(
            ['symbol', 'scan_date'], keep='first'
        )
        matched = []
        badge_only = 0
        other_keys = set(
            zip(
                x[~x['scan_type'].isin(BADGE_TYPES)]['symbol'],
                x[~x['scan_type'].isin(BADGE_TYPES)]['scan_date'],
            )
        )
        for _, row in g.iterrows():
            pool = control[
                (control['scan_date'] == row['scan_date'])
                & (control['symbol'] != row['symbol'])
            ]
            tier = row.get('liquidity_tier')
            if pd.notna(tier):
                same_tier = pool[pool['liquidity_tier'] == tier]
                if len(same_tier) >= 3:
                    pool = same_tier
            if not pool.empty:
                matched.append(float(row['ret_10g']) - float(pool['ret_10g'].mean()))
            if (row['symbol'], row['scan_date']) not in other_keys:
                badge_only += 1
        n = len(g)
        avg = float(g['ret_10g'].mean()) if n else float('nan')
        hit = float((g['ret_10g'] > 0).mean() * 100) if n else float('nan')
        matched_avg = float(np.mean(matched)) if matched else float('nan')
        lines.append(
            f"| {scan_type} | {n:,} | %{avg:+.2f} | %{hit:.1f} | "
            f"{len(matched):,} | %{matched_avg:+.2f} | {badge_only:,} |"
        )
    lines.extend([
        "",
        "Not: Bu eşleştirme nedensellik kanıtı değildir; fakat rozetlerin günlük tekrarlarla "
        "şişmeden ve aynı piyasa günü koşulunda bağımsız katkısını görünür kılar.",
    ])
    return lines


def main():
    conn = sqlite3.connect(DB)
    q = """SELECT ss.symbol, ss.scan_date, ss.scan_type,
                  ss.f_master_score AS ms, ss.f_52h_pos AS p52, ss.f_rsi AS rsi,
                  r.ret_5g, r.ret_10g, r.hit_10g
           FROM scan_signals ss
           JOIN signal_results r
             ON ss.symbol=r.symbol AND ss.scan_date=r.signal_date AND ss.scan_type=r.scan_type
           WHERE ss.f_master_score IS NOT NULL AND r.ret_10g IS NOT NULL
             AND COALESCE(ss.is_event_start, 1)=1
             AND COALESCE(r.entry_status, 'filled_legacy') LIKE 'filled%'"""
    d = pd.read_sql(q, conn)
    badge_lines = badge_contribution_audit(conn)
    conn.close()
    # getiri ölçeği: kesir mi yüzde mi → yüzdeye normalize
    scale = 100.0 if d['ret_10g'].abs().median() < 1.0 else 1.0
    for c in ['ret_5g', 'ret_10g']:
        d[c] = d[c] * scale

    reg = xu100_regime()
    if reg is not None:
        d['regime'] = d['scan_date'].map(reg)

    L = []
    L.append("# APP SKOR DENETİMİ — f_master_score (gerçek app rakamı, drift yok)")
    L.append(f"Join örneği: {len(d):,} satır (10g) · tarih {d['scan_date'].min()}→{d['scan_date'].max()} · "
             f"hisse {d['symbol'].nunique()} · taban 10g %{d['ret_10g'].mean():+.2f}")
    L.append("⚠ f_smart_money_score / f_ict_model / f_sentiment_score = VERİ YOK (hiç yazılmamış) → ölçülemez.")
    L.append("⚠ ret_20g henüz olgunlaşmadı (skorlar 3 Haz'dan beri yazılıyor) → 10g pencere.\n")

    # Genel güç
    L.append("## f_master_score → 10g getiri (quintile, düşük→yüksek skor)")
    s = d[['ms', 'ret_10g']].dropna().copy()
    try:
        s['b'] = pd.qcut(s['ms'], 5, labels=False, duplicates='drop')
        gg = s.groupby('b')['ret_10g'].agg(['mean', 'count'])
        for b, row in gg.iterrows():
            L.append(f"  Q{int(b)+1}: %{row['mean']:>+6.2f}  (n={int(row['count']):,})")
    except Exception:
        L.append("  (quintile üretilemedi)")
    r = quintile(d, 'ms')
    if r:
        L.append(f"→ spread (üst-alt): %{r['spread']:+.2f} · monoton: {'evet' if r['mono'] else 'HAYIR'} · n={r['n']:,}")

    # Karşılaştırma — aynı join'de p52/rsi (universe bulgusuyla tutarlı mı)
    L.append("\n## Aynı örnekte ham feature gücü (kıyas)")
    for c, nm in [('p52', '52H konum'), ('rsi', 'RSI')]:
        rr = quintile(d, c)
        if rr:
            L.append(f"- {nm}: spread %{rr['spread']:+.2f} (mono {'e' if rr['mono'] else 'h'}, n={rr['n']:,})")

    # REJİM AYRIMI
    if 'regime' in d.columns:
        dr = d.dropna(subset=['regime'])
        L.append("\n## REJİM AYRIMI — master_score üst-yarı vs alt-yarı (medyan böl)")
        L.append(f"{'rejim':7}{'n':>8}{'üst½':>9}{'alt½':>9}{'fark':>9}")
        robust = True; seen = 0
        for rn, g in dr.groupby('regime'):
            h = half_split(g, 'ms')
            if not h:
                L.append(f"{rn:7}{len(g):>8,}   (örnek yetersiz)"); continue
            seen += 1
            if h['spread'] <= 0:
                robust = False
            L.append(f"{rn:7}{h['n']:>8,}{h['hi']:>+9.2f}{h['lo']:>+9.2f}{h['spread']:>+9.2f}")
        L.append("")
        if seen >= 2:
            L.append("→ Her iki rejimde de fark POZİTİF ise master_score REJİM-DAYANIKLI;"
                     if robust else "→ Bir rejimde fark ≤0 → master_score o rejimde ÇALIŞMIYOR (boğa serabı riski).")

    L.append("\n## Okuma")
    L.append("- Pozitif + monoton spread = master_score gerçekten ayrıştırıyor; ≈0 = ölü.")
    L.append("- Bu yalnızca tarama-tetikli alt küme (seçim yanlılığı) + 10g + küçük örnek → İLK okuma.")
    L.append("- smart_money/ict/sentiment ölçmek için önce o kolonların yazımı düzeltilmeli (ayrı iş).")
    L.append("")
    L.extend(badge_lines)

    txt = "\n".join(L)
    open('app_score_report.md', 'w', encoding='utf-8').write(txt)
    print(txt)
    print("\n✅ Rapor → app_score_report.md")


if __name__ == '__main__':
    main()
