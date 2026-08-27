"""İŞ 8 / Aşama 1 — değişken vade laboratuvarı.

Bu betik yalnızca ölçüm yapar. ``patron.db`` salt-okunur açılır; canlı tarama
tablolarına, ``app.py``'ye ve ``scan_signals`` yazım hattına dokunmaz.

Her olay için ertesi işlem yapılabilir açılıştan başlayarak 1..20 seanslık
yol kaydı çıkarılır. ``best`` ve ``worst`` stratejinin yönüne göre imzalıdır:
uzun sinyalde High en-iyi/Low en-kötü, kısa sinyalde Low en-iyi/High en-kötü
harekettir. Alfa, aynı günün XU100 kapanış getirisi çıkarılarak hesaplanır.
Bu tanım bir eşik veya ``ideal_day`` seçmez; yalnızca doğal eğriyi raporlar.

Kullanım:
    python variable_horizon_lab.py

Çıktı:
    logs/degisken_vade_asama1.json
    logs/degisken_vade_asama1.md
    logs/degisken_vade_asama1_events.csv (olay × seans ham yol kaydı)
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from bist_data_store import active_version_id, read_active
from signal_policy import (
    MEASUREMENT_REGIME_FALLING,
    MEASUREMENT_REGIME_RISING,
    MEASUREMENT_REGIME_RULE,
    MEASUREMENT_REGIME_WINDOW,
    measurement_regime_series,
    resolve_next_open_entry,
)


ROOT = Path(__file__).resolve().parent
DB = ROOT / "patron.db"
OUT_JSON = ROOT / "logs" / "degisken_vade_asama1.json"
OUT_MD = ROOT / "logs" / "degisken_vade_asama1.md"
OUT_EVENTS = ROOT / "logs" / "degisken_vade_asama1_events.csv"
HORIZON = 20
MIN_REGIME_N = 150  # Kullanıcının mühürlediği yorumlanabilirlik kapısı.
METRICS = (
    "best_raw",
    "best_alpha",
    "worst_raw",
    "worst_alpha",
    "close_raw",
    "close_alpha",
)


def _prepare(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Aktif fiyat çerçevesini ölçülebilir, tekil günlük OHLC'ye indirger."""
    if df is None or getattr(df, "empty", True):
        return None
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out = out.loc[:, ~out.columns.duplicated()].copy()
    out.columns = [str(c).capitalize() for c in out.columns]
    if not {"Open", "High", "Low", "Close"}.issubset(out.columns):
        return None
    idx = pd.to_datetime(out.index, errors="coerce")
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    out = out[~out.index.isna()]
    out = out[~out.index.duplicated(keep="last")].sort_index()
    for col in ("Open", "High", "Low", "Close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["Open", "High", "Low", "Close"])


def _read_events() -> tuple[pd.DataFrame, str, str, int]:
    """Gerçek olay başlangıçlarını DB'yi değiştirmeden getirir."""
    if not DB.exists():
        raise RuntimeError(f"patron.db bulunamadı: {DB}")
    uri = f"file:{DB.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        last = con.execute("SELECT MAX(scan_date) FROM scan_signals").fetchone()[0]
        first = con.execute("SELECT MIN(scan_date) FROM scan_signals").fetchone()[0]
        if not last:
            return pd.DataFrame(), "", "", 0
        events = pd.read_sql_query(
            """
            SELECT id, scan_date, scan_type, symbol, bias, category
            FROM scan_signals
            WHERE is_event_start=1
              AND scan_date>=? AND scan_date<=?
            ORDER BY scan_date, scan_type, symbol, id
            """,
            con,
            params=(first, last),
        )
    if events.empty:
        return events, str(first), str(last), 0
    raw_count = int(len(events))
    events["scan_date"] = pd.to_datetime(events["scan_date"], errors="coerce").dt.normalize()
    events = events.dropna(subset=["scan_date", "scan_type", "symbol"])
    # Aynı tarama-hisse-gün üçlüsü bir olaydır; ardışık devamlar sayılmaz.
    events = events.drop_duplicates(
        subset=["scan_date", "scan_type", "symbol"], keep="last"
    ).reset_index(drop=True)
    events["scan_type"] = events["scan_type"].astype(str).str.strip()
    events["symbol"] = events["symbol"].astype(str).str.upper().str.strip()
    return events, str(first), str(last), raw_count


def _asof_pos(index: pd.DatetimeIndex, date_value: Any) -> int | None:
    target = pd.Timestamp(date_value).normalize()
    pos = int(index.searchsorted(target, side="right")) - 1
    return pos if pos >= 0 else None


def _exact_pos(index: pd.DatetimeIndex, date_value: Any) -> int | None:
    target = pd.Timestamp(date_value).normalize()
    pos = int(index.searchsorted(target, side="left"))
    if pos >= len(index) or index[pos] != target:
        return None
    return pos


def _direction(scan_type: str, bias: str) -> int:
    is_bear = "bear" in str(bias or "").lower() or scan_type in ("er_D4", "er_D5")
    return -1 if is_bear else 1


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(np.median(np.asarray(values, dtype=float)))


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return float(np.mean(np.asarray(values, dtype=float)))


def _win_rate(values: list[float]) -> float | None:
    """Endeksi gerçekten geçen olayların oranı; eşitlik kazanım sayılmaz."""
    if not values:
        return None
    return float(sum(value > 0.0 for value in values) / len(values) * 100.0)


def _fmt_value(value: float | None) -> str:
    return "—" if value is None else f"{float(value):.4f}"


def _fmt_rate(value: float | None) -> str:
    return "—" if value is None else f"{float(value):.1f}%"


def _first_arg(values: list[float | None], mode: str) -> int | None:
    valid = [(i + 1, float(v)) for i, v in enumerate(values) if v is not None]
    if not valid:
        return None
    return (max if mode == "max" else min)(valid, key=lambda item: item[1])[0]


def _first_after(values: list[float | None], start_day: int, relation: str) -> int | None:
    """Eşik koymadan ilk tam günler-arası düşüş/eşitlik gününü bulur."""
    for day in range(max(2, start_day + 1), len(values) + 1):
        before, current = values[day - 2], values[day - 1]
        if before is None or current is None:
            continue
        if relation == "decrease" and current < before:
            return day
        if relation == "equal" and current == before:
            return day
    return None


def _last_improvement_day(values: list[float | None], direction: str) -> int | None:
    """Kümülatif yolun son yeni seviye gününü, vade diye yorumlamadan verir."""
    valid = [(day, float(value)) for day, value in enumerate(values, start=1) if value is not None]
    if not valid:
        return None
    last_day = valid[0][0]
    for (_, before), (day, current) in zip(valid, valid[1:]):
        if (direction == "up" and current > before) or (
            direction == "down" and current < before
        ):
            last_day = day
    return last_day


def _best_to_worst_ratio(best: float | None, worst: float | None) -> float | None:
    """Ortanca lehte hareketin, mutlak aleyhte harekete oranı; sıfırda tanımsız."""
    if best is None or worst is None or worst == 0:
        return None
    return float(best / abs(worst))


def _diagnostics(days: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    # Best/worst koşan uçlardır: tepe/dip günleri doğal vade değildir. Bunlarda
    # yalnız seviye büyümesinin son görüldüğü gün tutulur; asıl zaman sinyali
    # kapanış eğrisinin tepe ve geri verme günleridir.
    for metric, direction in (("best_raw", "up"), ("worst_raw", "down")):
        out[f"last_level_expansion_day_{metric}"] = _last_improvement_day(
            [row.get(metric) for row in days], direction
        )
    # Kapanış eğrisindeki tepe ve ilk geri verme doğal vade tartışmasının asıl girdisidir.
    for suffix in ("raw", "alpha"):
        close = [row.get(f"close_{suffix}") for row in days]
        peak = _first_arg(close, "max")
        out[f"close_peak_day_{suffix}"] = peak
        out[f"close_giveback_first_day_{suffix}"] = (
            _first_after(close, peak or 1, "decrease") if peak else None
        )
    return out


def _make_report(payload: dict[str, Any]) -> str:
    meta = payload["meta"]
    cov = payload["coverage"]
    lines = [
        "# Değişken Vade Laboratuvarı — Aşama 1",
        "",
        "Bu rapor parametresiz 1–20 seans yol ölçümüdür; karar, rozet veya vade politikası değiştirmez.",
        "",
        "## Mühürler",
        "",
        f"- Giriş: ertesi işlem yapılabilir açılış + tavan kilidi (en fazla 3 kilitli seans); yön imzası mevcut politika ile aynı.",
        f"- Rejim: XU100 kapanışı SMA{meta['regime_window']} üstü/altı — {meta['regime_rule']}; yalnız ölçüm bölmesi.",
        "- Olay: yalnız `is_event_start=1`; aynı tarih-tarama-hisse tekrarları tek olaya indirildi.",
        "- Alfa: yönlü hisse metriği eksi, aynı giriş tarihinden yönlü XU100 kapanış getirisi.",
        "- `best`: uzun sinyalde High, kısa sinyalde Low; `worst` bunun karşı yönündeki uçtur.",
        "- Kapanışın ham ve XU100'e göre alfa eğrisi hem bu raporda hem olay×seans CSV'sinde bulunur.",
        "- Evren tabanı: her gün, XU100 günü eşleşen tüm olayların kapanış-alfa ortancası; hücre farkı bu tabana göredir.",
        "- Kazanma oranı: kapanış alfası sıfırdan büyük, yani aynı gün XU100'ü geçen olayların oranı.",
        f"- Rejim başına N < {meta['min_regime_n']} hücreleri BELİRSİZ olarak bırakıldı; veri atılmadı.",
        "",
        "## Kapsam",
        "",
        f"- DB olayları: {cov['events_after_dedup']:,} (ham is_event_start: {cov['events_before_dedup']:,})",
        f"- Tarama tipi: {cov['scanner_count']} · sembol: {cov['symbol_count']} · aktif veri sürümü: `{meta['active_version']}`",
        f"- Geçerli giriş+rejim: {cov['valid_entry_regime_events']:,} · 20 seansı tamamlayabilen hisse olayları ayrıca gün N'lerinde görünür.",
        f"- Olay×seans ham yol kaydı: {cov['event_path_rows']:,} satır → `{cov['event_path_file']}`.",
        "- Giriş/rejim/veri eksikleri aşağıdaki sayaçlarda açıkça tutuldu; eksik hücreler sıfır değildir.",
        "",
        "## Günlük evren tabanı",
        "",
        "| Gün | Hisse yolu N | Evren alfa N | Kapanış alfa ortanca | Kapanış alfa ortalama | Endeksi yenme |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["universe_baseline"]:
        lines.append(
            f"| {row['day']} | {row['stock_path_n']} | {row['n']} | "
            f"{_fmt_value(row['close_alpha_median'])} | {_fmt_value(row['close_alpha_mean'])} | "
            f"{_fmt_rate(row['close_alpha_win_rate'])} |"
        )
    lines += [
        "",
        "## Tarama × rejim eğrileri",
        "",
    ]
    for scanner in sorted(payload["curves"]):
        lines.append(f"### {scanner}")
        for regime in (MEASUREMENT_REGIME_RISING, MEASUREMENT_REGIME_FALLING):
            cell = payload["curves"][scanner].get(regime)
            if not cell:
                continue
            status = cell["status"]
            lines += [
                "",
                f"**{regime} · N={cell['event_n']} · {status}**",
                "",
                "| Gün | N | En iyi ham | En iyi alfa | En kötü ham | En kötü alfa | Best ÷ mutlak worst | Kapanış ham ortanca | Kapanış alfa ortanca | Kapanış alfa ortalama | Endeksi yenme | Evren alfa N | Evren alfa ortanca | Tabandan fark |",
                "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
            for row in cell["days"]:
                def fmt(key: str) -> str:
                    return _fmt_value(row.get(key))

                lines.append(
                    f"| {row['day']} | {row['n_raw']} | {fmt('best_raw')} | {fmt('best_alpha')} | "
                    f"{fmt('worst_raw')} | {fmt('worst_alpha')} | {fmt('best_to_worst_ratio_raw')} | "
                    f"{fmt('close_raw')} | {fmt('close_alpha')} | {fmt('close_alpha_mean')} | "
                    f"{_fmt_rate(row.get('close_alpha_win_rate'))} | {row['universe_close_alpha_n']} | "
                    f"{fmt('universe_close_alpha_median')} | {fmt('close_alpha_vs_universe')} |"
                )
            d = cell["diagnostics"]
            day_1, day_20 = cell["days"][0], cell["days"][-1]
            lines += [
                "",
                "Kapanış ritmi (doğal vade için asıl sinyal): "
                f"tepe günü ham/alfa {d['close_peak_day_raw']}/{d['close_peak_day_alpha']}; "
                f"ilk geri verme ham/alfa {d['close_giveback_first_day_raw'] or '20 gün içinde yok'}/"
                f"{d['close_giveback_first_day_alpha'] or '20 gün içinde yok'}.",
                "Yol düzeyi (ham): "
                f"G1 en iyi/en kötü/oran {_fmt_value(day_1.get('best_raw'))}/"
                f"{_fmt_value(day_1.get('worst_raw'))}/{_fmt_value(day_1.get('best_to_worst_ratio_raw'))}; "
                f"G20 en iyi/en kötü/oran {_fmt_value(day_20.get('best_raw'))}/"
                f"{_fmt_value(day_20.get('worst_raw'))}/{_fmt_value(day_20.get('best_to_worst_ratio_raw'))}.",
                "Kümülatif uçların son seviye genişlemesi (vade değildir): "
                f"en iyi ham {d['last_level_expansion_day_best_raw'] or 'veri yok'}. gün; "
                f"en kötü ham {d['last_level_expansion_day_worst_raw'] or 'veri yok'}. gün. "
                "Bu iki seri koşan uçtur; doğal süre yalnız kapanış ritminden yorumlanır.",
            ]
    lines += [
        "",
        "## Ölçüm kapısı",
        "",
        "Bu Aşama 1 çıktısı yalnız doğal süre eğrilerini üretir. Eşik, sınır, vade politikası, rozet ve ekran kararı üretilmedi; Aşama 2 ayrı emir olmadan başlatılmamalıdır.",
    ]
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    events, first_date, last_date, raw_event_count = _read_events()
    if events.empty:
        raise RuntimeError("is_event_start=1 olay bulunamadı.")

    version = active_version_id()
    benchmark = _prepare(read_active("XU100.IS", version))
    if benchmark is None or benchmark.empty:
        raise RuntimeError("Aktif veri kasasında XU100 günlük OHLC bulunamadı.")
    regime_series = measurement_regime_series(benchmark)

    # Hücre içinde gün → metrik → olay değerleri; ayrıca her olay×seans yolu
    # denetlenebilir bir CSV'ye akıtılır.
    values: dict[tuple[str, str], dict[int, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    universe_stock_path_n: Counter[int] = Counter()
    universe_close_alpha: dict[int, list[float]] = defaultdict(list)
    event_n: Counter[tuple[str, str]] = Counter()
    status_counts: Counter[str] = Counter()
    valid_events = 0
    event_rows_written = 0
    OUT_EVENTS.parent.mkdir(parents=True, exist_ok=True)
    event_file = OUT_EVENTS.open("w", newline="", encoding="utf-8")
    event_writer = csv.DictWriter(
        event_file,
        fieldnames=(
            "event_id", "scan_date", "scan_type", "symbol", "bias", "regime",
            "entry_date", "entry_price", "day", *METRICS,
        ),
    )
    event_writer.writeheader()

    for symbol, group in events.groupby("symbol", sort=False):
        symbol_df = _prepare(read_active(symbol, version))
        if symbol_df is None or symbol_df.empty:
            status_counts["hisse_verisi_yok"] += len(group)
            continue
        for row in group.itertuples(index=False):
            scan = str(row.scan_type)
            signal_date = row.scan_date
            signal_pos = _asof_pos(symbol_df.index, signal_date)
            benchmark_signal_pos = _asof_pos(benchmark.index, signal_date)
            if signal_pos is None or benchmark_signal_pos is None:
                status_counts["sinyal_gunu_eslesmedi"] += 1
                continue
            regime = regime_series.iloc[benchmark_signal_pos]
            if pd.isna(regime):
                status_counts["rejim_icin_50_seans_yok"] += 1
                continue

            entry = resolve_next_open_entry(
                symbol_df,
                signal_date,
                bias=str(row.bias or "bullish"),
                apply_bist_limit=True,
                max_locked_sessions=3,
            )
            entry_status = str(entry.get("status", "bilinmiyor"))
            status_counts[entry_status] += 1
            if not entry_status.startswith("filled"):
                continue
            stock_entry = int(entry["entry_pos"])
            benchmark_entry = _exact_pos(benchmark.index, entry["entry_date"])
            if benchmark_entry is None:
                status_counts["endeks_giris_gunu_yok"] += 1
                continue
            entry_price = float(entry["entry_price"])
            benchmark_entry_price = float(benchmark["Open"].iloc[benchmark_entry])
            if entry_price <= 0 or benchmark_entry_price <= 0:
                status_counts["gecersiz_giris_fiyati"] += 1
                continue

            stock_days = min(HORIZON, len(symbol_df) - stock_entry)
            benchmark_days = min(HORIZON, len(benchmark) - benchmark_entry)
            max_days = min(stock_days, benchmark_days)
            if max_days <= 0:
                status_counts["ileri_veri_yok"] += 1
                continue
            direction = _direction(scan, str(row.bias or ""))
            stock_slice = symbol_df.iloc[stock_entry : stock_entry + max_days]
            bench_slice = benchmark.iloc[benchmark_entry : benchmark_entry + max_days]
            # Aynı giriş cetvelindeki seansların tarihleri aynı değilse alfa o gün boş kalır.
            bench_by_date = bench_slice.copy()
            bench_by_date.index = pd.to_datetime(bench_by_date.index).normalize()
            stock_dates = pd.to_datetime(stock_slice.index).normalize()
            high = stock_slice["High"].to_numpy(dtype=float)
            low = stock_slice["Low"].to_numpy(dtype=float)
            close = stock_slice["Close"].to_numpy(dtype=float)
            if direction > 0:
                favorable = (high / entry_price - 1.0) * 100.0
                adverse = (low / entry_price - 1.0) * 100.0
            else:
                favorable = (low / entry_price - 1.0) * -100.0
                adverse = (high / entry_price - 1.0) * -100.0
            close_raw = (close / entry_price - 1.0) * 100.0 * direction
            best_raw = np.maximum.accumulate(favorable)
            worst_raw = np.minimum.accumulate(adverse)

            key = (scan, str(regime))
            event_n[key] += 1
            valid_events += 1
            for offset, session_date in enumerate(stock_dates, start=1):
                day_values: dict[str, float | None] = {
                    "best_raw": float(best_raw[offset - 1]),
                    "worst_raw": float(worst_raw[offset - 1]),
                    "close_raw": float(close_raw[offset - 1]),
                    "best_alpha": None,
                    "worst_alpha": None,
                    "close_alpha": None,
                }
                bench_row = bench_by_date.loc[session_date] if session_date in bench_by_date.index else None
                if bench_row is not None:
                    bench_close = float(bench_row["Close"])
                    bench_close_ret = (bench_close / benchmark_entry_price - 1.0) * 100.0 * direction
                    day_values["best_alpha"] = day_values["best_raw"] - bench_close_ret
                    day_values["worst_alpha"] = day_values["worst_raw"] - bench_close_ret
                    day_values["close_alpha"] = day_values["close_raw"] - bench_close_ret
                bucket = values[key][offset]
                for metric, metric_value in day_values.items():
                    if metric_value is not None and np.isfinite(metric_value):
                        bucket[metric].append(float(metric_value))
                universe_stock_path_n[offset] += 1
                close_alpha = day_values["close_alpha"]
                if close_alpha is not None and np.isfinite(close_alpha):
                    universe_close_alpha[offset].append(float(close_alpha))
                event_writer.writerow(
                    {
                        "event_id": getattr(row, "id", ""),
                        "scan_date": pd.Timestamp(signal_date).date().isoformat(),
                        "scan_type": scan,
                        "symbol": symbol,
                        "bias": str(row.bias or ""),
                        "regime": str(regime),
                        "entry_date": entry["entry_date"],
                        "entry_price": entry_price,
                        "day": offset,
                        **day_values,
                    }
                )
                event_rows_written += 1

    event_file.close()

    universe_baseline = []
    universe_by_day: dict[int, dict[str, Any]] = {}
    for day in range(1, HORIZON + 1):
        alpha_values = universe_close_alpha.get(day, [])
        row = {
            "day": day,
            "stock_path_n": int(universe_stock_path_n[day]),
            "n": len(alpha_values),
            "close_alpha_median": _median(alpha_values),
            "close_alpha_mean": _mean(alpha_values),
            "close_alpha_win_rate": _win_rate(alpha_values),
        }
        universe_baseline.append(row)
        universe_by_day[day] = row

    curves: dict[str, dict[str, Any]] = defaultdict(dict)
    for (scanner, regime), n_events in sorted(event_n.items()):
        days: list[dict[str, Any]] = []
        for day in range(1, HORIZON + 1):
            bucket = values[(scanner, regime)].get(day, {})
            row: dict[str, Any] = {"day": day}
            for metric in METRICS:
                metric_values = bucket.get(metric, [])
                row[f"n_{metric}"] = len(metric_values)
                row[metric] = _median(metric_values)
            close_alpha_values = bucket.get("close_alpha", [])
            universe_row = universe_by_day[day]
            row["best_to_worst_ratio_raw"] = _best_to_worst_ratio(
                row["best_raw"], row["worst_raw"]
            )
            row["close_alpha_mean"] = _mean(close_alpha_values)
            row["close_alpha_win_rate"] = _win_rate(close_alpha_values)
            row["universe_close_alpha_n"] = universe_row["n"]
            row["universe_close_alpha_median"] = universe_row["close_alpha_median"]
            row["close_alpha_vs_universe"] = (
                None
                if row["close_alpha"] is None or universe_row["close_alpha_median"] is None
                else float(row["close_alpha"] - universe_row["close_alpha_median"])
            )
            row["n_raw"] = row["n_best_raw"]
            row["n_alpha"] = row["n_best_alpha"]
            days.append(row)
        cell = {
            "event_n": int(n_events),
            "status": "BELİRSİZ" if n_events < MIN_REGIME_N else "YETERLİ ÖRNEKLEM",
            "days": days,
            "diagnostics": _diagnostics(days),
        }
        curves[scanner][regime] = cell

    coverage = {
        "events_before_dedup": raw_event_count,
        "events_after_dedup": int(len(events)),
        "scanner_count": int(events["scan_type"].nunique()),
        "symbol_count": int(events["symbol"].nunique()),
        "valid_entry_regime_events": int(valid_events),
        "event_path_rows": int(event_rows_written),
        "event_path_file": str(OUT_EVENTS),
        "status_counts": dict(sorted(status_counts.items())),
    }
    meta = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "first_scan_date": first_date,
        "last_scan_date": last_date,
        "active_version": version,
        "horizon_sessions": list(range(1, HORIZON + 1)),
        "regime_rule": MEASUREMENT_REGIME_RULE,
        "regime_window": MEASUREMENT_REGIME_WINDOW,
        "regimes": [MEASUREMENT_REGIME_RISING, MEASUREMENT_REGIME_FALLING],
        "entry_rule": "resolve_next_open_entry(apply_bist_limit=True,max_locked_sessions=3)",
        "dedup_rule": "is_event_start=1; unique(scan_date,scan_type,symbol)",
        "min_regime_n": MIN_REGIME_N,
        "ideal_day_used": False,
        "thresholds_selected": False,
        "alpha_definition": "directional stock metric minus directional XU100 close return on same session",
        "direction_definition": "bullish: High best/Low worst; bearish: Low best/High worst",
        "universe_baseline_rule": "all stock paths with matching XU100 session; close_alpha median",
        "win_rate_rule": "close_alpha > 0",
    }
    return {
        "meta": meta,
        "coverage": coverage,
        "universe_baseline": universe_baseline,
        "curves": curves,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="İŞ 8 Aşama 1 değişken vade eğrisi")
    parser.parse_args()  # Gelecekteki laboratuvar seçenekleri için uyum noktası; eşik yok.
    payload = run()
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    OUT_MD.write_text(_make_report(payload), encoding="utf-8")
    print(f"JSON: {OUT_JSON}")
    print(f"RAPOR: {OUT_MD}")
    print(json.dumps(payload["coverage"], ensure_ascii=False, indent=2))
    print(f"HÜCRE: {sum(len(v) for v in payload['curves'].values())}")


if __name__ == "__main__":
    main()
