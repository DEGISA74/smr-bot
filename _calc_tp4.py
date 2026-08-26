#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--capital", type=float, default=1_000_000.0)
    parser.add_argument("--commission", type=float, default=0.0005)
    args = parser.parse_args()

    frame = pd.read_csv(args.events)
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[
        (frame["engine"] == "V2")
        & (frame["mode"] == "canli")
        & (frame["horizon_days"] == 1)
        & frame["valid"].astype(str).str.lower().eq("true")
    ].copy()
    frame["gross_return"] = np.where(
        pd.to_numeric(frame["open_to_high_return_pct"], errors="coerce") >= 4.0,
        0.04,
        pd.to_numeric(frame["open_to_close_return_pct"], errors="coerce") / 100.0,
    )
    if frame["gross_return"].isna().any():
        raise RuntimeError("Açılış bazlı sonuçlarda eksik değer var.")

    capital = float(args.capital)
    initial = capital
    commission = float(args.commission)
    total_buy_commission = 0.0
    total_sell_commission = 0.0
    daily_rows = []
    peak_capital = capital
    max_drawdown = 0.0
    for date, group in frame.groupby("date", sort=True):
        start = capital
        allocation = start / len(group)
        buy_notional = allocation / (1.0 + commission)
        buy_fee_each = buy_notional * commission
        gross_sale = buy_notional * (1.0 + group["gross_return"].to_numpy(dtype=float))
        sell_fees = gross_sale * commission
        capital = float(np.sum(gross_sale - sell_fees))
        buy_fees = float(buy_fee_each * len(group))
        sell_fees_total = float(np.sum(sell_fees))
        total_buy_commission += buy_fees
        total_sell_commission += sell_fees_total
        peak_capital = max(peak_capital, capital)
        drawdown = capital / peak_capital - 1.0
        max_drawdown = min(max_drawdown, drawdown)
        daily_rows.append(
            {
                "date": str(date.date()),
                "candidates": int(len(group)),
                "tp4_hits": int((group["gross_return"] == 0.04).sum()),
                "start_capital": start,
                "end_capital": capital,
                "net_daily_return_pct": (capital / start - 1.0) * 100.0,
                "buy_commission": buy_fees,
                "sell_commission": sell_fees_total,
            }
        )

    daily = pd.DataFrame(daily_rows)
    result = {
        "assumption": {
            "engine": "V2 live top 12",
            "entry": "next session Open field proxy",
            "exit": "+4% touched intraday, otherwise same-day Close",
            "allocation": "equal weight among daily candidates",
            "reinvestment": "daily full compounding",
            "commission_each_buy_and_sell": commission,
            "slippage_spread_tax": "not included",
        },
        "period": {
            "start": str(frame["date"].min().date()),
            "end": str(frame["date"].max().date()),
            "sessions": int(frame["date"].nunique()),
            "candidate_events": int(len(frame)),
        },
        "performance": {
            "initial_capital": initial,
            "ending_capital": capital,
            "net_profit": capital - initial,
            "net_return_pct": (capital / initial - 1.0) * 100.0,
            "tp4_hit_count": int((frame["gross_return"] == 0.04).sum()),
            "tp4_hit_rate_pct": float((frame["gross_return"] == 0.04).mean() * 100.0),
            "positive_event_pct": float((frame["gross_return"] > 0.0).mean() * 100.0),
            "gross_event_avg_pct": float(frame["gross_return"].mean() * 100.0),
            "gross_event_median_pct": float(frame["gross_return"].median() * 100.0),
            "total_buy_commission": total_buy_commission,
            "total_sell_commission": total_sell_commission,
            "total_commission": total_buy_commission + total_sell_commission,
            "best_day_pct": float(daily["net_daily_return_pct"].max()),
            "worst_day_pct": float(daily["net_daily_return_pct"].min()),
            "positive_days_pct": float((daily["net_daily_return_pct"] > 0.0).mean() * 100.0),
            "max_drawdown_pct": max_drawdown * 100.0,
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    daily.to_csv(output.with_name("v2_tp4_daily_capital.csv"), index=False, encoding="utf-8-sig")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
