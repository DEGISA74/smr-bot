"""kirilim_olay.py — KIRILIM OLAY DEFTERİ (saf hesap, render YOK)

Ne işe yarar
------------
Sistemimizde sinyal doğduğu anın fotoğrafı var, SONRASI yok. Bu modül kırılımı
bir OLAY gibi takip eder: doğar → yaşar → ya güçte devam eder ya çöker ya ömrü
biter. Tekli panel + Master Scan + bülten aynı hükmü okusun diye tek kaynak.

Kanıt (ölçüldü — uydurulmadı)
-----------------------------
`kirilim_takip_backtest.py`, 637 BIST hissesi, 1832 kırılım olayı, 13 ay:

  Tespit barından SONRAKİ getiri (geleceğe bakma yok):
    · GÜÇTE DEVAM (10 bar geri dönmedi/çökmedi)  T+10  +2.07%  isabet %54.0   ← EN İYİ
    · RETEST onaylı                              T+10  +0.40%  isabet %45.4
    · ÇÖKTÜ                                      T+10  +0.04%  isabet %44.1
    · taban (tüm olaylar)                        T+10  +0.67%  isabet %46.9

  → RETEST ONAYI PRİM YAPMIYOR. "Geri gelip destek testi yapsın" klasik kuralı
    bizim veride tabanın altında kalıyor. Bu, [[project_kurulum_felsefesi_guc_devam]]
    bulgusuyla aynı yöne bakıyor: güçten devam > geri çekilme bekleme.
  → Değerli olan iki uç: GÜÇTE DEVAM (pozitif) ve ÇÖKÜŞ (kaçınılacak).

  ⚠ Tek evren (BIST) + 13 ay. Ayı piyasasında yeniden koşulmalı.
  ⚠ ATR cetveli AYRICA test edildi (`kirilim_cetvel_backtest.py`) ve mevcut
    sabit-yüzde cetvelimizi GEÇEMEDİ → cetvel değiştirilmedi, bilerek.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# --- ölçümde kullanılan parametreler (backtest ile aynı — değiştirirsen yeniden ölç)
YAPI_LEN = 20          # yapı seviyesi: önceki 20 barın zirvesi
ATR_LEN = 14
HACIM_LEN = 20
HACIM_ESIK = 1.5       # kırılım barında min hacim oranı
SABIT_ESIK = 1.01      # kapanış > seviye × 1.01  (ölçümde ATR'yi yendi)
OMUR_BAR = 10          # olay ömrü
RETEST_ATR = 0.20
COKUS_ATR = 0.25       # geçersizlik çizgisi = seviye − 0.25 × ATR
COKUS_KAPANIS = 2      # arka arkaya kaç kapanış

DURUMLAR = ("yok", "taze", "guclu_devam", "geri_test", "cokus", "omru_doldu")


def _atr(h, l, c, n=ATR_LEN):
    pc = np.roll(c, 1)
    pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).rolling(n).mean().to_numpy()


def _bos(neden=""):
    return dict(durum="yok", seviye=None, gecersizlik=None, gun=0,
                mesafe_atr=None, hacim_orani=None, etiket="", neden=neden)


def kirilim_durumu(df: pd.DataFrame) -> dict:
    """Son bar itibarıyla açık kırılım olayının durumunu döndürür.

    Dönüş dict:
      durum        : yok | taze | guclu_devam | geri_test | cokus | omru_doldu
      seviye       : kırılan yapı seviyesi (fiyat)
      gecersizlik  : bu seviyenin altına 2 kapanış = olay biter
      gun          : kırılımdan bu yana kaç bar geçti
      mesafe_atr   : kırılım barında seviyeden uzaklık (ATR cinsi)
      hacim_orani  : kırılım barındaki hacim / 20g ortalama
      etiket       : panele basılabilir kısa Türkçe hüküm
    """
    try:
        if df is None or len(df) < YAPI_LEN + ATR_LEN + 5:
            return _bos("yetersiz bar")
        for k in ("High", "Low", "Close", "Volume"):
            if k not in df.columns:
                return _bos("kolon eksik")

        c = df["Close"].to_numpy(float)
        h = df["High"].to_numpy(float)
        l = df["Low"].to_numpy(float)
        v = df["Volume"].to_numpy(float)
        n = len(c)

        seviye = pd.Series(h).rolling(YAPI_LEN).max().shift(1).to_numpy()
        atr = _atr(h, l, c)
        vma = pd.Series(v).rolling(HACIM_LEN).mean().shift(1).to_numpy()

        # --- en son kırılım barını geriye doğru ara (ömür penceresi içinde)
        kir_i = None
        for i in range(n - 1, max(YAPI_LEN + ATR_LEN, n - 1 - OMUR_BAR) - 1, -1):
            s, a, vm = seviye[i], atr[i], vma[i]
            if not (np.isfinite(s) and np.isfinite(a) and a > 0
                    and np.isfinite(vm) and vm > 0):
                continue
            if (v[i] / vm) <= HACIM_ESIK:
                continue
            if np.isfinite(seviye[i - 1]) and c[i - 1] > seviye[i - 1]:
                continue                       # yeni kırılım değil, trend devamı
            if c[i] > s * SABIT_ESIK:
                kir_i = i
                break

        if kir_i is None:
            return _bos("ömür penceresinde kırılım yok")

        s = float(seviye[kir_i])
        a = float(atr[kir_i])
        gun = n - 1 - kir_i
        gecersizlik = s - a * COKUS_ATR
        ortak = dict(
            seviye=round(s, 4),
            gecersizlik=round(gecersizlik, 4),
            gun=int(gun),
            mesafe_atr=round(float((c[kir_i] - s) / a), 2),
            hacim_orani=round(float(v[kir_i] / vma[kir_i]), 2),
            neden="",
        )

        if gun == 0:
            return dict(durum="taze", etiket="Bugün kırdı", **ortak)

        # --- kırılımdan sonraki barları sırayla oku: hangisi ÖNCE oldu?
        ardisik = 0
        for j in range(kir_i + 1, n):
            if c[j] < gecersizlik:
                ardisik += 1
            else:
                ardisik = 0
            if ardisik >= COKUS_KAPANIS:
                return dict(durum="cokus",
                            etiket=f"Kırılım çöktü ({j - kir_i}. günde)", **ortak)
            if l[j] <= s + a * RETEST_ATR and c[j] >= s:
                return dict(durum="geri_test",
                            etiket=f"Seviyeye geri döndü, tutuyor ({gun} gün)", **ortak)

        if gun >= OMUR_BAR:
            return dict(durum="omru_doldu",
                        etiket=f"{gun} gün oldu, olay kapandı", **ortak)

        return dict(durum="guclu_devam",
                    etiket=f"Geri dönmedi, {gun} gündür üstünde", **ortak)

    except Exception as e:
        return _bos(f"hata: {type(e).__name__}")


def panel_satiri(d: dict) -> str:
    """Panele basılacak tek satır. Boşsa '' döner (satır hiç çizilmez)."""
    if not d or d.get("durum") in (None, "yok"):
        return ""
    ikon = {"taze": "🔵", "guclu_devam": "🟢", "geri_test": "🟡",
            "cokus": "🔴", "omru_doldu": "⚪"}.get(d["durum"], "")
    if d["durum"] == "cokus":
        return f"{ikon} {d['etiket']} — {d['seviye']:.2f} kaybedildi"
    if d["durum"] == "omru_doldu":
        return f"{ikon} {d['etiket']}"
    return (f"{ikon} {d['etiket']} — geçersizlik {d['gecersizlik']:.2f} "
            f"(altına 2 kapanış olursa biter)")
