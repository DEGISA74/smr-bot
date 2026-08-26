#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V2 Erken Radar'ın bağımsız 17:40 karne ve 17:45 aday göndericisi."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from v2_early_radar_db import (
    get_candidate_run,
    get_service_started_date,
    initialize_service,
    load_signal_state,
    mark_published,
    publication_exists,
    record_candidate_run,
    record_result_run,
    stage_results,
    stage_signals,
)
from v2_early_radar_engine import scan_early_radar


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


ISTANBUL = ZoneInfo("Europe/Istanbul")
DEFAULT_ADMIN_ID = "1034525990"
DISPLAY_REASON = {
    "momentum": "momentum",
    "mum_yapisi": "mum yapısı",
    "konum": "fiyat konumu",
    "oynaklik": "oynaklık",
    "hacim": "hacim",
    "likidite": "likidite",
    "trend": "trend",
    "manip_riski": "hareket yapısı",
    "piyasa": "piyasa ortamı",
    "goreli_guc": "göreli güç",
}


def _load_json(path: Path) -> dict | None:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _config(project_root: Path) -> dict:
    return _load_json(project_root / "telegram_config.json") or {}


def _nested(mapping: dict, *keys: str) -> object | None:
    value: object = mapping
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _token(project_root: Path) -> str | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token.strip()
    config = _config(project_root)
    for path in (("bot_token",), ("token",), ("telegram", "bot_token"), ("bot", "token")):
        value = _nested(config, *path)
        if value:
            return str(value).strip()
    return None


def _targets(project_root: Path, *, test: bool) -> tuple[str, ...]:
    config = _config(project_root)
    admin = str(
        _nested(config, "admin_id")
        or _nested(config, "admin", "chat_id")
        or DEFAULT_ADMIN_ID
    )
    if test:
        return (admin,)
    candidates = (
        _nested(config, "channels", "free", "chat_id"),
        _nested(config, "chat", "chat_id"),
    )
    targets: list[str] = []
    for value in candidates:
        if value and str(value) not in targets:
            targets.append(str(value))
    if not targets:
        raise RuntimeError("Telegram yayın hedefleri telegram_config.json içinde bulunamadı.")
    return tuple(targets)


def _telegram_send(project_root: Path, chat_id: str, message: str) -> bool:
    token = _token(project_root)
    if not token:
        print("Telegram bot anahtarı bulunamadı.")
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=25,
        )
        if response.status_code != 200:
            print("Telegram HTTP", response.status_code, response.text[:160])
            return False
        return True
    except Exception as exc:
        print("Telegram hatası:", exc)
        return False


def _send_to_targets(project_root: Path, targets: tuple[str, ...], message: str, label: str) -> bool:
    success = True
    for chat_id in targets:
        sent = _telegram_send(project_root, chat_id, message)
        print(f"{label}:", "OK" if sent else "BAŞARISIZ", "→", chat_id)
        success = success and sent
    return success


def _save_json_atomic(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _reason_text(raw: object) -> str:
    parts = [part.strip() for part in str(raw).split("+") if part.strip()]
    return " + ".join(DISPLAY_REASON.get(part, part.replace("_", " ")) for part in parts)


def _candidate_state(candidates: pd.DataFrame, metadata: dict) -> dict:
    signal_date = str(metadata["signal_date"])
    return {
        "motor": "V2 Erken Radar",
        "as_of": signal_date,
        "status": "success",
        "created_at": datetime.now(ISTANBUL).isoformat(),
        "market_snapshot_timestamp": str(metadata["market_snapshot_timestamp"]),
        "eligible": int(metadata.get("eligible", 0)),
        "model_version": str(metadata.get("model_version", "")),
        "official_v2_untouched": True,
        "partial_day_shadow_model": True,
        "list": [
            {
                "rank": int(rank),
                "ticker": str(row["ticker"]),
                "olasilik_pct": round(float(row["olasilik_pct"]), 4),
                "neden": str(row.get("neden", "")),
                "reference_price": round(float(row["close"]), 6),
                "reference_timestamp": str(row["snapshot_timestamp"]),
                "b11_teyidi": bool(row.get("b11_teyidi", False)),
            }
            for rank, (_, row) in enumerate(candidates.iterrows(), start=1)
        ],
    }


def _candidate_message(candidates: pd.DataFrame, metadata: dict) -> str:
    signal_date = pd.Timestamp(metadata["signal_date"])
    snapshot = pd.Timestamp(metadata["market_snapshot_timestamp"])
    lines = [
        "🛰️ V2 ERKEN RADAR — T+1 ADAYLARI",
        f"{signal_date.strftime('%d.%m.%Y')} saatlik fotoğrafı · son veri {snapshot.strftime('%H:%M')}",
        f"Bir sonraki işlem günü için izlenen ilk {len(candidates)} · uygun havuz {int(metadata['eligible'])}",
        "",
    ]
    for number, (_, row) in enumerate(candidates.iterrows(), start=1):
        badge = " · ⭐ B11 teyidi" if bool(row.get("b11_teyidi", False)) else ""
        lines.append(
            f"📡 {number:>2}. {row['ticker']:<6} · erken puan %{float(row['olasilik_pct']):.1f} "
            f"· {_reason_text(row['neden'])}{badge}"
        )
    if bool(candidates.get("b11_teyidi", pd.Series(dtype=bool)).fillna(False).any()):
        lines.extend(["", "⭐ B11 teyidi: Tepe Yakını Sıkışma taramasıyla da eşleşti."])
    lines.extend(
        [
            "",
            "ℹ️ Bu, tamamlanmamış günün saatlik verisiyle çalışan ayrı bir erken radardır. "
            "Resmî V2 listesi ve puanı değişmez. İşlem sinyali ve yatırım tavsiyesi değildir.",
        ]
    )
    return "\n".join(lines)


def _hourly_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    index = pd.to_datetime(frame.index)
    if getattr(index, "tz", None) is None:
        index = index.tz_localize(ISTANBUL)
    else:
        index = index.tz_convert(ISTANBUL)
    frame = frame.copy()
    frame.index = index
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    return frame.loc[:, ["High", "Close"]].apply(pd.to_numeric, errors="coerce")


def _evaluation_date(project_root: Path, signal_date: pd.Timestamp) -> pd.Timestamp | None:
    market = _hourly_frame(project_root / "veriler_saatlik" / "XU100.IS_1h.parquet")
    dates = pd.DatetimeIndex(pd.to_datetime(pd.Index(market.index.date)).unique()).sort_values()
    later = dates[dates > signal_date.normalize()]
    return pd.Timestamp(later[0]).normalize() if len(later) else None


def _evaluate_state(project_root: Path, state: dict) -> tuple[list[dict], pd.Timestamp | None, int]:
    signal_date = pd.Timestamp(state["as_of"]).normalize()
    evaluation_date = _evaluation_date(project_root, signal_date)
    if evaluation_date is None:
        return [], None, len(state.get("list") or [])
    results: list[dict] = []
    missing = 0
    for fallback_rank, item in enumerate(state.get("list") or [], start=1):
        ticker = str(item.get("ticker", "")).strip().upper()
        path = project_root / "veriler_saatlik" / f"{ticker}.IS_1h.parquet"
        try:
            frame = _hourly_frame(path)
            day = frame[frame.index.date == evaluation_date.date()].dropna()
            reference = float(item["reference_price"])
            if day.empty or reference <= 0:
                raise ValueError("değerlendirme barı yok")
            high = float(day["High"].max())
            last = float(day["Close"].iloc[-1])
            results.append(
                {
                    "ticker": ticker,
                    "rank": int(item.get("rank", fallback_rank)),
                    "reference_price": reference,
                    "high": high,
                    "last": last,
                    "high_return_pct": (high / reference - 1.0) * 100.0,
                    "last_return_pct": (last / reference - 1.0) * 100.0,
                    "snapshot_timestamp": pd.Timestamp(day.index[-1]).isoformat(),
                }
            )
        except Exception:
            missing += 1
    results.sort(key=lambda row: row["high_return_pct"], reverse=True)
    return results, evaluation_date, missing


def _results_message(
    state: dict, results: list[dict], evaluation_date: pd.Timestamp, missing: int
) -> str:
    snapshot = max(pd.Timestamp(row["snapshot_timestamp"]) for row in results)
    lines = [
        "📊 V2 ERKEN RADAR — DÜNKÜ LİSTE NE YAPTI?",
        f"{evaluation_date.strftime('%d.%m.%Y')} · radar fiyatı → T+1 gün içi zirve / son fiyat",
        f"Son saatlik veri {snapshot.strftime('%H:%M')} · ölçülen {len(results)}/{len(state.get('list') or [])}",
        "",
    ]
    medals = ("🥇", "🥈", "🥉")
    for number, row in enumerate(results):
        marker = medals[number] if number < len(medals) else "▫️"
        lines.append(
            f"{marker} {row['ticker']:<6} zirve {row['high_return_pct']:+.1f}% · "
            f"son {row['last_return_pct']:+.1f}%"
        )
    high_returns = [float(row["high_return_pct"]) for row in results]
    last_returns = [float(row["last_return_pct"]) for row in results]
    hits = sum(value >= 5.0 for value in high_returns)
    positive = sum(value > 0.0 for value in last_returns)
    lines.extend(
        [
            "───────────────",
            f"🎯 Gün içinde +%5: {hits}/{len(results)} · Son fiyatta artıda: {positive}/{len(results)}",
            f"📈 Ortalama zirve {sum(high_returns) / len(high_returns):+.1f}% · "
            f"ortalama son {sum(last_returns) / len(last_returns):+.1f}%",
        ]
    )
    if missing:
        lines.append(f"⚠️ Saatlik verisi ölçülemeyen {missing} aday var.")
    lines.extend(
        [
            "",
            "ℹ️ Son fiyat, 17:40 çalışmasında elde bulunan en yeni saatlik bardır; resmî kapanış değildir. "
            "Erken Radar resmî V2'den ayrıdır. Geçmiş performans gelecek sonucu garanti etmez.",
        ]
    )
    return "\n".join(lines)


def _today_gate(date: pd.Timestamp, allow_stale: bool) -> None:
    if allow_stale:
        return
    today = pd.Timestamp(datetime.now(ISTANBUL).date())
    if today.weekday() >= 5 or date.normalize() != today:
        raise RuntimeError(
            f"Güncel seans verisi yok: veride {date.date()}, Türkiye takviminde {today.date()}. Gönderim durduruldu."
        )


def _safe_reason(error: object) -> str:
    text = " ".join(str(error).replace("\r", " ").replace("\n", " ").split())
    return (text or "Saatlik veri okunamadı.")[:300]


def _candidate_failure_message(run_date: pd.Timestamp, reason: str) -> str:
    return "\n".join(
        [
            "⚠️ V2 ERKEN RADAR — VERİ ALINAMADI",
            f"{run_date.strftime('%d.%m.%Y')} için saatlik veri güncel veya yeterli değil.",
            "Motor çalıştırılamadı; bir sonraki işlem günü için aday listesi üretilemedi.",
            f"Kontrol notu: {reason}",
            "",
            "ℹ️ Bu gün başarısız olarak kaydedildi. Bir sonraki 17:40 karnesinde sonuç olmadığı "
            "belirtilecek; motor 17:45'te yeni liste için yeniden çalışacak. Resmî V2 etkilenmedi.",
        ]
    )


def _no_signal_message(
    signal_date: pd.Timestamp,
    evaluation_date: pd.Timestamp,
    reason: str,
) -> str:
    return "\n".join(
        [
            "📊 V2 ERKEN RADAR — SONUÇ YOK",
            f"{signal_date.strftime('%d.%m.%Y')} günü aday listesi üretilemedi.",
            f"Bu nedenle {evaluation_date.strftime('%d.%m.%Y')} için ölçülecek T+1 sonucu yok.",
            f"Kayıt notu: {reason}",
            "",
            "ℹ️ Süreç durmadı. Erken Radar bugün 17:45'te yeni T+1 listesi için yeniden çalışacak. "
            "Resmî V2 etkilenmedi.",
        ]
    )


def _result_failure_message(
    signal_date: pd.Timestamp,
    evaluation_date: pd.Timestamp,
    reason: str,
) -> str:
    return "\n".join(
        [
            "⚠️ V2 ERKEN RADAR — SONUÇ HESAPLANAMADI",
            f"{signal_date.strftime('%d.%m.%Y')} aday listesi mevcut; ancak "
            f"{evaluation_date.strftime('%d.%m.%Y')} saatlik verisi alınamadı.",
            "Bu nedenle T+1 sonuç karnesi üretilemedi.",
            f"Kontrol notu: {reason}",
            "",
            "ℹ️ Bu hata kaydedildi. Erken Radar bugün 17:45'te yeni liste için ayrıca yeniden çalışacak. "
            "Resmî V2 etkilenmedi.",
        ]
    )


def _previous_weekday(date: pd.Timestamp) -> pd.Timestamp:
    previous = date.normalize() - pd.Timedelta(days=1)
    while previous.weekday() >= 5:
        previous -= pd.Timedelta(days=1)
    return previous


def _market_dates(project_root: Path) -> pd.DatetimeIndex:
    market = _hourly_frame(project_root / "veriler_saatlik" / "XU100.IS_1h.parquet")
    return pd.DatetimeIndex(pd.to_datetime(pd.Index(market.index.date)).unique()).sort_values()


def _expected_signal_date(project_root: Path, db_path: Path, today: pd.Timestamp) -> pd.Timestamp:
    calendar_previous = _previous_weekday(today)
    if get_candidate_run(db_path, str(calendar_previous.date())) is not None:
        return calendar_previous
    try:
        dates = _market_dates(project_root)
        today_is_present = bool((dates == today.normalize()).any())
        observed_previous = dates[dates < today.normalize()]
        if today_is_present and len(observed_previous):
            return pd.Timestamp(observed_previous[-1]).normalize()
    except Exception:
        pass
    return calendar_previous


def _announce_candidate_failure(
    args: argparse.Namespace,
    project_root: Path,
    state_path: Path,
    db_path: Path,
    run_date: pd.Timestamp,
    reason: str,
) -> int:
    message = _candidate_failure_message(run_date, reason)
    print(message)
    if args.dry_run:
        print("\nKuru çalışma: hata Telegram'a gönderilmedi ve canlı kayıtlar değiştirilmedi.")
        return 0
    publication_key = str(run_date.date())
    failure_state = {
        "motor": "V2 Erken Radar",
        "as_of": publication_key,
        "status": "failed",
        "reason": reason,
        "created_at": datetime.now(ISTANBUL).isoformat(),
        "official_v2_untouched": True,
        "list": [],
    }
    if not args.test:
        record_candidate_run(db_path, publication_key, "failed", reason)
        _save_json_atomic(state_path, failure_state)
        if publication_exists(db_path, "candidates_error", publication_key):
            print(publication_key, "veri hatası daha önce yayımlanmış; tekrar gönderilmedi.")
            return 0
    sent = _send_to_targets(
        project_root,
        _targets(project_root, test=args.test),
        message,
        "veri hatası gönderimi",
    )
    if not sent:
        return 1
    if not args.test:
        published_at = datetime.now(ISTANBUL).isoformat()
        record_candidate_run(
            db_path,
            publication_key,
            "failed",
            reason,
            published_at=published_at,
        )
        mark_published(db_path, "candidates_error", publication_key, published_at=published_at)
    return 0


def _run_candidates(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    state_path = Path(args.state).resolve()
    db_path = Path(args.db).resolve()
    today = pd.Timestamp(datetime.now(ISTANBUL).date())
    requested_date = pd.Timestamp(args.as_of).normalize() if args.as_of else today
    publication_key = str(requested_date.date())
    if not args.test and not args.dry_run:
        initialize_service(db_path, str(today.date()))
    if not args.test and not args.dry_run and publication_exists(db_path, "candidates", publication_key):
        print(publication_key, "Erken Radar adayları daha önce yayımlanmış; tekrar gönderilmedi.")
        return 0
    try:
        candidates, metadata = scan_early_radar(
            project_root, top_n=args.top, min_bars=args.min_bars, as_of=args.as_of
        )
        signal_date = pd.Timestamp(metadata["signal_date"])
        _today_gate(signal_date, args.allow_stale)
    except Exception as exc:
        return _announce_candidate_failure(
            args,
            project_root,
            state_path,
            db_path,
            requested_date,
            _safe_reason(exc),
        )
    state = _candidate_state(candidates, metadata)
    message = _candidate_message(candidates, metadata)
    print(message)
    if args.preview_state:
        _save_json_atomic(Path(args.preview_state).resolve(), state)
        print("Önizleme durum dosyası yazıldı:", Path(args.preview_state).resolve())
    if args.dry_run:
        print("\nKuru çalışma: Telegram, canlı durum dosyası ve Erken Radar DB değiştirilmedi.")
        return 0
    publication_key = str(state["as_of"])
    if not args.test:
        stage_signals(db_path, state)
        record_candidate_run(
            db_path,
            publication_key,
            "success",
            "Aday listesi üretildi.",
            snapshot_timestamp=str(metadata.get("market_snapshot_timestamp", "")),
            eligible_pool=int(metadata.get("eligible", 0)),
            candidate_count=len(candidates),
        )
    sent = _send_to_targets(project_root, _targets(project_root, test=args.test), message, "aday gönderimi")
    if not sent:
        return 1
    if not args.test:
        published_at = datetime.now(ISTANBUL).isoformat()
        stage_signals(db_path, state, published_at=published_at)
        record_candidate_run(
            db_path,
            publication_key,
            "success",
            "Aday listesi üretildi.",
            snapshot_timestamp=str(metadata.get("market_snapshot_timestamp", "")),
            eligible_pool=int(metadata.get("eligible", 0)),
            candidate_count=len(candidates),
            published_at=published_at,
        )
        mark_published(db_path, "candidates", publication_key, published_at=published_at)
        _save_json_atomic(state_path, state)
        print("Erken Radar durum dosyası güncellendi:", state_path)
    return 0


def _run_results(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    state_path = Path(args.state).resolve()
    db_path = Path(args.db).resolve()
    today = (
        pd.Timestamp(args.as_of).normalize()
        if args.allow_stale and args.as_of
        else pd.Timestamp(datetime.now(ISTANBUL).date())
    )
    state_file = _load_json(state_path)
    service_was_active = db_path.exists() or state_path.exists()
    service_started = get_service_started_date(db_path)
    if not args.test and not args.dry_run:
        initialize_service(db_path, str(today.date()))
        service_started = get_service_started_date(db_path)

    if args.allow_stale and state_file and state_file.get("as_of"):
        signal_date = pd.Timestamp(state_file["as_of"]).normalize()
        state = state_file if state_file.get("list") else None
        run = {"status": str(state_file.get("status", "success")), "reason": state_file.get("reason", "")}
    else:
        signal_date = _expected_signal_date(project_root, db_path, today)
        run = get_candidate_run(db_path, str(signal_date.date()))
        state = load_signal_state(db_path, str(signal_date.date()))
        if state is None and state_file and str(state_file.get("as_of")) == str(signal_date.date()):
            state = state_file if state_file.get("list") else None

    evaluation_expected = today
    publication_key = f"{signal_date.date()}->{evaluation_expected.date()}"
    if not args.test and not args.dry_run and publication_exists(db_path, "results", publication_key):
        print(publication_key, "Erken Radar karnesi daha önce yayımlanmış; tekrar gönderilmedi.")
        return 0

    if state is None:
        before_service = bool(
            service_started
            and signal_date.normalize() < pd.Timestamp(service_started).normalize()
        )
        if run is None and (before_service or (not service_was_active and not service_started)):
            print("Erken Radar henüz aday listesi yayımlamadı; ilk karne sessizce atlandı.")
            return 0
        reason = _safe_reason(
            (run or {}).get("reason")
            or "Bilgisayar kapalıydı, görev çalışmadı veya saatlik veri alınamadı."
        )
        message = _no_signal_message(signal_date, evaluation_expected, reason)
        print(message)
        if args.dry_run:
            print("\nKuru çalışma: sonuç-yok mesajı gönderilmedi ve canlı kayıtlar değiştirilmedi.")
            return 0
        if not args.test:
            record_result_run(
                db_path,
                str(signal_date.date()),
                str(evaluation_expected.date()),
                "no_signal",
                reason,
            )
            if publication_exists(db_path, "results_no_signal", publication_key):
                print(publication_key, "sonuç-yok mesajı daha önce yayımlanmış; tekrar gönderilmedi.")
                return 0
        sent = _send_to_targets(
            project_root,
            _targets(project_root, test=args.test),
            message,
            "sonuç-yok gönderimi",
        )
        if not sent:
            return 1
        if not args.test:
            published_at = datetime.now(ISTANBUL).isoformat()
            record_result_run(
                db_path,
                str(signal_date.date()),
                str(evaluation_expected.date()),
                "no_signal",
                reason,
                published_at=published_at,
            )
            mark_published(db_path, "results_no_signal", publication_key, published_at=published_at)
        return 0

    try:
        results, evaluation_date, missing = _evaluate_state(project_root, state)
        if evaluation_date is None:
            raise RuntimeError("Bir sonraki işlem gününe ait saatlik piyasa verisi yok.")
        _today_gate(evaluation_date, args.allow_stale)
        if not results:
            raise RuntimeError("Adayların hiçbirinde T+1 saatlik veri yok.")
    except Exception as exc:
        reason = _safe_reason(exc)
        message = _result_failure_message(signal_date, evaluation_expected, reason)
        print(message)
        if args.dry_run:
            print("\nKuru çalışma: sonuç hatası gönderilmedi ve canlı kayıtlar değiştirilmedi.")
            return 0
        if not args.test:
            record_result_run(
                db_path,
                str(signal_date.date()),
                str(evaluation_expected.date()),
                "data_error",
                reason,
            )
            if publication_exists(db_path, "results_error", publication_key):
                print(publication_key, "sonuç veri hatası daha önce yayımlanmış; tekrar gönderilmedi.")
                return 0
        sent = _send_to_targets(
            project_root,
            _targets(project_root, test=args.test),
            message,
            "sonuç hatası gönderimi",
        )
        if not sent:
            return 1
        if not args.test:
            published_at = datetime.now(ISTANBUL).isoformat()
            record_result_run(
                db_path,
                str(signal_date.date()),
                str(evaluation_expected.date()),
                "data_error",
                reason,
                published_at=published_at,
            )
            mark_published(db_path, "results_error", publication_key, published_at=published_at)
        return 0

    message = _results_message(state, results, evaluation_date, missing)
    print(message)
    if args.dry_run:
        print("\nKuru çalışma: Telegram ve Erken Radar DB değiştirilmedi.")
        return 0
    if not args.test:
        stage_signals(db_path, state, published_at=state.get("published_at"))
        stage_results(db_path, str(state["as_of"]), str(evaluation_date.date()), results)
        record_result_run(
            db_path,
            str(state["as_of"]),
            str(evaluation_date.date()),
            "success",
            "T+1 sonuç karnesi üretildi.",
        )
    sent = _send_to_targets(project_root, _targets(project_root, test=args.test), message, "karne gönderimi")
    if not sent:
        return 1
    if not args.test:
        published_at = datetime.now(ISTANBUL).isoformat()
        record_result_run(
            db_path,
            str(state["as_of"]),
            str(evaluation_date.date()),
            "success",
            "T+1 sonuç karnesi üretildi.",
            published_at=published_at,
        )
        mark_published(db_path, "results", publication_key, published_at=published_at)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V2 Erken Radar bağımsız Telegram göndericisi")
    parser.add_argument("--mode", choices=("candidates", "results", "initialize"), required=True)
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--state")
    parser.add_argument("--db")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--min-bars", type=int, default=8)
    parser.add_argument("--as-of", help="Yalnız prova için geçmiş aday veya değerlendirme tarihi")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test", action="store_true", help="Yalnız yöneticiye yollar; canlı kayıtları değiştirmez")
    parser.add_argument("--allow-stale", action="store_true", help="Yalnız prova için güncellik kilidini açar")
    parser.add_argument("--preview-state", help="Kuru provada üretilen aday durumunu ayrı dosyaya yazar")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = Path(args.project_root).resolve()
    if args.state is None:
        args.state = str(project_root / "v2_early_radar_state.json")
    if args.db is None:
        args.db = str(project_root / "v2_early_radar.db")
    if args.mode == "initialize":
        started_date = str(datetime.now(ISTANBUL).date())
        initialize_service(Path(args.db).resolve(), started_date)
        print("V2 Erken Radar hizmet başlangıcı kaydedildi:", started_date)
        return 0
    if args.mode == "candidates":
        return _run_candidates(args)
    return _run_results(args)


if __name__ == "__main__":
    raise SystemExit(main())
