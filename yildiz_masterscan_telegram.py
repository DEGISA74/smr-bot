#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Yıldız Pazar Master Scan (Maviye Yolculuk/Potansiyel Kalkışlar/Mavide Güçlenen)
günlük PRO göndericisi.

Yalnızca 3 BOĞA grubunu gönderir (ayı grupları bu üründe kullanılmıyor — bkz.
✅ SHORT FİZİBİLİTESİ kararı). Her grup, Güçlü Akış filtresinden (52H≥55 +
CMF20≥-8 — backtest_yildiz_masterscan.py, 24 Eyl 2026) geçenleri, likiditeye
göre (yüksekten düşüğe) sıralı gösterir. G (günlük) — H (haftalık) şimdilik yok.

V2/V3/V4 ile aynı mimari: Streamlit/app.py'ye bağımlı değil, kendi başına
XYLDZ evrenini okur ve tarar. Varsayılan hedef ADMIN (güvenlik); PRO'ya
gitmesi için --broadcast şart.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from tarama_sureklilik import DataUnavailable, guarded_main, require_data_date, skip_previous_report

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import yildiz_masterscan_core as ym
from sentiment_chart_core import calculate_sentiment_chart

BASE = Path(__file__).resolve().parent
ADMIN_ID = "1034525990"
PRO_CHAT = "-1003976047533"  # SMR Pro (telegram_config.json → channels.pro.chat_id, 24 Eyl doğrulandı)
ISTANBUL = ZoneInfo("Europe/Istanbul")

BULL_SIGNALS = ("mavi_yolculuk", "potansiyel_kalkis", "mavide_guclenen")


def _token() -> str | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token.strip()
    for source in ("/home/wm11tr/weektweet/.env", "/home/wm11tr/insider/.env"):
        try:
            for line in Path(source).read_text(encoding="utf-8").splitlines():
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    return line.split("=", 1)[1].strip()
        except Exception:
            pass
    try:
        config = json.loads((BASE / "telegram_config.json").read_text(encoding="utf-8"))
        value = config.get("bot_token")
        return str(value).strip() if value else None
    except Exception:
        return None


def _send(chat_id: str, message: str) -> bool:
    message = message.replace(" — ", ", ").replace(" – ", ", ").replace("—", "-").replace("–", "-")
    token = _token()
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


def _load_state(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_state(path: Path, as_of: str, sent_counts: dict[str, int]) -> None:
    payload = {"as_of": as_of, "sent_at": datetime.now(ISTANBUL).isoformat(), "counts": sent_counts}
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp, path)
    except Exception:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def _xyldz_members(data_dir: Path) -> list[str]:
    from data_layer import load_index_components
    members = load_index_components("XYLDZ", allow_network=True)
    if members:
        return members
    cache = data_dir / ".index_components.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8")).get("XYLDZ", {}).get("members", [])
    return []


def _build_message(payload: dict, today: str) -> tuple[str, dict[str, int]]:
    lines = [f"⭐ YILDIZ PAZAR MASTER SCAN — {today} SONUÇLARI", ""]
    counts: dict[str, int] = {}
    for signal in BULL_SIGNALS:
        label = ym.SIGNALS[signal]["label"]
        sub = ym.filter_signal(payload, "daily", signal, filters=["guclu_akis"])
        counts[signal] = len(sub)
        lines.append(f"🔹 {label} ({len(sub)})")
        lines.append(f"  {ym.SIGNALS[signal]['description']}")
        if sub.empty:
            lines.append("  (bugün aday yok)")
        else:
            for _, row in sub.iterrows():
                lines.append(f"  {row['Symbol']:<6} {row['Price']:.2f} ({row['Price_Change_Pct']:+.1f}%) · 52H%{row['Pos52']:.0f}")
        lines.append("")
    lines.append("ℹ️ İşlem sinyali değildir; alım-satım ve risk yönetimi size aittir. Yatırım tavsiyesi değildir.")
    return "\n".join(lines), counts


# 1 Eki 2026 — SİTE ÖZETİ (smartmoneyradar.app "Güçlü Akış" kutusu, site fazı adım 4).
# Mesajla AYNI filtrelenmiş liste. Siteye SADECE grup sayıları + listenin 1. hissesi gider
# (bulanık gerçek isim sayfa kaynağından okunabilirdi → diğerleri dosyaya hiç yazılmaz).
# Tam liste herkese açık OLMAYAN geçmiş kaydına (logs/) — "dünün karnesi" için.
# Hata Telegram gönderimini ASLA engellemez (çağıran try/except).
SITE_OUT = os.environ.get("SMR_SITE_YILDIZ_OUT", "/var/www/smr/frontend/yildiz_ozet.json")
SITE_GECMIS = BASE / "logs" / "yildiz_site_gecmis.jsonl"


def _site_export(payload: dict, as_of: str) -> None:
    gruplar, ilk, tam = [], None, {}
    for signal in BULL_SIGNALS:
        sub = ym.filter_signal(payload, "daily", signal, filters=["guclu_akis"])
        gruplar.append({"kod": signal, "label": ym.SIGNALS[signal]["label"],
                        "aciklama": ym.SIGNALS[signal]["description"], "adet": int(len(sub))})
        rows = [{"t": str(r["Symbol"]), "fiyat": round(float(r["Price"]), 2),
                 "degisim": round(float(r["Price_Change_Pct"]), 2), "pos52": round(float(r["Pos52"]), 1)}
                for _, r in sub.iterrows()]
        tam[signal] = rows
        if ilk is None and rows:
            # 3 Eki 2026: sitede ad maskeli (MPARK → M****); tam ad yalnız özel geçmiş kaydında
            ilk = dict(rows[0], grup=ym.SIGNALS[signal]["label"], t=str(rows[0]["t"])[:1] + "****")
    now = datetime.now(ISTANBUL).strftime("%Y-%m-%d %H:%M")
    ozet = {"as_of": as_of, "uretim": now, "evren": "Yıldız Pazar", "gruplar": gruplar,
            "toplam": sum(g["adet"] for g in gruplar), "ilk": ilk}
    tmp = SITE_OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ozet, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, SITE_OUT)
    SITE_GECMIS.parent.mkdir(parents=True, exist_ok=True)
    with open(SITE_GECMIS, "a", encoding="utf-8") as f:
        f.write(json.dumps({"as_of": as_of, "uretim": now, "liste": tam}, ensure_ascii=False) + "\n")


def _wait_for(clock: str) -> None:
    hour, minute = (int(v) for v in clock.split(":", 1))
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


def _wait_for_master_scan() -> datetime:
    """Kapanış taraması bitip bir dakika geçene kadar bekle; 22:30'u aşma."""
    completion = BASE / "logs" / "kapanis_master_scan_completion.json"
    now = datetime.now(ISTANBUL)
    cutoff = now.replace(hour=22, minute=30, second=0, microsecond=0)
    last_error = None
    while True:
        now = datetime.now(ISTANBUL)
        try:
            record = json.loads(completion.read_text(encoding="utf-8"))
            if (record.get("status") == "completed"
                    and str(record.get("day")) == now.date().isoformat()):
                completed = datetime.fromisoformat(record["completed_at"])
                if completed.tzinfo is None:
                    completed = completed.replace(tzinfo=ISTANBUL)
                target = completed.astimezone(ISTANBUL) + timedelta(minutes=1)
                if target >= cutoff:
                    raise RuntimeError("Master Scan + 1 dakika 22:30 sınırını aşıyor; Yıldız gönderimi yapılmadı.")
                remaining = (target - now).total_seconds()
                while remaining > 0:
                    time.sleep(min(10, remaining))
                    now = datetime.now(ISTANBUL)
                    remaining = (target - now).total_seconds()
                return target
        except FileNotFoundError:
            record = None
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as e:
            if str(e) != last_error:
                print(f"Master Scan tamamlanma kaydı bekleniyor: {e}")
                last_error = str(e)
        if now >= cutoff:
            raise RuntimeError("Master Scan 22:30'a kadar tamamlanmadı; Yıldız gönderimi yapılmadı.")
        time.sleep(5)


def _wait_until_fixed_send_time() -> None:
    """Hazır listeyi aynı gün 22:30'da gönder; dakika kaçtıysa geç gönderme."""
    now = datetime.now(ISTANBUL)
    target = now.replace(hour=22, minute=30, second=0, microsecond=0)
    if now >= target and (now.hour, now.minute) != (22, 30):
        raise RuntimeError("Yıldız listesi 22:30 gönderim saatine yetişmedi; geç gönderim yapılmadı.")
    while now < target:
        time.sleep(min(15, (target - now).total_seconds()))
        now = datetime.now(ISTANBUL)
    if (now.hour, now.minute) != (22, 30):
        raise RuntimeError("Yıldız listesi 22:30 gönderim saatine yetişmedi; geç gönderim yapılmadı.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Yıldız Pazar Master Scan — PRO göndericisi")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--broadcast", action="store_true", help="PRO kanalına yayınlar (varsayılan: sadece admin)")
    parser.add_argument("--send-at", default=None, help="Örn 20:15 — o saate kadar bekler")
    parser.add_argument("--after-master-scan", action="store_true",
                        help="Master Scan bitişinden 1 dakika sonra tarar; hazır listeyi 22:30'da gönderir")
    parser.add_argument("--data-dir", default=str(BASE / "veriler"))
    parser.add_argument("--state", default=str(BASE / "yildiz_masterscan_state.json"))
    args = parser.parse_args()
    send_not_before = _wait_for_master_scan() if args.after_master_scan else None

    data_dir = Path(args.data_dir).resolve()
    state_path = Path(args.state).resolve()
    state = _load_state(state_path)
    if skip_previous_report():
        state = None

    members = _xyldz_members(data_dir)
    if not members:
        raise DataUnavailable("XYLDZ bileşen listesi boş; Yıldız Pazar Master Scan durduruldu.")

    payload = ym.scan_yildiz_master(members, calculate_sentiment_chart, str(data_dir))
    if payload.get("status") in ("unavailable", "no_data"):
        raise DataUnavailable(f"Yıldız Pazar tarama sonucu yok: {payload.get('message')}")

    as_of = str(payload.get("as_of") or "")[:10]
    if not as_of:
        raise DataUnavailable("Yıldız Pazar tarama sonucunda tarih bilgisi yok.")
    require_data_date(as_of, timing="same_day", enabled=not args.dry_run)

    if state and state.get("as_of") == as_of and not args.dry_run:
        already_sent_at = None
        try:
            already_sent_at = datetime.fromisoformat(state.get("sent_at", ""))
            if already_sent_at.tzinfo is None:
                already_sent_at = already_sent_at.replace(tzinfo=ISTANBUL)
        except (TypeError, ValueError) as e:
            print("Önceki Yıldız gönderim saati okunamadı; eşzamanlı gönderim yeniden denenecek:", e)
        if send_not_before is None or (already_sent_at and already_sent_at >= send_not_before):
            print(f"{as_of} için Yıldız Pazar mesajı zaten gönderilmiş; tekrar gönderim yapılmadı.")
            return 0

    today_label = datetime.now(ISTANBUL).strftime("%d.%m.%Y")
    message, counts = _build_message(payload, today_label)
    print(message)
    print("\nGrup sayıları:", counts)

    if args.dry_run:
        print("\nKuru çalışma: Telegram'a gönderilmedi, kayıt dosyası değişmedi.")
        return 0

    if args.send_at:
        _wait_for(args.send_at)

    if args.after_master_scan and not args.dry_run:
        _wait_until_fixed_send_time()

    target = PRO_CHAT if args.broadcast else ADMIN_ID
    if not _send(target, message):
        return 1
    _save_state(state_path, as_of, counts)
    if args.broadcast:
        try:
            _site_export(payload, as_of)
        except Exception as e:
            print(f"[site özeti] yazılamadı (Telegram etkilenmedi): {e}")
    print(f"Gönderildi → {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(guarded_main(
        engine="yildiz_masterscan",
        label="YILDIZ PAZAR MASTER SCAN",
        main_func=main,
        targets=(PRO_CHAT,) if "--broadcast" in sys.argv else (ADMIN_ID,),
        base=BASE,
        live="--dry-run" not in sys.argv,
        announce_previous=False,
    ))
