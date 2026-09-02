# -*- coding: utf-8 -*-
"""Master Scan için ekransız, tek süreçli koşucu.

Ekran tarafındaki Streamlit oturumunu ``golden_record`` ile aynı sahte modül
yolundan yükler; hesap motorlarını yeniden yazmaz. Faz 1 ve Faz 2 burada tek
akışta çalışır. ``--kuru`` hesapları koşturur, fakat patron.db ve tamamlanma
kaydına yazmaz.
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import master_scan_engine
import kapanis_master_otomasyon


ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "logs" / "master_scan_kos.log"
ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")


def _canonical_symbol(value: Any) -> str:
    return str(value or "").strip().upper().replace(".IS", "")


def _db_float(value: Any, *, comma_decimal: bool = False) -> float | None:
    if value is None:
        return None
    try:
        raw = str(value).replace(",", ".") if comma_decimal else value
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class _DryRunSidecar:
    """Kuru koşunun DB'ye yazacağı scan_runs/scan_signals fotoğrafı."""

    def __init__(self, category: str, logger: logging.Logger) -> None:
        self.category = category
        self.run_at = datetime.now(ISTANBUL_TZ)
        self.logger = logger
        self.scan_results: dict[str, dict[str, Any]] = {}

    def _upsert(
        self, scan_type: str, row_count: int, symbols: list[dict[str, Any]],
    ) -> None:
        scan_type = str(scan_type or "").strip()
        if not scan_type:
            raise ValueError("Kuru yan-kayıt boş tarama tipi kabul etmiyor")
        current = self.scan_results.setdefault(
            scan_type, {"row_count": 0, "semboller": []}
        )
        # scan_runs aynı gün aynı tipi günceller; scan_signals ise mevcut
        # sembolü koruyup yeni sembolü ekler. Sidecar bunu birebir yansıtır.
        current["row_count"] = max(0, int(row_count or 0))
        seen = {_canonical_symbol(item["symbol"]) for item in current["semboller"]}
        for item in symbols:
            key = _canonical_symbol(item["symbol"])
            if not key:
                continue
            if key not in seen:
                current["semboller"].append(item)
                seen.add(key)

    def capture_scan_signal(self, scan_type: str, df_result: Any, category: str = "", **_: Any) -> None:
        row_count = len(df_result) if df_result is not None else 0
        symbols: list[dict[str, Any]] = []
        if df_result is not None and not getattr(df_result, "empty", False):
            if not hasattr(df_result, "iterrows"):
                raise TypeError(f"{scan_type} kuru fotoğraf için satır tablosu değil")
            for _, row in df_result.iterrows():
                symbol = (
                    row.get("Sembol", "") or row.get("Hisse", "")
                    or row.get("Ticker", "") or row.get("symbol", "")
                )
                if not symbol:
                    continue
                entry_raw = row.get("Fiyat", row.get("fiyat", None))
                score_raw = row.get(
                    "ToplamSkor",
                    row.get("Raw_Score", row.get("Skor", row.get("score", row.get("Teknik_Skor", None)))),
                )
                stop_raw = row.get("Stop", row.get("stop_level", row.get("StopSeviye", None)))
                symbols.append({
                    "symbol": str(symbol).replace(".IS", ""),
                    "score": _db_float(score_raw),
                    "entry_price": _db_float(entry_raw, comma_decimal=True),
                    "stop_level": _db_float(stop_raw, comma_decimal=True),
                })
        self._upsert(str(scan_type), row_count, symbols)
        self.logger.info(
            "[kuru][scan_signals] %s: %s satır yan-kayda alındı (%s)",
            scan_type, row_count, category or self.category,
        )

    def capture_early_radar(
        self, df_batch: Any, scenario_ids: list[str], category: str = "", **_: Any,
    ) -> None:
        counts: dict[str, int] = {}
        if df_batch is not None and not getattr(df_batch, "empty", False):
            if "ScenarioId" not in df_batch.columns:
                raise ValueError("Erken Radar kuru fotoğrafında ScenarioId kolonu yok")
            counts = df_batch["ScenarioId"].astype(str).value_counts().to_dict()
        for scenario_id in scenario_ids:
            scan_type = f"er_{scenario_id}"
            rows: list[dict[str, Any]] = []
            if df_batch is not None and not getattr(df_batch, "empty", False):
                for _, row in df_batch.iterrows():
                    if str(row.get("ScenarioId", "")) != str(scenario_id):
                        continue
                    symbol = row.get("Sembol", "")
                    if not symbol:
                        continue
                    rows.append({
                        "symbol": str(symbol).replace(".IS", ""),
                        "score": _db_float(row.get("Skor", 0)),
                        "entry_price": _db_float(row.get("Fiyat", 0), comma_decimal=True),
                        "stop_level": None,
                    })
            self._upsert(scan_type, int(counts.get(str(scenario_id), 0)), rows)
        self.logger.info(
            "[kuru][scan_signals] Erken Radar: %s senaryo tipi yan-kayda alındı (%s)",
            len(scenario_ids), category or self.category,
        )

    def write(self) -> Path:
        day = self.run_at.strftime("%Y-%m-%d")
        path = ROOT / "logs" / f"master_scan_kuru_{day}.json"
        payload = {
            "schema_version": 1,
            "run_at": self.run_at.isoformat(timespec="seconds"),
            "category": self.category,
            "engine_version": master_scan_engine.MASTER_SCAN_ENGINE_VERSION,
            "scan_results": self.scan_results,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        temporary.replace(path)
        self.logger.info(
            "[kuru] yan-kayıt yazıldı: %s (%s tarama tipi)", path, len(self.scan_results)
        )
        return path


def _build_logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("master_scan_kos")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S%z",
        )
        file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        file_handler.setFormatter(formatter)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
    return logger


def _resolve_category(asset_groups: dict[str, list[str]], requested: str) -> str:
    if requested in asset_groups:
        return requested
    folded = requested.strip().casefold()
    matches = [key for key in asset_groups if key.strip().casefold() == folded]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"Bilinmeyen Master Scan kategorisi: {requested!r}")
    raise ValueError(f"Kategori adı birden fazla evrenle eşleşti: {requested!r}")


def _dry_log_scan_signal(
    scan_type: str, df_result: Any, category: str = "",
    capture: Callable[..., Any] | None = None, **kwargs: Any,
) -> bool:
    """Batch motorlarının kuru koşuda patron.db yazmasını engeller."""
    if capture is not None:
        capture(scan_type, df_result, category=category, **kwargs)
    row_count = len(df_result) if df_result is not None else 0
    logging.getLogger("master_scan_kos").info(
        "[kuru][scan_signals] %s: %s satır DB'ye yazılmadı (%s)",
        scan_type, row_count, category,
    )
    return True


def _filter_scan_log_input(scan_pipeline: Any, df_result: Any) -> Any:
    """Ekran yazıcısının aktif atlama kümesini kuru fotoğrafa da uygular."""
    skip = getattr(scan_pipeline, "_SCAN_LOG_SKIP", set())
    if not skip or df_result is None or not hasattr(df_result, "columns"):
        return df_result
    for column in ("Sembol", "Hisse", "Ticker", "Symbol"):
        if column in df_result.columns:
            normalized = df_result[column].astype(str).str.upper().str.replace(
                ".IS", "", regex=False
            )
            return df_result[~normalized.isin(skip)]
    return df_result


def _install_dry_write_barrier(sidecar: _DryRunSidecar) -> tuple[Any, Any]:
    scan_pipeline = importlib.import_module("scan_pipeline")
    original = scan_pipeline.log_scan_signal

    def dry_log_scan_signal(scan_type: str, df_result: Any, category: str = "", **kwargs: Any) -> bool:
        filtered = _filter_scan_log_input(scan_pipeline, df_result)
        return _dry_log_scan_signal(
            scan_type, filtered, category=category,
            capture=sidecar.capture_scan_signal, **kwargs,
        )

    scan_pipeline.log_scan_signal = dry_log_scan_signal
    return scan_pipeline, original


def _restore_dry_write_barrier(saved: tuple[Any, Any] | None) -> None:
    if saved is None:
        return
    module, original = saved
    module.log_scan_signal = original


def _load_services(logger: logging.Logger) -> tuple[dict[str, Any], Any]:
    from golden_record import load_app_defs

    logger.info("golden_record ekransız tanım yükleme yolu hazırlanıyor")
    services = load_app_defs(verbose=True)
    import streamlit

    return services, streamlit


def _new_state(category: str, scan_list: list[str], dry_run: bool) -> dict[str, Any]:
    return {
        "category": category,
        "scan_list": list(scan_list),
        "_ms_engine_category": category,
        "_ms_engine_scan_list": list(scan_list),
        "_ms_engine_is_bist": "BIST" in category.upper(),
        "ticker": scan_list[0] if scan_list else "",
        "scan_data": None,
        "generate_prompt": False,
        "radar2_data": None,
        "liderlik_yolculugu_data": None,
        "accum_data": None,
        "minervini_data": None,
        "toplu_terazi_data": None,
        "wilder_divergence_data": None,
        "prelaunch_bos_data": None,
        "erken_radar_data": None,
        "golden_pattern_data": None,
        "harmonic_confluence_data": None,
        "rs_leaders_data": None,
        "cizgi_yapi_master_data": None,
        "magic_ribbon_session_data": None,
        "_master_scan_running": True,
        "_ms_faz2_bekliyor": [],
        "_ms_faz2_baglam": {},
        "_ms_faz2_resume_once": False,
        "_ms_faz2_interruptions": 0,
        "_scan_cache_restored": True,
        "_ms_dry_run": dry_run,
    }


def _progress_notifier(logger: logging.Logger):
    def bildir(level: str, text: str) -> None:
        if level not in {"progress", "toast", "warning", "error", "clear"}:
            raise ValueError(f"Bilinmeyen Master Scan bildirim seviyesi: {level}")
        clean = str(text).replace("\n", " | ")
        logger.info("[%s] %s", level, clean) if level != "error" else logger.error(
            "[%s] %s", level, clean
        )

    return bildir


def run(category: str, dry_run: bool, logger: logging.Logger) -> int:
    claimed = False
    dry_saved: tuple[Any, Any] | None = None
    dry_sidecar: _DryRunSidecar | None = None
    try:
        services, streamlit = _load_services(logger)
        asset_groups = services.get("ASSET_GROUPS")
        if not isinstance(asset_groups, dict):
            raise RuntimeError("ASSET_GROUPS ekransız koşucuya bağlanamadı")
        resolved_category = _resolve_category(asset_groups, category)
        scan_list = list(asset_groups[resolved_category])
        if not scan_list:
            raise ValueError(f"Master Scan evreni boş: {resolved_category}")

        if not dry_run:
            claimed = kapanis_master_otomasyon.claim_scan_start()
            if not claimed:
                logger.error("Başlatılmadı: başka bir Master Scan çalışma kilidini tutuyor")
                return 1

        if dry_run:
            dry_sidecar = _DryRunSidecar(resolved_category, logger)
            dry_saved = _install_dry_write_barrier(dry_sidecar)
            dry_pipeline = dry_saved[0]
            dry_services = dict(services)

            def capture_signal(scan_type: str, df_result: Any,
                               category: str = "", **kwargs: Any) -> None:
                dry_sidecar.capture_scan_signal(
                    scan_type,
                    _filter_scan_log_input(dry_pipeline, df_result),
                    category=category,
                    **kwargs,
                )

            def capture_early_radar(df_batch: Any, category: str = "",
                                    **kwargs: Any) -> None:
                scenario_map = getattr(dry_pipeline, "ERKEN_RADAR_SCENARIOS", None)
                if not isinstance(scenario_map, dict):
                    raise RuntimeError("Erken Radar senaryo sözleşmesi kuru koşuda bulunamadı")
                dry_sidecar.capture_early_radar(
                    df_batch, list(scenario_map), category=category, **kwargs,
                )

            dry_services["_ms_dry_capture_signal"] = capture_signal
            dry_services["_ms_dry_capture_early_radar"] = capture_early_radar
            services = dry_services
        master_scan_engine.configure_services(services)
        state = _new_state(resolved_category, scan_list, dry_run)
        streamlit.session_state = state
        bildir = _progress_notifier(logger)
        phase1_ok = master_scan_engine.run_phase1(state, bildir)
        if not phase1_ok:
            logger.error("Master Scan Faz 1 kısmi kaldı")
            return 1

        pending = list(state.get("_ms_faz2_bekliyor") or [])
        if not pending:
            logger.error("Master Scan Faz 1 bitti fakat Faz 2 kuyruğu boş")
            return 1
        logger.info(
            "Faz 1 tamamlandı; Faz 2 tek süreçte sürüyor (%s adım)", len(pending)
        )
        phase2_ok = master_scan_engine.execute_pending_phase2(state, bildir)
        if not phase2_ok:
            logger.error("Master Scan Faz 2 kısmi kaldı")
            return 1
        if dry_run:
            if dry_sidecar is None:
                raise RuntimeError("Kuru koşu yan-kayıt nesnesi oluşturulamadı")
            dry_sidecar.write()
        logger.info(
            "Master Scan başarıyla tamamlandı: kategori=%s, kuru=%s",
            resolved_category, dry_run,
        )
        return 0
    except KeyboardInterrupt:
        logger.exception("Master Scan kullanıcı tarafından kesildi")
        return 2
    except Exception:
        logger.exception("Master Scan çöktü")
        return 2
    finally:
        _restore_dry_write_barrier(dry_saved)
        if claimed:
            kapanis_master_otomasyon.release_scan_start()
            logger.info("Master Scan çalışma kilidi bırakıldı")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ekransız Master Scan koşucusu",
        epilog=(
            "Kullanım sırası:\n"
            "  1) Akşam ekran turu biter; patron.db yazılır.\n"
            "  2) python master_scan_kos.py --kategori \"BIST 500 \" --kuru\n"
            "     (kuru koşu ekran turundan SONRA çalışır ve yan-kayıt bırakır.)\n"
            "  3) python master_scan_kiyas.py --kuru-dosya "
            "logs/master_scan_kuru_YYYY-MM-DD.json --tarih YYYY-MM-DD"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--kategori", default="BIST 500 ", help="ASSET_GROUPS kategori adı")
    parser.add_argument(
        "--kuru", action="store_true",
        help="Hesapları çalıştır; patron.db/tamamlanma kaydına yazma, yan-kayıt üret",
    )
    args = parser.parse_args(argv)
    logger = _build_logger()
    logger.info(
        "Master Scan başlatılıyor: kategori=%r, kuru=%s, pid=%s, saat=%s",
        args.kategori, args.kuru, __import__("os").getpid(),
        datetime.now().isoformat(timespec="seconds"),
    )
    return run(args.kategori, args.kuru, logger)


if __name__ == "__main__":
    raise SystemExit(main())
