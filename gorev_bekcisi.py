#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GÖREV BEKÇİSİ — VPS içindeki zamanlanmış görevleri izler ve arıza olursa
admin'e Telegram uyarısı atar. Amaç: kullanıcı artık tek tek "görev geldi mi?"
diye bakmasın; sistem kendini izlesin.

TASARIM İLKESİ — SALT OKUR:
Her görevin KENDİ doğal başarı izini okur (state.json / dosya mtime). Görev
scriptlerine ASLA dokunmaz → onları bozma riski yok. Bekçinin kendi bug'ı bile
gerçek görevleri etkileyemez.

İKİ KAPI (bu bekçi tek başına yetmez):
  1) BU BEKÇİ  → VPS AYAKTA ama bir görev sessizce patladı/geç kaldı (misfire,
     SSL hatası, çökme). VPS içinden yakalar, Telegram'a yazar.
  2) GCP UPTIME CHECK (ayrı, Google altyapısında) → VPS KOMPLE ÖLÜ. Bekçi de
     ölür, o yüzden dışarıdan Google yoklar ve e-posta atar.

ÇALIŞMA: systemd timer ile her 15 dk. Her görev için "bugün deadline'a kadar
çalıştı mı?" bakar. Çalışmadıysa GÜNDE 1 KEZ uyarır (bekci_state.json).
Gün sonunda (20:00 TR sonrası) "✅ hepsi tamam" özeti atar → bu, BEKÇİNİN
KENDİSİNİN canlı olduğunun kanıtıdır (sessizlik = her şey yolunda DEĞİL, çünkü
bekçi de ölmüş olabilir; günlük yeşil tik bunu ayırt ettirir).
"""
import os
import pathlib
import sys
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

from bist_data_store import VOLUME_CONTROLLED_SOURCES, VOLUME_OFFICIAL_SOURCES

BASE = os.path.expanduser("~/smr")
HEALTH = os.path.join(BASE, "health")
os.makedirs(HEALTH, exist_ok=True)
STATE_FILE = os.path.join(HEALTH, "bekci_state.json")
CFG_PATH = os.path.join(BASE, "telegram_config.json")
ADMIN_ID = 1034525990                      # smr_bot ile aynı — uyarılar buraya
TR = timezone(timedelta(hours=3))          # Türkiye sabit UTC+3 (DST yok)


def _token():
    with open(CFG_PATH, encoding="utf-8") as f:
        return json.load(f)["bot_token"]


def tg(text):
    try:
        data = urllib.parse.urlencode({"chat_id": ADMIN_ID, "text": text}).encode()
        url = "https://api.telegram.org/bot%s/sendMessage" % _token()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=25).read()
        return True
    except Exception as e:
        print("[bekci] telegram gonderilemedi:", e)
        return False


# ─── başarı-izi okuyucular: görevin bugün EN SON başarılı olduğu epoch (yoksa None) ───
def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _aforizma_ts():
    """aforizma başarıda state.json'a last_sent_ts yazar (fail'de yazmaz)."""
    try:
        with open(os.path.expanduser("~/aforizma/aforizma_state.json"), encoding="utf-8") as f:
            st = json.load(f)
        ts = (st.get("state") or {}).get("last_sent_ts") or st.get("last_sent_ts")
        return datetime.fromisoformat(ts).timestamp() if ts else None
    except Exception:
        return None


now = datetime.now(TR)
today = now.date()


def slot(h, m):
    return datetime(today.year, today.month, today.day, h, m, tzinfo=TR)


HAFTAICI = {0, 1, 2, 3, 4}         # Pzt–Cuma
HERGUN = {0, 1, 2, 3, 4, 5, 6}

# ad, görünür_ad, gün_seti, beklenen_başlangıç(TR), deadline(TR), başarı-izi fn
TASKS = [
    ("tavan",       "🚀 Tavan/Yüksek Getiri listesi (sabah)", HAFTAICI, slot(9, 45),  slot(10, 20),
     lambda: _mtime(os.path.join(BASE, "tavan_state.json"))),
    ("yuksek_v3",   "🚀 Yüksek Getiri V3 (Elite 09:40)",       HAFTAICI, slot(9, 40),  slot(10, 15),
     lambda: _mtime(os.path.join(BASE, "yuksek_getiri_v3_state.json"))),
    ("aforizma_am", "💬 Aforizma öğle taslağı",                HERGUN,   slot(12, 45), slot(13, 20),
     _aforizma_ts),
    ("aforizma_pm", "💬 Aforizma akşam taslağı",               HERGUN,   slot(15, 45), slot(16, 20),
     _aforizma_ts),
    ("bulten",      "📰 PRO+ELITE akşam bülteni",              HAFTAICI, slot(19, 0),  slot(19, 40),
     lambda: _mtime(os.path.join(HEALTH, "bulten.done"))),
    ("firsat",      "📡 Fırsat Radarı",                        HAFTAICI, slot(19, 20), slot(19, 55),
     lambda: _mtime(os.path.join(BASE, "firsat_state.json"))),
]


# --- VERI KALITE KAPILARI (18 Agu 2026) --------------------------------------
# Neden: 18 Agu'da iki ariza SESSIZCE gecti - (1) kapanis turu borsa kapanmadan
# bitti, 192 hissenin "kapanisi" gun-ici fiyatta kaldi; (2) Is Yatirim hacim
# servisi butun gun %87-99 hata verdi, hacim Yahoo'nun bozuk BIST rakamina dustu.
# Ikisi de yalniz log'a yazildi, kimseye ulasmadi. Bu bolum onayli surumun
# manifestini SALT OKUR (fetcher'a dokunmaz; bekcinin bug'i veriyi bozamaz) ve
# esigin altindaysa gunde bir kez Telegram atar.
STORE_DIR = os.path.join(BASE, "health", "bist_store")
def _veri_ozeti():
    """Aktif manifestte güncel, resmî ve kontrollü hacim kapsamını döndürür."""
    try:
        with open(os.path.join(STORE_DIR, "active.json"), encoding="utf-8") as f:
            vid = json.load(f)["version_id"]
        with open(os.path.join(STORE_DIR, "manifests", vid + ".json"), encoding="utf-8") as f:
            syms = json.load(f).get("symbols", {})
    except Exception as e:
        print("[bekci] manifest okunamadi:", e)
        return None
    bugun = str(today)
    guncel = official = controlled = 0
    for meta in syms.values():
        if (meta.get("last") or "") != bugun:
            continue
        guncel += 1
        source = ((meta.get("field_sources") or {}).get("Volume") or "").lower()
        if source in VOLUME_OFFICIAL_SOURCES:
            official += 1
        elif source in VOLUME_CONTROLLED_SOURCES:
            controlled += 1
    return {
        "total": len(syms), "current": guncel,
        "official": official, "controlled": controlled,
        "usable": official + controlled,
    }


def _islem_gunu():
    """Takvim varsa ona sor; yoksa hafta ici say (tatilde yanlis alarm olmasin)."""
    try:
        from bist_calendar import is_trading_day
        return bool(is_trading_day(today))
    except Exception:
        return now.weekday() in HAFTAICI


# ad, gorunur_ad, deadline(TR), olcut, esik(oran), elle-cozum komutu
VERI_KAPILARI = [
    ("veri_fiyat", "\U0001F4B0 Kapanis fiyati (tum evren)", slot(19, 30), "guncel", 0.92,
     "venv/bin/python fetcher.py kapanis_final"),
    ("veri_hacim", "\U0001F4CA Hacim kapsamı (İş Yatırım + kontrollü borsapy)", slot(23, 15), "kesin", 0.80,
     "venv/bin/python finalize_volume.py"),
]


def veri_kapilarini_denetle(day):
    """(ozet_satirlari, yeni_ariza_metinleri)."""
    satirlar, arizalar = [], []
    if not _islem_gunu():
        return satirlar, arizalar
    ozet = _veri_ozeti()
    if ozet is None:
        return ["\u26A0\uFE0F Veri deposu okunamadi (manifest yok?)"], []
    toplam = ozet["total"]
    guncel = ozet["current"]
    usable = ozet["usable"]
    for ad, gad, deadline, olcut, esik, komut in VERI_KAPILARI:
        if olcut == "guncel":
            deger, payda = guncel, toplam
            detay = "%d/%d hissede bugunun bari var" % (guncel, toplam)
        else:
            deger, payda = usable, guncel
            detay = ("%d/%d hissede kullanılabilir hacim var (%d resmî + %d kontrollü borsapy yedeği)"
                     % (usable, guncel, ozet["official"], ozet["controlled"]))
        oran = (deger / payda) if payda else 0.0
        ok = oran >= esik
        satirlar.append("%s %s (%%%.0f)" % ("\u2705" if ok else "\U0001F534", gad, oran * 100))
        if now >= deadline and not ok and ad not in day["alerted"]:
            arizalar.append("%s -> %s (%%%.0f - esik %%%.0f)\nElle: cd ~/smr && %s"
                            % (gad, detay, oran * 100, esik * 100, komut))
            day["alerted"].append(ad)
    return satirlar, arizalar


# -- TARAMA KARNESI SAGLIGI (28 Agu 2026) --------------------------------
# Sebep: backtest_results.json VPS'te HIC yoktu; app.py ve smr_core.py'deki iki
# tuketici de sessizce bos kume dondu; PRO bulteni 11+ gun eksik gitti ve kimse
# fark etmedi. Karne canliya baglanmadan ONCE bu nobetci kuruluyor ki ayni sinif
# hata bir daha SESSIZCE yasayamasin. Karne yoksa/bayatsa/bozuksa alarm.
KARNE_DEADLINE_SAAT = (20, 30)      # aksam olcum zinciri bittikten sonra bakilir


def karne_saglik_denetle(day):
    """(ozet_satirlari, yeni_ariza_metinleri). Kapi basina gunde 1 uyarir."""
    try:
        if BASE not in sys.path:
            sys.path.insert(0, BASE)
        import tarama_karne as _tk
        rapor = _tk.saglik(pathlib.Path(BASE) / 'logs' / 'tarama_karne.json')
    except Exception as e:
        return ['⚠️ Tarama karnesi denetlenemedi (%s)' % type(e).__name__], []
    if rapor['saglikli']:
        return ['✅ 📋 Tarama karnesi (%d kayit)' % rapor['kayit']], []
    satir = ['🔴 📋 Tarama karnesi: %s' % rapor['sorun']]
    arizalar = []
    if now >= slot(*KARNE_DEADLINE_SAAT) and 'tarama_karne' not in day['alerted']:
        arizalar.append('📋 Tarama karnesi SAGLIKSIZ -> %s' % rapor['sorun']
                        + chr(10) + 'Web ve bot ayni fotografi okuyamaz.'
                        + chr(10) + 'Elle: cd ~/smr && venv/bin/python tarama_karne.py')
        day['alerted'].append('tarama_karne')
    return satir, arizalar

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(s):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[bekci] state yazilamadi:", e)


def main():
    dk = str(today)
    st = load_state()
    day = st.get(dk, {"alerted": [], "summary_sent": False})

    weekday = now.weekday()
    gunun_gorevleri = [t for t in TASKS if weekday in t[2]]

    durum = []   # (görünür_ad, ok_bool, uygulanabilir_bool)
    yeni_ariza = []

    for ad, gad, gunler, basla, deadline, iz_fn in gunun_gorevleri:
        iz = iz_fn()
        ok = iz is not None and iz >= basla.timestamp()
        deadline_gecti = now >= deadline
        durum.append((gad, ok, deadline_gecti))

        if deadline_gecti and not ok and ad not in day["alerted"]:
            yeni_ariza.append((ad, gad, deadline))
            day["alerted"].append(ad)

    veri_satirlari, veri_arizalari = veri_kapilarini_denetle(day)
    karne_satirlari, karne_arizalari = karne_saglik_denetle(day)
    veri_arizalari += karne_arizalari

    # ── VERİ ARIZASI uyarısı (kapı başına günde 1) ──
    if veri_arizalari:
        L = ["\U0001F534 VERİ BEKÇİSİ — BIST verisi eksik/şüpheli", ""]
        L += veri_arizalari
        L.append("")
        L.append("Log: tail -30 ~/smr/logs/fetcher.log")
        tg("\n".join(L))

    # ── ARIZA uyarısı (görev başına günde 1) ──
    if yeni_ariza:
        L = ["🔴 GÖREV BEKÇİSİ — UYARI", ""]
        for ad, gad, deadline in yeni_ariza:
            L.append("%s → %s'a kadar GELMEDİ (deadline geçti)." % (gad, deadline.strftime("%H:%M")))
        L.append("")
        L.append("VPS ayakta ama görev çalışmadı. Kontrol: sudo systemctl status smr-bot")
        L.append("Bülten için elle: admin sohbetinde /bulten")
        tg("\n".join(L))

    # ── GÜN SONU ÖZETİ (20:00 TR sonrası, günde 1) — bekçinin canlı olduğunun kanıtı ──
    son_deadline = max((t[4] for t in gunun_gorevleri), default=slot(20, 0))
    if now >= son_deadline and not day["summary_sent"]:
        tamam = [gad for gad, ok, dg in durum if ok]
        eksik = [gad for gad, ok, dg in durum if not ok]
        if not eksik:
            L = ["✅ GÖREV BEKÇİSİ — bugün tüm görevler tamam", ""]
            L += ["• " + g for g in tamam]
        else:
            L = ["⚠️ GÖREV BEKÇİSİ — gün özeti", "", "Tamam:"]
            L += ["• " + g for g in tamam] or ["• (yok)"]
            L += ["", "GELMEDİ:"]
            L += ["• " + g for g in eksik]
        if veri_satirlari:
            L += ["", "BIST VERİSİ:"] + veri_satirlari
        if karne_satirlari:
            L += ["", "ÖLÇÜM:"] + karne_satirlari
        tg("\n".join(L))
        day["summary_sent"] = True

    st[dk] = day
    # eski günleri buda (son 7 gün kalsın)
    for k in list(st.keys()):
        try:
            if (today - datetime.fromisoformat(k).date()).days > 7:
                del st[k]
        except Exception:
            pass
    save_state(st)


if __name__ == "__main__":
    main()
