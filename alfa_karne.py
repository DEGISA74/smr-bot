"""Tarama alfalarını kapanış ve işlem yapılabilir açılış cetveliyle karşılaştır.

Karar karnesi sabit T+3, T+5 ve T+20 vadelerini kullanır. Aynı örneklem
içinden sonradan en iyi günü seçen ``ideal_day`` bu araçta karar üretmez.

Kullanım:
    python alfa_karne.py
    python alfa_karne.py --gun 5
    python alfa_karne.py --lookback-days 90

Çıktı: konsol + logs/alfa_karne.md
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

from bist_data_store import active_version_id, read_active
from signal_policy import (
    MEASUREMENT_REGIME_FALLING,
    MEASUREMENT_REGIME_RISING,
    MEASUREMENT_REGIME_RULE,
    MEASUREMENT_REGIME_WINDOW,
    measurement_regime_series,
    resolve_next_open_entry,
)


try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


KOK = Path(__file__).parent
DB = KOK / "patron.db"
OUT = KOK / "logs" / "alfa_karne.md"
SABIT_VADELER = (3, 5, 20)
MIN_N = 150
KURUMSAL_ESIK = 0.15


def _hazirla(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or getattr(df, "empty", True):
        return None
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out = out.loc[:, ~out.columns.duplicated()].copy()
    out.columns = [str(c).capitalize() for c in out.columns]
    if not {"Open", "High", "Low", "Close"}.issubset(out.columns):
        return None
    out.index = pd.to_datetime(out.index)
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    out.index = out.index.normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    for col in ("Open", "High", "Low", "Close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["Open", "High", "Low", "Close"])


def _aktif_olaylar(lookback_days: int) -> tuple[pd.DataFrame, str, str]:
    with sqlite3.connect(DB) as con:
        son = con.execute("SELECT MAX(scan_date) FROM scan_signals").fetchone()[0]
        if not son:
            return pd.DataFrame(), "", ""
        bas = (pd.Timestamp(son) - pd.Timedelta(days=int(lookback_days))).date().isoformat()
        sig = pd.read_sql(
            "SELECT id, scan_date, scan_type, symbol, bias, category "
            "FROM scan_signals "
            "WHERE COALESCE(is_event_start, 1)=1 AND scan_date>=? AND scan_date<=? "
            "ORDER BY scan_date, scan_type, symbol, id",
            con,
            params=(bas, son),
        )
    if sig.empty:
        return sig, bas, son
    sig = sig.drop_duplicates(
        subset=["scan_date", "scan_type", "symbol"], keep="last"
    ).reset_index(drop=True)
    return sig, bas, son


def _kapanis_pozisyonu(df: pd.DataFrame, tarih) -> int | None:
    """Sinyal tatil/hafta sonu yazılmışsa son gerçek seans kapanışını bul."""
    hedef = pd.Timestamp(tarih).normalize()
    loc = int(df.index.searchsorted(hedef, side="right")) - 1
    return loc if loc >= 0 else None


def _tam_pozisyon(df: pd.DataFrame, tarih) -> int | None:
    """İşlem girişi için aynı takvim günündeki endeks seansını bul."""
    hedef = pd.Timestamp(tarih).normalize()
    loc = int(df.index.searchsorted(hedef, side="left"))
    if loc >= len(df) or df.index[loc] != hedef:
        return None
    return loc


def _kurumsal_indeksler(df: pd.DataFrame) -> set[int]:
    hareket = df["Close"].pct_change().abs() > KURUMSAL_ESIK
    return {i for i, deger in enumerate(hareket.tolist()) if bool(deger)}


def _kurumsal_var(indeksler: set[int], bas: int, son: int) -> bool:
    return any(bas < i <= son for i in indeksler)


def _yon_carpani(scan_type: str, bias: str) -> int:
    ayi = "bear" in str(bias or "").lower() or scan_type in ("er_D4", "er_D5")
    return -1 if ayi else 1


def _getiri(giris: float, cikis: float, yon: int) -> float:
    return ((float(cikis) / float(giris)) - 1.0) * 100.0 * yon


def _olc(vadeler: tuple[int, ...], lookback_days: int):
    sinyaller, bas, son = _aktif_olaylar(lookback_days)
    taramalar = sorted(sinyaller["scan_type"].dropna().astype(str).unique())
    surum = active_version_id()
    endeks = _hazirla(read_active("XU100.IS", surum))
    if endeks is None or endeks.empty:
        raise RuntimeError("Aktif veri kasasında XU100 günlük fiyatı bulunamadı.")
    rejimler = measurement_regime_series(endeks)

    depo: dict[str, pd.DataFrame | None] = {}
    kurumsal: dict[str, set[int]] = {}
    durumlar = Counter()
    satirlar = []

    for kayit in sinyaller.itertuples(index=False):
        sembol = str(kayit.symbol or "").upper().strip()
        if sembol not in depo:
            depo[sembol] = _hazirla(read_active(sembol, surum))
            kurumsal[sembol] = (
                _kurumsal_indeksler(depo[sembol]) if depo[sembol] is not None else set()
            )
        hisse = depo[sembol]
        if hisse is None or hisse.empty:
            durumlar["hisse_verisi_yok"] += 1
            continue

        sinyal_pos = _kapanis_pozisyonu(hisse, kayit.scan_date)
        endeks_sinyal_pos = _kapanis_pozisyonu(endeks, kayit.scan_date)
        if sinyal_pos is None or endeks_sinyal_pos is None:
            durumlar["sinyal_gunu_eslesmedi"] += 1
            continue
        rejim = rejimler.iloc[endeks_sinyal_pos]
        if pd.isna(rejim):
            durumlar["rejim_icin_50_seans_yok"] += 1
            continue

        giris = resolve_next_open_entry(
            hisse,
            kayit.scan_date,
            bias=str(kayit.bias or "bullish"),
            apply_bist_limit=sembol.endswith(".IS"),
            max_locked_sessions=3,
        )
        giris_durumu = str(giris.get("status", "bilinmiyor"))
        durumlar[giris_durumu] += 1
        if not giris_durumu.startswith("filled"):
            continue

        yeni_pos = int(giris["entry_pos"])
        endeks_yeni_pos = _tam_pozisyon(endeks, giris["entry_date"])
        if endeks_yeni_pos is None:
            durumlar["endeks_giris_gunu_yok"] += 1
            continue

        yon = _yon_carpani(str(kayit.scan_type), str(kayit.bias or ""))
        for gun in vadeler:
            eski_cikis = sinyal_pos + gun
            eski_endeks_cikis = endeks_sinyal_pos + gun
            yeni_cikis = yeni_pos + gun - 1
            yeni_endeks_cikis = endeks_yeni_pos + gun - 1
            if (
                eski_cikis >= len(hisse)
                or eski_endeks_cikis >= len(endeks)
                or yeni_cikis >= len(hisse)
                or yeni_endeks_cikis >= len(endeks)
            ):
                durumlar[f"T+{gun}_olgunlasmadi"] += 1
                continue
            if _kurumsal_var(kurumsal[sembol], sinyal_pos, eski_cikis):
                durumlar[f"T+{gun}_kurumsal_islem"] += 1
                continue
            if _kurumsal_var(kurumsal[sembol], yeni_pos - 1, yeni_cikis):
                durumlar[f"T+{gun}_kurumsal_islem"] += 1
                continue

            eski_ret = _getiri(
                hisse["Close"].iloc[sinyal_pos], hisse["Close"].iloc[eski_cikis], yon
            )
            eski_bench = _getiri(
                endeks["Close"].iloc[endeks_sinyal_pos],
                endeks["Close"].iloc[eski_endeks_cikis],
                yon,
            )
            yeni_ret = _getiri(
                float(giris["entry_price"]), hisse["Close"].iloc[yeni_cikis], yon
            )
            yeni_bench = _getiri(
                endeks["Open"].iloc[endeks_yeni_pos],
                endeks["Close"].iloc[yeni_endeks_cikis],
                yon,
            )
            satirlar.append(
                {
                    "scan_type": str(kayit.scan_type),
                    "vade": int(gun),
                    "eski_ret": eski_ret,
                    "eski_bench": eski_bench,
                    "eski_alfa": eski_ret - eski_bench,
                    "yeni_ret": yeni_ret,
                    "yeni_bench": yeni_bench,
                    "yeni_alfa": yeni_ret - yeni_bench,
                    "rejim": str(rejim),
                    "entry_status": giris_durumu,
                }
            )

    return (
        pd.DataFrame(satirlar), taramalar, durumlar,
        bas, son, surum, len(sinyaller),
    )


def _isaret(deger: float | None) -> int:
    if deger is None or pd.isna(deger):
        return 0
    return 1 if float(deger) > 0 else (-1 if float(deger) < 0 else 0)


def _ozet(sonuclar: pd.DataFrame, taramalar: list[str], vadeler: tuple[int, ...]):
    rows = []
    for tarama in taramalar:
        for gun in vadeler:
            grup = sonuclar[
                (sonuclar["scan_type"] == tarama) & (sonuclar["vade"] == gun)
            ]
            if grup.empty:
                rows.append(
                    {
                        "scan_type": tarama,
                        "vade": gun,
                        "n": 0,
                        "eski_ret": None,
                        "eski_bench": None,
                        "eski_alfa": None,
                        "yeni_ret": None,
                        "yeni_bench": None,
                        "yeni_alfa": None,
                        "delta": None,
                        "isaret_degisti": False,
                        "yukselen_n": 0,
                        "yukselen_eski_alfa": None,
                        "yukselen_yeni_alfa": None,
                        "yukselen_isaret_degisti": False,
                        "dusen_n": 0,
                        "dusen_eski_alfa": None,
                        "dusen_yeni_alfa": None,
                        "dusen_isaret_degisti": False,
                    }
                )
                continue
            eski_alfa = float(grup["eski_alfa"].mean())
            yeni_alfa = float(grup["yeni_alfa"].mean())
            rejim_ozeti = {}
            for etiket, anahtar in (
                (MEASUREMENT_REGIME_RISING, "yukselen"),
                (MEASUREMENT_REGIME_FALLING, "dusen"),
            ):
                parca = grup[grup["rejim"] == etiket]
                parca_eski = float(parca["eski_alfa"].mean()) if len(parca) else None
                parca_yeni = float(parca["yeni_alfa"].mean()) if len(parca) else None
                rejim_ozeti.update(
                    {
                        f"{anahtar}_n": len(parca),
                        f"{anahtar}_eski_alfa": parca_eski,
                        f"{anahtar}_yeni_alfa": parca_yeni,
                        f"{anahtar}_isaret_degisti": (
                            bool(len(parca))
                            and _isaret(parca_eski) != _isaret(parca_yeni)
                        ),
                    }
                )
            rows.append(
                {
                    "scan_type": tarama,
                    "vade": gun,
                    "n": len(grup),
                    "eski_ret": float(grup["eski_ret"].mean()),
                    "eski_bench": float(grup["eski_bench"].mean()),
                    "eski_alfa": eski_alfa,
                    "yeni_ret": float(grup["yeni_ret"].mean()),
                    "yeni_bench": float(grup["yeni_bench"].mean()),
                    "yeni_alfa": yeni_alfa,
                    "delta": yeni_alfa - eski_alfa,
                    "isaret_degisti": _isaret(eski_alfa) != _isaret(yeni_alfa),
                    **rejim_ozeti,
                }
            )
    return pd.DataFrame(rows)


def _yuzde(deger) -> str:
    return "—" if deger is None or pd.isna(deger) else f"{float(deger):+.2f}"


def _gecis(eski, yeni) -> str:
    return f"{_yuzde(eski)}→{_yuzde(yeni)}"


def _rejim_hukmu(r) -> str:
    yn, dn = int(r.yukselen_n), int(r.dusen_n)
    if yn == 0 or dn == 0:
        return "iki rejim yok"
    ya, da = _isaret(r.yukselen_yeni_alfa), _isaret(r.dusen_yeni_alfa)
    if ya > 0 and da > 0:
        yon = "iki rejimde pozitif"
    elif ya < 0 and da < 0:
        yon = "iki rejimde negatif"
    elif ya == 0 or da == 0:
        yon = "nötr hücre var"
    else:
        yon = "tek rejim"
    if min(yn, dn) < MIN_N:
        return f"{yon}; belirsiz (rejim N<150)"
    return yon


def _rapor(
    ozet: pd.DataFrame,
    sonuclar: pd.DataFrame,
    taramalar: list[str],
    durumlar: Counter,
    bas: str,
    son: str,
    surum: str,
    olay_sayisi: int,
    vadeler: tuple[int, ...],
) -> str:
    isaret = ozet[ozet["isaret_degisti"]].copy()
    rejim_isaret = ozet[
        ozet["yukselen_isaret_degisti"] | ozet["dusen_isaret_degisti"]
    ].copy()
    L = [
        "# ALFA KARNESİ — KAPANIŞ vs İŞLEM YAPILABİLİR AÇILIŞ",
        "",
        f"- Dönem: **{bas} → {son}** · aktif tarama: **{len(taramalar)}**",
        f"- Bağımsız olay: **{olay_sayisi}** · karşılaştırılan vade satırı: **{len(sonuclar)}**",
        f"- Aktif fiyat kasası: **{surum}**",
        "- Eski cetvel: sinyal günü kapanış → T+n kapanış; hisse ve XU100 aynı kapanıştan başlar.",
        "- Yeni cetvel: sinyalden sonraki ilk işlem yapılabilir açılış → giriş seansı dahil T+n kapanış; hisse ve XU100 aynı günün açılışından başlar.",
        "- Tavan kilidi: en fazla 3 kilitli seans atlanır; açılamayan olay karneye girmez.",
        "- Tekrar sayımı: yalnız `is_event_start=1`. Karşılaştırma aynı olaylar üzerinde eşleştirilmiştir.",
        "- Vade seçimi: yalnız sabit **T+3 / T+5 / T+20**; `ideal_day` karar dışıdır.",
        f"- Mühürlü rejim: **{MEASUREMENT_REGIME_RULE}** — sinyal gününün XU100 kapanışı, aynı gün dahil {MEASUREMENT_REGIME_WINDOW} seanslık basit ortalamanın üstündeyse YÜKSELEN; eşit veya altındaysa DÜŞEN.",
        "- Rejim yalnız karneyi böler; tarama açmaz, kapatmaz, skor veya sinyal ağırlığı değiştirmez.",
        "- Ayı yönlü taramalarda hisse ve endeks getirisi sinyal yönüne çevrilmiştir.",
        "",
        "## Giriş kapısı sayımı",
        "",
        "| Durum | Olay |",
        "|---|---:|",
    ]
    for ad, n in sorted(durumlar.items()):
        L.append(f"| {ad} | {n} |")

    L += [
        "",
        f"## İşaret değiştiren tarama-vade hücreleri ({len(isaret)})",
        "",
        "| Tarama | Vade | N | Eski alfa | Yeni alfa | Fark |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    if isaret.empty:
        L.append("| — | — | — | — | — | — |")
    else:
        for _, r in isaret.sort_values(["vade", "scan_type"]).iterrows():
            L.append(
                f"| **{r.scan_type}** | T+{int(r.vade)} | {int(r.n)} | "
                f"{_yuzde(r.eski_alfa)} | **{_yuzde(r.yeni_alfa)}** | {_yuzde(r.delta)} |"
            )

    L += [
        "",
        f"## Rejim içinde giriş cetveliyle işaret değiştiren hücreler ({len(rejim_isaret)})",
        "",
        "| Tarama | Vade | Yükselen N | Yükselen eski→yeni alfa | Düşen N | Düşen eski→yeni alfa |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    if rejim_isaret.empty:
        L.append("| — | — | — | — | — | — |")
    else:
        for _, r in rejim_isaret.sort_values(["vade", "scan_type"]).iterrows():
            L.append(
                f"| **{r.scan_type}** | T+{int(r.vade)} | {int(r.yukselen_n)} | "
                f"{_gecis(r.yukselen_eski_alfa, r.yukselen_yeni_alfa)} | "
                f"{int(r.dusen_n)} | {_gecis(r.dusen_eski_alfa, r.dusen_yeni_alfa)} |"
            )

    for gun in vadeler:
        L += [
            "",
            f"## T+{gun} — tüm aktif taramalar",
            "",
            "| Tarama | N | Eski getiri | Eski XU100 | Eski alfa | Yeni getiri | Yeni XU100 | Yeni alfa | Yükselen N | Yükselen eski→yeni alfa | Düşen N | Düşen eski→yeni alfa | Fark | Hüküm |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        parca = ozet[ozet["vade"] == gun].sort_values(
            ["yeni_alfa", "scan_type"], ascending=[False, True], na_position="last"
        )
        for _, r in parca.iterrows():
            if int(r.n) == 0:
                hukum = "örnek yok"
            elif int(r.n) < MIN_N:
                hukum = _rejim_hukmu(r) + "; toplam N<150"
            else:
                hukum = _rejim_hukmu(r)
                if bool(r.isaret_degisti):
                    hukum += "; ⚠ genel işaret değişti"
            L.append(
                f"| {r.scan_type} | {int(r.n)} | {_yuzde(r.eski_ret)} | "
                f"{_yuzde(r.eski_bench)} | {_yuzde(r.eski_alfa)} | "
                f"{_yuzde(r.yeni_ret)} | {_yuzde(r.yeni_bench)} | "
                f"**{_yuzde(r.yeni_alfa)}** | {int(r.yukselen_n)} | "
                f"{_gecis(r.yukselen_eski_alfa, r.yukselen_yeni_alfa)} | "
                f"{int(r.dusen_n)} | {_gecis(r.dusen_eski_alfa, r.dusen_yeni_alfa)} | "
                f"{_yuzde(r.delta)} | {hukum} |"
            )
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gun", type=int, choices=SABIT_VADELER)
    ap.add_argument("--lookback-days", type=int, default=90)
    a = ap.parse_args()
    vadeler = (a.gun,) if a.gun else SABIT_VADELER
    sonuclar, taramalar, durumlar, bas, son, surum, olay_sayisi = _olc(
        vadeler, a.lookback_days
    )
    ozet = _ozet(sonuclar, taramalar, vadeler)
    metin = _rapor(
        ozet, sonuclar, taramalar, durumlar,
        bas, son, surum, olay_sayisi, vadeler,
    )
    print(metin)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(metin, encoding="utf-8")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
