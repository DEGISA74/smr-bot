# -*- coding: utf-8 -*-
"""Magic Ribbon — BIST100 seans-mumu gözlem taraması.

Fast/Slow yön hizalanması, TradingView'in 5 dakikalık fiyatlarından üretilen
tam BIST seans mumları üzerinde hesaplanır. Her işlem günü yalnız iki mum
kullanılır: 09:55–14:00 ve 14:00–18:10. Sonuç işlem emri değildir; Master
Scan içinde derin inceleme için aday havuzudur.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from magic_ribbon_session_data import (
    get_magic_ribbon_session_data,
    session_block_ids,
    session_close_timestamp,
    session_gap_report,
    session_label,
)


ROOT = Path(__file__).resolve().parent
BIST100_PATH = ROOT / "_bist100.json"
MIN_BARS = 60
# 1 Eyl 2026 — DELİK KAPISI. Bozuk gün reddedilince geriye kalan mumlar hesapta
# yan yana sayılıyordu; araya bir hafta girmiş iki mum arasındaki "eğim" gerçek
# eğim değildir. Şeridi besleyen mum sayısı: CoraWave(10,3) ≈ 12 bar,
# LazyLine(15) ≈ 13 bar, eğim için +1. Bu yüzden BUGÜNKÜ okumanın dayandığı son
# 20 mum tek bir kesintisiz blokta olmalı; olmazsa hisse aday listesine girmez.
# Sayı sezgiyle değil göstergenin kendi geriye bakışından türetildi.
RIBBON_LOOKBACK_BARS = 20
ENGINE_VERSION = "magic-ribbon-bist-session-v1"
# 31 Ağu 2026 ilk ölçüm: 49 tam seans / 100 BIST100 / 578 sinyal.
# Yaklaşık T+5 alfa -%0,280, T+10 -%0,077, T+20 +%0,024 (gürültü).
# 1 Eyl 2026 — ölçüm delik-farkında yapılınca üç vadede de artıya döndü
# (+%0,273 / +%0,789 / +%1,867). BU KANIT DEĞİL: filtre tabanı da düzeltiyor ve
# Temmuz'un yalnız %34,6'sını bırakıyor (takvim yanlılığı); alfanın t değeri
# 1,33 ve bu örtüşen pencereler yok sayıldığı için iyimser bir üst sınır.
# Ayrım kanıtı yokken aday listesi ekrana çıkmaz; ham sinyaller ileri test için
# kaydedilmeye devam eder. İkinci rejimde yeniden ölçülmeden True yapılmayacak.
MAGIC_RIBBON_BIST_SESSION_RENDER_ENABLED = False


def _normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper().replace(".IS", "")


def load_bist100_symbols(path: Path | None = None) -> set[str]:
    """Güncel BIST100 listesini güvenli biçimde okur; hatada boş döner."""
    source = path or BIST100_PATH
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return set()
    if not isinstance(payload, list):
        return set()
    return {
        symbol
        for symbol in (_normalize_symbol(item) for item in payload)
        if symbol and symbol.isascii() and symbol.replace("_", "").isalnum()
    }


def wma(series: pd.Series, length: int) -> pd.Series:
    """Pine ta.wma karşılığı: eski bardan yeni bara 1..N ağırlıkları."""
    weights = np.arange(1, length + 1, dtype=float)
    total = float(weights.sum())
    return series.rolling(length, min_periods=length).apply(
        lambda values: float(np.dot(values, weights) / total), raw=True
    )


def cora_wave(source: pd.Series, length: int = 10, smooth: int = 3) -> pd.Series:
    """Pine f_CoraWave karşılığı."""
    start_wt = 0.01
    end_wt = float(length)
    ratio = math.pow(end_wt / start_wt, 1.0 / (length - 1)) - 1.0
    base = 1.0 + ratio * 2.0
    weights = start_wt * np.power(base, np.arange(1, length + 1, dtype=float))
    total = float(weights.sum())
    raw = source.rolling(length, min_periods=length).apply(
        lambda values: float(np.dot(values, weights) / total), raw=True
    )
    return wma(raw, smooth)


def lazy_line(source: pd.Series, length: int = 15) -> pd.Series:
    """Pine f_LazyLine karşılığı."""
    if length <= 4:
        return wma(source, length)
    w2 = int(math.floor(length / 3.0 + 0.5))
    w1 = int(math.floor((length - w2) / 2.0 + 0.5))
    w3 = int((length - w2) / 2.0)
    first = wma(source, w1)
    second = wma(first, w2)
    return wma(second, w3)


def add_ribbon_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Fast/Slow çizgileri ve yukarı-aşağı hizalanma durumlarını ekler."""
    out = df.copy()
    close = pd.to_numeric(out["Close"], errors="coerce").astype(float)
    out["fast_line"] = cora_wave(close, 10, 3)
    out["slow_line"] = lazy_line(close, 15)
    fast_up = out["fast_line"].gt(out["fast_line"].shift(1)).fillna(False)
    slow_up = out["slow_line"].gt(out["slow_line"].shift(1)).fillna(False)
    ribbon_up = (fast_up & slow_up).astype(bool)
    ribbon_down = ((~fast_up) & (~slow_up)).astype(bool)
    out["ribbon_up"] = ribbon_up
    out["ribbon_down"] = ribbon_down
    out["up_trigger"] = ribbon_up & ~ribbon_up.shift(1, fill_value=False)
    out["down_trigger"] = ribbon_down & ~ribbon_down.shift(1, fill_value=False)
    return out


def _format_bar(value: object) -> str:
    try:
        return pd.Timestamp(value).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return "—"


def _format_session_close(value: object) -> str:
    close_time = session_close_timestamp(value)
    if close_time is None:
        return "—"
    return close_time.strftime("%d.%m.%Y %H:%M")


def scan_magic_ribbon_bist100() -> pd.DataFrame:
    """BIST100 içinde aktif, kapanmış BIST seans-mumu hizalanmalarını döndürür."""
    symbols = sorted(load_bist100_symbols())
    rows: list[dict[str, object]] = []
    skipped = 0
    gapped = 0

    for symbol in symbols:
        df = get_magic_ribbon_session_data(symbol)
        if df is None or len(df) < MIN_BARS:
            skipped += 1
            continue
        try:
            enriched = add_ribbon_columns(df)
            valid = enriched[enriched[["fast_line", "slow_line"]].notna().all(axis=1)]
            if len(valid) < 2:
                skipped += 1
                continue

            # DELİK KAPISI — bugünkü okumayı besleyen mumlar kesintisiz mi?
            blocks = session_block_ids(enriched)
            if len(blocks) < RIBBON_LOOKBACK_BARS or (
                blocks.iloc[-1] != blocks.iloc[-RIBBON_LOOKBACK_BARS]
            ):
                gapped += 1
                continue
            gap = session_gap_report(enriched)

            last = enriched.iloc[-1]
            if not bool(last.get("ribbon_up", False)):
                continue

            trigger_positions = np.flatnonzero(enriched["up_trigger"].to_numpy(dtype=bool))
            if len(trigger_positions) == 0:
                skipped += 1
                continue
            trigger_i = int(trigger_positions[-1])
            age_bars = int(len(enriched) - 1 - trigger_i)

            up_bars = 0
            for value in reversed(enriched["ribbon_up"].tolist()):
                if not bool(value):
                    break
                up_bars += 1

            turnover = np.nan
            if "Volume" in enriched.columns:
                volume = pd.to_numeric(enriched["Volume"], errors="coerce")
                turnover = float((enriched["Close"].astype(float) * volume).tail(20).median())

            rows.append({
                "Sembol": symbol,
                "Fiyat": float(last["Close"]),
                "Durum": "YENİ HİZALANMA" if age_bars == 0 else "HİZALANMA SÜRÜYOR",
                "TetikYaşı": age_bars,
                "YukarıBar": up_bars,
                "SonBar": _format_bar(enriched.index[-1]),
                "SonSeans": session_label(enriched.index[-1]),
                "VeriKapanis": _format_session_close(enriched.index[-1]),
                "TetikBar": _format_bar(enriched.index[trigger_i]),
                "FastLine": float(last["fast_line"]),
                "SlowLine": float(last["slow_line"]),
                "MedianTurnover": turnover,
                # Veri kalitesi ileri testle birlikte saklanır: hükmü hangi
                # tamlıktaki seriyle verdiğimizi sonradan sorgulayabilelim.
                "Kapsama": float(gap["kapsama"]),
                "EksikGun": int(gap["eksik_gun"]),
            })
        except (KeyError, TypeError, ValueError, IndexError):
            skipped += 1

    result = pd.DataFrame(rows)
    if not result.empty:
        result["_fresh"] = result["TetikYaşı"].eq(0).astype(int)
        result = result.sort_values(
            ["_fresh", "TetikYaşı", "MedianTurnover"],
            ascending=[False, True, False],
            na_position="last",
        ).drop(columns="_fresh").reset_index(drop=True)

    result.attrs.update({
        "universe": "BIST100",
        "universe_count": len(symbols),
        "data_count": len(result) + skipped,
        "candidate_count": len(result),
        "skipped_count": skipped,
        "gapped_count": gapped,
        "closed_bars_only": True,
        "contiguous_lookback_bars": RIBBON_LOOKBACK_BARS,
    })
    return result


def kaydet(result: pd.DataFrame | None, scan_date: str | None = None) -> int:
    """BIST seans-mumu listesini, eski 4S kayıtlarından ayrı ileri-test defterine yazar."""
    if result is None or not isinstance(result, pd.DataFrame) or result.empty:
        return 0
    import sqlite3
    from datetime import datetime
    try:
        from db_layer import DB_FILE, init_db
    except ImportError:
        return 0
    gun = scan_date or datetime.now().strftime("%Y-%m-%d")
    evren = str(result.attrs.get("universe") or "BIST100")

    def _f(value):
        try:
            out = float(value)
            return out if out == out else None
        except (TypeError, ValueError):
            return None

    def _i(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    satirlar = []
    for _, row in result.iterrows():
        sembol = str(row.get("Sembol") or "").strip().upper()
        if not sembol:
            continue
        satirlar.append((
            gun, str(row.get("VeriKapanis") or ""), str(row.get("SonSeans") or ""), sembol, _f(row.get("Fiyat")),
            str(row.get("Durum") or ""), _i(row.get("TetikYaşı")), _i(row.get("YukarıBar")),
            _f(row.get("FastLine")), _f(row.get("SlowLine")), _f(row.get("MedianTurnover")),
            evren, ENGINE_VERSION, _f(row.get("Kapsama")), _i(row.get("EksikGun")),
        ))
    if not satirlar:
        return 0
    try:
        init_db()
        con = sqlite3.connect(DB_FILE, timeout=60)
        try:
            cur = con.executemany(
                "INSERT OR IGNORE INTO magic_ribbon_session_log "
                "(scan_date, bar_time, seans, symbol, price, durum, tetik_yasi, yukari_bar, "
                " fast_line, slow_line, ciro, universe, engine, kapsama, eksik_gun) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", satirlar)
            con.commit()
            return int(cur.rowcount or 0)
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return 0
