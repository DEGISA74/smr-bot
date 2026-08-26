"""
SMR — SERT GÜN (ŞOK) KOVA BACKTEST'İ (17 Tem 2026, ekran reformu tartışması)

Soru: "Sert düşüş günü (hisse ≤ -%3, endeks ≤ -%2) YARINI öngörüyor mu?
       Günün KARAKTERİ (kapanış konumu + hacim + şiddet) farkı değiştiriyor mu?"

Karar bağlamı: Kanıt Terazisi'ne 'sert gün' AYI/BOĞA oyu eklenecekse önce bu ölçüm
(extrapolasyon yasağı). Hücre baseline'dan belirgin ayrışmıyorsa OY YAZILMAZ.

Kovalar (hisse, gün getirisi ≤ -3%):
  şiddet: sert (-3..-5) / çok_sert (≤ -5)
  kapanış konumu: dipte (close_loc ≤ 0.35 — satıcı günü güçlü bitirdi) / toparlanmalı
  hacim: hacimli (≥ 1.5× 20g ort) / normal
Baseline: TÜM günler. Zehir bekçisi: pencerede |1g| > %15 (endeks %12) → olay atılır
(BIST günlük limit ±%10 → üstü bölünme/bozuk bar şüphesi; olay gününün kendisi limit içinde).

Çalıştırma:
    python sert_gun_backtest.py
    python sert_gun_backtest.py --sample 30 --days 250    # smoke
"""

import sys
import io
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

try:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR    = Path(__file__).parent
PARQUET_DIR = BASE_DIR / "veriler"
OUTPUT_JSON = BASE_DIR / "sert_gun_backtest.json"
OUTPUT_MD   = BASE_DIR / "sert_gun_report.md"
FWD_DAYS    = (5, 10, 20)


def load_parquet(fp: Path):
    try:
        df = pd.read_parquet(fp)
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        if not {"Open", "High", "Low", "Close", "Volume"}.issubset(df.columns):
            return None
        df = df[df["Close"] > 0]
        return df if len(df) >= 60 else None
    except Exception:
        return None


def backtest_one(df, scan_days, shock_th, guard):
    """Döner: (baseline_events, shock_events)
    shock_event = (siddet, kapanis, hacim, f5, f10, f20)"""
    c = df["Close"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    v = df["Volume"].astype(float)
    ret1 = c.pct_change()
    vol20 = v.rolling(20).mean()
    bad = (ret1.abs() > guard).to_numpy()
    cv, hv, lv, vv = c.to_numpy(), h.to_numpy(), l.to_numpy(), v.to_numpy()
    r1 = ret1.to_numpy()
    v20 = vol20.to_numpy()
    n = len(c)
    start = max(21, n - scan_days - max(FWD_DAYS))
    base, shock = [], []
    for t in range(start, n - max(FWD_DAYS)):
        if np.isnan(r1[t]):
            continue
        if bad[max(0, t - 1): t + max(FWD_DAYS) + 1].any():
            continue
        fw = []
        ok = True
        for k in FWD_DAYS:
            if cv[t] <= 0 or np.isnan(cv[t + k]):
                ok = False
                break
            fw.append((cv[t + k] / cv[t] - 1.0) * 100.0)
        if not ok:
            continue
        base.append(tuple(fw))
        if r1[t] <= shock_th:
            rng = hv[t] - lv[t]
            loc = (cv[t] - lv[t]) / rng if rng > 0 else 0.5
            siddet = "cok_sert" if r1[t] <= -0.05 else "sert"
            kapanis = "dipte" if loc <= 0.35 else "toparlanmali"
            hacim = "hacimli" if (v20[t] > 0 and vv[t] >= 1.5 * v20[t]) else "normal"
            shock.append((siddet, kapanis, hacim, fw[0], fw[1], fw[2]))
    return base, shock


def stats(rows):
    if not rows:
        return None
    a = np.array(rows, dtype=float)
    out = {"n": int(len(a))}
    for i, k in enumerate(FWD_DAYS):
        col = a[:, i]
        out[f"avg_{k}g"] = round(float(col.mean()), 2)
        out[f"med_{k}g"] = round(float(np.median(col)), 2)
        out[f"hit_{k}g"] = round(float((col > 0).mean() * 100), 1)
    return out


def summarize(base_rows, shock_rows):
    res = {"baseline": stats(base_rows), "tum_sok": stats([e[3:] for e in shock_rows]), "hucre": {}}
    df = pd.DataFrame(shock_rows, columns=["siddet", "kapanis", "hacim", "f5", "f10", "f20"])
    # tek boyutlu kesitler + tam hücreler
    for dim in ("siddet", "kapanis", "hacim"):
        for val in df[dim].unique():
            sub = df[df[dim] == val]
            res["hucre"][f"{dim}={val}"] = stats(sub[["f5", "f10", "f20"]].values.tolist())
    for (s, k, ha), sub in df.groupby(["siddet", "kapanis", "hacim"]):
        res["hucre"][f"{s}|{k}|{ha}"] = stats(sub[["f5", "f10", "f20"]].values.tolist())
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=750)
    ap.add_argument("--sample", type=int, default=0)
    args = ap.parse_args()

    files = sorted(PARQUET_DIR.glob("*_1d.parquet"))
    hisse, endeks = [], []
    for f in files:
        sym = f.name.replace("_1d.parquet", "")
        if sym.startswith("X") and sym.endswith(".IS"):
            endeks.append(f)
        elif sym.endswith(".IS"):
            hisse.append(f)
    if args.sample:
        hisse = hisse[: args.sample]

    results = {}
    for grup, flist, th, guard in (("hisse", hisse, -0.03, 0.15), ("endeks", endeks, -0.02, 0.12)):
        b_all, s_all, n_sym = [], [], 0
        for fp in flist:
            df = load_parquet(fp)
            if df is None:
                continue
            b, s = backtest_one(df, args.days, th, guard)
            if b:
                b_all.extend(b)
                s_all.extend(s)
                n_sym += 1
        results[grup] = {"n_symbols": n_sym, "n_shock": len(s_all), **summarize(b_all, s_all)}
        print(f"[{grup}] {n_sym} sembol · {len(b_all):,} baseline gün · {len(s_all):,} şok günü")

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": {"days": args.days, "hisse_esik": "-3%", "endeks_esik": "-2%",
                   "dipte": "close_loc<=0.35", "hacimli": ">=1.5x vol20"},
        "results": results,
    }
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = ["# Sert Gün (Şok) Kova Backtest'i — 17 Tem 2026",
             f"\nÜretim: {payload['generated_utc']} · pencere: son {args.days} bar\n"]
    for grup in ("hisse", "endeks"):
        r = results[grup]
        lines.append(f"\n## {grup.upper()} ({r['n_symbols']} sembol · {r['n_shock']:,} şok günü)\n")
        lines.append("| Kesit | N | 5g ort | 10g ort | 20g ort | 10g medyan | 10g isabet |")
        lines.append("|---|---|---|---|---|---|---|")
        def _row(ad, s):
            if not s:
                return None
            return (f"| {ad} | {s['n']:,} | {s['avg_5g']:+.2f}% | {s['avg_10g']:+.2f}% "
                    f"| {s['avg_20g']:+.2f}% | {s['med_10g']:+.2f}% | %{s['hit_10g']} |")
        for ad, s in (("BASELINE (tüm günler)", r["baseline"]), ("TÜM ŞOK GÜNLERİ", r["tum_sok"])):
            rr = _row(ad, s)
            if rr:
                lines.append(rr)
        for ad, s in sorted(r["hucre"].items()):
            rr = _row(ad, s)
            if rr:
                lines.append(rr)
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nJSON: {OUTPUT_JSON.name} · Rapor: {OUTPUT_MD.name}")

    for grup in ("hisse", "endeks"):
        r = results[grup]
        b, s = r["baseline"], r["tum_sok"]
        if b and s:
            print(f"\n{grup.upper()}: baseline 10g {b['avg_10g']:+.2f}% (isabet %{b['hit_10g']}) "
                  f"vs ŞOK 10g {s['avg_10g']:+.2f}% (isabet %{s['hit_10g']}, N={s['n']:,})")


if __name__ == "__main__":
    main()
