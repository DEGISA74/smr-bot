# -*- coding: utf-8 -*-
"""Çizgi Yapısı'nın Master Scan köprüsü.

Master Scan'in az önce aldığı ortak OHLCV fotoğrafını kullanır; ikinci kez
parquet taramaz ve tek-hisse ekranındaki ``cizgi_yapi.analiz`` ile aynı eleği
çalıştırır. Bu motorun sonucu henüz skor/AI/terazi kanıtı değildir; yalnızca
Tarama Merkezi'nde ayrı bir araştırma listesi olarak sunulur.
"""
from __future__ import annotations

from typing import Any, Iterable

import pandas as pd

import cizgi_yapi


def _clean_symbol(value: object) -> str:
    return str(value or "").upper().replace(".IS", "").strip()


def _frame_for_symbol(batch_data: Any, ticker: object) -> pd.DataFrame:
    """Master Scan MultiIndex tablosundan tek sembolün OHLCV tablosunu alır."""
    if batch_data is None or not hasattr(batch_data, "empty") or batch_data.empty:
        return pd.DataFrame()
    try:
        if isinstance(batch_data.columns, pd.MultiIndex):
            level_zero = set(str(value) for value in batch_data.columns.get_level_values(0))
            clean = _clean_symbol(ticker)
            for candidate in (str(ticker), clean, f"{clean}.IS"):
                if candidate in level_zero:
                    return batch_data[candidate].dropna(how="all").copy()
            return pd.DataFrame()
        return batch_data.dropna(how="all").copy()
    except Exception:
        return pd.DataFrame()


def _turnover(frame: pd.DataFrame) -> float:
    try:
        close = pd.to_numeric(frame["Close"], errors="coerce")
        volume = pd.to_numeric(frame["Volume"], errors="coerce")
        value = (close * volume).dropna()
        return float(value.tail(20).median()) if not value.empty else 0.0
    except Exception:
        return 0.0


def scan_batch_snapshot(
    batch_data: Any,
    symbols: Iterable[object],
    *,
    timeframe: str = "1d",
    lik_taban: float = cizgi_yapi.LIK_TABAN_VARSAYILAN,
    bist100_symbols: Iterable[object] = (),
) -> list[dict[str, Any]]:
    """Ortak Master Scan fotoğrafında Çizgi Yapısı adaylarını bulur.

    ``bist100_symbols`` yalnız öncelik rozeti içindir; adayları BIST100 ile
    sınırlamaz. Böylece BIST100 dışındaki likit hisseler de kaybolmaz.
    """
    bist100 = {_clean_symbol(value) for value in (bist100_symbols or [])}
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw_ticker in symbols or []:
        clean = _clean_symbol(raw_ticker)
        if not clean or clean in cizgi_yapi.ENDEKS_SEMBOL or clean in seen:
            continue
        seen.add(clean)

        frame = _frame_for_symbol(batch_data, raw_ticker)
        if frame.empty or len(frame) < 120:
            continue

        ticker_text = str(raw_ticker or "").upper().strip()
        is_bist = ticker_text.endswith(".IS") or clean in bist100
        turnover = _turnover(frame)
        if is_bist and lik_taban and turnover < lik_taban:
            continue

        try:
            structure = cizgi_yapi.analiz(frame, timeframe=timeframe, elek=True)
        except Exception:
            structure = None
        if not structure:
            continue

        display_ticker = ticker_text or clean
        if "." not in display_ticker and is_bist:
            display_ticker = f"{clean}.IS"
        status_text = cizgi_yapi.durum_rozeti(structure)[0]
        results.append(
            {
                "sembol": display_ticker,
                "kisa": clean,
                "bist": bool(is_bist),
                "bist100": bool(clean in bist100),
                "ciro": turnover,
                "kaynak": "Çizgi Yapısı",
                "ad": structure["ad"],
                "aile": structure["aile"],
                "yon": structure["yon"],
                "stage": structure["stage"],
                "durum": status_text,
                "bar": structure["ss"],
                "bas_tarih": structure["bas_tarih"],
                "temas": len(structure["temas_ust"]) + len(structure["temas_alt"]),
                "tetik": structure["tetik"],
                "gecersiz": structure["gecersiz"],
                "fiyat": structure["fiyat"],
                "agiz": round(structure["oran"] * 100, 1),
                "mesafe": (
                    round(abs(float(structure["mesafe"])), 1)
                    if structure["mesafe"] is not None
                    else None
                ),
                "kirilim_tarih": structure["kirilim_tarih"],
                "son_tarih": str(pd.Timestamp(frame.index[-1]).date()),
                "timeframe": timeframe,
                "engine_version": cizgi_yapi.ENGINE_VERSION,
            }
        )

    return results


__all__ = ["scan_batch_snapshot"]
