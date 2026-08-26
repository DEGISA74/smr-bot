"""
trajectory_automation.py — T+3 trajectory günlük otomasyon katmanı.

Bu katman Master Scan'i çalıştırmaz. Master Scan'in patron.db'ye yazdığı
sonuçları ve veri deposunun güncelliğini kontrol eder; hazır olduğunda
trajectory_forward_collector.py'yi çalıştırır.

Yazdığı yerler:
  - gelişmiş tarama/trajectory_automation_state.json
  - gelişmiş tarama/trajectory_automation.log
  - collector'ın kendi CSV/JSON çıktıları
  - bildirim istenirse signals.db içindeki mevcut scheduled_msgs kuyruğu

patron.db, app.py ve ana parquet dosyalarına yazmaz.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COLLECTOR = HERE / "trajectory_forward_collector.py"
ENTRY_SCENARIO = ROOT / "master_scan_giris_senaryolari.py"
DB = ROOT / "patron.db"
SIGNALS_DB = ROOT / "signals.db"
DAILY_XU = ROOT / "veriler" / "XU100.IS_1d.parquet"
HOURLY_XU = ROOT / "veriler_saatlik" / "XU100.IS_1h.parquet"
STATE_OUT = HERE / "trajectory_automation_state.json"
LOG_OUT = HERE / "trajectory_automation.log"
SNAPSHOT_OUT = HERE / "trajectory_forward_snapshots.csv"
TAIL_REPORT_OUT = HERE / "trajectory_right_tail_report.csv"
TAIL_REPORT_JSON = HERE / "trajectory_right_tail_report.json"
OUTCOME_OUT = HERE / "trajectory_forward_outcomes.csv"
KARNE_OUT = HERE / "trajectory_live_karne.csv"

DEFAULT_POLL_SECONDS = 300
DEFAULT_MAX_MINUTES = 270
DEFAULT_NOTIFY_CHAT_ID = 1034525990  # smr_bot.py içindeki mevcut ADMIN_ID
HOURLY_MAX_AGE_MINUTES = 150  # 15dk gecikme + tamamlanmamış saatlik bar payı
CROWDING_WARNING_MIN = 5


def now_istanbul() -> datetime:
    return datetime.now().astimezone()


def log(message: str) -> None:
    line = f"{now_istanbul().isoformat(timespec='seconds')} {message}"
    print(line.encode("ascii", errors="backslashreplace").decode("ascii"))
    with LOG_OUT.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_OUT.read_text(encoding="utf-8"))
    except Exception:
        return {"readiness": {}, "completed_close_dates": [], "notification_keys": []}


def save_state(state: dict[str, Any]) -> None:
    temp = STATE_OUT.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_OUT)


def is_trading_day(day: date) -> bool:
    try:
        from bist_calendar import is_trading_day as _is_trading_day
        return bool(_is_trading_day(day))
    except Exception:
        return day.weekday() < 5


def calendar_info() -> dict[str, Any]:
    today = now_istanbul().date()
    try:
        from bist_calendar import get_day_status, get_session_hours
        status, label = get_day_status(today)
        hours = get_session_hours(today)
    except Exception:
        status, label = ("open", "Normal Seans") if today.weekday() < 5 else ("closed", "Hafta sonu")
        hours = ("10:00", "18:00") if status == "open" else None
    return {
        "date": today.isoformat(),
        "status": status,
        "label": label,
        "session_hours": hours,
    }


def latest_expected_trading_day(today: date | None = None) -> str:
    cursor = today or now_istanbul().date()
    while not is_trading_day(cursor):
        cursor -= timedelta(days=1)
    return cursor.isoformat()


def parquet_last_date(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path, columns=["Close"])
        if df.empty:
            return None
        index = pd.to_datetime(df.index, errors="coerce")
        index = index[~index.isna()]
        return str(index[-1].date()) if len(index) else None
    except Exception as exc:
        log(f"[DATA] parquet okunamadı {path.name}: {exc}")
        return None


def parquet_last_timestamp(path: Path) -> pd.Timestamp | None:
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path, columns=["Close"])
        if df.empty:
            return None
        ts = pd.Timestamp(df.index[-1])
        if ts.tzinfo is None:
            ts = ts.tz_localize("Europe/Istanbul")
        else:
            ts = ts.tz_convert("Europe/Istanbul")
        return ts
    except Exception as exc:
        log(f"[DATA] saatlik zaman damgası okunamadı: {exc}")
        return None


def scan_signature(day: str | None) -> dict[str, Any]:
    if not DB.exists() or not day:
        return {"scan_date": None, "rows": 0, "symbols": 0, "max_scan_date": None}
    try:
        con = sqlite3.connect(DB)
        row = con.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT symbol), MAX(scan_date)
            FROM scan_signals
            WHERE substr(CAST(scan_date AS TEXT), 1, 10) = ?
            """,
            (day,),
        ).fetchone()
        con.close()
        return {
            "scan_date": day,
            "rows": int(row[0] or 0),
            "symbols": int(row[1] or 0),
            "max_scan_date": str(row[2]) if row[2] is not None else None,
        }
    except Exception as exc:
        log(f"[DB] scan_signals okunamadı: {exc}")
        return {"scan_date": day, "rows": 0, "symbols": 0, "max_scan_date": None}


def readiness(persist: bool = True) -> dict[str, Any]:
    today = now_istanbul().date()
    expected = latest_expected_trading_day(today)
    daily_last = parquet_last_date(DAILY_XU)
    scan = scan_signature(daily_last)
    data_ready = bool(is_trading_day(today) and daily_last and daily_last >= expected)
    scan_ready = bool(
        data_ready
        and scan["scan_date"] == daily_last
        and scan["rows"] > 0
    )
    signature_raw = json.dumps(
        {"expected": expected, "daily": daily_last, "scan": scan},
        sort_keys=True,
        ensure_ascii=False,
    )
    signature = hashlib.sha256(signature_raw.encode("utf-8")).hexdigest()[:16]

    state = load_state()
    old = state.get("readiness", {})
    stable_count = int(old.get("stable_count", 0)) + 1 if old.get("signature") == signature else 1
    result = {
        "expected_trading_day": expected,
        "today_is_trading_day": is_trading_day(today),
        "daily_last_date": daily_last,
        "scan": scan,
        "data_ready": data_ready,
        "scan_ready": scan_ready,
        "stable_count": stable_count,
        "stable_ready": bool(scan_ready and stable_count >= 2),
        "signature": signature,
    }
    if persist:
        state["readiness"] = result
        save_state(state)
    return result


def hourly_status() -> dict[str, Any]:
    expected = latest_expected_trading_day()
    last = parquet_last_date(HOURLY_XU)
    last_ts = parquet_last_timestamp(HOURLY_XU)
    now = pd.Timestamp(now_istanbul()).tz_convert("Europe/Istanbul")
    age = (now - last_ts).total_seconds() / 60 if last_ts is not None else None
    return {
        "expected_trading_day": expected,
        "hourly_last_date": last,
        "hourly_last_timestamp": last_ts.isoformat() if last_ts is not None else None,
        "age_minutes": round(age, 1) if age is not None else None,
        "max_age_minutes": HOURLY_MAX_AGE_MINUTES,
        "ready": bool(last and last >= expected and age is not None and 0 <= age <= HOURLY_MAX_AGE_MINUTES),
    }


def run_collector(mode: str, dry_run: bool) -> dict[str, Any]:
    command = [sys.executable, str(COLLECTOR), "--mode", mode]
    if dry_run:
        command.append("--dry-run")
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        log(f"[COLLECTOR] {mode} başarısız: {completed.stderr[-1000:]}")
        raise RuntimeError(f"collector {mode} exit={completed.returncode}")
    try:
        result = json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        log(f"[COLLECTOR] {mode} JSON çıktısı okunamadı: {completed.stdout[-1000:]}")
        raise
    log(f"[COLLECTOR] {mode}: {json.dumps(result, ensure_ascii=False)}")
    return result


def run_entry_scenarios(dry_run: bool) -> dict[str, Any]:
    """B11/C6/Zirve Devam/Radar2 iki giriş yolunun günlük fişini yeniler."""
    command = [sys.executable, str(ENTRY_SCENARIO), "--update"]
    if dry_run:
        command.append("--dry-run")
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        log(f"[ENTRY] giriş senaryoları başarısız: {completed.stderr[-1000:]}")
        raise RuntimeError(f"entry scenarios exit={completed.returncode}")
    try:
        result = json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        log(f"[ENTRY] JSON çıktısı okunamadı: {completed.stdout[-1000:]}")
        raise
    log(f"[ENTRY] {json.dumps(result, ensure_ascii=False)}")
    return result


def notify_chat_id() -> int:
    raw = os.environ.get("TRAJECTORY_NOTIFY_CHAT_ID")
    if raw:
        return int(raw)
    config_path = ROOT / "telegram_config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        value = config.get("trajectory_notify_chat_id")
        if value is not None:
            return int(value)
    except Exception:
        pass
    return DEFAULT_NOTIFY_CHAT_ID


def queue_notification(key: str, text: str, dry_run: bool) -> dict[str, Any]:
    state = load_state()
    sent = set(state.get("notification_keys", []))
    if key in sent:
        return {"queued": False, "duplicate": True, "key": key}
    if dry_run:
        log(f"[NOTIFY:DRY] {text.replace(chr(10), ' | ')}")
        return {"queued": False, "dry_run": True, "key": key}

    con = sqlite3.connect(SIGNALS_DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS scheduled_msgs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            send_at TEXT, chat_id INTEGER, text TEXT,
            sent INTEGER DEFAULT 0, added TEXT
        )"""
    )
    now = now_istanbul()
    cur = con.execute(
        "INSERT INTO scheduled_msgs (send_at, chat_id, text, sent, added) VALUES (?,?,?,?,?)",
        (
            now.strftime("%Y-%m-%d %H:%M"),
            notify_chat_id(),
            text,
            0,
            now.isoformat(timespec="seconds"),
        ),
    )
    con.commit()
    con.close()
    sent.add(key)
    state["notification_keys"] = sorted(sent)[-500:]
    save_state(state)
    log(f"[NOTIFY] scheduled_msgs kuyruğuna eklendi id={cur.lastrowid} key={key}")
    return {"queued": True, "id": cur.lastrowid, "key": key}


def close_message(result: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    """Yalnızca gerçek aşama değişimini bildirir; her kapanışta liste spam'i yapmaz."""
    asof = str(result.get("asof") or "")
    if not asof or not SNAPSHOT_OUT.exists():
        return {"notification": None}
    try:
        df = pd.read_csv(SNAPSHOT_OUT)
        df = df[(df["feature_source"] == "live_close") & (df["snapshot_date"].astype(str) == asof)]
        if df.empty:
            return {"notification": None}
        full = pd.read_csv(SNAPSHOT_OUT)
        full = full[full["feature_source"].astype(str) == "live_close"].copy()
        full["snapshot_date"] = full["snapshot_date"].astype(str).str[:10]
        full["_at"] = pd.to_datetime(full.get("snapshot_at"), errors="coerce")
        full = full.sort_values(["symbol", "event_start_date", "snapshot_date", "_at"])
        full = full.drop_duplicates(["symbol", "event_start_date", "snapshot_date"], keep="last")
        full["prior_v1"] = full.groupby(["symbol", "event_start_date"])["trajectory_v1"].shift(1)
        full["prior_core"] = full.groupby(["symbol", "event_start_date"])["cur_core"].shift(1)
        today = full[full["snapshot_date"] == asof].copy()
        candidates: list[dict[str, Any]] = []
        for item in today.itertuples(index=False):
            day = int(pd.to_numeric(getattr(item, "event_day", None), errors="coerce"))
            v1 = float(pd.to_numeric(getattr(item, "trajectory_v1", None), errors="coerce") or 0)
            core = float(pd.to_numeric(getattr(item, "cur_core", None), errors="coerce") or 0)
            strong = v1 >= 3 or core >= 2
            stage = None
            if day >= 3 and str(getattr(item, "status", "")) == "decision_ready" and strong:
                stage = "karar_hazir"
            elif day in (1, 2) and strong:
                prior_v1 = pd.to_numeric(getattr(item, "prior_v1", None), errors="coerce")
                prior_core = pd.to_numeric(getattr(item, "prior_core", None), errors="coerce")
                if (pd.notna(prior_v1) and v1 > float(prior_v1)) or (pd.notna(prior_core) and core > float(prior_core)):
                    stage = "gucleniyor"
            if stage:
                candidates.append({
                    "stage": stage, "symbol": str(getattr(item, "symbol")),
                    "event_date": str(getattr(item, "event_start_date")), "day": day,
                    "v1": int(v1), "core": int(core),
                    "persistence": int(pd.to_numeric(getattr(item, "israr", 0), errors="coerce") or 0),
                    "crowding": int(pd.to_numeric(getattr(item, "scan_types_last", 0), errors="coerce") or 0),
                })
        if not candidates:
            return {"notification": None, "transitions": 0}
        state = load_state()
        announced = set(state.get("trajectory_stage_keys", []))
        new = [item for item in candidates if f"{item['symbol']}|{item['event_date']}|{item['stage']}" not in announced]
        if not new:
            return {"notification": None, "transitions": 0}
        new.sort(key=lambda x: (x["stage"] == "karar_hazir", x["v1"], x["core"], x["persistence"]), reverse=True)
        shown = new[:10]
        lines = [f"📌 Trajectory kapanış aşama değişimi · {asof}", f"Yeni aşama geçen olay: {len(new)}"]
        for item in shown:
            tag = "🎯 KARAR HAZIR" if item["stage"] == "karar_hazir" else "📈 GÜÇLENİYOR"
            warning = f" · ⚠️ kalabalık ({item['crowding']})" if item["crowding"] >= CROWDING_WARNING_MIN else ""
            lines.append(f"{tag} {item['symbol']} T+{item['day']} · skor {item['v1']}/5 · çekirdek {item['core']}/3 · ısrar {item['persistence']} gün{warning}")
        fingerprint = hashlib.sha256("|".join(f"{x['symbol']}:{x['event_date']}:{x['stage']}" for x in new).encode("utf-8")).hexdigest()[:10]
        notification = queue_notification(f"close-stage|{asof}|{fingerprint}", "\n".join(lines), dry_run)
        if not dry_run:
            fresh = load_state()
            stage_keys = set(fresh.get("trajectory_stage_keys", []))
            stage_keys.update(f"{item['symbol']}|{item['event_date']}|{item['stage']}" for item in new)
            fresh["trajectory_stage_keys"] = sorted(stage_keys)[-3000:]
            save_state(fresh)
        return {"notification": notification, "transitions": len(new)}
    except Exception as exc:
        log(f"[NOTIFY] kapanış özeti üretilemedi: {exc}")
        return {"notification": None, "notification_error": str(exc)}


def intraday_progress_message(dry_run: bool) -> dict[str, Any]:
    """Önceki intraday snapshot'a göre hızlanan olayları tek digest bildirime çevirir."""
    if not SNAPSHOT_OUT.exists():
        return {"candidates": 0, "notification": None}
    try:
        df = pd.read_csv(SNAPSHOT_OUT)
        df = df[df["feature_source"].astype(str) == "live_intraday"].copy()
        if df.empty:
            return {"candidates": 0, "notification": None}
        candidates: list[dict[str, Any]] = []
        for (symbol, event_date), group in df.groupby(["symbol", "event_start_date"], sort=True):
            group = group.sort_values("snapshot_at")
            if len(group) < 2:
                continue
            old = group.iloc[-2]
            new = group.iloc[-1]
            old_v1, new_v1 = pd.to_numeric(old.get("trajectory_v1"), errors="coerce"), pd.to_numeric(new.get("trajectory_v1"), errors="coerce")
            old_core, new_core = pd.to_numeric(old.get("cur_core"), errors="coerce"), pd.to_numeric(new.get("cur_core"), errors="coerce")
            if not all(pd.notna(x) for x in [old_v1, new_v1, old_core, new_core]):
                continue
            old_strong = bool(old_v1 >= 3 or old_core >= 2)
            new_strong = bool(new_v1 >= 3 or new_core >= 2)
            # Aynı gün 3→4 gibi her kıpırdanış bildirim değildir. Sadece adayın
            # ilk kez güçlü eşiğe GEÇTİĞİ provizyonel saatlik an haber olur.
            if old_strong or not new_strong:
                continue
            old_ts = pd.Timestamp(old.get("snapshot_at"))
            new_ts = pd.Timestamp(new.get("snapshot_at"))
            elapsed_hours = max((new_ts - old_ts).total_seconds() / 3600, 1 / 60)
            speed = float((new_v1 - old_v1) / elapsed_hours)
            v1_series = pd.to_numeric(group["trajectory_v1"], errors="coerce")
            core_series = pd.to_numeric(group["cur_core"], errors="coerce")
            strong_series = ((v1_series >= 3) | (core_series >= 2)).fillna(False).tolist()
            persistence = 0
            for is_strong in reversed(strong_series):
                if not is_strong:
                    break
                persistence += 1
            crowding_raw = pd.to_numeric(new.get("scan_types_last"), errors="coerce")
            crowding = int(crowding_raw) if pd.notna(crowding_raw) else 0
            candidates.append({
                "symbol": str(symbol), "event_date": str(event_date),
                "snapshot_at": str(new.get("snapshot_at")),
                "event_day": int(pd.to_numeric(new.get("event_day"), errors="coerce")),
                "old_v1": int(old_v1), "new_v1": int(new_v1),
                "old_core": int(old_core), "new_core": int(new_core),
                "price": float(pd.to_numeric(new.get("price"), errors="coerce")),
                "speed": speed, "persistence": persistence, "crowding": crowding,
            })
        if not candidates:
            return {"candidates": 0, "notification": None}
        candidates.sort(key=lambda x: (x["speed"], x["persistence"], x["new_core"], x["new_v1"]), reverse=True)
        top = candidates[:10]
        lines = [
            "📈 Seans içi trajectory aşama geçişi (provizyonel)",
            f"İlk kez güçlü eşiğe geçen olay: {len(candidates)} | Gösterilen: {len(top)}",
        ]
        for item in top:
            lines.append(
                f"{item['symbol']} T+{item['event_day']}: "
                f"v1 {item['old_v1']}→{item['new_v1']}, "
                f"core {item['old_core']}→{item['new_core']}, "
                f"hız {item['speed']:+.2f}/saat, ısrar {item['persistence']} bar, "
                f"fiyat {item['price']:.2f}"
                + (f", ⚠️ kalabalık ({item['crowding']})" if item["crowding"] >= CROWDING_WARNING_MIN else "")
            )
        stamp = max(item["snapshot_at"] for item in top)
        fingerprint = hashlib.sha256(
            "|".join(f"{x['symbol']}:{x['new_v1']}:{x['new_core']}" for x in top).encode("utf-8")
        ).hexdigest()[:10]
        notification = queue_notification(
            f"intraday-progress|{stamp}|{fingerprint}",
            "\n".join(lines),
            dry_run,
        )
        return {"candidates": len(candidates), "notification": notification}
    except Exception as exc:
        log(f"[NOTIFY] intraday özeti üretilemedi: {exc}")
        return {"candidates": 0, "notification": None, "notification_error": str(exc)}


def wilson_lower(successes: int, total: int) -> float:
    if total <= 0:
        return 0.0
    z = 1.959963984540054
    p = successes / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    spread = z * ((p * (1 - p) / total) + (z * z / (4 * total * total))) ** 0.5
    return max(0.0, (centre - spread) / denom)


def right_tail_report(dry_run: bool) -> dict[str, Any]:
    """Scanner bazında ortalamadan bağımsız %30+ sağ-kuyruk sicili üretir."""
    if not OUTCOME_OUT.exists():
        return {"outcomes": 0, "report_rows": 0, "notification": None}
    outcomes = pd.read_csv(OUTCOME_OUT)
    if outcomes.empty:
        return {"outcomes": 0, "report_rows": 0, "notification": None}
    # Sağ-kuyruk sicili yalnızca gerçek 20 günlük pencere tamamlandığında konuşur.
    # 5/10 günlük ara sonuçların bunu erkenden şişirmesine izin verilmez.
    if "outcome_20_mature" in outcomes.columns:
        outcomes = outcomes[pd.to_numeric(outcomes["outcome_20_mature"], errors="coerce").fillna(0) == 1].copy()
    if outcomes.empty:
        return {"outcomes": 0, "report_rows": 0, "notification": None}
    try:
        con = sqlite3.connect(DB)
        events = pd.read_sql_query(
            """
            SELECT symbol, event_start_date, scan_type
            FROM scan_signals
            WHERE scan_date >= '2026-08-07'
              AND event_start_date IS NOT NULL
              AND scan_type IS NOT NULL
            """,
            con,
        )
        con.close()
        events["symbol"] = events["symbol"].astype(str).str.replace(".IS", "", regex=False)
        events["event_start_date"] = events["event_start_date"].astype(str).str[:10]
        events = events.drop_duplicates(["symbol", "event_start_date", "scan_type"])
        outcomes["symbol"] = outcomes["symbol"].astype(str)
        outcomes["event_start_date"] = outcomes["event_start_date"].astype(str).str[:10]
        outcomes["outcome_key"] = outcomes["symbol"] + "|" + outcomes["event_start_date"]
        joined = outcomes.merge(events, on=["symbol", "event_start_date"], how="inner")
        joined = joined.drop_duplicates(["outcome_key", "scan_type"])
        if joined.empty:
            return {"outcomes": len(outcomes), "report_rows": 0, "notification": None}
        rows: list[dict[str, Any]] = []
        for scan_type, group in joined.groupby("scan_type", sort=True):
            n = int(group["outcome_key"].nunique())
            hits = int(pd.to_numeric(group["hit30_hi"], errors="coerce").fillna(0).sum())
            rows.append({
                "scan_type": str(scan_type), "n": n, "hit30_count": hits,
                "hit30_rate": round(hits / n, 4) if n else 0.0,
                "hit30_wilson_lower": round(wilson_lower(hits, n), 4),
                "max_mfe": round(float(pd.to_numeric(group["mfe"], errors="coerce").max()), 4),
                "avg_postret": round(float(pd.to_numeric(group["postret"], errors="coerce").mean()), 4),
            })
        report = pd.DataFrame(rows).sort_values(
            ["hit30_wilson_lower", "hit30_rate", "n"], ascending=False
        )
        if not dry_run:
            report.to_csv(TAIL_REPORT_OUT, index=False, encoding="utf-8-sig")
            TAIL_REPORT_JSON.write_text(
                json.dumps({"outcomes": len(outcomes), "rows": rows}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        state = load_state()
        previous_count = int(state.get("tail_outcome_count", 0))
        notification = None
        if len(outcomes) > previous_count:
            display = report[report["n"] >= 5].head(5)
            lines = [
                "📊 Sağ-kuyruk sicili güncellendi",
                f"Olgunlaşan event: {len(outcomes)}",
            ]
            for item in display.itertuples(index=False):
                lines.append(
                    f"{item.scan_type}: {item.hit30_count}/{item.n} = "
                    f"%{item.hit30_rate * 100:.1f} %30+ | en yüksek MFE %{item.max_mfe:.1f}"
                )
            if len(display):
                notification = queue_notification(
                    f"tail-report|{len(outcomes)}", "\n".join(lines), dry_run
                )
        if not dry_run:
            state["tail_outcome_count"] = int(len(outcomes))
            save_state(state)
        return {"outcomes": len(outcomes), "report_rows": len(report), "notification": notification}
    except Exception as exc:
        log(f"[TAIL] sağ-kuyruk raporu üretilemedi: {exc}")
        return {"outcomes": len(outcomes), "report_rows": 0, "notification_error": str(exc)}


def run_close(dry_run: bool) -> dict[str, Any]:
    status = readiness(persist=not dry_run)
    if not status["stable_ready"]:
        return {"mode": "close", "status": "not_ready", "readiness": status}
    if not dry_run and status.get("daily_last_date") in set(load_state().get("completed_close_dates", [])):
        return {
            "mode": "close",
            "status": "already_completed",
            "asof": status.get("daily_last_date"),
        }
    result = run_collector("close", dry_run)
    summary = close_message(result, dry_run)
    # Her yeni kesin kapanışta, daha önceki kohortların 5/10/20 günlük sonuçları
    # da sessizce bir gün ileri taşınır. Ayrı bir görev unutulursa sicil donmaz.
    settle = run_settle(dry_run)
    if not dry_run:
        state = load_state()
        dates = set(state.get("completed_close_dates", []))
        if result.get("asof"):
            dates.add(str(result["asof"]))
        state["completed_close_dates"] = sorted(dates)[-120:]
        save_state(state)
    return {"mode": "close", "status": "completed", "collector": result, "settle": settle, **summary}


def run_settle(dry_run: bool) -> dict[str, Any]:
    result = run_collector("settle", dry_run)
    entry = run_entry_scenarios(dry_run)
    tail = right_tail_report(dry_run)
    try:
        from trajectory_live_karne import write_live_karne
        karne = write_live_karne(dry_run=dry_run)
    except Exception as exc:
        log(f"[KARNE] canlı sicil üretilemedi: {exc}")
        karne = {"error": str(exc)}
    return {"mode": "settle", "status": "completed", "collector": result, "entry_scenarios": entry, "right_tail": tail, "karne": karne}


def run_intraday(dry_run: bool) -> dict[str, Any]:
    calendar = calendar_info()
    if calendar["status"] == "closed":
        return {"mode": "intraday", "status": "calendar_closed", "calendar": calendar}
    hourly = hourly_status()
    if not hourly["ready"]:
        key = f"hourly-stale|{hourly['expected_trading_day']}|{hourly['hourly_last_date']}"
        text = (
            "⚠️ Trajectory saatlik kontrol bekledi\n"
            f"Beklenen gün: {hourly['expected_trading_day']}\n"
            f"Gelen son saatlik gün: {hourly['hourly_last_date'] or 'yok'}"
        )
        notification = queue_notification(key, text, dry_run)
        return {"mode": "intraday", "status": "hourly_not_ready", "calendar": calendar, "hourly": hourly, "notification": notification}
    result = run_collector("intraday", dry_run)
    progress = intraday_progress_message(dry_run)
    return {"mode": "intraday", "status": "completed", "calendar": calendar, "collector": result, "progress": progress}


def watch_close(poll_seconds: int, max_minutes: int, dry_run: bool) -> dict[str, Any]:
    started = now_istanbul()
    deadline = started + timedelta(minutes=max_minutes)
    checks = 0
    while True:
        checks += 1
        status = readiness(persist=not dry_run)
        log(
            f"[READY] data={status['data_ready']} scan={status['scan_ready']} "
            f"stable={status['stable_count']}/2 daily={status['daily_last_date']}"
        )
        if status["stable_ready"]:
            return {"mode": "watch-close", "checks": checks, "result": run_close(dry_run)}
        if dry_run or now_istanbul() >= deadline:
            return {"mode": "watch-close", "checks": checks, "status": "timeout_or_not_ready", "readiness": status}
        time.sleep(max(5, poll_seconds))


def main() -> int:
    parser = argparse.ArgumentParser(description="Trajectory günlük otomasyon ve bildirim orkestratörü")
    parser.add_argument("--mode", choices=["status", "watch-close", "close", "intraday", "settle", "tail-report", "karne"], default="status")
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--max-minutes", type=int, default=DEFAULT_MAX_MINUTES)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.mode == "status":
        result = {"readiness": readiness(persist=not args.dry_run), "hourly": hourly_status()}
    elif args.mode == "watch-close":
        result = watch_close(args.poll_seconds, args.max_minutes, args.dry_run)
    elif args.mode == "close":
        result = run_close(args.dry_run)
    elif args.mode == "intraday":
        result = run_intraday(args.dry_run)
    elif args.mode == "tail-report":
        result = right_tail_report(args.dry_run)
    elif args.mode == "karne":
        from trajectory_live_karne import write_live_karne
        result = write_live_karne(dry_run=args.dry_run)
    else:
        result = run_settle(args.dry_run)

    result["run_at"] = now_istanbul().isoformat(timespec="seconds")
    result["dry_run"] = bool(args.dry_run)
    output = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(output.encode("ascii", errors="backslashreplace").decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
