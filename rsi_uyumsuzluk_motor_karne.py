# -*- coding: utf-8 -*-
"""
rsi_uyumsuzluk_motor_karne.py — BULGU 2: iki RSI uyumsuzluk motorunun karnesi
=============================================================================
Soru: ekranda AYNI ANDA iki farkli RSI uyumsuzluk motoru konusuyor ve 650
hissede ikisinin de yon verdigi 124 vakanin 45'i (%36) TERS yonde. Hangisi
kalacak? Karar sezgiyle degil olcumle verilir.

  MOTOR A — GENEL OZET paneli (analysis_core icinde, sol panel "Gizli Poz")
    pencere: son 5 bar vs onceki 9 bar (-14:-5), High/Low uclarindan
    4 sinif: bull / bear / hidden_bull / hidden_bear (if-elif oncelikli)
    esik: RSI farki 2 puan

  MOTOR B — PA-DNA (ict_core icinde, sag panel "NEGATIF UYUMSUZLUK")
    pencere: son 5 bar vs onceki 15 bar (-20:-5), KAPANIS uclarindan
    2 sinif: bullish / bearish (+ neutral)
    filtreler: RSI<75, son mum kirmizi, fiyat SMA50'nin %20 ustunde degil
    NOT: canli kodda boga kontrolu ayi kontrolunden SONRA ve `elif` DEGIL
         -> ikisi birden tetiklenirse ayi hukmu sessizce EZILIYOR.
         Bu karne canli davranisi oldugu gibi olcer, ezilmeyi ayrica sayar.

Standalone: app.py'ye, taramalara, evidence'a, ekrana DOKUNMAZ. Salt okunur.

MUHURLER (sonuclar GORULMEDEN yazildi — 2 Eyl 2026):
  1. Uc vade olculur: T+5, T+10, T+20. Tek vadeden hukum verilmez.
     (kural: feedback_vade_sonradan_secilmez)
  2. Olcut ALFA: hisse getirisi - AYNI GUN XU100 getirisi. Sifira gore
     okumak yanlis. (kural: project_evren_tabani_mercegi)
  3. Isaret degisimi = gurultu. Bir kova uc vadede ayni isareti tasimiyorsa
     "ayrim yok" sayilir, ne kadar carpici olursa olsun.
  4. Kova basina N < 200 -> BELIRSIZ, hukum verilmez.
  5. Kazanan sonuca bakarak SECILMEZ. Gecme sarti onceden:
       (a) kovanin yonu ile alfasinin isareti UYUSACAK
           (boga kovasi pozitif alfa, ayi kovasi negatif alfa)
       (b) uc vadede de ayni isaret
       (c) baseline'dan |fark| >= 0.50 puan
     Iki motor da gecerse: yayilimi (en iyi kova - en kotu kova) buyuk olan.
     Ikisi de kalamazsa ikisi de kalmaz — ekrandan cikar.
  6. Look-ahead yok: her gun yalniz o gune kadarki bar kullanilir; getiri
     t kapanisindan t+N kapanisina.
  7. Evren: BIST hisseleri (.IS). Endeksler (XU/XB/XT/XY) ve XU100 disarida.
  8. Isabet = alfa > 0 orani (endeksi gecme orani), ham getiri degil.

Kullanim: python rsi_uyumsuzluk_motor_karne.py
Cikti   : logs/rsi_uyumsuzluk_motor_karne.md + .csv
"""
from __future__ import annotations
import sys, warnings
warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from pathlib import Path
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view as swv

ROOT = Path(__file__).resolve().parent
PQ = ROOT / "veriler"
OUT_MD = ROOT / "logs" / "rsi_uyumsuzluk_motor_karne.md"
OUT_CSV = ROOT / "logs" / "rsi_uyumsuzluk_motor_karne.csv"
BENCH = "XU100.IS_1d.parquet"

HORIZONS = (5, 10, 20)
MIN_N = 200            # muhur 4
ESIK_FARK = 0.50       # muhur 5c
WARMUP = 60            # RSI(14) + SMA50 oturana kadar gun yok


def rsi14(c: np.ndarray) -> np.ndarray:
    """Iki motorun da kullandigi BIREBIR ayni formul (rolling mean tabanli)."""
    s = pd.Series(c)
    d = s.diff()
    gain = d.where(d > 0, 0).rolling(14).mean()
    loss = (-d.where(d < 0, 0)).rolling(14).mean()
    return (100 - 100 / (1 + gain / loss)).to_numpy()


def motor_a(high, low, rsi, t_idx):
    """GENEL OZET motoru. Donen: sinif dizisi ('bull'/'bear'/'hidden_bull'/
    'hidden_bear'/'none'), t_idx ile ayni uzunlukta."""
    w5_lo, w9_lo = swv(low, 5), swv(low, 9)
    w5_hi, w9_hi = swv(high, 5), swv(high, 9)

    r_lo = (t_idx - 4) + w5_lo[t_idx - 4].argmin(axis=1)
    p_lo = (t_idx - 13) + w9_lo[t_idx - 13].argmin(axis=1)
    r_hi = (t_idx - 4) + w5_hi[t_idx - 4].argmax(axis=1)
    p_hi = (t_idx - 13) + w9_hi[t_idx - 13].argmax(axis=1)

    bull  = (low[r_lo] < low[p_lo])   & (rsi[r_lo] > rsi[p_lo] + 2)
    bear  = (high[r_hi] > high[p_hi]) & (rsi[r_hi] < rsi[p_hi] - 2)
    hbull = (low[r_lo] > low[p_lo])   & (rsi[r_lo] < rsi[p_lo] - 2)
    hbear = (high[r_hi] < high[p_hi]) & (rsi[r_hi] > rsi[p_hi] + 2)

    out = np.full(len(t_idx), "none", dtype=object)          # if / elif sirasi
    out[hbear] = "hidden_bear"
    out[hbull] = "hidden_bull"
    out[bear]  = "bear"
    out[bull]  = "bull"
    return out


def motor_b(close, open_, rsi, sma50, t_idx):
    """PA-DNA motoru. Donen: (sinif dizisi, ezilme bayragi).
    Ezilme = ayi kosulu tetiklendi ama boga kosulu uzerine yazdi (canli bug)."""
    w5_c, w15_c = swv(close, 5), swv(close, 15)
    w5_r, w15_r = swv(rsi, 5), swv(rsi, 15)

    c_max = w5_c[t_idx - 4].max(axis=1);  p_max = w15_c[t_idx - 19].max(axis=1)
    c_min = w5_c[t_idx - 4].min(axis=1);  p_min = w15_c[t_idx - 19].min(axis=1)
    r_cmax = w5_r[t_idx - 4].max(axis=1); r_pmax = w15_r[t_idx - 19].max(axis=1)
    r_cmin = w5_r[t_idx - 4].min(axis=1); r_pmin = w15_r[t_idx - 19].min(axis=1)

    rv = rsi[t_idx]
    is_red = close[t_idx] < open_[t_idx]
    parabolic = close[t_idx] > sma50[t_idx] * 1.20

    bear_raw = (c_max >= p_max) & (r_cmax < r_pmax) & (r_pmax > 60)
    bear = bear_raw & (rv < 75) & is_red & (~parabolic)
    bull = (c_min <= p_min) & (r_cmin > r_pmin) & (r_pmin < 45)

    out = np.full(len(t_idx), "neutral", dtype=object)
    out[bear] = "bearish"
    out[bull] = "bullish"          # canli kodda elif DEGIL -> ayiyi ezer
    return out, (bear & bull)


def yon_of(label):
    """Sinif -> yon (+1 boga / -1 ayi / 0). Gizli uyumsuzluk TREND DEVAMI
    demektir: gizli boga = yukari, gizli ayi = asagi."""
    return {"bull": 1, "hidden_bull": 1, "bullish": 1,
            "bear": -1, "hidden_bear": -1, "bearish": -1}.get(label, 0)


def main():
    files = sorted(p for p in PQ.glob("*_1d.parquet")
                   if ".IS_" in p.name
                   and not p.name.startswith(("XU", "XB", "XT", "XY")))
    print(f"Evren: {len(files)} BIST hissesi")

    # ── Benchmark: XU100 gunluk ileri getirileri (tarih -> ret) ──
    b = pd.read_parquet(PQ / BENCH)
    bc = b["Close"].astype(float).to_numpy()
    bench = {}
    for h in HORIZONS:
        r = np.full(len(bc), np.nan)
        r[:-h] = bc[h:] / bc[:-h] - 1.0
        bench[h] = pd.Series(r * 100.0, index=b.index)

    rows = []
    atlanan = 0
    for f in files:
        try:
            df = pd.read_parquet(f)
            if len(df) < WARMUP + max(HORIZONS) + 5:
                atlanan += 1
                continue
            c = df["Close"].astype(float).to_numpy()
            o = df["Open"].astype(float).to_numpy()
            h_ = df["High"].astype(float).to_numpy()
            l_ = df["Low"].astype(float).to_numpy()
            if not np.isfinite(c).all() or (c <= 0).any():
                atlanan += 1
                continue

            rsi = rsi14(c)
            sma50 = pd.Series(c).rolling(50).mean().to_numpy()
            n = len(c)
            t_idx = np.arange(WARMUP, n - max(HORIZONS))
            if len(t_idx) == 0:
                atlanan += 1
                continue

            a_lbl = motor_a(h_, l_, rsi, t_idx)
            b_lbl, ezildi = motor_b(c, o, rsi, sma50, t_idx)

            rec = {"sym": f.name.split("_")[0], "date": df.index[t_idx],
                   "A": a_lbl, "B": b_lbl, "ezildi": ezildi}
            for hz in HORIZONS:
                ret = (c[t_idx + hz] / c[t_idx] - 1.0) * 100.0
                brt = bench[hz].reindex(df.index[t_idx]).to_numpy()
                rec[f"alfa{hz}"] = ret - brt
            rows.append(pd.DataFrame(rec))
        except Exception:
            atlanan += 1
            continue

    d = pd.concat(rows, ignore_index=True)
    d = d.dropna(subset=[f"alfa{h}" for h in HORIZONS])
    print(f"Gozlem: {len(d):,} hisse-gun  (atlanan dosya: {atlanan})")

    base = {h: d[f"alfa{h}"].mean() for h in HORIZONS}
    base_hit = (d["alfa10"] > 0).mean() * 100
    print(f"BASELINE alfa: T+5 {base[5]:+.2f} · T+10 {base[10]:+.2f} · "
          f"T+20 {base[20]:+.2f} · endeksi gecme %{base_hit:.1f}")

    def kova_tablo(col, isim):
        out = []
        for lbl, g in d.groupby(col):
            r = {"motor": isim, "kova": lbl, "N": len(g),
                 "yon": yon_of(lbl),
                 "isabet10": (g["alfa10"] > 0).mean() * 100}
            for h in HORIZONS:
                r[f"alfa{h}"] = g[f"alfa{h}"].mean()
                r[f"fark{h}"] = g[f"alfa{h}"].mean() - base[h]
            out.append(r)
        return pd.DataFrame(out).sort_values("alfa10", ascending=False)

    ta, tb = kova_tablo("A", "MOTOR A"), kova_tablo("B", "MOTOR B")

    def gecti_mi(t):
        """Muhur 5: yon-alfa uyumu + uc vadede ayni isaret + esik."""
        sinyal = t[(t["yon"] != 0) & (t["N"] >= MIN_N)]
        ok = []
        for _, r in sinyal.iterrows():
            isaretler = [np.sign(r[f"fark{h}"]) for h in HORIZONS]
            tutarli = len(set(isaretler)) == 1 and isaretler[0] != 0
            uyum = np.sign(r["fark10"]) == r["yon"]
            buyuk = abs(r["fark10"]) >= ESIK_FARK
            ok.append({"kova": r["kova"], "N": int(r["N"]), "yon": int(r["yon"]),
                       "fark5": r["fark5"], "fark10": r["fark10"],
                       "fark20": r["fark20"], "tutarli": tutarli,
                       "yon_uyumu": uyum, "esik": buyuk,
                       "GECTI": bool(tutarli and uyum and buyuk)})
        return pd.DataFrame(ok)

    ga, gb = gecti_mi(ta), gecti_mi(tb)

    # ── Celiski kovasi: iki motor ters yon dedigi gunler ──
    d["ya"], d["yb"] = d["A"].map(yon_of), d["B"].map(yon_of)
    cel = d[(d.ya != 0) & (d.yb != 0)]
    ters = cel[cel.ya != cel.yb]
    ayni = cel[cel.ya == cel.yb]
    ez = d[d["ezildi"]]

    L = []
    L.append("# 🔬 RSI UYUMSUZLUK — İKİ MOTORUN KARNESİ\n")
    L.append(f"_Üretim: {pd.Timestamp.now():%Y-%m-%d %H:%M} · "
             f"{len(files)} BIST hissesi · {len(d):,} hisse-gün_\n")
    L.append("**Ölçüt: ALFA** = hisse getirisi − aynı gün XU100 getirisi. "
             "İsabet = endeksi geçme oranı.\n")
    L.append(f"**Baseline (tüm günler):** T+5 `{base[5]:+.2f}` · "
             f"T+10 `{base[10]:+.2f}` · T+20 `{base[20]:+.2f}` · "
             f"endeksi geçme `%{base_hit:.1f}`\n")

    for isim, t in (("MOTOR A — GENEL ÖZET paneli", ta),
                    ("MOTOR B — PA-DNA (sağ panel)", tb)):
        L.append(f"\n## {isim}\n")
        L.append("| Kova | Yön | N | isabet | alfa T+5 | alfa T+10 | alfa T+20 |"
                 " fark T+10 |")
        L.append("|---|:--:|--:|--:|--:|--:|--:|--:|")
        for _, r in t.iterrows():
            y = {1: "🟢", -1: "🔴", 0: "·"}[r["yon"]]
            L.append(f"| `{r['kova']}` | {y} | {int(r['N']):,} | "
                     f"%{r['isabet10']:.1f} | {r['alfa5']:+.2f} | "
                     f"**{r['alfa10']:+.2f}** | {r['alfa20']:+.2f} | "
                     f"{r['fark10']:+.2f} |")

    L.append("\n## Mühür 5 — geçme sınavı\n")
    L.append("Şart: (a) kovanın yönü ile alfasının işareti uyuşacak, "
             "(b) üç vadede de aynı işaret, (c) baseline'dan |fark| ≥ 0,50 puan, "
             f"(d) N ≥ {MIN_N}.\n")
    for isim, g in (("MOTOR A", ga), ("MOTOR B", gb)):
        L.append(f"\n**{isim}**\n")
        if g.empty:
            L.append("_Sınava girecek kova yok (N yetersiz)._\n")
            continue
        L.append("| Kova | N | fark T+5 | fark T+10 | fark T+20 | 3 vade tutarlı |"
                 " yön uyumu | eşik | SONUÇ |")
        L.append("|---|--:|--:|--:|--:|:--:|:--:|:--:|:--:|")
        for _, r in g.iterrows():
            tik = lambda b: "✓" if b else "✗"
            L.append(f"| `{r['kova']}` | {r['N']:,} | {r['fark5']:+.2f} | "
                     f"{r['fark10']:+.2f} | {r['fark20']:+.2f} | "
                     f"{tik(r['tutarli'])} | {tik(r['yon_uyumu'])} | "
                     f"{tik(r['esik'])} | "
                     f"{'**GEÇTİ**' if r['GECTI'] else 'kaldı'} |")

    L.append("\n## Çelişki — iki motor ters dediğinde ne oluyor?\n")
    L.append("| Durum | N | alfa T+5 | alfa T+10 | alfa T+20 | isabet |")
    L.append("|---|--:|--:|--:|--:|--:|")
    for ad, g in (("İkisi de aynı yön", ayni), ("İkisi TERS yön", ters),
                  ("B'de ayı hükmü ezildi (canlı bug)", ez)):
        if len(g) == 0:
            continue
        L.append(f"| {ad} | {len(g):,} | {g['alfa5'].mean():+.2f} | "
                 f"**{g['alfa10'].mean():+.2f}** | {g['alfa20'].mean():+.2f} | "
                 f"%{(g['alfa10'] > 0).mean() * 100:.1f} |")
    if len(cel):
        L.append(f"\nİki motorun da yön verdiği **{len(cel):,}** günün "
                 f"**{len(ters):,}**'i ters yönde (**%{len(ters)/len(cel)*100:.0f}**).\n")

    OUT_MD.parent.mkdir(exist_ok=True)
    OUT_MD.write_text("\n".join(L), encoding="utf-8")
    pd.concat([ta, tb]).to_csv(OUT_CSV, index=False, encoding="utf-8")
    print(f"\nRapor: {OUT_MD}")
    print("\n".join(L[-14:]))


if __name__ == "__main__":
    main()
