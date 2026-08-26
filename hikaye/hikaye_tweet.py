#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hikaye Tweet — etkilesim HIKAYESI TASLAGI secer ve admin'in Telegram DM'ine
gonderir (yari-otomatik: kullanici duzenleyip X'e/kanala atar).

Kaynak: hikaye_havuzu.json (elle yazilmis hikayeler; bot URETMEZ, sadece SECER).
Her hikaye: bir kaynak kisi + guncel rakamlar + ufak kurgu + soruyla biten kapanis.

Secim mantigi: SIRALI (aforizmadaki agirlikli-rastgeleden farkli).
  - state.sonraki_index'teki hikaye gonderilir, index +1.
  - Havuz bitince: dongu=true ise basa sarar (tur +1), degilse admin'e
    "havuz bitti" uyarisi atip durur.
  - Havuz sirasi 'sira' alanina gore; en gucluden basli.

Guvenlik:
  - Cift-tetik korumasi: son gonderimden < min_gap_hours ise atlar.
  - start_date oncesi gonderim yok.
  - Atomik state yazimi (.tmp + os.replace).

CLI:
  --test  : kapilari yok say, siradaki hikayeyi aninda gonder, state'e DOKUNMA
  --dry   : sec + logla, GONDERME, state'e DOKUNMA (token gerekmez)
  --peek  : siradaki hikayeyi ekrana bas (gondermez, state'e dokunmaz)
"""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).parent

# Windows konsolu (cp1254) emoji basamaz -> stdout'u UTF-8'e sabitle (VPS'te zaten UTF-8)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# .env varsa yukle (lokalde olmayabilir; --dry/--peek token istemez)
try:
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
    load_dotenv(BASE.parent / ".env")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("hikaye")

STATE_PATH = BASE / "hikaye_state.json"

TEST = "--test" in sys.argv
DRY = "--dry" in sys.argv
PEEK = "--peek" in sys.argv


# ---------- I/O ----------
def load_state():
    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_state(cfg):
    tmp = STATE_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def load_havuz(cfg):
    p = Path(cfg.get("havuz_path", "hikaye_havuzu.json"))
    if not p.is_absolute():
        p = (BASE / p).resolve()
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    hikayeler = data.get("hikayeler", [])
    # 'sira' alanina gore kararli sirala (yoksa dosya sirasi)
    hikayeler.sort(key=lambda h: h.get("sira", 10**9))
    return hikayeler


# ---------- Telegram (admin DM, duz metin) ----------
def tg_send(chat_id, text):
    import requests
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        log.error("TELEGRAM_BOT_TOKEN yok — gonderilemedi.")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
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


# ---------- Taslak bicimi ----------
SERI_BASLIK_DEFAULT = "Küçük Yatırımcı Notları - Feridun Abi"

# ☕️ kapanis imzasi — gune gore doner (arka arkaya iki gun ayni gelmez).
KAPANIS_HAVUZ = [
    "☕️ Sizce dostlar?",
    "☕️ Siz ne dersiniz?",
    "☕️ Bir çay demleyip düşünelim:",
    "☕️ Peki ya siz?",
    "☕️ Sizin cevabınız ne?",
]


def format_draft(h, gun_no, is_test=False, seri_baslik=SERI_BASLIK_DEFAULT):
    tag = " (TEST)" if is_test else ""
    kapanis = KAPANIS_HAVUZ[gun_no % len(KAPANIS_HAVUZ)]
    # Postlanacak govde: baslik -> hikaye -> imza -> soru.
    # (Ust '📨/📖' basligi ve alt 'ℹ️' notu SADECE admin taslaginin iskeleti; postlanmaz.)
    return (
        f"📨 HİKAYE TASLAK{tag} — düzenle & at\n"
        f"📖 Gün {gun_no} · [{h['id']}]\n"
        f"────────────────────\n"
        f"{seri_baslik}\n\n"
        f"{h['metin'].strip()}\n\n"
        f"{kapanis}\n"
        f"{h['soru'].strip()}\n"
        f"────────────────────\n"
        f"ℹ️ Sana özel taslak; sen atmadıkça kimse görmez. İsim/rakam/emoji sana ait."
    )


def format_bitti(seri_baslik):
    return (
        f"📭 {seri_baslik} — HAVUZ BİTTİ\n"
        f"────────────────────\n"
        f"Sıradaki hikaye kalmadı. Yeni hikayeler eklersen (hikaye_havuzu.json) "
        f"seri kaldığı yerden devam eder.\n"
        f"(Döngü kapalı olduğu için başa sarmadım.)"
    )


# ---------- Main ----------
def main():
    cfg = load_state()
    hikayeler = load_havuz(cfg)
    if not hikayeler:
        log.error("Havuz bos — gonderilecek hikaye yok.")
        return

    st = cfg["state"]
    n = len(hikayeler)
    idx = int(st.get("sonraki_index", 0))

    # PEEK: siradakini ekrana bas, cik
    if PEEK:
        if idx >= n:
            print("Havuz sonunda (index >= havuz boyu).")
            return
        print(format_draft(hikayeler[idx], idx + 1,
                           seri_baslik=cfg.get("seri_baslik", SERI_BASLIK_DEFAULT)))
        return

    tz = ZoneInfo(cfg.get("tz", "Europe/Istanbul"))
    now = datetime.now(tz)

    if not TEST and not DRY:
        # baslangic kapisi
        sd = cfg.get("start_date")
        if sd and now.date().isoformat() < sd:
            log.info(f"start_date ({sd}) oncesi — gonderim yok.")
            return
        # cift-tetik korumasi
        gap_h = cfg.get("min_gap_hours", 20)
        last = st.get("last_sent_ts")
        if last:
            try:
                delta = (now - datetime.fromisoformat(last)).total_seconds() / 3600
                if delta < gap_h:
                    log.info(f"Son gonderim {delta:.1f} saat once (< {gap_h}h) — atlaniyor.")
                    return
            except ValueError:
                pass

    # Havuz sonu kontrolu
    if idx >= n:
        if cfg.get("dongu", True):
            idx = 0
            st["tur"] = int(st.get("tur", 1)) + 1
            log.info(f"Havuz basa sardi — tur {st['tur']}.")
        else:
            log.info("Havuz bitti (dongu kapali).")
            if not DRY and not TEST:
                tg_send(str(cfg["admin_chat_id"]),
                        format_bitti(cfg.get("seri_baslik", "Para Hikayeleri")))
                st["last_sent_ts"] = now.isoformat()
                save_state(cfg)
            return

    h = hikayeler[idx]
    gun_no = idx + 1
    msg = format_draft(h, gun_no, is_test=TEST,
                       seri_baslik=cfg.get("seri_baslik", SERI_BASLIK_DEFAULT))
    log.info(f"Secilen: gun {gun_no}/{n} — [{h['id']}] ({h.get('kaynak','')})")

    if DRY:
        log.info("DRY-RUN — gonderilmedi:\n" + msg)
        return

    admin = str(cfg["admin_chat_id"])
    if not tg_send(admin, msg):
        log.error("Telegram gonderimi basarisiz — state guncellenmedi.")
        return
    log.info("Taslak admin DM'ine gonderildi.")

    if not TEST:
        st["sonraki_index"] = idx + 1
        st.setdefault("gonderilen_id", []).append(h["id"])
        st["gonderilen_id"] = st["gonderilen_id"][-200:]
        st["last_sent_ts"] = now.isoformat()
        save_state(cfg)
        log.info(f"State guncellendi — sonraki_index={st['sonraki_index']}.")


if __name__ == "__main__":
    main()
