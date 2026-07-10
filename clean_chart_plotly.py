#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Temiz mum grafiği — PLOTLY (gerçek mum render + eksenler, TradingView kalitesi).
mumlar + SMA50/EMA144/SMA100/SMA200 + POC + VWAP. Gürültü YOK. PNG/SVG export (kaleido)."""
import os, sys
import numpy as np, pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
BASE = os.path.dirname(os.path.abspath(__file__)); VER = os.path.join(BASE, 'veriler')


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


def poc(seg):
    pr = ((seg['High'] + seg['Low']) / 2).values; vo = seg['Volume'].values
    hist, edges = np.histogram(pr, bins=24, weights=vo); i = int(np.argmax(hist))
    return (edges[i] + edges[i+1]) / 2


def _wk_rangebreaks(idx):
    """Hafta sonu boşluğunu yalnızca 5-günlük (BIST) veride gizle.
    Kripto/FX/emtia 7-gün işlem görür → hafta sonu barı varsa rangebreak UYGULANMAZ
    (yoksa sürekli çizgiler — MA/VWAP — o noktalarda kopar, 'kesik kesik' görünür)."""
    try:
        dows = idx.dayofweek
        if bool(((dows == 5) | (dows == 6)).any()):
            return []
    except Exception:
        pass
    return [dict(bounds=['sat', 'mon'])]


def build_fig(ticker, nbar=120):
    df = load(ticker)
    if df is None or len(df) < 60: return None
    full = df['Close']
    mas = [('SMA 50', full.rolling(50, min_periods=1).mean(), '#e8e8e8'),
           ('EMA 144', full.ewm(span=144, adjust=False).mean(), '#a855f7'),
           ('SMA 100', full.rolling(100, min_periods=1).mean(), '#4aa3ff'),
           ('SMA 200', full.rolling(200, min_periods=1).mean(), '#f59e0b')]
    seg = df.tail(nbar)
    vwap = (((seg['High'] + seg['Low'] + seg['Close']) / 3 * seg['Volume']).cumsum() / seg['Volume'].cumsum())
    p = poc(seg)
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=seg.index, open=seg['Open'], high=seg['High'], low=seg['Low'], close=seg['Close'],
                                 increasing=dict(line=dict(color='#2ec177', width=1), fillcolor='#2ec177'),
                                 decreasing=dict(line=dict(color='#f0556a', width=1), fillcolor='#f0556a'),
                                 name='Fiyat', showlegend=False))
    def _lv(s):
        v = float(s.iloc[-1])
        return f"{v:.0f}" if v >= 100 else (f"{v:.2f}" if v >= 1 else f"{v:.4f}")
    for nm, ser, col in mas:
        fig.add_trace(go.Scatter(x=seg.index, y=ser.tail(nbar), mode='lines',
                                 line=dict(color=col, width=1.4), name=f"{nm} ({_lv(ser)})"))
    fig.add_trace(go.Scatter(x=seg.index, y=vwap, mode='lines',
                             line=dict(color='#1dc6c6', width=1.4, dash='dot'), name=f"VWAP ({_lv(vwap)})"))
    fig.add_hline(y=p, line=dict(color='#e0a72e', width=1.2, dash='dash'),
                  annotation_text=f'POC {p:.1f}', annotation_position='top left',
                  annotation_font=dict(color='#e0a72e', size=11))
    fig.update_layout(paper_bgcolor='#0d1623', plot_bgcolor='#0d1623', font=dict(color='#8aa0bb', size=11),
                      xaxis_rangeslider_visible=False, margin=dict(l=8, r=58, t=8, b=22),
                      legend=dict(orientation='h', y=1.03, x=0, font=dict(size=11), bgcolor='rgba(0,0,0,0)'),
                      width=720, height=300)
    fig.update_xaxes(gridcolor='#162234', showgrid=True, rangebreaks=_wk_rangebreaks(seg.index))
    fig.update_yaxes(gridcolor='#162234', side='right', tickformat='.1f')
    return fig


def build_ivme_fig(ticker, nbar=30):
    """İvme/Denge paneli — app calculate_synthetic_sentiment mantığıyla BİREBİR.
    Sol: Momentum (MF_Smooth barları + fiyat çizgisi). Sağ: Fiyat ↔ Eğilim (STP sarı vs Fiyat mavi + gri bant).
    AL/SAT dili ÇIKARILDI (uyumluluk)."""
    df = load(ticker)
    if df is None or len(df) < 40: return None
    close = df['Close']
    # 9 Tem 2026 — İVME KARIŞIMI: eski DEMA6 konum formülü yerine %50 STP eğimi
    # + %50 kompozit puan değişimi. TEK KAYNAK: indicators.compute_flow_momentum.
    from indicators import compute_flow_momentum
    mf, stp = compute_flow_momentum(df)
    if mf is None: return None
    d = pd.DataFrame({'Price': close.values, 'MF': mf.values, 'STP': stp.values},
                     index=df.index).tail(nbar)
    xs = [t.strftime('%d %b') for t in d.index]
    fig = make_subplots(rows=1, cols=2, horizontal_spacing=0.10,
                        specs=[[{'secondary_y': True}, {'secondary_y': False}]],
                        subplot_titles=('Momentum', 'Fiyat ↔ Eğilim'))
    # SOL — Momentum: barlar (poz mavi / neg kırmızı) + fiyat çizgisi (ikinci eksen)
    bar_col = ['#5B84C4' if v > 0 else '#ef4444' for v in d['MF']]
    fig.add_trace(go.Bar(x=xs, y=d['MF'], marker_color=bar_col, marker_line_width=0,
                         showlegend=False, name='Para Akışı'), row=1, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(x=xs, y=d['Price'], mode='lines', line=dict(color='#bfdbfe', width=2),
                             showlegend=False, name='Fiyat'), row=1, col=1, secondary_y=True)
    # SAĞ — Sentiment: STP (sarı) + Fiyat (mavi) + aradaki gri bant
    fig.add_trace(go.Scatter(x=xs, y=d['STP'], mode='lines', line=dict(color='#fbbf24', width=3),
                             showlegend=False, name='Eğilim'), row=1, col=2)
    fig.add_trace(go.Scatter(x=xs, y=d['Price'], mode='lines', line=dict(color='#bfdbfe', width=2),
                             fill='tonexty', fillcolor='rgba(148,163,184,0.13)',
                             showlegend=False, name='Fiyat'), row=1, col=2)
    fig.update_layout(paper_bgcolor='#0d1623', plot_bgcolor='#0d1623', font=dict(color='#8aa0bb', size=10),
                      margin=dict(l=8, r=8, t=24, b=22), width=720, height=150, bargap=0.25)
    fig.update_xaxes(gridcolor='#162234', showgrid=False, tickangle=-45, tickfont=dict(size=8), nticks=8)
    fig.update_yaxes(gridcolor='#162234', zeroline=True, zerolinecolor='#1e2c40')
    fig.update_yaxes(showticklabels=False, row=1, col=1, secondary_y=False)
    fig.update_yaxes(showticklabels=False, row=1, col=1, secondary_y=True)
    # 10 Tem 2026 — SABİT EKSEN: ±30 = ±3σ (app paneliyle aynı kural); aşan bar olursa genişler.
    _lim = max(30.0, float(d['MF'].abs().max()) * 1.05)
    fig.update_yaxes(range=[-_lim * 1.1, _lim * 1.1], row=1, col=1, secondary_y=False)
    fig.update_yaxes(side='right', tickformat='.0f', row=1, col=2)
    for ann in fig['layout']['annotations']:
        ann['font'] = dict(color='#38bdf8', size=12)
    return fig


# ════════════════════════════════════════════════════════════════════════════
# İNDİKATÖRLER (22 Haz 2026) — ecur99 RSI+SMA · HARSI (Heikin-Ashi RSI)
# ════════════════════════════════════════════════════════════════════════════
def _wilder_rsi(s, n=14):
    """Pine rsi() ile birebir — Wilder RMA (ewm alpha=1/n)."""
    d = s.diff()
    gain = d.clip(lower=0); loss = (-d).clip(lower=0)
    ag = gain.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    al = loss.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = ag / al.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _ha_smooth(arr):
    """HA-tarzı RSI yumuşatma: sm[i] = (sm[i-1] + v)/2, ilk geçerli = v."""
    out = np.full(len(arr), np.nan); prev = np.nan
    for i in range(len(arr)):
        v = arr[i]
        if np.isnan(v):
            continue
        out[i] = v if np.isnan(prev) else (prev + v) / 2.0
        prev = out[i]
    return out


def _pivots(arr, k, kind):
    """±k bar penceresinde yerel tepe ('H') / dip ('L') indeksleri."""
    idx = []
    n = len(arr)
    for i in range(k, n - k):
        win = arr[i - k:i + k + 1]
        if np.isnan(arr[i]) or np.isnan(win).any():
            continue
        if kind == 'H' and arr[i] == np.max(win):
            idx.append(i)
        elif kind == 'L' and arr[i] == np.min(win):
            idx.append(i)
    return idx


def _find_divergence(price, osc, k=3, recent=28):
    """Fiyat tepe/dip vs momentum (osilatör) tepe/dip uyumsuzluğu.
    'bear' = fiyat daha yüksek zirve ama momentum daha düşük zirve (tepe yorgunluğu).
    'bull' = fiyat daha düşük dip ama momentum daha yüksek dip (dip yorgunluğu).
    Döner: ('bear'|'bull', a, b) veya None."""
    n = len(price)
    res = None
    ph = _pivots(price, k, 'H')
    if len(ph) >= 2:
        a, b = ph[-2], ph[-1]
        if (b >= n - recent and b - a >= 5 and not np.isnan(osc[a]) and not np.isnan(osc[b])
                and price[b] > price[a] * 1.004 and osc[b] < osc[a] - 1.5 and osc[a] > 0):
            res = ('bear', a, b)
    pl = _pivots(price, k, 'L')
    if len(pl) >= 2:
        a, b = pl[-2], pl[-1]
        if (b >= n - recent and b - a >= 5 and not np.isnan(osc[a]) and not np.isnan(osc[b])
                and price[b] < price[a] * 0.996 and osc[b] > osc[a] + 1.5 and osc[a] < 0):
            if res is None or b > res[2]:
                res = ('bull', a, b)
    return res


def build_ecur99_fig(ticker, nbar=120):
    """ecur99 RSI with SMA — RSI(14, Kapanış) + SMA(50). OB/OS 70/30."""
    df = load(ticker)
    if df is None or len(df) < 70:
        return None
    rsi = _wilder_rsi(df['Close'], 14)
    sma = rsi.rolling(50, min_periods=1).mean()
    idx = df.tail(nbar).index
    fig = go.Figure()
    fig.add_hrect(y0=30, y1=70, fillcolor='#1e2c40', opacity=0.18, line_width=0)
    fig.add_hline(y=70, line=dict(color='#f0556a', width=1, dash='dash'), opacity=0.55)
    fig.add_hline(y=30, line=dict(color='#2ec177', width=1, dash='dash'), opacity=0.55)
    fig.add_hline(y=50, line=dict(color='#64748b', width=0.8, dash='dot'), opacity=0.5)
    fig.add_trace(go.Scatter(x=idx, y=sma.tail(nbar), mode='lines',
                             line=dict(color='#e0a72e', width=1.6), name='SMA 50'))
    fig.add_trace(go.Scatter(x=idx, y=rsi.tail(nbar), mode='lines',
                             line=dict(color='#4aa3ff', width=1.6), name='RSI 14'))
    fig.update_layout(paper_bgcolor='#0d1623', plot_bgcolor='#0d1623', font=dict(color='#8aa0bb', size=9),
                      margin=dict(l=8, r=40, t=6, b=18), width=720, height=100, showlegend=False,
                      yaxis=dict(range=[0, 100]))
    fig.update_xaxes(gridcolor='#162234', showgrid=False, rangebreaks=_wk_rangebreaks(idx),
                     tickfont=dict(size=8))
    fig.update_yaxes(gridcolor='#162234', side='right', tickvals=[30, 50, 70], tickfont=dict(size=8))
    return fig


def build_harsi_fig(ticker, nbar=120, len_h=14, len_rsi=7, smoothing=1):
    """HARSI (JayRogers, @version=4) — sıfır-merkezli Heikin-Ashi RSI mumları +
    smoothed RSI çizgisi (ohlc4, len 7) + histogram. Stochastic KAPALI (kullanıcı ayarı)."""
    df = load(ticker)
    if df is None or len(df) < 80:
        return None
    H, Lo, C, O = df['High'], df['Low'], df['Close'], df['Open']
    z = lambda s, n: (_wilder_rsi(s, n) - 50).values
    # HARSI mumları (uzunluk 14)
    closeRSI = z(C, len_h)
    openRSI = np.concatenate([[closeRSI[0]], closeRSI[:-1]])   # nz(closeRSI[1], closeRSI)
    hr = z(H, len_h); lr = z(Lo, len_h)
    highRSI = np.maximum(hr, lr); lowRSI = np.minimum(hr, lr)
    haClose = (openRSI + highRSI + lowRSI + closeRSI) / 4.0
    haOpen = np.full(len(df), np.nan); prevO = np.nan
    for i in range(len(df)):
        if np.isnan(closeRSI[i]):
            continue
        if np.isnan(prevO):
            haOpen[i] = (openRSI[i] + closeRSI[i]) / 2.0
        else:
            haOpen[i] = (prevO * smoothing + haClose[i - 1]) / (smoothing + 1)
        prevO = haOpen[i]
    haHigh = np.maximum(highRSI, np.maximum(haOpen, haClose))
    haLow = np.minimum(lowRSI, np.minimum(haOpen, haClose))
    # RSI çizgisi + histogram (ohlc4, len 7, smoothed)
    ohlc4 = (O + H + Lo + C) / 4.0
    rsiLine = _ha_smooth(z(ohlc4, len_rsi))
    s = slice(len(df) - nbar, len(df)); x = df.index[s]
    fig = go.Figure()
    # OB/OS bantları + çizgiler
    fig.add_hrect(y0=20, y1=30, fillcolor='#f0556a', opacity=0.07, line_width=0)
    fig.add_hrect(y0=-20, y1=20, fillcolor='#4aa3ff', opacity=0.05, line_width=0)
    fig.add_hrect(y0=-30, y1=-20, fillcolor='#2ec177', opacity=0.07, line_width=0)
    for y in (30, 20, -20, -30):
        fig.add_hline(y=y, line=dict(color='#64748b', width=0.7), opacity=0.4)
    fig.add_hline(y=0, line=dict(color='#e0a72e', width=0.8, dash='dot'), opacity=0.6)
    # histogram (RSI değeri) — altta
    fig.add_trace(go.Bar(x=x, y=rsiLine[s], marker_color='rgba(148,163,184,0.32)',
                         marker_line_width=0, name='Hist'))
    # HARSI mumları
    fig.add_trace(go.Candlestick(x=x, open=haOpen[s], high=haHigh[s], low=haLow[s], close=haClose[s],
                                 increasing=dict(line=dict(color='#14b8a6', width=1), fillcolor='#14b8a6'),
                                 decreasing=dict(line=dict(color='#f0556a', width=1), fillcolor='#f0556a'),
                                 name='HARSI'))
    # RSI çizgisi (altın)
    fig.add_trace(go.Scatter(x=x, y=rsiLine[s], mode='lines', line=dict(color='#FAC832', width=1.4), name='RSI'))

    # ── UYUMSUZLUK (divergence) — fiyat tepe/dip vs momentum (25 Haz 2026) ──
    _height, _bmargin = 110, 18
    _div = _find_divergence(C.values[s], haClose[s])
    if _div:
        _kind, _a, _b = _div
        _col = '#f0556a' if _kind == 'bear' else '#2ec177'
        _off = 5 if _kind == 'bear' else -5
        _ya, _yb = haClose[s][_a] + _off, haClose[s][_b] + _off
        _xa, _xb = x[_a].to_pydatetime(), x[_b].to_pydatetime()
        fig.add_trace(go.Scatter(x=[_xa, _xb], y=[_ya, _yb], mode='lines+markers',
                                 line=dict(color=_col, width=1.8, dash='dash'),
                                 marker=dict(color=_col, size=6), name='div', showlegend=False))
        _lbl = 'Ayı uyumsuzluğu' if _kind == 'bear' else 'Boğa uyumsuzluğu'
        fig.add_annotation(x=_xb, y=_yb, text=_lbl, showarrow=False,
                           font=dict(color=_col, size=11), xanchor='right',
                           yanchor='bottom' if _kind == 'bear' else 'top',
                           bgcolor='#0d1623', bordercolor=_col, borderwidth=0.8, borderpad=2)
        _cap = ('Ayı uyumsuzluğu: fiyat yeni zirve yaptı ama momentum daha düşük zirveden döndü.<br>'
                'Tepe yorgunluğu — yükseliş güç kaybediyor, dikkat.') if _kind == 'bear' else (
                'Boğa uyumsuzluğu: fiyat yeni dip yaptı ama momentum daha yüksek dipten döndü.<br>'
                'Dip yorgunluğu — düşüş güç kaybediyor, dönüş habercisi.')
        fig.add_annotation(xref='paper', yref='paper', x=0, y=-0.22, text=_cap, showarrow=False,
                           font=dict(color=_col, size=11), xanchor='left', yanchor='top', align='left')
        _height, _bmargin = 182, 80
    fig.update_layout(paper_bgcolor='#0d1623', plot_bgcolor='#0d1623', font=dict(color='#8aa0bb', size=9),
                      margin=dict(l=8, r=40, t=6, b=_bmargin), width=720, height=_height, showlegend=False,
                      xaxis_rangeslider_visible=False, bargap=0.2)
    fig.update_xaxes(gridcolor='#162234', showgrid=False, rangebreaks=_wk_rangebreaks(x),
                     tickfont=dict(size=8))
    fig.update_yaxes(gridcolor='#162234', side='right', tickfont=dict(size=8))
    return fig


if __name__ == '__main__':
    tk = sys.argv[1] if len(sys.argv) > 1 else 'SAHOL'
    mode = sys.argv[2] if len(sys.argv) > 2 else 'chart'
    if mode == 'ivme':
        fig = build_ivme_fig(tk)
        if fig is None: print('veri yok'); sys.exit(1)
        png = os.path.join(BASE, f'ivme_{tk}.png')
        fig.write_image(png, scale=2)
        print(f'✅ {png}')
    else:
        fig = build_fig(tk)
        if fig is None: print('veri yok'); sys.exit(1)
        png = os.path.join(BASE, f'cleanp_{tk}.png'); svg = os.path.join(BASE, f'cleanp_{tk}.svg')
        fig.write_image(png, scale=2)
        fig.write_image(svg)
        print(f'✅ {png} + {svg}')
