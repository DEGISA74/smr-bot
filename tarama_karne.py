"""Tarama Karnesi — sabit-vade, iki-rejimli tek ölçüm cetveli.

Saf modül: yalnız mühürlü Değişken Vade Aşama 1 çıktısını okur ve
tarama×vade kayıtlarını üretir. Canlı tarama, veritabanı, ekran ve ağ
erişimi yapmaz. Web ile bot ileride aynı sürümlü JSON çıktısını okumalıdır;
ham backtest özeti veya ideal-gün mantığı yeniden yorumlanmamalıdır.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


KARNE_SURUMU = "tarama-karne-v1"
KAYNAK_VARSAYILAN = Path("logs/degisken_vade_asama1.json")
CIKTI_VARSAYILAN = Path("logs/tarama_karne.json")

VADELER = (3, 5, 20)
REJIMLER = ("YUKSELEN", "DUSEN")
MIN_REJIM_N = 150
BAYATLIK_GUN = 7          # tuketici bu yastan eski karneyi SAGLIKLI saymaz


def _red(neden: str) -> None:
    raise ValueError(f"Tarama karnesi mühür doğrulamasından geçmedi: {neden}")


def _muhurleri_dogrula(veri: dict[str, Any]) -> None:
    meta = veri.get("meta")
    if not isinstance(meta, dict):
        _red("meta bölümü yok")
    if meta.get("entry_rule") != (
        "resolve_next_open_entry(apply_bist_limit=True,max_locked_sessions=3)"
    ):
        _red("giriş cetveli ertesi işlem yapılabilir açılış + tavan kilidi değil")
    if meta.get("regime_rule") != "XU100_CLOSE_VS_SMA50":
        _red("rejim XU100 SMA50 tanımı değil")
    if meta.get("dedup_rule") != "is_event_start=1; unique(scan_date,scan_type,symbol)":
        _red("tekrar sayımı olay başlangıcı kuralı değil")
    if meta.get("ideal_day_used") is not False:
        _red("ideal gün araması açık")
    if tuple(meta.get("horizon_sessions", ())) != tuple(range(1, 21)):
        _red("1–20 seans yolu eksik")
    if tuple(meta.get("regimes", ())) != REJIMLER:
        _red("iki mühürlü rejim bulunmuyor")
    if meta.get("universe_baseline_rule") != (
        "all stock paths with matching XU100 session; close_alpha median"
    ):
        _red("evren tabanı aynı giriş günlerinin ortancası değil")


def _kasa_son_kapanmis_seans() -> str | None:
    """Kasadaki son KAPANMIS seansin tarihi (YYYY-AA-GG). Okunamazsa None.

    Bugunun bari seans bitene kadar YARIMDIR; onu 'kapanmis' saymayiz.
    Bu yuzden son bar bugunse bir onceki bara bakariz.
    """
    try:
        import datetime as _dt
        import pandas as _pd
        from bist_data_store import active_version_id, read_active
        df = read_active("XU100.IS", active_version_id())
        if df is None or getattr(df, "empty", True):
            return None
        idx = _pd.to_datetime(df.index).normalize().sort_values()
        bugun = _pd.Timestamp(_dt.date.today())
        if len(idx) and idx[-1] == bugun:
            idx = idx[:-1]                      # bugunun yarim bari sayilmaz
        return idx[-1].date().isoformat() if len(idx) else None
    except Exception:
        return None


def _veri_kapsami_kapisi(veri: dict[str, Any], zorla: bool = False) -> None:
    """Kaynak laboratuvar, kasadaki son KAPANMIS seansi kapsamiyorsa uretme.

    28 Agu 2026 — ilk surumde bu kapi 'veri surumu kimligi ayni mi' diye
    bakiyordu ve YANLISTI: fetcher 10 dakikada bir kosuyor, surum kimligi gun
    icinde surekli degisiyor (v-…0828T104035 -> v-…0828T104533 birkac dakikada).
    Oyle bir kapi neredeyse her zaman reddeder, herkes --surum-atla'ya alisir ve
    kapi anlamini yitirir. Dogru olcut kimlik degil, VERININ KAPSADIGI SON SEANS:
    laboratuvar son kapanmis seansi iceriyorsa web ve bot ayni fotografi okur.
    """
    if zorla:
        return
    kapsam = (veri.get("meta") or {}).get("last_scan_date")
    kasa = _kasa_son_kapanmis_seans()
    if kasa is None or kapsam is None:
        return                                   # olculemiyorsa kilitleme
    if str(kapsam) < str(kasa):
        _red(f"kaynak laboratuvar bayat: son kapsanan seans={kapsam}, "
             f"kasadaki son kapanmis seans={kasa} "
             f"(once variable_horizon_lab.py'yi yeniden kos; bilerek eski "
             f"kaynaktan uretiyorsan --surum-atla)")


def _gun_satiri(rejim_egrisi: dict[str, Any], vade: int) -> dict[str, Any]:
    for satir in rejim_egrisi.get("days", []):
        if satir.get("day") == vade:
            return satir
    _red(f"T+{vade} satırı yok")


def _rejim_hucre(rejim_egrisi: dict[str, Any] | None, vade: int) -> tuple[Any, Any, bool]:
    """N, evren-taban farkı ve eğrinin gerçekten bulunup bulunmadığını döndürür."""
    if not isinstance(rejim_egrisi, dict):
        return None, None, False
    satir = _gun_satiri(rejim_egrisi, vade)
    return satir.get("n_close_alpha"), satir.get("close_alpha_vs_universe"), True


def _durum(
    yukselen_n: Any,
    yukselen_fark: Any,
    yukselen_var: bool,
    dusen_n: Any,
    dusen_fark: Any,
    dusen_var: bool,
) -> str:
    """Önceden mühürlenen kapı: belirsizlikte hüküm üretmez."""
    if not yukselen_var or not dusen_var:
        return "REJIM_EGRISI_YOK"
    if (
        not isinstance(yukselen_n, (int, float))
        or not isinstance(dusen_n, (int, float))
        or yukselen_n < MIN_REJIM_N
        or dusen_n < MIN_REJIM_N
    ):
        return "BELIRSIZ_ORNEKLEM"
    if not isinstance(yukselen_fark, (int, float)) or not isinstance(dusen_fark, (int, float)):
        return "VERI_EKSIK"
    if yukselen_fark <= 0 or dusen_fark <= 0:
        return "EVREN_TABANI_ALTI"
    return "KANITLI_TABAN_USTU"


def tarama_karnesi(veri: dict[str, Any], surum_atla: bool = False) -> dict[str, Any]:
    """Tek kaynaktan sürümlü tarama×vade cetveli üretir.

    Her kayıtta bilinçli olarak yalnız tarama, vade, iki rejimin N'i, iki
    rejimin evren-taban farkı ve durum bulunur. Sayısal 0–100 skor, ideal gün,
    gün başına getiri ve vade seçimi bu modülün dışında bırakılmıştır.
    """
    _muhurleri_dogrula(veri)
    _veri_kapsami_kapisi(veri, zorla=surum_atla)
    egriler = veri.get("curves")
    if not isinstance(egriler, dict):
        _red("tarama eğrileri yok")

    kayitlar: list[dict[str, Any]] = []
    for tarama in sorted(egriler):
        egriler_by_rejim = egriler[tarama]
        if not isinstance(egriler_by_rejim, dict):
            _red(f"{tarama}: rejim eğrisi sözlük değil")
        for vade in VADELER:
            yuk_n, yuk_fark, yuk_var = _rejim_hucre(egriler_by_rejim.get("YUKSELEN"), vade)
            dus_n, dus_fark, dus_var = _rejim_hucre(egriler_by_rejim.get("DUSEN"), vade)
            kayitlar.append(
                {
                    "tarama": tarama,
                    "vade": f"T+{vade}",
                    "yukselen_n": yuk_n,
                    "yukselen_taban_farki": yuk_fark,
                    "dusen_n": dus_n,
                    "dusen_taban_farki": dus_fark,
                    "durum": _durum(yuk_n, yuk_fark, yuk_var, dus_n, dus_fark, dus_var),
                }
            )

    return {
        "karne_surumu": KARNE_SURUMU,
        "uretim_tarihi_utc": datetime.now(timezone.utc).isoformat(),
        "veri_surumu": veri["meta"].get("active_version"),
        "kaynak_uretim_tarihi_utc": veri["meta"].get("generated_at_utc"),
        "kayitlar": kayitlar,
    }


def dosyadan_uret(girdi: Path = KAYNAK_VARSAYILAN, surum_atla: bool = False) -> dict[str, Any]:
    if not girdi.exists():
        raise FileNotFoundError(f"Mühürlü değişken-vade çıktısı yok: {girdi}")
    return tarama_karnesi(json.loads(girdi.read_text(encoding="utf-8")), surum_atla=surum_atla)


def karne_oku(yol: Path = CIKTI_VARSAYILAN,
              azami_gun: int = BAYATLIK_GUN) -> tuple[list[dict[str, Any]], str | None]:
    """TUKETICI KAPISI — web ve bot bunu cagirir. ASLA sessizce bos donmez.

    Donus: (kayitlar, sorun). `sorun` None ise karne saglikli. Degilse cagiran
    onu KULLANICIYA GORUNUR sekilde basmak zorundadir; bos liste gibi davranmak
    yasak.

    28 Agu 2026 — bu kapinin sebebi: backtest_results.json VPS'te hic yoktu,
    iki tuketici de sessizce bos kume dondu ve PRO bulteni 11+ gun eksik gitti.
    Kimse fark etmedi. Ayni tuzagi karne icin bastan kapatiyoruz.
    """
    try:
        if not yol.exists():
            return [], f"Tarama karnesi dosyasi YOK ({yol}) — tarama katmani gosterilemiyor."
        paket = json.loads(yol.read_text(encoding="utf-8"))
        kayitlar = paket.get("kayitlar") or []
        if not kayitlar:
            return [], "Tarama karnesi BOS — uretim basarisiz olmus olabilir."
        if paket.get("karne_surumu") != KARNE_SURUMU:
            return kayitlar, (f"Tarama karnesi surumu beklenenden farkli "
                              f"({paket.get('karne_surumu')} != {KARNE_SURUMU}).")
        damga = paket.get("uretim_tarihi_utc")
        try:
            yas = (datetime.now(timezone.utc) - datetime.fromisoformat(damga)).days
        except Exception:
            return kayitlar, "Tarama karnesinde okunabilir uretim tarihi yok."
        if yas > azami_gun:
            return kayitlar, f"Tarama karnesi BAYAT — {yas} gunluk (sinir {azami_gun})."
        return kayitlar, None
    except Exception as hata:
        return [], f"Tarama karnesi okunamadi: {type(hata).__name__}: {hata}"


def saglik(yol: Path = CIKTI_VARSAYILAN) -> dict[str, Any]:
    """gorev_bekcisi.py icin: {'saglikli': bool, 'sorun': str|None, 'kayit': int}."""
    kayitlar, sorun = karne_oku(yol)
    return {"saglikli": sorun is None, "sorun": sorun, "kayit": len(kayitlar)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=KAYNAK_VARSAYILAN)
    parser.add_argument("--output", type=Path, default=CIKTI_VARSAYILAN)
    parser.add_argument("--surum-atla", action="store_true",
                        dest="surum_atla",
                        help="Veri surumu kapisini atla (bilerek eski kaynaktan uret)")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    sonuc = dosyadan_uret(args.input, surum_atla=args.surum_atla)
    beklenen = len({satir["tarama"] for satir in sonuc["kayitlar"]}) * len(VADELER)
    if len(sonuc["kayitlar"]) != beklenen:
        _red("tarama×vade kayıt sayısı tutmuyor")
    if args.self_test:
        print(
            "TARAMA KARNE SELF-TEST OK | "
            f"tarama={beklenen // len(VADELER)} kayit={len(sonuc['kayitlar'])}"
        )
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(sonuc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "TARAMA KARNE OK | "
        f"tarama={beklenen // len(VADELER)} kayit={len(sonuc['kayitlar'])} "
        f"veri_surumu={sonuc['veri_surumu']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
