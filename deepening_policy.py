# -*- coding: utf-8 -*-
"""Seçili taramalar için kalite, geç kalma ve olay yolculuğu kuralları.

Bu katman ana tarama kapılarını değiştirmez. B11, RSI Pozitif Uyumsuzluk ve
Zirve Devam sinyallerine ikinci bir kalite notu ekler; Radar2 ile C6'yı tek
liderlik yaşam döngüsünde birleştirir.
"""

from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd


QUALITY_COLUMNS = (
    ("quality_score", "REAL"),
    ("quality_label", "TEXT"),
    ("quality_detail", "TEXT"),
    ("journey_stage", "TEXT"),
    ("journey_age", "INTEGER"),
    ("journey_key", "TEXT"),
)


def _frame(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            next(
                (
                    str(part).title()
                    for part in column
                    if str(part).lower() in {"open", "high", "low", "close", "volume"}
                ),
                "_".join(map(str, column)),
            )
            for column in df.columns
        ]
    aliases = {str(column).strip().lower(): column for column in df.columns}
    required = {}
    for name in ("open", "high", "low", "close", "volume"):
        source = aliases.get(name)
        if source is None:
            return pd.DataFrame()
        required[source] = name.title()
    df = df.rename(columns=required)[list(required.values())].copy()
    df.index = pd.to_datetime(df.index, errors="coerce")
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df = df[~df.index.isna()]
    df = df[~df.index.duplicated(keep="last")].sort_index()
    for column in df.columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = df["Close"].shift(1)
    true_range = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - previous).abs(),
            (df["Low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period, min_periods=period).mean()


def money_flow_quality(raw: pd.DataFrame) -> dict:
    """Fiyat-hacim izlerinden destekleyici para akışı kalite notu üretir.

    Bu ölçüm kurum kimliği görmez; gerçek fon girişi iddiası kurmaz.
    """
    df = _frame(raw)
    if len(df) < 30:
        return {
            "score": 50,
            "label": "VERİ YETERSİZ",
            "support_count": 0,
            "risk_count": 0,
            "detail": "Para akışı kalitesi için en az 30 seans gerekir.",
        }

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float).clip(lower=0)

    spread = (high - low).replace(0, np.nan)
    multiplier = (((close - low) - (high - close)) / spread).replace(
        [np.inf, -np.inf], np.nan
    ).fillna(0.0)
    money_volume = multiplier * volume
    volume20 = float(volume.tail(20).sum())
    cmf20 = float(money_volume.tail(20).sum() / volume20) if volume20 > 0 else 0.0

    direction = np.sign(close.diff()).fillna(0.0)
    obv = (direction * volume).cumsum()
    obv_denominator = float(volume.tail(10).sum())
    obv_impulse = (
        float((obv.iloc[-1] - obv.iloc[-11]) / obv_denominator)
        if len(obv) >= 11 and obv_denominator > 0
        else 0.0
    )

    change = close.diff()
    up_volume = float(volume.where(change > 0, 0.0).tail(20).sum())
    down_volume = float(volume.where(change < 0, 0.0).tail(20).sum())
    udvr20 = up_volume / down_volume if down_volume > 0 else (3.0 if up_volume > 0 else 1.0)

    force = change.fillna(0.0) * volume
    force_fast = force.ewm(span=2, adjust=False).mean()
    force_slow = force.ewm(span=13, adjust=False).mean()
    force_positive = bool(force_fast.iloc[-1] > 0 and force_slow.iloc[-1] > 0)
    force_negative = bool(force_fast.iloc[-1] < 0 and force_slow.iloc[-1] < 0)

    support = []
    risk = []
    if cmf20 >= 0.05:
        support.append(f"20 seanslık para dengesi pozitif ({cmf20:+.2f})")
    elif cmf20 <= -0.05:
        risk.append(f"20 seanslık para dengesi negatif ({cmf20:+.2f})")
    if obv_impulse >= 0.10:
        support.append("hacim akışı son 10 seansta yükseliyor")
    elif obv_impulse <= -0.10:
        risk.append("hacim akışı son 10 seansta geriliyor")
    if udvr20 >= 1.15:
        support.append(f"yükseliş günlerinin hacmi baskın ({udvr20:.2f}x)")
    elif udvr20 <= 0.85:
        risk.append(f"düşüş günlerinin hacmi baskın ({udvr20:.2f}x)")
    if force_positive:
        support.append("fiyat-hacim gücü iki hızda da pozitif")
    elif force_negative:
        risk.append("fiyat-hacim gücü iki hızda da negatif")

    raw_score = 50 + 12.5 * (len(support) - len(risk))
    score = int(round(float(np.clip(raw_score, 0, 100))))
    if score >= 75:
        label = "GÜÇLÜ DESTEK"
    elif score >= 63:
        label = "DESTEKLEYİCİ"
    elif score <= 25:
        label = "DAĞITIM RİSKİ"
    elif score <= 38:
        label = "ZAYIF AKIŞ"
    else:
        label = "NÖTR"

    pieces = []
    if support:
        pieces.append("Destek: " + "; ".join(support))
    if risk:
        pieces.append("Risk: " + "; ".join(risk))
    if not pieces:
        pieces.append("Fiyat-hacim izleri belirgin bir taraf göstermiyor.")
    return {
        "score": score,
        "label": label,
        "support_count": len(support),
        "risk_count": len(risk),
        "support": support,
        "risk": risk,
        "cmf20": round(cmf20, 4),
        "obv_impulse_10": round(obv_impulse, 4),
        "udvr20": round(float(udvr20), 3),
        "detail": " | ".join(pieces),
    }


def late_entry_profile(raw: pd.DataFrame) -> dict:
    """Fiyatın doğal tabanından ne kadar uzaklaştığını ölçer."""
    df = _frame(raw)
    if len(df) < 30:
        return {
            "state": "VERİ YETERSİZ",
            "score": 50,
            "extension_atr": None,
            "run5_pct": None,
            "run10_pct": None,
            "detail": "Geç kalma ölçümü için en az 30 seans gerekir.",
        }
    close = df["Close"].astype(float)
    atr = _atr(df)
    sma20 = close.rolling(20).mean()
    current = float(close.iloc[-1])
    atr_now = float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else 0.0
    extension_atr = (
        float((current - float(sma20.iloc[-1])) / atr_now) if atr_now > 0 else 0.0
    )
    run5 = float((current / float(close.iloc[-6]) - 1.0) * 100.0)
    run10 = float((current / float(close.iloc[-11]) - 1.0) * 100.0)
    prior_high = float(df["High"].iloc[-21:-1].max())
    breakout_pct = (
        float((current / prior_high - 1.0) * 100.0) if prior_high > 0 else 0.0
    )

    if extension_atr > 2.25 or run5 > 12.0 or run10 > 20.0:
        state = "GEÇ KALMIŞ"
        # Bu bir eleme puanı değildir. Geçmiş B11 örneklerinde uzamış grup
        # getiriyi korurken daha derin ters hareket gördü; bu yüzden "kovalama
        # riski" olarak orta-alt notlanır, sıfırlanmaz.
        score = 45
    elif extension_atr <= 1.25 and run5 <= 6.0 and breakout_pct <= 3.0:
        state = "ZAMANINDA"
        score = 100
    else:
        state = "SINIRDA"
        score = 60
    return {
        "state": state,
        "score": score,
        "extension_atr": round(extension_atr, 2),
        "run5_pct": round(run5, 2),
        "run10_pct": round(run10, 2),
        "breakout_pct": round(breakout_pct, 2),
        "detail": (
            f"20 seanslık ortalamadan {extension_atr:.2f} oynaklık birimi uzakta; "
            f"5 seans {run5:+.1f}%, 10 seans {run10:+.1f}%."
        ),
    }


def b11_pilot_profile(raw: pd.DataFrame) -> dict:
    flow = money_flow_quality(raw)
    late = late_entry_profile(raw)
    quality_score = int(round(flow["score"] * 0.65 + late["score"] * 0.35))
    if late["state"] == "GEÇ KALMIŞ":
        label = "KOVALAMA RİSKİ"
    elif flow["score"] >= 63 and flow["risk_count"] <= 1:
        label = "GÜÇLÜ PİLOT"
    elif flow["score"] <= 38:
        label = "AKIŞ TEYİDİ ZAYIF"
    else:
        label = "TEYİT BEKLİYOR"
    return {
        "quality_score": quality_score,
        "quality_label": label,
        "flow": flow,
        "late": late,
        "detail": f"{flow['detail']} | Zamanlama: {late['detail']}",
    }


def distribution_profile(raw: pd.DataFrame) -> dict:
    """Zirve yakınında satışa dönüşen hacim izlerini kalite notuna çevirir."""
    df = _frame(raw)
    if len(df) < 35:
        return {
            "score": 50,
            "label": "VERİ YETERSİZ",
            "risk_count": 0,
            "detail": "Dağıtım kontrolü için en az 35 seans gerekir.",
        }
    close = df["Close"].astype(float)
    open_ = df["Open"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float)
    average_volume = volume.rolling(20).mean()

    red = close < open_
    high_volume = volume > average_volume * 1.20
    distribution_days = int((red & high_volume).tail(10).sum())

    spread = (high - low).replace(0, np.nan)
    close_position = ((close - low) / spread).fillna(0.5)
    upper_wick = ((high - pd.concat([open_, close], axis=1).max(axis=1)) / spread).fillna(0.0)
    rejection_days = int(
        ((close_position < 0.35) & (upper_wick > 0.35) & high_volume).tail(10).sum()
    )

    flow = money_flow_quality(df)
    risk = []
    if distribution_days >= 2:
        risk.append(f"son 10 seansta {distribution_days} hacimli satış günü")
    if rejection_days >= 1:
        risk.append(f"{rejection_days} yüksek hacimli üstten satış mumu")
    if flow["cmf20"] <= -0.05:
        risk.append("20 seanslık para dengesi negatif")
    if flow["obv_impulse_10"] <= -0.10:
        risk.append("hacim akışı fiyatı desteklemiyor")
    if flow["udvr20"] <= 0.85:
        risk.append("düşüş günlerinin hacmi baskın")

    risk_count = len(risk)
    if risk_count >= 3:
        label = "DAĞITIM RİSKİ"
        score = 20
    elif risk_count == 2:
        label = "DİKKAT"
        score = 50
    else:
        label = "SAĞLIKLI DEVAM"
        score = 85 if risk_count == 0 else 70
    return {
        "score": score,
        "label": label,
        "risk_count": risk_count,
        "distribution_days": distribution_days,
        "rejection_days": rejection_days,
        "flow": flow,
        "detail": (
            "; ".join(risk)
            if risk
            else "Hacimli satış kümelenmesi veya belirgin para çıkışı görülmedi."
        ),
    }


def leadership_profile(raw: pd.DataFrame, benchmark) -> dict:
    """Bir hissenin liderliğe yeni mi geçtiğini, yoksa sinyalin yaşlandığını ölçer."""
    df = _frame(raw)
    if len(df) < 80 or benchmark is None:
        return {
            "leader_age": 0,
            "rs20_pct": 0.0,
            "late_state": "VERİ YETERSİZ",
            "detail": "Liderlik yaşı için yeterli hisse/endeks geçmişi yok.",
        }
    if isinstance(benchmark, pd.DataFrame):
        if "Close" not in benchmark.columns:
            return {
                "leader_age": 0,
                "rs20_pct": 0.0,
                "late_state": "VERİ YETERSİZ",
                "detail": "Endeks kapanışı bulunamadı.",
            }
        bench = benchmark["Close"].copy()
    else:
        bench = pd.Series(benchmark).copy()
    bench.index = pd.to_datetime(bench.index, errors="coerce")
    if getattr(bench.index, "tz", None) is not None:
        bench.index = bench.index.tz_localize(None)
    bench = pd.to_numeric(bench, errors="coerce").dropna().sort_index()

    stock, bench = df["Close"].align(bench, join="inner")
    if len(stock) < 60:
        return {
            "leader_age": 0,
            "rs20_pct": 0.0,
            "late_state": "VERİ YETERSİZ",
            "detail": "Hisse ile endeksin ortak takvimi yetersiz.",
        }
    ratio = stock / bench.replace(0, np.nan)
    rolling_high = ratio.rolling(20, min_periods=20).max()
    near_high = ratio >= rolling_high * 0.98
    sma50 = stock.rolling(50, min_periods=50).mean()
    leader_days = near_high & (stock > sma50)
    age = 0
    for value in reversed(leader_days.fillna(False).tolist()):
        if not value:
            break
        age += 1
    stock_ret = float(stock.iloc[-1] / stock.iloc[-21] - 1.0)
    bench_ret = float(bench.iloc[-1] / bench.iloc[-21] - 1.0)
    rs20 = (stock_ret - bench_ret) * 100.0
    late = late_entry_profile(df)
    late_state = "GEÇ SİNYAL" if age > 10 or late["state"] == "GEÇ KALMIŞ" else late["state"]
    return {
        "leader_age": int(age),
        "rs20_pct": round(rs20, 2),
        "late_state": late_state,
        "extension_atr": late.get("extension_atr"),
        "detail": (
            f"Endekse karşı liderlik {age} seanstır sürüyor; "
            f"20 seanslık göreceli fark {rs20:+.1f}%. {late['detail']}"
        ),
    }


def leadership_stage(
    radar2_candidate: bool,
    c6_confirmed: bool,
    leader_age: int = 0,
    late_state: str = "",
) -> dict:
    age = max(0, int(leader_age or 0))
    is_late = age > 10 or str(late_state).upper() in {"GEÇ SİNYAL", "GEÇ KALMIŞ"}
    if radar2_candidate and c6_confirmed and is_late:
        stage, score = "GEÇ SİNYAL", 25
    elif radar2_candidate and c6_confirmed and age <= 3:
        stage, score = "YENİ LİDER", 90
    elif radar2_candidate and c6_confirmed:
        stage, score = "LİDERLİK TEYİTLİ", 75
    elif radar2_candidate:
        stage, score = "ADAY", 55
    elif c6_confirmed and is_late:
        stage, score = "ESKİ LİDER", 25
    elif c6_confirmed:
        stage, score = "C6 TEYİTLİ", 65
    else:
        stage, score = "YOK", 0
    scan_type = {
        "ADAY": "liderlik_aday",
        "YENİ LİDER": "liderlik_yeni",
        "LİDERLİK TEYİTLİ": "liderlik_teyitli",
        "GEÇ SİNYAL": "liderlik_gec",
        "C6 TEYİTLİ": "liderlik_c6",
        "ESKİ LİDER": "liderlik_gec",
    }.get(stage, "liderlik_yok")
    return {"stage": stage, "quality_score": score, "scan_type": scan_type}


def build_leadership_lifecycle(radar2_df, erken_radar_df) -> pd.DataFrame:
    if radar2_df is None or not hasattr(radar2_df, "empty") or radar2_df.empty:
        return pd.DataFrame()
    c6_symbols = set()
    if (
        erken_radar_df is not None
        and hasattr(erken_radar_df, "empty")
        and not erken_radar_df.empty
        and "ScenarioId" in erken_radar_df.columns
    ):
        c6 = erken_radar_df[
            (erken_radar_df["ScenarioId"].astype(str) == "C6")
            & (erken_radar_df["Role"].isin(["primary", "confirmation"]))
        ]
        c6_symbols = {
            str(symbol).upper().replace(".IS", "")
            for symbol in c6.get("Sembol", pd.Series(dtype=str))
        }
    rows = []
    for _, row in radar2_df.iterrows():
        symbol = str(row.get("Sembol", "") or "")
        if not symbol:
            continue
        clean = symbol.upper().replace(".IS", "")
        c6_confirmed = clean in c6_symbols
        leader_age = int(row.get("Liderlik_Yasi", 0) or 0)
        late_state = str(row.get("Gec_Kalma", "") or "")
        stage = leadership_stage(True, c6_confirmed, leader_age, late_state)
        rows.append(
            {
                "Sembol": symbol,
                "Fiyat": row.get("Fiyat"),
                "Skor": stage["quality_score"],
                "Liderlik_Asamasi": stage["stage"],
                "Liderlik_Yasi": leader_age,
                "Radar2": True,
                "C6": c6_confirmed,
                "Gec_Kalma": late_state,
                "Kalite_Skoru": stage["quality_score"],
                "Kalite": stage["stage"],
                "Kalite_Detay": str(row.get("Liderlik_Detay", "") or ""),
                "Yolculuk_Asamasi": stage["stage"],
                "Yolculuk_Gunu": leader_age,
                "Yolculuk_Anahtari": "radar2_c6_liderlik",
                "Liderlik_Tarama": stage["scan_type"],
            }
        )
    return pd.DataFrame(rows)


def rsi_journey(raw: pd.DataFrame, signal: dict, max_sessions: int = 60) -> dict:
    """RSI olayını gerçekçi sonraki açılıştan stop/1R/2R/tepe yolculuğuna çevirir."""
    from signal_policy import resolve_next_open_entry

    df = _frame(raw)
    entry = resolve_next_open_entry(
        df,
        signal.get("Sinyal_Tarihi"),
        bias="bullish",
        apply_bist_limit=True,
        max_locked_sessions=3,
    )
    if not str(entry.get("status", "")).startswith("filled"):
        return {
            "status": entry.get("status", "GİRİŞ BEKLİYOR"),
            "stage": "GİRİŞ BEKLİYOR",
            "entry_date": entry.get("entry_date"),
            "entry_price": entry.get("entry_price"),
            "days_observed": 0,
        }
    stop = float(signal.get("Stop", 0) or 0)
    entry_price = float(entry["entry_price"])
    risk = entry_price - stop
    if stop <= 0 or risk <= 0:
        return {
            "status": "invalid_risk",
            "stage": "RİSK HATALI",
            "entry_date": entry.get("entry_date"),
            "entry_price": entry_price,
            "days_observed": 0,
        }

    start = int(entry["entry_pos"])
    end = min(len(df), start + max(1, int(max_sessions)))
    path = df.iloc[start:end]
    one_r_price = entry_price + risk
    two_r_price = entry_price + 2.0 * risk
    one_r_day = two_r_day = stop_day = None
    ambiguous_day = None

    for offset, (_, bar) in enumerate(path.iterrows(), start=1):
        low = float(bar["Low"])
        high = float(bar["High"])
        hit_stop = low <= stop
        hit_one = high >= one_r_price
        hit_two = high >= two_r_price
        if hit_stop and (hit_one or hit_two) and one_r_day is None:
            ambiguous_day = offset
            stop_day = offset
            break
        if hit_one and one_r_day is None:
            one_r_day = offset
        if hit_two and two_r_day is None:
            two_r_day = offset
            break
        if hit_stop:
            stop_day = offset
            break

    peak_path = path
    if stop_day is not None:
        # Stop görüldükten sonraki yükseliş artık bu işlemin yolculuğu değildir.
        # Stop gününü dahil ederek tepeyi yalnız pozisyon açıkken ölç.
        peak_path = path.iloc[:stop_day]

    if peak_path.empty:
        peak_day = None
        peak_date = None
        max_gain_pct = None
        max_r = None
    else:
        peak_pos = int(np.nanargmax(peak_path["High"].to_numpy(dtype=float)))
        peak_high = float(peak_path["High"].iloc[peak_pos])
        peak_day = peak_pos + 1
        peak_date = pd.Timestamp(peak_path.index[peak_pos]).date().isoformat()
        max_gain_pct = (peak_high / entry_price - 1.0) * 100.0
        max_r = (peak_high - entry_price) / risk

    if ambiguous_day is not None:
        stage = "STOP — AYNI GÜN BELİRSİZ"
        resolution = "stop_first_ambiguous"
    elif two_r_day is not None:
        stage = "2R GÖRÜLDÜ"
        resolution = "two_r"
    elif stop_day is not None and one_r_day is not None:
        stage = "1R SONRASI STOP"
        resolution = "stop_after_one_r"
    elif stop_day is not None:
        stage = "STOP"
        resolution = "stop"
    elif one_r_day is not None:
        stage = "1R GÖRÜLDÜ"
        resolution = "one_r_active"
    else:
        stage = "AKTİF"
        resolution = "active"

    return {
        "status": "ok",
        "stage": stage,
        "resolution": resolution,
        "entry_status": entry.get("status"),
        "entry_date": entry.get("entry_date"),
        "entry_price": round(entry_price, 4),
        "stop_price": round(stop, 4),
        "risk_value": round(risk, 4),
        "one_r_price": round(one_r_price, 4),
        "two_r_price": round(two_r_price, 4),
        "one_r_day": one_r_day,
        "two_r_day": two_r_day,
        "stop_day": stop_day,
        "ambiguous_day": ambiguous_day,
        "peak_day": peak_day,
        "peak_date": peak_date,
        "max_gain_pct": round(max_gain_pct, 3) if max_gain_pct is not None else None,
        "max_r": round(max_r, 3) if max_r is not None else None,
        "days_observed": len(path),
    }


def ensure_deepening_schema(conn) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(scan_signals)")}
    for name, kind in QUALITY_COLUMNS:
        if name not in columns:
            conn.execute(f"ALTER TABLE scan_signals ADD COLUMN {name} {kind}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rsi_journeys (
            signal_id       INTEGER PRIMARY KEY,
            symbol          TEXT NOT NULL,
            signal_date     TEXT NOT NULL,
            entry_status    TEXT,
            entry_date      TEXT,
            entry_price     REAL,
            stop_price      REAL,
            risk_value      REAL,
            one_r_price     REAL,
            two_r_price     REAL,
            one_r_day       INTEGER,
            two_r_day       INTEGER,
            stop_day        INTEGER,
            ambiguous_day   INTEGER,
            peak_day        INTEGER,
            peak_date       TEXT,
            max_gain_pct    REAL,
            max_r           REAL,
            days_observed   INTEGER,
            resolution      TEXT,
            journey_stage   TEXT,
            updated_at      TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rsi_journeys_resolution "
        "ON rsi_journeys(resolution, signal_date)"
    )


def upsert_rsi_journey(
    conn,
    signal_id: int,
    symbol: str,
    signal_date: str,
    journey: dict,
) -> None:
    ensure_deepening_schema(conn)
    conn.execute(
        """
        INSERT INTO rsi_journeys (
            signal_id, symbol, signal_date, entry_status, entry_date, entry_price,
            stop_price, risk_value, one_r_price, two_r_price, one_r_day, two_r_day,
            stop_day, ambiguous_day, peak_day, peak_date, max_gain_pct, max_r,
            days_observed, resolution, journey_stage, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(signal_id) DO UPDATE SET
            entry_status=excluded.entry_status,
            entry_date=excluded.entry_date,
            entry_price=excluded.entry_price,
            stop_price=excluded.stop_price,
            risk_value=excluded.risk_value,
            one_r_price=excluded.one_r_price,
            two_r_price=excluded.two_r_price,
            one_r_day=excluded.one_r_day,
            two_r_day=excluded.two_r_day,
            stop_day=excluded.stop_day,
            ambiguous_day=excluded.ambiguous_day,
            peak_day=excluded.peak_day,
            peak_date=excluded.peak_date,
            max_gain_pct=excluded.max_gain_pct,
            max_r=excluded.max_r,
            days_observed=excluded.days_observed,
            resolution=excluded.resolution,
            journey_stage=excluded.journey_stage,
            updated_at=excluded.updated_at
        """,
        (
            int(signal_id),
            str(symbol),
            str(signal_date),
            journey.get("entry_status") or journey.get("status"),
            journey.get("entry_date"),
            journey.get("entry_price"),
            journey.get("stop_price"),
            journey.get("risk_value"),
            journey.get("one_r_price"),
            journey.get("two_r_price"),
            journey.get("one_r_day"),
            journey.get("two_r_day"),
            journey.get("stop_day"),
            journey.get("ambiguous_day"),
            journey.get("peak_day"),
            journey.get("peak_date"),
            journey.get("max_gain_pct"),
            journey.get("max_r"),
            journey.get("days_observed"),
            journey.get("resolution"),
            journey.get("stage"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )


def journey_json(journey: dict) -> str:
    return json.dumps(journey or {}, ensure_ascii=False, sort_keys=True)
