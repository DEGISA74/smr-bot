# -*- coding: utf-8 -*-
"""
ayak_izi_backtest.py — KURUMSAL AYAK İZİ TEZİ TESTİ (28 Tem 2026)

TEZ (memory/project-saatlik-hacim-probu): Kurumlar emirleri kapanışa yakın çalıştırır
(VWAP/kapanış hedefi). Bir hissede hacim gün içinde giderek KAPANIŞA KAYIYORSA (birkaç
gün üst üste), bu kurumsal birikim = sonraki 5/10 günde XU100'ü GEÇME öngörüsü olabilir.

ÖLÇÜLEN TEK SORU: "Hacmi kapanışa kayan günler, sonraki 5/10 günde XU100'ü geçiyor mu?"
YÖNTEM (intraday_4s_olcum kalıbı): ayak-izi göstergesini dilimlere böl, dilim başına
ortalama piyasa-nötr alfa. MONOTON eğim varsa gösterge ayırıyor demektir.

VERİ AYRIMI (temiz): AYAK İZİ göstergesi → saatlik (veriler_saatlik/, ayrı klasör).
FORWARD getiri → günlük parquet (veriler/, uzun geçmiş). Saatlik yalnız göstergeyi
üretir; skora/panele/OHLC'ye KARIŞMAZ. Kazıma YOK.

⚠ LİKİT evren (tez orta/küçük ölçekte gürültü der). Tek koşu KANIT değil, keşif
  taraması — [[feedback-extrapolation-yasak]]. Monoton çıksa bile ayrı doğrulama ister.

Kullanım:
    python ayak_izi_backtest.py --n 150     # top-150 likit; eksik saatlikleri çek + analiz
    python ayak_izi_backtest.py --analiz     # sadece analiz (cache dolu)
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
SAATLIK = os.path.join(BASE, "veriler_saatlik")
TZ = "Europe/Istanbul"
ENDEKS = ("XU100", "XU030", "XU050", "XBANK", "XUSIN", "XUMAL", "XUTUM")
ONLY = "--analiz" in sys.argv
NTOP = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 150
FWD = [5, 10]
DILIM = 5
MIN_BAR = 6           # geçerli gün için en az 6 saatlik bar


def likit_evren(n):
    liq = {}
    for f in glob.glob(os.path.join(VERILER, "*.IS_1d.parquet")):
        s = os.path.basename(f).replace(".IS_1d.parquet", "")
        if s in ENDEKS or s.upper().startswith(("XU", "XB", "XT", "XY")):
            continue
        try:
            d = pd.read_parquet(f)
            if len(d) < 200:
                continue
            liq[s] = float((d["Close"] * d["Volume"]).tail(60).median())
        except Exception:
            pass
    return [s for s, _ in sorted(liq.items(), key=lambda x: -x[1])[:n]]


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
    yeni = 0
    for i, tk in enumerate(tickers, 1):
        yol = os.path.join(SAATLIK, f"{tk}.IS_1h.parquet")
        if os.path.exists(yol):
            continue
        try:
            d = saatlik_cek(tk)
            if d is not None and not d.empty:
                d.to_parquet(yol); yeni += 1
        except Exception:
            pass
        time.sleep(0.35)
        if yeni and yeni % 20 == 0:
            print(f"  +{yeni} yeni saatlik çekildi...", flush=True)
    print(f"cache hazır: {yeni} yeni indirildi (gerisi vardı) → {SAATLIK}", flush=True)


def gunluk_ayak_izi(hr):
    """Saatlik seriden GÜN BAZINDA ayak-izi göstergeleri.
    Döner: DataFrame index=gün(date), kolon= gec_pay, oglesonu_oran, vwat, rvol_gun."""
    recs = []
    for gun, g in hr.groupby(hr.index.date):
        g = g.sort_index()
        v = g["Volume"].astype(float).values
        n = len(g)
        if n < MIN_BAR or v.sum() <= 0:
            continue
        tot = v.sum()
        gec_pay = v[-2:].sum() / tot                      # son 2 saat hacim payı
        yari = n // 2
        ilk = v[:yari].sum(); son = v[yari:].sum()
        oglesonu = son / ilk if ilk > 0 else np.nan       # öğle sonrası / öncesi
        idx = np.arange(n)
        vwat = (idx * v).sum() / tot / (n - 1)            # hacim ağırlıklı zaman (0..1)
        recs.append({"gun": pd.Timestamp(gun), "gec_pay": gec_pay,
                     "oglesonu": oglesonu, "vwat": vwat, "gun_hacim": tot})
    if not recs:
        return None
    df = pd.DataFrame(recs).set_index("gun").sort_index()
    df["rvol_gun"] = df["gun_hacim"] / df["gun_hacim"].rolling(20, min_periods=5).mean()
    # 3 günlük düzleştirme — "üst üste kapanışa kayma" (tezin özü)
    df["gec_pay_3g"] = df["gec_pay"].rolling(3).mean()
    df["vwat_3g"] = df["vwat"].rolling(3).mean()
    return df


def analiz():
    # XU100 günlük forward
    xu = pd.read_parquet(os.path.join(VERILER, "XU100.IS_1d.parquet"))
    xu_close = xu["Close"].astype(float)
    xu_fwd = {k: (xu_close.shift(-k) / xu_close - 1) * 100 for k in FWD}

    parcalar = []
    dosyalar = glob.glob(os.path.join(SAATLIK, "*.IS_1h.parquet"))
    kullanildi = 0
    for f in dosyalar:
        tk = os.path.basename(f).replace(".IS_1h.parquet", "")
        dp = os.path.join(VERILER, f"{tk}.IS_1d.parquet")
        if not os.path.exists(dp):
            continue
        try:
            hr = pd.read_parquet(f)
            ai = gunluk_ayak_izi(hr)
            if ai is None or len(ai) < 5:
                continue
            dd = pd.read_parquet(dp)
            dclose = dd["Close"].astype(float)
            # gün date → günlük parquet timestamp eşle
            dmap = {ts.date(): ts for ts in dd.index}
            t = pd.DataFrame(index=ai.index)
            for col in ("gec_pay", "oglesonu", "vwat", "rvol_gun", "gec_pay_3g", "vwat_3g"):
                t[col] = ai[col]
            fwd_ok = {k: [] for k in FWD}
            for k in FWD:
                vals = []
                for gun in ai.index:
                    ts = dmap.get(gun.date())
                    if ts is None or ts not in dd.index:
                        vals.append(np.nan); continue
                    pos = dd.index.get_loc(ts)
                    if pos + k >= len(dd):
                        vals.append(np.nan); continue
                    r = (dclose.iloc[pos + k] / dclose.iloc[pos] - 1) * 100
                    xr = xu_fwd[k].reindex([ts]).iloc[0] if ts in xu_fwd[k].index else np.nan
                    vals.append(r - xr)      # piyasa-nötr alfa
                t[f"alfa{k}"] = vals
            t["tk"] = tk
            parcalar.append(t)
            kullanildi += 1
        except Exception:
            continue

    if not parcalar:
        print("veri yok"); return
    D = pd.concat(parcalar)
    D.to_csv(os.path.join(BASE, "ayak_izi_backtest.csv"))
    print(f"\nkullanılan hisse: {kullanildi} | gözlem: {len(D):,}")
    print(f"forward pencere: {FWD} işlem günü · piyasa-nötr (XU100 çıkarılmış)\n")

    OLC = [("gec_pay", "son 2s hacim payı"), ("gec_pay_3g", "son 2s payı (3g ort)"),
           ("vwat", "hacim ağırlıklı saat"), ("vwat_3g", "hacim ağ. saat (3g ort)"),
           ("oglesonu", "öğle sonrası/öncesi"), ("rvol_gun", "gün rvol (kontrol)")]

    for k in FWD:
        print("=" * 96)
        print(f"AYAK İZİ → sonraki {k} gün ALFA (dilim başına ort %) — MONOTON eğim = ayırıyor")
        print("=" * 96)
        print(f"{'GÖSTERGE':<26} {'en alt':>7} {'2':>7} {'3':>7} {'4':>7} {'en üst':>7} {'YAYILIM':>8} {'düzenli':>8} {'N':>7}")
        print("-" * 96)
        rows = []
        for col, ad in OLC:
            v = D[[col, f"alfa{k}"]].dropna()
            if len(v) < 2000:
                continue
            try:
                v["d"] = pd.qcut(v[col], DILIM, labels=False, duplicates="drop")
            except Exception:
                continue
            g = v.groupby("d")[f"alfa{k}"].mean()
            if len(g) < DILIM:
                continue
            yay = float(g.iloc[-1] - g.iloc[0])
            mono = (g.diff().dropna() > 0).all() or (g.diff().dropna() < 0).all()
            rows.append((ad, g.iloc[0], g.iloc[1], g.iloc[2], g.iloc[3], g.iloc[-1],
                         yay, "EVET" if mono else "-", len(v)))
        rows.sort(key=lambda r: abs(r[6]), reverse=True)
        for ad, a, b, c, d4, e, yay, mono, N in rows:
            print(f"{ad:<26} {a:>+7.2f} {b:>+7.2f} {c:>+7.2f} {d4:>+7.2f} {e:>+7.2f} "
                  f"{yay:>+8.2f} {mono:>8} {N:>7,}")
        print()

    # ── rvol'den bağımsız mı? En güçlü ayak-izi göstergesini rvol dilimine göre ayır ──
    print("=" * 96)
    print("BAĞIMSIZLIK KONTROLÜ: ayak izi (gec_pay_3g üst dilim) alfa'sı, sadece yüksek hacim mi?")
    print("=" * 96)
    v = D[["gec_pay_3g", "rvol_gun", "alfa5"]].dropna()
    if len(v) > 2000:
        v["ai_ust"] = v["gec_pay_3g"] >= v["gec_pay_3g"].quantile(0.8)
        v["rv_ust"] = v["rvol_gun"] >= v["rvol_gun"].quantile(0.8)
        tab = v.groupby(["rv_ust", "ai_ust"])["alfa5"].agg(["mean", "size"])
        print("  (satır: rvol üst%20 · sütun: ayak-izi üst%20) → 5g alfa ort")
        print(tab.round(3).to_string())
    print(f"\n✅ tam tablo: ayak_izi_backtest.csv")


def main():
    if not ONLY:
        ev = likit_evren(NTOP)
        print(f"likit evren: {len(ev)} hisse · eksik saatlikler çekiliyor")
        cache_doldur(ev)
    analiz()


if __name__ == "__main__":
    main()
