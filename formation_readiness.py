"""TOBO ve fincan-kulp için bildirim olgunluğu araştırma katmanı.

Bu modül formasyonun yalnız geometrisini değil, fiyatın bildirim yapılabilecek
evreye gelip gelmediğini ayırır. Canlı uygulamaya bağlı değildir ve veri çekmez.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd

from neckline_core import Pivot, _atr, _clean_frame, _extract_pivots


READINESS_VERSION = "0.3.0-research"


# TOBO boyun çizgisi, iki tepeyi keyfî biçimde birleştiren eğimli bir doğru
# değildir. Önce iki tepenin aynı yatay bölgeyi temsil edip etmediği ATR ile
# sınanır; geçerse çizgi iki tepenin ortalamasından yatay çizilir. Böylece
# üçgen/kama çizgisi TOBO diye isimlendirilemez.
TOBO_MAX_NECKLINE_GAP_ATR = 1.25
TOBO_MAX_NECKLINE_DRIFT_ATR_PER_10_BARS = 0.22
CUP_MAX_RIM_GAP_ATR = 1.50
CUP_MAX_RIM_DRIFT_ATR_PER_10_BARS = 0.25
TRIPLE_TOP_MAX_PEAK_GAP_ATR = 1.50
TRIPLE_TOP_MAX_PEAK_DRIFT_ATR_PER_10_BARS = 0.25
SEQUENCE_LOOKBACK_PIVOTS = 24
MAX_STRUCTURE_BARS = 220


@dataclass(frozen=True)
class FormationReadiness:
    pattern: str
    phase: str
    alert: str
    score: float
    neckline: float
    price: float
    distance_to_neckline_pct: float
    recovery_atr: float
    neckline_peak_gap_atr: float
    neckline_drift_atr_per_10_bars: float
    structure_start: str
    structure_end: str
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # Canlı katman bu anahtarı saklayarak aynı yapı için her gün aynı uyarıyı
        # tekrar göndermez; yalnız evre değişince yeni bildirim oluşur.
        payload["notification_key"] = f"{self.pattern}:{self.structure_start[:10]}:{self.structure_end[:10]}:{self.alert}"
        return payload


def _atr_at(atr: pd.Series, bar: int, fallback: float) -> float:
    if 0 <= bar < len(atr):
        value = float(atr.iloc[bar])
        if np.isfinite(value) and value > 0:
            return value
    return float(fallback)


def _horizontal_tobo_neckline(
    peak_one: Pivot,
    peak_two: Pivot,
    atr: pd.Series,
    atr_fallback: float,
    max_gap_atr: float = TOBO_MAX_NECKLINE_GAP_ATR,
    max_drift_atr_per_10_bars: float = TOBO_MAX_NECKLINE_DRIFT_ATR_PER_10_BARS,
) -> tuple[float, float, float] | None:
    """TOBO için yalnız yatay/çok hafif eğimli boyun bölgesini kabul eder."""
    span = peak_two.bar - peak_one.bar
    if span <= 0:
        return None
    reference_atr = float(np.median([
        _atr_at(atr, peak_one.bar, atr_fallback),
        _atr_at(atr, peak_two.bar, atr_fallback),
    ]))
    if not np.isfinite(reference_atr) or reference_atr <= 0:
        return None
    gap_atr = abs(peak_two.price - peak_one.price) / reference_atr
    drift_atr_per_10_bars = gap_atr * 10.0 / span
    if (
        gap_atr > max_gap_atr
        or drift_atr_per_10_bars > max_drift_atr_per_10_bars
    ):
        return None
    return float((peak_one.price + peak_two.price) / 2.0), float(gap_atr), float(drift_atr_per_10_bars)


def _latest(candidates: Iterable[FormationReadiness]) -> FormationReadiness | None:
    values = list(candidates)
    if not values:
        return None
    return max(values, key=lambda item: (item.structure_end, item.score))


def _pattern_sequences(pivots: list[Pivot], kinds: tuple[str, ...]) -> Iterable[tuple[Pivot, ...]]:
    """Ara küçük pivotları atlayarak büyük dönüş sırasını üretir."""
    values = pivots[-SEQUENCE_LOOKBACK_PIVOTS:]

    def walk(start_index: int, kind_index: int, selected: tuple[Pivot, ...]):
        if kind_index == len(kinds):
            yield selected
            return
        required = kinds[kind_index]
        for index in range(start_index, len(values)):
            pivot = values[index]
            if pivot.kind != required:
                continue
            if selected and pivot.bar - selected[0].bar > MAX_STRUCTURE_BARS:
                break
            yield from walk(index + 1, kind_index + 1, (*selected, pivot))

    yield from walk(0, 0, ())


def _ready_score(
    symmetry: float,
    recovery_atr: float,
    distance_atr: float,
    depth_atr: float,
) -> float:
    geometry = 30.0 * max(0.0, 1.0 - symmetry / 0.10)
    recovery = min(28.0, max(0.0, recovery_atr) * 14.0)
    proximity = 28.0 * max(0.0, 1.0 - max(0.0, distance_atr) / 5.0)
    depth = min(14.0, max(0.0, depth_atr) * 2.5)
    return round(min(100.0, geometry + recovery + proximity + depth), 1)


def _tobo_candidate(
    pivots: list[Pivot],
    close: float,
    atr: pd.Series,
    atr_now: float,
    end_bar: int,
) -> FormationReadiness | None:
    candidates: list[FormationReadiness] = []
    tolerance = max(0.035, 2.5 * atr_now / close)
    depth_floor = max(0.010, 0.5 * atr_now / close)
    for left, peak_one, head, peak_two, right in _pattern_sequences(pivots, ("L", "H", "L", "H", "L")):
        shoulder_avg = (left.price + right.price) / 2.0
        shoulder_gap = abs(left.price - right.price) / shoulder_avg
        head_depth = (min(left.price, right.price) - head.price) / shoulder_avg
        # Gerçek TOBO: üç dip ve aradaki iki tepede zaman/derinlik net olmalı.
        if (shoulder_gap > tolerance or head_depth < max(depth_floor, 1.25 * atr_now / close)
            or min(peak_one.bar-left.bar, head.bar-peak_one.bar, peak_two.bar-head.bar, right.bar-peak_two.bar) < 5
            or right.bar < end_bar - 35):
            continue
        neckline_check = _horizontal_tobo_neckline(peak_one, peak_two, atr, atr_now)
        if neckline_check is None:
            continue
        neckline, neckline_peak_gap_atr, neckline_drift_atr_per_10_bars = neckline_check
        if neckline - head.price < 1.10 * _atr_at(atr, head.bar, atr_now):
            continue
        distance_pct = (neckline - close) / neckline * 100.0
        recovery_atr = (close - right.price) / atr_now
        distance_atr = (neckline - close) / atr_now
        depth_atr = (shoulder_avg - head.price) / atr_now
        broken = close > neckline * 1.006
        if broken:
            phase, alert = "BOYUN_KIRILIMI", "ONAY_BEKLE"
            notes = (
                "İki tepe ATR ile yatay bölge olarak doğrulandı; boyun çizgisi bu iki tepenin ortalamasından çizildi.",
                "Boyun çizgisi aşıldı; kapanış ve retest henüz teyit edilmeli.",
            )
        elif recovery_atr >= 0.60 and distance_atr <= 5.0:
            phase, alert = "IKINCI_OMUZ_DONUSU", "HAZIR"
            notes = (
                "İki tepe ATR ile yatay bölge olarak doğrulandı; boyun çizgisi bu iki tepenin ortalamasından çizildi.",
                "İkinci omuzdan yukarı tepki başladı ve boyun çizgisi artık makul mesafede.",
                "Boyun çizgisi henüz ayrıca aşılmalı; bu teyit değil, hazırlık bildirimi.",
            )
        elif recovery_atr >= 0.60:
            phase, alert = "IKINCI_OMUZ_DONUSU", "ERKEN_BILDIRIM"
            notes = (
                "İki tepe ATR ile yatay bölge olarak doğrulandı; boyun çizgisi bu iki tepenin ortalamasından çizildi.",
                "İkinci omuzdan yukarı tepki başladı, fakat boyun çizgisine hâlâ uzak.",
                "İzleme bildirimi verilir; işlem teyidi veya yakın kırılım bildirimi verilmez.",
            )
        else:
            phase, alert = "IKINCI_OMUZ_OLUSUYOR", "IZLE"
            notes = (
                "İki tepe ATR ile yatay bölge olarak doğrulandı; boyun çizgisi bu iki tepenin ortalamasından çizildi.",
                "Başın dibinde değil, ikinci omuzun tamamlanması bekleniyor.",
            )
        candidates.append(
            FormationReadiness(
                pattern="TOBO",
                phase=phase,
                alert=alert,
                score=_ready_score(shoulder_gap, recovery_atr, distance_atr, depth_atr),
                neckline=round(neckline, 6),
                price=round(close, 6),
                distance_to_neckline_pct=round(distance_pct, 3),
                recovery_atr=round(recovery_atr, 3),
                neckline_peak_gap_atr=round(neckline_peak_gap_atr, 3),
                neckline_drift_atr_per_10_bars=round(neckline_drift_atr_per_10_bars, 3),
                structure_start=left.time,
                structure_end=right.time,
                notes=notes,
            )
        )
    return _latest(candidates)


def _cup_handle_candidate(
    pivots: list[Pivot],
    close: float,
    atr: pd.Series,
    atr_now: float,
    end_bar: int,
) -> FormationReadiness | None:
    candidates: list[FormationReadiness] = []
    rim_tolerance = max(0.050, 3.0 * atr_now / close)
    for left_rim, cup_bottom, right_rim, handle_low in _pattern_sequences(pivots, ("H", "L", "H", "L")):
        rim_avg = (left_rim.price + right_rim.price) / 2.0
        rim_gap = abs(left_rim.price - right_rim.price) / rim_avg
        cup_depth = right_rim.price - cup_bottom.price
        handle_depth = right_rim.price - handle_low.price
        rim_check = _horizontal_tobo_neckline(
            left_rim,
            right_rim,
            atr,
            atr_now,
            CUP_MAX_RIM_GAP_ATR,
            CUP_MAX_RIM_DRIFT_ATR_PER_10_BARS,
        )
        if (
            rim_gap > rim_tolerance
            or rim_check is None
            or cup_depth < 1.5 * atr_now
            or handle_depth <= 0
            or handle_depth > cup_depth * 0.65
            or min(cup_bottom.bar-left_rim.bar, right_rim.bar-cup_bottom.bar) < 8
            or handle_low.bar-right_rim.bar < 3
            or right_rim.price < cup_bottom.price + 2.0 * atr_now
            or handle_low.bar < end_bar - 35
        ):
            continue
        neckline, neckline_peak_gap_atr, neckline_drift_atr_per_10_bars = rim_check
        distance_pct = (neckline - close) / neckline * 100.0
        recovery_atr = (close - handle_low.price) / atr_now
        distance_atr = (neckline - close) / atr_now
        depth_atr = cup_depth / atr_now
        broken = close > neckline * 1.006
        if broken:
            phase, alert = "KULP_KIRILIMI", "ONAY_BEKLE"
            notes = ("Kulp üstü/rim seviyesi aşıldı; kapanış ve retest teyidi gerekli.",)
        elif recovery_atr >= 0.60 and distance_atr <= 5.0:
            phase, alert = "KULP_DONUSU", "HAZIR"
            notes = (
                "Kulp dibinden yukarı hareket başladı ve rim seviyesi artık makul mesafede.",
                "Rim seviyesi henüz ayrıca aşılmalı; bu teyit değil, hazırlık bildirimi.",
            )
        elif recovery_atr >= 0.60:
            phase, alert = "KULP_DONUSU", "ERKEN_BILDIRIM"
            notes = (
                "Kulp dibinden yukarı hareket başladı, fakat rim seviyesine hâlâ uzak.",
                "İzleme bildirimi verilir; işlem teyidi veya yakın kırılım bildirimi verilmez.",
            )
        else:
            phase, alert = "KULP_OLUSUYOR", "IZLE"
            notes = ("Fincan tamamlanmış olabilir; kulpta yukarı dönüş henüz başlamadı.",)
        candidates.append(
            FormationReadiness(
                pattern="FINCAN_KULP",
                phase=phase,
                alert=alert,
                score=_ready_score(rim_gap, recovery_atr, distance_atr, depth_atr),
                neckline=round(neckline, 6),
                price=round(close, 6),
                distance_to_neckline_pct=round(distance_pct, 3),
                recovery_atr=round(recovery_atr, 3),
                neckline_peak_gap_atr=round(neckline_peak_gap_atr, 3),
                neckline_drift_atr_per_10_bars=round(neckline_drift_atr_per_10_bars, 3),
                structure_start=left_rim.time,
                structure_end=handle_low.time,
                notes=notes,
            )
        )
    return _latest(candidates)


def _triple_top_candidate(
    pivots: list[Pivot],
    close: float,
    atr: pd.Series,
    atr_now: float,
    end_bar: int,
) -> FormationReadiness | None:
    """Yerel üçlü tepeyi, geçmişteki ilgisiz yükseklerle karıştırmadan izler."""
    candidates: list[FormationReadiness] = []
    for peak_one, trough_one, peak_two, trough_two, peak_three in _pattern_sequences(pivots, ("H", "L", "H", "L", "H")):
        if (
            min(
                trough_one.bar - peak_one.bar,
                peak_two.bar - trough_one.bar,
                trough_two.bar - peak_two.bar,
                peak_three.bar - trough_two.bar,
            ) < 5
            or peak_three.bar < end_bar - 35
        ):
            continue
        reference_atr = float(np.median([
            _atr_at(atr, peak_one.bar, atr_now),
            _atr_at(atr, peak_two.bar, atr_now),
            _atr_at(atr, peak_three.bar, atr_now),
        ]))
        if not np.isfinite(reference_atr) or reference_atr <= 0:
            continue
        peak_gap_atr = (max(peak_one.price, peak_two.price, peak_three.price) - min(peak_one.price, peak_two.price, peak_three.price)) / reference_atr
        peak_drift_atr_per_10 = peak_gap_atr * 10.0 / max(1, peak_three.bar - peak_one.bar)
        if (
            peak_gap_atr > TRIPLE_TOP_MAX_PEAK_GAP_ATR
            or peak_drift_atr_per_10 > TRIPLE_TOP_MAX_PEAK_DRIFT_ATR_PER_10_BARS
        ):
            continue
        top_level = float(np.mean([peak_one.price, peak_two.price, peak_three.price]))
        local_support = float(np.mean([trough_one.price, trough_two.price]))
        if top_level - local_support < 1.50 * reference_atr:
            continue
        distance_pct = (top_level - close) / top_level * 100.0
        distance_atr = (top_level - close) / atr_now
        recovery_atr = (close - trough_two.price) / atr_now
        if close >= top_level * 1.006:
            phase, alert = "UST_BANT_ASILDI", "ONAY_BEKLE"
        elif distance_atr <= 5.0:
            phase, alert = "UCLU_TEPE_BOLGESI", "IZLE"
        else:
            continue
        notes = (
            f"Yerel üçlü tepe bandı {top_level:.4f}; çizgi yalnız bu üç tepe kümesinden üretildi.",
            f"Alt teyit desteği {local_support:.4f}; bu seviye ayrı kırılım/retest evresidir.",
        )
        candidates.append(
            FormationReadiness(
                pattern="UCLU_TEPE",
                phase=phase,
                alert=alert,
                score=_ready_score(peak_gap_atr / 20.0, recovery_atr, distance_atr, (top_level - local_support) / atr_now),
                neckline=round(top_level, 6),
                price=round(close, 6),
                distance_to_neckline_pct=round(distance_pct, 3),
                recovery_atr=round(recovery_atr, 3),
                neckline_peak_gap_atr=round(peak_gap_atr, 3),
                neckline_drift_atr_per_10_bars=round(peak_drift_atr_per_10, 3),
                structure_start=peak_one.time,
                structure_end=peak_three.time,
                notes=notes,
            )
        )
    return _latest(candidates)


def _range_candidate(
    pivots: list[Pivot],
    close: float,
    atr: pd.Series,
    atr_now: float,
    end_bar: int,
) -> FormationReadiness | None:
    """Yatay üst ve alt bant birlikte varsa yapıyı range olarak adlandırır."""
    candidates: list[FormationReadiness] = []
    for top_one, low_one, top_two, low_two, top_three in _pattern_sequences(pivots, ("H", "L", "H", "L", "H")):
        if (
            min(
                low_one.bar - top_one.bar,
                top_two.bar - low_one.bar,
                low_two.bar - top_two.bar,
                top_three.bar - low_two.bar,
            ) < 5
            or top_three.bar < end_bar - 35
        ):
            continue
        top_pair = _horizontal_tobo_neckline(
            top_one,
            top_three,
            atr,
            atr_now,
            TRIPLE_TOP_MAX_PEAK_GAP_ATR,
            TRIPLE_TOP_MAX_PEAK_DRIFT_ATR_PER_10_BARS,
        )
        bottom_pair = _horizontal_tobo_neckline(low_one, low_two, atr, atr_now, 2.00, 0.30)
        if top_pair is None or bottom_pair is None:
            continue
        top_level = float(np.mean([top_one.price, top_two.price, top_three.price]))
        bottom_level = float(np.mean([low_one.price, low_two.price]))
        if top_level - bottom_level < 2.0 * atr_now:
            continue
        distance_pct = (top_level - close) / top_level * 100.0
        distance_atr = (top_level - close) / atr_now
        recovery_atr = (close - bottom_level) / atr_now
        if close >= top_level * 1.006:
            phase, alert = "UST_BANT_KIRILIMI", "ONAY_BEKLE"
        elif 0.0 <= distance_atr <= 5.0:
            phase, alert = "UST_BANDA_YAKIN", "IZLE"
        else:
            continue
        _, top_gap_atr, top_drift_atr_per_10_bars = top_pair
        notes = (
            f"Range üst bandı {top_level:.4f}; alt bandı {bottom_level:.4f}.",
            "Üst bant kırılımı ile teyit/retest evresi ayrıdır.",
        )
        candidates.append(
            FormationReadiness(
                pattern="RANGE",
                phase=phase,
                alert=alert,
                score=_ready_score(top_gap_atr / 20.0, recovery_atr, distance_atr, (top_level - bottom_level) / atr_now),
                neckline=round(top_level, 6),
                price=round(close, 6),
                distance_to_neckline_pct=round(distance_pct, 3),
                recovery_atr=round(recovery_atr, 3),
                neckline_peak_gap_atr=round(top_gap_atr, 3),
                neckline_drift_atr_per_10_bars=round(top_drift_atr_per_10_bars, 3),
                structure_start=top_one.time,
                structure_end=top_three.time,
                notes=notes,
            )
        )
    return _latest(candidates)


def analyze_formation_readiness(df: pd.DataFrame) -> list[FormationReadiness]:
    """Yalnız son mumla bilinen TOBO ve fincan-kulp bildirim evrelerini döndürür."""
    frame, _, valid = _clean_frame(df)
    if not valid or len(frame) < 70:
        return []
    atr = _atr(frame)
    atr_now = float(atr.iloc[-1])
    if not np.isfinite(atr_now) or atr_now <= 0:
        return []
    pivots = _extract_pivots(frame, atr_now)
    if len(pivots) < 4:
        return []
    close = float(frame["Close"].iloc[-1])
    end_bar = len(frame) - 1
    results = [
        _tobo_candidate(pivots, close, atr, atr_now, end_bar),
        _cup_handle_candidate(pivots, close, atr, atr_now, end_bar),
        _triple_top_candidate(pivots, close, atr, atr_now, end_bar),
        _range_candidate(pivots, close, atr, atr_now, end_bar),
    ]
    return sorted((item for item in results if item is not None), key=lambda item: item.score, reverse=True)


def self_test() -> dict[str, Any]:
    """Saf evre puanının kulp/omuz dönüşünde bildirim verdiğini sınar."""
    low_score = _ready_score(0.03, 0.10, 4.5, 4.0)
    ready_score = _ready_score(0.03, 1.20, 1.5, 4.0)
    atr = pd.Series([1.0] * 50)
    flat = _horizontal_tobo_neckline(Pivot(5, "2025-01-01", 100.0, "H"), Pivot(40, "2025-02-01", 100.7, "H"), atr, 1.0)
    steep = _horizontal_tobo_neckline(Pivot(5, "2025-01-01", 100.0, "H"), Pivot(20, "2025-01-20", 104.0, "H"), atr, 1.0)
    return {
        "passed": bool(ready_score > low_score and flat is not None and steep is None),
        "watch_score": low_score,
        "ready_score": ready_score,
        "flat_neckline_accepted": flat is not None,
        "steep_neckline_rejected": steep is None,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(self_test(), ensure_ascii=False, indent=2))
