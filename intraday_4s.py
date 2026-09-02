# -*- coding: utf-8 -*-
"""
intraday_4s.py — 4 SAATLİK MUM KATMANI (23 Tem 2026)

NEDEN: Kullanıcı kararlarını 4 saatlik grafikte veriyor; sistemin TAMAMI günlük
veriyle çalışıyor. "Sistem neden benim gördüğümü görmüyor" sorusunun bir parçası
bu olabilir. Bu modül 4 saatlik mumları kurar ve AYRI depoya yazar.

⚠ MEVCUT SİSTEME DOKUNMAZ. Günlük parquet'ler (veriler/*_1d.parquet) hiç
değişmez; 4 saatlikler veriler_4s/ altına yazılır. Hiçbir panel bunu okumaz —
şimdilik yalnız ölçüm için.

KAYNAK: Yahoo 1 saatlik (≈3 yıl geçmiş). BIST seansı 09:30-18:30 → günde 9
saatlik bar. 4 saatlik gruplama seans açılışına çapalanır (09:30 · 13:30 · 17:30),
takvim saatine değil — yoksa mumlar seansı ortadan böler.

Kullanım:
    python intraday_4s.py TUPRS            tek hisse, ekrana döker
    python intraday_4s.py TUPRS EREGL ...  birkaç hisse
    python intraday_4s.py --liste 100      en likit 100 hisse (günlük hacimden)
"""
import os
import sys
import time
import warnings

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
GUNLUK = os.path.join(BASE, "veriler")
DEPO = os.path.join(BASE, "veriler_4s")
TZ = "Europe/Istanbul"
SEANS_BASI = "09:30"


def saatlik_cek(sym, period="730d"):
    """Yahoo'dan 1 saatlik bar. BIST sembolü .IS ister."""
    t = sym if sym.endswith(".IS") else f"{sym}.IS"
    d = yf.download(t, interval="1h", period=period, progress=False, auto_adjust=False)
    if d is None or d.empty:
        return None
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d.index = d.index.tz_convert(TZ)
    d = d[~d.index.duplicated(keep="last")].sort_index()
    return d[["Open", "High", "Low", "Close", "Volume"]].dropna(how="all")


def dorde_cevir(saatlik):
    """1 saatlik → 4 saatlik. Gruplama seans açılışına çapalı (takvime değil)."""
    if saatlik is None or saatlik.empty:
        return None
    parcalar = []
    for gun, g in saatlik.groupby(saatlik.index.date):
        capa = pd.Timestamp(f"{gun} {SEANS_BASI}", tz=TZ)
        r = g.resample("4h", origin=capa, label="left", closed="left").agg(
            {"Open": "first", "High": "max", "Low": "min",
             "Close": "last", "Volume": "sum"}).dropna(subset=["Open"])
        # BIST seansı 9 saat → 2 tam mum + 1 saatlik ARTIK kalıyor. O artık mum
        # sahte bir bar gibi görünür (hacmi diğerlerinin beşte biri). Bir öncekine
        # katılır → günde tertemiz 2 adet 4 saatlik mum.
        if len(r) > 2:
            artik = r.iloc[2:]
            son = r.index[1]
            r.loc[son, "High"] = max(r.loc[son, "High"], artik["High"].max())
            r.loc[son, "Low"] = min(r.loc[son, "Low"], artik["Low"].min())
            r.loc[son, "Close"] = artik["Close"].iloc[-1]
            r.loc[son, "Volume"] = r.loc[son, "Volume"] + artik["Volume"].sum()
            r = r.iloc[:2]
        parcalar.append(r)
    if not parcalar:
        return None
    out = pd.concat(parcalar).sort_index()
    return out[out["Volume"] > 0] if "Volume" in out else out


def gostergeler_4s(df):
    """4 saatlik mumlar üzerinde gösterge katmanı.

    İKİ GRUP:
      (a) BİZİM göstergelerimiz — CMF/OBV/UDVR, günlükte kullandığımızın aynısı
      (b) KULLANICININ grafiğindeki, parametresi ekranda GÖRÜNEN göstergeler:
          RSI 14 · RSI 9 · CCI 20 · ADX 20 · WaveTrend 10/11 · MA şeridi 20/50/100/200

    ⚠ Lorentzian Classification EKLENMEDİ: o bir makine-öğrenmesi sınıflandırıcısı
      (kNN + Lorentzian uzaklık). Ekrandaki parametrelerden birebir klonlanamaz;
      yanlış klon, hiç olmamasından kötüdür. Ayrı iş olarak değerlendirilmeli.
    """
    import numpy as np
    o, h, l = df["Open"].astype(float), df["High"].astype(float), df["Low"].astype(float)
    c, v = df["Close"].astype(float), df["Volume"].astype(float)
    out = pd.DataFrame(index=df.index)

    def _rsi(seri, n):
        d = seri.diff()
        up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
        dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
        return 100 - 100 / (1 + up / dn.replace(0, np.nan))

    out["rsi14"] = _rsi(c, 14)
    out["rsi9"] = _rsi(c, 9)

    tp = (h + l + c) / 3
    sap = tp.rolling(20).apply(lambda x: abs(x - x.mean()).mean(), raw=True)
    out["cci20"] = (tp - tp.rolling(20).mean()) / (0.015 * sap.replace(0, np.nan))

    # ADX 20 (Wilder)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    up_m, dn_m = h.diff(), -l.diff()
    plus = ((up_m > dn_m) & (up_m > 0)) * up_m
    minus = ((dn_m > up_m) & (dn_m > 0)) * dn_m
    atr = tr.ewm(alpha=1 / 20, adjust=False).mean().replace(0, np.nan)
    pdi = 100 * plus.ewm(alpha=1 / 20, adjust=False).mean() / atr
    mdi = 100 * minus.ewm(alpha=1 / 20, adjust=False).mean() / atr
    out["adx20"] = (100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
                    ).ewm(alpha=1 / 20, adjust=False).mean()

    # WaveTrend 10/11 (LazyBear kalıbı — grafikteki WT 10 11)
    esa = tp.ewm(span=10, adjust=False).mean()
    dd = (tp - esa).abs().ewm(span=10, adjust=False).mean()
    ci = (tp - esa) / (0.015 * dd.replace(0, np.nan))
    out["wt1"] = ci.ewm(span=11, adjust=False).mean()
    out["wt2"] = out["wt1"].rolling(4).mean()

    # MagicRibbon iskeleti — ekranda görünen ortalamalar
    for n in (20, 50, 100, 200):
        out[f"sma{n}"] = c.rolling(n).mean()
    out["serit_dizili"] = ((out.sma20 > out.sma50) & (out.sma50 > out.sma100)
                           & (out.sma100 > out.sma200))

    # bizim göstergeler
    genislik = (h - l).replace(0, np.nan)
    mfv = ((((c - l) - (h - c)) / genislik) * v).fillna(0.0)
    for ad, p in (("cmf5", 5), ("cmf20", 20)):
        out[ad] = mfv.rolling(p).sum() / v.rolling(p).sum().replace(0, np.nan)
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    out["obv_egim"] = (obv - obv.shift(5)) / v.rolling(20).mean().replace(0, np.nan)
    yon = np.sign(c.diff())
    out["udvr"] = ((v * (yon > 0)).rolling(5).sum()
                   / (v * (yon < 0)).rolling(5).sum().replace(0, np.nan))
    return out


def depodan_oku(sym, azami_yas_gun=3):
    """4 saatlik depodan OKU — yoksa/bayatsa None (2 Eyl 2026, bulgu 10).

    NEDEN VAR: Vade Uyumu tablosunun 4S kolonu her panel açılışında doğrudan
    sağlayıcıdan çekiyordu; yerel depo (bu dosyanın beslediği veriler_4s/)
    hiç kullanılmıyordu. Depo denetlendi (231 dosya · ~155 bin bar-gün):
    NaN 0 · tekrar eden zaman damgası 0 · bozuk OHLC 0 · fiyat<=0 0 ·
    hacim<=0 0 · günlerin %99,15'inde tam 2 bar. Yani depo sağlıklı.

    BAYATLIK KAPISI: depo yalnız likit listedeki hisseleri besler; liste
    dışına düşen sembolün dosyası olduğu yerde kalır (16/231 örnek). Bayat
    dosyayla hesap yapmak "donuk aynayla koşmak" olur → azami yaştan eskiyse
    None döner, çağıran taraf sağlayıcıya düşer.

    Döner: (df, durum) · durum = {'kaynak','son_bar','yas_gun','yarim_bar'}
    df None ise durum['kaynak'] = 'yok'.
    """
    ad = str(sym).upper().replace(".IS", "")
    yol = os.path.join(DEPO, f"{ad}.IS_4h.parquet")
    bos = {"kaynak": "yok", "son_bar": None, "yas_gun": None, "yarim_bar": False}
    if not os.path.exists(yol):
        return None, bos
    try:
        d = pd.read_parquet(yol)
        if d is None or d.empty or "Close" not in d.columns:
            return None, bos
        son = d.index[-1]
        bugun = pd.Timestamp.now(tz=TZ).normalize()
        yas = int((bugun - son.normalize()).days)
        if yas > azami_yas_gun:
            return None, {"kaynak": "bayat", "son_bar": son,
                          "yas_gun": yas, "yarim_bar": False}
        # Son bar BUGÜNÜN barıysa ve seans sürüyorsa o bar henüz kapanmamıştır.
        simdi = pd.Timestamp.now(tz=TZ)
        yarim = bool(son.normalize() == bugun and simdi.hour < 18)
        return d, {"kaynak": "depo", "son_bar": son, "yas_gun": yas,
                   "yarim_bar": yarim}
    except Exception:
        return None, bos


def kaydet(sym, df):
    os.makedirs(DEPO, exist_ok=True)
    ad = sym.replace(".IS", "")
    yol = os.path.join(DEPO, f"{ad}.IS_4h.parquet")
    df.to_parquet(yol)
    return yol


def kaydet_saatlik(sym, h):
    # 3 Ağu 2026 — saatliği de ayrı depoya yaz (aynı 1h çekişinden; tek script hem 1h hem 4h)
    depo_1h = os.path.join(BASE, "veriler_saatlik")
    os.makedirs(depo_1h, exist_ok=True)
    ad = sym.replace(".IS", "")
    yol = os.path.join(depo_1h, f"{ad}.IS_1h.parquet")
    h.to_parquet(yol)
    return yol


LIKIT_PENCERE = 60      # kaç günlük likidite penceresi (14 Ağu 2026)


def likit_liste(n=100):
    """Günlük depodan en likit n BIST hissesi (SON 60 GÜN TL hacim medyanı).

    ⚠ 14 Ağu 2026 — ölçüt DEĞİŞTİ: eskiden hissenin TÜM geçmişinin medyanı
    alınıyordu. Bu, iki yıl önce likit olup bugün ölmüş hisseleri listede
    tutuyor, bugün canlanmış olanları dışarıda bırakıyordu.
    Ölçüldü: ilk 250'nin 38'i (≈%15) bu yüzden yanlış hisseydi — listeye giren
    AGROT/CLEBİ/CEMAŞ son 60 günde likit değil, dışarıda kalan CRFSA/ALCTL/DAGİ
    likitti. Artık pencere son `LIKIT_PENCERE` gün.
    """
    import glob
    liq = {}
    for f in glob.glob(os.path.join(GUNLUK, "*.IS_1d.parquet")):
        s = os.path.basename(f).replace(".IS_1d.parquet", "")
        if s.upper().startswith(("XU", "XB", "XT", "XY")):
            continue
        try:
            d = pd.read_parquet(f)
            if d.empty or len(d) < 200:
                continue
            _tl = (d["Close"] * d["Volume"]).tail(LIKIT_PENCERE)
            _m = float(_tl.median())
            if _m > 0:
                liq[s] = _m
        except Exception:
            pass
    return [s for s, _ in sorted(liq.items(), key=lambda x: -x[1])[:n]]


def main():
    arg = sys.argv[1:]
    if not arg:
        print(__doc__); return
    if arg[0] == "--liste":
        n = int(arg[1]) if len(arg) > 1 else 100
        semboller = likit_liste(n)
        print(f"en likit {len(semboller)} hisse indirilecek")
        tekil = False
    else:
        semboller = arg
        tekil = len(semboller) == 1

    ok = hata = 0
    for i, s in enumerate(semboller, 1):
        try:
            h = saatlik_cek(s)
            if h is not None and not h.empty:
                kaydet_saatlik(s, h)
            d4 = dorde_cevir(h)
            if d4 is None or d4.empty:
                print(f"  {s}: veri yok"); hata += 1; continue
            yol = kaydet(s, d4)
            ok += 1
            print(f"[{i}/{len(semboller)}] {s}: {len(d4)} adet 4s mum | "
                  f"{d4.index[0].date()} → {d4.index[-1].date()}")
            if tekil:
                print(f"\nson 8 mum ({os.path.basename(yol)}):")
                g = d4.tail(8).copy()
                g.index = [t.strftime("%d.%m %H:%M") for t in g.index]
                print(g.round(2).to_string())
                gun = d4.groupby(d4.index.date).size()
                print(f"\ngünde ortalama {gun.mean():.1f} adet 4 saatlik mum")
                print(f"son kapanış: {float(d4['Close'].iloc[-1]):.2f}")

                ind = gostergeler_4s(d4)
                print("\n--- 4 SAATLİK GÖSTERGELER (son 5 mum) ---")
                kol = ["rsi14", "rsi9", "cci20", "adx20", "wt1", "wt2",
                       "cmf5", "cmf20", "udvr"]
                gi = ind.tail(5)[kol].copy()
                gi.index = [t.strftime("%d.%m %H:%M") for t in gi.index]
                print(gi.round(2).to_string())
                sn = ind.iloc[-1]
                print(f"\nMA şeridi dizili mi (20>50>100>200): "
                      f"{'EVET' if bool(sn.serit_dizili) else 'hayır'}")
                print(f"SMA20 {sn.sma20:.2f} · SMA50 {sn.sma50:.2f} · "
                      f"SMA100 {sn.sma100:.2f} · SMA200 {sn.sma200:.2f}")
        except Exception as e:
            print(f"  {s}: HATA {e}"); hata += 1
        if len(semboller) > 1:
            time.sleep(0.4)          # Yahoo'yu yormayalım
    if len(semboller) > 1:
        print(f"\nbitti — başarılı {ok}, hatalı {hata} → {DEPO}")


if __name__ == "__main__":
    main()
