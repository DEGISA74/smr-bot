# -*- coding: utf-8 -*-
"""
backtest_kirmizi_mavi_long.py — "Kırmızı→Mavi dönüş" LONG hipotezi hakemliği
============================================================================
Amaç (30 Tem 2026): Kullanıcının tarif ettiği LONG kurulumunu backtest'le ölç.
Hipotez (giriş günü i'de HEPSİ doğruysa AL):
  1) Para Akış İvmesi barı son 1-3 günde KIRMIZIDAN MAVİYE döndü
     (compute_flow_momentum mf_blend ≤0 iken >0'a geçti)
  2) OBV yükseliyor (bugün > 5 gün önce)
  3) CMF > 0 (20g para akışı pozitif)
  4) STP eğimi yukarı (stp bugün > dün — barın omurgası S1>0)

Hakem = TAZE parquet forward getiri (5/10/20 gün). Baseline + leave-one-out
ablation + rejim kırılımı ile kanıt tabanlı hüküm. [[feedback-extrapolation-yasak]]

BIST100 = repo'da net üye listesi YOK → son `days` günün en likit 100 hissesi
(ortalama TL cirosu = mean(Close×Volume)) proxy olarak alınır (endeksler hariç).

Çıktı: konsol tablo + backtest_kirmizi_mavi_long.json
Kullanım: python backtest_kirmizi_mavi_long.py [--days 180] [--top 100]
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
from indicators import compute_flow_momentum, compute_obv_series
from signal_policy import MEASUREMENT_REGIME_RISING, measurement_regime_series

VERI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veriler")
OUT_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_kirmizi_mavi_long.json")
IDX_PREFIX = ("XU", "XB", "XT", "XY", "XG", "XK", "XL", "XM", "XS", "XW", "X0")


def cmf_series(df, period=20):
    """Rolling CMF serisi — indicators.compute_cmf ile aynı formül (per-gün)."""
    h, l, c, v = df["High"], df["Low"], df["Close"], df["Volume"]
    rng = (h - l).replace(0, np.nan)
    mfm = ((c - l) - (h - c)) / rng
    mfv = (mfm * v).fillna(0.0)
    return mfv.rolling(period).sum() / v.rolling(period).sum().replace(0, np.nan)


def build_universe(files, days, top):
    """En likit `top` hisse — son `days` günün ortalama TL cirosu (Close×Volume)."""
    liq = []
    for f in files:
        base = os.path.basename(f)
        tick = base.split(".")[0]
        if tick.startswith(IDX_PREFIX):
            continue
        try:
            df = pd.read_parquet(f, columns=["Close", "Volume"])
            if df is None or len(df) < days + 30:
                continue
            tail = df.tail(days)
            turnover = float((tail["Close"] * tail["Volume"]).mean())
            if np.isfinite(turnover) and turnover > 0:
                liq.append((turnover, f))
        except Exception:
            continue
    liq.sort(reverse=True)
    return [f for _, f in liq[:top]]


def xu100_regime():
    """XU100 tarih → rejim (+1 fiyat SMA50 üstü / -1 altı). Yoksa boş dict."""
    for name in ("XU100.IS_1d.parquet",):
        p = os.path.join(VERI_DIR, name)
        if os.path.exists(p):
            try:
                x = pd.read_parquet(p)
                states = measurement_regime_series(x)
                return {d: (1 if state == MEASUREMENT_REGIME_RISING else -1)
                        for d, state in states.dropna().items()}
            except Exception:
                pass
    return {}


def collect(df, days, regime):
    """Giriş günlerini topla. Her koşulun bool serisini üretip 5 varyant için maske döndürür.
    Döndürür: list of dict(rows) — masks + fwd getiriler + rejim."""
    n = len(df)
    if n < 80:
        return []
    mf, stp = compute_flow_momentum(df)
    if mf is None or stp is None:
        return []
    c = df["Close"].astype(float)
    v = df["Volume"].astype(float)
    obv = compute_obv_series(df)
    cmf = cmf_series(df)
    vma20 = v.rolling(20).mean()

    mf = mf.astype(float); stp = stp.astype(float)
    # Koşul serileri
    blue = mf > 0
    was_red = (mf.shift(1) <= 0) | (mf.shift(2) <= 0) | (mf.shift(3) <= 0)
    c_flip = blue & was_red                      # 1) kırmızı→mavi dönüş
    c_obv = obv > obv.shift(5)                    # 2) OBV yükseliyor
    c_cmf = cmf > 0                               # 3) CMF pozitif
    c_stp = stp.diff() > 0                        # 4) STP eğimi yukarı

    rows = []
    lo = max(50, n - days - 20)                   # değerlendirme penceresi başı
    for i in range(lo, n - 20):                   # +20 forward garanti
        vol_i = float(v.iloc[i]); vma_i = float(vma20.iloc[i])
        if vol_i <= 0 or not np.isfinite(vma_i) or vma_i <= 0:
            continue
        cp0 = float(c.iloc[i])
        if cp0 <= 0:
            continue
        f5 = (float(c.iloc[i + 5]) / cp0 - 1) * 100
        f10 = (float(c.iloc[i + 10]) / cp0 - 1) * 100
        f20 = (float(c.iloc[i + 20]) / cp0 - 1) * 100
        rows.append({
            "flip": bool(c_flip.iloc[i]), "obv": bool(c_obv.iloc[i]),
            "cmf": bool(c_cmf.iloc[i]), "stp": bool(c_stp.iloc[i]),
            "f5": f5, "f10": f10, "f20": f20,
            "reg": regime.get(df.index[i], 0),
        })
    return rows


def summ(rows, key):
    if not rows:
        return None
    a5 = [r["f5"] for r in rows]; a10 = [r["f10"] for r in rows]; a20 = [r["f20"] for r in rows]
    def st(a):
        return {"n": len(a), "ort": round(float(np.mean(a)), 2),
                "isabet": round(float(np.mean([1 if x > 0 else 0 for x in a]) * 100), 1),
                "medyan": round(float(np.median(a)), 2)}
    return {"5g": st(a5), "10g": st(a10), "20g": st(a20)}


def prow(label, s):
    if not s:
        print(f"{label:34s}  (veri yok)"); return
    print(f"{label:34s}  N={s['5g']['n']:>6d}  "
          f"5g {s['5g']['ort']:+6.2f}%/%{s['5g']['isabet']:<4.1f}  "
          f"10g {s['10g']['ort']:+6.2f}%/%{s['10g']['isabet']:<4.1f}  "
          f"20g {s['20g']['ort']:+6.2f}%/%{s['20g']['isabet']:<4.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--top", type=int, default=100)
    args = ap.parse_args()

    all_files = sorted(glob.glob(os.path.join(VERI_DIR, "*.IS_1d.parquet")))
    t0 = datetime.now()
    print(f"=== KIRMIZI→MAVİ LONG BACKTEST — BIST{args.top} × son {args.days} gün ===")
    print("Evren seçiliyor (likidite)...")
    files = build_universe(all_files, args.days, args.top)
    print(f"Evren: {len(files)} hisse (en likit {args.top}).")
    regime = xu100_regime()
    print(f"XU100 rejim haritası: {len(regime)} gün.")

    ALL = []
    done = 0
    for f in files:
        try:
            df = pd.read_parquet(f)
            ALL.extend(collect(df, args.days, regime))
        except Exception as ex:
            print(f"  ! {os.path.basename(f)}: {ex}")
        done += 1
        if done % 25 == 0:
            print(f"  ... {done}/{len(files)} ({(datetime.now()-t0).seconds}sn)")

    print(f"\nToplam değerlendirilen gün (evren×gün): {len(ALL)}")
    if not ALL:
        print("Veri yok."); return

    # ── TAM KOMBO ────────────────────────────────────────────────────────────
    combo = [r for r in ALL if r["flip"] and r["obv"] and r["cmf"] and r["stp"]]
    s_combo = summ(combo, None)
    s_base = summ(ALL, None)

    print("\n" + "=" * 96)
    print("SONUÇ  (ort getiri % / isabet %) — forward 5/10/20 işlem günü")
    print("=" * 96)
    prow(">>> TAM KOMBO (4 koşul birden)", s_combo)
    prow("Baseline (tüm günler, filtresiz)", s_base)

    # ── LEAVE-ONE-OUT ABLATION (hangi koşul taşıyor?) ────────────────────────
    print("\n-- Leave-one-out (bir koşulu ÇIKAR, kalan 3 ile) --")
    keys = ["flip", "obv", "cmf", "stp"]
    names = {"flip": "kırmızı→mavi dönüş", "obv": "OBV yükseliş",
             "cmf": "CMF>0", "stp": "STP eğim yukarı"}
    for drop in keys:
        keep = [k for k in keys if k != drop]
        sub = [r for r in ALL if all(r[k] for k in keep)]
        prow(f"  [-{names[drop]}]", summ(sub, None))

    # ── TEK KOŞUL (marjinal güç) ─────────────────────────────────────────────
    print("\n-- Tek koşul (yalnız o filtre) --")
    for k in keys:
        sub = [r for r in ALL if r[k]]
        prow(f"  yalnız {names[k]}", summ(sub, None))

    # ── REJİM KIRILIMI (tam kombo) ───────────────────────────────────────────
    print("\n-- Tam kombo rejim kırılımı (XU100 SMA50) --")
    up = [r for r in combo if r["reg"] == 1]
    dn = [r for r in combo if r["reg"] == -1]
    prow("  YÜKSELEN rejim", summ(up, None))
    prow("  DÜŞEN/YATAY rejim", summ(dn, None))

    # ── HÜKÜM ────────────────────────────────────────────────────────────────
    # Kıyas: bilinen en iyi LONG (Pre-Launch BOS ~%45 isabet / ~%15 20g ret; ER A1 ~%67/%4)
    print("\n" + "=" * 96)
    if s_combo:
        c10, c20 = s_combo["10g"], s_combo["20g"]
        b10 = s_base["10g"]
        edge10 = round(c10["ort"] - b10["ort"], 2)
        if c10["n"] < 40:
            verdict = f"YETERSİZ ÖRNEK (N={c10['n']}) — hüküm için gün sayısını/evreni artır."
        elif c20["ort"] >= 4.0 and c20["isabet"] >= 55 and edge10 > 0:
            verdict = (f"GÜÇLÜ — 20g ort %{c20['ort']} / isabet %{c20['isabet']}, "
                       f"baseline'a 10g edge {edge10:+}%. Taramaya + AI'a değer.")
        elif c20["ort"] > b10["ort"] and edge10 > 0.5:
            verdict = (f"ORTA — baseline'ı geçiyor (10g edge {edge10:+}%) ama en iyi LONG'ları "
                       f"(Pre-Launch BOS/ER A1) net geçmiyor. İkinci rejimde tekrar ölç.")
        else:
            verdict = (f"ZAYIF — baseline'a edge {edge10:+}% (10g). Ekstra değer yok, "
                       f"tarama yazma. Bileşenler zaten panelde.")
    else:
        verdict = "Sinyal üretilmedi."
    print(f">>> HÜKÜM: {verdict}")
    print("=" * 96)

    out = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "universe_note": f"BIST{args.top} proxy = son {args.days} gün en likit {len(files)} hisse (TL cirosu)",
        "eval_days": args.days,
        "n_eval_total": len(ALL),
        "combo": s_combo, "baseline": s_base,
        "regime_up": summ(up, None), "regime_down": summ(dn, None),
        "verdict": verdict,
        "note": "Tek rejimde koşulduysa 2. rejimde yeniden koş. [[feedback-extrapolation-yasak]]",
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nJSON yazıldı: {OUT_JSON}  ({(datetime.now()-t0).seconds}sn)")


if __name__ == "__main__":
    main()
