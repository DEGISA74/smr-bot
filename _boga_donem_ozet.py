# -*- coding: utf-8 -*-
"""Boğa dönemi (1 Oca–18 Şub 2026) tüm taramalar özeti — patron.db signal_results
sinyal_date ile süz, XU100 ileri getirisiyle alpha. Sıralama: yönlü beklenti (20g)."""
import sqlite3, os, json
import numpy as np, pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
D0, D1 = "2026-01-01", "2026-02-18"

# label haritası
lab = {}
try:
    bt = json.load(open(os.path.join(BASE, "backtest_results.json"), encoding="utf-8"))
    for r in bt["summary"]:
        lab[r["scan_type"]] = (r["label"], r.get("bias", "?"))
except Exception:
    pass

# XU100 forward getiri haritası (tarih -> ret)
xu = pd.read_parquet(os.path.join(BASE, "veriler", "XU100.IS_1d.parquet"))
xu_close = xu["Close"].astype(float).reset_index(drop=True)
xu_dates = [str(d)[:10] for d in xu.index]
dpos = {d: i for i, d in enumerate(xu_dates)}
def xu_fwd(dstr, k):
    i = dpos.get(dstr)
    if i is None or i + k >= len(xu_close):
        return None
    a, b = xu_close.iloc[i], xu_close.iloc[i + k]
    return (b / a - 1) * 100 if a > 0 else None

con = sqlite3.connect(os.path.join(BASE, "patron.db"))
rows = con.execute(f"""
    SELECT scan_type, bias, signal_date, ret_5g, ret_10g, ret_20g, hit_20g, max_gain_20g, max_loss_20g
    FROM signal_results
    WHERE signal_date >= ? AND signal_date <= ?
""", (D0, D1)).fetchall()
con.close()

from collections import defaultdict
G = defaultdict(list)
for st, bias, sd, r5, r10, r20, h20, mg, ml in rows:
    G[st].append(dict(bias=bias, sd=str(sd)[:10], r5=r5, r10=r10, r20=r20, h20=h20, mg=mg, ml=ml))

out = []
for st, rs in G.items():
    r20 = [x["r20"] for x in rs if x["r20"] is not None]
    if len(r20) < 8:
        continue
    label, bias = lab.get(st, (st, rs[0]["bias"]))
    bearish = (bias == "bearish")
    arr = np.array(r20)
    dret = -arr if bearish else arr            # yönlü getiri
    exp = float(np.mean(dret))
    hit = float(np.mean(dret > 0) * 100)
    wins = dret[dret > 0]; losses = dret[dret < 0]
    rr = float(abs(wins.mean() / losses.mean())) if len(wins) and len(losses) else None
    # alpha (yönlü)
    al = []
    for x in rs:
        if x["r20"] is None:
            continue
        xr = xu_fwd(x["sd"], 20)
        if xr is None:
            continue
        al.append((x["r20"] - xr) if not bearish else (xr - x["r20"]))
    alpha = float(np.mean(al)) if al else None
    out.append(dict(st=st, label=label, bias=("SHORT" if bearish else "LONG"),
                    n=len(r20), hit=hit, ret=exp, rr=rr, alpha=alpha,
                    r5=float(np.mean([x["r5"] for x in rs if x["r5"] is not None] or [0])),
                    r10=float(np.mean([x["r10"] for x in rs if x["r10"] is not None] or [0]))))

out.sort(key=lambda o: o["exp"] if "exp" in o else o["ret"], reverse=True)

print(f"=== BOĞA DÖNEMİ {D0} → {D1} — sinyal_date süzülmüş, yönlü 20g beklentiye göre ===")
print(f"XU100 dönem içi: ", end="")
i0, i1 = dpos.get("2026-01-02"), dpos.get("2026-02-18")
if i0 and i1:
    print(f"{(xu_close.iloc[i1]/xu_close.iloc[i0]-1)*100:+.1f}% (boğa teyidi)\n")
print(f"{'TARAMA':<34}{'yön':>6}{'N':>5}{'isb%':>6}{'20g%':>7}{'R/R':>5}{'BIST':>7}")
for o in out:
    rr = f"{o['rr']:.1f}" if o['rr'] else "-"
    al = f"{o['alpha']:+.1f}" if o['alpha'] is not None else "-"
    w = " !" if o['n'] < 25 else ""
    print(f"{o['label'][:33]:<34}{o['bias']:>6}{o['n']:>5}{o['hit']:>6.0f}{o['ret']:>7.1f}{rr:>5}{al:>7}{w}")

json.dump(out, open(os.path.join(BASE, "_boga_donem_ozet.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
