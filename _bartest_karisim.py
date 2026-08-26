# -*- coding: utf-8 -*-
"""
_bartest_karisim.py — S1 (STP eğimi) + S3 (kompozit puan değişimi) KARIŞIM denemeleri.
app.py'ye DOKUNMAZ. Çıktı: _bartest_MIX_<TICKER>.png + konsol M/K dizilimleri.
Karışım öncesi iki seri kendi 90 günlük oynaklığına bölünür (ortak ölçek),
sonra ağırlıklı ortalama alınır.
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
N = 31
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


def norm(s):
    """Seriyi kendi 90 günlük oynaklığına böl → ortak ölçek."""
    sd = s.tail(90).std()
    return s / sd if sd and not np.isnan(sd) else s


def compute(df):
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    tp = (h + l + c) / 3
    ema1 = tp.ewm(span=6, adjust=False).mean()          # STP (sarı çizgi)

    s1 = ema1.diff() / c * 1000                         # STP günlük eğimi
    puan = (rsi14(c) + mfi14(h, l, c, v) + (
        (c - c.rolling(20).min()) /
        (c.rolling(20).max() - c.rolling(20).min()).replace(0, np.nan) * 100
    ).fillna(50)) / 3
    puan = puan.ewm(span=3, adjust=False).mean()
    s3 = puan.diff()                                    # kompozit puan değişimi
    s3s = s3.ewm(span=3, adjust=False).mean()           # yumuşatılmış s3

    n1, n3, n3s = norm(s1), norm(s3), norm(s3s)
    return {
        "S1: STP eğimi (saf)":                   n1,
        "S3: Kompozit puan değişimi (yumuşak)":  n3s,
        "%50 S1 + %50 S3":                       0.5 * n1 + 0.5 * n3s,
        "%40 S1 + %60 S3":                       0.4 * n1 + 0.6 * n3s,
        "%30 S1 + %70 S3":                       0.3 * n1 + 0.7 * n3s,
    }, c


def seq(s):
    return "".join("M" if x > 0 else "K" for x in s)


def flips(s):
    q = seq(s)
    return sum(1 for a, b in zip(q, q[1:]) if a != b)


for t in TICKERS:
    df = pd.read_parquet(f"veriler/{t}.IS_1d.parquet")
    variants, close = compute(df)
    tail_close = close.tail(N)
    dates = [d.strftime("%d %b") for d in tail_close.index]

    fig, axes = plt.subplots(len(variants), 1, figsize=(13, 13.5), sharex=True)
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
        print(f"{name:36s} dizi: {seq(s)}  (renk değişimi: {flips(s)})")
    axes[-1].set_xticks(range(N))
    axes[-1].set_xticklabels(dates, rotation=45, ha="right", color=FG)
    fig.suptitle(f"{t} — S1+S3 karışım denemeleri (beyaz çizgi = fiyat)",
                 color="#e2e8f0", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.975])
    out = f"_bartest_MIX2_{t}.png"
    fig.savefig(out, dpi=110, facecolor=BG)
    plt.close(fig)
    print(f"kaydedildi: {out}")
