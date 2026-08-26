# -*- coding: utf-8 -*-
"""İDEAL VADE taraması — her taramanın hangi tutma süresinde (2–20g) en iyi
beklentiyi verdiğini bulur. Parquet replay (gerçek kurallar), iki rejim ayrı.
Kullanım: python _vade_sweep.py --d0 2026-01-01 --d1 2026-02-18 --etiket BOGA"""
import sys, os, glob, json, argparse
from datetime import datetime
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import numpy as np, pandas as pd
import scanners

BASE = os.path.dirname(os.path.abspath(__file__)); VERI = os.path.join(BASE, "veriler")
IDX_PREFIX = ("XU", "XB", "XT", "XY", "XG", "XK", "XL", "XM", "XS", "XW", "X0")
SCEN = scanners.ERKEN_RADAR_SCENARIOS
HORIZONS = [2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20]
BEAR = {"D4_orig", "D5_orig"}
# odaklanacağımız çekirdek + tümü; çekirdek başta gösterilir
CORE = ["D5_ayna", "zirve_devam", "zirve_sikisma", "er_B11", "er_C8", "er_C5", "D4_orig", "D5_orig"]


def universe(top, days=180):
    liq = []
    for f in sorted(glob.glob(os.path.join(VERI, "*.IS_1d.parquet"))):
        if os.path.basename(f).split(".")[0].startswith(IDX_PREFIX):
            continue
        try:
            df = pd.read_parquet(f, columns=["Close", "Volume"])
            if len(df) < 200:
                continue
            t = float((df.tail(days)["Close"] * df.tail(days)["Volume"]).mean())
            if np.isfinite(t) and t > 0:
                liq.append((t, f))
        except Exception:
            continue
    liq.sort(reverse=True)
    return [f for _, f in liq[:top]]


def zirve_D_series(df, xu):
    c = df["Close"].astype(float); o = df["Open"].astype(float); v = df["Volume"].astype(float)
    dip = c.rolling(250).min(); tepe = c.rolling(250).max()
    konum = (c - dip) / (tepe - dip).replace(0, np.nan) * 100
    sma50 = c.rolling(50).mean(); ust = c > sma50
    bant = (c.rolling(20).max() - c.rolling(20).min()) / c
    daralma = bant / bant.rolling(100).mean().replace(0, np.nan)
    av20 = v.rolling(20).mean()
    dist = (c < o) & (v > av20 * 1.5); accu = (c > o) & (v > av20 * 1.5)
    xa = xu.reindex(df.index).ffill()
    def rs(d): return ((c / c.shift(d) - 1) - (xa / xa.shift(d) - 1)) * 100
    rs60 = rs(60)
    return {"zirve_devam": (konum >= 95) & ust,
            "zirve_sikisma": (konum >= 80) & ust & (daralma <= 0.70),
            "D4_orig": (dist.rolling(5).sum() >= 2), "D5_orig": (~ust) & dist & (rs60 < -5),
            "D4_ayna": (accu.rolling(5).sum() >= 2), "D5_ayna": ust & accu & (rs60 > 5)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--d0", required=True); ap.add_argument("--d1", required=True)
    ap.add_argument("--etiket", default="DONEM"); ap.add_argument("--top", type=int, default=150)
    a = ap.parse_args(); D0, D1 = a.d0, a.d1
    t0 = datetime.now()
    xu = pd.read_parquet(os.path.join(VERI, "XU100.IS_1d.parquet"))["Close"].astype(float)
    xud = [str(d)[:10] for d in xu.index]; xpos = {d: k for k, d in enumerate(xud)}
    files = universe(a.top)
    i0 = next((k for k, d in enumerate(xud) if d >= D0), None)
    i1 = next((k for k in range(len(xud) - 1, -1, -1) if xud[k] <= D1), None)
    xu_ret = (xu.iloc[i1] / xu.iloc[i0] - 1) * 100 if (i0 and i1) else 0
    print(f"=== VADE SWEEP [{a.etiket}] {D0}→{D1} — BIST{len(files)} — XU100 {xu_ret:+.1f}% ===")

    from collections import defaultdict
    REC = defaultdict(list)  # st -> list of (rets dict by horizon, xrets dict)
    done = 0
    for f in files:
        try:
            df = pd.read_parquet(f)
            if len(df) < 200:
                continue
            dts = [str(d)[:10] for d in df.index]; c = df["Close"].astype(float).values; n = len(df)
            w0 = next((k for k, d in enumerate(dts) if d >= D0), None)
            w1 = next((k for k in range(n - 1, -1, -1) if dts[k] <= D1), None)
            if w0 is None or w1 is None or w0 < 61:
                continue
            ser = zirve_D_series(df, xu)
            prev = set()
            for i in range(w0 - 1, w1 + 1):
                sub = df.iloc[:i + 1]; xi = xpos.get(dts[i])
                bsub = xu.iloc[:xi + 1].to_frame("Close") if xi is not None else None
                ctx = scanners._er_build_context(sub, bsub)
                cur = set()
                if ctx is not None:
                    for sid, sc in SCEN.items():
                        try:
                            if sc["detect"](ctx):
                                cur.add("er_" + sid)
                        except Exception:
                            pass
                if i >= w0 and i + max(HORIZONS) < n:
                    trig = list(cur - prev)
                    for st, s in ser.items():
                        try:
                            if bool(s.iloc[i]) and not bool(s.iloc[i - 1]):
                                trig.append(st)
                        except Exception:
                            pass
                    if trig:
                        rets = {h: (c[i + h] / c[i] - 1) * 100 for h in HORIZONS if c[i] > 0}
                        xp = xpos.get(dts[i])
                        xr = {h: ((xu.iloc[xp + h] / xu.iloc[xp] - 1) * 100) if (xp is not None and xp + h < len(xu)) else None for h in HORIZONS}
                        for st in trig:
                            REC[st].append((rets, xr))
                prev = cur
        except Exception as ex:
            print(f"  ! {os.path.basename(f)}: {ex}")
        done += 1
        if done % 30 == 0:
            print(f"  ... {done}/{len(files)} ({(datetime.now()-t0).seconds}sn)")

    def horizon_stat(rows, h, bear):
        vals = [(r[h], x[h]) for r, x in rows if h in r and np.isfinite(r[h])]
        if len(vals) < 8:
            return None
        rets = np.array([a for a, _ in vals]); dret = -rets if bear else rets
        al = [((a - b) if not bear else (b - a)) for a, b in vals if b is not None]
        return dict(n=len(rets), exp=float(dret.mean()), hit=float(np.mean(dret > 0) * 100),
                    alpha=(float(np.mean(al)) if al else None))

    out = {}
    order = CORE + [s for s in REC if s not in CORE]
    print(f"\n{'TARAMA':<15}{'N':>5}  en iyi vade → beklenti/isabet/BIST   | 20g referans")
    for st in order:
        if st not in REC:
            continue
        bear = st in BEAR
        stats = {h: horizon_stat(REC[st], h, bear) for h in HORIZONS}
        stats = {h: s for h, s in stats.items() if s}
        if not stats:
            continue
        best_h = max(stats, key=lambda h: stats[h]["exp"])
        bs = stats[best_h]; r20 = stats.get(20)
        out[st] = {"best_h": best_h, "best": bs, "curve": stats}
        a20 = f"20g {r20['exp']:+.1f}%/%{r20['hit']:.0f}" if r20 else "20g —"
        al = f"BIST{bs['alpha']:+.1f}" if bs["alpha"] is not None else "BIST—"
        mark = " ★" if st in CORE else ""
        print(f"{st:<15}{bs['n']:>5}  {best_h:>2}g → {bs['exp']:+5.1f}%/%{bs['hit']:<4.0f}{al:>9}  | {a20}{mark}")

    json.dump({"etiket": a.etiket, "d0": D0, "d1": D1, "xu_ret": xu_ret, "scanners": out},
              open(os.path.join(BASE, f"_vade_sweep_{a.etiket}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\nJSON: _vade_sweep_{a.etiket}.json  ({(datetime.now()-t0).seconds}sn)")


if __name__ == "__main__":
    main()
