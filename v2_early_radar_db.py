#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V2 Erken Radar'a ait bağımsız SQLite sicili."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ISTANBUL = ZoneInfo("Europe/Istanbul")


def _now() -> str:
    return datetime.now(ISTANBUL).isoformat()


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS radar_signals (
            signal_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            rank INTEGER NOT NULL,
            probability_pct REAL NOT NULL,
            reason TEXT NOT NULL,
            reference_price REAL NOT NULL,
            reference_timestamp TEXT NOT NULL,
            b11_confirmation INTEGER NOT NULL DEFAULT 0,
            eligible_pool INTEGER NOT NULL,
            model_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            published_at TEXT,
            PRIMARY KEY (signal_date, symbol)
        );

        CREATE TABLE IF NOT EXISTS radar_results (
            signal_date TEXT NOT NULL,
            evaluation_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            reference_price REAL NOT NULL,
            t1_high REAL NOT NULL,
            t1_last REAL NOT NULL,
            high_return_pct REAL NOT NULL,
            last_return_pct REAL NOT NULL,
            snapshot_timestamp TEXT NOT NULL,
            evaluated_at TEXT NOT NULL,
            PRIMARY KEY (signal_date, evaluation_date, symbol),
            FOREIGN KEY (signal_date, symbol)
                REFERENCES radar_signals(signal_date, symbol)
        );

        CREATE TABLE IF NOT EXISTS radar_publications (
            kind TEXT NOT NULL,
            publication_key TEXT NOT NULL,
            published_at TEXT NOT NULL,
            PRIMARY KEY (kind, publication_key)
        );

        CREATE TABLE IF NOT EXISTS radar_runs (
            run_date TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            snapshot_timestamp TEXT,
            eligible_pool INTEGER NOT NULL DEFAULT 0,
            candidate_count INTEGER NOT NULL DEFAULT 0,
            attempted_at TEXT NOT NULL,
            published_at TEXT
        );

        CREATE TABLE IF NOT EXISTS radar_result_runs (
            signal_date TEXT NOT NULL,
            evaluation_date TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            published_at TEXT,
            PRIMARY KEY (signal_date, evaluation_date)
        );

        CREATE TABLE IF NOT EXISTS radar_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_radar_results_evaluation
            ON radar_results(evaluation_date);
        """
    )
    return connection


@contextmanager
def open_db(path: Path):
    connection = connect(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def publication_exists(path: Path, kind: str, publication_key: str) -> bool:
    with open_db(path) as connection:
        row = connection.execute(
            "SELECT 1 FROM radar_publications WHERE kind=? AND publication_key=?",
            (kind, publication_key),
        ).fetchone()
    return row is not None


def initialize_service(path: Path, started_date: str) -> None:
    with open_db(path) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO radar_meta(key, value) VALUES ('service_started_date', ?)",
            (str(started_date),),
        )


def get_service_started_date(path: Path) -> str | None:
    if not Path(path).exists():
        return None
    with open_db(path) as connection:
        row = connection.execute(
            "SELECT value FROM radar_meta WHERE key='service_started_date'"
        ).fetchone()
    return str(row["value"]) if row is not None else None


def record_candidate_run(
    path: Path,
    run_date: str,
    status: str,
    reason: str,
    *,
    snapshot_timestamp: str | None = None,
    eligible_pool: int = 0,
    candidate_count: int = 0,
    published_at: str | None = None,
) -> None:
    with open_db(path) as connection:
        connection.execute(
            """
            INSERT INTO radar_runs (
                run_date, status, reason, snapshot_timestamp,
                eligible_pool, candidate_count, attempted_at, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_date) DO UPDATE SET
                status=excluded.status,
                reason=excluded.reason,
                snapshot_timestamp=excluded.snapshot_timestamp,
                eligible_pool=excluded.eligible_pool,
                candidate_count=excluded.candidate_count,
                attempted_at=excluded.attempted_at,
                published_at=COALESCE(excluded.published_at, radar_runs.published_at)
            """,
            (
                str(run_date),
                str(status),
                str(reason),
                snapshot_timestamp,
                int(eligible_pool),
                int(candidate_count),
                _now(),
                published_at,
            ),
        )


def get_candidate_run(path: Path, run_date: str) -> dict | None:
    if not Path(path).exists():
        return None
    with open_db(path) as connection:
        row = connection.execute(
            "SELECT * FROM radar_runs WHERE run_date=?",
            (str(run_date),),
        ).fetchone()
    return dict(row) if row is not None else None


def load_signal_state(path: Path, signal_date: str) -> dict | None:
    if not Path(path).exists():
        return None
    with open_db(path) as connection:
        run = connection.execute(
            "SELECT * FROM radar_runs WHERE run_date=?",
            (str(signal_date),),
        ).fetchone()
        rows = connection.execute(
            "SELECT * FROM radar_signals WHERE signal_date=? ORDER BY rank",
            (str(signal_date),),
        ).fetchall()
    if run is None or str(run["status"]) != "success" or not rows:
        return None
    first = rows[0]
    return {
        "motor": "V2 Erken Radar",
        "as_of": str(signal_date),
        "created_at": str(run["attempted_at"]),
        "market_snapshot_timestamp": str(run["snapshot_timestamp"] or ""),
        "eligible": int(run["eligible_pool"]),
        "model_version": str(first["model_version"]),
        "official_v2_untouched": True,
        "partial_day_shadow_model": True,
        "list": [
            {
                "rank": int(row["rank"]),
                "ticker": str(row["symbol"]),
                "olasilik_pct": float(row["probability_pct"]),
                "neden": str(row["reason"]),
                "reference_price": float(row["reference_price"]),
                "reference_timestamp": str(row["reference_timestamp"]),
                "b11_teyidi": bool(row["b11_confirmation"]),
            }
            for row in rows
        ],
    }


def record_result_run(
    path: Path,
    signal_date: str,
    evaluation_date: str,
    status: str,
    reason: str,
    *,
    published_at: str | None = None,
) -> None:
    with open_db(path) as connection:
        connection.execute(
            """
            INSERT INTO radar_result_runs (
                signal_date, evaluation_date, status, reason, attempted_at, published_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_date, evaluation_date) DO UPDATE SET
                status=excluded.status,
                reason=excluded.reason,
                attempted_at=excluded.attempted_at,
                published_at=COALESCE(excluded.published_at, radar_result_runs.published_at)
            """,
            (
                str(signal_date),
                str(evaluation_date),
                str(status),
                str(reason),
                _now(),
                published_at,
            ),
        )


def stage_signals(path: Path, state: dict, *, published_at: str | None = None) -> None:
    signal_date = str(state["as_of"])
    created_at = str(state.get("created_at") or _now())
    eligible = int(state.get("eligible", 0))
    model_version = str(state.get("model_version", ""))
    rows = []
    for fallback_rank, item in enumerate(state.get("list") or [], start=1):
        rows.append(
            (
                signal_date,
                str(item["ticker"]).strip().upper(),
                int(item.get("rank", fallback_rank)),
                float(item["olasilik_pct"]),
                str(item.get("neden", "")),
                float(item["reference_price"]),
                str(item["reference_timestamp"]),
                int(bool(item.get("b11_teyidi", False))),
                eligible,
                model_version,
                created_at,
                published_at,
            )
        )
    with open_db(path) as connection:
        connection.executemany(
            """
            INSERT INTO radar_signals (
                signal_date, symbol, rank, probability_pct, reason,
                reference_price, reference_timestamp, b11_confirmation,
                eligible_pool, model_version, created_at, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_date, symbol) DO UPDATE SET
                rank=excluded.rank,
                probability_pct=excluded.probability_pct,
                reason=excluded.reason,
                reference_price=excluded.reference_price,
                reference_timestamp=excluded.reference_timestamp,
                b11_confirmation=excluded.b11_confirmation,
                eligible_pool=excluded.eligible_pool,
                model_version=excluded.model_version,
                created_at=excluded.created_at,
                published_at=COALESCE(excluded.published_at, radar_signals.published_at)
            """,
            rows,
        )


def stage_results(path: Path, signal_date: str, evaluation_date: str, results: list[dict]) -> None:
    evaluated_at = _now()
    rows = [
        (
            signal_date,
            evaluation_date,
            str(row["ticker"]),
            float(row["reference_price"]),
            float(row["high"]),
            float(row["last"]),
            float(row["high_return_pct"]),
            float(row["last_return_pct"]),
            str(row["snapshot_timestamp"]),
            evaluated_at,
        )
        for row in results
    ]
    with open_db(path) as connection:
        connection.executemany(
            """
            INSERT INTO radar_results (
                signal_date, evaluation_date, symbol, reference_price,
                t1_high, t1_last, high_return_pct, last_return_pct,
                snapshot_timestamp, evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_date, evaluation_date, symbol) DO UPDATE SET
                reference_price=excluded.reference_price,
                t1_high=excluded.t1_high,
                t1_last=excluded.t1_last,
                high_return_pct=excluded.high_return_pct,
                last_return_pct=excluded.last_return_pct,
                snapshot_timestamp=excluded.snapshot_timestamp,
                evaluated_at=excluded.evaluated_at
            """,
            rows,
        )


def mark_published(path: Path, kind: str, publication_key: str, *, published_at: str | None = None) -> None:
    timestamp = published_at or _now()
    with open_db(path) as connection:
        connection.execute(
            """
            INSERT INTO radar_publications(kind, publication_key, published_at)
            VALUES (?, ?, ?)
            ON CONFLICT(kind, publication_key) DO UPDATE SET
                published_at=excluded.published_at
            """,
            (kind, publication_key, timestamp),
        )
        if kind == "candidates":
            connection.execute(
                "UPDATE radar_signals SET published_at=? WHERE signal_date=?",
                (timestamp, publication_key),
            )
