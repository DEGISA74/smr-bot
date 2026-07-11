# -*- coding: utf-8 -*-
"""charts.py — ANA FIYAT GRAFIGI (Adim 8, 11 Tem 2026)
========================================================================
app.py bolme — _main_price_chart_plotly BIREBIR tasindi (davranis degisikligi
YOK, sadece adres). Interaktif Plotly candlestick: SMC (OB/FVG/BOS) + EMA144 +
SMA50/100/200 + hacim + VWAP sigma bantlari + aVWAP 52H + POC/naked POC.
Fotograf: golden_record price_chart_struct hedefi (yapisal parmak izi, sifir
fark) + tasima oncesi/sonrasi canli DOM kiyasi (gozle dogrulama).
"""
import traceback
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from data_layer import get_safe_historical_data, TICKER_DISPLAY_NAMES
from indicators import (calculate_anchored_vwap, calculate_multi_tf_pocs,
                        detect_naked_poc)
from scoring_core import _compute_smc_elements


def _main_price_chart_plotly(symbol, dark_mode):
    """İnteraktif Plotly candlestick: SMC + EMA144 + SMA50/100/200 + volume."""
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        df_full = get_safe_historical_data(symbol, period="1y")
        if df_full is None or len(df_full) < 10:
            return None, {}

        # MA'ları tam veri üzerinde hesapla (doğru seed için)
        close_full = df_full['Close']
        sma50_full  = close_full.rolling(50).mean()
        sma100_full = close_full.rolling(100).mean()
        sma200_full = close_full.rolling(200).mean()
        ema144_full = close_full.ewm(span=144, adjust=False).mean()

        # Son 90 günü al
        df = df_full.tail(90).copy()
        sma50_arr  = sma50_full.iloc[-90:]
        sma100_arr = sma100_full.iloc[-90:]
        sma200_arr = sma200_full.iloc[-90:]
        ema144_arr = ema144_full.iloc[-90:]

        opens  = df['Open'].values.astype(float)
        highs  = df['High'].values.astype(float)
        lows   = df['Low'].values.astype(float)
        closes = df['Close'].values.astype(float)
        dates  = df.index
        n      = len(df)

        smc = _compute_smc_elements(highs, lows, opens, closes, n_pivot=5)

        if dark_mode:
            bg       = '#060f1e'
            paper_bg = '#060f1e'
            fg       = '#7a92b0'
            grid     = 'rgba(255,255,255,0.05)'
        else:
            bg       = '#ffffff'
            paper_bg = '#ffffff'
            fg       = '#1e293b'
            grid     = 'rgba(0,0,0,0.06)'

        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            row_heights=[0.87, 0.13],
            vertical_spacing=0.0,
        )

        # Fiyat formatı: >=1000 → tam sayı, <1000 → 2 ondalık
        _pfmt = ',.0f' if closes[-1] >= 1000 else ',.2f'

        # ── Candlestick ───────────────────────────────────────────────
        fig.add_trace(go.Candlestick(
            x=dates, open=opens, high=highs, low=lows, close=closes,
            name='Fiyat',
            increasing_line_color='#00c896', increasing_fillcolor='#00c896',
            decreasing_line_color='#ff4757', decreasing_fillcolor='#ff4757',
            line_width=1,
            hovertemplate=(
                "<b>%{x|%d %b %Y}</b><br>"
                f"A: %{{open:{_pfmt}}}  "
                f"Y: %{{high:{_pfmt}}}  "
                f"D: %{{low:{_pfmt}}}  "
                f"K: %{{close:{_pfmt}}}"
                "<extra></extra>"
            ),
        ), row=1, col=1)

        # ── MA çizgileri ──────────────────────────────────────────────
        def _ma_lbl(name, arr):
            v = arr.dropna()
            if v.empty: return name
            val = v.iloc[-1]
            return f"{name}: {int(val):,}" if val >= 1000 else f"{name}: {val:.2f}"

        _l50  = _ma_lbl('SMA 50',  sma50_arr)
        _l100 = _ma_lbl('SMA 100', sma100_arr)
        _l144 = _ma_lbl('EMA 144', ema144_arr)
        _l200 = _ma_lbl('SMA 200', sma200_arr)

        for arr, color, name, width in [
            (sma50_arr,  '#ef5350', _l50,  1.3),
            (sma100_arr, '#2196F3', _l100, 1.3),
            (ema144_arr, '#a78bfa', _l144, 1.5),
            (sma200_arr, '#ff7043', _l200, 1.3),
        ]:
            fig.add_trace(go.Scatter(
                x=dates, y=arr,
                name=name, line=dict(color=color, width=width),
                hoverinfo='skip',
            ), row=1, col=1)

        # ── Volume ────────────────────────────────────────────────────
        vol_colors = ["rgba(0,200,150,0.55)" if closes[i] >= opens[i] else "rgba(255,71,87,0.45)"
                      for i in range(n)]
        fig.add_trace(go.Bar(
            x=dates, y=df['Volume'].values,
            name='Hacim', marker_color=vol_colors,
            marker_line_width=0, opacity=1.0,
            showlegend=False,
        ), row=2, col=1)

        # ── SMC Shapes ────────────────────────────────────────────────
        shapes = []
        annotations = []

        # Premium / Discount / EQ zones
        sh = smc['swing_high']
        sl = smc['swing_low']
        if sh is not None and sl is not None and sh > sl:
            rng     = sh - sl
            disc_hi = sl + rng * 0.30
            prem_lo = sh - rng * 0.30
            eq_lo   = sl + rng * 0.45
            eq_hi   = sl + rng * 0.55
            x0_zone = dates[0]
            x1_zone = dates[-1]
            for y0, y1, fc in [
                (sl,      disc_hi, 'rgba(38,166,154,0.07)'),
                (prem_lo, sh,      'rgba(239,83,80,0.07)'),
                (eq_lo,   eq_hi,   'rgba(167,139,250,0.09)'),
            ]:
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=x0_zone, x1=x1_zone, y0=y0, y1=y1,
                    fillcolor=fc, line_width=0, layer='below'))
            for lbl, ypos, clr in [
                ('DISCOUNT', (sl + disc_hi) / 2,   '#26a69a'),
                ('EQ',   (eq_lo + eq_hi) / 2,  '#a78bfa'),
                ('PREMIUM', (prem_lo + sh) / 2,    '#ef5350'),
            ]:
                annotations.append(dict(
                    x=dates[-1], y=ypos, xref='x', yref='y',
                    text=f"<b>{lbl}</b>", showarrow=False,
                    xanchor='left', font=dict(size=11, color=clr), bgcolor='rgba(0,0,0,0)'))

        # FVG — state-aware render (9 Haz 2026 Oturum 20)
        # fresh: dolu kutu (orjinal renk), tested: ince kutu, inverted: zıt renk + 'iFVG' label
        for _fvg_tuple in smc['fvg_bull']:
            xi, p_lo, p_hi, _st = (_fvg_tuple if len(_fvg_tuple) == 4
                                   else (_fvg_tuple[0], _fvg_tuple[1], _fvg_tuple[2], 'fresh'))
            if xi >= n: continue
            if _st == 'inverted':
                # Rol tersine döndü → bull FVG artık direnç (kırmızı vurgulu)
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=dates[xi], x1=dates[-1], y0=p_lo, y1=p_hi,
                    fillcolor='rgba(239,83,80,0.07)',
                    line=dict(color='#ef5350', width=0.8, dash='dash'), layer='below'))
                annotations.append(dict(
                    x=dates[min(xi + 1, n - 1)], y=p_hi, xref='x', yref='y',
                    text='iFVG↓', showarrow=False, yanchor='bottom',
                    font=dict(size=9, color='#ef5350', family='monospace')))
            elif _st == 'tested':
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=dates[xi], x1=dates[-1], y0=p_lo, y1=p_hi,
                    fillcolor='rgba(38,166,154,0.06)',
                    line=dict(color='#26a69a', width=0.4, dash='dot'), layer='below'))
                annotations.append(dict(
                    x=dates[min(xi + 1, n - 1)], y=p_hi, xref='x', yref='y',
                    text='FVG·', showarrow=False, yanchor='bottom',
                    font=dict(size=9, color='rgba(38,166,154,0.7)', family='monospace')))
            else:  # fresh
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=dates[xi], x1=dates[-1], y0=p_lo, y1=p_hi,
                    fillcolor='rgba(38,166,154,0.12)',
                    line=dict(color='#26a69a', width=0.5, dash='dot'), layer='below'))
                annotations.append(dict(
                    x=dates[min(xi + 1, n - 1)], y=p_hi, xref='x', yref='y',
                    text='FVG', showarrow=False, yanchor='bottom',
                    font=dict(size=10, color='#26a69a', family='monospace')))
        for _fvg_tuple in smc['fvg_bear']:
            xi, p_lo, p_hi, _st = (_fvg_tuple if len(_fvg_tuple) == 4
                                   else (_fvg_tuple[0], _fvg_tuple[1], _fvg_tuple[2], 'fresh'))
            if xi >= n: continue
            if _st == 'inverted':
                # Rol tersine döndü → bear FVG artık destek (yeşil vurgulu)
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=dates[xi], x1=dates[-1], y0=p_lo, y1=p_hi,
                    fillcolor='rgba(38,166,154,0.07)',
                    line=dict(color='#26a69a', width=0.8, dash='dash'), layer='below'))
                annotations.append(dict(
                    x=dates[min(xi + 1, n - 1)], y=p_lo, xref='x', yref='y',
                    text='iFVG↑', showarrow=False, yanchor='top',
                    font=dict(size=9, color='#26a69a', family='monospace')))
            elif _st == 'tested':
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=dates[xi], x1=dates[-1], y0=p_lo, y1=p_hi,
                    fillcolor='rgba(239,83,80,0.06)',
                    line=dict(color='#ef5350', width=0.4, dash='dot'), layer='below'))
                annotations.append(dict(
                    x=dates[min(xi + 1, n - 1)], y=p_lo, xref='x', yref='y',
                    text='FVG·', showarrow=False, yanchor='top',
                    font=dict(size=9, color='rgba(239,83,80,0.7)', family='monospace')))
            else:  # fresh
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=dates[xi], x1=dates[-1], y0=p_lo, y1=p_hi,
                    fillcolor='rgba(239,83,80,0.12)',
                    line=dict(color='#ef5350', width=0.5, dash='dot'), layer='below'))
                annotations.append(dict(
                    x=dates[min(xi + 1, n - 1)], y=p_lo, xref='x', yref='y',
                    text='FVG', showarrow=False, yanchor='top',
                    font=dict(size=10, color='#ef5350', family='monospace')))

        # Order Blocks — state-aware render (fresh / mitigation / breaker)
        # breaker: rol ters döner → bull OB artık direnç, bear OB artık destek
        for _ob in smc['ob_bull']:
            bi, o_, c_, h_, l_, _st = (_ob if len(_ob) == 6
                                       else (_ob[0], _ob[1], _ob[2], _ob[3], _ob[4], 'fresh'))
            if bi >= n: continue
            if _st == 'breaker':
                # Failed bull OB → şimdi direnç. Kırmızı bordeau.
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=dates[max(0, bi - 1)], x1=dates[min(n - 1, bi + 3)],
                    y0=l_, y1=h_,
                    fillcolor='rgba(239,83,80,0.10)',
                    line=dict(color='#ef5350', width=1.2, dash='dash')))
                annotations.append(dict(
                    x=dates[bi], y=h_, xref='x', yref='y',
                    text='BB↓', showarrow=False, yanchor='bottom',
                    font=dict(size=9, color='#ef5350', family='monospace')))
            elif _st == 'mitigation':
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=dates[max(0, bi - 1)], x1=dates[min(n - 1, bi + 3)],
                    y0=l_, y1=h_,
                    fillcolor='rgba(38,166,154,0.10)',
                    line=dict(color='#26a69a', width=0.7, dash='dot')))
                annotations.append(dict(
                    x=dates[bi], y=h_, xref='x', yref='y',
                    text='OB·', showarrow=False, yanchor='bottom',
                    font=dict(size=9, color='rgba(38,166,154,0.75)', family='monospace')))
            else:  # fresh
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=dates[max(0, bi - 1)], x1=dates[min(n - 1, bi + 3)],
                    y0=l_, y1=h_,
                    fillcolor='rgba(38,166,154,0.20)',
                    line=dict(color='#26a69a', width=1, dash='dash')))
                annotations.append(dict(
                    x=dates[bi], y=h_, xref='x', yref='y',
                    text='OB', showarrow=False, yanchor='bottom',
                    font=dict(size=10, color='#26a69a', family='monospace')))
        for _ob in smc['ob_bear']:
            bi, o_, c_, h_, l_, _st = (_ob if len(_ob) == 6
                                       else (_ob[0], _ob[1], _ob[2], _ob[3], _ob[4], 'fresh'))
            if bi >= n: continue
            if _st == 'breaker':
                # Failed bear OB → şimdi destek. Yeşil vurgulu.
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=dates[max(0, bi - 1)], x1=dates[min(n - 1, bi + 3)],
                    y0=l_, y1=h_,
                    fillcolor='rgba(38,166,154,0.10)',
                    line=dict(color='#26a69a', width=1.2, dash='dash')))
                annotations.append(dict(
                    x=dates[bi], y=l_, xref='x', yref='y',
                    text='BB↑', showarrow=False, yanchor='top',
                    font=dict(size=9, color='#26a69a', family='monospace')))
            elif _st == 'mitigation':
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=dates[max(0, bi - 1)], x1=dates[min(n - 1, bi + 3)],
                    y0=l_, y1=h_,
                    fillcolor='rgba(239,83,80,0.10)',
                    line=dict(color='#ef5350', width=0.7, dash='dot')))
                annotations.append(dict(
                    x=dates[bi], y=l_, xref='x', yref='y',
                    text='OB·', showarrow=False, yanchor='top',
                    font=dict(size=9, color='rgba(239,83,80,0.75)', family='monospace')))
            else:  # fresh
                shapes.append(dict(type='rect', xref='x', yref='y',
                    x0=dates[max(0, bi - 1)], x1=dates[min(n - 1, bi + 3)],
                    y0=l_, y1=h_,
                    fillcolor='rgba(239,83,80,0.20)',
                    line=dict(color='#ef5350', width=1, dash='dash')))
                annotations.append(dict(
                    x=dates[bi], y=l_, xref='x', yref='y',
                    text='OB', showarrow=False, yanchor='top',
                    font=dict(size=10, color='#ef5350', family='monospace')))

        # BOS / CHoCH
        for (bi, price, kind) in smc['bos_lines']:
            if bi < n:
                clr = '#38bdf8' if kind == 'BOS' else '#f472b6'
                shapes.append(dict(type='line', xref='x', yref='y',
                    x0=dates[0], x1=dates[-1], y0=price, y1=price,
                    line=dict(color=clr, width=1, dash='dot')))
                annotations.append(dict(
                    x=dates[bi], y=price, xref='x', yref='y',
                    text=f'<b>{kind}</b>', showarrow=False, yanchor='bottom',
                    font=dict(size=10, color=clr, family='monospace')))

        # EQH / EQL
        for (ba, bb, price) in smc['eqh']:
            if ba < n and bb < n:
                shapes.append(dict(type='line', xref='x', yref='y',
                    x0=dates[ba], x1=dates[bb], y0=price, y1=price,
                    line=dict(color='#ffd600', width=1, dash='dot')))
                annotations.append(dict(
                    x=dates[bb], y=price, xref='x', yref='y',
                    text='EQH', showarrow=False, yanchor='bottom',
                    font=dict(size=10, color='#ffd600')))
        for (ba, bb, price) in smc['eql']:
            if ba < n and bb < n:
                shapes.append(dict(type='line', xref='x', yref='y',
                    x0=dates[ba], x1=dates[bb], y0=price, y1=price,
                    line=dict(color='#fb923c', width=1, dash='dot')))
                annotations.append(dict(
                    x=dates[bb], y=price, xref='x', yref='y',
                    text='EQL', showarrow=False, yanchor='top',
                    font=dict(size=10, color='#fb923c')))

        disp = TICKER_DISPLAY_NAMES.get(symbol,
               symbol.split('.')[0].replace('=F','').replace('-USD',''))

        # ── 1. ANLIK FİYAT ÇİZGİSİ ───────────────────────────────────
        _cur_price = closes[-1]
        _pfmt_cur  = f"{int(_cur_price):,}" if _cur_price >= 1000 else f"{_cur_price:.2f}"
        shapes.append(dict(
            type='line', xref='x', yref='y',
            x0=dates[0], x1=dates[-1],
            y0=_cur_price, y1=_cur_price,
            line=dict(color='#facc15', width=1.2, dash='dot'),
            layer='above'
        ))
        annotations.append(dict(
            x=dates[-1], y=_cur_price, xref='x', yref='y',
            text=f"<b>{_pfmt_cur}</b>",
            showarrow=False, xanchor='left',
            font=dict(size=11, color='#facc15', family='monospace'),
            bgcolor='rgba(0,0,0,0.45)', borderpad=3,
        ))

        # ── 2. MULTI-TF POC STACK (20g + 60g + 250g) ──────────────────
        # 3 farklı pencerede en yoğun işlem fiyatı — üçü yakınlaşırsa Confluence Zone.
        _poc_val = None        # geriye uyumluluk için "ana POC" (250g yıllık tercih)
        _poc_mtf = {}
        try:
            _poc_mtf = calculate_multi_tf_pocs(df, current_price=_cur_price)
            # Ana POC için yıllık (var ise), yoksa 60g, yoksa 20g
            _poc_val = _poc_mtf.get('poc_250') or _poc_mtf.get('poc_60') or _poc_mtf.get('poc_20')

            # 3 POC için renk+stil paleti — kısa→uzun açıktan koyuya
            _poc_styles = [
                ('poc_20',  '20g',  '#fde68a', 1.0, 'dot'),       # açık sarı, ince
                ('poc_60',  '60g',  '#fbbf24', 1.5, 'dashdot'),   # amber orta
                ('poc_250', '250g', '#f59e0b', 2.2, 'solid'),     # koyu altın kalın
            ]
            for _key, _lbl, _clr, _w, _dash in _poc_styles:
                _pv = _poc_mtf.get(_key)
                if _pv is None or np.isnan(_pv):
                    continue
                shapes.append(dict(
                    type='line', xref='x', yref='y',
                    x0=dates[0], x1=dates[-1],
                    y0=_pv, y1=_pv,
                    line=dict(color=_clr, width=_w, dash=_dash),
                    layer='above'
                ))
                _pv_fmt = f"{int(_pv):,}" if _pv >= 1000 else f"{_pv:.2f}"
                annotations.append(dict(
                    x=dates[-1], y=_pv, xref='x', yref='y',
                    text=f"<b>POC{_lbl} {_pv_fmt}</b>",
                    showarrow=False, xanchor='left', yanchor='middle',
                    font=dict(size=10, color=_clr, family='monospace'),
                    bgcolor='rgba(0,0,0,0.55)', borderpad=2,
                ))

            # ── NAKED POC — geçmiş periyotlarda oluşmuş ama test edilmemiş ─
            # Kurumsal limit emir bölgesi hipotezi. Görsel: kesik gri-mavi çizgi + ⚓
            try:
                _naked_list = detect_naked_poc(df_full, lookback=20, bins=20, n_windows=6)
                # Çok kalabalık olmasın — fiyata en yakın 3 tanesi
                _naked_list = sorted(_naked_list, key=lambda p: abs(p - _cur_price))[:3]
                for _np_val in _naked_list:
                    if _np_val is None or np.isnan(_np_val):
                        continue
                    shapes.append(dict(
                        type='line', xref='x', yref='y',
                        x0=dates[0], x1=dates[-1],
                        y0=_np_val, y1=_np_val,
                        line=dict(color='#67e8f9', width=1.2, dash='dash'),
                        opacity=0.65,
                        layer='above'
                    ))
                    _np_fmt = f"{int(_np_val):,}" if _np_val >= 1000 else f"{_np_val:.2f}"
                    annotations.append(dict(
                        x=dates[-1], y=_np_val, xref='x', yref='y',
                        text=f"⚓ <b>nPOC {_np_fmt}</b>",
                        showarrow=False, xanchor='left', yanchor='middle',
                        font=dict(size=9, color='#67e8f9', family='monospace'),
                        bgcolor='rgba(0,0,0,0.55)', borderpad=2,
                    ))
            except Exception:
                pass

            # POC Confluence Zone — 3 POC %2 içinde ise dikey vurgu
            if _poc_mtf.get('confluence') and _poc_mtf.get('confluence_mid'):
                _cmid = _poc_mtf['confluence_mid']
                _spread = _poc_mtf.get('spread_pct', 0)
                _band = _cmid * 0.012  # ±%1.2 zarf
                shapes.append(dict(
                    type='rect', xref='x', yref='y',
                    x0=dates[0], x1=dates[-1],
                    y0=_cmid - _band, y1=_cmid + _band,
                    fillcolor='rgba(251,191,36,0.10)',
                    line=dict(width=0),
                    layer='below'
                ))
                annotations.append(dict(
                    x=dates[-1], y=_cmid + _band, xref='x', yref='y',
                    text=f"🎯 <b>POC CONFLUENCE</b> (spread %{_spread:.1f})",
                    showarrow=False, xanchor='left', yanchor='bottom',
                    font=dict(size=10, color='#fbbf24', family='monospace'),
                    bgcolor='rgba(0,0,0,0.65)', borderpad=3,
                ))
        except Exception:
            _poc_val = None
            _poc_mtf = {}

        # ── 3. YILLIK VWAP + σ-BANDS ──────────────────────────────────
        # 9 Haz 2026 Oturum 20: σ-bands eklendi. Citadel/Two Sigma/Renaissance
        # mean-reversion algoları ±1σ/±2σ VWAP bantlarını otomatik alım/satım
        # bölgesi olarak kullanır. Fiyat -2σ'da = institutional dip-buy zone.
        _vwap_cur = None
        _vwap_band_summary = {}
        try:
            _vwap_s   = (df_full['Close'] * df_full['Volume']).cumsum() / df_full['Volume'].cumsum()
            _vwap_arr = _vwap_s.iloc[-90:].values.astype(float)
            _vwap_cur = _vwap_arr[-1]
            _vwap_fmt = f"{int(_vwap_cur):,}" if _vwap_cur >= 1000 else f"{_vwap_cur:.2f}"

            # σ hesabı: VWAP'tan tipik sapmanın rolling std'i (20 bar pencere)
            _close_arr = df_full['Close'].iloc[-90:].values.astype(float)
            _dev = _close_arr - _vwap_arr
            _dev_s = pd.Series(_dev)
            _sigma = _dev_s.rolling(20, min_periods=5).std().bfill().values

            _upper_1 = _vwap_arr + _sigma
            _lower_1 = _vwap_arr - _sigma
            _upper_2 = _vwap_arr + 2 * _sigma
            _lower_2 = _vwap_arr - 2 * _sigma

            # σ-bands — soft fill (lower 2 önce çiz, sonra üst doluş; mor tonları)
            fig.add_trace(go.Scatter(
                x=dates, y=_upper_2,
                name='+2σ', line=dict(color='rgba(232,121,249,0.0)', width=0),
                hoverinfo='skip', showlegend=False,
            ), row=1, col=1)
            fig.add_trace(go.Scatter(
                x=dates, y=_lower_2,
                name='-2σ', line=dict(color='rgba(232,121,249,0.0)', width=0),
                fill='tonexty', fillcolor='rgba(232,121,249,0.05)',
                hoverinfo='skip', showlegend=False,
            ), row=1, col=1)
            # ±1σ kesik çizgiler
            fig.add_trace(go.Scatter(
                x=dates, y=_upper_1,
                name='VWAP +1σ',
                line=dict(color='rgba(232,121,249,0.55)', width=1.0, dash='dot'),
                hoverinfo='skip', showlegend=False,
            ), row=1, col=1)
            fig.add_trace(go.Scatter(
                x=dates, y=_lower_1,
                name='VWAP -1σ',
                line=dict(color='rgba(232,121,249,0.55)', width=1.0, dash='dot'),
                hoverinfo='skip', showlegend=False,
            ), row=1, col=1)

            # VWAP ana çizgi (üstte kalsın)
            fig.add_trace(go.Scatter(
                x=dates, y=_vwap_arr,
                name=f'VWAP(Y): {_vwap_fmt}',
                line=dict(color='#e879f9', width=2.0, dash='dash'),
                hoverinfo='skip',
            ), row=1, col=1)
            # Sağ kenarda VWAP etiketi
            annotations.append(dict(
                x=dates[-1], y=_vwap_cur, xref='x', yref='y',
                text=f"<b>VWAP {_vwap_fmt}</b>",
                showarrow=False, xanchor='left', yanchor='middle',
                font=dict(size=11, color='#e879f9', family='monospace'),
                bgcolor='rgba(0,0,0,0.55)', borderpad=3,
            ))
            # ±2σ etiketleri (sağ kenar — staggering pipeline yakalayacak)
            try:
                _u2 = float(_upper_2[-1]); _l2 = float(_lower_2[-1])
                _u2_fmt = f"{int(_u2):,}" if _u2 >= 1000 else f"{_u2:.2f}"
                _l2_fmt = f"{int(_l2):,}" if _l2 >= 1000 else f"{_l2:.2f}"
                annotations.append(dict(
                    x=dates[-1], y=_u2, xref='x', yref='y',
                    text=f"+2σ {_u2_fmt}",
                    showarrow=False, xanchor='left', yanchor='middle',
                    font=dict(size=9, color='rgba(232,121,249,0.85)', family='monospace'),
                    bgcolor='rgba(0,0,0,0.45)', borderpad=2,
                ))
                annotations.append(dict(
                    x=dates[-1], y=_l2, xref='x', yref='y',
                    text=f"-2σ {_l2_fmt}",
                    showarrow=False, xanchor='left', yanchor='middle',
                    font=dict(size=9, color='rgba(232,121,249,0.85)', family='monospace'),
                    bgcolor='rgba(0,0,0,0.45)', borderpad=2,
                ))
                # Özet — AI prompt / institutional_ref için
                _cp = float(_close_arr[-1])
                _vwap_band_summary = {
                    'vwap': _vwap_cur,
                    'upper_1': float(_upper_1[-1]),
                    'lower_1': float(_lower_1[-1]),
                    'upper_2': _u2,
                    'lower_2': _l2,
                    'zone': ('above +2σ' if _cp > _u2 else
                             ('above +1σ' if _cp > float(_upper_1[-1]) else
                              ('below -2σ' if _cp < _l2 else
                               ('below -1σ' if _cp < float(_lower_1[-1]) else 'inside ±1σ')))),
                    'sigma_dist': (_cp - _vwap_cur) / float(_sigma[-1]) if _sigma[-1] > 0 else 0
                }
            except Exception: pass
        except Exception:
            _vwap_cur = None

        # ── 3.A QUARTERLY / YEARLY OPENING (Q-Open, Y-Open) ───────────
        # Kurumsal kalibrasyon noktaları — JPM/GS algos referansı.
        # Y-Open = yılın ilk işlem gününün açılışı, Q-Open = çeyreğin ilk günü.
        _qy_summary = {}
        try:
            _last_date = df_full.index[-1]
            _this_year = _last_date.year
            _this_q = (_last_date.month - 1) // 3 + 1
            _q_start_month = (_this_q - 1) * 3 + 1
            # Y-Open: bu yılın ilk barı
            _y_bars = df_full[df_full.index.year == _this_year]
            if len(_y_bars) >= 1:
                _y_open = float(_y_bars['Open'].iloc[0])
                _y_open_fmt = f"{int(_y_open):,}" if _y_open >= 1000 else f"{_y_open:.2f}"
                shapes.append(dict(
                    type='line', xref='x', yref='y',
                    x0=dates[0], x1=dates[-1], y0=_y_open, y1=_y_open,
                    line=dict(color='#94a3b8', width=1.2, dash='dashdot'),
                    opacity=0.7, layer='above'
                ))
                annotations.append(dict(
                    x=dates[-1], y=_y_open, xref='x', yref='y',
                    text=f"<b>Y-Open {_y_open_fmt}</b>",
                    showarrow=False, xanchor='left', yanchor='middle',
                    font=dict(size=9, color='#cbd5e1', family='monospace'),
                    bgcolor='rgba(0,0,0,0.55)', borderpad=2,
                ))
                _qy_summary['y_open'] = _y_open
                _qy_summary['y_open_dist_pct'] = (float(df_full['Close'].iloc[-1]) - _y_open) / _y_open * 100
            # Q-Open: bu çeyreğin ilk barı
            _q_bars = df_full[(df_full.index.year == _this_year) & (df_full.index.month >= _q_start_month) & (df_full.index.month < _q_start_month + 3)]
            if len(_q_bars) >= 1:
                _q_open = float(_q_bars['Open'].iloc[0])
                # Sadece Y-Open'dan farklıysa çiz (Q1 başında çakışır)
                if abs(_q_open - _qy_summary.get('y_open', 0)) / max(abs(_qy_summary.get('y_open', 1)), 1) > 0.002:
                    _q_open_fmt = f"{int(_q_open):,}" if _q_open >= 1000 else f"{_q_open:.2f}"
                    shapes.append(dict(
                        type='line', xref='x', yref='y',
                        x0=dates[0], x1=dates[-1], y0=_q_open, y1=_q_open,
                        line=dict(color='#fbbf24', width=1.0, dash='dashdot'),
                        opacity=0.55, layer='above'
                    ))
                    annotations.append(dict(
                        x=dates[-1], y=_q_open, xref='x', yref='y',
                        text=f"<b>Q{_this_q}-Open {_q_open_fmt}</b>",
                        showarrow=False, xanchor='left', yanchor='middle',
                        font=dict(size=9, color='#fcd34d', family='monospace'),
                        bgcolor='rgba(0,0,0,0.55)', borderpad=2,
                    ))
                    _qy_summary['q_open'] = _q_open
                    _qy_summary['q_open_dist_pct'] = (float(df_full['Close'].iloc[-1]) - _q_open) / _q_open * 100
                    _qy_summary['q_num'] = _this_q
        except Exception:
            _qy_summary = {}

        # ── 3.B EVENT-ANCHORED VWAP (52H zirve + 52H dip) ─────────────
        # Klasik anchored VWAP TradingView Premium özelliği. BIST tarafında nadir.
        # Soru: "Son zirveden bu yana ortalama alıcı maliyeti nerede? / Son dipten?"
        # → Kurumsal anchored entry/exit referansı.
        _avwap_summary = {}
        try:
            # 52H penceresi (son ~252 bar) — anchor adayları
            _avwap_window = df_full.tail(252) if len(df_full) > 252 else df_full
            if len(_avwap_window) >= 30:
                # 52H zirve ve dip günlerinin df_full içindeki integer pozisyonu
                _hi_pos_in_window = int(np.argmax(_avwap_window['High'].values))
                _lo_pos_in_window = int(np.argmin(_avwap_window['Low'].values))
                _window_start = len(df_full) - len(_avwap_window)
                _hi_anchor_idx = _window_start + _hi_pos_in_window
                _lo_anchor_idx = _window_start + _lo_pos_in_window

                # Grafikte gösterilen son N barı (dates ile aynı genişlik)
                _plot_n = len(dates)
                _plot_start_idx_in_full = len(df_full) - _plot_n

                for _anchor_idx, _label, _color, _key in [
                    (_hi_anchor_idx, '52H ZİRVE', '#ef4444', 'high'),
                    (_lo_anchor_idx, '52H DİP',   '#10b981', 'low'),
                ]:
                    # Anchor günü grafik penceresi içinde mi?
                    if _anchor_idx >= len(df_full) - 1:
                        continue
                    _avwap_series = calculate_anchored_vwap(df_full, _anchor_idx)
                    if _avwap_series is None or len(_avwap_series) < 2:
                        continue
                    _avwap_cur_val = float(_avwap_series.iloc[-1])
                    if np.isnan(_avwap_cur_val):
                        continue

                    # Grafik pencere içine düşen kısmını çiz
                    _draw_from = max(_anchor_idx, _plot_start_idx_in_full)
                    _seg_offset_in_series = _draw_from - _anchor_idx
                    if _seg_offset_in_series >= len(_avwap_series):
                        continue
                    _seg = _avwap_series.iloc[_seg_offset_in_series:]
                    # X ekseni: dates array içindeki karşılık gelen indeksler
                    _seg_x = dates[_draw_from - _plot_start_idx_in_full :]
                    if len(_seg) != len(_seg_x):
                        # Hizalama güvenliği — kısa olanı al
                        _min_len = min(len(_seg), len(_seg_x))
                        _seg = _seg.iloc[-_min_len:]
                        _seg_x = _seg_x[-_min_len:]
                    if len(_seg) < 2:
                        continue

                    _avwap_fmt = f"{int(_avwap_cur_val):,}" if _avwap_cur_val >= 1000 else f"{_avwap_cur_val:.2f}"
                    fig.add_trace(go.Scatter(
                        x=_seg_x, y=_seg.values.astype(float),
                        name=f'aVWAP {_label}: {_avwap_fmt}',
                        line=dict(color=_color, width=1.6, dash='dot'),
                        opacity=0.85,
                        hoverinfo='skip',
                    ), row=1, col=1)
                    # Anchor başlangıç noktasında küçük marker
                    if _draw_from == _anchor_idx:
                        _anchor_price = float(df_full.iloc[_anchor_idx]['High' if _key == 'high' else 'Low'])
                        fig.add_trace(go.Scatter(
                            x=[_seg_x[0]], y=[_anchor_price],
                            mode='markers',
                            marker=dict(size=9, color=_color, symbol='triangle-up' if _key == 'low' else 'triangle-down',
                                        line=dict(color='white', width=1)),
                            name=f'⚓ {_label}',
                            hoverinfo='skip',
                            showlegend=False,
                        ), row=1, col=1)
                    # Sağ kenar etiketi
                    annotations.append(dict(
                        x=dates[-1], y=_avwap_cur_val, xref='x', yref='y',
                        text=f"<b>aVWAP{'↑' if _key=='low' else '↓'} {_avwap_fmt}</b>",
                        showarrow=False, xanchor='left', yanchor='middle',
                        font=dict(size=10, color=_color, family='monospace'),
                        bgcolor='rgba(0,0,0,0.55)', borderpad=2,
                    ))
                    _avwap_summary[_key] = (_avwap_cur_val,
                                            (_cur_price - _avwap_cur_val) / _avwap_cur_val * 100.0)
        except Exception:
            _avwap_summary = {}

        # ── 4. YAPISAL ÖZET — grafik dışında st.markdown ile gösterilecek ──
        # Hesapla, fig ile birlikte döndür
        _smc_summary = {}
        try:
            if sh is not None and sl is not None and sh > sl:
                _rng     = sh - sl
                _disc_hi = sl + _rng * 0.30
                _prem_lo = sh - _rng * 0.30
                _eq_lo   = sl + _rng * 0.45
                _eq_hi   = sl + _rng * 0.55
                if _cur_price >= _prem_lo:
                    _smc_summary['zone'] = ('PREMIUM', '#ef4444', '⚠️ dikkat')
                elif _cur_price <= _disc_hi:
                    _smc_summary['zone'] = ('DISCOUNT', '#26a69a', '✅ fırsat')
                elif _eq_lo <= _cur_price <= _eq_hi:
                    _smc_summary['zone'] = ('EQ', '#a78bfa', '➖ denge')
                else:
                    _smc_summary['zone'] = ('Geçiş', '#94a3b8', '')

            if _poc_val:
                _poc_dist = (_cur_price - _poc_val) / _poc_val * 100
                _smc_summary['poc'] = (_poc_val, _poc_dist)

            # Multi-TF POC stack özeti (AI prompt + GENEL ÖZET için)
            if _poc_mtf:
                _smc_summary['poc_mtf'] = {
                    'poc_20':  _poc_mtf.get('poc_20'),
                    'poc_60':  _poc_mtf.get('poc_60'),
                    'poc_250': _poc_mtf.get('poc_250'),
                    'spread_pct': _poc_mtf.get('spread_pct'),
                    'confluence': bool(_poc_mtf.get('confluence')),
                    'confluence_mid': _poc_mtf.get('confluence_mid'),
                    'dist': _poc_mtf.get('dist_from_price_pct', {}),
                }

            if _vwap_cur:
                _vwap_dist = (_cur_price - _vwap_cur) / _vwap_cur * 100
                _smc_summary['vwap'] = (_vwap_cur, _vwap_dist)

            # Event-anchored VWAP özeti
            if _avwap_summary:
                _smc_summary['avwap'] = _avwap_summary

            if smc['bos_lines']:
                _bos_s = sorted(smc['bos_lines'], key=lambda x: abs(x[1] - _cur_price))
                _smc_summary['bos'] = (_bos_s[0][2], _bos_s[0][1],
                                       (_cur_price - _bos_s[0][1]) / _bos_s[0][1] * 100)

            # State'li FVG'leri tuple-tolerant unpack et (eski 3-arity, yeni 4-arity)
            def _fvg_unpack(t):
                if len(t) == 4: return (t[0], t[1], t[2], t[3])
                return (t[0], t[1], t[2], 'fresh')
            _all_fvgs = [(p_lo, p_hi, 'bull' if _st != 'inverted' else 'bear_inverted', _st)
                         for (_, p_lo, p_hi, _st) in (_fvg_unpack(t) for t in smc['fvg_bull'])] + \
                        [(p_lo, p_hi, 'bear' if _st != 'inverted' else 'bull_inverted', _st)
                         for (_, p_lo, p_hi, _st) in (_fvg_unpack(t) for t in smc['fvg_bear'])]
            if _all_fvgs:
                _fvg_s = sorted(_all_fvgs, key=lambda x: abs((x[0]+x[1])/2 - _cur_price))
                _flo, _fhi, _fdir, _fst = _fvg_s[0]
                _fmid = (_flo + _fhi) / 2
                # AI prompt için 'inverted' bilgisi de geçer (eski 3-tuple yapısı korunur)
                _smc_summary['fvg'] = (_fdir, _fmid, (_cur_price - _fmid) / _fmid * 100)
                _smc_summary['fvg_state'] = _fst
        except Exception:
            pass

        # ── SAĞ KENAR ETİKETLERİ — çakışma önleme (9 Haz 2026 Oturum 20) ──
        # POC_MTF (3) + nPOC (3) + VWAP + aVWAP↑ + aVWAP↓ → 9 etikete kadar.
        # Y değerleri yakınsa üst üste binip okunmaz oluyor (SMC grafiği).
        # Çözüm: y'ye göre sırala, min_gap'ten az yaklaşanları kaydır + arrow ekle
        # (arrow gerçek price seviyesini gösterir, label staggered y'de oturur).
        try:
            _x_last_ann = dates[-1] if len(dates) else None
            if _x_last_ann is not None:
                _y_hi_chart = float(df['High'].max())
                _y_lo_chart = float(df['Low'].min())
                _y_range = _y_hi_chart - _y_lo_chart
                if _y_range > 0:
                    _min_gap = _y_range * 0.038  # %3.8 görünür y aralığı
                    # Sağ kenar etiketlerini ayır
                    _edge_ann = []
                    for _ann in annotations:
                        try:
                            if (_ann.get('x') == _x_last_ann
                                and _ann.get('xanchor') == 'left'
                                and not _ann.get('showarrow')):
                                _edge_ann.append(_ann)
                        except Exception:
                            continue
                    # Yukarıdan aşağıya sırala, kaydır
                    _edge_ann.sort(key=lambda a: -float(a.get('y', 0)))
                    _prev_y = None
                    for _ann in _edge_ann:
                        _ty = float(_ann.get('y', 0))
                        if _prev_y is None:
                            _placed_y = _ty
                        else:
                            _placed_y = min(_ty, _prev_y - _min_gap)
                        if abs(_placed_y - _ty) > _min_gap * 0.1:
                            # Çakışma — label kaydırıldı, ince arrow ekle
                            _ann['showarrow']  = True
                            _ann['arrowhead']  = 2
                            _ann['arrowsize']  = 0.7
                            _ann['arrowwidth'] = 0.9
                            _ann['arrowcolor'] = _ann.get('font', {}).get('color', '#94a3b8')
                            _ann['standoff']   = 1
                            # Arrow tip = orijinal y (gerçek seviye), tail+label = staggered y
                            _ann['ax']    = _x_last_ann
                            _ann['ay']    = _placed_y
                            _ann['axref'] = 'x'
                            _ann['ayref'] = 'y'
                            # x stays = arrow head (orijinal y)
                        _prev_y = _placed_y
        except Exception:
            pass

        fig.update_layout(
            shapes=shapes,
            annotations=annotations,
            paper_bgcolor=paper_bg,
            plot_bgcolor=bg,
            font=dict(color=fg, size=10, family="JetBrains Mono, monospace"),
            margin=dict(l=8, r=68, t=46, b=4),
            height=490,
            title=dict(
                text=(
                    f"<b style='color:{'#e2e8f0' if dark_mode else '#1e293b'};font-size:13px;'>{disp}</b>"
                    f"  <span style='color:#475569;font-size:10px;'>· SMC · Son {n}G</span>"
                    f"  &nbsp;<span style='color:#ef5350; font-size:10px;'>── {_l50}</span>"
                    f"  <span style='color:#2196F3; font-size:10px;'>── {_l100}</span>"
                    f"  <span style='color:#a78bfa; font-size:10px;'>── {_l144}</span>"
                    f"  <span style='color:#fb923c; font-size:10px;'>── {_l200}</span>"
                ),
                font=dict(size=11, color=fg), x=0.01, xanchor='left'),
            showlegend=False,
            xaxis_rangeslider_visible=False,
            hovermode='x unified',
            dragmode='pan',
        )
        # Hafta sonu + tatil günü boşluklarını kapat
        # Kriptolar 7/24 işlem görür — hafta sonu rangebreak uygulanmaz
        import pandas as _pd
        _is_crypto = (symbol.endswith('-USD') or symbol.endswith('USDT')
                      or symbol.endswith('-TRY') or symbol in ('BTC-USD','ETH-USD','BNB-USD'))
        if _is_crypto:
            _rangebreaks = []
        else:
            _all_bdays = _pd.date_range(start=dates[0], end=dates[-1], freq='B')
            _missing   = _all_bdays.difference(dates).tolist()
            _rangebreaks = [dict(bounds=['sat', 'mon'])]
            if _missing:
                _rangebreaks.append(dict(values=_missing))

        for row in [1, 2]:
            fig.update_xaxes(
                row=row, col=1,
                gridcolor=grid, showgrid=True,
                zeroline=False,
                showspikes=True, spikecolor=fg,
                spikedash='dot', spikethickness=1,
                rangebreaks=_rangebreaks,
                tickformat="%d %b",
            )
        fig.update_yaxes(
            row=1, col=1,
            gridcolor=grid, showgrid=True,
            zeroline=False, side='right',
            tickformat=_pfmt,
        )
        fig.update_yaxes(
            row=2, col=1,
            gridcolor=grid, showgrid=True,
            zeroline=False, side='right',
            tickformat='.2s',
        )
        return fig, _smc_summary
    except Exception as _e:
        import traceback; traceback.print_exc()
        return str(_e), {}   # hata mesajını döndür, None değil
