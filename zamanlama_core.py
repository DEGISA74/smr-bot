# -*- coding: utf-8 -*-
"""
zamanlama_core.py — 4 SAATLİK MOMENTUM & ZAMANLAMA FİLTRESİ
===========================================================
Kullanım Amacı:
  - Günlük trendi arkasına almış hisselerde "tepeden almayı önleyen" koruyucu fren.
  - 4 saatlik barlarda aşırı alım / şişkinlik tespiti (koruma amaçlı).
  - MKK Yabancı Takas serisi (3+ gün streak) ile çoklu zaman dilimi uyumu.

İlkeler:
  - Yön != Eylem: Asla "Al", "Giriş Yap", "Stop Şurası" gibi emir / yatırım tavsiyesi vermez.
  - Bayat Veri Reddi: saatlik_kapi kapsam denetimi (hata verirse REDDEDER) + son bar
    3 takvim gününden eskiyse veri yok sayılır.
  - Yarım Bar Koruması: kapanmamış seans barı hesaba GİRMEZ (_son_bar_yarim_mi).
  - İki Kademe: 4S yalnızca günlük kapıyı geçen hissede konuşur (gunluk_kapi_gecti).
"""
from __future__ import annotations

import os
from datetime import datetime, time as _dtime, timedelta
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEPO_4S = os.path.join(BASE_DIR, "veriler_4s")


# Bayat eşiği: 4S'te günde ~2 bar oluşur. 3 takvim günü ≈ 6 bar boşluk; RSI 14
# barla hesaplandığı için bunun ötesi "eski veriyle hüküm" demektir.
BAYAT_GUN_ESIGI = 3
# BIST seansı 18:10'da biter; 18:15'ten önce günün son barı KAPANMAMIŞTIR.
_SEANS_KAPANIS = _dtime(18, 15)


def _son_bar_yarim_mi(df: pd.DataFrame) -> bool:
    """Son bar HENÜZ KAPANMADI mı? (25 Ağu 2026)

    BIST'te 4S barlar 09:30 ve 13:30 damgalı. Bugüne ait son bar, seans
    kapanana kadar yarımdır — 49 dakikalık bir mumla RSI/WaveTrend hesaplamak
    hükmü gün içinde oynatır (sabah 'dengeli', akşam 'şişkin'). Şüphede
    YARIM say: eksik bilgiyle konuşmaktansa bir bar geriden konuşmak yeğdir.
    """
    try:
        son = pd.Timestamp(df.index[-1])
        simdi = pd.Timestamp.now(tz=son.tzinfo) if son.tzinfo is not None else pd.Timestamp.now()
        if son.date() != simdi.date():
            return False                      # dünkü/daha eski bar → kapanmış
        return simdi.time() < _SEANS_KAPANIS   # bugünkü bar + seans sürüyor → yarım
    except Exception:
        return True


def get_4s_data(symbol: str) -> pd.DataFrame | None:
    """4 saatlik parquet deposundan veriyi bayat/yarım korumasıyla okur.

    Üç kapı: (1) saatlik_kapi kapsam denetimi — HATA VERİRSE REDDEDER,
    (2) dosyanın son barı 3 günden eski olmamalı, (3) kapanmamış son bar atılır.
    """
    sym = symbol.replace(".IS", "").replace(".is", "").upper()

    # 1. Kapsam/tazelik kapısı — bekçi konuşamıyorsa GEÇİRME (fail-closed).
    #    Eski hali `except: pass` idi: bekçi hata verince kapı sessizce açık
    #    kalıyordu. Bu projede sessiz arıza en pahalı hata tipi.
    try:
        from saatlik_kapi import saatlik_durum
        if not saatlik_durum(sym).get("ok"):
            return None
    except ImportError:
        pass                     # modül hiç yoksa (kurulum eksik) diğer kapılar korur
    except Exception:
        return None              # bekçi VAR ama hata verdi → güvenme, reddet

    # 2. Dosya
    path = os.path.join(DEPO_4S, f"{sym}.IS_4h.parquet")
    if not os.path.exists(path):
        return None

    try:
        df = pd.read_parquet(path)
        if df is None or df.empty or len(df) < 20:
            return None

        # 3. Bayat mı? (son bar 3 takvim gününden eski)
        last_dt = df.index[-1]
        last_date = last_dt.date() if hasattr(last_dt, "date") else pd.to_datetime(last_dt).date()
        if (datetime.now().date() - last_date).days > BAYAT_GUN_ESIGI:
            return None

        # 4. Kapanmamış son barı at
        if _son_bar_yarim_mi(df):
            df = df.iloc[:-1]
            if len(df) < 20:
                return None

        return df
    except Exception:
        return None


def gunluk_kapi_gecti(df_gunluk: pd.DataFrame | None) -> bool:
    """1. KADEME — '4S konuşmaya değer mi?' (25 Ağu 2026)

    İki kademeli mimarinin üst basamağı: günlük tabloda trend ve para akışı
    olumsuzsa 4S zamanlaması ANLAMSIZDIR. Çöp hissede '4S dengeli' yazmak
    okuyucuyu yanıltır ve ekranı kalabalıklaştırır.

    Kapı: fiyat 50 günlük ortalamanın üstünde VE 20 günlük para akışı (CMF)
    negatif değil. İkisi de günlük veriden, ek maliyet yok.
    """
    try:
        if df_gunluk is None or len(df_gunluk) < 50:
            return False
        c = df_gunluk["Close"]
        if isinstance(c, pd.DataFrame):
            c = c.iloc[:, 0]
        c = c.astype(float)
        price = float(c.iloc[-1])
        sma50 = float(c.rolling(50).mean().iloc[-1])
        if not (price >= sma50):
            return False
        try:
            from indicators import compute_cmf
            cmf = compute_cmf(df_gunluk)
            if cmf is not None and float(cmf) < 0:
                return False
        except Exception:
            pass          # CMF okunamadıysa trend şartı tek başına yeter
        return True
    except Exception:
        return False


def _calc_rsi(seri: pd.Series, n: int = 14) -> pd.Series:
    d = seri.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def evaluate_4s_timing(symbol: str, df_4s: pd.DataFrame | None = None) -> dict:
    """
    4 Saatlik mumlar üzerinde koruyucu zamanlama ve momentum analizini yapar.
    
    Dönüş:
      - status: 'ASIRI_ALIM_SISKIN' | 'DENGELI' | 'BASKI_ALTINDA' | 'YOK'
      - rsi14: 4S RSI 14 değeri
      - badge_text: Ekranda gösterilecek kısa rozet metni
      - badge_color: 'red' | 'green' | 'yellow' | 'gray'
      - summary_text: Koruyucu objektif açıklama
    """
    if df_4s is None:
        df_4s = get_4s_data(symbol)

    res = {
        "status": "YOK",
        "label": "4S Veri Yok / Kapsam Dışı",
        "rsi14": None,
        "badge_text": "",
        "badge_color": "gray",
        "summary_text": "4 saatlik veri bulunamadı veya kapsam dışı.",
    }

    if df_4s is None or len(df_4s) < 20:
        return res

    try:
        c = df_4s["Close"].astype(float)
        h = df_4s["High"].astype(float)
        l = df_4s["Low"].astype(float)

        last_c = float(c.iloc[-1])

        # 1. RSI 14
        rsi_series = _calc_rsi(c, 14)
        last_rsi = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0

        # 2. WaveTrend (10, 11)
        tp = (h + l + c) / 3
        esa = tp.ewm(span=10, adjust=False).mean()
        dd = (tp - esa).abs().ewm(span=10, adjust=False).mean()
        ci = (tp - esa) / (0.015 * dd.replace(0, np.nan))
        wt1 = ci.ewm(span=11, adjust=False).mean()
        wt2 = wt1.rolling(4).mean()
        last_wt1 = float(wt1.iloc[-1])
        last_wt2 = float(wt2.iloc[-1])
        wt_al = last_wt1 > last_wt2

        # 3. Hareketli Ortalamalar
        sma20 = float(c.rolling(20).mean().iloc[-1])
        sma50 = float(c.rolling(50).mean().iloc[-1]) if len(c) >= 50 else sma20

        # 4. Koruyucu Karar Ağacı (Yalnızca objektif durum tespiti, tavsiye yok)
        if last_rsi >= 68 or (last_rsi >= 62 and last_wt1 > 55 and not wt_al):
            status = "ASIRI_ALIM_SISKIN"
            label = "🔴 4S Şişkin / Aşırı Alım"
            badge_text = f"⏱ 4S: Şişkin (RSI {last_rsi:.0f})"
            badge_color = "red"
            summary_text = (
                f"4 saatlik grafik aşırı alım bölgesinde (RSI {last_rsi:.1f}). "
                f"Kısa vadeli düzeltme riski — temkinli olunmalı."
            )
        elif last_c < sma50 and last_rsi < 40 and not wt_al:
            status = "BASKI_ALTINDA"
            label = "⚪ 4S Baskı Altında"
            badge_text = f"⏱ 4S: Baskı Altında (RSI {last_rsi:.0f})"
            badge_color = "gray"
            summary_text = f"4S momentum ortalamaların altında (RSI {last_rsi:.1f})."
        elif last_rsi <= 48 and wt_al:
            status = "DENGELI"
            label = "🟢 4S Dengeli / Destekte"
            badge_text = f"⏱ 4S: Dengeli (RSI {last_rsi:.0f})"
            badge_color = "green"
            summary_text = f"4S RSI dip bölgesinde dengeleniyor ({last_rsi:.1f}) ve WaveTrend pozitif."
        else:
            status = "DENGELI"
            label = "🟡 4S Dengeli"
            badge_text = f"⏱ 4S: Dengeli (RSI {last_rsi:.0f})"
            badge_color = "yellow"
            summary_text = f"4S RSI normal bölgede ({last_rsi:.1f})."

        res.update({
            "status": status,
            "label": label,
            "rsi14": round(last_rsi, 1),
            "wt_status": "AL" if wt_al else "SAT",
            "badge_text": badge_text,
            "badge_color": badge_color,
            "summary_text": summary_text,
        })
    except Exception as ex:
        res["summary_text"] = f"Hesaplama hatası: {str(ex)[:50]}"

    return res


def evaluate_asimetrik_firsat(symbol: str, yab_sig: dict | None = None, timing_res: dict | None = None) -> dict:
    """
    MKK Yabancı Takas serisi (yalnızca 3+ gün streak veya anchor) ile 4S momentumunu bağlar.
    ⚠️ in_days >= 1 (tek günlük giriş) karnede negatif çıktığı için filtrede KULLANILMAZ.
    """
    from db_layer import _compute_mkk_yabanci_signals

    if yab_sig is None:
        yab_sig = _compute_mkk_yabanci_signals(symbol)
    if timing_res is None:
        timing_res = evaluate_4s_timing(symbol)

    streak = yab_sig.get("streak_days", 0) or 0
    anchor = yab_sig.get("f_yabanci_anchor", 0) or 0
    out_days = yab_sig.get("out_days", 0) or 0

    t_status = timing_res.get("status", "YOK")
    rsi = timing_res.get("rsi14", 50)

    # Yalnızca sağlam kanıtlar: 3+ gün kesintisiz artış serisi veya anchor
    has_yabanci_streak = (streak >= 3 or anchor == 1)

    if has_yabanci_streak and t_status == "ASIRI_ALIM_SISKIN":
        verdict = "YABANCI_VAR_ASIRI_ALIM"
        verdict_badge = "⚠️ YABANCI VAR AMA 4S ŞİŞKİN"
        verdict_color = "yellow"
        verdict_desc = (
            f"Yabancı takas serisi sürüyor ({streak} gün alıcı) ancak 4 saatlik grafik aşırı alımda "
            f"(RSI {rsi}). Kısa vadeli düzeltme riski."
        )
    elif has_yabanci_streak and t_status == "DENGELI":
        verdict = "YABANCI_DESTEKLI_DENGELI"
        verdict_badge = "🟢 YABANCI SERİSİ + 4S DENGELİ"
        verdict_color = "green"
        verdict_desc = f"Yabancı takas serisi ({streak} gün alıcı) ve 4 saatlik momentum dengeli bölgede."
    elif out_days >= 2 and t_status in ("BASKI_ALTINDA", "ASIRI_ALIM_SISKIN"):
        verdict = "YABANCI_CIKIS_RISK"
        verdict_badge = "⛔ YABANCI ÇIKIŞI & ZAYIF MOMENTUM"
        verdict_color = "red"
        verdict_desc = f"Son günlerde yabancı çıkışı var ({out_days} gün top çıkışta) ve 4S momentum zayıf."
    else:
        verdict = "STANDART"
        verdict_badge = ""
        verdict_color = "gray"
        verdict_desc = "Takas ve 4S momentum nötr bölgede."

    return {
        "symbol": symbol,
        "verdict": verdict,
        "verdict_badge": verdict_badge,
        "verdict_color": verdict_color,
        "verdict_desc": verdict_desc,
        "yabanci_streak": streak,
        "timing": timing_res,
    }
