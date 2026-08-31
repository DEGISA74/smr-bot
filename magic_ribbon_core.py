# -*- coding: utf-8 -*-
"""
Magic Ribbon v5.1 — 4S BIST100 gözlem taraması.

Bu modül, kullanıcının paylaştığı Pine göstergesindeki Fast/Slow yön
hizalanmasını yalnızca kapanmış ve taze 4 saatlik barlarla hesaplar. Sonuç
bir işlem emri değildir; Master Scan içinde derin inceleme için aday havuzudur.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from zamanlama_core import get_4s_data


ROOT = Path(__file__).resolve().parent
BIST100_PATH = ROOT / "_bist100.json"
MIN_BARS = 60


def _normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper().replace(".IS", "")


def load_bist100_symbols(path: Path | None = None) -> set[str]:
    """Güncel BIST100 listesini güvenli biçimde okur; hatada boş döner."""
    source = path or BIST100_PATH
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return set()
    if not isinstance(payload, list):
        return set()
    return {
        symbol
        for symbol in (_normalize_symbol(item) for item in payload)
        if symbol and symbol.isascii() and symbol.replace("_", "").isalnum()
    }


def wma(series: pd.Series, length: int) -> pd.Series:
    """Pine ta.wma karşılığı: eski bardan yeni bara 1..N ağırlıkları."""
    weights = np.arange(1, length + 1, dtype=float)
    total = float(weights.sum())
    return series.rolling(length, min_periods=length).apply(
        lambda values: float(np.dot(values, weights) / total), raw=True
    )


def cora_wave(source: pd.Series, length: int = 10, smooth: int = 3) -> pd.Series:
    """Pine f_CoraWave karşılığı."""
    start_wt = 0.01
    end_wt = float(length)
    ratio = math.pow(end_wt / start_wt, 1.0 / (length - 1)) - 1.0
    base = 1.0 + ratio * 2.0
    weights = start_wt * np.power(base, np.arange(1, length + 1, dtype=float))
    total = float(weights.sum())
    raw = source.rolling(length, min_periods=length).apply(
        lambda values: float(np.dot(values, weights) / total), raw=True
    )
    return wma(raw, smooth)


def lazy_line(source: pd.Series, length: int = 15) -> pd.Series:
    """Pine f_LazyLine karşılığı."""
    if length <= 4:
        return wma(source, length)
    w2 = int(math.floor(length / 3.0 + 0.5))
    w1 = int(math.floor((length - w2) / 2.0 + 0.5))
    w3 = int((length - w2) / 2.0)
    first = wma(source, w1)
    second = wma(first, w2)
    return wma(second, w3)


def add_ribbon_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Fast/Slow çizgileri ve yukarı-aşağı hizalanma durumlarını ekler."""
    out = df.copy()
    close = pd.to_numeric(out["Close"], errors="coerce").astype(float)
    out["fast_line"] = cora_wave(close, 10, 3)
    out["slow_line"] = lazy_line(close, 15)
    fast_up = out["fast_line"].gt(out["fast_line"].shift(1)).fillna(False)
    slow_up = out["slow_line"].gt(out["slow_line"].shift(1)).fillna(False)
    ribbon_up = (fast_up & slow_up).astype(bool)
    ribbon_down = ((~fast_up) & (~slow_up)).astype(bool)
    out["ribbon_up"] = ribbon_up
    out["ribbon_down"] = ribbon_down
    out["up_trigger"] = ribbon_up & ~ribbon_up.shift(1, fill_value=False)
    out["down_trigger"] = ribbon_down & ~ribbon_down.shift(1, fill_value=False)
    return out


def _format_bar(value: object) -> str:
    try:
        return pd.Timestamp(value).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return "—"


def scan_magic_ribbon_bist100() -> pd.DataFrame:
    """BIST100 içinde aktif 4S yukarı hizalanmalarını döndürür.

    Veri tazeliği ve yarım bar kapısı ``zamanlama_core.get_4s_data`` içindedir.
    Sonuçlar yalnızca son kapanmış bar hâlâ iki çizgi yukarı eğimli ise üretilir.
    """
    symbols = sorted(load_bist100_symbols())
    rows: list[dict[str, object]] = []
    skipped = 0

    for symbol in symbols:
        df = get_4s_data(symbol)
        if df is None or len(df) < MIN_BARS:
            skipped += 1
            continue
        try:
            enriched = add_ribbon_columns(df)
            valid = enriched[enriched[["fast_line", "slow_line"]].notna().all(axis=1)]
            if len(valid) < 2:
                skipped += 1
                continue
            last = enriched.iloc[-1]
            if not bool(last.get("ribbon_up", False)):
                continue

            trigger_positions = np.flatnonzero(enriched["up_trigger"].to_numpy(dtype=bool))
            if len(trigger_positions) == 0:
                skipped += 1
                continue
            trigger_i = int(trigger_positions[-1])
            age_bars = int(len(enriched) - 1 - trigger_i)

            up_bars = 0
            for value in reversed(enriched["ribbon_up"].tolist()):
                if not bool(value):
                    break
                up_bars += 1

            turnover = np.nan
            if "Volume" in enriched.columns:
                volume = pd.to_numeric(enriched["Volume"], errors="coerce")
                turnover = float((enriched["Close"].astype(float) * volume).tail(20).median())

            rows.append({
                "Sembol": symbol,
                "Fiyat": float(last["Close"]),
                "Durum": "YENİ HİZALANMA" if age_bars == 0 else "HİZALANMA SÜRÜYOR",
                "TetikYaşı": age_bars,
                "YukarıBar": up_bars,
                "SonBar": _format_bar(enriched.index[-1]),
                "TetikBar": _format_bar(enriched.index[trigger_i]),
                "FastLine": float(last["fast_line"]),
                "SlowLine": float(last["slow_line"]),
                "MedianTurnover": turnover,
            })
        except (KeyError, TypeError, ValueError, IndexError):
            skipped += 1

    result = pd.DataFrame(rows)
    if not result.empty:
        result["_fresh"] = result["TetikYaşı"].eq(0).astype(int)
        result = result.sort_values(
            ["_fresh", "TetikYaşı", "MedianTurnover"],
            ascending=[False, True, False],
            na_position="last",
        ).drop(columns="_fresh").reset_index(drop=True)

    result.attrs.update({
        "universe": "BIST100",
        "universe_count": len(symbols),
        "data_count": len(result) + skipped,
        "candidate_count": len(result),
        "skipped_count": skipped,
        "closed_bars_only": True,
    })
    return result

