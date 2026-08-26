# -*- coding: utf-8 -*-
"""
panel_verdict_backtest.py — SMART MONEY HACİM ANALİZİ paneli konsensüs skoru backtest'i
=======================================================================================
Amaç (10 Tem 2026, öneri 9): Panelin sağ üst köşesindeki "70 — HAFİF ALIM" etiketi
el yapımı ağırlıklarla (5 bileşen × ±20 puan) üretiliyor. Bu script aynı skoru
geçmiş her gün için yeniden hesaplar ve etiketlerin 5/10/20 gün ileri getirisini ölçer.

Panel bileşenlerinin birebir kopyası (app.py render_smart_volume_panel konsensüs):
  va  : kapanış Value Area üstünde +1 / altında -1 / içinde 0   (VP lookback=20)
  d1  : günün hacim deltası işareti (CLV paylaştırma — calculate_volume_delta)
  rv  : RVOL >= 2.0 → +1, < 0.8 → -1, arası 0
  5g  : 5 günlük kümülatif delta işareti
  obv : OBV 5g VE 14g ikisi de yukarı → +1, ikisi de aşağı → -1, karışık 0
Skor = pos*20 - neg*20 + 50 (0-100 clamp). Etiket eşikleri panel ile aynı.

Not: Panel OBV bileşeni gerçekte CMF teyidi de içerir (get_obv_divergence_status);
burada dual-window yön yaklaşıklaması kullanılır — sonuç yaklaşıktır, yön doğrudur.

Çıktı: panel_verdict_backtest.json (panel rozeti bunu okur, N>=30 şartıyla)
Kullanım: python panel_verdict_backtest.py [--sample N] [--days D]
"""
import sys, os, glob, json, argparse
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indicators import calculate_volume_delta, calculate_full_volume_profile, compute_obv_series

VERI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veriler")
OUT_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel_verdict_backtest.json")

LABELS = ["ALIM BASKISI", "HAFİF ALIM", "NÖTR", "HAFİF SATIŞ", "SATIŞ BASKISI"]


def score_to_label(scr):
    if scr >= 80: return "ALIM BASKISI"
    if scr >= 62: return "HAFİF ALIM"
    if scr > 38:  return "NÖTR"
    if scr >= 20: return "HAFİF SATIŞ"
    return "SATIŞ BASKISI"


def eval_ticker(df, eval_days):
    """Bir hissenin son eval_days gününde (20g forward payıyla) günlük skor + ileri getiriler."""
    rows = []
    n = len(df)
    if n < 60 + eval_days + 20:
        eval_days = max(0, n - 80)
    if eval_days <= 0:
        return rows

    df = calculate_volume_delta(df)
    c = df["Close"].astype(float)
    v = df["Volume"].astype(float)
    vd = df["Volume_Delta"].astype(float)
    obv = compute_obv_series(df)
    vma20 = v.rolling(20).mean()
    vd5 = vd.rolling(5).sum()

    for back in range(20 + eval_days, 20, -1):   # eskiden yeniye; son 20 gün forward için ayrılır
        i = n - 1 - back
        if i < 40:
            continue
        vol_i = float(v.iloc[i]); vma_i = float(vma20.iloc[i])
        if vol_i <= 0 or vma_i <= 0 or not np.isfinite(vma_i):
            continue

        # va — Value Area konumu (VP lookback=20, panel default)
        try:
            vp = calculate_full_volume_profile(df.iloc[: i + 1], lookback=20)
            cp = float(c.iloc[i])
            s_va = 1 if cp > vp["vah"] else (-1 if cp < vp["val"] else 0)
        except Exception:
            s_va = 0

        # d1 / 5g — delta işaretleri
        s_d1 = 1 if vd.iloc[i] > 0 else (-1 if vd.iloc[i] < 0 else 0)
        s_5g = 1 if vd5.iloc[i] > 0 else (-1 if vd5.iloc[i] < 0 else 0)

        # rv — RVOL kovası (panel tile 4 eşikleri)
        rvol = vol_i / vma_i
        s_rv = 1 if rvol >= 2.0 else (-1 if rvol < 0.8 else 0)

        # obv — dual window yön
        o_now = float(obv.iloc[i]); o_5 = float(obv.iloc[i - 5]); o_14 = float(obv.iloc[i - 14])
        o5_up, o14_up = o_now > o_5, o_now > o_14
        s_obv = 1 if (o5_up and o14_up) else (-1 if (not o5_up and not o14_up) else 0)

        sigs = [s_va, s_d1, s_rv, s_5g, s_obv]
        pos = sum(1 for s in sigs if s > 0)
        neg = sum(1 for s in sigs if s < 0)
        scr = max(0, min(100, pos * 20 - neg * 20 + 50))

        cp0 = float(c.iloc[i])
        if cp0 <= 0:
            continue
        fwd = {}
        ok = True
        for h in (5, 10, 20):
            if i + h >= n:
                ok = False
                break
            fwd[h] = (float(c.iloc[i + h]) / cp0 - 1) * 100
        if not ok:
            continue
        rows.append((score_to_label(scr), fwd[5], fwd[10], fwd[20]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0, help="sadece ilk N hisse (hızlı test)")
    ap.add_argument("--days", type=int, default=130, help="hisse başına değerlendirme günü")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(VERI_DIR, "*.IS_1d.parquet")))
    files = [f for f in files if not os.path.basename(f).startswith(("XU", "XB", "XT", "XY"))]
    if args.sample:
        files = files[: args.sample]

    print(f"=== PANEL VERDICT BACKTEST — {len(files)} hisse × ~{args.days} gün ===")
    t0 = datetime.now()
    agg = {lbl: {"r5": [], "r10": [], "r20": []} for lbl in LABELS}
    done = 0
    for f in files:
        try:
            df = pd.read_parquet(f)
            if df is None or len(df) < 80:
                continue
            for lbl, r5, r10, r20 in eval_ticker(df, args.days):
                agg[lbl]["r5"].append(r5)
                agg[lbl]["r10"].append(r10)
                agg[lbl]["r20"].append(r20)
        except Exception as ex:
            print(f"  ! {os.path.basename(f)}: {ex}")
        done += 1
        if done % 100 == 0:
            print(f"  ... {done}/{len(files)} ({(datetime.now()-t0).seconds}sn)")

    out = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "universe": len(files),
        "eval_days": args.days,
        "note": "OBV bileşeni dual-window yaklaşıklaması (CMF teyidi yok) — panel skorunun yaklaşık kopyası",
        "labels": {},
    }
    print(f"\n{'ETİKET':16s} {'N':>7s} {'5g ort':>8s} {'10g ort':>8s} {'10g med':>8s} {'10g hit':>8s} {'20g ort':>8s}")
    print("-" * 70)
    for lbl in LABELS:
        r5, r10, r20 = agg[lbl]["r5"], agg[lbl]["r10"], agg[lbl]["r20"]
        n_ = len(r10)
        if n_ == 0:
            continue
        row = {
            "n": n_,
            "avg_5g": round(float(np.mean(r5)), 2),
            "avg_10g": round(float(np.mean(r10)), 2),
            "med_10g": round(float(np.median(r10)), 2),
            "hit_10g": round(float(np.mean([1 if x > 0 else 0 for x in r10]) * 100), 1),
            "avg_20g": round(float(np.mean(r20)), 2),
        }
        out["labels"][lbl] = row
        print(f"{lbl:16s} {n_:>7d} {row['avg_5g']:>7.2f}% {row['avg_10g']:>7.2f}% "
              f"{row['med_10g']:>7.2f}% {row['hit_10g']:>7.1f}% {row['avg_20g']:>7.2f}%")

    if args.sample:
        # Örneklem koşusu (test/smoke) — panel rozetinin okuduğu JSON'u EZME
        print(f"\n--sample modu: JSON yazılmadı (panel verisi korundu)  ({(datetime.now()-t0).seconds}sn)")
    else:
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\nJSON yazıldı: {OUT_JSON}  ({(datetime.now()-t0).seconds}sn)")


if __name__ == "__main__":
    main()
