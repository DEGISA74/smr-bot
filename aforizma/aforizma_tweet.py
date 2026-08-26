#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Aforizma Tweet — etkilesim/aforizma tweet TASLAGI secer ve admin'in Telegram
DM'ine gonderir (yari-otomatik: kullanici duzenleyip atar).

Kaynak: tweet_havuzu.json (elle yazilmis metinler; bot URETMEZ, sadece SECER).
Iki icerik ailesi:
  A) havuz[] — kavram basina 3 katman:
     - ayna          : olgun ozlu soz (her maddede var)
     - ters_kose     : kabul gormus dogruyu tokatlayan karsi-gorus
     - ciplak_gercek : en sivri versiyon (cogu maddede yok; NADIR gonderilir)
     Seri basligi: "Kucuk Yatirimci Notlari {seri_no}" (her gonderimde +1)
  B) mitler[] — "Yanlis Bilinenler": her tweette 3 mit/duzeltme cifti,
     mumkun oldugunca FARKLI kategorilerden. Kendi sayaci: mit_seri_no.

Kova orani (oranlar): ayna %60 / ters_kose %35 / ciplak_gercek %5.
MIT takvime bagli: Cmt/Paz OGLE slotu (hafta ici ve aksam slotu -> aforizma).
Gunde 2 kez timer ile tetiklenir; her tetikte 1 taslak gonderir.

Tekrar onleme:
  - Bir kavram (id) son ~25 gonderimde tekrar cikmaz.
  - Ayni tam varyant (id|katman) son ~80 gonderimde tekrar cikmaz.
  - Pes pese ayni kategori gelmez.
  - Kisitlar tikanirsa kademeli gevsetilir (asla "gonderemedim" demez).

Guvenlik: son gonderimden bu yana < min_gap_hours ise atlar (cift-tetik korumasi).

CLI:
  --test  : kapilari yok say, aninda 1 ornek taslak gonder, state'e DOKUNMA
  --dry   : sec + logla, GONDERME, state'e DOKUNMA (token gerekmez)
  --sim N : N gonderim simule et (dagilimi goster), GONDERME, state'e DOKUNMA
"""

import os
import sys
import json
import random
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

# .env varsa yukle (lokalde olmayabilir; --dry/--sim token istemez)
try:
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("aforizma")

STATE_PATH = BASE / "aforizma_state.json"

TEST = "--test" in sys.argv
DRY = "--dry" in sys.argv
FORCE_MIT = "--mit" in sys.argv    # test: takvimi yok say, MIT taslagi uret
FORCE_KONUM = "--konumlama" in sys.argv  # test: KONUMLAMA taslagi uret
FORCE_ACI = "--aci" in sys.argv          # test: ACI GERCEK taslagi uret
SIM = 0
if "--sim" in sys.argv:
    try:
        SIM = int(sys.argv[sys.argv.index("--sim") + 1])
    except (ValueError, IndexError):
        SIM = 30

KATMAN_ETIKET = {
    "ayna": "🪞 Ayna",
    "ters_kose": "↩️ Ters Köşe",
    "ciplak_gercek": "🩹 Çıplak Gerçek",
}
KAT_ETIKET = {
    "psikoloji": "Psikoloji",
    "bist": "BIST & Bilanço",
    "makro": "Global & Makro",
    "teknik": "Teknik & Price Action",
    "egitim": "Eğitim & Portföy",
    "kripto": "Kripto & Türev",
}

# Aforizma ailesi okuyucuya ders veren ama yukarıdan konuşmayan bir ses taşır.
# Sorular, seçilen metnin dersini "biz" diliyle düşünmeye açar; metnin kendisini
# otomatik dönüştürmez.
KATILIM_SORULARI = {
    "psikoloji": [
        "Biz bu refleksin devreye girdiğini hangi anda fark ediyoruz?",
        "Böyle bir anda ekran mı bizi yönetiyor, yoksa plan mı?",
        "Biz bugün hangi küçük kuralı koyarsak yarın daha az pişman oluruz?",
    ],
    "bist": [
        "Bu hikâyeye kapılmadan önce biz hangi veriyi bir daha kontrol ederiz?",
        "Bizim için asıl soru ne: hikâye mi, fiyatı taşıyan kanıt mı?",
        "Bu başlıkta hangi karşı senaryo planımızı daha sağlam tutar?",
    ],
    "makro": [
        "Bu gelişme portföyümüze hangi kapıdan girer, önce onu mu konuşsak?",
        "Manşetin ötesine geçmek için biz hangi veriyi birlikte izleriz?",
        "Bu tabloda hangi varsayımımızı yeniden kontrol etmemiz gerekir?",
    ],
    "teknik": [
        "Bu sinyale güvenmeden önce biz hangi teyidi görmek isteriz?",
        "Grafikte heyecan var diye planı değiştirmeden önce hangi riski sınırlarız?",
        "Bu hareketi tek başına değil, hangi işaretlerle birlikte okuruz?",
    ],
    "egitim": [
        "Bu notu kendi planımıza nereye koyarız?",
        "Daha sağlam karar için önce hangi soruyu kendimize sorarız?",
        "Bu konuyu portföyümüzde hangi somut kuralla karşılarız?",
    ],
    "kripto": [
        "Bu işleme girmeden önce biz hangi riski mutlaka kontrol ederiz?",
        "Getirinin yanında hangi çıkış yolunun açık kaldığını doğrularız?",
        "Bu pozisyon ters giderse planımızın hangi bölümü bizi korur?",
    ],
}

# Tekrar-onleme pencereleri
ID_PENCERE = 25      # bir kavram bu kadar gonderim boyunca tekrar cikmaz
UNIT_PENCERE = 80    # ayni tam varyant bu kadar gonderim boyunca tekrar cikmaz


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
    p = Path(cfg.get("havuz_path", "../tweet_havuzu.json"))
    if not p.is_absolute():
        p = (BASE / p).resolve()
    with open(p, encoding="utf-8") as f:
        return json.load(f)


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


# ---------- Birim havuzu ----------
def build_units(havuz):
    """Her maddeyi (metni dolu) katmanlarina acar: [{id,katman,kategori,konu,metin}, ...]"""
    units = []
    for item in havuz["havuz"]:
        for katman in ("ayna", "ters_kose", "ciplak_gercek"):
            metin = item.get(katman)
            if metin:
                units.append({
                    "id": item["id"],
                    "katman": katman,
                    "kategori": item.get("kategori", ""),
                    "konu": item.get("konu", ""),
                    "metin": metin.strip(),
                })
    return units


def pick_unit(units, st, oranlar, rng):
    """Kova orani + tekrar onleme ile bir birim sec. Asla None donmez (havuz bosunsa haric)."""
    recent_ids = set(st.get("recent_ids", [])[-ID_PENCERE:])
    recent_units = set(st.get("recent_units", [])[-UNIT_PENCERE:])
    last_kat = st.get("last_kategori")

    # Katmanlari orana gore agirlikli sirala (once denenecek katman)
    katmanlar = ["ayna", "ters_kose", "ciplak_gercek"]
    agirlik = [max(oranlar.get(k, 0), 0.0001) for k in katmanlar]
    sirali = _weighted_order(katmanlar, agirlik, rng)

    # Kademeli gevsetme: (id-penceresi, varyant-penceresi, kategori-cakismasi)
    for use_id, use_unit, use_kat in [(True, True, True),
                                      (True, True, False),
                                      (False, True, False),
                                      (False, False, False)]:
        for katman in sirali:
            cands = []
            for u in units:
                if u["katman"] != katman:
                    continue
                if use_id and u["id"] in recent_ids:
                    continue
                if use_unit and f"{u['id']}|{u['katman']}" in recent_units:
                    continue
                if use_kat and last_kat and u["kategori"] == last_kat:
                    continue
                cands.append(u)
            if cands:
                return rng.choice(cands)
    return units[0] if units else None


def _weighted_order(items, weights, rng):
    """Agirliksiz-yerine-koymasiz ornekleme ile items'i agirliga gore sirala."""
    items = list(items)
    weights = list(weights)
    order = []
    while items:
        total = sum(weights)
        r = rng.uniform(0, total)
        acc = 0.0
        for i, w in enumerate(weights):
            acc += w
            if r <= acc:
                order.append(items.pop(i))
                weights.pop(i)
                break
        else:
            order.append(items.pop())
            weights.pop()
    return order


MIT_PENCERE = 30   # bir mit bu kadar KULLANILMIS mit boyunca tekrar cikmaz (~10 tweet)


def pick_mitler(mitler, st, rng, n=3):
    """3 mit sec — mumkun oldugunca FARKLI kategorilerden, tekrar onlemeli."""
    recent = set(st.get("recent_mitler", [])[-MIT_PENCERE:])
    cands = [m for m in mitler if m["id"] not in recent] or list(mitler)
    if len(cands) < n:
        cands = list(mitler)
    rng.shuffle(cands)
    secilen, kats = [], set()
    for m in cands:
        if m["kategori"] in kats:
            continue
        secilen.append(m)
        kats.add(m["kategori"])
        if len(secilen) == n:
            break
    for m in cands:                       # yeterli farkli kategori yoksa doldur
        if len(secilen) >= n:
            break
        if m not in secilen:
            secilen.append(m)
    return secilen


def push_recent_mit(st, secilen):
    st.setdefault("recent_mitler", []).extend(m["id"] for m in secilen)
    st["recent_mitler"] = st["recent_mitler"][-(MIT_PENCERE * 2):]


def push_recent(st, unit):
    st.setdefault("recent_ids", []).append(unit["id"])
    st["recent_ids"] = st["recent_ids"][-(ID_PENCERE * 2):]
    st.setdefault("recent_units", []).append(f"{unit['id']}|{unit['katman']}")
    st["recent_units"] = st["recent_units"][-(UNIT_PENCERE * 2):]
    st["last_kategori"] = unit["kategori"]


# ---------- Taslak bicimi ----------
OZEL_TIPLER = {"ikilem": "ikilemler", "risk": "riskler", "efsane": "efsaneler",
               "manset": "mansetler"}
OZEL_ETIKET = {"ikilem": "⚖️ İkilem Sorusu", "risk": "⚠️ Risk Altındasın",
               "efsane": "🧙 Piyasa Efsanesi", "manset": "🗯️ Manşet"}


def _human_text(text):
    """Yalnızca Telegram'a giden metni ortak, sade yazım diline çevirir."""
    return (text.strip()
                .replace("**", "")
                .replace('"', "'")
                .replace("«", "'")
                .replace("»", "'")
                .replace("—", ";")
                .replace("–", "-")
                .replace("💬 ", ""))


def _format_admin_draft(header, body, question=None, is_test=False):
    """Bütün aforizma aileleri için ortak, sade taslak kabuğu."""
    tag = " (TEST)" if is_test else ""
    parts = [
        f"📨 AFORİZMA TASLAK{tag} - düzenle & at",
        header,
        body,
    ]
    if question:
        parts.append(question)
    parts.append("ℹ️ Sana özel taslak; sen atmadıkça kimse görmez.")
    return "\n\n".join(_human_text(part) for part in parts if part)


def pick_liste(items, st, key, rng):
    """Basit listeden tekrar-onlemeli sec (pencere = listenin ~1/3'u)."""
    pencere = max(5, len(items) // 3)
    recent = set(st.get(f"recent_{key}", [])[-pencere:])
    cands = [x for x in items if x["id"] not in recent] or list(items)
    return rng.choice(cands)


def push_recent_liste(st, key, item):
    k = f"recent_{key}"
    st.setdefault(k, []).append(item["id"])
    st[k] = st[k][-60:]


def format_aci(x, is_test=False, seri_baslik="Küçük Yatırımcı Notları", seri_no=None):
    if x.get("soru"):   # ZENGİN format (3 katmanlı: soru-hook -> kurulum -> dönüş -> kapanış)
        govde = "\n\n".join([x["soru"], x["kurulum"], x["donus"], x["kapanis"]])
        return _format_admin_draft(
            f"☕️ {seri_baslik} - Acı Gerçekler",
            govde,
            "Biz bu notu kendi planımızda hangi noktaya yazıyoruz?",
            is_test=is_test,
        )
    # KLASİK format (eski tek-satır madde — havuzda fallback olarak duruyor)
    return _format_admin_draft(
        f"☕️ {seri_baslik} - Acı Gerçekler",
        f"{x['baslik']}\n\n{x['metin']}",
        "Biz burada en kolay hangi gerçeği görmezden geliyoruz?",
        is_test=is_test,
    )


def format_konumlama(x, is_test=False):
    return _format_admin_draft(
        "☕️ Küçük Yatırımcı Notları - Not Defterim",
        f"'{x['baslik']}'\n\n{x['govde']}",
        "Biz bu notu yarınki kararımızda nasıl kullanırız?",
        is_test=is_test,
    )


def format_ozel_draft(tip, x, no=None, is_test=False):
    if tip == "ikilem":
        govde = x["soru"]
        baslik = "İkilem"
        soru = "Biz burada hangi tarafı seçmeden önce bir daha düşünürüz?"
    elif tip == "risk":
        govde = ("Hepimizin zaman zaman düştüğü tuzaklardan biri:\n\n"
                 f"{x['davranis']}\n\n{x['sitem']}")
        baslik = "Risk Notu"
        soru = "Böyle bir anda biz hangi kuralımıza geri döneriz?"
    elif tip == "manset":
        govde = f"'{x['baslik']}'\n\n{x['govde']}"
        baslik = "Manşet"
        soru = "Bu başlıkta biz manşetin ötesinde neyi kontrol ederiz?"
    else:  # efsane
        govde = (f"'{x['efsane']}'\n\n"
                 f"Gerçekte ne oluyor?\n{x['gercek']}")
        baslik = "Piyasa Efsanesi"
        soru = "Biz bu cümleyi duyunca önce hangi veriye bakarız?"
    return _format_admin_draft(
        f"☕️ Küçük Yatırımcı Notları - {baslik}",
        govde,
        soru,
        is_test=is_test,
    )


def format_mit_draft(secilen, baslik, no, is_test=False):
    govde = "\n\n".join(
        f"'{m['mit']}' diye biliyoruz.\nAslında: {m['duzeltme']}" for m in secilen
    )
    return _format_admin_draft(
        f"☕️ Küçük Yatırımcı Notları - {baslik}",
        govde,
        "Bu üç düşünceden hangisini biz kendi planımızda yeniden kontrol ederiz?",
        is_test=is_test,
    )


def format_draft(unit, is_test=False, seri_baslik="", seri_no=None, rng=None):
    baslik = seri_baslik or "Küçük Yatırımcı Notları"
    soru_havuzu = KATILIM_SORULARI.get(unit["kategori"], [
        "Bu notu kendi planımıza nereye koyarız?"
    ])
    soru = (rng or random).choice(soru_havuzu)
    return _format_admin_draft(
        f"☕️ {baslik} - {KAT_ETIKET.get(unit['kategori'], unit['kategori'])}",
        unit["metin"],
        soru,
        is_test=is_test,
    )


# ---------- Simulasyon (dagilim testi) ----------
def run_sim(units, mitler, cfg, n):
    st = json.loads(json.dumps(cfg["state"]))  # kopya
    rng = random.Random()
    from collections import Counter
    kc, katc = Counter(), Counter()
    tekrar_mesafe = []
    son_gorulen = {}
    oranlar = cfg["oranlar"]
    for i in range(n):
        u = pick_unit(units, st, oranlar, rng)
        kc[u["katman"]] += 1
        katc[u["kategori"]] += 1
        key = f"{u['id']}|{u['katman']}"
        if key in son_gorulen:
            tekrar_mesafe.append(i - son_gorulen[key])
        son_gorulen[key] = i
        push_recent(st, u)
    print(f"\n=== {n} gonderim simulasyonu ===")
    print("Katman dagilimi:")
    for k in ("ayna", "ters_kose", "mit", "ciplak_gercek"):
        print(f"  {k:16s}: {kc[k]:4d}  (%{100*kc[k]/n:.1f})")
    print("Kategori dagilimi:")
    for k, v in katc.most_common():
        print(f"  {k:16s}: {v:4d}")
    if tekrar_mesafe:
        print(f"En yakin varyant tekrari: {min(tekrar_mesafe)} gonderim sonra "
              f"(gunde 2 => ~{min(tekrar_mesafe)/2:.0f} gun)")
    else:
        print(f"{n} gonderimde HIC birebir varyant tekrari yok.")
    print(f"Toplam atilabilir birim: {len(units)}")


# ---------- Main ----------
def main():
    cfg = load_state()
    havuz = load_havuz(cfg)
    units = build_units(havuz)
    if not units:
        log.error("Havuz bos — atilabilir birim yok.")
        return

    if SIM:
        run_sim(units, havuz.get("mitler", []), cfg, SIM)
        return

    tz = ZoneInfo(cfg.get("tz", "Europe/Istanbul"))
    now = datetime.now(tz)
    st = cfg["state"]

    if not TEST and not DRY:
        # baslangic kapisi
        sd = cfg.get("start_date")
        if sd and now.date().isoformat() < sd:
            log.info(f"start_date ({sd}) oncesi — gonderim yok.")
            return
        # cift-tetik korumasi
        gap_h = cfg.get("min_gap_hours", 4)
        last = st.get("last_sent_ts")
        if last:
            try:
                delta = (now - datetime.fromisoformat(last)).total_seconds() / 3600
                if delta < gap_h:
                    log.info(f"Son gonderim {delta:.1f} saat once (< {gap_h}h) — atlaniyor.")
                    return
            except ValueError:
                pass

    rng = random.Random()
    oranlar = cfg["oranlar"]
    mitler = havuz.get("mitler", [])

    konumlamalar = havuz.get("konumlamalar", [])
    aci_list = havuz.get("aci_gercekler", [])

    # TAKVIMLI icerikler:
    #   Cmt/Paz 1. slot (saat<14) -> MIT (Yanlis Bilinenler)
    #   Cmt/Paz 2. slot (saat>=14) -> ACI GERCEKLER
    #   Sali 1. slot (saat<14)     -> KONUMLAMA (Not Defterim)
    # Digerleri -> agirlikli aile (manset dahil).
    use_mit = len(mitler) >= 3 and (
        FORCE_MIT or (now.weekday() in cfg.get("mit_gunler", [5, 6])
                      and now.hour < cfg.get("mit_saat_siniri", 14))
    )
    use_aci = bool(aci_list) and (
        FORCE_ACI or (now.weekday() in cfg.get("aci_gunler", [5, 6])
                      and now.hour >= cfg.get("aci_saat_min", 14))
    )
    use_konum = bool(konumlamalar) and (
        FORCE_KONUM or (now.weekday() in cfg.get("konumlama_gunler", [1])
                        and now.hour < cfg.get("konumlama_saat_siniri", 14))
    )

    unit = None
    secilen_mit = None
    ozel_tip = ozel_item = None
    konum_item = aci_item = None
    seri_no = mit_no = efs_no = None

    # Takvimli degilse: once ozel tip zorlamasi, yoksa agirlikli aile secimi
    if not use_mit and not use_konum and not use_aci:
        for t in OZEL_TIPLER:
            if f"--{t}" in sys.argv:
                ozel_tip = t
        if ozel_tip is None:
            aile = ["aforizma"] + [t for t in OZEL_TIPLER if havuz.get(OZEL_TIPLER[t])]
            agir = [sum(oranlar.get(k, 0) for k in ("ayna", "ters_kose", "ciplak_gercek"))]
            agir += [oranlar.get(t, 0) for t in aile[1:]]
            sec = rng.choices(aile, weights=agir, k=1)[0]
            ozel_tip = None if sec == "aforizma" else sec

    if use_aci:
        # Zengin (3 katmanlı) acı gercekleri tercih et; yoksa klasik havuza dus
        aci_zengin = [x for x in aci_list if x.get("soru")]
        aci_item = pick_liste(aci_zengin or aci_list, st, "aci", rng)
        seri_no = st.get("seri_no")
        log.info(f"Secilen: ACI GERCEK -> {aci_item['id']}")
        msg = format_aci(aci_item, is_test=TEST,
                         seri_baslik=cfg.get("seri_baslik", "Küçük Yatırımcı Notları"),
                         seri_no=seri_no)
    elif use_konum:
        konum_item = pick_liste(konumlamalar, st, "konumlama", rng)
        log.info(f"Secilen: KONUMLAMA -> {konum_item['id']}")
        msg = format_konumlama(konum_item, is_test=TEST)
    elif ozel_tip:
        ozel_item = pick_liste(havuz[OZEL_TIPLER[ozel_tip]], st, ozel_tip, rng)
        if ozel_tip == "efsane":
            efs_no = st.get("efsane_seri_no", 1)
        log.info(f"Secilen: {ozel_tip.upper()} -> {ozel_item['id']}")
        msg = format_ozel_draft(ozel_tip, ozel_item, no=efs_no, is_test=TEST)
    elif use_mit:
        secilen_mit = pick_mitler(mitler, st, rng)
        mit_no = st.get("mit_seri_no", 1)
        log.info("Secilen: MİT x3 -> " + ", ".join(m["id"] for m in secilen_mit))
        msg = format_mit_draft(secilen_mit, cfg.get("mit_seri_baslik", "Yanlış Bilinenler"),
                               mit_no, is_test=TEST)
    else:
        unit = pick_unit(units, st, oranlar, rng)
        log.info(f"Secilen: [{unit['id']}] {unit['katman']} / {unit['kategori']} — {unit['konu']}")
        seri_baslik = cfg.get("seri_baslik", "")
        seri_no = st.get("seri_no")
        msg = format_draft(unit, is_test=TEST, seri_baslik=seri_baslik,
                           seri_no=seri_no, rng=rng)

    if DRY:
        log.info("DRY-RUN — gonderilmedi:\n" + msg)
        return

    admin = str(cfg["admin_chat_id"])
    if not tg_send(admin, msg):
        log.error("Telegram gonderimi basarisiz — state guncellenmedi.")
        return
    log.info("Taslak admin DM'ine gonderildi.")

    if not TEST:
        if aci_item:
            push_recent_liste(st, "aci", aci_item)
            if seri_no and aci_item.get("soru"):   # zengin acı ana seriyi ilerletir
                st["seri_no"] = seri_no + 1
        elif konum_item:
            push_recent_liste(st, "konumlama", konum_item)
        elif ozel_tip:
            push_recent_liste(st, ozel_tip, ozel_item)
            if ozel_tip == "efsane":
                st["efsane_seri_no"] = (efs_no or 1) + 1
        elif use_mit:
            push_recent_mit(st, secilen_mit)
            st["mit_seri_no"] = (mit_no or 1) + 1
        else:
            push_recent(st, unit)
            if seri_no:                   # seri numarasi her gonderimde 1 artar
                st["seri_no"] = seri_no + 1
        st["last_sent_ts"] = now.isoformat()
        save_state(cfg)
        log.info("State guncellendi.")


if __name__ == "__main__":
    main()
