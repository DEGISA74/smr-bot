# -*- coding: utf-8 -*-
"""VPS Master Scan geçişinin ilk, yazmayan gölge aşaması.

Bu dosya sinyal, skor veya patron.db yazmaz. Yalnız VPS'teki onaylı kapanış
fotoğrafını denetler ve o akşam bağımsız Master Scan başlatılabilecek durumda
olup olmadığını atomik bir iş defterine kaydeder. Gerçek tarama motoru ancak
aynı veriyle lokal sonuçlar birkaç gün birebir karşılaştırıldıktan sonra buraya
bağlanacaktır.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pytz

import kapanis_master_otomasyon as close_gate
from bist_calendar import is_trading_day
from bist_data_store import active_version_id
from veri_saglik_audit import _beklenen_son_seans, collect_audit


ROOT = Path(__file__).resolve().parent
TZ_ISTANBUL = pytz.timezone("Europe/Istanbul")
JOB_DIR = ROOT / "logs" / "master_scan_jobs"
LATEST_PATH = ROOT / "logs" / "master_scan_shadow_latest.json"
LOCK_PATH = ROOT / "master_scan_shadow.lock"


def _istanbul_now(now: datetime | None = None) -> datetime:
    value = now or datetime.now(TZ_ISTANBUL)
    return TZ_ISTANBUL.localize(value) if value.tzinfo is None else value.astimezone(TZ_ISTANBUL)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def _single_shadow_run(timeout_seconds: float = 15.0) -> Iterator[None]:
    """Aynı dakikadaki cron çakışmasını engeller; eski kilit 30 dk sonra güvenle düşer."""
    started = time.monotonic()
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            handle = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "started_at": _istanbul_now().isoformat()}, stream)
            break
        except FileExistsError:
            try:
                if time.time() - LOCK_PATH.stat().st_mtime > 30 * 60:
                    os.replace(LOCK_PATH, LOCK_PATH.with_suffix(".stale"))
                    continue
            except OSError:
                pass
            if time.monotonic() - started >= timeout_seconds:
                raise TimeoutError("Gölge Master Scan zaten çalışıyor")
            time.sleep(0.25)
    try:
        yield
    finally:
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            pass


def build_shadow_record(now: datetime | None = None) -> dict[str, Any]:
    """Kapanış kasasını doğrudan denetler; hiçbir piyasa verisi veya DB değiştirmez."""
    current = _istanbul_now(now)
    expected = str(_beklenen_son_seans(current))
    record: dict[str, Any] = {
        "schema_version": 1,
        "mode": "shadow",
        "checked_at": current.isoformat(),
        "day": current.strftime("%Y-%m-%d"),
        "expected_session": expected,
        "category": "BIST 500",
        "action": "do_not_run",
        "reason": "",
        "data_version": None,
        "health": {},
    }
    if not is_trading_day(current):
        record["reason"] = "BIST işlem günü değil"
        return record
    if current.hour < close_gate.CHECK_HOUR:
        record["reason"] = f"Kapanış kapısı henüz açılmadı ({close_gate.CHECK_HOUR}:00 sonrası gerekir)"
        return record
    try:
        version_id = active_version_id()
        manifest = close_gate._manifest_snapshot(version_id, expected)
        audit = collect_audit(current)
        total = int(audit.get("toplam", 0))
        clean = len(audit.get("yesil", []))
        minimum = math.ceil(total * close_gate.MIN_SCANNABLE_RATIO)
        critical = sorted(set(manifest.get("critical_rejected", [])) | set(audit.get("kritik_kirmizi", [])))
        price_ready = not critical and clean >= minimum
        health = {
            "total": total,
            "clean_count": clean,
            "minimum_clean_count": minimum,
            "critical_rejected": critical,
            "sari_count": len(audit.get("sari", [])),
            "kirmizi_count": len(audit.get("kirmizi", [])),
            "price_ready": price_ready,
            "manifest_fresh_count": manifest.get("fresh_count", 0),
            "manifest_total": manifest.get("total", 0),
        }
        record["data_version"] = version_id
        record["health"] = health
        if price_ready:
            record["action"] = "would_run"
            record["reason"] = "VPS onaylı kapanış fotoğrafı bağımsız Master Scan için hazır"
        else:
            record["reason"] = "Kapanış verisi güvenlik kapısından geçmedi"
    except Exception as exc:
        record["reason"] = f"Gölge veri denetimi hatası: {type(exc).__name__}: {exc}"
    return record


def run_shadow(now: datetime | None = None) -> dict[str, Any]:
    with _single_shadow_run():
        record = build_shadow_record(now)
        dated = JOB_DIR / f"{record['day'].replace('-', '')}_shadow.json"
        _atomic_json(dated, record)
        _atomic_json(LATEST_PATH, record)
        return record


def main() -> int:
    parser = argparse.ArgumentParser(description="VPS Master Scan yazmayan gölge kapısı")
    parser.add_argument("--json", action="store_true", help="yalnız JSON çıktı ver")
    args = parser.parse_args()
    result = run_shadow()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("action") in {"would_run", "do_not_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
