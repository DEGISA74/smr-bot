# -*- coding: utf-8 -*-
"""Formasyon V2 ile Streamlit görünümü arasındaki güvenli uyum katmanı.

Bu modül yalnızca tek-hisse Formasyon Grafiği için sunum verisi hazırlar.
Master Scan, Canlı Sinyaller, AI promptları ve eski formasyon taramasını değiştirmez.
"""

from __future__ import annotations

import base64
import math
import tempfile
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from formasyon_v2 import analyze_formations, render_candidate_chart


V2_PATTERN_LABELS = {
    "YÜKSELEN_ÜÇGEN": "Yükselen Üçgen",
    "ALÇALAN_ÜÇGEN": "Alçalan Üçgen",
    "SİMETRİK_ÜÇGEN": "Simetrik Üçgen",
    "YÜKSELEN_KAMA": "Yükselen Kama",
    "ALÇALAN_KAMA": "Alçalan Kama",
    "FİNCAN_KULP": "Fincan-Kulp",
    "TOBO": "TOBO",
    "OBO": "OBO",
}

_OLD_DIRECT_TYPES = {"cup", "tobo", "triangle", "dtriangle"}
_OLD_COMBINED_SHAPES = {"fincan", "tobo", "ucgen", "dtri"}

_STAGE_INFO = {
    "KULP_BEKLENİYOR": (1, "Kulp bekleniyor"),
    "OLUŞUYOR": (2, "Yapı oluşuyor"),
    "YAKIN": (3, "Kritik çizgiye yaklaşıyor"),
    "KIRILIM_ADAYI": (4, "Kırılım adayı — teyit bekleniyor"),
    "KIRILIM_DOĞRULANDI": (5, "Kırılım doğrulandı"),
    "YENİDEN_TEST": (5, "Kırılan çizgi yeniden test ediliyor"),
}


def old_chart_is_v2_scope(chart_data: Any) -> bool:
    """Eski grafiğin V2'nin yetkili olduğu beşli aileye ait olup olmadığını söyler."""
    if not isinstance(chart_data, dict):
        return False
    chart_type = str(chart_data.get("type", ""))
    if chart_type in _OLD_DIRECT_TYPES:
        return True
    return (
        chart_type == "birlesik"
        and str(chart_data.get("shape", "")) in _OLD_COMBINED_SHAPES
    )


def _finite_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _quality_band(score: float) -> tuple[str, str]:
    if score >= 90:
        return "YÜKSEK KALİTE", "#10b981"
    if score >= 80:
        return "ADAY — TEYİT GEREKİR", "#f59e0b"
    return "ERKEN ADAY", "#94a3b8"


def _story(pattern: str, trigger: float) -> str:
    price = f"{trigger:,.2f}"
    stories = {
        "YÜKSELEN_ÜÇGEN": (
            f"Tepeler yaklaşık <b>{price}</b> çizgisinde dururken dipler yükseliyor. "
            "Alıcılar her geri çekilmede daha yukarıdan devreye giriyor; şekli doğrulayan "
            "ana sınav üst çizginin kapanışla aşılması."
        ),
        "ALÇALAN_ÜÇGEN": (
            f"Dipler yaklaşık <b>{price}</b> desteğinde tutunurken tepeler alçalıyor. "
            "Satıcılar her denemede fiyatı daha aşağıdan karşılıyor; şekli doğrulayan ana "
            "sınav desteğin kapanışla aşağı kırılması."
        ),
        "FİNCAN_KULP": (
            f"Fiyat yuvarlak bir tabandan toparlanıp yeniden <b>{price}</b> ağız çizgisine "
            "geldi ve sağ tarafta daha küçük bir dinlenme alanı oluşturdu. Kulp tamamlanmadan "
            "ve ağız çizgisi aşılmadan yapı kesinleşmiş sayılmıyor."
        ),
        "SİMETRİK_ÜÇGEN": (
            f"Tepeler alçalırken dipler yükseliyor; iki çizgi yaklaşık <b>{price}</b> civarında "
            "bir tepe noktasına doğru sıkışıyor. Yön nötr — asıl sinyal, fiyatın hangi çizgiyi "
            "kapanışla kırdığıdır; kırdığı yöne doğru hareket beklenir."
        ),
        "ALÇALAN_KAMA": (
            f"Hem tepeler hem dipler alçalıyor ama tepeler daha hızlı iniyor; çizgiler "
            f"<b>{price}</b> üst sınırına doğru sıkışıyor. Düşüş yavaşlıyor — bu bir boğa "
            "(yukarı dönüş) adayıdır. Üst çizgi kapanışla aşılmadan yalnızca şekil vardır."
        ),
        "YÜKSELEN_KAMA": (
            f"Hem tepeler hem dipler yükseliyor ama dipler daha hızlı çıkıyor; çizgiler "
            f"<b>{price}</b> alt sınırına doğru sıkışıyor. Yükseliş tıkanıyor — bu bir ayı "
            "(aşağı dönüş) adayıdır. Alt çizgi kapanışla kırılmadan yalnızca şekil vardır."
        ),
        "TOBO": (
            f"Ortadaki dip iki omuzdan daha aşağıda; boyun çizgisi yaklaşık <b>{price}</b>. "
            "Bu bir dip dönüşü adayıdır. Boyun aşılmadan yalnızca şekil vardır, teyitli dönüş yoktur."
        ),
        "OBO": (
            f"Ortadaki tepe iki omuzdan daha yukarıda; boyun çizgisi yaklaşık <b>{price}</b>. "
            "Bu bir tepe dönüşü adayıdır. Boyun aşağı kırılmadan yalnızca şekil vardır, teyitli düşüş yoktur."
        ),
    }
    return stories.get(pattern, "Formasyonun ana temasları ve kırılım çizgisi aynı motor tarafından ölçüldü.")


def _conclusion(
    direction: str,
    stage: str,
    trigger: float,
    invalidation: float,
    target: Optional[float],
) -> str:
    bullish = direction == "bullish"
    trigger_text = f"{trigger:,.2f}"
    invalid_text = f"{invalidation:,.2f}"
    break_text = (
        f"<b>{trigger_text}</b> üzerinde kapanış"
        if bullish
        else f"<b>{trigger_text}</b> altında kapanış"
    )
    invalid_text_full = (
        f"<b>{invalid_text}</b> altına kapanış"
        if bullish
        else f"<b>{invalid_text}</b> üzerine kapanış"
    )

    if stage in {"KIRILIM_DOĞRULANDI", "YENİDEN_TEST"}:
        target_text = (
            f" Ölçülü hedef <b>{target:,.2f}</b>."
            if target is not None
            else ""
        )
        retest_text = (
            " Şimdi kırılan çizginin destek/direnç olarak çalışıp çalışmadığı izlenmeli."
            if stage == "YENİDEN_TEST"
            else ""
        )
        return (
            f"{break_text} geldi; kırılım motor tarafından doğrulandı.{target_text}"
            f"{retest_text} {invalid_text_full} yapıyı geçersiz kılar."
        )

    if stage == "KIRILIM_ADAYI":
        return (
            f"Fiyat çizgiyi zorluyor fakat teyit tamamlanmadı. {break_text} ve devamlılık "
            f"görülmeden kesin sinyal sayılmamalı. {invalid_text_full} yapıyı bozar."
        )

    if stage == "YAKIN":
        return (
            f"Fiyat kritik çizgiye yaklaştı. {break_text} gelmeden formasyon aktif değil. "
            f"{invalid_text_full} yapıyı geçersiz kılar."
        )

    return (
        f"Yapı oluşuyor; henüz işlem teyidi yok. Önce {break_text} beklenmeli. "
        f"{invalid_text_full} formasyonu geçersiz kılar."
    )


def build_v2_view(
    df: pd.DataFrame,
    ticker: str,
    timeframe: str = "1d",
) -> dict[str, Any]:
    """Tek ticker için en öncelikli aktif V2 formasyonunu UI sözlüğüne çevirir."""
    try:
        report = analyze_formations(
            df,
            ticker=ticker,
            timeframe=timeframe,
            max_results=8,
        )
    except Exception as exc:
        return {
            "available": False,
            "data_ok": False,
            "issues": [f"V2 analiz hatası: {exc}"],
        }

    if not report.data_ok or not report.patterns:
        return {
            "available": False,
            "data_ok": bool(report.data_ok),
            "issues": list(report.data_issues),
            "engine_version": report.engine_version,
        }

    candidate = report.patterns[0]
    if candidate.pattern not in V2_PATTERN_LABELS:
        return {
            "available": False,
            "data_ok": True,
            "issues": ["Aktif formasyon V2 görünüm kapsamı dışında."],
            "engine_version": report.engine_version,
        }

    chart_b64 = ""
    try:
        with tempfile.TemporaryDirectory(prefix="smr_formasyon_v2_") as temp_dir:
            chart_path = Path(temp_dir) / "formasyon_v2.png"
            render_candidate_chart(
                df,
                candidate,
                chart_path,
                ticker=ticker,
                timeframe=timeframe,
            )
            chart_b64 = base64.b64encode(chart_path.read_bytes()).decode("ascii")
    except Exception as exc:
        report.data_issues.append(f"V2 grafik üretilemedi: {exc}")

    current_price = _finite_float(df["Close"].iloc[-1]) if df is not None and not df.empty else None
    target = _finite_float(candidate.target)
    trigger = _finite_float(candidate.trigger) or 0.0
    invalidation = _finite_float(candidate.invalidation) or 0.0
    quality = float(candidate.quality_score)
    quality_label, quality_color = _quality_band(quality)
    stage_level, stage_label = _STAGE_INFO.get(candidate.stage, (2, candidate.stage))

    risk_reward = None
    if current_price and target is not None:
        risk = abs(invalidation - current_price)
        reward = abs(target - current_price)
        if risk > 0:
            risk_reward = reward / risk

    return {
        "available": True,
        "engine_version": report.engine_version,
        "timeframe": timeframe,
        "pattern": candidate.pattern,
        "pattern_label": V2_PATTERN_LABELS[candidate.pattern],
        "direction": candidate.direction,
        "stage": candidate.stage,
        "stage_level": stage_level,
        "stage_total": 5,
        "stage_label": stage_label,
        "quality_score": quality,
        "quality_label": quality_label,
        "quality_color": quality_color,
        "trigger": trigger,
        "invalidation": invalidation,
        "target": target,
        "current_price": current_price,
        "risk_reward": risk_reward,
        "start_time": candidate.start_time,
        "end_time": candidate.end_time,
        "breakout_time": candidate.breakout_time,
        "chart_b64": chart_b64,
        "story": _story(candidate.pattern, trigger),
        "conclusion": _conclusion(
            candidate.direction,
            candidate.stage,
            trigger,
            invalidation,
            target,
        ),
        "issues": list(report.data_issues),
        "notes": list(candidate.notes),
        "checks": dict(candidate.checks),
    }
