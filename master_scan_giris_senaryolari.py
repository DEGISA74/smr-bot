# -*- coding: utf-8 -*-
"""Master Scan giriş senaryoları.

Bu modül B11, C6, Zirve Devam ve Radar2 olaylarını iki ayrı yoldan izler:

* anında giriş: sinyal kapanışı (T0) sonrası ilk işlem günü açılış;
* teyitli giriş: T0'dan üç işlem günü sonra üçlü hâlâ 3/3 ise T+4 açılış.

İki yol aynı olay anahtarını paylaşır; böylece teyitli işlem ayrı bir sinyal
gibi sayılmaz. Hesaplar yalnızca T0/T+3'e kadar bilinen günlük veriyi kullanır.
Çıktılar ``gelişmiş tarama`` klasöründe tutulur ve ana scan_signals tablosuna
yazılmaz. Bu nedenle mevcut tarama eşikleri ve Master Scan sıralaması değişmez.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "patron.db"
DAILY_DIR = ROOT / "veriler"
OUT_DIR = ROOT / "gelişmiş tarama"
OUT_CSV = OUT_DIR / "master_scan_giris_senaryolari.csv"
OUT_JSON = OUT_DIR / "master_scan_giris_senaryolari.json"
START_DATE = "2026-08-07"
END_DATE = "2026-09-30"

TARGET_SCANNERS = {
    "er_B11": "B11",
    "er_C6": "C6",
    "zirve_devam": "Zirve Devam",
    "radar2": "Radar2",
}


def _clean_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    return text[:-3] if text.endswith(".IS") else text


def _date_text(value: object) -> str:
    return str(pd.Timestamp(value).date())


def _daily_path(symbol: str) -> Path:
    return DAILY_DIR / f"{_clean_symbol(symbol)}.IS_1d.parquet"


def _read_daily(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        frame = pd.read_parquet(path)
    except Exception:
        return None
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(frame.columns):
        return None
    frame = frame.loc[:, ["Open", "High", "Low", "Close", "Volume"]].copy()
    index = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="coerce"))
    if index.tz is not None:
        index = index.tz_localize(None)
    frame.index = index.normalize()
    frame = frame[~frame.index.isna()]
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    for column in frame.columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["Open", "High", "Low", "Close"])


def _load_calendar(xu: pd.DataFrame) -> list[str]:
    return [str(value.date()) for value in xu.index]


def _date_at(calendar: list[str], start: str, offset: int) -> str | None:
    try:
        position = calendar.index(start)
    except ValueError:
        return None
    target = position + offset
    return calendar[target] if 0 <= target < len(calendar) else None


def _previous_date(calendar: list[str], day: str) -> str | None:
    try:
        position = calendar.index(day)
    except ValueError:
        return None
    return calendar[position - 1] if position > 0 else None


def _indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    close = out["Close"]
    high = out["High"]
    low = out["Low"]
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    out["sma20"] = close.rolling(20).mean()
    out["atr14"] = true_range.rolling(14).mean()
    return out


def _feature_row(
    stock: pd.DataFrame,
    xu: pd.DataFrame,
    day: str,
    rs_base_day: str | None,
    calendar: list[str],
) -> dict[str, object] | None:
    day_ts = pd.Timestamp(day)
    if day_ts not in stock.index or day_ts not in xu.index:
        return None
    stock_i = _indicators(stock)
    row = stock_i.loc[day_ts]
    prev_day = _previous_date(calendar, day)
    if prev_day is None or pd.Timestamp(prev_day) not in stock_i.index or pd.Timestamp(prev_day) not in xu.index:
        return None
    prev_ts = pd.Timestamp(prev_day)
    ratio = float(row["Close"]) / float(xu.loc[day_ts, "Close"])
    if rs_base_day is None or pd.Timestamp(rs_base_day) not in stock_i.index or pd.Timestamp(rs_base_day) not in xu.index:
        rs_base_day = prev_day
    base_ts = pd.Timestamp(rs_base_day)
    base_ratio = float(stock_i.loc[base_ts, "Close"]) / float(xu.loc[base_ts, "Close"])
    close = float(row["Close"])
    previous_close = float(stock_i.loc[prev_ts, "Close"])
    sma20 = float(row["sma20"]) if pd.notna(row["sma20"]) else np.nan
    atr14 = float(row["atr14"]) if pd.notna(row["atr14"]) else np.nan
    atr_move = (
        int((close - previous_close) / atr14 > 0.5)
        if np.isfinite(atr14) and atr14 > 0
        else None
    )
    ma20 = int(close > sma20) if np.isfinite(sma20) else None
    rs_up = int(ratio > base_ratio) if np.isfinite(ratio) and np.isfinite(base_ratio) else None
    values = [rs_up, ma20, atr_move]
    return {
        "relative_strength": rs_up,
        "ma20_above": ma20,
        "atr_strength": atr_move,
        "triple_count": int(sum(value == 1 for value in values if value is not None)),
        "triple_available": int(sum(value is not None for value in values)),
        "triple_pass": int(all(value == 1 for value in values)) if all(value is not None for value in values) else 0,
        "relative_strength_ratio": round(ratio, 8),
        "sma20": round(sma20, 6) if np.isfinite(sma20) else None,
        "atr14": round(atr14, 6) if np.isfinite(atr14) else None,
    }


def _load_events() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    try:
        con = sqlite3.connect(DB_PATH)
        frame = pd.read_sql_query(
            """
            SELECT scan_date, symbol, scan_type, event_id, event_start_date,
                   event_day, is_event_start, bias, entry_price
            FROM scan_signals
            WHERE scan_date >= ? AND scan_date <= ?
              AND scan_type IN ('er_B11', 'er_C6', 'zirve_devam', 'radar2')
            """,
            con,
            params=(START_DATE, END_DATE),
        )
        con.close()
    except Exception:
        return pd.DataFrame()
    if frame.empty:
        return frame
    frame["symbol"] = frame["symbol"].map(_clean_symbol)
    frame["scan_date"] = frame["scan_date"].map(_date_text)
    frame["event_start_date"] = frame["event_start_date"].map(_date_text)
    frame["scan_type"] = frame["scan_type"].astype(str).str.strip()
    frame["is_event_start"] = pd.to_numeric(frame["is_event_start"], errors="coerce").fillna(0).astype(int)
    frame["event_day"] = pd.to_numeric(frame["event_day"], errors="coerce")
    # Bir olayın tekrarlanan T+1/T+2 satırlarını bir kez sinyal kabul ederiz.
    # Önce açık event-start işaretini, yoksa olayın ilk görüldüğü günü kullanırız.
    frame["event_key"] = frame.apply(
        lambda row: f"{row['scan_type']}|{row['symbol']}|{row['event_id']}"
        if pd.notna(row["event_id"])
        else f"{row['scan_type']}|{row['symbol']}|{row['event_start_date']}",
        axis=1,
    )
    starts = frame[frame["is_event_start"] == 1].copy()
    fallback = frame.sort_values("scan_date").drop_duplicates("event_key", keep="first")
    if not starts.empty:
        starts = starts.sort_values("scan_date").drop_duplicates("event_key", keep="first")
        keys = set(starts["event_key"])
        fallback = fallback[~fallback["event_key"].isin(keys)]
    events = pd.concat([starts, fallback], ignore_index=True, sort=False)
    events = events.sort_values(["scan_date", "scan_type", "symbol"])
    return events.drop_duplicates("event_key", keep="first").reset_index(drop=True)


def _outcome(
    stock: pd.DataFrame,
    xu: pd.DataFrame,
    calendar: list[str],
    entry_date: str | None,
    horizon: int,
) -> dict[str, object]:
    if entry_date is None or entry_date not in calendar:
        return {"mature": 0}
    exit_date = _date_at(calendar, entry_date, horizon)
    if exit_date is None or pd.Timestamp(entry_date) not in stock.index or pd.Timestamp(exit_date) not in stock.index:
        return {"mature": 0}
    if pd.Timestamp(entry_date) not in xu.index or pd.Timestamp(exit_date) not in xu.index:
        return {"mature": 0}
    entry = float(stock.loc[pd.Timestamp(entry_date), "Open"])
    if not np.isfinite(entry) or entry <= 0:
        return {"mature": 0}
    exit_close = float(stock.loc[pd.Timestamp(exit_date), "Close"])
    xu_entry = float(xu.loc[pd.Timestamp(entry_date), "Open"])
    xu_exit = float(xu.loc[pd.Timestamp(exit_date), "Close"])
    if not np.isfinite(exit_close) or not np.isfinite(xu_entry) or xu_entry <= 0 or not np.isfinite(xu_exit):
        return {"mature": 0}
    postret = (exit_close / entry - 1) * 100
    xu_return = (xu_exit / xu_entry - 1) * 100
    return {
        "mature": 1,
        "entry_date": entry_date,
        "entry_price": round(entry, 6),
        "exit_date": exit_date,
        "postret": round(postret, 4),
        "alpha_xu100": round(postret - xu_return, 4),
        "xu100_return": round(xu_return, 4),
        "win": int(postret > 0),
    }


def _build_rows() -> list[dict[str, object]]:
    xu = _read_daily(DAILY_DIR / "XU100.IS_1d.parquet")
    if xu is None or xu.empty:
        return []
    calendar = _load_calendar(xu)
    events = _load_events()
    if events.empty:
        return []
    frame_cache: dict[str, pd.DataFrame | None] = {}
    rows: list[dict[str, object]] = []
    for event in events.itertuples(index=False):
        symbol = _clean_symbol(event.symbol)
        signal_date = str(event.scan_date)
        if signal_date not in calendar:
            continue
        stock = frame_cache.get(symbol)
        if symbol not in frame_cache:
            stock = _read_daily(_daily_path(symbol))
            frame_cache[symbol] = stock
        if stock is None:
            continue
        t0 = _feature_row(stock, xu, signal_date, None, calendar)
        if t0 is None:
            continue
        d3 = _date_at(calendar, signal_date, 3)
        t3 = _feature_row(stock, xu, d3, signal_date, calendar) if d3 else None
        immediate_entry = _date_at(calendar, signal_date, 1)
        confirmed_pass = int(t3.get("triple_pass", 0)) if t3 else 0
        confirmed_entry = _date_at(calendar, signal_date, 4) if confirmed_pass else None
        event_id = str(event.event_id) if pd.notna(event.event_id) else str(event.event_start_date)
        row: dict[str, object] = {
            "event_key": str(event.event_key),
            "scanner": str(event.scan_type),
            "scanner_label": TARGET_SCANNERS.get(str(event.scan_type), str(event.scan_type)),
            "symbol": symbol,
            "signal_date": signal_date,
            "event_id": event_id,
            "event_start_date": str(event.event_start_date),
            "immediate_entry_date": immediate_entry,
            "immediate_triple_count": t0["triple_count"],
            "immediate_triple_available": t0["triple_available"],
            "immediate_triple_pass": t0["triple_pass"],
            "immediate_rs": t0["relative_strength"],
            "immediate_ma20": t0["ma20_above"],
            "immediate_atr": t0["atr_strength"],
            "confirmed_decision_date": d3,
            "confirmed_triple_count": t3.get("triple_count") if t3 else None,
            "confirmed_triple_available": t3.get("triple_available") if t3 else None,
            "confirmed_triple_pass": confirmed_pass,
            "confirmed_rs": t3.get("relative_strength") if t3 else None,
            "confirmed_ma20": t3.get("ma20_above") if t3 else None,
            "confirmed_atr": t3.get("atr_strength") if t3 else None,
            "confirmed_entry_date": confirmed_entry,
        }
        for scenario, entry_date in (("immediate", immediate_entry), ("confirmed", confirmed_entry)):
            for horizon in (5, 10, 20):
                result = _outcome(stock, xu, calendar, entry_date, horizon)
                prefix = f"{scenario}_{horizon}"
                for key, value in result.items():
                    row[f"{prefix}_{key}"] = value
        rows.append(row)
    return rows


def _metric_rows(frame: pd.DataFrame) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if frame.empty:
        return rows
    for scanner, group in frame.groupby("scanner", sort=True):
        for scenario, prefix in (("Anında giriş", "immediate"), ("Teyitli giriş", "confirmed")):
            eligible = group.copy()
            pass_column = "immediate_triple_pass" if scenario == "Anında giriş" else "confirmed_triple_pass"
            eligible = eligible[pd.to_numeric(eligible[pass_column], errors="coerce").fillna(0) == 1]
            for horizon in (5, 10, 20):
                mature_col = f"{prefix}_{horizon}_mature"
                ret_col = f"{prefix}_{horizon}_postret"
                alpha_col = f"{prefix}_{horizon}_alpha_xu100"
                if mature_col not in eligible.columns:
                    continue
                mature = eligible[pd.to_numeric(eligible[mature_col], errors="coerce").fillna(0) == 1].copy()
                returns = (
                    pd.to_numeric(mature[ret_col], errors="coerce").dropna()
                    if ret_col in mature.columns else pd.Series(dtype="float64")
                )
                alphas = (
                    pd.to_numeric(mature[alpha_col], errors="coerce").dropna()
                    if alpha_col in mature.columns else pd.Series(dtype="float64")
                )
                wins = returns[returns > 0]
                losses = returns[returns < 0]
                gross_win = float(wins.sum()) if not wins.empty else 0.0
                gross_loss = float(abs(losses.sum())) if not losses.empty else 0.0
                rows.append({
                    "scanner": str(scanner),
                    "scanner_label": TARGET_SCANNERS.get(str(scanner), str(scanner)),
                    "scenario": scenario,
                    "horizon": horizon,
                    "n": int(len(returns)),
                    "win_rate": round(float((returns > 0).mean() * 100), 2) if len(returns) else None,
                    "avg_return": round(float(returns.mean()), 2) if len(returns) else None,
                    "rr": round(float(wins.mean() / abs(losses.mean())), 2) if not wins.empty and not losses.empty and losses.mean() != 0 else None,
                    "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
                    "beat_xu100_pct": round(float((alphas > 0).mean() * 100), 2) if len(alphas) else None,
                    "avg_alpha": round(float(alphas.mean()), 2) if len(alphas) else None,
                })
    return rows


def update_entry_scenarios(*, write_output: bool = True) -> dict[str, object]:
    rows = _build_rows()
    frame = pd.DataFrame(rows)
    metrics = _metric_rows(frame)
    result: dict[str, object] = {
        "as_of": str(frame["signal_date"].max()) if not frame.empty else None,
        "target_end_date": END_DATE,
        "start_date": START_DATE,
        "events": int(len(frame)),
        "triple_immediate": int(frame.get("immediate_triple_pass", pd.Series(dtype=int)).sum()) if not frame.empty else 0,
        "triple_confirmed": int(frame.get("confirmed_triple_pass", pd.Series(dtype=int)).sum()) if not frame.empty else 0,
        "metrics": metrics,
        "output": str(OUT_CSV),
    }
    if write_output:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        frame.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
        OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _load_output() -> pd.DataFrame:
    if not OUT_CSV.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(OUT_CSV)
    except Exception:
        return pd.DataFrame()


def get_radar2_badge(symbol: object) -> str:
    """Radar2 kartına eklenecek güncel üçlü rozeti."""
    frame = _load_output()
    if frame.empty:
        return ""
    symbol = _clean_symbol(symbol)
    rows = frame[(frame["scanner"] == "radar2") & (frame["symbol"] == symbol)].copy()
    if rows.empty:
        return ""
    rows = rows.sort_values("signal_date")
    row = rows.iloc[-1]
    count = _int_value(row.get("immediate_triple_count"))
    if _int_value(row.get("immediate_triple_pass")) == 1:
        return "  ⚡ÜÇLÜ 3/3"
    return f"  · üçlü {count}/3"


def _fmt(value: object, suffix: str = "") -> str:
    value = pd.to_numeric(value, errors="coerce")
    if pd.isna(value):
        return "—"
    return f"{float(value):.1f}{suffix}"


def _int_value(value: object, default: int = 0) -> int:
    number = pd.to_numeric(value, errors="coerce")
    return int(number) if pd.notna(number) else default


def render_master_scan_entry_scenarios() -> None:
    """Master Scan sonuçlarının altında ayrı giriş-senaryosu masası."""
    import streamlit as st

    frame = _load_output()
    st.markdown(
        "<div style='border-left:5px solid #a855f7;padding-left:10px;margin:12px 0 8px;"
        "font-weight:900;font-size:1rem;color:#d8b4fe;'>🚦 GİRİŞ SENARYOLARI — ÜÇLÜ DESTEK MASASI</div>",
        unsafe_allow_html=True,
    )
    if frame.empty:
        st.info("İlk günlük kapanış koleksiyonu henüz oluşmadı; bu masa Master Scan sonrası otomatik dolacak.")
        return
    as_of = str(frame["signal_date"].max())[:10]
    st.caption(
        f"Kapsam: B11 · C6 · Zirve Devam · Radar2 | son sinyal: {as_of} | "
        f"birikim hedefi: {END_DATE}. Üçlü sinyali mevcut taramayı kapatmaz; yalnız kalite rozeti ve ikinci giriş yolu üretir."
    )
    latest = frame.sort_values("signal_date").drop_duplicates(["scanner", "symbol"], keep="last")
    summary_rows: list[dict[str, object]] = []
    for scanner in TARGET_SCANNERS:
        group = latest[latest["scanner"] == scanner]
        summary_rows.append({
            "Tarama": TARGET_SCANNERS.get(str(scanner), str(scanner)),
            "Son aday N": int(len(group)),
            "Anında 3/3": int(pd.to_numeric(group["immediate_triple_pass"], errors="coerce").fillna(0).sum()) if not group.empty else 0,
            "Teyitli 3/3": int(pd.to_numeric(group["confirmed_triple_pass"], errors="coerce").fillna(0).sum()) if not group.empty else 0,
        })
    st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)

    metric_rows = _metric_rows(frame)
    metrics = pd.DataFrame(metric_rows)
    if not metrics.empty:
        metrics = metrics.copy()
        metrics["Senaryo"] = metrics["scenario"]
        metrics["Tarama"] = metrics["scanner_label"]
        metrics["Vade"] = metrics["horizon"].map(lambda value: f"{int(value)}g")
        metrics["Olgun N"] = metrics["n"]
        metrics["Durum"] = metrics["n"].map(
            lambda value: "Karar yok · N<30" if int(value) < 30 else "İzlenebilir · N≥30"
        )
        metrics["Win rate"] = metrics["win_rate"].map(lambda x: _fmt(x, "%"))
        metrics["RR"] = metrics["rr"].map(_fmt)
        metrics["PF"] = metrics["profit_factor"].map(_fmt)
        metrics["BIST'i yenme"] = metrics["beat_xu100_pct"].map(lambda x: _fmt(x, "%"))
        metrics["Ort. alfa"] = metrics["avg_alpha"].map(lambda x: _fmt(x, "%"))
        st.markdown("**Olgun sonuçlar — 5g / 10g / 20g**")
        st.dataframe(
            metrics[["Tarama", "Senaryo", "Vade", "Olgun N", "Durum", "Win rate", "RR", "PF", "BIST'i yenme", "Ort. alfa"]],
            hide_index=True,
            use_container_width=True,
        )
        st.caption("20 günlük N henüz küçükse sonuç karar değil, gözlem fişidir. 5g ve 10g ara sonuçlar erken görünür; nihai kıyas 20g olgunluğunda yapılır.")

    current = latest[latest["scanner"].isin(TARGET_SCANNERS)].copy()
    if not current.empty:
        current["Tarama"] = current["scanner_label"]
        current["Sinyal"] = current["symbol"]
        current["Anında"] = current["immediate_triple_count"].map(lambda x: f"{_int_value(x)}/3")
        current["Teyit"] = current["confirmed_triple_count"].map(lambda x: f"{_int_value(x)}/3")
        current["Teyit girişi"] = current["confirmed_entry_date"].fillna("—")
        st.markdown("**Son adaylarda üçlü durumu**")
        st.dataframe(
            current[["Tarama", "Sinyal", "signal_date", "Anında", "Teyit", "Teyit girişi"]]
            .rename(columns={"signal_date": "Sinyal tarihi"})
            .sort_values(["Tarama", "Sinyal"]),
            hide_index=True,
            use_container_width=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Master Scan giriş senaryoları")
    parser.add_argument("--update", action="store_true", help="DB/parquet'ten senaryo fişini yenile")
    parser.add_argument("--dry-run", action="store_true", help="Hesapla fakat dosyaya yazma")
    args = parser.parse_args()
    result = update_entry_scenarios(write_output=not args.dry_run)
    result["dry_run"] = bool(args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
