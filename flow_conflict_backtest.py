# -*- coding: utf-8 -*-
"""
flow_conflict_backtest.py — 5G akış ↔ 10G konsensüs ÇELİŞKİ hakemliği
=====================================================================
Amaç (22 Tem 2026, öneri #4): SMART MONEY HACİM paneli iki farklı pencere kullanıyor:
  - 5G  : 5 günlük kümülatif hacim deltası işareti (Tile 5, "5 Günde Net Alım/Satış")
  - 10G : 5 bileşenli konsensüs etiketi (SKOR 10G şeridi, panel sağ üst "HAFİF SATIŞ" vb.)
Bunlar ZIT yön gösterdiğinde (kısa=alıcı ama konsensüs=satıcı, ya da tersi) panel hangisine
uyacağını sezgiyle seçiyordu (kısa pencere manşeti eziyordu). Bu script geçmişteki TÜM
çelişki günlerini bulur ve "çelişkide hangisini takip etmek daha kârlıydı" sorusunu
GETİRİ ile yanıtlar. Sonuç ağırlık kararını (öneri #1 hiyerarşi) sezgiden değil kanıttan
doğurur. [[feedback-extrapolation-yasak]]

Konsensüs skoru panel_verdict_backtest.py ile BİREBİR aynı hesaplanır (tek kaynak kopyası).
Hakem = TAZE parquet (data_layer'ın yazdığı) forward getiri; bölünme temizliği parquet'e güvenir.

Çıktı: konsol tablo + flow_conflict_backtest.json
Kullanım: python flow_conflict_backtest.py [--sample N] [--days D]
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
OUT_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flow_conflict_backtest.json")


def score_to_dir(scr):
    """Konsensüs skoru → yön: +1 alıcı / -1 satıcı / 0 nötr (panel etiket eşikleriyle)."""
    if scr >= 62:  return 1    # ALIM BASKISI / HAFİF ALIM
    if scr > 38:   return 0    # NÖTR
    return -1                  # HAFİF SATIŞ / SATIŞ BASKISI


def eval_ticker(df, eval_days):
    """Çelişki günlerini döndürür: (dir10, dir5, fwd5, fwd10, fwd20)."""
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

    for back in range(20 + eval_days, 20, -1):
        i = n - 1 - back
        if i < 40:
            continue
        vol_i = float(v.iloc[i]); vma_i = float(vma20.iloc[i])
        if vol_i <= 0 or vma_i <= 0 or not np.isfinite(vma_i):
            continue

        # 5G yönü (kısa pencere) — panel Tile 5 ile aynı: 5 günlük kümülatif delta işareti
        f5 = float(vd5.iloc[i])
        dir5 = 1 if f5 > 0 else (-1 if f5 < 0 else 0)
        if dir5 == 0:
            continue

        # 10G konsensüs (5 bileşen — panel_verdict_backtest ile birebir)
        try:
            vp = calculate_full_volume_profile(df.iloc[: i + 1], lookback=20)
            cp = float(c.iloc[i])
            s_va = 1 if cp > vp["vah"] else (-1 if cp < vp["val"] else 0)
        except Exception:
            s_va = 0
        s_d1 = 1 if vd.iloc[i] > 0 else (-1 if vd.iloc[i] < 0 else 0)
        s_5g = dir5
        rvol = vol_i / vma_i
        s_rv = 1 if rvol >= 2.0 else (-1 if rvol < 0.8 else 0)
        o_now = float(obv.iloc[i]); o_5 = float(obv.iloc[i - 5]); o_14 = float(obv.iloc[i - 14])
        o5_up, o14_up = o_now > o_5, o_now > o_14
        s_obv = 1 if (o5_up and o14_up) else (-1 if (not o5_up and not o14_up) else 0)

        sigs = [s_va, s_d1, s_rv, s_5g, s_obv]
        pos = sum(1 for s in sigs if s > 0)
        neg = sum(1 for s in sigs if s < 0)
        scr = max(0, min(100, pos * 20 - neg * 20 + 50))
        dir10 = score_to_dir(scr)

        # SADECE çelişki günleri: ikisi de non-zero VE zıt yön
        if dir10 == 0 or dir10 == dir5:
            continue

        cp0 = float(c.iloc[i])
        if cp0 <= 0:
            continue
        fwd = {}
        ok = True
        for h in (5, 10, 20):
            if i + h >= n:
                ok = False; break
            fwd[h] = (float(c.iloc[i + h]) / cp0 - 1) * 100
        if not ok:
            continue
        rows.append((dir10, dir5, fwd[5], fwd[10], fwd[20]))
    return rows


def _summ(dir10s, rets):
    """Konsensüs yönünü takip etmenin getirisi = dir10 * fwd. >0 → 10G haklı."""
    follow10 = [d * r for d, r in zip(dir10s, rets)]
    n = len(follow10)
    if n == 0:
        return None
    avg = float(np.mean(follow10))
    hit = float(np.mean([1 if x > 0 else 0 for x in follow10]) * 100)
    return {"n": n, "follow10_avg": round(avg, 2), "follow10_hit": round(hit, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--days", type=int, default=130)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(VERI_DIR, "*.IS_1d.parquet")))
    files = [f for f in files if not os.path.basename(f).startswith(("XU", "XB", "XT", "XY"))]
    if args.sample:
        files = files[: args.sample]

    print(f"=== FLOW CONFLICT BACKTEST — {len(files)} hisse × ~{args.days} gün ===")
    t0 = datetime.now()
    # Tüm çelişki satırları
    D10, R5, R10, R20 = [], [], [], []
    # Alt kırılım: 5G alıcı iken (konsensüs satıcı)  vs  5G satıcı iken (konsensüs alıcı)
    subA = {"d10": [], "r10": []}   # dir5=+1, dir10=-1
    subB = {"d10": [], "r10": []}   # dir5=-1, dir10=+1
    done = 0
    for f in files:
        try:
            df = pd.read_parquet(f)
            if df is None or len(df) < 80:
                continue
            for dir10, dir5, r5, r10, r20 in eval_ticker(df, args.days):
                D10.append(dir10); R5.append(r5); R10.append(r10); R20.append(r20)
                if dir5 == 1:
                    subA["d10"].append(dir10); subA["r10"].append(r10)
                else:
                    subB["d10"].append(dir10); subB["r10"].append(r10)
        except Exception as ex:
            print(f"  ! {os.path.basename(f)}: {ex}")
        done += 1
        if done % 100 == 0:
            print(f"  ... {done}/{len(files)} ({(datetime.now()-t0).seconds}sn)")

    n_conf = len(D10)
    print(f"\nToplam çelişki günü: {n_conf}")
    if n_conf == 0:
        print("Çelişki günü bulunamadı."); return

    print(f"\n{'HORİZON':10s} {'N':>7s} {'10G-takip ort':>14s} {'10G-takip isabet':>18s}")
    print("-" * 52)
    out_h = {}
    for h, rets in (("5g", R5), ("10g", R10), ("20g", R20)):
        s = _summ(D10, rets)
        out_h[h] = s
        print(f"{h:10s} {s['n']:>7d} {s['follow10_avg']:>13.2f}% {s['follow10_hit']:>17.1f}%")

    print("\n-- Alt kırılım (10g getiri) --")
    sA = _summ(subA["d10"], subA["r10"])   # 5G alıcı, 10G satıcı
    sB = _summ(subB["d10"], subB["r10"])   # 5G satıcı, 10G alıcı
    if sA: print(f"5G ALICI ↔ 10G SATICI : N={sA['n']:>5d}  10G-takip ort {sA['follow10_avg']:+.2f}%  isabet %{sA['follow10_hit']:.1f}")
    if sB: print(f"5G SATICI ↔ 10G ALICI : N={sB['n']:>5d}  10G-takip ort {sB['follow10_avg']:+.2f}%  isabet %{sB['follow10_hit']:.1f}")

    # Verdict: 10g horizonda 10G-takip ort > 0 ve isabet > 50 → uzun pencere kazanır
    v10 = out_h["10g"]
    if v10 and v10["follow10_avg"] > 0 and v10["follow10_hit"] >= 52:
        verdict = "10G (konsensüs) KAZANIR → hiyerarşi #1 uygula (yön uzun pencerede)"
    elif v10 and v10["follow10_avg"] < 0 and v10["follow10_hit"] <= 48:
        verdict = "5G (kısa pencere) KAZANIR → manşet zaten doğru, sadece rozet kalsın"
    else:
        verdict = "AYIRT EDİCİ DEĞİL → rozet kalsın, ağırlık değiştirme (yazı-tura bandı)"
    print(f"\n>>> HÜKÜM: {verdict}")

    out = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "universe": len(files),
        "eval_days": args.days,
        "n_conflict": n_conf,
        "horizons": out_h,
        "sub_5g_alici_10g_satici": sA,
        "sub_5g_satici_10g_alici": sB,
        "verdict": verdict,
        "note": "follow10 = dir10*fwd; >0 → çelişkide konsensüs yönü daha kârlıydı. Tek rejimde koşulduysa yeniden koş.",
    }
    if args.sample:
        print(f"\n--sample modu: JSON yazılmadı ({(datetime.now()-t0).seconds}sn)")
    else:
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\nJSON yazıldı: {OUT_JSON}  ({(datetime.now()-t0).seconds}sn)")


if __name__ == "__main__":
    main()
