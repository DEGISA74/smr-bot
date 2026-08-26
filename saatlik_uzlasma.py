# -*- coding: utf-8 -*-
"""
saatlik_uzlasma.py — TAM GÜN UZLAŞMA KAPISI (19 Ağu 2026)

NEDEN VAR:
Saatlik ve günlük depo aynı hisse için farklı fiyat söyleyebiliyor. Ölçüldü
(555 hisse × son 20 TAM seans): kütlenin medyan farkı %0,22, hisselerin %99'u
%1,3'ün altında. Ama dört hisse bambaşka bir yerde duruyor — KTLEV %235,
AKFIS %83, SEKFK %81, OZKGY %64. Bunlar "biraz sapmış" değil, saatlik geçmişi
bedelsiz/bölünme sonrası yeniden bazlanmamış hisseler: günlük depo düzeltilmiş,
saatlik düzeltilmemiş. Böyle bir hisseyle kurulan her saatlik hesap zehirli.

EŞİK NASIL SEÇİLDİ (gözle DEĞİL):
Sapmalar sıralandığında aralarındaki en büyük KAT farkı aranır. Ölçümde bu
uçurum 65 kat çıktı (%64,29 → %0,98). Eşik o uçurumun ortasına konur. Uçurum
yoksa (kimse kopmamışsa) KİMSE karantinaya alınmaz — kural kendi kendini
kalibre eder, yeni bir bölünme olduğunda o hisse uçurumun üstünde belirir.
Mutlak yüzde ezberlenmez. [[feedback-extrapolation-yasak]]

TEK GÜN YETMEZ: fark son N TAM seansın MEDYANI'ndan okunur. Bir günlük sapma
Yahoo'nun anlık hatası olabilir; her gün tekrarlayan sapma baz sorunudur.

ÇIKTI: veriler_saatlik/.karantina.json → `saatlik_kapi` bunu okur ve
karantinadaki hisse için saatlik hesaba İZİN VERMEZ.

CLI:
    python saatlik_uzlasma.py          → ölç, raporu yaz (dosyaya yazmaz)
    python saatlik_uzlasma.py --yaz    → ölç + karantina dosyasını güncelle
"""
import os
import json
import glob
from datetime import datetime

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DEPO = os.environ.get("SMR_HOURLY_DIR", os.path.join(BASE, "veriler_saatlik"))
GUNLUK = os.path.join(BASE, "veriler")
KARANTINA_DOSYA = os.path.join(DEPO, ".karantina.json")

PENCERE = 20          # kaç TAM seans geriye bakılır
MIN_GUN = 5           # bu kadar tam gün bulunamazsa hüküm verilmez
MIN_UCURUM_KAT = 10.0  # "kopma" sayılması için en az bu kadar kat fark
UST_DILIM = 0.05      # uçurum yalnız en kötü %5'lik dilimde aranır (gürültüde arama)


def _tam_gun_olcumleri(ticker):
    """Son PENCERE tam seans için (saatlik son kapanış / günlük kapanış) oranları."""
    import saatlik_kapi as k
    sym = str(ticker).upper().replace(".IS", "")
    hy = os.path.join(DEPO, "%s.IS_1h.parquet" % sym)
    gy = os.path.join(GUNLUK, "%s.IS_1d.parquet" % sym)
    if not (os.path.exists(hy) and os.path.exists(gy)):
        return []
    h = k._oku_ham(hy)
    d = pd.read_parquet(gy)
    di = pd.to_datetime(d.index)
    di = di.tz_localize(None) if getattr(di, "tz", None) is not None else di
    gunluk = pd.Series(pd.to_numeric(d["Close"], errors="coerce").values,
                       index=[x.date() for x in di])
    gunler = [x.date() for x in h.index]
    saatlik_son = h.groupby(gunler)["Close"].last()
    bar_sayisi = h.groupby(gunler).size()

    oranlar = []
    for gun in sorted(set(saatlik_son.index) & set(gunluk.index), reverse=True):
        beklenen = len(k.beklenen_damgalar(gun))
        if not beklenen or int(bar_sayisi.loc[gun]) < beklenen:
            continue                      # YARIM gün kıyaslamaya girmez
        gc = float(gunluk.loc[gun])
        sc = float(saatlik_son.loc[gun])
        if gc <= 0 or not np.isfinite(gc) or not np.isfinite(sc):
            continue
        oranlar.append(sc / gc)
        if len(oranlar) >= PENCERE:
            break
    return oranlar


def olc(semboller=None):
    """Depodaki her hisse için sapma karnesi (yüzde)."""
    if semboller is None:
        semboller = [os.path.basename(f).replace(".IS_1h.parquet", "")
                     for f in glob.glob(os.path.join(DEPO, "*.IS_1h.parquet"))]
    satir = []
    for sym in sorted(semboller):
        try:
            oranlar = _tam_gun_olcumleri(sym)
        except Exception:
            continue
        if len(oranlar) < MIN_GUN:
            continue
        o = np.array(oranlar, dtype=float)          # [0] = en yeni gün
        medyan_oran = float(np.median(o))
        # ⚠ Sınıflandırma SON günlerden yapılır. Bölünme pencerenin ORTASINDA
        # olduysa 20 günlük oran iki kümeye ayrılır ve yayılım patlar; bu, veri
        # tutarsız demek değil — olay yeni demektir. Son 5 gün sabit bir çarpan
        # gösteriyorsa bu onarılabilir bir BAZ KAYMASIDIR.
        son = o[:5]
        son_oran = float(np.median(son))
        son_yayilim = float(np.percentile(son, 75) - np.percentile(son, 25)) * 100.0
        satir.append({
            "sym": sym,
            "gun": len(o),
            "oran": medyan_oran,
            "sapma": abs(medyan_oran - 1.0) * 100.0,
            "yayilim": float(np.percentile(o, 75) - np.percentile(o, 25)) * 100.0,
            "son_oran": son_oran,
            "son_sapma": abs(son_oran - 1.0) * 100.0,
            "son_yayilim": son_yayilim,
        })
    return satir


def esik_bul(satir):
    """Sapma dağılımındaki en büyük KAT uçurumunu bul, eşiği ortasına koy.

    Döner: (esik, ucurum_kat, bilgi_metni). Uçurum yoksa esik=None → karantina yok.
    """
    if not satir:
        return None, 0.0, "ölçüm yok"
    s = sorted((r["sapma"] for r in satir), reverse=True)
    p99 = float(np.percentile(s, 99))
    tavan = max(3, int(len(s) * UST_DILIM))       # yalnız en kötü dilimde ara
    en_iyi = (0.0, None)
    for i in range(min(tavan, len(s) - 1)):
        alt = s[i + 1]
        if alt <= 1e-9:
            continue
        kat = s[i] / alt
        if kat > en_iyi[0]:
            en_iyi = (kat, i)
    kat, i = en_iyi
    if i is None or kat < MIN_UCURUM_KAT:
        return None, kat, "belirgin kopma yok (en büyük kat farkı %.1f×)" % kat
    ust, alt = s[i], s[i + 1]
    esik = float(np.sqrt(max(ust, 1e-9) * max(alt, 1e-9)))   # uçurumun ortası
    bilgi = ("uçurum %.1f× (%%%.2f ↔ %%%.2f) → eşik %%%.2f · dağılım p99=%%%.2f"
             % (kat, ust, alt, esik, p99))
    return esik, kat, bilgi


def karne(semboller=None):
    satir = olc(semboller)
    esik, kat, bilgi = esik_bul(satir)
    karantina = []
    if esik is not None:
        for r in satir:
            if r["sapma"] > esik:
                r = dict(r)
                # SON günlerde oran sabit bir çarpansa → bölünme/bedelsiz
                # (saatliği yeniden indirmek çözer). Son günlerde bile oynaksa
                # → kaynak tutarsızlığı (indirmek çözmeyebilir, elde inceleme).
                r["tip"] = ("BAZ_KAYMASI"
                            if r["son_yayilim"] < max(r["son_sapma"] * 0.10, 0.5)
                            else "TUTARSIZ")
                # Bölünme pencerenin ORTASINDA olduysa son günler zaten temizdir:
                # bugünkü FİYAT doğru, bozuk olan GEÇMİŞ. Karantina yine sürer —
                # saatlik geçmişe bakan her hesap (4s mumlar, OBV eğimi, gün-içi
                # relatif güç) o eski barlarla zehirlenir. Ayrım 3. adım için:
                # son_temiz olan hisseye yalnız geçmiş onarımı gerekir.
                r["son_temiz"] = bool(r["son_sapma"] <= esik)
                karantina.append(r)
    return {"olculen": len(satir), "esik": esik, "ucurum_kat": kat,
            "bilgi": bilgi, "karantina": sorted(karantina, key=lambda x: -x["sapma"])}


def yaz(sonuc):
    veri = {
        "gun": datetime.now().strftime("%Y-%m-%d"),
        "esik_pct": sonuc["esik"],
        "bilgi": sonuc["bilgi"],
        "olculen": sonuc["olculen"],
        "liste": {r["sym"]: {"sapma_pct": round(r["sapma"], 3),
                             "oran": round(r["oran"], 6),
                             "son_oran": round(r["son_oran"], 6),
                             "son_temiz": r["son_temiz"],
                             "gun": r["gun"], "tip": r["tip"]}
                  for r in sonuc["karantina"]},
    }
    os.makedirs(DEPO, exist_ok=True)
    with open(KARANTINA_DOSYA, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=1)
    return KARANTINA_DOSYA


def karantina_oku():
    """saatlik_kapi bunu okur. Döner: {sym: kayit} (yoksa boş)."""
    try:
        with open(KARANTINA_DOSYA, "r", encoding="utf-8") as f:
            return json.load(f).get("liste") or {}
    except Exception:
        return {}


if __name__ == "__main__":
    import sys
    sonuc = karne()
    print("olculen hisse: %d (son %d tam seans)" % (sonuc["olculen"], PENCERE))
    print("esik: %s" % sonuc["bilgi"])
    if not sonuc["karantina"]:
        print("karantina: YOK")
    else:
        print("karantina: %d hisse" % len(sonuc["karantina"]))
        for r in sonuc["karantina"]:
            print("  %-8s sapma=%%%-9.2f oran=%-7.4f son5_oran=%-7.4f %-12s %s"
                  % (r["sym"], r["sapma"], r["oran"], r["son_oran"], r["tip"],
                     "son gunler TEMIZ (yalniz gecmis bozuk)" if r["son_temiz"]
                     else "bugun de bozuk"))
    if "--yaz" in sys.argv:
        print("yazildi: %s" % yaz(sonuc))
    else:
        print("(dosyaya yazmak icin: --yaz)")
