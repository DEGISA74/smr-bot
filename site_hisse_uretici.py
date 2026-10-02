#!/usr/bin/env python3
"""
site_hisse_uretici.py — smartmoneyradar.app için HER BIST HİSSESİNİN analiz dosyası.

1 Eki 2026 — Site fazı adım 1 (güncellik). Kural: hesap merkezde, ziyaretçi sadece
hazır dosya okur (1000 kişi gelse de sunucu hesap yapmaz).

- Veri: yerel depo (veriler/*.parquet) — data_layer.get_safe_historical_data AYNA
  MODUNDA (SMR_MIRROR_READONLY varsayılan 1) BIST için Yahoo'ya GİTMEZ, sadece okur.
- Akış grafikleri: app.py "Para Akış İvmesi & Fiyat Dengesi" paneliyle AYNI fonksiyon
  (sentiment_chart_core.calculate_sentiment_chart).
- Ortalamalar / 52H / RSI (Wilder = TradingView) depodaki günlük seriden.
- fiyat_saati = o hissenin depo dosyasının yazıldığı saat (dürüst saat: acil liste
  dışındaki hisseler ~50 dk'da bir tazelenir).

Çıktı (SMR_SITE_HISSE_OUT, varsayılan /var/www/smr/frontend/hisse):
  <TICKER>.json  — {meta, hisse, grafik}
  _liste.json    — {uretim, adet, hisseler:[{t, ad}], piyasa:{...genişlik}}
Her dosya atomik yazılır (yarım dosya görünmez).

Evren: BIST100 + 5 ana endeks (1-2 Eki 2026 kararı — tüm BIST VPS'e ağır geldi).
Kullanım:  python site_hisse_uretici.py            # BIST100 + XU100
           python site_hisse_uretici.py AKSEN THYAO # birkaç hisse (deneme)
Cron (VPS): flock + nice/ionice (en düşük öncelik), seans içi 30 dk'da bir + kapanış sonrası son tur.
"""
import os
import sys
import json
import time
import math
import logging
import warnings
from datetime import datetime

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

import pandas as pd

from data_layer import (
    get_safe_historical_data, get_display_name, load_index_components,
    CACHE_DIR, _TZ_ISTANBUL,
)
from sentiment_chart_core import calculate_sentiment_chart
from indicators import compute_cmf, compute_relative_obv_state, compute_updown_volume_ratio

OUT_DIR = os.environ.get("SMR_SITE_HISSE_OUT", "/var/www/smr/frontend/hisse")
GRAFIK_BAR = 30


def _num(v, nd=2):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(v) or math.isinf(v)) else round(v, nd)


def _rsi_wilder(close: pd.Series, period: int = 14) -> float | None:
    d = close.diff()
    gain = d.where(d > 0, 0.0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-d.where(d < 0, 0.0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, float("nan"))
    return _num((100 - 100 / (1 + rs)).iloc[-1], 1)


def _atomic_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _fiyat_saati(ticker: str) -> str | None:
    try:
        ts = os.path.getmtime(os.path.join(CACHE_DIR, f"{ticker}_1d.parquet"))
        return datetime.fromtimestamp(ts, _TZ_ISTANBUL).strftime("%d.%m %H:%M")
    except OSError:
        return None


_XU_CACHE = {}


def _xu100():
    if "df" not in _XU_CACHE:
        _XU_CACHE["df"] = get_safe_historical_data("XU100.IS", period="1y")
    return _XU_CACHE["df"]


def _akilli_para(ticker: str, df: pd.DataFrame) -> dict | None:
    """2 Eki 2026 — site 'Akıllı Para · HİSSE' kartı. app'in gösterge fonksiyonları (indicators).
    Endekse göre güç = son 20 işlem günü hisse getirisi − XU100 getirisi (puan).
    Hacim/ortalama: seans sürerken bugünün yarım hacmi yanıltır → son TAMAMLANMIŞ gün."""
    if ticker.startswith("X") or len(df) < 25:
        return None
    try:
        xu = _xu100()
        c, xc, v = df["Close"].astype(float), xu["Close"].astype(float), df["Volume"].astype(float)
        h20 = (c.iloc[-1] / c.iloc[-21] - 1) * 100
        x20 = (xc.iloc[-1] / xc.iloc[-21] - 1) * 100
        simdi = datetime.now(_TZ_ISTANBUL)
        seans_suruyor = (df.index[-1].date() == simdi.date()
                         and (simdi.hour, simdi.minute) < (18, 30))
        i = -2 if seans_suruyor else -1
        ort = v.iloc[i - 20:i].mean()
        rel = compute_relative_obv_state(df, xu, lookback=20) or {}
        ud = compute_updown_volume_ratio(df, period=20) or {}
        return {"guc20": _num(h20 - x20, 1), "hisse20": _num(h20, 1), "xu20": _num(x20, 1),
                "cmf5": _num(compute_cmf(df, period=5), 2), "cmf20": _num(compute_cmf(df, period=20), 2),
                "hacim_ort": _num(v.iloc[i] / ort, 2) if ort else None,
                "obv_endeks": rel.get("state"),
                "ud_oran": _num(ud.get("ratio"), 2), "ud_durum": ud.get("state")}
    except Exception:
        return None


def hisse_uret(ticker: str) -> dict | None:
    # app.py ile BİREBİR: MA tablosu ve 52H şeridi get_safe_historical_data(period="1y")
    # çıktısından hesaplanır (EMA144 gibi değerler serinin başlangıcına duyarlı).
    df = get_safe_historical_data(ticker, period="1y")
    if df is None or df.empty or "Close" not in df or len(df) < 30:
        return None
    c = df["Close"].astype(float)
    k, onceki = float(c.iloc[-1]), float(c.iloc[-2])
    hi, lo = float(df["High"].max()), float(df["Low"].min())   # app 52H şeridi: tüm pencerenin H/L'si
    sma = lambda n: _num(c.rolling(n).mean().iloc[-1]) if len(c) >= n else None
    ema = lambda n: _num(c.ewm(span=n, adjust=False).mean().iloc[-1])

    grafik = []
    try:
        sd = calculate_sentiment_chart(ticker, "daily", market_profile="BIST")
        if sd is not None and not sd.empty:
            for r in sd.tail(GRAFIK_BAR).itertuples():
                row = {"date": str(r.Date_Str), "mf": _num(r.MF_Smooth, 4),
                       "stp": _num(r.STP), "price": _num(r.Price),
                       "sentiment": _num(r.Sentiment, 3)}
                if None in (row["mf"], row["stp"], row["price"]):
                    continue
                if row["sentiment"] is None:
                    row.pop("sentiment")
                grafik.append(row)
    except Exception:
        grafik = []

    bare = ticker.replace(".IS", "")
    return {
        "meta": {"fiyat_saati": _fiyat_saati(ticker), "son_bar": str(df.index[-1].date())},
        "hisse": {
            "ticker": bare, "ad": get_display_name(ticker),
            "kapanis": _num(k), "degisim_pct": _num((k / onceki - 1) * 100) if onceki else None,
            "sma50": sma(50), "sma100": sma(100), "sma200": sma(200),
            "ema5": ema(5), "ema8": ema(8), "ema13": ema(13), "ema144": ema(144),
            "rsi": _rsi_wilder(c),
            "yillik_yuksek": _num(hi), "yillik_dusuk": _num(lo),
            "pozisyon_pct": _num((k - lo) / (hi - lo) * 100, 1) if hi > lo else None,
        },
        "grafik": grafik,
        "akilli": _akilli_para(ticker, df),
    }


def _bist100_evreni() -> list[str]:
    """1 Eki 2026 — SADECE BIST100 + XU100 (kullanıcı kararı). 2 çekirdekli VPS'te ~620
    hisse seans içi fetcher/İş Yatırım turlarıyla çakışıp 7 dk'da 272'ye ancak geliyordu.
    Kaynak: data_layer.load_index_components (haftalık önbellek, ağ YOK); yoksa _bist100.json."""
    uyeler = []
    try:
        uyeler = list(load_index_components("XU100", allow_network=False) or [])
    except Exception:
        uyeler = []
    if len(uyeler) < 50:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_bist100.json"),
                      encoding="utf-8") as f:
                uyeler = json.load(f)
        except Exception:
            uyeler = []
    # 2 Eki 2026 — ana endeksler de (kullanıcı: endeks grafikleri). Ücretsiz kotaya sayılmaz.
    endeksler = ["XU100.IS", "XU030.IS", "XBANK.IS", "XUSIN.IS", "XTUMY.IS"]
    return endeksler + [u if u.endswith(".IS") else u + ".IS" for u in uyeler]


def main(argv: list[str]) -> int:
    t0 = time.time()
    if argv:
        evren = [a.upper() if a.upper().endswith(".IS") else a.upper() + ".IS" for a in argv]
    else:
        evren = _bist100_evreni()
    os.makedirs(OUT_DIR, exist_ok=True)
    uretim = datetime.now(_TZ_ISTANBUL).strftime("%Y-%m-%d %H:%M")

    liste, hata = [], 0
    kart_adet = 0
    try:
        import site_paylas_kart as _kart
    except Exception:
        _kart = None
    # Kart çizimi VPS'te ~2 sn/hisse (105 hisse = 3,5 dk) → gün içinde sunucuyu yormasın diye
    # SADECE akşam (kapanış sonrası, 19:00 TR+) turunda çizilir. Kart dünün/bugünün kapanışını gösterir.
    if _kart is not None and datetime.now(_TZ_ISTANBUL).hour < 19 and not os.environ.get("SMR_KART_ZORLA"):
        _kart = None
    ust200 = ust50 = rsi50 = olculen = 0
    gunluk = []          # (ticker, günlük değişim %) — piyasa nabzı (BIST100, endeksler hariç)
    guclu = []           # (ticker, endekse göre 20g güç puanı) — 'Endeksten Güçlü' kutusu
    for t in evren:
        try:
            out = hisse_uret(t)
        except Exception:
            out = None
        if not out:
            hata += 1
            continue
        out["meta"]["uretim"] = uretim
        h = out["hisse"]
        _atomic_json(os.path.join(OUT_DIR, f"{h['ticker']}.json"), out)
        if _kart is not None:     # 2 Eki 2026 — X paylaşım görseli; hata JSON'u bozmaz
            try:
                kart_adet += bool(_kart.uret(out, os.path.dirname(OUT_DIR)))
            except Exception:
                pass
        liste.append({"t": h["ticker"], "ad": h["ad"]})
        if not h["ticker"].startswith("X"):           # genişlik: endeksler hariç
            k = h["kapanis"]
            if k is not None and h["sma200"] is not None:
                olculen += 1
                ust200 += k > h["sma200"]
                ust50 += h["sma50"] is not None and k > h["sma50"]
                rsi50 += h["rsi"] is not None and h["rsi"] > 50
            if (out.get("akilli") or {}).get("guc20") is not None:
                guclu.append((h["ticker"], out["akilli"]["guc20"]))
            if h["degisim_pct"] is not None:
                gunluk.append((h["ticker"], h["degisim_pct"], out["meta"]["son_bar"]))

    piyasa = None
    if olculen:
        piyasa = {"olculen": olculen,
                  "sma200_ustu_pct": round(ust200 / olculen * 100, 1),
                  "sma50_ustu_pct": round(ust50 / olculen * 100, 1),
                  "rsi50_ustu_pct": round(rsi50 / olculen * 100, 1)}
    # 2 Eki 2026 — nabız SADECE en güncel günün barı gelmiş hisselerle sayılır (seans başında
    # depo sırayla tazelenirken dünkü/bugünkü değişim karışmasın). 'kapsanan' kaç hissede ölçüldüğü.
    if gunluk:
        _gun = max(g for _, _, g in gunluk)
        gunluk = [(t, d) for t, d, g in gunluk if g == _gun]
    if piyasa is not None and gunluk:
        # 2 Eki 2026 — PİYASA NABZI (site sol sütun): yükselen/düşen sayısı + en çok
        # yükselen/düşen 3 hisse. Aynı turun verisi; ek okuma yok.
        sirali = sorted(gunluk, key=lambda x: x[1], reverse=True)
        piyasa.update({
            "gun": _gun, "kapsanan": len(gunluk),
            "yukselen": sum(1 for _, d in gunluk if d > 0),
            "dusen": sum(1 for _, d in gunluk if d < 0),
            "yatay": sum(1 for _, d in gunluk if d == 0),
            "en_cok_yukselen": [{"t": t, "degisim": d} for t, d in sirali[:3]],
            "en_cok_dusen": [{"t": t, "degisim": d} for t, d in sirali[-3:][::-1]],
        })
    if piyasa is not None and guclu:
        # 2 Eki 2026 — ENDEKSTEN GÜÇLÜ: son 20 işlem gününde XU100'ü en çok geçen 5 BIST100
        # hissesi (puan = hisse getirisi − XU100 getirisi). Ölçüm, tahmin değil.
        piyasa["endeksten_guclu"] = [{"t": t, "puan": g} for t, g in sorted(guclu, key=lambda x: -x[1])[:5]]
    if not argv:   # deneme çalıştırması listeyi ezmez
        _atomic_json(os.path.join(OUT_DIR, "_liste.json"),
                     {"uretim": uretim, "adet": len(liste), "evren": "BIST100",
                      "hisseler": liste, "piyasa": piyasa})
        # Evren dışında kalmış eski dosyalar (BIST100'den çıkan hisse, yarım kalmış tur)
        # yayında bayat kalmasın.
        gecerli = {f"{h['t']}.json" for h in liste} | {"_liste.json"}
        for ad in os.listdir(OUT_DIR):
            if ad.endswith(".json") and ad not in gecerli:
                try:
                    os.remove(os.path.join(OUT_DIR, ad))
                except OSError:
                    pass
    print(f"[site_hisse_uretici] {uretim} · {len(liste)} hisse yazıldı · {kart_adet} kart · {hata} atlandı · "
          f"{time.time() - t0:.1f} sn · çıktı {OUT_DIR}")
    return 0 if liste else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
