# -*- coding: utf-8 -*-
"""PİLOT: formasyon_core (v1) ↔ formasyon_v2 walk-forward karşılaştırması (27 Tem 2026).
Geleceği görmez: her tarih kesitinde motora yalnız o güne kadarki mumlar verilir.
İKİSİ DE AYNI ölçümden geçer (giriş=sinyal barı kapanışı, ileri getiri 5/10/20 bar,
yöne göre işaretli). Tek fark: motorun tespiti. Sinyal anı: v2 KIRILIM_DOĞRULANDI /
core KIRILDI = BREAK; ikisinde YAKIN = NEAR. ⚠ TEK REJİM — yön verir, kesin hüküm değil."""
import sys, glob, os, json, time
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
import numpy as np, pandas as pd
import formasyon_v2 as fv2
import formasyon_core as fc

HOR = (5, 10, 20); STEP = 3; START = 250
BASE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(BASE, "logs", "formasyon_pilot.json")
OUT_MD   = os.path.join(BASE, "logs", "formasyon_pilot.md")
N_TICKERS = 700       # tüm evren (kısa geçmiş → hisse başına ~2 kesit, yine hızlı)
MIN_BARS  = 272       # ≥1 kesit için (START 250 + 20 forward + 1)


def v2_signals(hist, sym):
    try:
        rep = fv2.analyze_formations(hist, ticker=sym, timeframe="1d")
    except Exception:
        return []
    if not rep.data_ok:
        return []
    out = []
    for c in rep.patterns:
        if c.stage in ("KIRILIM_DOĞRULANDI", "KIRILIM_ADAYI", "UZAMIŞ"):
            bucket = "BREAK"
        elif c.stage == "YAKIN":
            bucket = "NEAR"
        else:
            continue
        out.append((c.pattern, c.direction, bucket, c.start_time))
    return out


def core_signals(hist, sym):
    try:
        r = fc.analyze(hist, sym)
    except Exception:
        return []
    if not r:
        return []
    state = r["state"]
    if state == "KIRILDI":
        bucket = "BREAK"
    elif state == "YAKIN":
        bucket = "NEAR"
    else:
        return []
    shape = r["shape"]
    direction = "bearish" if shape == "dtri" else "bullish"
    idx = hist.tail(500).index
    fs = max(0, min(int(r["fs"]), len(idx) - 1))
    return [(shape, direction, bucket, str(idx[fs].date()))]


def _fwd(work, bar, direction):
    entry = float(work["Close"].iloc[bar]); sign = 1.0 if direction == "bullish" else -1.0
    return {h: sign * (float(work["Close"].iloc[bar + h]) / entry - 1.0) * 100.0 for h in HOR}


def run_engine(sigfn, work, sym):
    n = len(work); final = n - max(HOR) - 1; recs = []; seen = set()
    if final < START:
        return recs
    for bar in range(START - 1, final + 1, STEP):
        hist = work.iloc[: bar + 1]
        for (shape, direction, bucket, skey) in sigfn(hist, sym):
            dk = (shape, bucket, skey)
            if dk in seen:
                continue
            seen.add(dk)
            m = _fwd(work, bar, direction)
            recs.append(dict(shape=shape, bucket=bucket, direction=direction,
                             r5=m[5], r10=m[10], r20=m[20]))
    return recs


def main():
    t0 = time.time()
    files = sorted(glob.glob(os.path.join(BASE, "veriler", "*.IS_1d.parquet")))
    picked = []
    for p in files:
        try:
            if len(pd.read_parquet(p, columns=["Close"])) >= MIN_BARS:
                picked.append(p)
        except Exception:
            pass
        if len(picked) >= N_TICKERS:
            break
    print(f"Pilot: {len(picked)} hisse, walk-forward başlıyor...", flush=True)
    rows = {"core": [], "v2": []}
    for k, p in enumerate(picked, 1):
        sym = os.path.basename(p).replace("_1d.parquet", "")
        try:
            df = pd.read_parquet(p)
            work = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
            rows["core"] += run_engine(core_signals, work, sym)
            rows["v2"]   += run_engine(v2_signals, work, sym)
        except Exception:
            pass
        if k % 20 == 0:
            print(f"  {k}/{len(picked)} ({time.time()-t0:.0f}s)", flush=True)

    def agg(recs):
        from collections import defaultdict
        g = defaultdict(list)
        for r in recs:
            g[(r["shape"], r["bucket"])].append(r)
            g[("__TÜM__", r["bucket"])].append(r)
        out = {}
        for key, rs in g.items():
            r10 = [x["r10"] for x in rs]; r20 = [x["r20"] for x in rs]
            out[f"{key[0]}|{key[1]}"] = dict(
                n=len(rs),
                hit10=round(100 * np.mean([x > 0 for x in r10]), 1),
                avg_r10=round(float(np.mean(r10)), 2),
                med_r10=round(float(np.median(r10)), 2),
                avg_r20=round(float(np.mean(r20)), 2),
                hit20=round(100 * np.mean([x > 0 for x in r20]), 1),
            )
        return out

    res = {"core": agg(rows["core"]), "v2": agg(rows["v2"]),
           "meta": dict(tickers=len(picked), horizons=HOR, step=STEP,
                        secs=round(time.time() - t0, 1))}
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    json.dump(res, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # Markdown özet
    L = [f"# Formasyon Pilot Backtest — {len(picked)} hisse, {res['meta']['secs']}s",
         "⚠ TEK REJİM (yerel parquet) — yön verir, kesin hüküm değil.", "",
         "Sinyal: v2 KIRILIM_* / core KIRILDI = BREAK · ikisinde YAKIN = NEAR. Getiri yöne göre işaretli.", ""]
    for eng in ("core", "v2"):
        L.append(f"## {eng}")
        L.append("| şekil\\|kova | N | hit10% | ort_r10 | med_r10 | ort_r20 | hit20% |")
        L.append("|---|---|---|---|---|---|---|")
        for key in sorted(res[eng], key=lambda kk: (-res[eng][kk]['n'])):
            d = res[eng][key]
            L.append(f"| {key} | {d['n']} | {d['hit10']} | {d['avg_r10']} | {d['med_r10']} | {d['avg_r20']} | {d['hit20']} |")
        L.append("")
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))
    print(f"\n[çıktı] {OUT_MD}  ·  {OUT_JSON}")


if __name__ == "__main__":
    main()
