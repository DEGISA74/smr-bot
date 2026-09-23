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
    text = text.replace(" — ", ", ").replace(" – ", ", ").replace("—", "-").replace("–", "-")  # AI em-dash temizle
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
        f"{h['soru'].strip()}"
    )


def format_bitti(seri_baslik):
    return (
        f"📭 {seri_baslik} — HAVUZ BİTTİ\n"
        f"────────────────────\n"
        f"Sıradaki hikaye kalmadı. Yeni hikayeler eklersen (hikaye_havuzu.json) "
        f"seri kaldığı yerden devam eder.\n"
        f"(Döngü kapalı olduğu için başa sarmadım.)"
    )


# ---------- Secim: DOKUMA (tuzak haftalik garanti + her 3. gonderi enflasyon) ----------
# 20 Eyl 2026: duz sirali -> temaya dokunmus secim. UC serit:
#   1) TUZAK (Tema 5: sinyal/kurs/finfluencer) — her ISO-hafta HAFTANIN ILK
#      gonderisinde 1 GARANTI, SIRALI (modulosuz), 10'u BITENE KADAR; sonra serit
#      kendiliginden emekli olur. "one cekme" + "her hafta kesinlikle 1" (kullanici
#      20 Eyl). tuzak_hafta = son tuzak cikan ISO-hafta anahtari; tuzak_idx imlec.
#   2) ENFLASYON — tuzak-disi gonderilerin her 3.'u (weave_sayac % 3). Tuzak
#      weave_sayac'a DOKUNMAZ; enfl cadence bozulmaz (tuzak haftasinda ~4 gonderi
#      kalir, enfl yine >=1/hafta). genel/enfl modulo ile basa sarar (dongu).
#   3) GENEL — geri kalan.
# gun_no = toplam gonderi (sonraki_index); kapanis imzasi rotasyonu buna baglidir.
def _listeler(hikayeler):
    tuzak = [h for h in hikayeler if (h.get("tema") or "genel") == "tuzak"]
    enfl = [h for h in hikayeler if (h.get("tema") or "genel") == "enflasyon"]
    genel = [h for h in hikayeler
             if (h.get("tema") or "genel") not in ("tuzak", "enflasyon")]
    return genel, enfl, tuzak


def _hafta_key(now):
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def _migrate(st, genel, enfl, tuzak):
    """Eski state'ten dokuma imleclerine tek seferlik gecis (gonderilen_id'ye
    gore: her seritte kac tanesi zaten gitmis). tuzak_hafta="" -> ilk uygun
    gonderi bu hafta hemen bir tuzak cikarir (one cekme)."""
    sent = set(st.get("gonderilen_id", []))
    if "genel_idx" not in st or "enfl_idx" not in st:
        st["genel_idx"] = sum(1 for h in genel if h["id"] in sent)
        st["enfl_idx"] = sum(1 for h in enfl if h["id"] in sent)
        st.setdefault("weave_sayac", 0)
    if "tuzak_idx" not in st:
        st["tuzak_idx"] = sum(1 for h in tuzak if h["id"] in sent)
        st["tuzak_hafta"] = ""


def sec_hikaye(hikayeler, st, now):
    """(hikaye, tema, gun_no) doner; st imleclerini ilerletir (KAYDETMEZ)."""
    genel, enfl, tuzak = _listeler(hikayeler)
    _migrate(st, genel, enfl, tuzak)
    gun_no = int(st.get("sonraki_index", 0)) + 1

    # 1) TUZAK — haftada 1 GARANTI, haftanin ilk gonderisinde, bitene kadar (SIRALI)
    hafta = _hafta_key(now)
    if int(st.get("tuzak_idx", 0)) < len(tuzak) and st.get("tuzak_hafta") != hafta:
        h = tuzak[int(st["tuzak_idx"])]
        st["tuzak_idx"] = int(st["tuzak_idx"]) + 1
        st["tuzak_hafta"] = hafta
        return h, "tuzak", gun_no

    # 2) ENFLASYON dokumasi — tuzak-disi gonderilerin her 3.'u
    sayac = int(st.get("weave_sayac", 0)) + 1
    if (sayac % 3 == 0) and enfl:
        h = enfl[int(st["enfl_idx"]) % len(enfl)]
        st["enfl_idx"] = int(st["enfl_idx"]) + 1
        st["weave_sayac"] = sayac
        return h, "enflasyon", gun_no

    # 3) GENEL
    if genel:
        h = genel[int(st["genel_idx"]) % len(genel)]
        st["genel_idx"] = int(st["genel_idx"]) + 1
        st["weave_sayac"] = sayac
        return h, "genel", gun_no

    return None, None, gun_no


# ---------- Main ----------
def main():
    cfg = load_state()
    hikayeler = load_havuz(cfg)
    if not hikayeler:
        log.error("Havuz bos — gonderilecek hikaye yok.")
        return

    st = cfg["state"]
    tz = ZoneInfo(cfg.get("tz", "Europe/Istanbul"))
    now = datetime.now(tz)

    # PEEK: siradakini goster, state'e DOKUNMA (kopya uzerinde hesapla)
    if PEEK:
        st_kopya = json.loads(json.dumps(st))
        h, tema, gun_no = sec_hikaye(hikayeler, st_kopya, now)
        if h is None:
            print("Secilecek hikaye yok.")
            return
        print(f"[tema={tema} · gun={gun_no}]")
        print(format_draft(h, gun_no, seri_baslik=cfg.get("seri_baslik", SERI_BASLIK_DEFAULT)))
        return

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

    # TEST/DRY state'i degistirmez (kopya); gercek gonderim st'yi ilerletir.
    calisma_st = json.loads(json.dumps(st)) if (TEST or DRY) else st
    h, tema, gun_no = sec_hikaye(hikayeler, calisma_st, now)
    if h is None:
        log.info("Secilecek hikaye yok.")
        return

    msg = format_draft(h, gun_no, is_test=TEST,
                       seri_baslik=cfg.get("seri_baslik", SERI_BASLIK_DEFAULT))
    log.info(f"Secilen: gun {gun_no} — [{h['id']}] tema={tema}")

    if DRY:
        log.info("DRY-RUN — gonderilmedi:\n" + msg)
        return

    admin = str(cfg["admin_chat_id"])
    if not tg_send(admin, msg):
        log.error("Telegram gonderimi basarisiz — state guncellenmedi.")
        return
    log.info("Taslak admin DM'ine gonderildi.")

    if not TEST:
        # sec_hikaye st imleclerini zaten ilerletti; kalan sayaclari yaz.
        st["sonraki_index"] = int(st.get("sonraki_index", 0)) + 1
        st.setdefault("gonderilen_id", []).append(h["id"])
        st["gonderilen_id"] = st["gonderilen_id"][-200:]
        st["last_sent_ts"] = now.isoformat()
        save_state(cfg)
        log.info(f"State: gun={st['sonraki_index']} genel_idx={st['genel_idx']} "
                 f"enfl_idx={st['enfl_idx']} tuzak_idx={st.get('tuzak_idx')} "
                 f"weave={st['weave_sayac']}")


if __name__ == "__main__":
    main()
