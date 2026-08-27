#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BME — Breakout Momentum Engine bağımsız backtesti.

Kaynak Pine kodu paylaşılmadığı için bu, BME'nin pazarlama metnindeki bileşenlerin
şeffaf ve tekrar edilebilir bir uygulamasıdır; orijinal göstergenin birebir klonu
değildir. Yalnız yerel veriler_saatlik/ dosyalarını okur. Canlı uygulamaya, günlük
veri kasasına, tarama kurallarına veya veritabanlarına yazmaz.

Test çerçevesi:
  - Evren: veriler/.index_components.json içindeki güncel BIST 100 üyeleri
  - Zaman dilimi: seans başlangıcına bağlı 1 saatlik mumlardan üretilen 4 saatlik mum
  - Giriş: sinyal mumunun teyit edilmiş kapanışı
  - İleri vadeler: 1 / 5 / 10 / 20 işlem günü (2 / 10 / 20 / 40 adet 4s mum)
  - Maliyet: her sonuçtan temkinli %0,20 gidiş-dönüş maliyet düşülür
  - Short sonuçları teoriktir; BIST'te açığa satış erişimi ve maliyeti değişkendir.

Çıktı:
  - bme_backtest_report.md
  - bme_backtest_results.json
"""
from __future__ import annotations

import json
import sys
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from signal_policy import (
    MEASUREMENT_REGIME_FALLING,
    MEASUREMENT_REGIME_RISING,
    measurement_regime_series,
)

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
HOURLY_DIR = ROOT / "veriler_saatlik"
MEMBERS_FILE = ROOT / "veriler" / ".index_components.json"
REPORT_FILE = ROOT / "bme_backtest_report.md"
RESULTS_FILE = ROOT / "bme_backtest_results.json"

# BME metninde parametre verilmediğinden, her biri raporda açıkça belirtilen
# muhafazakâr varsayımlar kullanılır. Bu değerlerle sonradan optimizasyon yapılmaz.
STRUCTURE_BARS = 20
EMA_FAST = 20
EMA_SLOW = 50
ADX_PERIOD = 14
VOLUME_PERIOD = 20
VOL_Z_PERIOD = 50
ATR_PERIOD = 14
COMPRESSION_PERIOD = 50
COMPRESSION_LOOKBACK = 10
EVENT_COOLDOWN = 10
RETEST_WINDOW = 6
ROUND_TRIP_COST_PCT = 0.20
HORIZONS = {"1g": 2, "5g": 10, "10g": 20, "20g": 40}


def _clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame | None:
    """Bozuk/tekrarlı saatlik satırları yalnız test belleğinde dışarıda bırakır."""
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    required = ["Open", "High", "Low", "Close", "Volume"]
    if not set(required).issubset(df.columns):
        return None
    out = df[required].copy()
    out.index = pd.to_datetime(out.index)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    for col in required:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    valid = (
        out[["Open", "High", "Low", "Close"]].gt(0).all(axis=1)
        & (out["High"] >= out[["Open", "Close", "Low"]].max(axis=1))
        & (out["Low"] <= out[["Open", "Close", "High"]].min(axis=1))
        & (out["Volume"] >= 0)
    )
    out = out.loc[valid].dropna(subset=required)
    return out if not out.empty else None


def to_session_4h(hourly: pd.DataFrame) -> pd.DataFrame:
    """intraday_4s.py ile aynı şekilde 09:30'a bağlı 4 saatlik BIST mumları kurar."""
    # 09:30–12:30 ilk mum; 13:30–17:30 ikinci mumdur. Son saat ikinci muma
    # katıldığı için günlük döngü+resample yerine aynı seans anahtarını bir kez
    # üretiriz; 100 hisselik backtestte sonucu değiştirmeden belirgin hız kazandırır.
    local_day = hourly.index.normalize()
    second_bar = (hourly.index.hour >= 13).astype(int)
    bucket = local_day + pd.to_timedelta(9 + 4 * second_bar, unit="h") + pd.Timedelta(minutes=30)
    staged = hourly.copy()
    staged["_bucket"] = bucket
    out = staged.groupby("_bucket", sort=True).agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(subset=["Open", "High", "Low", "Close"])
    return out[out["Volume"] > 0]


def _rma(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def add_bme_features(df: pd.DataFrame) -> pd.DataFrame:
    """Yalnız ilgili mum ve geçmişini kullanan BME bileşenleri."""
    out = df.copy()
    open_, high, low, close, volume = (out[c].astype(float) for c in ["Open", "High", "Low", "Close", "Volume"])

    previous_close = close.shift(1)
    tr = pd.concat([high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1).max(axis=1)
    atr = _rma(tr, ATR_PERIOD)
    out["atr"] = atr
    out["atr_pct"] = atr / close.replace(0, np.nan)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=out.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=out.index)
    plus_di = 100 * _rma(plus_dm, ADX_PERIOD) / atr.replace(0, np.nan)
    minus_di = 100 * _rma(minus_dm, ADX_PERIOD) / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    out["adx"] = _rma(dx, ADX_PERIOD)
    out["plus_di"] = plus_di
    out["minus_di"] = minus_di

    delta = close.diff()
    gains = _rma(delta.clip(lower=0), 14)
    losses = _rma((-delta.clip(upper=0)), 14)
    out["rsi"] = 100 - 100 / (1 + gains / losses.replace(0, np.nan))
    out["roc"] = close.pct_change(10) * 100
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out["macd_hist"] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
    out["ema_fast"] = close.ewm(span=EMA_FAST, adjust=False).mean()
    out["ema_slow"] = close.ewm(span=EMA_SLOW, adjust=False).mean()

    volume_mean = volume.shift(1).rolling(VOLUME_PERIOD).mean()
    volume_std = volume.shift(1).rolling(VOL_Z_PERIOD).std(ddof=0)
    out["rvol"] = volume / volume_mean.replace(0, np.nan)
    out["volume_z"] = (volume - volume_mean) / volume_std.replace(0, np.nan)

    # Sıkışma, bugünkü kırılımdan önceki barlarda aranır; bugünün ATR'si geçmişe
    # taşınmaz. Böylece geleceği görme riski oluşmaz.
    prior_atr_pct = out["atr_pct"].shift(1)
    compression_floor = out["atr_pct"].shift(2).rolling(COMPRESSION_PERIOD).quantile(0.20)
    compressed_bar = prior_atr_pct <= compression_floor
    out["compression_recent"] = compressed_bar.rolling(COMPRESSION_LOOKBACK).max().fillna(0).astype(bool)
    out["atr_expansion"] = tr >= atr.shift(1) * 1.20

    out["bull_level"] = high.shift(1).rolling(STRUCTURE_BARS).max()
    out["bear_level"] = low.shift(1).rolling(STRUCTURE_BARS).min()
    out["bull_break"] = close > out["bull_level"]
    out["bear_break"] = close < out["bear_level"]
    out["body_ratio"] = (close - open_).abs() / (high - low).replace(0, np.nan)
    out["close_location"] = (close - low) / (high - low).replace(0, np.nan)

    # Uyarlanır eşik: yüksek oynaklıkta veya yön gücü zayıfken sinyal daha pahalı
    # olmalı. Bu, yatay pazardaki tek mumluk taşmaları azaltmak içindir.
    high_volatility = prior_atr_pct > prior_atr_pct.rolling(100).quantile(0.80)
    out["adaptive_threshold"] = np.where(high_volatility | (out["adx"] < 20), 65, 60)
    return out


def score_bar(row: pd.Series, side: str) -> tuple[float, dict[str, float]]:
    """Kırılım zaten oluşmuşsa, açıklamadaki 0–100 kalite puanını hesaplar."""
    is_bull = side == "LONG"
    level = row["bull_level"] if is_bull else row["bear_level"]
    close = row["Close"]
    atr = row["atr"]
    if not np.isfinite(level) or not np.isfinite(atr) or atr <= 0:
        return np.nan, {}

    trend_full = (close > row["ema_fast"] > row["ema_slow"]) if is_bull else (close < row["ema_fast"] < row["ema_slow"])
    trend_partial = (close > row["ema_fast"]) if is_bull else (close < row["ema_fast"])
    dmi_ok = (row["plus_di"] > row["minus_di"]) if is_bull else (row["minus_di"] > row["plus_di"])
    rsi_ok = row["rsi"] >= 55 if is_bull else row["rsi"] <= 45
    roc_ok = row["roc"] > 0 if is_bull else row["roc"] < 0
    macd_ok = row["macd_hist"] > 0 if is_bull else row["macd_hist"] < 0
    color_ok = row["Close"] > row["Open"] if is_bull else row["Close"] < row["Open"]
    location_ok = row["close_location"] >= 0.70 if is_bull else row["close_location"] <= 0.30
    distance = (close - level) / atr if is_bull else (level - close) / atr

    parts = {
        "structure": 20.0,
        "trend": 15.0 if trend_full else (7.0 if trend_partial else 0.0),
        "adx_dmi": 10.0 if row["adx"] >= 20 and dmi_ok else 0.0,
        "rsi": 5.0 if rsi_ok else 0.0,
        "roc": 5.0 if roc_ok else 0.0,
        "macd": 5.0 if macd_ok else 0.0,
        "rvol": 8.0 if row["rvol"] >= 1.20 else 0.0,
        "volume_z": 7.0 if row["volume_z"] >= 1.00 else 0.0,
        "compression": 5.0 if bool(row["compression_recent"]) else 0.0,
        "atr_expansion": 5.0 if bool(row["atr_expansion"]) else 0.0,
        "body": 5.0 if color_ok and row["body_ratio"] >= 0.55 else 0.0,
        "close_location": 5.0 if location_ok else 0.0,
        "distance": 5.0 if 0 <= distance <= 1.50 else (2.0 if distance <= 2.50 else 0.0),
    }
    return float(sum(parts.values())), parts


def _side_return(entry: float, future_close: float, side: str) -> float:
    raw = (future_close / entry - 1.0) * 100.0
    return raw if side == "LONG" else -raw


def _forward_metrics(df: pd.DataFrame, entry_index: int, side: str) -> dict[str, float | None]:
    entry = float(df["Close"].iloc[entry_index])
    result: dict[str, float | None] = {}
    for label, bars in HORIZONS.items():
        if entry_index + bars >= len(df):
            result[f"gross_{label}"] = None
            result[f"net_{label}"] = None
            continue
        gross = _side_return(entry, float(df["Close"].iloc[entry_index + bars]), side)
        result[f"gross_{label}"] = round(gross, 4)
        result[f"net_{label}"] = round(gross - ROUND_TRIP_COST_PCT, 4)

    # İlk beş işlem günündeki en iyi/en kötü potansiyel; satılabilir sonuç değildir,
    # fakat stop mesafesi hakkında çıplak kapanış getirisinden daha fazla bilgi verir.
    window = min(10, len(df) - entry_index - 1)
    if window > 0:
        highs = df["High"].iloc[entry_index + 1:entry_index + 1 + window]
        lows = df["Low"].iloc[entry_index + 1:entry_index + 1 + window]
        if side == "LONG":
            mfe = (float(highs.max()) / entry - 1.0) * 100.0
            mae = (float(lows.min()) / entry - 1.0) * 100.0
        else:
            mfe = (entry / float(lows.min()) - 1.0) * 100.0
            mae = (entry / float(highs.max()) - 1.0) * 100.0
        result["mfe_5g"] = round(mfe, 4)
        result["mae_5g"] = round(mae, 4)
    else:
        result["mfe_5g"] = None
        result["mae_5g"] = None
    return result


def _event_outcomes(df: pd.DataFrame, event_index: int, side: str, level: float, atr: float) -> tuple[bool, int | None]:
    """Kırılım sonrasındaki FAIL veya başarıyla tutunan ilk retest mumunu bulur."""
    end = min(event_index + RETEST_WINDOW, len(df) - 1)
    for j in range(event_index + 1, end + 1):
        close = float(df["Close"].iloc[j])
        low = float(df["Low"].iloc[j])
        high = float(df["High"].iloc[j])
        if side == "LONG":
            if close < level - 0.50 * atr:
                return True, None
            if low <= level + 0.25 * atr and close >= level:
                return False, j
        else:
            if close > level + 0.50 * atr:
                return True, None
            if high >= level - 0.25 * atr and close <= level:
                return False, j
    return False, None


def _classify(score: float) -> str:
    if score >= 80:
        return "EXTREME"
    if score >= 70:
        return "STRONG"
    return "QUALIFIED"


def _market_regime() -> dict[str, str]:
    """XU100'ün günlük kapanışından tarih-bazlı piyasa bağlamı kurar.

    Endeksin saatlik dosyasında hacim sıfır olduğundan 4 saatlik depoda mumlar
    haklı olarak elenir. Bu yalnız segmentleme katmanıdır; BME sinyal puanına
    girmez. Bu yüzden yerel, onaylı günlük XU100 kapanışıyla günlük rejim kurmak
    daha dürüst bir referanstır.
    """
    path = ROOT / "veriler" / "XU100.IS_1d.parquet"
    if not path.exists():
        return {}
    source = _clean_ohlcv(pd.read_parquet(path))
    if source is None or len(source) < 60:
        return {}
    regimes = measurement_regime_series(source)
    return {
        str(pd.Timestamp(timestamp).date()): (
            "BOĞA" if state == MEASUREMENT_REGIME_RISING else "AYI/YATAY"
        )
        for timestamp, state in regimes.dropna().items()
        if state in (MEASUREMENT_REGIME_RISING, MEASUREMENT_REGIME_FALLING)
    }


def _read_members() -> list[str]:
    payload = json.loads(MEMBERS_FILE.read_text(encoding="utf-8"))
    return list(payload["XU100"]["members"])


def _summary(rows: list[dict], prefix: str = "net_") -> dict:
    summary: dict[str, object] = {"events": len(rows)}
    for label in HORIZONS:
        values = [r[f"{prefix}{label}"] for r in rows if r.get(f"{prefix}{label}") is not None]
        if not values:
            summary[label] = None
            continue
        values_np = np.asarray(values, dtype=float)
        summary[label] = {
            "n": int(len(values_np)),
            "hit_pct": round(float((values_np > 0).mean() * 100), 1),
            "median_pct": round(float(np.median(values_np)), 2),
            "mean_pct": round(float(np.mean(values_np)), 2),
        }
    for metric in ("mfe_5g", "mae_5g"):
        values = [r[metric] for r in rows if r.get(metric) is not None]
        summary[metric] = round(float(np.median(values)), 2) if values else None
    if rows:
        summary["fail_6bar_pct"] = round(float(np.mean([bool(r["failed_6bar"]) for r in rows]) * 100), 1)
        summary["retest_6bar_pct"] = round(float(np.mean([bool(r["retest_confirmed"]) for r in rows]) * 100), 1)
    else:
        summary["fail_6bar_pct"] = None
        summary["retest_6bar_pct"] = None
    return summary


def _cell(stats: dict | None) -> str:
    if not stats:
        return "—"
    return f"%{stats['hit_pct']:.1f} / {stats['median_pct']:+.2f}% (N={stats['n']})"


def _table_line(label: str, rows: list[dict]) -> str:
    s = _summary(rows)
    return "| " + " | ".join([
        label,
        str(s["events"]),
        _cell(s["1g"]),
        _cell(s["5g"]),
        _cell(s["10g"]),
        _cell(s["20g"]),
        f"{s['mfe_5g']:+.2f}% / {s['mae_5g']:+.2f}%" if s["mfe_5g"] is not None else "—",
        f"%{s['fail_6bar_pct']:.1f}" if s["fail_6bar_pct"] is not None else "—",
        f"%{s['retest_6bar_pct']:.1f}" if s["retest_6bar_pct"] is not None else "—",
    ]) + " |"


def _baseline_rows(df: pd.DataFrame, side: str) -> list[dict]:
    rows: list[dict] = []
    max_horizon = max(HORIZONS.values())
    for i in range(120, len(df) - max_horizon):
        item = _forward_metrics(df, i, side)
        item.update({"failed_6bar": False, "retest_confirmed": False})
        rows.append(item)
    return rows


def collect() -> tuple[list[dict], list[dict], dict, dict]:
    members = _read_members()
    regime = _market_regime()
    events: list[dict] = []
    retests: list[dict] = []
    baseline = {"LONG": [], "SHORT": []}
    coverage = {"members": len(members), "loaded": 0, "excluded": {}, "first": None, "last": None}

    for position, ticker in enumerate(members, start=1):
        path = HOURLY_DIR / f"{ticker}.IS_1h.parquet"
        if not path.exists():
            coverage["excluded"][ticker] = "dosya_yok"
            continue
        try:
            hourly = _clean_ohlcv(pd.read_parquet(path))
            if hourly is None:
                coverage["excluded"][ticker] = "okunabilir_ohlcv_yok"
                continue
            df = to_session_4h(hourly)
            if len(df) < 180:
                coverage["excluded"][ticker] = f"yetersiz_4s_mum:{len(df)}"
                continue
            df = add_bme_features(df)
        except Exception as exc:
            coverage["excluded"][ticker] = f"okuma_hatasi:{type(exc).__name__}"
            continue

        coverage["loaded"] += 1
        first, last = str(df.index.min()), str(df.index.max())
        coverage["first"] = first if coverage["first"] is None or first < coverage["first"] else coverage["first"]
        coverage["last"] = last if coverage["last"] is None or last > coverage["last"] else coverage["last"]
        baseline["LONG"].extend(_baseline_rows(df, "LONG"))
        baseline["SHORT"].extend(_baseline_rows(df, "SHORT"))

        last_event = {"LONG": -EVENT_COOLDOWN - 1, "SHORT": -EVENT_COOLDOWN - 1}
        for i in range(120, len(df)):
            row = df.iloc[i]
            for side, break_col, level_col in [
                ("LONG", "bull_break", "bull_level"),
                ("SHORT", "bear_break", "bear_level"),
            ]:
                if not bool(row[break_col]) or i - last_event[side] <= EVENT_COOLDOWN:
                    continue
                score, parts = score_bar(row, side)
                threshold = float(row["adaptive_threshold"])
                if not np.isfinite(score) or score < threshold:
                    continue

                level = float(row[level_col])
                atr = float(row["atr"])
                failed, retest_index = _event_outcomes(df, i, side, level, atr)
                market_state = regime.get(str(pd.Timestamp(df.index[i]).date()), "BİLİNMİYOR")
                event = {
                    "ticker": ticker,
                    "timestamp": str(df.index[i]),
                    "side": side,
                    "class": _classify(score),
                    "score": round(score, 1),
                    "threshold": round(threshold, 1),
                    "level": round(level, 5),
                    "entry": round(float(row["Close"]), 5),
                    "market_regime": str(market_state),
                    "failed_6bar": bool(failed),
                    "retest_confirmed": retest_index is not None,
                    "parts": {name: round(value, 1) for name, value in parts.items()},
                }
                event.update(_forward_metrics(df, i, side))
                events.append(event)
                last_event[side] = i

                if retest_index is not None:
                    retest = {key: value for key, value in event.items() if key not in {"parts", "entry"}}
                    retest["timestamp"] = str(df.index[retest_index])
                    retest["entry"] = round(float(df["Close"].iloc[retest_index]), 5)
                    retest["retest_of_timestamp"] = event["timestamp"]
                    retest.update(_forward_metrics(df, retest_index, side))
                    retests.append(retest)

        # Ara ilerleme yazısı özellikle çıkarıldı: uzun yerel testlerde çıktı kanalı
        # kapanırsa hesap tamamlanmadan kesilmemeli. Sonuç sayısı finalde yazılır.

    return events, retests, baseline, coverage


def make_report(events: list[dict], retests: list[dict], baseline: dict, coverage: dict) -> tuple[str, dict]:
    lines = [
        "# Breakout Momentum Engine — BIST 100 4 Saatlik Backtest",
        "",
        "## Doğrulama sınırı",
        "",
        "Bu çalışma, gönderilen BME tanımındaki bileşenleri yerel saatlik fiyat dosyalarına uygulayan bağımsız prototiptir. Orijinal TradingView/Pine kodu, parametreleri, puan ağırlıkları ve gerçek event-state kuralı paylaşılmadığı için **orijinal göstergenin doğrulanmış performansı değildir**.",
        "",
        "Sinyal puanı yalnız sinyal mumunun kapanışında bilinen bilgiyle hesaplanır. Retest ve FAIL ise kırılımdan sonraki en fazla 6 adet 4 saatlik mumda gözlenen durumdur; retest performansında giriş retesti doğrulayan mumun kapanışından başlar.",
        "",
        "## Veri kapsamı",
        "",
        f"- Güncel BIST 100 listesi: {coverage['members']} hisse; testte yeterli 4 saatlik geçmişi olan: {coverage['loaded']} hisse.",
        f"- 4 saatlik veri aralığı: {coverage['first']} → {coverage['last']}.",
        f"- Dışarıda kalan: {len(coverage['excluded'])} hisse ({', '.join(coverage['excluded']) if coverage['excluded'] else 'yok'}).",
        "- 1 saatlik mumlar BIST seansı 09:30'a bağlanarak iki adet 4 saatlik muma dönüştürüldü; seansın son artığı ikinci muma katıldı.",
        "",
        "## Sabit test kuralları",
        "",
        f"- Yapısal kırılım: önceki {STRUCTURE_BARS} adet 4 saatlik mumun en yüksek/en düşük seviyesinin kapanışla aşılması.",
        f"- Puan: yapı 20, EMA rejimi 15, ADX/DMI 10, RSI/ROC/MACD 15, RVOL/Z-hacim 15, sıkışma+ATR genişlemesi 10, mum kalitesi 10, kırılım mesafesi 5 puan.",
        "- Eşik: normal ortamda 60; ADX 20 altındaysa veya ATR oynaklığı kendi geçmişinin üst %20'sindeyse 65. STRONG 70+, EXTREME 80+.",
        f"- Aynı yönde tekrar sayımı önlemek için {EVENT_COOLDOWN} adet 4 saatlik mum bekleme kuralı kullanıldı.",
        f"- İleri getiriler: 1/5/10/20 işlem günü. Net sütunlar sonuçtan %{ROUND_TRIP_COST_PCT:.2f} toplam maliyet düşer; gerçek makas ve açığa satış maliyeti hisseye göre farklıdır.",
        "- SHORT sonuçları matematiksel yön testidir; BIST'te her hissede sürekli uygulanabilir bir işlem kuralı sayılmaz.",
        "",
        "## Referans: her uygun 4 saatlik mumdan yön almak",
        "",
        "Hücre: net pozitif sonuç oranı / medyan net getiri. Bu, sinyalin piyasanın doğal yön sapmasını geçip geçmediğini görmek için referanstır.",
        "",
        "| Yön | Gözlem | 1 gün | 5 gün | 10 gün | 20 gün |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    baseline_summary = {}
    for side in ("LONG", "SHORT"):
        summary = _summary(baseline[side])
        baseline_summary[side] = summary
        lines.append("| " + " | ".join([side, str(summary["events"]), _cell(summary["1g"]), _cell(summary["5g"]), _cell(summary["10g"]), _cell(summary["20g"])]) + " |")

    lines.extend([
        "",
        "## Ana sinyaller",
        "",
        "Hücre: net pozitif sonuç oranı / medyan net getiri. MFE/MAE = ilk 5 işlem gününde medyan en iyi / en kötü fiyat hareketi. FAIL = kırılımdan sonra 6 adet 4 saatlik mum içinde seviyenin 0,5 ATR ötesinde kapanışla bozulması. Retest = aynı pencerede seviyeye dokunup yapıyı kapanışta koruması.",
        "",
        "| Sinyal | Olay | 1 gün | 5 gün | 10 gün | 20 gün | 5g MFE / MAE | FAIL | Retest |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    signal_summary: dict[str, dict] = {}
    for side in ("LONG", "SHORT"):
        for cls in ("TÜM", "QUALIFIED", "STRONG", "EXTREME"):
            rows = [event for event in events if event["side"] == side and (cls == "TÜM" or event["class"] == cls)]
            label = f"{side} — {cls}"
            lines.append(_table_line(label, rows))
            signal_summary[label] = _summary(rows)

    lines.extend([
        "",
        "## Piyasa yönüne göre ayrım",
        "",
        "Piyasa yönü, XU100'ün aynı günkü günlük kapanışta kendi 50 günlük EMA'sının üstünde/altında olmasına göre belirlendi. Bu yalnız sonuç segmentidir; BME puanına dahil edilmedi.",
        "",
        "| Sinyal | Olay | 1 gün | 5 gün | 10 gün | 20 gün | 5g MFE / MAE | FAIL | Retest |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    regime_summary: dict[str, dict] = {}
    for side in ("LONG", "SHORT"):
        for market in ("BOĞA", "AYI/YATAY"):
            rows = [event for event in events if event["side"] == side and event["market_regime"] == market]
            label = f"{side} — XU100 {market}"
            lines.append(_table_line(label, rows))
            regime_summary[label] = _summary(rows)

    lines.extend([
        "",
        "## Retest sonrası gerçekçi giriş",
        "",
        "Bu bölüm ilk kırılımı değil, retesti doğrulayan mumun kapanışından sonraki getiriyi ölçer. Bu nedenle ilk sinyal performansıyla doğrudan karşılaştırırken girişin daha geç olduğu unutulmamalıdır.",
        "",
        "| Sinyal | Olay | 1 gün | 5 gün | 10 gün | 20 gün | 5g MFE / MAE | FAIL | Retest |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    retest_summary: dict[str, dict] = {}
    for side in ("LONG", "SHORT"):
        for cls in ("TÜM", "QUALIFIED", "STRONG", "EXTREME"):
            rows = [event for event in retests if event["side"] == side and (cls == "TÜM" or event["class"] == cls)]
            label = f"{side} — {cls}"
            lines.append(_table_line(label, rows))
            retest_summary[label] = _summary(rows)

    score_counts = Counter((event["side"], event["class"]) for event in events)
    serializable = {
        "method": {
            "source_code_available": False,
            "interval": "1h yerel parquet -> seans-bağlı 4h",
            "structure_bars": STRUCTURE_BARS,
            "event_cooldown_4h_bars": EVENT_COOLDOWN,
            "retest_window_4h_bars": RETEST_WINDOW,
            "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            "horizons": HORIZONS,
        },
        "coverage": coverage,
        "event_count": len(events),
        "retest_count": len(retests),
        "score_counts": {f"{side}_{cls}": count for (side, cls), count in score_counts.items()},
        "baseline_summary": baseline_summary,
        "signal_summary": signal_summary,
        "regime_summary": regime_summary,
        "retest_summary": retest_summary,
        "events": events,
        "retest_events": retests,
    }
    return "\n".join(lines) + "\n", serializable


def main() -> int:
    if not MEMBERS_FILE.exists() or not HOURLY_DIR.exists():
        print("BIST 100 üye dosyası veya saatlik parquet klasörü bulunamadı.")
        return 1
    print("BME 4 saatlik olayları toplanıyor (100 BIST hissesi, yalnız yerel parquet)...")
    events, retests, baseline, coverage = collect()
    report, result = make_report(events, retests, baseline, coverage)
    REPORT_FILE.write_text(report, encoding="utf-8")
    RESULTS_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nTamamlandı: {len(events)} ana olay · {len(retests)} onaylı retest")
    print(f"Rapor: {REPORT_FILE.name}")
    print(f"Ham sonuç: {RESULTS_FILE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
