"""Gold Mine sabit-vade puan laboratuvarı.

Bu araç canlı puanlayıcı değildir. Mühürlü Değişken Vade Aşama 1
çıktısını yeniden yorumlar; app.py, smr_core.py, veritabanı veya VPS'e
yazmaz. Amaç, ideal-gün aramasını ve gün başına getiri formülünü tamamen
dışarıda bırakan kapının bugün hangi taramalardan geçebileceğini saymaktır.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INPUT_DEFAULT = Path("logs/degisken_vade_asama1.json")
JSON_DEFAULT = Path("logs/goldmine_fixed_horizon_lab.json")
MARKDOWN_DEFAULT = Path("logs/goldmine_fixed_horizon_lab.md")

HORIZONS = (3, 5, 20)
REGIMES = ("YUKSELEN", "DUSEN")
MIN_REGIME_N = 150


def _fail(message: str) -> None:
    raise ValueError(f"Mühür doğrulaması geçmedi: {message}")


def _validate_sealed_input(payload: dict[str, Any]) -> None:
    """Laboratuvarın yalnız kabul edilmiş cetvelden okuduğunu doğrular."""
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        _fail("meta bölümü yok")
    if meta.get("entry_rule") != (
        "resolve_next_open_entry(apply_bist_limit=True,max_locked_sessions=3)"
    ):
        _fail("giriş cetveli ertesi-açılış + tavan-kilidi değil")
    if meta.get("regime_rule") != "XU100_CLOSE_VS_SMA50":
        _fail("rejim XU100 / SMA50 değil")
    if meta.get("dedup_rule") != "is_event_start=1; unique(scan_date,scan_type,symbol)":
        _fail("olay tekrarı temizleme kuralı değişmiş")
    if meta.get("ideal_day_used") is not False:
        _fail("ideal gün araması veri setinde açık")
    if tuple(meta.get("horizon_sessions", ())) != tuple(range(1, 21)):
        _fail("1–20 seans yolu eksik")
    if tuple(meta.get("regimes", ())) != ("YUKSELEN", "DUSEN"):
        _fail("iki mühürlü rejim bulunmuyor")
    if meta.get("universe_baseline_rule") != (
        "all stock paths with matching XU100 session; close_alpha median"
    ):
        _fail("evren tabanı tanımı değişmiş")


def _day_row(regime_payload: dict[str, Any], horizon: int) -> dict[str, Any]:
    for row in regime_payload.get("days", []):
        if row.get("day") == horizon:
            return row
    _fail(f"T+{horizon} satırı yok")


def _stat(row: dict[str, Any]) -> dict[str, Any]:
    """Bir rejim-hücreyi rapor için açık alanlara indirger."""
    return {
        "missing_regime_curve": False,
        "n": row.get("n_close_alpha"),
        "scanner_median_alpha": row.get("close_alpha"),
        "universe_median_alpha": row.get("universe_close_alpha_median"),
        "median_vs_universe": row.get("close_alpha_vs_universe"),
        "mean_alpha": row.get("close_alpha_mean"),
        "win_rate_pct": row.get("close_alpha_win_rate"),
    }


def _missing_regime_stat() -> dict[str, Any]:
    """Kaynak eğride bulunmayan rejimi sıfır değil, açık eksik olarak taşır."""
    return {
        "missing_regime_curve": True,
        "n": None,
        "scanner_median_alpha": None,
        "universe_median_alpha": None,
        "median_vs_universe": None,
        "mean_alpha": None,
        "win_rate_pct": None,
    }


def _classify(by_regime: dict[str, dict[str, Any]]) -> tuple[str, float | None, list[str]]:
    """Önceden yazılmış, parametresiz sabit-vade kapısı.

    Puan yüzde-puan cinsinden iki rejimdeki daha zayıf ortanca evren
    üstünlüğüdür. 0–100 dönüştürmesi yoktur; böylece zaman veya kısa vade
    ödüllendirilmez. Her iki rejim de N>=150 ve tabandan pozitif değilse
    puan kesinlikle üretilmez.
    """
    reasons: list[str] = []
    deltas: list[float] = []
    for regime in REGIMES:
        stat = by_regime[regime]
        n = stat["n"]
        delta = stat["median_vs_universe"]
        if stat["missing_regime_curve"]:
            reasons.append(f"{regime}: rejim eğrisi yok")
            continue
        if not isinstance(n, (int, float)) or n < MIN_REGIME_N:
            reasons.append(f"{regime}: N<{MIN_REGIME_N}")
            continue
        if not isinstance(delta, (int, float)):
            reasons.append(f"{regime}: taban farkı yok")
            continue
        if delta <= 0:
            reasons.append(f"{regime}: evren tabanını aşmıyor")
            continue
        deltas.append(float(delta))
    if reasons:
        return "PUAN_YOK", None, reasons
    return "PUAN_URETIR", min(deltas), []


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    _validate_sealed_input(payload)
    curves = payload.get("curves")
    if not isinstance(curves, dict):
        _fail("tarama eğrileri yok")

    rows: list[dict[str, Any]] = []
    for scanner in sorted(curves):
        scanner_curve = curves[scanner]
        if not isinstance(scanner_curve, dict):
            _fail(f"{scanner}: rejim eğrisi sözlük değil")
        for horizon in HORIZONS:
            by_regime = {
                regime: (
                    _stat(_day_row(scanner_curve[regime], horizon))
                    if regime in scanner_curve
                    else _missing_regime_stat()
                )
                for regime in REGIMES
            }
            status, score, reasons = _classify(by_regime)
            rows.append(
                {
                    "scanner": scanner,
                    "horizon_sessions": horizon,
                    "status": status,
                    "conservative_baseline_advantage_pp": score,
                    "reasons": reasons,
                    "regimes": by_regime,
                }
            )

    qualified = [row for row in rows if row["status"] == "PUAN_URETIR"]
    return {
        "meta": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": str(INPUT_DEFAULT),
            "source_active_version": payload["meta"].get("active_version"),
            "entry_rule": payload["meta"]["entry_rule"],
            "regime_rule": payload["meta"]["regime_rule"],
            "dedup_rule": payload["meta"]["dedup_rule"],
            "ideal_day_used": False,
            "horizons": list(HORIZONS),
            "min_regime_n": MIN_REGIME_N,
            "baseline_metric": "close_alpha median minus same-day universe close_alpha median",
            "score_definition": (
                "Only when both regimes have N>=150 and positive median advantage; "
                "score is the smaller of the two regime advantages, in percentage points."
            ),
            "no_0_100_mapping": True,
            "no_per_day_division": True,
        },
        "summary": {
            "scanner_count": len(curves),
            "scanner_horizon_cells": len(rows),
            "qualified_cells": len(qualified),
            "qualified_scanners": sorted({row["scanner"] for row in qualified}),
            "qualified_by_horizon": {
                f"T+{horizon}": sum(
                    1
                    for row in qualified
                    if row["horizon_sessions"] == horizon
                )
                for horizon in HORIZONS
            },
        },
        "rows": rows,
    }


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value:+.{digits}f}"
    return str(value)


def to_markdown(result: dict[str, Any]) -> str:
    meta = result["meta"]
    summary = result["summary"]
    lines = [
        "# Gold Mine — Sabit Vade Puan Laboratuvarı",
        "",
        "Bu rapor laboratuvar ölçümüdür; canlı puanlayıcıyı, botu, ekranı veya VPS'i değiştirmez.",
        "",
        "## Önceden Yazılmış Tanım",
        "",
        "- Giriş: ertesi işlem yapılabilir açılış + en fazla üç tavan-kilitli seansı atlama.",
        "- Tekrar: yalnız olay başlangıcı; aynı tarih–tarama–sembol bir kez sayılır.",
        "- Rejim: XU100 kapanışı 50 günlük ortalamanın üstü/altı.",
        "- Vade: yalnız T+3, T+5 ve T+20; ideal gün aranmaz.",
        "- Karşılaştırma: her rejimdeki ortanca XU100 alfadan, aynı günlerin evren ortancası çıkarılır.",
        f"- Kapı: iki rejimde de N ≥ {MIN_REGIME_N} ve taban farkı pozitif olmalı.",
        "- Puan: yalnız kapıyı geçen hücrede, iki rejimin daha küçük taban üstünlüğü; birimi yüzde puandır. 0–100 dönüşümü ve güne bölme yoktur.",
        "- Kapıyı geçmeyen hücre: BELİRSİZ veya taban altı; puan üretilmez.",
        "",
        "## Sonuç",
        "",
        f"- Tarama: {summary['scanner_count']} · tarama×vade hücresi: {summary['scanner_horizon_cells']}",
        f"- Puan üreten hücre: {summary['qualified_cells']} · benzersiz tarama: {len(summary['qualified_scanners'])}",
        "- Vadeye göre: " + " · ".join(
            f"{horizon}: {count}"
            for horizon, count in summary["qualified_by_horizon"].items()
        ),
        "",
        "## Tam Liste",
        "",
        "| Tarama | Vade | Yükselen N | Yükselen taban farkı | Düşen N | Düşen taban farkı | Durum | Temkinli puan | Neden |",
        "|---|---:|---:|---:|---:|---:|---|---:|---|",
    ]
    for row in result["rows"]:
        up = row["regimes"]["YUKSELEN"]
        down = row["regimes"]["DUSEN"]
        score = row["conservative_baseline_advantage_pp"]
        reason = "; ".join(row["reasons"]) if row["reasons"] else "iki rejim de geçti"
        lines.append(
            "| {scanner} | T+{horizon} | {up_n} | {up_delta} | {down_n} | {down_delta} | {status} | {score} | {reason} |".format(
                scanner=row["scanner"],
                horizon=row["horizon_sessions"],
                up_n=up["n"] if up["n"] is not None else "—",
                up_delta=_fmt(up["median_vs_universe"]),
                down_n=down["n"] if down["n"] is not None else "—",
                down_delta=_fmt(down["median_vs_universe"]),
                status=row["status"],
                score=_fmt(score),
                reason=reason,
            )
        )
    lines.extend(
        [
            "",
            "## Mühür Kaydı",
            "",
            f"- Kaynak veri sürümü: `{meta['source_active_version']}`",
            f"- Giriş cetveli: `{meta['entry_rule']}`",
            f"- Rejim: `{meta['regime_rule']}`",
            f"- İdeal gün: `{meta['ideal_day_used']}`",
            f"- Güne bölme: `{meta['no_per_day_division']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT_DEFAULT)
    parser.add_argument("--json-out", type=Path, default=JSON_DEFAULT)
    parser.add_argument("--markdown-out", type=Path, default=MARKDOWN_DEFAULT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Kaynak laboratuvar çıktısı yok: {args.input}")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = evaluate(payload)
    if len(result["rows"]) != result["summary"]["scanner_count"] * len(HORIZONS):
        _fail("tarama×vade satır sayısı tutmuyor")
    if args.self_test:
        print(
            "SELF-TEST OK | "
            f"scanners={result['summary']['scanner_count']} "
            f"cells={len(result['rows'])} "
            f"qualified={result['summary']['qualified_cells']}"
        )
        return 0

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.markdown_out.write_text(to_markdown(result), encoding="utf-8")
    print(
        "GOLDMINE FIXED-HORIZON LAB OK | "
        f"scanners={result['summary']['scanner_count']} "
        f"cells={len(result['rows'])} "
        f"qualified={result['summary']['qualified_cells']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
