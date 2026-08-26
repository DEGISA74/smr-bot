#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V1 ve V2 yüksek hareket motorlarının sızıntısız iki aylık karşılaştırması."""

from __future__ import annotations

import argparse
import json
import math
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd


EXCLUDED = {"XU100", "XU030", "XU050", "XBANK", "XUSIN", "XUMAL"}
HORIZONS = (1, 2, 3, 4, 5)
TARGET_PCT = 5.0


def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    idx = pd.to_datetime(out.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def _wilson(hits: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return math.nan, math.nan
    z = 1.959963984540054
    p = hits / total
    den = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / den
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total) / den
    return 100.0 * max(0.0, center - margin), 100.0 * min(1.0, center + margin)


def _safe_float(value) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return float(value)


def load_prices(data_dir: Path) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    print("Fiyat arşivi okunuyor...", flush=True)
    market = _normalise(pd.read_parquet(data_dir / "XU100.IS_1d.parquet"))
    prices: dict[str, pd.DataFrame] = {}
    for number, path in enumerate(sorted(data_dir.glob("*.IS_1d.parquet")), start=1):
        ticker = path.name.replace(".IS_1d.parquet", "")
        if ticker in EXCLUDED:
            continue
        try:
            frame = _normalise(pd.read_parquet(path))
            if len(frame) >= 80:
                prices[ticker] = frame
        except Exception:
            continue
        if number % 150 == 0:
            print(f"  {number} dosya okundu...", flush=True)
    print(f"Fiyat arşivi hazır: {len(prices)} hisse.", flush=True)
    return prices, market


def v1_scans(
    signal_dates: list[pd.Timestamp],
    prices: dict[str, pd.DataFrame],
    market: pd.DataFrame,
    live,
) -> pd.DataFrame:
    rows: list[dict] = []
    for day_no, date in enumerate(signal_dates, start=1):
        if date not in market.index:
            continue
        market_pos = int(market.index.get_loc(date))
        close = market["Close"]
        if market_pos < 10:
            regime, market_change = "BILINMEZ", 0.0
        else:
            market_change = (float(close.iloc[market_pos]) / float(close.iloc[market_pos - 10]) - 1.0) * 100.0
            regime = (
                "HIZLI_RALLI" if market_change >= 5.0 else
                "ILIMLI_YUKARI" if market_change >= 2.0 else
                "YATAY" if market_change >= -2.0 else
                "ZAYIF" if market_change >= -5.0 else "DUSUS"
            )
        weights = live._TAV_REJIM_AGIRLIK[regime]
        for ticker, frame in prices.items():
            if date not in frame.index:
                continue
            pos = int(frame.index.get_loc(date))
            try:
                feature = live._tav_features(frame, pos)
                if feature is None or float(feature["vol_tl"]) < 2_000_000:
                    continue
                if live._liquidity_manip(frame.iloc[: pos + 1]).get("manip") == "yüksek":
                    continue
                score_a = live._tav_score_A(feature) * weights["A"]
                score_c = live._tav_score_C(feature) * weights["C"]
                score_e = live._tav_score_E(feature) * weights["E"]
                score_d = live._tav_score_D(feature) * weights["D"]
                boost_a = boost_c = boost_e = boost_d = 0.0
                if pd.notna(feature["pct_T"]) and pd.notna(feature["vol_T"]):
                    if feature["pct_T"] > 2 and feature["vol_T"] > 1.2:
                        boost_a += 12; boost_e += 18; boost_c += 6
                    elif feature["pct_T"] > 1:
                        boost_a += 6; boost_e += 9; boost_c += 3
                    elif feature["pct_T"] < -3 and feature["vol_T"] < 0.7:
                        boost_d += 15
                if pd.notna(feature["vol_5g_slope"]):
                    if feature["vol_5g_slope"] > 0.5:
                        boost_a += 8; boost_e += 10; boost_c += 8
                    elif feature["vol_5g_slope"] > 0.2:
                        boost_a += 4; boost_e += 5; boost_c += 4
                if feature["is_doji"]:
                    boost_c += 12
                if feature["is_green"] and feature["body_pct"] > 60:
                    boost_a += 8; boost_e += 10
                if feature["is_hammer"]:
                    boost_d += 10
                if pd.notna(feature["ret_5g"]):
                    if feature["ret_5g"] > 10:
                        boost_a += 8
                    elif feature["ret_5g"] < -8:
                        boost_d += 8
                scores = {
                    "A": score_a + boost_a,
                    "C": score_c + boost_c,
                    "E": score_e + boost_e,
                    "D": score_d + boost_d,
                }
                ordered = sorted(scores.values(), reverse=True)
                confidence = max(0.0, ordered[1] - 30.0) * 0.6
                if ordered[2] > 30.0:
                    confidence += (ordered[2] - 30.0) * 0.3
                rows.append(
                    {
                        "date": date,
                        "ticker": ticker,
                        "v1_score": max(scores.values()) + confidence,
                        "v1_category": max(scores, key=scores.get),
                        "regime": regime,
                        "market_change_10d": market_change,
                    }
                )
            except Exception:
                continue
        print(f"V1 tarama {day_no}/{len(signal_dates)} tamamlandı: {date.date()}", flush=True)
    out = pd.DataFrame(rows)
    return out.sort_values(["date", "v1_score"], ascending=[True, False], ignore_index=True)


def v2_walk_forward(dataset: pd.DataFrame, signal_dates: list[pd.Timestamp], model: dict, v2) -> pd.DataFrame:
    print("V2 ileri-yürüyen tahminleri hazırlanıyor...", flush=True)
    wanted = [date for date in signal_dates if date in set(dataset["date"].unique())]
    l2 = float(model["training"]["l2"])
    half_life = float(model["training"]["half_life_days"])
    pieces: list[pd.DataFrame] = []
    for start in range(0, len(wanted), 5):
        test_dates = wanted[start : start + 5]
        train = dataset[dataset["date"] < test_dates[0]].copy()
        test = dataset[dataset["date"].isin(test_dates)].copy()
        if train.empty or test.empty:
            continue
        prep = v2.Preprocessor.fit(train)
        x_train = prep.transform(train)
        x_test = prep.transform(test)
        weights = v2._recency_weights(train["date"], half_life)
        beta = v2.fit_logistic(
            x_train,
            train["target"].to_numpy(dtype=float),
            sample_weight=weights,
            l2=l2,
        )
        test["v2_probability"] = v2.predict_probability(x_test, beta)
        pieces.append(test[["date", "ticker", "v2_probability"]])
        print(
            f"  V2 eğitim: {train['date'].min().date()}–{train['date'].max().date()} · "
            f"sınav: {test_dates[0].date()}–{test_dates[-1].date()}",
            flush=True,
        )
    if not pieces:
        raise RuntimeError("V2 ileri-yürüyen tahmin üretemedi.")
    out = pd.concat(pieces, ignore_index=True)
    return out.sort_values(["date", "v2_probability"], ascending=[True, False], ignore_index=True)


def build_selections(v1: pd.DataFrame, v2: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    selections: list[pd.DataFrame] = []
    pools: list[pd.DataFrame] = []
    all_dates = sorted(set(v1["date"]) & set(v2["date"]))
    for date in all_dates:
        one = v1[v1["date"] == date].sort_values("v1_score", ascending=False)
        two = v2[v2["date"] == date].sort_values("v2_probability", ascending=False)
        common_tickers = set(one["ticker"]) & set(two["ticker"])
        common_one = one[one["ticker"].isin(common_tickers)]
        common_two = two[two["ticker"].isin(common_tickers)]

        live_one = one[one["v1_score"] >= 150.0].head(12)
        live_two = two.head(12)
        modes = [
            ("canli", "V1", live_one, one),
            ("canli", "V2", live_two, two),
            ("esit_top12", "V1", one.head(12), one),
            ("esit_top12", "V2", two.head(12), two),
            ("ortak_havuz_top12", "V1", common_one.head(12), common_one),
            ("ortak_havuz_top12", "V2", common_two.head(12), common_two),
        ]
        if len(live_one) > 0:
            modes.extend(
                [
                    ("v1_alarm_sayisina_esit", "V1", live_one, one),
                    ("v1_alarm_sayisina_esit", "V2", two.head(len(live_one)), two),
                ]
            )
        for mode, engine, selected, pool in modes:
            picked = selected[["date", "ticker"]].copy()
            picked["mode"] = mode
            picked["engine"] = engine
            picked["rank"] = np.arange(1, len(picked) + 1)
            selections.append(picked)
            available = pool[["date", "ticker"]].copy()
            available["mode"] = mode
            available["engine"] = engine
            pools.append(available)
    return pd.concat(selections, ignore_index=True), pd.concat(pools, ignore_index=True).drop_duplicates()


class OutcomeBook:
    def __init__(self, prices: dict[str, pd.DataFrame], market: pd.DataFrame):
        self.prices = prices
        self.market = market
        self.sessions = list(market.index)
        self.position = {date: number for number, date in enumerate(self.sessions)}

    @lru_cache(maxsize=None)
    def get(self, date_text: str, ticker: str, horizon: int) -> dict:
        date = pd.Timestamp(date_text)
        frame = self.prices.get(ticker)
        market_pos = self.position.get(date)
        blank = {
            "valid": False,
            "contaminated": False,
            "high_return_pct": math.nan,
            "hit5": 0,
            "close_return_pct": math.nan,
            "drawdown_pct": math.nan,
            "xu_close_return_pct": math.nan,
            "next_open_gap_pct": math.nan,
            "open_to_high_return_pct": math.nan,
            "open_to_close_return_pct": math.nan,
            "future_bars": 0,
        }
        if frame is None or market_pos is None or date not in frame.index:
            return blank
        future_dates = self.sessions[market_pos + 1 : market_pos + horizon + 1]
        if len(future_dates) < horizon:
            return blank
        base = float(frame.at[date, "Close"])
        if not np.isfinite(base) or base <= 0:
            return blank
        highs: list[float] = []
        lows: list[float] = []
        previous_close = base
        contaminated = False
        future_bars = 0
        for future_date in future_dates:
            if future_date not in frame.index:
                continue
            future_bars += 1
            close_value = float(frame.at[future_date, "Close"])
            if previous_close > 0 and abs(close_value / previous_close - 1.0) > 0.35:
                contaminated = True
            previous_close = close_value
            highs.append(float(frame.at[future_date, "High"]))
            lows.append(float(frame.at[future_date, "Low"]))
        high_return = (max(highs) / base - 1.0) * 100.0 if highs else math.nan
        drawdown = (min(lows) / base - 1.0) * 100.0 if lows else math.nan
        exact_date = future_dates[-1]
        first_date = future_dates[0]
        close_return = (
            (float(frame.at[exact_date, "Close"]) / base - 1.0) * 100.0
            if exact_date in frame.index else math.nan
        )
        next_open = float(frame.at[first_date, "Open"]) if first_date in frame.index else math.nan
        next_open_gap = (next_open / base - 1.0) * 100.0 if np.isfinite(next_open) and next_open > 0 else math.nan
        open_to_high = (
            (max(highs) / next_open - 1.0) * 100.0
            if highs and np.isfinite(next_open) and next_open > 0 else math.nan
        )
        open_to_close = (
            (float(frame.at[exact_date, "Close"]) / next_open - 1.0) * 100.0
            if exact_date in frame.index and np.isfinite(next_open) and next_open > 0 else math.nan
        )
        xu_return = (
            (float(self.market.at[exact_date, "Close"]) / float(self.market.at[date, "Close"]) - 1.0) * 100.0
        )
        return {
            "valid": not contaminated,
            "contaminated": contaminated,
            "high_return_pct": high_return,
            "hit5": int(np.isfinite(high_return) and high_return >= TARGET_PCT and not contaminated),
            "close_return_pct": close_return if not contaminated else math.nan,
            "drawdown_pct": drawdown if not contaminated else math.nan,
            "xu_close_return_pct": xu_return,
            "next_open_gap_pct": next_open_gap if not contaminated else math.nan,
            "open_to_high_return_pct": open_to_high if not contaminated else math.nan,
            "open_to_close_return_pct": open_to_close if not contaminated else math.nan,
            "future_bars": future_bars,
        }


def attach_outcomes(rows: pd.DataFrame, book: OutcomeBook, horizon: int) -> pd.DataFrame:
    if rows.empty:
        return rows.copy()
    records = [book.get(str(pd.Timestamp(row.date).date()), row.ticker, horizon) for row in rows.itertuples()]
    return pd.concat([rows.reset_index(drop=True), pd.DataFrame(records)], axis=1)


def summarise(
    selected: pd.DataFrame,
    pool: pd.DataFrame,
    book: OutcomeBook,
    horizon: int,
    allowed_dates: set[pd.Timestamp],
    total_days: int,
) -> dict:
    selected = selected[selected["date"].isin(allowed_dates)].copy()
    pool = pool[pool["date"].isin(allowed_dates)].copy()
    event = attach_outcomes(selected, book, horizon)
    universe = attach_outcomes(pool, book, horizon)
    clean = event[event["valid"]].copy()
    clean_pool = universe[universe["valid"]].copy()
    total = len(clean)
    hits = int(clean["hit5"].sum()) if total else 0
    ci_low, ci_high = _wilson(hits, total)

    expected_hits = 0.0
    expected_total = 0
    for date, group in clean.groupby("date"):
        one_pool = clean_pool[clean_pool["date"] == date]
        if one_pool.empty:
            continue
        expected_hits += len(group) * float(one_pool["hit5"].mean())
        expected_total += len(group)
    baseline = 100.0 * expected_hits / expected_total if expected_total else math.nan
    precision = 100.0 * hits / total if total else math.nan
    close_rows = clean[np.isfinite(clean["close_return_pct"])].copy()
    high_rows = clean[np.isfinite(clean["high_return_pct"])].copy()
    open_rows = clean[np.isfinite(clean["open_to_close_return_pct"])].copy()
    pool_hits = int(clean_pool["hit5"].sum()) if len(clean_pool) else 0
    active_dates = int(clean["date"].nunique()) if total else 0
    hit_days = int(clean.groupby("date")["hit5"].max().sum()) if total else 0
    unique = int(clean["ticker"].nunique()) if total else 0
    return {
        "horizon_days": horizon,
        "calendar_days_tested": total_days,
        "active_days": active_dates,
        "day_coverage_pct": 100.0 * active_dates / total_days if total_days else math.nan,
        "selections": int(len(event)),
        "clean_selections": total,
        "contaminated_excluded": int(event["contaminated"].sum()) if len(event) else 0,
        "unique_tickers": unique,
        "repeat_selection_pct": 100.0 * (1.0 - unique / total) if total else math.nan,
        "hit5_count": hits,
        "hit5_precision_pct": precision,
        "hit5_ci95_low": ci_low,
        "hit5_ci95_high": ci_high,
        "matched_baseline_pct": baseline,
        "lift": precision / baseline if baseline and np.isfinite(precision) else math.nan,
        "excess_hit_pp": precision - baseline if np.isfinite(precision) and np.isfinite(baseline) else math.nan,
        "pool_hit5_count": pool_hits,
        "recall_pct": 100.0 * hits / pool_hits if pool_hits else math.nan,
        "days_with_hit_pct": 100.0 * hit_days / active_dates if active_dates else math.nan,
        "high_return_avg_pct": float(high_rows["high_return_pct"].mean()) if len(high_rows) else math.nan,
        "high_return_median_pct": float(high_rows["high_return_pct"].median()) if len(high_rows) else math.nan,
        "close_data_coverage_pct": 100.0 * len(close_rows) / total if total else math.nan,
        "close_positive_pct": 100.0 * float((close_rows["close_return_pct"] > 0).mean()) if len(close_rows) else math.nan,
        "close_hit5_pct": 100.0 * float((close_rows["close_return_pct"] >= 5).mean()) if len(close_rows) else math.nan,
        "close_return_avg_pct": float(close_rows["close_return_pct"].mean()) if len(close_rows) else math.nan,
        "close_return_median_pct": float(close_rows["close_return_pct"].median()) if len(close_rows) else math.nan,
        "close_return_p10_pct": float(close_rows["close_return_pct"].quantile(0.10)) if len(close_rows) else math.nan,
        "drawdown_avg_pct": float(close_rows["drawdown_pct"].mean()) if len(close_rows) else math.nan,
        "drawdown_p10_pct": float(close_rows["drawdown_pct"].quantile(0.10)) if len(close_rows) else math.nan,
        "xu_close_return_avg_pct": float(close_rows["xu_close_return_pct"].mean()) if len(close_rows) else math.nan,
        "close_excess_vs_xu_avg_pp": (
            float((close_rows["close_return_pct"] - close_rows["xu_close_return_pct"]).mean())
            if len(close_rows) else math.nan
        ),
        "next_open_gap_avg_pct": float(open_rows["next_open_gap_pct"].mean()) if len(open_rows) else math.nan,
        "next_open_gap_median_pct": float(open_rows["next_open_gap_pct"].median()) if len(open_rows) else math.nan,
        "open_to_high_hit5_pct": (
            100.0 * float((open_rows["open_to_high_return_pct"] >= 5.0).mean()) if len(open_rows) else math.nan
        ),
        "open_to_high_avg_pct": float(open_rows["open_to_high_return_pct"].mean()) if len(open_rows) else math.nan,
        "open_to_high_median_pct": float(open_rows["open_to_high_return_pct"].median()) if len(open_rows) else math.nan,
        "open_to_close_positive_pct": (
            100.0 * float((open_rows["open_to_close_return_pct"] > 0.0).mean()) if len(open_rows) else math.nan
        ),
        "open_to_close_avg_pct": float(open_rows["open_to_close_return_pct"].mean()) if len(open_rows) else math.nan,
        "open_to_close_median_pct": float(open_rows["open_to_close_return_pct"].median()) if len(open_rows) else math.nan,
        "open_to_close_p10_pct": float(open_rows["open_to_close_return_pct"].quantile(0.10)) if len(open_rows) else math.nan,
    }


def clustered_bootstrap_difference(
    selections: pd.DataFrame,
    book: OutcomeBook,
    allowed_dates: set[pd.Timestamp],
    mode: str,
    horizon: int,
    repeats: int = 2000,
) -> dict:
    data = selections[(selections["mode"] == mode) & selections["date"].isin(allowed_dates)]
    daily: list[dict] = []
    for date in sorted(set(data["date"])):
        item = {"date": date}
        complete = True
        for engine in ("V1", "V2"):
            group = attach_outcomes(data[(data["date"] == date) & (data["engine"] == engine)], book, horizon)
            group = group[group["valid"]]
            if group.empty:
                complete = False
                break
            item[f"{engine}_hit"] = float(group["hit5"].mean()) * 100.0
            close_group = group[np.isfinite(group["close_return_pct"])]
            item[f"{engine}_close"] = float(close_group["close_return_pct"].mean()) if len(close_group) else math.nan
        if complete:
            daily.append(item)
    daily_frame = pd.DataFrame(daily)
    if daily_frame.empty:
        return {}
    rng = np.random.default_rng(20260802)
    hit_differences: list[float] = []
    close_differences: list[float] = []
    for _ in range(repeats):
        sample = daily_frame.iloc[rng.integers(0, len(daily_frame), len(daily_frame))]
        hit_differences.append(float((sample["V2_hit"] - sample["V1_hit"]).mean()))
        close_differences.append(float((sample["V2_close"] - sample["V1_close"]).mean()))
    return {
        "paired_days": int(len(daily_frame)),
        "v2_minus_v1_hit_pp": float((daily_frame["V2_hit"] - daily_frame["V1_hit"]).mean()),
        "hit_difference_ci95_low": float(np.quantile(hit_differences, 0.025)),
        "hit_difference_ci95_high": float(np.quantile(hit_differences, 0.975)),
        "v2_minus_v1_close_pp": float((daily_frame["V2_close"] - daily_frame["V1_close"]).mean()),
        "close_difference_ci95_low": float(np.quantile(close_differences, 0.025)),
        "close_difference_ci95_high": float(np.quantile(close_differences, 0.975)),
    }


def _fmt(value, digits=1) -> str:
    return "—" if value is None or not np.isfinite(value) else f"{value:.{digits}f}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--start", default="2026-06-01")
    parser.add_argument("--end", default="2026-07-31")
    parser.add_argument("--engine", default="v2")
    args = parser.parse_args()

    project = Path(args.project_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(project))
    import tavan_telegram as v1_live
    if getattr(args, "engine", "v2") == "v3":
        import yuksek_getiri_engine_v3 as v2
        _mf = "yuksek_getiri_v3_model.json"
    else:
        import yuksek_getiri_engine_v2 as v2
        _mf = "yuksek_getiri_v2_model.json"

    data_dir = project / "veriler"
    model = v2.load_model(project / _mf)
    print("V2 özellik tablosu hazırlanıyor...", flush=True)
    dataset, dataset_meta = v2.build_training_dataset(
        data_dir=data_dir,
        target_pct=TARGET_PCT,
        min_turnover_tl=float(model["universe"]["min_median_turnover_20_tl"]),
        min_universe_per_date=int(model["universe"]["min_universe_per_training_date"]),
        history_days=0,
    )
    prices, market = load_prices(data_dir)
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    window = [date for date in market.index if start <= date <= end]
    if len(window) <= max(HORIZONS):
        raise RuntimeError("Backtest penceresi çok kısa.")
    signal_dates = window[:-1]
    common_dates = set(window[:-max(HORIZONS)])
    print(
        f"Sınav penceresi: {window[0].date()}–{window[-1].date()} · {len(window)} seans · "
        f"1 günlük {len(signal_dates)} sinyal günü · ortak 5 günlük kohort {len(common_dates)} gün",
        flush=True,
    )

    v2_scores = v2_walk_forward(dataset, signal_dates, model, v2)
    v1_scores = v1_scans(signal_dates, prices, market, v1_live)
    common_signal_dates = sorted(set(v1_scores["date"]) & set(v2_scores["date"]))
    selections, pools = build_selections(v1_scores, v2_scores)
    book = OutcomeBook(prices, market)

    print("Vade ve getiri tabloları hesaplanıyor...", flush=True)
    metric_rows: list[dict] = []
    for cohort in ("full_available", "common_5day"):
        for horizon in HORIZONS:
            if cohort == "full_available":
                allowed = set(window[:-horizon])
            else:
                allowed = common_dates
            for mode in sorted(set(selections["mode"])):
                for engine in ("V1", "V2"):
                    selected = selections[(selections["mode"] == mode) & (selections["engine"] == engine)]
                    pool = pools[(pools["mode"] == mode) & (pools["engine"] == engine)]
                    if selected.empty or pool.empty:
                        continue
                    summary = summarise(selected, pool, book, horizon, allowed, len(allowed))
                    summary.update({"cohort": cohort, "mode": mode, "engine": engine})
                    metric_rows.append(summary)
    metrics = pd.DataFrame(metric_rows)

    production_common = metrics[(metrics["cohort"] == "common_5day") & (metrics["mode"] == "canli")].copy()
    ideals: list[dict] = []
    for engine in ("V1", "V2"):
        group = production_common[production_common["engine"] == engine].copy()
        detection = group.loc[group["lift"].idxmax()]
        closing = group.loc[group["close_return_median_pct"].idxmax()]
        month_ideals: dict[str, int | None] = {}
        for month in ("2026-06", "2026-07"):
            month_dates = {date for date in common_dates if str(date.date())[:7] == month}
            month_rows = []
            for horizon in HORIZONS:
                selected = selections[(selections["mode"] == "canli") & (selections["engine"] == engine)]
                pool = pools[(pools["mode"] == "canli") & (pools["engine"] == engine)]
                month_rows.append(summarise(selected, pool, book, horizon, month_dates, len(month_dates)))
            valid_month = [row for row in month_rows if np.isfinite(row["lift"])]
            month_ideals[month] = max(valid_month, key=lambda row: row["lift"])["horizon_days"] if valid_month else None
        ideals.append(
            {
                "engine": engine,
                "ideal_detection_horizon_days": int(detection["horizon_days"]),
                "ideal_detection_lift": float(detection["lift"]),
                "ideal_detection_hit_pct": float(detection["hit5_precision_pct"]),
                "ideal_close_horizon_days": int(closing["horizon_days"]),
                "ideal_close_median_pct": float(closing["close_return_median_pct"]),
                "june_detection_horizon": month_ideals["2026-06"],
                "july_detection_horizon": month_ideals["2026-07"],
                "horizon_stable": month_ideals["2026-06"] == month_ideals["2026-07"],
            }
        )
    ideals_frame = pd.DataFrame(ideals)

    monthly_rows: list[dict] = []
    for ideal in ideals:
        engine = ideal["engine"]
        horizon = int(ideal["ideal_detection_horizon_days"])
        for month in ("2026-06", "2026-07"):
            dates = {date for date in common_dates if str(date.date())[:7] == month}
            selected = selections[(selections["mode"] == "canli") & (selections["engine"] == engine)]
            pool = pools[(pools["mode"] == "canli") & (pools["engine"] == engine)]
            row = summarise(selected, pool, book, horizon, dates, len(dates))
            row.update({"engine": engine, "month": month, "chosen_horizon_days": horizon})
            monthly_rows.append(row)
    monthly = pd.DataFrame(monthly_rows)

    head_to_head = clustered_bootstrap_difference(
        selections, book, set(window[:-1]), "esit_top12", 1
    )
    overlap_rows = []
    for date in common_signal_dates:
        one = set(
            selections[(selections["date"] == date) & (selections["mode"] == "esit_top12") & (selections["engine"] == "V1")]["ticker"]
        )
        two = set(
            selections[(selections["date"] == date) & (selections["mode"] == "esit_top12") & (selections["engine"] == "V2")]["ticker"]
        )
        overlap_rows.append(
            {
                "date": str(date.date()),
                "overlap_count": len(one & two),
                "jaccard_pct": 100.0 * len(one & two) / len(one | two) if one | two else math.nan,
            }
        )
    overlap = pd.DataFrame(overlap_rows)

    production_selection = selections[selections["mode"] == "canli"].copy()
    event_rows: list[pd.DataFrame] = []
    for horizon in HORIZONS:
        attached = attach_outcomes(production_selection, book, horizon)
        attached["horizon_days"] = horizon
        event_rows.append(attached)
    events = pd.concat(event_rows, ignore_index=True)

    metrics.to_csv(output_dir / "v1_v2_horizon_metrics.csv", index=False, encoding="utf-8-sig")
    ideals_frame.to_csv(output_dir / "v1_v2_ideal_horizons.csv", index=False, encoding="utf-8-sig")
    monthly.to_csv(output_dir / "v1_v2_monthly_stability.csv", index=False, encoding="utf-8-sig")
    events.to_csv(output_dir / "v1_v2_production_events.csv", index=False, encoding="utf-8-sig")
    overlap.to_csv(output_dir / "v1_v2_daily_overlap.csv", index=False, encoding="utf-8-sig")

    live_one_day = metrics[
        (metrics["cohort"] == "full_available")
        & (metrics["mode"] == "canli")
        & (metrics["horizon_days"] == 1)
    ].sort_values("engine")
    equal_one_day = metrics[
        (metrics["cohort"] == "full_available")
        & (metrics["mode"] == "esit_top12")
        & (metrics["horizon_days"] == 1)
    ].sort_values("engine")
    common_pool_one_day = metrics[
        (metrics["cohort"] == "full_available")
        & (metrics["mode"] == "ortak_havuz_top12")
        & (metrics["horizon_days"] == 1)
    ].sort_values("engine")

    report_payload = {
        "generated_for_window": {"start": args.start, "end": args.end},
        "method": {
            "signal_reference": "T kapanışı",
            "intraday_target": "T+1..T+h seanslarında herhangi bir gün içi yüksek >= T kapanışı +%5",
            "close_return": "T+h kapanışı / T kapanışı - 1",
            "v2_validation": "5 seanslık bloklarla expanding walk-forward; her blok yalnız önceki tarihlerle eğitildi",
            "ideal_detection_horizon": "ortak 5-gün kohortunda eşleşmiş piyasa tabanına karşı en yüksek lift",
            "ideal_close_horizon": "ortak 5-gün kohortunda en yüksek medyan kapanış hareketi",
            "realistic_open_reference": "T+1 açılışı; liste 09:30'da yayımlandığı için ayrıca ölçüldü",
            "corporate_action_rule": "gelecek patikada mutlak günlük kapanış hareketi >%35 olan olay dışlandı",
        },
        "data": dataset_meta,
        "head_to_head_equal_top12_h1": head_to_head,
        "average_top12_overlap": {
            "count": _safe_float(overlap["overlap_count"].mean()),
            "jaccard_pct": _safe_float(overlap["jaccard_pct"].mean()),
        },
        "ideal_horizons": ideals,
        "live_one_day": live_one_day.replace({np.nan: None}).to_dict("records"),
        "equal_top12_one_day": equal_one_day.replace({np.nan: None}).to_dict("records"),
        "common_pool_top12_one_day": common_pool_one_day.replace({np.nan: None}).to_dict("records"),
        "caveats": [
            "Rakamlar işlem getirisi değildir; sinyal kapanışına göre sonraki fiyat hareketidir.",
            "Sinyal T kapanışı tamamlandıktan sonra oluştuğu için T kapanışından fiili alım varsayımı yapılamaz.",
            "İdeal vade aynı iki aylık pencereden seçildi; ileri dönemde gölge testle doğrulanmalıdır.",
            "Aynı hisse farklı günlerde tekrar seçildiği için olaylar tamamen bağımsız değildir.",
        ],
    }
    with (output_dir / "v1_v2_backtest_report.json").open("w", encoding="utf-8") as handle:
        json.dump(report_payload, handle, ensure_ascii=False, indent=2, allow_nan=False)

    lines = [
        "# V1 – V2 Son İki Ay Karşılaştırması",
        "",
        f"Dönem: **{args.start} – {args.end}** · {len(window)} BIST seansı",
        "",
        "## Canlı yayın düzeni — 1 seans",
        "",
        "| Motor | Aktif gün | Aday | +%5 gün içi | Piyasa tabanı | Yoğunluk | Ort. zirve | Ort. kapanış | Medyan kapanış | Pozitif kapanış |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in live_one_day.itertuples():
        lines.append(
            f"| {row.engine} | {row.active_days}/{row.calendar_days_tested} | {row.clean_selections} | "
            f"%{_fmt(row.hit5_precision_pct)} | %{_fmt(row.matched_baseline_pct)} | {_fmt(row.lift, 2)}x | "
            f"%{_fmt(row.high_return_avg_pct)} | %{_fmt(row.close_return_avg_pct)} | "
            f"%{_fmt(row.close_return_median_pct)} | %{_fmt(row.close_positive_pct)} |"
        )
    lines.extend(
        [
            "",
        "## Eşit ilk 12 — 1 seans",
            "",
            "| Motor | Aday | +%5 gün içi | %95 güven aralığı | Piyasa tabanı | Yoğunluk | Ort. kapanış | Medyan kapanış | En kötü %10 kapanış |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in equal_one_day.itertuples():
        lines.append(
            f"| {row.engine} | {row.clean_selections} | %{_fmt(row.hit5_precision_pct)} | "
            f"%{_fmt(row.hit5_ci95_low)}–%{_fmt(row.hit5_ci95_high)} | %{_fmt(row.matched_baseline_pct)} | "
            f"{_fmt(row.lift, 2)}x | %{_fmt(row.close_return_avg_pct)} | %{_fmt(row.close_return_median_pct)} | "
            f"%{_fmt(row.close_return_p10_pct)} |"
        )
    lines.extend(
        [
            "",
            "## 09:30 sonrası gerçekçi açılış referansı — canlı düzen, 1 seans",
            "",
            "| Motor | Ort. açılış boşluğu | Açılıştan +%5 zirve | Ort. açılış→zirve | Ort. açılış→kapanış | Medyan açılış→kapanış | En kötü %10 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in live_one_day.itertuples():
        lines.append(
            f"| {row.engine} | %{_fmt(row.next_open_gap_avg_pct)} | %{_fmt(row.open_to_high_hit5_pct)} | "
            f"%{_fmt(row.open_to_high_avg_pct)} | %{_fmt(row.open_to_close_avg_pct)} | "
            f"%{_fmt(row.open_to_close_median_pct)} | %{_fmt(row.open_to_close_p10_pct)} |"
        )
    lines.extend(
        [
            "",
            "## Tamamen aynı hisse havuzu, eşit ilk 12 — 1 seans",
            "",
            "| Motor | Aday | +%5 gün içi | Ortak piyasa tabanı | Yoğunluk | Ort. kapanış | Medyan kapanış |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in common_pool_one_day.itertuples():
        lines.append(
            f"| {row.engine} | {row.clean_selections} | %{_fmt(row.hit5_precision_pct)} | "
            f"%{_fmt(row.matched_baseline_pct)} | {_fmt(row.lift, 2)}x | "
            f"%{_fmt(row.close_return_avg_pct)} | %{_fmt(row.close_return_median_pct)} |"
        )
    lines.extend(
        [
            "",
            "## Vade tablosu — canlı düzen, aynı 5-günlük kohort",
            "",
            "| Motor | Vade | +%5 gün içi | Piyasa tabanı | Yoğunluk | Ort. zirve | Ort. kapanış | Medyan kapanış | En kötü %10 kapanış |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in production_common.sort_values(["engine", "horizon_days"]).itertuples():
        lines.append(
            f"| {row.engine} | {row.horizon_days} seans | %{_fmt(row.hit5_precision_pct)} | "
            f"%{_fmt(row.matched_baseline_pct)} | {_fmt(row.lift, 2)}x | %{_fmt(row.high_return_avg_pct)} | "
            f"%{_fmt(row.close_return_avg_pct)} | %{_fmt(row.close_return_median_pct)} | %{_fmt(row.close_return_p10_pct)} |"
        )
    lines.extend(
        [
            "",
            "## Pencere içi ideal vadeler",
            "",
            "| Motor | Yakalama için ideal | Bu vadede yoğunluk | Kapanış için ideal | Haziran yakalama | Temmuz yakalama | Kararlı mı? |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in ideals_frame.itertuples():
        lines.append(
            f"| {row.engine} | {row.ideal_detection_horizon_days} seans | {row.ideal_detection_lift:.2f}x | "
            f"{row.ideal_close_horizon_days} seans | {row.june_detection_horizon} | {row.july_detection_horizon} | "
            f"{'Evet' if row.horizon_stable else 'Hayır'} |"
        )
    lines.extend(
        [
            "",
            "## İstatistiksel başa baş farkı",
            "",
            f"Eşit ilk 12 ve 1 seans ölçümünde V2−V1 gün içi +%5 farkı: "
            f"**{_fmt(head_to_head.get('v2_minus_v1_hit_pp'), 2)} puan** "
            f"(%95 gün-kümeli aralık: {_fmt(head_to_head.get('hit_difference_ci95_low'), 2)} ile "
            f"{_fmt(head_to_head.get('hit_difference_ci95_high'), 2)}).",
            "",
            f"İki listenin günlük ortalama ortak hissesi: **{overlap['overlap_count'].mean():.1f}/12**; "
            f"ortalama Jaccard benzerliği **%{overlap['jaccard_pct'].mean():.1f}**.",
            "",
            "## Dürüst okuma notları",
            "",
            "- Bunlar gerçekleşmiş işlem getirileri değil, sinyal kapanışına göre sonraki fiyat hareketleridir.",
            "- Sinyal kapanış tamamlandıktan sonra üretildiği için önceki kapanış ölçüsüne ek olarak ertesi açılış referansı ayrıca raporlandı.",
            "- Uzun vade başarı oranını mekanik olarak yükseltir; bu yüzden ideal vade ham başarıyla değil piyasa tabanına göre yoğunlukla seçildi.",
            "- İdeal vade aynı iki aylık örnekten seçildi; satış iddiasına dönüştürmeden önce ileri tarihli gölge test gerekir.",
        ]
    )
    (output_dir / "v1_v2_backtest_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Raporlar yazıldı:", output_dir, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
