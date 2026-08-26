# -*- coding: utf-8 -*-
"""
tepe_cikis_arastirma.py — GÜN-İÇİ TEPEDE NE OLUYOR + UYGULANABİLİR SAT KURALI (28 Tem 2026)

BAĞLAM: erken_hacim_backtest.py kanıtladı — ALARM adaylarının değeri ertesi gün
gün-içi TEPESİNDE (+%3,3) saklı; ama tepe açılışta değil, gün içinde geliyor
(açılıştan al→sat sadece +%0,2). Karne o tepeyi "kâr" sayıyor ama kimse tam
zirveden satamaz. SORU: tepe çevresinde ne oluyor, ve tepeyi UYGULANABİLİR bir
sat kuralıyla (sabit hedef / iz-süren stop / momentum dönüşü / saat-stop) ne
kadar yakalayabiliriz?

GİRİŞ VARSAYIMI: T-1 KAPANIŞtan al (17:30 yayını bunu mümkün kılar). Çıkış =
ertesi gün (T) gün içinde, saatlik bara göre mekanik kural.

VERİ: Yahoo saatlik, AYRI klasör veriler_saatlik/ — salt araştırma, hiçbir panel/
skor OKUMAZ, OHLC/parquet'e KARIŞMAZ (6 Tem Frankenstein çizgisi). Kazıma YOK.

Kullanım:
    python tepe_cikis_arastirma.py            # cache'i doldur (yoksa) + analiz
    python tepe_cikis_arastirma.py --analiz   # sadece analiz (cache dolu varsay)
"""
import glob
import os
import sys
import io
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
VERILER = os.path.join(BASE, "veriler")
SAATLIK = os.path.join(BASE, "veriler_saatlik")   # AYRI klasör — araştırma cache
CSV = os.path.join(BASE, "erken_hacim_backtest.csv")
TZ = "Europe/Istanbul"
ONLY_ANALYZE = "--analiz" in sys.argv


def saatlik_cek(tk, period="60d"):
    import yfinance as yf
    d = yf.download(f"{tk}.IS", interval="1h", period=period, progress=False, auto_adjust=False)
    if d is None or d.empty:
        return None
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d.index = d.index.tz_convert(TZ)
    d = d[~d.index.duplicated(keep="last")].sort_index()
    return d[["Open", "High", "Low", "Close", "Volume"]].dropna(how="all")


def cache_doldur(tickers):
    os.makedirs(SAATLIK, exist_ok=True)
    ok = miss = 0
    for i, tk in enumerate(tickers, 1):
        yol = os.path.join(SAATLIK, f"{tk}.IS_1h.parquet")
        if os.path.exists(yol):
            ok += 1
            continue
        try:
            d = saatlik_cek(tk)
            if d is None or d.empty:
                miss += 1
            else:
                d.to_parquet(yol)
                ok += 1
        except Exception:
            miss += 1
        if i % 25 == 0:
            print(f"  cache {i}/{len(tickers)} (ok {ok}, yok {miss})", flush=True)
        time.sleep(0.35)
    print(f"cache bitti: {ok} var, {miss} çekilemedi → {SAATLIK}", flush=True)


def _rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def eval_kurallar(day, entry, rsi_ser):
    """day = eval gününün saatlik OHLC (kronolojik). entry = T-1 kapanış.
    Döner: her kuralın gerçekleşen getirisi (%) + tepe bilgisi."""
    o = day["Open"].values; h = day["High"].values
    l = day["Low"].values; c = day["Close"].values
    rs = rsi_ser.reindex(day.index).values if rsi_ser is not None else np.full(len(day), np.nan)
    n = len(day)
    peak = h.max()
    peak_i = int(h.argmax())

    def ret(px):
        return (px / entry - 1) * 100

    out = {
        "n_bar": n,
        "peak_ret": ret(peak),
        "peak_bar": peak_i + 1,           # 1=ilk saat
        "open_ret": ret(o[0]),            # gap (açılışta sat)
        "close_ret": ret(c[-1]),          # kapanışta sat
    }

    # ── Kural 1-4: SABİT HEDEF (limit sat entry×(1+X)) ──
    for X in (1.0, 1.5, 2.0, 3.0):
        hedef = entry * (1 + X / 100)
        px = None
        for i in range(n):
            if o[i] >= hedef:             # açılış hedefin üstünde → açılışta dol
                px = o[i]; break
            if h[i] >= hedef:             # gün içinde hedefe değdi
                px = hedef; break
        out[f"hedef_{X:g}"] = ret(px if px is not None else c[-1])

    # ── Kural 5-6: İZ-SÜREN STOP (tepeden %Y geri çekilince sat) ──
    for Y in (1.0, 1.5, 2.0):
        run_hi = entry
        px = None
        for i in range(n):
            run_hi = max(run_hi, h[i])
            stop = run_hi * (1 - Y / 100)
            if o[i] <= stop:              # açılış stopun altında → açılışta çık
                px = o[i]; break
            if l[i] <= stop:
                px = stop; break
        out[f"iz_{Y:g}"] = ret(px if px is not None else c[-1])

    # ── Kural 7: MOMENTUM DÖNÜŞÜ (ilk yeşilden sonra ilk kırmızı saat kapanışında sat) ──
    px = None; gordu_yesil = False
    for i in range(n):
        if c[i] > o[i]:
            gordu_yesil = True
        elif gordu_yesil and c[i] < o[i]:
            px = c[i]; break
    out["mom_donus"] = ret(px if px is not None else c[-1])

    # ── Kural 8: RSI DÖNÜŞÜ (saatlik RSI >70 idi, altına kırınca kapanışta sat) ──
    px = None; asti = False
    for i in range(n):
        if not np.isnan(rs[i]):
            if rs[i] >= 70:
                asti = True
            elif asti and rs[i] < 70:
                px = c[i]; break
    out["rsi_donus"] = ret(px if px is not None else c[-1])

    # ── Kural 9-10: SAAT-STOP (N. saatte koşulsuz sat) ──
    for N in (2, 3):
        idx = min(N, n) - 1
        out[f"saat_{N}"] = ret(c[idx])

    return out


def analiz():
    D = pd.read_csv(CSV, parse_dates=["target"])
    A = D[D["in_alarm"]].copy()
    rows = []
    eksik = 0
    for _, r in A.iterrows():
        tk, t = r["tk"], r["target"]
        yol = os.path.join(SAATLIK, f"{tk}.IS_1h.parquet")
        dp = os.path.join(VERILER, f"{tk}.IS_1d.parquet")
        if not os.path.exists(yol) or not os.path.exists(dp):
            eksik += 1; continue
        try:
            hr = pd.read_parquet(yol)
            dd = pd.read_parquet(dp)
            if t not in dd.index:
                eksik += 1; continue
            entry = float(dd.loc[t, "Close"]) if np.ndim(dd.loc[t, "Close"]) == 0 else float(dd.loc[t, "Close"].iloc[-1])
            # eval günü = target'tan sonraki ilk saatlik-veri günü
            gunler = sorted(set(hr.index.date))
            fut = [g for g in gunler if g > t.date()]
            if not fut:
                eksik += 1; continue
            ev = fut[0]
            day = hr[hr.index.date == ev]
            if len(day) < 3 or entry <= 0:
                eksik += 1; continue
            rsi_ser = _rsi(hr["Close"])
            res = eval_kurallar(day, entry, rsi_ser)
            res.update({"tk": tk, "target": t.date(), "eval": ev,
                        "kat": r["kat"], "rejim": r["rejim"]})
            rows.append(res)
        except Exception:
            eksik += 1; continue

    R = pd.DataFrame(rows)
    if R.empty:
        print("analiz için veri yok (cache boş olabilir)"); return
    R.to_csv(os.path.join(BASE, "tepe_cikis_arastirma.csv"), index=False)
    print(f"\nDeğerlendirilen ALARM olayı: {len(R)}  (saatlik/entry eksik: {eksik})")
    print(f"Ortalama gün-içi bar: {R['n_bar'].mean():.1f}\n")

    # ── Tepe zamanlaması ──
    print("=" * 92)
    print("1) TEPE NE ZAMAN GELİYOR? (saatlik bar sırası, 1=açılış saati)")
    print("=" * 92)
    vc = R["peak_bar"].value_counts().sort_index()
    for bar, cnt in vc.items():
        print(f"  {int(bar)}. saat: {cnt:4d} olay  (%{100*cnt/len(R):.0f})  "
              + "█" * int(40 * cnt / vc.max()))
    ilk3 = (R["peak_bar"] <= 3).mean() * 100
    print(f"\n  Tepe ilk 3 saatte gelen olay: %{ilk3:.0f}")

    # ── Kural karşılaştırması ──
    print("\n" + "=" * 92)
    print("2) ÇIKIŞ KURALLARI — gerçekleşen getiri (giriş = T-1 kapanış)")
    print("   (referans: PEAK = ulaşılamaz tavan · GAP/open = açılışta sat · CLOSE = kapanışta tut)")
    print("=" * 92)
    kural_ad = {
        "peak_ret": "🔺 PEAK (ulaşılamaz)", "open_ret": "GAP açılışta sat",
        "close_ret": "kapanışta tut",
        "hedef_1": "hedef +%1.0", "hedef_1.5": "hedef +%1.5", "hedef_2": "hedef +%2.0",
        "hedef_3": "hedef +%3.0",
        "iz_1": "iz-stop %1.0", "iz_1.5": "iz-stop %1.5", "iz_2": "iz-stop %2.0",
        "mom_donus": "momentum dönüşü", "rsi_donus": "RSI>70 dönüşü",
        "saat_2": "2. saatte sat", "saat_3": "3. saatte sat",
    }
    print(f"{'KURAL':<22} {'ort%':>7} {'medyan%':>8} {'poz%':>6} {'std':>6} {'kötü5%':>7}")
    print("-" * 92)
    order = ["peak_ret", "open_ret", "close_ret", "hedef_1", "hedef_1.5", "hedef_2",
             "hedef_3", "iz_1", "iz_1.5", "iz_2", "mom_donus", "rsi_donus", "saat_2", "saat_3"]
    for k in order:
        s = R[k].dropna()
        if s.empty:
            continue
        p5 = np.percentile(s, 5)
        print(f"{kural_ad[k]:<22} {s.mean():>+7.2f} {s.median():>+8.2f} "
              f"{(s > 0).mean()*100:>5.0f}% {s.std():>6.2f} {p5:>+7.2f}")

    # ── En iyi uygulanabilir kural (peak/open/close hariç) ──
    aday = [k for k in order if k not in ("peak_ret", "open_ret", "close_ret")]
    means = {k: R[k].mean() for k in aday}
    best = max(means, key=means.get)
    print("\n" + "─" * 92)
    print(f"EN YÜKSEK ORTALAMA (uygulanabilir kural): {kural_ad[best]} → {means[best]:+.2f}%")
    print(f"  vs GAP açılışta sat {R['open_ret'].mean():+.2f}%  ·  vs kapanışta tut {R['close_ret'].mean():+.2f}%")
    print(f"  ⚠ komisyon ~binde 1-2 · tek rejim (60g, ağırlıkla zayıf/yatay) — tek koşu KANIT DEĞİL")

    # ── Rejim kırılımı: en iyi kural rejime göre ──
    print("\n" + "=" * 92)
    print(f"3) '{kural_ad[best]}' REJİME GÖRE (dayanıklı mı, yoksa tek rejim mi?)")
    print("=" * 92)
    for rj in ["HIZLI_RALLI", "ILIMLI_YUKARI", "YATAY", "ZAYIF", "DUSUS"]:
        s = R[R["rejim"] == rj][best].dropna()
        if len(s) < 5:
            print(f"  {rj:<16} (yetersiz örnek: {len(s)})"); continue
        print(f"  {rj:<16} N={len(s):<4} ort {s.mean():>+6.2f}%  poz %{(s>0).mean()*100:.0f}")

    print(f"\n✅ tam tablo: tepe_cikis_arastirma.csv ({len(R)} olay)")


def main():
    if not ONLY_ANALYZE:
        D = pd.read_csv(CSV)
        tickers = sorted(D[D["in_alarm"]]["tk"].unique())
        print(f"ALARM tekil ticker: {len(tickers)} → saatlik cache dolduruluyor")
        cache_doldur(tickers)
    analiz()


if __name__ == "__main__":
    main()
