"""Boyun çizgisi ve yapısal destek/direnç çekirdeği.

Bu modül formasyon adı vermek için değil, grafikteki gerçek karar çizgilerini
bulmak için vardır. Fincan-kulp, TOBO, OBO, range, üçgen ve kama daha sonra bu
çıktıyı tüketebilir. Veri çekmez, dosya yazmaz, Streamlit veya veritabanına
dokunmaz.

Ana ilke:
    Tek fitil çizgi değildir. Zaman içinde ayrışmış pivot temasları, küçük
    sapmaların kabul edildiği bir tolerans bandında birleşmeli; fiyat da bu
    çizgiden anlamlı biçimde tepki vermiş olmalıdır.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from itertools import combinations
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd


ENGINE_VERSION = "0.1.0-research"
REQUIRED_COLUMNS = ("Open", "High", "Low", "Close")


CFG = {
    "min_rows": 90,
    "max_rows": 520,
    "pivot_radius": 3,
    "pivot_pct_floor": 0.025,
    "pivot_atr_mult": 1.15,
    "pivot_pct_cap": 0.10,
    "min_span_bars": 18,
    "max_span_bars": 320,
    "min_touches": 2,
    # Tolerans çizgiyi kalınlaştırmak için değil, gerçek hayattaki küçük
    # sapmaları kabul etmek içindir. Hisse oynaklaştıkça ATR ile büyür.
    "touch_atr_mult": 0.85,
    "touch_pct_floor": 0.0075,
    "touch_pct_cap": 0.035,
    "horizontal_max_drift_pct": 0.035,
    "break_atr_mult": 0.35,
    "break_pct_floor": 0.006,
    "near_atr_mult": 1.25,
    "near_pct_floor": 0.012,
    "reaction_atr_mult": 1.25,
    "reaction_pct_floor": 0.015,
    "reaction_window": 14,
    "fresh_touch_bars": 100,
    "dedupe_level_pct": 0.012,
}


@dataclass(frozen=True)
class Pivot:
    bar: int
    time: str
    price: float
    kind: str  # H veya L


@dataclass
class StructuralLine:
    role: str
    orientation: str
    state: str
    score: float
    level_now: float
    slope_per_bar: float
    start_bar: int
    end_bar: int
    start_time: str
    end_time: str
    touch_tolerance_pct: float
    break_buffer_pct: float
    touches: list[Pivot] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LineReport:
    engine_version: str
    data_ok: bool
    row_count: int
    first_time: Optional[str]
    last_time: Optional[str]
    issues: list[str]
    pivots: list[Pivot]
    lines: list[StructuralLine]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ts(value: Any) -> str:
    return pd.Timestamp(value).isoformat()


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if np.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _clean_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str], bool]:
    issues: list[str] = []
    if df is None or not isinstance(df, pd.DataFrame):
        return pd.DataFrame(), ["Fiyat tablosu yok."], False
    missing = [name for name in REQUIRED_COLUMNS if name not in df.columns]
    if missing:
        return pd.DataFrame(), [f"Eksik fiyat sütunları: {', '.join(missing)}."], False

    work = df.copy()
    try:
        work.index = pd.to_datetime(work.index)
    except Exception:
        return pd.DataFrame(), ["Tarih dizini okunamadı."], False
    work = work[~work.index.duplicated(keep="last")].sort_index()
    columns = list(REQUIRED_COLUMNS) + (["Volume"] if "Volume" in work.columns else [])
    work = work[columns]
    for column in columns:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    before = len(work)
    work = work.dropna(subset=list(REQUIRED_COLUMNS))
    if before != len(work):
        issues.append(f"{before - len(work)} eksik fiyat satırı çıkarıldı.")
    valid = (
        (work["Open"] > 0)
        & (work["High"] > 0)
        & (work["Low"] > 0)
        & (work["Close"] > 0)
        & (work["High"] >= work[["Open", "Close", "Low"]].max(axis=1))
        & (work["Low"] <= work[["Open", "Close", "High"]].min(axis=1))
    )
    if int((~valid).sum()):
        issues.append(f"{int((~valid).sum())} geçersiz OHLC satırı çıkarıldı.")
        work = work.loc[valid]
    work = work.tail(int(CFG["max_rows"]))
    if len(work) < int(CFG["min_rows"]):
        issues.append(f"Yetersiz geçmiş: {len(work)} bar var.")
        return work, issues, False
    return work, issues, True


def _atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    previous = df["Close"].shift(1)
    true_range = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - previous).abs(),
            (df["Low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(window, min_periods=max(5, window // 2)).mean()


def _pivot_threshold(df: pd.DataFrame, atr_now: float) -> float:
    close = df["Close"].to_numpy(dtype=float)
    pct = atr_now / max(close[-1], 1e-9)
    return float(
        np.clip(
            max(float(CFG["pivot_pct_floor"]), pct * float(CFG["pivot_atr_mult"])),
            float(CFG["pivot_pct_floor"]),
            float(CFG["pivot_pct_cap"]),
        )
    )


def _extract_pivots(df: pd.DataFrame, atr_now: float) -> list[Pivot]:
    radius = int(CFG["pivot_radius"])
    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    raw: list[Pivot] = []
    for bar in range(radius, len(df) - radius):
        high_window = high[bar - radius : bar + radius + 1]
        low_window = low[bar - radius : bar + radius + 1]
        is_high = high[bar] >= float(high_window.max()) - 1e-12
        is_low = low[bar] <= float(low_window.min()) + 1e-12
        if is_high == is_low:
            continue
        raw.append(Pivot(bar, _ts(df.index[bar]), float(high[bar] if is_high else low[bar]), "H" if is_high else "L"))

    alternating: list[Pivot] = []
    for pivot in raw:
        if not alternating:
            alternating.append(pivot)
            continue
        last = alternating[-1]
        if pivot.kind != last.kind:
            alternating.append(pivot)
            continue
        more_extreme = pivot.price > last.price if pivot.kind == "H" else pivot.price < last.price
        if more_extreme:
            alternating[-1] = pivot

    threshold = _pivot_threshold(df, atr_now)
    changed = True
    while changed and len(alternating) >= 4:
        changed = False
        for index in range(len(alternating) - 1):
            left, right = alternating[index], alternating[index + 1]
            move = abs(right.price - left.price) / max(abs(left.price), 1e-9)
            if move >= threshold:
                continue
            if index == 0:
                alternating.pop(index + 1)
            elif index + 1 == len(alternating) - 1:
                alternating.pop(index)
            else:
                alternating.pop(index + 1)
                alternating.pop(index)
            changed = True
            break
    return alternating


def _line_value(slope: float, intercept: float, bar: int | float) -> float:
    return float(slope * float(bar) + intercept)


def _fit(points: Iterable[Pivot]) -> Optional[tuple[float, float, float]]:
    values = list(points)
    if len(values) < 2:
        return None
    x = np.asarray([point.bar for point in values], dtype=float)
    y = np.asarray([point.price for point in values], dtype=float)
    if np.ptp(x) <= 0:
        return None
    slope, intercept = np.polyfit(x, y, 1)
    predicted = slope * x + intercept
    rmse_pct = float(np.sqrt(np.mean((y - predicted) ** 2)) / max(abs(float(np.median(y))), 1e-9))
    return float(slope), float(intercept), rmse_pct


def _line_buffers(price: float, atr_now: float) -> tuple[float, float, float]:
    touch = float(
        np.clip(
            max(atr_now * float(CFG["touch_atr_mult"]) / max(price, 1e-9), float(CFG["touch_pct_floor"])),
            float(CFG["touch_pct_floor"]), float(CFG["touch_pct_cap"]),
        )
    )
    broken = max(atr_now * float(CFG["break_atr_mult"]) / max(price, 1e-9), float(CFG["break_pct_floor"]))
    near = max(atr_now * float(CFG["near_atr_mult"]) / max(price, 1e-9), float(CFG["near_pct_floor"]))
    return touch, float(broken), float(near)


def _classify_orientation(role: str, total_drift_pct: float) -> str:
    if abs(total_drift_pct) <= float(CFG["horizontal_max_drift_pct"]):
        return "YATAY"
    if role == "DIRENC":
        return "ALCALAN" if total_drift_pct < 0 else "YUKSELEN"
    return "YUKSELEN" if total_drift_pct > 0 else "ALCALAN"


def _reaction_ratio(
    df: pd.DataFrame,
    touches: list[Pivot],
    role: str,
    slope: float,
    intercept: float,
    atr_now: float,
) -> float:
    if not touches:
        return 0.0
    reaction = max(atr_now * float(CFG["reaction_atr_mult"]), float(df["Close"].iloc[-1]) * float(CFG["reaction_pct_floor"]))
    valid = 0
    for touch in touches:
        start = touch.bar + 1
        end = min(len(df), start + int(CFG["reaction_window"]))
        if end <= start:
            continue
        boundary = _line_value(slope, intercept, touch.bar)
        if role == "DIRENC":
            moved = boundary - float(df["Low"].iloc[start:end].min())
        else:
            moved = float(df["High"].iloc[start:end].max()) - boundary
        valid += int(moved >= reaction)
    return valid / len(touches)


def _unbroken_until_last_touch(
    df: pd.DataFrame,
    role: str,
    slope: float,
    intercept: float,
    start_bar: int,
    last_touch_bar: int,
    break_buffer_pct: float,
) -> bool:
    if last_touch_bar <= start_bar:
        return True
    bars = np.arange(start_bar, last_touch_bar + 1)
    levels = slope * bars + intercept
    closes = df["Close"].to_numpy(dtype=float)[start_bar : last_touch_bar + 1]
    if role == "DIRENC":
        return not bool(np.any(closes > levels * (1 + break_buffer_pct)))
    return not bool(np.any(closes < levels * (1 - break_buffer_pct)))


def _state(
    df: pd.DataFrame,
    role: str,
    slope: float,
    intercept: float,
    last_touch_bar: int,
    break_buffer_pct: float,
    near_pct: float,
) -> tuple[str, dict[str, Any]]:
    close = df["Close"].to_numpy(dtype=float)
    bars = np.arange(len(close))
    levels = slope * bars + intercept
    start = min(max(last_touch_bar + 1, 1), len(close) - 1)
    if role == "DIRENC":
        crossed = close > levels * (1 + break_buffer_pct)
        lost = close < levels * (1 - break_buffer_pct)
        hold = close >= levels * (1 - break_buffer_pct)
    else:
        crossed = close < levels * (1 - break_buffer_pct)
        lost = close > levels * (1 + break_buffer_pct)
        hold = close <= levels * (1 + break_buffer_pct)
    hits = np.flatnonzero(crossed[start:])
    now_level = max(float(levels[-1]), 1e-9)
    distance = ((now_level - close[-1]) / now_level) if role == "DIRENC" else ((close[-1] - now_level) / now_level)
    metrics: dict[str, Any] = {"distance_to_line_pct": round(float(distance * 100), 3)}
    if hits.size:
        breakout_bar = start + int(hits[0])
        age = len(close) - 1 - breakout_bar
        metrics["breakout_bar"] = breakout_bar
        metrics["breakout_age_bars"] = age
        if bool(lost[-1]):
            return "FAILED_BREAKOUT", metrics
        if age >= 2 and abs(close[-1] - levels[-1]) / now_level <= near_pct and bool(hold[-1]):
            return "RETEST_HOLD", metrics
        consecutive = bool(breakout_bar + 1 < len(close) and crossed[breakout_bar + 1])
        if consecutive:
            return "BREAKOUT_CONFIRMED", metrics
        return "BREAKOUT_CANDIDATE", metrics
    if abs(distance) <= near_pct:
        return "APPROACHING", metrics
    return "ACTIVE", metrics


def _score(
    touches: list[Pivot],
    span: int,
    reaction_ratio: float,
    rmse_pct: float,
    tolerance_pct: float,
    fresh: bool,
    current_distance_pct: float,
    state: str,
) -> float:
    contacts = min(36.0, 14.0 + 7.0 * len(touches))
    duration = min(14.0, span / 8.0)
    reaction = 16.0 * reaction_ratio
    alignment = max(0.0, 12.0 * (1 - rmse_pct / max(tolerance_pct, 1e-9)))
    freshness = 4.0 if fresh else 0.0
    # Çok iyi ama bugünle ilgisiz eski çizgi arşivde kalır; ekran/tarama
    # sıralamasında güncel boyun çizgisinin önüne geçmez.
    relevance = max(0.0, 10.0 * (1 - current_distance_pct / 0.18))
    lifecycle = 8.0 if state in {"APPROACHING", "BREAKOUT_CANDIDATE", "BREAKOUT_CONFIRMED", "RETEST_HOLD"} else 0.0
    return round(float(np.clip(contacts + duration + reaction + alignment + freshness + relevance + lifecycle, 0, 100)), 1)


def _candidate_lines(df: pd.DataFrame, pivots: list[Pivot], atr_now: float) -> list[StructuralLine]:
    output: list[StructuralLine] = []
    current_bar = len(df) - 1
    for role, kind in (("DIRENC", "H"), ("DESTEK", "L")):
        points = [pivot for pivot in pivots if pivot.kind == kind and pivot.bar >= max(0, current_bar - int(CFG["max_span_bars"]))]
        for first, last in combinations(points, 2):
            span = last.bar - first.bar
            if not int(CFG["min_span_bars"]) <= span <= int(CFG["max_span_bars"]):
                continue
            seed_slope = (last.price - first.price) / span
            seed_intercept = first.price - seed_slope * first.bar
            ref = float(np.median(df["Close"].iloc[first.bar : last.bar + 1]))
            tolerance_pct, break_pct, near_pct = _line_buffers(ref, atr_now)
            touches = [
                point for point in points
                if first.bar <= point.bar <= last.bar
                and abs(point.price - _line_value(seed_slope, seed_intercept, point.bar)) / max(abs(_line_value(seed_slope, seed_intercept, point.bar)), 1e-9) <= tolerance_pct
            ]
            if len(touches) < int(CFG["min_touches"]):
                continue
            fitted = _fit(touches)
            if fitted is None:
                continue
            slope, intercept, rmse_pct = fitted
            touches = [
                point for point in points
                if first.bar <= point.bar <= last.bar
                and abs(point.price - _line_value(slope, intercept, point.bar)) / max(abs(_line_value(slope, intercept, point.bar)), 1e-9) <= tolerance_pct
            ]
            if len(touches) < int(CFG["min_touches"]):
                continue
            start_bar, last_touch_bar = touches[0].bar, touches[-1].bar
            if last_touch_bar - start_bar < int(CFG["min_span_bars"]):
                continue
            if not _unbroken_until_last_touch(df, role, slope, intercept, start_bar, last_touch_bar, break_pct):
                continue
            reaction_ratio = _reaction_ratio(df, touches, role, slope, intercept, atr_now)
            if reaction_ratio < 0.50:
                continue
            line_start = _line_value(slope, intercept, start_bar)
            line_end = _line_value(slope, intercept, last_touch_bar)
            drift = (line_end / max(abs(line_start), 1e-9) - 1.0)
            orientation = _classify_orientation(role, drift)
            fresh = current_bar - last_touch_bar <= int(CFG["fresh_touch_bars"])
            state, state_metrics = _state(df, role, slope, intercept, last_touch_bar, break_pct, near_pct)
            current_distance_pct = abs(float(state_metrics.get("distance_to_line_pct", 0.0))) / 100
            score = _score(
                touches,
                last_touch_bar - start_bar,
                reaction_ratio,
                rmse_pct,
                tolerance_pct,
                fresh,
                current_distance_pct,
                state,
            )
            notes = [
                "Küçük sapmalar volatiliteye göre tolere edildi.",
                "Tek fitil değil, zaman içinde ayrışmış pivot temasları kullanıldı.",
            ]
            if orientation == "YATAY":
                notes.append("Yatay çizgi boyun, range sınırı veya yatay destek/direnç adayıdır.")
            output.append(
                StructuralLine(
                    role=role,
                    orientation=orientation,
                    state=state,
                    score=score,
                    level_now=round(_line_value(slope, intercept, current_bar), 4),
                    slope_per_bar=round(slope, 8),
                    start_bar=start_bar,
                    end_bar=current_bar,
                    start_time=_ts(df.index[start_bar]),
                    end_time=_ts(df.index[-1]),
                    touch_tolerance_pct=round(tolerance_pct * 100, 3),
                    break_buffer_pct=round(break_pct * 100, 3),
                    touches=touches,
                    metrics={
                        "touch_count": len(touches),
                        "span_bars": last_touch_bar - start_bar,
                        "last_touch_age_bars": current_bar - last_touch_bar,
                        "line_drift_pct": round(drift * 100, 3),
                        "pivot_fit_error_pct": round(rmse_pct * 100, 3),
                        "reaction_ratio": round(reaction_ratio, 3),
                        **state_metrics,
                    },
                    notes=notes,
                )
            )
    return output


def _dedupe(lines: list[StructuralLine]) -> list[StructuralLine]:
    ordered = sorted(lines, key=lambda line: (-line.score, -line.metrics.get("touch_count", 0), -line.start_bar))
    kept: list[StructuralLine] = []
    for line in ordered:
        duplicate = False
        for existing in kept:
            if line.role != existing.role or line.orientation != existing.orientation:
                continue
            gap = abs(line.level_now - existing.level_now) / max(abs(existing.level_now), 1e-9)
            overlap_start = max(line.start_bar, existing.start_bar)
            overlap_end = min(line.end_bar, existing.end_bar)
            overlap = max(0, overlap_end - overlap_start)
            union = max(1, max(line.end_bar, existing.end_bar) - min(line.start_bar, existing.start_bar))
            if gap <= float(CFG["dedupe_level_pct"]) and overlap / union >= 0.55:
                duplicate = True
                break
        if not duplicate:
            kept.append(line)
    return sorted(kept, key=lambda line: (-line.score, line.role, line.orientation))


def analyze_structural_lines(df: pd.DataFrame, max_results: int = 16) -> LineReport:
    """Bir OHLC tablosundan yapısal çizgileri üretir; dış dünyaya yazmaz."""
    clean, issues, data_ok = _clean_frame(df)
    if not data_ok or clean.empty:
        return LineReport(ENGINE_VERSION, False, len(clean), None, None, issues, [], [])
    atr_now = _finite(_atr(clean).iloc[-1], float((clean["High"] - clean["Low"]).tail(20).median()))
    if atr_now <= 0:
        issues.append("Güncel oynaklık ölçülemedi.")
        return LineReport(ENGINE_VERSION, False, len(clean), _ts(clean.index[0]), _ts(clean.index[-1]), issues, [], [])
    pivots = _extract_pivots(clean, atr_now)
    lines = _dedupe(_candidate_lines(clean, pivots, atr_now))
    return LineReport(
        engine_version=ENGINE_VERSION,
        data_ok=True,
        row_count=len(clean),
        first_time=_ts(clean.index[0]),
        last_time=_ts(clean.index[-1]),
        issues=issues,
        pivots=pivots,
        lines=lines[: max(1, int(max_results))],
    )


def _synthetic_frame() -> pd.DataFrame:
    """Hafif öz-test için yatay dirençli, sonra kırılan sentetik seri."""
    rng = np.random.default_rng(7)
    close = []
    for index in range(120):
        if index < 90:
            base = 92 + 0.06 * index + 4 * np.sin(index / 5)
            base = min(base, 100.0)
        else:
            base = 100 + 0.45 * (index - 90)
        close.append(base + rng.normal(0, 0.25))
    close = np.asarray(close)
    high = close + rng.uniform(0.2, 0.65, len(close))
    low = close - rng.uniform(0.2, 0.65, len(close))
    open_ = close + rng.normal(0, 0.2, len(close))
    high = np.maximum(high, np.maximum(open_, close))
    low = np.minimum(low, np.minimum(open_, close))
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close},
        index=pd.date_range("2025-01-01", periods=len(close), freq="B"),
    )


def self_test() -> dict[str, Any]:
    report = analyze_structural_lines(_synthetic_frame())
    has_resistance = any(line.role == "DIRENC" and line.orientation == "YATAY" for line in report.lines)
    return {"passed": bool(report.data_ok and has_resistance), "line_count": len(report.lines), "lines": [line.to_dict() for line in report.lines[:3]]}


if __name__ == "__main__":
    import json

    print(json.dumps(self_test(), ensure_ascii=False, indent=2))
