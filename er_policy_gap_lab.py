"""ER politika boşluğu için salt-okunur laboratuvar ölçümü.

Bu betik canlı tarama, politika, ekran, ``scan_signals`` veya VPS'e yazmaz.
Yalnızca dört kayıtsız Erken Radar senaryosunun sabit vadelerdeki sonucunu,
aynı aday giriş günlerindeki aktif BIST evreni tabanına göre raporlar.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from bist_data_store import active_version_id, load_manifest, read_active
from evidence import scanner_vade_metadata
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


ROOT = Path(__file__).resolve().parent
DB = ROOT / "patron.db"
OUT_JSON = ROOT / "logs" / "er_policy_gap_lab.json"
OUT_MD = ROOT / "logs" / "er_policy_gap_lab.md"
OUT_EVENTS = ROOT / "logs" / "er_policy_gap_lab_events.csv"
SCAN_TYPES = ("er_B2", "er_B3", "er_B4", "er_B10")
HORIZONS = (3, 5, 20)
MIN_REGIME_N = 150


def _prepare(frame: pd.DataFrame | None) -> pd.DataFrame | None:
    if frame is None or getattr(frame, "empty", True):
        return None
    out = frame.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out = out.loc[:, ~out.columns.duplicated()].copy()
    out.columns = [str(column).capitalize() for column in out.columns]
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


def _exact_pos(index: pd.DatetimeIndex, value: Any) -> int | None:
    target = pd.Timestamp(value).normalize()
    position = int(index.searchsorted(target, side="left"))
    if position >= len(index) or index[position] != target:
        return None
    return position


def _asof_pos(index: pd.DatetimeIndex, value: Any) -> int | None:
    target = pd.Timestamp(value).normalize()
    position = int(index.searchsorted(target, side="right")) - 1
    return position if position >= 0 else None


def _summary(values: list[float]) -> dict[str, float | int | None]:
    clean = np.asarray([value for value in values if np.isfinite(value)], dtype=float)
    if not len(clean):
        return {"n": 0, "median": None, "mean": None, "win_rate": None}
    return {
        "n": int(len(clean)),
        "median": float(np.median(clean)),
        "mean": float(np.mean(clean)),
        "win_rate": float(np.mean(clean > 0.0) * 100.0),
    }


def _status(n: int) -> str:
    if not n:
        return "VERİ YOK"
    return "YETERLİ ÖRNEKLEM" if n >= MIN_REGIME_N else "BELİRSİZ"


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"%{float(value):+.2f}"


def _fmt_rate(value: float | None) -> str:
    return "—" if value is None else f"%{float(value):.1f}"


def _read_events() -> tuple[pd.DataFrame, dict[str, Any]]:
    if not DB.exists():
        raise RuntimeError(f"patron.db bulunamadı: {DB}")
    placeholders = ",".join("?" for _ in SCAN_TYPES)
    uri = f"file:{DB.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = pd.read_sql_query(
            f"""
            SELECT id, scan_date, scan_type, symbol, bias, category
            FROM scan_signals
            WHERE is_event_start=1
              AND scan_type IN ({placeholders})
            ORDER BY scan_date, scan_type, symbol, id
            """,
            connection,
            params=SCAN_TYPES,
        )
        per_scanner = connection.execute(
            f"""
            SELECT scan_type, COUNT(*)
            FROM scan_signals
            WHERE is_event_start=1
              AND scan_type IN ({placeholders})
            GROUP BY scan_type
            ORDER BY scan_type
            """,
            SCAN_TYPES,
        ).fetchall()
    raw_count = int(len(rows))
    if rows.empty:
        return rows, {"raw_event_starts": raw_count, "sql_counts": dict(per_scanner)}
    rows["scan_date"] = pd.to_datetime(rows["scan_date"], errors="coerce").dt.normalize()
    rows["scan_type"] = rows["scan_type"].astype(str).str.strip()
    rows["symbol"] = rows["symbol"].map(_symbol)
    rows = rows.dropna(subset=["scan_date"])
    rows = rows[rows["symbol"] != ""]
    deduped = rows.drop_duplicates(
        subset=["scan_date", "scan_type", "symbol"], keep="last"
    ).reset_index(drop=True)
    return deduped, {
        "raw_event_starts": raw_count,
        "after_same_day_dedup": int(len(deduped)),
        "sql_counts": {str(scan_type): int(count) for scan_type, count in per_scanner},
        "first_scan_date": deduped["scan_date"].min().date().isoformat(),
        "last_scan_date": deduped["scan_date"].max().date().isoformat(),
        "sql": "WHERE is_event_start=1 AND scan_type IN ('er_B2','er_B3','er_B4','er_B10')",
    }


def _active_equities(version: str) -> list[str]:
    manifest = load_manifest(version) or {}
    symbols = {_symbol(value) for value in (manifest.get("symbols") or {})}
    # Aktif kasadaki XU100 yalnız rejim içindir; hisse evreni tabanına girmez.
    symbols.discard("XU100")
    return sorted(symbol for symbol in symbols if symbol)


def _paths_for_dates(
    stock: pd.DataFrame, entry_dates: set[pd.Timestamp]
) -> dict[pd.Timestamp, dict[int, float]]:
    """Bir hissenin her aday giriş günündeki sabit-vade ham kapanış sonucunu döndürür."""
    paths: dict[pd.Timestamp, dict[int, float]] = {}
    for entry_date in entry_dates:
        position = _exact_pos(stock.index, entry_date)
        if position is None:
            continue
        entry = float(stock["Open"].iloc[position])
        if not np.isfinite(entry) or entry <= 0:
            continue
        values: dict[int, float] = {}
        for horizon in HORIZONS:
            exit_position = position + horizon - 1
            if exit_position >= len(stock):
                continue
            close = float(stock["Close"].iloc[exit_position])
            if np.isfinite(close):
                values[horizon] = (close / entry - 1.0) * 100.0
        if values:
            paths[entry_date] = values
    return paths


def _self_test() -> None:
    index = pd.DatetimeIndex(["2026-08-03", "2026-08-04", "2026-08-05"])
    frame = pd.DataFrame(
        {"Open": [100.0, 101.0, 102.0], "High": [101.0, 102.0, 103.0],
         "Low": [99.0, 100.0, 101.0], "Close": [100.0, 102.0, 104.0]}, index=index
    )
    result = _paths_for_dates(frame, {pd.Timestamp("2026-08-03")})
    assert round(result[pd.Timestamp("2026-08-03")][3], 4) == 4.0
    assert _status(149) == "BELİRSİZ" and _status(150) == "YETERLİ ÖRNEKLEM"


def run() -> dict[str, Any]:
    _self_test()
    events, event_meta = _read_events()
    if events.empty:
        raise RuntimeError("Dört hedef senaryo için is_event_start=1 olayı bulunamadı.")

    version = active_version_id()
    benchmark = _prepare(read_active("XU100.IS", version))
    if benchmark is None or benchmark.empty:
        raise RuntimeError("Aktif fiyat kasasında XU100 günlük OHLC bulunamadı.")
    regimes = measurement_regime_series(benchmark)
    equities = _active_equities(version)
    if not equities:
        raise RuntimeError("Aktif manifestte BIST hisse evreni bulunamadı.")

    data_cache: dict[str, pd.DataFrame | None] = {}
    candidate: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    entry_dates: dict[tuple[str, str, int], set[pd.Timestamp]] = defaultdict(set)
    candidate_rows: list[dict[str, Any]] = []
    statuses: dict[str, Counter[str]] = defaultdict(Counter)

    for row in events.itertuples(index=False):
        scanner = str(row.scan_type)
        symbol = _symbol(row.symbol)
        if symbol not in data_cache:
            data_cache[symbol] = _prepare(read_active(symbol, version))
        stock = data_cache[symbol]
        if stock is None or stock.empty:
            statuses[scanner]["hisse_verisi_yok"] += 1
            continue
        signal_pos = _asof_pos(benchmark.index, row.scan_date)
        if signal_pos is None:
            statuses[scanner]["endeks_sinyal_gunu_yok"] += 1
            continue
        regime = regimes.iloc[signal_pos]
        if pd.isna(regime):
            statuses[scanner]["rejim_icin_50_seans_yok"] += 1
            continue
        entry = resolve_next_open_entry(
            stock,
            row.scan_date,
            bias=str(row.bias or "bullish"),
            apply_bist_limit=True,
            max_locked_sessions=3,
        )
        entry_status = str(entry.get("status", "bilinmiyor"))
        statuses[scanner][entry_status] += 1
        if not entry_status.startswith("filled"):
            continue
        entry_position = int(entry["entry_pos"])
        entry_date = pd.Timestamp(entry["entry_date"]).normalize()
        entry_price = float(entry["entry_price"])
        for horizon in HORIZONS:
            exit_position = entry_position + horizon - 1
            if exit_position >= len(stock):
                statuses[scanner][f"T{horizon}_olgunlasmadi"] += 1
                continue
            close = float(stock["Close"].iloc[exit_position])
            if not np.isfinite(close):
                statuses[scanner][f"T{horizon}_gecersiz_kapanis"] += 1
                continue
            value = (close / entry_price - 1.0) * 100.0
            key = (scanner, str(regime), horizon)
            candidate[key].append(value)
            entry_dates[key].add(entry_date)
            candidate_rows.append(
                {
                    "event_id": int(row.id),
                    "scan_date": pd.Timestamp(row.scan_date).date().isoformat(),
                    "scan_type": scanner,
                    "symbol": symbol,
                    "regime": str(regime),
                    "entry_date": entry_date.date().isoformat(),
                    "entry_price": entry_price,
                    "entry_status": entry_status,
                    "entry_delay": int(entry.get("entry_delay") or 0),
                    "locked_sessions": int(entry.get("locked_sessions") or 0),
                    "horizon_sessions": horizon,
                    "long_close_return": value,
                }
            )

    all_entry_dates = set().union(*entry_dates.values()) if entry_dates else set()
    baseline_by_date: dict[int, dict[pd.Timestamp, list[float]]] = {
        horizon: defaultdict(list) for horizon in HORIZONS
    }
    baseline_status = Counter()
    for number, symbol in enumerate(equities, start=1):
        if symbol not in data_cache:
            data_cache[symbol] = _prepare(read_active(symbol, version))
        stock = data_cache[symbol]
        if stock is None or stock.empty:
            baseline_status["hisse_verisi_yok"] += 1
            continue
        for date_value, values in _paths_for_dates(stock, all_entry_dates).items():
            for horizon, result in values.items():
                baseline_by_date[horizon][date_value].append(result)
        if number % 200 == 0:
            print(f"taban: {number}/{len(equities)} hisse", flush=True)

    rows: list[dict[str, Any]] = []
    for scanner in SCAN_TYPES:
        current_meta = scanner_vade_metadata(scanner)
        for horizon in HORIZONS:
            for regime in (MEASUREMENT_REGIME_RISING, MEASUREMENT_REGIME_FALLING):
                key = (scanner, regime, horizon)
                candidate_summary = _summary(candidate[key])
                dates = sorted(entry_dates[key])
                baseline_values = [
                    value
                    for date_value in dates
                    for value in baseline_by_date[horizon].get(date_value, [])
                ]
                baseline_summary = _summary(baseline_values)
                candidate_median = candidate_summary["median"]
                baseline_median = baseline_summary["median"]
                candidate_mean = candidate_summary["mean"]
                baseline_mean = baseline_summary["mean"]
                rows.append(
                    {
                        "scan_type": scanner,
                        "horizon_sessions": horizon,
                        "regime": regime,
                        "n": int(candidate_summary["n"]),
                        "status": _status(int(candidate_summary["n"])),
                        "unique_entry_dates": len(dates),
                        "candidate_median": candidate_median,
                        "candidate_mean": candidate_mean,
                        "candidate_win_rate": candidate_summary["win_rate"],
                        "baseline_n": int(baseline_summary["n"]),
                        "baseline_median": baseline_median,
                        "baseline_mean": baseline_mean,
                        "baseline_win_rate": baseline_summary["win_rate"],
                        "median_vs_baseline": (
                            float(candidate_median - baseline_median)
                            if candidate_median is not None and baseline_median is not None else None
                        ),
                        "mean_vs_baseline": (
                            float(candidate_mean - baseline_mean)
                            if candidate_mean is not None and baseline_mean is not None else None
                        ),
                        "current_masa": current_meta.get("masa"),
                        "current_horizon": current_meta.get("vade_gun"),
                        "current_durum": current_meta.get("durum"),
                    }
                )

    payload: dict[str, Any] = {
        "meta": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "active_version": version,
            "scan_types": list(SCAN_TYPES),
            "horizons": list(HORIZONS),
            "min_regime_n": MIN_REGIME_N,
            "regime_rule": MEASUREMENT_REGIME_RULE,
            "regime_window": MEASUREMENT_REGIME_WINDOW,
            "entry_rule": "resolve_next_open_entry(bias=signal_bias, apply_bist_limit=True, max_locked_sessions=3)",
            "dedup_rule": "is_event_start=1; unique(scan_date, scan_type, symbol)",
            "baseline_definition": (
                "İş A ile aynı seçicilik tabanı: adayın gerçek giriş günlerinde aktif manifestteki "
                "her BIST hissesi o günün açılışından sabit vadeyle tutulur. Bu işlem simülasyonu "
                "değil, aynı gün kesitinde taramanın rastgele hisse seçiminden üstün olup olmadığını ölçer."
            ),
            "policy_changed": False,
            "live_files_changed": False,
        },
        "event_query": event_meta,
        "coverage": {
            "active_equity_count": len(equities),
            "candidate_rows": len(candidate_rows),
            "candidate_statuses": {key: dict(sorted(value.items())) for key, value in statuses.items()},
            "baseline_statuses": dict(sorted(baseline_status.items())),
        },
        "rows": rows,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with OUT_EVENTS.open("w", newline="", encoding="utf-8") as file:
        if candidate_rows:
            writer = csv.DictWriter(file, fieldnames=list(candidate_rows[0]))
            writer.writeheader()
            writer.writerows(candidate_rows)
    return payload


def _report(payload: dict[str, Any]) -> str:
    meta = payload["meta"]
    events = payload["event_query"]
    coverage = payload["coverage"]
    lines = [
        "# ER Politika Boşluğu — Laboratuvar Ölçümü",
        "",
        "Bu rapor politika, rozet, ekran, canlı tarama veya veritabanı yazımını değiştirmez.",
        "",
        "## Mühürler",
        "",
        "- Olaylar yalnız `is_event_start=1`; aynı tarih/tarama/hisse tekrarı tekilleştirildi.",
        "- Rejim, XU100 kapanışının 50 seans ortalamasına göre üstü/altı; tarama filtresi değildir.",
        "- Giriş, ertesi işlem yapılabilir açılış; tavan kilidi uygulanır ve en çok üç kilitli seans atlanır.",
        "- Vade sonuç bakılmadan sabit seçildi: T+3, T+5, T+20.",
        f"- Bir rejim hücresi N < {meta['min_regime_n']} ise BELİRSİZ; politika hükmü üretilmez.",
        "- Taban, İş A ile aynı seçicilik denetimidir: adayın gerçek giriş gününde aktif kasadaki tüm BIST hisseleri aynı sabit vadede karşılaştırılır. Taban işlem uygulanabilirliği değil, hisse seçiminin kesitsel kontrolüdür.",
        "",
        "## Sorgu ve kapsam",
        "",
        f"- SQL: `{events.get('sql')}`",
        f"- Olay başlangıcı: ham {events.get('raw_event_starts', 0):,} → aynı gün tekilleştirilmiş {events.get('after_same_day_dedup', 0):,}",
        f"- Dönem: {events.get('first_scan_date')}–{events.get('last_scan_date')} · aktif kasa: `{meta['active_version']}`",
        f"- Taban evreni: aktif manifestte {coverage['active_equity_count']:,} BIST hissesi.",
        f"- Uygun aday-vade satırı: {coverage['candidate_rows']:,}",
        "",
        "## Karne",
        "",
        "Pozitif `fark`, taramanın aynı giriş günlerindeki evren tabanının üstünde kaldığını gösterir. Bu işaret tek başına politika hükmü değildir; N eşiği zorunludur.",
        "",
        "| Tarama | Vade | Rejim | N | Durum | Aday ort. | Aday ortanca | Aday isabet | Taban N | Taban ort. | Taban ortanca | Taban isabet | Ort. fark | Ortanca fark | Gün |",
        "|---|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["rows"]:
        lines.append(
            f"| {row['scan_type']} | T+{row['horizon_sessions']} | {row['regime']} | {row['n']} | {row['status']} | "
            f"{_fmt(row['candidate_mean'])} | {_fmt(row['candidate_median'])} | {_fmt_rate(row['candidate_win_rate'])} | "
            f"{row['baseline_n']} | {_fmt(row['baseline_mean'])} | {_fmt(row['baseline_median'])} | {_fmt_rate(row['baseline_win_rate'])} | "
            f"{_fmt(row['mean_vs_baseline'])} | {_fmt(row['median_vs_baseline'])} | {row['unique_entry_dates']} |"
        )
    lines.extend([
        "",
        "## Mevcut varsayılanın okuması",
        "",
        "Dört senaryonun da mevcut kod yolu `KATALOG · T+20 GÖZLEM · VADE BEKLİYOR` varsayılanına düşer. Bu bir karar vadesi değil, sinyalin sonsuza kadar taşınmaması için genel son kullanma sınırıdır. Yukarıdaki N eşiği geçilmeden bu geçici katalog yeri karar masasına çevrilmemelidir.",
        "",
        "## Sınır",
        "",
        "Bu ölçüm Gold Mine'ın eski `ideal_day` puanını kullanmaz. Eski puanın herhangi bir hücresi, sabit vade ve evren tabanı ile yeniden onaylanmış sayılmaz.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    payload = run()
    OUT_MD.write_text(_report(payload), encoding="utf-8")
    print(f"Rapor: {OUT_MD}")
    print(f"Veri:  {OUT_JSON}")
    print(f"Olay:  {OUT_EVENTS}")
    for row in payload["rows"]:
        print(
            f"{row['scan_type']} T+{row['horizon_sessions']} {row['regime']}: "
            f"N={row['n']} {row['status']} ortanca fark={_fmt(row['median_vs_baseline'])}"
        )


if __name__ == "__main__":
    main()
