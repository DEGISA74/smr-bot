#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BIST veri kasası, karantina ve sağlayıcı trafiği için tek sağlık raporu."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from bist_data_store import QUARANTINE, load_manifest, store_status
from provider_traffic import provider_status


def collect_status() -> dict:
    base = store_status()
    manifest = load_manifest() or {}
    symbols = manifest.get("symbols", {})
    last_dates = {}
    provisional = 0
    warned = 0
    for entry in symbols.values():
        last = str(entry.get("last") or "bilinmiyor")[:10]
        last_dates[last] = last_dates.get(last, 0) + 1
        quality = entry.get("quality", {}) or {}
        provisional += int(quality.get("status") == "provisional")
        warned += int(bool(quality.get("warnings")))
    created = manifest.get("created_at")
    age_minutes = None
    if created:
        try:
            age_minutes = round((time.time() - datetime.fromisoformat(
                created.replace("Z", "+00:00")).timestamp()) / 60, 1)
        except Exception:
            pass
    recent_q = []
    for p in sorted(QUARANTINE.glob("*.json"), key=lambda x: x.stat().st_mtime,
                    reverse=True)[:10]:
        try:
            row = json.loads(p.read_text(encoding="utf-8"))
            recent_q.append({"file": p.name, "reason": row.get("reason"),
                             "rejected": len(row.get("rejected", {})),
                             "time": datetime.fromtimestamp(
                                 p.stat().st_mtime, timezone.utc).isoformat()})
        except Exception:
            recent_q.append({"file": p.name, "reason": "okunamadı"})
    traffic = provider_status()
    now = time.time()
    for p in traffic.get("providers", {}).values():
        p["cooldown_remaining_sec"] = max(
            0, round(float(p.get("cooldown_until", 0) or 0) - now))
    return {**base, "age_minutes": age_minutes,
            "latest_bar_distribution": dict(sorted(last_dates.items(), reverse=True)),
            "provisional_symbols": provisional, "warned_symbols": warned,
            "active_rejected": len(manifest.get("rejected", {})),
            "recent_quarantine": recent_q, "provider_traffic": traffic,
            "generated_at": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    print(json.dumps(collect_status(), ensure_ascii=False, indent=2))
