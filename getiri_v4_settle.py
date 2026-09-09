#!/usr/bin/env python3
"""V4 Tarama Motoru — AKŞAM ARŞİV + SETTLE (karne defteri).

V4 (yalnızca yönetici gölge botu) her gün listesini yuksek_getiri_v4_state.json'a yazar
ama ÜZERİNE yazar (geçmiş tutulmaz) ve sonucu hiçbir yere kaydetmez. Bu iş, canlı V4 botuna
HİÇ DOKUNMADAN, iki işi yapar:
  1) O anki v4_state.json'daki listeyi patron4.db'ye arşivler (idempotent, as_of anahtarı).
  2) Ertesi seansın gün-içi zirvesine/kapanışına göre getiriyi yazar (final barlarla).

Not: Geçmiş liste tutulmadığı için backfill YOK — kayıt bugünden itibaren birikir.
Getiri hesabı V4'ün kendi _previous_results mantığını birebir yeniden kullanır.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from yuksek_getiri_telegram_v4 import _previous_results

BASE = Path(__file__).resolve().parent


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS v4_candidates(
            as_of       TEXT NOT NULL,
            ticker      TEXT NOT NULL,
            rank        INTEGER,
            prob        REAL,
            close       REAL,
            stage       TEXT,
            archived_at TEXT,
            PRIMARY KEY(as_of, ticker)
        );
        CREATE TABLE IF NOT EXISTS v4_results(
            as_of           TEXT NOT NULL,
            evaluation_date TEXT,
            ticker          TEXT NOT NULL,
            high_ret_pct    REAL,
            close_ret_pct   REAL,
            settled_at      TEXT,
            PRIMARY KEY(as_of, ticker)
        );
        """
    )


def _archive_state(con: sqlite3.Connection, state_path: Path) -> str | None:
    """v4_state.json'daki güncel listeyi idempotent arşivler; as_of döndürür."""
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    as_of = str(state.get("as_of") or "").strip()
    rows = state.get("list") or []
    if not as_of or not rows:
        return None
    stage = str(state.get("stage") or "")
    now = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    for rank, item in enumerate(rows, start=1):
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        con.execute(
            """
            INSERT INTO v4_candidates(as_of, ticker, rank, prob, close, stage, archived_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(as_of, ticker) DO UPDATE SET
                rank=excluded.rank, prob=excluded.prob, close=excluded.close, stage=excluded.stage
            """,
            (as_of, ticker, rank, item.get("olasilik_pct"), item.get("close"), stage, now),
        )
    return as_of


def _unsettled(con: sqlite3.Connection) -> list[str]:
    rows = con.execute(
        """
        SELECT c.as_of AS as_of, COUNT(*) AS n_cand,
               SUM(CASE WHEN r.ticker IS NOT NULL THEN 1 ELSE 0 END) AS n_res
        FROM v4_candidates c
        LEFT JOIN v4_results r ON r.as_of=c.as_of AND r.ticker=c.ticker
        GROUP BY c.as_of HAVING n_res < n_cand ORDER BY c.as_of
        """
    ).fetchall()
    return [str(r[0]) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description="V4 tarama motoru akşam arşiv + settle")
    ap.add_argument("--db", default=str(BASE / "patron4.db"))
    ap.add_argument("--state", default=str(BASE / "yuksek_getiri_v4_state.json"))
    ap.add_argument("--data-dir", default=str(BASE / "veriler"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data_dir = Path(args.data_dir).resolve()
    now = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    try:
        _ensure_schema(con)
        archived = _archive_state(con, Path(args.state))
        if archived:
            print(f"Arşivlendi: {archived}")
        else:
            print("Arşivlenecek güncel V4 listesi bulunamadı (state.json boş/eksik).")

        settled = pending = 0
        for as_of in _unsettled(con):
            cands = con.execute(
                "SELECT ticker, rank, prob FROM v4_candidates WHERE as_of=? ORDER BY rank", (as_of,)
            ).fetchall()
            state = {"as_of": as_of, "list": [{"ticker": c["ticker"]} for c in cands]}
            rows, evaluation = _previous_results(state, data_dir)
            if not rows or evaluation is None:
                print(f"  {as_of}: bekliyor (ertesi seans kapanışı henüz yok / veri eksik)")
                pending += 1
                continue
            by_tk = {str(r["ticker"]).upper(): r for r in rows}
            best = max(rows, key=lambda r: r["high_ret"])
            avg = sum(r["high_ret"] for r in rows) / len(rows)
            tag = "[DRY]" if args.dry_run else "yazıldı"
            print(
                f"  {as_of} → {evaluation.date()}: {len(rows)}/{len(cands)} hisse {tag} "
                f"| ort zirve {avg:+.1f}%  en iyi {best['ticker']} {best['high_ret']:+.1f}%"
            )
            if not args.dry_run:
                for c in cands:
                    tk = str(c["ticker"]).upper()
                    r = by_tk.get(tk)
                    if not r:
                        continue
                    con.execute(
                        """
                        INSERT INTO v4_results(as_of, evaluation_date, ticker, high_ret_pct, close_ret_pct, settled_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(as_of, ticker) DO UPDATE SET
                            evaluation_date=excluded.evaluation_date,
                            high_ret_pct=excluded.high_ret_pct,
                            close_ret_pct=excluded.close_ret_pct,
                            settled_at=excluded.settled_at
                        """,
                        (as_of, evaluation.strftime("%Y-%m-%d"), tk, r["high_ret"], r["close_ret"], now),
                    )
            settled += 1
        if not args.dry_run:
            con.commit()
        print(f"BİTTİ: {settled} gün settle edildi, {pending} gün bekliyor.")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
