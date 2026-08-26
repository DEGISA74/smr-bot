# -*- coding: utf-8 -*-
"""Patron Terminal — bağımsız Formasyon V2 araştırma motoru.

Bu dosya mevcut canlı formasyon motorunu veya Streamlit arayüzünü değiştirmez.
Amaç, karar ile çizimin aynı tepe/dip ve aynı seviyeleri kullanacağı ölçülebilir
bir çekirdek kurmaktır.

Desteklenen yapılar:
  * Yükselen üçgen
  * Alçalan üçgen
  * Fincan-kulp (kulp tamamlanmadan yalnızca "fincan adayı")
  * TOBO
  (OBO 27 Tem 2026'da çıkarıldı — tek-spike "baş" seçimi güvenilmezdi; H&S
   dedektörü yalnız TOBO/bullish inverse üretir.)

Önemli:
  * Kalite puanı, formasyonun kârlılık olasılığı değildir.
  * Hedef yalnızca doğrulanmış kırılımdan sonra üretilir.
  * Eşikler başlangıç araştırma değerleridir; insan etiketi ve ileri getiri
    ölçümü olmadan canlı karar/ağırlık olarak kullanılmamalıdır.

Kütüphane kullanımı:
    report = analyze_formations(df, ticker="EREGL", timeframe="1d")

Yerel denetim:
    python formasyon_v2.py --ticker EREGL --timeframe both
    python formasyon_v2.py --universe --timeframe 1d --limit 100
    python formasyon_v2.py --self-test
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd


ENGINE_VERSION = "2.0.1-research"
REQUIRED_COLUMNS = ("Open", "High", "Low", "Close")


TIMEFRAME_CONFIG = {
    "1d": {
        "min_rows": 90,
        "max_rows": 520,
        "pivot_radius": 3,
        "pivot_pct_floor": 0.025,
        "triangle_spans": (45, 60, 80, 105, 140),
        "triangle_min_span": 25,
        "triangle_max_span": 150,
        "cup_min_bars": 35,
        "cup_max_bars": 190,
        "handle_min_bars": 3,
        "handle_max_bars": 30,
        "hs_min_bars": 25,
        "hs_max_bars": 190,
        "recent_structure_bars": 90,
        "gap_limit": 0.18,
        "hs_max_wait_after_right_shoulder": 30,
    },
    "4h": {
        "min_rows": 120,
        "max_rows": 900,
        "pivot_radius": 4,
        "pivot_pct_floor": 0.018,
        "triangle_spans": (60, 90, 120, 180, 240),
        "triangle_min_span": 35,
        "triangle_max_span": 260,
        "cup_min_bars": 50,
        "cup_max_bars": 300,
        "handle_min_bars": 4,
        "handle_max_bars": 45,
        "hs_min_bars": 35,
        "hs_max_bars": 280,
        "recent_structure_bars": 140,
        "gap_limit": 0.15,
        "hs_max_wait_after_right_shoulder": 45,
    },
}


@dataclass(frozen=True)
class PatternPoint:
    role: str
    bar: int
    time: str
    price: float


@dataclass(frozen=True)
class PatternLine:
    role: str
    start_bar: int
    end_bar: int
    start_time: str
    end_time: str
    start_price: float
    end_price: float


@dataclass
class PatternCandidate:
    pattern: str
    direction: str
    stage: str
    quality_score: float
    trigger: float
    invalidation: float
    target: Optional[float]
    start_bar: int
    end_bar: int
    start_time: str
    end_time: str
    breakout_bar: Optional[int] = None
    breakout_time: Optional[str] = None
    points: list[PatternPoint] = field(default_factory=list)
    lines: list[PatternLine] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class FormationReport:
    engine_version: str
    ticker: Optional[str]
    timeframe: str
    row_count: int
    first_time: Optional[str]
    last_time: Optional[str]
    data_ok: bool
    data_issues: list[str]
    patterns: list[PatternCandidate]
    inactive_patterns: list[PatternCandidate] = field(default_factory=list)
    calibration_status: str = "ARAŞTIRMA — İNSAN ETİKETİ VE GETİRİ KARNESİ BEKLİYOR"


@dataclass(frozen=True)
class _Pivot:
    bar: int
    price: float
    kind: str


@dataclass(frozen=True)
class _LineFit:
    slope: float
    intercept: float
    r2: float
    mean_error_pct: float

    def at(self, bar: int | float) -> float:
        return float(self.slope * float(bar) + self.intercept)


def _ts(value: Any) -> str:
    try:
        return pd.Timestamp(value).isoformat()
    except Exception:
        return str(value)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def _price_jump_series(df: pd.DataFrame) -> pd.Series:
    previous_close = df["Close"].shift(1)
    close_jump = df["Close"].pct_change().abs()
    open_gap = (df["Open"] / previous_close - 1).abs()
    return pd.concat([close_jump, open_gap], axis=1).max(axis=1)


def _clean_frame(df: pd.DataFrame, timeframe: str) -> tuple[pd.DataFrame, list[str], bool]:
    if timeframe not in TIMEFRAME_CONFIG:
        raise ValueError(f"Desteklenmeyen zaman dilimi: {timeframe}")
    issues: list[str] = []
    if df is None or not isinstance(df, pd.DataFrame):
        return pd.DataFrame(), ["Veri tablosu yok."], False
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        return pd.DataFrame(), [f"Eksik sütunlar: {', '.join(missing)}"], False

    work = df.copy()
    if not isinstance(work.index, pd.DatetimeIndex):
        try:
            work.index = pd.to_datetime(work.index)
        except Exception:
            return pd.DataFrame(), ["Tarih dizini okunamadı."], False
    work = work[~work.index.duplicated(keep="last")].sort_index()
    keep = list(REQUIRED_COLUMNS) + (["Volume"] if "Volume" in work.columns else [])
    work = work[keep]
    for col in keep:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    before = len(work)
    work = work.dropna(subset=list(REQUIRED_COLUMNS))
    if len(work) != before:
        issues.append(f"{before - len(work)} adet eksik fiyat satırı çıkarıldı.")

    valid = (
        (work["Open"] > 0)
        & (work["High"] > 0)
        & (work["Low"] > 0)
        & (work["Close"] > 0)
        & (work["High"] >= work[["Open", "Close", "Low"]].max(axis=1))
        & (work["Low"] <= work[["Open", "Close", "High"]].min(axis=1))
    )
    invalid_count = int((~valid).sum())
    if invalid_count:
        issues.append(f"{invalid_count} adet geçersiz OHLC satırı çıkarıldı.")
        work = work.loc[valid]

    cfg = TIMEFRAME_CONFIG[timeframe]
    work = work.tail(int(cfg["max_rows"]))
    if len(work) < int(cfg["min_rows"]):
        issues.append(
            f"Yetersiz geçmiş: {len(work)} adet bar var, en az "
            f"{int(cfg['min_rows'])} adet gerekli."
        )
        return work, issues, False

    price_jump = _price_jump_series(work)
    jump_rows = price_jump[price_jump > float(cfg["gap_limit"])]
    for idx, value in jump_rows.tail(5).items():
        issues.append(
            f"Bölünme/veri kopukluğu adayı: {_ts(idx)} tarihinde "
            f"%{abs(float(value)) * 100:.1f} fiyat sıçraması."
        )
    if "Volume" not in work.columns:
        issues.append("Hacim sütunu yok; hacim yalnızca yumuşak teyit olduğu için analiz sürecek.")
        work["Volume"] = np.nan
    elif float(work["Volume"].fillna(0).iloc[-1]) <= 0:
        issues.append("Son barda hacim sıfır; hacim teyidi kullanılmayacak.")
    return work, issues, True


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["Close"].shift(1)
    parts = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev).abs(),
            (df["Low"] - prev).abs(),
        ],
        axis=1,
    )
    return parts.max(axis=1)


def _atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    return _true_range(df).rolling(window, min_periods=max(5, window // 2)).mean()


def _dynamic_move_threshold(df: pd.DataFrame, timeframe: str) -> float:
    cfg = TIMEFRAME_CONFIG[timeframe]
    atr_pct = (_atr(df) / df["Close"]).replace([np.inf, -np.inf], np.nan)
    recent = _finite(atr_pct.tail(60).median(), float(cfg["pivot_pct_floor"]))
    return float(np.clip(max(float(cfg["pivot_pct_floor"]), recent * 1.15), 0.015, 0.10))


def _extract_pivots(df: pd.DataFrame, timeframe: str) -> list[_Pivot]:
    cfg = TIMEFRAME_CONFIG[timeframe]
    radius = int(cfg["pivot_radius"])
    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    raw: list[_Pivot] = []
    for i in range(radius, len(df) - radius):
        wh = high[i - radius : i + radius + 1]
        wl = low[i - radius : i + radius + 1]
        is_high = high[i] >= float(np.max(wh)) - 1e-12
        is_low = low[i] <= float(np.min(wl)) + 1e-12
        if is_high and is_low:
            continue
        if is_high:
            raw.append(_Pivot(i, float(high[i]), "H"))
        elif is_low:
            raw.append(_Pivot(i, float(low[i]), "L"))

    raw.sort(key=lambda p: p.bar)
    alternating: list[_Pivot] = []
    for pivot in raw:
        if not alternating:
            alternating.append(pivot)
            continue
        last = alternating[-1]
        if pivot.kind == last.kind:
            more_extreme = (
                pivot.price > last.price if pivot.kind == "H" else pivot.price < last.price
            )
            if more_extreme:
                alternating[-1] = pivot
        else:
            alternating.append(pivot)

    min_move = _dynamic_move_threshold(df, timeframe)
    changed = True
    while changed and len(alternating) >= 4:
        changed = False
        for i in range(len(alternating) - 1):
            left, right = alternating[i], alternating[i + 1]
            move = abs(right.price - left.price) / max(abs(left.price), 1e-9)
            if move >= min_move:
                continue
            if i == 0:
                alternating.pop(i + 1)
            elif i + 1 == len(alternating) - 1:
                alternating.pop(i)
            else:
                alternating.pop(i + 1)
                alternating.pop(i)
            changed = True
            break
    return alternating


def _fit_line(points: Iterable[_Pivot]) -> Optional[_LineFit]:
    values = list(points)
    if len(values) < 2:
        return None
    x = np.asarray([p.bar for p in values], dtype=float)
    y = np.asarray([p.price for p in values], dtype=float)
    if np.ptp(x) <= 0:
        return None
    slope, intercept = np.polyfit(x, y, 1)
    predicted = slope * x + intercept
    sst = float(np.sum((y - float(np.mean(y))) ** 2))
    sse = float(np.sum((y - predicted) ** 2))
    r2 = 1.0 - sse / sst if sst > 1e-12 else 1.0
    mean_error = float(np.mean(np.abs(y - predicted) / np.maximum(np.abs(predicted), 1e-9)))
    return _LineFit(float(slope), float(intercept), float(r2), mean_error)


def _point(df: pd.DataFrame, role: str, pivot: _Pivot) -> PatternPoint:
    return PatternPoint(role, pivot.bar, _ts(df.index[pivot.bar]), round(pivot.price, 4))


def _line(
    df: pd.DataFrame,
    role: str,
    fit: _LineFit,
    start_bar: int,
    end_bar: int,
) -> PatternLine:
    return PatternLine(
        role=role,
        start_bar=int(start_bar),
        end_bar=int(end_bar),
        start_time=_ts(df.index[start_bar]),
        end_time=_ts(df.index[end_bar]),
        start_price=round(fit.at(start_bar), 4),
        end_price=round(fit.at(end_bar), 4),
    )


def _gap_in_range(df: pd.DataFrame, start: int, end: int, timeframe: str) -> bool:
    limit = float(TIMEFRAME_CONFIG[timeframe]["gap_limit"])
    segment_start = max(0, start - 1)
    segment = df.iloc[segment_start : min(len(df), end + 1)]
    moves = _price_jump_series(segment)
    return bool((moves > limit).any())


def _volume_ratio(df: pd.DataFrame, bar: int) -> Optional[float]:
    if "Volume" not in df or bar < 5:
        return None
    vol = df["Volume"].to_numpy(dtype=float)
    base = np.nanmedian(vol[max(0, bar - 20) : bar])
    if not math.isfinite(base) or base <= 0 or not math.isfinite(vol[bar]):
        return None
    return float(vol[bar] / base)


def _boundary_state(
    df: pd.DataFrame,
    fit: _LineFit,
    direction: str,
    atr_now: float,
    invalidation: float,
    apex_bar: Optional[float] = None,
    search_start: int = 1,
    max_break_age: int = 30,
) -> tuple[str, Optional[int], dict[str, Any]]:
    close = df["Close"].to_numpy(dtype=float)
    n = len(close)
    lines = np.asarray([fit.at(i) for i in range(n)], dtype=float)
    now_line = max(lines[-1], 1e-9)
    atr_pct = atr_now / max(close[-1], 1e-9)
    break_buffer = float(np.clip(atr_pct * 0.25, 0.004, 0.015))
    near_band = float(np.clip(atr_pct * 1.25, 0.012, 0.045))
    signed = (close - lines) / np.maximum(np.abs(lines), 1e-9)
    crossed = signed > break_buffer if direction == "bullish" else signed < -break_buffer
    breakout_bar: Optional[int] = None
    for i in range(max(1, int(search_start)), n):
        if crossed[i] and not crossed[i - 1]:
            breakout_bar = i
            break
    if breakout_bar is None and bool(crossed[-1]) and search_start < n:
        hits = np.where(crossed[max(1, int(search_start)) :])[0]
        if hits.size:
            breakout_bar = max(1, int(search_start)) + int(hits[0])

    if direction == "bullish":
        invalid = close[-1] < invalidation
        dist = (now_line - close[-1]) / now_line
    else:
        invalid = close[-1] > invalidation
        dist = (close[-1] - now_line) / now_line
    if invalid:
        return "GEÇERSİZ", breakout_bar, {
            "distance_to_trigger_pct": round(dist * 100, 3),
            "break_buffer_pct": round(break_buffer * 100, 3),
            "near_band_pct": round(near_band * 100, 3),
        }

    if apex_bar is not None and apex_bar < n - 1 and breakout_bar is None:
        return "SÜRESİ_DOLDU", None, {
            "distance_to_trigger_pct": round(dist * 100, 3),
            "apex_bar": round(float(apex_bar), 2),
        }

    if breakout_bar is not None and not bool(crossed[-1]):
        failed_back = (
            signed[-1] < -break_buffer
            if direction == "bullish"
            else signed[-1] > break_buffer
        )
        return ("GEÇERSİZ" if failed_back else "YENİDEN_TEST"), breakout_bar, {
            "distance_to_trigger_pct": round(dist * 100, 3),
            "breakout_age_bars": n - 1 - breakout_bar,
            "breakout_lost": bool(failed_back),
        }

    if breakout_bar is not None and bool(crossed[-1]):
        consecutive = bool(n >= 2 and crossed[-1] and crossed[-2])
        vr = _volume_ratio(df, breakout_bar)
        age = n - 1 - breakout_bar
        if age > max_break_age:
            stage = "UZAMIŞ"
        elif consecutive or (vr is not None and vr >= 1.30):
            stage = "KIRILIM_DOĞRULANDI"
        else:
            stage = "KIRILIM_ADAYI"
        return stage, breakout_bar, {
            "distance_to_trigger_pct": round(dist * 100, 3),
            "breakout_volume_ratio": round(vr, 3) if vr is not None else None,
            "two_close_confirmation": consecutive,
            "breakout_age_bars": age,
        }
    stage = "YAKIN" if dist <= near_band else "OLUŞUYOR"
    return stage, None, {
        "distance_to_trigger_pct": round(dist * 100, 3),
        "near_band_pct": round(near_band * 100, 3),
    }


def _score(parts: dict[str, float]) -> float:
    return round(float(np.clip(sum(parts.values()), 0.0, 100.0)), 1)


def _detect_triangles(
    df: pd.DataFrame,
    pivots: list[_Pivot],
    timeframe: str,
    atr_now: float,
) -> list[PatternCandidate]:
    n = len(df)
    cfg = TIMEFRAME_CONFIG[timeframe]
    highs_all = [p for p in pivots if p.kind == "H"]
    lows_all = [p for p in pivots if p.kind == "L"]
    found: list[PatternCandidate] = []

    for span in cfg["triangle_spans"]:
        start_cut = max(0, n - int(span))
        highs = [p for p in highs_all if p.bar >= start_cut][-6:]
        lows = [p for p in lows_all if p.bar >= start_cut][-6:]
        if len(highs) < 3 or len(lows) < 3:
            continue
        start = min(highs[0].bar, lows[0].bar)
        end = max(highs[-1].bar, lows[-1].bar)
        structure_span = end - start
        if not int(cfg["triangle_min_span"]) <= structure_span <= int(cfg["triangle_max_span"]):
            continue
        if _gap_in_range(df, start, n - 1, timeframe):
            continue

        top = _fit_line(highs)
        bottom = _fit_line(lows)
        if top is None or bottom is None:
            continue
        price_ref = float(np.median(df["Close"].iloc[start : end + 1]))
        top_drift = top.slope * structure_span / max(price_ref, 1e-9)
        bottom_drift = bottom.slope * structure_span / max(price_ref, 1e-9)
        start_gap = top.at(start) - bottom.at(start)
        end_gap = top.at(end) - bottom.at(end)
        if start_gap <= 0 or end_gap <= 0 or end_gap >= start_gap * 0.82:
            continue

        denom = bottom.slope - top.slope
        apex = (top.intercept - bottom.intercept) / denom if abs(denom) > 1e-12 else math.inf
        if not math.isfinite(apex) or apex < end - max(3, structure_span * 0.10):
            continue
        if apex > n + structure_span * 1.25:
            continue

        upper_error_ok = top.mean_error_pct <= 0.040
        lower_error_ok = bottom.mean_error_pct <= 0.040
        historical_end = max(start + 1, n - 3)
        bars = np.arange(start, historical_end)
        close_seg = df["Close"].to_numpy(dtype=float)[start:historical_end]
        upper_seg = np.asarray([top.at(i) for i in bars])
        lower_seg = np.asarray([bottom.at(i) for i in bars])
        tolerance = max(atr_now * 0.75, price_ref * 0.012)
        containment = float(
            np.mean((close_seg <= upper_seg + tolerance) & (close_seg >= lower_seg - tolerance))
        )
        if containment < 0.78:
            continue

        # --- 5 BİRLEŞEN TİP: kenar eğimlerine göre sınıfla — ÖLÇÜM KARAR VERİR (10 Ağu 2026) ---
        # FLAT bandı: |toplam drift| ≤ %1.5 → yatay kenar. Eşiğin üstünde ama r2<0.55 ise
        # eğim saçılmış demektir → yine yatay say. Böylece göz kararı yok, sadece geometri.
        FLAT = 0.015

        def _edge(drift: float, fit: _LineFit) -> str:
            if abs(drift) <= FLAT:
                return "FLAT"            # near-yatay: r2 ilgisiz (düz çizginin r2'si doğal düşer)
            if fit.r2 < 0.55:
                return "NOISE"           # eğimli AMA saçılmış → temiz sınır değil (düz de sayma!)
            return "UP" if drift > 0 else "DOWN"

        if not (upper_error_ok and lower_error_ok):
            continue
        # baskın kenar gerçekten dik + hattı sağlam olmalı — yoksa gürültü/kanal (formasyon değil)
        steep_drift = max(abs(top_drift), abs(bottom_drift))
        steep_fit = top if abs(top_drift) >= abs(bottom_drift) else bottom
        if steep_drift < 0.05 or steep_fit.r2 < 0.55:
            continue
        ts, bs = _edge(top_drift, top), _edge(bottom_drift, bottom)
        if "NOISE" in (ts, bs):
            continue                     # bir kenar temiz hat değil (dik+saçılmış) → formasyon yok
        # (üst_kenar, alt_kenar) -> (isim, aile, yön, kırılım_hattı). yön None → ön-trend belirler.
        _combo = {
            ("FLAT", "UP"): ("YÜKSELEN_ÜÇGEN", "üçgen", "bullish", "top"),
            ("DOWN", "FLAT"): ("ALÇALAN_ÜÇGEN", "üçgen", "bearish", "bottom"),
            ("DOWN", "UP"): ("SİMETRİK_ÜÇGEN", "üçgen", None, None),
            ("UP", "UP"): ("YÜKSELEN_KAMA", "kama", "bearish", "bottom"),
            ("DOWN", "DOWN"): ("ALÇALAN_KAMA", "kama", "bullish", "top"),
        }.get((ts, bs))
        if _combo is None:
            continue  # genişleyen / yatay-yatay / tanımsız kenar kombinasyonu
        name, family, direction, trig = _combo
        # KAMA gürültü kapısı: yumuşak kenar da dikse bu kama değil, düşen/yükselen TREND KANALI.
        # Gerçek kama sıkışan/yavaşlayan konsolidasyondur; en az bir kenarı ≤%5 olmalı.
        if family == "kama" and min(abs(top_drift), abs(bottom_drift)) > 0.05:
            continue
        # simetrik üçgen yönü: ön-trend devamı (ölçülür, göz kararı değil)
        if direction is None:
            _, pt_total = _prior_trend(df["Close"].to_numpy(dtype=float), start, "bullish")
            direction, trig = ("bullish", "top") if pt_total >= 0 else ("bearish", "bottom")

        buf = atr_now * 0.50
        if trig == "top":
            trigger_fit, invalidation = top, bottom.at(n - 1) - buf
        else:
            trigger_fit, invalidation = bottom, top.at(n - 1) + buf

        stage, break_bar, state_metrics = _boundary_state(
            df,
            trigger_fit,
            direction,
            atr_now,
            invalidation,
            apex,
            search_start=end,
            max_break_age=30 if timeframe == "1d" else 45,
        )
        trigger = trigger_fit.at(n - 1)
        height = max(0.0, top.at(end) - bottom.at(end))
        target: Optional[float] = None
        if (
            stage == "OLUŞUYOR"
            and abs(float(state_metrics.get("distance_to_trigger_pct", 0.0))) > 18.0
        ):
            continue
        if stage in ("KIRILIM_DOĞRULANDI", "UZAMIŞ"):
            target = trigger + height if direction == "bullish" else max(0.01, trigger - height)
            if (
                direction == "bullish" and float(df["Close"].iloc[-1]) >= target
            ) or (
                direction == "bearish" and float(df["Close"].iloc[-1]) <= target
            ):
                stage = "TAMAMLANDI"

        points = [
            *[_point(df, "üst_temas", p) for p in highs],
            *[_point(df, "alt_temas", p) for p in lows],
        ]
        lines = [
            _line(df, "üst_sınır", top, start, n - 1),
            _line(df, "alt_sınır", bottom, start, n - 1),
        ]
        quality = _score(
            {
                "zorunlu_geometri": 45,
                "eğim_uyumu": min(15, steep_drift * 140),
                "çizgi_uyumu": max(0, 15 * min(top.r2, bottom.r2)),
                "sıkışma": max(0, 15 * (1 - end_gap / start_gap)),
                "koridor": 10 * containment,
            }
        )
        found.append(
            PatternCandidate(
                pattern=name,
                direction=direction,
                stage=stage,
                quality_score=quality,
                trigger=round(trigger, 4),
                invalidation=round(float(invalidation), 4),
                target=round(target, 4) if target is not None else None,
                start_bar=start,
                end_bar=n - 1,
                start_time=_ts(df.index[start]),
                end_time=_ts(df.index[-1]),
                breakout_bar=break_bar,
                breakout_time=_ts(df.index[break_bar]) if break_bar is not None else None,
                points=points,
                lines=lines,
                checks={
                    "en_az_3_üst_temas": len(highs) >= 3,
                    "en_az_3_alt_temas": len(lows) >= 3,
                    "üst_kenar": ts,
                    "alt_kenar": bs,
                    "çizgiler_yakınsıyor": True,
                    "fiyat_koridorda": True,
                    "veri_kopukluğu_yok": True,
                },
                metrics={
                    "formasyon_ailesi": family,
                    "upper_touch_count": len(highs),
                    "lower_touch_count": len(lows),
                    "upper_r2": round(top.r2, 3),
                    "lower_r2": round(bottom.r2, 3),
                    "upper_total_drift_pct": round(top_drift * 100, 3),
                    "lower_total_drift_pct": round(bottom_drift * 100, 3),
                    "containment_pct": round(containment * 100, 2),
                    "apex_bar": round(float(apex), 2),
                    **state_metrics,
                },
                notes=[
                    "Kalite puanı yalnızca şekil düzenini anlatır; getiri olasılığı değildir.",
                    "İki sınır da karar motorunun kullandığı aynı temas noktalarından üretildi.",
                ],
            )
        )
    return _dedupe(found)


def _pretrend_gain(close: np.ndarray, rim_bar: int, lookback: int = 80) -> float:
    start = max(0, rim_bar - lookback)
    segment = close[start : rim_bar + 1]
    if len(segment) < 20:
        return 0.0
    base = float(np.min(segment))
    return (float(close[rim_bar]) - base) / max(base, 1e-9)


def _cup_roundness(close: np.ndarray, left: int, right: int) -> tuple[float, int, float]:
    segment = close[left : right + 1]
    smooth = pd.Series(segment).ewm(span=5, adjust=False).mean().to_numpy()
    dip_rel = int(np.argmin(smooth))
    dip_bar = left + dip_rel
    x = np.linspace(-1.0, 1.0, len(smooth))
    coef = np.polyfit(x, smooth, 2)
    predicted = np.polyval(coef, x)
    sst = float(np.sum((smooth - np.mean(smooth)) ** 2))
    r2 = 1.0 - float(np.sum((smooth - predicted) ** 2)) / sst if sst > 1e-12 else 0.0
    return float(r2 if coef[0] > 0 else -abs(r2)), dip_bar, float(coef[0])


def _detect_cups(
    df: pd.DataFrame,
    pivots: list[_Pivot],
    timeframe: str,
    atr_now: float,
) -> list[PatternCandidate]:
    cfg = TIMEFRAME_CONFIG[timeframe]
    close = df["Close"].to_numpy(dtype=float)
    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    highs = [p for p in pivots if p.kind == "H"]
    n = len(df)
    found: list[PatternCandidate] = []

    for li in range(len(highs) - 1):
        left = highs[li]
        for ri in range(li + 1, len(highs)):
            right = highs[ri]
            duration = right.bar - left.bar
            if duration < int(cfg["cup_min_bars"]) or duration > int(cfg["cup_max_bars"]):
                continue
            bars_after = n - 1 - right.bar
            if bars_after > int(cfg["handle_max_bars"]) + 10:
                continue
            if _gap_in_range(df, left.bar, n - 1, timeframe):
                continue
            rim = float((left.price + right.price) / 2.0)
            rim_diff = abs(left.price - right.price) / max(rim, 1e-9)
            rim_tolerance = float(np.clip(atr_now / max(rim, 1e-9) * 1.5, 0.035, 0.075))
            if rim_diff > rim_tolerance:
                continue
            interior_high = high[left.bar + 3 : max(left.bar + 4, right.bar - 2)]
            if (
                interior_high.size
                and float(np.max(interior_high)) > max(left.price, right.price) * 1.025
            ):
                continue

            roundness, dip_bar, curvature = _cup_roundness(close, left.bar, right.bar)
            dip_price = float(low[dip_bar])
            depth = (rim - dip_price) / max(rim, 1e-9)
            dip_position = (dip_bar - left.bar) / max(duration, 1)
            if not 0.12 <= depth <= 0.50:
                continue
            if not 0.20 <= dip_position <= 0.80:
                continue
            if roundness < 0.30:
                continue
            bottom_band = dip_price + (rim - dip_price) * 0.18
            bottom_width = int(np.sum(close[left.bar : right.bar + 1] <= bottom_band))
            if bottom_width < max(3, int(duration * 0.06)):
                continue
            pretrend = _pretrend_gain(close, left.bar)
            if pretrend < 0.12:
                continue

            handle_min = int(cfg["handle_min_bars"])
            handle_max = min(
                int(cfg["handle_max_bars"]),
                max(handle_min, int(duration / 3)),
            )
            handle_duration = bars_after
            handle_ok = handle_min <= handle_duration <= handle_max
            handle_low_bar: Optional[int] = None
            handle_depth_ratio: Optional[float] = None
            handle_floor = dip_price + (rim - dip_price) * 0.62
            if handle_duration >= 1:
                handle_slice = low[right.bar + 1 : n]
                if handle_slice.size:
                    handle_low_bar = right.bar + 1 + int(np.argmin(handle_slice))
                    handle_low = float(low[handle_low_bar])
                    handle_depth_ratio = (rim - handle_low) / max(rim - dip_price, 1e-9)
                    handle_ok = (
                        handle_ok
                        and handle_low >= handle_floor
                        and handle_depth_ratio <= 0.40
                    )
                    if (
                        handle_duration >= handle_min
                        and (
                            handle_low < handle_floor
                            or handle_depth_ratio > 0.40
                            or handle_duration > handle_max
                        )
                    ):
                        continue

            trigger_fit = _LineFit(0.0, rim, 1.0, rim_diff / 2)
            invalidation = (
                float(low[handle_low_bar] - atr_now * 0.35)
                if handle_ok and handle_low_bar is not None
                else float(dip_price - atr_now * 0.35)
            )
            if not handle_ok:
                stage = "KULP_BEKLENİYOR"
                break_bar = None
                state_metrics = {
                    "distance_to_trigger_pct": round((rim - close[-1]) / rim * 100, 3)
                }
                pattern_name = "FİNCAN_ADAYI"
            else:
                stage, break_bar, state_metrics = _boundary_state(
                    df,
                    trigger_fit,
                    "bullish",
                    atr_now,
                    invalidation,
                    search_start=right.bar,
                    max_break_age=30 if timeframe == "1d" else 45,
                )
                pattern_name = "FİNCAN_KULP"

            target: Optional[float] = None
            if stage in ("KIRILIM_DOĞRULANDI", "UZAMIŞ"):
                target = rim + (rim - dip_price)
                if float(close[-1]) >= target:
                    stage = "TAMAMLANDI"
            volume_fade: Optional[bool] = None
            if "Volume" in df:
                v = df["Volume"].to_numpy(dtype=float)
                half = left.bar + duration // 2
                v1 = np.nanmedian(v[left.bar:half])
                v2 = np.nanmedian(v[half : right.bar + 1])
                if math.isfinite(v1) and v1 > 0 and math.isfinite(v2):
                    volume_fade = bool(v2 < v1)

            points = [
                _point(df, "sol_dudak", left),
                PatternPoint("fincan_dibi", dip_bar, _ts(df.index[dip_bar]), round(dip_price, 4)),
                _point(df, "sağ_dudak", right),
            ]
            if handle_low_bar is not None:
                points.append(
                    PatternPoint(
                        "kulp_dibi",
                        handle_low_bar,
                        _ts(df.index[handle_low_bar]),
                        round(float(low[handle_low_bar]), 4),
                    )
                )
            lines = [_line(df, "fincan_ağzı", trigger_fit, left.bar, n - 1)]
            quality = _score(
                {
                    "zorunlu_fincan": 40,
                    "kulp": 20 if handle_ok else 0,
                    "dudak_uyumu": max(0, 12 * (1 - rim_diff / max(rim_tolerance, 1e-9))),
                    "yuvarlaklık": max(0, 13 * min(roundness, 1.0)),
                    "ön_trend": min(10, pretrend * 35),
                    "hacim": 5 if volume_fade else 0,
                }
            )
            found.append(
                PatternCandidate(
                    pattern=pattern_name,
                    direction="bullish",
                    stage=stage,
                    quality_score=quality,
                    trigger=round(rim, 4),
                    invalidation=round(invalidation, 4),
                    target=round(target, 4) if target is not None else None,
                    start_bar=left.bar,
                    end_bar=n - 1,
                    start_time=_ts(df.index[left.bar]),
                    end_time=_ts(df.index[-1]),
                    breakout_bar=break_bar,
                    breakout_time=_ts(df.index[break_bar]) if break_bar is not None else None,
                    points=points,
                    lines=lines,
                    checks={
                        "öncesinde_yükseliş": True,
                        "iki_dudak_uyumlu": True,
                        "yuvarlak_dip": True,
                        "v_tipi_değil": True,
                        "kulp_tamam": bool(handle_ok),
                        "veri_kopukluğu_yok": True,
                    },
                    metrics={
                        "cup_duration_bars": duration,
                        "handle_duration_bars": handle_duration,
                        "cup_depth_pct": round(depth * 100, 3),
                        "rim_difference_pct": round(rim_diff * 100, 3),
                        "roundness_r2": round(roundness, 3),
                        "bottom_width_bars": bottom_width,
                        "pretrend_gain_pct": round(pretrend * 100, 3),
                        "handle_depth_of_cup_pct": (
                            round(handle_depth_ratio * 100, 3)
                            if handle_depth_ratio is not None
                            else None
                        ),
                        "volume_fade": volume_fade,
                        **state_metrics,
                    },
                    notes=[
                        (
                            "Kulp zorunlu şartları geçti."
                            if handle_ok
                            else "Fincan geometrisi var; gerçek kulp henüz kanıtlanmadı."
                        ),
                        "Hedef yalnızca doğrulanmış kırılımdan sonra açılır.",
                    ],
                )
            )
    return _dedupe(found)


def _prior_trend(
    close: np.ndarray,
    shoulder_bar: int,
    direction: str,
    lookback: int = 80,
) -> tuple[bool, float]:
    start = max(0, shoulder_bar - lookback)
    segment = close[start : shoulder_bar + 1]
    if len(segment) < 20:
        return False, 0.0
    x = np.arange(len(segment), dtype=float)
    slope = float(np.polyfit(x, segment, 1)[0])
    total = slope * max(1, len(segment) - 1) / max(float(np.mean(segment)), 1e-9)
    return (total < -0.08, total) if direction == "bullish" else (total > 0.08, total)


def _flat_resistance_lines(
    df: pd.DataFrame,
    highs: list[_Pivot],
    timeframe: str,
) -> list[dict[str, Any]]:
    """formasyon_core.find_necklines PORTU (27 Tem 2026) — YATAY ana direnç hatları.
    v1 ilkesi 'önce DÜZ ANA ÇİZGİYİ bul': çok-temaslı yatay dirençleri (band %4.5)
    çıkarır, en taze temastan geriye yürür, fiyat hattı %5'ten fazla deldiği yerde keser.
    Döner: [{level, touches:[_Pivot], fs, ls_t}] — en yüksek geçerli hat başta."""
    band, pierce = 0.045, 0.05
    span_min = 20 if timeframe == "1d" else 35
    touch_fresh = 70 if timeframe == "1d" else 120
    break_recent = 120 if timeframe == "1d" else 200
    high = df["High"].to_numpy(dtype=float)
    close = df["Close"].to_numpy(dtype=float)
    n = len(df)
    if n < span_min + 2 or len(highs) < 2:
        return []
    curr = float(close[-1])
    out: list[dict[str, Any]] = []
    used: set[int] = set()
    for seed in sorted(highs, key=lambda p: -p.price):
        if seed.bar in used:
            continue
        cluster = [p for p in highs
                   if abs(p.price - seed.price) / max(seed.price, 1e-9) <= band]
        if len(cluster) < 2:
            continue
        cluster.sort(key=lambda p: p.bar)
        kept = [cluster[-1]]                                   # çekirdek: en taze temas
        for j in range(len(cluster) - 2, -1, -1):             # geriye yürü
            level_try = min(p.price for p in kept + [cluster[j]])
            seg = high[cluster[j].bar : kept[-1].bar + 1]
            if seg.size and float(seg.max()) > level_try * (1 + pierce):
                break                                         # delme — hattın geçerli kısmı biter
            kept.insert(0, cluster[j])
        if len(kept) < 2:
            continue
        level = min(p.price for p in kept)                    # gövde/alt kenar
        fs, ls_t = kept[0].bar, kept[-1].bar
        if ls_t - fs < span_min:
            continue
        if curr <= level * (1 + pierce):
            if n - 1 - ls_t > touch_fresh:
                continue                                      # kırılmamış → temas taze olmalı
        else:
            above = np.where(close[ls_t:] > level * 1.03)[0]
            if not above.size or (n - 1 - (ls_t + int(above[0]))) > break_recent:
                continue
        for p in kept:
            used.add(p.bar)
        out.append({"level": float(level), "touches": kept, "fs": int(fs), "ls_t": int(ls_t)})
    # yakın seviyeleri dedup (çok-temaslı kazanır), sonra ana hat = en yüksek geçerli hat
    out.sort(key=lambda d: (-len(d["touches"]), -d["ls_t"]))
    dedup: list[dict[str, Any]] = []
    for d in out:
        if not any(abs(d["level"] - e["level"]) / max(e["level"], 1e-9) < 0.015 for e in dedup):
            dedup.append(d)
    dedup.sort(key=lambda d: -d["level"])
    return dedup


def _detect_tobo(
    df: pd.DataFrame,
    pivots: list[_Pivot],
    timeframe: str,
    atr_now: float,
) -> list[PatternCandidate]:
    """TOBO (ters omuz-baş-omuz / bullish inverse) — formasyon_core v1 PORTU (27 Tem 2026).

    İlke: ÖNCE yatay direnç (boyun) hattı, ALTINDA baş = iki boyun teması ARASINDA net en
    derin dip + ön-trend (baş, sol omuz öncesi tepeden ≥%15 aşağıda). Codex v2'nin katı
    5-pivot LHLHL zigzag'ı tek bir spike mumunu 'baş' seçebiliyordu (ARTMS/BORLS vakası);
    bu port çok-temaslı GERÇEK boyun hattına dayanır. Kırılım/aşama/hedef v2'nin ortak
    durum makinesinden (_boundary_state). OBO (bearish top) 27 Tem'de çıkarıldı."""
    close = df["Close"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    n = len(df)
    highs = [p for p in pivots if p.kind == "H"]
    lows = [p for p in pivots if p.kind == "L"]
    found: list[PatternCandidate] = []
    if len(lows) < 2 or len(highs) < 2:
        return found

    pretrend_win, pretrend_min_hist, pretrend_drop = 60, 40, 0.15

    def _pretrend_ok(left_bar: int, head_price: float) -> tuple[bool, float]:
        lo = max(0, left_bar - pretrend_win)
        if left_bar - lo < pretrend_min_hist:
            return True, 0.0                                  # yeterli geçmiş yok → eleme yapma
        prior_max = float(np.nanmax(close[lo : left_bar + 1]))
        if prior_max <= 0:
            return True, 0.0
        drop = 1.0 - head_price / prior_max
        return head_price <= prior_max * (1 - pretrend_drop), drop

    for nl in _flat_resistance_lines(df, highs, timeframe):
        try:
            level = float(nl["level"])
            fs = int(nl["fs"])
            # GERÇEK BOYUN birkaç kez teyit alır. 2 temas = fincan ağzı (sol+sağ dudak),
            # TOBO değil — omuz-baş-omuz ARASINDAKİ tepelerden en az 3 temas ister (10 Ağu 2026).
            if len(nl["touches"]) < 3:
                continue
            if _gap_in_range(df, fs, n - 1, timeframe):
                continue
            touch_bars = [t.bar for t in nl["touches"]]
            lows_after = [p for p in lows if p.bar >= fs]
            if not lows_after:
                continue
            # --- BAŞ: iki boyun teması ARASINDA net en derin dip ---
            head = None
            for cand in sorted(lows_after, key=lambda p: p.price):
                if not (any(tb < cand.bar for tb in touch_bars)
                        and any(tb > cand.bar for tb in touch_bars)):
                    continue                                  # ln...baş...rn yapısal şartı
                others = [p.price for p in lows_after if p.bar != cand.bar]
                if others and cand.price >= min(others) * 0.97:
                    break                                     # baş net derin değil
                head = cand
                break
            if head is None:
                continue
            # --- SOL OMUZ: baştan önceki son dip ---
            pre_lows = [p for p in lows if p.bar < head.bar]
            if not pre_lows:
                continue
            left_sh = pre_lows[-1]
            # BAŞ EN DERİN OLMALI: sol omuz da baştan yüksek (sığ) değilse bu TOBO değil —
            # motor fincanın gerçek dibini "omuz" yapıp sığ bir yeri "baş" seçmesin (10 Ağu 2026).
            if left_sh.price <= head.price * 1.005:
                continue
            # --- ÖN-TREND: baş, sol omuz öncesi tepeden ≥%15 aşağıda ---
            pre_ok, pretrend_drop_val = _pretrend_ok(left_sh.bar, head.price)
            if not pre_ok:
                continue
            # --- SAĞ OMUZ: baştan sonra ilk YÜKSEK dip (varsa; yoksa sağ taraf oluşuyor) ---
            post_lows = [p for p in lows if p.bar > head.bar and p.price > head.price * 1.005]
            right_sh = post_lows[0] if post_lows else None
            # --- BOYUN = düz direnç hattı (yatay _LineFit) ---
            neck_fit = _LineFit(0.0, level, 1.0, 0.0)
            invalidation = head.price - atr_now * 0.40
            min_head = max(0.035, atr_now / max(head.price, 1e-9) * 0.75)
            head_depth = (level - head.price) / max(level, 1e-9)
            if head_depth < min_head:
                continue
            search_from = right_sh.bar if right_sh is not None else head.bar
            stage, break_bar, state_metrics = _boundary_state(
                df, neck_fit, "bullish", atr_now, invalidation,
                search_start=search_from,
                max_break_age=30 if timeframe == "1d" else 45,
            )
            # yapı bozulmuş mu: baştan sonra başın altına yeni dip yapıldıysa TOBO iptal
            check_end = break_bar if break_bar is not None else n
            seg_low = low[head.bar + 1 : check_end]
            if seg_low.size and float(seg_low.min()) < head.price - atr_now * 0.25:
                continue
            if (stage == "OLUŞUYOR"
                    and abs(float(state_metrics.get("distance_to_trigger_pct", 0.0))) > 18.0):
                continue
            trigger = level
            height = level - head.price
            if height <= 0 or height / max(abs(trigger), 1e-9) > 0.60:
                continue
            target: Optional[float] = None
            if stage in ("KIRILIM_DOĞRULANDI", "UZAMIŞ"):
                target = trigger + height
                if float(close[-1]) >= target:
                    stage = "TAMAMLANDI"
            # --- noktalar + çizgi ---
            first_touch = nl["touches"][0]
            last_touch = nl["touches"][-1]
            points = [
                _point(df, "boyun_1", first_touch),
                _point(df, "sol_omuz", left_sh),
                _point(df, "baş", head),
                _point(df, "boyun_2", last_touch),
            ]
            if right_sh is not None:
                points.append(_point(df, "sağ_omuz", right_sh))
            lines = [_line(df, "boyun_çizgisi", neck_fit, fs, n - 1)]
            if right_sh is not None:
                sh_mid = (left_sh.price + right_sh.price) / 2
                shoulder_diff = abs(left_sh.price - right_sh.price) / max(sh_mid, 1e-9)
            else:
                shoulder_diff = 0.0
            quality = _score({
                "boyun_çok_temas": min(30.0, 12.0 + 6.0 * len(nl["touches"])),
                "baş_belirginliği": min(22.0, head_depth * 120.0),
                "ön_trend": min(20.0, pretrend_drop_val * 90.0),
                "omuz_simetrisi": (max(0.0, 14.0 * (1 - shoulder_diff / 0.12))
                                   if right_sh is not None else 6.0),
                "sağ_omuz_var": 8.0 if right_sh is not None else 0.0,
                "yapı_tazeliği": 6.0,
            })
            found.append(PatternCandidate(
                pattern="TOBO",
                direction="bullish",
                stage=stage,
                quality_score=quality,
                trigger=round(trigger, 4),
                invalidation=round(float(invalidation), 4),
                target=round(target, 4) if target is not None else None,
                start_bar=int(left_sh.bar),
                end_bar=n - 1,
                start_time=_ts(df.index[left_sh.bar]),
                end_time=_ts(df.index[-1]),
                breakout_bar=break_bar,
                breakout_time=_ts(df.index[break_bar]) if break_bar is not None else None,
                points=points,
                lines=lines,
                checks={
                    "yatay_boyun_hattı": True,
                    "baş_iki_temas_arası": True,
                    "baş_net_derin": True,
                    "ön_trend_düşüş": bool(pretrend_drop_val >= pretrend_drop),
                    "sağ_omuz_var": right_sh is not None,
                    "veri_kopukluğu_yok": True,
                },
                metrics={
                    "neckline_touch_count": len(nl["touches"]),
                    "neckline_level": round(level, 4),
                    "head_prominence_pct": round(head_depth * 100, 3),
                    "pretrend_drop_pct": round(pretrend_drop_val * 100, 3),
                    "shoulder_difference_pct": round(shoulder_diff * 100, 3),
                    **state_metrics,
                },
                notes=[
                    "Boyun = çok-temaslı yatay direnç (v1 'önce düz ana çizgi' ilkesi).",
                    "Baş, iki boyun teması arasında net en derin dip; ön-trend zorunlu.",
                    "Hedef yalnızca doğrulanmış boyun kırılımından sonra açılır.",
                ],
            ))
        except Exception:
            continue
    return _dedupe(found)


def _dedupe(candidates: list[PatternCandidate]) -> list[PatternCandidate]:
    def family(name: str) -> str:
        return "FİNCAN" if name in ("FİNCAN_ADAYI", "FİNCAN_KULP") else name

    ordered = sorted(
        candidates,
        key=lambda c: (
            family(c.pattern),
            0 if c.pattern == "FİNCAN_KULP" else 1,
            -c.quality_score,
            -c.start_bar,
        ),
    )
    kept: list[PatternCandidate] = []
    for candidate in ordered:
        duplicate = False
        for existing in kept:
            if family(existing.pattern) != family(candidate.pattern):
                continue
            overlap_start = max(existing.start_bar, candidate.start_bar)
            overlap_end = min(existing.end_bar, candidate.end_bar)
            union_start = min(existing.start_bar, candidate.start_bar)
            union_end = max(existing.end_bar, candidate.end_bar)
            overlap = max(0, overlap_end - overlap_start)
            union = max(1, union_end - union_start)
            trigger_gap = abs(existing.trigger - candidate.trigger) / max(
                abs(existing.trigger), 1e-9
            )
            if existing.end_bar == candidate.end_bar or (
                trigger_gap <= 0.05 and overlap / union >= 0.65
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
    return sorted(kept, key=lambda c: (-c.quality_score, -c.start_bar))


def analyze_formations(
    df: pd.DataFrame,
    ticker: Optional[str] = None,
    timeframe: str = "1d",
    max_results: int = 8,
) -> FormationReport:
    """Bir OHLCV tablosunu inceler; canlı sisteme veya diske yazmaz."""
    clean, issues, data_ok = _clean_frame(df, timeframe)
    if not data_ok or clean.empty:
        return FormationReport(
            engine_version=ENGINE_VERSION,
            ticker=ticker,
            timeframe=timeframe,
            row_count=len(clean),
            first_time=_ts(clean.index[0]) if len(clean) else None,
            last_time=_ts(clean.index[-1]) if len(clean) else None,
            data_ok=False,
            data_issues=issues,
            patterns=[],
        )

    pivots = _extract_pivots(clean, timeframe)
    atr_series = _atr(clean)
    atr_now = _finite(atr_series.iloc[-1], float((clean["High"] - clean["Low"]).tail(20).median()))
    if atr_now <= 0:
        issues.append("Güncel oynaklık ölçülemedi; formasyon kararı üretilmedi.")
        return FormationReport(
            ENGINE_VERSION,
            ticker,
            timeframe,
            len(clean),
            _ts(clean.index[0]),
            _ts(clean.index[-1]),
            False,
            issues,
            [],
        )

    candidates = [
        *_detect_triangles(clean, pivots, timeframe, atr_now),
        *_detect_tobo(clean, pivots, timeframe, atr_now),
        *_detect_cups(clean, pivots, timeframe, atr_now),
    ]
    candidates = _dedupe(candidates)
    stage_rank = {
        "KIRILIM_DOĞRULANDI": 0,
        "YENİDEN_TEST": 1,
        "KIRILIM_ADAYI": 2,
        "YAKIN": 3,
        "OLUŞUYOR": 4,
        "KULP_BEKLENİYOR": 5,
        "UZAMIŞ": 6,
        "TAMAMLANDI": 7,
        "GEÇERSİZ": 8,
        "SÜRESİ_DOLDU": 9,
    }
    candidates.sort(
        key=lambda c: (
            stage_rank.get(c.stage, 99),
            -c.quality_score,
            -c.start_bar,
        )
    )
    inactive_stages = {"GEÇERSİZ", "SÜRESİ_DOLDU", "UZAMIŞ", "TAMAMLANDI"}
    active = [candidate for candidate in candidates if candidate.stage not in inactive_stages]
    inactive = [candidate for candidate in candidates if candidate.stage in inactive_stages]
    return FormationReport(
        engine_version=ENGINE_VERSION,
        ticker=ticker,
        timeframe=timeframe,
        row_count=len(clean),
        first_time=_ts(clean.index[0]),
        last_time=_ts(clean.index[-1]),
        data_ok=True,
        data_issues=issues,
        patterns=active[: max(1, int(max_results))],
        inactive_patterns=inactive[: max(1, int(max_results))],
    )


def report_to_dict(report: FormationReport) -> dict[str, Any]:
    return asdict(report)


def render_candidate_chart(
    df: pd.DataFrame,
    candidate: PatternCandidate,
    output_path: Path,
    ticker: Optional[str] = None,
    timeframe: str = "1d",
) -> Path:
    """Motorun karar verdiği aynı nokta ve çizgilerle denetim PNG'si üretir."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    clean, _, ok = _clean_frame(df, timeframe)
    if not ok or clean.empty:
        raise ValueError("Grafik için geçerli veri yok.")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    view_start = max(0, candidate.start_bar - 20)
    view_end = min(len(clean) - 1, candidate.end_bar)
    x = np.arange(view_start, view_end + 1)
    part = clean.iloc[view_start : view_end + 1]

    fig, ax = plt.subplots(figsize=(15, 7), dpi=150)
    fig.patch.set_facecolor("#07111f")
    ax.set_facecolor("#07111f")
    up = part["Close"].to_numpy() >= part["Open"].to_numpy()
    colors = np.where(up, "#14b8a6", "#ef5350")
    ax.vlines(x, part["Low"], part["High"], color=colors, linewidth=0.7, alpha=0.9)
    body_floor = max(float(part["Close"].median()) * 0.001, 1e-6)
    for bar, open_v, close_v, color in zip(x, part["Open"], part["Close"], colors):
        bottom = min(float(open_v), float(close_v))
        height = max(abs(float(close_v) - float(open_v)), body_floor)
        ax.add_patch(
            Rectangle(
                (bar - 0.32, bottom),
                0.64,
                height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.5,
            )
        )

    line_colors = {
        "üst_sınır": "#fb923c",
        "alt_sınır": "#38bdf8",
        "boyun_çizgisi": "#f59e0b",
        "fincan_ağzı": "#f59e0b",
    }
    for line in candidate.lines:
        ax.plot(
            [line.start_bar, line.end_bar],
            [line.start_price, line.end_price],
            linestyle="--",
            linewidth=2.0,
            color=line_colors.get(line.role, "#f59e0b"),
            label=line.role,
            zorder=4,
        )

    role_colors = {
        "baş": "#f59e0b",
        "fincan_dibi": "#22d3ee",
        "kulp_dibi": "#a78bfa",
        "sol_omuz": "#38bdf8",
        "sağ_omuz": "#38bdf8",
        "sol_dudak": "#a78bfa",
        "sağ_dudak": "#a78bfa",
        "üst_temas": "#fb923c",
        "alt_temas": "#38bdf8",
        "boyun_1": "#f59e0b",
        "boyun_2": "#f59e0b",
    }
    for point in candidate.points:
        color = role_colors.get(point.role, "#f8fafc")
        marker = "v" if point.role in {"üst_temas", "boyun_1", "boyun_2"} else "^"
        ax.scatter(point.bar, point.price, s=48, marker=marker, color=color, zorder=6)
        ax.annotate(
            point.role,
            (point.bar, point.price),
            xytext=(0, 9 if marker == "^" else -14),
            textcoords="offset points",
            color=color,
            fontsize=7,
            ha="center",
            va="bottom" if marker == "^" else "top",
        )

    ax.axhline(
        candidate.invalidation,
        color="#ef4444",
        linestyle=":",
        linewidth=1.2,
        label=f"geçersizlik {candidate.invalidation:.2f}",
    )
    if candidate.target is not None:
        ax.axhline(
            candidate.target,
            color="#22c55e",
            linestyle=":",
            linewidth=1.2,
            label=f"hedef {candidate.target:.2f}",
        )
    current = float(clean["Close"].iloc[-1])
    ax.axhline(current, color="#94a3b8", linestyle=":", linewidth=0.8, alpha=0.65)
    ax.set_xlim(view_start - 2, view_end + 2)
    tick_count = min(9, len(x))
    tick_bars = np.linspace(view_start, view_end, tick_count, dtype=int)
    ax.set_xticks(tick_bars)
    ax.set_xticklabels(
        [pd.Timestamp(clean.index[i]).strftime("%d %b %y") for i in tick_bars],
        rotation=25,
        ha="right",
        color="#94a3b8",
        fontsize=8,
    )
    ax.tick_params(axis="y", colors="#94a3b8", labelsize=8)
    ax.grid(axis="y", color="#334155", alpha=0.35, linewidth=0.7)
    for spine in ax.spines.values():
        spine.set_color("#334155")
    title_ticker = ticker or "?"
    ax.set_title(
        f"{title_ticker} — {candidate.pattern} — {candidate.stage} — "
        f"şekil kalitesi {candidate.quality_score:.1f}/100",
        color="#e2e8f0",
        loc="left",
        fontsize=12,
        fontweight="bold",
    )
    handles, labels = ax.get_legend_handles_labels()
    unique = dict(zip(labels, handles))
    if unique:
        legend = ax.legend(
            unique.values(),
            unique.keys(),
            loc="upper left",
            frameon=True,
            fontsize=8,
        )
        legend.get_frame().set_facecolor("#0f172a")
        legend.get_frame().set_edgecolor("#334155")
        for text_item in legend.get_texts():
            text_item.set_color("#cbd5e1")
    fig.tight_layout()
    fig.savefig(output_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return output_path


def render_report_charts(
    df: pd.DataFrame,
    report: FormationReport,
    output_dir: Path,
) -> list[Path]:
    output_dir = Path(output_dir)
    paths: list[Path] = []
    ticker = (report.ticker or "UNKNOWN").replace("/", "_")
    for rank, candidate in enumerate(report.patterns, start=1):
        safe_pattern = (
            candidate.pattern.replace("Ü", "U")
            .replace("Ç", "C")
            .replace("İ", "I")
            .replace("Ş", "S")
            .replace("Ğ", "G")
            .replace("Ö", "O")
        )
        path = output_dir / f"{ticker}_{report.timeframe}_{rank:02d}_{safe_pattern}.png"
        paths.append(
            render_candidate_chart(
                df,
                candidate,
                path,
                ticker=report.ticker,
                timeframe=report.timeframe,
            )
        )
    return paths


def load_local_timeframes(
    root: Path,
    ticker: str,
    timeframe: str = "both",
) -> dict[str, FormationReport]:
    symbol = ticker.upper().replace(".IS", "")
    jobs: list[tuple[str, Path]] = []
    if timeframe in ("1d", "both"):
        jobs.append(("1d", root / "veriler" / f"{symbol}.IS_1d.parquet"))
    if timeframe in ("4h", "both"):
        jobs.append(("4h", root / "veriler_4s" / f"{symbol}.IS_4h.parquet"))
    out: dict[str, FormationReport] = {}
    for tf, path in jobs:
        if not path.exists():
            out[tf] = FormationReport(
                ENGINE_VERSION,
                symbol,
                tf,
                0,
                None,
                None,
                False,
                [f"Dosya bulunamadı: {path.name}"],
                [],
            )
            continue
        frame = pd.read_parquet(path)
        out[tf] = analyze_formations(frame, ticker=symbol, timeframe=tf)
    return out


def _run_universe(
    root: Path,
    timeframe: str,
    limit: Optional[int],
    chart_dir: Optional[Path] = None,
) -> dict[str, Any]:
    folder = root / ("veriler" if timeframe == "1d" else "veriler_4s")
    suffix = "_1d.parquet" if timeframe == "1d" else "_4h.parquet"
    files = sorted(folder.glob(f"*{suffix}"))
    if limit is not None:
        files = files[: max(0, int(limit))]
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    chart_paths: list[str] = []
    for path in files:
        ticker = path.name[: -len(suffix)].replace(".IS", "")
        try:
            frame = pd.read_parquet(path)
            report = analyze_formations(frame, ticker=ticker, timeframe=timeframe)
            for pattern in report.patterns:
                rows.append(
                    {
                        "ticker": ticker,
                        "timeframe": timeframe,
                        "pattern": pattern.pattern,
                        "stage": pattern.stage,
                        "quality_score": pattern.quality_score,
                        "trigger": pattern.trigger,
                        "invalidation": pattern.invalidation,
                        "target": pattern.target,
                        "last_time": report.last_time,
                    }
                )
            if chart_dir is not None and report.patterns:
                chart_paths.extend(
                    str(chart_path)
                    for chart_path in render_report_charts(frame, report, chart_dir)
                )
            if not report.data_ok:
                failures.append({"ticker": ticker, "reason": "; ".join(report.data_issues)})
        except Exception as exc:
            failures.append({"ticker": ticker, "reason": f"{type(exc).__name__}: {exc}"})
    pattern_counts: dict[str, int] = {}
    stage_counts: dict[str, int] = {}
    for row in rows:
        pattern_counts[row["pattern"]] = pattern_counts.get(row["pattern"], 0) + 1
        stage_counts[row["stage"]] = stage_counts.get(row["stage"], 0) + 1
    return {
        "engine_version": ENGINE_VERSION,
        "timeframe": timeframe,
        "files_scanned": len(files),
        "detections": len(rows),
        "pattern_counts": pattern_counts,
        "stage_counts": stage_counts,
        "data_failures": failures,
        "rows": rows,
        "chart_paths": chart_paths,
    }


def _synthetic_frame(kind: str, n: int = 160) -> pd.DataFrame:
    rng = np.random.default_rng(20260725)
    x = np.arange(n, dtype=float)
    base = np.full(n, 100.0)
    if kind == "ascending_triangle":
        lower = 82 + 0.12 * x
        upper = np.full(n, 104.0)
        wave = (np.sin(x / 4.8) + 1) / 2
        close = lower + (upper - lower) * wave
    elif kind == "descending_triangle":
        upper = 122 - 0.13 * x
        lower = np.full(n, 100.0)
        wave = (np.sin(x / 4.8) + 1) / 2
        close = lower + (upper - lower) * wave
    elif kind == "tobo":
        # gerçekçi ters O-B-O: baş EN DERİN + boyun (116) 3 kez teyit edilir
        anchors_x = np.asarray([0, 18, 38, 58, 78, 98, 118, 138, n - 1], dtype=float)
        anchors_y = np.asarray([120, 100, 116, 88, 116, 100, 116, 100, 125], dtype=float)
        close = np.interp(x, anchors_x, anchors_y)
    elif kind == "cup_handle":
        left_rim, right_rim = 40, 135
        close = np.empty(n, dtype=float)
        close[: left_rim + 1] = np.linspace(80, 110, left_rim + 1)
        cup_x = np.linspace(-1.0, 1.0, right_rim - left_rim + 1)
        close[left_rim : right_rim + 1] = 84 + 26 * (cup_x**2)
        handle_x = np.arange(n - right_rim - 1)
        handle = np.interp(
            handle_x,
            [0, max(1, len(handle_x) // 3), max(2, len(handle_x) - 1)],
            [109, 103, 108],
        )
        close[right_rim + 1 :] = handle
    else:
        close = base + rng.normal(0, 0.5, n).cumsum()
    close += rng.normal(0, 0.12, n)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.35
    low = np.minimum(open_, close) - 0.35
    volume = np.full(n, 1_000_000.0)
    index = pd.date_range("2026-01-01", periods=n, freq="4h")
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=index,
    )


def _self_test() -> dict[str, Any]:
    results: dict[str, Any] = {}
    for kind, expected in (
        ("ascending_triangle", "YÜKSELEN_ÜÇGEN"),
        ("descending_triangle", "ALÇALAN_ÜÇGEN"),
        ("tobo", "TOBO"),
        ("cup_handle", "FİNCAN_KULP"),
        ("noise", None),
    ):
        report = analyze_formations(_synthetic_frame(kind), ticker=kind, timeframe="4h")
        names = [p.pattern for p in report.patterns]
        passed = expected in names if expected else not any("ÜÇGEN" in name for name in names)
        results[kind] = {"passed": passed, "patterns": names}
    results["all_passed"] = all(item["passed"] for key, item in results.items() if key != "all_passed")
    return results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Bağımsız Formasyon V2 araştırma motoru")
    parser.add_argument("--ticker", help="Örnek: EREGL veya XU100")
    parser.add_argument("--timeframe", choices=("1d", "4h", "both"), default="both")
    parser.add_argument("--universe", action="store_true", help="Yerel parquet evrenini tara")
    parser.add_argument("--limit", type=int, default=None, help="Evren taramasında dosya sınırı")
    parser.add_argument("--output", type=Path, default=None, help="JSON çıktısını bu dosyaya yaz")
    parser.add_argument(
        "--chart-dir",
        type=Path,
        default=None,
        help="Aktif adayları motorun kendi karar çizgileriyle PNG olarak çiz",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent

    if args.self_test:
        payload: Any = _self_test()
    elif args.universe:
        if args.timeframe == "both":
            payload = {
                "1d": _run_universe(root, "1d", args.limit, args.chart_dir),
                "4h": _run_universe(root, "4h", args.limit, args.chart_dir),
            }
        else:
            payload = _run_universe(root, args.timeframe, args.limit, args.chart_dir)
    elif args.ticker:
        reports = load_local_timeframes(root, args.ticker, args.timeframe)
        payload = {tf: report_to_dict(report) for tf, report in reports.items()}
        if args.chart_dir:
            chart_paths: list[str] = []
            symbol = args.ticker.upper().replace(".IS", "")
            for tf, report in reports.items():
                folder = "veriler" if tf == "1d" else "veriler_4s"
                suffix = "_1d.parquet" if tf == "1d" else "_4h.parquet"
                path = root / folder / f"{symbol}.IS{suffix}"
                if path.exists():
                    chart_paths.extend(
                        str(p)
                        for p in render_report_charts(
                            pd.read_parquet(path),
                            report,
                            args.chart_dir,
                        )
                    )
            payload["_chart_paths"] = chart_paths
    else:
        parser.error("--ticker, --universe veya --self-test seçeneklerinden biri gerekli.")
        return 2

    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
