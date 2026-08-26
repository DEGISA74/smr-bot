#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gelişmiş Yüksek Getiri Motoru V2 Telegram göndericisi.

Eski tavan/yüksek getiri göndericisinden tamamen bağımsızdır. Her çalışmada:
1) Bir önceki V2 listesinin sonraki seans gün içi zirve karnesini,
2) Güncel V2 ilk 10 aday listesini gönderir.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from yuksek_getiri_engine_v2 import load_model, score_latest
from patron2_yuksek_getiri_db import (
    calculate_tp4_strategy,
    mark_run_published,
    settle_results,
    stage_candidates,
)
from tarama_sureklilik import DataUnavailable, guarded_main, require_data_date, skip_previous_report


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


BASE = Path(__file__).resolve().parent
FREE_CHAT = "-1003943892201"
SOHBET_CHAT = "-1003851678286"
ADMIN_ID = "1034525990"
BROADCAST = (FREE_CHAT, SOHBET_CHAT)
ISTANBUL = ZoneInfo("Europe/Istanbul")

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


def _token() -> str | None:
    for path in ("/home/wm11tr/weektweet/.env", "/home/wm11tr/insider/.env"):
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("TELEGRAM_BOT_TOKEN="):
                        return line.split("=", 1)[1].strip()
        except Exception:
            pass
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def _telegram_send(chat_id: str, message: str) -> bool:
    token = _token()
    if not token:
        print("Telegram bot anahtarı bulunamadı.")
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=25,
        )
        if response.status_code != 200:
            print("Telegram HTTP", response.status_code, response.text[:160])
            return False
        return True
    except Exception as exc:
        print("Telegram hatası:", exc)
        return False


def _load_state(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as handle:
            state = json.load(handle)
        return state if isinstance(state, dict) else None
    except Exception:
        return None


def _save_state(
    path: Path,
    target_date: str,
    candidates: pd.DataFrame,
    metadata: dict,
    model: dict,
) -> None:
    payload = {
        "motor": "gelişmiş yüksek getiri motoru v2",
        "as_of": target_date,
        "sent_at": datetime.now(ISTANBUL).isoformat(),
        "eligible": int(metadata.get("eligible", 0)),
        "model_version": str(model.get("model_version", "")),
        "list": [
            {
                "rank": int(rank),
                "ticker": str(row["ticker"]),
                "olasilik_pct": round(float(row["olasilik_pct"]), 4),
                "neden": str(row.get("neden", "")),
                "close": round(float(row["close"]), 6),
                "b11_teyidi": bool(row.get("b11_teyidi", False)),
            }
            for rank, (_, row) in enumerate(candidates.iterrows(), start=1)
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _candidate_rows(candidates: pd.DataFrame) -> list[dict]:
    return [
        {
            "rank": rank,
            "symbol": str(row["ticker"]),
            "probability_pct": float(row["olasilik_pct"]),
            "reason": str(row.get("neden", "")),
            "signal_close": float(row["close"]),
        }
        for rank, (_, row) in enumerate(candidates.iterrows(), start=1)
    ]


def _state_candidate_rows(state: dict, results: list[dict] | None = None) -> list[dict]:
    result_by_symbol = {
        str(row["ticker"]).strip().upper(): row for row in (results or [])
    }
    rows = []
    for fallback_rank, item in enumerate(state.get("list") or [], start=1):
        symbol = str(item.get("ticker", "")).strip().upper()
        result = result_by_symbol.get(symbol)
        rows.append(
            {
                "rank": int(item.get("rank", fallback_rank)),
                "symbol": symbol,
                "probability_pct": float(item.get("olasilik_pct", 0.0)),
                "reason": str(item.get("neden", "")),
                "signal_close": float(result["base"]) if result else item.get("close"),
            }
        )
    return rows


def _result_rows(results: list[dict]) -> list[dict]:
    return [
        {
            "symbol": row["ticker"],
            "signal_close": row["base"],
            "t1_open": row["open"],
            "t1_high": row["high"],
            "t1_close": row["close"],
            "close_to_high_return_pct": row["return_pct"],
            "close_to_close_return_pct": row["close_return_pct"],
            "open_to_high_return_pct": row["open_to_high_return_pct"],
            "open_to_close_return_pct": row["open_to_close_return_pct"],
            "tp4_hit": row["tp4_hit"],
            "strategy_exit_reason": row["strategy_exit_reason"],
            "strategy_exit_price": row["strategy_exit_price"],
            "strategy_gross_return_pct": row["strategy_gross_return_pct"],
            "strategy_net_return_pct": row["strategy_net_return_pct"],
        }
        for row in results
    ]


def _normalized_ohlcv(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    index = pd.to_datetime(frame.index)
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    frame = frame.copy()
    frame.index = index.normalize()
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def _evaluate_previous(state: dict | None, data_dir: Path) -> tuple[list[dict], pd.Timestamp | None]:
    if not state or not state.get("as_of") or not state.get("list"):
        return [], None

    as_of = pd.Timestamp(state["as_of"]).normalize()
    market_path = data_dir / "XU100.IS_1d.parquet"
    if not market_path.exists():
        print("Karne atlandı: XU100 günlük verisi bulunamadı.")
        return [], None

    try:
        market = _normalized_ohlcv(market_path)
        later_sessions = market.index[market.index > as_of]
        if len(later_sessions) == 0:
            print("Karne atlandı: değerlendirilecek sonraki tamamlanmış seans henüz yok.")
            return [], None
        evaluation_date = pd.Timestamp(later_sessions[0])
    except Exception as exc:
        print("Karne piyasa takvimi okunamadı:", exc)
        return [], None

    results: list[dict] = []
    for item in state["list"]:
        ticker = str(item.get("ticker", "")).strip().upper()
        path = data_dir / f"{ticker}.IS_1d.parquet"
        if not ticker or not path.exists():
            print(f"Karne atlandı: {ticker or '(boş kod)'} verisi yok.")
            continue
        try:
            frame = _normalized_ohlcv(path)
            if as_of not in frame.index or evaluation_date not in frame.index:
                print(f"Karne atlandı: {ticker} için iki gerekli seans birlikte yok.")
                continue
            base = float(frame.at[as_of, "Close"])
            open_price = float(frame.at[evaluation_date, "Open"])
            high = float(frame.at[evaluation_date, "High"])
            close = float(frame.at[evaluation_date, "Close"])
            if min(base, open_price, high, close) <= 0:
                continue
            strategy = calculate_tp4_strategy(open_price, high, close)
            results.append(
                {
                    "ticker": ticker,
                    "rank": int(item.get("rank", 999)),
                    "probability": float(item.get("olasilik_pct", 0.0)),
                    "b11_teyidi": bool(item.get("b11_teyidi", False)),
                    "base": base,
                    "open": open_price,
                    "high": high,
                    "close": close,
                    "return_pct": (high / base - 1.0) * 100.0,
                    "close_return_pct": (close / base - 1.0) * 100.0,
                    "open_to_high_return_pct": (high / open_price - 1.0) * 100.0,
                    "open_to_close_return_pct": (close / open_price - 1.0) * 100.0,
                    **strategy,
                }
            )
        except Exception as exc:
            print(f"Karne okunamadı: {ticker}: {exc}")
    results.sort(key=lambda row: row["return_pct"], reverse=True)
    return results, evaluation_date


def _previous_message(results: list[dict], evaluation_date: pd.Timestamp | None) -> str | None:
    if not results or evaluation_date is None:
        return None
    # Sadece motorun dün en çok güvendiği ilk 5 (güven sırası) — sonuç bilinmeden
    # seçilir, hindsight yok. Görselde en çok yükseleni üste almak için getiriye göre sırala.
    results = sorted(results, key=lambda row: row["rank"])[:5]
    results = sorted(results, key=lambda row: row["return_pct"], reverse=True)
    lines = [
        "📊 DÜNKÜ GELİŞMİŞ YÜKSEK GETİRİ MOTORU V2 — NE YAPTI?",
        f"{evaluation_date.strftime('%d.%m.%Y')} seansı · önceki kapanış → gün içi en yüksek",
        "🎯 Ölçülen hedef: gün içi en az +%5 hareket",
        "⭐ Dün en güvendiğimiz ilk 5 hisse",
        "HİSSE / ZİRVE GETİRİSİ                 │ %4 STRATEJİ NET",
        "",
    ]
    medals = ("🥇", "🥈", "🥉")
    for number, row in enumerate(results):
        marker = medals[number] if number < len(medals) else "▫️"
        strategy_label = "TP" if row["tp4_hit"] else "KAPANIŞ"
        badge = " ⭐B11" if row.get("b11_teyidi") else ""
        lines.append(
            f"{marker} {row['ticker']:<6}{badge} {row['return_pct']:+.1f}%   "
            f"{row['base']:.2f} → {row['high']:.2f}  │ "
            f"{row['strategy_net_return_pct']:+.1f}% {strategy_label}"
        )
    returns = [row["return_pct"] for row in results]
    hits = sum(value >= 5.0 for value in returns)
    positive = sum(value > 0.0 for value in returns)
    best = results[0]
    tp4_hits = sum(bool(row["tp4_hit"]) for row in results)
    strategy_average = sum(float(row["strategy_net_return_pct"]) for row in results) / len(results)
    final_total = 1_000_000 * (1 + strategy_average / 100.0)
    final_total_str = f"{final_total:,.0f}".replace(",", ".")
    lines.extend(
        [
            "───────────────",
            f"🏆 En iyi: {best['ticker']} {best['return_pct']:+.1f}%  ·  "
            f"Ortalama {sum(returns) / len(returns):+.1f}%",
            f"🎯 +%5 hedefi: {hits}/{len(results)}  ·  Artıda: {positive}/{len(results)}",
            f"💼 %4 stratejisi: {tp4_hits}/{len(results)} hedef · 1 milyon lira eşit "
            f"bölünseydi → {final_total_str} TL",
            "",
            "ℹ️ %4 strateji sütunu: T+1 açılıştan alım; +%4 görülürse satış, görülmezse "
            "seans kapanışında çıkış. Alışta ve satışta ayrı ayrı on binde 5 komisyon düşülmüştür. "
            "Fiyat kayması ve vergiler dahil değildir. Geçmiş performans gelecek sonucu garanti etmez.",
        ]
    )
    return "\n".join(lines)


def _reason_text(raw: object) -> str:
    parts = [part.strip() for part in str(raw).split("+") if part.strip()]
    return " + ".join(DISPLAY_REASON.get(part, part.replace("_", " ")) for part in parts)


def _today_message(candidates: pd.DataFrame, metadata: dict, model: dict) -> str:
    today = datetime.now(ISTANBUL).strftime("%d.%m.%Y")
    objective = float(model["objective"]["target_pct"])
    ranked = model["validation"]["ranked"].get(f"top_{len(candidates)}")
    if ranked is None:
        ranked = model["validation"]["ranked"].get("top_10", {})

    lines = [
        f"🚀 GELİŞMİŞ YÜKSEK GETİRİ MOTORU V2 — {today}",
        f"🎯 Amaç: önceki kapanıştan sonraki seansın gün içi zirvesinde en az +%{objective:g} hareketi yakalamak",
    ]
    if ranked:
        lines.append(
            f"🧪 Görülmemiş {int(ranked['days'])} seans: ilk {len(candidates)} isabet "
            f"%{float(ranked['precision_pct']):.1f} · piyasa %{float(ranked['matched_baseline_pct']):.1f} · "
            f"{float(ranked['lift']):.2f} kat yoğunluk"
        )
    lines.extend(
        [
            f"Veri kapanışı: {pd.Timestamp(metadata['target_date']).strftime('%d.%m.%Y')} · "
            f"uygun havuz {int(metadata['eligible'])} · ilk {len(candidates)}",
            "",
        ]
    )
    for number, (_, row) in enumerate(candidates.iterrows(), start=1):
        badge = " · ⭐ B11 teyidi" if bool(row.get("b11_teyidi", False)) else ""
        lines.append(
            f"📈 {number:>2}. {row['ticker']:<6} · model olasılığı %{float(row['olasilik_pct']):.1f} "
            f"· {_reason_text(row['neden'])}{badge}"
        )
    if bool(candidates.get("b11_teyidi", pd.Series(dtype=bool)).fillna(False).any()):
        lines.extend(["", "⭐ B11 teyidi: Tepe Yakını Sıkışma taramasıyla da eşleşti."])
    lines.extend(
        [
            "",
            "ℹ️ Motor yüksek hareket adayını yakalamaya çalışır; bu liste işlem sinyali değildir. "
            "Alış-satış zamanı ve risk yönetimi kullanıcıya aittir. Yatırım tavsiyesi değildir.",
        ]
    )
    return "\n".join(lines)


def _send_to_targets(targets: tuple[str, ...], message: str, label: str) -> bool:
    success = True
    for chat_id in targets:
        sent = _telegram_send(chat_id, message)
        print(f"{label}:", "OK" if sent else "BAŞARISIZ", "→", chat_id)
        success = success and sent
    return success


def _wait_for_delivery_time(clock_text: str) -> None:
    try:
        hour_text, minute_text = clock_text.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError as exc:
        raise ValueError("Gönderim saati SS:DD biçiminde olmalı; örnek: 09:30") from exc

    now = datetime.now(ISTANBUL)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    remaining = (target - now).total_seconds()
    if remaining <= 0:
        print(f"Planlanan {clock_text} saati geçti; mesaj bekletilmeden gönderilecek.")
        return
    if remaining > 15 * 60:
        raise RuntimeError(
            f"Planlanan saate {remaining / 60:.1f} dakika var; yanlış cron saatine karşı gönderim durduruldu."
        )
    print(f"Hesaplama hazır; Türkiye saati {clock_text} olana kadar beklenecek.")
    while True:
        remaining = (target - datetime.now(ISTANBUL)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(15.0, remaining))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gelişmiş Yüksek Getiri Motoru V2 Telegram göndericisi")
    parser.add_argument("--test", action="store_true", help="Yalnızca yöneticiye gönderir; günlük kaydı değiştirmez")
    parser.add_argument("--dry-run", action="store_true", help="Mesajları üretir fakat Telegram'a göndermez")
    parser.add_argument("--send-at", help="Hesaplamadan sonra Türkiye saatine göre bu saati bekler; örnek 09:30")
    parser.add_argument("--top", type=int, default=10, help="Gösterilecek aday sayısı")
    parser.add_argument("--data-dir", default=str(BASE / "veriler"))
    parser.add_argument("--model", default=str(BASE / "yuksek_getiri_v2_model.json"))
    parser.add_argument("--state", default=str(BASE / "yuksek_getiri_v2_state.json"))
    parser.add_argument("--db", default=str(BASE / "patron2.db"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    data_dir = Path(args.data_dir).resolve()
    model_path = Path(args.model).resolve()
    state_path = Path(args.state).resolve()
    db_path = Path(args.db).resolve()

    model = load_model(model_path)
    candidates, metadata = score_latest(data_dir, model, args.top)
    if candidates.empty:
        raise DataUnavailable("V2 uygun aday havuzu üretilemedi; günlük veri eksik veya güncel değil.")
    target_date = str(metadata["target_date"])
    require_data_date(target_date, timing="morning", enabled=not args.test and not args.dry_run)
    state = _load_state(state_path)
    if skip_previous_report():
        state = None

    if not args.test and not args.dry_run and state and state.get("as_of") == target_date:
        try:
            stage_candidates(
                db_path,
                "v2",
                target_date,
                _state_candidate_rows(state),
                eligible_pool=state.get("eligible"),
                model_version=str(state.get("model_version", "")),
                published=True,
                published_at=state.get("sent_at"),
            )
            mark_run_published(db_path, "v2", target_date, len(state.get("list") or []))
        except Exception as exc:
            print("Patron DB V2 sicil onarımı başarısız:", exc)
            return 1
        print(f"{target_date} kapanışı için V2 listesi daha önce gönderilmiş; tekrar gönderim yapılmadı.")
        return 0

    previous, evaluation_date = _evaluate_previous(state, data_dir)
    report = _previous_message(previous, evaluation_date)
    today_message = _today_message(candidates, metadata, model)

    if not args.test and not args.dry_run:
        try:
            if state and state.get("as_of") and state.get("list"):
                stage_candidates(
                    db_path,
                    "v2",
                    str(state["as_of"]),
                    _state_candidate_rows(state, previous),
                    eligible_pool=state.get("eligible"),
                    model_version=str(state.get("model_version", "")),
                    published=True,
                    published_at=state.get("sent_at"),
                )
                settle_results(
                    db_path,
                    "v2",
                    str(state["as_of"]),
                    evaluation_date,
                    _result_rows(previous),
                )
            stage_candidates(
                db_path,
                "v2",
                target_date,
                _candidate_rows(candidates),
                eligible_pool=int(metadata.get("eligible", 0)),
                model_version=str(model.get("model_version", "")),
            )
        except Exception as exc:
            print("Patron DB V2 kaydı başarısız; izsiz ilan yapılmadı:", exc)
            return 1

    if report:
        print(report, "\n")
    else:
        print("(Değerlendirilebilir önceki V2 listesi yok; karne atlandı.)")
    print(today_message)

    if args.dry_run:
        print("\nKuru çalışma tamamlandı; Telegram ve günlük kayıt dosyası değiştirilmedi.")
        return 0

    if args.send_at:
        _wait_for_delivery_time(args.send_at)

    targets = (ADMIN_ID,) if args.test else BROADCAST
    if report:
        _send_to_targets(targets, report, "karne gönderimi")
    today_sent = _send_to_targets(targets, today_message, "V2 gönderimi")

    if not args.test and today_sent:
        _save_state(state_path, target_date, candidates, metadata, model)
        try:
            mark_run_published(db_path, "v2", target_date, len(candidates))
        except Exception as exc:
            print("V2 listesi gönderildi fakat Patron DB yayın işareti yazılamadı:", exc)
            return 1
        print("V2 günlük kayıt dosyası güncellendi:", state_path)
    elif not args.test:
        print("V2 listesi tüm hedeflere ulaşmadığı için günlük kayıt dosyası değiştirilmedi.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(guarded_main(
        engine="v2_yuksek_getiri",
        label="GELİŞMİŞ YÜKSEK GETİRİ MOTORU V2",
        main_func=main,
        targets=BROADCAST,
        base=BASE,
        live=not any(flag in sys.argv for flag in ("--test", "--dry-run")),
    ))
