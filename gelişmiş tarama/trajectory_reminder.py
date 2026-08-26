# -*- coding: utf-8 -*-
"""
trajectory_reminder.py — AKŞAM MASTER SCAN HATIRLATMASI (forward collection güvenlik ağı)

Neden: Forward trajectory validasyonu, kullanıcının HER işlem akşamı Master Scan'i
elle çalıştırmasına bağlı. Bir akşam unutulursa o günün T+1/T+2/T+3 fotoğrafı KALICI
kaybolur. Bu script, o güne ait Master Scan görünmüyorsa Telegram'dan admin'i dürter.

Çalışma:
  * İşlem günü değilse (bist_calendar) sessizce çıkar.
  * patron.db'deki son Master Scan tarihi == bugün ise: iş tamam, sessizce çıkar.
  * Aksi halde (bugün taranmamış) ve akşam saatiyse: tek satır Telegram hatırlatması.
  * Aynı akşam en fazla MAX kez dürtme (state dosyası ile).

Teslimat: signals.db kuyruğuna DEĞİL, doğrudan Telegram Bot API ile (bot süreci
VPS'te olabilir; bu hatırlatma yerel makinede çalışır, bota bağımlı olmamalı).

Salt-okur: patron.db yalnız okunur; yalnız kendi state dosyasını yazar.
  python trajectory_reminder.py            # gerçek (saat/koşul uygunsa gönderir)
  python trajectory_reminder.py --dry-run  # gönderme, ne yapacağını yaz
  python trajectory_reminder.py --force    # saat kapısını atla (test)
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

try:  # Windows konsolu emoji/utf-8 basabilsin
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "patron.db"
CFG_PATH = ROOT / "telegram_config.json"
STATE = Path(__file__).resolve().parent / "trajectory_reminder_state.json"
ADMIN_ID = 1034525990
EVENING_HOUR = 19          # bu saatten önce (yerel) gönderme (--force ile atlanır)
MAX_PER_EVENING = 2        # aynı akşam en çok kaç dürtme


def istanbul_now() -> datetime:
    try:
        import pytz
        return datetime.now(pytz.timezone("Europe/Istanbul"))
    except Exception:
        return datetime.now()


def is_trading_day(d) -> bool:
    try:
        sys.path.insert(0, str(ROOT))
        from bist_calendar import is_trading_day as _itd
        return bool(_itd(d))
    except Exception:
        return d.weekday() < 5


def last_master_scan() -> str | None:
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        row = con.execute("SELECT MAX(substr(scan_date,1,10)) FROM scan_signals").fetchone()
        con.close()
        return row[0] if row else None
    except Exception:
        return None


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(s: dict) -> None:
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def send_telegram(text: str) -> tuple[bool, str]:
    try:
        cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        token = cfg["bot_token"]
    except Exception as e:
        return False, f"config/token okunamadı: {e}"
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": ADMIN_ID, "text": text},
            timeout=20,
        )
        return (r.status_code == 200), f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"gönderim hatası: {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="saat kapısını atla")
    args = ap.parse_args()

    now = istanbul_now()
    today = now.strftime("%Y-%m-%d")

    if not is_trading_day(now.date()):
        print(json.dumps({"action": "skip", "reason": "kapalı_gün", "date": today}, ensure_ascii=False))
        return 0

    last = last_master_scan()
    if last == today:
        # iş tamam — state temizle, sessiz çık
        st = load_state()
        if st.get("date") == today and st.get("done"):
            pass
        save_state({"date": today, "done": True, "count": st.get("count", 0)})
        print(json.dumps({"action": "ok", "reason": "bugün_tarandı", "last_scan": last}, ensure_ascii=False))
        return 0

    if not args.force and now.hour < EVENING_HOUR:
        print(json.dumps({"action": "wait", "reason": "akşam_değil", "hour": now.hour}, ensure_ascii=False))
        return 0

    st = load_state()
    count = st.get("count", 0) if st.get("date") == today else 0
    if count >= MAX_PER_EVENING:
        print(json.dumps({"action": "skip", "reason": "günlük_limit", "count": count}, ensure_ascii=False))
        return 0

    nudge = "İlk hatırlatma" if count == 0 else "TEKRAR — hâlâ görünmüyor"
    text = (
        f"⏳ PATRON TRAJECTORY — Master Scan hatırlatması ({today})\n"
        f"{nudge}.\n\n"
        f"Bugün işlem günü ve forward toplama için Master Scan GEREKLİ.\n"
        f"Son görülen Master Scan: {last or 'yok'} (bugün değil).\n\n"
        f"Akşam günlük veri tazelenince Master Scan'i çalıştır → collector otomatik "
        f"snapshot alır. Kaçarsa bugünkü kohort takibi (T+1/T+2/T+3) KALICI kaybolur."
    )

    if args.dry_run:
        print(json.dumps({"action": "would_send", "count_will_be": count + 1,
                          "last_scan": last, "text_preview": text[:80]}, ensure_ascii=False))
        return 0

    ok, info = send_telegram(text)
    if ok:
        save_state({"date": today, "done": False, "count": count + 1,
                    "last_sent_at": now.isoformat(timespec="seconds")})
    print(json.dumps({"action": "sent" if ok else "fail", "info": info,
                      "count": count + 1 if ok else count}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
