#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BIST aday paketi ve onaylı sürüm aktarımı.

Lokal bilgisayar yalnız aday paket üretir. VPS paketi mevcut aktif sürümle
karşılaştırıp doğrulama kapısından geçirir. VPS->lokal yönünde ise yalnız onaylı
manifest ve içerik-hash'i doğrulanmış nesneler kabul edilir; aktif işaret en son
değişir.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import bist_data_store as store


def _safe_extract(zf: zipfile.ZipFile, target: Path) -> None:
    root = target.resolve()
    for info in zf.infolist():
        dest = (target / info.filename).resolve()
        if root != dest and root not in dest.parents:
            raise ValueError("paket içinde geçersiz yol")
    zf.extractall(target)


def create_candidate_package(candidates: dict[str, dict[str, Any]], *,
                             source: str, reason: str,
                             parent_version: str | None = None,
                             output: str | Path | None = None) -> Path:
    parent = parent_version or store.active_version_id()
    package_id = store._version_id("candidate-package")
    out = Path(output) if output else store.OUTBOX / f"{package_id}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=store.STAGING) as td:
        work = Path(td)
        meta: dict[str, Any] = {
            "schema": 1, "type": "bist_candidate", "package_id": package_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "parent_version": parent, "source": source, "reason": reason,
            "candidates": {},
        }
        for raw_sym, raw_spec in sorted(candidates.items()):
            sym = store._symbol(raw_sym)
            spec = dict(raw_spec or {})
            entry = {k: v for k, v in spec.items()
                     if k not in {"price_df", "volume_df", "reference_df"}}
            safe_sym = sym.replace(".", "_")
            for key in ("price_df", "volume_df", "reference_df"):
                df = spec.get(key)
                if df is None or getattr(df, "empty", True):
                    continue
                rel = f"frames/{safe_sym}_{key}.parquet"
                path = work / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(path, compression="snappy")
                entry[key] = rel
            meta["candidates"][sym] = entry
        (work / "package.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp = out.with_suffix(out.suffix + f".{os.getpid()}.tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in work.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(work).as_posix())
        os.replace(tmp, out)
    return out


def apply_candidate_package(package: str | Path) -> dict[str, Any]:
    package = Path(package)
    with tempfile.TemporaryDirectory(dir=store.STAGING) as td:
        work = Path(td)
        with zipfile.ZipFile(package, "r") as zf:
            _safe_extract(zf, work)
        meta = json.loads((work / "package.json").read_text(encoding="utf-8"))
        if meta.get("type") != "bist_candidate" or meta.get("schema") != 1:
            raise ValueError("tanınmayan aday paketi")
        current = store.active_version_id()
        if meta.get("parent_version") != current:
            q = store.QUARANTINE / f"{meta.get('package_id', package.stem)}-stale.json"
            store._atomic_json(q, {"reason": "stale_parent",
                                   "package": str(package),
                                   "expected": current,
                                   "received": meta.get("parent_version")})
            return {"ok": False, "blocked": True, "reason": "stale_parent",
                    "active_version": current}
        candidates: dict[str, dict[str, Any]] = {}
        for sym, raw in meta.get("candidates", {}).items():
            spec = dict(raw)
            for key in ("price_df", "volume_df", "reference_df"):
                rel = spec.get(key)
                if rel:
                    spec[key] = pd.read_parquet(work / rel)
            candidates[sym] = spec
        return store.promote_batch(
            candidates, reason=str(meta.get("reason") or "candidate_package"),
            source_run={"package_id": meta.get("package_id"),
                        "source": meta.get("source"),
                        "created_at": meta.get("created_at")})


def export_active_bundle(output: str | Path, base_version: str | None = None) -> dict[str, Any]:
    manifest = store.ensure_bootstrap()
    base = store.load_manifest(base_version) if base_version else None
    base_hashes = {s: e.get("sha256") for s, e in (base or {}).get("symbols", {}).items()}
    changed = {s: e for s, e in manifest["symbols"].items()
               if base_hashes.get(s) != e.get("sha256")}
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=store.STAGING) as td:
        work = Path(td)
        (work / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        meta = {"schema": 1, "type": "bist_approved_bundle",
                "version_id": manifest["version_id"],
                "base_version": base_version if base else None,
                "object_count": len(changed),
                "created_at": datetime.now(timezone.utc).isoformat()}
        (work / "bundle.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        for entry in changed.values():
            rel = Path(entry["object"])
            src = store.STORE / rel
            if not src.exists() or store._sha256(src) != entry.get("sha256"):
                raise ValueError(f"onaylı nesne bozuk/eksik: {rel}")
            dst = work / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        tmp = out.with_suffix(out.suffix + f".{os.getpid()}.tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in work.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(work).as_posix())
        os.replace(tmp, out)
    return {"ok": True, "version_id": manifest["version_id"],
            "base_version": meta["base_version"], "objects": len(changed),
            "output": str(out)}


def import_approved_bundle(package: str | Path) -> dict[str, Any]:
    package = Path(package)
    with tempfile.TemporaryDirectory(dir=store.STAGING) as td:
        work = Path(td)
        with zipfile.ZipFile(package, "r") as zf:
            _safe_extract(zf, work)
        meta = json.loads((work / "bundle.json").read_text(encoding="utf-8"))
        manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
        if meta.get("type") != "bist_approved_bundle" or meta.get("schema") != 1:
            raise ValueError("tanınmayan onaylı sürüm paketi")
        if manifest.get("version_id") != meta.get("version_id"):
            raise ValueError("manifest sürüm kimliği uyuşmuyor")
        with store.writer_lock():
            included: list[tuple[Path, Path]] = []
            for sym, entry in manifest.get("symbols", {}).items():
                rel = Path(entry["object"])
                incoming = work / rel
                existing = store.STORE / rel
                src = incoming if incoming.exists() else existing
                if not src.exists() or store._sha256(src) != entry.get("sha256"):
                    raise ValueError(f"paket eksik/bozuk nesne: {sym}")
                if incoming.exists():
                    included.append((incoming, existing))
            for src, dst in included:
                dst.parent.mkdir(parents=True, exist_ok=True)
                tmp = dst.with_suffix(dst.suffix + f".{os.getpid()}.tmp")
                shutil.copy2(src, tmp)
                os.replace(tmp, dst)
            store._atomic_json(store.MANIFESTS / f"{manifest['version_id']}.json", manifest)
            for sym, entry in manifest.get("symbols", {}).items():
                obj = store.STORE / entry["object"]
                if any(dst == obj for _, dst in included):
                    store._materialize_legacy(sym, obj)
            store._atomic_json(store.ACTIVE_FILE, {
                "version_id": manifest["version_id"],
                "activated_at": datetime.now(timezone.utc).isoformat(),
                "imported": True})
            store._event("bundle_imported", {"version_id": manifest["version_id"],
                                              "objects": len(included)})
        return {"ok": True, "version_id": manifest["version_id"],
                "objects": len(included)}


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply-candidate"); a.add_argument("package")
    e = sub.add_parser("export"); e.add_argument("output"); e.add_argument("--base")
    i = sub.add_parser("import"); i.add_argument("package")
    r = sub.add_parser("rollback"); r.add_argument("version")
    args = ap.parse_args()
    if args.cmd == "apply-candidate": result = apply_candidate_package(args.package)
    elif args.cmd == "export": result = export_active_bundle(args.output, args.base)
    elif args.cmd == "import": result = import_approved_bundle(args.package)
    else: result = store.activate_version(args.version)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
