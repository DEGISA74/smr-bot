# -*- coding: utf-8 -*-
"""
cikis_kurali_backtest.py — ÇIKIŞ KURALI ÖLÇÜMÜ (23 Tem 2026)

SORU: Erken Radar sinyalleri ortalama +%13 yukarı, -%9 aşağı gösterip SIFIRDA
bitiyor. Yani hareket var, tutamıyoruz. Hangi çıkış kuralı o hareketin ne
kadarını cebe koyar?

İKİ AİLE YARIŞIYOR:
  A) MEKANİK  — hedef / stop / takip eden stop / süre  (her üründe var)
  B) AKILLI PARA — CMF · akış ivmesi · OBV · UDVR dönüşü (BİZİM iddiamız)

B, A'yı yeniyorsa akıllı para okuması gerçekten değer üretiyor demektir.
Yenmiyorsa da bilmemiz gerekiyor — bu soru bugüne kadar hiç sorulmadı.

KURALLAR (sızıntı yok):
  · Giriş  : sinyal günü KAPANIŞTAN SONRA üretilir → ERTESİ GÜN kapanışında girilir.
  · Çıkış  : göstergeye dayalı çıkışlar da ertesi gün kapanışında uygulanır.
  · Aynı gün hem hedef hem stop görülürse → STOP önce sayılır (kötümser taraf).
  · Alfa   : işlemin getirisi − XU100'ün AYNI günlerdeki getirisi.
  · Sadece 'bullish' sinyaller (er_D4 gibi ayı senaryoları hariç).

⚠ Tek rejim uyarısı: ER sinyalleri 19 May 2026'da başladı. Pencere dar,
  son ayın 20 günlük sonucu olgunlaşmadı. YÖN okunur, rakam kesin değildir.
  [[feedback-extrapolation-yasak]]
"""
import os
import sqlite3
import numpy as np
import pandas as pd

import indicators as I

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "patron.db")
VERI = os.path.join(BASE, "veriler")
MAX_GUN = 20          # en fazla elde tutma
BENCH = "XU100.IS"


# ─────────────────────────────────────────────────────────────── veri ──
def _oku(sym):
    for ad in (f"{sym}.IS_1d.parquet", f"{sym}_1d.parquet"):
        p = os.path.join(VERI, ad)
        if os.path.exists(p):
            try:
                d = pd.read_parquet(p)
            except Exception:
                return None
            if d.empty or not {"Open", "High", "Low", "Close", "Volume"} <= set(d.columns):
                return None
            d = d[~d.index.duplicated(keep="last")].sort_index()
            return d
    return None


def sinyalleri_al():
    c = sqlite3.connect(DB)
    d = pd.read_sql(
        "SELECT scan_date, symbol, scan_type FROM scan_signals "
        "WHERE scan_type LIKE 'er%' AND bias='bullish'", c)
    c.close()
    d["scan_date"] = pd.to_datetime(d["scan_date"])
    # endeksler işlem enstrümanı değil — çıkış kuralı testinden çıkar
    d = d[~d["symbol"].str.upper().str.startswith(("XU", "XB", "XT", "XY"))]
    return d.drop_duplicates(["scan_date", "symbol", "scan_type"])


# ────────────────────────────────────────────── akıllı para serileri ──
def _cmf_seri(df, period):
    """Kayan CMF serisi — indicators.compute_cmf'in vektörel karşılığı."""
    h = df["High"].astype(float); l = df["Low"].astype(float)
    c = df["Close"].astype(float); v = df["Volume"].astype(float)
    genislik = (h - l).replace(0, np.nan)
    mfm = ((c - l) - (h - c)) / genislik
    mfv = (mfm * v).fillna(0.0)
    hac = v.rolling(period).sum().replace(0, np.nan)
    return (mfv.rolling(period).sum() / hac)


def cmf_dogrula():
    """Vektörel CMF, ürünün compute_cmf'i ile aynı mı? Farklıysa çalışma DURUR."""
    ornek = [f for f in os.listdir(VERI) if f.endswith("_1d.parquet")][:6]
    enbuyuk = 0.0
    for f in ornek:
        d = pd.read_parquet(os.path.join(VERI, f))
        if d.empty or len(d) < 60 or not {"High", "Low", "Close", "Volume"} <= set(d.columns):
            continue
        d = d[~d.index.duplicated(keep="last")].sort_index()
        for per in (5, 20):
            seri = _cmf_seri(d, per)
            for i in (len(d) - 1, len(d) - 7, len(d) - 30):
                bizim = seri.iloc[i]
                urun = I.compute_cmf(d.iloc[: i + 1], period=per)
                if pd.notna(bizim) and urun is not None:
                    enbuyuk = max(enbuyuk, abs(float(bizim) - float(urun)))
    print(f"CMF doğrulama — ürün fonksiyonuyla en büyük fark: {enbuyuk:.8f}")
    if enbuyuk > 1e-6:
        raise SystemExit("DUR: vektörel CMF ürünün hesabından sapıyor.")
    return True


def akilli_para_serileri(df):
    """Her BAR için akıllı para durumu (o günün kapanışıyla bilinebilir olan).
    Döner: DataFrame — cmf5, cmf20, ivme, obv_egim, udvr."""
    out = pd.DataFrame(index=df.index)
    n = len(df)

    # CMF iki pencere — VEKTÖREL (bar bar compute_cmf çağırmak O(n²), saatler sürer).
    # Formül indicators.compute_cmf ile birebir; doğrulaması cmf_dogrula() içinde.
    for ad, per in (("cmf5", 5), ("cmf20", 20)):
        out[ad] = _cmf_seri(df, per)

    # Para akış ivmesi — DİKKAT: compute_flow_momentum İKİ parça döner
    # (ivme barları, referans çizgi). İlk parça alınmazsa seri boş kalır ve
    # kural hiç ateşlemez — sessizce "sonuç yok" değil "ölçüm yok" olur.
    try:
        iv = I.compute_flow_momentum(df)
        if isinstance(iv, tuple):
            iv = iv[0]
        elif isinstance(iv, pd.DataFrame):
            iv = iv.iloc[:, 0]
        arr = np.asarray(iv, dtype=float).ravel()
        if arr.size < n:
            arr = np.concatenate([np.full(n - arr.size, np.nan), arr])
        out["ivme"] = pd.Series(arr[-n:], index=df.index)
    except Exception:
        out["ivme"] = np.nan

    # OBV eğimi (5 günlük değişim, ortalama hacme göre ölçeklenmiş)
    try:
        obv = pd.Series(np.asarray(I.compute_obv_series(df), dtype=float)[-n:], index=df.index)
        hac = df["Volume"].rolling(20).mean().replace(0, np.nan)
        out["obv_egim"] = (obv - obv.shift(5)) / hac
    except Exception:
        out["obv_egim"] = np.nan

    # UDVR — yükselen gün hacmi / düşen gün hacmi (5 gün)
    yon = np.sign(df["Close"].diff())
    up = (df["Volume"] * (yon > 0)).rolling(5).sum()
    dn = (df["Volume"] * (yon < 0)).rolling(5).sum().replace(0, np.nan)
    out["udvr"] = up / dn
    return out


def _israrli(kosul, gun):
    """Koşul 'gun' kez ÜST ÜSTE sağlandı mı (tek günlük gürültü elenir)."""
    k = kosul.fillna(False).astype(int)
    return k.rolling(gun).sum() >= gun


# ────────────────────────────────────────────────────── çıkış kuralı ──
def kurallari_uret():
    """(ad, aile, fonksiyon) — fonksiyon: (yol, ap) → (çıkış_bar, çıkış_getirisi)

    İKİ ÇIKIŞ TİPİ, İKİ FARKLI FİYAT:
      · SEVİYE emri (hedef/stop/takip): emir zaten piyasada duruyor → seviyenin
        KENDİSİNDEN çıkılır, o barın kapanışından değil.
      · GÖSTERGE/SÜRE: sinyal barın KAPANIŞIYLA bilinir → ERTESİ barın
        kapanışında çıkılır (aynı kapanıştan çıkmak geleceği bilmek olurdu).
    """
    K = []
    mek = lambda ad, fn: K.append((ad, "mekanik", fn))
    akl = lambda ad, fn: K.append((ad, "akıllı para", fn))

    # 0 — taban
    mek("0 · hiç dokunma (20g)", lambda y, ap: None)

    # 1 — sabit hedef (seviyeden)
    for h in (5, 8, 12):
        mek(f"1 · hedef +%{h}", lambda y, ap, h=h: _seviye(y, y["yuk"] >= h, h))

    # 2 — sabit stop (seviyeden)
    for s in (3, 5, 8):
        mek(f"2 · stop -%{s}", lambda y, ap, s=s: _seviye(y, y["dus"] <= -s, -s))

    # 3 — hedef + stop; aynı bar ikisi de görülürse STOP sayılır (kötümser)
    for h, s in ((8, 5), (12, 8), (6, 3)):
        mek(f"3 · +%{h} / -%{s}", lambda y, ap, h=h, s=s: _hedef_stop(y, h, s))

    # 4 — takip eden stop: tepeden geri çekilme; çıkış (tepe − pay) seviyesinden
    for g in (4, 6, 8):
        mek(f"4 · takip stop %{g}", lambda y, ap, g=g: _takip(y, g))

    # 5 — süre sınırı (kapanıştan)
    for t in (3, 5, 10):
        mek(f"5 · {t} gün sonra çık", lambda y, ap, t=t: _kapanis(y, t - 1))

    # 6 — yarı yarıya (+%6'da yarısı, kalanı %6 takip stop)
    mek("6 · yarısı +%6, kalanı takip", "YARIM")

    # 7 — MA20 altında kapanış (gösterge → ertesi bar)
    mek("7 · MA20 altında kapanış", lambda y, ap: _gosterge(y, y["ma20_alti"]))

    # 8-11 — AKILLI PARA (pencere × ısrar)
    for pen, kol in (("5g", "cmf5"), ("20g", "cmf20")):
        for isr in (1, 2, 3):
            akl(f"8 · CMF {pen} negatif ({isr}g ısrar)",
                lambda y, ap, kol=kol, isr=isr: _gosterge(y, _israrli(ap[kol] < 0, isr)))
    for isr in (1, 2, 3):
        akl(f"9 · akış ivmesi negatif ({isr}g ısrar)",
            lambda y, ap, isr=isr: _gosterge(y, _israrli(ap["ivme"] < 0, isr)))
        akl(f"10 · OBV dağıtımda ({isr}g ısrar)",
            lambda y, ap, isr=isr: _gosterge(y, _israrli(ap["obv_egim"] < 0, isr)))
        akl(f"11 · satıcı baskın UDVR<1 ({isr}g ısrar)",
            lambda y, ap, isr=isr: _gosterge(y, _israrli(ap["udvr"] < 1.0, isr)))

    # 12 — karma: akıllı para VEYA stop, hangisi önce gelirse
    for s in (5, 8):
        akl(f"12 · CMF20 negatif (2g) + stop -%{s}",
            lambda y, ap, s=s: _once(_gosterge(y, _israrli(ap["cmf20"] < 0, 2)),
                                     _seviye(y, y["dus"] <= -s, -s)))
    return K


# ── çıkış tipleri: hepsi (bar, getiri) veya None döner ──────────────────
def _seviye(y, mask, lvl):
    j = _ilk(mask)
    return None if j is None else (j, float(lvl))


def _kapanis(y, j):
    if j is None or j >= len(y):
        return None
    return (j, float(y["kap"].iloc[j]))


def _gosterge(y, mask):
    """Sinyal j barında oluştu → j+1 kapanışında çık (sızıntı yok)."""
    j = _ilk(mask)
    if j is None:
        return None
    k = j + 1
    if k >= len(y):
        return None                      # ertesi bar yok → sona kadar tut
    return (k, float(y["kap"].iloc[k]))


def _hedef_stop(y, h, s):
    jh, js = _ilk(y["yuk"] >= h), _ilk(y["dus"] <= -s)
    if jh is None and js is None:
        return None
    if jh is None:
        return (js, float(-s))
    if js is None:
        return (jh, float(h))
    if js <= jh:                          # aynı bar → stop önce (kötümser)
        return (js, float(-s))
    return (jh, float(h))


def _takip(y, pay):
    tepe = y["yuk"].cummax()
    j = _ilk((tepe - y["dus"]) >= pay)
    if j is None:
        return None
    return (j, float(max(tepe.iloc[j] - pay, y["dus"].iloc[j])))


def _once(a, b):
    v = [x for x in (a, b) if x is not None]
    if not v:
        return None
    return min(v, key=lambda x: x[0])


def _ilk(mask):
    m = np.asarray(pd.Series(mask).fillna(False), dtype=bool)
    idx = np.flatnonzero(m)
    return int(idx[0]) if idx.size else None


def _min2(a, b):
    vals = [x for x in (a, b) if x is not None]
    return min(vals) if vals else None


# ─────────────────────────────────────────────────────────── koşum ──
def main():
    cmf_dogrula()
    print("veriler yükleniyor…")
    sinyaller = sinyalleri_al()
    print(f"bullish ER sinyali: {len(sinyaller):,}  ({sinyaller.symbol.nunique()} hisse)")

    bench = _oku("XU100")
    if bench is None:
        print("XU100 verisi yok — alfa hesaplanamaz"); return
    bc = bench["Close"].astype(float)

    # hisse başına bir kez: fiyat + akıllı para serileri
    semboller = sorted(sinyaller["symbol"].unique())
    veri, apser = {}, {}
    for s in semboller:
        d = _oku(s)
        if d is None or len(d) < 60:
            continue
        veri[s] = d
        apser[s] = akilli_para_serileri(d)
    print(f"veri bulunan hisse: {len(veri)}")

    kurallar = kurallari_uret()
    sonuc = {ad: [] for ad, _, _ in kurallar}
    aile = {ad: a for ad, a, _ in kurallar}

    atlanan = 0
    for _, sg in sinyaller.iterrows():
        s = sg["symbol"]
        d = veri.get(s)
        if d is None:
            atlanan += 1; continue
        idx = d.index
        pos = idx.searchsorted(sg["scan_date"])
        # GİRİŞ: sinyal gününden SONRAKİ ilk kapanış
        gir = pos + 1 if (pos < len(idx) and idx[pos] == sg["scan_date"]) else pos
        if gir + 2 >= len(idx):
            atlanan += 1; continue

        girfiy = float(d["Close"].iloc[gir])
        if girfiy <= 0:
            atlanan += 1; continue

        son = min(gir + MAX_GUN, len(idx) - 1)
        dilim = d.iloc[gir + 1: son + 1]
        if dilim.empty:
            atlanan += 1; continue

        yol = pd.DataFrame({
            "yuk": (dilim["High"].astype(float) / girfiy - 1) * 100,
            "dus": (dilim["Low"].astype(float) / girfiy - 1) * 100,
            "kap": (dilim["Close"].astype(float) / girfiy - 1) * 100,
        }, index=dilim.index)
        yol["geri"] = yol["yuk"].cummax() - yol["kap"]          # tepeden geri veriş
        ma20 = d["Close"].rolling(20).mean()
        yol["ma20_alti"] = (dilim["Close"] < ma20.loc[dilim.index]).values

        ap = apser[s].loc[dilim.index]

        # piyasa getirisi bir kez hesaplanır, çıkış barına göre kesilir
        try:
            b0 = float(bc.loc[:idx[gir]].iloc[-1])
            bser = bc.reindex(yol.index, method="ffill")
            piyasa = (bser / b0 - 1) * 100
        except Exception:
            piyasa = pd.Series(0.0, index=yol.index)

        for ad, _, fn in kurallar:
            cik = _yarim(yol) if fn == "YARIM" else fn(yol, ap)
            if cik is None:                       # kural tetiklemedi → sona kadar tut
                gun, r = len(yol) - 1, float(yol["kap"].iloc[-1])
            else:
                gun, r = cik
                gun = min(int(gun), len(yol) - 1)
            piy = float(piyasa.iloc[gun]) if not pd.isna(piyasa.iloc[gun]) else 0.0
            sonuc[ad].append((r, r - piy, gun + 1))

    print(f"atlanan sinyal: {atlanan:,}\n")

    satir = []
    for ad, _, _ in kurallar:
        v = sonuc[ad]
        if not v:
            continue
        a = np.array([x[0] for x in v]); alfa = np.array([x[1] for x in v])
        gun = np.array([x[2] for x in v])
        kaz = a[a > 0]; kay = a[a <= 0]
        satir.append({
            "kural": ad, "aile": aile[ad], "N": len(v),
            "ort_getiri": a.mean(), "ort_alfa": alfa.mean(),
            "kazanma%": (a > 0).mean() * 100,
            "ort_kazanç": kaz.mean() if kaz.size else 0.0,
            "ort_kayıp": kay.mean() if kay.size else 0.0,
            "en_kötü": a.min(), "ort_gün": gun.mean(),
        })
    t = pd.DataFrame(satir).sort_values("ort_alfa", ascending=False)
    pd.set_option("display.width", 250)
    print("=" * 118)
    print("ÇIKIŞ KURALI YARIŞI — bullish Erken Radar sinyalleri, XU100'e göre alfa")
    print("=" * 118)
    print(t.round(2).to_string(index=False))
    t.to_csv(os.path.join(BASE, "cikis_kurali_backtest.csv"), index=False, encoding="utf-8")
    print("\nyazıldı: cikis_kurali_backtest.csv")


def _yarim(yol):
    """Yarısı +%6 hedefte satılır, kalan yarı %6 takip eden stopla götürülür."""
    j1 = _ilk(yol["yuk"] >= 6)
    if j1 is None:                                   # hedefe hiç gelmedi
        return (len(yol) - 1, float(yol["kap"].iloc[-1]))
    kalan = yol.iloc[j1:]
    tepe = kalan["yuk"].cummax()
    j2 = _ilk((tepe - kalan["dus"]) >= 6)
    if j2 is None:
        gun, r2 = len(yol) - 1, float(kalan["kap"].iloc[-1])
    else:
        gun = j1 + int(j2)
        r2 = float(max(tepe.iloc[j2] - 6, kalan["dus"].iloc[j2]))
    return (gun, 0.5 * 6.0 + 0.5 * r2)


if __name__ == "__main__":
    main()
