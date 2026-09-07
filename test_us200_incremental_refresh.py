from __future__ import annotations

import pandas as pd

import us200_fetcher as fetcher


def _frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    values = range(1, len(index) + 1)
    return pd.DataFrame(
        {
            "Open": [100.0 + value for value in values],
            "High": [101.0 + value for value in values],
            "Low": [99.0 + value for value in values],
            "Close": [100.5 + value for value in values],
            "Volume": [1_000_000.0] * len(index),
        },
        index=index,
    )


def _daily_path(tmp_path, monkeypatch, frame: pd.DataFrame):
    daily = tmp_path / "daily"
    daily.mkdir()
    monkeypatch.setattr(fetcher, "DAILY_DIR", daily)
    path = daily / "NVDA_1d.parquet"
    frame.to_parquet(path)
    return path


def test_daily_refresh_requests_only_tail_when_history_is_complete(tmp_path, monkeypatch):
    old = _frame(pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=5), periods=540))
    _daily_path(tmp_path, monkeypatch, old)
    calls = []

    def fake_download(symbol, **kwargs):
        calls.append(kwargs)
        return _frame(pd.bdate_range(old.index[-1], periods=2))

    monkeypatch.setattr(fetcher, "_download", fake_download)
    result = fetcher.refresh_daily("NVDA")

    assert result["ok"] is True
    assert result["mode"] == "tail"
    assert "period" not in calls[0]
    assert calls[0]["start"] == (old.index[-1] - pd.Timedelta(days=1)).date().isoformat()


def test_daily_refresh_repairs_missing_history_from_its_first_gap(tmp_path, monkeypatch):
    old = _frame(pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=5), periods=30))
    _daily_path(tmp_path, monkeypatch, old)
    calls = []

    def fake_download(symbol, **kwargs):
        calls.append(kwargs)
        return _frame(pd.DatetimeIndex([old.index[0] - pd.Timedelta(days=1), old.index[0]]))

    monkeypatch.setattr(fetcher, "_download", fake_download)
    result = fetcher.refresh_daily("NVDA")

    assert result["ok"] is True
    assert result["mode"] == "head_repair"
    assert "period" not in calls[0]
    assert calls[0]["end"] == (old.index[0] + pd.Timedelta(days=1)).date().isoformat()


def test_empty_yahoo_reply_never_overwrites_existing_daily_history(tmp_path, monkeypatch):
    old = _frame(pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=5), periods=540))
    path = _daily_path(tmp_path, monkeypatch, old)
    monkeypatch.setattr(fetcher, "_download", lambda *args, **kwargs: pd.DataFrame(columns=fetcher.OHLCV))

    result = fetcher.refresh_daily("NVDA")
    stored = pd.read_parquet(path)

    assert result["ok"] is False
    assert result["reason"] == "boş veya kapanmamış cevap"
    assert len(stored) == len(old)
    assert stored.index.max() == old.index.max()
