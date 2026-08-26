# -*- coding: utf-8 -*-
"""
erken_hacim_backtest.py — EKONOMİK ÖNERME TESTİ (28 Tem 2026)

SORU (kazıma YOK, elimdeki günlük veriyle):
  Tavan/yüksek-getiri listesi ŞU AN sabah 09:45 çıkıyor → aday gap'li açılıyor →
  o açılıştan alınca kâr gap'e gidiyor. Eğer listeyi T-1 seansı KAPANMADAN ÖNCE
  (~17:30) versek, abone KAPANIŞtan alabilir → gap'i o cebe atar.

  Bu backtest tam bunu ölçer: motoru her T-1 gününde çalıştır, ALARM/TOP-N'i seç,
  İKİ farklı giriş senaryosunu karşılaştır:
    ESKİ (sabah yayın):  ertesi gün AÇILIŞtan al   → open[T+1] baz
    YENİ (17:30 yayın):  T-1 KAPANIŞtan al          → close[T-1] baz
  ve çıkışları (ertesi gün open / high / close) piyasa-nötr alfa ile raporlar.

  ⚠ Motor = tavan_engine (TEK KAYNAK, canlı ile aynı). Skora/panele DOKUNMAZ,
    salt okur. "Erken hacim gerçekten kazandırıyor mu?" kapısı — geçmezse fikir durur.
"""
import glob
import os
import sys
import io
import warnings

import numpy as np
import pandas as pd

import tavan_engine as te

warnings.filterwarnings("ignore")
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
VERILER = os.path.join(BASE, "veriler")
LOOKBACK = int(sys.argv[sys.argv.index("--gun") + 1]) if "--gun" in sys.argv else 250
TOPN_LIST = [7, 12]
ENDEKS = ("XU100", "XU030", "XU050", "XBANK", "XUSIN", "XUMAL")

# ─── Veri yükle ───
print("Parquetler yükleniyor...")
ALL = {}
for f in glob.glob(f"{VERILER}/*.IS_1d.parquet"):
    tk = os.path.basename(f).replace(".IS_1d.parquet", "")
    if tk in ENDEKS:
        continue
    try:
        d = pd.read_parquet(f)
        if len(d) >= 100:
            ALL[tk] = d
    except Exception:
        pass
xu = pd.read_parquet(f"{VERILER}/XU100.IS_1d.parquet")
print(f"{len(ALL)} hisse + XU100 yüklendi.")

ref = next(iter(ALL.values()))
all_dates = ref.index.tolist()
target_dates = all_dates[-LOOKBACK - 1:-1]   # T+1 hesaplanabilsin diye son günü atla
print(f"Pencere: {target_dates[0].date()} → {target_dates[-1].date()} ({len(target_dates)} gün)\n")


def scan_one_day(target):
    """T-1 (target) kapanışıyla listeyi üret. Motor = tavan_engine (canlı ile birebir)."""
    if target not in xu.index:
        return None, "BILINMEZ"
    i_xu = xu.index.get_loc(target)
    rejim, _ = te.detect_rejim(xu["Close"], i_xu, lookback=10)
    agr = te.REJIM_AGIRLIK[rejim]
    rows = []
    for tk, df in ALL.items():
        if target not in df.index:
            continue
        i = df.index.get_loc(target)
        feat = te.features(df, i)
        if feat is None or feat["vol_tl"] < te.MIN_VOL_TL:
            continue
        sc = te.score_row(feat, agr)
        rows.append({"tk": tk, "skor": round(sc["skor"], 1), "kat": sc["kat"]})
    if not rows:
        return None, rejim
    return pd.DataFrame(rows).sort_values("skor", ascending=False).reset_index(drop=True), rejim


def nextbar(df, target):
    """target'ın BİR SONRAKİ barı (T+1). Yoksa None."""
    if target not in df.index:
        return None, None
    i = df.index.get_loc(target)
    if i + 1 >= len(df):
        return None, None
    return df.iloc[i], df.iloc[i + 1]


# ─── Piyasa-nötr baz: XU100'ün aynı penceredeki getirisi ───
def xu_ret(target, mode):
    t, n = nextbar(xu, target)
    if t is None:
        return np.nan
    base = float(t["Close"])          # T-1 kapanış
    if mode == "close_to_open":
        return (float(n["Open"]) / base - 1) * 100
    if mode == "close_to_close":
        return (float(n["Close"]) / base - 1) * 100
    if mode == "open_to_close":       # ESKİ: açılıştan al, kapanışta sat
        return (float(n["Close"]) / float(n["Open"]) - 1) * 100
    return np.nan


# ─── Ana döngü: her gün, her aday için giriş/çıkış getirileri ───
rec = []   # her satır: bir aday-gün
for _ti, target in enumerate(target_dates, 1):
    if _ti % 10 == 0:
        print(f"  {_ti}/{len(target_dates)} gün...", flush=True)
    df_scan, rejim = scan_one_day(target)
    if df_scan is None:
        continue
    alarm = set(df_scan[df_scan["skor"] >= te.ALARM_ESIK]["tk"])
    topn = {n: set(df_scan.head(n)["tk"]) for n in TOPN_LIST}
    for _, r in df_scan.iterrows():
        tk = r["tk"]
        df = ALL[tk]
        t, n = nextbar(df, target)
        if t is None:
            continue
        base_close = float(t["Close"])
        nopen, nhigh, nclose = float(n["Open"]), float(n["High"]), float(n["Close"])
        if base_close <= 0 or nopen <= 0:
            continue
        rec.append({
            "target": target, "tk": tk, "skor": r["skor"], "kat": r["kat"],
            "rejim": rejim,
            "in_alarm": tk in alarm,
            "in_top7": tk in topn[7],
            "in_top12": tk in topn[12],
            # YENİ (kapanıştan al):
            "kapanis_acilis": (nopen / base_close - 1) * 100,     # gap yakalama
            "kapanis_zirve": (nhigh / base_close - 1) * 100,      # iyimser tavan
            "kapanis_kapanis": (nclose / base_close - 1) * 100,   # gün sonu
            # ESKİ (açılıştan al):
            "acilis_zirve": (nhigh / nopen - 1) * 100,
            "acilis_kapanis": (nclose / nopen - 1) * 100,
            # piyasa-nötr baz (XU100 aynı gün):
            "xu_ka": xu_ret(target, "close_to_open"),
            "xu_kk": xu_ret(target, "close_to_close"),
            "xu_ak": xu_ret(target, "open_to_close"),
        })

D = pd.DataFrame(rec)
if D.empty:
    print("veri yok — çık"); sys.exit()

D.to_csv(os.path.join(BASE, "erken_hacim_backtest.csv"), index=False)


def blok(mask, ad):
    s = D[mask]
    if s.empty:
        return f"{ad:<26} (aday yok)"
    n = len(s)
    # YENİ: kapanıştan al
    ka = s["kapanis_acilis"]          # gap yakalama (net, komisyonsuz)
    kk = s["kapanis_kapanis"]         # kapanıştan al, ertesi gün kapanışta sat
    kz = s["kapanis_zirve"]           # kapanıştan al, ertesi gün zirvede sat (iyimser)
    # ESKİ: açılıştan al
    ak = s["acilis_kapanis"]
    # piyasa-nötr alfa (kapanış→kapanış)
    alfa_kk = (s["kapanis_kapanis"] - s["xu_kk"]).mean()
    alfa_ka = (s["kapanis_acilis"] - s["xu_ka"]).mean()
    return (
        f"{ad:<26} N={n:<6} "
        f"| GAP(kap→açılış) ort {ka.mean():+.2f}%  poz%{(ka > 0).mean()*100:.0f}  α{alfa_ka:+.2f}  "
        f"| KAP→KAP ort {kk.mean():+.2f}%  poz%{(kk > 0).mean()*100:.0f}  α{alfa_kk:+.2f}  "
        f"| ESKİ(açılış→kap) ort {ak.mean():+.2f}%  poz%{(ak > 0).mean()*100:.0f}  "
        f"| iyimser(kap→zirve) {kz.mean():+.2f}%"
    )


print("=" * 120)
print("EKONOMİK ÖNERME — YENİ (T-1 kapanıştan al) vs ESKİ (T açılıştan al)")
print("piyasa-nötr α = getiri − XU100 aynı pencere · GAP = gecelik açılış farkı (17:30 yayının asıl kazancı)")
print("=" * 120)
print(blok(D["in_alarm"], "ALARM (skor≥150)"))
for nn in TOPN_LIST:
    print(blok(D[f"in_top{nn}"], f"TOP {nn}"))
print(blok(D["skor"] >= 100, "skor≥100"))
print(blok(pd.Series(True, index=D.index), "TÜM HAVUZ (baz çizgi)"))

print("\n" + "─" * 120)
print("KALIP BAZLI (ALARM içinde) — kap→açılış gap:")
for k in ["A", "C", "E", "D"]:
    print("  ", blok(D["in_alarm"] & (D["kat"] == k), f"kat {k}"))

print("\n" + "─" * 120)
print("REJİM BAZLI (ALARM) — kap→açılış gap:")
for rj in ["HIZLI_RALLI", "ILIMLI_YUKARI", "YATAY", "ZAYIF", "DUSUS"]:
    print("  ", blok(D["in_alarm"] & (D["rejim"] == rj), rj))

# ─── ÖZET KARAR ───
a = D[D["in_alarm"]]
print("\n" + "=" * 120)
print("KARAR SİNYALİ:")
if not a.empty:
    gap = a["kapanis_acilis"]
    old = a["acilis_kapanis"]
    print(f"  ALARM adayı, T-1 kapanıştan al → ertesi açılışta sat: ort {gap.mean():+.2f}% "
          f"(poz %{(gap > 0).mean()*100:.0f}, medyan {gap.median():+.2f}%)")
    print(f"  ESKİ yol (açılıştan al → kapanışta sat):              ort {old.mean():+.2f}% "
          f"(poz %{(old > 0).mean()*100:.0f})")
    print(f"  → Erken yayının kattığı GAP farkı: {gap.mean() - old.mean():+.2f} puan/işlem")
print(f"\n✅ tam tablo: erken_hacim_backtest.csv ({len(D)} aday-gün)")
