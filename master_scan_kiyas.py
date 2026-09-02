# -*- coding: utf-8 -*-
"""İki Master Scan turunu sıfır toleransla karşılaştırır."""
from __future__ import annotations

import argparse
import json
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


def compare(old_day: str, new_day: str, category: str, db_path: Path) -> dict[str, Any]:
    old_day = _parse_date(old_day)
    new_day = _parse_date(new_day)
    with _connect_readonly(db_path) as connection:
        old_runs = _load_run_rows(connection, old_day, category)
        new_runs = _load_run_rows(connection, new_day, category)
        old_signals = _load_signal_rows(connection, old_day, category)
        new_signals = _load_signal_rows(connection, new_day, category)

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
    return {
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


def _markdown_report(result: dict[str, Any]) -> str:
    lines = [
        f"# Master Scan kıyas — {result['old_date']} → {result['new_date']}",
        "",
        f"**Sonuç: {result['status']}**",
        f"- Kategori: `{result['category']}`",
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
    parser = argparse.ArgumentParser(description="İki Master Scan turunu kıyasla")
    parser.add_argument("positional_dates", nargs="*", metavar="TARİH")
    parser.add_argument("--eski-tarih", "--eski", dest="old_date")
    parser.add_argument("--yeni-tarih", "--yeni", dest="new_date")
    parser.add_argument("--kategori", default="BIST 500 ")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    old_day, new_day = _resolve_dates(args)
    result = compare(old_day, new_day, args.kategori, args.db)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    markdown_path = REPORT_DIR / f"master_scan_kiyas_{new_day}.md"
    json_path = REPORT_DIR / f"master_scan_kiyas_{new_day}.json"
    markdown_path.write_text(_markdown_report(result), encoding="utf-8")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{result['status']}: {old_day} → {new_day}")
    print(f"Rapor: {markdown_path}")
    print(f"JSON: {json_path}")
    return 0 if result["status"] == "AYNI" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Kıyas çalışmadı: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
