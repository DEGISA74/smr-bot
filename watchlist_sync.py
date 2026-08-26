#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Yerel favori sembollerini küçük bir paketle VPS watchlist'ine eşitler.

Bu araç yalnız ``watchlist.symbol`` satırlarını okur/yazar. Patron veritabanının
başka hiçbir tablosunu taşımaz veya değiştirmez.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = 1
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.=\-^]{1,32}$")


def _normalise_symbols(raw_symbols: object) -> list[str]:
    if not isinstance(raw_symbols, list):
        raise ValueError("Favori paketi sembol listesi içermiyor.")
    symbols: set[str] = set()
    for raw_symbol in raw_symbols:
        if not isinstance(raw_symbol, str):
            raise ValueError("Favori listesinde metin olmayan değer var.")
        symbol = raw_symbol.strip().upper()
        if not symbol or not SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError(f"Geçersiz favori sembolü: {raw_symbol!r}")
        symbols.add(symbol)
    return sorted(symbols)


def export_watchlist(db_path: Path, output_path: Path) -> dict:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        rows = conn.execute("SELECT symbol FROM watchlist ORDER BY symbol").fetchall()
    finally:
        conn.close()

    symbols = _normalise_symbols([row[0] for row in rows])
    payload = {
        "schema": SCHEMA,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + f".{os.getpid()}.tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary_path, output_path)
    return {"symbols": len(symbols), "output": str(output_path)}


def apply_watchlist(db_path: Path, input_path: Path) -> dict:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA:
        raise ValueError("Desteklenmeyen favori paketi sürümü.")
    desired = set(_normalise_symbols(payload.get("symbols")))

    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = {str(row[0]).strip().upper()
                    for row in conn.execute("SELECT symbol FROM watchlist").fetchall()}
        to_remove = sorted(existing - desired)
        to_add = sorted(desired - existing)
        if to_remove:
            placeholders = ",".join("?" for _ in to_remove)
            conn.execute(f"DELETE FROM watchlist WHERE symbol IN ({placeholders})", to_remove)
        if to_add:
            conn.executemany("INSERT INTO watchlist (symbol) VALUES (?)", [(symbol,) for symbol in to_add])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"symbols": len(desired), "added": to_add, "removed": to_remove}


def main() -> int:
    parser = argparse.ArgumentParser(description="Yalnız favori sembollerini eşitler.")
    parser.add_argument("mode", choices=("export", "apply"))
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--file", required=True, type=Path)
    args = parser.parse_args()

    result = (export_watchlist(args.db, args.file) if args.mode == "export"
              else apply_watchlist(args.db, args.file))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
