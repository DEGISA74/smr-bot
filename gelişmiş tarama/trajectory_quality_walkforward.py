# -*- coding: utf-8 -*-
"""T+3 quality research -- report-only, no scanner thresholds are changed.

The existing trajectory reports answer "did the score grow?".  This companion
report asks four deliberately narrower questions with the same event unit
(symbol + T0):

* Was the T0-to-T+3 move healthy relative to the stock's own volatility?
* Did persistence add anything independently?
* Does the present T+3 decision proxy improve the right-tail base rate?
* Which +30% outcomes would a strict decision proxy have missed?

All price movement used for a decision ends on T+3; every result begins at
T+4.  The script only reads the two pre-existing research CSVs and writes
report files next to itself.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
V3_PATH = HERE / "trajectory_v3_events.csv"
V1_PATH = HERE / "trajectory_events.csv"
REPORT_CSV = HERE / "trajectory_quality_walkforward.csv"
REPORT_JSON = HERE / "trajectory_quality_walkforward.json"
MISSED_CSV = HERE / "trajectory_quality_missed_tail.csv"

TARGET = 30.0
MIN_GROUP_N = 25


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame.get(column), errors="coerce")


def _metrics(frame: pd.DataFrame) -> dict[str, Any] | None:
    if frame.empty:
        return None
    hit = _num(frame, "hit30_hi")
    ret = _num(frame, "postret")
    alpha = _num(frame, "alpha")
    mfe = _num(frame, "mfe")
    mae = _num(frame, "mae")
    wins = ret[ret > 0]
    losses = ret[ret < 0]
    gross_loss = float(abs(losses.sum()))
    return {
        "n": int(len(frame)),
        "unique_symbols": int(frame["symbol"].nunique()),
        "hit30": round(float(hit.mean()), 4),
        "clean30": round(float(_num(frame, "clean30").mean()), 4),
        "mean_return": round(float(ret.mean()), 4),
        "mean_alpha": round(float(alpha.mean()), 4),
        "mean_mfe": round(float(mfe.mean()), 4),
        "mean_mae": round(float(mae.mean()), 4),
        "win_rate": round(float((ret > 0).mean()), 4),
        "payoff_ratio": round(float(wins.mean() / abs(losses.mean())), 4) if len(wins) and len(losses) else None,
        "profit_factor": round(float(wins.sum() / gross_loss), 4) if gross_loss else None,
    }


def _append(rows: list[dict[str, Any]], frame: pd.DataFrame, *, family: str, group: str,
            month: str = "pooled", evaluation: str = "descriptive") -> None:
    metric = _metrics(frame)
    if metric is None:
        return
    rows.append({
        "family": family,
        "group": group,
        "month": month,
        "evaluation": evaluation,
        "sample_status": "usable" if metric["n"] >= MIN_GROUP_N else "small_n",
        **metric,
    })


def load_events(v3_path: Path = V3_PATH, v1_path: Path = V1_PATH) -> pd.DataFrame:
    """Join only the one-to-one historical event rows needed for this audit."""
    v3 = pd.read_csv(v3_path)
    v1 = pd.read_csv(v1_path)
    keys = ["symbol", "T0"]
    for frame in (v3, v1):
        frame["symbol"] = frame["symbol"].astype(str).str.replace(".IS", "", regex=False).str.strip()
        frame["T0"] = frame["T0"].astype(str).str[:10]
        frame.drop_duplicates(keys, keep="last", inplace=True)
    required = ["symbol", "T0", "month", "v1", "v2", "postret", "alpha", "mfe", "mae", "hit30_hi", "clean30"]
    missing = [col for col in required if col not in v3]
    if missing:
        raise ValueError(f"trajectory_v3_events.csv eksik kolon: {', '.join(missing)}")
    need_v1 = ["symbol", "T0", "persistence", "conf_move", "vol20"]
    missing = [col for col in need_v1 if col not in v1]
    if missing:
        raise ValueError(f"trajectory_events.csv eksik kolon: {', '.join(missing)}")
    merged = v3.merge(v1[need_v1], on=keys, how="inner", validate="one_to_one")
    merged["vol20"] = _num(merged, "vol20")
    merged["conf_move"] = _num(merged, "conf_move")
    merged["speed_vs_vol"] = merged["conf_move"] / merged["vol20"].where(merged["vol20"] > 0)
    merged["cur_core_proxy"] = (
        (_num(merged, "c_rs").fillna(0) > 0).astype(int)
        + (_num(merged, "c_ma20").fillna(0) > 0).astype(int)
        + (_num(merged, "c_atr_move").fillna(0) > 0).astype(int)
    )
    merged["decision_proxy"] = ((_num(merged, "v1") >= 3) | (merged["cur_core_proxy"] >= 2)).astype(int)
    merged["persistence_group"] = np.select(
        [_num(merged, "persistence") <= 0, _num(merged, "persistence") == 1],
        ["0 gun", "1 gun"], default="2+ gun",
    )
    merged["month"] = merged["month"].astype(str)
    return merged


def _pooled_speed_groups(events: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    out = events.copy()
    usable = _num(out, "speed_vs_vol").dropna()
    if len(usable) < 3:
        out["speed_group"] = "hesaplanamadi"
        return out, {}
    low, high = usable.quantile([1 / 3, 2 / 3]).tolist()
    out["speed_group"] = np.select(
        [_num(out, "speed_vs_vol") <= low, _num(out, "speed_vs_vol") > high],
        ["yavas", "hizli"], default="normal",
    )
    out.loc[_num(out, "speed_vs_vol").isna(), "speed_group"] = "hesaplanamadi"
    return out, {"low": round(float(low), 4), "high": round(float(high), 4)}


def _walk_forward_speed(events: pd.DataFrame) -> list[dict[str, Any]]:
    """Test-month bands use only earlier months' thresholds; no live rule is inferred."""
    rows: list[dict[str, Any]] = []
    months = sorted(events["month"].dropna().unique())
    for month in months:
        train = events[events["month"] < month]
        test = events[events["month"] == month].copy()
        usable = _num(train, "speed_vs_vol").dropna()
        if len(usable) < MIN_GROUP_N or test.empty:
            continue
        low, high = usable.quantile([1 / 3, 2 / 3]).tolist()
        test["speed_group"] = np.select(
            [_num(test, "speed_vs_vol") <= low, _num(test, "speed_vs_vol") > high],
            ["yavas", "hizli"], default="normal",
        )
        _append(rows, test, family="speed_walk_forward", group="tum test havuzu", month=month,
                evaluation="train_locked")
        for group in ("yavas", "normal", "hizli"):
            _append(rows, test[test["speed_group"] == group], family="speed_walk_forward", group=group,
                    month=month, evaluation="train_locked")
    return rows


def build_report(events: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    events, speed_cutoffs = _pooled_speed_groups(events)
    rows: list[dict[str, Any]] = []
    _append(rows, events, family="baseline", group="tum T0 havuzu")
    for group in ("yavas", "normal", "hizli", "hesaplanamadi"):
        _append(rows, events[events["speed_group"] == group], family="speed_descriptive", group=group)
    for group in ("0 gun", "1 gun", "2+ gun"):
        _append(rows, events[events["persistence_group"] == group], family="persistence", group=group)
    _append(rows, events[events["decision_proxy"] == 1], family="selection_proxy", group="T+3 karar hazir proxy")
    _append(rows, events[events["decision_proxy"] == 0], family="selection_proxy", group="T+3 izleme proxy")
    for month in sorted(events["month"].unique()):
        test = events[events["month"] == month]
        _append(rows, test, family="selection_monthly", group="tum T0 havuzu", month=month, evaluation="out_of_sample_month")
        _append(rows, test[test["decision_proxy"] == 1], family="selection_monthly", group="T+3 karar hazir proxy", month=month, evaluation="out_of_sample_month")
    rows.extend(_walk_forward_speed(events))
    report = pd.DataFrame(rows)

    tail = events[_num(events, "hit30_hi") == 1].copy()
    missed = tail[tail["decision_proxy"] == 0].copy()
    missed = missed.sort_values(["mfe", "postret"], ascending=False)
    missed_columns = [
        "symbol", "T0", "month", "mfe", "postret", "alpha", "persistence", "conf_move", "vol20",
        "speed_vs_vol", "v1", "cur_core_proxy", "decision_proxy", "rsi_slope", "rs_slope",
    ]
    missed = missed[[col for col in missed_columns if col in missed.columns]]
    base = _metrics(events) or {}
    selected = _metrics(events[events["decision_proxy"] == 1]) or {}
    summary = {
        "status": "research_only",
        "observation_unit": "unique symbol + T0",
        "decision_timing": "T+3 kapanis; sonuc T+4 acilis sonrasi 20 islem gunu",
        "events_joined": int(len(events)),
        "months": sorted(events["month"].unique().tolist()),
        "speed_vs_vol_cutoffs_descriptive": speed_cutoffs,
        "baseline": base,
        "decision_proxy": selected,
        "missed_right_tail": {
            "tail_events": int(len(tail)),
            "missed_events": int(len(missed)),
            "missed_share": round(float(len(missed) / len(tail)), 4) if len(tail) else None,
            "note": "Bu, mevcut T+3 proxy esiginin firsat maliyetidir; tek basina esik degistirme emri degildir.",
        },
        "guardrail": "Hicbir bulgu otomatik tarama esigi veya agirligi degistirmez. En az farkli rejimlerde ileri donuk tekrar aranir.",
    }
    return report, summary, missed


def write_report(*, dry_run: bool = False) -> dict[str, Any]:
    events = load_events()
    report, summary, missed = build_report(events)
    if not dry_run:
        report.to_csv(REPORT_CSV, index=False, encoding="utf-8-sig")
        missed.to_csv(MISSED_CSV, index=False, encoding="utf-8-sig")
        REPORT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"report_rows": int(len(report)), "missed_tail_rows": int(len(missed)), "summary": summary}


def main() -> int:
    result = write_report()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
