#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Üretim verisine dokunmadan BIST sürüm kapısının kritik senaryolarını sınar."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd


def _frame() -> pd.DataFrame:
    idx = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=12)
    close = pd.Series(range(100, 112), index=idx, dtype="float64")
    return pd.DataFrame({"Open": close - .5, "High": close + 1,
                         "Low": close - 1, "Close": close,
                         "Volume": 1_000_000.0}, index=idx)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        legacy = root / "veriler"; store_dir = root / "store"
        legacy.mkdir()
        os.environ["SMR_CACHE_DIR"] = str(legacy)
        os.environ["SMR_BIST_STORE"] = str(store_dir)
        import bist_data_store as s
        s.EVENT_LOG = root / "events.jsonl"

        original = _frame()
        original.to_parquet(legacy / "TEST.IS_1d.parquet")
        boot = s.ensure_bootstrap()
        assert boot["symbols"]["TEST.IS"]["rows"] == len(original)
        v0 = s.active_version_id()

        last = original.index[-1]
        price = original.loc[[last], ["Open", "High", "Low", "Close"]].copy()
        price["Close"] += .5; price["High"] += .5
        volume = pd.DataFrame({"Volume": [1_250_000.0]}, index=[last])
        good = s.promote_batch({"TEST.IS": {
            "price_df": price, "price_source": "yahoo",
            "volume_df": volume, "volume_source": "isyatirim",
        }}, reason="selftest_good", max_reject_ratio=.9)
        assert good["ok"] and good["version_id"] != v0
        v1 = good["version_id"]
        assert float(s.read_active("TEST.IS").loc[last, "Volume"]) == 1_250_000.0

        malformed = price.copy(); malformed["Low"] = malformed["High"] + 10
        bad = s.promote_batch({"TEST.IS": {
            "price_df": malformed, "price_source": "yahoo",
        }}, reason="selftest_bad", max_reject_ratio=.9)
        assert not bad["ok"] and s.active_version_id() == v1

        old_day = original.index[0]
        locked = original.loc[[old_day], ["Open", "High", "Low", "Close"]].copy()
        locked += 5
        no_change = s.promote_batch({"TEST.IS": {
            "price_df": locked, "price_source": "yahoo",
        }}, reason="selftest_old_locked", max_reject_ratio=.9)
        assert no_change["ok"] and no_change.get("no_change")
        assert float(s.read_active("TEST.IS").loc[old_day, "Close"]) == float(original.loc[old_day, "Close"])

        rb = s.activate_version(v0)
        assert rb["ok"] and s.active_version_id() == v0
        assert float(s.read_active("TEST.IS").loc[last, "Volume"]) == 1_000_000.0

        import bist_exchange as x
        package_price = original.loc[[last], ["Open", "High", "Low", "Close"]].copy()
        package_price["Close"] += .25; package_price["High"] += .25
        candidate_zip = x.create_candidate_package({"TEST.IS": {
            "price_df": package_price, "price_source": "yahoo_settled",
        }}, source="selftest_local", reason="selftest_candidate",
            parent_version=v0, output=root / "candidate.zip")
        applied = x.apply_candidate_package(candidate_zip)
        assert applied["ok"] and applied["version_id"] != v0
        v2 = applied["version_id"]

        bundle = root / "approved.zip"
        exported = x.export_active_bundle(bundle)
        assert exported["ok"] and exported["version_id"] == v2
        s.activate_version(v0)
        imported = x.import_approved_bundle(bundle)
        assert imported["ok"] and s.active_version_id() == v2
        stale = x.apply_candidate_package(candidate_zip)
        assert not stale["ok"] and stale["reason"] == "stale_parent"

        import provider_traffic as traffic
        old_state, old_lock = traffic.STATE_FILE, traffic.LOCK_FILE
        traffic.STATE_FILE = root / "traffic.json"
        traffic.LOCK_FILE = root / "traffic.lock"
        try:
            traffic.acquire_slot("isyatirim", max_wait=1, priority="probe")
            traffic.record_success("isyatirim")
            traffic.record_failure("isyatirim", status_code=429,
                                   error="selftest", retry_after=2)
            cooldown_open = False
            try:
                traffic.acquire_slot("isyatirim", max_wait=.1)
            except traffic.ProviderCooldown:
                cooldown_open = True
            assert cooldown_open
        finally:
            traffic.STATE_FILE, traffic.LOCK_FILE = old_state, old_lock
        print("PASS: staging, doğrulama, eski-bar kilidi, karantina, rollback, "
              "aday paketi, onaylı sürüm senkronu ve 429 ortak sigortası")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
