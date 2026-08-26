#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PARA AKIŞI pusulası — app _render_genel_ozet_panel Force Compass'ı BİREBİR standalone.
OBV durumu (get_obv_divergence_status mantığı) → _y_force · CMF dual-window → _x_force · Δ5g.
3 şekil (OBV ▲/▼, Para Akışı ◆, Δ5g ■) + bileşke ok + BOĞA/AYI/SAT/AL. HTML snippet üretir."""
import os, sys
import numpy as np, pandas as pd
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
BASE = os.path.dirname(os.path.abspath(__file__)); VER = os.path.join(BASE, 'veriler')
UP = '#2ec177'; DN = '#f0556a'; NEU = '#64748b'; LBL = '#94a3b8'; MUT = '#8aa0bb'


def split_adj(df):
    df = df.copy().sort_index(); pc = ['Open', 'High', 'Low', 'Close']
    for _ in range(10):
        cl = df['Close'].ffill().values; f = False
        for i in range(1, len(cl)):
            if cl[i-1] <= 0 or cl[i] <= 0: continue
            r = cl[i-1] / cl[i]
            if r >= 1.20:
                for col in pc: df.iloc[:i, df.columns.get_loc(col)] = df.iloc[:i][col].values / r
                f = True; break
        if not f: break
    return df


def load(ticker):
    ct = ticker if ticker.endswith('.IS') else f'{ticker}.IS'
    for c in (f'{VER}/{ct}_1d.parquet', f'{VER}/{ticker}_1d.parquet'):
        if os.path.exists(c): return split_adj(pd.read_parquet(c))
    return None


def compute_cmf(df, period=20):
    try:
        vol = df['Volume']; hl = df['High'] - df['Low']
        mfm = np.where(hl > 0, ((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / hl, 0.0)
        mfv = pd.Series(mfm, index=df.index) * vol
        vs = float(vol.tail(period).sum())
        return float(mfv.tail(period).sum() / vs) if vs > 0 else 0.0
    except Exception:
        return 0.0


def obv_status_title(df):
    """get_obv_divergence_status'ın başlık üretimi — BİREBİR port."""
    if df is None or len(df) < 30: return "Veri Yok"
    change = df['Close'].diff(); direction = np.sign(change).fillna(0)
    obv = (direction * df['Volume']).cumsum(); obv_sma = obv.rolling(20).mean()
    cmf_20 = compute_cmf(df, 20)
    cmf_strong_neg = cmf_20 < -0.05
    p_now = float(df['Close'].iloc[-1]); p_5 = float(df['Close'].iloc[-6])
    obv_now = float(obv.iloc[-1]); obv_5 = float(obv.iloc[-6]); obv_14 = float(obv.iloc[-15])
    obv_sma_now = float(obv_sma.iloc[-1])
    obv_5g_up = obv_now > obv_5; obv_14g_up = obv_now > obv_14
    price_5g_up = p_now > p_5; is_obv_strong = obv_now > obv_sma_now
    if obv_5g_up and not obv_14g_up: return "🔄 OBV KAFA ÇEVİRİYOR (Toparlanma)"
    if not obv_5g_up and obv_14g_up: return "🔄 OBV KAFA ÇEVİRİYOR (Zayıflama)"
    if not price_5g_up and obv_5g_up and obv_14g_up:
        if is_obv_strong:
            return "⚠️ ŞÜPHELİ GİRİŞ" if cmf_strong_neg else "🔥 GÜÇLÜ GİZLİ GİRİŞ"
        return "👀 Olası Toplama (Zayıf)"
    if price_5g_up and not obv_5g_up and not obv_14g_up: return "⚠️ GİZLİ ÇIKIŞ (Dağıtım)"
    if is_obv_strong and obv_5g_up and obv_14g_up:
        if p_now < float(df['Close'].iloc[-2]):
            return "⚠️ SAHTE GÜÇ" if cmf_strong_neg else "🛡️ DÜŞÜŞE DİRENÇ (Kurumsal Emilim)"
        return "⚠️ ZAYIF TEYİT" if cmf_strong_neg else "✅ SAĞLIKLI TREND (Hacim Onaylı)"
    return "⚖️ ZAYIF İVME (Hacimsiz Bölge)"


def _y_force_from(title):
    # NOT: app'te .lower()+Türkçe-anahtar eşleşmesi Türkçe İ casing'i (U+0307) yüzünden
    # bozuk → OBV ekseni çoğu zaman 0'a düşüyor. Burada TAM başlık eşleşmesi (robust).
    t = title or ''
    if t in ("✅ SAĞLIKLI TREND (Hacim Onaylı)", "🔥 GÜÇLÜ GİZLİ GİRİŞ"): return 1.0
    if t == "🛡️ DÜŞÜŞE DİRENÇ (Kurumsal Emilim)": return 0.6
    if t == "👀 Olası Toplama (Zayıf)": return 0.5
    if t == "🔄 OBV KAFA ÇEVİRİYOR (Toparlanma)": return 0.25
    if t == "🔄 OBV KAFA ÇEVİRİYOR (Zayıflama)": return -0.25
    if t == "⚠️ GİZLİ ÇIKIŞ (Dağıtım)": return -1.0
    return 0.0


def forces(df):
    title = obv_status_title(df)
    y = _y_force_from(title)
    cmf20 = compute_cmf(df, 20); cmf5 = compute_cmf(df, 5)
    r5 = df.tail(5); d5 = float(((r5['Close'] - r5['Open']) * r5['Volume']).sum())
    THR = 0.05
    c5p, c5n = cmf5 > THR, cmf5 < -THR; c20p, c20n = cmf20 > THR, cmf20 < -THR
    if c5p and c20p: st = "strong_pos"
    elif c5n and c20n: st = "strong_neg"
    elif c5p and c20n: st = "turning_up"
    elif c5n and c20p: st = "turning_down"
    elif c20p: st = "pos"
    elif c20n: st = "neg"
    else: st = "neutral"
    fmap = {"strong_pos": 1.0, "pos": 0.7, "turning_up": 0.3, "neutral": 0.0,
            "turning_down": -0.3, "neg": -0.7, "strong_neg": -1.0}
    x_cmf = fmap[st]; x_d5 = 1.0 if d5 > 0 else (-1.0 if d5 < 0 else 0.0)
    x = (x_cmf + x_d5) / 2.0
    c = df['Close'].astype(float); h = df['High'].astype(float)
    l = df['Low'].astype(float); v = df['Volume'].astype(float)
    rng = float(h.iloc[-1] - l.iloc[-1])
    close_loc = float((c.iloc[-1] - l.iloc[-1]) / rng) if rng > 0 else 0.5
    vol20 = float(v.tail(20).mean())
    vol_mult = float(v.iloc[-1] / vol20) if vol20 > 0 else 0.0
    ret1 = float((c.iloc[-1] / c.iloc[-2] - 1.0) * 100.0)
    return dict(title=title, y=y, x=x, cmf_state=st, cmf_force=fmap[st], d5=d5,
                cmf5=cmf5, cmf20=cmf20, close_loc=close_loc,
                pressure=(close_loc * 2.0 - 1.0) * 100.0,
                vol_mult=vol_mult, ret1=ret1)


def _dot_color(s): return UP if s > 0.1 else (DN if s < -0.1 else NEU)


def build_compass_svg(f, scale=2.0):
    cx, cy, rmax = 50, 52, 32
    x_end = cx + f['x'] * rmax; y_end = cy - f['y'] * rmax
    def safe(s, mn=0.35):
        s = float(s)
        return (mn if s >= 0 else -mn) if abs(s) < mn else max(-1.0, min(1.0, s))
    obvd = safe(f['y']); cmfd = safe(f['cmf_force']); d5d = safe(1.0 if f['d5'] > 0 else (-1.0 if f['d5'] < 0 else 0.0))
    rdot = rmax * 0.78; sh = 5
    ox, oy = cx, cy - obvd * rdot
    mx, my = cx + cmfd * rdot * 0.707, cy - cmfd * rdot * 0.707
    dx, dy = cx - d5d * rdot * 0.707, cy - d5d * rdot * 0.707
    oc, mc, dc = _dot_color(f['y']), _dot_color(f['cmf_force']), _dot_color(f['d5'])
    if f['y'] >= 0:
        obv_sh = f"<polygon points='{ox},{oy-sh} {ox-sh*0.866:.1f},{oy+sh*0.5:.1f} {ox+sh*0.866:.1f},{oy+sh*0.5:.1f}' fill='{oc}'/>"
    else:
        obv_sh = f"<polygon points='{ox},{oy+sh} {ox-sh*0.866:.1f},{oy-sh*0.5:.1f} {ox+sh*0.866:.1f},{oy-sh*0.5:.1f}' fill='{oc}'/>"
    cmf_sh = f"<polygon points='{mx:.1f},{my-sh:.1f} {mx+sh:.1f},{my:.1f} {mx:.1f},{my+sh:.1f} {mx-sh:.1f},{my:.1f}' fill='{mc}'/>"
    d5_sh = f"<rect x='{dx-sh*0.85:.1f}' y='{dy-sh*0.85:.1f}' width='{sh*1.7:.1f}' height='{sh*1.7:.1f}' fill='{dc}'/>"
    net = f['y'] * 1.2 + f['x'] * 0.8
    arc = UP if net > 0.35 else (DN if net < -0.35 else '#f59e0b')
    W, H = int(110 * scale), int(100 * scale)
    return (
        f"<svg width='{W}' height='{H}' viewBox='0 0 110 100'>"
        f"<line x1='14' y1='{cy}' x2='86' y2='{cy}' stroke='{LBL}' stroke-width='1.0'/>"
        f"<line x1='{cx}' y1='17' x2='{cx}' y2='87' stroke='{LBL}' stroke-width='1.0'/>"
        f"<circle cx='{cx}' cy='{cy}' r='32' fill='none' stroke='{NEU}' stroke-width='0.9' stroke-dasharray='1.5,2'/>"
        f"<line x1='{cx}' y1='{cy}' x2='{x_end:.1f}' y2='{y_end:.1f}' stroke='{arc}' stroke-width='2.2' stroke-linecap='round' opacity='0.55'/>"
        f"{obv_sh}{cmf_sh}{d5_sh}"
        f"<text x='50' y='12' text-anchor='middle' font-size='8.5' fill='{LBL}' font-weight='700'>BOĞA</text>"
        f"<text x='50' y='97' text-anchor='middle' font-size='8.5' fill='{LBL}' font-weight='700'>AYI</text>"
        f"<text x='3' y='55' text-anchor='start' font-size='8.5' fill='{LBL}' font-weight='700'>SAT</text>"
        f"<text x='107' y='55' text-anchor='end' font-size='8.5' fill='{LBL}' font-weight='700'>AL</text>"
        f"</svg>"
    )


def build_signals_block(f):
    def mini_obv(clr, ispos):
        pts = "7,11 1.5,4 12.5,4" if ispos is False else "7,3 1.5,11 12.5,11"
        return f"<svg width='14' height='14' viewBox='0 0 14 14'><polygon points='{pts}' fill='{clr}'/></svg>"
    mini_cmf = lambda clr: f"<svg width='14' height='14' viewBox='0 0 14 14'><polygon points='7,1 13,7 7,13 1,7' fill='{clr}'/></svg>"
    mini_d5 = lambda clr: f"<svg width='14' height='14' viewBox='0 0 14 14'><rect x='2' y='2' width='10' height='10' fill='{clr}'/></svg>"
    y = f['y']
    obv_pos = True if y > 0.3 else (False if y < -0.3 else None)
    obv_short = "güçlü" if y > 0.6 else ("zayıflıyor" if y > 0 else ("nötr" if y == 0 else ("zayıf" if y > -0.6 else "neg.")))
    cmf_lbl = {"strong_pos": ("güçlü", True), "pos": ("poz.", True), "turning_up": ("dönüyor ↗", True),
               "neutral": ("nötr", None), "turning_down": ("zayıflıyor ↘", False), "neg": ("neg.", False),
               "strong_neg": ("güçlü neg.", False)}
    cmf_short, cmf_pos = cmf_lbl.get(f['cmf_state'], ("—", None))
    d5_pos = True if f['d5'] > 0 else (False if f['d5'] < 0 else None)
    d5_short = "poz." if f['d5'] > 0 else ("neg." if f['d5'] < 0 else "—")
    oc, mc, dc = _dot_color(y), _dot_color(f['cmf_force']), _dot_color(f['d5'])
    def row(lbl, val, ispos, icon):
        sc = UP if ispos else (DN if ispos is False else NEU)
        return (f"<div style='display:flex;align-items:center;gap:6px;line-height:1.5;white-space:nowrap;'>"
                f"<span style='display:inline-flex;width:14px;height:14px;flex-shrink:0;'>{icon}</span>"
                f"<span style='font-size:0.72rem;color:{MUT};font-weight:600;min-width:70px;'>{lbl}</span>"
                f"<span style='font-size:0.74rem;color:{sc};font-weight:700;'>{val}</span></div>")
    return ("<div style='display:flex;flex-direction:column;gap:5px;justify-content:center;padding-left:18px;'>"
            + row("OBV", obv_short, obv_pos, mini_obv(oc, obv_pos))
            + row("Para Akışı", cmf_short, cmf_pos, mini_cmf(mc))
            + row("Δ 5g", d5_short, d5_pos, mini_d5(dc))
            + "</div>")


def build_compass_html(ticker):
    df = load(ticker)
    if df is None or len(df) < 30: return None
    f = forces(df)

    pressure = float(f['pressure'])
    pressure_txt = (f"Alıcı +%{abs(pressure):.0f}" if pressure >= 0
                    else f"Satıcı -%{abs(pressure):.0f}")
    pressure_clr = UP if pressure >= 0 else DN
    # Kapanış Mumu — fiyat günü, o günkü aralığın neresinde kapattı? (5 bölge + %)
    close_pct = float(f['close_loc']) * 100.0
    if close_pct >= 75:   _close_word = "tepeye yakın"
    elif close_pct >= 60: _close_word = "üst-orta"
    elif close_pct >= 40: _close_word = "orta"
    elif close_pct >= 25: _close_word = "alt-orta"
    else:                 _close_word = "dibe yakın"
    close_txt = f"{_close_word} (%{close_pct:.0f})"
    close_clr = UP if close_pct >= 75 else (DN if close_pct <= 25 else NEU)
    # İşlem Hacmi — bugünkü hacim, 20g ortalamanın kaç katı? (kıyas kelimesi + kat)
    vm = float(f['vol_mult'])
    _vm_word = ("normalin üstünde" if vm >= 1.2 else
                "normalin altında" if vm < 0.8 else "normal")
    vm_txt = f"{_vm_word} ({vm:.2f}×)"
    vm_clr = UP if vm >= 1.2 else (DN if vm < 0.8 else NEU)
    cmf5 = float(f['cmf5']); cmf20 = float(f['cmf20'])
    cmf5_clr = UP if cmf5 > 0.05 else (DN if cmf5 < -0.05 else NEU)
    orta_txt = "pozitif" if cmf20 > 0.05 else ("negatif" if cmf20 < -0.05 else "dengede")
    orta_clr = UP if cmf20 > 0.05 else (DN if cmf20 < -0.05 else NEU)
    y = float(f['y'])
    obv_txt = ("birikim" if y > 0.6 else "toparlanıyor" if y > 0.1 else
               "dağıtım" if y < -0.6 else "zayıflıyor" if y < -0.1 else "yönsüz")
    obv_clr = UP if y > 0.1 else (DN if y < -0.1 else NEU)

    # ── YAPISAL 5-20 gün özeti (eski "durum" rozeti yerine — 7 Ağu 2026) ──
    # Vademiz 3-21 gün: kısa vade = 5g para akışı, yapısal = 20g. İki cümle + harman.
    if cmf5 > 0.05:
        y5_txt = f"5 günde para girişi güçlü ({cmf5 * 100:+.1f}%)."; y5_clr = UP
    elif cmf5 < -0.05:
        y5_txt = f"5 günde para çıkışı sürüyor ({cmf5 * 100:+.1f}%)."; y5_clr = DN
    else:
        y5_txt = f"5 günde akış dengede ({cmf5 * 100:+.1f}%)."; y5_clr = NEU
    if cmf20 > 0.05:
        y20_txt = "20 günde kalıcı birikim var."; y20_clr = UP
    elif cmf20 < -0.05:
        y20_txt = "20 günde kalıcı dağıtım var."; y20_clr = DN
    else:
        y20_txt = "20 günde akış dengede, kalıcı birikim yok."; y20_clr = NEU
    _p5 = cmf5 > 0.05; _n5 = cmf5 < -0.05; _p20 = cmf20 > 0.05; _n20 = cmf20 < -0.05
    if _p5 and _p20:
        harman_txt = "→ Kısa ve uzun vade alıcıda, birikim yapıya oturuyor."; harman_clr = UP
    elif _n5 and _n20:
        harman_txt = "→ Kısa ve uzun vade satıcıda, dağıtım sürüyor."; harman_clr = DN
    elif _p5 and not _p20:
        harman_txt = "→ Kısa vadeli ilgi başladı ama henüz yapıya dönüşmedi."; harman_clr = '#f59e0b'
    elif _n5 and not _n20:
        harman_txt = "→ Kısa vadeli satış var ama uzun vadeli yapı bozulmadı."; harman_clr = '#f59e0b'
    else:
        harman_txt = "→ Her iki vadede de net bir yön yok."; harman_clr = NEU

    def row(lbl, val, clr):
        return (f"<div style='display:grid;grid-template-columns:1fr 1fr;border-top:1px solid #1e2c40;'>"
                f"<span style='padding:5px 8px;font-size:0.69rem;color:{MUT};font-weight:600;'>{lbl}</span>"
                f"<span style='padding:5px 8px;border-left:1px solid #1e2c40;font-size:0.71rem;"
                f"color:{clr};font-weight:800;'>{val}</span></div>")
    return (
        f"<div style='background:#111a28;border:1px solid #1e2c40;border-radius:10px;padding:8px 12px;"
        f"font-family:\"Segoe UI\",Arial;'>"
        f"<div style='font-size:0.86rem;font-weight:800;color:#e6edf6;margin-bottom:7px;'>"
        f"ALICI–SATICI BASKISI</div>"
        f"<div style='border:1px solid #1e2c40;border-radius:7px;overflow:hidden;'>"
        + row("Son seans", pressure_txt, pressure_clr)
        + row("Kapanış Mumu", close_txt, close_clr)
        + row("İşlem Hacmi", vm_txt, vm_clr)
        + row("5g para akışı", f"{cmf5 * 100:+.1f}%", cmf5_clr)
        + row("20g para akışı", orta_txt, orta_clr)
        + row("OBV", obv_txt, obv_clr)
        + f"</div><div style='margin-top:7px;border:1px solid #1e2c40;border-radius:6px;padding:6px 8px;'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;'>"
        f"<span style='font-size:0.64rem;font-weight:800;color:#cbd5e1;letter-spacing:0.04em;'>YAPISAL</span>"
        f"<span style='font-size:0.58rem;color:{MUT};'>5-20 gün</span></div>"
        f"<div style='font-size:0.63rem;color:{y5_clr};line-height:1.5;'>{y5_txt}</div>"
        f"<div style='font-size:0.63rem;color:{y20_clr};line-height:1.5;'>{y20_txt}</div>"
        f"<div style='margin-top:4px;padding-top:4px;border-top:1px solid #1e2c40;"
        f"font-size:0.63rem;color:{harman_clr};line-height:1.5;'>{harman_txt}</div>"
        f"</div>"
        f"</div>"
    )


if __name__ == '__main__':
    tk = sys.argv[1] if len(sys.argv) > 1 else 'SAHOL'
    html = build_compass_html(tk)
    if not html: print('veri yok'); sys.exit(1)
    f = forces(load(tk))
    open(os.path.join(BASE, f'compass_{tk}.html'), 'w', encoding='utf-8').write(html)
    print(f"✅ compass_{tk}.html · OBV='{f['title']}' y={f['y']:+.2f} x={f['x']:+.2f} cmf={f['cmf_state']} d5={f['d5']:+.0f}")
