#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YÜKSEK GETİRİ MOTORU V2 — BAĞIMSIZ ARAŞTIRMA / EĞİTİM MOTORU
================================================================

Bu dosya mevcut tavan motorundan tamamen bağımsızdır. Eski motoru import etmez,
değiştirmez veya canlı akışa bağlanmaz.

Amaç:
    T günü kapanışında bilinen verilerle, T+1 seansında fiyatın T kapanışına göre
    gün içi en az +%5 yüksek görme olasılığını tahmin etmek.

Başarı tanımı işlem getirisi değildir. Motor yalnızca yüksek hareket adaylarını
önceden yoğunlaştırmayı ölçer. Ana karne TOP-N precision, aynı gün/eşit aday
sayılı piyasa tabanına göre lift ve toplam yüksek-hareket yakalama oranıdır.

Bağımlılıklar: pandas + numpy + pyarrow (mevcut proje bağımlılıkları).

Örnek:
    python yuksek_getiri_engine_v2.py --self-test
    python yuksek_getiri_engine_v2.py --train --data-dir veriler
    python yuksek_getiri_engine_v2.py --scan --data-dir veriler

Eğitim; tarih sırasını bozmayan ileri-yürüyen doğrulama kullanır. Gelecekteki
satırlar eğitim özelliklerine sızmaz. Model yalnızca bu dosyanın ürettiği ayrı
JSON modelini ve ayrı rapor/CSV çıktısını yazar.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


MODEL_VERSION = "2.0.0"
DEFAULT_TARGET_PCT = 5.0
DEFAULT_MIN_TURNOVER_TL = 2_000_000.0
DEFAULT_TOP_N = 10
DEFAULT_MIN_UNIVERSE_PER_DATE = 300
MIN_HISTORY_BARS = 80
EXCLUDED_SYMBOLS = {"XU100", "XU030", "XU050", "XBANK", "XUSIN", "XUMAL"}

FEATURES: Tuple[str, ...] = (
    "ret_1g",
    "ret_3g",
    "ret_5g",
    "ret_10g",
    "ret_20g",
    "gap_pct",
    "intraday_ret_pct",
    "range_pct",
    "close_location",
    "body_to_range",
    "upper_wick_pct",
    "lower_wick_pct",
    "rsi_14",
    "pos_52h",
    "near_high_20",
    "near_high_60",
    "bb_width_pct",
    "atr_14_pct",
    "volume_ratio_1_20",
    "volume_ratio_5_20",
    "turnover_ratio_1_20",
    "log_turnover_20",
    "up_days_5",
    "above_sma_20_pct",
    "above_sma_50_pct",
    "large_move_count_20",
    "market_ret_5g",
    "market_ret_10g",
    "market_ret_20g",
    "market_atr_14_pct",
    "relative_ret_5g",
    "relative_ret_10g",
    "relative_ret_20g",
    "cmf_20",
    "obv_slope_20",
    "rs_above_ma_20",
)

FEATURE_FAMILY: Dict[str, str] = {
    "ret_1g": "momentum",
    "ret_3g": "momentum",
    "ret_5g": "momentum",
    "ret_10g": "momentum",
    "ret_20g": "momentum",
    "gap_pct": "mum_yapisi",
    "intraday_ret_pct": "mum_yapisi",
    "range_pct": "mum_yapisi",
    "close_location": "mum_yapisi",
    "body_to_range": "mum_yapisi",
    "upper_wick_pct": "mum_yapisi",
    "lower_wick_pct": "mum_yapisi",
    "rsi_14": "konum",
    "pos_52h": "konum",
    "near_high_20": "konum",
    "near_high_60": "konum",
    "bb_width_pct": "oynaklik",
    "atr_14_pct": "oynaklik",
    "volume_ratio_1_20": "hacim",
    "volume_ratio_5_20": "hacim",
    "turnover_ratio_1_20": "hacim",
    "log_turnover_20": "likidite",
    "up_days_5": "momentum",
    "above_sma_20_pct": "trend",
    "above_sma_50_pct": "trend",
    "large_move_count_20": "manip_riski",
    "market_ret_5g": "piyasa",
    "market_ret_10g": "piyasa",
    "market_ret_20g": "piyasa",
    "market_atr_14_pct": "piyasa",
    "relative_ret_5g": "goreli_guc",
    "relative_ret_10g": "goreli_guc",
    "relative_ret_20g": "goreli_guc",
    "cmf_20": "para_akisi",
    "obv_slope_20": "para_akisi",
    "rs_above_ma_20": "goreli_guc",
    "market_above_sma20_pct": "piyasa",
    "market_above_sma50_pct": "piyasa",
    "v1_momentum": "v1_kategori",
    "v1_squeeze": "v1_kategori",
    "v1_resistance": "v1_kategori",
    "v1_dip": "v1_kategori",
}


def _atomic_json_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _numeric_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    required = ("Open", "High", "Low", "Close", "Volume")
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Eksik OHLCV kolonları: {missing}")
    out = df.loc[:, required].copy()
    for col in required:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    index = pd.to_datetime(out.index)
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    out.index = index.normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    return num / den.replace(0, np.nan)


def _simple_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).rolling(period, min_periods=period).mean()
    rs = _safe_div(gain, loss)
    return 100.0 - 100.0 / (1.0 + rs)


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["Close"].shift(1)
    parts = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return parts.max(axis=1)


def _market_context(xu100: pd.DataFrame) -> pd.DataFrame:
    xu = _numeric_ohlcv(xu100)
    close = xu["Close"]
    context = pd.DataFrame(index=xu.index)
    context["market_ret_5g"] = close.pct_change(5) * 100.0
    context["market_ret_10g"] = close.pct_change(10) * 100.0
    context["market_ret_20g"] = close.pct_change(20) * 100.0
    context["market_atr_14_pct"] = _safe_div(
        _true_range(xu).rolling(14, min_periods=14).mean(), close
    ) * 100.0
    context["market_close"] = close
    context["market_above_sma20_pct"] = _safe_div(close, close.rolling(20, min_periods=20).mean()).sub(1.0).mul(100.0)
    context["market_above_sma50_pct"] = _safe_div(close, close.rolling(50, min_periods=50).mean()).sub(1.0).mul(100.0)
    return context


def build_feature_frame(df: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    """Her satırda yalnızca o gün ve öncesinden bilinen özellikleri üretir."""
    x = _numeric_ohlcv(df)
    open_ = x["Open"]
    high = x["High"]
    low = x["Low"]
    close = x["Close"]
    volume = x["Volume"]
    prev_close = close.shift(1)
    candle_range = (high - low).replace(0, np.nan)
    turnover = close * volume
    turnover_20 = turnover.rolling(20, min_periods=20).median()
    vol_20 = volume.rolling(20, min_periods=20).mean()
    vol_5 = volume.rolling(5, min_periods=5).mean()
    ret_1g = close.pct_change() * 100.0

    out = pd.DataFrame(index=x.index)
    out["ret_1g"] = ret_1g
    out["ret_3g"] = close.pct_change(3) * 100.0
    out["ret_5g"] = close.pct_change(5) * 100.0
    out["ret_10g"] = close.pct_change(10) * 100.0
    out["ret_20g"] = close.pct_change(20) * 100.0
    out["gap_pct"] = _safe_div(open_, prev_close).sub(1.0).mul(100.0)
    out["intraday_ret_pct"] = _safe_div(close, open_).sub(1.0).mul(100.0)
    out["range_pct"] = _safe_div(high - low, close).mul(100.0)
    out["close_location"] = _safe_div(close - low, candle_range).mul(100.0)
    out["body_to_range"] = _safe_div(close - open_, candle_range).mul(100.0)
    out["upper_wick_pct"] = _safe_div(high - pd.concat([open_, close], axis=1).max(axis=1), candle_range).mul(100.0)
    out["lower_wick_pct"] = _safe_div(pd.concat([open_, close], axis=1).min(axis=1) - low, candle_range).mul(100.0)
    out["rsi_14"] = _simple_rsi(close, 14)

    rolling_high_252 = high.rolling(252, min_periods=60).max()
    rolling_low_252 = low.rolling(252, min_periods=60).min()
    out["pos_52h"] = _safe_div(close - rolling_low_252, rolling_high_252 - rolling_low_252).mul(100.0)
    out["near_high_20"] = _safe_div(close, high.rolling(20, min_periods=20).max()).mul(100.0)
    out["near_high_60"] = _safe_div(close, high.rolling(60, min_periods=60).max()).mul(100.0)
    out["bb_width_pct"] = _safe_div(close.rolling(20, min_periods=20).std(), close.rolling(20, min_periods=20).mean()).mul(100.0)
    out["atr_14_pct"] = _safe_div(_true_range(x).rolling(14, min_periods=14).mean(), close).mul(100.0)
    out["volume_ratio_1_20"] = _safe_div(volume, vol_20)
    out["volume_ratio_5_20"] = _safe_div(vol_5, vol_20)
    out["turnover_ratio_1_20"] = _safe_div(turnover, turnover_20)
    out["log_turnover_20"] = np.log1p(turnover_20.clip(lower=0))
    out["up_days_5"] = (ret_1g > 0).rolling(5, min_periods=5).sum()
    out["above_sma_20_pct"] = _safe_div(close, close.rolling(20, min_periods=20).mean()).sub(1.0).mul(100.0)
    out["above_sma_50_pct"] = _safe_div(close, close.rolling(50, min_periods=50).mean()).sub(1.0).mul(100.0)
    out["large_move_count_20"] = (ret_1g.abs() >= 9.0).rolling(20, min_periods=20).sum()

    mfm = _safe_div((close - low) - (high - close), candle_range)
    mfv = mfm * volume
    out["cmf_20"] = _safe_div(mfv.rolling(20, min_periods=20).sum(), volume.rolling(20, min_periods=20).sum())
    obv = (np.sign(ret_1g.fillna(0.0)) * volume).cumsum()
    out["obv_slope_20"] = _safe_div(obv - obv.shift(20), vol_20.mul(20.0))

    out = out.join(market, how="left")
    out["relative_ret_5g"] = out["ret_5g"] - out["market_ret_5g"]
    out["relative_ret_10g"] = out["ret_10g"] - out["market_ret_10g"]
    out["relative_ret_20g"] = out["ret_20g"] - out["market_ret_20g"]
    rs_line = _safe_div(close, out["market_close"])
    out["rs_above_ma_20"] = _safe_div(rs_line, rs_line.rolling(20, min_periods=20).mean()).sub(1.0).mul(100.0)

    out["v1_momentum"] = out["rsi_14"].clip(50, 90).sub(50).div(40).mul(out["pos_52h"].clip(0, 100).div(100)).mul(100.0)
    out["v1_squeeze"] = out["bb_width_pct"].rolling(60, min_periods=20).rank(pct=True).rsub(1.0).mul(100.0)
    out["v1_resistance"] = out["near_high_20"].clip(90, 100).sub(90).div(10).mul(out["volume_ratio_1_20"].clip(0, 3).div(3)).mul(100.0)
    out["v1_dip"] = out["rsi_14"].clip(20, 50).sub(20).div(30).rsub(1.0).mul(out["pos_52h"].clip(0, 100).div(100).rsub(1.0)).mul(100.0)

    out["close"] = close
    out["turnover_20"] = turnover_20
    out["history_bars"] = np.arange(1, len(out) + 1)
    out["corporate_action_clean"] = (
        ret_1g.abs().rolling(20, min_periods=1).max() < 35.0
    )
    out["next_bar_date"] = out.index.to_series().shift(-1)
    out["next_high_pct"] = _safe_div(high.shift(-1), close).sub(1.0).mul(100.0)
    out["next_close_pct"] = _safe_div(close.shift(-1), close).sub(1.0).mul(100.0)
    return out.replace([np.inf, -np.inf], np.nan)


def _iter_symbol_files(data_dir: Path) -> Iterable[Tuple[str, Path]]:
    for path in sorted(data_dir.glob("*.IS_1d.parquet")):
        symbol = path.name.replace(".IS_1d.parquet", "")
        if symbol not in EXCLUDED_SYMBOLS:
            yield symbol, path


def load_market(data_dir: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    path = data_dir / "XU100.IS_1d.parquet"
    if not path.exists():
        raise FileNotFoundError(f"XU100 verisi bulunamadı: {path}")
    xu = _numeric_ohlcv(pd.read_parquet(path))
    return xu, _market_context(xu)


def build_training_dataset(
    data_dir: Path,
    target_pct: float,
    min_turnover_tl: float,
    min_universe_per_date: int,
    history_days: int = 0,
) -> Tuple[pd.DataFrame, dict]:
    xu, market = load_market(data_dir)
    expected_next_session = pd.Series(xu.index[1:], index=xu.index[:-1])
    pieces: List[pd.DataFrame] = []
    read_ok = 0
    skipped = 0
    for symbol, path in _iter_symbol_files(data_dir):
        try:
            raw = pd.read_parquet(path)
            if len(raw) < MIN_HISTORY_BARS + 1:
                skipped += 1
                continue
            frame = build_feature_frame(raw, market)
            eligible = (
                (frame["history_bars"] >= MIN_HISTORY_BARS)
                & (frame["turnover_20"] >= min_turnover_tl)
                & frame["corporate_action_clean"].fillna(False)
                & frame["next_high_pct"].notna()
                & frame["next_bar_date"].eq(frame.index.to_series().map(expected_next_session))
            )
            use = frame.loc[eligible, list(FEATURES) + ["next_high_pct", "next_close_pct"]].copy()
            if use.empty:
                skipped += 1
                continue
            use["ticker"] = symbol
            use["date"] = use.index
            use["target"] = (use["next_high_pct"] >= target_pct).astype(np.int8)
            pieces.append(use.reset_index(drop=True))
            read_ok += 1
        except Exception:
            skipped += 1

    if not pieces:
        raise RuntimeError("Eğitim için uygun hisse verisi üretilemedi.")
    dataset = pd.concat(pieces, ignore_index=True)
    dataset["date"] = pd.to_datetime(dataset["date"]).dt.tz_localize(None).dt.normalize()
    raw_date_count = int(dataset["date"].nunique())
    coverage = dataset.groupby("date").size()
    valid_dates = coverage[coverage >= min_universe_per_date].index
    dataset = dataset[dataset["date"].isin(valid_dates)].copy()
    if dataset.empty:
        raise RuntimeError(
            f"Hiçbir tarihte en az {min_universe_per_date} uygun hisse bulunamadı."
        )
    if history_days > 0:
        dates = np.sort(dataset["date"].unique())
        if len(dates) > history_days:
            dataset = dataset[dataset["date"] >= dates[-history_days]].copy()
    dataset.sort_values(["date", "ticker"], inplace=True, ignore_index=True)
    meta = {
        "symbols_read": read_ok,
        "symbols_skipped": skipped,
        "rows": int(len(dataset)),
        "dates": int(dataset["date"].nunique()),
        "raw_dates_before_coverage_gate": raw_date_count,
        "min_universe_per_date": min_universe_per_date,
        "min_daily_rows": int(dataset.groupby("date").size().min()),
        "max_daily_rows": int(dataset.groupby("date").size().max()),
        "start": str(dataset["date"].min().date()),
        "end": str(dataset["date"].max().date()),
        "target_rate": float(dataset["target"].mean()),
        "xu_last_date": str(xu.index[-1].date()),
    }
    return dataset, meta


@dataclass
class Preprocessor:
    median: np.ndarray
    low: np.ndarray
    high: np.ndarray
    mean: np.ndarray
    std: np.ndarray

    @classmethod
    def fit(cls, frame: pd.DataFrame) -> "Preprocessor":
        raw = frame.loc[:, list(FEATURES)].to_numpy(dtype=float)
        median = np.nanmedian(raw, axis=0)
        median = np.where(np.isfinite(median), median, 0.0)
        filled = np.where(np.isfinite(raw), raw, median)
        low = np.nanpercentile(filled, 1.0, axis=0)
        high = np.nanpercentile(filled, 99.0, axis=0)
        clipped = np.clip(filled, low, high)
        mean = clipped.mean(axis=0)
        std = clipped.std(axis=0)
        std = np.where(std < 1e-9, 1.0, std)
        return cls(median=median, low=low, high=high, mean=mean, std=std)

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        raw = frame.loc[:, list(FEATURES)].to_numpy(dtype=float)
        filled = np.where(np.isfinite(raw), raw, self.median)
        clipped = np.clip(filled, self.low, self.high)
        return (clipped - self.mean) / self.std

    def to_dict(self) -> dict:
        return {
            "median": self.median.tolist(),
            "low": self.low.tolist(),
            "high": self.high.tolist(),
            "mean": self.mean.tolist(),
            "std": self.std.tolist(),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Preprocessor":
        return cls(**{key: np.asarray(payload[key], dtype=float) for key in ("median", "low", "high", "mean", "std")})


def _sigmoid(values: np.ndarray) -> np.ndarray:
    values = np.clip(values, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-values))


def _recency_weights(dates: pd.Series, half_life_days: float) -> np.ndarray:
    if half_life_days <= 0:
        return np.ones(len(dates), dtype=float)
    end = pd.Timestamp(dates.max())
    age = (end - pd.to_datetime(dates)).dt.days.to_numpy(dtype=float)
    return np.power(0.5, age / half_life_days)


def fit_logistic(
    x: np.ndarray,
    y: np.ndarray,
    sample_weight: Optional[np.ndarray] = None,
    l2: float = 2.0,
    max_iter: int = 50,
) -> np.ndarray:
    n = len(y)
    if n == 0 or len(np.unique(y)) < 2:
        raise ValueError("Model eğitimi için iki hedef sınıfı da gerekli.")
    design = np.column_stack([np.ones(n), x])
    beta = np.zeros(design.shape[1], dtype=float)
    base_rate = float(np.clip(np.average(y, weights=sample_weight), 1e-6, 1 - 1e-6))
    beta[0] = math.log(base_rate / (1.0 - base_rate))
    weights = np.ones(n, dtype=float) if sample_weight is None else np.asarray(sample_weight, dtype=float)
    penalty = np.eye(design.shape[1], dtype=float)
    penalty[0, 0] = 0.0
    regularization = l2 * n / 1000.0

    for _ in range(max_iter):
        eta = design @ beta
        prob = _sigmoid(eta)
        variance = np.clip(prob * (1.0 - prob), 1e-6, None)
        work_weight = weights * variance
        adjusted = eta + (y - prob) / variance
        lhs = design.T @ (work_weight[:, None] * design) + regularization * penalty
        rhs = design.T @ (work_weight * adjusted)
        try:
            new_beta = np.linalg.solve(lhs, rhs)
        except np.linalg.LinAlgError:
            new_beta = np.linalg.lstsq(lhs, rhs, rcond=None)[0]
        if float(np.max(np.abs(new_beta - beta))) < 1e-7:
            beta = new_beta
            break
        beta = new_beta
    return beta


def predict_probability(x: np.ndarray, beta: np.ndarray) -> np.ndarray:
    return _sigmoid(beta[0] + x @ beta[1:])


def _wilson_interval(hits: int, total: int) -> Tuple[float, float]:
    if total <= 0:
        return float("nan"), float("nan")
    z = 1.96
    p = hits / total
    den = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / den
    half = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total) / den
    return 100.0 * (center - half), 100.0 * (center + half)


def evaluate_ranked(predictions: pd.DataFrame, top_values: Sequence[int]) -> dict:
    report: Dict[str, dict] = {}
    total_targets = int(predictions["target"].sum())
    for top_n in top_values:
        selected_parts: List[pd.DataFrame] = []
        baseline_expected = 0.0
        active_days = 0
        hit_days = 0
        for _, day in predictions.groupby("date", sort=True):
            n = min(top_n, len(day))
            if n <= 0:
                continue
            chosen = day.nlargest(n, "probability")
            selected_parts.append(chosen)
            baseline_expected += n * float(day["target"].mean())
            active_days += 1
            hit_days += int(chosen["target"].sum() > 0)
        selected = pd.concat(selected_parts, ignore_index=True) if selected_parts else pd.DataFrame()
        count = int(len(selected))
        hits = int(selected["target"].sum()) if count else 0
        precision = 100.0 * hits / count if count else 0.0
        baseline = 100.0 * baseline_expected / count if count else 0.0
        lift = precision / baseline if baseline > 0 else 0.0
        recall = 100.0 * hits / total_targets if total_targets else 0.0
        ci_low, ci_high = _wilson_interval(hits, count)
        report[f"top_{top_n}"] = {
            "days": active_days,
            "selected": count,
            "hits": hits,
            "precision_pct": precision,
            "precision_ci95_low": ci_low,
            "precision_ci95_high": ci_high,
            "matched_baseline_pct": baseline,
            "lift": lift,
            "recall_pct": recall,
            "days_with_at_least_one_hit_pct": 100.0 * hit_days / active_days if active_days else 0.0,
        }
    return report


def calibration_table(predictions: pd.DataFrame, bins: int = 5) -> List[dict]:
    if predictions.empty:
        return []
    ranked = predictions.copy()
    ranked["bucket"] = pd.qcut(ranked["probability"].rank(method="first"), bins, labels=False)
    rows: List[dict] = []
    for bucket, group in ranked.groupby("bucket", sort=True):
        rows.append(
            {
                "bucket": int(bucket) + 1,
                "n": int(len(group)),
                "avg_probability_pct": float(group["probability"].mean() * 100.0),
                "actual_target_pct": float(group["target"].mean() * 100.0),
            }
        )
    return rows


def walk_forward_validate(
    dataset: pd.DataFrame,
    min_train_days: int,
    test_days: int,
    l2: float,
    half_life_days: float,
) -> Tuple[pd.DataFrame, List[dict]]:
    dates = np.sort(dataset["date"].unique())
    if len(dates) < min_train_days + test_days:
        raise ValueError(
            f"İleri-yürüyen test için yetersiz tarih: {len(dates)}; "
            f"en az {min_train_days + test_days} gerekli."
        )
    predictions: List[pd.DataFrame] = []
    folds: List[dict] = []
    fold_no = 0
    for start in range(min_train_days, len(dates), test_days):
        test_set_dates = dates[start : start + test_days]
        if len(test_set_dates) == 0:
            break
        train = dataset[dataset["date"] < test_set_dates[0]].copy()
        test = dataset[dataset["date"].isin(test_set_dates)].copy()
        if train.empty or test.empty:
            continue
        prep = Preprocessor.fit(train)
        x_train = prep.transform(train)
        x_test = prep.transform(test)
        y_train = train["target"].to_numpy(dtype=float)
        sample_weight = _recency_weights(train["date"], half_life_days)
        beta = fit_logistic(x_train, y_train, sample_weight=sample_weight, l2=l2)
        test["probability"] = predict_probability(x_test, beta)
        fold_ranked = evaluate_ranked(test, top_values=(5, 10, 12))
        predictions.append(test[["date", "ticker", "target", "next_high_pct", "probability"]])
        fold_no += 1
        folds.append(
            {
                "fold": fold_no,
                "train_start": str(train["date"].min().date()),
                "train_end": str(train["date"].max().date()),
                "test_start": str(test["date"].min().date()),
                "test_end": str(test["date"].max().date()),
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "train_target_rate": float(train["target"].mean()),
                "test_target_rate": float(test["target"].mean()),
                "top_5": fold_ranked["top_5"],
                "top_10": fold_ranked["top_10"],
                "top_12": fold_ranked["top_12"],
            }
        )
    if not predictions:
        raise RuntimeError("İleri-yürüyen test tahmini üretilemedi.")
    return pd.concat(predictions, ignore_index=True), folds


def fit_final_model(
    dataset: pd.DataFrame,
    target_pct: float,
    min_turnover_tl: float,
    min_universe_per_date: int,
    l2: float,
    half_life_days: float,
    validation: dict,
) -> dict:
    prep = Preprocessor.fit(dataset)
    x = prep.transform(dataset)
    y = dataset["target"].to_numpy(dtype=float)
    weights = _recency_weights(dataset["date"], half_life_days)
    beta = fit_logistic(x, y, sample_weight=weights, l2=l2)
    coefficients = {
        feature: float(value) for feature, value in zip(FEATURES, beta[1:])
    }
    return {
        "model_version": MODEL_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "objective": {
            "description": "T kapanışından T+1 gün içi en yükseğe yüksek hareket",
            "target_pct": target_pct,
            "entry_reference": "T günü kapanışı",
            "exit_reference": "T+1 seansı gün içi en yüksek",
            "trade_strategy_claim": False,
        },
        "universe": {
            "min_median_turnover_20_tl": min_turnover_tl,
            "min_history_bars": MIN_HISTORY_BARS,
            "min_universe_per_training_date": min_universe_per_date,
            "excluded_symbols": sorted(EXCLUDED_SYMBOLS),
        },
        "training": {
            "start": str(dataset["date"].min().date()),
            "end": str(dataset["date"].max().date()),
            "rows": int(len(dataset)),
            "dates": int(dataset["date"].nunique()),
            "target_rate": float(dataset["target"].mean()),
            "l2": l2,
            "half_life_days": half_life_days,
        },
        "features": list(FEATURES),
        "feature_family": FEATURE_FAMILY,
        "preprocessor": prep.to_dict(),
        "intercept": float(beta[0]),
        "coefficients": coefficients,
        "validation": validation,
    }


def load_model(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        model = json.load(handle)
    if model.get("model_version") != MODEL_VERSION:
        raise ValueError(f"Model sürümü uyumsuz: {model.get('model_version')} != {MODEL_VERSION}")
    if tuple(model.get("features", ())) != FEATURES:
        raise ValueError("Model özellik sırası bu motorla uyuşmuyor.")
    return model


def latest_feature_rows(data_dir: Path, min_turnover_tl: float) -> Tuple[pd.DataFrame, pd.Timestamp, dict]:
    xu, market = load_market(data_dir)
    target_date = pd.Timestamp(xu.index[-1])
    rows: List[pd.DataFrame] = []
    skipped_date = 0
    skipped_quality = 0
    for symbol, path in _iter_symbol_files(data_dir):
        try:
            raw = pd.read_parquet(path)
            if target_date not in raw.index or len(raw) < MIN_HISTORY_BARS:
                skipped_date += 1
                continue
            frame = build_feature_frame(raw, market)
            row = frame.loc[[target_date]].copy()
            eligible = (
                float(row["history_bars"].iloc[0]) >= MIN_HISTORY_BARS
                and float(row["turnover_20"].iloc[0]) >= min_turnover_tl
                and bool(row["corporate_action_clean"].iloc[0])
            )
            if not eligible:
                skipped_quality += 1
                continue
            row["ticker"] = symbol
            rows.append(row.reset_index(drop=True))
        except Exception:
            skipped_quality += 1
    if not rows:
        raise RuntimeError("Son tarih için uygun aday havuzu üretilemedi.")
    frame = pd.concat(rows, ignore_index=True)
    return frame, target_date, {
        "eligible": int(len(frame)),
        "skipped_date": skipped_date,
        "skipped_quality": skipped_quality,
    }


def score_latest(data_dir: Path, model: dict, top_n: int) -> Tuple[pd.DataFrame, dict]:
    minimum = float(model["universe"]["min_median_turnover_20_tl"])
    latest, target_date, coverage = latest_feature_rows(data_dir, minimum)
    prep = Preprocessor.from_dict(model["preprocessor"])
    beta = np.asarray(
        [model["intercept"]] + [model["coefficients"][name] for name in FEATURES],
        dtype=float,
    )
    x = prep.transform(latest)
    latest["probability"] = predict_probability(x, beta)
    standardized = pd.DataFrame(x, columns=FEATURES, index=latest.index)
    contribution = standardized.mul(beta[1:], axis=1)

    reasons: List[str] = []
    for idx in latest.index:
        family_value: Dict[str, float] = {}
        for feature in FEATURES:
            family = FEATURE_FAMILY[feature]
            family_value[family] = family_value.get(family, 0.0) + float(contribution.at[idx, feature])
        positive = sorted(family_value.items(), key=lambda item: item[1], reverse=True)
        reasons.append(" + ".join(name for name, value in positive[:3] if value > 0) or "taban olasılık")
    latest["neden"] = reasons
    out = latest.nlargest(top_n, "probability").copy()
    out["olasilik_pct"] = out["probability"] * 100.0
    columns = [
        "ticker",
        "olasilik_pct",
        "neden",
        "close",
        "ret_5g",
        "ret_10g",
        "rsi_14",
        "pos_52h",
        "volume_ratio_1_20",
        "turnover_20",
    ]
    metadata = {"target_date": str(target_date.date()), **coverage}
    return out.loc[:, columns].reset_index(drop=True), metadata


def _json_safe(payload):
    if isinstance(payload, dict):
        return {str(key): _json_safe(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_json_safe(value) for value in payload]
    if isinstance(payload, (np.integer,)):
        return int(payload)
    if isinstance(payload, (np.floating,)):
        return float(payload)
    if isinstance(payload, pd.Timestamp):
        return payload.isoformat()
    return payload


def run_training(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir).resolve()
    print("Veri seti hazırlanıyor...")
    dataset, dataset_meta = build_training_dataset(
        data_dir=data_dir,
        target_pct=args.target_pct,
        min_turnover_tl=args.min_turnover,
        min_universe_per_date=args.min_universe,
        history_days=args.history_days,
    )
    print(
        f"{dataset_meta['symbols_read']} hisse · {dataset_meta['dates']} seans · "
        f"{dataset_meta['rows']} satır · doğal +%{args.target_pct:g} oranı "
        f"%{dataset_meta['target_rate'] * 100:.2f}"
    )
    print("İleri-yürüyen doğrulama çalışıyor...")
    predictions, folds = walk_forward_validate(
        dataset,
        min_train_days=args.min_train_days,
        test_days=args.test_days,
        l2=args.l2,
        half_life_days=args.half_life_days,
    )
    ranked = evaluate_ranked(predictions, top_values=(5, 10, 12))
    validation = {
        "method": "expanding_walk_forward",
        "out_of_sample_rows": int(len(predictions)),
        "out_of_sample_dates": int(predictions["date"].nunique()),
        "out_of_sample_target_rate": float(predictions["target"].mean()),
        "folds": folds,
        "ranked": ranked,
        "calibration": calibration_table(predictions, bins=5),
    }
    for name, metrics in ranked.items():
        print(
            f"{name.upper()}: {metrics['hits']}/{metrics['selected']} = "
            f"%{metrics['precision_pct']:.2f} · piyasa %{metrics['matched_baseline_pct']:.2f} · "
            f"{metrics['lift']:.2f}x · toplam yakalama %{metrics['recall_pct']:.2f}"
        )

    model = fit_final_model(
        dataset=dataset,
        target_pct=args.target_pct,
        min_turnover_tl=args.min_turnover,
        min_universe_per_date=args.min_universe,
        l2=args.l2,
        half_life_days=args.half_life_days,
        validation=validation,
    )
    model_path = Path(args.model_out).resolve()
    report_path = Path(args.report_out).resolve()
    _atomic_json_write(model_path, _json_safe(model))
    _atomic_json_write(
        report_path,
        _json_safe(
            {
                "model_version": MODEL_VERSION,
                "objective": model["objective"],
                "dataset": dataset_meta,
                "validation": validation,
            }
        ),
    )
    print(f"Model yazıldı: {model_path}")
    print(f"Rapor yazıldı: {report_path}")

    latest, latest_meta = score_latest(data_dir, model, args.top)
    latest_path = Path(args.latest_out).resolve()
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest.to_csv(latest_path, index=False, encoding="utf-8-sig")
    print(
        f"Son tarama: {latest_meta['target_date']} · uygun havuz {latest_meta['eligible']} · "
        f"ilk {len(latest)}"
    )
    print(latest.to_string(index=False, formatters={"olasilik_pct": lambda value: f"{value:.2f}"}))
    print(f"Son aday CSV: {latest_path}")


def run_scan(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir).resolve()
    model = load_model(Path(args.model_out).resolve())
    latest, metadata = score_latest(data_dir, model, args.top)
    if args.latest_out:
        path = Path(args.latest_out).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        latest.to_csv(path, index=False, encoding="utf-8-sig")
    print(
        f"Hedef veri tarihi: {metadata['target_date']} · uygun havuz {metadata['eligible']} · "
        f"tarihi eksik {metadata['skipped_date']} · kalite elemesi {metadata['skipped_quality']}"
    )
    print(latest.to_string(index=False, formatters={"olasilik_pct": lambda value: f"{value:.2f}"}))


def self_test() -> None:
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2024-01-01", periods=180)
    ret = rng.normal(0.0007, 0.025, len(dates))
    close = 20.0 * np.exp(np.cumsum(ret))
    open_ = close * (1.0 + rng.normal(0, 0.006, len(dates)))
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.002, 0.035, len(dates)))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.002, 0.03, len(dates)))
    volume = rng.lognormal(mean=14.5, sigma=0.5, size=len(dates))
    stock = pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )
    market = _market_context(stock)
    before = build_feature_frame(stock, market)
    changed = stock.copy()
    changed.loc[dates[-20]:, "Close"] *= 3.0
    changed.loc[dates[-20]:, "High"] *= 3.0
    changed.loc[dates[-20]:, "Low"] *= 3.0
    changed.loc[dates[-20]:, "Open"] *= 3.0
    after = build_feature_frame(changed, _market_context(changed))
    checkpoint = dates[-30]
    a = before.loc[checkpoint, list(FEATURES)].to_numpy(dtype=float)
    b = after.loc[checkpoint, list(FEATURES)].to_numpy(dtype=float)
    if not np.allclose(a, b, equal_nan=True):
        raise AssertionError("Gelecek veri geçmiş özellikleri değiştirdi; veri sızıntısı var.")

    sample = before.dropna(subset=["next_high_pct"]).copy()
    sample["target"] = (sample["next_high_pct"] >= 3.0).astype(int)
    sample["date"] = sample.index
    prep = Preprocessor.fit(sample)
    x = prep.transform(sample)
    y = sample["target"].to_numpy(dtype=float)
    beta = fit_logistic(x, y, l2=2.0)
    prob = predict_probability(x, beta)
    if not np.isfinite(prob).all() or not ((prob >= 0) & (prob <= 1)).all():
        raise AssertionError("Model geçerli olasılık üretmedi.")
    print("✅ SELF-TEST PASS — geçmiş özelliklerinde gelecek sızıntısı yok, model olasılıkları geçerli.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bağımsız Yüksek Getiri Motoru V2")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--train", action="store_true", help="Eğit + ileri-yürüyen doğrula + model yaz")
    action.add_argument("--scan", action="store_true", help="Kayıtlı modelle son günü tara")
    action.add_argument("--self-test", action="store_true", help="Sentetik güvenlik testini çalıştır")
    parser.add_argument("--data-dir", default="veriler")
    parser.add_argument("--model-out", default="yuksek_getiri_v2_model.json")
    parser.add_argument("--report-out", default="yuksek_getiri_v2_report.json")
    parser.add_argument("--latest-out", default="yuksek_getiri_v2_latest.csv")
    parser.add_argument("--target-pct", type=float, default=DEFAULT_TARGET_PCT)
    parser.add_argument("--min-turnover", type=float, default=DEFAULT_MIN_TURNOVER_TL)
    parser.add_argument("--min-universe", type=int, default=DEFAULT_MIN_UNIVERSE_PER_DATE)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--history-days", type=int, default=0, help="0=tüm uygun tarih")
    parser.add_argument("--min-train-days", type=int, default=120)
    parser.add_argument("--test-days", type=int, default=20)
    parser.add_argument("--l2", type=float, default=2.0)
    parser.add_argument("--half-life-days", type=float, default=180.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.self_test:
        self_test()
    elif args.train:
        run_training(args)
    else:
        run_scan(args)


if __name__ == "__main__":
    main()
