# -*- coding: utf-8 -*-
"""
backtest_guclu_donus_filtre.py — Güçlü Dönüş: OBV+CMF teyit filtresi VAR/YOK
===========================================================================
Amaç (30 Tem 2026): "Düşen rejimde OBV+CMF dönüşü" tek başına orta çıktı (~4.5/10).
Tez: bir DÖNÜŞ taramasına EK TEYİT KATMANI olarak daha değerli — sahte dönüşleri
(düşen bıçak) eler. Bu script tezi ölçer: Güçlü Dönüş adaylarını (scanners.py
calculate_guclu_donus_adaylari birebir) tüm geçmişte üretir, sonra teyit filtresi
(OBV↑ 5g & CMF>0) VARken vs YOKken 5/10/20g getiriyi kıyaslar. [[feedback-extrapolation-yasak]]

Güçlü Dönüş kuralı (scanners.py v9): ZORUNLU 50≤RSI≤65 + 7 kriterden ≥6.
  P1 fiyat>EMA13 · P2 EMA13 5g eğim↑ · P3 RS>%2 (20g, XU100) · P4 RSI>RSI-EMA9
  · P5 OBV 5g↑ · P6 hacim 10g'de ≥5 kez ort üstü · P7 fiyat>yıllık VWAP
Teyit filtresi = OBV 5g↑ & CMF(20)>0  (P5 zaten OBV içeriyor → asıl yeni bilgi CMF).
Giriş = TAZE tetik (dün nitelenmiyordu, bugün nitelendi) → örtüşen pencere azaltılır.

Evren = son 180g en likit 100 hisse. Hakem = TAZE parquet forward getiri.
Çıktı: konsol + backtest_guclu_donus_filtre.json
Kullanım: python backtest_guclu_donus_filtre.py [--top 100]
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
from indicators import compute_obv_series

VERI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veriler")
OUT_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_guclu_donus_filtre.json")
IDX_PREFIX = ("XU", "XB", "XT", "XY", "XG", "XK", "XL", "XM", "XS", "XW", "X0")


def cmf_series(df, period=20):
    h, l, c, v = df["High"], df["Low"], df["Close"], df["Volume"]
    rng = (h - l).replace(0, np.nan)
    mfm = ((c - l) - (h - c)) / rng
    mfv = (mfm * v).fillna(0.0)
    return mfv.rolling(period).sum() / v.rolling(period).sum().replace(0, np.nan)


def rsi_series(close, period=14):
    d = close.diff()
    gain = d.where(d > 0, 0).rolling(period).mean()
    loss = (-d.where(d < 0, 0)).rolling(period).mean()
    return 100 - (100 / (1 + gain / loss.replace(0, np.nan)))


def build_universe(files, days, top):
    liq = []
    for f in files:
        if os.path.basename(f).split(".")[0].startswith(IDX_PREFIX):
            continue
        try:
            df = pd.read_parquet(f, columns=["Close", "Volume"])
            if df is None or len(df) < days + 30:
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
    sma = x["Close"].rolling(50).mean()
    reg = {d: (1 if c > s else -1) for d, c, s in zip(x.index, x["Close"], sma) if pd.notna(s)}
    return x["Close"], reg


def collect(df, xu_close, reg_map):
    n = len(df)
    if n < 60:
        return []
    c = df["Close"].astype(float)
    v = df["Volume"].astype(float)
    if v.isnull().all():
        return []

    rsi = rsi_series(c)
    ema13 = c.ewm(span=13, adjust=False).mean()
    rsi_ema9 = rsi.ewm(span=9, adjust=False).mean()
    obv = compute_obv_series(df)
    cmf = cmf_series(df)
    avg_vol20 = v.rolling(20).mean()
    vol_above = (v > avg_vol20).astype(float)
    vol_10g = vol_above.rolling(10).sum()
    vwap_annual = (c * v).cumsum() / v.cumsum().replace(0, np.nan)

    # RS vs XU100 (20g) — XU100'ü df tarihlerine hizala
    if xu_close is not None:
        xu = xu_close.reindex(df.index).ffill()
        hisse_ret20 = c / c.shift(20) - 1
        bist_ret20 = xu / xu.shift(20) - 1
        rs_pct = (hisse_ret20 - bist_ret20) * 100
    else:
        rs_pct = pd.Series(0.0, index=df.index)

    # 7 kriter (bool seri)
    p1 = c > ema13
    p2 = ema13 > ema13.shift(5)
    p3 = rs_pct > 2.0
    p4 = rsi > rsi_ema9
    p5 = obv > obv.shift(5)
    p6 = vol_10g >= 5
    p7 = c > vwap_annual
    score = (p1.astype(int) + p2.astype(int) + p3.astype(int) + p4.astype(int)
             + p5.astype(int) + p6.astype(int) + p7.astype(int))

    rsi_band = (rsi >= 50) & (rsi <= 65)
    qualified = rsi_band & (score >= 6)

    # Teyit filtresi
    cmf_pos = cmf > 0
    obv_up = obv > obv.shift(5)
    filt = obv_up & cmf_pos

    rows = []
    for i in range(25, n - 20):
        if not bool(qualified.iloc[i]):
            continue
        if bool(qualified.iloc[i - 1]):     # TAZE tetik değil → atla (örtüşme kırp)
            continue
        cp0 = float(c.iloc[i])
        if cp0 <= 0 or not np.isfinite(cp0):
            continue
        rows.append({
            "filt": bool(filt.iloc[i]),
            "cmf_pos": bool(cmf_pos.iloc[i]),
            "f5": (float(c.iloc[i + 5]) / cp0 - 1) * 100,
            "f10": (float(c.iloc[i + 10]) / cp0 - 1) * 100,
            "f20": (float(c.iloc[i + 20]) / cp0 - 1) * 100,
            "reg": reg_map.get(df.index[i], 0),
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
        print(f"{label:34s}  (N=0)"); return
    print(f"{label:34s}  N={s['5g']['n']:>5d}  "
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
    print(f"=== GÜÇLÜ DÖNÜŞ — OBV+CMF TEYİT FİLTRESİ VAR/YOK — BIST{args.top}, TÜM GEÇMİŞ ===")
    files = build_universe(all_files, args.liq_days, args.top)
    xu_close, reg_map = load_xu100()
    print(f"Evren: {len(files)} hisse | XU100 rejim günü: {len(reg_map)}")

    ALL = []
    done = 0
    for f in files:
        try:
            ALL.extend(collect(pd.read_parquet(f), xu_close, reg_map))
        except Exception as ex:
            print(f"  ! {os.path.basename(f)}: {ex}")
        done += 1
        if done % 25 == 0:
            print(f"  ... {done}/{len(files)} ({(datetime.now()-t0).seconds}sn)")

    print(f"\nToplam TAZE Güçlü Dönüş tetiği: {len(ALL)}")
    if not ALL:
        print("Sinyal yok."); return

    withf = [r for r in ALL if r["filt"]]
    without = [r for r in ALL if not r["filt"]]
    s_all, s_w, s_wo = summ(ALL), summ(withf), summ(without)

    print("\n" + "=" * 100)
    print("KIYAS (ort getiri % / isabet %) — forward 5/10/20g")
    print("=" * 100)
    prow("TÜM adaylar (filtresiz)", s_all)
    prow(">>> FİLTRELİ (OBV↑ & CMF>0)", s_w)
    prow("FİLTREYİ GEÇEMEYEN", s_wo)

    # Fark
    if s_w and s_wo:
        d20 = round(s_w["20g"]["ort"] - s_wo["20g"]["ort"], 2)
        h20 = round(s_w["20g"]["isabet"] - s_wo["20g"]["isabet"], 1)
        d20a = round(s_w["20g"]["ort"] - s_all["20g"]["ort"], 2)
        print(f"\nFark (filtreli − geçemeyen): 20g ort {d20:+}%  ·  isabet {h20:+} puan")
        print(f"Fark (filtreli − tüm)       : 20g ort {d20a:+}%")

    # Düşen rejim alt-kırılımı
    print("\n-- SADECE düşen rejim (XU100<SMA50) --")
    dn_all = [r for r in ALL if r["reg"] == -1]
    dn_w = [r for r in dn_all if r["filt"]]
    dn_wo = [r for r in dn_all if not r["filt"]]
    prow("  düşen — tüm adaylar", summ(dn_all))
    prow("  düşen — FİLTRELİ", summ(dn_w))
    prow("  düşen — geçemeyen", summ(dn_wo))

    # Hüküm
    print("\n" + "=" * 100)
    if not s_w or s_w["20g"]["n"] < 40:
        verdict = f"YETERSİZ ÖRNEK (filtreli N={s_w['20g']['n'] if s_w else 0}) — hüküm yok."
    else:
        d20 = s_w["20g"]["ort"] - s_wo["20g"]["ort"] if s_wo else s_w["20g"]["ort"]
        h20 = s_w["20g"]["isabet"] - (s_wo["20g"]["isabet"] if s_wo else 0)
        if d20 >= 2.0 and h20 >= 4:
            verdict = (f"FİLTRE DEĞER KATIYOR — filtreli 20g ort {d20:+.2f}% ve isabet {h20:+.1f} puan "
                       f"daha iyi. Teyit katmanı Güçlü Dönüş'e eklenmeye değer.")
        elif d20 >= 0.8 or h20 >= 3:
            verdict = (f"HAFİF KATKI — fark var ama küçük (20g {d20:+.2f}%, isabet {h20:+.1f}p). "
                       f"Zayıf tarama için kurtarıcı olmayabilir; ikinci rejimde tekrar ölç.")
        else:
            verdict = (f"KATKISIZ — filtre ayırt etmiyor (20g {d20:+.2f}%, isabet {h20:+.1f}p). "
                       f"Teyit katmanı Güçlü Dönüş'e değer katmıyor.")
    print(f">>> HÜKÜM: {verdict}")
    print("=" * 100)

    out = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "universe": len(files), "n_signals": len(ALL),
        "tum": s_all, "filtreli": s_w, "geçemeyen": s_wo,
        "dusen_tum": summ(dn_all), "dusen_filtreli": summ(dn_w), "dusen_gecemeyen": summ(dn_wo),
        "verdict": verdict,
        "note": "TAZE tetik (örtüşme kırpılmış). Filtre=OBV↑&CMF>0. Tek rejimse yeniden koş.",
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nJSON yazıldı: {OUT_JSON}  ({(datetime.now()-t0).seconds}sn)")


if __name__ == "__main__":
    main()
