#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tarama Motoru V4 — işlem yapılabilir evren kapılı sabah gölge taraması.

V4, V1/V2/V3 dosyalarına dokunmaz. V3'ün kanıtlı günlük sıralamasını yalnızca
resmî pazar ve likidite kapısından geçmiş hisseler arasından seçer. Bu aşama
17:35 kapanış-öncesi model değildir; o modelin temiz başlangıç karnesidir.
"""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from kapanis_oncesi_evren import Policy, _as_of, _load_status, _row_for_ticker, audit
from yuksek_getiri_engine_v3 import load_model, score_latest


ISTANBUL = ZoneInfo("Europe/Istanbul")
STAGE = "SABAH_GOLGE_EVRESI"
MIN_ACTIONABLE_CANDIDATES = 3


def score_latest_v4(
    data_dir: Path,
    hourly_dir: Path,
    status_path: Path,
    model_path: Path,
    *,
    top_n: int = 5,
    as_of: datetime | None = None,
) -> tuple[pd.DataFrame, dict, dict]:
    """V3 günlük puanlamasını, resmî V4 işlem kapısından geçirir."""
    now = as_of or datetime.now(ISTANBUL)
    if not status_path.exists():
        raise RuntimeError("Resmî pazar dosyası yok; V4 aday listesi üretilemez.")
    # Sabah gölge listesinde henüz saatlik giriş verisi kullanılmaz. Bu yüzden
    # yüzlerce saatlik dosyayı boşuna açma; 17:35 ve sonrası gerçek kapı devreye girer.
    audit_hourly_dir = hourly_dir if now.timetz().replace(tzinfo=None) >= time(17, 35) else hourly_dir / "__sabahda_okunmaz__"
    universe = audit(data_dir, audit_hourly_dir, now, Policy(), status_path)
    allowed = {
        str(row["ticker"]).upper(): row
        for row in universe["rows"]
        if row.get("bucket") in {"ANA", "KONTROLLU"}
    }
    if not allowed:
        raise RuntimeError("Resmî pazar ve likidite kapısından geçen hisse yok.")

    model = load_model(model_path)
    # Önce geniş günlük sıralama, sonra V4 kapısı: ilk beşin elenmesi halinde
    # daha alt sıradaki uygun hisseler adil biçimde yukarı gelir.
    ranked, metadata = score_latest(data_dir, model, top_n=max(100, top_n * 10))
    selected = ranked[ranked["ticker"].astype(str).str.upper().isin(allowed)].head(top_n).copy()
    selected["pazar"] = selected["ticker"].map(lambda key: allowed[str(key).upper()].get("market"))
    selected["medyan_ciro_20_tl"] = selected["ticker"].map(
        lambda key: float(allowed[str(key).upper()].get("median_turnover_20_tl", 0.0))
    )
    selected = selected.reset_index(drop=True)

    count = len(selected)
    can_allocate = count >= MIN_ACTIONABLE_CANDIDATES
    allocation = 1_000_000.0 / count if can_allocate else 0.0
    selected["ayrilan_tutar_tl"] = allocation
    v4_meta = {
        **metadata,
        "stage": STAGE,
        "eligible_v4_universe": len(allowed),
        "selected_count": count,
        "allocation_enabled": can_allocate,
        "allocation_per_ticker_tl": allocation,
        "no_trade_reason": None if can_allocate else "Üçten az uygun aday: sermaye korunur.",
        "base_model_version": str(model.get("model_version", "")),
        "official_status_source": universe.get("official_status_source"),
    }
    return selected, v4_meta, model


def filter_v3_state_v4(
    state: dict,
    data_dir: Path,
    hourly_dir: Path,
    status_path: Path,
    *,
    as_of: datetime | None = None,
) -> tuple[pd.DataFrame, dict]:
    """T günü 17:50'de V3 adaylarını V4 kapısından geçirir."""
    if not state or not state.get("as_of") or not state.get("list"):
        raise RuntimeError("V3'ün aynı sabah ürettiği aday kaydı yok; V4 işlem yapmaz.")
    now = as_of or datetime.now(ISTANBUL)
    if not status_path.exists():
        raise RuntimeError("Resmî pazar dosyası yok; V4 aday listesi üretilemez.")
    markets, restrictions, source = _load_status(status_path)
    audit_hourly_dir = hourly_dir if now.timetz().replace(tzinfo=None) >= time(17, 35) else hourly_dir / "__sabahda_okunmaz__"
    rows = []
    for item in state["list"]:
        ticker = str(item.get("ticker", "")).strip().upper()
        daily_path = data_dir / f"{ticker}.IS_1d.parquet"
        if not ticker or not daily_path.exists():
            continue
        audit_row = _row_for_ticker(ticker, daily_path, audit_hourly_dir, now, Policy(), markets, restrictions)
        if audit_row.get("bucket") in {"ANA", "KONTROLLU"}:
            rows.append({
                "ticker": ticker,
                "olasilik_pct": float(item.get("olasilik_pct", 0.0)),
                "neden": "V3 sabah sıralaması + V4 işlem kapısı",
                "close": float(item.get("close", 0.0)),
                "pazar": audit_row.get("market"),
                "medyan_ciro_20_tl": float(audit_row.get("median_turnover_20_tl", 0.0)),
            })
    selected = pd.DataFrame(rows, columns=["ticker", "olasilik_pct", "neden", "close", "pazar", "medyan_ciro_20_tl"])
    count = len(selected)
    can_allocate = count >= MIN_ACTIONABLE_CANDIDATES
    allocation = 1_000_000.0 / count if can_allocate else 0.0
    selected["ayrilan_tutar_tl"] = allocation
    return selected, {
        "target_date": str(state["as_of"]),
        "list_date": now.date().isoformat(),
        "stage": STAGE,
        "eligible_v4_universe": count,
        "selected_count": count,
        "allocation_enabled": can_allocate,
        "allocation_per_ticker_tl": allocation,
        "no_trade_reason": None if can_allocate else "Üçten az uygun aday: sermaye korunur.",
        "base_model_version": str(state.get("model_version", "")),
        "official_status_source": source,
    }


def parse_as_of(value: str | None) -> datetime:
    return _as_of(value)
