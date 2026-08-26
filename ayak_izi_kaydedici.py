# -*- coding: utf-8 -*-
"""
ayak_izi_kaydedici.py — KURUMSAL AYAK İZİ VERİ TOPLAYICI (28 Tem 2026)

NE: Her likit hissenin GÜNLÜK "hacim kapanışa kayma" göstergelerini üretir ve AYRI
    bir loga append eder. Amaç: ileriye-dönük, örtüşmeyen, TEMİZ veri biriktirmek —
    böylece birkaç hafta + ikinci rejim sonra ayak-izi tezini DÜRÜSTÇE karara bağlarız.

NEDEN SADECE TOPLAYICI: ayak_izi_sinyal_test.py, 55 günlük geri-doldurulmuş veriyle
    kenarı NE doğruladı NE öldürdü (örtüşmeyen fark +0,07pp, GA sıfırı kapsıyor,
    yaygınlık %48). Kanıt için temiz forward veri şart. Bu yüzden: skor İDDİASI YOK.

⚠ KIRMIZI ÇİZGİLER (6 Tem Frankenstein):
  - Intraday hacim / saatlik OHLC → OHLC'ye, veriler/*.parquet'e, panele KARIŞMAZ.
  - Hiçbir skor/tarama bu logu OKUMAZ. Sadece ilerideki backtest okuyacak.
  - Kazıma YOK — yalnız Yahoo saatlik (halka açık, gecikmeli).
  - Append-only + idempotent: aynı (gün, hisse) iki kez yazılmaz.

DEPO: ayak_izi_kayit/ayak_izi_log.parquet   (ayrı klasör)
Kolonlar: gun, tk, kapanis, gun_hacim, n_bar, gec_pay, oglesonu, vwat, adv_tl
  (rvol / 3g-düzleştirme / forward getiri → ANALİZ anında hesaplanır, log ham kalır.)

Kullanım:
    python ayak_izi_kaydedici.py --seed         # veriler_saatlik/ cache'inden geçmişi doldur
    python ayak_izi_kaydedici.py                # günlük: likit evreni taze çek, yeni günleri ekle
    python ayak_izi_kaydedici.py --n 500        # evren büyüklüğü (varsayılan 300)
    python ayak_izi_kaydedici.py --durum        # log özeti
"""
import glob
import os
import sys
import io
import time

import numpy as np
import pandas as pd

import warnings
warnings.filterwarnings("ignore")
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
VERILER = os.path.join(BASE, "veriler")
SAATLIK_CACHE = os.path.join(BASE, "veriler_saatlik")     # --seed kaynağı
DEPO = os.path.join(BASE, "ayak_izi_kayit")               # AYRI klasör
LOG = os.path.join(DEPO, "ayak_izi_log.parquet")
TZ = "Europe/Istanbul"
SEANS_KAPANIS = 18, 35        # bu saatten sonra bugün "tamamlanmış" sayılır
MIN_BAR = 6
KOLONLAR = ["gun", "tk", "kapanis", "gun_hacim", "n_bar", "gec_pay", "oglesonu", "vwat", "adv_tl"]

SEED = "--seed" in sys.argv
DURUM = "--durum" in sys.argv
NTOP = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 300


# ───────── gösterge (ayak_izi_backtest ile AYNI tanım) ─────────
def gun_ozellikleri(g):
    """Bir günün saatlik OHLCV'si → ayak-izi göstergeleri (dict) | None."""
    g = g.sort_index()
    v = g["Volume"].astype(float).values
    n = len(g)
    if n < MIN_BAR or v.sum() <= 0:
        return None
    tot = v.sum()
    yari = n // 2
    ilk, son = v[:yari].sum(), v[yari:].sum()
    idx = np.arange(n)
    return {
        "kapanis": float(g["Close"].iloc[-1]),
        "gun_hacim": float(tot),
        "n_bar": int(n),
        "gec_pay": float(v[-2:].sum() / tot),                 # son 2 saat hacim payı
        "oglesonu": float(son / ilk) if ilk > 0 else np.nan,  # öğle sonrası/öncesi
        "vwat": float((idx * v).sum() / tot / (n - 1)),       # hacim ağırlıklı saat (0..1)
    }


def _adv_tl(tk):
    """Günlük parquet'ten 20g medyan TL hacim (likidite etiketi — analizde filtre)."""
    p = os.path.join(VERILER, f"{tk}.IS_1d.parquet")
    if not os.path.exists(p):
        return np.nan
    try:
        d = pd.read_parquet(p)
        return float((d["Close"] * d["Volume"]).tail(20).median())
    except Exception:
        return np.nan


# ───────── log G/Ç (append-only, idempotent) ─────────
def _load_log():
    if os.path.exists(LOG):
        try:
            return pd.read_parquet(LOG)
        except Exception:
            pass
    return pd.DataFrame(columns=KOLONLAR)


def _append(rows):
    if not rows:
        print("yeni satır yok."); return 0
    os.makedirs(DEPO, exist_ok=True)
    cur = _load_log()
    var = set(zip(cur["gun"].astype(str), cur["tk"])) if len(cur) else set()
    yeni = [r for r in rows if (str(r["gun"]), r["tk"]) not in var]
    if not yeni:
        print("hepsi zaten logda (idempotent)."); return 0
    out = pd.concat([cur, pd.DataFrame(yeni, columns=KOLONLAR)], ignore_index=True)
    out["gun"] = pd.to_datetime(out["gun"]).dt.date.astype(str)
    out = out.drop_duplicates(["gun", "tk"]).sort_values(["gun", "tk"]).reset_index(drop=True)
    out.to_parquet(LOG, index=False)
    print(f"+{len(yeni)} yeni satır yazıldı → {LOG}  (toplam {len(out)})")
    return len(yeni)


def _gun_tamam_mi(gun_date):
    """Bu gün 'tamamlanmış' (tüm seans bitmiş) mı? Bugünse ancak kapanış saatinden sonra."""
    now = pd.Timestamp.now(tz=TZ)
    if gun_date < now.date():
        return True
    if gun_date == now.date():
        return (now.hour, now.minute) >= SEANS_KAPANIS
    return False


def _hisseden_gunler(tk, hr):
    """Bir hissenin saatlik df'inden, TAMAMLANMIŞ her günün ayak-izi satırlarını üret."""
    adv = _adv_tl(tk)
    rows = []
    for gun, g in hr.groupby(hr.index.date):
        if not _gun_tamam_mi(gun):
            continue
        f = gun_ozellikleri(g)
        if f is None:
            continue
        f.update({"gun": pd.Timestamp(gun).date(), "tk": tk, "adv_tl": adv})
        rows.append({k: f[k] for k in KOLONLAR})
    return rows


# ───────── mod: seed (cache'ten geçmiş) ─────────
def seed():
    dosyalar = glob.glob(os.path.join(SAATLIK_CACHE, "*.IS_1h.parquet"))
    if not dosyalar:
        print("veriler_saatlik/ boş — önce cache doldurulmalı (ayak_izi_backtest.py)."); return
    print(f"seed: {len(dosyalar)} saatlik dosyadan geçmiş doldruluyor...")
    rows = []
    for f in dosyalar:
        tk = os.path.basename(f).replace(".IS_1h.parquet", "")
        try:
            hr = pd.read_parquet(f)
            hr.index = pd.to_datetime(hr.index)
            rows.extend(_hisseden_gunler(tk, hr))
        except Exception:
            continue
    _append(rows)


# ───────── mod: günlük (taze çek) ─────────
def gunluk():
    import yfinance as yf
    # likit evren (günlük TL hacim)
    liq = {}
    for f in glob.glob(os.path.join(VERILER, "*.IS_1d.parquet")):
        s = os.path.basename(f).replace(".IS_1d.parquet", "")
        if s.upper().startswith(("XU", "XB", "XT", "XY")):
            continue
        try:
            d = pd.read_parquet(f)
            if len(d) >= 200:
                liq[s] = float((d["Close"] * d["Volume"]).tail(60).median())
        except Exception:
            pass
    evren = [s for s, _ in sorted(liq.items(), key=lambda x: -x[1])[:NTOP]]
    print(f"günlük: {len(evren)} likit hisse taranıyor (son günler, taze saatlik)...")
    rows = []
    for i, tk in enumerate(evren, 1):
        try:
            d = yf.download(f"{tk}.IS", interval="1h", period="7d", progress=False, auto_adjust=False)
            if d is None or d.empty:
                continue
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.get_level_values(0)
            d.index = d.index.tz_convert(TZ)
            d = d[~d.index.duplicated(keep="last")].sort_index()
            rows.extend(_hisseden_gunler(tk, d[["Open", "High", "Low", "Close", "Volume"]].dropna(how="all")))
        except Exception:
            pass
        time.sleep(0.3)
        if i % 50 == 0:
            print(f"  {i}/{len(evren)}...", flush=True)
    _append(rows)


def durum():
    L = _load_log()
    if L.empty:
        print("log boş."); return
    print(f"log: {len(L):,} satır · {L['tk'].nunique()} hisse · {L['gun'].nunique()} gün")
    print(f"tarih: {L['gun'].min()} → {L['gun'].max()}")
    print(f"gün başına ort hisse: {len(L)/L['gun'].nunique():.0f}")
    print("\nson 5 gün:")
    for g, sub in list(L.groupby("gun"))[-5:]:
        print(f"  {g}: {len(sub)} hisse · gec_pay ort {sub['gec_pay'].mean():.3f}")


def main():
    if DURUM:
        durum(); return
    if SEED:
        seed()
    else:
        gunluk()
    print()
    durum()


if __name__ == "__main__":
    main()
