#!/usr/bin/env python3
"""V3 Yüksek Getiri — AKŞAM SETTLE (karne doldurucu).

Sorun: canlı sabah botu (yuksek_getiri_telegram_v3.py) listeyi 09:40'ta yayınlar ve
aynı anda "dünkü listeyi getirisiyle ölç" adımını da çalıştırır. Ama sabah saatinde
o günün kapanışı henüz oluşmadığından ölçüm HEP boş döner → patron3.db.results 4 Ağustos'tan
beri donmuştu (yalnızca tek gün settle olmuştu).

Bu iş AKŞAM (kapanış + veri güncellemesi sonrası) çalışır. Gönderim botuna DOKUNMAZ:
sadece patron3.db'de yayınlanmış (published=1) ama sonucu eksik olan tüm günleri okur,
ertesi seansın gün-içi zirvesine/kapanışına göre getiriyi yazar (backfill dahil).

Mevcut test edilmiş mantığı birebir yeniden kullanır:
  - _evaluate_previous / _result_rows  → yuksek_getiri_telegram_v3.py
  - settle_results (idempotent + v3 portföyünü yeniden kurar) → patron3_getiri_db.py
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from patron3_getiri_db import settle_results
from yuksek_getiri_telegram_v3 import _evaluate_previous, _result_rows

BASE = Path(__file__).resolve().parent


def _unsettled_days(db_path: str, engine: str) -> list[tuple[str, list[sqlite3.Row]]]:
    """Sonucu eksik (results < candidates) yayınlanmış günleri eskiden yeniye döndürür."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        groups = con.execute(
            """
            SELECT c.signal_date AS signal_date,
                   COUNT(*) AS n_cand,
                   SUM(CASE WHEN r.symbol IS NOT NULL THEN 1 ELSE 0 END) AS n_res
            FROM candidates c
            LEFT JOIN results r
              ON r.engine = c.engine AND r.signal_date = c.signal_date AND r.symbol = c.symbol
            WHERE c.engine = ? AND c.published = 1
            GROUP BY c.signal_date
            HAVING n_res < n_cand
            ORDER BY c.signal_date
            """,
            (engine,),
        ).fetchall()
        out: list[tuple[str, list[sqlite3.Row]]] = []
        for g in groups:
            cands = con.execute(
                "SELECT symbol, rank, probability_pct FROM candidates "
                "WHERE engine = ? AND signal_date = ? AND published = 1 ORDER BY rank",
                (engine, g["signal_date"]),
            ).fetchall()
            out.append((str(g["signal_date"]), cands))
        return out
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="V3 yüksek getiri akşam settle / karne doldurucu")
    ap.add_argument("--db", default=str(BASE / "patron3.db"))
    ap.add_argument("--engine", default="v3")
    ap.add_argument("--data-dir", default=str(BASE / "veriler"))
    ap.add_argument("--dry-run", action="store_true", help="Yazmaz; sadece ne settle edileceğini gösterir")
    args = ap.parse_args()

    data_dir = Path(args.data_dir).resolve()
    todo = _unsettled_days(args.db, args.engine)
    if not todo:
        print("Settle edilecek gün yok — karne güncel.")
        return 0

    settled = pending = 0
    for signal_date, cands in todo:
        state = {
            "as_of": signal_date,
            "list": [
                {
                    "ticker": c["symbol"],
                    "rank": c["rank"] if c["rank"] is not None else 999,
                    "olasilik_pct": c["probability_pct"] if c["probability_pct"] is not None else 0.0,
                }
                for c in cands
            ],
        }
        previous, evaluation_date = _evaluate_previous(state, data_dir)
        if not previous or evaluation_date is None:
            print(f"  {signal_date}: bekliyor (ertesi seans kapanışı henüz yok / veri eksik)")
            pending += 1
            continue

        best = max(previous, key=lambda r: r["return_pct"])
        avg = sum(r["return_pct"] for r in previous) / len(previous)
        tag = "[DRY]" if args.dry_run else "yazıldı"
        print(
            f"  {signal_date} → {evaluation_date.date()}: {len(previous)}/{len(cands)} hisse {tag} "
            f"| ort {avg:+.1f}%  en iyi {best['ticker']} {best['return_pct']:+.1f}%"
        )
        if not args.dry_run:
            settle_results(args.db, args.engine, signal_date, evaluation_date, _result_rows(previous))
        settled += 1

    print(f"BİTTİ: {settled} gün settle edildi, {pending} gün bekliyor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
