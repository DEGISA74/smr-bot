#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seans_profili.py — GÜN-İÇİ SEANS İLERLEMESİ (26 Ağu 2026)

NEDEN VAR
---------
Panel bitmiş bir günün verisiyle konuşuyordu ama elindeki gün bitmemişti.
26 Ağu 11:53'te XU100 cirosu 73,5 milyar TL'ydi; 20 günlük ortalama 187 milyar.
Sistem bunu 0,39x görüp "hacim düşük" dedi — oysa seansın daha 2 saati geçmişti.

Sistemdeki TEK hacim düzeltmesi arefe (yarım gün) katsayısıydı (0,3125).
"Seansın kaçta kaçı geçti" diye bir kavram YOKTU.

NEDEN DOĞRUSAL DEĞİL
--------------------
"Geçen süre / toplam seans" formülü BIST'te sabahları yanlış. 200 hisse,
5.464 hisse-gün ölçümü (`olcum_yap()`):

    saat          gerçek pay    doğrusal varsayım    fark
    10:00-11:00      %18,2            %12,5        +5,7 puan
    11:00-12:00      %13,0            %12,5         (küm. +6,1)
    13:00 sonrası      ~%12            %12,5        ±1,2 puan altı

Yani doğrusal katsayı sabah hacmi %45'e varan oranda ŞİŞİRİR — her sabah 11'de
sahte "hacim patlaması" alarmı üretirdi. Bu modül ölçülmüş profili kullanır.

KULLANIM SINIRI — ÖNEMLİ
------------------------
Buradan çıkan sayı bir TAHMİNDİR. Yalnızca GÖSTERİM ve rvol için kullanılır.
Karnesi tam-bar üstünde kalibre edilmiş eşiklere (terazi_core.sok_degerlendir'in
"hacim >= 1,5x" ayı oyu gibi) BESLENMEZ — yarım-bar tahminine ölçülmüş eşik
uygulamak karneyi geçersiz kılar ama rozet "ölçülmüş" demeye devam eder.
Bu, kapatılan 4S rozetiyle aynı tuzaktır. → memory/feedback_rozet_olcumden_gecer

scan_signals'a da sızmaz: Master Scan 19:55'te (watchdog 22:15) koşar, o saatte
bar TAMDIR ve katsayı 1,0'dır. Seans içi elle koşulan taramayı `depo_tazelik.
yazim_izni()` zaten bloke ediyor (26 Ağu 12:47'de canlı doğrulandı).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFIL_DOSYA = ROOT / "health" / "seans_profili.json"
TR = timezone(timedelta(hours=3))

# Seansın en az bu kadarı geçmeden hacim hükmü verilmez (payda çok küçük →
# rvol uçar). Açılışın ilk ~25 dakikası bu kapıya takılır.
MIN_ILERLEME = 0.10

# ── ÖLÇÜLMÜŞ PROFİL (26 Ağu 2026 · 267 hisse · 14.429 hisse-gün) ────────────
# Anahtar = saatin BAŞLANGICI (10 → 10:00-11:00), değer = günlük hacim içindeki payı.
#
# BAR DAMGASI TUZAĞI: saatlik parquet'te bar "H:30" diye damgalanır ama taşıdığı
# aralık [H:00, H+1:00)'dir. Ayrıca her günün başında 09:30 damgalı bir açılış-öncesi
# kütüğü var (payı %0,33) — 10:00 kovasına eklendi. 8 kova × 1 saat = 480 dakika =
# tam seans, toplam %100. Bunu kaçırıp "saat >= 10" diye süzersen açılış saati düşer.
#
# ZAMAN DİLİMİ TUZAĞI: depodaki saatlik dosyaların yarısı zaman-dilimsiz ve
# ZATEN İstanbul saatinde. UTC sanıp çevirirsen 3 saat kayar ve profil bozulur.
# Ölçüm yalnız dilim bilgisi olan dosyalardan yapılır (belirsizlik alınmaz).
#
# Yeniden ölçmek için: python seans_profili.py --olc
VARSAYILAN_PROFIL = {
    10: 0.1846,   # açılış saati — günün en yoğunu (09:30 kütüğü dahil)
    11: 0.1256,
    12: 0.1028,
    13: 0.1025,
    14: 0.1116,
    15: 0.1195,
    16: 0.1362,
    17: 0.1172,   # kapanış saati
}

_profil_memo: dict | None = None

# Yüzdeye gelen iyelik eki okunuşa göre değişir: %42'si (kırk ikisi), %38'i
# (otuz sekizi), %30'u (otuzu). Son basamak 0 ise onluğun kendisi belirler.
_EK_BIRLER = {1: "i", 2: "si", 3: "ü", 4: "ü", 5: "i",
              6: "sı", 7: "si", 8: "i", 9: "u"}
_EK_ONLAR = {1: "u", 2: "si", 3: "u", 4: "ı", 5: "si",
             6: "ı", 7: "si", 8: "i", 9: "ı", 10: "ü"}


def yuzde_eki(n: int) -> str:
    """`n` yüzdesi için doğru iyelik eki ('%42' → 'si')."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "i"
    if n % 10:
        return _EK_BIRLER[n % 10]
    if n == 100:
        return _EK_ONLAR[10]
    return _EK_ONLAR.get((n // 10) % 10, "u") if n >= 10 else "ı"


# ------------------------------------------------------------------ profil
def profil() -> dict:
    """Saat → hacim payı. Diskte ölçüm varsa onu, yoksa varsayılanı kullanır."""
    global _profil_memo
    if _profil_memo is not None:
        return _profil_memo
    p = dict(VARSAYILAN_PROFIL)
    try:
        if PROFIL_DOSYA.exists():
            ham = json.loads(PROFIL_DOSYA.read_text(encoding="utf-8"))
            saatler = {int(k): float(v) for k, v in (ham.get("saat_payi") or {}).items()}
            top = sum(saatler.values())
            if saatler and 0.95 <= top <= 1.05:
                p = {k: v / top for k, v in saatler.items()}
    except Exception:
        pass
    _profil_memo = p
    return p


def _kumulatif(saat: int, dakika: int, prof: dict) -> float:
    """Seans başından `saat:dakika`ya kadar beklenen hacim payı (0..1)."""
    saatler = sorted(prof)
    kum = 0.0
    for s in saatler:
        if saat > s:
            kum += prof[s]
        elif saat == s:
            kum += prof[s] * (dakika / 60.0)
            break
        else:
            break
    return max(0.0, min(1.0, kum))


# ------------------------------------------------- seansın neresindeyiz
def seans_ilerleme(now: datetime | None = None):
    """Şu ana kadar günün hacminin ne kadarının oluşmuş olması BEKLENİR?

    Döner: (pay, durum)
      pay    : 0..1 arası beklenen hacim oranı · seans kapalıysa/bittiyse 1.0
      durum  : 'kapali' | 'acilmadi' | 'devam' | 'bitti'
    """
    now = now or datetime.now(TR)
    try:
        from bist_calendar import get_session_hours, is_half_day
        saatler = get_session_hours(now.date())
    except Exception:
        saatler, is_half_day = ("10:00", "18:00"), (lambda _d=None: False)

    if not saatler:
        return 1.0, "kapali"                       # tatil/hafta sonu

    ac_s, ac_d = (int(x) for x in saatler[0].split(":"))
    kp_s, kp_d = (int(x) for x in saatler[1].split(":"))
    acilis = now.replace(hour=ac_s, minute=ac_d, second=0, microsecond=0)
    kapanis = now.replace(hour=kp_s, minute=kp_d, second=0, microsecond=0)

    if now < acilis:
        return 0.0, "acilmadi"
    if now >= kapanis:
        return 1.0, "bitti"

    try:
        yarim = bool(is_half_day(now.date()))
    except Exception:
        yarim = False

    if yarim:
        # Arefe profili ÖLÇÜLMEDİ (yılda birkaç gün). Dürüst davranıp arefe
        # penceresi içinde doğrusal ilerleme kullanıyoruz.
        toplam = (kapanis - acilis).total_seconds()
        return max(0.0, min(1.0, (now - acilis).total_seconds() / toplam)), "devam"

    return _kumulatif(now.hour, now.minute, profil()), "devam"


# ------------------------------------------------------- kısmi bar hükmü
def kismi_bar_durumu(bar_tarihi, now: datetime | None = None,
                     pay_override: float | None = None) -> dict:
    """Elimizdeki son bar BUGÜNÜN YARIM barı mı? Hacim nasıl düzeltilmeli?

    bar_tarihi: son mumun tarihi (date / datetime / Timestamp).

    Döner sözlük:
      kismi     : bool  — son bar bugünün ve seans sürüyor
      katsayi   : float — 20g ortalaması bununla ÇARPILIR (tam barda 1.0)
      pay       : float — seansın geçen oranı (0..1)
      yeterli   : bool  — MIN_ILERLEME geçildi mi (False → hacim hükmü verme)
      rozet     : str   — ekranda gösterilecek kısa etiket ('' = gösterme)
      aciklama  : str   — tooltip / uzun not
    """
    bos = {"kismi": False, "katsayi": 1.0, "pay": 1.0,
           "yeterli": True, "rozet": "", "aciklama": ""}
    now = now or datetime.now(TR)

    try:
        b = getattr(bar_tarihi, "date", lambda: bar_tarihi)()
    except Exception:
        b = bar_tarihi
    if b != now.date():
        return bos                                  # son bar bugünün değil → tam

    pay, durum = seans_ilerleme(now)
    if durum in ("kapali", "bitti"):
        return bos                                  # gün tamamlandı → tam bar
    if durum == "acilmadi":
        return bos

    # `data_layer.apply_volume_projection` hacmi zaten tam güne projekte etti ve
    # kullandığı oranı df.attrs'a damgaladı. Rozet O oranı söylemeli — yoksa ekran
    # "%45" derken hesap %46,6 varsaymış olur ve iki sayı tutmaz.
    if pay_override is not None:
        try:
            pay = max(0.0, min(1.0, float(pay_override)))
        except (TypeError, ValueError):
            pass
        if pay >= 1.0:
            return bos                              # projeksiyon yok → tam sayılır

    yeterli = pay >= MIN_ILERLEME
    yuzde = int(round(pay * 100))
    ek = yuzde_eki(yuzde)
    return {
        "kismi": True,
        "katsayi": max(pay, MIN_ILERLEME),          # sıfıra bölmeyi engeller
        "pay": pay,
        "yeterli": yeterli,
        "rozet": f"seansın %{yuzde}'{ek}",
        "aciklama": (
            f"Bugünün mumu HENÜZ KAPANMADI — seansın yaklaşık %{yuzde}'{ek} geçti. "
            "Hacim karşılaştırması bu orana göre düzeltildi (ölçülmüş BIST "
            "gün-içi hacim profili). Kapanışta değişebilir."
            if yeterli else
            f"Seans yeni başladı (%{yuzde}) — hacim hükmü için henüz çok erken."
        ),
    }


def normal_gun_payi(saat: int, dakika: int = 0) -> float:
    """NORMAL BIST gününde `saat:dakika`ya kadar beklenen hacim payı (0..1).

    Saf profil sorgusu — takvim sormaz, arefe/tatil bilmez. Çağıran taraf o
    kontrolleri kendi yapar (`data_layer.apply_volume_projection` yapıyor).
    10:00 öncesi 0.0, 18:00 sonrası 1.0.
    """
    try:
        s, d = int(saat), int(dakika)
    except (TypeError, ValueError):
        return 1.0
    if s < 10:
        return 0.0
    if s >= 18:
        return 1.0
    return _kumulatif(s, d, profil())


def gun_katsayisi(bar_tarihi, now: datetime | None = None) -> float:
    """20g hacim ortalamasının ÇARPILACAĞI tek katsayı — arefe + gün-içi BİRLİKTE.

    ÇİFTE DÜZELTME TUZAĞI: `bist_calendar.get_rvol_day_factor` arefe gününü
    zaten 0,3125 ile düzeltiyor. Gün-içi katsayı ayrı uygulanırsa arefe gününde
    iki kez düzeltme olur. Bu yüzden ikisi TEK yerde birleşir; çağıran taraf
    artık `get_rvol_day_factor`'ı ayrıca uygulamamalı.

      tam gün (geçmiş bar)      → 1.0   (arefe ise 0.3125)
      bugün, seans sürüyor      → geçen pay        (arefe ise × 0.3125)
      bugün, seans bitti/kapalı → 1.0   (arefe ise 0.3125)
    """
    now = now or datetime.now(TR)
    try:
        b = getattr(bar_tarihi, "date", lambda: bar_tarihi)()
    except Exception:
        b = bar_tarihi

    try:
        from bist_calendar import get_rvol_day_factor
        arefe_f = float(get_rvol_day_factor(b))
    except Exception:
        arefe_f = 1.0

    if b != now.date():
        return arefe_f                              # geçmiş bar → sadece takvim
    pay, durum = seans_ilerleme(now)
    if durum in ("kapali", "bitti", "acilmadi"):
        return arefe_f
    return arefe_f * max(pay, MIN_ILERLEME)


def rvol_paydasi(ortalama_hacim: float, bar_tarihi, now: datetime | None = None):
    """Kısmi barda 20g ortalamasını seansın geçen oranına indirger.

    Döner: (payda, durum_sozlugu). payda <= 0 ise hesap yapılmamalı.
    """
    d = kismi_bar_durumu(bar_tarihi, now)
    try:
        ort = float(ortalama_hacim)
    except (TypeError, ValueError):
        return 0.0, d
    return ort * gun_katsayisi(bar_tarihi, now), d


# ------------------------------------------------------------------ ölçüm
def olcum_yap(ornek_hisse: int = 200, gunluk_bar: int = 400) -> dict:
    """Saatlik depodan BIST gün-içi hacim profilini YENİDEN ölçer.

    Yalnız .IS hisseleri, 10:00-18:00 İstanbul, tam günler (>=7 bar).
    Sonucu health/seans_profili.json'a yazar.
    """
    import glob
    import warnings

    import numpy as np
    import pandas as pd
    warnings.filterwarnings("ignore")

    depo = ROOT / "veriler_saatlik"
    fs = [f for f in glob.glob(str(depo / "*.IS_1h.parquet"))
          if not os.path.basename(f).startswith(("XU", "XB"))]
    fs = fs[:ornek_hisse] if ornek_hisse else fs

    rows: dict[int, list] = {}
    ngun = 0
    kullanilan = 0
    for f in fs:
        try:
            h = pd.read_parquet(f)
            # Zaman dilimi belirsizse ALMA: dilimsiz dosyalar zaten İstanbul
            # saatinde, ama bunu dosyadan kanıtlayamayız — ölçümü kirletmesin.
            if h.index.tz is None:
                continue
            if "Volume" not in h or float(h["Volume"].sum()) == 0:
                continue
            h = h.copy()
            h.index = h.index.tz_convert("Europe/Istanbul")
            h = h.tail(gunluk_bar)
            h = h[(h.index.hour >= 9) & (h.index.hour <= 17)]
            h["gun"] = h.index.date
            h["saat"] = h.index.hour
            cnt = h.groupby("gun")["Volume"].transform("size")
            tot = h.groupby("gun")["Volume"].transform("sum")
            # Yalnız 9 barlı TAM günler (09:30 kütüğü + 8 saat kovası).
            # Yarım günler profili çarpıtır; canlı gün zaten eksiktir.
            h = h[(tot > 0) & (cnt == 9)]
            if h.empty:
                continue
            kullanilan += 1
            ngun += h["gun"].nunique()
            h["pay"] = h["Volume"] / tot
            for s, g in h.groupby("saat"):
                rows.setdefault(int(s), []).extend(g["pay"].tolist())
        except Exception:
            pass

    if not rows:
        return {"hata": "saatlik depodan olcum cikmadi"}

    ham = {s: float(np.mean(v)) for s, v in sorted(rows.items())}
    # Damga → gerçek aralık: bar "H:30" aslında [H:00, H+1:00) saatini taşır.
    # 09:30 damgası açılış-öncesi kütüğü → 10:00 kovasına eklenir.
    saat_payi: dict[int, float] = {}
    for s, v in ham.items():
        hedef = 10 if s == 9 else int(s)
        saat_payi[hedef] = saat_payi.get(hedef, 0.0) + v
    top = sum(saat_payi.values())
    saat_payi = {s: v / top for s, v in sorted(saat_payi.items())}

    out = {
        "olcum_tarihi": datetime.now(TR).strftime("%Y-%m-%d %H:%M"),
        "hisse": kullanilan,
        "hisse_gun": int(ngun),
        "saat_payi": {str(k): round(v, 5) for k, v in saat_payi.items()},
    }
    try:
        PROFIL_DOSYA.parent.mkdir(parents=True, exist_ok=True)
        PROFIL_DOSYA.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    except Exception:
        pass
    global _profil_memo
    _profil_memo = None
    return out


# -------------------------------------------------------------------- CLI
def main() -> int:
    import sys
    # Windows konsolu cp1254 — cikti ASCII kalir.
    if "--olc" in sys.argv[1:]:
        r = olcum_yap()
        if "hata" in r:
            print("HATA:", r["hata"])
            return 1
        print("olcum: %d hisse, %d hisse-gun -> %s"
              % (r["hisse"], r["hisse_gun"], PROFIL_DOSYA))
        kum = 0.0
        for k in sorted(r["saat_payi"], key=int):
            v = float(r["saat_payi"][k])
            kum += v
            print("  %s:00-%s:00  pay %%%4.1f  kumulatif %%%5.1f"
                  % (k, int(k) + 1, v * 100, kum * 100))
        return 0

    now = datetime.now(TR)
    pay, durum = seans_ilerleme(now)
    print("su an (TR): %s" % now.strftime("%Y-%m-%d %H:%M"))
    print("seans durumu: %s | beklenen hacim payi: %%%.1f" % (durum, pay * 100))
    d = kismi_bar_durumu(now.date(), now)
    print("son bar bugunse -> kismi=%s katsayi=%.3f yeterli=%s rozet=%r"
          % (d["kismi"], d["katsayi"], d["yeterli"], d["rozet"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
