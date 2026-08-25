# -*- coding: utf-8 -*-
"""
pusula_engine.py — 5 BOYUTLU KOMBİNATUVAR PİYASA PUSULASI MOTORU
================================================================
Patron Terminal / Smart Money Radar için kurumsal piyasa sentez motoru.

Girdiler:
  1. Geometri & Çizgi Yapıları (Alçalan/Yükselen Kama, Üçgen kırılımları, temas sayısı, gün yaşı, tetik/geçersizlik)
  2. Price Action & ICT (Bearish/Bullish SFP, CHoCH, BOS, FVG, Order Block, Dow Jones HH/HL zinciri)
  3. Uyumsuzluklar & Osilatörler (Wilder RSI Pozitif/Negatif Uyumsuzluk, CMF/OBV Fiyat Ayrışması, RSI Uç Durumu)
  4. Hacim & VPA İmzası (%200+ Hacim Şoku, Emilim/Absorption, Hacimsiz Test/No Supply, Hacim Zayıflığı)
  5. Hareketli Ortalamalar & Rejim Eşikleri (SMA 50/100/200 Mesafeleri, 52H Zirve/Dip, EMA 21 Dinamik Destek, Trend Yaşı)

Çıktı:
  - title: Başlık (Karakter & Olay)
  - text: 2-3 cümlelik derinlikli, teknik verilerle güçlendirilmiş, akıcı Türkçe kurumsal sentez metni.
  - note: 1 cümlelik "Ne anlama geliyor / Ne yapmalı?" operasyonel içgörü.
  - color: Durum rengi (Yeşil, Kırmızı, Sarı, Mavi vb.)
  - archetype: Tespit edilen piyasa mekaniği arketipi.
"""

from __future__ import annotations
import math
from typing import Any, Dict, Optional
import pandas as pd
import numpy as np


# Renk Sabitleri
CLR_GREEN = "#00e676"    # Güçlü Boğa / Onaylı Kırılım
CLR_RED = "#ff5252"      # Net Ayı / Dağıtım / Tuzak
CLR_YELLOW = "#ffb300"   # Uyarı / Çatlak / Sıkışma / Zayıflık
CLR_CYAN = "#38bdf8"     # Taban Oluşumu / Erken Uyanış / Pozitif Uyumsuzluk
CLR_NEUTRAL = "#94a3b8"  # Nötr / Denge


def _safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    if val is None:
        return default
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (ValueError, TypeError):
        return default


def _calculate_mas_and_structure(df: pd.DataFrame) -> Dict[str, Any]:
    """DataFrame'den hareketli ortalamaları ve yapısal metrikleri güvenle hesaplar."""
    res = {
        "price": None,
        "ema5": None, "ema9": None, "ema21": None,
        "sma50": None, "sma100": None, "sma200": None,
        "sma50_dist_pct": None, "sma200_dist_pct": None,
        "sma50_days_above": 0, "sma50_days_below": 0,
        "high52": None, "low52": None, "dist_52h_high_pct": None, "dist_52h_low_pct": None,
        "vol_ratio": None, "vol_shock": False,
        "rsi": None, "rsi_streak_extreme": 0, "rsi_extreme_type": None,
        "ret5": None, "ret20": None,
    }
    if df is None or len(df) < 5:
        return res

    try:
        close = df['Close']
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = close.dropna()
        if len(close) < 5:
            return res

        curr_p = float(close.iloc[-1])
        res["price"] = curr_p

        # EMA'lar
        res["ema5"] = float(close.ewm(span=5, adjust=False).mean().iloc[-1])
        res["ema9"] = float(close.ewm(span=9, adjust=False).mean().iloc[-1])
        res["ema21"] = float(close.ewm(span=21, adjust=False).mean().iloc[-1])

        # SMA'lar
        if len(close) >= 50:
            s50 = close.rolling(50).mean()
            res["sma50"] = float(s50.iloc[-1])
            res["sma50_dist_pct"] = ((curr_p / res["sma50"]) - 1.0) * 100
            
            # SMA 50 üstünde/altında kaç gün?
            above_series = close >= s50
            if bool(above_series.iloc[-1]):
                streak = 0
                for v in reversed(above_series.values):
                    if v: streak += 1
                    else: break
                res["sma50_days_above"] = streak
            else:
                streak = 0
                for v in reversed(above_series.values):
                    if not v: streak += 1
                    else: break
                res["sma50_days_below"] = streak

        if len(close) >= 100:
            res["sma100"] = float(close.rolling(100).mean().iloc[-1])

        if len(close) >= 200:
            s200 = float(close.rolling(200).mean().iloc[-1])
            res["sma200"] = s200
            res["sma200_dist_pct"] = ((curr_p / s200) - 1.0) * 100

        # 52 Hafta (252 bar)
        p_len = min(len(close), 252)
        h52 = float(df['High'].iloc[-p_len:].max()) if 'High' in df else float(close.iloc[-p_len:].max())
        l52 = float(df['Low'].iloc[-p_len:].min()) if 'Low' in df else float(close.iloc[-p_len:].min())
        res["high52"] = h52
        res["low52"] = l52
        if h52 > 0:
            res["dist_52h_high_pct"] = ((curr_p / h52) - 1.0) * 100
        if l52 > 0:
            res["dist_52h_low_pct"] = ((curr_p / l52) - 1.0) * 100

        # Getiriler
        if len(close) >= 6:
            res["ret5"] = float((curr_p / close.iloc[-6] - 1.0) * 100)
        if len(close) >= 21:
            res["ret20"] = float((curr_p / close.iloc[-21] - 1.0) * 100)

        # Hacim
        if 'Volume' in df:
            vol = df['Volume']
            if isinstance(vol, pd.DataFrame):
                vol = vol.iloc[:, 0]
            if len(vol) >= 20:
                v_last = float(vol.iloc[-1])
                v_avg20 = float(vol.iloc[-21:-1].mean())
                if v_avg20 > 0:
                    ratio = v_last / v_avg20
                    res["vol_ratio"] = ratio
                    res["vol_shock"] = (ratio >= 2.0)

        # RSI (14)
        if len(close) >= 15:
            delta = close.diff()
            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)
            avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            rs = avg_gain / avg_loss.replace(0, np.nan)
            rsi_series = 100 - (100 / (1 + rs))
            if not rsi_series.empty:
                last_rsi = float(rsi_series.iloc[-1])
                res["rsi"] = last_rsi
                if last_rsi >= 80:
                    res["rsi_extreme_type"] = "overbought"
                    streak = 0
                    for val in reversed(rsi_series.values):
                        if val >= 80: streak += 1
                        else: break
                    res["rsi_streak_extreme"] = streak
                elif last_rsi <= 25:
                    res["rsi_extreme_type"] = "oversold"
                    streak = 0
                    for val in reversed(rsi_series.values):
                        if val <= 25: streak += 1
                        else: break
                    res["rsi_streak_extreme"] = streak

    except Exception:
        pass

    return res


def _synthesize_raw(
    ticker: str,
    df: Optional[pd.DataFrame] = None,
    cizgi_view: Optional[Dict[str, Any]] = None,
    ict_data: Optional[Dict[str, Any]] = None,
    terazi_res: Optional[Dict[str, Any]] = None,
    feat: Optional[Dict[str, Any]] = None,
    hier_pack: Optional[Dict[str, Any]] = None,
    is_index: bool = False,
    display_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    5 Boyutlu Kombinatuvar Sentez Fonksiyonu.
    Gelen tüm verileri öncelik matrisine göre tartıp tek bir akıllı, profesyonel
    sentez başlığı, metni ve notu üretir.
    """
    name = display_name or ticker.upper()
    feat = feat or {}
    hier_pack = hier_pack or {}
    terazi_res = terazi_res or {}
    ict_data = ict_data or {}
    cizgi_view = cizgi_view or {}

    # 0. Temel Hesaplamalar
    ma = _calculate_mas_and_structure(df)
    price = ma["price"]
    price_str = f"{price:.2f}" if price is not None else ""

    # Hacim çarpanı
    vol_ratio = ma["vol_ratio"]
    vol_pct_str = f"%{int(round(vol_ratio * 100))}" if vol_ratio is not None else ""
    vol_x_str = f"{vol_ratio:.1f}x" if vol_ratio is not None else ""

    # RSI & SFP
    rsi_val = _safe_float(feat.get("f_rsi")) if feat.get("f_rsi") is not None else ma["rsi"]
    rsi_streak = ma["rsi_streak_extreme"]
    sfp_bear = bool(feat.get("f_sfp_bear")) or (ict_data.get("sfp", {}).get("title") == "⚠️ Bearish SFP (Boğa Tuzağı)")
    sfp_bull = bool(feat.get("f_sfp_bull")) or (ict_data.get("sfp", {}).get("title") == "💎 Bullish SFP (Ayı Tuzağı)")

    # Para Akışı / CMF / OBV
    cmf_dual = feat.get("f_cmf_dual", "")
    short_flow = hier_pack.get("_h_5_lbl", "")
    mid_flow = hier_pack.get("_h_20_lbl", "")

    # Çizgi Yapısı (Formasyon)
    has_cizgi = bool(cizgi_view.get("available"))
    cizgi_pat = cizgi_view.get("pattern_label", "")
    cizgi_stage = cizgi_view.get("stage", "")
    cizgi_trig = _safe_float(cizgi_view.get("trigger"))
    cizgi_inval = _safe_float(cizgi_view.get("invalidation"))
    cizgi_bars = cizgi_view.get("bar", 0)
    cizgi_dir = cizgi_view.get("direction", "")

    # Terazi oyları
    terazi_contra = terazi_res.get("karsi_oy")
    terazi_hukum = terazi_res.get("hukum", "")
    terazi_conf = terazi_res.get("guven_pct", 50)
    terazi_conflict = terazi_res.get("celiskili", False)

    # SMA'lar ve Dirençler
    sma50 = ma["sma50"]
    sma100 = ma["sma100"]
    sma200 = ma["sma200"]
    sma50_days_up = ma["sma50_days_above"]
    sma50_days_down = ma["sma50_days_below"]

    # ─────────────────────────────────────────────────────────────────────────
    # ÖNCELİK 1: ÇİZGİ YAPISI / FORMASYON KIRILIMI (KLGYO VAKASI VB.)
    # ─────────────────────────────────────────────────────────────────────────
    if has_cizgi and cizgi_stage in ("KIRILIM_DOĞRULANDI", "KIRILIM_ADAYI", "YENİDEN_TEST"):
        # Direnç hedefleri
        res_targets = []
        if price is not None:
            if sma100 and sma100 > price:
                res_targets.append(f"{sma100:.2f} (100 SMA)")
            if sma200 and sma200 > price:
                res_targets.append(f"{sma200:.2f} (200 SMA)")
        res_str = " ve ".join(res_targets) if res_targets else ""

        vol_note = f"ortalamanın {vol_pct_str}'si ({vol_x_str}) hacim patlamasıyla" if (vol_ratio and vol_ratio >= 1.5) else "hacim desteğiyle"
        trig_str = f"{cizgi_trig:.2f}" if cizgi_trig else "kritik direnç"
        inval_str = f"{cizgi_inval:.2f}" if cizgi_inval else ""

        if cizgi_dir == "yukari" or "Kama" in cizgi_pat or "Üçgen" in cizgi_pat or "Tobo" in cizgi_pat:
            title = f"{cizgi_bars} Günlük {cizgi_pat} Kırıldı"
            text = (
                f"{name}, {cizgi_bars} gündür devam eden {cizgi_pat} yapısını {trig_str} üzerinde "
                f"{vol_note} yukarı kırdı. Para akışı dip dönüşünü destekliyor; {trig_str} üzerinde kalıcılık "
                f"trend dönüşü için kritik eşiktir"
            )
            if res_str:
                text += f"; yukarıda ilk ana sınav {res_str} direnç hattıdır."
            else:
                text += "."
            
            note = f"Kırılım teyitli; {trig_str} yapının geçersizlik sınırı." + (f" (Geçersizlik: {inval_str})" if inval_str else "")
            return {
                "title": title,
                "text": text,
                "note": note,
                "color": CLR_GREEN,
                "archetype": "CIZGI_KIRILIM_BOGA"
            }
        elif cizgi_dir == "asagi":
            title = f"{cizgi_pat} Aşağı Kırıldı (Destek Kaybı)"
            text = (
                f"{name}, {cizgi_bars} günlük {cizgi_pat} alt sınırını {trig_str} altında kırdı. "
                f"Satış baskısı artarken formasyon düşüş yönlü tetiklendi; taban arayışı derinleşebilir."
            )
            note = f"Kırılan {trig_str} çizgisi artık direnç konumunda."
            return {
                "title": title,
                "text": text,
                "note": note,
                "color": CLR_RED,
                "archetype": "CIZGI_KIRILIM_AYI"
            }

    # ─────────────────────────────────────────────────────────────────────────
    # ÖNCELİK 2: ICT LİKİDİTE AVI / SFP / TEPE-DİP TUZAKLARI (XU100 VAKASI)
    # ─────────────────────────────────────────────────────────────────────────
    if sfp_bear:
        rsi_desc = (f"{rsi_streak} gündür süren RSI {rsi_val:.0f} aşırı alımı" if (rsi_val and rsi_streak >= 2)
                    else (f"RSI {rsi_val:.0f} aşırı alım bölgesi" if (rsi_val and rsi_val >= 70) else "kısa vadeli yorgunluk"))
        title = "Zirvede Boğa Tuzağı (Bearish SFP)"
        text = (
            f"{name} ana trendde yukarı rejimde olsa da, tepe seviyesinde alıcıları içeri çeken bir "
            f"Bearish SFP (Likidite Tuzağı) oluştu. Fiyatın yükselmesine karşın {rsi_desc} ve para akışındaki "
            f"kısa vadeli zayıflama, kurumsal aktörlerin tepe likiditesinde kâr hafiflettiğine işaret ediyor."
        )
        note = "Tepe bölgesinde likidite alındı; kısa vadeli dinamik destekler bu kurulumun sınavı."
        return {
            "title": title,
            "text": text,
            "note": note,
            "color": CLR_YELLOW,
            "archetype": "BEARISH_SFP_TRAP"
        }

    if sfp_bull:
        title = "Dipte Ayı Tuzağı (Bullish SFP)"
        text = (
            f"{name} dip seviyesinde satıcıları tuzağa düşüren bir Bullish SFP (Likidite Temizliği) yaptı. "
            f"Dip iğnesi sonrası gelen alımlar ve toparlanan para akışı, tahtanın alttan güçlü emildiğini gösteriyor."
        )
        note = "Dip temizliği tamamlandı; iğnenin dibi bu kurulumun geçersizlik seviyesi."
        return {
            "title": title,
            "text": text,
            "note": note,
            "color": CLR_GREEN,
            "archetype": "BULLISH_SFP_SPRING"
        }

    # ─────────────────────────────────────────────────────────────────────────
    # ÖNCELİK 3: WILDER RSI POZİTİF / NEGATİF UYUMSUZLUĞU (DİP / TEPE DÖNÜŞÜ)
    # ─────────────────────────────────────────────────────────────────────────
    if feat.get("f_rsi_div_pos") or terazi_res.get("karsi_oy", {}).get("ad") == "RSI pozitif uyumsuzluk":
        title = "Dipte RSI Pozitif Uyumsuzluğu"
        text = (
            f"{name} fiyat olarak baskı altında kalsa da dip seviyede net bir RSI Pozitif Uyumsuzluğu oluşturdu. "
            f"Fiyatın yeni dip yapamaması ve osilatörün yukarı ivmelenmesi, satış gücünün tükendiğini ve "
            f"akıllı paranın gizli birikim yaptığını gösteriyor."
        )
        note = "Fiyat yeni dip yapmazken osilatör yukarı ayrıştı — uyumsuzluk oluşmuş durumda."
        return {
            "title": title,
            "text": text,
            "note": note,
            "color": CLR_CYAN,
            "archetype": "RSI_POZ_DIV"
        }

    # ─────────────────────────────────────────────────────────────────────────
    # ÖNCELİK 4: OLGUN KURUMSAL TAŞIMA TRENDİ (TÜPRAŞ / THYAO / SECULAR TREND)
    # ─────────────────────────────────────────────────────────────────────────
    if sma50_days_up >= 30 and (sma50 is not None and price is not None and price >= sma50):
        # Secular trend içinde miyiz?
        ema21 = ma["ema21"]
        dist_ema21 = ((price / ema21) - 1.0) * 100 if ema21 else 0
        
        if rsi_val and rsi_val >= 80:
            # RSI 80 olmuş ama 40 gündür 50 SMA üstünde taşıma trendinde
            title = "Kurumsal Taşıma Trendi (Momentum Güçlü)"
            text = (
                f"{name}, {sma50_days_up} gündür 50 günlük ortalamanın üzerinde süren güçlü bir kurumsal taşıma trendinde. "
                f"RSI'ın {rsi_val:.0f} seviyesinde olması bir bozulma değil, trendin momentum gücüdür. "
                f"Ana omurga 50G ortalaması üzerinde kaya gibi sağlam kalmaya devam ediyor."
            )
            note = "Trend olgun; hareketli ortalamalar ana omurga olarak çalışmayı sürdürüyor."
            return {
                "title": title,
                "text": text,
                "note": note,
                "color": CLR_GREEN,
                "archetype": "SECULAR_MOMENTUM_RUN"
            }
        elif abs(dist_ema21) <= 2.0:
            # EMA 21 civarında dinleniyor (Healthy Pullback)
            title = "Trend İçi Sağlıklı Dinlenme (EMA 21 Testi)"
            text = (
                f"{name} aylardır süren ana yükseliş trendinde EMA 21 dinamik destek bölgesine doğru teknik bir sindirme yaşıyor. "
                f"Satışlarda panik veya kurumsal çıkış izi yok; hareketli ortalamaları yukarı çekmek için zamana yayılan sağlıklı bir soğuma süreci işliyor."
            )
            note = "Trend omurgası korunuyor; EMA 21 kısa vadeli denge çizgisi konumunda."
            return {
                "title": title,
                "text": text,
                "note": note,
                "color": CLR_GREEN,
                "archetype": "SECULAR_PULLBACK"
            }
        else:
            title = "Kurumsal Taşıma Trendi Korunuyor"
            text = (
                f"{name}, {sma50_days_up} gündür 50 günlük ortalamanın üzerinde ana yükseliş trendini sürdürüyor. "
                f"Para akışı ve fiyat yapısı kurumsal taşımayı teyit ediyor; ara düzeltmeler trendin genel yönünü bozmuyor."
            )
            note = f"Fiyat 50 günlük ortalamanın ({sma50:.2f}) üzerinde kalmayı sürdürüyor."
            return {
                "title": title,
                "text": text,
                "note": note,
                "color": CLR_GREEN,
                "archetype": "SECULAR_TREND"
            }

    # ─────────────────────────────────────────────────────────────────────────
    # ÖNCELİK 5: PARABOLİK COŞKU & AŞIRI UZAKLAŞMA (BLOW-OFF RISK)
    # ─────────────────────────────────────────────────────────────────────────
    if ma["sma50_dist_pct"] and ma["sma50_dist_pct"] >= 25.0 and rsi_val and rsi_val >= 82:
        title = "Parabolik Coşku (Aşırı Uzaklaşma)"
        text = (
            f"{name} 50 günlük ortalamasının %{ma['sma50_dist_pct']:.0f} üzerine çıkarak dikey bir coşku (klimaks) evresine girdi. "
            f"Momentum çok kuvvetli olsa da ortalamalardan bu derece istatistiksel sapma, kâr satışlarının da sert gelme riskini artırır."
        )
        note = "Trend güçlü, ancak ortalamalardan uzaklaşma arttı; geri çekilme payı daralmış durumda."
        return {
            "title": title,
            "text": text,
            "note": note,
            "color": CLR_YELLOW,
            "archetype": "BLOW_OFF_CLIMAX"
        }

    # ─────────────────────────────────────────────────────────────────────────
    # ÖNCELİK 6: VOLATİLİTE SIKIŞMASI / ENERJİ BİRİKİMİ (SQUEEZE)
    # ─────────────────────────────────────────────────────────────────────────
    if vol_ratio and vol_ratio <= 0.65 and (ma["ret5"] is not None and abs(ma["ret5"]) <= 1.8):
        title = "Volatilite Sıkışması (Bant Daralması)"
        text = (
            f"{name} daralan bir fiyat bandında enerji biriktiriyor; işlem hacmi 20 günlük ortalamanın %{int(vol_ratio*100)}'sine kadar kurudu. "
            f"Bu tip düşük hacimli yatay konsolidasyonlar genellikle sert ve yönlü bir kırılımla sonuçlanır."
        )
        note = "Bantlar daraldı; fiyat sıkışma aralığının içinde, yön henüz belirsiz."
        return {
            "title": title,
            "text": text,
            "note": note,
            "color": CLR_CYAN,
            "archetype": "VOLATILITY_SQUEEZE"
        }

    # ─────────────────────────────────────────────────────────────────────────
    # ÖNCELİK 7: 200 GÜNLÜK ORTALAMA SINAVI (BOĞA / AYI REJİM EŞİĞİ)
    # ─────────────────────────────────────────────────────────────────────────
    if sma200 and price:
        dist_200 = ((price / sma200) - 1.0) * 100
        if abs(dist_200) <= 2.5:
            if price >= sma200:
                title = "200 SMA Üzerinde Rejim Sınavı"
                text = (
                    f"{name} uzun vadeli boğa/ayı rejim çizgisi olan 200 SMA ({sma200:.2f}) üzerinde tutunma mücadelesi veriyor. "
                    f"Bu seviyenin üzerinde kalıcılık, hissenin uzun vadeli pozitif döngüye geçişini teyit edecektir."
                )
                note = f"Fiyat 200 günlük ortalamanın ({sma200:.2f}) üzerinde — ana rejim sınırının üst tarafı."
                return {
                    "title": title, "text": text, "note": note,
                    "color": CLR_GREEN if (short_flow == "pozitif") else CLR_YELLOW,
                    "archetype": "SMA200_TEST_BULL"
                }
            else:
                title = "200 SMA Direnç Eşiğinde"
                text = (
                    f"{name} uzun vadeli ana direnci olan 200 SMA ({sma200:.2f}) eşiğine dayandı. "
                    f"Düşüş trendinin kalıcı olarak sonlanması için 200 günlük bariyerin hacim patlamasıyla aşılması gerekiyor."
                )
                note = f"Fiyat 200 günlük ortalamanın ({sma200:.2f}) altında — ana rejim sınırının alt tarafı."
                return {
                    "title": title, "text": text, "note": note,
                    "color": CLR_YELLOW,
                    "archetype": "SMA200_TEST_BEAR"
                }

    # ─────────────────────────────────────────────────────────────────────────
    # ÖNCELİK 8: YAPISAL DÜŞÜŞ TRENDİ / SATIŞ BASKISI (MARKDOWN / WATERFALL)
    # ─────────────────────────────────────────────────────────────────────────
    if sma50_days_down >= 15 or (sma50 and price and price < sma50 and sma200 and price < sma200):
        if short_flow == "pozitif" or (ma["ret5"] and ma["ret5"] > 2.0):
            title = "Düşüş Trendinde Tepki Yükselişi"
            text = (
                f"{name} ana trendde 50 ve 200 günlük ortalamaların altında satıcılı rejimde. "
                f"Son seanslardaki toparlanma henüz yapısal bir trend dönüşü değil, ana düşüş trendi içi tepki niteliğindedir."
            )
            _s50_txt = f"{sma50:.2f}" if sma50 else "—"
            note = (f"50 günlük ortalama ({_s50_txt}) aşılmadıkça yükselişler "
                    f"düşüş trendi içi tepki konumunda.")
            return {
                "title": title, "text": text, "note": note,
                "color": CLR_YELLOW,
                "archetype": "BEAR_MARKET_RALLY"
            }
        else:
            title = "Fiyat Ana Ortalamaların Altında"
            text = (
                f"{name} tüm ana hareketli ortalamalarının altında negatif rejimde kalmaya devam ediyor. "
                f"Para akışında henüz kurumsal bir emilim görülmüyor; her yükseliş denemesi satıcılar tarafından karşılanıyor."
            )
            note = "Düşüş trendi aktif; dip oluşumu ve para girişi henüz teyitli değil."
            return {
                "title": title, "text": text, "note": note,
                "color": CLR_RED,
                "archetype": "MARKDOWN_BEAR"
            }

    # ─────────────────────────────────────────────────────────────────────────
    # ÖNCELİK 9: TAZE YÜKSELİŞ BAŞLANGICI / KIRILIM (BREAKOUT IGNITION)
    # ─────────────────────────────────────────────────────────────────────────
    if sma50_days_up in range(1, 15) and (short_flow == "pozitif" or (vol_ratio and vol_ratio >= 1.2)):
        title = "50 Günlük Ortalama Yeni Geçildi"
        text = (
            f"{name} 50 günlük ortalamasının üzerine yeni çıktı ve para akışı alıcı tarafında güçleniyor. "
            f"Kısa vadeli momentum pozitif; hareketin devamı için 50G ortalama üzerinde kapanış serisinin sürmesi beklenir."
        )
        _s50_txt2 = f"{sma50:.2f}" if sma50 else "—"
        note = f"50 günlük destek ({_s50_txt2}) üzerinde kalındıkça yukarı yapı korunuyor."
        return {
            "title": title, "text": text, "note": note,
            "color": CLR_GREEN,
            "archetype": "FRESH_IGNITION"
        }

    # ─────────────────────────────────────────────────────────────────────────
    # FALLBACK / GENEL AKILLI SENTEZ
    # ─────────────────────────────────────────────────────────────────────────
    # Hiçbir spesifik uç tetiklenmediyse akış ve ortalama dengesini profesyonelce özetle
    if price and sma50 and price >= sma50:
        title = "Kısa ve Ana Trend Dengeli Yukarı"
        text = (
            f"{name} 50 günlük ortalamasının üzerinde pozitif trend yapısını koruyor. "
            f"Para akışı ve hacim desteği dengeli; ana yön yukarıyı işaret ediyor."
        )
        note = "Fiyat ana ortalamaların üzerinde; kısa ve orta vade yapısı hizalı."
        return {
            "title": title, "text": text, "note": note,
            "color": CLR_GREEN,
            "archetype": "BALANCED_BULL"
        }
    else:
        title = "Yön Arayışı & Denge Fazı"
        text = (
            f"{name} yatay bantta denge arayışını sürdürüyor. "
            f"Belirgin bir kurumsal para girişi veya kırılım henüz oluşmadı; net yön için hacimli bir kopuş beklenmeli."
        )
        note = "Piyasa bekle-gör modunda; yön için destek/direnç sınırlarından birinin kırılması gerekiyor."
        return {
            "title": title, "text": text, "note": note,
            "color": CLR_NEUTRAL,
            "archetype": "NEUTRAL_RANGE"
        }


# =============================================================================
# ARKETİP KARNESİ — 25 Ağu 2026, `_pusula_backtest.py` ölçümü
# =============================================================================
# 3.985 sinyal · scan_signals × signal_returns · T+5/T+10/T+20 · look-ahead yok
# Motor sezgiyle yazılmıştı; ölçüm 5 dalın TERS yönde konuştuğunu gösterdi
# (ekranda iyi haber, getiride ortalamanın altı — ve tersi).
#
# ⚠ TEK REJİM: ölçüm 2026'nın tek piyasa dönemini kapsıyor, baseline T+20 = -0,95
# (düşen tape). Rejim değişince `python _pusula_backtest.py` YENİDEN KOŞ ve bu
# sözlüğü güncelle. Kural: AJAN_KURALLARI §2.1 / §2.3.
#
# Değer: (T+20 ortalamaya fark [puan], örneklem N, T+20 isabet %)
#        None  → ölçüldü ama ayrım çıkmadı (işaret değişiyor / fark < 0,35)
ARKETIP_KARNE = {
    "SECULAR_MOMENTUM_RUN": (+10.38, 60,  67),
    "SECULAR_PULLBACK":     (+5.58,  124, 52),
    "SMA200_TEST_BEAR":     (+2.34,  222, 36),
    "MARKDOWN_BEAR":        (+1.28,  611, 43),
    "CIZGI_KIRILIM_BOGA":   (+1.12,  39,  44),
    "NEUTRAL_RANGE":        None,
    "BALANCED_BULL":        None,
    "SECULAR_TREND":        None,
    "VOLATILITY_SQUEEZE":   (-1.80,  267, 32),
    "SMA200_TEST_BULL":     (-1.95,  162, 32),
    "FRESH_IGNITION":       (-3.20,  539, 27),
    "BEAR_MARKET_RALLY":    (-3.51,  273, 26),
}

# Ölçüm penceresinde 30'dan az örnek gördüğü veya hiç tetiklenmediği için
# hüküm verilemeyen dallar (BLOW_OFF_CLIMAX N=6 · SFP dalları · CIZGI_KIRILIM_AYI).
# RSI_POZ_DIV ayrıca ölçülemez: `f_rsi_div_pos` scan_signals'ta yok.


def karne_notu(archetype: str) -> str:
    """Arketipin ölçülmüş karnesini tek cümlede verir. Yön ≠ Eylem: yorum yok,
    yalnız rakam. Ölçülmemişse dürüstçe onu söyler."""
    if archetype not in ARKETIP_KARNE:
        return "📊 Karne: bu kurulum henüz ölçülmedi (örneklem yetersiz)."
    k = ARKETIP_KARNE[archetype]
    if k is None:
        return "📊 Karne: ölçüldü, ileri getiride ayrım çıkmadı — yön göstergesi olarak zayıf."
    fark, n, isabet = k
    yon = "ÜSTÜNDE" if fark > 0 else "ALTINDA"
    return (f"📊 Karne ({n} örnek, 2026): 20 gün sonrası ortalamanın "
            f"{abs(fark):.1f} puan {yon}, isabet %{isabet}.")


def synthesize_market_compass(*args, **kwargs) -> Dict[str, Any]:
    """Piyasa Pusulası — anlatı + ÖLÇÜLMÜŞ KARNE.

    `_synthesize_raw` durumu tespit eder; bu sarmalayıcı her arketipe geçmiş
    karnesini ekler. Böylece ekranda "ne oluyor" ile "bu kurulum geçmişte ne
    yaptı" birlikte görünür — kullanıcı yönü kendi tartar (AJAN_KURALLARI §3.1).
    """
    out = _synthesize_raw(*args, **kwargs)
    if not isinstance(out, dict):
        return out
    ark = out.get("archetype") or ""
    out["karne"] = karne_notu(ark)
    out["karne_fark"] = (ARKETIP_KARNE.get(ark) or (None,))[0]
    _note = out.get("note") or ""
    out["note"] = f"{_note} {out['karne']}".strip()
    return out
