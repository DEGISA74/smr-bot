# -*- coding: utf-8 -*-
"""Kapanış verisi tamamlanınca BIST Master Scan'i güvenle sıraya alır.

Bu modül Streamlit ekranı çizmez. Yalnız aktif veri kasasını denetler, eksik
fiyat/hacim için mevcut resmî işleri en fazla birer kez ister ve uyarı zamanını
hesaplar. Böylece app.py sadece kullanıcıya ne olduğunu gösterir.
"""
from __future__ import annotations

import math
import os
import json
import subprocess
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

import pytz

from bist_calendar import is_trading_day
from bist_data_store import (VOLUME_CONTROLLED_SOURCES, VOLUME_OFFICIAL_SOURCES,
                              active_version_id, load_manifest)
from veri_saglik_audit import _beklenen_son_seans, collect_audit


ROOT = Path(__file__).resolve().parent
TZ_ISTANBUL = pytz.timezone("Europe/Istanbul")
CHECK_HOUR = 20
FIRST_WARNING_SECONDS = 5 * 60
FINAL_WARNING_SECONDS = 2 * 60
FINAL_WARNING_START_SECONDS = FIRST_WARNING_SECONDS - FINAL_WARNING_SECONDS
MIN_SCANNABLE_RATIO = 0.95
FINAL_VOLUME_MIN_RATIO = 0.85
COMPLETION_RECORD_PATH = ROOT / "logs" / "kapanis_master_scan_completion.json"
RUN_LOCK_PATH = ROOT / "logs" / "kapanis_master_scan_running.json"
RUN_LOCK_MAX_AGE_SECONDS = 6 * 60 * 60

# Ağır denetim 600+ parquet okur. Streamlit ekranının içinde çalışırsa, her
# otomatik yenilemede sayfayı yeniden başlatıp kullanıcıyı "Running" döngüsüne
# sokar. Bu nedenle denetim tek arka plan işinde yürür; ekran yalnız aktif
# veri kasasının hafif manifestini okur.
_AUDIT_CACHE: dict[str, dict[str, Any]] = {}
_AUDIT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kapanis-audit")
_AUDIT_LOCK = Lock()
_AUDIT_FUTURE: Future | None = None
_AUDIT_FUTURE_VERSION: str | None = None


def _istanbul_now(now: datetime | None = None) -> datetime:
    """Tarih damgasını tek yerde İstanbul saatine sabitler."""
    now = now or datetime.now(TZ_ISTANBUL)
    if now.tzinfo is None:
        return TZ_ISTANBUL.localize(now)
    return now.astimezone(TZ_ISTANBUL)


def _read_completion_record() -> dict[str, Any]:
    """Bozuk/eski kayıt otomasyonu kilitlemesin diye güvenli biçimde okur."""
    try:
        payload = json.loads(COMPLETION_RECORD_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def is_scan_completed_today(now: datetime | None = None) -> bool:
    """Akşam penceresinde biten tarama, aynı gece ikinci turu açmasın.

    ⚠ 1 Eyl 2026 — GÜN İÇİ TARAMA AKŞAMKİNİ SUSTURUYORDU. Kural yalnız
    "kayıt bugünden mi" diye soruyordu. Oysa gün ortasında elle koşulan her
    tur (test, kontrol, merak) da bu kaydı BUGÜNÜN tarihiyle yazıyor. Akşam
    20:00 otomasyonu onu görüp "bugün zaten tarandı" diyor ve taramayı HİÇ
    BAŞLATMIYORDU — üstelik sessizce: görünmez tarayıcı açılıyor, sayfa
    çiziliyor, hiçbir şey olmuyor, kimse fark etmiyor. 1 Eylül akşamı tam
    bu yaşandı: 15:00'teki bir test 20:00 turunu öldürdü.

    Engelin dayanağı da yoktu: ``load_scan_result`` kapanıştan (18:20) önce
    hesaplanmış hiçbir sonucu zaten kabul etmiyor. Yani kural, kullanılmayan
    bir sonuç uğruna günün gerçek turunu iptal ediyordu.

    Artık kayıt, AKŞAM PENCERESİ (``CHECK_HOUR``) açıldıktan sonra yazılmışsa
    geçerli sayılır. Görünmez oturumu yöneten ``master_scan_headless_session.ps1``
    aynı kontrolü 28 Ağu 2026'dan beri yapıyordu; kural buraya konmamıştı.
    ``completed_at`` yoksa veya okunamazsa eski davranışa düşülür — o taraf
    temkinli: mükerrer tarama açmaktansa açmamayı seçer.
    """
    current = _istanbul_now(now)
    record = _read_completion_record()
    if record.get("status") not in {"completed", "partial"}:
        return False
    if record.get("day") != current.strftime("%Y-%m-%d"):
        return False
    stamp = record.get("completed_at")
    if not stamp:
        return True
    try:
        written = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return True
    if written.tzinfo is None:
        written = TZ_ISTANBUL.localize(written)
    else:
        written = written.astimezone(TZ_ISTANBUL)
    window_open = current.replace(
        hour=CHECK_HOUR, minute=0, second=0, microsecond=0)
    return written >= window_open


def claim_scan_start(now: datetime | None = None) -> bool:
    """Aynı akşam yalnız bir ekran oturumunun otomatik taramayı başlatmasını sağlar."""
    current = _istanbul_now(now)
    day = current.strftime("%Y-%m-%d")
    payload = {"day": day, "claimed_at": current.isoformat(), "pid": os.getpid()}
    RUN_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(RUN_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            existing = json.loads(RUN_LOCK_PATH.read_text(encoding="utf-8"))
            claimed_at = datetime.fromisoformat(str(existing.get("claimed_at", "")))
            if claimed_at.tzinfo is None:
                claimed_at = TZ_ISTANBUL.localize(claimed_at)
            age = (current - claimed_at.astimezone(TZ_ISTANBUL)).total_seconds()
            if existing.get("day") == day and 0 <= age < RUN_LOCK_MAX_AGE_SECONDS:
                return False
        except (OSError, ValueError, TypeError):
            pass
        try:
            RUN_LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            return False
        return claim_scan_start(current)
    try:
        os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        return True
    finally:
        os.close(fd)


def release_scan_start() -> None:
    """Tamamlanan veya hata alan otomatik taramanın başlangıç kilidini bırakır."""
    try:
        RUN_LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def mark_scan_completed(
    now: datetime | None = None,
    category: str = "BIST 500",
    critical_failures: list[str] | None = None,
) -> bool:
    """BIST taramasının tam/kısmi sonucunu atomik günlük kayda geçirir."""
    current = _istanbul_now(now)
    failures = sorted({str(item).strip() for item in (critical_failures or []) if str(item).strip()})
    record = {
        "status": "partial" if failures else "completed",
        "day": current.strftime("%Y-%m-%d"),
        "completed_at": current.isoformat(),
        "category": str(category).strip(),
        "critical_failures": failures,
    }
    temporary = COMPLETION_RECORD_PATH.with_suffix(".tmp")
    try:
        COMPLETION_RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, COMPLETION_RECORD_PATH)
        release_scan_start()
        return True
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def is_check_window(now: datetime | None = None) -> bool:
    """Yalnız BIST işlem günlerinde saat 20:00 sonrası otomasyonu açar."""
    now = now or datetime.now(TZ_ISTANBUL)
    if now.tzinfo is None:
        now = TZ_ISTANBUL.localize(now)
    else:
        now = now.astimezone(TZ_ISTANBUL)
    return bool(is_trading_day(now) and now.hour >= CHECK_HOUR)


def _entry_is_index(symbol: str) -> bool:
    clean = str(symbol).replace(".IS", "").upper()
    return clean.startswith(("XU", "XB", "XT", "XY", "XK", "XG", "XI", "XUS"))


def _read_or_start_audit(version_id: str, now: datetime) -> tuple[dict[str, Any] | None, bool]:
    """Ağır denetimi ekranı kilitlemeden başlatır veya tamamlanan sonucu verir."""
    global _AUDIT_FUTURE, _AUDIT_FUTURE_VERSION
    with _AUDIT_LOCK:
        cached = _AUDIT_CACHE.get(version_id)
        if cached is not None:
            return deepcopy(cached), False

        if _AUDIT_FUTURE is not None and _AUDIT_FUTURE.done():
            completed_version = _AUDIT_FUTURE_VERSION
            try:
                completed = _AUDIT_FUTURE.result()
            except Exception:
                completed = None
            _AUDIT_FUTURE = None
            _AUDIT_FUTURE_VERSION = None
            if completed is not None and completed_version:
                _AUDIT_CACHE[completed_version] = completed
                # Aynı akşam çok sayıda küçük veri sürümü oluşabilir; yalnız son
                # birkaç fotoğrafı tutmak yeterli, bellek büyümez.
                while len(_AUDIT_CACHE) > 3:
                    _AUDIT_CACHE.pop(next(iter(_AUDIT_CACHE)))
            cached = _AUDIT_CACHE.get(version_id)
            if cached is not None:
                return deepcopy(cached), False

        if _AUDIT_FUTURE is None:
            _AUDIT_FUTURE = _AUDIT_EXECUTOR.submit(collect_audit, now)
            _AUDIT_FUTURE_VERSION = version_id
        return None, True


def _manifest_snapshot(version_id: str, expected: str) -> dict[str, Any]:
    """Onaylı sürümün hızlı fiyat/hacim fotoğrafını çıkarır; parquet okumaz."""
    manifest = load_manifest(version_id) or {}
    symbols = manifest.get("symbols", {}) or {}
    rejected = manifest.get("rejected", {}) or {}
    stale = [
        (sym, f"bayat: son {str((entry or {}).get('last') or '')[:10]}, beklenen {expected}")
        for sym, entry in symbols.items()
        if str((entry or {}).get("last") or "")[:10] != expected
    ]
    stale_symbols = {sym for sym, _ in stale}
    rejected_symbols = set(rejected)
    critical_symbols = {"XU100.IS", "XU030.IS", "XBANK.IS", "XTUMY.IS", "XUSIN.IS"}
    critical_rejected = sorted(
        sym for sym in critical_symbols
        if sym in stale_symbols or sym in rejected_symbols or sym not in symbols
    )
    fresh_entries = [
        (sym, entry) for sym, entry in symbols.items()
        if sym not in stale_symbols
    ]
    volume_entries = [(sym, entry) for sym, entry in fresh_entries if not _entry_is_index(sym)]
    official_volume = []
    controlled_volume = []
    for sym, entry in volume_entries:
        source = str((entry or {}).get("field_sources", {}).get("Volume") or "").lower()
        if source in VOLUME_OFFICIAL_SOURCES:
            official_volume.append(sym)
        elif source in VOLUME_CONTROLLED_SOURCES:
            controlled_volume.append(sym)
    usable_volume = official_volume + controlled_volume
    total = len(symbols) + len(rejected_symbols - set(symbols))
    min_scannable = math.ceil(total * MIN_SCANNABLE_RATIO)
    return {
        "total": total,
        "symbols": symbols,
        "stale": stale,
        "rejected": [(sym, "onaylı kasada reddedildi") for sym in sorted(rejected_symbols)],
        "critical_rejected": critical_rejected,
        "fresh_count": len(fresh_entries),
        "price_ready": not critical_rejected and len(fresh_entries) >= min_scannable,
        # Eski tüketiciler için final_volume_count kullanılabilir toplamı taşır;
        # resmî ve kontrollü yedek ayrımı ayrıca açıkça yayınlanır.
        "final_volume_count": len(usable_volume),
        "official_volume_count": len(official_volume),
        "controlled_volume_count": len(controlled_volume),
        "volume_total": len(volume_entries),
    }


def collect_close_health(now: datetime | None = None) -> dict[str, Any]:
    """Master Scan için hızlı ekran kapısı + arka plan derin denetimi ölçer."""
    now = now or datetime.now(TZ_ISTANBUL)
    version_id = active_version_id()
    expected = str(_beklenen_son_seans(now))
    manifest = _manifest_snapshot(version_id, expected)
    audit, audit_pending = _read_or_start_audit(version_id, now)
    volume_total = manifest["volume_total"]
    volume_ratio = (manifest["final_volume_count"] / volume_total) if volume_total else 0.0
    volume_ready = volume_ratio >= FINAL_VOLUME_MIN_RATIO

    if audit is None:
        # Derin kontrol bitene dek Scan kapalıdır; buna rağmen manifestteki bayat
        # fiyat ve yetersiz hacim kapsamı için talep hemen ve yalnız bir kez yapılabilir.
        blocked = sorted({*(sym for sym, _ in manifest["stale"]), *(sym for sym, _ in manifest["rejected"])})
        return {
            "version_id": version_id,
            "expected_date": expected,
            "total": manifest["total"],
            "clean_count": manifest["fresh_count"],
            "stale": manifest["stale"],
            "rejected": manifest["rejected"],
            "critical_rejected": manifest["critical_rejected"],
            "blocked_symbols": blocked,
            "price_ready": manifest["price_ready"],
            "price_refresh_needed": bool(manifest["stale"]),
            "volume_ready": volume_ready,
            "final_volume_count": manifest["final_volume_count"],
            "official_volume_count": manifest["official_volume_count"],
            "controlled_volume_count": manifest["controlled_volume_count"],
            "volume_total": volume_total,
            "volume_ratio": volume_ratio,
            "audit_pending": audit_pending,
            # Saat 20:00 için fiyat bütünlüğü kapısı belirleyicidir. Resmî
            # hacim gecikmesi ayrı alarm/yenileme işidir; taramayı rehin almaz.
            "scan_ready": manifest["price_ready"],
        }

    stale = {sym: reason for sym, reason in manifest["stale"]}
    stale.update({sym: reason for sym, reason in audit["sari"]})
    rejected = {sym: reason for sym, reason in manifest["rejected"]}
    rejected.update({sym: reason for sym, reason in audit["kirmizi"]})
    critical_rejected = sorted(set(manifest["critical_rejected"]) | set(audit["kritik_kirmizi"]))
    blocked = sorted(set(stale) | set(rejected))
    min_scannable = math.ceil(audit["toplam"] * MIN_SCANNABLE_RATIO)
    price_ready = not critical_rejected and len(audit["yesil"]) >= min_scannable
    return {
        "version_id": version_id,
        "expected_date": expected,
        "total": audit["toplam"],
        "clean_count": len(audit["yesil"]),
        "stale": sorted(stale.items()),
        "rejected": sorted(rejected.items()),
        "critical_rejected": critical_rejected,
        "blocked_symbols": blocked,
        "price_ready": price_ready,
        "price_refresh_needed": bool(stale),
        "volume_ready": volume_ready,
        "final_volume_count": manifest["final_volume_count"],
        "official_volume_count": manifest["official_volume_count"],
        "controlled_volume_count": manifest["controlled_volume_count"],
        "volume_total": volume_total,
        "volume_ratio": volume_ratio,
        "audit_pending": False,
        "scan_ready": price_ready,
    }


def next_poll_seconds(phase: str, remaining_seconds: int | None) -> int:
    """Ekranı huzursuz etmeden yalnız sonraki gerçek otomasyon eşiğinde yeniler."""
    if phase == "waiting_for_data":
        return 60
    if phase == "first_warning":
        return max(5, int(remaining_seconds or FIRST_WARNING_SECONDS) - FINAL_WARNING_SECONDS)
    if phase == "final_warning":
        return max(5, int(remaining_seconds or FINAL_WARNING_SECONDS))
    return 60


def request_missing_data(price_needed: bool, volume_needed: bool) -> dict[str, bool]:
    """Resmî kapanış işlerini arka planda birer kez başlatır; veri yazmaz."""
    result = {"price_requested": False, "volume_requested": False}
    if os.name == "nt":
        # Yerel ayna ekranda istek, mevcut güvenli SSH sarmalayıcılarından gider.
        jobs = (
            ("price_requested", price_needed, ["cmd.exe", "/c", str(ROOT / "run_settle.bat")]),
            ("volume_requested", volume_needed, ["cmd.exe", "/c", str(ROOT / "run_finalize_volume.bat")]),
        )
    else:
        # VPS'te yeni tam Yahoo turu açılmaz. Mevcut acil-liste fiyat turu ile
        # hacim kesinleştiricisi, cron'daki AYNI kilitlerle çağrılır.
        jobs = (
            ("price_requested", price_needed,
             ["/usr/bin/flock", "-n", str(ROOT / "fetcher-price.lock"),
              sys.executable, "fetcher.py", "acil"]),
            ("volume_requested", volume_needed,
             ["/usr/bin/flock", "-n", str(ROOT / "fetcher-volume.lock"),
              sys.executable, "finalize_volume.py"]),
        )
    for key, needed, command in jobs:
        if not needed:
            continue
        try:
            kwargs = dict(
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                command,
                **kwargs,
            )
            result[key] = True
        except OSError:
            pass
    return result


def advance_schedule(state: dict[str, Any] | None, health: dict[str, Any],
                     now: datetime | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Kapanış kontrolü → 5dk yumuşak uyarı → son 2dk zorunlu uyarı akışı."""
    now = now or datetime.now(TZ_ISTANBUL)
    if now.tzinfo is None:
        now = TZ_ISTANBUL.localize(now)
    else:
        now = now.astimezone(TZ_ISTANBUL)
    state = dict(state or {})
    today = now.strftime("%Y-%m-%d")
    if state.get("day") != today:
        state = {"day": today}

    if state.get("started", False):
        return state, {
            "phase": "done",
            "request_price": False,
            "request_volume": False,
            "remaining_seconds": None,
        }

    request_price = bool(
        health.get("price_refresh_needed", not health.get("price_ready"))
        and not state.get("price_requested", False)
    )
    request_volume = bool(
        not health.get("volume_ready") and not state.get("volume_requested", False)
    )

    if not health.get("price_ready"):
        state.pop("ready_at", None)
        action = {
            "phase": "waiting_for_data",
            "request_price": request_price,
            "request_volume": request_volume,
            "remaining_seconds": None,
        }
        return state, action

    if "ready_at" not in state:
        state["ready_at"] = now.timestamp()
    elapsed = max(0, int(now.timestamp() - float(state["ready_at"])))
    remaining = max(0, FIRST_WARNING_SECONDS - elapsed)
    if elapsed >= FIRST_WARNING_SECONDS:
        phase = "start"
    elif elapsed >= FINAL_WARNING_START_SECONDS:
        phase = "final_warning"
    else:
        phase = "first_warning"
    return state, {
        "phase": phase,
        "request_price": request_price,
        "request_volume": request_volume,
        "remaining_seconds": remaining,
    }
