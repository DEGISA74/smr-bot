# -*- coding: utf-8 -*-
"""Giriş seansındaki BIST kurumsal işlem kırılmalarını 'alındı' sayımından çıkarır."""

import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path


def reconcile(db_path="patron.db", json_path="backtest_results.json"):
    db_path = Path(db_path)
    json_path = Path(json_path)
    conn = sqlite3.connect(str(db_path), timeout=30)
    rows = conn.execute(
        """
        SELECT r.signal_id,s.scan_type
        FROM signal_results r
        JOIN scan_signals s ON s.id=r.signal_id
        WHERE UPPER(COALESCE(s.category,'')) LIKE '%BIST%'
          AND ABS(r.entry_gap_pct)>=15
          AND r.ret_5g IS NULL AND r.ret_10g IS NULL AND r.ret_20g IS NULL
          AND r.entry_status LIKE 'filled%'
        """
    ).fetchall()
    ids = [int(r[0]) for r in rows]
    by_type = Counter(r[1] for r in rows)
    if ids:
        conn.executemany(
            "UPDATE signal_results SET entry_status='excluded_corporate_action' WHERE signal_id=?",
            [(x,) for x in ids],
        )
        conn.executemany("DELETE FROM signal_returns WHERE signal_id=?", [(x,) for x in ids])
        conn.commit()
    status_counts = dict(
        conn.execute(
            "SELECT entry_status,COUNT(*) FROM signal_results GROUP BY entry_status"
        ).fetchall()
    )
    avg_gap = conn.execute(
        "SELECT ROUND(AVG(entry_gap_pct),3) FROM signal_results "
        "WHERE entry_status LIKE 'filled%' AND entry_gap_pct IS NOT NULL"
    ).fetchone()[0]
    conn.close()

    backup = None
    if json_path.exists() and ids:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = json_path.with_name(f"{json_path.stem}.pre_reconcile_{stamp}{json_path.suffix}")
        shutil.copy2(json_path, backup)
        data = json.loads(json_path.read_text(encoding="utf-8"))
        meta = data.setdefault("eval_meta", {})
        meta["entry_status_counts"] = status_counts
        meta["evaluated"] = max(0, int(meta.get("evaluated", 0)) - len(ids))
        meta["avg_entry_gap_pct"] = avg_gap
        for row in data.get("summary", []):
            n = by_type.get(row.get("scan_type"), 0)
            if n:
                row["total_signals"] = max(0, int(row.get("total_signals", 0)) - n)
        json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return {
        "excluded_ids": ids,
        "excluded_by_scan_type": dict(by_type),
        "entry_status_counts": status_counts,
        "avg_filled_gap_pct": avg_gap,
        "json_backup": str(backup) if backup else None,
    }


if __name__ == "__main__":
    print(json.dumps(reconcile(), ensure_ascii=False, indent=2))
