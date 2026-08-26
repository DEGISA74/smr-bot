#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kapanis oncesi motoru icin islem yapilabilir BIST evreni denetimi.

Bu dosya V1/V2/V3'u degistirmez ve aday secmez. Yalnizca bir hissenin
17:35 civarinda gercekci bicimde alinip alinmayacagini denetler:

* Gunluk likidite, fiyat gecmisi ve baz bozulmasi,
* Saatlik verinin o gun gercekten taze olmasi,
* Resmi pazar / tedbir bilgisinin harici dosyadan gelmesi.

Resmi durum dosyasi istege baglidir. Dosya olmadan program hisseyi
``RESMI_DURUM_BEKLIYOR`` diye isaretler; onu ANA EVREN'e koymaz.

Ornek:
  python kapanis_oncesi_evren.py --as-of 2026-08-05T17:35
  python kapanis_oncesi_evren.py --as-of 2026-08-05T17:35 \
      --restrictions kapanis_oncesi_resmi_durum.json --output evren.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


ISTANBUL = ZoneInfo("Europe/Istanbul")
EXCLUDED_PREFIXES = ("XU", "XB", "XT", "XY")
HARD_MARKETS = {"YIP", "YAKIN_IZLEME", "POIP", "POIP"}
CORE_MARKETS = {"YILDIZ", "ANA"}
CONDITIONAL_MARKETS = {"ALT", "GSP"}
RESTRICTION_WORDS = ("VBTS", "BRUT", "TEK_FIYAT", "TEK FIYAT")


@dataclass(frozen=True)
class Policy:
    capital_tl: float = 1_000_000.0
    top_n: int = 5
    min_history_days: int = 180
    min_turnover_tl: float = 25_000_000.0
    alt_market_min_turnover_tl: float = 50_000_000.0
    max_corporate_action_move_pct: float = 35.0
    corporate_action_lookback_days: int = 20
    hourly_bar_minutes: int = 60
    session_start: str = "09:30"
    decision_time: str = "17:35"

    def allocation_table(self) -> list[dict[str, float | int]]:
        """Az adayda pay basina riskin nasil buyudugunu acik eder."""
        rows: list[dict[str, float | int]] = []
        for count in range(1, self.top_n + 1):
            allocation = self.capital_tl / count
            # Tek pozisyon, 20 gunluk ortanca cironun en fazla %1,33'u olsun.
            turnover_need = max(self.min_turnover_tl, allocation * 75.0)
            # Son iki saatte en az 10 katlik fiili donus yoksa emir tasinmaz.
            final_two_hours_need = allocation * 10.0
            rows.append(
                {
                    "candidate_count": count,
                    "allocation_per_ticker_tl": allocation,
                    "required_median_daily_turnover_tl": turnover_need,
                    "required_final_two_hours_turnover_tl": final_two_hours_need,
                }
            )
        return rows


def _ticker_from_daily_path(path: Path) -> str:
    return path.name.replace(".IS_1d.parquet", "").upper()


def _normalise_market(value: object) -> str:
    text = str(value or "").strip().upper()
    replacements = str.maketrans({"İ": "I", "Ş": "S", "Ğ": "G", "Ü": "U", "Ö": "O", "Ç": "C"})
    return text.translate(replacements).replace("-", "_").replace(" ", "_")


def _as_of(value: str | None) -> datetime:
    if not value:
        return datetime.now(ISTANBUL)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ISTANBUL)
    return parsed.astimezone(ISTANBUL)


def _numeric_daily(path: Path) -> pd.DataFrame:
    data = pd.read_parquet(path)
    needed = ("Open", "High", "Low", "Close", "Volume")
    if any(column not in data.columns for column in needed):
        raise ValueError("OHLCV eksik")
    data = data.loc[:, needed].copy()
    data.index = pd.DatetimeIndex(pd.to_datetime(data.index)).tz_localize(None).normalize()
    data = data[~data.index.duplicated(keep="last")].sort_index()
    for column in needed:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    return data.dropna(subset=["Close", "Volume"])


def _hourly_completed_bars(path: Path, as_of: datetime, policy: Policy) -> tuple[int, int, float, str | None]:
    """Sadece karari verirken tamamlanmis mumlari sayar.

    Yahoo'nun acik olan saatlik mumu da dosyaya gelebilir. O mumun fiyatini
    sinyale katmak gelecegi bilmek degildir ama canli/historik karsilastirmayi
    bozar; bu nedenle bitisi ``as_of`` anindan once olmayan mum sayilmaz.
    """
    if not path.exists():
        return 0, 0, 0.0, None
    data = pd.read_parquet(path)
    if data.empty or not isinstance(data.index, pd.DatetimeIndex):
        return 0, 0, 0.0, None
    index = pd.DatetimeIndex(pd.to_datetime(data.index))
    if index.tz is None:
        index = index.tz_localize(ISTANBUL)
    else:
        index = index.tz_convert(ISTANBUL)
    frame = data.copy()
    frame.index = index
    day = as_of.date()
    same_day = frame.loc[frame.index.date == day].copy()
    if same_day.empty:
        last = index.max().isoformat() if len(index) else None
        return 0, 0, 0.0, last

    bar_end = same_day.index + pd.Timedelta(minutes=policy.hourly_bar_minutes)
    completed = same_day.loc[bar_end <= pd.Timestamp(as_of)]
    start_hour, start_minute = (int(part) for part in policy.session_start.split(":"))
    session_start = datetime.combine(day, time(start_hour, start_minute), tzinfo=ISTANBUL)
    expected = max(0, int((as_of - session_start).total_seconds() // 3600))
    expected = min(expected, 8)  # 17:30'da biten 16:30 mumu dahil; 17:30 aktif mum degil.
    turnover = 0.0
    if not completed.empty and {"Close", "Volume"}.issubset(completed.columns):
        turnover = float(
            (pd.to_numeric(completed["Close"], errors="coerce") * pd.to_numeric(completed["Volume"], errors="coerce"))
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
            .tail(2)
            .sum()
        )
    return int(len(completed)), expected, turnover, index.max().isoformat()


def _load_status(path: Path | None) -> tuple[dict[str, str], dict[str, str], str | None]:
    if path is None:
        return {}, {}, None
    payload = json.loads(path.read_text(encoding="utf-8"))
    markets = {str(key).upper(): _normalise_market(value) for key, value in dict(payload.get("markets", {})).items()}
    restrictions = {str(key).upper(): _normalise_market(value) for key, value in dict(payload.get("restrictions", {})).items()}
    source = str(payload.get("source") or path)
    return markets, restrictions, source


def _row_for_ticker(
    ticker: str,
    daily_path: Path,
    hourly_dir: Path,
    as_of: datetime,
    policy: Policy,
    markets: dict[str, str],
    restrictions: dict[str, str],
) -> dict[str, Any]:
    reasons: list[str] = []
    try:
        daily = _numeric_daily(daily_path)
    except Exception as exc:
        return {"ticker": ticker, "bucket": "YASAK", "reasons": [f"GUNLUK_VERI_HATASI:{type(exc).__name__}"]}

    # Ayni gunun yarim gunluk kaydini asla gecmis ortalamasina katma.
    history = daily.loc[daily.index.date < as_of.date()].copy()
    history_days = int(len(history))
    turnover = history["Close"] * history["Volume"] if not history.empty else pd.Series(dtype=float)
    median_turnover = float(turnover.tail(20).median()) if len(turnover) >= 20 else 0.0
    max_move = float(history["Close"].pct_change().abs().tail(policy.corporate_action_lookback_days).max() * 100.0) if len(history) >= 2 else np.nan

    if history_days < policy.min_history_days:
        reasons.append("YETERSIZ_GECMIS")
    if median_turnover < policy.min_turnover_tl:
        reasons.append("CIRO_YETERSIZ")
    if np.isfinite(max_move) and max_move >= policy.max_corporate_action_move_pct:
        reasons.append("BAZ_VEYA_KURUMSAL_ISLEM_RISKI")

    hourly_path = hourly_dir / f"{ticker}.IS_1h.parquet"
    completed, expected, last_two_hour_turnover, hourly_last = _hourly_completed_bars(hourly_path, as_of, policy)
    if expected and completed < expected:
        reasons.append("SAATLIK_VERI_BAYAT_VEYA_EKSIK")

    market = markets.get(ticker)
    restriction = restrictions.get(ticker)
    if restriction and any(word in restriction for word in RESTRICTION_WORDS):
        reasons.append(f"AKTIF_TEDBIR:{restriction}")
    if market in HARD_MARKETS:
        reasons.append(f"YASAK_PAZAR:{market}")

    # Saatlik tazelik bir hissenin kalici karakteri degil, sadece bugunun
    # islem kapisidir. Ciro/gecmis/tedbir gibi kalici nedenlerden ayri tut.
    temporary_data_issue = "SAATLIK_VERI_BAYAT_VEYA_EKSIK" in reasons
    permanent_fail = any(reason != "SAATLIK_VERI_BAYAT_VEYA_EKSIK" for reason in reasons)
    official_unknown = market is None
    if permanent_fail:
        bucket = "YASAK"
    elif temporary_data_issue:
        bucket = "BUGUN_ISLEM_DISI"
    elif official_unknown:
        bucket = "RESMI_DURUM_BEKLIYOR"
    elif market in CONDITIONAL_MARKETS:
        if median_turnover < policy.alt_market_min_turnover_tl:
            bucket = "YASAK"
            reasons.append("ALT_PAZAR_ICIN_CIRO_YETERSIZ")
        else:
            bucket = "KONTROLLU"
    elif market in CORE_MARKETS:
        bucket = "ANA"
    else:
        bucket = "RESMI_DURUM_BEKLIYOR"

    return {
        "ticker": ticker,
        "bucket": bucket,
        "market": market,
        "restriction": restriction,
        "history_days": history_days,
        "median_turnover_20_tl": median_turnover,
        "max_abs_daily_move_20_pct": max_move,
        "completed_hourly_bars": completed,
        "expected_hourly_bars": expected,
        "last_two_completed_hours_turnover_tl": last_two_hour_turnover,
        "hourly_last_timestamp": hourly_last,
        "reasons": reasons,
    }


def audit(
    data_dir: Path,
    hourly_dir: Path,
    as_of: datetime,
    policy: Policy,
    restrictions_path: Path | None,
) -> dict[str, Any]:
    markets, restrictions, official_source = _load_status(restrictions_path)
    rows: list[dict[str, Any]] = []
    for daily_path in sorted(data_dir.glob("*.IS_1d.parquet")):
        ticker = _ticker_from_daily_path(daily_path)
        if ticker.startswith(EXCLUDED_PREFIXES):
            continue
        rows.append(_row_for_ticker(ticker, daily_path, hourly_dir, as_of, policy, markets, restrictions))
    frame = pd.DataFrame(rows)
    counts = frame["bucket"].value_counts().to_dict() if not frame.empty else {}
    return {
        "as_of": as_of.isoformat(),
        "official_status_source": official_source,
        "policy": asdict(policy),
        "allocation_policy": policy.allocation_table(),
        "summary": {"scanned_tickers": len(frame), "buckets": counts},
        "rows": frame.sort_values(["bucket", "median_turnover_20_tl", "ticker"], ascending=[True, False, True]).to_dict("records") if not frame.empty else [],
    }


def main() -> int:
    # Task Scheduler/Windows terminali bazen Türkçe oklarla yazılmış metni
    # cp1254'e sigdiramaz; denetimin rapor yazimini etkilemesine izin verme.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Kapanis oncesi islem evreni denetimi")
    parser.add_argument("--data-dir", default="veriler")
    parser.add_argument("--hourly-dir", default="veriler_saatlik")
    parser.add_argument("--as-of", help="Turkiye saatiyle YYYY-AA-GGTHH:DD")
    parser.add_argument("--restrictions", help="Resmi pazar ve tedbir JSON dosyasi")
    parser.add_argument("--output", help="JSON rapor yolu")
    args = parser.parse_args()

    result = audit(
        Path(args.data_dir),
        Path(args.hourly_dir),
        _as_of(args.as_of),
        Policy(),
        Path(args.restrictions) if args.restrictions else None,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print("\nSermaye dağılımı:")
    for row in result["allocation_policy"]:
        print(
            f"{row['candidate_count']} aday → hisse başı {row['allocation_per_ticker_tl']:,.0f} TL · "
            f"min günlük ciro {row['required_median_daily_turnover_tl']:,.0f} TL · "
            f"son 2 saat {row['required_final_two_hours_turnover_tl']:,.0f} TL"
        )
    if args.output:
        destination = Path(args.output)
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\nRapor yazıldı: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
