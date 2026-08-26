#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tarama Motoru V4'ün yalnızca yönetici bottan sabah göndericisidir."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from yuksek_getiri_engine_v4 import filter_v3_state_v4, parse_as_of
from tarama_sureklilik import DataUnavailable, guarded_main, require_data_date, skip_previous_report

BASE = Path(__file__).resolve().parent
ADMIN_ID = "1034525990"  # Bilerek tek hedef: yayın kanalı yok.
ISTANBUL = ZoneInfo("Europe/Istanbul")

try:
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _token() -> str | None:
    for source in ("/home/wm11tr/weektweet/.env", "/home/wm11tr/insider/.env"):
        try:
            for line in Path(source).read_text(encoding="utf-8").splitlines():
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    return line.split("=", 1)[1].strip()
        except Exception:
            pass
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def _send(message: str) -> bool:
    token = _token()
    if not token:
        print("Telegram bot anahtarı bulunamadı.")
        return False
    try:
        response = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": ADMIN_ID, "text": message, "disable_web_page_preview": True}, timeout=25)
        if response.status_code != 200:
            print("Telegram HTTP", response.status_code, response.text[:160])
            return False
        return True
    except Exception as exc:
        print("Telegram hatası:", exc)
        return False


def _load_state(path: Path) -> dict | None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else None
    except Exception:
        return None


def _save_state(path: Path, date: str, candidates: pd.DataFrame, meta: dict) -> None:
    payload = {"engine": "v4", "stage": meta["stage"], "as_of": date, "sent_at": datetime.now(ISTANBUL).isoformat(), "list": candidates[["ticker", "olasilik_pct", "close", "ayrilan_tutar_tl"]].to_dict("records"), "metadata": meta}
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _daily(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path).copy()
    index = pd.DatetimeIndex(pd.to_datetime(frame.index))
    frame.index = index.tz_localize(None).normalize() if index.tz is not None else index.normalize()
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def _previous_results(state: dict | None, data_dir: Path) -> tuple[list[dict], pd.Timestamp | None]:
    if not state or not state.get("as_of") or not state.get("list"):
        return [], None
    signal = pd.Timestamp(state["as_of"]).normalize()
    market_path = data_dir / "XU100.IS_1d.parquet"
    if not market_path.exists():
        return [], None
    sessions = _daily(market_path).index
    later = sessions[sessions > signal]
    if not len(later):
        return [], None
    evaluation = pd.Timestamp(later[0])
    rows = []
    for item in state["list"]:
        ticker = str(item.get("ticker", "")).upper()
        path = data_dir / f"{ticker}.IS_1d.parquet"
        if not path.exists():
            continue
        frame = _daily(path)
        if signal not in frame.index or evaluation not in frame.index:
            continue
        base, high, close = (float(frame.at[signal, "Close"]), float(frame.at[evaluation, "High"]), float(frame.at[evaluation, "Close"]))
        if min(base, high, close) > 0:
            rows.append({"ticker": ticker, "high_ret": (high / base - 1) * 100, "close_ret": (close / base - 1) * 100})
    return rows, evaluation


def _report(rows: list[dict], evaluation: pd.Timestamp | None) -> str | None:
    if not rows or evaluation is None:
        return None
    rows.sort(key=lambda row: row["high_ret"], reverse=True)
    lines = ["📊 DÜNKÜ TARAMA MOTORU V4 — NE YAPTI?", f"{evaluation.strftime('%d.%m.%Y')} seansı · önceki kapanış → gün içi en yüksek", "⚠️ Bu sabah gölge evresinin karnesidir; gerçek 17:35 giriş sonucu değildir.", ""]
    for no, row in enumerate(rows, 1):
        lines.append(f"{no}. {row['ticker']:<6} zirve {row['high_ret']:+.1f}% · kapanış {row['close_ret']:+.1f}%")
    average = sum(row["high_ret"] for row in rows) / len(rows)
    return "\n".join(lines + ["──────────────", f"🏆 Ortalama zirve hareketi: {average:+.1f}%", "ℹ️ Kasa sonucu gösterilmez: gerçek giriş saati henüz 17:35 değildir.", "⏳ Bir sonraki günün listesi 5 dakika içinde hazırlanacak."])


def _today(candidates: pd.DataFrame, meta: dict) -> str:
    lines = [f"🧪 TARAMA MOTORU V4 — {datetime.now(ISTANBUL).strftime('%d.%m.%Y')}", f"Bir sonraki gün için gölge listesi · resmî pazar + likidite kapısından geçen havuz: {meta['eligible_v4_universe']}"]
    if not meta["allocation_enabled"]:
        return "\n".join(lines + ["", f"🛑 İŞLEM YOK — {meta['no_trade_reason']}", "V4, 17:35 kapanış-öncesi verisi hazır olana kadar sermaye önermez."])
    amount = f"{meta['allocation_per_ticker_tl']:,.0f}".replace(",", ".")
    lines += [f"1.000.000 TL kuralı: {len(candidates)} hisseye eşit, hisse başı {amount} TL", ""]
    for rank, (_, row) in enumerate(candidates.iterrows(), 1):
        lines.append(f"📈 {rank}. {row['ticker']:<6} · %{float(row['olasilik_pct']):.1f} · {row['pazar']} pazar · {row['neden']}")
    return "\n".join(lines + ["", "ℹ️ Gölge aşaması: bu sabah listesi alım emri değildir. V4’ün gerçek hedefi 17:35’te oluşacak ayrı giriş modelidir."])


def _wait(clock: str) -> None:
    hour, minute = (int(value) for value in clock.split(":", 1))
    now = datetime.now(ISTANBUL)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    remaining = (target - now).total_seconds()
    if remaining <= 0:
        return
    if remaining > 900:
        raise RuntimeError("Planlanan saate 15 dakikadan fazla var; cron saati doğrulanmalı.")
    while remaining > 0:
        time.sleep(min(15, remaining))
        remaining = (target - datetime.now(ISTANBUL)).total_seconds()


def main() -> int:
    parser = argparse.ArgumentParser(description="Tarama Motoru V4 — yalnızca yönetici botu")
    parser.add_argument("--dry-run", action="store_true")
    delivery = parser.add_mutually_exclusive_group()
    delivery.add_argument("--report-only", action="store_true", help="Sadece T+1 sonuç karnesini gönderir")
    delivery.add_argument("--list-only", action="store_true", help="Sadece yeni V4 listesini gönderir")
    parser.add_argument("--send-at", default="09:50")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--data-dir", default=str(BASE / "veriler"))
    parser.add_argument("--hourly-dir", default=str(BASE / "veriler_saatlik"))
    parser.add_argument("--status", default=str(BASE / "kapanis_oncesi_resmi_durum.json"))
    parser.add_argument("--model", default=str(BASE / "yuksek_getiri_v3_model.json"))
    parser.add_argument("--state", default=str(BASE / "yuksek_getiri_v4_state.json"))
    parser.add_argument("--source-state", default=str(BASE / "yuksek_getiri_v3_state.json"), help="V3'ün 09:40 aday kaydı")
    parser.add_argument("--as-of")
    args = parser.parse_args()
    state_path, data_dir = Path(args.state), Path(args.data_dir)
    state = _load_state(state_path)
    if skip_previous_report():
        state = None
    if args.report_only:
        rows, evaluation = _previous_results(state, data_dir)
        if state and state.get("list"):
            if not rows or evaluation is None:
                raise DataUnavailable("V4 sonuç karnesi için güncel günlük veri üretilemedi.")
            require_data_date(evaluation, timing="same_day", enabled=not args.dry_run)
        report = _report(rows, evaluation)
        if not report:
            print("Değerlendirilebilir önceki V4 listesi yok; sonuç mesajı gönderilmedi.")
            return 0
        print(report)
        if args.dry_run:
            print("\nKuru çalışma: Telegram'a gönderilmedi, kayıt dosyası değişmedi.")
            return 0
        _wait(args.send_at)
        return 0 if _send(report) else 1
    source_state = _load_state(Path(args.source_state))
    if source_state and source_state.get("as_of"):
        require_data_date(source_state["as_of"], timing="morning", enabled=not args.dry_run)
    if not args.dry_run:
        hourly_dates = []
        for item in (source_state or {}).get("list", []):
            hourly_path = Path(args.hourly_dir) / f"{str(item.get('ticker', '')).upper()}.IS_1h.parquet"
            try:
                hourly = pd.read_parquet(hourly_path)
                if not hourly.empty:
                    hourly_dates.append(pd.DatetimeIndex(pd.to_datetime(hourly.index)).max())
            except Exception:
                continue
        require_data_date(max(hourly_dates) if hourly_dates else None, timing="same_day")
    candidates, meta = filter_v3_state_v4(source_state, data_dir, Path(args.hourly_dir), Path(args.status), as_of=parse_as_of(args.as_of))
    if state and state.get("as_of") == str(meta["list_date"]) and not args.dry_run:
        print("Bu kapanış için V4 listesi zaten gönderilmiş; tekrar gönderim yapılmadı.")
        return 0
    report, today = _report(*_previous_results(state, data_dir)), _today(candidates, meta)
    if not args.list_only:
        print(report or "(Değerlendirilebilir önceki V4 listesi yok.)")
    print("\n" + today)
    if args.dry_run:
        print("\nKuru çalışma: Telegram'a gönderilmedi, kayıt dosyası değişmedi.")
        return 0
    _wait(args.send_at)
    if report and not args.list_only and not _send(report):
        return 1
    if not _send(today):
        return 1
    _save_state(state_path, str(meta["list_date"]), candidates, meta)
    print("V4 yönetici kaydı güncellendi.")
    return 0


if __name__ == "__main__":
    _report_mode = "--report-only" in sys.argv
    raise SystemExit(guarded_main(
        engine="v4_report" if _report_mode else "v4_list",
        previous_engine="v4_list" if _report_mode else None,
        label="TARAMA MOTORU V4",
        main_func=main,
        targets=(ADMIN_ID,),
        base=BASE,
        live="--dry-run" not in sys.argv,
        announce_previous=_report_mode,
    ))
