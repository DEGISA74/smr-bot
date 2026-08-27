# -*- coding: utf-8 -*-
"""
backtest_dip_ayna.py — "Tepe-satış modelinin tersi dibi bulur mu?" testi
=========================================================================
Soru (31 Tem 2026): ER D4/D5 (dağıtım/zayıflık dedektörleri) düşen rejimde
%66-67 isabetle düşüşü doğru biliyor. Kullanıcı sorusu: bunların AYNASI
(toplama/güç dedektörü) dipten dönüşü yakalar mı?

Bu script tahminle değil ÖLÇÜMLE cevaplar. [[feedback-extrapolation-yasak]]

Dedektörler (scanners.py D4/D5 kurallarından birebir türetildi):
  BASELINE (referans, SHORT olarak ölçülür — düşüş = kazanç):
    D4_orig  = distribution_count_5d >= 2
    D5_orig  = (fiyat<50MA) & dağıtım günü & RS60<-5
  SAF AYNA (LONG — D4/D5'in düz tersi):
    D4_ayna  = accumulation_count_5d >= 2          (5g'de 2+ hacimli YEŞİL)
    D5_ayna  = (fiyat>50MA) & toplama günü & RS60>+5
  GEÇİŞ / DİP DÖNÜŞ (LONG — "D5 yanıyordu, ŞİMDİ döndü"):
    D5_gecis = son 10g'de D5 en az 1 kez yandı  &  bugün toplama günü
               &  RS dönüyor (RS20<0 & RS5>0)  &  hâlâ ucuz (pos60<0.50)

Dağıtım günü = kırmızı & hacim>20g ort×1.5 · Toplama günü = yeşil & aynı hacim.
Giriş = TAZE tetik (dün nitelenmiyordu) → örtüşme kırpılır.
Evren = son 180g en likit N hisse. Hakem = TAZE parquet forward getiri (5/10/20g)
+ XU100 alpha + düşen-rejim alt kırılımı.

Kullanım: python backtest_dip_ayna.py [--top 120]
Çıktı: konsol + backtest_dip_ayna.json
"""
import sys, os, glob, json, argparse
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import pandas as pd
from signal_policy import MEASUREMENT_REGIME_RISING, measurement_regime_series

VERI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veriler")
OUT_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_dip_ayna.json")
IDX_PREFIX = ("XU", "XB", "XT", "XY", "XG", "XK", "XL", "XM", "XS", "XW", "X0")


def build_universe(files, days, top):
    liq = []
    for f in files:
        if os.path.basename(f).split(".")[0].startswith(IDX_PREFIX):
            continue
        try:
            df = pd.read_parquet(f, columns=["Close", "Volume"])
            if df is None or len(df) < days + 80:
                continue
            t = float((df.tail(days)["Close"] * df.tail(days)["Volume"]).mean())
            if np.isfinite(t) and t > 0:
                liq.append((t, f))
        except Exception:
            continue
    liq.sort(reverse=True)
    return [f for _, f in liq[:top]]


def load_xu100():
    p = os.path.join(VERI_DIR, "XU100.IS_1d.parquet")
    if not os.path.exists(p):
        return None, {}
    x = pd.read_parquet(p)
    states = measurement_regime_series(x)
    reg = {d: (1 if state == MEASUREMENT_REGIME_RISING else -1)
           for d, state in states.dropna().items()}
    return x["Close"], reg


def collect(df, xu_close, reg_map):
    n = len(df)
    if n < 90:
        return []
    c = df["Close"].astype(float)
    o = df["Open"].astype(float)
    v = df["Volume"].astype(float)
    if v.isnull().all():
        return []

    sma50 = c.rolling(50).mean()
    avg_vol20 = v.rolling(20).mean()

    # Gün tipleri
    dist_day = (c < o) & (v > avg_vol20 * 1.5)   # dağıtım (hacimli kırmızı)
    accu_day = (c > o) & (v > avg_vol20 * 1.5)   # toplama (hacimli yeşil)
    dist_5 = dist_day.rolling(5).sum()
    accu_5 = accu_day.rolling(5).sum()

    # RS vs XU100
    if xu_close is not None:
        xu = xu_close.reindex(df.index).ffill()
        def rs(d):
            return ((c / c.shift(d) - 1) - (xu / xu.shift(d) - 1)) * 100
        rs5, rs20, rs60 = rs(5), rs(20), rs(60)
    else:
        rs5 = rs20 = rs60 = pd.Series(0.0, index=df.index)

    above50 = c > sma50
    pos60 = (c - c.rolling(60).min()) / (c.rolling(60).max() - c.rolling(60).min()).replace(0, np.nan)

    # === Dedektörler (bool seri) ===
    D4_orig = dist_5 >= 2
    D5_orig = (~above50) & dist_day & (rs60 < -5)
    D4_ayna = accu_5 >= 2
    D5_ayna = above50 & accu_day & (rs60 > 5)
    rs_turning = (rs20 < 0) & (rs5 > 0)
    D5_fired_recent = D5_orig.rolling(10).sum().shift(1) >= 1   # son 10g'de yandı (bugün hariç)
    D5_gecis = D5_fired_recent & accu_day & rs_turning & (pos60 < 0.50)

    dets = {"D4_orig": (D4_orig, "short"), "D5_orig": (D5_orig, "short"),
            "D4_ayna": (D4_ayna, "long"), "D5_ayna": (D5_ayna, "long"),
            "D5_gecis": (D5_gecis, "long")}

    rows = {k: [] for k in dets}
    idx = df.index
    for name, (sig, side) in dets.items():
        s = sig.fillna(False)
        for i in range(60, n - 20):
            if not bool(s.iloc[i]):
                continue
            if bool(s.iloc[i - 1]):     # taze değil → örtüşme kırp
                continue
            cp0 = float(c.iloc[i])
            if cp0 <= 0 or not np.isfinite(cp0):
                continue
            def fret(k):
                cp = float(c.iloc[i + k])
                return (cp / cp0 - 1) * 100 if np.isfinite(cp) else None
            xret = {}
            if xu_close is not None:
                x0 = float(xu.iloc[i]) if np.isfinite(xu.iloc[i]) else None
                for k in (5, 10, 20):
                    xk = float(xu.iloc[i + k]) if np.isfinite(xu.iloc[i + k]) else None
                    xret[k] = (xk / x0 - 1) * 100 if (x0 and xk) else None
            rows[name].append({"side": side,
                               "f5": fret(5), "f10": fret(10), "f20": fret(20),
                               "x5": xret.get(5), "x10": xret.get(10), "x20": xret.get(20),
                               "reg": reg_map.get(idx[i], 0)})
    return rows


def summ(rows):
    """side'a göre yönlü özet. long: kazanç=ret>0. short: kazanç=ret<0 (getiri short kârı olarak +'ya çevrilir)."""
    if not rows:
        return None
    side = rows[0]["side"]
    out = {}
    for k, xk in ((5, "x5"), (10, "x10"), (20, "x20")):
        fk = f"f{k}"
        vals = [(r[fk], r[xk]) for r in rows if r[fk] is not None]
        if not vals:
            out[f"{k}g"] = None
            continue
        rets = np.array([a for a, _ in vals])
        # yönlü getiri: long=+ret, short=-ret (short kazancı)
        dret = rets if side == "long" else -rets
        alphas = []
        for a, x in vals:
            if x is None:
                continue
            alphas.append((a - x) if side == "long" else (x - a))
        out[f"{k}g"] = {
            "n": len(rets),
            "ort": round(float(np.mean(dret)), 2),
            "isabet": round(float(np.mean(dret > 0) * 100), 1),
            "alpha": round(float(np.mean(alphas)), 2) if alphas else None,
            "medyan": round(float(np.median(dret)), 2),
        }
    return out


def prow(label, s):
    if not s or not s.get("20g"):
        print(f"{label:26s}  (N=0/yetersiz)")
        return
    def cell(w):
        d = s.get(w)
        if not d:
            return f"{w:>3} —"
        a = f"a{d['alpha']:+5.1f}" if d["alpha"] is not None else "a  —"
        return f"{w} {d['ort']:+6.2f}%/%{d['isabet']:<4.1f} {a}"
    print(f"{label:26s} N={s['20g']['n']:>4d} | " + " | ".join(cell(w) for w in ("5g", "10g", "20g")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=120)
    ap.add_argument("--liq_days", type=int, default=180)
    args = ap.parse_args()

    all_files = sorted(glob.glob(os.path.join(VERI_DIR, "*.IS_1d.parquet")))
    t0 = datetime.now()
    print(f"=== DİP AYNA TESTİ — D4/D5 tersi dibi bulur mu? — BIST{args.top}, TÜM GEÇMİŞ ===")
    files = build_universe(all_files, args.liq_days, args.top)
    xu_close, reg_map = load_xu100()
    print(f"Evren: {len(files)} hisse | XU100 rejim günü: {len(reg_map)}\n")

    AGG = {k: [] for k in ("D4_orig", "D5_orig", "D4_ayna", "D5_ayna", "D5_gecis")}
    done = 0
    for f in files:
        try:
            r = collect(pd.read_parquet(f), xu_close, reg_map)
            for k in AGG:
                AGG[k].extend(r.get(k, []))
        except Exception as ex:
            print(f"  ! {os.path.basename(f)}: {ex}")
        done += 1
        if done % 30 == 0:
            print(f"  ... {done}/{len(files)} ({(datetime.now()-t0).seconds}sn)")

    print("\n" + "=" * 104)
    print("Yönlü getiri (ort %/isabet %, a=alpha vs XU100) — long: yukarı kazanç · short: aşağı kazanç")
    print("=" * 104)
    S = {k: summ(v) for k, v in AGG.items()}
    print("\n-- BASELINE (referans, SHORT olarak) --")
    prow("D4_orig (dağıtım×2)", S["D4_orig"])
    prow("D5_orig (trend bozuk)", S["D5_orig"])
    print("\n-- SAF AYNA (LONG, D4/D5 düz tersi) --")
    prow("D4_ayna (toplama×2)", S["D4_ayna"])
    prow("D5_ayna (güç devam)", S["D5_ayna"])
    print("\n-- GEÇİŞ / DİP DÖNÜŞ (LONG) --")
    prow("D5_gecis (dönüş teyidi)", S["D5_gecis"])

    # Düşen rejim alt kırılımı
    print("\n-- SADECE düşen rejim (XU100<SMA50) --")
    Sdn = {}
    for k, v in AGG.items():
        dn = [r for r in v if r["reg"] == -1]
        Sdn[k] = summ(dn)
        prow("  " + k, Sdn[k])

    # Hüküm
    print("\n" + "=" * 104)
    def g20(s):
        return s.get("20g") if s else None
    lines = []
    for k, adı in (("D4_ayna", "Saf ayna D4"), ("D5_ayna", "Saf ayna D5"), ("D5_gecis", "Geçiş dedektörü")):
        d = g20(S[k])
        if not d or d["n"] < 40:
            lines.append(f"{adı}: YETERSİZ ÖRNEK (N={d['n'] if d else 0}) — hüküm yok.")
        elif d["ort"] >= 2.0 and (d["alpha"] or 0) >= 1.5:
            lines.append(f"{adı}: DEĞERLİ — 20g +{d['ort']}% / alpha {d['alpha']:+} (N={d['n']}). Kanıtlı LONG adayı.")
        elif d["ort"] > 0:
            lines.append(f"{adı}: ZAYIF POZİTİF — 20g +{d['ort']}% / alpha {d['alpha']} (N={d['n']}). Tek başına yetmez, teyitle.")
        else:
            lines.append(f"{adı}: BAŞARISIZ — 20g {d['ort']}% / alpha {d['alpha']} (N={d['n']}). Ayna simetrisi ÇALIŞMIYOR.")
    for L in lines:
        print(">>> " + L)
    print("=" * 104)

    out = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "universe": len(files), "windows": [5, 10, 20],
           "tum_rejim": S, "dusen_rejim": Sdn, "hukum": lines,
           "note": "TAZE tetik. Baseline short olarak. Tek rejimse (düşüş) ikinci rejimde tekrar koş."}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nJSON yazıldı: {OUT_JSON}  ({(datetime.now()-t0).seconds}sn)")


if __name__ == "__main__":
    main()
