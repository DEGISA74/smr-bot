#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""İki hacim kaynağının BİRBİRİNİ TUTUP TUTMADIĞINI ölçer (31 Ağu 2026).

NEDEN VAR
=========
Sistem 26-31 Ağu arası sessizce tek kaynağa (borsapy) düştü çünkü hüküm
"kaynağın ADI"na göre veriliyordu: İş Yatırım = resmî, borsapy = yedek.
31 Ağu ölçümü bu ayrımı çürüttü — 14 hisse × 17 gün karşılaştırıldığında
ortanca fark %0,00, günlerin %94,5'i ±%1 içindeydi. Yani likit hisselerde
iki kaynak pratikte AYNI veriyi veriyor.

Bu yüzden hüküm artık kaynağın kimliğine değil, İKİSİNİN UYUŞMASINA bakar:

  * uyuşuyorlarsa  → veri sağlam, hangisinden geldiği önemsiz
  * ayrışıyorlarsa → o barda gerçekten sorun var, işaretlenir
  * biri susuyorsa → diğeri devam eder ama "çapraz kontrolsüz" sayılır

Tek kaynakla bu sorulardan HİÇBİRİ sorulamaz. İkinci kaynağın asıl değeri
yedeklilik değil, işte bu ikinci göz.

NE YAPMAZ
=========
Üretim verisine YAZMAZ. Aktif sürümü değiştirmez, parquet'e dokunmaz,
tarama/skor/AI akışına girmez. Yalnız ölçer ve karne yazar.

KULLANIM
========
    python hacim_capraz.py              # dönen örneklem, karne yaz
    python hacim_capraz.py --rapor      # son karneyi insan diliyle yazdır
    python hacim_capraz.py --ornek 50   # örneklem boyutu
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
KARNE_PATH = LOG_DIR / "hacim_capraz_karne.json"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "hacim_capraz.log", encoding="utf-8"),
              logging.StreamHandler()])
log = logging.getLogger(__name__)

# Örneklem: her gece farklı dilim → zamanla tüm evren taranır, tek turda
# sunucu yorulmaz. 30 sembol × ~12 sn ≈ 6 dakika.
ORNEKLEM = int(os.environ.get("CAPRAZ_ORNEKLEM", "30"))
GERI_GUN = int(os.environ.get("CAPRAZ_GERI_GUN", "20"))

# LİKİT EŞİĞİ — cizgi_yapi ile aynı taban (25mn TL). Ayrım şart: ince
# tahtalarda iki kaynağın ayrışması BEKLENEN bir şey, alarm sebebi değil.
# Kapı yalnız likit kohortta çalışır; ince tahta sonucu RAPORLANIR, hüküm vermez.
LIKIT_TABAN = float(os.environ.get("CAPRAZ_LIKIT_TABAN", "25000000"))

# UYUŞMA KAPISI — 31 Ağu ölçülen taban: likit kohortta günlerin %94,5'i ±%1,
# %98,3'ü ±%5 içindeydi. Kapı ±%2'de %90'a kuruldu: ölçülen tabanın belirgin
# ALTINDA, yani normal gürültü alarm üretmez, gerçek ayrışma üretir.
UYUSMA_TOLERANS = float(os.environ.get("CAPRAZ_TOLERANS", "2.0"))
UYUSMA_ESIGI = float(os.environ.get("CAPRAZ_ESIK", "0.90"))
# Hüküm verebilmek için likit kohortta en az bu kadar gün ölçülmüş olmalı.
MIN_GUN = int(os.environ.get("CAPRAZ_MIN_GUN", "60"))


def _evren() -> list[str]:
    """Aktif manifestteki hisseler (endeksler hariç — İş Yatırım endeks vermez)."""
    from bist_data_store import load_manifest
    manifest = load_manifest() or {}
    semboller = sorted((manifest.get("symbols") or {}).keys())
    return [s for s in semboller
            if not s.replace(".IS", "").upper().startswith(
                ("XU", "XB", "XT", "XY", "XK", "XG", "XI", "XUS"))]


def _dilim(evren: list[str], boyut: int, gun: str) -> list[str]:
    """Gün numarasına göre dönen dilim — zamanla tüm evren kapsanır."""
    if not evren or boyut <= 0:
        return []
    boyut = min(boyut, len(evren))
    dilim_sayisi = max(1, (len(evren) + boyut - 1) // boyut)
    sira = int(datetime.strptime(gun, "%Y-%m-%d").toordinal()) % dilim_sayisi
    bas = sira * boyut
    return (evren + evren)[bas:bas + boyut]


def _ciro(frame: pd.DataFrame) -> float:
    try:
        deger = (pd.to_numeric(frame["Close"], errors="coerce")
                 * pd.to_numeric(frame["Volume"], errors="coerce")).dropna()
        return float(deger.tail(20).median()) if not deger.empty else 0.0
    except Exception:
        return 0.0


def _normalize(frame: pd.DataFrame | None) -> pd.DataFrame | None:
    if frame is None or getattr(frame, "empty", True):
        return None
    if "Volume" not in frame.columns:
        return None
    out = frame.copy()
    idx = []
    for ts in out.index:
        t = pd.Timestamp(ts)
        idx.append((t.tz_localize(None) if t.tzinfo else t).normalize())
    out.index = pd.DatetimeIndex(idx)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def _isyatirim(sembol: str) -> pd.DataFrame | None:
    from isyatirim_gateway import fetch_once
    try:
        return _normalize(fetch_once(sembol, period_days=GERI_GUN + 5,
                                     priority="probe", max_wait=90))
    except Exception as exc:
        log.debug("isyatirim okunamadi %s: %s", sembol, exc)
        return None


def _borsapy(sembol: str, bas: datetime, bit: datetime) -> pd.DataFrame | None:
    try:
        from provider_traffic import acquire_slot
        from borsapy._providers.tradingview import get_tradingview_provider
    except Exception as exc:
        log.warning("borsapy katmani yuklenemedi: %s", exc)
        return None
    try:
        acquire_slot("borsapy", priority="probe", max_wait=90)
        return _normalize(get_tradingview_provider().get_history(
            symbol=sembol.replace(".IS", ""), interval="1d", start=bas, end=bit))
    except Exception as exc:
        log.debug("borsapy okunamadi %s: %s", sembol, exc)
        return None


def karsilastir(semboller: list[str]) -> dict:
    """Her sembol için iki kaynağı ortak günlerde karşılaştırır."""
    bit = datetime.now()
    bas = bit - timedelta(days=GERI_GUN + 10)
    kohort = {"likit": [], "ince": []}
    ayrisan: list[dict] = []
    sembol_durum: list[dict] = []
    tek_kaynak = 0

    for sembol in semboller:
        a = _isyatirim(sembol)
        b = _borsapy(sembol, bas, bit)
        if a is None or b is None:
            tek_kaynak += 1
            sembol_durum.append({
                "sembol": sembol,
                "durum": ("isyatirim_yok" if a is None and b is not None else
                          "borsapy_yok" if b is None and a is not None else
                          "iki_kaynak_da_yok"),
                "gun": 0,
            })
            continue

        ortak = a.index.intersection(b.index)
        if len(ortak) == 0:
            sembol_durum.append({"sembol": sembol, "durum": "ortak_gun_yok", "gun": 0})
            continue

        va = pd.to_numeric(a.loc[ortak, "Volume"], errors="coerce")
        vb = pd.to_numeric(b.loc[ortak, "Volume"], errors="coerce")
        gecerli = (va > 0) & (vb > 0)
        va, vb = va[gecerli], vb[gecerli]
        if len(va) == 0:
            sembol_durum.append({"sembol": sembol, "durum": "pozitif_gun_yok", "gun": 0})
            continue

        fark = ((vb - va) / va * 100.0).abs()
        grup = "likit" if _ciro(a) >= LIKIT_TABAN else "ince"
        kohort[grup].extend(float(x) for x in fark.values)
        for tarih, deger in fark.items():
            if float(deger) > UYUSMA_TOLERANS:
                ayrisan.append({
                    "sembol": sembol, "tarih": str(pd.Timestamp(tarih).date()),
                    "fark_pct": round(float(deger), 2), "kohort": grup,
                    "isyatirim": float(va.loc[tarih]), "borsapy": float(vb.loc[tarih]),
                })
        sembol_durum.append({
            "sembol": sembol, "durum": "olculdu", "kohort": grup,
            "gun": int(len(fark)), "ort_fark_pct": round(float(fark.mean()), 3),
            "max_fark_pct": round(float(fark.max()), 2),
        })

    return {"kohort": kohort, "ayrisan": ayrisan,
            "sembol_durum": sembol_durum, "tek_kaynak": tek_kaynak}


def _oranlar(farklar: list[float]) -> dict:
    if not farklar:
        return {"gun": 0}
    n = len(farklar)
    sirali = sorted(farklar)
    return {
        "gun": n,
        "ortanca_pct": round(sirali[n // 2], 3),
        "ort_pct": round(sum(sirali) / n, 3),
        "max_pct": round(sirali[-1], 2),
        "uyusma_1pct": round(sum(1 for x in sirali if x <= 1.0) / n, 4),
        "uyusma_2pct": round(sum(1 for x in sirali if x <= 2.0) / n, 4),
        "uyusma_5pct": round(sum(1 for x in sirali if x <= 5.0) / n, 4),
    }


def karne_uret(semboller: list[str] | None = None) -> dict:
    gun = str(datetime.now().date())
    evren = _evren()
    if semboller is None:
        semboller = _dilim(evren, ORNEKLEM, gun)
    if not semboller:
        return {"tarih": gun, "hukum": "olculemedi", "sebep": "evren bos"}

    log.info("Capraz kontrol basliyor: %d sembol (evren %d)", len(semboller), len(evren))
    basla = time.time()
    ham = karsilastir(semboller)
    likit = _oranlar(ham["kohort"]["likit"])
    ince = _oranlar(ham["kohort"]["ince"])

    # HÜKÜM yalnız likit kohorttan çıkar — ölçülmüş tabanı olan tek yer orası.
    if likit.get("gun", 0) < MIN_GUN:
        hukum, sebep = "olculemedi", (
            "likit kohortta yalniz %d gun olculdu (en az %d gerekli); "
            "kaynaklardan biri susuyor olabilir" % (likit.get("gun", 0), MIN_GUN))
    elif likit.get("uyusma_2pct", 0.0) >= UYUSMA_ESIGI:
        hukum, sebep = "uyusuyor", (
            "likit kohortta gunlerin %%%.1f'i +-%%%.1f icinde"
            % (likit["uyusma_2pct"] * 100, UYUSMA_TOLERANS))
    else:
        hukum, sebep = "ayrisma_var", (
            "likit kohortta uyusma %%%.1f — kapi %%%.0f"
            % (likit["uyusma_2pct"] * 100, UYUSMA_ESIGI * 100))

    karne = {
        "tarih": gun,
        "uretildi": datetime.now().isoformat(timespec="seconds"),
        "sure_sn": round(time.time() - basla, 1),
        "hukum": hukum,
        "sebep": sebep,
        "kapi": {"tolerans_pct": UYUSMA_TOLERANS, "esik": UYUSMA_ESIGI,
                 "min_gun": MIN_GUN, "likit_taban_tl": LIKIT_TABAN},
        "ornek_sembol": len(semboller),
        "tek_kaynak_sembol": ham["tek_kaynak"],
        "likit": likit,
        "ince_tahta": ince,          # raporlanir, HUKUM VERMEZ
        "ayrisan": sorted(ham["ayrisan"], key=lambda r: -r["fark_pct"])[:40],
        "sembol_durum": ham["sembol_durum"],
    }
    tmp = KARNE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(karne, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(KARNE_PATH)
    log.info("Karne yazildi: hukum=%s | %s", hukum, sebep)
    return karne


def karne_oku() -> dict:
    try:
        return json.loads(KARNE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _rapor(karne: dict) -> None:
    if not karne:
        print("Karne yok — once 'python hacim_capraz.py' calistirin.")
        return
    print("HACIM CAPRAZ KONTROL KARNESI —", karne.get("tarih"))
    print("  hukum        :", karne.get("hukum"), "|", karne.get("sebep"))
    print("  ornek        : %d sembol, %s sn" % (karne.get("ornek_sembol", 0),
                                                 karne.get("sure_sn")))
    print("  tek kaynak   : %d sembolde ikinci kaynak yok" % karne.get("tek_kaynak_sembol", 0))
    for ad in ("likit", "ince_tahta"):
        blok = karne.get(ad) or {}
        if blok.get("gun"):
            print("  %-11s: %d gun · ortanca %%%.2f · +-%%1 uyusma %%%.1f · +-%%2 %%%.1f · max %%%.1f"
                  % (ad, blok["gun"], blok["ortanca_pct"], blok["uyusma_1pct"] * 100,
                     blok["uyusma_2pct"] * 100, blok["max_pct"]))
        else:
            print("  %-11s: olculemedi" % ad)
    ayrisan = karne.get("ayrisan") or []
    if ayrisan:
        print("  ayrisan barlar (ilk 10):")
        for r in ayrisan[:10]:
            print("    %-10s %s  %%%.1f  (%s)" % (r["sembol"], r["tarih"],
                                                  r["fark_pct"], r["kohort"]))


def main() -> int:
    argv = sys.argv[1:]
    if "--rapor" in argv:
        _rapor(karne_oku())
        return 0
    if "--ornek" in argv:
        try:
            globals()["ORNEKLEM"] = int(argv[argv.index("--ornek") + 1])
        except Exception:
            pass
    karne = karne_uret()
    _rapor(karne)
    return 0 if karne.get("hukum") != "ayrisma_var" else 1


if __name__ == "__main__":
    raise SystemExit(main())
