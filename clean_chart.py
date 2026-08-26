#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Temiz mum grafiği — mumlar + SMA50/EMA144/SMA100/SMA200 + POC + VWAP. Gürültü YOK.
SVG üretir (infografik sağ-üst için). Kullanıcı spec'i: sade, sadece bu 6 katman."""
import os, sys
import numpy as np, pandas as pd
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
BASE = os.path.dirname(os.path.abspath(__file__)); VER = os.path.join(BASE, 'veriler')
BG='#0d1623'; TXT='#e6edf6'; MUT='#8aa0bb'; UP='#2ec177'; DN='#f0556a'; LINE='#1e2c40'

def split_adj(df):
    df=df.copy().sort_index(); pc=['Open','High','Low','Close']
    for _ in range(10):
        cl=df['Close'].ffill().values; f=False
        for i in range(1,len(cl)):
            if cl[i-1]<=0 or cl[i]<=0: continue
            r=cl[i-1]/cl[i]
            if r>=1.20:
                for col in pc: df.iloc[:i,df.columns.get_loc(col)]=df.iloc[:i][col].values/r
                f=True;break
        if not f: break
    return df

def poc(seg):
    pr=((seg['High']+seg['Low'])/2).values; vo=seg['Volume'].values
    hist,edges=np.histogram(pr,bins=24,weights=vo)
    i=int(np.argmax(hist)); return (edges[i]+edges[i+1])/2

def chart_svg(ticker, W=460, H=320, nbar=70):
    ct=ticker if ticker.endswith('.IS') else f'{ticker}.IS'
    df=None
    for c in (f'{VER}/{ct}_1d.parquet', f'{VER}/{ticker}_1d.parquet'):
        if os.path.exists(c): df=split_adj(pd.read_parquet(c)); break
    if df is None or len(df)<60: return None
    full=df['Close']
    mas={'SMA 50':full.rolling(50,min_periods=1).mean(),'EMA 144':full.ewm(span=144,adjust=False).mean(),
         'SMA 100':full.rolling(100,min_periods=1).mean(),'SMA 200':full.rolling(200,min_periods=1).mean()}
    seg=df.tail(nbar); o=seg['Open'];h=seg['High'];l=seg['Low'];c=seg['Close'];v=seg['Volume']
    n=len(seg)
    vwap=(((seg['High']+seg['Low']+seg['Close'])/3*v).cumsum()/v.cumsum())
    p=poc(seg)
    X0,X1,Y0,Y1=6,W-46,8,H-8
    lo=min(l.min(),vwap.min(),p)*0.995; hi=max(h.max(),vwap.max(),p)*1.005
    xs=lambda i:X0+i*((X1-X0)/(n-1)); ys=lambda pr:Y1-(pr-lo)/(hi-lo)*(Y1-Y0)
    s=[f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" style="background:{BG};border-radius:8px;">']
    # POC zonu (yatay) + VWAP çizgisi
    s.append(f'<line x1="{X0}" y1="{ys(p):.1f}" x2="{X1}" y2="{ys(p):.1f}" stroke="#e0a72e" stroke-width="1.2" stroke-dasharray="5 3" opacity="0.9"/>')
    s.append(f'<text x="{X1+3}" y="{ys(p)+3:.1f}" font-size="9" fill="#e0a72e">POC {p:.1f}</text>')
    vpts=' '.join(f'{xs(i):.1f},{ys(vwap.iloc[i]):.1f}' for i in range(n))
    s.append(f'<polyline points="{vpts}" fill="none" stroke="#1dc6c6" stroke-width="1.3" stroke-dasharray="3 2" opacity="0.85"/>')
    s.append(f'<text x="{X1+3}" y="{ys(vwap.iloc[-1])+3:.1f}" font-size="9" fill="#1dc6c6">VWAP</text>')
    # mumlar
    cw=((X1-X0)/(n-1))*0.62
    for i in range(n):
        up=c.iloc[i]>=o.iloc[i]; col=UP if up else DN; x=xs(i)
        s.append(f'<line x1="{x:.1f}" y1="{ys(h.iloc[i]):.1f}" x2="{x:.1f}" y2="{ys(l.iloc[i]):.1f}" stroke="{col}" stroke-width="0.9"/>')
        yt=ys(max(o.iloc[i],c.iloc[i]));yb=ys(min(o.iloc[i],c.iloc[i]))
        s.append(f'<rect x="{x-cw/2:.1f}" y="{yt:.1f}" width="{cw:.1f}" height="{max(1.2,yb-yt):.1f}" fill="{col}" rx="0.8"/>')
    # MA çizgileri (ince, sağda etiket)
    macol={'SMA 50':'#e8e8e8','EMA 144':'#a855f7','SMA 100':'#4aa3ff','SMA 200':'#f59e0b'}
    for nm,ser in mas.items():
        sm=ser.tail(n)
        pts=' '.join(f'{xs(i):.1f},{ys(sm.iloc[i]):.1f}' for i in range(n))
        s.append(f'<polyline points="{pts}" fill="none" stroke="{macol[nm]}" stroke-width="1.3" opacity="0.9"/>')
        s.append(f'<text x="{X1+3}" y="{ys(sm.iloc[-1])+3:.1f}" font-size="8.5" fill="{macol[nm]}">{nm.split()[0][0]}{nm.split()[1]}</text>')
    s.append('</svg>')
    return ''.join(s)

if __name__=='__main__':
    tk=sys.argv[1] if len(sys.argv)>1 else 'SAHOL'
    svg=chart_svg(tk)
    if svg:
        open(os.path.join(BASE,f'clean_chart_{tk}.svg'),'w',encoding='utf-8').write(svg)
        print(f'✅ clean_chart_{tk}.svg ({len(svg)} bytes)')
    else:
        print('veri yok')
