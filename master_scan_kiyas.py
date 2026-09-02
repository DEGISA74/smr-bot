# -*- coding: utf-8 -*-
"""İki Master Scan turunu sıfır toleransla karşılaştırır."""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "patron.db"
REPORT_DIR = ROOT / "logs"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _parse_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Tarih YYYY-AA-GG biçiminde olmalı: {value!r}") from exc


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"Kıyas veritabanı bulunamadı: {path}")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def _load_run_rows(connection: sqlite3.Connection, day: str, category: str) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT scan_type, row_count, category, recorded_at
        FROM scan_runs
        WHERE scan_date = ? AND trim(COALESCE(category, '')) = trim(?)
        ORDER BY scan_type
        """,
        (day, category),
    ).fetchall()
    return {
        str(row["scan_type"]): {
            "row_count": int(row["row_count"] or 0),
            "category": row["category"],
            "recorded_at": row["recorded_at"],
        }
        for row in rows
    }


def _canonical_symbol(value: Any) -> str:
    return str(value or "").strip().upper().replace(".IS", "")


def _load_signal_rows(
    connection: sqlite3.Connection, day: str, category: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT scan_type, symbol, score, entry_price, stop_level
        FROM scan_signals
        WHERE scan_date = ? AND trim(COALESCE(category, '')) = trim(?)
        ORDER BY scan_type, symbol
        """,
        (day, category),
    ).fetchall()
    signals: dict[tuple[str, str], dict[str, Any]] = {}
    duplicates: list[tuple[str, str]] = []
    for row in rows:
        key = (str(row["scan_type"]), _canonical_symbol(row["symbol"]))
        if key in signals:
            duplicates.append(key)
        signals[key] = {
            "score": row["score"],
            "entry_price": row["entry_price"],
            "stop_level": row["stop_level"],
        }
    if duplicates:
        joined = ", ".join(f"{scan}:{symbol}" for scan, symbol in duplicates[:10])
        suffix = "" if len(duplicates) <= 10 else f" (+{len(duplicates) - 10})"
        raise ValueError(f"Aynı turda yinelenen scan_type+symbol anahtarı: {joined}{suffix}")
    return signals


def _compare_loaded(
    old_day: str,
    new_day: str,
    category: str,
    old_runs: dict[str, dict[str, Any]],
    new_runs: dict[str, dict[str, Any]],
    old_signals: dict[tuple[str, str], dict[str, Any]],
    new_signals: dict[tuple[str, str], dict[str, Any]],
    *,
    comparison_mode: str = "iki_tarih",
    old_source: str | None = None,
    new_source: str | None = None,
) -> dict[str, Any]:
    old_types = set(old_runs)
    new_types = set(new_runs)
    missing_types = sorted(old_types - new_types)
    extra_types = sorted(new_types - old_types)

    row_count_diffs = [
        {
            "scan_type": scan_type,
            "old": old_runs[scan_type]["row_count"],
            "new": new_runs[scan_type]["row_count"],
        }
        for scan_type in sorted(old_types & new_types)
        if old_runs[scan_type]["row_count"] != new_runs[scan_type]["row_count"]
    ]

    old_symbols = {
        scan_type: sorted(symbol for current_type, symbol in old_signals if current_type == scan_type)
        for scan_type in old_types | new_types
    }
    new_symbols = {
        scan_type: sorted(symbol for current_type, symbol in new_signals if current_type == scan_type)
        for scan_type in old_types | new_types
    }
    symbol_diffs = []
    for scan_type in sorted(old_types | new_types):
        old_set = set(old_symbols[scan_type])
        new_set = set(new_symbols[scan_type])
        if old_set != new_set:
            symbol_diffs.append({
                "scan_type": scan_type,
                "missing_symbols": sorted(old_set - new_set),
                "extra_symbols": sorted(new_set - old_set),
            })

    numeric_diffs = []
    for key in sorted(set(old_signals) & set(new_signals)):
        scan_type, symbol = key
        for field in ("score", "entry_price", "stop_level"):
            old_value = old_signals[key][field]
            new_value = new_signals[key][field]
            if old_value != new_value:
                numeric_diffs.append({
                    "scan_type": scan_type,
                    "symbol": symbol,
                    "field": field,
                    "old": old_value,
                    "new": new_value,
                })

    passed = not (missing_types or extra_types or row_count_diffs or symbol_diffs or numeric_diffs)
    result = {
        "status": "AYNI" if passed else "FARK VAR",
        "old_date": old_day,
        "new_date": new_day,
        "category": category,
        "criteria": {
            "scan_type_set": "exact",
            "row_counts": "exact",
            "symbol_sets": "exact",
            "numeric_fields": {"score": 0, "entry_price": 0, "stop_level": 0},
        },
        "old_scan_type_count": len(old_types),
        "new_scan_type_count": len(new_types),
        "missing_scan_types": missing_types,
        "extra_scan_types": extra_types,
        "row_count_diffs": row_count_diffs,
        "symbol_diffs": symbol_diffs,
        "numeric_diffs": numeric_diffs,
    }
    if comparison_mode != "iki_tarih":
        result["comparison_mode"] = comparison_mode
        result["old_source"] = old_source or "yan-kayıt"
        result["new_source"] = new_source or "patron.db"
    return result


def compare(old_day: str, new_day: str, category: str, db_path: Path) -> dict[str, Any]:
    old_day = _parse_date(old_day)
    new_day = _parse_date(new_day)
    with _connect_readonly(db_path) as connection:
        old_runs = _load_run_rows(connection, old_day, category)
        new_runs = _load_run_rows(connection, new_day, category)
        old_signals = _load_signal_rows(connection, old_day, category)
        new_signals = _load_signal_rows(connection, new_day, category)
    return _compare_loaded(
        old_day, new_day, category,
        old_runs, new_runs, old_signals, new_signals,
    )


def _sidecar_number(value: Any, field: str, scan_type: str, symbol: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"Kuru yan-kayıt sayısal olmayan {field} içeriyor: {scan_type}/{symbol}"
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(
            f"Kuru yan-kayıt sonlu olmayan {field} içeriyor: {scan_type}/{symbol}"
        )
    return value


def _load_sidecar(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    if not path.exists():
        raise FileNotFoundError(f"Kuru yan-kayıt bulunamadı: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Kuru yan-kayıt okunamadı: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Kuru yan-kayıt JSON kökü nesne olmalı")
    if payload.get("schema_version") != 1:
        raise ValueError("Kuru yan-kayıt schema_version=1 olmalı")
    for field in ("run_at", "category", "engine_version"):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            raise ValueError(f"Kuru yan-kayıt alanı eksik/geçersiz: {field}")
    scan_results = payload.get("scan_results")
    if not isinstance(scan_results, dict):
        raise ValueError("Kuru yan-kayıt scan_results nesne olmalı")

    runs: dict[str, dict[str, Any]] = {}
    signals: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_scan_type, raw_result in scan_results.items():
        scan_type = str(raw_scan_type).strip()
        if not scan_type or not isinstance(raw_result, dict):
            raise ValueError(f"Kuru yan-kayıt tarama tipi kaydı geçersiz: {raw_scan_type!r}")
        row_count = raw_result.get("row_count")
        if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
            raise ValueError(f"Kuru yan-kayıt row_count geçersiz: {scan_type}")
        symbols = raw_result.get("semboller")
        if not isinstance(symbols, list):
            raise ValueError(f"Kuru yan-kayıt semboller listesi geçersiz: {scan_type}")
        runs[scan_type] = {
            "row_count": row_count,
            "category": payload["category"],
            "recorded_at": payload["run_at"],
        }
        for item in symbols:
            if not isinstance(item, dict):
                raise ValueError(f"Kuru yan-kayıt sembol satırı nesne olmalı: {scan_type}")
            symbol_raw = item.get("symbol")
            if not isinstance(symbol_raw, str) or not symbol_raw.strip():
                raise ValueError(f"Kuru yan-kayıt sembolü geçersiz: {scan_type}")
            symbol = _canonical_symbol(symbol_raw)
            key = (scan_type, symbol)
            if key in signals:
                raise ValueError(f"Kuru yan-kayıtta yinelenen scan_type+symbol: {scan_type}/{symbol}")
            signals[key] = {
                "score": _sidecar_number(item.get("score"), "score", scan_type, symbol),
                "entry_price": _sidecar_number(
                    item.get("entry_price"), "entry_price", scan_type, symbol
                ),
                "stop_level": _sidecar_number(
                    item.get("stop_level"), "stop_level", scan_type, symbol
                ),
            }
    return payload, runs, signals


def compare_sidecar(
    sidecar_path: Path, day: str, category: str | None, db_path: Path,
) -> dict[str, Any]:
    day = _parse_date(day)
    payload, sidecar_runs, sidecar_signals = _load_sidecar(sidecar_path)
    try:
        sidecar_day = datetime.fromisoformat(payload["run_at"]).date().isoformat()
    except ValueError as exc:
        raise ValueError("Kuru yan-kayıt run_at ISO tarih-saat biçiminde olmalı") from exc
    if sidecar_day != day:
        raise ValueError(
            f"Kuru yan-kayıt aynı gün olmalı: dosya={sidecar_day}, --tarih={day}"
        )
    effective_category = category if category is not None else payload["category"]
    with _connect_readonly(db_path) as connection:
        db_runs = _load_run_rows(connection, day, effective_category)
        db_signals = _load_signal_rows(connection, day, effective_category)
    result = _compare_loaded(
        day, day, effective_category,
        sidecar_runs, db_runs, sidecar_signals, db_signals,
        comparison_mode="kuru_dosya_vs_aynı_gün_db",
        old_source=f"kuru yan-kayıt: {sidecar_path}",
        new_source=f"patron.db: {day}",
    )
    result["sidecar_run_at"] = payload["run_at"]
    result["sidecar_engine_version"] = payload["engine_version"]
    result["sidecar_category"] = payload["category"]
    return result


def _markdown_report(result: dict[str, Any]) -> str:
    if result.get("comparison_mode") == "kuru_dosya_vs_aynı_gün_db":
        title = f"# Master Scan kıyas — kuru yan-kayıt ↔ patron.db — {result['new_date']}"
        source_lines = [
            f"- Kuru yan-kayıt: `{result['old_source']}`",
            f"- DB turu: `{result['new_source']}`",
            f"- Yan-kayıt koşu saati: `{result['sidecar_run_at']}`",
            f"- Yan-kayıt motor sürümü: `{result['sidecar_engine_version']}`",
        ]
    else:
        title = f"# Master Scan kıyas — {result['old_date']} → {result['new_date']}"
        source_lines = []
    lines = [
        title,
        "",
        f"**Sonuç: {result['status']}**",
        f"- Kategori: `{result['category']}`",
        *source_lines,
        f"- Eski tur tarama tipi: {result['old_scan_type_count']}",
        f"- Yeni tur tarama tipi: {result['new_scan_type_count']}",
        "- Ölçüt: tarama tipi, satır sayısı, sembol kümesi ve `score` / `entry_price` / `stop_level` için sıfır tolerans.",
        "",
        "## Tarama tipi kümesi",
        "",
        f"- Eksik: {', '.join(result['missing_scan_types']) or 'yok'}",
        f"- Fazla: {', '.join(result['extra_scan_types']) or 'yok'}",
        "",
        "## Satır sayısı farkları",
        "",
    ]
    row_diffs = result["row_count_diffs"]
    lines.extend(
        f"- `{item['scan_type']}`: {item['old']} → {item['new']}"
        for item in row_diffs
    )
    if not row_diffs:
        lines.append("- yok")
    lines.extend(["", "## Sembol kümesi farkları", ""])
    symbol_diffs = result["symbol_diffs"]
    for item in symbol_diffs:
        lines.append(
            f"- `{item['scan_type']}` — eksik: {', '.join(item['missing_symbols']) or 'yok'}; "
            f"fazla: {', '.join(item['extra_symbols']) or 'yok'}"
        )
    if not symbol_diffs:
        lines.append("- yok")
    lines.extend(["", "## Sayısal farklar", ""])
    numeric_diffs = result["numeric_diffs"]
    for item in numeric_diffs:
        lines.append(
            f"- `{item['scan_type']} / {item['symbol']} / {item['field']}`: "
            f"{item['old']!r} → {item['new']!r}"
        )
    if not numeric_diffs:
        lines.append("- yok")
    return "\n".join(lines) + "\n"


def _resolve_dates(args: argparse.Namespace) -> tuple[str, str]:
    old_day = args.old_date
    new_day = args.new_date
    if old_day is None and new_day is None and len(args.positional_dates) == 2:
        old_day, new_day = args.positional_dates
    if old_day is None or new_day is None:
        raise ValueError("İki tur tarihi gerekli: --eski-tarih YYYY-AA-GG --yeni-tarih YYYY-AA-GG")
    if args.positional_dates:
        raise ValueError("Tarihleri ya konumsal ya da --eski-tarih/--yeni-tarih ile verin; karıştırmayın")
    return _parse_date(old_day), _parse_date(new_day)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Master Scan turlarını sıfır toleransla kıyasla",
        epilog=(
            "Kullanım sırası:\n"
            "  1) Akşam ekran turu biter; patron.db yazılır.\n"
            "  2) python master_scan_kos.py --kategori \"BIST 500 \" --kuru\n"
            "     (kuru koşu ekran turundan SONRA yan-kayıt bırakır.)\n"
            "  3) python master_scan_kiyas.py --kuru-dosya "
            "logs/master_scan_kuru_YYYY-MM-DD.json --tarih YYYY-MM-DD"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("positional_dates", nargs="*", metavar="TARİH")
    parser.add_argument("--eski-tarih", "--eski", dest="old_date")
    parser.add_argument("--yeni-tarih", "--yeni", dest="new_date")
    parser.add_argument(
        "--kuru-dosya", type=Path,
        help="Kuru koşunun logs/master_scan_kuru_YYYY-MM-DD.json yan-kaydı",
    )
    parser.add_argument(
        "--tarih", help="--kuru-dosya için aynı günün YYYY-AA-GG tarihi",
    )
    parser.add_argument("--kategori", default=None)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)

    if args.kuru_dosya is not None:
        if args.positional_dates or args.old_date is not None or args.new_date is not None:
            raise ValueError("--kuru-dosya kipi iki-tarih kipiyle karıştırılamaz")
        if args.tarih is None:
            raise ValueError("--kuru-dosya ile --tarih YYYY-AA-GG birlikte gerekli")
        day = _parse_date(args.tarih)
        result = compare_sidecar(args.kuru_dosya, day, args.kategori, args.db)
        report_stem = f"master_scan_kiyas_kuru_{day}"
        print(f"{result['status']}: kuru yan-kayıt ↔ patron.db ({day})")
    else:
        if args.tarih is not None:
            raise ValueError("--tarih yalnızca --kuru-dosya kipiyle kullanılabilir")
        old_day, new_day = _resolve_dates(args)
        category = args.kategori if args.kategori is not None else "BIST 500 "
        result = compare(old_day, new_day, category, args.db)
        report_stem = f"master_scan_kiyas_{new_day}"
        print(f"{result['status']}: {old_day} → {new_day}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    markdown_path = REPORT_DIR / f"{report_stem}.md"
    json_path = REPORT_DIR / f"{report_stem}.json"
    markdown_path.write_text(_markdown_report(result), encoding="utf-8")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Rapor: {markdown_path}")
    print(f"JSON: {json_path}")
    return 0 if result["status"] == "AYNI" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Kıyas çalışmadı: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
