"""İŞ A — kısa yönlü taramaların işlem yapılabilir evren fizibilitesi.

Bu bağımsız laboratuvar betiği yalnızca aktif fiyat kasasını ve ``patron.db``
olay başlangıçlarını salt-okunur okur. Canlı tarama, politika, ekran ve
``scan_signals`` yazım hattına dokunmaz.

Ölçüm bir kısa pozisyonun dayanak paydaki yön-çevrilmiş kapanış getirisidir;
vadeli sözleşme baz farkı, teminat, borçlanma maliyeti ve kayma ölçülmez.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
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


ROOT = Path(__file__).resolve().parent
DB = ROOT / "patron.db"
OUT_JSON = ROOT / "logs" / "short_tarama_fizibilite.json"
OUT_MD = ROOT / "logs" / "short_tarama_fizibilite.md"
OUT_EVENTS = ROOT / "logs" / "short_tarama_fizibilite_events.csv"
SCAN_TYPES = ("er_D4", "er_D5", "birlesik_dtri")
HORIZONS = (3, 5, 10, 20)
MIN_REGIME_N = 150

# Borsa İstanbul, VİOP piyasa işleyişi sayfasında 27 Ağustos 2026'da yayımlanan
# üç grup pay vadeli dayanakları. Geçmiş tarihli sözleşme listesi bulunamadığı
# için tüm örneklemde bugünkü liste kullanılır; rapor bunu ileriye bakma olarak
# açıkça işaretler.
VIOP_PAY_FUTURES_20260827 = frozenset(
    "AKBNK ASELS ASTOR BIMAS EKGYO EREGL GARAN ISCTR KCHOL SAHOL SASA THYAO "
    "TRALT TUPRS YKBNK AEFES GUBRF HALKB KRDMD MGROS PETKM PGSUS SISE TAVHL "
    "TCELL TOASO TRMET TTKOM VAKBN AKSEN ALARK ARCLK BRSAN CIMSA DOAS DOHOL "
    "ENJSA ENKAI FROTO HEKTS ODAS OYAKC SOKM TKFEN TSKB ULKER VESTL".split()
)

# BIST 50, 01.07–30.09.2026 dönemi: 27 Ağustos'ta alınan 50 üyeli önbellek,
# Borsa'nın 19 Haziran dönem değişikliğiyle çapraz denetlendi. 01.04–30.06
# üyeliği, 19 Mart resmî değişikliği ve 19 Haziran sonraki değişikliği kullanılıp
# geriye kurulur; böylece Mayıs-Haziran sinyallerinde ileriye bakma olmaz.
BIST50_JUL_SEP_2026 = frozenset(
    "AEFES AKBNK AKSEN ALARK ASELS ASTOR BIMAS BRSAN BTCIM CANTE CCOLA CIMSA "
    "DSTKF ECILC EFOR EKGYO ENKAI EREGL FROTO GARAN GLRMK GUBRF HALKB HEKTS "
    "ISCTR KCHOL KRDMD KTLEV KUYAS MGROS MIATK OYAKC PASEU PETKM PGSUS SAHOL "
    "SASA SISE TAVHL TCELL THYAO TOASO TRALT TRMET TTKOM TUPRS TURSG ULKER "
    "VAKBN YKBNK".split()
)
BIST50_APR_JUN_2026 = frozenset(
    (BIST50_JUL_SEP_2026 - {"ECILC", "EFOR", "GLRMK", "KTLEV"})
    | {"ARCLK", "DOAS", "MAVI", "TSKB"}
)

UNIVERSE_META = {
    "VIOP_TEK_HISSE": {
        "description": "Borsa İstanbul VİOP sayfasındaki pay vadeli dayanak listesi",
        "as_of": "2026-08-27",
        "source_url": "https://www.borsaistanbul.com/piyasalar/viop/piyasa-isleyisi",
        "historical_membership": False,
        "lookahead_risk": True,
    },
    "SPOT_ACIGA_SATIS_BIST50": {
        "description": "Açığa satışın sınırlı olarak serbest olduğu BIST 50 üyeliği",
        "as_of": "2026-04-01..2026-09-30",
        "source_url": "https://www.borsaistanbul.com/piyasalar/pay-piyasasi/piyasa-isleyisi",
        "membership_sources": (
            "https://www.borsaistanbul.com/duyuru/15392/"
            "2026-yili-ikinci-uc-aylik-donemi-icin-bist-pay-endeksleri-kapsaminda-yer-alacak-paylar-belirlenmistir",
            "https://www.borsaistanbul.com/duyuru/15483/bist-pay-endeksleri-donemsel-degisiklikleri",
        ),
        "historical_membership": True,
        "lookahead_risk": False,
    },
}


def _prepare(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or getattr(df, "empty", True):
        return None
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out = out.loc[:, ~out.columns.duplicated()].copy()
    out.columns = [str(col).capitalize() for col in out.columns]
    if not {"Open", "High", "Low", "Close"}.issubset(out.columns):
        return None
    index = pd.to_datetime(out.index, errors="coerce")
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    out.index = index.normalize()
    out = out[~out.index.isna()]
    out = out[~out.index.duplicated(keep="last")].sort_index()
    for column in ("Open", "High", "Low", "Close"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    return out.dropna(subset=["Open", "High", "Low", "Close"])


def _symbol(value: Any) -> str:
    return str(value or "").upper().strip().replace(".IS", "")


def _read_events() -> tuple[pd.DataFrame, str, str, int]:
    if not DB.exists():
        raise RuntimeError(f"patron.db bulunamadı: {DB}")
    uri = f"file:{DB.resolve()}?mode=ro"
    placeholders = ",".join("?" for _ in SCAN_TYPES)
    with sqlite3.connect(uri, uri=True) as conn:
        bounds = conn.execute(
            f"SELECT MIN(scan_date), MAX(scan_date) FROM scan_signals "
            f"WHERE is_event_start=1 AND scan_type IN ({placeholders})",
            SCAN_TYPES,
        ).fetchone()
        first_date, last_date = bounds
        if not last_date:
            return pd.DataFrame(), "", "", 0
        events = pd.read_sql_query(
            f"""
            SELECT id, scan_date, scan_type, symbol, bias, category
            FROM scan_signals
            WHERE is_event_start=1
              AND scan_type IN ({placeholders})
              AND scan_date>=? AND scan_date<=?
            ORDER BY scan_date, scan_type, symbol, id
            """,
            conn,
            params=(*SCAN_TYPES, first_date, last_date),
        )
    raw_count = int(len(events))
    if events.empty:
        return events, str(first_date), str(last_date), raw_count
    events["scan_date"] = pd.to_datetime(events["scan_date"], errors="coerce").dt.normalize()
    events["symbol"] = events["symbol"].map(_symbol)
    events["scan_type"] = events["scan_type"].astype(str).str.strip()
    events = events.dropna(subset=["scan_date"])
    events = events[events["symbol"] != ""]
    events = events.drop_duplicates(
        subset=["scan_date", "scan_type", "symbol"], keep="last"
    ).reset_index(drop=True)
    return events, str(first_date), str(last_date), raw_count


def _bist50_members_on(signal_date: Any) -> frozenset[str]:
    date = pd.Timestamp(signal_date).normalize()
    if pd.Timestamp("2026-04-01") <= date <= pd.Timestamp("2026-06-30"):
        return BIST50_APR_JUN_2026
    if pd.Timestamp("2026-07-01") <= date <= pd.Timestamp("2026-09-30"):
        return BIST50_JUL_SEP_2026
    return frozenset()


def _in_universe(universe: str, symbol: str, signal_date: Any) -> bool:
    if universe == "VIOP_TEK_HISSE":
        return symbol in VIOP_PAY_FUTURES_20260827
    if universe == "SPOT_ACIGA_SATIS_BIST50":
        return symbol in _bist50_members_on(signal_date)
    raise ValueError(f"Bilinmeyen evren: {universe}")


def _asof_pos(index: pd.DatetimeIndex, date_value: Any) -> int | None:
    position = int(index.searchsorted(pd.Timestamp(date_value).normalize(), side="right")) - 1
    return position if position >= 0 else None


def _exact_pos(index: pd.DatetimeIndex, date_value: Any) -> int | None:
    position = int(index.searchsorted(pd.Timestamp(date_value).normalize(), side="left"))
    if position >= len(index) or index[position] != pd.Timestamp(date_value).normalize():
        return None
    return position


def _median(values: list[float]) -> float | None:
    return float(np.median(values)) if values else None


def _mean(values: list[float]) -> float | None:
    return float(np.mean(values)) if values else None


def _win_rate(values: list[float]) -> float | None:
    return float(sum(value > 0.0 for value in values) / len(values) * 100.0) if values else None


def _fmt(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}%"


def _fmt_number(value: float | None) -> str:
    return "—" if value is None else f"{float(value):.2f}"


def _status(n: int) -> str:
    if n == 0:
        return "VERİ YOK"
    return "YETERLİ ÖRNEKLEM" if n >= MIN_REGIME_N else "BELİRSİZ"


def _empty_cell() -> dict[str, list[float]]:
    return {"return": [], "adverse": []}


def _self_test() -> None:
    assert len(VIOP_PAY_FUTURES_20260827) == 47
    assert len(BIST50_JUL_SEP_2026) == len(BIST50_APR_JUN_2026) == 50
    assert "ECILC" not in BIST50_APR_JUN_2026 and "ECILC" in BIST50_JUL_SEP_2026
    assert "ARCLK" in BIST50_APR_JUN_2026 and "ARCLK" not in BIST50_JUL_SEP_2026
    short_close = np.asarray([105.0, 90.0])
    short_high = np.asarray([112.0, 96.0])
    assert np.allclose((short_close / 100.0 - 1.0) * -100.0, [-5.0, 10.0])
    assert np.allclose(np.minimum.accumulate((short_high / 100.0 - 1.0) * -100.0), [-12.0, -12.0])


def run() -> dict[str, Any]:
    events, first_date, last_date, raw_count = _read_events()
    if events.empty:
        raise RuntimeError("İstenen üç ayı taraması için is_event_start=1 olayı bulunamadı.")
    version = active_version_id()
    benchmark = _prepare(read_active("XU100.IS", version))
    if benchmark is None or benchmark.empty:
        raise RuntimeError("Aktif fiyat kasasında XU100 günlük OHLC bulunamadı.")
    regime_series = measurement_regime_series(benchmark)

    buckets: dict[tuple[str, str, int, str], dict[str, list[float]]] = defaultdict(_empty_cell)
    eligible_events: Counter[str] = Counter()
    valid_events: Counter[str] = Counter()
    status_counts: dict[str, Counter[str]] = defaultdict(Counter)
    event_rows: list[dict[str, Any]] = []
    data_cache: dict[str, pd.DataFrame | None] = {}

    for row in events.itertuples(index=False):
        symbol = _symbol(row.symbol)
        scan_date = pd.Timestamp(row.scan_date).normalize()
        for universe in UNIVERSE_META:
            if not _in_universe(universe, symbol, scan_date):
                continue
            eligible_events[universe] += 1
            if symbol not in data_cache:
                data_cache[symbol] = _prepare(read_active(symbol, version))
            stock = data_cache[symbol]
            if stock is None or stock.empty:
                status_counts[universe]["hisse_verisi_yok"] += 1
                continue
            benchmark_signal_pos = _asof_pos(benchmark.index, scan_date)
            if benchmark_signal_pos is None:
                status_counts[universe]["endeks_sinyal_gunu_yok"] += 1
                continue
            regime = regime_series.iloc[benchmark_signal_pos]
            if pd.isna(regime):
                status_counts[universe]["rejim_icin_50_seans_yok"] += 1
                continue
            # Tarama bias'i ne yazarsa yazsın bu laboratuvarın üç sinyali kısa
            # yönlü ölçülür; girişteki tavan kilidi de kısa yön için uygulanır.
            entry = resolve_next_open_entry(
                stock, scan_date, bias="bearish", apply_bist_limit=True, max_locked_sessions=3
            )
            entry_status = str(entry.get("status", "bilinmiyor"))
            status_counts[universe][entry_status] += 1
            if not entry_status.startswith("filled"):
                continue
            stock_entry = int(entry["entry_pos"])
            benchmark_entry = _exact_pos(benchmark.index, entry["entry_date"])
            if benchmark_entry is None:
                status_counts[universe]["endeks_giris_gunu_yok"] += 1
                continue
            valid_events[universe] += 1
            entry_price = float(entry["entry_price"])
            for horizon in HORIZONS:
                if stock_entry + horizon > len(stock):
                    status_counts[universe][f"T{horizon}_ileri_veri_yok"] += 1
                    continue
                window = stock.iloc[stock_entry : stock_entry + horizon]
                close_price = float(window["Close"].iloc[-1])
                adverse_path = (window["High"].to_numpy(dtype=float) / entry_price - 1.0) * -100.0
                close_return = (close_price / entry_price - 1.0) * -100.0
                adverse_return = float(np.min(adverse_path))
                key = (universe, str(row.scan_type), horizon, str(regime))
                buckets[key]["return"].append(float(close_return))
                buckets[key]["adverse"].append(adverse_return)
                event_rows.append(
                    {
                        "universe": universe,
                        "event_id": row.id,
                        "scan_date": scan_date.date().isoformat(),
                        "scan_type": str(row.scan_type),
                        "symbol": symbol,
                        "regime": str(regime),
                        "entry_date": entry["entry_date"],
                        "entry_price": entry_price,
                        "horizon_sessions": horizon,
                        "short_close_return": float(close_return),
                        "worst_adverse_return": adverse_return,
                    }
                )

    rows: list[dict[str, Any]] = []
    for universe in UNIVERSE_META:
        for scanner in SCAN_TYPES:
            for horizon in HORIZONS:
                for regime in (MEASUREMENT_REGIME_RISING, MEASUREMENT_REGIME_FALLING):
                    values = buckets[(universe, scanner, horizon, regime)]
                    returns, adverse = values["return"], values["adverse"]
                    n = len(returns)
                    rows.append(
                        {
                            "universe": universe,
                            "scan_type": scanner,
                            "horizon_sessions": horizon,
                            "regime": regime,
                            "n": n,
                            "status": _status(n),
                            "short_return_median": _median(returns),
                            "short_return_mean": _mean(returns),
                            "short_win_rate": _win_rate(returns),
                            "adverse_gt_10_rate": (
                                float(sum(value < -10.0 for value in adverse) / n * 100.0) if n else None
                            ),
                            "adverse_gt_20_rate": (
                                float(sum(value < -20.0 for value in adverse) / n * 100.0) if n else None
                            ),
                            "worst_single_adverse": min(adverse) if adverse else None,
                        }
                    )

    payload = {
        "meta": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "first_scan_date": first_date,
            "last_scan_date": last_date,
            "active_version": version,
            "scan_types": list(SCAN_TYPES),
            "horizons": list(HORIZONS),
            "regime_rule": MEASUREMENT_REGIME_RULE,
            "regime_window": MEASUREMENT_REGIME_WINDOW,
            "entry_rule": "resolve_next_open_entry(bias='bearish',apply_bist_limit=True,max_locked_sessions=3)",
            "dedup_rule": "is_event_start=1; unique(scan_date,scan_type,symbol)",
            "min_regime_n": MIN_REGIME_N,
            "return_definition": "short direction-adjusted Close return from executable next open",
            "adverse_definition": "minimum short return from High over fixed horizon",
            "thresholds_selected": False,
            "policy_changed": False,
        },
        "universes": {
            **UNIVERSE_META,
            "VIOP_TEK_HISSE": {**UNIVERSE_META["VIOP_TEK_HISSE"], "symbols": sorted(VIOP_PAY_FUTURES_20260827)},
            "SPOT_ACIGA_SATIS_BIST50": {
                **UNIVERSE_META["SPOT_ACIGA_SATIS_BIST50"],
                "apr_jun_symbols": sorted(BIST50_APR_JUN_2026),
                "jul_sep_symbols": sorted(BIST50_JUL_SEP_2026),
            },
        },
        "coverage": {
            "events_before_dedup": raw_count,
            "events_after_dedup": int(len(events)),
            "eligible_events": dict(eligible_events),
            "valid_entry_regime_events": dict(valid_events),
            "status_counts": {universe: dict(sorted(counts.items())) for universe, counts in status_counts.items()},
            "event_row_count": len(event_rows),
        },
        "rows": rows,
    }
    OUT_EVENTS.parent.mkdir(parents=True, exist_ok=True)
    with OUT_EVENTS.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=tuple(event_rows[0]) if event_rows else ())
        if event_rows:
            writer.writeheader()
            writer.writerows(event_rows)
    return payload


def _make_report(payload: dict[str, Any]) -> str:
    meta, coverage = payload["meta"], payload["coverage"]
    lines = [
        "# Short Taraması Fizibilitesi — Laboratuvar",
        "",
        "Bu rapor canlı sinyal, politika, rozet veya ekran değişikliği üretmez. Geçmiş olayların, işlem yapılabilir iki gerçek evrendeki kısa-yönlü dayanak pay sonucu ölçülmüştür.",
        "",
        "## Evren hükmü",
        "",
        "- VİOP: 27 Ağustos 2026 tarihli Borsa İstanbul sayfasındaki 47 pay vadeli dayanağı kullanıldı. Geçmiş sözleşme listesi bulunamadığından bu evrende ileriye bakma riski vardır.",
        "- Spot açığa satış: Borsa İstanbul'un güncel kuralında açığa satış, ilan edilen BIST 50 ile sınırlıdır; daha geniş ayrı bir spot açığa satış evreni yoktur. BIST 50 üyeliği Mayıs–Haziran ve Temmuz–Ağustos için resmî dönem değişiklikleriyle tarihsel kuruldu; ileriye bakma yoktur.",
        "",
        "## Mühürler",
        "",
        "- Olay: yalnız `is_event_start=1`; aynı tarih-tarama-hisse tekrarları tek olaydır.",
        "- Rejim: XU100 kapanışı SMA50 üstü/altı; yalnız karneyi böler, tarama filtresi değildir.",
        "- Giriş: ertesi işlem yapılabilir açılış; kısa yön için tavan kilidi uygulanır ve en çok üç kilitli seans atlanır.",
        "- Getiri: kısa yönlü kapanış getirisi; endeks alfası değildir. Pozitif sayı kısa pozisyon lehinedir.",
        "- Aleyhte uç: girişten sonraki sabit pencerenin en yüksek fiyatından hesaplanan, kısa pozisyonun en kötü anlık getirisi.",
        f"- Rejim hücresinde N < {meta['min_regime_n']} ise sonuç BELİRSİZ; hüküm üretilmez.",
        "- -%10 ve -%20 kuyruk oranları kullanıcının istediği sabit risk raporlama eşikleridir; sonuçtan seçilmedi.",
        "",
        "## Kapsam",
        "",
        f"- Olay başlangıcı: ham {coverage['events_before_dedup']:,} → tekilleştirilmiş {coverage['events_after_dedup']:,}",
        f"- Aktif fiyat kasası: `{meta['active_version']}` · dönem: {meta['first_scan_date']}–{meta['last_scan_date']}",
        f"- VİOP: evrene giren {coverage['eligible_events'].get('VIOP_TEK_HISSE', 0):,}, giriş+rejim geçerli {coverage['valid_entry_regime_events'].get('VIOP_TEK_HISSE', 0):,} olay.",
        f"- Spot BIST 50: evrene giren {coverage['eligible_events'].get('SPOT_ACIGA_SATIS_BIST50', 0):,}, giriş+rejim geçerli {coverage['valid_entry_regime_events'].get('SPOT_ACIGA_SATIS_BIST50', 0):,} olay.",
        "",
        "## Karne",
        "",
        "| Evren | Tarama | Vade | Rejim | N | Durum | Ortanca | Ortalama | İsabet | Aleyhte >%10 | Aleyhte >%20 | En kötü tek olay |",
        "|---|---|---:|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["rows"]:
        lines.append(
            f"| {row['universe']} | {row['scan_type']} | T+{row['horizon_sessions']} | {row['regime']} | {row['n']} | {row['status']} | "
            f"{_fmt(row['short_return_median'])} | {_fmt(row['short_return_mean'])} | {_fmt(row['short_win_rate'])} | "
            f"{_fmt(row['adverse_gt_10_rate'])} | {_fmt(row['adverse_gt_20_rate'])} | {_fmt(row['worst_single_adverse'])} |"
        )
    lines += [
        "",
        "## Evrensel sınırlar",
        "",
        "Bu, dayanak payın geçmiş performans fizibilitesidir. VİOP sözleşmesi ile pay arasındaki baz farkını, teminat etkisini; spotta da ödünç bulunurluğunu, ücretleri, güncel VBTS tedbirlerini ve işlem kaymasını kapsamaz. Bu nedenle yeterli örneklem çıksa bile canlı short listesi kararı değildir.",
        "",
        "## Ham olay kaydı",
        "",
        f"Her olay-vade satırı: `{OUT_EVENTS}`",
        "",
        "## Kaynaklar",
        "",
        "- VİOP pay vadeli dayanak grupları: https://www.borsaistanbul.com/piyasalar/viop/piyasa-isleyisi",
        "- Açığa satışın BIST 50 sınırlaması: https://www.borsaistanbul.com/piyasalar/pay-piyasasi/piyasa-isleyisi",
        "- BIST 50, 01.04–30.06.2026 dönem değişikliği: https://www.borsaistanbul.com/duyuru/15392/2026-yili-ikinci-uc-aylik-donemi-icin-bist-pay-endeksleri-kapsaminda-yer-alacak-paylar-belirlenmistir",
        "- BIST 50, 01.07–30.09.2026 dönem değişikliği: https://www.borsaistanbul.com/duyuru/15483/bist-pay-endeksleri-donemsel-degisiklikleri",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Kısa tarama gerçek evren fizibilitesi")
    parser.add_argument("--self-test", action="store_true", help="Saf hesap ve evren tutarlılığı testi")
    args = parser.parse_args()
    if args.self_test:
        _self_test()
        print("short_feasibility_lab self-test: OK")
        return
    payload = run()
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    OUT_MD.write_text(_make_report(payload), encoding="utf-8")
    print(f"JSON: {OUT_JSON}")
    print(f"RAPOR: {OUT_MD}")
    print(json.dumps(payload["coverage"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
