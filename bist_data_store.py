#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BIST günlük verisi için aday, doğrulama, sürüm ve geri dönüş çekirdeği.

Ana ilke: sağlayıcılar yalnız aday üretir. Bu modül dışındaki üretim kodu BIST
günlük ana dosyasına yazmaz. Aktif sürüm işareti bütün dosyalar doğrulandıktan ve
uyumluluk aynası hazırlandıktan sonra tek işlemle değiştirilir.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
LEGACY_DIR = Path(os.environ.get("SMR_CACHE_DIR", ROOT / "veriler"))
STORE = Path(os.environ.get("SMR_BIST_STORE", ROOT / "health" / "bist_store"))
OBJECTS = STORE / "objects"
MANIFESTS = STORE / "manifests"
STAGING = STORE / "staging"
QUARANTINE = STORE / "quarantine"
INBOX = STORE / "inbox"
OUTBOX = STORE / "outbox"
ACTIVE_FILE = STORE / "active.json"
LOCK_FILE = STORE / ".writer.lock"
EVENT_LOG = ROOT / "logs" / "bist_data_events.jsonl"

for _p in (OBJECTS, MANIFESTS, STAGING, QUARANTINE, INBOX, OUTBOX, EVENT_LOG.parent):
    _p.mkdir(parents=True, exist_ok=True)

PRICE_SOURCES = {"yahoo", "yahoo_settled", "borsapy_gapfill", "repair_yahoo"}
VOLUME_SOURCES = {"isyatirim", "isyatirim_cache", "yahoo_provisional",
                  "repair_isyatirim", "index_ciro", "borsapy"}
VOLUME_OFFICIAL_SOURCES = frozenset({
    "isyatirim", "isyatirim_cache", "repair_isyatirim", "index_ciro",
})
VOLUME_CONTROLLED_SOURCES = frozenset({"borsapy"})
CRITICAL = {"XU100.IS", "XU030.IS", "XBANK.IS", "XTUMY.IS", "XUSIN.IS"}
OHLC = ["Open", "High", "Low", "Close"]
# Bir bar kaç işlem günü boyunca normal turlarla düzeltilebilir kalır; sonrası kilitli.
# 10 gün = geç gelen İş Yatırım hacminin gerçek değeri yakalaması için geniş nefes payı.
# Dar pencere, hacmi 0'da kilitleyip SASA tipi kaybı dondurabiliyordu.
MUTABLE_WINDOW_DAYS = int(os.environ.get("SMR_MUTABLE_WINDOW_DAYS", "10"))


@contextmanager
def writer_lock(timeout: float = 120.0, stale_after: float = 1800.0):
    """Bütün BIST yazıcıları için tek süreçler-arası kilit."""
    deadline = time.time() + timeout
    fd = None
    while fd is None:
        try:
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"pid": os.getpid(), "at": time.time()}).encode())
        except FileExistsError:
            try:
                if time.time() - LOCK_FILE.stat().st_mtime > stale_after:
                    LOCK_FILE.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.time() >= deadline:
                raise TimeoutError("BIST tek-yazıcı kilidi alınamadı")
            time.sleep(0.15)
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


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _event(kind: str, payload: dict[str, Any]) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload}
    try:
        with EVENT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _symbol(symbol: str) -> str:
    s = str(symbol).upper().strip()
    if not s.endswith(".IS"):
        s += ".IS"
    return s


def _legacy_path(symbol: str) -> Path:
    return LEGACY_DIR / f"{_symbol(symbol)}_1d.parquet"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _version_id(prefix: str = "v") -> str:
    return f"{prefix}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"


def active_version_id() -> str:
    try:
        return str(json.loads(ACTIVE_FILE.read_text(encoding="utf-8"))["version_id"])
    except Exception:
        return "legacy"


def load_manifest(version_id: str | None = None) -> dict[str, Any] | None:
    vid = version_id or active_version_id()
    if not vid or vid == "legacy":
        return None
    try:
        data = json.loads((MANIFESTS / f"{vid}.json").read_text(encoding="utf-8"))
        return data if isinstance(data.get("symbols"), dict) else None
    except Exception:
        return None


def volume_source_quality(source: str | None) -> str:
    """Hacim kaynağını bütün tüketiciler için tek kalite sınıfına çevirir."""
    value = str(source or "").lower()
    if value in VOLUME_OFFICIAL_SOURCES:
        return "official"
    if value in VOLUME_CONTROLLED_SOURCES:
        return "controlled_fallback"
    if value == "yahoo_provisional":
        return "provisional"
    return "unknown"


def active_volume_meta(symbol: str, version_id: str | None = None) -> dict[str, str]:
    """Aktif manifestten sembolün hacim kaynağını salt-okur olarak döndürür."""
    manifest = load_manifest(version_id)
    if not manifest:
        return {"source": "", "quality": "unknown", "last": ""}
    entry = (manifest.get("symbols", {}) or {}).get(_symbol(symbol), {}) or {}
    source = str((entry.get("field_sources", {}) or {}).get("Volume") or "").lower()
    return {
        "source": source,
        "quality": volume_source_quality(source),
        "last": str(entry.get("last") or "")[:10],
    }


def resolve_active_path(symbol: str, version_id: str | None = None) -> Path:
    sym = _symbol(symbol)
    manifest = load_manifest(version_id)
    if manifest:
        entry = manifest.get("symbols", {}).get(sym)
        if entry:
            obj = STORE / entry.get("object", "")
            if obj.exists():
                return obj
        # Aktif manifestte olmayan/reddedilmiş sembol eski uyumluluk dosyasına
        # düşemez; aksi halde karantina sessizce delinmiş olur.
        return STORE / "missing" / f"{sym}_1d.parquet"
    return _legacy_path(sym)


def read_active(symbol: str, version_id: str | None = None) -> pd.DataFrame | None:
    p = resolve_active_path(symbol, version_id)
    try:
        df = pd.read_parquet(p)
        return _normalise_frame(df, allow_partial=False)
    except Exception:
        return None


def _normalise_frame(df: pd.DataFrame | None, *, allow_partial: bool) -> pd.DataFrame | None:
    if df is None or getattr(df, "empty", True):
        return None
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out = out.loc[:, ~out.columns.duplicated()].copy()
    out.columns = [str(c).capitalize() for c in out.columns]
    needed = set(OHLC) if not allow_partial else set()
    if not needed.issubset(out.columns):
        return None
    try:
        idx = pd.DatetimeIndex(pd.to_datetime(out.index, errors="coerce"))
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        out.index = idx.normalize()
    except Exception:
        return None
    out = out[~out.index.isna()]
    out = out.sort_index()
    return out


def _last_trading_days(n: int = 4, today: date | None = None) -> set[date]:
    today = today or datetime.now().date()
    try:
        from bist_calendar import is_trading_day
    except Exception:
        is_trading_day = lambda d: d.weekday() < 5
    out: set[date] = set()
    cur = today
    for _ in range(20):
        if is_trading_day(cur):
            out.add(cur)
            if len(out) >= n:
                break
        cur -= timedelta(days=1)
    return out


def _frame_meta(df: pd.DataFrame, sha: str, object_rel: str,
                field_sources: dict[str, str], quality: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": object_rel.replace("\\", "/"), "sha256": sha,
        "rows": int(len(df)), "first": str(df.index.min())[:10],
        "last": str(df.index.max())[:10], "field_sources": field_sources,
        "quality": quality,
    }


def ensure_bootstrap() -> dict[str, Any]:
    """Mevcut sağlam depoyu ilk sürüm olarak yalnız ilk yazıcı çalışmasında alır."""
    current = load_manifest()
    if current:
        return current
    symbols: dict[str, Any] = {}
    rejected: dict[str, Any] = {}
    hard_rejected: set[str] = set()
    bootstrap_run = _version_id("bootstrap-stage")
    run_dir = STAGING / bootstrap_run
    run_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(LEGACY_DIR.glob("*.IS_1d.parquet")):
        sym = src.name.replace("_1d.parquet", "")
        try:
            df = _normalise_frame(pd.read_parquet(src), allow_partial=False)
            if df is None or df.empty:
                rejected[sym] = ["boş veya OHLC eksik"]
                hard_rejected.add(sym)
                continue
            dated, dw = _candidate_dates_ok(df.copy(), sym)
            if len(dated) != len(df):
                removed = df.loc[df.index.difference(dated.index)]
                flat = ((removed["Open"] == removed["Close"])
                        & (removed["High"] == removed["Close"])
                        & (removed["Low"] == removed["Close"]))
                zero = pd.to_numeric(removed.get("Volume", 0), errors="coerce").fillna(0) <= 0
                if bool((flat & zero).all()):
                    # Resmî tatildeki düz, sıfır-hacimli Yahoo kopyaları veri değildir.
                    df = dated.copy()
                else:
                    rejected[sym] = dw
                    hard_rejected.add(sym)
                    _event("bootstrap_reject", {"symbol": sym, "warnings": rejected[sym]})
                    continue
            checked, pw = _valid_price_rows(df.copy())
            if len(checked) != len(df):
                bad_days = [str(pd.Timestamp(x).date()) for x in df.index.difference(checked.index)]
                rejected[sym] = {"warnings": dw + pw, "rejected_days": bad_days}
                _event("bootstrap_rows_quarantined", {
                    "symbol": sym, "rejected_days": bad_days})
                if checked.empty:
                    hard_rejected.add(sym)
                    continue
                # Eski sağlam satırlar korunur; yalnız matematiği bozuk günler
                # ilk sürüme alınmaz ve özel onarım kuyruğuna bırakılır.
                df = df.loc[checked.index].copy()
            df[OHLC] = checked[OHLC]
            digest, rel, dst = _write_object(sym, df, run_dir)
            symbols[sym] = _frame_meta(
                df, digest, rel,
                {"OHLC": "legacy_verified", "Volume": "legacy_verified"},
                {"status": "bootstrap_degraded" if sym in rejected else "bootstrap",
                 "warnings": rejected.get(sym, [])})
        except Exception as exc:
            rejected[sym] = [str(exc)[:200]]
            hard_rejected.add(sym)
            _event("bootstrap_reject", {"symbol": sym, "error": str(exc)[:200]})
    vid = _version_id("bootstrap")
    manifest = {
        "schema": 2, "version_id": vid, "parent": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reason": "existing_verified_store_bootstrap", "symbols": symbols,
        "stats": {"total": len(symbols) + len(hard_rejected),
                  "accepted": len(symbols), "rejected_symbols": len(rejected),
                  "hard_rejected": len(hard_rejected)},
        "rejected": rejected,
    }
    _atomic_json(MANIFESTS / f"{vid}.json", manifest)
    _atomic_json(ACTIVE_FILE, {"version_id": vid, "activated_at": manifest["created_at"]})
    if rejected:
        _atomic_json(QUARANTINE / f"{bootstrap_run}.json", {
            "reason": "bootstrap_validation", "rejected": rejected})
    shutil.rmtree(run_dir, ignore_errors=True)
    _event("bootstrap", {"version_id": vid, "symbols": len(symbols)})
    return manifest


def _candidate_dates_ok(df: pd.DataFrame, symbol: str) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    if df.index.duplicated().any():
        warnings.append("adayda yinelenen tarih vardı; son kayıt tutuldu")
        df = df[~df.index.duplicated(keep="last")]
    today = datetime.now().date()
    keep = []
    try:
        from bist_calendar import is_trading_day
    except Exception:
        is_trading_day = lambda d: d.weekday() < 5
    for ts in df.index:
        d = pd.Timestamp(ts).date()
        if d > today:
            warnings.append(f"gelecek tarih reddedildi: {d}")
            keep.append(False)
        elif not is_trading_day(d):
            warnings.append(f"işlem dışı tarih reddedildi: {d}")
            keep.append(False)
        else:
            keep.append(True)
    return df.loc[np.asarray(keep, dtype=bool)], warnings


def _valid_price_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    if not set(OHLC).issubset(df.columns):
        return df.iloc[0:0], ["OHLC alanları eksik"]
    for c in OHLC:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    good = df[OHLC].notna().all(axis=1)
    good &= (df[OHLC] > 0).all(axis=1)
    good &= df["High"] >= df[["Open", "Close", "Low"]].max(axis=1)
    good &= df["Low"] <= df[["Open", "Close", "High"]].min(axis=1)
    if int((~good).sum()):
        warnings.append(f"{int((~good).sum())} matematiksel bozuk fiyat satırı reddedildi")
    return df.loc[good, OHLC], warnings


def _merge_candidate(old: pd.DataFrame | None, spec: dict[str, Any],
                     *, repair: bool = False) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    report: dict[str, Any] = {
        "accepted": False, "added_days": [], "changed_days": [],
        "rejected_days": [], "warnings": [], "field_sources": {},
    }
    base = old.copy() if old is not None else pd.DataFrame(columns=OHLC + ["Volume"])
    if "Volume" not in base.columns:
        base["Volume"] = np.nan
    mutable = _last_trading_days(MUTABLE_WINDOW_DAYS)

    price_requested = spec.get("price_df") is not None
    price = _normalise_frame(spec.get("price_df"), allow_partial=True)
    price_source = str(spec.get("price_source", "")).lower()
    if price is not None:
        if price_source not in PRICE_SOURCES:
            report["warnings"].append(f"yetkisiz fiyat kaynağı reddedildi: {price_source or 'yok'}")
            price = None
        else:
            price, w = _candidate_dates_ok(price, str(spec.get("symbol", "")))
            report["warnings"].extend(w)
            price, w = _valid_price_rows(price)
            report["warnings"].extend(w)
    if price_requested and (price is None or price.empty):
        report["warnings"].append("fiyat adayı bütünüyle geçersiz; sembol reddedildi")
        return None, report
    if price is not None and not price.empty:
        for ts, row in price.iterrows():
            d = pd.Timestamp(ts).date()
            exists = ts in base.index
            if exists and d not in mutable and not repair:
                changed = any(abs(float(base.at[ts, c]) - float(row[c])) >
                              max(1e-8, abs(float(base.at[ts, c])) * 1e-6) for c in OHLC)
                if changed:
                    report["rejected_days"].append({"date": str(d), "field": "OHLC", "reason": "eski_bar_kilitli"})
                continue
            if not exists and d not in mutable and not repair:
                report["rejected_days"].append({"date": str(d), "field": "OHLC", "reason": "eski_eksik_gun_ozel_onarim_gerekli"})
                continue
            if exists:
                prior = base.loc[ts, OHLC].astype(float)
                if bool((np.abs(prior.values - row[OHLC].astype(float).values) >
                         np.maximum(1e-8, np.abs(prior.values) * 1e-6)).any()):
                    report["changed_days"].append(str(d))
            else:
                base.loc[ts, OHLC] = row[OHLC].values
                base.loc[ts, "Volume"] = np.nan
                report["added_days"].append(str(d))
            base.loc[ts, OHLC] = row[OHLC].values
        report["field_sources"]["OHLC"] = price_source

    volume_requested = spec.get("volume_df") is not None
    volume = _normalise_frame(spec.get("volume_df"), allow_partial=True)
    volume_source = str(spec.get("volume_source", "")).lower()
    if volume is not None:
        if "Volume" not in volume.columns or volume_source not in VOLUME_SOURCES:
            report["warnings"].append(f"yetkisiz/eksik hacim adayı reddedildi: {volume_source or 'yok'}")
            volume = None
        else:
            volume, w = _candidate_dates_ok(volume, str(spec.get("symbol", "")))
            report["warnings"].extend(w)
            volume["Volume"] = pd.to_numeric(volume["Volume"], errors="coerce")
            volume = volume[volume["Volume"].notna() & (volume["Volume"] >= 0)]
    if volume_requested and (volume is None or volume.empty):
        report["warnings"].append("hacim adayı bütünüyle geçersiz; sembol reddedildi")
        return None, report
    if volume is not None and not volume.empty:
        for ts, row in volume.iterrows():
            d = pd.Timestamp(ts).date()
            if ts not in base.index:
                report["rejected_days"].append({"date": str(d), "field": "Volume", "reason": "fiyat_bari_yok"})
                continue
            if d not in mutable and not repair:
                old_v = base.at[ts, "Volume"]
                if pd.isna(old_v) or abs(float(old_v) - float(row["Volume"])) > max(1.0, abs(float(old_v)) * 1e-6):
                    report["rejected_days"].append({"date": str(d), "field": "Volume", "reason": "eski_bar_kilitli"})
                continue
            if volume_source == "borsapy" and d != max(mutable):
                report["rejected_days"].append({
                    "date": str(d), "field": "Volume",
                    "reason": "borsapy_hacmi_yalniz_son_islem_gunu",
                })
                continue
            if volume_source == "yahoo_provisional" and d != max(mutable):
                report["rejected_days"].append({"date": str(d), "field": "Volume", "reason": "yahoo_hacim_yalniz_bugun_gecici"})
                continue
            # SASA KALKANI: gerçek (pozitif) hacmin üstüne 0 yazılamaz. Kaynak bir işlem
            # günü için 0 dönerse (Yahoo intraday / İş Yatırım kısmi cevap) eldeki gerçek
            # hacim korunur; bir günün hacmi sonradan sıfıra dönmez.
            new_v = float(row["Volume"])
            old_v = base.at[ts, "Volume"] if ts in base.index else np.nan
            if new_v <= 0 and pd.notna(old_v) and float(old_v) > 0:
                report["rejected_days"].append({"date": str(d), "field": "Volume", "reason": "sifir_hacim_gercegi_ezemez"})
                continue
            base.at[ts, "Volume"] = new_v
        report["field_sources"]["Volume"] = volume_source
        report["volume_quality"] = volume_source_quality(volume_source)

    if base.empty or not set(OHLC).issubset(base.columns):
        report["warnings"].append("birleştirilecek sağlam fiyat serisi yok")
        return None, report
    base = base.sort_index()
    base = base[~base.index.duplicated(keep="last")]
    price_good, w = _valid_price_rows(base.copy())
    report["warnings"].extend(w)
    if len(price_good) != len(base):
        return None, report
    base[OHLC] = price_good[OHLC]

    # Yeni/oynanmış barlarda aşırı sıçrama kanıt yoksa turu zehirlemesin.
    evidence = spec.get("evidence") or {}
    if not evidence.get("corporate_action") and len(base) >= 2:
        jumps = base["Close"].pct_change(fill_method=None).abs()
        touched = set(report["added_days"]) | set(report["changed_days"])
        bad = [str(pd.Timestamp(ts).date()) for ts in jumps[jumps > 0.35].index
               if str(pd.Timestamp(ts).date()) in touched]
        if bad:
            report["warnings"].append(f"kanıtsız aşırı fiyat sıçraması: {', '.join(bad[:3])}")
            return None, report
    ref_close = spec.get("reference_close")
    if ref_close is not None and len(base):
        try:
            last = float(base["Close"].iloc[-1]); ref = float(ref_close)
            if ref > 0 and max(last, ref) / min(last, ref) > 1.05:
                report["warnings"].append(f"ikinci kaynak ciddi ayrışma: {last:.2f} vs {ref:.2f}")
                return None, report
        except Exception:
            pass
    if "Volume" in base.columns and len(base) >= 2:
        v = pd.to_numeric(base["Volume"], errors="coerce")
        changed_price = base["Close"].pct_change(fill_method=None).abs() > 1e-12
        bad_zero = (v.fillna(0) <= 0) & changed_price
        if bad_zero.tail(4).any():
            report["warnings"].append("son günlerde fiyat değiştiği halde hacim yok; fiyat korunup hacim geçici sayıldı")
    report["accepted"] = True
    report["status"] = (
        "provisional" if report["field_sources"].get("Volume") == "yahoo_provisional"
        else "controlled_fallback" if report["field_sources"].get("Volume") == "borsapy"
        else "verified"
    )
    return base, report


def _write_object(symbol: str, df: pd.DataFrame, run_dir: Path) -> tuple[str, str, Path]:
    sym = _symbol(symbol)
    staged = run_dir / f"{sym}_1d.parquet"
    df.to_parquet(staged, compression="snappy")
    check = _normalise_frame(pd.read_parquet(staged), allow_partial=False)
    if check is None or len(check) != len(df):
        raise ValueError("staging tekrar-okuma doğrulaması başarısız")
    digest = _sha256(staged)
    dst = OBJECTS / sym / f"{digest}.parquet"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        tmp = dst.with_suffix(f".parquet.{os.getpid()}.tmp")
        shutil.copy2(staged, tmp)
        os.replace(tmp, dst)
    return digest, str(dst.relative_to(STORE)), dst


def _materialize_legacy(symbol: str, object_path: Path) -> None:
    target = _legacy_path(symbol)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".parquet.{os.getpid()}.tmp")
    shutil.copy2(object_path, tmp)
    os.replace(tmp, target)


def promote_batch(candidates: dict[str, dict[str, Any]], *, reason: str,
                  source_run: dict[str, Any] | None = None, repair: bool = False,
                  max_reject_ratio: float = 0.20) -> dict[str, Any]:
    """Adayları doğrular, önceki sağlam sembolleri taşır ve yeni sürümü etkinleştirir."""
    with writer_lock():
        parent = ensure_bootstrap()
        symbols = dict(parent.get("symbols", {}))
        run_id = _version_id("candidate")
        run_dir = STAGING / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        accepted: dict[str, Any] = {}
        rejected: dict[str, Any] = {}
        day_rejections: dict[str, Any] = {}
        changed_objects: dict[str, Path] = {}
        for raw_sym, raw_spec in candidates.items():
            sym = _symbol(raw_sym)
            spec = dict(raw_spec or {})
            spec["symbol"] = sym
            old = read_active(sym, parent["version_id"])
            try:
                merged, report = _merge_candidate(old, spec, repair=repair)
                if merged is None or not report.get("accepted"):
                    rejected[sym] = report
                    continue
                if report.get("rejected_days"):
                    day_rejections[sym] = report["rejected_days"]
                digest, rel, obj = _write_object(sym, merged, run_dir)
                old_sha = symbols.get(sym, {}).get("sha256")
                if digest == old_sha:
                    report["unchanged"] = True
                    accepted[sym] = report
                    continue
                sources = dict(symbols.get(sym, {}).get("field_sources", {}))
                sources.update(report.get("field_sources", {}))
                symbols[sym] = _frame_meta(
                    merged, digest, rel, sources,
                    {"status": report.get("status", "verified"),
                     "volume_quality": report.get("volume_quality", volume_source_quality(sources.get("Volume"))),
                     "warnings": report.get("warnings", []),
                     "run_id": run_id})
                changed_objects[sym] = obj
                accepted[sym] = report
            except Exception as exc:
                rejected[sym] = {"accepted": False, "warnings": [str(exc)[:300]]}

        total = max(1, len(candidates))
        reject_ratio = len(rejected) / total
        critical_rejects = sorted(set(rejected).intersection(CRITICAL))
        systemic = reject_ratio > max_reject_ratio or bool(critical_rejects)
        if systemic:
            q = QUARANTINE / f"{run_id}.json"
            _atomic_json(q, {
                "run_id": run_id, "reason": reason, "parent": parent["version_id"],
                "systemic": systemic, "critical_rejects": critical_rejects,
                "accepted": accepted, "rejected": rejected, "source_run": source_run or {},
            })
            _event("promotion_blocked", {"run_id": run_id, "reason": reason,
                                         "rejected": len(rejected), "changed": len(changed_objects),
                                         "critical_rejects": critical_rejects})
            shutil.rmtree(run_dir, ignore_errors=True)
            return {"ok": False, "run_id": run_id, "blocked": systemic,
                    "changed": len(changed_objects), "accepted": accepted,
                    "rejected": rejected, "active_version": parent["version_id"]}

        # Kaynak aynı sağlam içeriği tekrar verdiyse bu bir hata değildir. Yeni sürüm
        # üretmeden başarılı no-op dönülür; fetcher aynı turu gereksiz yere tekrarlamaz.
        if not changed_objects:
            if rejected or day_rejections:
                _atomic_json(QUARANTINE / f"{run_id}.json", {
                    "run_id": run_id, "reason": reason, "parent": parent["version_id"],
                    "systemic": False, "accepted": accepted, "rejected": rejected,
                    "rejected_days": day_rejections,
                    "source_run": source_run or {},
                })
            shutil.rmtree(run_dir, ignore_errors=True)
            _event("promotion_no_change", {"run_id": run_id, "reason": reason,
                                            "rejected": len(rejected)})
            return {"ok": True, "no_change": True,
                    "version_id": parent["version_id"],
                    "active_version": parent["version_id"], "changed": 0,
                    "accepted": accepted, "rejected": rejected}

        vid = _version_id("v")
        created = datetime.now(timezone.utc).isoformat()
        manifest = {
            "schema": 2, "version_id": vid, "parent": parent["version_id"],
            "created_at": created, "reason": reason, "source_run": source_run or {},
            "symbols": symbols,
            "stats": {"candidate_total": len(candidates), "accepted": len(accepted),
                      "changed": len(changed_objects), "rejected": len(rejected),
                      "carried_forward": len(symbols) - len(changed_objects)},
            "rejected": rejected,
            "rejected_days": day_rejections,
        }
        _atomic_json(MANIFESTS / f"{vid}.json", manifest)
        # Eski doğrudan okuyucular için uyumluluk aynası önce hazırlanır.
        for sym, obj in changed_objects.items():
            _materialize_legacy(sym, obj)
        # Okuyucuların gördüğü aktif sürüm EN SON tek işlemle değişir.
        _atomic_json(ACTIVE_FILE, {"version_id": vid, "parent": parent["version_id"],
                                  "activated_at": created})
        q = QUARANTINE / f"{run_id}.json"
        if rejected or day_rejections:
            _atomic_json(q, {"run_id": run_id, "reason": reason,
                             "rejected": rejected,
                             "rejected_days": day_rejections})
        shutil.rmtree(run_dir, ignore_errors=True)
        _event("promoted", {"version_id": vid, "parent": parent["version_id"],
                            "reason": reason, "changed": len(changed_objects),
                            "rejected": len(rejected)})
        return {"ok": True, "version_id": vid, "parent": parent["version_id"],
                "changed": len(changed_objects), "accepted": accepted,
                "rejected": rejected}


def activate_version(version_id: str) -> dict[str, Any]:
    """Onaylı eski sürüme geri döner; sağlayıcıya gitmez."""
    with writer_lock():
        target = load_manifest(version_id)
        if not target:
            raise FileNotFoundError(f"sürüm bulunamadı: {version_id}")
        for sym, entry in target.get("symbols", {}).items():
            obj = STORE / entry.get("object", "")
            if obj.exists():
                _materialize_legacy(sym, obj)
        _atomic_json(ACTIVE_FILE, {"version_id": version_id,
                                  "activated_at": datetime.now(timezone.utc).isoformat(),
                                  "rollback": True})
        _event("rollback", {"version_id": version_id})
        return {"ok": True, "version_id": version_id}


def store_status() -> dict[str, Any]:
    manifest = load_manifest()
    return {
        "active_version": active_version_id(),
        "created_at": manifest.get("created_at") if manifest else None,
        "symbols": len(manifest.get("symbols", {})) if manifest else 0,
        "stats": manifest.get("stats", {}) if manifest else {},
        "manifests": len(list(MANIFESTS.glob("*.json"))),
        "quarantine": len(list(QUARANTINE.glob("*.json"))),
    }
