# -*- coding: utf-8 -*-
"""Patron Terminal — Formasyon V3 çoklu pencere ve çoklu vade araştırma motoru.

V3, mevcut ``formasyon_v2.py`` dosyasını değiştirmez. V2'nin doğrulanmış
geometri motorunu ayrı geçmiş pencerelerinde ve ayrı zaman dilimlerinde
çalıştırır; sonuçları tek bir formasyona zorlamak yerine uyum/çelişki raporu
olarak döndürür.

Temel ilkeler:

* V2 çekirdeği aynen korunur; V3 bir orkestrasyon katmanıdır.
* Her pencere yalnızca kendi kesitindeki mumları görür. Gelecek veri analize
  sızmaz.
* Aynı vadede farklı formasyonlar çıkarsa ``ÇATIŞAN_YAPILAR`` döner ve zoraki
  birincil formasyon seçilmez.
* Kalite puanı şekil düzenidir; kârlılık puanı değildir.
* Tek günlük veri verilirse yalnızca günlük analiz çalışır. 4 saatlik analiz
  için ayrı 4 saatlik DataFrame sağlanmalıdır.

Örnek:

    report = analyze_formations_v3(
        {"1d": daily_df, "4h": four_hour_df},
        ticker="DOHOL",
        timeframes=("1d", "4h"),
    )

Bu dosya veri indirmez, veritabanına yazmaz ve Streamlit arayüzünü değiştirmez.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

import formasyon_v2 as _v2


ENGINE_VERSION = "3.0.0-multiframe-research"
SUPPORTED_TIMEFRAMES = ("1d", "4h")


# V2'nin minimum veri şartları nedeniyle kısa pencerenin kendisi 90/120 bar
# tutulur. V2 bu kesitin içinde ayrıca 45/60/... barlık üçgen pencerelerini
# dener. Böylece kısa yapı korunurken motorun veri yeterlilik kapısı aşılmaz.
DEFAULT_LOOKBACK_WINDOWS: dict[str, tuple[tuple[str, int], ...]] = {
    "1d": (
        ("kisa", 90),
        ("orta", 140),
        ("genis", 260),
        ("baglam", 520),
    ),
    "4h": (
        ("kisa", 120),
        ("orta", 240),
        ("genis", 480),
        ("baglam", 900),
    ),
}


_STAGE_RANK = {
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


@dataclass
class FormationV3Report:
    """V3'ün tek hisse için ürettiği denetlenebilir rapor."""

    engine_version: str
    ticker: Optional[str]
    requested_timeframes: list[str]
    data_ok: bool
    data_issues: list[str]
    timeframe_runs: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    consensus: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    overall_status: str
    primary_candidate: Optional[dict[str, Any]] = None
    calibration_status: str = (
        "ARAŞTIRMA — ÇOKLU PENCERE/VADİ UYUMU; GETİRİ KARNESİ BEKLİYOR"
    )


def _json_safe(value: Any) -> Any:
    """NumPy/Pandas değerlerini JSON'a güvenle çevrilebilir hale getirir."""

    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, pd.Timedelta)):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def report_to_dict(report: FormationV3Report) -> dict[str, Any]:
    """Raporu JSON'a yazılabilir sözlüğe çevirir."""

    return _json_safe(asdict(report))


def report_to_json(report: FormationV3Report, *, indent: int = 2) -> str:
    """Raporu JSON metni olarak döndürür."""

    return json.dumps(report_to_dict(report), ensure_ascii=False, indent=indent)


def render_v3_candidate_chart(
    df: pd.DataFrame,
    candidate: Mapping[str, Any],
    output_path: Path,
    *,
    ticker: Optional[str] = None,
    context_bars: int = 20,
) -> Path:
    """V3 kaydındaki bağımsız üst/alt çizgi başlangıçlarıyla PNG üretir.

    ``candidate``; ``FormationV3Report.candidates`` içinden gelen sözlük veya
    aynı alanları taşıyan başka bir sözlük olabilir. Çizim, adayın bitiş
    tarihinden sonraki mumları kullanmaz.
    """

    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    timeframe = str(candidate.get("timeframe", "1d"))
    clean, _, ok = _v2._clean_frame(df, timeframe)
    if not ok or clean.empty:
        raise ValueError("V3 grafiği için geçerli veri yok.")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    end_time = pd.Timestamp(candidate.get("end_time", clean.index[-1]))
    end_pos = int(clean.index.searchsorted(end_time, side="right") - 1)
    end_pos = max(0, min(end_pos, len(clean) - 1))
    start_time = pd.Timestamp(candidate.get("start_time", clean.index[0]))
    start_pos = int(clean.index.searchsorted(start_time, side="left"))
    start_pos = max(0, min(start_pos, end_pos))
    view_start = max(0, start_pos - int(context_bars))
    x = np.arange(view_start, end_pos + 1)
    part = clean.iloc[view_start : end_pos + 1]

    fig, ax = plt.subplots(figsize=(15, 7), dpi=150)
    fig.patch.set_facecolor("#07111f")
    ax.set_facecolor("#07111f")
    up = part["Close"].to_numpy(dtype=float) >= part["Open"].to_numpy(dtype=float)
    colors = np.where(up, "#14b8a6", "#ef5350")
    ax.vlines(
        x,
        part["Low"].to_numpy(dtype=float),
        part["High"].to_numpy(dtype=float),
        color=colors,
        linewidth=0.7,
        alpha=0.9,
    )
    body_floor = max(float(part["Close"].median()) * 0.001, 1e-6)
    for bar, open_value, close_value, color in zip(
        x,
        part["Open"],
        part["Close"],
        colors,
    ):
        bottom = min(float(open_value), float(close_value))
        height = max(abs(float(close_value) - float(open_value)), body_floor)
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

    line_colors = {"üst_sınır": "#ff9d3f", "alt_sınır": "#2db8f2"}
    for line in candidate.get("lines", []):
        try:
            line_start = int(clean.index.searchsorted(pd.Timestamp(line["start_time"])))
            line_end = int(
                clean.index.searchsorted(pd.Timestamp(line["end_time"]), side="right") - 1
            )
            line_start = max(view_start, min(line_start, end_pos))
            line_end = max(line_start, min(line_end, end_pos))
            line_x = np.arange(line_start, line_end + 1)
            start_price = float(line["start_price"])
            end_price = float(line["end_price"])
            line_y = np.linspace(start_price, end_price, len(line_x))
            role = str(line.get("role", ""))
            ax.plot(
                line_x,
                line_y,
                linestyle="--",
                linewidth=2.0,
                color=line_colors.get(role, "#c6cbd4"),
                label=role,
            )
        except Exception:
            continue

    for point in candidate.get("points", []):
        try:
            point_bar = int(clean.index.searchsorted(pd.Timestamp(point["time"])))
            if point_bar < view_start or point_bar > end_pos:
                continue
            is_upper = str(point.get("role", "")).startswith("üst")
            color = "#ff9d3f" if is_upper else "#2db8f2"
            marker = "v" if is_upper else "^"
            ax.scatter(
                [point_bar],
                [float(point["price"])],
                color=color,
                marker=marker,
                s=55,
                zorder=5,
            )
        except Exception:
            continue

    ticker_text = ticker or candidate.get("ticker") or "HISSE"
    title = (
        f"{ticker_text} — {candidate.get('pattern', 'FORMASYON')} — "
        f"{candidate.get('stage', 'DURUM')} — "
        f"{timeframe} / {candidate.get('window_label', 'pencere')} — "
        f"şekil kalitesi {float(candidate.get('quality_score', 0.0)):.1f}/100"
    )
    ax.set_title(title, color="#e8edf5", fontsize=14, fontweight="bold", loc="left")
    ax.grid(True, color="#243041", alpha=0.55, linewidth=0.6)
    ax.tick_params(colors="#9aa7b8")
    for spine in ax.spines.values():
        spine.set_color("#2e3b4d")
    ax.legend(loc="upper left", facecolor="#0d192b", edgecolor="#2e3b4d", labelcolor="#dce5f2")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return output_path


def _ordered_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Pencere kesmeden önce tarih sırasını sabitler; fiyat temizliğini V2 yapar."""

    if frame is None or not isinstance(frame, pd.DataFrame):
        return frame
    work = frame.copy()
    if not isinstance(work.index, pd.DatetimeIndex):
        try:
            work.index = pd.to_datetime(work.index)
        except Exception:
            return work
    work = work[~work.index.duplicated(keep="last")]
    return work.sort_index()


def _normalise_timeframes(
    data: pd.DataFrame | Mapping[str, pd.DataFrame],
    timeframe: str,
    timeframes: Optional[Sequence[str]],
) -> tuple[str, ...]:
    if timeframes is not None:
        requested = tuple(str(item) for item in timeframes)
    elif isinstance(data, Mapping):
        requested = tuple(str(item) for item in data.keys())
    else:
        requested = (timeframe,)

    if not requested:
        raise ValueError("En az bir zaman dilimi seçilmelidir.")
    invalid = [item for item in requested if item not in SUPPORTED_TIMEFRAMES]
    if invalid:
        raise ValueError(f"Desteklenmeyen zaman dilimleri: {', '.join(invalid)}")
    return tuple(dict.fromkeys(requested))


def _windows_for(
    timeframe: str,
    overrides: Optional[
        Mapping[str, Sequence[int] | Mapping[str, int]]
    ],
) -> tuple[tuple[str, int], ...]:
    if not overrides or timeframe not in overrides:
        return DEFAULT_LOOKBACK_WINDOWS[timeframe]

    raw = overrides[timeframe]
    if isinstance(raw, Mapping):
        items = tuple((str(label), int(bars)) for label, bars in raw.items())
    else:
        labels = ("kisa", "orta", "genis", "baglam", "ek")
        items = tuple(
            (labels[index] if index < len(labels) else f"pencere_{index + 1}", int(bars))
            for index, bars in enumerate(raw)
        )
    if not items or any(bars <= 0 for _, bars in items):
        raise ValueError(f"{timeframe} için pencere uzunlukları pozitif olmalıdır.")
    return items


def _pattern_family(pattern: str) -> str:
    if pattern in {"FİNCAN_ADAYI", "FİNCAN_KULP"}:
        return "fincan"
    if "ÜÇGEN" in pattern:
        return "üçgen"
    if "KAMA" in pattern:
        return "kama"
    if pattern == "TOBO":
        return "tobo"
    return pattern.lower()


def _candidate_record(
    candidate: Any,
    *,
    timeframe: str,
    window_label: str,
    requested_bars: int,
    source_rows_used: int,
) -> dict[str, Any]:
    raw = asdict(candidate)
    raw.update(
        {
            "timeframe": timeframe,
            "window_label": window_label,
            "lookback_bars": int(requested_bars),
            "source_rows_used": int(source_rows_used),
            "structure_bars": max(1, int(candidate.end_bar - candidate.start_bar + 1)),
            "pattern_family": _pattern_family(candidate.pattern),
            "stage_rank": _STAGE_RANK.get(candidate.stage, 99),
        }
    )
    return _json_safe(raw)


def _sample_pivots(points: Sequence[Any], max_points: int) -> list[Any]:
    """Çok kalabalık temas listesini ilk/orta/son noktaları koruyarak küçültür."""

    if len(points) <= max_points:
        return list(points)
    indexes = np.linspace(0, len(points) - 1, max_points).round().astype(int)
    return [points[int(index)] for index in dict.fromkeys(indexes)]


def _line_hypotheses(
    points: Sequence[Any],
    close: np.ndarray,
    atr_now: float,
    timeframe: str,
) -> list[tuple[Any, list[Any], float]]:
    """Geniş yapı için üç temaslı çizgi adayları üretir.

    V2'nin son altı temas kuralı yerine, temas üçlülerinden çizgi adayları
    üretir ve çizgiye yakın diğer temasları destek olarak sayar. En iyi birkaç
    çizgi geri döner; bütün kombinasyonlar doğrudan dışarıya taşınmaz.
    """

    if len(points) < 3:
        return []
    sampled = _sample_pivots(points, 16 if timeframe == "1d" else 18)
    min_span = int(_v2.TIMEFRAME_CONFIG[timeframe]["triangle_min_span"])
    price_ref = float(np.nanmedian(close)) if close.size else 1.0
    tolerance = max(float(atr_now) * 0.90, price_ref * 0.018)
    hypotheses: list[tuple[Any, list[Any], float]] = []

    for triple in combinations(sampled, 3):
        if triple[-1].bar - triple[0].bar < min_span:
            continue
        fit = _v2._fit_line(list(triple))
        if fit is None:
            continue
        inliers = [
            point
            for point in points
            if abs(float(point.price) - fit.at(point.bar)) <= tolerance
        ]
        if len(inliers) < 3 or inliers[-1].bar - inliers[0].bar < min_span:
            continue
        refit = _v2._fit_line(inliers)
        if refit is None:
            continue
        inliers = [
            point
            for point in points
            if abs(float(point.price) - refit.at(point.bar)) <= tolerance
        ]
        if len(inliers) < 3 or inliers[-1].bar - inliers[0].bar < min_span:
            continue
        error = float(np.mean(
            [
                abs(float(point.price) - refit.at(point.bar))
                / max(abs(refit.at(point.bar)), 1e-9)
                for point in inliers
            ]
        ))
        support = len(inliers) / max(len(points), 1)
        score = support * 100.0 + min(20.0, len(inliers) * 2.0) - error * 300.0
        hypotheses.append((refit, inliers, score))

    hypotheses.sort(key=lambda item: item[2], reverse=True)
    kept: list[tuple[Any, list[Any], float]] = []
    for fit, inliers, score in hypotheses:
        if any(
            abs(fit.slope - existing.slope) / max(abs(fit.slope), 1e-9) < 0.10
            and abs(fit.intercept - existing.intercept)
            / max(abs(fit.intercept), 1e-9) < 0.02
            for existing, _, _ in kept
        ):
            continue
        kept.append((fit, inliers, score))
        if len(kept) >= 12:
            break
    return kept


def _outer_line_hypotheses(
    points: Sequence[Any],
    close: np.ndarray,
    atr_now: float,
    timeframe: str,
    side: str,
) -> list[tuple[Any, list[Any], float]]:
    """Kullanıcı çizimine yakın dış-zarf çizgileri üretir.

    Bu arama iki gerçek temas arasındaki çizgiyi dener. Üst sınırda fiyatın
    belirgin biçimde üstte kalmasını, alt sınırda ise belirgin biçimde altta
    kalmasını istemez. Böylece çizgi, son temasların ortalaması değil, yapının
    dış kenarı olur.
    """

    if len(points) < 2:
        return []
    sampled = _sample_pivots(points, 18 if timeframe == "1d" else 20)
    min_span = int(_v2.TIMEFRAME_CONFIG[timeframe]["triangle_min_span"])
    price_ref = float(np.nanmedian(close)) if close.size else 1.0
    tolerance = max(float(atr_now) * 0.90, price_ref * 0.018)
    hypotheses: list[tuple[Any, list[Any], float]] = []

    for first, last in combinations(sampled, 2):
        if last.bar - first.bar < min_span:
            continue
        fit = _v2._fit_line([first, last])
        if fit is None:
            continue
        eligible = [point for point in points if point.bar >= first.bar]
        if len(eligible) < 2:
            continue
        if side == "upper":
            violations = [
                point
                for point in eligible
                if float(point.price) > fit.at(point.bar) + tolerance
            ]
        else:
            violations = [
                point
                for point in eligible
                if float(point.price) < fit.at(point.bar) - tolerance
            ]
        if violations:
            worst = max(
                abs(float(point.price) - fit.at(point.bar)) for point in violations
            )
            if worst > tolerance * 1.75:
                continue

        touches = [
            point
            for point in eligible
            if abs(float(point.price) - fit.at(point.bar)) <= tolerance
        ]
        if len(touches) < 2 or touches[-1].bar - touches[0].bar < min_span:
            continue
        mean_error = float(np.mean(
            [
                abs(float(point.price) - fit.at(point.bar))
                / max(abs(fit.at(point.bar)), 1e-9)
                for point in touches
            ]
        ))
        support = len(touches) / max(len(eligible), 1)
        score = (
            28.0
            + min(24.0, len(touches) * 4.0)
            + support * 35.0
            - mean_error * 220.0
        )
        hypotheses.append((fit, touches, score))

    hypotheses.sort(key=lambda item: item[2], reverse=True)
    kept: list[tuple[Any, list[Any], float]] = []
    for fit, touches, score in hypotheses:
        if any(
            abs(fit.slope - existing.slope) / max(abs(fit.slope), 1e-9) < 0.08
            and abs(fit.at(touches[0].bar) - existing.at(existing_points[0].bar))
            / max(price_ref, 1e-9)
            < 0.025
            for existing, existing_points, _ in kept
        ):
            continue
        kept.append((fit, touches, score))
        if len(kept) >= 18:
            break
    return kept


def _structural_boundary_line_hypotheses(
    points: Sequence[Any],
    close: np.ndarray,
    atr_now: float,
    timeframe: str,
    side: str,
) -> list[tuple[Any, list[Any], float]]:
    """Geniş yapıda ana taban/tepe sınırlarını arar.

    V2'nin son temas yaklaşımı, geniş bir yatay tabanın ardından gelen daha
    yüksek dipleri son birkaç küçük salınımla karıştırabiliyordu. Kullanıcının
    DOHOL/TRALT çizimlerinde görülen sınır ise erken bir ana dipten/tepeden
    başlıyor ve sonraki temasları dışarıdan taşıyor. ``side=lower`` yükselen
    tabanı, ``side=upper`` aşağı eğimli tavanı temsil eder.
    """

    if side not in {"upper", "lower"} or len(points) < 2 or close.size < 1:
        return []

    total_bars = len(close)
    cfg = _v2.TIMEFRAME_CONFIG[timeframe]
    # İki temasın arasında bir miktar mesafe arıyoruz; 1d'de 20 mumluk
    # taban→ikinci dip ilişkisi, 25 mumluk genel üçgen eşiğinin biraz altında
    # olabilir (TRALT örneği). Yapının kendisi aşağıda ayrıca doğrulanır.
    min_anchor_span = max(20, int(cfg["triangle_min_span"]) - 5)
    sampled = _sample_pivots(points, 24 if timeframe == "1d" else 28)
    price_ref = float(np.nanmedian(close)) if close.size else 1.0
    tolerance = max(float(atr_now) * 1.15, price_ref * 0.018)
    # Çok erken başlayan çizgiler, önceki düşüş trendinin tabanını da
    # formasyonun alt sınırı sanabiliyor. Kullanıcı çizimlerinde gördüğümüz
    # ana taban, geniş pencerenin orta/son bölümünde başlıyor.
    anchor_floor = int(total_bars * 0.45)
    anchor_ceiling = int(total_bars * 0.86)
    hypotheses: list[tuple[Any, list[Any], float]] = []

    for first, last in combinations(sampled, 2):
        if first.bar < anchor_floor or first.bar > anchor_ceiling:
            continue
        if last.bar - first.bar < min_anchor_span:
            continue
        fit = _v2._fit_line([first, last])
        if fit is None:
            continue
        if side == "lower" and fit.slope <= 0:
            continue
        if side == "upper" and fit.slope >= 0:
            continue

        if side == "lower":
            # Bir sonraki küçük dalgada daha düşük bir dip oluşuyorsa ilk
            # temas ana taban değildir. Çizgiyi o yeni tabandan başlat; aksi
            # halde TRALT'taki 30 Mart 40,18 seviyesi, 4 Mayıs 39,40 tabanını
            # yanlış biçimde gölgeler.
            local_radius = max(30, min_anchor_span)
            local_floor = max(
                tolerance * 0.12,
                abs(float(first.price)) * 0.010,
            )
            nearby_lows = [
                point
                for point in points
                if point is not first
                and abs(point.bar - first.bar) <= local_radius
            ]
            if any(float(point.price) < float(first.price) - local_floor for point in nearby_lows):
                continue

        eligible = [point for point in points if point.bar >= first.bar]
        if len(eligible) < 2:
            continue

        # Kırılımdan sonraki barlar sınır çizgisini bozabilir. İlk belirgin
        # ihlale kadar olan bölüm formasyonun gövdesidir; sonraki bölüm ise
        # kırılım sonrası harekettir ve eski yapının çizgisini silemez.
        if side == "lower":
            is_violation = lambda point: float(point.price) < fit.at(point.bar) - tolerance
        else:
            is_violation = lambda point: float(point.price) > fit.at(point.bar) + tolerance
        first_violation = next(
            (point.bar for point in eligible if is_violation(point)),
            None,
        )
        if first_violation is not None and first_violation <= last.bar:
            continue
        pre_break = [
            point
            for point in eligible
            if first_violation is None or point.bar < first_violation
        ]

        touches = [
            point
            for point in pre_break
            if abs(float(point.price) - fit.at(point.bar)) <= tolerance
        ]
        if len(touches) < 2:
            continue
        if touches[-1].bar - touches[0].bar < min_anchor_span:
            continue
        if side == "lower":
            progressive_touch = any(
                float(point.price) >= float(first.price) + tolerance * 0.35
                for point in touches[1:]
            )
        else:
            progressive_touch = any(
                float(point.price) <= float(first.price) - tolerance * 0.35
                for point in touches[1:]
            )
        if not progressive_touch:
            continue

        mean_error = float(np.mean(
            [
                abs(float(point.price) - fit.at(point.bar))
                / max(abs(fit.at(point.bar)), 1e-9)
                for point in touches
            ]
        ))
        support = len(touches) / max(len(pre_break), 1)
        coverage = (last.bar - first.bar) / max(float(total_bars), 1.0)
        earlyness = 1.0 - first.bar / max(float(total_bars - 1), 1.0)
        extension = max(0.0, total_bars - 1 - last.bar) / max(
            float(total_bars), 1.0
        )
        signed_move = fit.slope * (last.bar - first.bar) / max(abs(first.price), 1e-9)
        boundary_move = signed_move if side == "lower" else -signed_move
        score = (
            45.0
            + min(22.0, len(touches) * 5.0)
            + support * 28.0
            + min(18.0, coverage * 35.0)
            + min(12.0, earlyness * 12.0)
            + min(12.0, extension * 18.0)
            + min(15.0, max(0.0, boundary_move) * 100.0)
            - mean_error * 220.0
        )
        hypotheses.append((fit, touches, score))

    hypotheses.sort(key=lambda item: item[2], reverse=True)
    kept: list[tuple[Any, list[Any], float]] = []
    for fit, touches, score in hypotheses:
        if any(
            abs(fit.slope - existing.slope) / max(abs(fit.slope), 1e-9) < 0.10
            and abs(fit.at(touches[0].bar) - existing.at(existing_points[0].bar))
            / max(price_ref, 1e-9)
            < 0.025
            for existing, existing_points, _ in kept
        ):
            continue
        kept.append((fit, touches, score))
        if len(kept) >= 10:
            break
    return kept


def _dedupe_wide_candidates(candidates: list[Any]) -> list[Any]:
    """Geniş adaylarda kısa iç çizgi yerine ana dış sınırı korur."""

    inactive_stages = {"GEÇERSİZ", "SÜRESİ_DOLDU", "UZAMIŞ", "TAMAMLANDI"}
    grouped: dict[tuple[str, str], list[Any]] = {}
    for candidate in candidates:
        grouped.setdefault(
            (str(candidate.pattern), str(candidate.direction)),
            [],
        ).append(candidate)

    def rank(candidate: Any) -> tuple[Any, ...]:
        metrics = candidate.metrics or {}
        lower_drift = float(metrics.get("lower_total_drift_pct", 0.0) or 0.0)
        lower_start = int(metrics.get("lower_line_start_bar", 0) or 0)
        source_bars = max(1, int(candidate.end_bar) + 1)
        # Önceki düşüş trendinin diplerini değil, mevcut yapının orta/son
        # bölümünde oluşan ana tabanı tercih et. Bu, DOHOL'de 7 Nisan ve
        # TRALT'ta 4 Mayıs diplerinin son küçük salınımlara yenilmesini önler.
        lower_is_current_structure = lower_start >= int(source_bars * 0.60)
        lower_age = int(metrics.get("lower_line_start_age_bars", 0) or 0)
        upper_age = int(metrics.get("upper_line_start_age_bars", 0) or 0)
        return (
            0 if candidate.stage in inactive_stages else 1,
            1 if lower_drift > 0.0 and lower_is_current_structure else 0,
            lower_age if lower_drift > 0.0 and lower_is_current_structure else 0,
            upper_age if float(metrics.get("upper_total_drift_pct", 0.0) or 0.0) < 0.0 else 0,
            int(metrics.get("lower_touch_count", 0) or 0),
            int(metrics.get("upper_touch_count", 0) or 0),
            float(candidate.quality_score or 0.0),
            -int(candidate.start_bar),
        )

    kept = [max(rows, key=rank) for rows in grouped.values() if rows]
    return sorted(kept, key=lambda candidate: (-float(candidate.quality_score or 0.0), -candidate.start_bar))


def _detect_wide_triangles(
    df: pd.DataFrame,
    timeframe: str,
    requested_bars: int,
) -> list[Any]:
    """V3 geniş yapı keşfi: son altı temas yerine destekli çizgi hipotezleri."""

    clean, _, data_ok = _v2._clean_frame(df, timeframe)
    if not data_ok or clean.empty:
        return []
    pivots = _v2._extract_pivots(clean, timeframe)
    atr_series = _v2._atr(clean)
    atr_now = _v2._finite(
        atr_series.iloc[-1],
        float((clean["High"] - clean["Low"]).tail(20).median()),
    )
    if atr_now <= 0:
        return []

    n = len(clean)
    cfg = _v2.TIMEFRAME_CONFIG[timeframe]
    start_cut = max(0, n - int(requested_bars))
    highs = [point for point in pivots if point.kind == "H" and point.bar >= start_cut]
    lows = [point for point in pivots if point.kind == "L" and point.bar >= start_cut]
    if len(highs) < 3 or len(lows) < 3:
        return []
    if _v2._gap_in_range(clean, start_cut, n - 1, timeframe):
        return []

    close = clean["Close"].to_numpy(dtype=float)
    top_lines = _line_hypotheses(highs, close, atr_now, timeframe)
    top_lines += _outer_line_hypotheses(highs, close, atr_now, timeframe, "upper")
    bottom_lines = _line_hypotheses(lows, close, atr_now, timeframe)
    bottom_lines += _outer_line_hypotheses(lows, close, atr_now, timeframe, "lower")
    # Geniş kullanıcı çizimlerinde alt sınır çoğu zaman son iki dipten değil,
    # daha erken ana tabandan başlayıp daha yüksek dipleri taşır.
    top_lines += _structural_boundary_line_hypotheses(
        highs,
        close,
        atr_now,
        timeframe,
        "upper",
    )
    bottom_lines += _structural_boundary_line_hypotheses(
        lows,
        close,
        atr_now,
        timeframe,
        "lower",
    )
    found: list[Any] = []
    flat_limit = 0.015

    for top, top_points, top_score in top_lines:
        for bottom, bottom_points, bottom_score in bottom_lines:
            start = min(top_points[0].bar, bottom_points[0].bar)
            end = max(top_points[-1].bar, bottom_points[-1].bar)
            shape_start = max(top_points[0].bar, bottom_points[0].bar)
            line_start_gap_bars = abs(top_points[0].bar - bottom_points[0].bar)
            max_line_start_gap = 30 if timeframe == "1d" else 60
            if line_start_gap_bars > max_line_start_gap:
                continue
            structure_span = end - start
            if not int(cfg["triangle_min_span"]) <= structure_span <= int(
                cfg["triangle_max_span"]
            ):
                continue
            price_ref = float(np.median(clean["Close"].iloc[start : end + 1]))
            top_drift = top.slope * structure_span / max(price_ref, 1e-9)
            bottom_drift = bottom.slope * structure_span / max(price_ref, 1e-9)
            if bottom.slope > 0:
                # Hangi çizgi üreticisinden geldiğine bakılmaksızın, yükselen
                # alt sınırın başlangıcı yerel ana taban olmalıdır. Böylece
                # kısa çizgi üreticisi 30 Mart'ı seçse bile, 4 Mayıs'taki daha
                # düşük TRALT dibini görmezden gelemez.
                anchor = bottom_points[0]
                local_radius = max(30, int(cfg["triangle_min_span"]))
                local_floor = max(atr_now * 0.15, price_ref * 0.010)
                nearby_lows = [
                    point
                    for point in lows
                    if point is not anchor
                    and abs(point.bar - anchor.bar) <= local_radius
                ]
                if any(
                    float(point.price) < float(anchor.price) - local_floor
                    for point in nearby_lows
                ):
                    continue
            # Her iki sınırın da gerçekten birlikte gözlenmeye başladığı
            # noktadaki boşluk ölçülür. Daha erken başlayan çizgi, diğer
            # çizgi henüz oluşmadan geriye doğru sonsuzca uzatılmamalıdır.
            start_gap = top.at(shape_start) - bottom.at(shape_start)
            end_gap = top.at(end) - bottom.at(end)
            if start_gap <= 0 or end_gap <= 0 or end_gap >= start_gap * 0.90:
                continue
            if start_gap / max(price_ref, 1e-9) > 0.30:
                continue

            denom = bottom.slope - top.slope
            apex = (
                (top.intercept - bottom.intercept) / denom
                if abs(denom) > 1e-12
                else math.inf
            )
            if not math.isfinite(apex):
                continue
            if apex < end - max(3, structure_span * 0.10):
                continue
            if apex > n + structure_span * 1.50:
                continue

            historical_end = max(start + 1, n - 3)
            bars = np.arange(start, historical_end)
            close_seg = close[start:historical_end]
            upper_seg = np.asarray([top.at(index) for index in bars])
            lower_seg = np.asarray([bottom.at(index) for index in bars])
            tolerance = max(atr_now * 0.90, price_ref * 0.018)
            containment = float(
                np.mean(
                    (close_seg <= upper_seg + tolerance)
                    & (close_seg >= lower_seg - tolerance)
                )
            )
            if containment < 0.70:
                continue

            def edge(drift: float, fit: Any) -> str:
                if abs(drift) <= flat_limit:
                    return "FLAT"
                if fit.r2 < 0.45:
                    return "NOISE"
                return "UP" if drift > 0 else "DOWN"

            top_edge, bottom_edge = edge(top_drift, top), edge(bottom_drift, bottom)
            if "NOISE" in (top_edge, bottom_edge):
                continue
            combo = {
                ("FLAT", "UP"): ("YÜKSELEN_ÜÇGEN", "üçgen", "bullish", "top"),
                ("DOWN", "FLAT"): ("ALÇALAN_ÜÇGEN", "üçgen", "bearish", "bottom"),
                ("DOWN", "UP"): ("SİMETRİK_ÜÇGEN", "üçgen", None, None),
                ("UP", "UP"): ("YÜKSELEN_KAMA", "kama", "bearish", "bottom"),
                ("DOWN", "DOWN"): ("ALÇALAN_KAMA", "kama", "bullish", "top"),
            }.get((top_edge, bottom_edge))
            if combo is None:
                continue
            name, family, direction, trigger_side = combo
            if family == "kama" and min(abs(top_drift), abs(bottom_drift)) > 0.05:
                continue
            if direction is None:
                _, prior_total = _v2._prior_trend(close, start, "bullish")
                direction, trigger_side = (
                    ("bullish", "top") if prior_total >= 0 else ("bearish", "bottom")
                )

            buffer = atr_now * 0.50
            if trigger_side == "top":
                trigger_fit, invalidation = top, bottom.at(n - 1) - buffer
            else:
                trigger_fit, invalidation = bottom, top.at(n - 1) + buffer
            stage, break_bar, state_metrics = _v2._boundary_state(
                clean,
                trigger_fit,
                direction,
                atr_now,
                invalidation,
                apex,
                search_start=end,
                max_break_age=30 if timeframe == "1d" else 45,
            )
            if (
                stage == "OLUŞUYOR"
                and abs(float(state_metrics.get("distance_to_trigger_pct", 0.0))) > 18.0
            ):
                continue
            trigger = trigger_fit.at(n - 1)
            height = max(0.0, top.at(end) - bottom.at(end))
            target: Optional[float] = None
            if stage in ("KIRILIM_DOĞRULANDI", "UZAMIŞ"):
                target = trigger + height if direction == "bullish" else max(0.01, trigger - height)
                if (
                    direction == "bullish" and float(close[-1]) >= target
                ) or (
                    direction == "bearish" and float(close[-1]) <= target
                ):
                    stage = "TAMAMLANDI"

            points = [
                *[_v2._point(clean, "üst_temas", point) for point in top_points],
                *[_v2._point(clean, "alt_temas", point) for point in bottom_points],
            ]
            # Sınır çizgisi, kırılım sonrasında mevcut son muma kadar zorla
            # uzatılmaz. Kullanıcının çizimindeki gibi kırılım mumunda veya
            # kırılım yoksa son geçerli temas bölgesinde sona erer.
            line_end_bar = max(end, break_bar) if break_bar is not None else end
            lines = [
                # Kullanıcının çizimindeki gibi her sınır kendi ilk temasından
                # başlar; iki çizgi ortak bir mumdan zorla başlatılmaz.
                _v2._line(clean, "üst_sınır", top, top_points[0].bar, line_end_bar),
                _v2._line(clean, "alt_sınır", bottom, bottom_points[0].bar, line_end_bar),
            ]
            line_quality = max(0.0, min(20.0, (top_score + bottom_score) * 0.10))
            start_alignment_quality = max(
                0.0,
                12.0
                * (1.0 - line_start_gap_bars / max(float(max_line_start_gap), 1.0)),
            )
            quality = _v2._score(
                {
                    "zorunlu_geometri": 40.0,
                    "eğim_uyumu": min(15.0, max(abs(top_drift), abs(bottom_drift)) * 140),
                    "çizgi_uyumu": min(20.0, 10.0 * min(top.r2, bottom.r2)),
                    "sıkışma": max(0.0, 15.0 * (1.0 - end_gap / start_gap)),
                    "koridor": 10.0 * containment,
                    "temas_destegi": line_quality,
                    "sinir_baslangic_uyumu": start_alignment_quality,
                }
            )
            found.append(
                _v2.PatternCandidate(
                    pattern=name,
                    direction=direction,
                    stage=stage,
                    quality_score=quality,
                    trigger=round(trigger, 4),
                    invalidation=round(float(invalidation), 4),
                    target=round(target, 4) if target is not None else None,
                    start_bar=start,
                    end_bar=n - 1,
                    start_time=_v2._ts(clean.index[start]),
                    end_time=_v2._ts(clean.index[-1]),
                    breakout_bar=break_bar,
                    breakout_time=(
                        _v2._ts(clean.index[break_bar])
                        if break_bar is not None
                        else None
                    ),
                    points=points,
                    lines=lines,
                    checks={
                        "v3_genis_temas_arama": True,
                        "en_az_3_ust_temas": len(top_points) >= 3,
                        "en_az_3_alt_temas": len(bottom_points) >= 3,
                        "ust_kenar": top_edge,
                        "alt_kenar": bottom_edge,
                        "cizgiler_yakinsiyor": True,
                        "fiyat_koridorda": True,
                    },
                    metrics={
                        "formasyon_ailesi": family,
                        "detector": "v3_wide_pivot_fit",
                        "upper_touch_count": len(top_points),
                        "lower_touch_count": len(bottom_points),
                        "upper_r2": round(top.r2, 3),
                        "lower_r2": round(bottom.r2, 3),
                        "upper_total_drift_pct": round(top_drift * 100, 3),
                        "lower_total_drift_pct": round(bottom_drift * 100, 3),
                        "containment_pct": round(containment * 100, 2),
                        "shape_start_time": _v2._ts(clean.index[shape_start]),
                        "upper_line_start_bar": int(top_points[0].bar),
                        "lower_line_start_bar": int(bottom_points[0].bar),
                        "upper_line_start_age_bars": int(n - 1 - top_points[0].bar),
                        "lower_line_start_age_bars": int(n - 1 - bottom_points[0].bar),
                        "line_end_bar": int(line_end_bar),
                        "line_start_gap_bars": line_start_gap_bars,
                        "line_start_alignment_quality": round(start_alignment_quality, 3),
                        "start_gap_pct": round(start_gap / max(price_ref, 1e-9) * 100, 3),
                        "apex_bar": round(float(apex), 2),
                        **state_metrics,
                    },
                    notes=[
                        "Geniş yapı: son altı temasla sınırlı olmayan destekli çizgi hipotezi.",
                        "Çizimler üst ve alt sınırın kendi ilk temasından başlatıldı.",
                        "Bu aday araştırma amaçlıdır; V2 kalite puanıyla aynı kalibrasyonda değildir.",
                    ],
                )
            )
    return _dedupe_wide_candidates(found)


def _interval(record: Mapping[str, Any]) -> Optional[tuple[pd.Timestamp, pd.Timestamp]]:
    try:
        start = pd.Timestamp(record["start_time"])
        end = pd.Timestamp(record["end_time"])
        if end < start:
            start, end = end, start
        return start, end
    except Exception:
        return None


def _overlap_ratio(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    left_interval = _interval(left)
    right_interval = _interval(right)
    if left_interval is None or right_interval is None:
        return 0.0
    left_start, left_end = left_interval
    right_start, right_end = right_interval
    overlap_start = max(left_start, right_start)
    overlap_end = min(left_end, right_end)
    union_start = min(left_start, right_start)
    union_end = max(left_end, right_end)
    union_seconds = max((union_end - union_start).total_seconds(), 1.0)
    overlap_seconds = max((overlap_end - overlap_start).total_seconds(), 0.0)
    return overlap_seconds / union_seconds


def _build_consensus(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        key = (
            str(candidate["pattern"]),
            str(candidate.get("direction") or "neutral"),
            str(candidate["pattern_family"]),
        )
        groups.setdefault(key, []).append(candidate)

    result: list[dict[str, Any]] = []
    for (pattern, direction, family), rows in groups.items():
        by_timeframe: dict[str, set[str]] = {}
        start_times: list[str] = []
        qualities: list[float] = []
        for row in rows:
            by_timeframe.setdefault(str(row["timeframe"]), set()).add(
                str(row["window_label"])
            )
            start_times.append(str(row["start_time"]))
            qualities.append(float(row.get("quality_score") or 0.0))
        windows_by_timeframe = {
            tf: sorted(labels) for tf, labels in by_timeframe.items()
        }
        window_count = sum(len(labels) for labels in by_timeframe.values())
        timeframe_count = len(by_timeframe)
        result.append(
            {
                "pattern": pattern,
                "pattern_family": family,
                "direction": direction,
                "window_count": window_count,
                "timeframe_count": timeframe_count,
                "windows_by_timeframe": windows_by_timeframe,
                "support_rows": len(rows),
                "start_time_min": min(start_times) if start_times else None,
                "start_time_max": max(start_times) if start_times else None,
                "median_quality": round(float(np.median(qualities)), 2)
                if qualities
                else None,
                "status": (
                    "ÇOKLU_PENCERE_VE_VADE_UYUMU"
                    if window_count >= 2 and timeframe_count >= 2
                    else "ÇOKLU_PENCERE_UYUMU"
                    if window_count >= 2
                    else "ÇOKLU_VADE_UYUMU"
                    if timeframe_count >= 2
                    else "TEK_PENCERE"
                ),
            }
        )
    result.sort(
        key=lambda row: (
            -int(row["window_count"]),
            -int(row["timeframe_count"]),
            -float(row["median_quality"] or 0.0),
        )
    )
    return result


def _build_conflicts(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aynı vadede üst üste binen farklı formasyonları çelişki olarak döndürür."""

    conflicts: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for left, right in combinations(candidates, 2):
        if left["timeframe"] != right["timeframe"]:
            continue
        if left["pattern"] == right["pattern"]:
            continue
        overlap = _overlap_ratio(left, right)
        if overlap < 0.35:
            continue
        pattern_pair = tuple(sorted((str(left["pattern"]), str(right["pattern"]))))
        key = (
            str(left["timeframe"]),
            pattern_pair,
            str(left.get("start_time")),
            str(right.get("start_time")),
        )
        if key in seen:
            continue
        seen.add(key)
        conflicts.append(
            {
                "timeframe": left["timeframe"],
                "type": "AYNI_VADEDE_FARKLI_YAPI",
                "left": {
                    "pattern": left["pattern"],
                    "window_label": left["window_label"],
                    "lookback_bars": left["lookback_bars"],
                    "start_time": left["start_time"],
                    "quality_score": left["quality_score"],
                },
                "right": {
                    "pattern": right["pattern"],
                    "window_label": right["window_label"],
                    "lookback_bars": right["lookback_bars"],
                    "start_time": right["start_time"],
                    "quality_score": right["quality_score"],
                },
                "overlap_ratio": round(overlap, 3),
            }
        )
    conflicts.sort(
        key=lambda row: (
            str(row["timeframe"]),
            -float(row["overlap_ratio"]),
        )
    )
    return conflicts


def _choose_primary(
    candidates: list[dict[str, Any]],
    consensus: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    if not candidates or conflicts:
        return None

    supported = {
        (row["pattern"], row["direction"]): row for row in consensus
    }

    def rank(row: dict[str, Any]) -> tuple[Any, ...]:
        group = supported.get((row["pattern"], row["direction"]), {})
        return (
            int(group.get("window_count", 1)),
            int(group.get("timeframe_count", 1)),
            -int(row.get("stage_rank", 99)),
            float(row.get("quality_score") or 0.0),
            -int(row.get("lookback_bars") or 0),
        )

    return max(candidates, key=rank)


def analyze_formations_v3(
    data: pd.DataFrame | Mapping[str, pd.DataFrame],
    *,
    ticker: Optional[str] = None,
    timeframe: str = "1d",
    timeframes: Optional[Sequence[str]] = None,
    lookback_windows: Optional[
        Mapping[str, Sequence[int] | Mapping[str, int]]
    ] = None,
    include_inactive: bool = False,
    max_results_per_window: int = 8,
) -> FormationV3Report:
    """Bir hisseyi birden fazla geçmiş penceresi ve zaman diliminde inceler.

    ``data`` tek DataFrame ise yalnızca ``timeframe`` analiz edilir. Birden
    fazla vade için ``{"1d": daily_df, "4h": four_hour_df}`` biçiminde sözlük
    verilmelidir. V3 veri çekmez; kaynak tarih sınırı çağıranın verdiği
    DataFrame'dir.
    """

    requested = _normalise_timeframes(data, timeframe, timeframes)
    if isinstance(data, Mapping):
        frames = {str(key): value for key, value in data.items()}
    else:
        frames = {timeframe: data}

    issues: list[str] = []
    timeframe_runs: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    for tf in requested:
        frame = frames.get(tf)
        if frame is None:
            issues.append(f"{tf}: veri DataFrame'i verilmedi.")
            timeframe_runs.append(
                {
                    "timeframe": tf,
                    "data_ok": False,
                    "source_rows": 0,
                    "windows": [],
                    "issues": ["Veri DataFrame'i verilmedi."],
                }
            )
            continue
        if not isinstance(frame, pd.DataFrame):
            message = "Veri bir Pandas DataFrame değil."
            issues.append(f"{tf}: {message}")
            timeframe_runs.append(
                {
                    "timeframe": tf,
                    "data_ok": False,
                    "source_rows": 0,
                    "windows": [],
                    "issues": [message],
                }
            )
            continue

        ordered = _ordered_frame(frame)
        windows = _windows_for(tf, lookback_windows)
        cfg = _v2.TIMEFRAME_CONFIG[tf]
        run_issues: list[str] = []
        window_rows: list[dict[str, Any]] = []
        completed_effective_lengths: set[int] = set()
        successful_windows = 0

        for label, requested_bars in windows:
            effective_bars = min(len(ordered), int(requested_bars))
            if effective_bars in completed_effective_lengths:
                window_rows.append(
                    {
                        "label": label,
                        "requested_bars": requested_bars,
                        "effective_bars": effective_bars,
                        "status": "AYNI_VERİ_KESİTİ_ZATEN_ÇALIŞTI",
                    }
                )
                continue
            completed_effective_lengths.add(effective_bars)

            if effective_bars < int(cfg["min_rows"]):
                message = (
                    f"{label}: {effective_bars} mum var; en az "
                    f"{int(cfg['min_rows'])} mum gerekli."
                )
                run_issues.append(message)
                window_rows.append(
                    {
                        "label": label,
                        "requested_bars": requested_bars,
                        "effective_bars": effective_bars,
                        "status": "YETERSİZ_VERİ",
                    }
                )
                continue

            slice_df = ordered.tail(effective_bars)
            try:
                v2_report = _v2.analyze_formations(
                    slice_df,
                    ticker=ticker,
                    timeframe=tf,
                    max_results=max_results_per_window,
                )
            except Exception as exc:
                message = f"{label}: V2 analizi başarısız: {exc}"
                run_issues.append(message)
                window_rows.append(
                    {
                        "label": label,
                        "requested_bars": requested_bars,
                        "effective_bars": effective_bars,
                        "status": "HATA",
                    }
                )
                continue

            successful_windows += 1 if v2_report.data_ok else 0
            for candidate in v2_report.patterns:
                record = _candidate_record(
                    candidate,
                    timeframe=tf,
                    window_label=label,
                    requested_bars=requested_bars,
                    source_rows_used=v2_report.row_count,
                )
                record["detector"] = "v2_recent_pivots"
                candidates.append(record)

            # Geniş/bağlam pencerelerinde V2'nin son altı temas sınırını aşan
            # ikinci keşif. Bu katman yalnızca aktif adayları ekler; amacı,
            # kısa V2 yapısıyla çelişen daha geniş yapıyı görünür kılmaktır.
            wide_limit = 260 if tf == "1d" else 480
            wide_candidates: list[Any] = []
            if requested_bars >= wide_limit and v2_report.data_ok:
                try:
                    wide_candidates = _detect_wide_triangles(
                        slice_df,
                        tf,
                        requested_bars,
                    )
                except Exception as exc:
                    run_issues.append(f"{label}: geniş yapı araması başarısız: {exc}")
                for candidate in wide_candidates:
                    is_inactive = candidate.stage in {
                        "GEÇERSİZ",
                        "SÜRESİ_DOLDU",
                        "UZAMIŞ",
                        "TAMAMLANDI",
                    }
                    if is_inactive and not include_inactive:
                        continue
                    record = _candidate_record(
                        candidate,
                        timeframe=tf,
                        window_label=label,
                        requested_bars=requested_bars,
                        source_rows_used=v2_report.row_count,
                    )
                    record["detector"] = "v3_wide_pivot_fit"
                    if is_inactive:
                        record["inactive"] = True
                    candidates.append(record)
            if include_inactive:
                for candidate in v2_report.inactive_patterns:
                    record = _candidate_record(
                        candidate,
                        timeframe=tf,
                        window_label=label,
                        requested_bars=requested_bars,
                        source_rows_used=v2_report.row_count,
                    )
                    record["inactive"] = True
                    candidates.append(record)

            report_issues = list(v2_report.data_issues)
            run_issues.extend(
                f"{label}: {message}" for message in report_issues
            )
            window_rows.append(
                {
                    "label": label,
                    "requested_bars": requested_bars,
                    "effective_bars": effective_bars,
                    "status": "TAMAM",
                    "v2_data_ok": bool(v2_report.data_ok),
                    "candidate_count": len(v2_report.patterns),
                    "wide_candidate_count": sum(
                        1
                        for candidate in wide_candidates
                        if candidate.stage
                        not in {"GEÇERSİZ", "SÜRESİ_DOLDU", "UZAMIŞ", "TAMAMLANDI"}
                    ),
                    "inactive_count": len(v2_report.inactive_patterns),
                    "last_time": v2_report.last_time,
                }
            )

        if run_issues:
            issues.extend(f"{tf}: {message}" for message in run_issues)
        timeframe_runs.append(
            {
                "timeframe": tf,
                "data_ok": bool(successful_windows),
                "source_rows": len(ordered),
                "source_first_time": (
                    str(ordered.index[0]) if len(ordered) else None
                ),
                "source_last_time": (
                    str(ordered.index[-1]) if len(ordered) else None
                ),
                "windows": window_rows,
                "issues": run_issues,
            }
        )

    active_candidates = [
        row for row in candidates if not bool(row.get("inactive", False))
    ]
    consensus = _build_consensus(active_candidates)
    conflicts = _build_conflicts(active_candidates)
    primary = _choose_primary(active_candidates, consensus, conflicts)
    successful_timeframes = [
        row for row in timeframe_runs if bool(row.get("data_ok"))
    ]

    if not active_candidates:
        overall_status = "FORMASYON_YOK"
    elif conflicts:
        overall_status = "ÇATIŞAN_YAPILAR"
    elif any(
        int(row.get("window_count", 0)) >= 2
        or int(row.get("timeframe_count", 0)) >= 2
        for row in consensus
    ):
        overall_status = "ÇOKLU_PENCERE_VEYA_VADE_UYUMU"
    else:
        overall_status = "TEK_PENCERE_ADAYI"

    return FormationV3Report(
        engine_version=ENGINE_VERSION,
        ticker=ticker,
        requested_timeframes=list(requested),
        data_ok=bool(successful_timeframes),
        data_issues=issues,
        timeframe_runs=timeframe_runs,
        candidates=candidates,
        consensus=consensus,
        conflicts=conflicts,
        overall_status=overall_status,
        primary_candidate=primary,
    )


def _default_parquet_path(root: Path, ticker: str, timeframe: str) -> Path:
    return root / "veriler" / f"{ticker}.IS_{timeframe}.parquet"


def _self_test() -> dict[str, Any]:
    dates = pd.date_range("2025-01-01", periods=180, freq="B")
    close = np.linspace(10.0, 14.0, len(dates))
    frame = pd.DataFrame(
        {
            "Open": close * 0.99,
            "High": close * 1.01,
            "Low": close * 0.98,
            "Close": close,
            "Volume": np.full(len(dates), 100_000.0),
        },
        index=dates,
    )
    report = analyze_formations_v3(frame, ticker="V3_SELFTEST", timeframe="1d")
    assert report.engine_version == ENGINE_VERSION
    assert report.data_ok is True
    assert report.requested_timeframes == ["1d"]
    assert report.timeframe_runs[0]["source_rows"] == 180
    return {
        "ok": True,
        "engine_version": ENGINE_VERSION,
        "status": report.overall_status,
        "windows": len(report.timeframe_runs[0]["windows"]),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Formasyon V3 araştırma motoru")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--ticker")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--parquet-1d", type=Path)
    parser.add_argument("--parquet-4h", type=Path)
    parser.add_argument(
        "--timeframe",
        choices=("1d", "4h", "both"),
        default="1d",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.self_test:
        print(json.dumps(_self_test(), ensure_ascii=False, indent=2))
        return 0

    if not args.ticker and not (args.parquet_1d or args.parquet_4h):
        parser.error("--ticker veya parquet yolu verilmelidir.")

    frames: dict[str, pd.DataFrame] = {}
    selected = ("1d", "4h") if args.timeframe == "both" else (args.timeframe,)
    for tf in selected:
        explicit = args.parquet_1d if tf == "1d" else args.parquet_4h
        path = explicit or (
            _default_parquet_path(args.root, args.ticker, tf)
            if args.ticker
            else None
        )
        if path is None or not path.exists():
            continue
        frames[tf] = pd.read_parquet(path)

    if not frames:
        parser.error("Seçilen vadeler için okunabilir parquet bulunamadı.")

    report = analyze_formations_v3(
        frames,
        ticker=args.ticker,
        timeframes=selected,
    )
    output = report_to_json(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0


__all__ = [
    "DEFAULT_LOOKBACK_WINDOWS",
    "ENGINE_VERSION",
    "FormationV3Report",
    "analyze_formations_v3",
    "render_v3_candidate_chart",
    "report_to_dict",
    "report_to_json",
]


if __name__ == "__main__":
    raise SystemExit(main())
