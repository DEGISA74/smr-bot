from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from us200_fetcher import build_four_hour, clean_ohlcv, merge_history
from us200_universe import US200_TICKERS, validate_us200_universe


def _frame(index, closes):
    close = pd.Series(closes, index=index, dtype="float64")
    return pd.DataFrame({
        "Open": close - 0.25,
        "High": close + 0.50,
        "Low": close - 0.50,
        "Close": close,
        "Volume": 1000.0,
    }, index=index)


def test_universe_is_exactly_200_unique_yahoo_symbols():
    validate_us200_universe()
    assert len(US200_TICKERS) == 200
    assert len(set(US200_TICKERS)) == 200
    assert "BRK-B" in US200_TICKERS
    assert not any(symbol.endswith(".IS") for symbol in US200_TICKERS)


def test_hourly_session_builds_two_new_york_anchored_bars():
    tz = ZoneInfo("America/New_York")
    index = pd.date_range("2026-09-04 09:30", periods=7, freq="1h", tz=tz)
    hourly = _frame(index, [100, 101, 102, 103, 104, 105, 106])
    four = build_four_hour(hourly, now=datetime(2026, 9, 5, 12, tzinfo=tz))
    assert list(four.index.hour) == [9, 13]
    assert list(four["SourceBars"]) == [4, 3]
    assert list(four["BarMinutes"]) == [240, 150]
    assert four.iloc[0]["Open"] == 99.75
    assert four.iloc[0]["Close"] == 103.0
    assert four.iloc[1]["Close"] == 106.0
    assert four.iloc[0]["Volume"] == 4000.0
    assert four.iloc[1]["Volume"] == 3000.0


def test_open_session_day_is_not_published_as_complete_four_hour_data():
    tz = ZoneInfo("America/New_York")
    index = pd.date_range("2026-09-04 09:30", periods=4, freq="1h", tz=tz)
    hourly = _frame(index, [100, 101, 102, 103])
    four = build_four_hour(hourly, now=datetime(2026, 9, 4, 13, 45, tzinfo=tz))
    assert four.empty


def test_split_rebase_preserves_old_history_and_new_scale():
    old_index = pd.date_range("2026-08-01", periods=5, freq="D")
    new_index = pd.date_range("2026-08-04", periods=4, freq="D")
    old = _frame(old_index, [100, 102, 104, 106, 108])
    new = _frame(new_index, [10.6, 10.8, 11.0, 11.2])
    merged = merge_history(old, new, interval="1d")
    assert len(merged) == 7
    assert abs(float(merged.iloc[0]["Close"]) - 10.0) < 1e-9
    assert abs(float(merged.loc[pd.Timestamp("2026-08-04"), "Close"]) - 10.6) < 1e-9
    assert abs(float(merged.iloc[0]["Volume"]) - 10000.0) < 1e-9


def test_impossible_ohlc_row_is_rejected():
    index = pd.DatetimeIndex(["2026-09-03", "2026-09-04"])
    frame = _frame(index, [100, 101])
    frame.loc[index[-1], "High"] = 90
    cleaned = clean_ohlcv(frame, interval="1d")
    assert list(cleaned.index) == [pd.Timestamp("2026-09-03")]


def test_us200_batch_mirror_never_downloads_missing_symbol(tmp_path, monkeypatch):
    import data_layer

    monkeypatch.setenv("SMR_US_MIRROR_READONLY", "1")
    monkeypatch.setattr(data_layer, "CACHE_DIR", str(tmp_path))
    _frame(pd.date_range("2025-01-01", periods=500, freq="B"), [100 + i for i in range(500)]).to_parquet(
        tmp_path / "NVDA_1d.parquet"
    )

    def _network_must_not_run(*args, **kwargs):
        raise AssertionError("Ayna modundaki toplu tarama Yahoo'ya gitmemeli")

    monkeypatch.setattr(data_layer.yf, "download", _network_must_not_run)
    data_layer._get_batch_data_cached_versioned.clear()
    batch = data_layer.get_batch_data_cached(["NVDA", "MISSING"], period="1y")

    assert list(dict.fromkeys(batch.columns.get_level_values(0))) == ["NVDA"]
    assert len(batch) == 500
