# -*- coding: utf-8 -*-
"""Sağlayıcılar için süreçler-arası ortak hız ve sigorta yöneticisi."""
from __future__ import annotations

import json
import os
import random
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
HEALTH_DIR = ROOT / "health"
HEALTH_DIR.mkdir(exist_ok=True)
STATE_FILE = HEALTH_DIR / "provider_traffic.json"
LOCK_FILE = HEALTH_DIR / ".provider_traffic.lock"

_INTERVALS = {
    "isyatirim": float(os.environ.get("ISY_MIN_INTERVAL", "0.90")),
    "yahoo": float(os.environ.get("YAHOO_MIN_INTERVAL", "0.12")),
    "borsapy": float(os.environ.get("BORSAPY_MIN_INTERVAL", "0.60")),
}
_COOLDOWNS = {
    "isyatirim": float(os.environ.get("ISY_COOLDOWN_SECONDS", "900")),
    "yahoo": float(os.environ.get("YAHOO_COOLDOWN_SECONDS", "300")),
    "borsapy": float(os.environ.get("BORSAPY_COOLDOWN_SECONDS", "600")),
}
_PER_MINUTE = {
    "isyatirim": int(os.environ.get("ISY_MAX_PER_MINUTE", "45")),
    "yahoo": int(os.environ.get("YAHOO_MAX_PER_MINUTE", "120")),
    "borsapy": int(os.environ.get("BORSAPY_MAX_PER_MINUTE", "60")),
}


class ProviderCooldown(RuntimeError):
    def __init__(self, provider: str, wait_seconds: float):
        self.provider = provider
        self.wait_seconds = max(0.0, float(wait_seconds))
        super().__init__(f"{provider} sigortası açık ({self.wait_seconds:.0f} sn)")


@contextmanager
def _file_lock(timeout: float = 20.0, stale_after: float = 120.0):
    deadline = time.time() + timeout
    fd = None
    while fd is None:
        try:
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {time.time()}".encode("ascii", "ignore"))
        except FileExistsError:
            try:
                if time.time() - LOCK_FILE.stat().st_mtime > stale_after:
                    LOCK_FILE.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.time() >= deadline:
                raise TimeoutError("sağlayıcı trafik kilidi alınamadı")
            time.sleep(0.05 + random.random() * 0.05)
    try:
        yield
    finally:
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass


def _load() -> dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("providers"), dict):
            return data
    except Exception:
        pass
    return {"schema": 1, "providers": {}, "updated_at": None}


def _save(data: dict[str, Any]) -> None:
    tmp = STATE_FILE.with_suffix(f".json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def _provider(data: dict[str, Any], name: str) -> dict[str, Any]:
    p = data["providers"].setdefault(name, {})
    for key, value in {
        "next_allowed": 0.0, "cooldown_until": 0.0,
        "consecutive_failures": 0, "success": 0, "failure": 0,
        "last_status": "new", "last_error": "", "last_request_at": 0.0,
        "last_success_at": 0.0,
        "recent_requests": [],
    }.items():
        p.setdefault(key, value)
    return p


def acquire_slot(provider: str, *, max_wait: float = 60.0,
                 priority: str = "normal") -> None:
    provider = str(provider).lower().strip()
    interval = max(0.05, _INTERVALS.get(provider, 1.0))
    deadline = time.time() + max(0.0, max_wait)
    while True:
        wait_for = 0.0
        with _file_lock():
            data = _load()
            p = _provider(data, provider)
            now = time.time()
            cooldown = float(p.get("cooldown_until", 0.0) or 0.0)
            if cooldown > now:
                raise ProviderCooldown(provider, cooldown - now)
            next_allowed = float(p.get("next_allowed", 0.0) or 0.0)
            recent = [float(x) for x in p.get("recent_requests", [])
                      if now - float(x) < 60.0]
            # Arka plan ölçümleri bütçenin yarısını aşamaz; kapanış/final işler
            # tam bütçeyi kullanabilir. Böylece kritik tur düşük önceliğin arkasında kalmaz.
            cap_ratio = {"probe": 0.50, "repair": 0.75,
                         "regular_fetch": 0.90}.get(priority, 1.0)
            minute_cap = max(1, int(_PER_MINUTE.get(provider, 60) * cap_ratio))
            minute_ready = len(recent) < minute_cap
            if next_allowed <= now and minute_ready:
                p["next_allowed"] = now + interval + random.uniform(0.04, 0.18)
                p["last_request_at"] = now
                p["last_priority"] = priority
                recent.append(now)
                p["recent_requests"] = recent
                data["updated_at"] = datetime.now(timezone.utc).isoformat()
                _save(data)
                return
            wait_for = max(0.0, next_allowed - now)
            if not minute_ready and recent:
                wait_for = max(wait_for, 60.0 - (now - min(recent)) + 0.05)
        if time.time() + wait_for > deadline:
            raise TimeoutError(f"{provider} istek bütçesi sıra süresi doldu")
        time.sleep(min(max(wait_for, 0.05), 1.0))


def record_success(provider: str) -> None:
    provider = str(provider).lower().strip()
    with _file_lock():
        data = _load()
        p = _provider(data, provider)
        p["success"] = int(p.get("success", 0)) + 1
        p["consecutive_failures"] = 0
        p["last_status"] = "ok"
        p["last_error"] = ""
        p["last_success_at"] = time.time()
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save(data)


def record_failure(provider: str, *, status_code: int | None = None,
                   error: str = "", retry_after: float | None = None,
                   kind: str = "error") -> float:
    provider = str(provider).lower().strip()
    with _file_lock():
        data = _load()
        p = _provider(data, provider)
        now = time.time()
        fails = int(p.get("consecutive_failures", 0)) + 1
        p["failure"] = int(p.get("failure", 0)) + 1
        p["consecutive_failures"] = fails
        p["last_status"] = str(status_code or kind)
        p["last_error"] = str(error)[:300]
        cooldown = 0.0
        if status_code in (403, 429):
            cooldown = float(retry_after or _COOLDOWNS.get(provider, 900.0))
        elif kind == "timeout" and fails >= 12:
            # 31 Agu 2026 - YAVAS sunucu BOZUK sunucu degildir. Eskiden 4 ardisik
            # zaman asimi saglayiciyi 15 dk'ya kadar kapatiyordu; acquire_slot da
            # BEKLEMEDEN hata firlattigi icin kalan yuzlerce sembol hic denenmeden
            # eleniyordu. Kismi yavaslama boylece TAM karartmaya donusuyordu.
            # Artik esik yuksek, ceza kisa: sunucu kendi kendine toparlar.
            cooldown = min(120.0, 10.0 * fails)
        elif kind == "connection" and fails >= 4:
            # Baglanti kurulamiyorsa gercek ariza - eski sert ceza korunur.
            cooldown = min(_COOLDOWNS.get(provider, 600.0), 60.0 * fails)
        elif kind in ("empty", "invalid") and fails >= 8:
            cooldown = min(_COOLDOWNS.get(provider, 600.0), 30.0 * fails)
        if cooldown:
            p["cooldown_until"] = max(float(p.get("cooldown_until", 0.0)), now + cooldown)
            p["next_allowed"] = max(float(p.get("next_allowed", 0.0)), now + cooldown)
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save(data)
        return cooldown


def provider_status(provider: str | None = None) -> dict[str, Any]:
    with _file_lock():
        data = _load()
    if provider is None:
        return data
    return dict(data.get("providers", {}).get(str(provider).lower(), {}))


def reset_provider(provider: str) -> None:
    with _file_lock():
        data = _load()
        data.setdefault("providers", {}).pop(str(provider).lower(), None)
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save(data)
