"""SİTE TARAMA ÖZETİ (3 Eki 2026) — "Algoritmanın Taramaları" vitrin kartının verisi.

VPS Master Scan fotoğrafını (veriler/scan_cache/master_scan__BIST_500_.pkl) okur ve
programdaki Tarama Kataloğu sayılarını AYNI kodla üretir:
  tarama_merkezi.build_catalog  → her taramada tekil hisse sayısı
  + Yıldız Pazar (XYLDZ) evrenine kısıtlama (trajectory_tarama_merkezi.
    _restrict_catalog_to_universe ile aynı kural; VPS'teki modül sürümü bu
    fonksiyonu henüz taşımadığı için kural burada aynen tekrarlanır).

Siteye SADECE tarama adı + sayı + listenin 1. hissesinin MASKELİ adı (ilk harf + ****) ile
fiyat / günlük değişim / 52 hafta konumu gider; hiçbir hissenin tam adı dosyaya yazılmaz
(sayfa kaynağından okunamasın — yildiz_ozet.json ile aynı ilke).
Radar (Market Intelligence) vitrinde yok: neredeyse her hisseyi listeler, sayısı bilgi taşımaz.
Yeni hesap/eşik YOK. Fotoğraf değişmediyse dosya yeniden yazılmaz.

Çalıştırma: python site_tarama_ozet.py  (VPS cron, akşam Master Scan penceresi)
"""
from __future__ import annotations

import json
import os
import pickle
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
PKL = BASE / "veriler" / "scan_cache" / "master_scan__BIST_500_.pkl"
SITE_OUT = os.environ.get("SMR_SITE_TARAMA_OUT", "/var/www/smr/frontend/tarama_ozet.json")
ISTANBUL = ZoneInfo("Europe/Istanbul")
VITRIN_DISI = {"scan_data"}          # Radar (Market Intelligence)


def maskele(sym) -> str:
    """MPARK → M**** · uzunluk sabit, harf sayısı da ele vermesin."""
    s = _clean_sym(sym)
    return (s[:1] + "****") if s else ""


def _fiyat_bilgisi(sym: str) -> dict:
    """site_hisse_uretici.hisse_uret ile aynı kaynak/formül (1y günlük seri)."""
    from data_layer import get_safe_historical_data
    try:
        df = get_safe_historical_data(sym + ".IS", period="1y")
        c = df["Close"].astype(float)
        k, onceki = float(c.iloc[-1]), float(c.iloc[-2])
        hi, lo = float(df["High"].max()), float(df["Low"].min())
        return {"fiyat": round(k, 2),
                "degisim": round((k / onceki - 1) * 100, 2) if onceki else None,
                "pos52": round((k - lo) / (hi - lo) * 100, 1) if hi > lo else None}
    except Exception:
        return {}


def _clean_sym(sym) -> str:
    return str(sym or "").upper().replace(".IS", "").strip()


def _restrict(catalog: list, universe: list | None) -> list:
    """trajectory_tarama_merkezi._restrict_catalog_to_universe ile birebir aynı kural."""
    if universe is None:
        return catalog
    allowed = {_clean_sym(s) for s in universe if _clean_sym(s)}
    out = []
    for cat in catalog:
        item = dict(cat)
        item["symbols"] = [_clean_sym(s) for s in cat.get("symbols", []) if _clean_sym(s) in allowed]
        item["count"] = len(item["symbols"])
        out.append(item)
    return out


def main() -> int:
    sys.path.insert(0, str(BASE))
    import tarama_merkezi
    from data_layer import load_index_components

    if not PKL.exists():
        print("fotoğraf yok:", PKL)
        return 1
    with open(PKL, "rb") as f:
        snap = pickle.load(f)
    data = snap.get("data") or {}
    ts = snap.get("ts")
    as_of = ts.astimezone(ISTANBUL).strftime("%Y-%m-%d") if hasattr(ts, "astimezone") else str(ts)[:10]

    universe = load_index_components("XYLDZ", allow_network=False)
    if not universe:
        print("XYLDZ listesi yok — boş evrenle yanlış sayı yazılmaz")
        return 1
    catalog = _restrict(tarama_merkezi.build_catalog(data.get), universe)

    taramalar, ilk = [], None
    for cat in catalog:
        if not cat["count"] or cat["key"] in VITRIN_DISI:
            continue                          # program da 0 sonuçlu taramayı göstermez
        taramalar.append({"ad": cat["name"], "aile": cat["family"], "adet": cat["count"]})
        if ilk is None:
            ilk = {"t": maskele(cat["symbols"][0]), "tarama": cat["name"],
                   **_fiyat_bilgisi(cat["symbols"][0])}

    ozet = {"as_of": as_of, "evren": "Yıldız Pazar", "taramalar": taramalar, "ilk": ilk}
    try:
        with open(SITE_OUT, encoding="utf-8") as f:
            eski = json.load(f)
        if {k: eski.get(k) for k in ozet} == ozet:
            print("değişiklik yok", as_of)
            return 0
    except Exception:
        pass
    ozet["uretim"] = datetime.now(ISTANBUL).strftime("%Y-%m-%d %H:%M")
    tmp = SITE_OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ozet, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, SITE_OUT)
    print("yazıldı", as_of, len(taramalar), "tarama")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
