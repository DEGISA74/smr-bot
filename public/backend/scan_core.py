"""
scan_core.py — SMR Public Vitrin Backend
-----------------------------------------
Streamlit bağımlılığı YOKTUR.
Veri kaynağı: yfinance (Marketstack API'ye geçiş için tek yer: fetch_data())
Görev: BIST taraması → latest.json üretimi
"""

import json
import os
import pathlib
import datetime
import time
import yfinance as yf
import pandas as pd

# VPS'te ~/smr/veriler/, local'de SMR_CACHE_DIR env var ile override
_CACHE_DIR = pathlib.Path(os.environ.get("SMR_CACHE_DIR", "/home/wm11tr/smr/veriler"))

# ==============================================================================
# BÖLÜM 1 — BIST HİSSE LİSTESİ
# app.py ile senkron tutulmalı (elle güncellenir)
# ==============================================================================

PRIORITY_TICKERS = [
    "XU100.IS", "XU030.IS", "XBANK.IS", "XTUMY.IS", "XUSIN.IS",
    "EREGL.IS", "SISE.IS", "TUPRS.IS", "AKBNK.IS", "ARCLK.IS",
    "ASELS.IS", "BIMAS.IS", "CCOLA.IS", "EKGYO.IS", "ENKAI.IS",
    "FROTO.IS", "GARAN.IS", "GUBRF.IS", "HALKB.IS", "ISCTR.IS",
    "KCHOL.IS", "KRDMD.IS", "PETKM.IS", "PGSUS.IS",
    "SAHOL.IS", "SASA.IS", "TCELL.IS", "THYAO.IS", "TKFEN.IS",
    "TOASO.IS", "TTKOM.IS", "TTRAK.IS", "VAKBN.IS", "YKBNK.IS",
]

OTHER_TICKERS = [
    "A1CAP.IS","ACSEL.IS","ADEL.IS","ADESE.IS","ADGYO.IS","AEFES.IS","AGESA.IS",
    "AGHOL.IS","AGROT.IS","AKCNS.IS","AKENR.IS","AKFGY.IS","AKGRT.IS","AKMGY.IS",
    "AKSA.IS","AKSEN.IS","AKSGY.IS","ALARK.IS","ALBRK.IS","ALCTL.IS","ALFAS.IS",
    "ALKIM.IS","ALTNY.IS","ALVES.IS","ANELE.IS","ANHYT.IS","ANSGR.IS",
    "ARCLK.IS","ARDYZ.IS","ARENA.IS","ARSAN.IS","ARTMS.IS","ARZUM.IS","ASELS.IS",
    "ASTOR.IS","ASUZU.IS","ATEKS.IS","ATLAS.IS","AVGYO.IS","AVHOL.IS","AVOD.IS",
    "AVTUR.IS","AYDEM.IS","AYEN.IS","AYGAZ.IS","AZTEK.IS","BAGFS.IS","BAKAB.IS",
    "BANVT.IS","BASCM.IS","BERA.IS","BEYAZ.IS","BIENY.IS","BIMAS.IS","BIOEN.IS",
    "BIZIM.IS","BJKAS.IS","BLCYT.IS","BMSTL.IS","BNTAS.IS","BORLS.IS","BOSSA.IS",
    "BRISA.IS","BRKSN.IS","BRSAN.IS","BRYAT.IS","BSOKE.IS","BTCIM.IS","BUCIM.IS",
    "BURCE.IS","BVSAN.IS","CANTE.IS","CCOLA.IS","CELHA.IS","CEMAS.IS","CEMTS.IS",
    "CIMSA.IS","CLEBI.IS","CMENT.IS","COSMO.IS","CWENE.IS","DAGI.IS","DARDL.IS",
    "DENGE.IS","DERIM.IS","DESA.IS","DEVA.IS","DOAS.IS","DOHOL.IS","DURDO.IS",
    "DYOBY.IS","DZGYO.IS","ECILC.IS","ECZYT.IS","EDIP.IS","EGEEN.IS","EGGUB.IS",
    "EGPRO.IS","EKGYO.IS","EKSUN.IS","EMKEL.IS","ENJSA.IS","ENKAI.IS","ENTRA.IS",
    "ERBOS.IS","EREGL.IS","ESCAR.IS","ESEN.IS","EUHOL.IS","EUPWR.IS","FADE.IS",
    "FENER.IS","FLAP.IS","FONET.IS","FRIGO.IS","FROTO.IS","GARAN.IS","GENIL.IS",
    "GEREL.IS","GESAN.IS","GLBMD.IS","GLRYH.IS","GLYHO.IS","GOKNR.IS","GOODY.IS",
    "GOZDE.IS","GRSEL.IS","GSDHO.IS","GSRAY.IS","GUBRF.IS","GWIND.IS","HALKB.IS",
    "HATEK.IS","HEKTS.IS","HLGYO.IS","HRKET.IS","HUBVC.IS","HURGZ.IS","ICBCT.IS",
    "IDGYO.IS","IHAAS.IS","INDES.IS","INFO.IS","INVEO.IS","IPEKE.IS","ISBIR.IS",
    "ISCTR.IS","ISDMR.IS","ISGSY.IS","ISGYO.IS","ISKUR.IS","ISMEN.IS","IZENR.IS",
    "IZMDC.IS","JANTS.IS","TRALT.IS","ONRYT.IS","EFOR.IS","KAREL.IS","KARSN.IS",
    "KATMR.IS","KCAER.IS","KCHOL.IS","KENT.IS","KERVT.IS","KLKIM.IS","KLMSN.IS",
    "KMPUR.IS","KOCMT.IS","KONKA.IS","KONTR.IS","KONYA.IS","KORDS.IS",
    "KOZAL.IS","KRDMA.IS","KRDMB.IS","KRDMD.IS","KRONT.IS","KRSTL.IS","KRTEK.IS",
    "KSTUR.IS","KTSKR.IS","KUTPO.IS","LIDER.IS","LIDFA.IS","LINK.IS","LOGO.IS",
    "MAALT.IS","MAGEN.IS","MAKIM.IS","MANAS.IS","MARBL.IS","MARTI.IS","MAVI.IS",
    "MEDTR.IS","MEGAP.IS","MEPET.IS","MERCN.IS","MERIT.IS","MERKO.IS","METEM.IS",
    "METRO.IS","MGROS.IS","MIPAZ.IS","MNDRS.IS","MOBTL.IS","MPARK.IS","MRSHL.IS",
    "MTRKS.IS","NETAS.IS","NTGAZ.IS","NUHCM.IS","ODAS.IS","ORGE.IS","OTKAR.IS",
    "OYAKC.IS","OZRDN.IS","PAGYO.IS","PAPIL.IS","PARSN.IS","PCILT.IS","PENGD.IS",
    "PENTA.IS","PETKM.IS","PETUN.IS","PGSUS.IS","PKART.IS","POLHO.IS","PRDGS.IS",
    "PRKAB.IS","PRKME.IS","PRZMA.IS","QNBFB.IS","QNBFL.IS","RALYH.IS","RAYSG.IS",
    "RODRG.IS","ROYAL.IS","RTALB.IS","RYGYO.IS","RYSAS.IS","SAHOL.IS","SAMAT.IS",
    "SANEL.IS","SANFM.IS","SARKY.IS","SASA.IS","SDTTR.IS","SELEC.IS","SELGD.IS",
    "SILVR.IS","SISE.IS","SKBNK.IS","SKTAS.IS","SMRTG.IS","SNGYO.IS","SOKM.IS",
    "SONME.IS","SUMAS.IS","SURGY.IS","SUWEN.IS","TABGD.IS","TATGD.IS","TAVHL.IS",
    "TCELL.IS","TEKTU.IS","TEZOL.IS","THYAO.IS","TKFEN.IS","TLMAN.IS","TOASO.IS",
    "TRGYO.IS","TSKB.IS","TTKOM.IS","TTRAK.IS","TUPRS.IS","TURGG.IS","ULKER.IS",
    "ULUSE.IS","ULUUN.IS","UMPAS.IS","UNLU.IS","USAK.IS","VAKBN.IS","VAKFN.IS",
    "VAKKO.IS","VESBE.IS","VESTL.IS","YAPRK.IS","YATAS.IS","YAYLA.IS","YKBNK.IS",
    "YUNSA.IS","ZOREN.IS","GIPTA.IS","TRHOL.IS","AAGYO.IS","BIGEN.IS","GLRMK.IS",
]

# Tekil liste, kopyasız
BIST_TICKERS = list(dict.fromkeys(PRIORITY_TICKERS + OTHER_TICKERS))
# Sadece hisse (endeks sembollerini tarama dışı bırak)
BIST_STOCKS  = [t for t in BIST_TICKERS if not t.startswith("X")]


# ==============================================================================
# BÖLÜM 2 — VERİ ÇEKME
# Marketstack'e geçince SADECE bu fonksiyon değişir.
# ==============================================================================

def _read_parquet_cache(ticker: str) -> "pd.DataFrame | None":
    """VPS parquet cache'den okur. Dosya yoksa veya 48 saatten eskiyse None döner."""
    p = _CACHE_DIR / f"{ticker}.parquet"
    if not p.exists():
        return None
    if (time.time() - p.stat().st_mtime) > 172_800:   # 48 saat
        return None
    try:
        df = pd.read_parquet(p)
        return df if not df.empty else None
    except Exception:
        return None


def fetch_data(ticker: str, period: str = "1y") -> pd.DataFrame | None:
    """
    OHLCV verisi çeker.
    Önce VPS parquet cache'e bakar; yoksa yfinance'e düşer.
    Geçiş: Marketstack → sadece bu fonksiyon güncellenir.
    """
    # ── VPS parquet cache
    _cached = _read_parquet_cache(ticker)
    if _cached is not None:
        return _cached

    # ── Fallback: yfinance
    try:
        df = yf.download(ticker, period=period, progress=False,
                         auto_adjust=True, prepost=False)
        if df.empty:
            return None
        # MultiIndex temizliği
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.dropna(subset=["Close"], inplace=True)
        # BIST bölünme/bedelsiz artırım retroaktif düzeltme
        # BIST ±%10 devre kesici → tek günde >%20 düşüş kesinlikle bölünmedir
        _is_bist = ticker.endswith(".IS") and not ticker.startswith(("XU", "XB", "XT"))
        if _is_bist and len(df) >= 5:
            price_cols = [c for c in ["Open", "High", "Low", "Close"] if c in df.columns]
            for _ in range(10):
                closes = df["Close"].ffill().values
                split_found = False
                for i in range(1, len(closes)):
                    prev_c, curr_c = closes[i - 1], closes[i]
                    if prev_c <= 0 or curr_c <= 0:
                        continue
                    ratio = prev_c / curr_c
                    if ratio >= 1.20:
                        for col in price_cols:
                            df.iloc[:i, df.columns.get_loc(col)] = df.iloc[:i][col].values / ratio
                        if "Volume" in df.columns:
                            df.iloc[:i, df.columns.get_loc("Volume")] = df.iloc[:i]["Volume"].values * ratio
                        split_found = True
                        break
                if not split_found:
                    break
        return df
    except Exception as e:
        print(f"[fetch_data] {ticker} hata: {e}")
        return None


# ==============================================================================
# BÖLÜM 3 — TEKNİK GÖSTERGELER
# ==============================================================================

def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss  = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs    = gain / loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """SMA50, SMA200, RSI14, EMA20, OBV ekler."""
    df = df.copy()
    df["sma50"]  = df["Close"].rolling(50).mean()
    df["sma200"] = df["Close"].rolling(200).mean()
    df["ema20"]  = df["Close"].ewm(span=20, adjust=False).mean()
    df["rsi"]    = calc_rsi(df["Close"])

    # OBV
    obv = [0]
    for i in range(1, len(df)):
        if df["Close"].iloc[i] > df["Close"].iloc[i - 1]:
            obv.append(obv[-1] + df["Volume"].iloc[i])
        elif df["Close"].iloc[i] < df["Close"].iloc[i - 1]:
            obv.append(obv[-1] - df["Volume"].iloc[i])
        else:
            obv.append(obv[-1])
    df["obv"] = obv
    return df


# ==============================================================================
# BÖLÜM 4 — XU100 ANALİZİ
# ==============================================================================

def analyze_xu100() -> dict:
    """XU100 için açık kart verisi üretir."""
    df = fetch_data("XU100.IS", period="1y")
    if df is None or len(df) < 50:
        return {"hata": "Veri çekilemedi"}

    df = calc_indicators(df)
    last = df.iloc[-1]
    prev = df.iloc[-2]

    kapanis      = round(float(last["Close"]), 2)
    degisim_pct  = round((float(last["Close"]) - float(prev["Close"])) / float(prev["Close"]) * 100, 2)
    sma50        = round(float(last["sma50"]),  2) if pd.notna(last["sma50"])  else None
    sma200       = round(float(last["sma200"]), 2) if pd.notna(last["sma200"]) else None
    rsi          = round(float(last["rsi"]),    1) if pd.notna(last["rsi"])    else None

    # Rejim
    if sma200 and kapanis > sma200 * 1.03:
        rejim = "Boğa Trendi"
        rejim_renk = "green"
    elif sma200 and kapanis < sma200 * 0.97:
        rejim = "Ayı Baskısı"
        rejim_renk = "red"
    else:
        rejim = "Denge Bölgesi"
        rejim_renk = "orange"

    # 52 haftalık pozisyon
    yillik_yuksek = round(float(df["High"].max()), 2)
    yillik_dusuk  = round(float(df["Low"].min()),  2)
    aralik        = yillik_yuksek - yillik_dusuk
    pozisyon_pct  = round((kapanis - yillik_dusuk) / aralik * 100, 1) if aralik > 0 else 50

    return {
        "kapanis":       kapanis,
        "degisim_pct":   degisim_pct,
        "sma50":         sma50,
        "sma200":        sma200,
        "rsi":           rsi,
        "rejim":         rejim,
        "rejim_renk":    rejim_renk,
        "yillik_yuksek": yillik_yuksek,
        "yillik_dusuk":  yillik_dusuk,
        "pozisyon_pct":  pozisyon_pct,
    }


# ==============================================================================
# BÖLÜM 4b — XU100 GRAFİK VERİSİ  (app.py calculate_synthetic_sentiment ile özdeş)
# ==============================================================================

def generate_xu100_chart_data(n_rows: int = 30) -> list:
    """
    app.py > calculate_synthetic_sentiment() ile birebir aynı formül:
      typical_price = (H + L + C) / 3
      EMA1  = typical_price.ewm(span=6, adjust=False).mean()
      EMA2  = EMA1.ewm(span=6,  adjust=False).mean()
      DEMA6 = 2*EMA1 - EMA2
      MF_Smooth = (typical_price - DEMA6) / DEMA6 * 1000   ← momentum barları
      STP       = EMA1                                      ← sarı çizgi
      Price     = Close                                     ← beyaz çizgi
    Son n_rows satır döner — her öğe: {date, mf, stp, price}
    date formatı: '%d %b'  (ör. "27 Mar", "11 May")
    """
    try:
        df = fetch_data("XU100.IS", period="6mo")
        if df is None or len(df) < 30:
            return []

        # OHLCV — yfinance MultiIndex temizliği zaten fetch_data'da yapılıyor
        typical = (df["High"] + df["Low"] + df["Close"]) / 3
        ema1    = typical.ewm(span=6, adjust=False).mean()
        ema2    = ema1.ewm(span=6, adjust=False).mean()
        dema6   = 2 * ema1 - ema2
        mf_smooth = (typical - dema6) / dema6 * 1000

        df = df.copy()
        df["MF_Smooth"] = mf_smooth.values
        df["STP"]       = ema1.values
        df["Price"]     = df["Close"]

        # Son n_rows satır
        tail = df.tail(n_rows).reset_index()

        # Tarih sütunu bul
        date_col = None
        for dc in ("Date", "Datetime", "date", "datetime"):
            if dc in tail.columns:
                date_col = dc
                break
        if date_col is None:
            # Fallback: son günden geriye giderek tarih üret
            import datetime as _dt
            end = _dt.date.today()
            dates = pd.date_range(end=end, periods=n_rows, freq="B")
            tail["_date"] = dates
            date_col = "_date"

        tail["_date"] = pd.to_datetime(tail[date_col])

        result = []
        for _, row in tail.iterrows():
            result.append({
                "date":  row["_date"].strftime("%d %b"),
                "mf":    round(float(row["MF_Smooth"]), 4),
                "stp":   round(float(row["STP"]),       2),
                "price": round(float(row["Price"]),     2),
            })
        return result

    except Exception as e:
        print(f"[generate_xu100_chart_data] hata: {e}")
        return []


# ==============================================================================
# BÖLÜM 5 — BIST PIYASA SAĞLIĞI TARAMASI
# ==============================================================================

def scan_bist_health(ticker_list: list = None, limit: int = 150) -> dict:
    """
    BIST hisselerini tarar, aggregate piyasa sağlık skoru üretir.
    Ayrıca güçlü sinyal veren hisseleri one_cikanlar listesine ekler.
    limit: kaç hisse taransın (API istek limiti için)
    """
    if ticker_list is None:
        ticker_list = BIST_STOCKS[:limit]

    taranan       = 0
    sma200_ustu   = 0
    sma50_ustu    = 0
    rsi_50_ustu   = 0
    guclu_sinyal  = 0   # SMA200 üstü + RSI>50 + hacim artışı

    one_cikanlar  = []  # Güçlü sinyal veren hisselerin detayları

    for ticker in ticker_list:
        try:
            df = fetch_data(ticker, period="1y")
            if df is None or len(df) < 200:
                continue
            df = calc_indicators(df)
            last = df.iloc[-1]
            prev = df.iloc[-2]

            close   = float(last["Close"])
            s200    = float(last["sma200"]) if pd.notna(last["sma200"]) else None
            s50     = float(last["sma50"])  if pd.notna(last["sma50"])  else None
            rsi_val = float(last["rsi"])    if pd.notna(last["rsi"])    else None

            taranan += 1

            if s200 and close > s200:
                sma200_ustu += 1
            if s50 and close > s50:
                sma50_ustu += 1
            if rsi_val and rsi_val > 50:
                rsi_50_ustu += 1

            # Güçlü sinyal: 3 kriter aynı anda
            vol_avg10   = df["Volume"].iloc[-10:].mean()
            vol_bugun   = float(last["Volume"])
            hacim_artti = vol_bugun > vol_avg10 * 1.2

            if (s200 and close > s200 and
                rsi_val and rsi_val > 52 and
                hacim_artti):
                guclu_sinyal += 1

                # Değişim % (önceki güne göre)
                degisim = round((close - float(prev["Close"])) / float(prev["Close"]) * 100, 2)
                # SMA200'e göre uzaklık %
                s200_uzaklik = round((close - s200) / s200 * 100, 1) if s200 else None

                # --- Detay alanları (hisse analiz paneli için) ---
                # Rejim
                if close > s200 * 1.03:
                    rejim, rejim_renk = "Boğa Trendi", "green"
                elif close < s200 * 0.97:
                    rejim, rejim_renk = "Ayı Baskısı", "red"
                else:
                    rejim, rejim_renk = "Denge Bölgesi", "orange"

                # 52 haftalık pozisyon
                yl_yuksek = round(float(df["High"].max()), 2)
                yl_dusuk  = round(float(df["Low"].min()),  2)
                aralik    = yl_yuksek - yl_dusuk
                poz_pct   = round((close - yl_dusuk) / aralik * 100, 1) if aralik > 0 else 50

                # OBV yönü (son 5 bar)
                obv_s5   = df["obv"].iloc[-5:].values.tolist()
                obv_yonu = "yukari" if obv_s5[-1] > obv_s5[0] else "asagi"

                # RSI trendi (son 3 bar)
                rsi_s3    = df["rsi"].iloc[-3:].values.tolist()
                rsi_trend = "yukari" if rsi_s3[-1] > rsi_s3[0] else "asagi"

                # Destek / Direnç (20 günlük)
                destek = round(float(df["Low"].iloc[-20:].min()),  2)
                direnc = round(float(df["High"].iloc[-20:].max()), 2)

                # EMA 5, 8, 13
                ema5  = round(float(df["Close"].ewm(span=5,  adjust=False).mean().iloc[-1]), 2)
                ema8  = round(float(df["Close"].ewm(span=8,  adjust=False).mean().iloc[-1]), 2)
                ema13 = round(float(df["Close"].ewm(span=13, adjust=False).mean().iloc[-1]), 2)

                # Chart data — DEMA6 momentum formülü (son 30 bar)
                try:
                    _typ  = (df["High"] + df["Low"] + df["Close"]) / 3
                    _e1   = _typ.ewm(span=6, adjust=False).mean()
                    _e2   = _e1.ewm(span=6, adjust=False).mean()
                    _dema = 2 * _e1 - _e2
                    _mf   = (_typ - _dema) / _dema * 1000
                    _t    = df.tail(30).copy()
                    _t["_MF"]  = _mf.values[-30:]
                    _t["_STP"] = _e1.values[-30:]
                    _t = _t.reset_index()
                    _dc = next((c for c in ("Date","Datetime","date","datetime") if c in _t.columns), None)
                    if _dc:
                        _t["_d"] = pd.to_datetime(_t[_dc])
                        chart_data = [{"date": r["_d"].strftime("%d %b"), "mf": round(float(r["_MF"]),4), "stp": round(float(r["_STP"]),2), "price": round(float(r["Close"]),2)} for _, r in _t.iterrows()]
                    else:
                        chart_data = []
                except Exception:
                    chart_data = []

                one_cikanlar.append({
                    "ticker":       ticker.replace(".IS", ""),
                    "close":        round(close, 2),
                    "degisim_pct":  degisim,
                    "rsi":          round(rsi_val, 1),
                    "sma200_pct":   s200_uzaklik,
                    "hacim_x":      round(vol_bugun / vol_avg10, 2) if vol_avg10 else None,
                    # detay alanları
                    "sma50":        round(s50, 2) if s50 else None,
                    "sma200":       round(s200, 2) if s200 else None,
                    "rejim":        rejim,
                    "rejim_renk":   rejim_renk,
                    "yillik_yuksek": yl_yuksek,
                    "yillik_dusuk":  yl_dusuk,
                    "pozisyon_pct":  poz_pct,
                    "obv_yonu":     obv_yonu,
                    "rsi_trend":    rsi_trend,
                    "destek":       destek,
                    "direnc":       direnc,
                    "ema5":         ema5,
                    "ema8":         ema8,
                    "ema13":        ema13,
                    "chart_data":   chart_data,
                })

            time.sleep(0.05)  # yfinance rate limit koruması

        except Exception as e:
            print(f"[scan_bist_health] {ticker} atlandı: {e}")
            continue

    if taranan == 0:
        return {"hata": "Hiç hisse taranamadı"}

    # RSI'ya göre sırala (en güçlü önce), max 20 kayıt
    one_cikanlar.sort(key=lambda x: x["rsi"], reverse=True)
    one_cikanlar = one_cikanlar[:20]

    # Genel skor: SMA200 üstü % × 0.5 + RSI>50 % × 0.3 + güçlü sinyal % × 0.2
    s200_pct  = sma200_ustu / taranan * 100
    rsi_pct   = rsi_50_ustu / taranan * 100
    guclu_pct = guclu_sinyal / taranan * 100
    genel_skor = round(s200_pct * 0.5 + rsi_pct * 0.3 + guclu_pct * 0.2, 1)

    return {
        "taranan":          taranan,
        "sma200_ustu":      sma200_ustu,
        "sma200_ustu_pct":  round(s200_pct, 1),
        "sma50_ustu":       sma50_ustu,
        "rsi_50_ustu":      rsi_50_ustu,
        "guclu_sinyal":     guclu_sinyal,
        "genel_skor":       genel_skor,
        "one_cikanlar":     one_cikanlar,
    }


# ==============================================================================
# BÖLÜM 6 — JSON ÜRETİMİ
# ==============================================================================

KILITLI_MODULLER = {
    "ict_bottomline": {
        "baslik":  "ICT Bottom Line",
        "teaser":  "Likidite yönü, kritik OB bölgesi ve kurumsal ayak izi analizi",
        "plan":    "PRO"
    },
    "teknik_seviyeler": {
        "baslik":  "Teknik Seviyeler",
        "teaser":  "Destek / direnç matrisi — 5 kritik fiyat seviyesi",
        "plan":    "PRO"
    },
    "smc_hacim": {
        "baslik":  "SMC Hacim Analizi",
        "teaser":  "Para akışı anomalisi ve kurumsal hacim izleri",
        "plan":    "ELITE"
    },
    "risk_haritasi": {
        "baslik":  "Risk & Volatilite Haritası",
        "teaser":  "ATR bazlı risk bölgeleri ve volatilite yapısı",
        "plan":    "ELITE"
    },
}


def build_latest_json(output_path: str = "latest.json") -> dict:
    """
    Tam JSON'u üretir ve dosyaya yazar.
    produce_json.py bu fonksiyonu çağırır.
    """
    now = datetime.datetime.now()

    print("[1/4] XU100 analiz ediliyor...")
    xu100_data = analyze_xu100()

    print("[2/4] XU100 grafik verisi hesaplaniyor...")
    chart_data = generate_xu100_chart_data(n_rows=30)

    print("[3/4] BIST taraması başlıyor...")
    piyasa_data = scan_bist_health()

    print("[4/4] JSON derleniyor...")

    payload = {
        "meta": {
            "tarih":       now.strftime("%Y-%m-%d"),
            "guncelleme":  now.strftime("%H:%M"),
            "versiyon":    "1.0",
            "kaynak":      "Önceki kapanış verisi · Eğitim / veri analitiği amaçlıdır"
        },
        "xu100":         xu100_data,
        "xu100_grafik":  chart_data,
        "piyasa_ozeti":  piyasa_data,
        "kilitli":       KILITLI_MODULLER,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[OK] {output_path} yazildi - {now.strftime('%H:%M:%S')}")
    return payload


# ==============================================================================
# BÖLÜM 7 — DOĞRUDAN ÇALIŞTIRILIRSA TEST
# ==============================================================================

if __name__ == "__main__":
    result = build_latest_json("latest_test.json")
    print("\n--- OZET ---")
    print(f"XU100   : {result['xu100'].get('kapanis')} / Rejim: {result['xu100'].get('rejim')}")
    print(f"Taranan : {result['piyasa_ozeti'].get('taranan')} hisse")
    print(f"SMA200+ : %{result['piyasa_ozeti'].get('sma200_ustu_pct')}")
    print(f"Skor    : {result['piyasa_ozeti'].get('genel_skor')}/100")
