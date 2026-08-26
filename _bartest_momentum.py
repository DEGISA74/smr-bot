# -*- coding: utf-8 -*-
"""
_bartest_momentum.py — Para Akış İvmesi bar formülü denemesi (app.py'ye DOKUNMAZ)
4 örnek hisse (EREGL, SISE, TUPRS, AKBNK) için 4 varyantı yan yana çizer:
  MEVCUT : (TP - DEMA6)/DEMA6 * 1000        -> konum (bugünkü panel)
  S1     : STP(EMA6) günlük eğimi           -> ivme (referans mantığı)
  S2     : STP 3 günlük eğimi               -> yumuşatılmış ivme (blok görünüm)
  S3     : Kompozit 0-100 puan (RSI+MFI+20g konum, EMA3) günlük değişimi
Çıktı: _bartest_<TICKER>.png (koyu tema, panelle aynı renkler) + konsol M/K dizilimi
"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

TICKERS = ["EREGL", "SISE", "TUPRS", "AKBNK"]
N = 31          # panelde ~30 bar var
BLUE, RED, LINE, BG, FG = "#5B84C4", "#ef4444", "#bfdbfe", "#0d1526", "#94a3b8"


def rsi14(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def mfi14(high, low, close, vol, n=14):
    tp = (high + low + close) / 3
    mf = tp * vol
    pos = mf.where(tp > tp.shift(), 0.0).rolling(n).sum()
    neg = mf.where(tp < tp.shift(), 0.0).rolling(n).sum()
    r = pos / neg.replace(0, np.nan)
    return (100 - 100 / (1 + r)).fillna(50)


def compute_variants(df):
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    tp = (h + l + c) / 3
    ema1 = tp.ewm(span=6, adjust=False).mean()          # panelin sarı çizgisi (STP)
    ema2 = ema1.ewm(span=6, adjust=False).mean()
    dema6 = 2 * ema1 - ema2

    mevcut = (tp - dema6) / dema6 * 1000                # bugünkü bar
    s1 = ema1.diff() / c * 1000                         # STP günlük eğimi (binde)
    s2 = ema1.diff(3) / c * 1000                        # STP 3 günlük eğimi
    puan = (rsi14(c) + mfi14(h, l, c, v) + (
        (c - c.rolling(20).min()) /
        (c.rolling(20).max() - c.rolling(20).min()).replace(0, np.nan) * 100
    ).fillna(50)) / 3
    puan = puan.ewm(span=3, adjust=False).mean()        # yumuşatılmış 0-100 puan
    s3 = puan.diff()                                    # puanın günlük değişimi
    return {"MEVCUT (konum)": mevcut, "S1: STP eğimi (günlük)": s1,
            "S2: STP eğimi (3 gün)": s2, "S3: Kompozit puan değişimi": s3}, c


def seq(s):
    return "".join("M" if x > 0 else "K" for x in s)


def flips(s):
    q = seq(s)
    return sum(1 for a, b in zip(q, q[1:]) if a != b)


for t in TICKERS:
    df = pd.read_parquet(f"veriler/{t}.IS_1d.parquet")
    variants, close = compute_variants(df)
    tail_close = close.tail(N)
    dates = [d.strftime("%d %b") for d in tail_close.index]

    fig, axes = plt.subplots(4, 1, figsize=(13, 11), sharex=True)
    fig.patch.set_facecolor(BG)
    print(f"\n=== {t} ===")
    for ax, (name, series) in zip(axes, variants.items()):
        s = series.tail(N)
        colors = [BLUE if x > 0 else RED for x in s]
        ax.bar(range(N), s.values, color=colors, width=0.55, zorder=2)
        ax.axhline(0, color="#334155", lw=0.8)
        ax2 = ax.twinx()
        ax2.plot(range(N), tail_close.values, color=LINE, lw=1.6, zorder=3)
        ax2.set_yticks([])
        for a in (ax, ax2):
            a.set_facecolor(BG)
            for sp in a.spines.values():
                sp.set_visible(False)
        ax.tick_params(colors=FG, labelsize=8)
        ax.set_yticks([])
        ax.set_title(name, color="#38bdf8", fontsize=11, loc="left")
        print(f"{name:28s} dizi: {seq(s)}  (renk değişimi: {flips(s)})")
    axes[-1].set_xticks(range(N))
    axes[-1].set_xticklabels(dates, rotation=45, ha="right", color=FG)
    fig.suptitle(f"{t} — bar formülü denemeleri (beyaz çizgi = fiyat)",
                 color="#e2e8f0", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    out = f"_bartest_{t}.png"
    fig.savefig(out, dpi=110, facecolor=BG)
    plt.close(fig)
    print(f"kaydedildi: {out}")
