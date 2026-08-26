# -*- coding: utf-8 -*-
"""Ekrandan bağımsız gece bekçisi: yedek, eksik getiri onarımı ve günlük karne."""
from __future__ import annotations

import argparse
import json
import sys

from patron_db_guard import create_consistent_backup, write_daily_karne


def run(max_batches: int) -> dict:
    # Hesap/sinyal üretmez. Sadece geçmişte eksik kalmış forward-getiri satırlarını
    # INSERT OR IGNORE ile tamamlar; mevcut sonuçları asla güncellemez veya silmez.
    from scan_pipeline import backfill_signal_returns

    backup = create_consistent_backup("watchdog")
    batches = []
    for _ in range(max(0, int(max_batches))):
        filled, skipped = backfill_signal_returns()
        batches.append({"filled": int(filled), "skipped": int(skipped)})
        if filled == 0:
            break
    report = write_daily_karne("BIST 500")
    return {
        "backup": str(backup),
        "return_repair": batches,
        "karne_ok": bool(report["ok"]),
        "issues": report["issues"],
        "report": "logs/master_scan_karne_latest.json",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--max-batches", type=int, default=30)
    args = parser.parse_args()
    if args.check_only:
        result = {"report": write_daily_karne("BIST 500")}
    else:
        result = run(args.max_batches)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
