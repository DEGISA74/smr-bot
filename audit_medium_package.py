# -*- coding: utf-8 -*-
"""Temel iskelet paketinin veritabanı son-kontrolü."""

import json
import sqlite3
from pathlib import Path


def audit(db_path="patron.db"):
    conn = sqlite3.connect(str(Path(db_path)))
    out = {}
    out["signals"] = conn.execute("SELECT COUNT(*) FROM scan_signals").fetchone()[0]
    out["events"] = conn.execute(
        "SELECT COUNT(*) FROM scan_signals WHERE is_event_start=1"
    ).fetchone()[0]
    out["continuations"] = conn.execute(
        "SELECT COUNT(*) FROM scan_signals WHERE is_event_start=0"
    ).fetchone()[0]
    out["missing_lifecycle"] = conn.execute(
        "SELECT COUNT(*) FROM scan_signals WHERE event_id IS NULL "
        "OR event_start_date IS NULL OR event_day IS NULL OR is_event_start IS NULL"
    ).fetchone()[0]
    out["scan_runs"] = conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0]
    out["family_overlap_symbol_days"] = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT symbol,scan_date
            FROM scan_signals
            WHERE is_event_start=1
              AND scan_type IN ('er_B11','er_C5','zirve_sikisma')
            GROUP BY symbol,scan_date HAVING COUNT(*)>1
        )
        """
    ).fetchone()[0]
    for table in ("signal_results", "signal_returns"):
        exists = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if exists else 0
        out[f"{table}_on_continuations"] = (
            conn.execute(
                f"SELECT COUNT(*) FROM {table} r JOIN scan_signals s ON s.id=r.signal_id "
                "WHERE s.is_event_start=0"
            ).fetchone()[0]
            if exists else 0
        )
    if out["signal_results"]:
        out["entry_status"] = dict(
            conn.execute(
                "SELECT COALESCE(entry_status,'NULL'),COUNT(*) FROM signal_results "
                "GROUP BY COALESCE(entry_status,'NULL')"
            ).fetchall()
        )
        out["entry_gap"] = conn.execute(
            "SELECT ROUND(AVG(entry_gap_pct),3),ROUND(MAX(ABS(entry_gap_pct)),3) "
            "FROM signal_results WHERE entry_gap_pct IS NOT NULL"
        ).fetchone()
        out["large_entry_gaps"] = conn.execute(
            """
            SELECT r.symbol,r.signal_date,r.entry_date,ROUND(r.entry_gap_pct,2),
                   s.category,s.scan_type,r.entry_status
            FROM signal_results r
            JOIN scan_signals s ON s.id=r.signal_id
            WHERE ABS(r.entry_gap_pct)>=15
            ORDER BY ABS(r.entry_gap_pct) DESC LIMIT 25
            """
        ).fetchall()
    conn.close()
    out["passed"] = (
        out["signals"] == out["events"] + out["continuations"]
        and out["missing_lifecycle"] == 0
        and out["signal_results_on_continuations"] == 0
        and out["signal_returns_on_continuations"] == 0
    )
    return out


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
