"""
SMR Telegram Bot — smr_core.py üzerinden doğrudan analiz yapar.
Playwright / Streamlit bağımlılığı yok.
İçerik: mplfinance grafik + ICT Bottom Line + AI Görev 3 (Teknik Kart)
"""

import logging
import json
import os
import signal
import atexit
import asyncio
import hashlib
from datetime import datetime
from collections import defaultdict

from aiohttp import web

from google import genai
from google.genai import types as genai_types

from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from telegram.error import Conflict, NetworkError, TimedOut

from smr_tickers import resolve_ticker
import smr_core

# ─── CONFIG ──────────────────────────────────────────────────────────────────
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
CFG_PATH  = os.path.join(BASE_DIR, "telegram_config.json")

with open(CFG_PATH, "r", encoding="utf-8") as f:
    CFG = json.load(f)

BOT_TOKEN      = CFG["bot_token"]
GEMINI_API_KEY = CFG.get("gemini_api_key", "")

FREE_ID   = int(CFG["channels"]["free"]["chat_id"])
PRO_ID    = int(CFG["channels"]["pro"]["chat_id"])
ELITE_ID  = int(CFG["channels"]["elite"]["chat_id"])
CHAT_ID   = int(CFG["chat"]["chat_id"])

DAILY_LIMITS = {FREE_ID: 1, PRO_ID: 3, ELITE_ID: 10}

# Shopier API
SHOPIER_API_KEY = CFG.get("shopier_api_key", "")
ADMIN_ID        = 1034525990  # Bildirimler buraya gidecek

# Global bot referansı
_bot_app = None

# Son kontrol edilen sipariş ID'si (tekrar bildirim önlenir)
_last_order_id: int = 0
TIER_NAME    = {FREE_ID: "FREE", PRO_ID: "PRO", ELITE_ID: "ELITE"}

# Limitsiz kullanıcılar (admin/sahip) — user_id VE username ile tanınır
UNLIMITED_USERS    = {1034525990}           # SAVAŞ — Telegram user_id
UNLIMITED_USERNAMES = {"SmartMoneyRadar26"} # SAVAŞ — Telegram @username (@ olmadan)

# ─── KALICI KULLANIM SAYACI (bot restart'ta sıfırlanmaz) ─────────────────────
USAGE_FILE = os.path.join(BASE_DIR, "usage_tracker.json")

def _load_usage() -> dict:
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_usage(data: dict):
    try:
        with open(USAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass

def _usage_get(chat_id: int, user_id: int, today: str) -> int:
    data = _load_usage()
    return data.get(f"{chat_id}:{user_id}:{today}", 0)

def _usage_inc(chat_id: int, user_id: int, today: str):
    data = _load_usage()
    key  = f"{chat_id}:{user_id}:{today}"
    data[key] = data.get(key, 0) + 1
    # Eski günleri temizle (7 günden eski)
    cutoff = (datetime.now().date().toordinal() - 7)
    data = {k: v for k, v in data.items()
            if datetime.strptime(k.split(":")[-1], "%Y-%m-%d").date().toordinal() >= cutoff}
    _save_usage(data)

usage_tracker: dict = defaultdict(int)  # geriye dönük uyumluluk için bırakıldı

# Eşzamanlı analiz sınırı (yfinance CPU-bound olduğu için)
ANALYSIS_SEMAPHORE = asyncio.Semaphore(2)

# Gemini yapılandırma
if GEMINI_API_KEY and GEMINI_API_KEY != "BURAYA_GEMINI_API_KEY_YAZ":
    _genai_client = genai.Client(api_key=GEMINI_API_KEY)
    GEMINI_OK = True
else:
    _genai_client = None
    GEMINI_OK = False

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ─── TEK INSTANCE KONTROLÜ (PID lock) ────────────────────────────────────────
PID_FILE = os.path.join(BASE_DIR, "smr_bot.pid")

def _ensure_single_instance():
    """
    Başka bir bot process'i çalışıyorsa önce SIGTERM, sonra SIGKILL gönderir.
    Böylece 409 Conflict (iki process aynı anda getUpdates) önlenir.
    """
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            if old_pid != os.getpid():
                log.info(f"Eski bot process bulundu (PID={old_pid}) — sonlandırılıyor...")
                try:
                    os.kill(old_pid, signal.SIGTERM)
                    import time; time.sleep(3)
                except ProcessLookupError:
                    pass  # Zaten ölmüş
                try:
                    os.kill(old_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except (ValueError, IOError, OSError):
            pass

    # Kendi PID'ini yaz
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    log.info(f"PID dosyası yazıldı: {os.getpid()}")

    # Çıkışta temizle
    atexit.register(lambda: os.remove(PID_FILE) if os.path.exists(PID_FILE) else None)


# ─── GEMİNİ: AI GÖREV 3 ─────────────────────────────────────────────────────
async def call_gemini_gorev3(gorev3_prompt: str, ticker: str) -> str:
    """
    Görev prompt'unu Gemini'ye gönder.
    429 kota hatası gelirse 60 sn bekleyip 3 kez tekrar dener.
    Hata veya API key yoksa boş string döner.
    """
    if not GEMINI_OK or _genai_client is None:
        log.warning("Gemini API key ayarlanmamış — AI atlanıyor")
        return ""

    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _genai_client.models.generate_content(
                    model="gemini-2.5-flash-lite",
                    contents=gorev3_prompt,
                    config=genai_types.GenerateContentConfig(max_output_tokens=3000)
                )
            )
            text = response.text.strip() if response.text else ""
            log.info(f"AI üretildi: {len(text)} karakter (deneme {attempt})")
            return text

        except Exception as e:
            err_str = str(e)
            # 429 = kota aşımı → bekle ve tekrar dene
            if "429" in err_str or "quota" in err_str.lower() or "ResourceExhausted" in err_str:
                wait_sec = 60 * attempt  # 1. → 60sn, 2. → 120sn, 3. → 180sn
                log.warning(f"Gemini kota aşımı (deneme {attempt}/{max_retries}) — {wait_sec}sn bekleniyor")
                await asyncio.sleep(wait_sec)
            else:
                # Başka hata — retry etme
                log.error(f"Gemini API hatası: {e}")
                return ""

    log.error("Gemini: max retry aşıldı — AI atlanıyor")
    return ""


# ─── CORE ANALİZ (smr_core.py üzerinden) ────────────────────────────────────
async def get_analysis(ticker: str, tier: str = "free") -> tuple:
    """
    Returns: (img_bytes, ict_bottom_line, ai_text)

    Playwright yok — tüm analiz smr_core.py üzerinden doğrudan yapılır:
      1. get_data       → yfinance DataFrame
      2. calculate_ict_analysis → ICT dict (bottom_line dahil)
      3. generate_chart → mplfinance PNG
      4. get_stock_info → fiyat/değişim
      5. build_ai_prompt + Gemini → AI analiz (pro/elite)

    ANALYSIS_SEMAPHORE ile max 2 eşzamanlı analiz çalışır.
    """
    async with ANALYSIS_SEMAPHORE:
        log.info(f"Analiz başlatılıyor [{ticker}] — tier={tier}")
        loop = asyncio.get_event_loop()

        try:
            # Tek seferlik fetch + analiz + grafik — yfinance yalnızca 1 kez çağrılır
            # 60 sn timeout: yfinance askıda kalırsa bot çökmez
            df, ict, info, img_bytes = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: smr_core.fetch_and_analyze(ticker)),
                timeout=60
            )

            if df is None or len(df) < 60:
                log.warning(f"[{ticker}] Yetersiz veri — analiz iptal")
                return None, "", ""

            # Teknik Özet — FREE / PRO / ELITE için aynı kısa kart
            ict_text = await loop.run_in_executor(
                None, lambda: smr_core.build_teknik_ozet(ticker, df, ict)
            )

            # AI analiz: PRO / ELITE
            # Kritik: Gemini timeout/hata verirse kısa kartı bozma; fallback metni göster.
            ai_text = ""

            ai_fallback_text = (
                "🔬 Kapsamlı AI analiz modülü şu an yoğunluk nedeniyle yetişemedi. "
                "Ama kısa teknik kart hazır: trend, destek/direnç ve risk sinyalleri yukarıda. "
                "Biraz sonra tekrar denersen derin uzman yorumu da gelsin. 🚀"
            )

            if tier in ("pro", "elite") and GEMINI_OK:
                try:
                    if tier == "elite":
                        prompt = smr_core.build_ai_prompt_gorev1(ticker, ict, info, df)
                    else:
                        prompt = smr_core.build_ai_prompt(ticker, ict, info, df)

                    if prompt:
                        log.info(f"[{ticker}] Gemini'ye gönderiliyor tier={tier} ({len(prompt)} kr)")
                        ai_text = await asyncio.wait_for(
                            call_gemini_gorev3(prompt, ticker), timeout=120
                        )

                except asyncio.TimeoutError:
                    log.error(f"[{ticker}] Gemini timeout — fallback AI metni gönderilecek")
                    ai_text = ai_fallback_text
                except Exception as e:
                    log.error(f"[{ticker}] Gemini/AI hatası — fallback AI metni gönderilecek: {e}", exc_info=True)
                    ai_text = ai_fallback_text

            log.info(
                f"[{ticker}] Analiz tamamlandı — "
                f"ICT={'OK' if ict_text else 'Yok'} | AI={len(ai_text)} kr"
            )
            # Sinyali kaydet (Faza 1)
            if ict.get("status") == "OK":
                smr_core.log_scan_signal(ticker, "bot_request", ict)

            return img_bytes if img_bytes else None, ict_text, ai_text

        except asyncio.TimeoutError:
            log.error(f"[{ticker}] Analiz timeout (60sn) — yfinance yanıt vermedi")
            return None, "", ""
        except Exception as e:
            log.error(f"get_analysis hatası [{ticker}]: {e}", exc_info=True)
            return None, "", ""


# ─── TEKNİK KART: TELEGRAM FORMATLAMA ────────────────────────────────────────
def format_ai_message(ticker: str, raw_text: str, tier: str = "pro") -> list[str]:
    """
    Gemini'den gelen AI metnini Telegram için hazırla.
    Max 4096 karakter olan Telegram mesaj limiti aşılmasın diye böler.
    """
    if not raw_text:
        return []

    if tier == "elite":
        header = f"🔬 *SMR ELİTE UZMAN ANALİZ — #{ticker}*\n{'━'*20}\n"
        intro  = f"#{ticker} için oluşturduğum derin analiz şöyle:\n\n"
    else:
        header = f"🤖 *AI TEKNİK KART — #{ticker}*\n{'━'*20}\n"
        intro  = ""

    # Gemini'nin preamble satırlarını sil (Harika bir veri / ICT metodolojisi... gibi)
    lines = raw_text.splitlines()
    # İlk boş olmayan satır gerçek içerikle başlamıyorsa atla
    skip_keywords = ("harika", "mükemmel", "kesinlikle", "tabii", "elbette",
                     "ict metodolojisi", "aşağıdadır", "aşağıda sunuyorum",
                     "algoritmik", "veri seti")
    while lines and any(kw in lines[0].lower() for kw in skip_keywords):
        lines.pop(0)
    # Başta kalan boş satırları da atla
    while lines and not lines[0].strip():
        lines.pop(0)

    # Çift boş satırları tek yap
    cleaned_lines = []
    prev_empty = False
    for line in lines:
        is_empty = not line.strip()
        if is_empty and prev_empty:
            continue
        cleaned_lines.append(line)
        prev_empty = is_empty
    cleaned = "\n".join(cleaned_lines).strip()

    # Telegram 4096 karakter limiti — gerekirse parçala
    MAX_LEN = 3800
    parts = []
    full_text = header + intro + cleaned

    if len(full_text) <= MAX_LEN:
        parts.append(full_text)
    else:
        # İlk parça header ile
        parts.append(header + cleaned[:MAX_LEN - len(header)])
        remaining = cleaned[MAX_LEN - len(header):]

        # Kalan parçalar
        while remaining:
            chunk = remaining[:MAX_LEN]
            parts.append(chunk)
            remaining = remaining[MAX_LEN:]

    return parts


# ─── TELEGRAM HANDLER ────────────────────────────────────────────────────────
async def handle_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.channel_post
    if not msg:
        return

    chat_id = msg.chat_id
    text    = (msg.text or "").strip()

    if chat_id not in DAILY_LIMITS:
        return
    if not text.startswith("#"):
        return

    raw_ticker = text.lstrip("#").strip().split()[0].upper()
    if not raw_ticker or len(raw_ticker) < 2 or len(raw_ticker) > 12:
        return

    # ── Ticker doğrulama ────────────────────────────────────────────────────
    app_ticker, suggestions = resolve_ticker(raw_ticker)
    if app_ticker is None:
        if suggestions:
            sugg_str = " · ".join(f"#{s}" for s in suggestions)
            await msg.reply_text(
                f"❓ *#{raw_ticker}* listede yok.\n\n"
                f"Demek istediğin bunlardan biri mi?\n{sugg_str}",
                parse_mode="Markdown"
            )
        else:
            await msg.reply_text(
                f"❌ *#{raw_ticker}* tanımadım ve benzer bir hisse de bulamadım.\n"
                f"Ticker'ı kontrol et (örnek: #KCHOL, #AAPL, #BTC)",
                parse_mode="Markdown"
            )
        return
    # Çözümlenen ticker ile devam et (örn. "BTC" → "BTC-USD")
    raw_ticker = app_ticker
    # ────────────────────────────────────────────────────────────────────────

    # Kanal postlarında from_user her zaman None gelir
    if msg.from_user:
        user_id = msg.from_user.id
        username = msg.from_user.username or ""
    elif update.channel_post:
        # Kanala sadece admin yazabilir → limitsiz say
        user_id  = msg.chat_id
        username = ""
    else:
        return  # Grup içinde anonim admin — yanıtlama

    tier     = TIER_NAME[chat_id]
    tier_key = tier.lower()  # "free", "pro", "elite"

    # ── Limit kontrolü ──────────────────────────────────────────────────────
    is_unlimited = (
        update.channel_post  # kanal postu = sadece admin yazabilir
        or user_id in UNLIMITED_USERS
        or (username and username.lower() in {u.lower() for u in UNLIMITED_USERNAMES})
    )
    if not is_unlimited:
        today = datetime.now().date().isoformat()
        limit = DAILY_LIMITS[chat_id]
        used  = _usage_get(chat_id, user_id, today)
        if used >= limit:
            upgrade = "\n\n💎 Günde 3 sorgu için PRO üyelik: [yakında]" if chat_id == FREE_ID else ""
            await msg.reply_text(
                f"⚠️ Günlük limitine ulaştın ({limit}/{limit}).\nYarın tekrar dene.{upgrade}"
            )
            return
        _usage_inc(chat_id, user_id, today)
    # ─────────────────────────────────────────────────────────────────────────

    log.info(f"[{tier}] #{raw_ticker} — user={user_id} unlimited={is_unlimited}")

    # Bekleme mesajı + "fotoğraf gönderiliyor..." indikatörü
    if tier_key == "elite" and GEMINI_OK:
        wait_text = f"#{raw_ticker} için kapsamlı analiz hazırlanıyor 🔍"
    elif tier_key == "pro" and GEMINI_OK:
        wait_text = f"#{raw_ticker} analizi hazırlanıyor ✨"
    else:
        wait_text = f"#{raw_ticker} analizi hazırlanıyor..."
    wait_msg = await msg.reply_text(wait_text)

    # Arka planda her 4 sn'de "upload_photo" action gönder (kullanıcı "fotoğraf gönderiliyor..." görür)
    _stop_action = asyncio.Event()
    async def _keep_typing():
        while not _stop_action.is_set():
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action="upload_photo")
            except Exception:
                pass
            await asyncio.sleep(4)
    _typing_task = asyncio.create_task(_keep_typing())

    try:
        img_bytes, ict_text, teknik_kart = await get_analysis(raw_ticker, tier=tier_key)
    finally:
        _stop_action.set()
        _typing_task.cancel()

    if img_bytes is None:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=wait_msg.message_id,
            text=f"❌ #{raw_ticker} analizi alınamadı. Ticker doğru mu? (Örn: #KCHOL, #AAPL, #BTC)"
        )
        return

    # ICT Bottomline blok
    ict_block = ""
    if ict_text:
        ict_block = f"\n\n{ict_text[:900]}"

    # Caption
    if chat_id == FREE_ID:
        caption = (
            f"📊 *#{raw_ticker}* — Smart Money Radar Kısa Analizi\n"
            f"━━━━━━━━━━━━━━━━━━━"
            f"{ict_block}\n\n"
            f"⚠️ _Eğitim amaçlıdır, yatırım tavsiyesi değildir._\n"
            f"_— PRO üyeler bu hisse dahil 3 hissenin Detaylı Teknik Kartını da okudu. Daha fazla sorgu ve daha fazla analiz. Sen de ister misin?_"
        )
    elif chat_id == PRO_ID:
        caption = (
            f"📊 *#{raw_ticker}* — Smart Money Radar Kısa Analizi\n"
            f"━━━━━━━━━━━━━━━━━━━"
            f"{ict_block}\n\n"
            f"⚠️ _Eğitim amaçlıdır, yatırım tavsiyesi değildir._\n"
            f"_— ELİTE üyeler bu hisse dahil 10 hisse için tam uzman analizini aldı. Farkı görmek ister misin?_"
        )
    else:
        caption = (
            f"📊 *#{raw_ticker}* — Smart Money Radar Kısa Analizi\n"
            f"━━━━━━━━━━━━━━━━━━━"
            f"{ict_block}\n\n"
            f"⚠️ _Eğitim amaçlıdır, yatırım tavsiyesi değildir._"
        )

    if len(caption) > 1020:
        caption = caption[:1020] + "…"

    await context.bot.delete_message(chat_id=chat_id, message_id=wait_msg.message_id)

    # Screenshot + ICT gönder
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=img_bytes,
        caption=caption,
        parse_mode="Markdown"
    )

    # PRO / ELİTE: AI analiz ayrı mesaj olarak gönder
    if tier_key in ("pro", "elite") and teknik_kart:
        ai_parts = format_ai_message(raw_ticker, teknik_kart, tier=tier_key)
        for part in ai_parts:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=part,
                    parse_mode="Markdown"
                )
            except Exception as e:
                log.warning(f"AI mesaj gönderim hatası (Markdown): {e}")
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=part.replace("*", "").replace("`", "").replace("_", "")
                    )
                except Exception as e2:
                    log.error(f"AI mesaj düz metin de başarısız: {e2}")

    elif tier_key in ("pro", "elite") and not teknik_kart and GEMINI_OK:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ AI analiz bu sefer üretilemedi.",
        )


# ─── KANAL TEMİZLEYİCİ ───────────────────────────────────────────────────────
async def delete_non_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Sinyal kanallarında # ile başlamayan mesajları sil.
    Admin ve bot mesajları dokunulmaz.
    """
    msg = update.message or update.channel_post
    if not msg:
        return

    chat_id = msg.chat_id
    if chat_id not in (FREE_ID, PRO_ID, ELITE_ID):
        return

    # Bot mesajlarına dokunma
    if msg.from_user and msg.from_user.is_bot:
        return

    # Admin mesajlarına dokunma
    uname = (msg.from_user.username or "") if msg.from_user else ""
    if msg.from_user and (msg.from_user.id in UNLIMITED_USERS or uname in UNLIMITED_USERNAMES):
        return

    text = (msg.text or "").strip()
    if text.startswith("#"):
        return  # Geçerli mesaj — handle_ticker işleyecek

    # # ile başlamıyor → sil + kısa uyarı
    try:
        await msg.delete()
        warn = await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Bu kanalda yalnızca hisse kodu yazabilirsiniz.\nÖrnek: #THYAO",
        )
        await asyncio.sleep(5)
        await warn.delete()
    except Exception as e:
        log.warning(f"Mesaj silinemedi (chat={chat_id}): {e}")


# ─── SOHBET GRUBU MODERASYONU ────────────────────────────────────────────────
CHAT_RULES = (
    "👋 *SMR Sohbet Grubuna Hoş Geldin!*\n\n"
    "📌 *Kurallar:*\n"
    "• Reklam ve tanıtım yasaktır\n"
    "• Hisse/kripto pump paylaşımı yasaktır\n"
    "• PRO/ELİTE kanal içeriğini buraya iletmek yasaktır\n"
    "• Hakaret ve küfür yasaktır\n"
    "• Analiz için: @SMR_Free_Kanal\n\n"
    "⚠️ _Eğitim amaçlıdır, yatırım tavsiyesi değildir._"
)

async def handle_chat_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sohbet grubu moderasyonu."""
    msg = update.message
    if not msg or msg.chat_id != CHAT_ID:
        return

    # Bot mesajlarına dokunma
    if msg.from_user and msg.from_user.is_bot:
        return

    # Admin mesajlarına dokunma
    uid   = msg.from_user.id if msg.from_user else 0
    uname = (msg.from_user.username or "") if msg.from_user else ""
    if _is_admin(uid, uname):
        return

    text = (msg.text or "").strip()

    # # ile başlayan mesaj → sil, FREE kanalına yönlendir
    if text.startswith("#"):
        try:
            await msg.delete()
            warn = await context.bot.send_message(
                chat_id=CHAT_ID,
                text="📊 Analiz için FREE kanalımıza gidin: @SMRFreeKanal\nBu grupta hisse kodu ile analiz yapılmaz.",
            )
            await asyncio.sleep(8)
            await warn.delete()
        except Exception as e:
            log.warning(f"Sohbet grubu silme hatası: {e}")
        return

    # PRO/ELITE kanalından forward → sil
    if msg.forward_from_chat and msg.forward_from_chat.id in (PRO_ID, ELITE_ID):
        try:
            await msg.delete()
            warn = await context.bot.send_message(
                chat_id=CHAT_ID,
                text="🚫 PRO/ELİTE kanal içeriğini buraya iletmek yasaktır.",
            )
            await asyncio.sleep(8)
            await warn.delete()
        except Exception as e:
            log.warning(f"Forward silme hatası: {e}")
        return

    # Reklam/link filtresi (t.me linkleri — kendi kanallar hariç)
    if msg.entities:
        for ent in msg.entities:
            if ent.type in ("url", "text_link"):
                url = text[ent.offset:ent.offset + ent.length] if ent.type == "url" else (ent.url or "")
                # Kendi bot/kanallar hariç dış t.me linkleri sil
                if "t.me" in url and "SMR" not in url.upper():
                    try:
                        await msg.delete()
                        warn = await context.bot.send_message(
                            chat_id=CHAT_ID,
                            text="🚫 Reklam ve dış grup linkleri bu grupta yasaktır.",
                        )
                        await asyncio.sleep(8)
                        await warn.delete()
                    except Exception as e:
                        log.warning(f"Link silme hatası: {e}")
                    return


async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gruba yeni katılan üyeye hoş geldin mesajı."""
    if not update.message or update.message.chat_id != CHAT_ID:
        return
    for member in (update.message.new_chat_members or []):
        if member.is_bot:
            continue
        name = member.first_name or member.username or "Yeni Üye"
        try:
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=f"👋 Hoş geldin *{name}*!\n\n{CHAT_RULES}",
                parse_mode="Markdown"
            )
        except Exception as e:
            log.warning(f"Hoş geldin mesajı gönderilemedi: {e}")


# ─── ADMİN KOMUTLARI ─────────────────────────────────────────────────────────
def _is_admin(user_id: int, username: str = "") -> bool:
    return user_id in UNLIMITED_USERS or username in UNLIMITED_USERNAMES

async def cmd_adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /adduser @ahmet PRO 30
    /adduser @ahmet ELITE 30 not:vip
    """
    msg = update.message or update.channel_post
    if not msg: return
    user_id  = msg.from_user.id if msg.from_user else 0
    username = msg.from_user.username or "" if msg.from_user else ""
    if not _is_admin(user_id, username):
        await msg.reply_text("⛔ Bu komut sadece admin içindir.")
        return

    log.info(f"[CMD] /adduser — user_id={user_id} args={context.args}")
    args = context.args  # ['@ahmet', 'PRO', '30'] veya ['@ahmet', 'ELITE', '30', 'not:vip']
    if not args or len(args) < 3:
        await msg.reply_text(
            "❌ Kullanım: /adduser @kullaniciadi TİER GÜN\n"
            "Örnek: /adduser @ahmet PRO 30\n"
            "Tier: FREE / PRO / ELITE"
        )
        return

    uname  = args[0].lstrip("@")
    tier   = args[1].upper()
    try:
        days = int(args[2])
    except ValueError:
        await msg.reply_text("❌ Gün sayısı rakam olmalı. Örnek: /adduser @ahmet PRO 30")
        return
    note = args[3] if len(args) > 3 else ""

    if tier not in ("FREE", "PRO", "ELITE"):
        await msg.reply_text("❌ Tier FREE, PRO veya ELITE olmalı.")
        return

    # Mevcut user_id yoksa 0 ile kaydet, username üzerinden tanınacak
    # Gerçek user_id ileride /start komutuyla eşleştirilecek
    existing = smr_core.sub_get_by_username(uname)
    uid = existing["user_id"] if existing else 0

    expiry = smr_core.sub_add(uid, uname, tier, days, note)
    await msg.reply_text(
        f"✅ *{uname}* eklendi!\n"
        f"Tier: *{tier}* | Süre: {days} gün | Bitiş: `{expiry}`",
        parse_mode="Markdown"
    )
    log.info(f"[ADMIN] adduser: @{uname} → {tier} ({days}g) bitiş:{expiry}")


async def cmd_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/removeuser @ahmet"""
    msg = update.message or update.channel_post
    if not msg: return
    user_id  = msg.from_user.id if msg.from_user else 0
    username = msg.from_user.username or "" if msg.from_user else ""
    if not _is_admin(user_id, username):
        await msg.reply_text("⛔ Bu komut sadece admin içindir.")
        return

    if not context.args:
        await msg.reply_text("❌ Kullanım: /removeuser @kullaniciadi")
        return

    uname = context.args[0].lstrip("@")
    ok = smr_core.sub_remove_by_username(uname)
    if ok:
        await msg.reply_text(f"✅ *@{uname}* abonelikten çıkarıldı.", parse_mode="Markdown")
        log.info(f"[ADMIN] removeuser: @{uname}")
    else:
        await msg.reply_text(f"❓ *@{uname}* kayıtlarda bulunamadı.", parse_mode="Markdown")


async def cmd_listusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/listusers — aktif ve süresi dolmuş aboneleri göster"""
    msg = update.message or update.channel_post
    if not msg: return
    user_id  = msg.from_user.id if msg.from_user else 0
    username = msg.from_user.username or "" if msg.from_user else ""
    log.info(f"[CMD] /listusers — user_id={user_id}")
    if not _is_admin(user_id, username):
        await msg.reply_text("⛔ Bu komut sadece admin içindir.")
        return

    active  = smr_core.sub_list_active()
    expired = smr_core.sub_list_expired()

    lines = ["📋 *AKTİF ABONELER*"]
    if active:
        for s in active:
            tier_icon = {"elite": "💎", "pro": "⭐", "free": "🆓"}.get(s["tier"], "•")
            lines.append(f"{tier_icon} @{s['username']} — {s['tier'].upper()} — {s['expiry_date']}")
    else:
        lines.append("_Henüz aktif abone yok._")

    if expired:
        lines.append("\n⏰ *SÜRESİ DOLANLAR*")
        for s in expired:
            lines.append(f"❌ @{s['username']} — {s['tier'].upper()} — {s['expiry_date']}")

    await msg.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/myid — kullanıcı ID'sini göster (debug için)"""
    msg = update.message
    if not msg or not msg.from_user:
        return
    uid  = msg.from_user.id
    uname = msg.from_user.username or "(yok)"
    await msg.reply_text(
        f"👤 *Telegram Bilgilerin*\n"
        f"User ID: `{uid}`\n"
        f"Username: @{uname}",
        parse_mode="Markdown"
    )
    log.info(f"[MYID] user_id={uid} username={uname!r}")


async def cmd_durum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/durum — kullanıcı kendi aboneliğini sorgular"""
    msg = update.message
    if not msg or not msg.from_user: return
    user_id = msg.from_user.id
    username = msg.from_user.username or ""

    # user_id ile ara, yoksa username ile ara
    rec = smr_core.sub_get(user_id)
    if not rec and username:
        rec = smr_core.sub_get_by_username(username)

    if not rec:
        await msg.reply_text(
            "❌ Aktif aboneliğin bulunamadı.\n"
            "Abone olmak için @SmartMoneyRadar26 ile iletişime geç."
        )
        return

    from datetime import date
    today  = date.today().isoformat()
    active = rec["expiry_date"] >= today
    kalan  = (date.fromisoformat(rec["expiry_date"]) - date.today()).days

    if active:
        tier_icon = {"elite": "💎", "pro": "⭐", "free": "🆓"}.get(rec["tier"], "•")
        await msg.reply_text(
            f"{tier_icon} *{rec['tier'].upper()} Abonesin*\n"
            f"Bitiş: `{rec['expiry_date']}` ({kalan} gün kaldı)",
            parse_mode="Markdown"
        )
    else:
        await msg.reply_text(
            f"⏰ Aboneliğin `{rec['expiry_date']}` tarihinde sona erdi.\n"
            f"Yenilemek için @SmartMoneyRadar26 ile iletişime geç.",
            parse_mode="Markdown"
        )


# ─── HATA HANDLER ────────────────────────────────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """
    409 Conflict ve geçici ağ hatalarını sessizce yutar.
    Diğer hatalar ERROR seviyesinde loglanır.
    """
    err = context.error
    if isinstance(err, Conflict):
        # 409 — genellikle eski process henüz ölmemişken gelir, zararsız
        log.debug(f"Telegram Conflict (409) — yoksayıldı: {err}")
        return
    if isinstance(err, (NetworkError, TimedOut)):
        log.warning(f"Geçici ağ hatası — yoksayıldı: {err}")
        return
    # Diğer hatalar
    log.error(f"Beklenmeyen hata: {err}", exc_info=context.error)


# ─── GÜNLÜK SIFIRLAMA (00:00) ────────────────────────────────────────────────
async def reset_daily_limits(context: ContextTypes.DEFAULT_TYPE):
    """Her gece 00:00'da kullanım sayaçlarını sıfırla."""
    count = len(usage_tracker)
    usage_tracker.clear()
    log.info(f"Günlük limitler sıfırlandı ({count} kayıt temizlendi)")


# ─── 19:00 GÜNLÜK BÜLTEN ─────────────────────────────────────────────────────
async def _send_bulletin_to_channel(
    context, chat_id: int, tier: str, now_str: str, is_sunday: bool = False
):
    """Verilen kanala XU100 bülteni gönder (tier: 'pro' veya 'elite').
    is_sunday=True ise başlık "Pazar Hatırlatması" olarak işaretlenir.
    """
    tier_label = tier.upper()
    log.info(f"[{tier_label}] Bülten hazırlanıyor (chat_id={chat_id})...")
    try:
        img_bytes, ict_text, ai_text = await get_analysis("XU100", tier=tier)
        log.info(
            f"[{tier_label}] Analiz: {'OK' if img_bytes else 'YOK'} | "
            f"ICT: {len(ict_text)} kr | AI: {len(ai_text)} kr"
        )

        ict_block = ""
        if ict_text:
            ict_block = f"\n\n{ict_text[:500]}"

        if is_sunday:
            header = f"📅 *SMR Pazar Hatırlatması — {now_str}*\n_↩️ Cuma analizinin tekrarı_"
        else:
            header = f"📅 *SMR Günlük Bülten — {now_str}*"

        caption = (
            f"{header}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📊 *XU100 Genel Görünüm*"
            f"{ict_block}\n\n"
            f"⚠️ _Eğitim amaçlıdır, yatırım tavsiyesi değildir._"
        )
        if len(caption) > 1020:
            caption = caption[:1020] + "…"

        if img_bytes:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=img_bytes,
                caption=caption,
                parse_mode="Markdown"
            )
            log.info(f"[{tier_label}] Bülten fotoğrafı gönderildi ✅")
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"📅 *SMR Günlük Bülten — {now_str}*\n⚠️ Görsel alınamadı.",
                parse_mode="Markdown"
            )
            log.warning(f"[{tier_label}] Görsel yok — text-only mesaj gönderildi")

        # AI analizi de gönder (PRO → Teknik Kart, ELITE → Uzman Analiz)
        if ai_text:
            parts = format_ai_message("XU100", ai_text, tier=tier)
            for part in parts:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=part,
                        parse_mode="Markdown"
                    )
                except Exception:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=part.replace("*", "").replace("`", "").replace("_", "")
                    )
            log.info(f"[{tier_label}] AI analizi gönderildi ({len(parts)} parça)")
        else:
            log.info(f"[{tier_label}] AI analizi yok — yalnızca görsel gönderildi")

    except Exception as e:
        log.error(f"[{tier_label}] Bülten gönderim HATASI: {e}", exc_info=True)


async def send_daily_bulletin(context: ContextTypes.DEFAULT_TYPE):
    """
    Gönderim takvimi:
      Pazartesi–Cuma (0-4) → günlük bülten
      Pazar (6)            → Cuma analizinin hatırlatması
      Cumartesi (5)        → atla
    """
    weekday = datetime.now().weekday()  # 0=Pzt … 5=Cmt, 6=Paz

    if weekday == 5:  # Cumartesi
        log.info("Günlük bülten atlandı — Cumartesi.")
        return

    is_sunday = weekday == 6
    now_str   = datetime.now().strftime("%d.%m.%Y")
    gun_adi   = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"][weekday]

    log.info(
        f"{'Pazar Hatırlatması' if is_sunday else 'Günlük Bülten'} "
        f"gönderiliyor ({gun_adi}) — PRO + ELITE..."
    )

    # PRO önce, ELITE ardından (sequential — semaphore çakışması olmaz)
    await _send_bulletin_to_channel(context, PRO_ID,   tier="pro",   now_str=now_str, is_sunday=is_sunday)
    await _send_bulletin_to_channel(context, ELITE_ID, tier="elite", now_str=now_str, is_sunday=is_sunday)


# ─── SHOPİER API — PERİYODİK SİPARİŞ KONTROLÜ ───────────────────────────────
async def check_shopier_orders(context=None):
    """
    Her 5 dakikada bir Shopier API'den yeni siparişleri çeker.
    Yeni sipariş varsa admin'e Telegram bildirimi gönderir.
    """
    global _last_order_id
    if not SHOPIER_API_KEY:
        return

    import aiohttp as _aiohttp

    headers = {
        "Authorization": f"Bearer {SHOPIER_API_KEY}",
        "Accept": "application/json",
    }

    try:
        async with _aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.shopier.com/v1/orders?sort=-id&per_page=5",
                headers=headers,
                timeout=_aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    log.warning(f"[Shopier] API yanıt: {resp.status}")
                    return
                data = await resp.json()

        orders = data.get("data", [])
        if not orders:
            return

        new_orders = []
        for order in orders:
            oid = int(order.get("id", 0))
            if oid > _last_order_id:
                new_orders.append(order)

        if not new_orders:
            return

        # En yüksek ID'yi kaydet
        _last_order_id = max(int(o.get("id", 0)) for o in new_orders)

        bot = _bot_app.bot if _bot_app else None
        if not bot:
            return

        for order in reversed(new_orders):
            oid      = order.get("id", "?")
            tutar    = order.get("total_price", "?")
            musteri  = f"{order.get('customer', {}).get('name', '')} {order.get('customer', {}).get('surname', '')}".strip()
            email    = order.get("customer", {}).get("email", "?")

            # Ürün adı
            items    = order.get("items", [])
            urun     = items[0].get("name", "?") if items else "?"

            # Telegram kullanıcı adı — özel alan
            custom   = order.get("custom_fields", {}) or {}
            tg_user  = (
                custom.get("Telegram Kullanıcı Adı")
                or custom.get("telegram_kullanici_adi")
                or custom.get("telegram")
                or "?"
            ).strip().lstrip("@")

            # Tier tespiti
            urun_up = urun.upper()
            if "ELITE" in urun_up or "ELİTE" in urun_up:
                tier, days = "ELITE", 30
            elif "PRO" in urun_up:
                tier, days = "PRO", 30
            else:
                tier, days = "?", 30

            if tg_user != "?" and tier != "?":
                cmd = f"`/adduser @{tg_user} {tier} {days} shopier`"
            else:
                cmd = "⚠️ Kullanıcı adı/tier belirlenemedi — manuel ekle"

            msg = (
                f"💰 *YENİ SİPARİŞ!*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📦 Ürün: {urun}\n"
                f"💎 Tier: {tier}\n"
                f"👤 Müşteri: {musteri}\n"
                f"📧 E-posta: {email}\n"
                f"📱 Telegram: @{tg_user}\n"
                f"💵 Tutar: {tutar}₺\n"
                f"🔖 Sipariş No: {oid}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Eklemek için:\n{cmd}"
            )
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=msg,
                parse_mode="Markdown"
            )
            log.info(f"[Shopier] Yeni sipariş bildirimi: #{oid} | {urun} | @{tg_user}")

    except Exception as e:
        log.error(f"[Shopier] API hatası: {e}", exc_info=True)


# ─── SHOPİER OSB WEBHOOK (eski — pasif) ──────────────────────────────────────
async def shopier_osb(request: web.Request) -> web.Response:
    """
    Shopier Otomatik Sipariş Bildirimi (OSB) endpoint'i.
    Shopier ödeme tamamlanınca bu URL'ye POST atar.
    Admin'e Telegram bildirimi gönderir.
    """
    try:
        data = await request.post()

        # Sipariş bilgileri
        siparis_id  = data.get("id", "?")
        urun        = data.get("urun_adi", "?")
        tutar       = data.get("toplam_tutar", "?")
        musteri     = data.get("musteri_adi_soyadi", "?")
        email       = data.get("musteri_email", "?")

        # Telegram kullanıcı adı — özel alan adı Shopier'da ne yazıldıysa
        tg_user = (
            data.get("Telegram Kullanıcı Adı")
            or data.get("telegram_kullanici_adi")
            or data.get("telegram")
            or "?"
        ).strip().lstrip("@")

        # Tier tespiti
        urun_upper = urun.upper()
        if "ELITE" in urun_upper or "ELİTE" in urun_upper:
            tier = "ELITE"
            days = 30
        elif "PRO" in urun_upper:
            tier = "PRO"
            days = 30
        else:
            tier = "?"
            days = 30

        if tg_user != "?" and tier != "?":
            cmd = f"`/adduser @{tg_user} {tier} {days} shopier`"
        else:
            cmd = "⚠️ Kullanıcı adı veya tier belirlenemedi — manuel ekle"

        msg = (
            f"💰 *YENİ SİPARİŞ!*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Ürün: {urun}\n"
            f"💎 Tier: {tier}\n"
            f"👤 Müşteri: {musteri}\n"
            f"📧 E-posta: {email}\n"
            f"📱 Telegram: @{tg_user}\n"
            f"💵 Tutar: {tutar}₺\n"
            f"🔖 Sipariş No: {siparis_id}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"Eklemek için kopyala:\n{cmd}"
        )

        if _bot_app:
            await _bot_app.bot.send_message(
                chat_id=ADMIN_ID,
                text=msg,
                parse_mode="Markdown"
            )
            log.info(f"[OSB] Sipariş bildirimi gönderildi — {urun} | @{tg_user}")
        else:
            log.error("[OSB] Bot henüz hazır değil — bildirim gönderilemedi")

    except Exception as e:
        log.error(f"[OSB] Hata: {e}", exc_info=True)

    # Shopier her zaman 200 bekler
    return web.Response(text="OK", status=200)


# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    import pytz
    from telegram.ext import JobQueue

    # Eski instance varsa öldür (409 Conflict'i önler)
    _ensure_single_instance()

    log.info("SMR Bot başlatılıyor...")
    if GEMINI_OK:
        log.info("✅ Gemini API hazır — AI Görev 3 aktif")
    else:
        log.warning("⚠️  Gemini API key YOK — telegram_config.json'a 'gemini_api_key' ekle")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(20)
        .read_timeout(20)
        .write_timeout(20)
        .get_updates_connect_timeout(10)
        .get_updates_read_timeout(10)
        .build()
    )

    global _bot_app
    _bot_app = app

    # Hata handler — 409 / ağ hatalarını sessizce yutar
    app.add_error_handler(error_handler)

    # Kanal temizleyici — # ile başlamayan mesajları sil (önce çalışmalı)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, delete_non_ticker), group=0)

    # Admin komutları
    app.add_handler(CommandHandler("adduser",    cmd_adduser))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser))
    app.add_handler(CommandHandler("listusers",  cmd_listusers))
    app.add_handler(CommandHandler("durum",      cmd_durum))
    app.add_handler(CommandHandler("myid",       cmd_myid))

    # Sohbet grubu moderasyonu
    app.add_handler(MessageHandler(filters.Chat(CHAT_ID) & filters.TEXT, handle_chat_group), group=0)
    app.add_handler(MessageHandler(filters.Chat(CHAT_ID) & filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))

    # handle_ticker group=1'de çalışmalı — group=0'daki delete_non_ticker'dan SONRA
    # PTB v20: her group sırayla işlenir; aynı grup içinde ilk eşleşen kazanır
    app.add_handler(
        MessageHandler(
            (filters.TEXT & ~filters.COMMAND) | filters.UpdateType.CHANNEL_POST,
            handle_ticker
        ),
        group=1
    )

    tz_istanbul = pytz.timezone("Europe/Istanbul")

    # 00:00 — günlük limit sıfırlama
    app.job_queue.run_daily(
        reset_daily_limits,
        time=datetime.strptime("00:00", "%H:%M").time().replace(tzinfo=tz_istanbul),
        name="reset_daily"
    )

    # 19:00 — PRO kanalına günlük bülten
    app.job_queue.run_daily(
        send_daily_bulletin,
        time=datetime.strptime("19:00", "%H:%M").time().replace(tzinfo=tz_istanbul),
        name="daily_bulletin"
    )

    # Her 5 dakikada Shopier sipariş kontrolü
    if SHOPIER_API_KEY:
        app.job_queue.run_repeating(
            check_shopier_orders,
            interval=300,  # 5 dakika
            first=30,      # Başlangıçtan 30sn sonra ilk kontrol
            name="shopier_check"
        )
        log.info("✅ Shopier sipariş kontrolü aktif (5dk)")

    log.info("✅ Bot aktif. Bülten: 19:00 | Sıfırlama: 00:00")

    # aiohttp — Shopier OSB endpoint
    web_app = web.Application()
    web_app.router.add_post("/shopier", shopier_osb)

    async def run_all():
        # Telegram bot başlat
        await app.initialize()
        await app.start()
        await app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            poll_interval=2.0,
            timeout=5,
        )
        # Web server başlat (port 8080)
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", 8080)
        await site.start()
        log.info("✅ Shopier OSB endpoint aktif: port 8080/shopier")

        # Sonsuza kadar çalış
        try:
            await asyncio.Event().wait()
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            await runner.cleanup()

    asyncio.run(run_all())


if __name__ == "__main__":
    main()