# -*- coding: utf-8 -*-
"""Günlük tarama satırlarını bağımsız olaylara dönüştürür ve eski giriş ölçümlerini temizler."""

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from signal_policy import rebuild_event_metadata


def migrate(db_path: Path, apply: bool = False):
    if not db_path.exists() or not db_path.is_file():
        raise FileNotFoundError(f"Veritabanı bulunamadı: {db_path}")
    db_path = db_path.resolve()
    conn = sqlite3.connect(str(db_path), timeout=30)
    before = conn.execute("SELECT COUNT(*) FROM scan_signals").fetchone()[0]
    existing_results = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='signal_results'"
    ).fetchone()[0]
    existing_returns = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='signal_returns'"
    ).fetchone()[0]
    if not apply:
        conn.close()
        return {
            "mode": "dry-run",
            "scan_rows": before,
            "will_rebuild_lifecycle": before,
            "will_clear_signal_results": bool(existing_results),
            "will_clear_signal_returns": bool(existing_returns),
        }
    conn.close()

    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{db_path.stem}_pre_medium_{stamp}{db_path.suffix}"
    shutil.copy2(db_path, backup_path)

    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE")
        lifecycle = rebuild_event_metadata(conn)
        cleared_results = 0
        cleared_returns = 0
        if existing_results:
            cleared_results = conn.execute("SELECT COUNT(*) FROM signal_results").fetchone()[0]
            conn.execute("DELETE FROM signal_results")
        if existing_returns:
            cleared_returns = conn.execute("SELECT COUNT(*) FROM signal_returns").fetchone()[0]
            conn.execute("DELETE FROM signal_returns")
        null_lifecycle = conn.execute(
            "SELECT COUNT(*) FROM scan_signals "
            "WHERE event_id IS NULL OR event_start_date IS NULL "
            "OR event_day IS NULL OR is_event_start IS NULL"
        ).fetchone()[0]
        if null_lifecycle:
            raise RuntimeError(f"Yaşam döngüsü eksik kalan satır: {null_lifecycle}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "mode": "applied",
        "backup": str(backup_path),
        **lifecycle,
        "cleared_signal_results": cleared_results,
        "cleared_signal_returns": cleared_returns,
        "null_lifecycle_rows": null_lifecycle,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="patron.db")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(migrate(Path(args.db), apply=args.apply))
