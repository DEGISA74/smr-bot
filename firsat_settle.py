#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIRSAT RADARI — SONUÇ DEFTERİ (settle / karne).

firsat_radari.py her gün TÜM tespitleri firsat_radar_log.jsonl'a yazar ama getiriyi
hesaplamaz. Bu iş, canlı radara DOKUNMADAN, o log'u okur ve her sinyalin sinyal fiyatından
ileri getirisini (T+1/T+3/T+5/T+10 · zirve ve kapanış) firsat_karne.db'ye yazar.

Hiçbir sinyal atılmaz; ama alarma giden (yeni KIRDI Q>=3 + SIKIŞTI) sinyaller
`alarm_eligible=1` ile işaretlenir → "fiilen gönderilen" ayrı süzülebilir.
Sinyal 10 seans olgunlaşana kadar her koşuda yeniden güncellenir (idempotent);
olgunlaşınca complete=1 olur ve bir daha işlenmez.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
HORIZONS = (1, 3, 5, 10)


def _daily(path: str) -> pd.DataFrame:
    frame = pd.read_parquet(path).copy()
    idx = pd.DatetimeIndex(pd.to_datetime(frame.index))
    frame.index = idx.tz_localize(None).normalize() if idx.tz is not None else idx.normalize()
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def _ensure_schema(con: sqlite3.Connection) -> None:
    cols = ", ".join(f"ret_h{h}_high REAL, ret_h{h}_close REAL" for h in HORIZONS)
    con.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS firsat_signals(
            date TEXT, tk TEXT, kutu TEXT, durum TEXT, type TEXT, q INTEGER,
            fiyat REAL, alarm_eligible INTEGER, logged_at TEXT,
            PRIMARY KEY(date, tk, kutu)
        );
        CREATE TABLE IF NOT EXISTS firsat_results(
            date TEXT, tk TEXT, kutu TEXT, fiyat REAL, {cols},
            sessions_avail INTEGER, complete INTEGER, settled_at TEXT,
            PRIMARY KEY(date, tk, kutu)
        );
        """
    )


def _alarm_eligible(sig: dict) -> int:
    kutu = str(sig.get("kutu", ""))
    if kutu == "sikis":
        return 1
    if kutu == "kirdi" and int(sig.get("Q", 0) or 0) >= 3 and str(sig.get("durum", "")) != "fail":
        return 1
    return 0


def _archive_log(con: sqlite3.Connection, log_path: str, now: str) -> int:
    if not os.path.exists(log_path):
        return 0
    n = 0
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                s = json.loads(line)
            except Exception:
                continue
            tk = str(s.get("tk", "")).strip().upper()
            date = str(s.get("date", "")).strip()
            kutu = str(s.get("kutu", "")).strip()
            if not tk or not date or not kutu:
                continue
            con.execute(
                """
                INSERT INTO firsat_signals(date, tk, kutu, durum, type, q, fiyat, alarm_eligible, logged_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, tk, kutu) DO UPDATE SET
                    durum=excluded.durum, type=excluded.type, q=excluded.q,
                    fiyat=excluded.fiyat, alarm_eligible=excluded.alarm_eligible
                """,
                (date, tk, kutu, s.get("durum"), s.get("type"), int(s.get("Q", 0) or 0),
                 s.get("fiyat"), _alarm_eligible(s), now),
            )
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Fırsat Radarı sonuç defteri (settle)")
    ap.add_argument("--db", default=os.path.join(BASE, "firsat_karne.db"))
    ap.add_argument("--log", default=os.path.join(BASE, "firsat_radar_log.jsonl"))
    ap.add_argument("--data-dir", default=os.path.join(BASE, "veriler"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = datetime.now().isoformat(timespec="seconds")
    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    try:
        _ensure_schema(con)
        archived = _archive_log(con, args.log, now)
        print(f"Arşivlenen/güncellenen sinyal: {archived}")

        # complete=1 olanlar bir daha işlenmez
        done = {
            (r["date"], r["tk"], r["kutu"])
            for r in con.execute("SELECT date, tk, kutu FROM firsat_results WHERE complete=1").fetchall()
        }
        todo = con.execute(
            "SELECT date, tk, kutu, fiyat FROM firsat_signals ORDER BY tk, date"
        ).fetchall()

        # ticker başına parquet'i tek kez oku
        by_tk: dict[str, list[sqlite3.Row]] = {}
        for r in todo:
            if (r["date"], r["tk"], r["kutu"]) in done:
                continue
            by_tk.setdefault(r["tk"], []).append(r)

        settled = pending = missing = 0
        for tk, sigs in by_tk.items():
            path = os.path.join(args.data_dir, f"{tk}.IS_1d.parquet")
            if not os.path.exists(path):
                missing += len(sigs)
                continue
            try:
                frame = _daily(path)
            except Exception:
                missing += len(sigs)
                continue
            highs, closes, index = frame["High"], frame["Close"], frame.index
            for r in sigs:
                base = float(r["fiyat"]) if r["fiyat"] else 0.0
                if base <= 0:
                    continue
                d = pd.Timestamp(r["date"]).normalize()
                fut = index[index > d]
                navail = len(fut)
                if navail < 1:
                    pending += 1
                    continue
                vals: dict[str, float | None] = {}
                for h in HORIZONS:
                    if navail >= h:
                        window = fut[:h]
                        vals[f"ret_h{h}_high"] = float(highs.loc[window].max()) / base * 100.0 - 100.0
                        vals[f"ret_h{h}_close"] = float(closes.loc[window[-1]]) / base * 100.0 - 100.0
                    else:
                        vals[f"ret_h{h}_high"] = None
                        vals[f"ret_h{h}_close"] = None
                complete = 1 if navail >= max(HORIZONS) else 0
                if not args.dry_run:
                    cols = ["date", "tk", "kutu", "fiyat"] + [f"ret_h{h}_high" for h in HORIZONS] + \
                           [f"ret_h{h}_close" for h in HORIZONS] + ["sessions_avail", "complete", "settled_at"]
                    ordered = [r["date"], r["tk"], r["kutu"], base] + \
                              [vals[f"ret_h{h}_high"] for h in HORIZONS] + \
                              [vals[f"ret_h{h}_close"] for h in HORIZONS] + [navail, complete, now]
                    placeholders = ", ".join("?" for _ in cols)
                    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("date", "tk", "kutu"))
                    con.execute(
                        f"INSERT INTO firsat_results({', '.join(cols)}) VALUES ({placeholders}) "
                        f"ON CONFLICT(date, tk, kutu) DO UPDATE SET {updates}",
                        ordered,
                    )
                if complete:
                    settled += 1
                else:
                    pending += 1
        if not args.dry_run:
            con.commit()

        # özet
        total_res = con.execute("SELECT COUNT(*) FROM firsat_results").fetchone()[0]
        print(f"BİTTİ: {settled} sinyal olgun (T+10 tam), {pending} olgunlaşıyor, "
              f"{missing} veri yok. Defterde toplam {total_res} sonuç satırı.")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
