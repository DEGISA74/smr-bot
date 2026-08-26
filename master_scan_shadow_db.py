# -*- coding: utf-8 -*-
"""VPS gölge Master Scan için canlı DB'ye dokunmadan çalışma alanı hazırlar."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pytz


ROOT = Path(__file__).resolve().parent
TZ_ISTANBUL = pytz.timezone("Europe/Istanbul")


def _now() -> datetime:
    return datetime.now(TZ_ISTANBUL)


def prepare(source: Path, destination: Path) -> dict:
    """SQLite'ın kendi snapshot mekanizmasıyla yeni gölge DB üretir.

    Kaynak dosya açılır ama değiştirilmez; hedef tamamen yazılıp atomik olarak
    yer değiştirmeden eski gölge DB de korunur.
    """
    if not source.exists():
        raise FileNotFoundError(f"kaynak DB yok: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        temporary.unlink(missing_ok=True)
        source_conn = sqlite3.connect(source, timeout=60)
        target_conn = sqlite3.connect(temporary)
        try:
            source_conn.backup(target_conn)
            target_conn.execute(
                """
                CREATE TABLE IF NOT EXISTS master_scan_shadow_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    source_db TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT
                )
                """
            )
            target_conn.execute(
                "INSERT INTO master_scan_shadow_jobs(created_at, source_db, status, detail) VALUES(?,?,?,?)",
                (_now().isoformat(), str(source), "prepared", "Henüz hesap/sinyal çalıştırılmadı"),
            )
            target_conn.commit()
            quick_check = target_conn.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            target_conn.close()
            source_conn.close()
        if quick_check != "ok":
            raise RuntimeError(f"gölge DB bütünlük kontrolü geçmedi: {quick_check}")
        os.replace(temporary, destination)
        return {
            "prepared_at": _now().isoformat(),
            "source": str(source),
            "destination": str(destination),
            "quick_check": quick_check,
            "bytes": destination.stat().st_size,
        }
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "patron.db")
    parser.add_argument("--destination", type=Path, default=ROOT / "patron_shadow.db")
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.destination), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
