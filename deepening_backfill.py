# -*- coding: utf-8 -*-
"""22/23/24/25/28 derinleştirme paketini geçmiş veriye uygular.

Mevcut sinyalleri silmez. B11, C6, Radar2 ve Zirve Devam satırlarına kalite
notlarını ekler; RSI Pozitif Uyumsuzluk olaylarını nokta-zamanlı üretir; Radar2
ile C6'dan liderlik yaşam döngüsü satırlarını oluşturur.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from deepening_policy import (
    b11_pilot_profile,
    distribution_profile,
    ensure_deepening_schema,
    leadership_profile,
    leadership_stage,
    upsert_rsi_journey,
)
from rsi_divergence_scanner import enumerate_wilder_positive_divergences
from signal_policy import (
    assign_event_metadata_for_date,
    ensure_event_schema,
    register_scan_run,
)


BASE = Path(__file__).resolve().parent
DB_FILE = BASE / "patron.db"
PARQUET_DIR = BASE / "veriler"
BACKUP_DIR = BASE / "backups"
REPORT_FILE = BASE / "deepening_report.md"
LEADERSHIP_TYPES = (
    "liderlik_aday",
    "liderlik_yeni",
    "liderlik_teyitli",
    "liderlik_gec",
)


def _ticker(symbol: str) -> str:
    clean = str(symbol or "").upper().replace(".IS", "")
    return f"{clean}.IS"


def _load(symbol: str, cache: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    clean = str(symbol or "").upper().replace(".IS", "")
    if clean in cache:
        return cache[clean]
    candidates = (
        PARQUET_DIR / f"{clean}.IS_1d.parquet",
        PARQUET_DIR / f"{clean}_1d.parquet",
    )
    frame = None
    for path in candidates:
        if not path.exists():
            continue
        try:
            frame = pd.read_parquet(path)
            frame.index = pd.to_datetime(frame.index, errors="coerce")
            if getattr(frame.index, "tz", None) is not None:
                frame.index = frame.index.tz_localize(None)
            frame = frame[~frame.index.isna()]
            frame = frame[~frame.index.duplicated(keep="last")].sort_index()
            break
        except Exception:
            frame = None
    cache[clean] = frame
    return frame


def _slice(frame: pd.DataFrame | None, date_value) -> pd.DataFrame | None:
    if frame is None or frame.empty:
        return None
    target = pd.Timestamp(date_value)
    if target.tzinfo is not None:
        target = target.tz_localize(None)
    part = frame.loc[: target]
    return part if not part.empty else None


def _backup_database() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"patron_pre_deepening_{datetime.now():%Y%m%d_%H%M%S}.db"
    source = sqlite3.connect(DB_FILE)
    target = sqlite3.connect(path)
    source.backup(target)
    target.close()
    source.close()
    return path


def _update_quality(
    conn,
    signal_id: int,
    score,
    label,
    detail,
    stage=None,
    age=None,
    key=None,
) -> None:
    conn.execute(
        """
        UPDATE scan_signals
        SET quality_score=?, quality_label=?, quality_detail=?,
            journey_stage=?, journey_age=?, journey_key=?
        WHERE id=?
        """,
        (score, label, detail, stage, age, key, int(signal_id)),
    )


def enrich_existing(conn, cache: dict[str, pd.DataFrame]) -> dict:
    counts = Counter()
    benchmark = _load("XU100", cache)
    rows = conn.execute(
        """
        SELECT id, symbol, scan_type, scan_date
        FROM scan_signals
        WHERE scan_type IN ('er_B11','er_C6','radar2','zirve_devam')
        ORDER BY scan_date, id
        """
    ).fetchall()
    for signal_id, symbol, scan_type, scan_date in rows:
        frame = _slice(_load(symbol, cache), scan_date)
        if frame is None:
            counts[f"{scan_type}_missing"] += 1
            continue
        if scan_type == "er_B11":
            profile = b11_pilot_profile(frame)
            _update_quality(
                conn,
                signal_id,
                profile["quality_score"],
                profile["quality_label"],
                profile["detail"],
                profile["quality_label"],
                1,
                "b11_pilot",
            )
        elif scan_type in ("er_C6", "radar2"):
            bench_slice = _slice(benchmark, scan_date)
            profile = leadership_profile(frame, bench_slice)
            age = int(profile.get("leader_age", 0) or 0)
            label = (
                "GEÇ SİNYAL"
                if profile.get("late_state") == "GEÇ SİNYAL"
                else ("YENİ LİDER" if age <= 3 else "LİDERLİK TEYİDİ")
            )
            score = 25 if label == "GEÇ SİNYAL" else (90 if age <= 3 else 75)
            _update_quality(
                conn,
                signal_id,
                score,
                label,
                profile.get("detail"),
                "C6 TEYİTLİ" if scan_type == "er_C6" else "ADAY",
                age,
                "radar2_c6_liderlik",
            )
        elif scan_type == "zirve_devam":
            profile = distribution_profile(frame)
            _update_quality(
                conn,
                signal_id,
                profile["score"],
                profile["label"],
                profile["detail"],
                profile["label"],
                1,
                "zirve_devam_dagitim",
            )
        counts[scan_type] += 1
    conn.commit()
    return dict(counts)


def backfill_leadership(conn) -> dict:
    c6 = {
        (str(date), str(symbol).upper().replace(".IS", ""))
        for date, symbol in conn.execute(
            """
            SELECT scan_date, symbol
            FROM scan_signals
            WHERE scan_type='er_C6'
            """
        )
    }
    radar_rows = conn.execute(
        """
        SELECT id, scan_date, symbol, entry_price, category,
               quality_score, quality_label, quality_detail, journey_age
        FROM scan_signals
        WHERE scan_type='radar2'
        ORDER BY scan_date, symbol
        """
    ).fetchall()
    by_date_type = defaultdict(list)
    inserted = Counter()
    for (
        _,
        scan_date,
        symbol,
        entry_price,
        category,
        quality_score,
        quality_label,
        quality_detail,
        journey_age,
    ) in radar_rows:
        clean = str(symbol).upper().replace(".IS", "")
        confirmed = (str(scan_date), clean) in c6
        stage = leadership_stage(
            True,
            confirmed,
            int(journey_age or 0),
            str(quality_label or ""),
        )
        scan_type = stage["scan_type"]
        if scan_type not in LEADERSHIP_TYPES:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO scan_signals (
                scan_date, symbol, scan_type, score, bias, entry_price, category,
                quality_score, quality_label, quality_detail,
                journey_stage, journey_age, journey_key
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                scan_date,
                clean,
                scan_type,
                stage["quality_score"],
                "bullish",
                entry_price,
                category,
                stage["quality_score"],
                stage["stage"],
                quality_detail,
                stage["stage"],
                int(journey_age or 0),
                "radar2_c6_liderlik",
            ),
        )
        inserted[scan_type] += int(conn.execute("SELECT changes()").fetchone()[0] > 0)
        by_date_type[(str(scan_date), scan_type)].append(clean)

    radar_dates = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT scan_date FROM scan_signals WHERE scan_type='radar2' ORDER BY scan_date"
        )
    ]
    for scan_date in radar_dates:
        for scan_type in LEADERSHIP_TYPES:
            count = len(by_date_type.get((str(scan_date), scan_type), []))
            previous = register_scan_run(
                conn, scan_type, str(scan_date), count, "BIST 500 "
            )
            assign_event_metadata_for_date(
                conn, scan_type, str(scan_date), previous
            )
    conn.commit()
    totals = {
        scan_type: int(
            conn.execute(
                "SELECT COUNT(*) FROM scan_signals WHERE scan_type=?",
                (scan_type,),
            ).fetchone()[0]
        )
        for scan_type in LEADERSHIP_TYPES
    }
    return {
        "inserted_this_run": dict(inserted),
        "total": totals,
    }


def _parquet_files() -> list[Path]:
    return sorted(
        path
        for path in PARQUET_DIR.glob("*.IS_1d.parquet")
        if not path.name.upper().startswith(("XU", "XB", "XT", "XY"))
    )


def backfill_rsi(
    conn,
    cache: dict[str, pd.DataFrame],
    days: int,
    limit: int | None = None,
) -> dict:
    files = _parquet_files()
    if limit:
        files = files[: max(1, int(limit))]
    latest = max(
        (
            pd.Timestamp(frame.index.max())
            for path in files
            if (frame := _load(path.name.replace(".IS_1d.parquet", ""), cache))
            is not None
            and not frame.empty
        ),
        default=pd.Timestamp.today(),
    )
    start = (latest - timedelta(days=max(30, int(days)))).normalize()
    inserted = 0
    journeys = 0
    scanned = 0
    skipped = 0

    for path in files:
        symbol = path.name.replace(".IS_1d.parquet", "")
        frame = _load(symbol, cache)
        if frame is None or len(frame) < 120:
            skipped += 1
            continue
        scanned += 1
        try:
            events = enumerate_wilder_positive_divergences(
                _ticker(symbol), frame, start_date=start
            )
        except Exception:
            skipped += 1
            continue
        for signal in events:
            signal_date = str(signal["Sinyal_Tarihi"])
            clean = symbol.upper().replace(".IS", "")
            conn.execute(
                """
                INSERT OR IGNORE INTO scan_signals (
                    scan_date, symbol, scan_type, score, bias, entry_price,
                    stop_level, category, quality_score, quality_label,
                    quality_detail, journey_stage, journey_age, journey_key,
                    event_start_date, event_day, is_event_start
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    signal_date,
                    clean,
                    "rsi_pozitif_uyumsuzluk",
                    signal.get("Kalite_Skoru"),
                    "bullish",
                    signal.get("Giris"),
                    signal.get("Stop"),
                    "BIST 500 ",
                    signal.get("Kalite_Skoru"),
                    signal.get("Kalite"),
                    signal.get("Kalite_Detay"),
                    signal.get("Yolculuk_Asamasi"),
                    signal.get("Yolculuk_Gunu"),
                    "rsi_pozitif_uyumsuzluk",
                    signal_date,
                    1,
                    1,
                ),
            )
            was_inserted = int(conn.execute("SELECT changes()").fetchone()[0] > 0)
            inserted += was_inserted
            row = conn.execute(
                """
                SELECT id FROM scan_signals
                WHERE scan_date=? AND symbol=? AND scan_type='rsi_pozitif_uyumsuzluk'
                """,
                (signal_date, clean),
            ).fetchone()
            if row:
                signal_id = int(row[0])
                conn.execute(
                    "UPDATE scan_signals SET event_id=COALESCE(event_id, ?) WHERE id=?",
                    (signal_id, signal_id),
                )
                journey = signal.get("_journey") or {}
                upsert_rsi_journey(
                    conn, signal_id, clean, signal_date, journey
                )
                journeys += 1

    trading_dates = []
    benchmark = _load("XU100", cache)
    if benchmark is not None and not benchmark.empty:
        trading_dates = [
            pd.Timestamp(value).date().isoformat()
            for value in benchmark.loc[start:].index
        ]
    for scan_date in trading_dates:
        count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM scan_signals
                WHERE scan_type='rsi_pozitif_uyumsuzluk' AND scan_date=?
                """,
                (scan_date,),
            ).fetchone()[0]
        )
        register_scan_run(
            conn,
            "rsi_pozitif_uyumsuzluk",
            scan_date,
            count,
            "BIST 500 ",
        )
    conn.commit()
    return {
        "scanned_symbols": scanned,
        "skipped_symbols": skipped,
        "inserted_signals": inserted,
        "journeys": journeys,
        "start_date": start.date().isoformat(),
        "end_date": latest.date().isoformat(),
    }


def write_report(conn, backup_path: Path | None, results: dict) -> None:
    journey_total = int(conn.execute("SELECT COUNT(*) FROM rsi_journeys").fetchone()[0])
    journey_frame = pd.read_sql_query(
        """
        SELECT resolution, peak_day, max_gain_pct, max_r
        FROM rsi_journeys
        """,
        conn,
    )
    resolutions = conn.execute(
        """
        SELECT COALESCE(resolution,'unknown'), COUNT(*)
        FROM rsi_journeys
        GROUP BY COALESCE(resolution,'unknown')
        ORDER BY COUNT(*) DESC
        """
    ).fetchall()
    peak = conn.execute(
        """
        SELECT AVG(peak_day), AVG(max_gain_pct), AVG(max_r)
        FROM rsi_journeys
        WHERE peak_day IS NOT NULL
        """
    ).fetchone()
    peak_days = pd.to_numeric(journey_frame.get("peak_day"), errors="coerce").dropna()
    median_peak = float(peak_days.median()) if not peak_days.empty else None
    peak_q25 = float(peak_days.quantile(0.25)) if not peak_days.empty else None
    peak_q75 = float(peak_days.quantile(0.75)) if not peak_days.empty else None
    two_r_count = int((journey_frame.get("resolution") == "two_r").sum())
    stop_count = int(
        journey_frame.get("resolution", pd.Series(dtype=str))
        .isin(["stop", "stop_after_one_r", "stop_first_ambiguous"])
        .sum()
    )
    quality_counts = conn.execute(
        """
        SELECT scan_type, COALESCE(quality_label,'NOTSUZ'), COUNT(*)
        FROM scan_signals
        WHERE scan_type IN (
            'er_B11','zirve_devam','liderlik_aday','liderlik_yeni',
            'liderlik_teyitli','liderlik_gec','rsi_pozitif_uyumsuzluk'
        )
        GROUP BY scan_type, COALESCE(quality_label,'NOTSUZ')
        ORDER BY scan_type, COUNT(*) DESC
        """
    ).fetchall()
    b11_performance = conn.execute(
        """
        SELECT COALESCE(ss.quality_label,'NOTSUZ'), COUNT(sr.id),
               AVG(sr.ret_20g), AVG(sr.max_gain_20g), AVG(sr.max_loss_20g)
        FROM scan_signals ss
        JOIN signal_results sr ON sr.signal_id=ss.id
        WHERE ss.scan_type='er_B11'
          AND COALESCE(ss.is_event_start,1)=1
        GROUP BY COALESCE(ss.quality_label,'NOTSUZ')
        ORDER BY COUNT(sr.id) DESC
        """
    ).fetchall()
    zirve_performance = conn.execute(
        """
        SELECT COALESCE(ss.quality_label,'NOTSUZ'), COUNT(r.id),
               AVG(r.return_pct),
               AVG(CASE WHEN r.return_pct > 0 THEN 1.0 ELSE 0.0 END)
        FROM scan_signals ss
        JOIN signal_returns r ON r.signal_id=ss.id AND r.day_offset=3
        WHERE ss.scan_type='zirve_devam'
          AND COALESCE(ss.is_event_start,1)=1
          AND r.return_pct IS NOT NULL
        GROUP BY COALESCE(ss.quality_label,'NOTSUZ')
        ORDER BY COUNT(r.id) DESC
        """
    ).fetchall()
    lines = [
        "# Derinleştirme Paketi Denetim Raporu",
        "",
        f"- Oluşturulma: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- Yedek: {backup_path or 'dry-run'}",
        f"- RSI olay yolculuğu: {journey_total}",
        (
            f"- Doğal tepe merkezi: {median_peak:.1f}. seans "
            f"(orta %50: {peak_q25:.1f}–{peak_q75:.1f})"
            if median_peak is not None
            else "- Doğal tepe merkezi: veri yok"
        ),
        (
            f"- 2R görülme: {two_r_count}/{journey_total} "
            f"(%{two_r_count / journey_total * 100:.1f})"
            if journey_total
            else "- 2R görülme: veri yok"
        ),
        (
            f"- Stop: {stop_count}/{journey_total} "
            f"(%{stop_count / journey_total * 100:.1f})"
            if journey_total
            else "- Stop: veri yok"
        ),
        f"- Ortalama tepe seansı: {peak[0]:.2f}" if peak and peak[0] is not None else "- Ortalama tepe seansı: veri yok",
        f"- Ortalama en yüksek kazanç: %{peak[1]:.2f}" if peak and peak[1] is not None else "- Ortalama en yüksek kazanç: veri yok",
        f"- Ortalama en yüksek R: {peak[2]:.2f}R" if peak and peak[2] is not None else "- Ortalama en yüksek R: veri yok",
        "",
        "## İşlem özeti",
        "",
    ]
    for key, value in results.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## RSI sonuçlanma biçimi", ""])
    for label, count in resolutions:
        lines.append(f"- {label}: {count}")
    lines.extend(["", "## Kalite katmanı dağılımı", ""])
    for scan_type, label, count in quality_counts:
        lines.append(f"- {scan_type} · {label}: {count}")
    lines.extend(["", "## B11 pilot ayrımı — 20 seans yalnız tanı ölçümü", ""])
    for label, count, ret20, max_gain, max_loss in b11_performance:
        lines.append(
            f"- {label}: N={count}, kapanış %{(ret20 or 0):+.2f}, "
            f"en yüksek %{(max_gain or 0):+.2f}, en düşük %{(max_loss or 0):+.2f}"
        )
    lines.extend(["", "## Zirve Devam — kendi doğal 3 seans ölçümü", ""])
    for label, count, ret3, hit3 in zirve_performance:
        lines.append(
            f"- {label}: N={count}, ortalama %{(ret3 or 0):+.2f}, "
            f"pozitif oran %{(hit3 or 0) * 100:.1f}"
        )
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not DB_FILE.exists():
        raise SystemExit(f"Veritabanı bulunamadı: {DB_FILE}")
    backup = None if args.dry_run else _backup_database()
    target = ":memory:" if args.dry_run else str(DB_FILE)
    conn = sqlite3.connect(target, timeout=60)
    if args.dry_run:
        source = sqlite3.connect(DB_FILE)
        source.backup(conn)
        source.close()
    ensure_event_schema(conn)
    ensure_deepening_schema(conn)

    cache: dict[str, pd.DataFrame] = {}
    results = {
        "enriched": enrich_existing(conn, cache),
        "leadership": backfill_leadership(conn),
        "rsi": backfill_rsi(conn, cache, args.days, args.limit),
    }
    write_report(conn, backup, results)
    conn.commit()
    conn.close()
    print(results)
    print(f"report={REPORT_FILE}")
    if backup:
        print(f"backup={backup}")


if __name__ == "__main__":
    main()
