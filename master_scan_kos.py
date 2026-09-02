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
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import master_scan_engine
import kapanis_master_otomasyon


ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "logs" / "master_scan_kos.log"


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


def _dry_log_scan_signal(scan_type: str, df_result: Any, category: str = "", **_: Any) -> bool:
    """Batch motorlarının kuru koşuda patron.db yazmasını engeller."""
    row_count = len(df_result) if df_result is not None else 0
    logging.getLogger("master_scan_kos").info(
        "[kuru][scan_signals] %s: %s satır yazılmadı (%s)",
        scan_type, row_count, category,
    )
    return True


def _install_dry_write_barrier() -> tuple[Any, Any]:
    scan_pipeline = importlib.import_module("scan_pipeline")
    original = scan_pipeline.log_scan_signal
    scan_pipeline.log_scan_signal = _dry_log_scan_signal
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
    try:
        services, streamlit = _load_services(logger)
        master_scan_engine.configure_services(services)
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
            dry_saved = _install_dry_write_barrier()
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
    parser = argparse.ArgumentParser(description="Ekransız Master Scan koşucusu")
    parser.add_argument("--kategori", default="BIST 500 ", help="ASSET_GROUPS kategori adı")
    parser.add_argument(
        "--kuru", action="store_true",
        help="Hesapları çalıştır; patron.db ve tamamlanma kaydına yazma",
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
