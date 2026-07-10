# -*- coding: utf-8 -*-
"""
_bartest_eksen.py — Momentum bar EKSEN denemesi (app.py'ye DOKUNMAZ).
Aynı barlar (üretimdeki compute_flow_momentum), iki farklı y-ekseni:
  ÜST : MEVCUT — otomatik eksen (en büyük bar paneli doldurur, her pencere 'coşkulu')
  ALT : ÖNERİ  — sabit ±30 (barlar σ×10 ölçeğinde → ±30 = ±3 sigma).
        ±10 (1σ) kılavuz çizgileri; |bar|>30 kırpılır ve ▲/▼ ile işaretlenir.
Çıktı: _bartest_EKSEN_<TICKER>.png + konsol istatistikleri.
"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from indicators import compute_flow_momentum

TICKERS = ["SASA", "ASELS", "TUPRS", "AKBNK"]
N = 31
BLUE, RED, LINE, BG, FG = "#5B84C4", "#ef4444", "#bfdbfe", "#0d1526", "#94a3b8"
LIMIT = 30.0   # ±3 sigma

for t in TICKERS:
    df = pd.read_parquet(f"veriler/{t}.IS_1d.parquet")
    mf, _stp = compute_flow_momentum(df)
    s = mf.tail(N)
    close = df["Close"].tail(N)
    dates = [d.strftime("%d %b") for d in s.index]
    colors = [BLUE if x > 0 else RED for x in s]

    clipped = s.clip(-LIMIT, LIMIT)
    n_clip = int((s.abs() > LIMIT).sum())
    print(f"{t:6s} | max|bar|: {float(s.abs().max()):5.1f} | ort|bar|: {float(s.abs().mean()):5.1f} "
          f"| >30 (kırpılan): {n_clip}/{N}")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 7.5), sharex=True)
    fig.patch.set_facecolor(BG)

    # ── ÜST: mevcut görünüm (otomatik eksen) ─────────────────────────────
    ax1.bar(range(N), s.values, color=colors, width=0.55, zorder=2)
    ax1.axhline(0, color="#334155", lw=0.8)
    ax1.set_title("MEVCUT: otomatik eksen — en büyük bar paneli doldurur",
                  color="#38bdf8", fontsize=11, loc="left")

    # ── ALT: sabit ±30 eksen (±3σ) + 1σ kılavuzları + kırpma işareti ─────
    ax2.bar(range(N), clipped.values, color=colors, width=0.55, zorder=2)
    ax2.axhline(0, color="#334155", lw=0.8)
    for y in (10, -10):
        ax2.axhline(y, color="#334155", lw=0.6, ls=":")
    ax2.axhline(LIMIT, color="#475569", lw=0.7, ls="--")
    ax2.axhline(-LIMIT, color="#475569", lw=0.7, ls="--")
    for i, v in enumerate(s.values):
        if v > LIMIT:
            ax2.annotate("▲", (i, LIMIT), color=BLUE, ha="center", va="bottom", fontsize=8)
        elif v < -LIMIT:
            ax2.annotate("▼", (i, -LIMIT), color=RED, ha="center", va="top", fontsize=8)
    ax2.set_ylim(-LIMIT * 1.15, LIMIT * 1.15)
    ax2.set_title("ÖNERİ: sabit ±30 eksen (±3σ) — sakin gün küçük bar, gerçek olay büyük bar",
                  color="#38bdf8", fontsize=11, loc="left")

    # fiyat çizgisi her ikisine (ikincil eksen)
    for ax in (ax1, ax2):
        axp = ax.twinx()
        axp.plot(range(N), close.values, color=LINE, lw=1.6, zorder=3)
        axp.set_yticks([])
        for a in (ax, axp):
            a.set_facecolor(BG)
            for sp in a.spines.values():
                sp.set_visible(False)
        ax.tick_params(colors=FG, labelsize=8)
        ax.set_yticks([-30, -10, 0, 10, 30] if ax is ax2 else [])

    ax2.set_xticks(range(N))
    ax2.set_xticklabels(dates, rotation=45, ha="right", color=FG)
    fig.suptitle(f"{t} — aynı barlar, iki eksen (beyaz çizgi = fiyat)",
                 color="#e2e8f0", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    out = f"_bartest_EKSEN_{t}.png"
    fig.savefig(out, dpi=110, facecolor=BG)
    plt.close(fig)
    print(f"kaydedildi: {out}")
