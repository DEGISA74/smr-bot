# -*- coding: utf-8 -*-
"""Yerel Master Scan kaydindan okunan sirket buyuklugu deposu.

Bu modul tarama sirasinda ag istegi yapmaz. Sirket buyuklugu sadece daha once
tamamlanmis bir Master Scan fotografından uretilen JSON dosyasindan okunur.
"""
from __future__ import annotations

import json
import pickle
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE_PATH = ROOT_DIR / "veriler" / "scan_cache" / "market_cap_cache.json"


def _ticker_key(ticker: object) -> str:
    """Ayni hisseyi farkli harf bicimlerinde tek anahtara indirger."""
    return str(ticker or "").strip().upper()


def load_market_cap_map(cache_path: Optional[Path] = None) -> dict[str, float]:
    """Yerel depodan pozitif piyasa degerlerini okur; ag istegi yapmaz."""
    path = Path(cache_path or DEFAULT_CACHE_PATH)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_values = payload.get("market_caps", {})
    except (OSError, ValueError, TypeError):
        return {}

    result: dict[str, float] = {}
    for ticker, value in raw_values.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            result[_ticker_key(ticker)] = number
    return result


def seed_market_cap_cache_from_snapshot(
    snapshot_path: Path,
    cache_path: Optional[Path] = None,
) -> dict[str, int]:
    """Tamamlanmis Master Scan fotografındaki M.Cap alanlarini yerel depoya yazar."""
    source = Path(snapshot_path)
    target = Path(cache_path or DEFAULT_CACHE_PATH)
    with source.open("rb") as handle:
        snapshot = pickle.load(handle)
    data = snapshot.get("data", {}) if isinstance(snapshot, dict) else {}

    market_caps: dict[str, float] = {}
    scanned_rows = 0
    for result_name in ("golden_results", "platin_results"):
        frame = data.get(result_name)
        if not isinstance(frame, pd.DataFrame) or not {"Hisse", "M.Cap"}.issubset(frame.columns):
            continue
        for ticker, market_cap in frame[["Hisse", "M.Cap"]].itertuples(index=False, name=None):
            scanned_rows += 1
            try:
                value = float(market_cap)
            except (TypeError, ValueError):
                continue
            if value > 0:
                market_caps[_ticker_key(ticker)] = value

    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "completed_master_scan_snapshot",
        "seeded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "snapshot": source.name,
        "market_caps": market_caps,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"rows_scanned": scanned_rows, "tickers_saved": len(market_caps)}
