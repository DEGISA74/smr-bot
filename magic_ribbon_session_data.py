# -*- coding: utf-8 -*-
"""Magic Ribbon için TradingView 5 dakikalık BIST seans-mumu veri hattı.

Bu hat yalnız Magic Ribbon'a aittir. Yahoo günlük/saatlik kasalarına ve eski
``veriler_4s`` deposuna yazmaz. TradingView'in 5 dakikalık BIST mumlarından
her tam işlem gününde iki sabit seans mumu kurar:

* 09:55–14:00
* 14:00–18:10

TradingView'in herkese açık 5 dakika akışı, işlem oluşmayan 18:00 aralığını
bazı günler hiç göndermeyebilir. Bu tek, kapanıştaki boş aralık OHLC sonucunu
değiştirmez; onun dışındaki her eksik gün tamamen reddedilir. Böylece kapanışa
yetişmemiş veya gün içi deliği olan veri şerit hesabına sessizce giremez.
"""
from __future__ import annotations

from datetime import datetime, time as dtime
import os
from pathlib import Path
import time
from typing import Iterable

import pandas as pd

from provider_traffic import (
    ProviderCooldown,
    acquire_slot,
    record_failure,
    record_success,
)
from bist_calendar import is_trading_day


ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "veriler_magic_ribbon_5m"
SESSION_DIR = ROOT / "veriler_magic_ribbon_seans"
TZ = "Europe/Istanbul"
STALE_DAYS = 3
SESSION_COMPLETE_TIME = dtime(18, 15)
FIRST_START = dtime(9, 55)
SECOND_START = dtime(14, 0)
LAST_BAR_START = dtime(18, 5)
FIRST_LABEL = "09:55–14:00"
SECOND_LABEL = "14:00–18:10"
EXPECTED_BARS_PER_DAY = 99
MIN_REQUEST_GAP_SECONDS = 3.2


class MagicRibbonSessionDataError(RuntimeError):
    """Magic Ribbon'a özgü veri hattı hatası."""


def _symbol(value: object) -> str:
    return str(value or "").strip().upper().replace(".IS", "")


def raw_path(symbol: str) -> Path:
    return RAW_DIR / f"{_symbol(symbol)}.IS_5m.parquet"


def session_path(symbol: str) -> Path:
    return SESSION_DIR / f"{_symbol(symbol)}.IS_session.parquet"


def _ist_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=TZ)


def _as_istanbul_index(index: pd.Index) -> pd.DatetimeIndex:
    result = pd.to_datetime(index)
    if getattr(result, "tz", None) is None:
        return result.tz_localize(TZ)
    return result.tz_convert(TZ)


def _normalise_5m(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(column not in frame.columns for column in required):
        return pd.DataFrame()
    result = frame[required].copy()
    result.index = _as_istanbul_index(result.index)
    result = result[~result.index.duplicated(keep="last")].sort_index()
    for column in required:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["Open", "High", "Low", "Close"])
    return result[
        (result["Open"] > 0)
        & (result["High"] > 0)
        & (result["Low"] > 0)
        & (result["Close"] > 0)
    ]


def _expected_index(day: object, start: dtime, end: dtime) -> pd.DatetimeIndex:
    date_text = pd.Timestamp(day).strftime("%Y-%m-%d")
    return pd.date_range(
        pd.Timestamp(f"{date_text} {start.strftime('%H:%M')}", tz=TZ),
        pd.Timestamp(f"{date_text} {end.strftime('%H:%M')}", tz=TZ),
        freq="5min",
    )


def _aggregate_window(
    frame: pd.DataFrame,
    expected: pd.DatetimeIndex,
    *,
    allowed_empty: pd.DatetimeIndex | None = None,
) -> dict[str, float]:
    allowed = set(allowed_empty) if allowed_empty is not None else set()
    missing = expected.difference(frame.index)
    if any(stamp not in allowed for stamp in missing):
        raise MagicRibbonSessionDataError("Eksik 5 dakikalık seans penceresi")
    window = frame.loc[frame.index.intersection(expected)].sort_index()
    if window[["Open", "High", "Low", "Close"]].isna().any().any():
        raise MagicRibbonSessionDataError("Bozuk 5 dakikalık seans penceresi")
    return {
        "Open": float(window["Open"].iloc[0]),
        "High": float(window["High"].max()),
        "Low": float(window["Low"].min()),
        "Close": float(window["Close"].iloc[-1]),
        "Volume": float(window["Volume"].fillna(0.0).sum()),
    }


def build_session_bars(frame_5m: pd.DataFrame) -> pd.DataFrame:
    """Tam 5 dakikalık günleri iki BIST seans mumuna dönüştürür.

    İlk pencere 09:55–13:55 başlangıçlı 49 mumdan oluşur ve 14:00'te biter.
    İkinci pencere 14:00–18:05 başlangıçlı 50 mumdan oluşur ve 18:10'da biter.
    """
    frame = _normalise_5m(frame_5m)
    if frame.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume", "Session"])

    rows: list[dict[str, object]] = []
    rejected_days: list[str] = []
    for day, daily in frame.groupby(frame.index.date):
        first_expected = _expected_index(day, FIRST_START, dtime(13, 55))
        second_expected = _expected_index(day, SECOND_START, LAST_BAR_START)
        try:
            first = _aggregate_window(daily, first_expected)
            # TradingView bazen işlem geçmeyen 18:00–18:05 aralığını hiç
            # yayınlamaz. 18:05 kapanış barı geldiyse bu tek boşluk toplam
            # Open/High/Low/Close/Hacim sonucunu değiştirmez; başka hiçbir
            # zaman boşluğu kabul edilmez.
            second = _aggregate_window(daily, second_expected, allowed_empty=second_expected[-2:-1])
        except MagicRibbonSessionDataError:
            rejected_days.append(str(day))
            continue
        rows.extend((
            {"Datetime": first_expected[0], **first, "Session": FIRST_LABEL},
            {"Datetime": second_expected[0], **second, "Session": SECOND_LABEL},
        ))

    if not rows:
        result = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume", "Session"])
    else:
        result = pd.DataFrame(rows).set_index("Datetime").sort_index()
    result.attrs["rejected_days"] = rejected_days
    result.attrs["expected_bars_per_day"] = EXPECTED_BARS_PER_DAY
    return result


def _atomic_write(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    frame.to_parquet(temporary)
    os.replace(temporary, path)


def _read_parquet(path: Path) -> pd.DataFrame:
    try:
        return pd.read_parquet(path)
    except (OSError, ValueError, TypeError):
        return pd.DataFrame()


def _merge_frames(old: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    joined = pd.concat([old, fresh], axis=0) if not old.empty else fresh.copy()
    joined = _normalise_5m(joined)
    return joined[~joined.index.duplicated(keep="last")].sort_index()


def _download_5m(symbol: str, period: str) -> pd.DataFrame:
    try:
        from borsapy import download
    except ImportError as exc:
        raise MagicRibbonSessionDataError("TradingView veri katmanı kurulu değil") from exc

    try:
        acquire_slot("borsapy", max_wait=180.0, priority="probe")
    except (ProviderCooldown, TimeoutError) as exc:
        raise MagicRibbonSessionDataError("TradingView trafik sigortası beklemede") from exc
    try:
        result = download(symbol, period=period, interval="5m", progress=False)
    except (ConnectionError, OSError, TimeoutError, ValueError, RuntimeError) as exc:
        record_failure("borsapy", error=str(exc), kind="connection")
        raise MagicRibbonSessionDataError(f"TradingView 5 dakika isteği başarısız: {symbol}") from exc
    result = _normalise_5m(result)
    if result.empty:
        record_failure("borsapy", error=f"bos 5m cevap: {symbol}", kind="empty")
        raise MagicRibbonSessionDataError(f"TradingView 5 dakika verisi boş: {symbol}")
    record_success("borsapy")
    return result


def refresh_symbol(symbol: str, *, bootstrap: bool = False) -> dict[str, object]:
    """Bir sembolün 5 dakika ve iki-seans kasasını günceller.

    İlk kurulumda TradingView'in izin verdiği son yaklaşık 55 seans (``6mo``
    isteği) alınır. Sonraki turlarda yalnız son beş gün yeniden sorulur.
    """
    clean_symbol = _symbol(symbol)
    if not clean_symbol:
        raise MagicRibbonSessionDataError("Boş sembol")
    old = _read_parquet(raw_path(clean_symbol))
    period = "6mo" if bootstrap or old.empty else "5d"
    fresh = _download_5m(clean_symbol, period)
    combined = _merge_frames(old, fresh)
    sessions = build_session_bars(combined)
    if sessions.empty:
        raise MagicRibbonSessionDataError(f"Tam seans mumu oluşmadı: {clean_symbol}")
    _atomic_write(combined, raw_path(clean_symbol))
    _atomic_write(sessions, session_path(clean_symbol))
    return {
        "symbol": clean_symbol,
        "raw_rows": int(len(combined)),
        "session_rows": int(len(sessions)),
        "last_session": str(sessions.index[-1]),
        "rejected_days": list(sessions.attrs.get("rejected_days", [])),
    }


def refresh_symbols(
    symbols: Iterable[str], *, bootstrap: bool = False,
    min_gap_seconds: float = MIN_REQUEST_GAP_SECONDS,
) -> dict[str, object]:
    """Sembolleri tek tek ve düşük tempoyla yeniler; hata alanı liste dışına taşmaz."""
    ordered = sorted({_symbol(symbol) for symbol in symbols if _symbol(symbol)})
    result: dict[str, object] = {"ok": [], "failed": [], "total": len(ordered)}
    last_started: float | None = None
    for symbol in ordered:
        if last_started is not None:
            wait_for = min_gap_seconds - (time.monotonic() - last_started)
            if wait_for > 0:
                time.sleep(wait_for)
        last_started = time.monotonic()
        try:
            result["ok"].append(refresh_symbol(symbol, bootstrap=bootstrap))
        except MagicRibbonSessionDataError as exc:
            result["failed"].append({"symbol": symbol, "reason": str(exc)})
    return result


def get_magic_ribbon_session_data(symbol: str) -> pd.DataFrame | None:
    """Yalnız taze ve kapanmış BIST seans mumlarını döndürür."""
    frame = _read_parquet(session_path(symbol))
    if frame.empty or len(frame) < 2:
        return None
    required = ["Open", "High", "Low", "Close"]
    if any(column not in frame.columns for column in required):
        return None
    frame = frame.copy()
    frame.index = _as_istanbul_index(frame.index)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    last_day = frame.index[-1].date()
    now = _ist_now()
    if (now.date() - last_day).days > STALE_DAYS:
        return None
    # Master Scan kapanıştan sonra çalışır. İşlem günü akşamında önceki seansın
    # verisini aday listesine taşımak, "güncel" diye bayat sinyal göstermektir.
    if is_trading_day(now.date()) and now.time() >= SESSION_COMPLETE_TIME and last_day != now.date():
        return None
    if last_day == now.date() and now.time() < SESSION_COMPLETE_TIME:
        frame = frame[frame.index.date < now.date()]
    if len(frame) < 2:
        return None
    return frame


def session_label(timestamp: object) -> str:
    try:
        stamp = pd.Timestamp(timestamp).tz_convert(TZ) if pd.Timestamp(timestamp).tzinfo else pd.Timestamp(timestamp)
    except (TypeError, ValueError):
        return "—"
    return SECOND_LABEL if stamp.time() >= SECOND_START else FIRST_LABEL


def session_close_timestamp(timestamp: object) -> pd.Timestamp | None:
    try:
        stamp = pd.Timestamp(timestamp)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(TZ)
        else:
            stamp = stamp.tz_convert(TZ)
    except (TypeError, ValueError):
        return None
    end = dtime(18, 10) if stamp.time() >= SECOND_START else dtime(14, 0)
    return stamp.normalize() + pd.Timedelta(hours=end.hour, minutes=end.minute)


# ── SEANS SÜREKLİLİĞİ (1 Eyl 2026) ────────────────────────────────────────────
# Bozuk günü tamamen reddetme kuralı veriyi temiz tutuyor ama arkasında bir
# tuzak bırakıyordu: geriye kalan mumlar hesapta YAN YANA sayılıyordu. Araya bir
# hafta girmiş iki mum arasındaki "eğim" gerçek eğim değildir; "10 mum sonra"
# dediğimiz vade de delikli hissede 5 iş gününü aşar. Aşağıdaki yardımcılar
# deliği görünür kılar: hangi mumun bir öncekiyle GERÇEKTEN komşu olduğunu ve
# bir sembolün penceresinde kaç işlem gününün eksik olduğunu söyler.

def _previous_trading_day(day: object, limit: int = 12) -> object | None:
    """Verilen günden önceki en yakın BIST işlem gününü döner."""
    current = pd.Timestamp(day).date()
    for _ in range(limit):
        current = current - pd.Timedelta(days=1)
        if is_trading_day(current):
            return current
    return None


def contiguous_prev_mask(frame: pd.DataFrame) -> pd.Series:
    """Her mum için: bir önceki mum gerçekten bir önceki seans mı?

    Sabah mumunun komşusu bir önceki işlem gününün öğleden sonra mumudur;
    öğleden sonra mumunun komşusu aynı günün sabah mumudur. İlk mum daima
    False (öncesi yok).
    """
    if frame is None or frame.empty:
        return pd.Series(dtype=bool)
    index = _as_istanbul_index(frame.index)
    flags = [False] * len(index)
    for position in range(1, len(index)):
        current, previous = index[position], index[position - 1]
        if current.time() >= SECOND_START:
            flags[position] = (
                previous.date() == current.date() and previous.time() < SECOND_START
            )
        else:
            expected_day = _previous_trading_day(current.date())
            flags[position] = (
                expected_day is not None
                and previous.date() == expected_day
                and previous.time() >= SECOND_START
            )
    return pd.Series(flags, index=index)


def session_block_ids(frame: pd.DataFrame) -> pd.Series:
    """Kesintisiz mum bloklarını numaralar; delikte numara artar.

    Aynı numaraya sahip mumlar aralarında delik OLMADAN birbirini izler.
    """
    mask = contiguous_prev_mask(frame)
    if mask.empty:
        return pd.Series(dtype=int)
    return (~mask).cumsum()


def session_gap_report(frame: pd.DataFrame) -> dict[str, object]:
    """Bir sembolün seans penceresindeki delikleri sayar."""
    bos = {
        "gun": 0, "beklenen_gun": 0, "eksik_gun": 0, "kapsama": 0.0,
        "en_uzun_bosluk": 0, "eksik_gunler": [], "son_delik_uzakligi": None,
    }
    if frame is None or frame.empty:
        return bos
    index = _as_istanbul_index(frame.index)
    days = sorted({stamp.date() for stamp in index})
    if not days:
        return bos
    expected = [
        stamp.date()
        for stamp in pd.date_range(days[0], days[-1], freq="D")
        if is_trading_day(stamp.date())
    ]
    present = set(days)
    missing = [day for day in expected if day not in present]

    longest = run = 0
    for day in expected:
        run = run + 1 if day in missing else 0
        longest = max(longest, run)

    son_uzaklik = None
    if missing:
        # Son delikten bugüne kaç işlem günü geçti (taze delik daha zararlı).
        son_delik = max(missing)
        son_uzaklik = sum(1 for day in expected if day > son_delik and day in present)

    return {
        "gun": len(days),
        "beklenen_gun": len(expected),
        "eksik_gun": len(missing),
        "kapsama": round(len(days) / len(expected), 4) if expected else 0.0,
        "en_uzun_bosluk": longest,
        "eksik_gunler": [str(day) for day in missing],
        "son_delik_uzakligi": son_uzaklik,
    }
