#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Aktif tarama motorları için ortak veri-yok ve devam protokolü.

Tarama formüllerine dokunmaz. Çalışma günlüğü tutar, veri/motor hatasını bir kez
Telegram'a bildirir ve bir sonraki çalışmada eski liste karnesini atlatır.
"""

from __future__ import annotations

import json
import argparse
import os
import sqlite3
import sys
import traceback
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

import requests


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


ISTANBUL = ZoneInfo("Europe/Istanbul")
SKIP_PREVIOUS_ENV = "SMR_SKIP_PREVIOUS_REPORT"


class DataUnavailable(RuntimeError):
    """Motorun hesap yapabileceği güncel veri bulunmadığını bildirir."""


def _now() -> datetime:
    return datetime.now(ISTANBUL)


def _safe_reason(value: object) -> str:
    text = " ".join(str(value).replace("\r", " ").replace("\n", " ").split())
    return (text or "Veri okunamadı veya motor tamamlanamadı.")[:320]


def _token(base: Path) -> str | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token.strip()
    for source in (
        Path("/home/wm11tr/weektweet/.env"),
        Path("/home/wm11tr/insider/.env"),
    ):
        try:
            for line in source.read_text(encoding="utf-8").splitlines():
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    return line.split("=", 1)[1].strip()
        except Exception:
            pass
    try:
        config = json.loads((base / "telegram_config.json").read_text(encoding="utf-8"))
        value = config.get("bot_token")
        return str(value).strip() if value else None
    except Exception:
        return None


def _send_to_targets(base: Path, targets: Iterable[str], message: str) -> bool:
    token = _token(base)
    if not token:
        print("[süreklilik] Telegram bot anahtarı bulunamadı.")
        return False
    success = True
    for chat_id in tuple(dict.fromkeys(str(value) for value in targets if value)):
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": message,
                    "disable_web_page_preview": True,
                },
                timeout=25,
            )
            sent = response.status_code == 200
            if not sent:
                print("[süreklilik] Telegram HTTP", response.status_code, response.text[:160])
        except Exception as exc:
            sent = False
            print("[süreklilik] Telegram hatası:", exc)
        print("[süreklilik] bildirim:", "OK" if sent else "BAŞARISIZ", "→", chat_id)
        success = success and sent
    return success


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS engine_meta (
            engine TEXT PRIMARY KEY,
            service_started_date TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS engine_runs (
            engine TEXT NOT NULL,
            run_date TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            published_at TEXT,
            PRIMARY KEY (engine, run_date)
        );
        CREATE TABLE IF NOT EXISTS publications (
            engine TEXT NOT NULL,
            kind TEXT NOT NULL,
            publication_key TEXT NOT NULL,
            published_at TEXT NOT NULL,
            PRIMARY KEY (engine, kind, publication_key)
        );
        """
    )
    return connection


@contextmanager
def _db(path: Path):
    connection = _connect(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _initialize(path: Path, engine: str, run_date: date) -> str:
    with _db(path) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO engine_meta(engine, service_started_date) VALUES (?, ?)",
            (engine, run_date.isoformat()),
        )
        row = connection.execute(
            "SELECT service_started_date FROM engine_meta WHERE engine=?",
            (engine,),
        ).fetchone()
    return str(row["service_started_date"])


def _run_status(path: Path, engine: str, run_date: date) -> dict | None:
    if not path.exists():
        return None
    with _db(path) as connection:
        row = connection.execute(
            "SELECT * FROM engine_runs WHERE engine=? AND run_date=?",
            (engine, run_date.isoformat()),
        ).fetchone()
    return dict(row) if row is not None else None


def _record_run(
    path: Path,
    engine: str,
    run_date: date,
    status: str,
    reason: str,
    *,
    published_at: str | None = None,
) -> None:
    with _db(path) as connection:
        connection.execute(
            """
            INSERT INTO engine_runs(engine, run_date, status, reason, attempted_at, published_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(engine, run_date) DO UPDATE SET
                status=excluded.status,
                reason=excluded.reason,
                attempted_at=excluded.attempted_at,
                published_at=COALESCE(excluded.published_at, engine_runs.published_at)
            """,
            (
                engine,
                run_date.isoformat(),
                status,
                _safe_reason(reason),
                _now().isoformat(),
                published_at,
            ),
        )


def _publication_exists(path: Path, engine: str, kind: str, key: str) -> bool:
    with _db(path) as connection:
        row = connection.execute(
            "SELECT 1 FROM publications WHERE engine=? AND kind=? AND publication_key=?",
            (engine, kind, key),
        ).fetchone()
    return row is not None


def _mark_published(path: Path, engine: str, kind: str, key: str) -> str:
    timestamp = _now().isoformat()
    with _db(path) as connection:
        connection.execute(
            """
            INSERT INTO publications(engine, kind, publication_key, published_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(engine, kind, publication_key) DO UPDATE SET
                published_at=excluded.published_at
            """,
            (engine, kind, key, timestamp),
        )
    return timestamp


def _previous_run_date(run_date: date, cadence: str) -> date:
    if cadence == "weekly":
        return run_date - timedelta(days=7)
    previous = run_date - timedelta(days=1)
    while previous.weekday() >= 5:
        previous -= timedelta(days=1)
    return previous


def require_data_date(
    actual: object,
    *,
    timing: str,
    enabled: bool = True,
    current_date: date | None = None,
) -> None:
    """Eski parquet'in yeni gün listesi gibi yayımlanmasını engeller."""
    if not enabled:
        return
    today = current_date or _now().date()
    if timing in {"morning", "weekly"}:
        expected = _previous_run_date(today, "weekday")
    elif timing == "same_day":
        expected = today
    else:
        raise ValueError(f"Bilinmeyen veri zamanlaması: {timing}")
    try:
        actual_date = date.fromisoformat(str(actual)[:10])
    except Exception as exc:
        raise DataUnavailable("Veri tarihi okunamadı veya boş.") from exc
    if actual_date != expected:
        raise DataUnavailable(
            f"Güncel veri tarihi {expected.isoformat()} olmalıydı; gelen son tarih {actual_date.isoformat()}."
        )


def _failure_message(label: str, run_date: date, reason: str) -> str:
    return "\n".join(
        [
            f"⚠️ {label} — VERİ ALINAMADI / MOTOR ÇALIŞMADI",
            f"{run_date.strftime('%d.%m.%Y')} taraması tamamlanamadı.",
            f"Kontrol notu: {_safe_reason(reason)}",
            "",
            "ℹ️ Hata kaydedildi. Sistem durdurulmadı; bir sonraki planlı çalışmada "
            "otomatik olarak yeniden deneyecek.",
        ]
    )


def _missing_previous_message(label: str, previous: date, current: date, reason: str) -> str:
    return "\n".join(
        [
            f"📊 {label} — ÖNCEKİ TARAMA İÇİN SONUÇ YOK",
            f"{previous.strftime('%d.%m.%Y')} günü liste/veri üretilemedi veya görev çalışmadı.",
            f"Bu nedenle o güne ait sonuç karnesi yok. Kayıt notu: {_safe_reason(reason)}",
            "",
            f"ℹ️ Süreç devam ediyor; motor {current.strftime('%d.%m.%Y')} taramasını şimdi normal şekilde çalıştıracak.",
        ]
    )


def guarded_main(
    *,
    engine: str,
    label: str,
    main_func: Callable[[], int | None],
    targets: Iterable[str],
    base: Path,
    live: bool = True,
    cadence: str = "weekday",
    announce_previous: bool = True,
    previous_engine: str | None = None,
    run_date: date | None = None,
    sender: Callable[[Path, Iterable[str], str], bool] | None = None,
) -> int:
    """Motoru çalıştırır; veri hatasını yutar, kaydeder ve sonraki güne bırakır."""

    if not live:
        result = main_func()
        return int(result or 0)

    base = Path(base).resolve()
    database = base / "tarama_sureklilik.db"
    today = run_date or _now().date()
    started = date.fromisoformat(_initialize(database, engine, today))
    dependency = previous_engine or engine
    if dependency != engine:
        dependency_started = date.fromisoformat(_initialize(database, dependency, today))
    else:
        dependency_started = started
    previous = _previous_run_date(today, cadence)
    previous_status = _run_status(database, dependency, previous)
    previous_missing = previous >= dependency_started and (
        previous_status is None or previous_status.get("status") != "success"
    )
    send = sender or _send_to_targets

    old_skip = os.environ.get(SKIP_PREVIOUS_ENV)
    if previous_missing:
        os.environ[SKIP_PREVIOUS_ENV] = "1"
        if announce_previous:
            key = f"{previous.isoformat()}->{today.isoformat()}"
            reason = (
                str(previous_status.get("reason"))
                if previous_status
                else "Bilgisayar/VPS kapalıydı, görev çalışmadı veya veri alınamadı."
            )
            message = _missing_previous_message(label, previous, today, reason)
            print(message)
            if not _publication_exists(database, engine, "missing_previous", key):
                if send(base, targets, message):
                    _mark_published(database, engine, "missing_previous", key)

    try:
        result = main_func()
        code = int(result or 0)
        if code != 0:
            raise RuntimeError(f"Motor çıkış kodu {code}")
        _record_run(database, engine, today, "success", "Tarama tamamlandı.")
        return 0
    except Exception as exc:
        reason = _safe_reason(exc)
        print(f"[süreklilik] {label} tamamlanamadı: {reason}")
        if not isinstance(exc, DataUnavailable):
            traceback.print_exc()
        _record_run(database, engine, today, "failed", reason)
        key = today.isoformat()
        message = _failure_message(label, today, reason)
        print(message)
        if not _publication_exists(database, engine, "failure", key):
            if send(base, targets, message):
                published_at = _mark_published(database, engine, "failure", key)
                _record_run(
                    database,
                    engine,
                    today,
                    "failed",
                    reason,
                    published_at=published_at,
                )
        return 0
    finally:
        if old_skip is None:
            os.environ.pop(SKIP_PREVIOUS_ENV, None)
        else:
            os.environ[SKIP_PREVIOUS_ENV] = old_skip


def skip_previous_report() -> bool:
    return os.environ.get(SKIP_PREVIOUS_ENV) == "1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Tarama süreklilik sicilini güvenli biçimde başlatır")
    parser.add_argument("--initialize", nargs="+", metavar="ENGINE", required=True)
    parser.add_argument("--base", default=str(Path(__file__).resolve().parent))
    args = parser.parse_args()
    base = Path(args.base).resolve()
    today = _now().date()
    for engine in args.initialize:
        started = _initialize(base / "tarama_sureklilik.db", engine, today)
        print(f"{engine}: hizmet başlangıcı {started}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
