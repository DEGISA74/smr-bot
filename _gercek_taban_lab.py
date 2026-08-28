"""Aşama 1'in 'evren tabanı' sinyal-ağırlıklıydı. Bu betik GERÇEK hisse tabanını ölçer:
aynı 74 giriş gününde, kasadaki TÜM BIST hisselerini al-tut, 20 seans, XU100'e göre alfa.
Salt-okunur laboratuvar. Canlı hiçbir şeye dokunmaz."""
import warnings, json; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from bist_data_store import active_version_id, load_manifest, read_active

V = active_version_id()
man = load_manifest(V) or {}
syms = sorted(man.get("symbols", {}).keys())
print(f"kasa {V} · sembol {len(syms)}", flush=True)

ev = pd.read_csv("logs/degisken_vade_asama1_events.csv", usecols=["entry_date", "day"])
dates = sorted(pd.to_datetime(ev[ev.day == 1].entry_date.unique()).normalize())
print(f"giris gunu: {len(dates)}  {dates[0].date()} -> {dates[-1].date()}", flush=True)

def prep(d):
    if d is None or getattr(d, "empty", True): return None
    if isinstance(d.columns, pd.MultiIndex): d.columns = d.columns.get_level_values(0)
    d = d.loc[:, ~d.columns.duplicated()].copy()
    d.columns = [str(c).capitalize() for c in d.columns]
    if not {"Open", "Close"}.issubset(d.columns): return None
    i = pd.to_datetime(d.index, errors="coerce")
    if getattr(i, "tz", None) is not None: i = i.tz_localize(None)
    d.index = i.normalize()
    d = d[~d.index.isna()]
    return d[~d.index.duplicated(keep="last")].sort_index()

bm = prep(read_active("XU100.IS", V))
bidx = bm.index
H = 20

def paths(d):
    """her giris gunu icin 1..20 gunluk kapanis getirisi (%)"""
    out = {}
    for dt in dates:
        p = int(d.index.searchsorted(dt, side="left"))
        if p >= len(d.index) or d.index[p] != dt: continue
        o = float(d["Open"].iloc[p])
        if not np.isfinite(o) or o <= 0: continue
        n = min(H, len(d) - p)
        if n < 1: continue
        c = d["Close"].to_numpy(dtype=float)[p:p + n]
        out[dt] = (c / o - 1.0) * 100.0
    return out

bpath = paths(bm)
per_day = {k: [] for k in range(1, H + 1)}
done = skipped = 0
for s in syms:
    if s.upper().startswith("XU") or s.upper().startswith("^"): continue
    try: d = prep(read_active(s, V))
    except Exception: d = None
    if d is None or d.empty: skipped += 1; continue
    for dt, sp in paths(d).items():
        bp = bpath.get(dt)
        if bp is None: continue
        n = min(len(sp), len(bp))
        for k in range(n):
            a = sp[k] - bp[k]
            if np.isfinite(a): per_day[k + 1].append(a)
    done += 1
    if done % 200 == 0: print(f"  ...{done} hisse", flush=True)

print(f"islenen {done} · veri yok {skipped}", flush=True)
rows = []
for k in range(1, H + 1):
    a = np.asarray(per_day[k], dtype=float)
    rows.append({"gun": k, "N": int(a.size),
                 "ortanca": float(np.median(a)) if a.size else None,
                 "ortalama": float(a.mean()) if a.size else None,
                 "endeksi_yenme": float((a > 0).mean() * 100) if a.size else None})
out = pd.DataFrame(rows)
out.to_csv("logs/gercek_hisse_tabani.csv", index=False)
print(out.to_string(index=False))
