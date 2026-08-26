# -*- coding: utf-8 -*-
"""Master Scan fotoğrafını karşılaştırılabilir, küçük bir gölge manifestine çevirir.

Bu araç yalnız daha önce uygulamanın ürettiği yerel pickle fotoğrafını okur.
Sinyal, fiyat, skor veya veritabanı yazmaz. Çıktıdaki sembol parmak izleri,
VPS gölge motorunun aynı kapanışta aynı sonucu üretip üretmediğini ölçmek içindir.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytz


TZ_ISTANBUL = pytz.timezone("Europe/Istanbul")
SYMBOL_COLUMNS = ("Sembol", "Hisse", "Ticker", "Symbol", "symbol", "tk")
KEY_TO_SCAN = {
    "ict_scan_data": "ict_sniper",
    "nadir_firsat_scan_data": "nadir_firsat",
    "golden_results": "altin_setup",
    "platin_results": "platin_setup",
    "tekli_altin_results": "tekli_altin",
    "accum_data": "gizli_birikim",
    "scan_data": "radar1",
    "radar2_data": "radar2",
    "harmonic_confluence_data": "harmonik_confluence",
    "minervini_data": "minervini",
    "rs_leaders_data": "rs_leaders",
    "guclu_donus_data": "guclu_donus",
    "wilder_divergence_data": "wilder_pozitif_uyumsuzluk",
    "stp_uyanis_data": "stp_uyanis",
    "prelaunch_bos_data": "prelaunch_bos",
}


def _clean_symbol(value: object) -> str:
    return str(value or "").strip().upper().replace(".IS", "")


def _symbols(frame: Any) -> list[str]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    column = next((name for name in SYMBOL_COLUMNS if name in frame.columns), None)
    if column is None:
        return []
    return sorted({_clean_symbol(value) for value in frame[column].tolist() if _clean_symbol(value)})


def _fingerprint(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _component(name: str, frame: Any) -> dict[str, Any]:
    symbols = _symbols(frame)
    rows = int(len(frame)) if isinstance(frame, pd.DataFrame) else 0
    return {
        "name": name,
        "rows": rows,
        "unique_symbols": len(symbols),
        "symbol_fingerprint": _fingerprint(symbols),
        "symbols": symbols,
    }


def build_manifest(snapshot_path: Path) -> dict[str, Any]:
    raw = pd.read_pickle(snapshot_path)
    payload = raw.get("data", raw) if isinstance(raw, dict) else raw
    if not isinstance(payload, dict):
        raise ValueError("Master Scan fotoğrafı sözlük biçiminde değil")
    components: dict[str, dict[str, Any]] = {}
    for key, scan_type in KEY_TO_SCAN.items():
        if key in payload:
            components[scan_type] = _component(scan_type, payload[key])

    early = payload.get("erken_radar_data")
    if isinstance(early, pd.DataFrame):
        if "ScenarioId" in early.columns:
            for scenario_id, subset in early.groupby(early["ScenarioId"].astype(str), dropna=False):
                scan_type = f"er_{scenario_id}"
                components[scan_type] = _component(scan_type, subset)
        else:
            components["erken_radar"] = _component("erken_radar", early)

    leadership = payload.get("liderlik_yolculugu_data")
    if isinstance(leadership, pd.DataFrame):
        if "Liderlik_Tarama" in leadership.columns:
            for scan_type, subset in leadership.groupby(leadership["Liderlik_Tarama"].astype(str), dropna=False):
                components[scan_type] = _component(str(scan_type), subset)
        else:
            components["liderlik"] = _component("liderlik", leadership)

    all_rows = [f"{name}:{data['rows']}:{data['symbol_fingerprint']}" for name, data in sorted(components.items())]
    timestamp = raw.get("ts") if isinstance(raw, dict) else None
    return {
        "schema_version": 1,
        "generated_at": datetime.now(TZ_ISTANBUL).isoformat(),
        "source_snapshot": str(snapshot_path),
        "source_snapshot_timestamp": str(timestamp) if timestamp is not None else None,
        "components": components,
        "master_fingerprint": _fingerprint(all_rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    manifest = build_manifest(args.snapshot)
    text = json.dumps(manifest, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
