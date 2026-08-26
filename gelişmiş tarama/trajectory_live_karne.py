# -*- coding: utf-8 -*-
"""T+3 canlı sicili: aday yolculuğu gerçekten ek değer üretiyor mu?

Bu dosya salt-okurdur. Kapanış snapshot'ları ve olgunlaşan forward sonuçlarını
okur; yalnızca bu klasörde CSV/JSON raporu üretir. Tarama eşiğini değiştirmez,
rejimi filtre yapmaz ve patron.db/parquet yazmaz.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


HERE = Path(__file__).resolve().parent
SNAPSHOTS = HERE / "trajectory_forward_snapshots.csv"
OUTCOMES = HERE / "trajectory_forward_outcomes.csv"
REPORT_CSV = HERE / "trajectory_live_karne.csv"
REPORT_JSON = HERE / "trajectory_live_karne.json"
HORIZONS = (5, 10, 20)
MIN_EVALUATION_N = 30


def _num(series: pd.Series | object) -> pd.Series:
    if isinstance(series, pd.Series):
        return pd.to_numeric(series, errors="coerce")
    return pd.Series(dtype="float64")


def _event_key(frame: pd.DataFrame) -> pd.Series:
    return frame["symbol"].astype(str).str.replace(".IS", "", regex=False) + "|" + frame["event_start_date"].astype(str).str[:10]


def _latest_decisions(snapshots: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "event_start_date", "event_day"}
    if snapshots.empty or not required.issubset(snapshots.columns):
        return pd.DataFrame()
    work = snapshots.copy()
    if "feature_source" in work:
        work = work[work["feature_source"].astype(str) == "live_close"]
    work["event_day"] = _num(work["event_day"])
    work = work[work["event_day"] == 3].copy()
    if work.empty:
        return work
    work["_key"] = _event_key(work)
    work["_at"] = pd.to_datetime(work.get("snapshot_at"), errors="coerce")
    return work.sort_values("_at").drop_duplicates("_key", keep="last")


def _strong(frame: pd.DataFrame) -> pd.Series:
    return (_num(frame.get("trajectory_v1")) >= 3) | (_num(frame.get("cur_core")) >= 2)


def _missed_tail(outcomes: pd.DataFrame, decisions: pd.DataFrame, horizon: int = 20) -> dict[str, Any]:
    """Record the opportunity cost of the T+3 decision rule; never alter it here."""
    mature_col = f"outcome_{horizon}_mature"
    hit_col = f"hit30_hi_{horizon}"
    if decisions.empty or mature_col not in outcomes or hit_col not in outcomes:
        return {"status": "not_ready", "horizon": horizon}
    mature = outcomes[_num(outcomes[mature_col]).fillna(0) == 1].copy()
    if mature.empty:
        return {"status": "not_ready", "horizon": horizon}
    decisions = decisions.copy()
    decisions["_selected"] = _strong(decisions).astype(int)
    selected = decisions.set_index("_key")["_selected"].to_dict()
    mature["_selected"] = mature["_key"].map(selected)
    evaluated = mature[mature["_selected"].notna()].copy()
    tail = evaluated[_num(evaluated[hit_col]) == 1].copy()
    missed = tail[_num(tail["_selected"]) == 0].copy()
    visible = []
    for row in missed.sort_values(f"mfe_{horizon}", ascending=False).head(10).itertuples(index=False):
        visible.append({
            "symbol": str(getattr(row, "symbol")),
            "event_start_date": str(getattr(row, "event_start_date"))[:10],
            "mfe": round(float(getattr(row, f"mfe_{horizon}")), 2),
            "postret": round(float(getattr(row, f"postret_{horizon}")), 2),
        })
    return {
        "status": "available",
        "horizon": horizon,
        "evaluated_events": int(len(evaluated)),
        "tail_events": int(len(tail)),
        "missed_events": int(len(missed)),
        "missed_share": round(float(len(missed) / len(tail)), 4) if len(tail) else None,
        "examples": visible,
        "note": "Kacirilan kuyruk, karar esiginin firsat maliyetidir; tek basina esik degistirme gerekcesi degildir.",
    }


def _one_metric(group: pd.DataFrame, horizon: int, label: str, market_window: str) -> dict[str, Any] | None:
    mature_col = f"outcome_{horizon}_mature"
    ret_col = f"postret_{horizon}"
    alpha_col = f"alpha_xu100_{horizon}"
    xu_col = f"xu100_return_{horizon}"
    mfe_col = f"mfe_{horizon}"
    mae_col = f"mae_{horizon}"
    hit_col = f"hit30_hi_{horizon}"
    work = group.copy()
    if mature_col in work:
        work = work[_num(work[mature_col]).fillna(0) == 1]
    elif horizon == 20 and "postret" in work:  # Eski dosya sözleşmesiyle uyum.
        ret_col, alpha_col, mfe_col, mae_col, hit_col = "postret", "alpha_xu100", "mfe", "mae", "hit30_hi"
        work = work[_num(work[ret_col]).notna()]
    else:
        return None
    ret = _num(work.get(ret_col))
    work = work[ret.notna()].copy()
    ret = _num(work.get(ret_col))
    if xu_col in work:
        xu = _num(work.get(xu_col))
        if market_window == "zayıf_tape":
            work = work[xu < 0].copy()
        elif market_window == "güçlü_tape":
            work = work[xu >= 0].copy()
        ret = _num(work.get(ret_col))
    if work.empty:
        return None
    gains, losses = ret[ret > 0], ret[ret < 0]
    gross_gain, gross_loss = float(gains.sum()), float(abs(losses.sum()))
    n = int(len(work))
    return {
        "grup": label,
        "piyasa_penceresi": market_window,
        "vade_gun": horizon,
        "n": n,
        "win_rate": round(float((ret > 0).mean()), 4),
        "ortalama_getiri": round(float(ret.mean()), 4),
        "bist100_alpha": round(float(_num(work.get(alpha_col)).mean()), 4) if alpha_col in work else None,
        "ortalama_mfe": round(float(_num(work.get(mfe_col)).mean()), 4) if mfe_col in work else None,
        "ortalama_mae": round(float(_num(work.get(mae_col)).mean()), 4) if mae_col in work else None,
        "sag_kuyruk_30": round(float(_num(work.get(hit_col)).mean()), 4) if hit_col in work else None,
        "ortalama_kazanc": round(float(gains.mean()), 4) if len(gains) else None,
        "ortalama_kayip": round(float(abs(losses.mean())), 4) if len(losses) else None,
        "payoff_ratio": round(float(gains.mean() / abs(losses.mean())), 4) if len(gains) and len(losses) else None,
        "profit_factor": round(gross_gain / gross_loss, 4) if gross_loss else None,
        "olgunluk": "ölçülebilir" if n >= MIN_EVALUATION_N else "veri yetersiz",
    }


def build_live_karne(snapshots: pd.DataFrame, outcomes: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Tüm T0 havuzu ile T+3'te öne çıkan grubun ayrı, ileriye dönük sicilini kurar."""
    if outcomes.empty or not {"symbol", "event_start_date"}.issubset(outcomes.columns):
        return pd.DataFrame(), {"durum": "Henüz olgunlaşmış sonuç yok.", "mature_counts": {}}
    outcomes = outcomes.copy()
    outcomes["_key"] = _event_key(outcomes)
    decisions = _latest_decisions(snapshots)
    decision_map = decisions.set_index("_key") if not decisions.empty else pd.DataFrame()
    groups: dict[str, pd.DataFrame] = {"Tüm T0 havuzu": outcomes}
    if not decisions.empty:
        ready_keys = set(decisions.loc[_strong(decisions), "_key"])
        groups["T+3 karar hazır"] = outcomes[outcomes["_key"].isin(ready_keys)]
        for core in (0, 1, 2, 3):
            keys = set(decisions.loc[_num(decisions.get("cur_core")) == core, "_key"])
            groups[f"T+3 çekirdek = {core}"] = outcomes[outcomes["_key"].isin(keys)]
        component_rules = {
            "T+3 RSI hızı olumlu": _num(decisions.get("rsi_up")) >= 1,
            "T+3 BIST100 göreli güç olumlu": _num(decisions.get("rs_up")) >= 1,
            "T+3 ısrar ≥2 gün": _num(decisions.get("israr")) >= 2,
            "T+3 MA20 üstü": _num(decisions.get("ma20")) >= 1,
            "T+3 ATR hareketi olumlu": _num(decisions.get("atr_move")) >= 1,
        }
        for label, mask in component_rules.items():
            groups[label] = outcomes[outcomes["_key"].isin(set(decisions.loc[mask, "_key"]))]

    rows: list[dict[str, Any]] = []
    for label, group in groups.items():
        for horizon in HORIZONS:
            for market_window in ("tümü", "zayıf_tape", "güçlü_tape"):
                metric = _one_metric(group, horizon, label, market_window)
                if metric is not None:
                    rows.append(metric)
    report = pd.DataFrame(rows)
    mature_counts = {
        str(horizon): int((_num(outcomes.get(f"outcome_{horizon}_mature")).fillna(0) == 1).sum())
        for horizon in HORIZONS
    }
    ready_20 = report[(report.get("grup") == "T+3 karar hazır") & (report.get("vade_gun") == 20) & (report.get("piyasa_penceresi") == "tümü")]
    all_20 = report[(report.get("grup") == "Tüm T0 havuzu") & (report.get("vade_gun") == 20) & (report.get("piyasa_penceresi") == "tümü")]
    summary = {
        "asof": str(pd.Timestamp.now(tz="Europe/Istanbul").date()),
        "mature_counts": mature_counts,
        "decision_events": int(len(decisions)),
        "rule_status": "Yeterli canlı örnek oluşmadan eşik/ağırlık değiştirilemez.",
        "ready_vs_all_20": {
            "karar_hazir": ready_20.to_dict(orient="records"),
            "tum_havuz": all_20.to_dict(orient="records"),
        },
        "missed_right_tail_20": _missed_tail(outcomes, decisions, 20),
    }
    return report, summary


def write_live_karne(*, dry_run: bool = False) -> dict[str, Any]:
    snapshots = pd.read_csv(SNAPSHOTS) if SNAPSHOTS.exists() else pd.DataFrame()
    outcomes = pd.read_csv(OUTCOMES) if OUTCOMES.exists() else pd.DataFrame()
    report, summary = build_live_karne(snapshots, outcomes)
    if not dry_run:
        report.to_csv(REPORT_CSV, index=False, encoding="utf-8-sig")
        REPORT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"report_rows": int(len(report)), "summary": summary, "output": str(REPORT_CSV)}


def main() -> int:
    parser = argparse.ArgumentParser(description="T+3 canlı sicil / seçilen-vs-tüm-havuz raporu")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(write_live_karne(dry_run=args.dry_run), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
