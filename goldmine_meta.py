# -*- coding: utf-8 -*-
"""
goldmine_meta.py — GOLD MINE VİTRİN META-BACKTEST (C adımı, 30 Haz 2026)
=======================================================================
Soru: GOLD MINE vitrini (goldmine_log, her gün top-20 rank'lı seçim) İŞE YARIYOR MU?
  1. Vitrin seçimleri baseline'ı (o günkü tüm-evren ort.) yeniyor mu?
  2. RANK getiriyi öngörüyor mu (rank 1-5 > rank 11-20)?
  3. Hangi scanner'ın vitrin seçimleri iyi/kötü?

İleri getiri parquet'ten hesaplanır (D günü close → +N işlem günü close). universe_snapshot
Mayıs'ta bittiği için (goldmine tarihleri sonrası) doğrudan fiyattan ölçüyoruz.

⚠️ Örneklem küçük + taze (6 gün, çoğu sadece 5g olgun) → PRELİMİNER. Yargı ~15 Tem.

Kullanım: python goldmine_meta.py
"""
import sqlite3, sys, os, glob
import pandas as pd
import numpy as np
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

DB = os.environ.get("SMR_EDGE_DB", "patron.db")
VERILER = "veriler"

# parquet close serilerini cache'le
_CACHE = {}
def _closes(sym):
    sym = sym.replace(".IS", "")
    if sym in _CACHE:
        return _CACHE[sym]
    fp = os.path.join(VERILER, f"{sym}.IS_1d.parquet")
    if not os.path.exists(fp):
        _CACHE[sym] = None; return None
    try:
        df = pd.read_parquet(fp)
        s = df["Close"].copy()
        s.index = [str(x)[:10] for x in s.index]
        _CACHE[sym] = s
    except Exception:
        _CACHE[sym] = None
    return _CACHE[sym]

def fwd_ret(sym, date, n):
    """D günü close → +n işlem günü close getiri (%). Olgunlaşmadıysa None."""
    s = _closes(sym)
    if s is None or date not in s.index:
        return None
    i = s.index.get_loc(date)
    if isinstance(i, slice) or i + n >= len(s):
        return None
    c0 = float(s.iloc[i]); c1 = float(s.iloc[i + n])
    return (c1 / c0 - 1) * 100 if c0 > 0 else None


def market_baseline(date, n, universe):
    """O gün, tüm evren ort. n-günlük getiri (baseline)."""
    rets = [fwd_ret(sym, date, n) for sym in universe]
    rets = [r for r in rets if r is not None]
    return (np.mean(rets), len(rets)) if rets else (None, 0)


def main():
    con = sqlite3.connect(DB, timeout=10)
    gm = pd.read_sql("SELECT scan_date, symbol, rank, score, scanner FROM goldmine_log", con)
    con.close()
    universe = [os.path.basename(f).replace(".IS_1d.parquet", "")
                for f in glob.glob(f"{VERILER}/*.IS_1d.parquet")]

    # her goldmine satırına 5g + 10g getiri
    gm["ret5"] = [fwd_ret(r.symbol, r.scan_date, 5) for r in gm.itertuples()]
    gm["ret10"] = [fwd_ret(r.symbol, r.scan_date, 10) for r in gm.itertuples()]

    dates = sorted(gm["scan_date"].unique())
    out = ["# 🏆 GOLD MINE VİTRİN META-BACKTEST",
           f"_goldmine_log: {len(gm)} seçim · {len(dates)} gün ({dates[0]}..{dates[-1]})_\n",
           "⚠️ Örneklem küçük + taze — PRELİMİNER, yargı ~15 Tem 2026.\n"]

    # 1) Vitrin vs baseline
    out.append("## 1) Vitrin seçimleri baseline'ı (tüm-evren ort.) yeniyor mu?\n")
    out.append("| Ufuk | Vitrin ort | dolu | Baseline ort | Fark (alfa) |")
    out.append("|---|--:|--:|--:|--:|")
    for lbl, col, n in [("5 gün", "ret5", 5), ("10 gün", "ret10", 10)]:
        gv = gm[col].dropna()
        # baseline: aynı günlerin evren ortalaması (vitrin dolu olan günler)
        bdays = gm.loc[gm[col].notna(), "scan_date"].unique()
        bvals = []
        for d in bdays:
            mb, _ = market_baseline(d, n, universe)
            if mb is not None: bvals.append(mb)
        base = np.mean(bvals) if bvals else None
        vm = gv.mean() if len(gv) else None
        alpha = (vm - base) if (vm is not None and base is not None) else None
        out.append(f"| {lbl} | {vm:+.2f} | {len(gv)} | "
                   f"{base:+.2f} | **{alpha:+.2f}** |" if vm is not None and base is not None
                   else f"| {lbl} | — | {len(gv)} | — | — |")

    # 2) Rank getiriyi öngörüyor mu?
    out.append("\n## 2) RANK getiriyi öngörüyor mu? (rank kovaları, 5g)\n")
    gm["rank_kova"] = pd.cut(gm["rank"], [0, 5, 10, 20],
                             labels=["1-5 (üst)", "6-10 (orta)", "11-20 (alt)"])
    out.append("| Rank kova | n(5g) | ort 5g | ort 10g |")
    out.append("|---|--:|--:|--:|")
    for k in ["1-5 (üst)", "6-10 (orta)", "11-20 (alt)"]:
        sub = gm[gm["rank_kova"] == k]
        r5 = sub["ret5"].dropna(); r10 = sub["ret10"].dropna()
        out.append(f"| {k} | {len(r5)} | {r5.mean():+.2f} | "
                   f"{(r10.mean() if len(r10) else float('nan')):+.2f} |" if len(r5) else f"| {k} | 0 | — | — |")

    # 3) Scanner bazında
    out.append("\n## 3) Hangi scanner'ın vitrin seçimleri iyi? (5g, n≥3)\n")
    out.append("| Scanner | n | ort 5g |")
    out.append("|---|--:|--:|")
    g = gm.dropna(subset=["ret5"]).groupby("scanner")["ret5"].agg(["count", "mean"])
    g = g[g["count"] >= 3].sort_values("mean", ascending=False)
    for scn, row in g.iterrows():
        out.append(f"| {scn} | {int(row['count'])} | {row['mean']:+.2f} |")

    report = "\n".join(out)
    open("goldmine_meta_report.md", "w", encoding="utf-8").write(report)
    print(report)


if __name__ == "__main__":
    main()
