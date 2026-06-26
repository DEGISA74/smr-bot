#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeekTweet — hafta sonu ansiklopedik borsa thread'i TASLAĞI üretir ve
admin'in Telegram DM'ine gönderir (yarı-otomatik: kullanıcı kopyalayıp atar).

Akış:
  • Cumartesi → Smart Money müfredatı (sıralı, SM-01 → SM-74)
  • Pazar     → çeşitlilik (Teknik Analiz / Psikoloji / BIST'e Özel dönüşümlü)
  • Türkiye'ye özel (kural/sayı içeren) konular SADECE doğrulanmış "factsheet"
    doluysa gönderilir; boşsa atlanır (yanlış kural riskine karşı).

start_date'ten önce hiçbir şey göndermez. Aynı gün iki kez göndermez.
CLI: --test (kapıları yok say, anında 1 örnek taslak gönder, state'e dokunma)
     --dry  (üret + logla, GÖNDERME, state'e dokunma)
"""

import os
import sys
import json
import logging
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
import google.generativeai as genai

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("weektweet")

TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
genai.configure(api_key=GEMINI_KEY)
MODEL = genai.GenerativeModel("gemini-flash-latest")

TOPICS_PATH = BASE / "topics.json"

TEST = "--test" in sys.argv
DRY = "--dry" in sys.argv

CAT_LABEL = {
    "smart_money": "Smart Money",
    "teknik_analiz": "Teknik Analiz",
    "psikoloji": "Piyasa Psikolojisi",
    "bist_ozel": "BIST'e Özel",
}


# ---------- I/O ----------
def load_data():
    with open(TOPICS_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    tmp = TOPICS_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TOPICS_PATH)


# ---------- Telegram (admin DM, düz metin) ----------
def tg_send(chat_id, text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=25,
        )
        if r.status_code != 200:
            log.warning(f"Telegram HTTP {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.warning(f"Telegram fail: {e}")
        return False


# ---------- Konu seçimi ----------
def _sendable(t):
    """Türkiye-fişi gerektiren ama fişi boş olan konular gönderilemez."""
    if t.get("needs_factsheet") and not t.get("factsheet"):
        return False
    return True


def pick_from_pool(pool, start_idx):
    """start_idx'ten itibaren (sarmal) ilk gönderilebilir konu. (topic, next_idx) ya da (None, start_idx)."""
    n = len(pool)
    for off in range(n):
        i = (start_idx + off) % n
        if _sendable(pool[i]):
            return pool[i], (i + 1) % n
    return None, start_idx


def choose_topic(data, weekday):
    """weekday: 5=Cmt → smart_money sıralı; 6=Paz → çeşitlilik dönüşümlü."""
    pools = data["pools"]
    state = data["state"]

    if weekday == 5:
        topic, nxt = pick_from_pool(pools["smart_money"], state["smart_money_idx"])
        if topic:
            state["smart_money_idx"] = nxt
            return topic, "smart_money"

    # Pazar (ya da Cmt fallback) → dönüşümlü çeşitlilik
    rot = data["sunday_rotation"]
    rstart = state["sunday_rotation_idx"]
    for roff in range(len(rot)):
        cat = rot[(rstart + roff) % len(rot)]
        topic, nxt = pick_from_pool(pools[cat], state[f"{cat}_idx"])
        if topic:
            state[f"{cat}_idx"] = nxt
            state["sunday_rotation_idx"] = (rstart + roff + 1) % len(rot)
            return topic, cat

    # Son çare → smart_money evergreen
    topic, nxt = pick_from_pool(pools["smart_money"], state["smart_money_idx"])
    if topic:
        state["smart_money_idx"] = nxt
        return topic, "smart_money"
    return None, None


# ---------- Gemini ----------
def build_prompt(topic):
    fs = topic.get("factsheet")
    fs_block, rule_fs = "", ""
    if fs:
        fs_txt = "\n".join(f"- {x}" for x in fs) if isinstance(fs, list) else str(fs)
        fs_block = (
            "\n\nDOĞRULANMIŞ BİLGİ FİŞİ (Türkiye'ye özel — SADECE bunları kullan):\n" + fs_txt
        )
        rule_fs = (
            "Türkiye'ye özel HER sayı/kural/oran SADECE yukarıdaki fişten gelsin; "
            "fişte olmayan hiçbir rakam, oran ya da tarih ekleme. "
        )
    return f"""Türkçe finansal eğitim içeriği üreten, sade ve DOĞRU anlatan bir borsa eğitmenisin.
Aşağıdaki konuda X (Twitter) için 3 ya da 4 tweet'lik bir THREAD yaz.

KONU: {topic['title']}{fs_block}

KURALLAR:
- 3 ya da 4 tweet. Her tweet kendi başına anlamlı, en fazla ~270 karakter.
- Tweet'leri tam olarak şu satırla ayır: ──────────
- Her tweet'in başına sıra numarası koy: 1/4, 2/4 ...
- İlk tweet merak uyandıran bir kanca (soru ya da çarpıcı cümle) ile açsın; son tweet net bir çıkarımla bitsin.
- Ansiklopedik ama sıkıcı değil — günlük, akıcı Türkçe. En fazla 1 benzetme.
- Emoji ölçülü: tweet başına 0 ya da 1.
- UYDURMA YASAĞI: emin olmadığın spesifik tarih/istatistik/isim/rakam verme; emin değilsen kavramsal konuş. {rule_fs}
- Bu eğitim içeriğidir, yatırım tavsiyesi DEĞİL. "şunu al/sat" deme.
- Hashtag kullanma (gerekirse en fazla 1 tane).
- SADECE thread metnini döndür; başka açıklama, başlık ya da not ekleme."""


def generate_thread(topic):
    resp = MODEL.generate_content(
        build_prompt(topic),
        generation_config={"temperature": 0.85},
    )
    return (resp.text or "").strip()


def format_draft(topic, cat, body, is_test=False):
    tag = " (TEST)" if is_test else ""
    return (
        f"📝 HAFTA SONU TASLAK{tag} — kopyala & at\n"
        f"[{topic['id']}] · {CAT_LABEL.get(cat, cat)}\n"
        f"Konu: {topic['title']}\n"
        f"────────────────────\n"
        f"{body}\n"
        f"────────────────────\n"
        f"ℹ️ Sana özel taslak; sen atmadıkça kimse görmez."
    )


# ---------- Main ----------
def main():
    data = load_data()
    tz = ZoneInfo(data.get("tz", "Europe/Istanbul"))
    now = datetime.now(tz)
    today = now.date()
    admin = str(data["admin_chat_id"])

    if not TEST:
        if now.weekday() not in (5, 6):
            log.info(f"Hafta içi ({now:%A}) — gönderim yok.")
            return
        start = date.fromisoformat(data["start_date"])
        if today < start:
            log.info(f"start_date ({start}) öncesi — gönderim yok (bugün {today}).")
            return
        if data["state"].get("last_sent_date") == today.isoformat():
            log.info(f"Bugün ({today}) zaten gönderildi — atlanıyor.")
            return

    weekday = 5 if TEST else now.weekday()  # test → Smart Money örneği
    topic, cat = choose_topic(data, weekday)
    if not topic:
        log.warning("Gönderilebilir konu bulunamadı (hepsi fiş bekliyor?).")
        return
    log.info(f"Seçilen konu: [{topic['id']}] {topic['title']} ({cat})")

    try:
        body = generate_thread(topic)
    except Exception as e:
        log.error(f"Gemini üretim hatası: {e}")
        tg_send(admin, f"⚠️ WeekTweet: '{topic['id']}' için içerik üretilemedi: {e}")
        return
    if not body:
        log.error("Boş içerik döndü.")
        return

    msg = format_draft(topic, cat, body, is_test=TEST)

    if DRY:
        log.info("DRY-RUN — gönderilmedi:\n" + msg)
        return

    ok = tg_send(admin, msg)
    if not ok:
        log.error("Telegram gönderimi başarısız — state güncellenmedi.")
        return
    log.info("Taslak admin DM'ine gönderildi.")

    if not TEST:
        data["state"]["last_sent_date"] = today.isoformat()
        save_data(data)
        log.info("State güncellendi.")


if __name__ == "__main__":
    main()
