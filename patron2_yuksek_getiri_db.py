#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sadece V1/V2 yuksek getiri adaylarini tutan bagimsiz patron2.db katmani."""

from __future__ import annotations

import argparse
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable


ENGINES = {"v1", "v2"}
TP_RATE = 0.04
COMMISSION_RATE = 0.0005
INITIAL_CAPITAL_TL = 1_000_000.0


def calculate_tp4_strategy(
    open_price: float,
    high_price: float,
    close_price: float,
    target_rate: float = TP_RATE,
    commission_rate: float = COMMISSION_RATE,
) -> dict:
    """T+1 acilistan al, +%4'te sat; degilse kapanista cik senaryosu."""
    values = (open_price, high_price, close_price)
    if not all(math.isfinite(float(value)) and float(value) > 0.0 for value in values):
        raise ValueError("T+1 Open/High/Close fiyatlari pozitif ve sonlu olmali.")
    if target_rate <= 0.0 or not 0.0 <= commission_rate < 1.0:
        raise ValueError("Hedef veya komisyon orani gecersiz.")

    open_price = float(open_price)
    high_price = float(high_price)
    close_price = float(close_price)
    tp_hit = high_price >= open_price * (1.0 + target_rate)
    exit_price = open_price * (1.0 + target_rate) if tp_hit else close_price
    gross_return = exit_price / open_price - 1.0
    net_multiplier = (1.0 + gross_return) * (1.0 - commission_rate) / (1.0 + commission_rate)
    return {
        "tp4_hit": bool(tp_hit),
        "strategy_exit_reason": "tp4" if tp_hit else "session_close",
        "strategy_exit_price": exit_price,
        "strategy_gross_return_pct": gross_return * 100.0,
        "strategy_net_return_pct": (net_multiplier - 1.0) * 100.0,
    }


def _engine(value: str) -> str:
    engine = str(value or "").strip().lower()
    if engine not in ENGINES:
        raise ValueError(f"Motor yalnizca v1 veya v2 olabilir: {value!r}")
    return engine


def _connect(db_path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(str(Path(db_path)), timeout=30.0)
    connection.execute("PRAGMA busy_timeout=30000")
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def _database(db_path: Path | str):
    connection = _connect(db_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS candidates (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            engine                TEXT NOT NULL CHECK(engine IN ('v1', 'v2')),
            signal_date           TEXT NOT NULL,
            symbol                TEXT NOT NULL,
            rank                  INTEGER NOT NULL,
            score                 REAL,
            probability_pct       REAL,
            reason                TEXT,
            category              TEXT,
            signal_close          REAL,
            eligible_pool         INTEGER,
            model_version         TEXT,
            published             INTEGER NOT NULL DEFAULT 0,
            created_at            TEXT NOT NULL,
            published_at          TEXT,
            UNIQUE(engine, signal_date, symbol)
        );

        CREATE TABLE IF NOT EXISTS results (
            id                         INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id               INTEGER,
            engine                     TEXT NOT NULL CHECK(engine IN ('v1', 'v2')),
            signal_date                TEXT NOT NULL,
            evaluation_date            TEXT NOT NULL,
            symbol                     TEXT NOT NULL,
            signal_close               REAL NOT NULL,
            t1_open                    REAL NOT NULL,
            t1_high                    REAL NOT NULL,
            t1_close                   REAL NOT NULL,
            close_to_high_return_pct   REAL NOT NULL,
            close_to_close_return_pct  REAL NOT NULL,
            open_to_high_return_pct    REAL NOT NULL,
            open_to_close_return_pct   REAL NOT NULL,
            tp4_hit                    INTEGER,
            strategy_exit_reason       TEXT,
            strategy_exit_price        REAL,
            strategy_gross_return_pct  REAL,
            buy_commission_rate        REAL,
            sell_commission_rate       REAL,
            strategy_net_return_pct    REAL,
            evaluated_at               TEXT NOT NULL,
            UNIQUE(engine, signal_date, symbol),
            FOREIGN KEY(candidate_id) REFERENCES candidates(id)
        );

        CREATE TABLE IF NOT EXISTS portfolio_daily (
            engine                    TEXT NOT NULL CHECK(engine IN ('v1', 'v2')),
            evaluation_date           TEXT NOT NULL,
            signal_date               TEXT NOT NULL,
            strategy_name             TEXT NOT NULL,
            expected_candidates       INTEGER NOT NULL,
            evaluated_candidates      INTEGER NOT NULL,
            data_complete             INTEGER NOT NULL,
            capital_chain_complete    INTEGER NOT NULL,
            tp4_hits                  INTEGER,
            tp4_hit_rate_pct          REAL,
            gross_daily_return_pct    REAL,
            net_daily_return_pct      REAL,
            initial_capital_tl        REAL NOT NULL,
            start_capital_tl          REAL,
            buy_commission_tl         REAL,
            sell_commission_tl        REAL,
            total_commission_tl       REAL,
            end_capital_tl            REAL,
            cumulative_net_return_pct REAL,
            calculated_at             TEXT NOT NULL,
            PRIMARY KEY(engine, evaluation_date),
            UNIQUE(engine, signal_date)
        );

        CREATE TABLE IF NOT EXISTS scan_runs (
            engine      TEXT NOT NULL CHECK(engine IN ('v1', 'v2')),
            signal_date TEXT NOT NULL,
            row_count   INTEGER NOT NULL DEFAULT 0,
            published   INTEGER NOT NULL DEFAULT 0,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY(engine, signal_date)
        );

        CREATE INDEX IF NOT EXISTS idx_candidates_engine_date
            ON candidates(engine, signal_date, published, rank);
        CREATE INDEX IF NOT EXISTS idx_results_engine_eval
            ON results(engine, evaluation_date, symbol);
        """
    )


def initialize(db_path: Path | str) -> None:
    with _database(db_path) as connection:
        ensure_schema(connection)


def stage_candidates(
    db_path: Path | str,
    engine: str,
    signal_date: str,
    rows: Iterable[dict],
    *,
    eligible_pool: int | None = None,
    model_version: str = "",
    published: bool = False,
    published_at: str | None = None,
) -> int:
    """Adaylari idempotent yazar; V1 ve V2 ayni tabloda motor etiketiyle ayrilir."""
    engine = _engine(engine)
    rows = list(rows)
    now = _now_text()
    publication_time = str(published_at or now) if published else None
    with _database(db_path) as connection:
        ensure_schema(connection)
        for fallback_rank, row in enumerate(rows, start=1):
            symbol = str(row.get("symbol", "")).strip().upper()
            if not symbol:
                continue
            connection.execute(
                """
                INSERT INTO candidates(
                    engine, signal_date, symbol, rank, score, probability_pct,
                    reason, category, signal_close, eligible_pool, model_version,
                    published, created_at, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(engine, signal_date, symbol) DO UPDATE SET
                    rank=excluded.rank,
                    score=COALESCE(excluded.score, candidates.score),
                    probability_pct=COALESCE(excluded.probability_pct, candidates.probability_pct),
                    reason=CASE WHEN excluded.reason<>'' THEN excluded.reason ELSE candidates.reason END,
                    category=CASE WHEN excluded.category<>'' THEN excluded.category ELSE candidates.category END,
                    signal_close=COALESCE(excluded.signal_close, candidates.signal_close),
                    eligible_pool=COALESCE(excluded.eligible_pool, candidates.eligible_pool),
                    model_version=CASE WHEN excluded.model_version<>'' THEN excluded.model_version ELSE candidates.model_version END,
                    published=MAX(candidates.published, excluded.published),
                    published_at=COALESCE(candidates.published_at, excluded.published_at)
                """,
                (
                    engine,
                    str(signal_date),
                    symbol,
                    int(row.get("rank", fallback_rank)),
                    _optional_float(row.get("score")),
                    _optional_float(row.get("probability_pct")),
                    str(row.get("reason", "")),
                    str(row.get("category", "")),
                    _optional_float(row.get("signal_close")),
                    _optional_int(eligible_pool),
                    str(model_version or ""),
                    int(bool(published)),
                    now,
                    publication_time,
                ),
            )
    return len(rows)


def mark_run_published(db_path: Path | str, engine: str, signal_date: str, row_count: int) -> None:
    engine = _engine(engine)
    now = _now_text()
    with _database(db_path) as connection:
        ensure_schema(connection)
        connection.execute(
            """
            UPDATE candidates
            SET published=1, published_at=COALESCE(published_at, ?)
            WHERE engine=? AND signal_date=?
            """,
            (now, engine, str(signal_date)),
        )
        connection.execute(
            """
            INSERT INTO scan_runs(engine, signal_date, row_count, published, recorded_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(engine, signal_date) DO UPDATE SET
                row_count=excluded.row_count,
                published=1,
                recorded_at=excluded.recorded_at
            """,
            (engine, str(signal_date), max(0, int(row_count)), now),
        )


def settle_results(
    db_path: Path | str,
    engine: str,
    signal_date: str,
    evaluation_date,
    rows: Iterable[dict],
) -> dict | None:
    engine = _engine(engine)
    rows = list(rows)
    if evaluation_date is None or not rows:
        return None
    evaluation_text = evaluation_date.strftime("%Y-%m-%d")
    now = _now_text()
    with _database(db_path) as connection:
        ensure_schema(connection)
        for row in rows:
            symbol = str(row.get("symbol", "")).strip().upper()
            candidate = connection.execute(
                "SELECT id FROM candidates WHERE engine=? AND signal_date=? AND symbol=?",
                (engine, str(signal_date), symbol),
            ).fetchone()
            strategy_net = _optional_float(row.get("strategy_net_return_pct"))
            connection.execute(
                """
                INSERT INTO results(
                    candidate_id, engine, signal_date, evaluation_date, symbol,
                    signal_close, t1_open, t1_high, t1_close,
                    close_to_high_return_pct, close_to_close_return_pct,
                    open_to_high_return_pct, open_to_close_return_pct,
                    tp4_hit, strategy_exit_reason, strategy_exit_price,
                    strategy_gross_return_pct, buy_commission_rate,
                    sell_commission_rate, strategy_net_return_pct, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(engine, signal_date, symbol) DO UPDATE SET
                    candidate_id=excluded.candidate_id,
                    evaluation_date=excluded.evaluation_date,
                    signal_close=excluded.signal_close,
                    t1_open=excluded.t1_open,
                    t1_high=excluded.t1_high,
                    t1_close=excluded.t1_close,
                    close_to_high_return_pct=excluded.close_to_high_return_pct,
                    close_to_close_return_pct=excluded.close_to_close_return_pct,
                    open_to_high_return_pct=excluded.open_to_high_return_pct,
                    open_to_close_return_pct=excluded.open_to_close_return_pct,
                    tp4_hit=excluded.tp4_hit,
                    strategy_exit_reason=excluded.strategy_exit_reason,
                    strategy_exit_price=excluded.strategy_exit_price,
                    strategy_gross_return_pct=excluded.strategy_gross_return_pct,
                    buy_commission_rate=excluded.buy_commission_rate,
                    sell_commission_rate=excluded.sell_commission_rate,
                    strategy_net_return_pct=excluded.strategy_net_return_pct,
                    evaluated_at=excluded.evaluated_at
                """,
                (
                    int(candidate["id"]) if candidate else None,
                    engine,
                    str(signal_date),
                    evaluation_text,
                    symbol,
                    float(row["signal_close"]),
                    float(row["t1_open"]),
                    float(row["t1_high"]),
                    float(row["t1_close"]),
                    float(row["close_to_high_return_pct"]),
                    float(row["close_to_close_return_pct"]),
                    float(row["open_to_high_return_pct"]),
                    float(row["open_to_close_return_pct"]),
                    int(bool(row.get("tp4_hit"))) if strategy_net is not None else None,
                    row.get("strategy_exit_reason"),
                    _optional_float(row.get("strategy_exit_price")),
                    _optional_float(row.get("strategy_gross_return_pct")),
                    COMMISSION_RATE if strategy_net is not None else None,
                    COMMISSION_RATE if strategy_net is not None else None,
                    strategy_net,
                    now,
                ),
            )
        if engine == "v2":
            _rebuild_v2_portfolio(connection)
            portfolio = connection.execute(
                "SELECT * FROM portfolio_daily WHERE engine='v2' AND evaluation_date=?",
                (evaluation_text,),
            ).fetchone()
            return dict(portfolio) if portfolio else None
    return None


def _rebuild_v2_portfolio(connection: sqlite3.Connection) -> None:
    groups = connection.execute(
        """
        SELECT
            c.signal_date,
            MIN(r.evaluation_date) AS evaluation_date,
            COUNT(c.id) AS expected_count,
            COUNT(r.id) AS evaluated_count
        FROM candidates c
        LEFT JOIN results r
          ON r.engine=c.engine AND r.signal_date=c.signal_date AND r.symbol=c.symbol
        WHERE c.engine='v2' AND c.published=1
        GROUP BY c.signal_date
        HAVING MIN(r.evaluation_date) IS NOT NULL
        ORDER BY MIN(r.evaluation_date), c.signal_date
        """
    ).fetchall()
    connection.execute("DELETE FROM portfolio_daily WHERE engine='v2'")
    capital = INITIAL_CAPITAL_TL
    chain_complete = True
    now = _now_text()

    for group in groups:
        signal_date = str(group["signal_date"])
        evaluation_date = str(group["evaluation_date"])
        expected = int(group["expected_count"])
        evaluated = int(group["evaluated_count"])
        complete = expected > 0 and expected == evaluated
        returns = connection.execute(
            """
            SELECT tp4_hit, strategy_gross_return_pct, strategy_net_return_pct
            FROM results
            WHERE engine='v2' AND signal_date=? ORDER BY symbol
            """,
            (signal_date,),
        ).fetchall()
        strategy_complete = complete and all(
            row["strategy_gross_return_pct"] is not None
            and row["strategy_net_return_pct"] is not None
            for row in returns
        )
        tp_hits = sum(int(row["tp4_hit"] or 0) for row in returns)
        gross_daily = (
            sum(float(row["strategy_gross_return_pct"]) for row in returns) / evaluated
            if strategy_complete else None
        )
        net_daily = (
            sum(float(row["strategy_net_return_pct"]) for row in returns) / evaluated
            if strategy_complete else None
        )
        start_capital = buy_fee = sell_fee = end_capital = cumulative = None
        if not strategy_complete:
            chain_complete = False
        elif chain_complete:
            start_capital = capital
            allocation = start_capital / evaluated
            buy_notional = allocation / (1.0 + COMMISSION_RATE)
            buy_fee = buy_notional * COMMISSION_RATE * evaluated
            gross_sales = [
                buy_notional * (1.0 + float(row["strategy_gross_return_pct"]) / 100.0)
                for row in returns
            ]
            sell_fee = sum(value * COMMISSION_RATE for value in gross_sales)
            end_capital = sum(gross_sales) - sell_fee
            net_daily = (end_capital / start_capital - 1.0) * 100.0
            capital = end_capital
            cumulative = (capital / INITIAL_CAPITAL_TL - 1.0) * 100.0

        connection.execute(
            """
            INSERT INTO portfolio_daily(
                engine, evaluation_date, signal_date, strategy_name,
                expected_candidates, evaluated_candidates, data_complete,
                capital_chain_complete, tp4_hits, tp4_hit_rate_pct,
                gross_daily_return_pct, net_daily_return_pct,
                initial_capital_tl, start_capital_tl, buy_commission_tl,
                sell_commission_tl, total_commission_tl, end_capital_tl,
                cumulative_net_return_pct, calculated_at
            ) VALUES ('v2', ?, ?, 'open_tp4_else_close_fee_0.0005_each_side',
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evaluation_date,
                signal_date,
                expected,
                evaluated,
                int(complete),
                int(chain_complete and strategy_complete),
                tp_hits,
                (tp_hits / evaluated * 100.0) if evaluated else None,
                gross_daily,
                net_daily,
                INITIAL_CAPITAL_TL,
                start_capital,
                buy_fee,
                sell_fee,
                (buy_fee + sell_fee) if buy_fee is not None and sell_fee is not None else None,
                end_capital,
                cumulative,
                now,
            ),
        )


def _optional_float(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _optional_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _main() -> int:
    parser = argparse.ArgumentParser(description="patron2.db V1/V2 yuksek getiri sicili")
    parser.add_argument("--db", required=True)
    parser.add_argument("--init", action="store_true")
    args = parser.parse_args()
    if not args.init:
        parser.error("--init gerekli")
    initialize(args.db)
    print(f"patron2.db hazir: {Path(args.db).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
