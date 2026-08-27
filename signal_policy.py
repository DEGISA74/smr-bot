# -*- coding: utf-8 -*-
"""Tarama olayları, tarama aileleri ve uygulanabilir giriş fiyatı için ortak kurallar."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable

import pandas as pd


# Karne ve backtestlerde kullanilacak TEK piyasa rejimi tanimi.
# Bu tanim yalniz olcumleri iki piyasa kosuluna ayirmak icindir; canli tarama
# acip kapatmak, skor veya sinyal agirligi degistirmek icin kullanilamaz.
MEASUREMENT_REGIME_WINDOW = 50
MEASUREMENT_REGIME_RISING = "YUKSELEN"
MEASUREMENT_REGIME_FALLING = "DUSEN"
MEASUREMENT_REGIME_RULE = "XU100_CLOSE_VS_SMA50"


def measurement_regime_series(data: pd.DataFrame | pd.Series) -> pd.Series:
    """XU100 kapanisi SMA50 ustundeyse yukselen, degilse dusen rejim.

    Ilk 49 seans yeterli ortalama olmadigi icin rejimsiz birakilir. Esitlik
    dusen tarafa yazilir. Sonuc yalniz olcum segmentidir, tarama filtresi degildir.
    """
    close = data["Close"] if isinstance(data, pd.DataFrame) else data
    close = pd.to_numeric(close, errors="coerce")
    average = close.rolling(
        MEASUREMENT_REGIME_WINDOW,
        min_periods=MEASUREMENT_REGIME_WINDOW,
    ).mean()
    result = pd.Series(pd.NA, index=close.index, dtype="object")
    valid = close.notna() & average.notna()
    result.loc[valid & (close > average)] = MEASUREMENT_REGIME_RISING
    result.loc[valid & (close <= average)] = MEASUREMENT_REGIME_FALLING
    return result


# Aynı ekonomik hikâyeyi farklı adlarla anlatan taramalar tek bağımsız oy sayılır.
# Özellikle B11 + C5 + Zirve Sıkışma aynı "sıkışma/birikim" ailesidir.
_FAMILY_BY_SCAN = {
    "er_B11": "sikisma_birikim",
    "er_C5": "sikisma_birikim",
    "zirve_sikisma": "sikisma_birikim",
    "altin_setup": "altin_platin_vip_rozet",
    "platin_setup": "altin_platin_vip_rozet",
    "vip_formasyon": "altin_platin_vip_rozet",
    "wilder_pozitif_onayli": "rsi_pozitif_uyumsuzluk",
    "wilder_pozitif_izleme": "rsi_pozitif_uyumsuzluk",
    "rsi_pozitif_uyumsuzluk": "rsi_pozitif_uyumsuzluk",
    "liderlik_aday": "radar2_c6_liderlik",
    "liderlik_yeni": "radar2_c6_liderlik",
    "liderlik_teyitli": "radar2_c6_liderlik",
    "liderlik_c6": "radar2_c6_liderlik",
    "liderlik_gec": "radar2_c6_liderlik",
}

BADGE_SCAN_TYPES = frozenset({"altin_setup", "platin_setup", "vip_formasyon"})


def scanner_family(scan_type: str) -> str:
    """Bir taramanın bağımsız hikâye ailesini döndürür."""
    key = str(scan_type or "").strip()
    return _FAMILY_BY_SCAN.get(key, key or "bilinmeyen")


def unique_scanner_families(scan_types: Iterable[str]) -> set[str]:
    return {scanner_family(x) for x in scan_types if str(x or "").strip()}


def ensure_event_schema(conn) -> None:
    """Olay yaşam döngüsü kolonlarını ve tarama çalışma takvimini idempotent kurar."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(scan_signals)").fetchall()}
    for name, kind in (
        ("event_id", "INTEGER"),
        ("event_start_date", "TEXT"),
        ("event_day", "INTEGER"),
        ("is_event_start", "INTEGER DEFAULT 1"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE scan_signals ADD COLUMN {name} {kind}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_runs (
            scan_date  TEXT NOT NULL,
            scan_type  TEXT NOT NULL,
            row_count  INTEGER NOT NULL DEFAULT 0,
            category   TEXT,
            recorded_at TEXT,
            PRIMARY KEY (scan_date, scan_type)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scan_signals_event_start "
        "ON scan_signals(is_event_start, scan_date, scan_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scan_signals_lifecycle "
        "ON scan_signals(symbol, scan_type, scan_date)"
    )


def register_scan_run(conn, scan_type: str, scan_date: str, row_count: int, category: str = ""):
    """Taramanın sonuç vermese bile çalıştığını kaydeder; önceki çalışma tarihini döndürür."""
    ensure_event_schema(conn)
    previous = conn.execute(
        "SELECT MAX(scan_date) FROM scan_runs WHERE scan_type=? AND scan_date<?",
        (scan_type, scan_date),
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO scan_runs(scan_date, scan_type, row_count, category, recorded_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(scan_date, scan_type) DO UPDATE SET
            row_count=excluded.row_count,
            category=CASE WHEN excluded.category<>'' THEN excluded.category ELSE scan_runs.category END,
            recorded_at=excluded.recorded_at
        """,
        (
            scan_date,
            scan_type,
            max(0, int(row_count or 0)),
            str(category or ""),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    return previous


def assign_event_metadata_for_date(
    conn, scan_type: str, scan_date: str, previous_run_date: str | None
) -> None:
    """Bugünkü satırları, önceki tarama çalışmasındaki üyeliğe göre olaya bağlar."""
    ensure_event_schema(conn)
    current = conn.execute(
        "SELECT id, symbol, event_id, event_start_date, event_day "
        "FROM scan_signals WHERE scan_type=? AND scan_date=? ORDER BY id",
        (scan_type, scan_date),
    ).fetchall()
    for signal_id, symbol, event_id, event_start, event_day in current:
        if event_id is not None and event_start and event_day:
            continue
        prior = None
        if previous_run_date:
            prior = conn.execute(
                """
                SELECT id, event_id, event_start_date, event_day
                FROM scan_signals
                WHERE scan_type=? AND symbol=? AND scan_date=?
                ORDER BY id DESC LIMIT 1
                """,
                (scan_type, symbol, previous_run_date),
            ).fetchone()
        if prior:
            prior_id, prior_event, prior_start, prior_day = prior
            new_event_id = int(prior_event or prior_id)
            new_start = str(prior_start or previous_run_date)
            new_day = int(prior_day or 1) + 1
            is_start = 0
        else:
            new_event_id = int(signal_id)
            new_start = str(scan_date)
            new_day = 1
            is_start = 1
        conn.execute(
            """
            UPDATE scan_signals
            SET event_id=?, event_start_date=?, event_day=?, is_event_start=?
            WHERE id=?
            """,
            (new_event_id, new_start, new_day, is_start, int(signal_id)),
        )


def rebuild_event_metadata(conn) -> dict:
    """Eski günlük satırları, elde bulunan tarama takviminden bağımsız olaylara dönüştürür."""
    ensure_event_schema(conn)
    rows = conn.execute(
        "SELECT id, symbol, scan_type, scan_date FROM scan_signals "
        "ORDER BY scan_type, scan_date, symbol, id"
    ).fetchall()
    run_dates: dict[str, list[str]] = defaultdict(list)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for _, _, scan_type, scan_date in rows:
        counts[(scan_type, scan_date)] += 1
        if not run_dates[scan_type] or run_dates[scan_type][-1] != scan_date:
            run_dates[scan_type].append(scan_date)
    previous_run = {
        (scan_type, date): (dates[i - 1] if i else None)
        for scan_type, dates in run_dates.items()
        for i, date in enumerate(dates)
    }
    state = {}
    updates = []
    event_count = 0
    continuation_count = 0
    for signal_id, symbol, scan_type, scan_date in rows:
        key = (symbol, scan_type)
        prior = state.get(key)
        expected_prior = previous_run[(scan_type, scan_date)]
        if prior and prior["date"] == expected_prior:
            event_id = prior["event_id"]
            start_date = prior["start_date"]
            event_day = prior["event_day"] + 1
            is_start = 0
            continuation_count += 1
        else:
            event_id = int(signal_id)
            start_date = scan_date
            event_day = 1
            is_start = 1
            event_count += 1
        state[key] = {
            "date": scan_date,
            "event_id": event_id,
            "start_date": start_date,
            "event_day": event_day,
        }
        updates.append((event_id, start_date, event_day, is_start, int(signal_id)))
    conn.executemany(
        """
        UPDATE scan_signals
        SET event_id=?, event_start_date=?, event_day=?, is_event_start=?
        WHERE id=?
        """,
        updates,
    )
    for (scan_type, scan_date), row_count in counts.items():
        conn.execute(
            """
            INSERT INTO scan_runs(scan_date, scan_type, row_count, category, recorded_at)
            VALUES (?, ?, ?, '', ?)
            ON CONFLICT(scan_date, scan_type) DO UPDATE SET row_count=excluded.row_count
            """,
            (
                scan_date,
                scan_type,
                row_count,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
    return {
        "rows": len(rows),
        "events": event_count,
        "continuations": continuation_count,
        "scan_runs": len(counts),
    }


def resolve_next_open_entry(
    df: pd.DataFrame,
    signal_date,
    bias: str = "bullish",
    apply_bist_limit: bool = True,
    max_locked_sessions: int = 3,
) -> dict:
    """Sinyal kapanışından sonraki ilk gerçekten işlem yapılabilir açılışı bulur."""
    if df is None or df.empty:
        return {"status": "missing_history"}
    if "Open" not in df.columns or "Close" not in df.columns:
        return {"status": "missing_open"}
    work = df.sort_index()
    idx = pd.to_datetime(work.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    compare_idx = idx.normalize()
    signal_ts = pd.Timestamp(signal_date)
    if signal_ts.tzinfo is not None:
        signal_ts = signal_ts.tz_localize(None)
    start = int(compare_idx.searchsorted(signal_ts.normalize(), side="right"))
    if start >= len(work):
        return {"status": "pending_next_session"}

    is_bullish = "bear" not in str(bias or "bullish").lower()
    locked = 0
    attempts = max(1, int(max_locked_sessions))
    for pos in range(start, min(start + attempts, len(work))):
        open_price = pd.to_numeric(pd.Series([work["Open"].iloc[pos]]), errors="coerce").iloc[0]
        if pd.isna(open_price) or float(open_price) <= 0:
            return {"status": "missing_open", "entry_delay": pos - start}
        previous_close = pd.to_numeric(
            pd.Series([work["Close"].iloc[pos - 1]]), errors="coerce"
        ).iloc[0]
        gap_pct = None
        locked_at_limit = False
        if not pd.isna(previous_close) and float(previous_close) > 0:
            gap_pct = (float(open_price) / float(previous_close) - 1.0) * 100.0
            if apply_bist_limit and "High" in work.columns and "Low" in work.columns:
                high = float(work["High"].iloc[pos])
                low = float(work["Low"].iloc[pos])
                if is_bullish:
                    locked_at_limit = gap_pct >= 9.5 and low >= float(previous_close) * 1.095
                else:
                    locked_at_limit = gap_pct <= -9.5 and high <= float(previous_close) * 0.905
        if locked_at_limit:
            locked += 1
            continue
        return {
            "status": "filled_next_open" if pos == start else "filled_after_limit",
            "entry_pos": pos,
            "entry_price": float(open_price),
            "entry_date": idx[pos].date().isoformat(),
            "entry_gap_pct": round(gap_pct, 4) if gap_pct is not None else None,
            "entry_delay": pos - start,
            "locked_sessions": locked,
        }
    if start + attempts > len(work):
        return {"status": "pending_after_limit", "locked_sessions": locked}
    return {"status": "unfilled_limit_locked", "locked_sessions": locked}
