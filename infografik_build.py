#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""İnfografik BİRLEŞTİRİCİ (v3) — gerçek paneller + temiz Plotly grafik → tek PNG.
Üst: stat şeridi + FİYAT + HOOK · SOL: PARA AKIŞI pusulası + Görev 4 kartları ·
SAĞ: temiz mum grafiği (Plotly) + İvme/Denge (Plotly). chromium screenshot.
al/sat/hedef/stop YASAK — saf gözlem/eğitim."""
import os, sys, base64, re
import numpy as np
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import infographic as ig            # load, compute, gorev4
import clean_chart_plotly as cc     # build_fig, build_ivme_fig
import compass_panel as cp          # build_compass_html
# NOT: playwright (chromium) sadece PNG export için → render() içinde lazy import.
# In-app st.html yolu build_widget_html kullanır, chromium gerektirmez.

BASE = os.path.dirname(os.path.abspath(__file__))
BG = '#0a1019'; CARD = '#111a28'; CARD2 = '#0d1623'; LINE = '#1e2c40'
TXT = '#e6edf6'; MUT = '#8aa0bb'; UP = '#2ec177'; DN = '#f0556a'; INFO = '#4aa3ff'; GOLD = '#e0a72e'

# Özet ile DİKKAT arasına: gözlem/eğitim disclaimer'ı (büyük harf, 22 Haz 2026)
DISCLAIMER_PANEL = (
    f"<div style='background:{CARD};border:1px solid {LINE};border-radius:10px;padding:8px 11px;margin-bottom:7px;'>"
    f"<span style='font-size:11px;line-height:1.5;color:{MUT};letter-spacing:0.2px;'>"
    f"BU BİR GÖZLEM VE EĞİTİM İÇERİĞİDİR. ANALİZLER KESİNLİK İÇERMEZ, OLASILIK İÇERİR. "
    f"YATIRIM TAVSİYESİ DEĞİLDİR.</span></div>"
)

# Özet kartının altına minik uyarı paneli (22 Haz 2026)
NOTICE_BADGE = (
    f"<div style='background:{CARD2};border:1px solid {GOLD}44;border-radius:10px;"
    f"padding:8px 11px;margin-top:1px;'>"
    f"<span style='font-size:17px;line-height:1.55;color:{MUT};letter-spacing:0.2px;'>"
    f"<b style='color:{GOLD};'>DİKKAT.</b> BU GÖRSEL EĞİTİM AMAÇLIDIR. YAPAY ZEKA ÜRETİMİ DEĞİLDİR. "
    f"33000 SATIRLIK ALGORİTMAMIN ÇIKTISIDIR. "
    f"<b style='color:{INFO};'>#SMARTMONEYRADAR</b></span></div>"
)


def _fig_b64(fig):
    return base64.b64encode(fig.to_image(format='png', scale=2)).decode()


def _market_stats(ticker, df):
    """RS Gücü (vs XU100) + Beta + RVOL — XU100 parquet ile."""
    out = dict(rs=None, beta=None, rvol=None)
    try:
        v = df['Volume']; m = float(v.tail(20).mean())
        out['rvol'] = float(v.iloc[-1] / m) if m > 0 else None
    except Exception: pass
    try:
        idx = ig.load('XU100')
        if idx is not None:
            sc = df['Close']; ic = idx['Close']
            common = sc.index.intersection(ic.index)
            sc = sc.reindex(common); ic = ic.reindex(common)
            if len(common) >= 130:
                w = 126
                out['rs'] = float((sc.iloc[-1] / sc.iloc[-w-1]) / (ic.iloc[-1] / ic.iloc[-w-1]))
            if len(common) >= 60:
                sr = sc.pct_change().dropna(); ir = ic.pct_change().dropna()
                k = min(len(sr), len(ir), 252)
                sr = sr.iloc[-k:].values; ir = ir.iloc[-k:].values
                var = float(np.var(ir))
                if var > 0: out['beta'] = float(np.cov(sr, ir)[0, 1] / var)
    except Exception: pass
    # STP (sentetik eğilim = tipik fiyatın EMA6'sı) — fiyat üstünde/altında, kaç gün, kesti mi
    try:
        tp = (df['High'] + df['Low'] + df['Close']) / 3
        stp = tp.ewm(span=6, adjust=False).mean()
        st = (df['Close'].values - stp.values) >= 0     # True=üstünde
        above = bool(st[-1]); n = 1
        for i in range(len(st) - 2, -1, -1):
            if st[i] == above: n += 1
            else: break
        out['stp_above'] = above; out['stp_days'] = int(n); out['stp_crossed'] = (n == 1)
    except Exception: pass
    return out


def _statbox(d, ms):
    def cell(lbl, val, clr, sep):
        br = f"border-right:1px solid {LINE};" if sep else ""
        return (f"<div style='text-align:center;padding:2px 14px;{br}'>"
                f"<div style='font-size:10px;color:{MUT};letter-spacing:0.3px;'>{lbl}</div>"
                f"<div style='font-size:16px;font-weight:800;color:{clr};'>{val}</div></div>")
    rs = ms['rs']; rs_v = f"{rs:.2f}×" if rs else "—"; rs_c = UP if (rs or 1) >= 1 else DN
    mom = d['mom']; mom_v = f"%{abs(mom):.1f}" if mom is not None else "—"
    mom_c = UP if (mom or 0) >= 0 else DN; mom_a = '▲' if (mom or 0) >= 0 else '▼'
    rvol_v = f"{ms['rvol']:.1f}×" if ms['rvol'] else "—"
    beta_v = f"β {ms['beta']:.2f}" if ms['beta'] else "—"
    rsi_c = DN if d['rsi'] >= 70 else (UP if d['rsi'] <= 30 else TXT)
    sa = ms.get('stp_above'); sd = ms.get('stp_days'); sx = ms.get('stp_crossed')
    if sa is None:
        stp_v = '—'; stp_c = TXT
    else:
        ar = '↑' if sa else '↓'; stp_c = UP if sa else DN
        stp_v = f"{ar} kesti" if sx else f"{ar} {sd}g"
    items = [('RS GÜCÜ', rs_v, rs_c), ('MOMENTUM', f'{mom_a} {mom_v}', mom_c),
             ('RSI', f"{d['rsi']:.0f}", rsi_c), ('HACİM', rvol_v, TXT), ('BETA', beta_v, TXT),
             ('STP', stp_v, stp_c)]
    cells = ''.join(cell(l, v, c, i < len(items) - 1) for i, (l, v, c) in enumerate(items))
    return (f"<div style='display:flex;align-items:center;border:1px solid {LINE};border-radius:10px;"
            f"background:{CARD2};padding:4px 2px;'>{cells}</div>")


def _card(title, body):
    return (f"<div style='background:{CARD};border:1px solid {LINE};border-radius:10px;"
            f"padding:9px 12px;margin-bottom:7px;'>"
            f"<div style='font-size:12px;font-weight:700;color:{MUT};margin-bottom:3px;'>{title}</div>"
            f"<div style='font-size:15px;line-height:1.5;color:{TXT};'>{body}</div></div>")


def _card_warn(title, body):
    """UYARI kartı — kırmızı kenar+başlık (çelişki/risk varsa)."""
    return (f"<div style='background:{CARD};border:1px solid {DN}66;border-radius:10px;"
            f"padding:9px 12px;margin-bottom:7px;'>"
            f"<div style='font-size:12px;font-weight:700;color:{DN};margin-bottom:3px;'>{title}</div>"
            f"<div style='font-size:15px;line-height:1.5;color:{TXT};'>{body}</div></div>")


def _signal_box(df, d):
    """GENEL ÖZET üst doğrulama bandı — standalone port (app _render_genel_ozet_panel).
    Verdict (YUKARI/AŞAĞI/KARARSIZ + N/4) · LONG çubuğu · HACİM/OBV/YAPI/RSI ok kolonu ·
    5 mumluk mini şerit · 5g/20g/50g/200g trafik ışıkları. AL/SAT dili YOK."""
    NEU = '#64748b'; LBL = '#94a3b8'
    # ── 4 bağımsız sinyal ─────────────────────────────────────────────
    try:
        _r5 = df.tail(5)
        _d5 = float(((_r5['Close'] - _r5['Open']) * _r5['Volume']).sum())
    except Exception:
        _d5 = 0.0
    sig_hacim = 1 if _d5 > 0 else (-1 if _d5 < 0 else 0)
    _ofy = float(d.get('obv_force', 0.0) or 0.0)
    sig_obv = 1 if _ofy > 0.1 else (-1 if _ofy < -0.1 else 0)
    sig_yapi = 0
    try:
        _h = df['High'].astype(float).values; _l = df['Low'].astype(float).values
        if _h[-1] > _h[-2] > _h[-3] and _l[-1] > _l[-2] > _l[-3]:
            sig_yapi = 1
    except Exception:
        pass
    _rv = float(d.get('rsi', 50.0) or 50.0)
    sig_rsi = 1 if _rv <= 60 else (-1 if _rv > 70 else 0)
    up = sum(1 for s in (sig_hacim, sig_obv, sig_yapi, sig_rsi) if s > 0)
    dn = sum(1 for s in (sig_hacim, sig_obv, sig_yapi, sig_rsi) if s < 0)
    if   up == 4:             net, nc = "YUKARI ★", "#22c55e"
    elif dn == 4:             net, nc = "AŞAĞI ★", "#dc2626"
    elif up >= 3:             net, nc = "YUKARI", "#4ade80"
    elif dn >= 3:             net, nc = "AŞAĞI", "#f87171"
    elif up == 2 and dn == 2: net, nc = "ÇELİŞKİLİ", "#fb923c"
    elif up >= 2 and dn < 2:  net, nc = "HAFİF YUKARI", "#86efac"
    elif dn >= 2 and up < 2:  net, nc = "HAFİF AŞAĞI", "#fca5a5"
    else:                     net, nc = "KARARSIZ", "#fbbf24"
    dom = max(up, dn)

    def _cell(lbl, sig):
        if sig > 0:   ar, clr = "▲", UP
        elif sig < 0: ar, clr = "▼", DN
        else:         ar, clr = "→", NEU
        return (f"<div style='padding:3px 8px;background:{clr}1a;border:1px solid {clr}4d;"
                f"border-radius:5px;display:flex;align-items:center;justify-content:space-between;"
                f"gap:8px;line-height:1.1;min-width:74px;'>"
                f"<span style='font-size:9px;color:{LBL};font-weight:700;letter-spacing:0.04em;'>{lbl}</span>"
                f"<span style='font-size:13px;color:{clr};font-weight:900;'>{ar}</span></div>")
    arrow_stack = ("<div style='display:flex;flex-direction:column;gap:3px;flex:0 0 auto;'>"
                   + _cell("HACİM", sig_hacim) + _cell("OBV", sig_obv)
                   + _cell("YAPI", sig_yapi) + _cell("RSI", sig_rsi) + "</div>")

    # ── 5 mumluk mini şerit (son 5 OHLC) ──────────────────────────────
    mini = ""
    try:
        _d5f = df.tail(5)
        hs = _d5f['High'].astype(float).values; ls = _d5f['Low'].astype(float).values
        oo = _d5f['Open'].astype(float).values; cc = _d5f['Close'].astype(float).values
        pmax = float(hs.max()); rng = max(pmax - float(ls.min()), 1e-9)
        W, H = 110, 80; cw = W / 5; bw = cw * 0.42; parts = []
        for i in range(5):
            o, h, l, c = float(oo[i]), float(hs[i]), float(ls[i]), float(cc[i])
            xc = i * cw + cw / 2
            yh = (pmax - h) / rng * H; yl = (pmax - l) / rng * H
            yo = (pmax - o) / rng * H; yc = (pmax - c) / rng * H
            _cc = UP if c >= o else DN
            parts.append(f"<line x1='{xc:.1f}' y1='{yh:.1f}' x2='{xc:.1f}' y2='{yl:.1f}' stroke='{_cc}' stroke-width='1.4'/>")
            bt = min(yo, yc); bh = max(abs(yc - yo), 1.5)
            parts.append(f"<rect x='{xc - bw/2:.1f}' y='{bt:.1f}' width='{bw:.1f}' height='{bh:.1f}' fill='{_cc}'/>")
        mini = (f"<svg width='100%' height='{H}' viewBox='0 0 {W} {H}' preserveAspectRatio='xMidYMid meet' "
                f"style='max-width:110px;'>" + "".join(parts) + "</svg>")
    except Exception:
        pass

    # ── 5g/20g/50g/200g trafik ışıkları ───────────────────────────────
    tf = ""
    try:
        cl = df['Close'].astype(float); cn = float(cl.iloc[-1]); tfs = []
        if len(cl) >= 6:
            p5 = float(cl.iloc[-6]); tfs.append(("5g", 1 if cn > p5 else (-1 if cn < p5 else 0)))
        else: tfs.append(("5g", 0))
        for _lbl, _p in (("20g", 20), ("50g", 50), ("200g", 200)):
            if len(cl) >= _p:
                _sv = float(cl.rolling(_p).mean().iloc[-1]); tfs.append((_lbl, 1 if cn > _sv else -1))
            else: tfs.append((_lbl, 0))
        cells = []
        for lbl, dd in tfs:
            tc = UP if dd > 0 else (DN if dd < 0 else NEU)
            cells.append(f"<div style='display:flex;align-items:center;gap:5px;padding:1px 4px;line-height:1.1;"
                         f"justify-content:space-between;min-width:48px;'>"
                         f"<span style='font-size:9px;color:{LBL};font-weight:700;'>{lbl}</span>"
                         f"<span style='display:inline-block;width:9px;height:9px;border-radius:50%;"
                         f"background:{tc};box-shadow:0 0 4px {tc}99;'></span></div>")
        tf = ("<div style='display:flex;flex-direction:column;gap:3px;flex:0 0 auto;justify-content:center;'>"
              + "".join(cells) + "</div>")
    except Exception:
        pass

    # ── LONG çubuğu (infografik genel-sağlık kompoziti) ───────────────
    hv = int(d.get('health', 50) or 50)
    hc = UP if hv >= 55 else (GOLD if hv >= 40 else DN)
    longbar = (f"<span style='display:inline-block;width:46px;height:5px;background:#1e293b;border-radius:3px;"
               f"position:relative;margin-right:5px;vertical-align:middle;'>"
               f"<span style='position:absolute;left:0;top:0;width:{min(hv,100)}%;height:100%;background:{hc};"
               f"border-radius:3px;box-shadow:0 0 4px {hc};'></span></span>"
               f"<span style='color:{hc};font-size:12px;'>{hv}/100</span>")

    return (f"<div style='background:rgba(56,189,248,0.07);border:1px solid {LINE};border-radius:10px;"
            f"padding:9px 11px;'>"
            f"<div style='display:flex;align-items:center;flex-wrap:wrap;row-gap:4px;"
            f"font-family:\"JetBrains Mono\",ui-monospace,Consolas,monospace;font-weight:800;'>"
            f"<span style='color:{nc};font-size:14px;white-space:nowrap;'>{net} "
            f"<span style='opacity:0.6;font-size:10px;font-weight:600;'>{dom}/4</span></span>"
            f"<span style='display:inline-flex;align-items:center;white-space:nowrap;margin-left:auto;'>"
            f"<span style='color:{NEU};padding:0 8px;font-weight:400;font-size:12px;'>|</span>"
            f"<span style='color:{NEU};font-size:10px;font-weight:600;letter-spacing:0.04em;margin-right:4px;'>LONG</span>{longbar}</span>"
            f"</div>"
            f"<div style='display:flex;gap:8px;margin-top:8px;align-items:center;'>{arrow_stack}"
            f"<div style='flex:1;display:flex;justify-content:center;align-items:center;'>{mini}</div>{tf}</div>"
            f"</div>")


def _ind_wrap(title, inner):
    """İndikatör kabı — başlık + içerik (img veya inline svg)."""
    return (f"<div style='background:{CARD};border:1px solid {LINE};border-radius:10px;padding:8px;margin-bottom:8px;'>"
            f"<div style='font-size:12px;font-weight:700;color:{MUT};margin-bottom:5px;'>{title}</div>{inner}</div>")


def _ind_block_b64(title, fig):
    return _ind_wrap(title, f"<img src='data:image/png;base64,{_fig_b64(fig)}' style='width:100%;'/>") if fig is not None else ''


def _ind_block_svg(title, fig):
    return _ind_wrap(title, _fig_svg(fig)) if fig is not None else ''


# Görünen başlık etiketleri — sözlük ANAHTARLARI ('GENEL' vs) SABİT kalır (g[k] araması +
# 'if k in g' filtresi bunlara bağlı); sadece kartta GÖRÜNEN metin buradan değişir → kırılmaz.
_HDR = {'GENEL': 'GENEL DURUM', 'TEKNİK': 'TEKNİK ÖZET', 'AKILLI PARA': 'AKILLI PARA İZLERİ'}


def build_html(ticker):
    df = ig.load(ticker)
    if df is None or len(df) < 60: return None
    d = ig.compute(ticker, df); g = ig.gorev4(d)
    tk = d['ticker']
    chart = _fig_b64(cc.build_fig(ticker)); ivme = _fig_b64(cc.build_ivme_fig(ticker))
    harsi_block = _ind_block_b64('Momentum göstergesi', cc.build_harsi_fig(ticker))
    compass = cp.build_compass_html(ticker) or ""
    chg_clr = UP if d['chg'] >= 0 else DN; arrow = '▲' if d['chg'] >= 0 else '▼'
    stats = _statbox(d, _market_stats(ticker, df))
    cards = "".join(_card(_HDR.get(k, k), g[k]) for k in ('GENEL', 'AKILLI PARA', 'SONUÇ') if k in g)
    if 'UYARI' in g: cards += _card_warn('⚠ UYARI', g['UYARI'])
    sbox = _signal_box(df, d)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',Arial,sans-serif;}}
body{{background:{BG};}}
img{{display:block;border-radius:8px;}}
</style></head><body>
<div id="infografik" style="width:980px;background:{BG};padding:16px;color:{TXT};">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px;">
    <div style="display:flex;align-items:center;gap:10px;">
      <div><div style="font-size:24px;font-weight:800;">{tk} · Teknik Görünüm</div>
      <div style="font-size:11px;color:{MUT};letter-spacing:0.5px;">SMART MONEY RADAR · ALGORİTMİK ÖZET</div></div>
    </div>
    {stats}
    <div style="background:#0c2238;border:1px solid {LINE};border-radius:10px;padding:11px 22px;text-align:right;display:flex;flex-direction:column;justify-content:center;">
      <div style="font-size:34px;font-weight:800;line-height:1.04;">{d['last']:.2f}</div>
      <div style="font-size:20px;font-weight:700;color:{chg_clr};margin-top:2px;">{arrow} %{abs(d['chg']):.2f}</div>
    </div>
  </div>
  <div style="background:{INFO}1a;border:1px solid {INFO}55;border-radius:8px;padding:8px 14px;margin-bottom:12px;font-weight:700;color:{INFO};font-size:14px;">{g['hook']}</div>
  <div style="display:grid;grid-template-columns:330px 1fr;gap:12px;align-items:start;">
    <div>{compass}<div style="height:8px;"></div>{sbox}<div style="height:8px;"></div>{cards}</div>
    <div>
      <div style="background:{CARD};border:1px solid {LINE};border-radius:10px;padding:8px;margin-bottom:8px;">
        <div style="font-size:12px;font-weight:700;color:{MUT};margin-bottom:5px;">Teknik yapı · mumlar + SMA50/EMA144/SMA100/SMA200 + POC + VWAP</div>
        <img src="data:image/png;base64,{chart}" style="width:100%;"/>
      </div>
      {harsi_block}
      <div style="background:{CARD};border:1px solid {LINE};border-radius:10px;padding:8px;margin-bottom:8px;">
        <div style="font-size:12px;font-weight:700;color:{MUT};margin-bottom:5px;">Para Akış İvmesi & Fiyat Dengesi</div>
        <img src="data:image/png;base64,{ivme}" style="width:100%;"/>
      </div>
      <div style="display:flex;gap:8px;align-items:stretch;">
        <div style="flex:1;">{DISCLAIMER_PANEL}</div>
        <div style="flex:1.5;">{NOTICE_BADGE}</div>
      </div>
    </div>
  </div>
  <div style="font-size:10.5px;color:{MUT};margin-top:10px;text-align:center;">Eğitim amaçlıdır. Yatırım tavsiyesi değildir. http://smartmoneyradar.app</div>
</div>
</body></html>"""


def _fig_svg(fig):
    s = fig.to_image(format='svg').decode('utf-8')
    i = s.find('<svg')
    s = s[i:] if i >= 0 else s
    # responsive: kök <svg> width/height → %100 (viewBox korunur → kolona orantılı sığar)
    s = re.sub(r'(<svg\b[^>]*?)\swidth="\d+(?:\.\d+)?"\s+height="\d+(?:\.\d+)?"',
               r'\1 width="100%" height="auto"', s, count=1)
    return s


def build_widget_html(ticker):
    """show_widget için: Plotly figürleri inline SVG (base64 PNG değil), sadece iç div."""
    df = ig.load(ticker)
    if df is None or len(df) < 60: return None
    d = ig.compute(ticker, df); g = ig.gorev4(d)
    tk = d['ticker']
    # NOT: in-app'te inline SVG iframe'de oranını koruyamıyor (uzun render) → base64 PNG kullan.
    chart = _fig_b64(cc.build_fig(ticker)); ivme = _fig_b64(cc.build_ivme_fig(ticker))
    harsi_block = _ind_block_b64('Momentum göstergesi', cc.build_harsi_fig(ticker))
    compass = cp.build_compass_html(ticker) or ""
    chg_clr = UP if d['chg'] >= 0 else DN; arrow = '▲' if d['chg'] >= 0 else '▼'
    stats = _statbox(d, _market_stats(ticker, df))
    cards = "".join(_card(_HDR.get(k, k), g[k]) for k in ('GENEL', 'AKILLI PARA', 'SONUÇ') if k in g)
    if 'UYARI' in g: cards += _card_warn('⚠ UYARI', g['UYARI'])
    sbox = _signal_box(df, d)
    return f"""<div style="background:{BG};padding:16px;color:{TXT};font-family:'Segoe UI',Arial,sans-serif;border-radius:12px;">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px;">
    <div style="display:flex;align-items:center;gap:10px;">
      <div><div style="font-size:24px;font-weight:800;">{tk} · Teknik Görünüm</div>
      <div style="font-size:11px;color:{MUT};">SMART MONEY RADAR · ALGORİTMİK ÖZET</div></div>
    </div>
    {stats}
    <div style="background:#0c2238;border:1px solid {LINE};border-radius:10px;padding:11px 22px;text-align:right;display:flex;flex-direction:column;justify-content:center;">
      <div style="font-size:34px;font-weight:800;line-height:1.04;">{d['last']:.2f}</div>
      <div style="font-size:20px;font-weight:700;color:{chg_clr};margin-top:2px;">{arrow} %{abs(d['chg']):.2f}</div>
    </div>
  </div>
  <div style="background:{INFO}1a;border:1px solid {INFO}55;border-radius:8px;padding:8px 14px;margin-bottom:12px;font-weight:700;color:{INFO};font-size:14px;">{g['hook']}</div>
  <div style="display:grid;grid-template-columns:330px 1fr;gap:12px;align-items:start;">
    <div>{compass}<div style="height:8px;"></div>{sbox}<div style="height:8px;"></div>{cards}</div>
    <div>
      <div style="background:{CARD};border:1px solid {LINE};border-radius:10px;padding:8px;margin-bottom:8px;">
        <div style="font-size:12px;font-weight:700;color:{MUT};margin-bottom:5px;">Teknik yapı · mumlar + SMA50/EMA144/SMA100/SMA200 + POC + VWAP</div><img src="data:image/png;base64,{chart}" style="width:100%;display:block;border-radius:8px;"/>
      </div>
      {harsi_block}
      <div style="background:{CARD};border:1px solid {LINE};border-radius:10px;padding:8px;margin-bottom:8px;">
        <div style="font-size:12px;font-weight:700;color:{MUT};margin-bottom:5px;">Para Akış İvmesi & Fiyat Dengesi</div><img src="data:image/png;base64,{ivme}" style="width:100%;display:block;border-radius:8px;"/>
      </div>
      <div style="display:flex;gap:8px;align-items:stretch;">
        <div style="flex:1;">{DISCLAIMER_PANEL}</div>
        <div style="flex:1.5;">{NOTICE_BADGE}</div>
      </div>
    </div>
  </div>
  <div style="font-size:10.5px;color:{MUT};margin-top:10px;text-align:center;">Eğitim amaçlıdır. Yatırım tavsiyesi değildir. http://smartmoneyradar.app</div>
</div>"""


def render(ticker, out=None):
    from playwright.sync_api import sync_playwright   # lazy — chromium sadece PNG için
    html = build_html(ticker)
    if html is None: return None
    out = out or os.path.join(BASE, f'infografik_{ticker}.png')
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 1020, 'height': 820}, device_scale_factor=2)
        pg.set_content(html, wait_until='networkidle')
        pg.locator('#infografik').screenshot(path=out)
        b.close()
    return out


def render_bytes(ticker):
    """render() gibi chromium ile PNG üretir ama dosyaya değil BELLEĞE alır → bayt döndürür.
    Bot/SMR-ELITE için (dosya çakışması yok). Veri/chromium yoksa None."""
    from playwright.sync_api import sync_playwright   # lazy — chromium sadece PNG için
    html = build_html(ticker)
    if html is None: return None
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 1020, 'height': 820}, device_scale_factor=2)
        pg.set_content(html, wait_until='networkidle')
        png = pg.locator('#infografik').screenshot()
        b.close()
    return png


if __name__ == '__main__':
    tk = sys.argv[1] if len(sys.argv) > 1 else 'SAHOL'
    out = render(tk)
    print(f'✅ {out}' if out else 'veri yok')
