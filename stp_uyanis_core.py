# -*- coding: utf-8 -*-
"""STP Uyanış — uzun baskı sonrası tepkiyi izleyen saf hesap çekirdeği.

Bu modül yalnız OHLCV tablosu okur; Streamlit, veritabanı veya ağ kullanmaz.
STP, mevcut Sentiment paneliyle aynıdır: typical price'ın 6-periyot EWMA'sı.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd


MIN_DAYS_BELOW = 7
LONG_PRESSURE_DAYS = 15
ONE_BOUNCE_WINDOW_DAYS = 16
MAX_TRACKING_AGE = 5


def _bullish_candle_name(open_: pd.Series, high: pd.Series, low: pd.Series,
                         close: pd.Series, index: int) -> str:
    """Kesişim günündeki uzun-yönlü klasik mum etiketini döndürür."""
    if index < 2:
        return ""

    o0, c0 = float(open_.iloc[index - 1]), float(close.iloc[index - 1])
    o1, c1 = float(open_.iloc[index]), float(close.iloc[index])
    h1, l1 = float(high.iloc[index]), float(low.iloc[index])
    body = abs(c1 - o1)
    green = c1 > o1
    red_prev = c0 < o0

    if red_prev and green and o1 <= c0 and c1 >= o0 and body > 0:
        return "Boğa engulfing"

    o2, c2 = float(open_.iloc[index - 2]), float(close.iloc[index - 2])
    if (c2 < o2 and abs(c0 - o0) < abs(c2 - o2) * 0.6
            and green and c1 > (o2 + c2) / 2):
        return "Morning star"

    if red_prev and green and o1 < c0 and c1 > (o0 + c0) / 2 and c1 < o0:
        return "Piercing line"

    lower_wick = min(o1, c1) - l1
    upper_wick = h1 - max(o1, c1)
    if body > 0 and lower_wick > body * 2 and upper_wick < body * 0.6:
        return "Hammer"
    return ""


def _classify_pressure(
    close: pd.Series,
    stp: pd.Series,
    event_index: int,
    min_days_below: int,
) -> Optional[dict[str, Any]]:
    """Kesişim öncesindeki baskıyı üç ayrı, sunuma dönük gruba ayırır.

    Uzun grupta 15 kesintisiz seans veya 16 seansta yalnız bir kısa yukarı
    tepki kabul edilir. Erken grupta ise 7--14 seansın tamamının STP altında
    kalması şarttır. Bu ayrım puan üretmez; yalnız hangi takip sunumunun
    kullanılacağını belirtir.
    """
    days_below = 0
    cursor = event_index - 1
    while cursor >= 0 and float(close.iloc[cursor]) < float(stp.iloc[cursor]):
        days_below += 1
        cursor -= 1

    if days_below >= LONG_PRESSURE_DAYS:
        return {
            "days_below": int(days_below),
            "pressure_kind": "long_strict",
            "pressure_label": f"{LONG_PRESSURE_DAYS}+ gün kesintisiz baskı",
            "long_pressure": True,
            "early_observation": False,
            "pressure_rank": 3,
        }

    # Tek tepki yalnız gerçekten arada kaldığında kabul edilir: son gün tekrar
    # STP altında olmalı, pencerenin iki ucu tepki olamaz ve kalan 15 günün
    # tamamı aşağıda olmalıdır.
    if event_index >= ONE_BOUNCE_WINDOW_DAYS:
        window_start = event_index - ONE_BOUNCE_WINDOW_DAYS
        prior_close = close.iloc[window_start:event_index]
        prior_stp = stp.iloc[window_start:event_index]
        under = (prior_close < prior_stp).to_numpy(dtype=bool)
        above = (prior_close > prior_stp).to_numpy(dtype=bool)
        bounce_positions = np.flatnonzero(above)
        one_internal_bounce = bool(
            int(under.sum()) == LONG_PRESSURE_DAYS
            and len(bounce_positions) == 1
            and 0 < int(bounce_positions[0]) < ONE_BOUNCE_WINDOW_DAYS - 1
            and bool(under[-1])
        )
        if one_internal_bounce:
            return {
                "days_below": int(days_below),
                "pressure_kind": "long_one_bounce",
                "pressure_label": "15 gün baskı · 1 kısa tepki",
                "long_pressure": True,
                "early_observation": False,
                "pressure_rank": 2,
            }

    if days_below >= min_days_below:
        return {
            "days_below": int(days_below),
            "pressure_kind": "early_strict",
            "pressure_label": (
                f"{min_days_below}–{LONG_PRESSURE_DAYS - 1} gün kesintisiz baskı"
            ),
            "long_pressure": False,
            "early_observation": True,
            "pressure_rank": 1,
        }
    return None


def calculate_stp_uyanis_status(
    df: pd.DataFrame,
    min_days_below: int = MIN_DAYS_BELOW,
    max_tracking_age: int = MAX_TRACKING_AGE,
) -> Optional[dict[str, Any]]:
    """Aktif STP Uyanış olayını döndürür; olay yoksa ``None``.

    Aktif pencere T0 kesişiminden T+5 planlanan çıkış seansına kadardır. T+1 ve
    T+2 kapanışları STP üzerinde kalmalı, bu iki günün dibi T0 mum dibini
    kırmamalıdır. Hacim/mum/anlamlı kesiş verileri ek bilgi olarak taşınır;
    burada puan veya alım emri üretilmez.
    """
    required = ("Open", "High", "Low", "Close")
    if df is None or df.empty or any(column not in df.columns for column in required):
        return None

    try:
        work = df.loc[:, [column for column in ("Open", "High", "Low", "Close", "Volume")
                          if column in df.columns]].copy()
        work = work.dropna(subset=list(required))
        if len(work) < 40:
            return None

        open_ = pd.to_numeric(work["Open"], errors="coerce")
        high = pd.to_numeric(work["High"], errors="coerce")
        low = pd.to_numeric(work["Low"], errors="coerce")
        close = pd.to_numeric(work["Close"], errors="coerce")
        valid = open_.notna() & high.notna() & low.notna() & close.notna()
        open_, high, low, close = open_[valid], high[valid], low[valid], close[valid]
        if len(close) < 40:
            return None

        volume = (pd.to_numeric(work.loc[valid, "Volume"], errors="coerce")
                  if "Volume" in work.columns else pd.Series(np.nan, index=close.index))
        typical_price = (high + low + close) / 3.0
        stp = typical_price.ewm(span=6, adjust=False).mean()

        # Son iki kapanış STP altında kaldıysa tepki artık ekranda tutulmaz.
        # Yalnız bugün tekrar altına kesmişse, işlem planını silmeden uyarı verilir.
        last_two_below = bool(
            float(close.iloc[-2]) < float(stp.iloc[-2])
            and float(close.iloc[-1]) < float(stp.iloc[-1])
        )
        if last_two_below:
            return None
        recross_down = bool(
            float(close.iloc[-2]) >= float(stp.iloc[-2])
            and float(close.iloc[-1]) < float(stp.iloc[-1])
        )
        cross_up = (close.shift(1) <= stp.shift(1)) & (close > stp)
        cross_positions = np.flatnonzero(cross_up.fillna(False).to_numpy())
        if len(cross_positions) == 0:
            return None

        event_index = int(cross_positions[-1])
        age = len(close) - 1 - event_index
        if age < 0 or age > max_tracking_age:
            return None

        pressure = _classify_pressure(close, stp, event_index, min_days_below)
        if pressure is None:
            return None
        days_below = int(pressure["days_below"])

        signal_low = float(low.iloc[event_index])
        t1_ok = None
        t2_ok = None
        if age >= 1:
            t1_ok = bool(
                float(close.iloc[event_index + 1]) > float(stp.iloc[event_index + 1])
                and float(low.iloc[event_index + 1]) >= signal_low
            )
        if age >= 2:
            today_ok = bool(
                float(close.iloc[event_index + 2]) > float(stp.iloc[event_index + 2])
                and float(low.iloc[event_index + 2]) >= signal_low
            )
            t2_ok = bool(t1_ok and today_ok)
        invalidated = (t1_ok is False) or (t2_ok is False)

        if recross_down:
            state = "recross_down"
            state_label = "STP TEKRAR ALTINA KESTİ"
        elif invalidated:
            state = "invalid"
            state_label = "GEÇERSİZ"
        elif age == 0:
            state = "t0"
            state_label = "T0 · ONAY BEKLİYOR"
        elif age == 1:
            state = "t1"
            state_label = "T+1 · TEYİT TAKİBİ"
        elif age == 2:
            state = "confirmed"
            state_label = "T+2 · ONAYLANDI"
        elif age < 5:
            state = "active"
            state_label = f"T+{age} · PLAN TAKİPTE"
        else:
            state = "exit"
            state_label = "T+5 · ÇIKIŞ GÜNÜ"

        previous_volume = volume.iloc[max(0, event_index - 20):event_index].dropna()
        average_volume = float(previous_volume.mean()) if len(previous_volume) >= 20 else np.nan
        signal_volume = float(volume.iloc[event_index]) if pd.notna(volume.iloc[event_index]) else np.nan
        volume_ratio = (signal_volume / average_volume
                        if np.isfinite(signal_volume) and np.isfinite(average_volume) and average_volume > 0
                        else np.nan)

        previous_close = close.shift(1)
        true_range = pd.concat([
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ], axis=1).max(axis=1)
        atr14 = true_range.rolling(14, min_periods=14).mean()
        penetration = float(close.iloc[event_index] - stp.iloc[event_index])
        meaningful = bool(pd.notna(atr14.iloc[event_index])
                          and penetration >= float(atr14.iloc[event_index]) * 0.25)

        signal_date = close.index[event_index]
        return {
            "state": state,
            "state_label": state_label,
            "event_age": int(age),
            "days_below": int(days_below),
            "long_pressure": bool(pressure["long_pressure"]),
            "pressure_kind": pressure["pressure_kind"],
            "pressure_label": pressure["pressure_label"],
            "early_observation": bool(pressure["early_observation"]),
            "pressure_rank": int(pressure["pressure_rank"]),
            "signal_date": signal_date,
            "signal_price": float(close.iloc[event_index]),
            "signal_low": signal_low,
            "current_price": float(close.iloc[-1]),
            "stp": float(stp.iloc[-1]),
            "volume_ratio": None if not np.isfinite(volume_ratio) else round(float(volume_ratio), 2),
            "candle": _bullish_candle_name(open_, high, low, close, event_index),
            "meaningful": meaningful,
            "t1_ok": t1_ok,
            "t2_ok": t2_ok,
            "invalidated": invalidated,
            "recross_down": recross_down,
        }
    except Exception:
        return None
