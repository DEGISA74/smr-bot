# -*- coding: utf-8 -*-
"""patron.db için süreçler arası yazım kilidi, tutarlı yedek ve günlük karne.

Bu modül hesap yapmaz; mevcut sinyalleri, fiyatları veya skorları değiştirmez.
Yalnız aynı anda iki yazıcının SQLite dosyasına yüklenmesini önler ve yapılan
Master Scan'in gerçekten diske yazıldığını sayısal olarak denetler.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pytz


ROOT = Path(__file__).resolve().parent
TZ_ISTANBUL = pytz.timezone("Europe/Istanbul")
LOCK_PATH = ROOT / "logs" / "patron_db_write.lock"
KARNE_DIR = ROOT / "logs"
BACKUP_DIR = ROOT / "backups"
_COMPONENTS = ("Erken Radar", "Liderlik", "Gold Mine")


def _now(now: datetime | None = None) -> datetime:
    value = now or datetime.now(TZ_ISTANBUL)
    if value.tzinfo is None:
        return TZ_ISTANBUL.localize(value)
    return value.astimezone(TZ_ISTANBUL)


def database_path() -> Path:
    """Testte kopya DB seçilebilsin; normalde tek canlı patron.db kullanılır."""
    raw = os.environ.get("PATRON_DB_FILE", "").strip()
    return Path(raw) if raw else ROOT / "patron.db"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def database_write_lock(purpose: str, timeout_seconds: float = 120.0) -> Iterator[None]:
    """Tek yazıcı kapısı. Kilit bekler; zaman aşımında yazmak yerine hata verir."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    acquired = False
    token = {
        "pid": os.getpid(),
        "purpose": str(purpose),
        "started_at": _now().isoformat(),
    }
    while time.monotonic() - started < timeout_seconds:
        try:
            handle = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(token, stream, ensure_ascii=False)
            acquired = True
            break
        except FileExistsError:
            # Bir süreç kilidi bırakıp çökerse ertesi iş sonsuza kadar beklemesin.
            # 30 dakikadan genç kilide ASLA dokunulmaz; canlı uzun iş korunur.
            try:
                age = time.time() - LOCK_PATH.stat().st_mtime
                if age > 30 * 60:
                    stale = LOCK_PATH.with_suffix(".stale")
                    os.replace(LOCK_PATH, stale)
                    continue
            except OSError:
                pass
            time.sleep(0.25)
    if not acquired:
        raise TimeoutError(f"patron.db yazım sırası {timeout_seconds:.0f} sn içinde açılmadı: {purpose}")
    try:
        yield
    finally:
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            pass


def create_consistent_backup(label: str = "guard", now: datetime | None = None) -> Path:
    """Canlı DB'nin SQLite snapshot'ını alır; kaynak dosyayı asla kopyalayarak ezmez."""
    current = _now(now)
    source_path = database_path()
    if not source_path.exists():
        raise FileNotFoundError(f"patron.db bulunamadı: {source_path}")
    safe_label = "".join(ch for ch in str(label) if ch.isalnum() or ch in ("-", "_")) or "guard"
    destination = BACKUP_DIR / f"patron_{current.strftime('%Y%m%d_%H%M%S')}_{safe_label}.db"
    temporary = destination.with_suffix(".tmp")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    with database_write_lock("tutarli_yedek"):
        source = sqlite3.connect(source_path, timeout=60)
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        os.replace(temporary, destination)
    return destination


def _connect_readonly() -> sqlite3.Connection:
    path = database_path().resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(path, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_component_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS master_scan_components (
            scan_date TEXT NOT NULL,
            category TEXT NOT NULL,
            component TEXT NOT NULL,
            status TEXT NOT NULL,
            expected_count INTEGER,
            actual_count INTEGER,
            detail TEXT,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (scan_date, category, component)
        )
        """
    )


def record_component_result(
    component: str,
    ok: bool,
    category: str = "BIST 500",
    expected_count: int | None = None,
    actual_count: int | None = None,
    detail: str = "",
    now: datetime | None = None,
) -> None:
    """Kritik adımın sonucu için kalıcı makbuz yazar; eski satırları etkilemez."""
    current = _now(now)
    clean_category = str(category or "").strip()
    with database_write_lock(f"master_component_{component}"):
        conn = sqlite3.connect(database_path(), timeout=60)
        try:
            _ensure_component_table(conn)
            conn.execute(
                """
                INSERT INTO master_scan_components
                (scan_date, category, component, status, expected_count, actual_count, detail, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scan_date, category, component) DO UPDATE SET
                    status=excluded.status,
                    expected_count=excluded.expected_count,
                    actual_count=excluded.actual_count,
                    detail=excluded.detail,
                    recorded_at=excluded.recorded_at
                """,
                (
                    current.strftime("%Y-%m-%d"), clean_category, str(component),
                    "ok" if ok else "failed", expected_count, actual_count,
                    str(detail or ""), current.isoformat(),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def build_daily_karne(category: str = "BIST 500", now: datetime | None = None) -> dict[str, Any]:
    """Silmeden/hesaplamadan yalnız sayım yapar ve Master Scan gerçeğini döndürür."""
    current = _now(now)
    day = current.strftime("%Y-%m-%d")
    clean_category = str(category or "").strip()
    conn = _connect_readonly()
    try:
        quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        foreign_key_issues = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        duplicates = conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT scan_date, symbol, scan_type, COUNT(*) AS n
                FROM scan_signals
                GROUP BY scan_date, symbol, scan_type
                HAVING n > 1
            )
            """
        ).fetchone()[0]
        runs = {
            row["scan_type"]: int(row["row_count"])
            for row in conn.execute(
                "SELECT scan_type, row_count FROM scan_runs WHERE scan_date=? AND trim(category)=?",
                (day, clean_category),
            )
        }
        actuals = {
            row["scan_type"]: int(row["row_count"])
            for row in conn.execute(
                """
                SELECT scan_type, COUNT(*) AS row_count FROM scan_signals
                WHERE scan_date=? AND trim(category)=?
                GROUP BY scan_type
                """,
                (day, clean_category),
            )
        }
        count_mismatches = [
            {"scan_type": name, "scan_run": count, "written": actuals.get(name, 0)}
            for name, count in runs.items() if actuals.get(name, 0) != count
        ]
        component_rows = {}
        try:
            component_rows = {
                row["component"]: dict(row)
                for row in conn.execute(
                    """
                    SELECT component, status, expected_count, actual_count, detail, recorded_at
                    FROM master_scan_components
                    WHERE scan_date=? AND category=?
                    """,
                    (day, clean_category),
                )
            }
        except sqlite3.OperationalError:
            component_rows = {}
        missing_components = [name for name in _COMPONENTS if name not in component_rows]
        failed_components = [
            name for name, row in component_rows.items()
            if row.get("status") != "ok"
        ]
        pending_returns = conn.execute(
            """
            SELECT COUNT(*) FROM scan_signals ss
            WHERE COALESCE(ss.is_event_start, 1)=1
              AND ss.scan_date < ?
              AND NOT EXISTS (SELECT 1 FROM signal_returns sr WHERE sr.signal_id=ss.id)
            """,
            (day,),
        ).fetchone()[0]
    finally:
        conn.close()

    issues: list[str] = []
    if quick_check != "ok":
        issues.append(f"SQLite bütünlük kontrolü: {quick_check}")
    if foreign_key_issues:
        issues.append(f"{foreign_key_issues} yabancı anahtar bağı kopuk")
    if duplicates:
        issues.append(f"{duplicates} çift günlük sinyal")
    if count_mismatches:
        issues.append(f"{len(count_mismatches)} tarama yazım sayısı uyuşmuyor")
    if missing_components:
        issues.append("kritik makbuz eksik: " + ", ".join(missing_components))
    if failed_components:
        issues.append("kritik adım başarısız: " + ", ".join(failed_components))
    return {
        "generated_at": current.isoformat(),
        "day": day,
        "category": clean_category,
        "ok": not issues,
        "issues": issues,
        "database": {
            "quick_check": quick_check,
            "foreign_key_issues": foreign_key_issues,
            "duplicate_daily_signals": duplicates,
        },
        "scan_runs": runs,
        "written_signal_counts": actuals,
        "count_mismatches": count_mismatches,
        "components": component_rows,
        "pending_return_event_starts": int(pending_returns),
    }


def write_daily_karne(category: str = "BIST 500", now: datetime | None = None) -> dict[str, Any]:
    report = build_daily_karne(category=category, now=now)
    dated = KARNE_DIR / f"master_scan_karne_{report['day'].replace('-', '')}.json"
    _atomic_json(dated, report)
    _atomic_json(KARNE_DIR / "master_scan_karne_latest.json", report)
    return report
