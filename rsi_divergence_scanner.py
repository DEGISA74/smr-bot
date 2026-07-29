# -*- coding: utf-8 -*-
"""Wilder RSI pozitif uyumsuzluk taraması.

Bu modül yalnızca fiyat/hacim verisinden hesap yapar. Streamlit, veri indirme ve
veritabanı yazımı scan_pipeline.py tarafında kalır.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from deepening_policy import money_flow_quality, rsi_journey


RSI_PERIOD = 14
ATR_PERIOD = 14
MIN_HISTORY = 120
PIVOT_SIDE = 2
PAIR_MIN_GAP = 5
PAIR_MAX_GAP = 30
TRIGGER_MAX_AGE = 7
ACTIVE_TRIGGER_BARS = 3
PRICE_LOWER_ATR = 0.25
RSI_MIN_DELTA = 4.0
RSI_FIRST_MAX = 40.0
RSI_SECOND_MAX = 50.0
MIN_ADV_TL = 30_000_000.0
HIGH_LIQ_TL = 300_000_000.0
MAX_ENTRY_ATR_FROM_LOW = 1.5
MIN_RR = 1.5

RESULT_COLUMNS = [
    "Sembol",
    "Durum",
    "Sinyal_Tarihi",
    "Tetik_Yasi",
    "Ilk_Dip",
    "Ikinci_Dip",
    "Fiyat",
    "Giris",
    "Stop",
    "Hedef",
    "Risk_Odul",
    "RSI",
    "RSI_1",
    "RSI_2",
    "RSI_Farki",
    "Giris_Dipten_ATR",
    "ADV20_Mn_TL",
    "Dipler_Arasi_Bar",
    "Para_Akisi_Skor",
    "Para_Akisi_Kalite",
    "Para_Akisi_Destek",
    "Para_Akisi_Risk",
    "Kalite_Skoru",
    "Kalite",
    "Kalite_Detay",
    "Yolculuk_Asamasi",
    "Yolculuk_Gunu",
    "Yolculuk_Anahtari",
    "Bir_R_Gunu",
    "Iki_R_Gunu",
    "Stop_Gunu",
    "Tepe_Gunu",
    "Maks_Kazanc",
    "Maks_R",
    "Aciklama",
]


def _normalise_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    frame = raw.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        flattened = []
        for column in frame.columns:
            parts = [str(part) for part in column]
            match = next(
                (
                    part
                    for part in parts
                    if part.lower() in {"open", "high", "low", "close", "volume"}
                ),
                "_".join(parts),
            )
            flattened.append(match)
        frame.columns = flattened

    aliases = {str(column).strip().lower(): column for column in frame.columns}
    required = {}
    for name in ("open", "high", "low", "close", "volume"):
        if name not in aliases:
            return pd.DataFrame()
        required[aliases[name]] = name.title()

    frame = frame.rename(columns=required)[list(required.values())].copy()
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    if getattr(frame.index, "tz", None) is not None:
        frame.index = frame.index.tz_localize(None)
    frame = frame[~frame.index.isna()]
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    for column in frame.columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["Open", "High", "Low", "Close", "Volume"])


def _wilder_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff().to_numpy(dtype=float)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    result = np.full(len(close), np.nan, dtype=float)
    if len(close) <= period:
        return pd.Series(result, index=close.index)

    average_gain = float(np.nanmean(gains[1 : period + 1]))
    average_loss = float(np.nanmean(losses[1 : period + 1]))
    if average_gain == 0 and average_loss == 0:
        result[period] = 50.0
    elif average_loss == 0:
        result[period] = 100.0
    else:
        result[period] = 100 - 100 / (1 + average_gain / average_loss)

    for index in range(period + 1, len(close)):
        average_gain = ((period - 1) * average_gain + gains[index]) / period
        average_loss = ((period - 1) * average_loss + losses[index]) / period
        if average_gain == 0 and average_loss == 0:
            result[index] = 50.0
        elif average_loss == 0:
            result[index] = 100.0
        else:
            result[index] = 100 - 100 / (1 + average_gain / average_loss)
    return pd.Series(result, index=close.index)


def _atr(frame: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    prior_close = frame["Close"].shift(1)
    true_range = pd.concat(
        [
            frame["High"] - frame["Low"],
            (frame["High"] - prior_close).abs(),
            (frame["Low"] - prior_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period, min_periods=period).mean()


def _pivot_lows(low: np.ndarray) -> list[int]:
    pivots = []
    for index in range(PIVOT_SIDE, len(low) - PIVOT_SIDE):
        value = low[index]
        if not np.isfinite(value):
            continue
        if (
            value < low[index - 1]
            and value < low[index - 2]
            and value < low[index + 1]
            and value < low[index + 2]
        ):
            pivots.append(index)
    return pivots


def _healthy_window(frame: pd.DataFrame, signal_index: int) -> bool:
    if signal_index < MIN_HISTORY - 1:
        return False
    recent = frame.iloc[signal_index - MIN_HISTORY + 1 : signal_index + 1]
    if len(recent) < MIN_HISTORY:
        return False
    if (recent[["Open", "High", "Low", "Close"]] <= 0).any().any():
        return False
    if (recent["High"] < recent[["Open", "Close", "Low"]].max(axis=1)).any():
        return False
    if (recent["Low"] > recent[["Open", "Close", "High"]].min(axis=1)).any():
        return False
    if int((recent["Volume"].tail(10) <= 0).sum()) >= 5:
        return False
    if (recent["Close"].pct_change().tail(60).abs() > 0.25).any():
        return False
    return True


def _resolved_after_trigger(
    low: np.ndarray,
    high: np.ndarray,
    trigger: int,
    stop: float,
    target: float,
) -> bool:
    if trigger >= len(low) - 1:
        return False
    later_low = low[trigger + 1 :]
    later_high = high[trigger + 1 :]
    return bool(
        np.any(later_low <= stop)
        or (target > 0 and np.any(later_high >= target))
    )


def detect_wilder_positive_divergence(
    ticker: str,
    raw: pd.DataFrame,
    active_trigger_bars: int = ACTIVE_TRIGGER_BARS,
) -> dict | None:
    """Bir BIST hissesindeki en yeni, henüz sonuçlanmamış Wilder uyumsuzluğunu bulur."""
    ticker_upper = str(ticker).upper()
    if not ticker_upper.endswith(".IS") or ticker_upper.startswith("XU"):
        return None

    frame = _normalise_ohlcv(raw)
    if len(frame) < MIN_HISTORY:
        return None

    close_series = frame["Close"]
    rsi_series = _wilder_rsi(close_series)
    atr_series = _atr(frame)
    sma5_series = close_series.rolling(5, min_periods=5).mean()

    high = frame["High"].to_numpy(dtype=float)
    low = frame["Low"].to_numpy(dtype=float)
    close = close_series.to_numpy(dtype=float)
    volume = frame["Volume"].to_numpy(dtype=float)
    rsi = rsi_series.to_numpy(dtype=float)
    atr = atr_series.to_numpy(dtype=float)
    sma5 = sma5_series.to_numpy(dtype=float)
    pivots = _pivot_lows(low)
    candidates = []

    for first, second in zip(pivots, pivots[1:]):
        if second < MIN_HISTORY - 1:
            continue
        gap = second - first
        if gap < PAIR_MIN_GAP or gap > PAIR_MAX_GAP:
            continue
        if not (
            np.isfinite(atr[first])
            and atr[first] > 0
            and np.isfinite(atr[second])
            and atr[second] > 0
            and np.isfinite(rsi[first])
            and np.isfinite(rsi[second])
        ):
            continue

        rebound_high = float(np.nanmax(high[first + 1 : second]))
        if rebound_high - low[first] < atr[first]:
            continue
        if low[second] > low[first] - PRICE_LOWER_ATR * atr[second]:
            continue
        if rsi[first] > RSI_FIRST_MAX or rsi[second] > RSI_SECOND_MAX:
            continue
        if rsi[second] < rsi[first] + RSI_MIN_DELTA:
            continue

        trigger = None
        for index in range(
            second + PIVOT_SIDE,
            min(second + TRIGGER_MAX_AGE, len(frame) - 1) + 1,
        ):
            if not (np.isfinite(rsi[index]) and np.isfinite(sma5[index])):
                continue
            if float(np.nanmin(low[second + 1 : index + 1])) < low[second]:
                break
            if (
                close[index] > high[index - 1]
                and close[index] > sma5[index]
                and rsi[index] > rsi[index - 1]
            ):
                trigger = index
                break
        if trigger is None:
            continue

        trigger_age = len(frame) - 1 - trigger
        if trigger_age < 0 or trigger_age > max(0, int(active_trigger_bars)):
            continue
        if not _healthy_window(frame, trigger):
            continue

        adv20 = float(
            np.nanmean(
                close[trigger - 19 : trigger + 1]
                * volume[trigger - 19 : trigger + 1]
            )
        )
        if not np.isfinite(adv20) or adv20 < MIN_ADV_TL:
            continue
        average_volume = float(np.nanmean(volume[trigger - 19 : trigger + 1]))
        relative_volume = (
            float(volume[trigger] / average_volume) if average_volume > 0 else 0.0
        )
        run5 = (
            float(close[trigger] / close[trigger - 5] - 1)
            if trigger >= 5
            else 0.0
        )
        run10 = (
            float(close[trigger] / close[trigger - 10] - 1)
            if trigger >= 10
            else 0.0
        )
        if (
            adv20 < HIGH_LIQ_TL
            and (run5 > 0.20 or run10 > 0.40)
            and relative_volume > 3.0
        ):
            continue

        entry = float(close[trigger])
        stop = float(low[second] - PRICE_LOWER_ATR * atr[second])
        target = rebound_high
        risk = entry - stop
        reward = target - entry
        if risk <= 0 or reward <= 0:
            continue

        entry_atr = float((entry - low[second]) / atr[second])
        if entry_atr > MAX_ENTRY_ATR_FROM_LOW:
            continue

        risk_reward = float(reward / risk)
        confirmed = risk_reward >= MIN_RR
        if _resolved_after_trigger(low, high, trigger, stop, target):
            continue

        status = "✅ ONAYLI" if confirmed else "👀 İZLEME"
        rsi_delta = float(rsi[second] - rsi[first])
        # Para akışı zorunlu kapı değildir. Uyumsuzluğun kalitesini destekleyen
        # veya zayıflatan ikinci kanıt olarak yalnız tetik anına kadar hesaplanır.
        flow_quality = money_flow_quality(frame.iloc[: trigger + 1])
        divergence_score = float(
            np.clip(
                45.0
                + rsi_delta * 3.0
                + min(risk_reward, 3.0) * 8.0
                - entry_atr * 6.0,
                0.0,
                100.0,
            )
        )
        quality_score = int(
            round(divergence_score * 0.70 + float(flow_quality["score"]) * 0.30)
        )
        candidates.append(
            {
                "Sembol": ticker_upper,
                "Durum": status,
                "Sinyal_Tarihi": frame.index[trigger].date().isoformat(),
                "Tetik_Yasi": int(trigger_age),
                "Ilk_Dip": frame.index[first].date().isoformat(),
                "Ikinci_Dip": frame.index[second].date().isoformat(),
                "Fiyat": round(float(close[-1]), 4),
                "Giris": round(entry, 4),
                "Stop": round(stop, 4),
                "Hedef": round(target, 4),
                "Risk_Odul": round(risk_reward, 2),
                "RSI": round(float(rsi[-1]), 2),
                "RSI_1": round(float(rsi[first]), 2),
                "RSI_2": round(float(rsi[second]), 2),
                "RSI_Farki": round(rsi_delta, 2),
                "Giris_Dipten_ATR": round(entry_atr, 2),
                "ADV20_Mn_TL": round(adv20 / 1_000_000, 1),
                "Dipler_Arasi_Bar": int(gap),
                "Para_Akisi_Skor": int(flow_quality["score"]),
                "Para_Akisi_Kalite": str(flow_quality["label"]),
                "Para_Akisi_Destek": int(flow_quality["support_count"]),
                "Para_Akisi_Risk": int(flow_quality["risk_count"]),
                "Kalite_Skoru": quality_score,
                "Kalite": (
                    f"{'ONAYLI' if confirmed else 'İZLEME'} · "
                    f"Para akışı {str(flow_quality['label']).lower()}"
                ),
                "Kalite_Detay": str(flow_quality["detail"]),
                "Yolculuk_Anahtari": "rsi_pozitif_uyumsuzluk",
                "Aciklama": (
                    f"RSI {rsi[first]:.1f}→{rsi[second]:.1f}; "
                    f"tetik {trigger_age} işlem günü önce"
                ),
                "_confirmed": bool(confirmed),
                "_trigger_index": int(trigger),
            }
        )

    if not candidates:
        return None

    best = max(
        candidates,
        key=lambda row: (
            bool(row["_confirmed"]),
            int(row["_trigger_index"]),
            float(row["Risk_Odul"]),
        ),
    )
    best.pop("_confirmed", None)
    best.pop("_trigger_index", None)
    journey = rsi_journey(frame, best, max_sessions=60)
    best["Yolculuk_Asamasi"] = journey.get("stage")
    best["Yolculuk_Gunu"] = journey.get("days_observed")
    best["Bir_R_Gunu"] = journey.get("one_r_day")
    best["Iki_R_Gunu"] = journey.get("two_r_day")
    best["Stop_Gunu"] = journey.get("stop_day")
    best["Tepe_Gunu"] = journey.get("peak_day")
    best["Maks_Kazanc"] = journey.get("max_gain_pct")
    best["Maks_R"] = journey.get("max_r")
    best["_journey"] = journey
    return best


def enumerate_wilder_positive_divergences(
    ticker: str,
    raw: pd.DataFrame,
    start_date=None,
) -> list[dict]:
    """Geçmiş sinyalleri tetik gününde bilinen verilerle, ileriye bakmadan üretir."""
    frame = _normalise_ohlcv(raw)
    if len(frame) < MIN_HISTORY:
        return []
    start_ts = pd.Timestamp(start_date).normalize() if start_date is not None else None
    close_series = frame["Close"]
    rsi_series = _wilder_rsi(close_series)
    atr_series = _atr(frame)
    sma5_series = close_series.rolling(5, min_periods=5).mean()
    high = frame["High"].to_numpy(dtype=float)
    low = frame["Low"].to_numpy(dtype=float)
    close = close_series.to_numpy(dtype=float)
    volume = frame["Volume"].to_numpy(dtype=float)
    rsi = rsi_series.to_numpy(dtype=float)
    atr = atr_series.to_numpy(dtype=float)
    sma5 = sma5_series.to_numpy(dtype=float)
    pivots = _pivot_lows(low)
    by_trigger: dict[int, list[dict]] = {}

    for first, second in zip(pivots, pivots[1:]):
        if second < MIN_HISTORY - 1:
            continue
        gap = second - first
        if gap < PAIR_MIN_GAP or gap > PAIR_MAX_GAP:
            continue
        if not (
            np.isfinite(atr[first])
            and atr[first] > 0
            and np.isfinite(atr[second])
            and atr[second] > 0
            and np.isfinite(rsi[first])
            and np.isfinite(rsi[second])
        ):
            continue
        rebound_high = float(np.nanmax(high[first + 1 : second]))
        if rebound_high - low[first] < atr[first]:
            continue
        if low[second] > low[first] - PRICE_LOWER_ATR * atr[second]:
            continue
        if rsi[first] > RSI_FIRST_MAX or rsi[second] > RSI_SECOND_MAX:
            continue
        if rsi[second] < rsi[first] + RSI_MIN_DELTA:
            continue

        trigger = None
        for index in range(
            second + PIVOT_SIDE,
            min(second + TRIGGER_MAX_AGE, len(frame) - 1) + 1,
        ):
            if not (np.isfinite(rsi[index]) and np.isfinite(sma5[index])):
                continue
            if float(np.nanmin(low[second + 1 : index + 1])) < low[second]:
                break
            if (
                close[index] > high[index - 1]
                and close[index] > sma5[index]
                and rsi[index] > rsi[index - 1]
            ):
                trigger = index
                break
        if trigger is None:
            continue
        if start_ts is not None and frame.index[trigger].normalize() < start_ts:
            continue
        if not _healthy_window(frame, trigger):
            continue

        adv20 = float(
            np.nanmean(
                close[trigger - 19 : trigger + 1]
                * volume[trigger - 19 : trigger + 1]
            )
        )
        if not np.isfinite(adv20) or adv20 < MIN_ADV_TL:
            continue
        average_volume = float(np.nanmean(volume[trigger - 19 : trigger + 1]))
        relative_volume = (
            float(volume[trigger] / average_volume) if average_volume > 0 else 0.0
        )
        run5 = float(close[trigger] / close[trigger - 5] - 1.0)
        run10 = float(close[trigger] / close[trigger - 10] - 1.0)
        if (
            adv20 < HIGH_LIQ_TL
            and (run5 > 0.20 or run10 > 0.40)
            and relative_volume > 3.0
        ):
            continue

        entry = float(close[trigger])
        stop = float(low[second] - PRICE_LOWER_ATR * atr[second])
        target = rebound_high
        risk = entry - stop
        reward = target - entry
        if risk <= 0 or reward <= 0:
            continue
        entry_atr = float((entry - low[second]) / atr[second])
        if entry_atr > MAX_ENTRY_ATR_FROM_LOW:
            continue
        risk_reward = float(reward / risk)
        confirmed = risk_reward >= MIN_RR
        rsi_delta = float(rsi[second] - rsi[first])
        flow_quality = money_flow_quality(frame.iloc[: trigger + 1])
        divergence_score = float(
            np.clip(
                45.0
                + rsi_delta * 3.0
                + min(risk_reward, 3.0) * 8.0
                - entry_atr * 6.0,
                0.0,
                100.0,
            )
        )
        quality_score = int(
            round(divergence_score * 0.70 + float(flow_quality["score"]) * 0.30)
        )
        by_trigger.setdefault(trigger, []).append(
            {
                "Sembol": str(ticker).upper(),
                "Durum": "✅ ONAYLI" if confirmed else "👀 İZLEME",
                "Sinyal_Tarihi": frame.index[trigger].date().isoformat(),
                "Tetik_Yasi": 0,
                "Ilk_Dip": frame.index[first].date().isoformat(),
                "Ikinci_Dip": frame.index[second].date().isoformat(),
                "Fiyat": round(entry, 4),
                "Giris": round(entry, 4),
                "Stop": round(stop, 4),
                "Hedef": round(target, 4),
                "Risk_Odul": round(risk_reward, 2),
                "RSI": round(float(rsi[trigger]), 2),
                "RSI_1": round(float(rsi[first]), 2),
                "RSI_2": round(float(rsi[second]), 2),
                "RSI_Farki": round(rsi_delta, 2),
                "Giris_Dipten_ATR": round(entry_atr, 2),
                "ADV20_Mn_TL": round(adv20 / 1_000_000, 1),
                "Dipler_Arasi_Bar": int(gap),
                "Para_Akisi_Skor": int(flow_quality["score"]),
                "Para_Akisi_Kalite": str(flow_quality["label"]),
                "Para_Akisi_Destek": int(flow_quality["support_count"]),
                "Para_Akisi_Risk": int(flow_quality["risk_count"]),
                "Kalite_Skoru": quality_score,
                "Kalite": (
                    f"{'ONAYLI' if confirmed else 'İZLEME'} · "
                    f"Para akışı {str(flow_quality['label']).lower()}"
                ),
                "Kalite_Detay": str(flow_quality["detail"]),
                "Yolculuk_Anahtari": "rsi_pozitif_uyumsuzluk",
                "Aciklama": (
                    f"RSI {rsi[first]:.1f}→{rsi[second]:.1f}; tetik günü"
                ),
                "_confirmed": bool(confirmed),
                "_trigger_index": int(trigger),
            }
        )

    events = []
    for trigger in sorted(by_trigger):
        signal = max(
            by_trigger[trigger],
            key=lambda row: (
                bool(row["_confirmed"]),
                float(row["Risk_Odul"]),
            ),
        )
        signal.pop("_confirmed", None)
        signal.pop("_trigger_index", None)
        journey = rsi_journey(frame, signal, max_sessions=60)
        signal["Yolculuk_Asamasi"] = journey.get("stage")
        signal["Yolculuk_Gunu"] = journey.get("days_observed")
        signal["Bir_R_Gunu"] = journey.get("one_r_day")
        signal["Iki_R_Gunu"] = journey.get("two_r_day")
        signal["Stop_Gunu"] = journey.get("stop_day")
        signal["Tepe_Gunu"] = journey.get("peak_day")
        signal["Maks_Kazanc"] = journey.get("max_gain_pct")
        signal["Maks_R"] = journey.get("max_r")
        signal["_journey"] = journey
        events.append(signal)
    return events


def build_positive_divergence_view(
    raw: pd.DataFrame,
    signal: dict,
    context_bars: int = 20,
) -> dict | None:
    """Popup grafiği için fiyat/RSI serisini ve işaretli noktaları hazırlar."""
    if not isinstance(signal, dict):
        return None
    frame = _normalise_ohlcv(raw)
    if frame.empty:
        return None

    def _position(date_value) -> int | None:
        try:
            target = pd.Timestamp(date_value).normalize()
        except Exception:
            return None
        normalized = frame.index.normalize()
        matches = np.flatnonzero(normalized == target)
        return int(matches[-1]) if len(matches) else None

    first = _position(signal.get("Ilk_Dip"))
    second = _position(signal.get("Ikinci_Dip"))
    trigger = _position(signal.get("Sinyal_Tarihi"))
    if first is None or second is None or trigger is None:
        return None
    if not (first < second <= trigger < len(frame)):
        return None

    rsi = _wilder_rsi(frame["Close"]).to_numpy(dtype=float)
    start = max(0, first - max(5, int(context_bars)))
    end = len(frame)
    chart = frame.iloc[start:end].copy()
    chart_rsi = rsi[start:end]
    if chart.empty or not np.isfinite(chart_rsi).any():
        return None

    return {
        "dates": [timestamp.to_pydatetime() for timestamp in chart.index],
        "close": chart["Close"].to_numpy(dtype=float),
        "high": chart["High"].to_numpy(dtype=float),
        "low": chart["Low"].to_numpy(dtype=float),
        "rsi": chart_rsi,
        "first_pos": first - start,
        "second_pos": second - start,
        "trigger_pos": trigger - start,
        "first_price": float(frame["Low"].iloc[first]),
        "second_price": float(frame["Low"].iloc[second]),
        "trigger_price": float(frame["Close"].iloc[trigger]),
        "first_rsi": float(rsi[first]),
        "second_rsi": float(rsi[second]),
        "current_price": float(frame["Close"].iloc[-1]),
    }


def empty_result_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)
