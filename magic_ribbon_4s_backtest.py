# -*- coding: utf-8 -*-
"""
Magic Ribbon v5.1 — 4 saatlik backtest

Kullanıcının paylaştığı RedK Magic Ribbon göstergesini, Pine Script'teki
varsayılan parametreleri değiştirmeden Python'da yeniden üretir ve 4 saatlik
parquet kasası üzerinde ölçer.

Ölçüm kuralı:
  - Fast/Slow çizgilerinin aynı yönde ilk kez hizalanması kapanışta sinyaldir.
  - İşlem bir sonraki 4 saatlik mumun açılışında varsayılır; look-ahead yoktur.
  - Long-only ve short-only sonuçları ayrı raporlanır.
  - Varsayılan %0,20 toplam giriş+çıkış maliyeti ayrıca uygulanır.
  - 1 Temmuz 2025 sonrası dönem, öncesinde parametre seçmeden out-of-sample
    (dış test) olarak raporlanır.

Çalıştırma:
    .venv\\Scripts\\python.exe magic_ribbon_4s_backtest.py
    .venv\\Scripts\\python.exe magic_ribbon_4s_backtest.py --maliyet 0.003
    .venv\\Scripts\\python.exe magic_ribbon_4s_backtest.py --baslangic 2024-01-01
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from magic_ribbon_core import add_ribbon_columns

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


BASE = Path(__file__).resolve().parent
DEPO_4S = BASE / "veriler_4s"
DEFAULT_SPLIT = pd.Timestamp("2025-07-01")
DEFAULT_COST = 0.002
MIN_BARS = 300
MIN_TRAIN_BARS = 180
TOP_VOLATILE = 20
WATCHLIST = [
    "AKBNK", "AKFYE", "ASELS", "ASTOR", "BRSAN", "EREGL", "GUBRF",
    "KONTR", "SASA", "SMRTG", "TERA", "THYAO", "TUPRS", "YEOTK",
]


def read_4h(path: Path, start: pd.Timestamp, today: pd.Timestamp) -> pd.DataFrame | None:
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if df is None or df.empty or "Open" not in df.columns or "Close" not in df.columns:
        return None
    df = df.copy()
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = idx
    df = df[~df.index.duplicated(keep="last")].sort_index()
    # Güncel günün 09:30 barı gün ortasında okunursa kapanmamıştır; backtestte
    # bugünün bütün barlarını dışarıda bırakarak bu riski kapatıyoruz.
    df = df[df.index.normalize() < today]
    df = df[df.index >= start]
    needed = ["Open", "High", "Low", "Close"]
    for col in needed:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=needed)
    df = df[(df["Open"] > 0) & (df["High"] > 0) & (df["Low"] > 0) & (df["Close"] > 0)]
    if len(df) < MIN_BARS:
        return None
    return add_ribbon_columns(df)


def cost_factor(cost: float) -> float:
    """Toplam round-trip maliyeti iki tarafa eşit böler."""
    half = cost / 2.0
    return (1.0 - half) / (1.0 + half)


def simulate(
    df: pd.DataFrame,
    direction: str,
    cost: float,
    start: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Sinyali kapanışta görüp sonraki mumun açılışında işlem varsayar."""
    if direction not in {"long", "short"}:
        raise ValueError(direction)
    valid_mask = df[["fast_line", "slow_line"]].notna().all(axis=1)
    if not valid_mask.any():
        return pd.DataFrame()
    warm_idx = int(np.flatnonzero(valid_mask.to_numpy())[0])
    start_idx = warm_idx if start is None else int(df.index.searchsorted(start, side="left"))
    start_idx = max(start_idx, warm_idx)
    if start_idx >= len(df) - 1:
        return pd.DataFrame()

    trigger_in = "up_trigger" if direction == "long" else "down_trigger"
    trigger_out = "down_trigger" if direction == "long" else "up_trigger"
    pos = False
    entry_i: int | None = None
    entry_price: float | None = None
    trades: list[dict] = []
    cf = cost_factor(cost)

    for signal_i in range(start_idx, len(df) - 1):
        exec_i = signal_i + 1
        exec_price = float(df["Open"].iloc[exec_i])
        if not np.isfinite(exec_price) or exec_price <= 0:
            continue
        if not pos and bool(df[trigger_in].iloc[signal_i]):
            pos = True
            entry_i = exec_i
            entry_price = exec_price
        elif pos and bool(df[trigger_out].iloc[signal_i]):
            assert entry_i is not None and entry_price is not None
            exit_i = exec_i
            exit_price = exec_price
            high = float(df["High"].iloc[entry_i : exit_i + 1].max())
            low = float(df["Low"].iloc[entry_i : exit_i + 1].min())
            if direction == "long":
                gross_factor = exit_price / entry_price
                mfe = high / entry_price - 1.0
                mae = low / entry_price - 1.0
            else:
                gross_factor = entry_price / exit_price
                mfe = entry_price / low - 1.0
                mae = entry_price / high - 1.0
            net_factor = gross_factor * cf
            trades.append(
                {
                    "entry_time": str(df.index[entry_i]),
                    "exit_time": str(df.index[exit_i]),
                    "entry_i": entry_i,
                    "exit_i": exit_i,
                    "holding_bars": exit_i - entry_i,
                    "gross_pct": (gross_factor - 1.0) * 100.0,
                    "net_pct": (net_factor - 1.0) * 100.0,
                    "mfe_pct": mfe * 100.0,
                    "mae_pct": mae * 100.0,
                }
            )
            pos = False
            entry_i = None
            entry_price = None

    # Son açık pozisyonu son kapanışta kapat. Son kapanış bugünün değil, dolu
    # geçmişteki son kapanıştır; bu yüzden yarım mum kullanılmaz.
    if pos and entry_i is not None and entry_price is not None:
        exit_i = len(df) - 1
        exit_price = float(df["Close"].iloc[exit_i])
        high = float(df["High"].iloc[entry_i : exit_i + 1].max())
        low = float(df["Low"].iloc[entry_i : exit_i + 1].min())
        if direction == "long":
            gross_factor = exit_price / entry_price
            mfe = high / entry_price - 1.0
            mae = low / entry_price - 1.0
        else:
            gross_factor = entry_price / exit_price
            mfe = entry_price / low - 1.0
            mae = entry_price / high - 1.0
        trades.append(
            {
                "entry_time": str(df.index[entry_i]),
                "exit_time": str(df.index[exit_i]),
                "entry_i": entry_i,
                "exit_i": exit_i,
                "holding_bars": exit_i - entry_i,
                "gross_pct": (gross_factor - 1.0) * 100.0,
                "net_pct": (gross_factor * cf - 1.0) * 100.0,
                "mfe_pct": mfe * 100.0,
                "mae_pct": mae * 100.0,
            }
        )
    return pd.DataFrame(trades)


def forward_horizon_signals(
    df: pd.DataFrame,
    direction: str,
    cost: float,
    start: pd.Timestamp | None = None,
    horizons: tuple[int, ...] = (5, 10, 20),
) -> pd.DataFrame:
    """Her yeni hizalanma sinyalini sabit ileri vadelerde ayrıca ölçer."""
    empty = pd.DataFrame(columns=["signal_time", "entry_time", "horizon_bars", "gross_pct", "net_pct"])
    if direction not in {"long", "short"}:
        raise ValueError(direction)
    valid_mask = df[["fast_line", "slow_line"]].notna().all(axis=1)
    if not valid_mask.any():
        return empty
    warm_idx = int(np.flatnonzero(valid_mask.to_numpy())[0])
    start_idx = warm_idx if start is None else int(df.index.searchsorted(start, side="left"))
    start_idx = max(start_idx, warm_idx)
    trigger = "up_trigger" if direction == "long" else "down_trigger"
    cf = cost_factor(cost)
    rows = []
    for signal_i in range(start_idx, len(df) - 1):
        if not bool(df[trigger].iloc[signal_i]):
            continue
        entry_i = signal_i + 1
        entry_price = float(df["Open"].iloc[entry_i])
        if not np.isfinite(entry_price) or entry_price <= 0:
            continue
        for horizon in horizons:
            exit_i = entry_i + horizon - 1
            if exit_i >= len(df):
                continue
            exit_price = float(df["Close"].iloc[exit_i])
            if direction == "long":
                gross_factor = exit_price / entry_price
            else:
                gross_factor = entry_price / exit_price
            rows.append({
                "signal_time": str(df.index[signal_i]),
                "entry_time": str(df.index[entry_i]),
                "horizon_bars": horizon,
                "gross_pct": (gross_factor - 1.0) * 100.0,
                "net_pct": (gross_factor * cf - 1.0) * 100.0,
            })
    return pd.DataFrame(rows)


def forward_horizon_baseline(
    df: pd.DataFrame,
    direction: str,
    cost: float,
    start: pd.Timestamp | None = None,
    horizons: tuple[int, ...] = (5, 10, 20),
) -> pd.DataFrame:
    """Aynı hissede sinyal şartı olmadan her 4S bardan alınan referans."""
    empty = pd.DataFrame(columns=["entry_time", "horizon_bars", "net_pct"])
    if direction not in {"long", "short"}:
        raise ValueError(direction)
    valid_mask = df[["Open", "Close"]].notna().all(axis=1)
    if not valid_mask.any():
        return empty
    start_idx = 0 if start is None else int(df.index.searchsorted(start, side="left"))
    cf = cost_factor(cost)
    rows = []
    for entry_i in range(start_idx, len(df)):
        entry_price = float(df["Open"].iloc[entry_i])
        if not np.isfinite(entry_price) or entry_price <= 0:
            continue
        for horizon in horizons:
            exit_i = entry_i + horizon - 1
            if exit_i >= len(df):
                continue
            exit_price = float(df["Close"].iloc[exit_i])
            gross_factor = (exit_price / entry_price) if direction == "long" else (entry_price / exit_price)
            rows.append({
                "entry_time": str(df.index[entry_i]),
                "horizon_bars": horizon,
                "net_pct": (gross_factor * cf - 1.0) * 100.0,
            })
    return pd.DataFrame(rows)


def fixed_horizon_metrics(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty:
        return {
            "signals": 0,
            "win_rate_pct": None,
            "avg_net_pct": None,
            "median_net_pct": None,
            "profit_factor": None,
        }
    r = pd.to_numeric(trades["net_pct"], errors="coerce").dropna() / 100.0
    wins = r[r > 0]
    losses = r[r < 0]
    return {
        "signals": int(len(r)),
        "win_rate_pct": float((r > 0).mean() * 100.0),
        "avg_net_pct": float(r.mean() * 100.0),
        "median_net_pct": float(r.median() * 100.0),
        "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) else None,
    }


def metrics(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty:
        return {
            "trades": 0,
            "win_rate_pct": None,
            "avg_net_pct": None,
            "median_net_pct": None,
            "profit_factor": None,
            "compound_net_pct": None,
            "max_drawdown_pct": None,
            "avg_holding_bars": None,
            "median_holding_bars": None,
        }
    r = pd.to_numeric(trades["net_pct"], errors="coerce").dropna() / 100.0
    eq = (1.0 + r).cumprod()
    peak = eq.cummax()
    dd = eq / peak - 1.0
    wins = r[r > 0]
    losses = r[r < 0]
    pf = float(wins.sum() / abs(losses.sum())) if len(losses) else None
    return {
        "trades": int(len(r)),
        "win_rate_pct": float((r > 0).mean() * 100.0),
        "avg_net_pct": float(r.mean() * 100.0),
        "median_net_pct": float(r.median() * 100.0),
        "profit_factor": pf,
        "compound_net_pct": float((eq.iloc[-1] - 1.0) * 100.0),
        "max_drawdown_pct": float(dd.min() * 100.0),
        "avg_holding_bars": float(trades["holding_bars"].mean()),
        "median_holding_bars": float(trades["holding_bars"].median()),
    }


def buy_hold_metrics(df: pd.DataFrame, start: pd.Timestamp | None) -> dict:
    first = 0 if start is None else int(df.index.searchsorted(start, side="left"))
    if first >= len(df) - 1:
        return {"return_pct": None}
    entry = float(df["Open"].iloc[first])
    exit_ = float(df["Close"].iloc[-1])
    return {"return_pct": float((exit_ / entry - 1.0) * 100.0)}


def volatility_stats(df: pd.DataFrame, split: pd.Timestamp) -> dict:
    train = df[df.index < split]
    r = train["Close"].pct_change().dropna()
    if r.empty:
        return {"train_bars": int(len(train)), "std_bar_pct": None, "median_abs_bar_pct": None}
    return {
        "train_bars": int(len(train)),
        "std_bar_pct": float(r.std() * 100.0),
        "median_abs_bar_pct": float(r.abs().median() * 100.0),
    }


def safe_float(x):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return None
    return float(x) if isinstance(x, (np.floating, float, int)) else x


def run(args) -> tuple[dict, pd.DataFrame]:
    start = pd.Timestamp(args.baslangic)
    split = pd.Timestamp(args.split)
    today = pd.Timestamp.now().normalize()
    rows: list[dict] = []
    all_trades: list[pd.DataFrame] = []
    all_horizon_trades: list[pd.DataFrame] = []
    all_baseline_horizon_trades: list[pd.DataFrame] = []
    paths = sorted(DEPO_4S.glob("*.IS_4h.parquet"))
    for path in paths:
        symbol = path.name.replace(".IS_4h.parquet", "")
        df = read_4h(path, start, today)
        if df is None:
            continue
        if int((df.index < split).sum()) < MIN_TRAIN_BARS:
            continue
        vol = volatility_stats(df, split)
        full_long = simulate(df, "long", args.maliyet, None)
        full_short = simulate(df, "short", args.maliyet, None)
        oos_long = simulate(df, "long", args.maliyet, split)
        oos_short = simulate(df, "short", args.maliyet, split)
        oos_h_long = forward_horizon_signals(df, "long", args.maliyet, split)
        oos_h_short = forward_horizon_signals(df, "short", args.maliyet, split)
        oos_b_long = forward_horizon_baseline(df, "long", args.maliyet, split)
        oos_b_short = forward_horizon_baseline(df, "short", args.maliyet, split)
        row = {
            "symbol": symbol,
            "bars": int(len(df)),
            "first_bar": str(df.index[0]),
            "last_bar": str(df.index[-1]),
            **vol,
            "full_long": metrics(full_long),
            "full_short": metrics(full_short),
            "oos_long": metrics(oos_long),
            "oos_short": metrics(oos_short),
            "oos_horizon_long": {
                str(h): fixed_horizon_metrics(oos_h_long[oos_h_long.horizon_bars == h])
                for h in (5, 10, 20)
            },
            "oos_horizon_short": {
                str(h): fixed_horizon_metrics(oos_h_short[oos_h_short.horizon_bars == h])
                for h in (5, 10, 20)
            },
            "buy_hold_full": buy_hold_metrics(df, None),
            "buy_hold_oos": buy_hold_metrics(df, split),
        }
        rows.append(row)
        for side, trades in (("long", oos_long), ("short", oos_short)):
            if not trades.empty:
                t = trades.copy()
                t["symbol"] = symbol
                t["side"] = side
                all_trades.append(t)
        for side, trades in (("long", oos_h_long), ("short", oos_h_short)):
            if not trades.empty:
                t = trades.copy()
                t["symbol"] = symbol
                t["side"] = side
                all_horizon_trades.append(t)
        for side, trades in (("long", oos_b_long), ("short", oos_b_short)):
            if not trades.empty:
                t = trades.copy()
                t["symbol"] = symbol
                t["side"] = side
                all_baseline_horizon_trades.append(t)

    rows.sort(key=lambda x: x.get("median_abs_bar_pct") or -1, reverse=True)
    flat_rows = []
    for row in rows:
        for period in ("full_long", "full_short", "oos_long", "oos_short"):
            flat = {"symbol": row["symbol"], "bars": row["bars"], "std_bar_pct": row["std_bar_pct"],
                    "median_abs_bar_pct": row["median_abs_bar_pct"], "period": period,
                    **row[period], "buy_hold_pct": row["buy_hold_full" if period.startswith("full") else "buy_hold_oos"]["return_pct"]}
            flat_rows.append(flat)
    flat_df = pd.DataFrame(flat_rows)
    all_trade_df = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    all_horizon_df = pd.concat(all_horizon_trades, ignore_index=True) if all_horizon_trades else pd.DataFrame()
    all_baseline_horizon_df = (
        pd.concat(all_baseline_horizon_trades, ignore_index=True)
        if all_baseline_horizon_trades else pd.DataFrame()
    )

    top_volatile = [r["symbol"] for r in rows[:TOP_VOLATILE]]
    volatile_df = flat_df[(flat_df.symbol.isin(top_volatile)) & (flat_df.period == "oos_long")].copy()
    volatile_df = volatile_df.sort_values(["avg_net_pct", "trades"], ascending=[False, False])
    eligible_oos = flat_df[(flat_df.period == "oos_long") & (flat_df.trades >= 8)].copy()
    eligible_oos = eligible_oos.sort_values(["avg_net_pct", "trades"], ascending=[False, False])

    pooled = {}
    if not all_trade_df.empty:
        for side in ("long", "short"):
            pooled[side] = metrics(all_trade_df[all_trade_df.side == side])
            # Farklı hisselerin işlemlerini tek bir sıraya dizip bileşiklemek
            # gerçek bir portföy simülasyonu değildir; bu iki alan pooled'da
            # bilinçli olarak boş bırakılır.
            pooled[side]["compound_net_pct"] = None
            pooled[side]["max_drawdown_pct"] = None

    horizon_pooled = {}
    horizon_baseline = {}
    horizon_comparison = {}
    if not all_horizon_df.empty:
        for side in ("long", "short"):
            for horizon in (5, 10, 20):
                hdf = all_horizon_df[
                    (all_horizon_df.side == side) & (all_horizon_df.horizon_bars == horizon)
                ]
                horizon_pooled[f"{side}_T+{horizon}"] = fixed_horizon_metrics(hdf)
                if not all_baseline_horizon_df.empty:
                    bdf = all_baseline_horizon_df[
                        (all_baseline_horizon_df.side == side) & (all_baseline_horizon_df.horizon_bars == horizon)
                    ]
                    horizon_baseline[f"{side}_T+{horizon}"] = fixed_horizon_metrics(bdf)
                    s = horizon_pooled[f"{side}_T+{horizon}"]
                    b = horizon_baseline[f"{side}_T+{horizon}"]
                    horizon_comparison[f"{side}_T+{horizon}"] = {
                        "signal_avg_net_pct": s["avg_net_pct"],
                        "baseline_avg_net_pct": b["avg_net_pct"],
                        "edge_avg_net_pct": (
                            s["avg_net_pct"] - b["avg_net_pct"]
                            if s["avg_net_pct"] is not None and b["avg_net_pct"] is not None else None
                        ),
                        "signal_median_net_pct": s["median_net_pct"],
                        "baseline_median_net_pct": b["median_net_pct"],
                        "edge_median_net_pct": (
                            s["median_net_pct"] - b["median_net_pct"]
                            if s["median_net_pct"] is not None and b["median_net_pct"] is not None else None
                        ),
                    }

    eligible_by_side = {}
    for side in ("long", "short"):
        p = flat_df[(flat_df.period == f"oos_{side}") & (flat_df.trades >= 8)].copy()
        eligible_by_side[side] = {
            "tickers_with_min_8_trades": int(len(p)),
            "tickers_positive_avg_net_pct": int((p.avg_net_pct > 0).sum()),
            "positive_ticker_ratio_pct": float((p.avg_net_pct > 0).mean() * 100.0) if len(p) else None,
            "median_ticker_avg_net_pct": float(p.avg_net_pct.median()) if len(p) else None,
            "median_ticker_win_rate_pct": float(p.win_rate_pct.median()) if len(p) else None,
            "median_ticker_profit_factor": float(p.profit_factor.median()) if len(p) else None,
        }
    report = {
        "meta": {
            "strategy": "RedK Magic Ribbon v5.1",
            "source": "user-pasted Pine Script",
            "timeframe": "4h",
            "data_dir": str(DEPO_4S),
            "data_files_found": len(paths),
            "symbols_measured": len(rows),
            "start": str(start.date()),
            "split": str(split.date()),
            "excluded_current_day": str(today.date()),
            "cost_round_trip_pct": args.maliyet * 100.0,
            "signal_execution": "signal at close, execution at next bar open",
            "parameters": {"cora_length": 10, "cora_smooth": 3, "lazy_length": 15},
        },
        "pooled_oos": pooled,
        "fixed_horizon_oos": horizon_pooled,
        "baseline_fixed_horizon_oos": horizon_baseline,
        "fixed_horizon_signal_edge_vs_baseline": horizon_comparison,
        "cross_sectional_oos_min_8_trades": eligible_by_side,
        "top_volatile_by_train_median_abs_bar_pct": top_volatile,
        "volatile_oos_long": volatile_df.to_dict(orient="records"),
        "watchlist_oos_long": flat_df[(flat_df.symbol.isin(WATCHLIST)) & (flat_df.period == "oos_long")]
            .sort_values("median_abs_bar_pct", ascending=False).to_dict(orient="records"),
        "best_oos_long_min_8_trades": eligible_oos.head(30).to_dict(orient="records"),
        "rows": rows,
    }
    return report, flat_df


def fmt(x, digits=2):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x:.{digits}f}"


def print_report(report: dict, flat_df: pd.DataFrame):
    m = report["meta"]
    print("MAGIC RIBBON v5.1 — 4 SAATLİK BACKTEST")
    print("=" * 92)
    print(f"Ölçülen hisse: {m['symbols_measured']} / {m['data_files_found']} dosya | "
          f"dönem: {m['start']} → {m['excluded_current_day']} günü dışarıda | "
          f"ayrım: {m['split']} sonrası")
    print(f"Parametre: Cora 10 / Smooth 3 · LazyLine 15 · maliyet: %{m['cost_round_trip_pct']:.2f} round-trip")
    print("Kural: hizalanma kapanışta görülür, işlem sonraki 4S mumun açılışında varsayılır.")
    print("\nTOPLU OOS SONUÇ — 2025-07-01 sonrası")
    print("-" * 92)
    print(f"{'yön':<10}{'işlem':>8}{'isabet':>10}{'ort.net':>12}{'medyan':>12}{'PF':>10}{'bileşik':>12}{'MDD':>12}")
    print("-" * 92)
    for side in ("long", "short"):
        x = report["pooled_oos"].get(side, {})
        print(f"{side:<10}{x.get('trades', 0):>8}{fmt(x.get('win_rate_pct')):>9}%"
              f"{fmt(x.get('avg_net_pct')):>11}%{fmt(x.get('median_net_pct')):>11}%"
              f"{fmt(x.get('profit_factor')):>10}{fmt(x.get('compound_net_pct')):>11}%"
              f"{fmt(x.get('max_drawdown_pct')):>11}%")
    print("\nHİSSE-BAZLI OOS ÖZETİ — en az 8 işlem")
    print("-" * 92)
    for side in ("long", "short"):
        x = report["cross_sectional_oos_min_8_trades"][side]
        print(f"{side:<10} ölçülen hisse: {x['tickers_with_min_8_trades']:>4} · "
              f"pozitif ortalama: {x['tickers_positive_avg_net_pct']:>4} "
              f"(%{fmt(x['positive_ticker_ratio_pct'])}) · "
              f"hisse medyan ort.net: %{fmt(x['median_ticker_avg_net_pct'])} · "
              f"medyan PF: {fmt(x['median_ticker_profit_factor'])}")
    print("\nSİNYAL BAZLI SABİT VADELER — OOS")
    print("-" * 92)
    print(f"{'yön/vade':<14}{'sinyal':>10}{'isabet':>10}{'sinyal ort.':>13}{'referans':>12}{'fark':>10}{'medyan':>12}{'PF':>10}")
    print("-" * 92)
    for side in ("long", "short"):
        for horizon in (5, 10, 20):
            x = report["fixed_horizon_oos"].get(f"{side}_T+{horizon}", {})
            cmp = report["fixed_horizon_signal_edge_vs_baseline"].get(f"{side}_T+{horizon}", {})
            print(f"{side + ' T+' + str(horizon):<14}{x.get('signals', 0):>10}"
                  f"{fmt(x.get('win_rate_pct')):>9}%{fmt(x.get('avg_net_pct')):>12}%"
                  f"{fmt(cmp.get('baseline_avg_net_pct')):>11}%{fmt(cmp.get('edge_avg_net_pct')):>9}%"
                  f"{fmt(x.get('median_net_pct')):>11}%{fmt(x.get('profit_factor')):>10}")
    print("\nEĞİTİM DÖNEMİNE GÖRE EN OYNAK 20 HİSSE — OOS LONG")
    print("-" * 112)
    print(f"{'#':>3}{'hisse':<10}{'bar std':>10}{'med.abs':>10}{'işlem':>8}{'isabet':>10}{'ort.net':>12}{'PF':>10}{'bileşik':>12}{'MDD':>12}")
    print("-" * 112)
    volatile = pd.DataFrame(report["volatile_oos_long"])
    if not volatile.empty:
        for i, r in enumerate(volatile.itertuples(index=False), 1):
            print(f"{i:>3}{r.symbol:<10}{fmt(r.std_bar_pct):>9}%{fmt(r.median_abs_bar_pct):>9}%"
                  f"{int(r.trades):>8}{fmt(r.win_rate_pct):>9}%{fmt(r.avg_net_pct):>11}%"
                  f"{fmt(r.profit_factor):>10}{fmt(r.compound_net_pct):>11}%{fmt(r.max_drawdown_pct):>11}%")
    watchlist = pd.DataFrame(report["watchlist_oos_long"])
    print("\nSIK İZLENEN VOLATİL ADAYLAR — OOS LONG")
    print("-" * 112)
    print(f"{'hisse':<10}{'bar std':>10}{'med.abs':>10}{'işlem':>8}{'isabet':>10}{'ort.net':>12}{'PF':>10}{'bileşik':>12}{'MDD':>12}")
    print("-" * 112)
    if not watchlist.empty:
        for r in watchlist.itertuples(index=False):
            print(f"{r.symbol:<10}{fmt(r.std_bar_pct):>9}%{fmt(r.median_abs_bar_pct):>9}%"
                  f"{int(r.trades):>8}{fmt(r.win_rate_pct):>9}%{fmt(r.avg_net_pct):>11}%"
                  f"{fmt(r.profit_factor):>10}{fmt(r.compound_net_pct):>11}%{fmt(r.max_drawdown_pct):>11}%")
    print("\nNOT: En iyi görünen hisseler aynı veri üzerinde seçildiği için otomatik olarak kanıt sayılmaz.")
    print("Sonraki karar için OOS'ta en az 8 işlem ve farklı dönemlerde aynı yön aranmalıdır.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baslangic", default="2023-10-01")
    parser.add_argument("--split", default="2025-07-01")
    parser.add_argument("--maliyet", type=float, default=DEFAULT_COST,
                        help="round-trip toplam maliyet; varsayılan 0.002 = %%0,20")
    parser.add_argument("--json", default=str(BASE / "magic_ribbon_4s_backtest.json"))
    parser.add_argument("--csv", default=str(BASE / "magic_ribbon_4s_backtest.csv"))
    args = parser.parse_args()
    report, flat_df = run(args)
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=safe_float)
    flat_df.to_csv(args.csv, index=False, encoding="utf-8-sig")
    print_report(report, flat_df)
    print(f"\nKaydedildi: {args.json}")
    print(f"Kaydedildi: {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
