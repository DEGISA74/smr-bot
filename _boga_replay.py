# -*- coding: utf-8 -*-
"""Boğa dönemi (1 Oca–18 Şub 2026) — tüm taramaları parquet'ten yeniden oynat.
DB'de bu dönem yok (sinyaller Mayıs'tan). Gerçek kurallarla nokta-zamanlı replay:
  • 36 Erken Radar senaryosu  (scanners._er_build_context + detect lambdaları)
  • zirve_devam / zirve_sikisma  (zirve_taramalari kuralları)
  • D4/D5 (short) + D4_ayna/D5_ayna (long, aynalar)
Giriş = TAZE tetik. Forward 5/10/20g + XU100 alpha. Yönlü beklentiye göre sırala.
Kullanım: python _boga_replay.py [--top 100]"""
import sys, os, glob, json, argparse
from datetime import datetime
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import numpy as np, pandas as pd
import scanners

BASE = os.path.dirname(os.path.abspath(__file__))
VERI = os.path.join(BASE, "veriler")
D0, D1 = "2026-01-01", "2026-02-18"
IDX_PREFIX = ("XU", "XB", "XT", "XY", "XG", "XK", "XL", "XM", "XS", "XW", "X0")
SCEN = scanners.ERKEN_RADAR_SCENARIOS


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
    """Vektörel: zirve_devam, zirve_sikisma, D4/D5, aynalar → bool seri sözlüğü."""
    c = df["Close"].astype(float); o = df["Open"].astype(float); v = df["Volume"].astype(float)
    dip = c.rolling(250).min(); tepe = c.rolling(250).max()
    konum = (c - dip) / (tepe - dip).replace(0, np.nan) * 100
    sma50 = c.rolling(50).mean(); ust = c > sma50
    bant = (c.rolling(20).max() - c.rolling(20).min()) / c
    daralma = bant / bant.rolling(100).mean().replace(0, np.nan)
    av20 = v.rolling(20).mean()
    dist = (c < o) & (v > av20 * 1.5); accu = (c > o) & (v > av20 * 1.5)
    xu_al = xu.reindex(df.index).ffill()
    def rs(d): return ((c / c.shift(d) - 1) - (xu_al / xu_al.shift(d) - 1)) * 100
    rs60 = rs(60)
    return {
        "zirve_devam":   (konum >= 95) & ust,
        "zirve_sikisma": (konum >= 80) & ust & (daralma <= 0.70),
        "D4_orig":  (dist.rolling(5).sum() >= 2),
        "D5_orig":  (~ust) & dist & (rs60 < -5),
        "D4_ayna":  (accu.rolling(5).sum() >= 2),
        "D5_ayna":  ust & accu & (rs60 > 5),
    }


BEAR = {"D4_orig", "D5_orig"}   # short olarak ölçülür


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--top", type=int, default=100)
    args = ap.parse_args()
    t0 = datetime.now()
    xu = pd.read_parquet(os.path.join(VERI, "XU100.IS_1d.parquet"))["Close"].astype(float)
    files = universe(args.top)
    print(f"=== BOĞA REPLAY {D0}→{D1} — BIST{len(files)} — gerçek kurallar ===")
    xu_dates = [str(d)[:10] for d in xu.index]
    i0 = next((k for k, d in enumerate(xu_dates) if d >= D0), None)
    i1 = next((k for k in range(len(xu_dates) - 1, -1, -1) if xu_dates[k] <= D1), None)
    if i0 and i1:
        print(f"XU100 dönem: {(xu.iloc[i1]/xu.iloc[i0]-1)*100:+.1f}%\n")
    xu_dpos = {d: k for k, d in enumerate(xu_dates)}

    from collections import defaultdict
    REC = defaultdict(list)   # scan_type -> list of dict(f5,f10,f20,x20)
    done = 0
    for f in files:
        try:
            df = pd.read_parquet(f)
            if len(df) < 200:
                continue
            dts = [str(d)[:10] for d in df.index]
            c = df["Close"].astype(float).values
            n = len(df)
            w0 = next((k for k, d in enumerate(dts) if d >= D0), None)
            w1 = next((k for k in range(n - 1, -1, -1) if dts[k] <= D1), None)
            if w0 is None or w1 is None or w0 < 61:   # ER/D için 60 bar yeter; zirve içeride NaN→False korunur
                continue
            ser = zirve_D_series(df, xu)
            # forward return helpers
            def fret(i, k):
                j = i + k
                return (c[j] / c[i] - 1) * 100 if j < n and c[i] > 0 and np.isfinite(c[j]) else None
            def xfret(dstr, k):
                p = xu_dpos.get(dstr)
                if p is None or p + k >= len(xu):
                    return None
                a, b = xu.iloc[p], xu.iloc[p + k]
                return (b / a - 1) * 100 if a > 0 else None
            prev_er = set()
            # önceki gün ER (w0-1) baz
            for i in range(w0 - 1, w1 + 1):
                # ER context nokta-zamanlı
                sub = df.iloc[:i + 1]
                xi = xu_dpos.get(dts[i])
                bsub = xu.iloc[:xi + 1].to_frame("Close") if xi is not None else None
                ctx = scanners._er_build_context(sub, bsub)
                cur_er = set()
                if ctx is not None:
                    for sid, sc in SCEN.items():
                        try:
                            if sc["detect"](ctx):
                                cur_er.add("er_" + sid)
                        except Exception:
                            pass
                if i >= w0 and i + 20 < n:
                    dstr = dts[i]
                    triggers = []
                    # ER taze
                    for st in (cur_er - prev_er):
                        triggers.append(st)
                    # zirve + D taze
                    for st, s in ser.items():
                        try:
                            if bool(s.iloc[i]) and not bool(s.iloc[i - 1]):
                                triggers.append(st)
                        except Exception:
                            pass
                    for st in triggers:
                        r5, r10, r20 = fret(i, 5), fret(i, 10), fret(i, 20)
                        if r20 is None:
                            continue
                        REC[st].append({"r5": r5, "r10": r10, "r20": r20, "x20": xfret(dstr, 20)})
                prev_er = cur_er
        except Exception as ex:
            print(f"  ! {os.path.basename(f)}: {ex}")
        done += 1
        if done % 20 == 0:
            print(f"  ... {done}/{len(files)} ({(datetime.now()-t0).seconds}sn)")

    # özet
    rows = []
    for st, rs in REC.items():
        r20 = np.array([x["r20"] for x in rs if x["r20"] is not None])
        if len(r20) < 8:
            continue
        bear = st in BEAR
        dret = -r20 if bear else r20
        wins, losses = dret[dret > 0], dret[dret < 0]
        rr = float(abs(wins.mean() / losses.mean())) if len(wins) and len(losses) else None
        al = [((x["r20"] - x["x20"]) if not bear else (x["x20"] - x["r20"]))
              for x in rs if x["r20"] is not None and x["x20"] is not None]
        rows.append(dict(st=st, side=("SHORT" if bear else "LONG"), n=len(r20),
                         hit=float(np.mean(dret > 0) * 100), exp=float(dret.mean()),
                         rr=rr, alpha=(float(np.mean(al)) if al else None)))
    rows.sort(key=lambda o: o["exp"], reverse=True)
    print(f"\n{'TARAMA':<16}{'yön':>6}{'N':>5}{'isb%':>6}{'20g%':>7}{'R/R':>5}{'BIST':>7}")
    for o in rows:
        rr = f"{o['rr']:.1f}" if o["rr"] else "-"
        al = f"{o['alpha']:+.1f}" if o["alpha"] is not None else "-"
        w = " !" if o["n"] < 25 else ""
        print(f"{o['st']:<16}{o['side']:>6}{o['n']:>5}{o['hit']:>6.0f}{o['exp']:>7.1f}{rr:>5}{al:>7}{w}")
    json.dump(rows, open(os.path.join(BASE, "_boga_replay.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\nJSON: _boga_replay.json  ({(datetime.now()-t0).seconds}sn)")


if __name__ == "__main__":
    main()
