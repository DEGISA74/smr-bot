# -*- coding: utf-8 -*-
"""
backtest_dusen_obv_cmf_donus.py — Düşen rejimde OBV+CMF dönüşü (çoklu rejim)
===========================================================================
Amaç (30 Tem 2026): Önceki backtest'te (backtest_kirmizi_mavi_long) tek parlak
hücre = DÜŞEN rejim + OBV/CMF pozitif, 20g +%16 / %80 isabet (N=277) çıktı AMA
tek düşen rejimdi → extrapolasyon riski. Bu script cevheri izole eder:
  - Bar-dönüşü ve STP koşulları ATILDI (ablation'da ölü ağırlıktı).
  - TÜM parquet geçmişi kullanılır (~2 yıl → birden fazla rejim salınımı).
  - Sinyal DÜŞEN rejime (XU100 < SMA50) kısıtlanır.
  - EN KRİTİK: düşen rejim ayrı EPISODLARA bölünür; sinyal tek kümeden mi yoksa
    farklı düşüş dönemlerinde de mi çalışıyor kontrol edilir. [[feedback-extrapolation-yasak]]

Varyantlar (giriş günü i, hepsi düşen rejim günlerinde):
  A) OBV yukarı (i > i-5)  &  CMF>0                    (durum)
  B) OBV yukarı            &  CMF taze dönüş (≤0→>0 son 3g)  (dönüş)
  C) OBV dip dönüşü (i>i-3 & i-3<i-6)  &  CMF>0        (OBV tabanı)
  D) yalnız CMF taze dönüş
  E) yalnız OBV yukarı

Hakem = TAZE parquet forward 5/10/20g. Evren = son 180g en likit 100 hisse.
Çıktı: konsol + backtest_dusen_obv_cmf_donus.json
Kullanım: python backtest_dusen_obv_cmf_donus.py [--top 100]
"""
import sys, os, glob, json, argparse
from datetime import datetime
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indicators import compute_obv_series

VERI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veriler")
OUT_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_dusen_obv_cmf_donus.json")
IDX_PREFIX = ("XU", "XB", "XT", "XY", "XG", "XK", "XL", "XM", "XS", "XW", "X0")


def cmf_series(df, period=20):
    h, l, c, v = df["High"], df["Low"], df["Close"], df["Volume"]
    rng = (h - l).replace(0, np.nan)
    mfm = ((c - l) - (h - c)) / rng
    mfv = (mfm * v).fillna(0.0)
    return mfv.rolling(period).sum() / v.rolling(period).sum().replace(0, np.nan)


def build_universe(files, days, top):
    liq = []
    for f in files:
        tick = os.path.basename(f).split(".")[0]
        if tick.startswith(IDX_PREFIX):
            continue
        try:
            df = pd.read_parquet(f, columns=["Close", "Volume"])
            if df is None or len(df) < days + 30:
                continue
            tail = df.tail(days)
            t = float((tail["Close"] * tail["Volume"]).mean())
            if np.isfinite(t) and t > 0:
                liq.append((t, f))
        except Exception:
            continue
    liq.sort(reverse=True)
    return [f for _, f in liq[:top]]


def xu100_regime_and_episodes():
    """XU100 tarih→rejim(+1/-1) + düşen EPISODLAR (kesintisiz -1 blokları)."""
    p = os.path.join(VERI_DIR, "XU100.IS_1d.parquet")
    if not os.path.exists(p):
        return {}, []
    x = pd.read_parquet(p)
    sma = x["Close"].rolling(50).mean()
    reg = np.where(x["Close"] > sma, 1, -1)
    reg_map = {d: int(r) for d, r in zip(x.index, reg)}
    # Düşen episodları çıkar (SMA hazır olduktan sonra)
    episodes = []
    cur_start = None
    dates = list(x.index)
    for k in range(len(dates)):
        if pd.isna(sma.iloc[k]):
            continue
        if reg[k] == -1:
            if cur_start is None:
                cur_start = dates[k]
            last = dates[k]
        else:
            if cur_start is not None:
                episodes.append((cur_start, last))
                cur_start = None
    if cur_start is not None:
        episodes.append((cur_start, dates[-1]))
    # Çok kısa blokları (<5 gün) at
    episodes = [(a, b) for a, b in episodes if (b - a).days >= 5]
    return reg_map, episodes


def which_episode(dt, episodes):
    for idx, (a, b) in enumerate(episodes):
        if a <= dt <= b:
            return idx
    return -1


def collect(df, reg_map, episodes):
    n = len(df)
    if n < 80:
        return []
    c = df["Close"].astype(float)
    v = df["Volume"].astype(float)
    obv = compute_obv_series(df)
    cmf = cmf_series(df)
    vma20 = v.rolling(20).mean()

    obv_up = obv > obv.shift(5)
    obv_turn = (obv > obv.shift(3)) & (obv.shift(3) < obv.shift(6))
    cmf_pos = cmf > 0
    cmf_cross = cmf_pos & ((cmf.shift(1) <= 0) | (cmf.shift(2) <= 0) | (cmf.shift(3) <= 0))

    rows = []
    for i in range(55, n - 20):
        dt = df.index[i]
        if reg_map.get(dt, 0) != -1:      # SADECE düşen rejim
            continue
        vol_i = float(v.iloc[i]); vma_i = float(vma20.iloc[i])
        if vol_i <= 0 or not np.isfinite(vma_i) or vma_i <= 0:
            continue
        cp0 = float(c.iloc[i])
        if cp0 <= 0:
            continue
        rows.append({
            "A": bool(obv_up.iloc[i] and cmf_pos.iloc[i]),
            "B": bool(obv_up.iloc[i] and cmf_cross.iloc[i]),
            "C": bool(obv_turn.iloc[i] and cmf_pos.iloc[i]),
            "D": bool(cmf_cross.iloc[i]),
            "E": bool(obv_up.iloc[i]),
            "f5": (float(c.iloc[i + 5]) / cp0 - 1) * 100,
            "f10": (float(c.iloc[i + 10]) / cp0 - 1) * 100,
            "f20": (float(c.iloc[i + 20]) / cp0 - 1) * 100,
            "ep": which_episode(dt, episodes),
        })
    return rows


def summ(rows):
    if not rows:
        return None
    def st(a):
        return {"n": len(a), "ort": round(float(np.mean(a)), 2),
                "isabet": round(float(np.mean([1 if x > 0 else 0 for x in a]) * 100), 1),
                "medyan": round(float(np.median(a)), 2)}
    return {"5g": st([r["f5"] for r in rows]), "10g": st([r["f10"] for r in rows]),
            "20g": st([r["f20"] for r in rows])}


def prow(label, s):
    if not s:
        print(f"{label:32s}  (veri yok / N=0)"); return
    print(f"{label:32s}  N={s['5g']['n']:>6d}  "
          f"5g {s['5g']['ort']:+6.2f}%/%{s['5g']['isabet']:<4.1f}  "
          f"10g {s['10g']['ort']:+6.2f}%/%{s['10g']['isabet']:<4.1f}  "
          f"20g {s['20g']['ort']:+6.2f}%/%{s['20g']['isabet']:<4.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--liq_days", type=int, default=180)
    args = ap.parse_args()

    all_files = sorted(glob.glob(os.path.join(VERI_DIR, "*.IS_1d.parquet")))
    t0 = datetime.now()
    print(f"=== DÜŞEN REJİM OBV+CMF DÖNÜŞ — BIST{args.top}, TÜM GEÇMİŞ ===")
    files = build_universe(all_files, args.liq_days, args.top)
    reg_map, episodes = xu100_regime_and_episodes()
    print(f"Evren: {len(files)} hisse | Rejim günü: {len(reg_map)} | Düşen episod: {len(episodes)}")
    for idx, (a, b) in enumerate(episodes):
        print(f"   Episod {idx}: {a.date()} → {b.date()}  ({(b-a).days} gün)")

    ALL = []
    done = 0
    for f in files:
        try:
            ALL.extend(collect(pd.read_parquet(f), reg_map, episodes))
        except Exception as ex:
            print(f"  ! {os.path.basename(f)}: {ex}")
        done += 1
        if done % 25 == 0:
            print(f"  ... {done}/{len(files)} ({(datetime.now()-t0).seconds}sn)")

    print(f"\nToplam DÜŞEN rejim günü (evren×gün): {len(ALL)}")
    if not ALL:
        print("Veri yok."); return

    print("\n" + "=" * 100)
    print("DÜŞEN REJİM — varyantlar (ort getiri % / isabet %) forward 5/10/20g")
    print("=" * 100)
    prow("Baseline (tüm düşen günler)", summ(ALL))
    print("-" * 100)
    vardefs = {
        "A": "OBV↑ & CMF>0 (durum)", "B": "OBV↑ & CMF taze dönüş",
        "C": "OBV taban & CMF>0", "D": "yalnız CMF taze dönüş", "E": "yalnız OBV↑",
    }
    var_rows = {}
    for k, name in vardefs.items():
        sub = [r for r in ALL if r[k]]
        var_rows[k] = sub
        prow(f"[{k}] {name}", summ(sub))

    # ── EN KRİTİK: en iyi varyantın EPİSOD tutarlılığı ───────────────────────
    # "En iyi" = 20g ort × isabet çarpımı en yüksek, N≥60
    scored = [(k, (summ(var_rows[k])["20g"]["ort"] * summ(var_rows[k])["20g"]["isabet"]))
              for k in vardefs if var_rows[k] and len(var_rows[k]) >= 60]
    best = max(scored, key=lambda z: z[1])[0] if scored else "A"
    print("\n" + "=" * 100)
    print(f"EPİSOD TUTARLILIK — varyant [{best}] {vardefs[best]} (tek küme mi, her düşüşte mi?)")
    print("=" * 100)
    by_ep = defaultdict(list)
    for r in var_rows[best]:
        by_ep[r["ep"]].append(r)
    ep_out = {}
    pos_eps = 0
    for idx in sorted(by_ep):
        if idx < 0:
            continue
        s = summ(by_ep[idx])
        a, b = episodes[idx]
        prow(f"  Ep{idx} {a.date()}→{b.date()}", s)
        ep_out[idx] = {"range": f"{a.date()}→{b.date()}", "stats": s}
        if s and s["20g"]["ort"] > 0 and s["20g"]["isabet"] >= 55:
            pos_eps += 1

    n_ep = len([i for i in by_ep if i >= 0])
    print("\n" + "=" * 100)
    sbest = summ(var_rows[best])
    if not sbest or sbest["20g"]["n"] < 60:
        verdict = f"YETERSİZ ÖRNEK (N={sbest['20g']['n'] if sbest else 0}) — hüküm yok."
    elif n_ep >= 2 and pos_eps >= max(2, n_ep - 1):
        verdict = (f"SAĞLAM — [{best}] {n_ep} düşen episodun {pos_eps}'inde pozitif "
                   f"(20g +%{sbest['20g']['ort']}/%{sbest['20g']['isabet']}). Çoklu rejim tuttu → "
                   f"düşen-rejim dönüş taraması yazmaya değer.")
    elif n_ep >= 2 and pos_eps >= 1:
        verdict = (f"KISMİ — [{best}] {n_ep} episodun {pos_eps}'inde tuttu. Karışık; "
                   f"1 rejime bağımlı olabilir. Yeni düşüşte tekrar ölç, aceleyle yazma.")
    else:
        verdict = (f"TEK KÜME — [{best}] esasen tek episodtan geliyor (pos_eps={pos_eps}/{n_ep}). "
                   f"Extrapolasyon riski. Tarama YAZMA.")
    print(f">>> HÜKÜM: {verdict}")
    print("=" * 100)

    out = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "universe": len(files), "n_down_days": len(ALL),
        "episodes": [f"{a.date()}→{b.date()}" for a, b in episodes],
        "variants": {k: summ(var_rows[k]) for k in vardefs},
        "best_variant": best, "best_by_episode": ep_out,
        "verdict": verdict,
        "note": "Sinyal SADECE düşen rejim (XU100<SMA50). Episod tutarlılığı = anti-extrapolasyon kapısı.",
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nJSON yazıldı: {OUT_JSON}  ({(datetime.now()-t0).seconds}sn)")


if __name__ == "__main__":
    main()
