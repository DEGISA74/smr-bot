# -*- coding: utf-8 -*-
"""Lokal ve VPS Master Scan manifestlerini deterministik karşılaştırır."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz


TZ_ISTANBUL = pytz.timezone("Europe/Istanbul")


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError(f"Geçersiz manifest: {path}")
    return data


def compare(local: dict[str, Any], vps: dict[str, Any]) -> dict[str, Any]:
    local_components = local.get("components", {}) or {}
    vps_components = vps.get("components", {}) or {}
    names = sorted(set(local_components) | set(vps_components))
    differences = []
    for name in names:
        left, right = local_components.get(name), vps_components.get(name)
        if left is None or right is None:
            differences.append({"component": name, "kind": "missing", "local": bool(left), "vps": bool(right)})
            continue
        if int(left.get("rows", 0)) != int(right.get("rows", 0)):
            differences.append({"component": name, "kind": "row_count", "local": left.get("rows"), "vps": right.get("rows")})
        if left.get("symbol_fingerprint") != right.get("symbol_fingerprint"):
            local_symbols = set(left.get("symbols", []))
            vps_symbols = set(right.get("symbols", []))
            differences.append({
                "component": name,
                "kind": "symbols",
                "only_local": sorted(local_symbols - vps_symbols),
                "only_vps": sorted(vps_symbols - local_symbols),
            })
    return {
        "schema_version": 1,
        "compared_at": datetime.now(TZ_ISTANBUL).isoformat(),
        "same": not differences,
        "local_master_fingerprint": local.get("master_fingerprint"),
        "vps_master_fingerprint": vps.get("master_fingerprint"),
        "differences": differences,
        "component_count": len(names),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("local", type=Path)
    parser.add_argument("vps", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = compare(load_manifest(args.local), load_manifest(args.vps))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["same"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
