#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GÜNDEM TWEET v2 — sabah raporu (27 Ağu 2026 yeniden yazıldı).

ESKİSİ NEDEN ROBOTTU: kelime kıtlığı değil, HER GÜN AYNI İSKELET. Rapor beş
kutuyu hep aynı sırayla dolduruyordu (açılış → sektör → hisse → kapanış → soru)
ve havuzlar küçüktü ("yeşil gün" için tek bir soru vardı → 21 ve 26 Ağustos'ta
aynı cümle çıktı). Üstüne her gün aynı 4 rakam anlatılıyordu.

YENİ KURGU:
  1. GERÇEK KATMANI (gundem_veri.py) — gün içi hikâye, ısrar oranı, kazanan/
     kaybeden asimetrisi, hafta karnesi, sığ tavan sayımı. Her gün farklı.
  2. MANŞET SEÇİCİ — "dünün en ilginç şeyi ne?" sorusunu veriye sorar; raporun
     BİÇİMİ her gün ona göre kurulur. Bazı gün sektör, bazı gün tek hisse,
     bazı gün çelişki, bazı gün "anlatacak bir şey yok" (o gün rapor kısadır).
  3. YAZAR = Gemini — rakamlar makinede kalır, cümleyi model yazar.
     SAYI KAPISI: çıktıdaki her sayı gerçek listeyle karşılaştırılır; bir tanesi
     bile tutmazsa taslak REDDEDİLİR ve şablona düşülür. Uydurma sayı geçemez.
  4. TEKRAR NÖBETÇİSİ — son 12 taslağın manşeti/alt başlığı/girişi saklanır;
     aynı manşet ve aynı cümle geri gelemez.

İSİM VERME KURALI (memory/project_isim_verme_evreni.md):
  • Hesap BIST100/piyasa geneli · adı yazılan hisse SADECE BIST50'den.
  • Filtrenin adı METİNDE GEÇMEZ. "VIOP" yok, "BIST50" yok, "en likit N hisse"
    yok. Hisseyi gösteririz, nereden seçtiğimizi anlatmayız.

ZAMANLAMA: Pzt–Cum 08:30. Pazartesi Cuma kapanışını + HAFTA KARNESİNİ anlatır
(eskiden Cumartesi gidiyordu; hafta sonu kimse okumuyordu).

CLI:
  --dry            hesapla + göster, GÖNDERME
  --test           kapıları yok say, hemen gönder
  --tarih YYYY-MM-DD   belirli bir günün raporunu üret (geçmişe dönük deneme)
  --sablon         Gemini'yi atla, şablonla yaz (karşılaştırma için)
  --gecmisi-yoksay tekrar nöbetçisini devre dışı bırak (deneme)
"""
import os, re, sys, json, random, datetime as dt
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import gundem_veri as gv

BASE = Path(__file__).parent
ADMIN_CHAT_ID = "1034525990"
GECMIS_YOL = BASE / "gundem_gecmis.json"
GECMIS_TUT = 12            # son kaç taslağın parmak izi saklansın
MANSET_BEKLE = 6           # aynı manşet kaç taslak boyunca geri gelemez

DRY   = "--dry" in sys.argv
TEST  = "--test" in sys.argv
SABLON_ZORLA = "--sablon" in sys.argv
GECMIS_YOKSAY = "--gecmisi-yoksay" in sys.argv
TARIH = None
if "--tarih" in sys.argv:
    try: TARIH = sys.argv[sys.argv.index("--tarih") + 1]
    except IndexError: pass


# ─────────────────────── ŞİRKET ADLARI ───────────────────────
# borsapy ham adları çirkin ("T. HALK BANKASI", "IS BANKASI (C)"). Metinde
# insanların kullandığı ad geçmeli. Bilinmeyen için ham ad başlık-biçimine döner.
GUZEL_AD = {
    "AKBNK": "Akbank", "GARAN": "Garanti", "ISCTR": "İş Bankası", "YKBNK": "Yapı Kredi",
    "VAKBN": "Vakıfbank", "HALKB": "Halkbank", "THYAO": "Türk Hava Yolları",
    "ASELS": "Aselsan", "TUPRS": "Tüpraş", "EREGL": "Erdemir", "KCHOL": "Koç Holding",
    "SAHOL": "Sabancı Holding", "BIMAS": "BİM", "MGROS": "Migros", "SISE": "Şişecam",
    "TCELL": "Turkcell", "TTKOM": "Türk Telekom", "FROTO": "Ford Otosan",
    "TOASO": "Tofaş", "PETKM": "Petkim", "SASA": "Sasa", "EKGYO": "Emlak Konut",
    "PGSUS": "Pegasus", "TAVHL": "TAV", "ENKAI": "Enka", "ASTOR": "Astor Enerji",
    "AEFES": "Anadolu Efes", "CCOLA": "Coca-Cola İçecek", "ULKER": "Ülker",
    "KRDMD": "Kardemir", "GUBRF": "Gübre Fabrikaları", "HEKTS": "Hektaş",
    "BRSAN": "Borusan Boru", "CIMSA": "Çimsa", "OYAKC": "Oyak Çimento",
    "AKSEN": "Aksa Enerji", "ALARK": "Alarko Holding", "TURSG": "Türkiye Sigorta",
    "ECILC": "Eczacıbaşı İlaç", "MIATK": "Mia Teknoloji", "KTLEV": "Katılımevim",
    "DSTKF": "Destek Faktoring", "BTCIM": "Batıçim", "CANTE": "Can2 Termik",
    "EFOR": "Efor Yatırım", "GLRMK": "Gülermak", "KUYAS": "Kuyaş Yatırım",
    "PASEU": "Pasifik Eurasia", "TRALT": "Türk Altın", "TRMET": "TR Anadolu Metal",
}

def ad(kod, ham=""):
    if kod in GUZEL_AD:
        return GUZEL_AD[kod]
    h = (ham or kod).replace(".", " ").strip()
    return " ".join(w.capitalize() for w in h.split()) or kod


def etiket(kod):
    """BIST kodu → tweet etiketi. Ev stili küçük harf (13-14 Ağu 2026 tercihi)."""
    return "#" + str(kod).replace(".IS", "").strip().lower()


_SESLI = "aeıioöuü"
_UYUM  = {"a": "ın", "ı": "ın", "e": "in", "i": "in",
          "o": "un", "u": "un", "ö": "ün", "ü": "ün"}

def ilgi(kelime, ozel=True):
    """Türkçe ilgi eki. ozel=True özel isim (kesme işaretli), False cins isim.

    'Halkbank' → "Halkbank'ın" · 'Enka' → "Enka'nın" · 'bankalar' → 'bankaların'.
    Ek uyumu olmadan metin yabancı duruyor ("Halkbank'in günü"). Son sesliye
    göre ın/in/un/ün; kelime sesliyle bitiyorsa araya kaynaştırma 'n'si girer.
    """
    k = (kelime or "").strip()
    if not k:
        return k
    son = next((c for c in reversed(k.lower()) if c in _SESLI), "a")
    ek = _UYUM.get(son, "ın")
    tampon = "n" if k[-1].lower() in _SESLI else ""
    return f"{k}'{tampon}{ek}" if ozel else f"{k}{tampon}{ek}"


# ─────────────────────── ZAMAN KELİMESİ ───────────────────────
def zaman_kelimesi(veri_tarihi, bugun=None):
    """'dün' mü 'Cuma' mı? Pazartesi raporu Pazar'ı anlatamaz."""
    bugun = bugun or dt.date.today()
    try:
        d = dt.date.fromisoformat(veri_tarihi)
    except Exception:
        return "dün"
    fark = (bugun - d).days
    if fark <= 1:
        return "dün"
    gunler = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
    if fark <= 4:
        return gunler[d.weekday()]
    return "son işlem gününde"


# ─────────────────────── TEKRAR NÖBETÇİSİ ───────────────────────
def gecmis_oku():
    try:
        return json.load(open(GECMIS_YOL, encoding="utf-8"))
    except Exception:
        return []

def gecmis_yaz(kayit):
    g = gecmis_oku()
    g = [k for k in g if k.get("tarih") != kayit.get("tarih")]
    g.append(kayit)
    try:
        json.dump(g[-GECMIS_TUT:], open(GECMIS_YOL, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception:
        pass

def yakin_zamanda_kullanildi(manset_id, n=MANSET_BEKLE):
    if GECMIS_YOKSAY:
        return False
    return manset_id in [k.get("manset_id") for k in gecmis_oku()[-n:]]


# ─────────────────────── MANŞET SEÇİCİ ───────────────────────
# Her aday: (id, skor, alt_başlık, konu_özeti). Skor = "bugün bu ne kadar ilginç".
# Alt başlık = ☕️ satırının ikinci parçası (kullanıcı kararı: C seçeneği).
def manset_adaylari(f, hafta=None, pazartesi=False, cuma=False):
    a = []
    x = f["x"]; sek = f.get("sektor") or {}
    gi = set(f["gun_ici"])
    buyukler = [b for b in f["buyukler"] if not b.get("artefakt")]
    ilk = buyukler[0] if buyukler else None

    # — sektör ayrışması —
    if len(sek) >= 2:
        lider = max(sek, key=lambda k: sek[k]); geri = min(sek, key=lambda k: sek[k])
        fark = sek[lider] - sek[geri]
        if fark >= 1.0:
            a.append(("sektor", 60 + fark * 4,
                      f"{ilgi(lider, ozel=False).capitalize()} günü",
                      f"{lider} %{sek[lider]:+.1f}, {geri} %{sek[geri]:+.1f} — para taraf seçti"))

    # — gün içi ters dönüşler (en anlatılası hikâyeler) —
    if "artida_acti_eksi_kapatti" in gi:
        a.append(("gun_ici_geri_verdi", 88, "Kazandığını geri veren gün",
                  f"açılış %{f['acilis']:+.2f}, kapanış %{x:+.2f}"))
    if "eksi_acti_arti_kapatti" in gi:
        a.append(("gun_ici_toparladi", 86, "Tersine dönen gün",
                  f"açılış %{f['acilis']:+.2f}, kapanış %{x:+.2f}"))
    if "boslukla_acildi" in gi:
        a.append(("bosluk", 92, "Boşlukta açılan gün",
                  f"endeks %{f['acilis']:+.2f} ile açtı, %{x:+.2f} kapattı"))
    if "dipten_topladi" in gi:
        a.append(("dipten", 78, "Dipten toplanan gün",
                  f"gün içi dip %{f['dip']:+.2f}, kapanış %{x:+.2f}"))
    if "dibinde_kapatti" in gi and x < -1:
        a.append(("dipte_kapandi", 90, "Kapanışa kadar süren satış",
                  f"gün en dibinde bitti (%{x:+.2f})"))
    if "hic_eksiye_dusmedi" in gi and x > 0.4:
        a.append(("hic_eksiye", 62, "Bir kez bile eksiye düşmeyen gün",
                  f"gün içi dip %{f['dip']:+.2f}"))
    if "hic_artiya_gecmedi" in gi and x < -0.4:
        a.append(("hic_artiya", 74, "Artıyı hiç görmeyen gün",
                  f"gün içi zirve %{f['zirve']:+.2f}"))

    # — ısrar: yükseliş devam mı, yer değiştirme mi —
    if x > 0.4 and f["yesil"] >= 60:
        if f["israr_pct"] < 30:
            a.append(("israr_yok", 80, "Yer değiştiren yeşil",
                      f"yükselen {f['yesil']} hissenin sadece {f['israr']}'i önceki gün de yükselmişti"))
        elif f["israr_pct"] > 58:
            a.append(("israr_var", 70, "Israrcı alıcı",
                      f"yükselen {f['yesil']} hissenin {f['israr']}'i önceki gün de yükselmişti"))

    # — endeks ile ekranın çelişmesi —
    if abs(x) < 0.4 and (f["kirmizi"] > f["yesil"] * 1.5 or f["yesil"] > f["kirmizi"] * 1.5):
        a.append(("celiski", 72, "Endeks sakin, ekran karışık",
                  f"endeks %{x:+.2f} ama {f['yesil']} yeşile karşı {f['kirmizi']} kırmızı"))

    # — büyük bir isimde sert hareket —
    if ilk and abs(ilk["pct"]) >= 5.0:
        yon = "tavan" if ilk["pct"] >= gv.TAVAN_ESIK else ("sert düşüş" if ilk["pct"] < 0 else "sert yükseliş")
        a.append((f"buyuk_{ilk['kod']}", 76 + abs(ilk["pct"]),
                  f"{ilgi(ad(ilk['kod'], ilk['ad']))} günü",
                  f"{ad(ilk['kod'], ilk['ad'])} %{ilk['pct']:+.1f} ({yon})"))

    # — hacim patlaması (fiyat değil, ilgi) —
    hac = max(buyukler, key=lambda b: b["rvol"]) if buyukler else None
    if hac and hac["rvol"] >= 2.2:
        a.append((f"hacim_{hac['kod']}", 66,
                  "Hacmin konuştuğu yer",
                  f"{ad(hac['kod'], hac['ad'])} hacmi normalin {hac['rvol']:.1f} katı"))

    # — endeks düşerken tavan yağmuru = tahtacı işi —
    if x < -0.5 and f["sig_tavan"] >= 4:
        a.append(("sig_tavan", 68, "Endeks düşerken tavan yapanlar",
                  f"{f['tavan']} tavanın {f['sig_tavan']}'i sığ tahtada"))

    # — hafta çerçevesi (Pzt/Cuma) —
    if pazartesi and hafta:
        a.append(("hafta_basi", 96, "Haftaya başlarken",
                  f"geçen hafta %{hafta['getiri']:+.1f}, {hafta['yesil_gun']} yeşil "
                  f"{hafta['kirmizi_gun']} kırmızı gün"))


    # — hiçbir şey yoksa: kısa gün. Bu bir kusur değil, en insani rapor budur. —
    a.append(("sakin", 10, "Sakin bir gün", f"endeks %{x:+.2f}, anlatacak çok şey yok"))
    a.sort(key=lambda z: -z[1])
    return a


SORU_TIPLERI = ["DAVRANIS", "IKI_OKUMA", "OLCUT"]

def soru_tipi_sec():
    """Kapanış sorusunun tipini kod seçer; son 2 taslakta kullanılan elenir.

    Modele "üç kalıptan birini seç" demek yetmedi: iki gün üst üste aynı kalıbı
    ("X mi yoksa Y mi?") kurdu. Aynı ders ikinci konu seçiminde de çıkmıştı.
    """
    son = [k.get("soru_tipi") for k in gecmis_oku()[-2:]]
    for t in SORU_TIPLERI:
        if GECMIS_YOKSAY or t not in son:
            return t
    return SORU_TIPLERI[0]


def ikinci_katman_sec(adaylar, manset):
    """Manşetten sonra işlenecek İKİNCİ konuyu kod seçer.

    Modele "şunlardan birini seç" demek işe yaramadı: 5 günün 5'inde de aynı
    konuyu (ısrar oranı) seçti, aynı yerde, aynı kurguyla — yeni bir tekrar tiki
    doğdu. Seçimi koda alıp son taslaklarda kullanılanı eleyince konu dönüyor.
    """
    kullanilmis = {k.get("ikinci_id") for k in gecmis_oku()[-3:]}
    for m in adaylar:
        if m[0] == manset[0] or m[0] == "sakin":
            continue
        if m[0] in kullanilmis and not GECMIS_YOKSAY:
            continue
        return m
    for m in adaylar:                      # hepsi elenmişse en güçlü farklı konu
        if m[0] != manset[0] and m[0] != "sakin":
            return m
    return None


def manset_sec(f, hafta, pazartesi, cuma):
    adaylar = manset_adaylari(f, hafta, pazartesi, cuma)
    for m in adaylar:
        if not yakin_zamanda_kullanildi(m[0]):
            return m, adaylar
    return adaylar[0], adaylar        # hepsi yakın zamanda çıktıysa en güçlüsü


def yz(v, ondalik=1):
    """Yüzde biçimleyici. Sıfıra çok yakın değer '%+0.0' diye yazılınca metin
    tuhaflaşıyordu ("açılışta %+0.0 seviyesindeyken") → 'yatay' de."""
    try: v = float(v)
    except Exception: return "?"
    return "yatay" if abs(v) < 0.05 else f"%{v:+.{ondalik}f}"


def tl_yaz(mn):
    """1056.5 → '1.1 milyar TL' · 640.2 → '640 milyon TL'.

    Fişi makine formatında verirsek model aynen kopyalar ('14,921 milyon TL').
    İnsan '15 milyar' der. Biçimi KAYNAKTA düzeltmek, sonradan uyarmaktan sağlam.
    """
    try: mn = float(mn)
    except Exception: return "?"
    if mn >= 1000:
        return f"{mn/1000:.1f} milyar TL".replace(".0 milyar", " milyar")
    return f"{mn:.0f} milyon TL"


# ─────────────────────── GERÇEK FİŞİ (Gemini'ye giden + sayı kapısı kaynağı) ───
def fis_metni(f, hafta, manset, zaman, ikinci=None, soru_tipi=None):
    """Modele giden gerçek listesi. Buradaki sayılar dışına ÇIKAMAZ."""
    sek = f.get("sektor") or {}
    L = [
        f"GÜN: {zaman} ({f['tarih']})",
        f"ENDEKS kapanış: {yz(f['x'])}",
        f"ENDEKS açılış: {yz(f['acilis'])} · gün içi dip: {yz(f['dip'])} · "
        f"gün içi zirve: {yz(f['zirve'])}",
        f"Son 5 günün toplamı: {yz(f['cum5'])} (BU HAFTALIK GETİRİ DEĞİL, "
        f"sadece son 5 işlem gününün toplamı)",
        f"GENİŞLİK: {f['yesil']} hisse yükseldi, {f['kirmizi']} hisse düştü",
        f"Yükselenlerin ortalaması: %{f['yesil_ort']:+.1f} · düşenlerin ortalaması: %{f['kirmizi_ort']:+.1f}",
        f"Tavan sayısı: {f['tavan']} (bunların {f['sig_tavan']}'i sığ tahtada) · taban sayısı: {f['taban']}",
    ]
    # ISRAR sadece KONU İSE verilir. Fişte durduğu sürece model onu her rapora
    # sıkıştırıyordu (5 günün 5'inde çıktı) — vermeyince tekrar da bitiyor.
    konular = {manset[0], (ikinci[0] if ikinci else "")}
    if konular & {"israr_yok", "israr_var"}:
        L.append(f"ISRAR: yükselen {f['yesil']} hissenin {f['israr']}'i önceki gün de "
                 f"yükselmişti (%{f['israr_pct']:.0f})")
    if f["dusen_seri"] >= 2:
        L.append(f"Bu günden önce {f['dusen_seri']} gün üst üste düşülmüştü")
    if f["yukselen_seri"] >= 2:
        L.append(f"Bu günden önce {f['yukselen_seri']} gün üst üste yükselinmişti")
    if len(sek) >= 2:
        lider = max(sek, key=lambda k: sek[k]); geri = min(sek, key=lambda k: sek[k])
        # Model iki sektörü ters yazabiliyor (28 Ağu vakası). Sonucu AÇIKÇA söyle.
        L.append(f"SEKTÖR: {lider} {yz(sek[lider])} · {geri} {yz(sek[geri])}"
                 f"  → ÖNDE OLAN: {lider}. Bunu ters yazma.")
    else:
        for k, v in sek.items():
            L.append(f"SEKTÖR {k}: {yz(v)}")
    L.append(f"GÜNÜN ŞEKLİ: {', '.join(f['gun_ici']) if f['gun_ici'] else 'belirgin bir şekil yok'}")

    L.append("")
    L.append("ANABİLECEĞİN HİSSELER — metinde SADECE ETİKETİ yaz, şirket adını yazma."
             " Bu listenin DIŞINA çıkma:")
    for b in [q for q in f["buyukler"] if not q.get("artefakt")][:5]:
        L.append(f"  - {etiket(b['kod'])} ({ad(b['kod'], b['ad'])}): %{b['pct']:+.1f} · "
                 f"hacmi normalinin {b['rvol']:.1f} katı · {tl_yaz(b['tl_mn'])} işlem")
    L.append(f"  (bu gruptan {f['buyuk_yesil']}'i yükseldi, {f['buyuk_kirmizi']}'i düştü)")

    if hafta:
        L.append("")
        L.append(f"HAFTA: geçen hafta endeks %{hafta['getiri']:+.1f}; "
                 f"{hafta['yesil_gun']} gün yeşil, {hafta['kirmizi_gun']} gün kırmızı. "
                 f"En iyi gün %{hafta['en_iyi_gun']:+.1f}, en kötü gün %{hafta['en_kotu_gun']:+.1f}.")
        if hafta.get("tek_gun_payi") and hafta["tek_gun_payi"] > 0.75:
            L.append("  NOT: haftanın kazancının neredeyse tamamı tek bir güne sığdı.")

    L.append("")
    L.append("⚠ SAAT BİLGİN YOK: zirvenin/dibin günün hangi saatinde olduğu ölçülmedi."
             " 'öğleden sonra', 'sabah saatlerinde', 'kapanışa dakikalar kala' YAZMA.")
    dc = [q for q in (f.get("dikkat") or [])
          if q["kod"] not in {b["kod"] for b in f["buyukler"][:3]}]
    if dc:
        L.append("")
        L.append("DİKKAT ÇEKENLER — bunları raporun SONUNDA tek kısa cümlede, "
                 "etiketleriyle an (en fazla 3, yorum yapma, sadece 'dikkat çekenler' de):")
        for q in dc:
            L.append(f"  - {etiket(q['kod'])}: %{q['pct']:+.1f}, "
                     f"hacmi normalinin {q['rvol']:.1f} katı")

    yon = ("ARTI (yeşil)" if f["x"] > 0.15
           else "EKSİ (kırmızı)" if f["x"] < -0.15 else "YATAY")
    L.append(f"GÜNÜN YÖNÜ: {yon} — metinde ve soruda buna ters bir şey söyleme.")
    L.append(f"BUGÜNKÜ MANŞET (raporu bunun etrafında kur): {manset[2]} — {manset[3]}")
    if ikinci:
        L.append(f"İKİNCİ KONU (manşetten sonra SADECE bunu işle, başka başlık açma): "
                 f"{ikinci[3]}")
    if soru_tipi:
        L.append(f"KAPANIŞ SORUSU TİPİ (bu tipte sor): {soru_tipi}")
    return "\n".join(L)


# ─────────────────────── SAYI KAPISI ───────────────────────
_SAYI_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")

def izinli_sayilar(fis, f, hafta):
    """Fişte geçen her sayı + makul yuvarlamaları."""
    izin = set()
    def ekle(v):
        try: v = float(v)
        except Exception: return
        izin.add(round(v, 2)); izin.add(round(v, 1)); izin.add(round(abs(v), 2))
        izin.add(round(abs(v), 1)); izin.add(float(round(v))); izin.add(float(round(abs(v))))
    for m in _SAYI_RE.finditer(fis.replace(",", "")):
        ekle(m.group().replace(",", "."))
    for b in f["buyukler"]:
        for k in ("pct", "rvol", "tl_mn"):
            ekle(b[k])
        ekle(b["tl_mn"] / 1000.0)          # "15 milyar TL" gibi birim çevirisi
        # "hacmi normalinin 0.4 katı" = "normalinin %40'ı" — AYNI ŞEY.
        # 28 Ağu sabahı bu yüzden sağlam bir taslak reddedilip cılız şablona
        # düşüldü. Oranın yüzde hâli de izinli olmalı.
        ekle(b["rvol"] * 100.0)
        ekle(abs(b["rvol"] - 1.0) * 100.0)  # "normalinden %60 az/fazla"
    if hafta:
        for k in ("getiri", "en_iyi_gun", "en_kotu_gun", "yesil_gun", "kirmizi_gun"):
            ekle(hafta.get(k))
    # yüzdelerin tam sayıya yuvarlanmış hâli ("dörtte biri" yerine "%25")
    ekle(f["israr_pct"]); ekle(f["yesil"]); ekle(f["kirmizi"])
    return izin

def sayi_kapisi(metin, izin):
    """Metindeki HER sayı izinli listede olmalı. Değilse taslak reddedilir.

    Etiketler taramadan MUAF: "#bist100" içindeki 100 bir ölçüm değil, isimdir.
    (İlk sürümde bu yüzden sağlam taslaklar boşuna reddediliyordu.)
    """
    metin = re.sub(r"#\w+", " ", metin)
    sorunlu = []
    for m in _SAYI_RE.finditer(metin):
        ham = m.group().replace(",", ".")
        try: v = float(ham)
        except Exception: continue
        if any(abs(v - iz) <= 0.06 for iz in izin):
            continue
        sorunlu.append(ham)
    return sorunlu

_SAAT_RE = re.compile(
    "(öğleden sonra|öğleden önce|öğlen|sabah saatler|akşam saatler|"
    "kapanışa dakikalar|seans ortas|gün ortas|ilk yarım saat|son yarım saat|"
    "akşam üzeri|akşamüstü|öğle üzeri|öğleüstü|öğle vakti|"
    # saat kalıbı SADECE iki nokta üst üste. Nokta da kabul etseydik
    # "%2.58 katı" içindeki 2.58 saat sanılıp sağlam taslak reddedilirdi.
    "açılıştan hemen sonra|[0-9]{1,2}:[0-9]{2})", re.IGNORECASE)

def saat_kapisi(metin):
    """Günün hangi saatinde ne olduğunu BİLMİYORUZ; uyduran taslak reddedilir.

    Sadece talimatla engellenemedi (denemede iki kez 'öğleden sonra' yazdı).
    Sayı uydurmakla aynı sınıf: ölçülmemiş bir iddia.
    """
    return [m.group(0) for m in _SAAT_RE.finditer(metin)]


def sektor_kapisi(metin, f):
    """Sektör adının yanına YANLIŞ rakam yazılmış mı?

    'sanayi %-0.1, bankalar %+0.4' — iki rakam da gerçek ama yerleri ters.
    Sayı kapısı bunu göremez; adın hemen ardındaki yüzdeyi gerçekle kıyaslarız.
    """
    sek = f.get("sektor") or {}
    sorunlu = []
    for adi, gercek in sek.items():
        kok = adi[:5]                       # "banka" / "sanay" — ek almış hâlleri de yakalar
        for m in re.finditer(rf"{kok}\w*[^.;\n%]{{0,30}}%\s*([-+]?\d+[.,]?\d*)",
                             metin, re.IGNORECASE):
            try:
                v = float(m.group(1).replace(",", "."))
            except ValueError:
                continue
            # işaret ya da değer tutmuyorsa bildir (0.1 tolerans yuvarlama payı)
            if abs(abs(v) - abs(gercek)) > 0.15 or (v * gercek < 0 and abs(gercek) > 0.05):
                sorunlu.append(f"{adi}→%{v}")
    return sorunlu


def isim_kapisi(metin, f):
    """Verilmeyen hisse kodu/etiketi geçmiş mi?"""
    temiz = [q for q in f["buyukler"] if not q.get("artefakt")][:8]   # kapı geniş kalsın
    izinli = {b["kod"] for b in temiz}
    izinli |= {ad(b["kod"], b["ad"]).lower() for b in temiz}
    # DİKKAT ÇEKENLER BIST100'den gelir, BIST50 listesinde yoktur — onlar da izinli
    izinli |= {q["kod"] for q in (f.get("dikkat") or [])}
    sorunlu = []
    for h in re.findall(r"#([A-Za-zÇĞİÖŞÜçğıöşü0-9]{3,8})", metin):
        if h.upper() not in izinli and h.lower() not in ("bist100", "bist", "borsa"):
            sorunlu.append("#" + h)
    for t in re.findall(r"\b([A-ZÇĞİÖŞÜ]{4,6})\b", metin):
        if t not in izinli and t not in ("BIST", "TL", "XU100"):
            sorunlu.append(t)
    return sorunlu


# ─────────────────────── GEMINI YAZAR ───────────────────────
SES_REHBERI = """Sen bir borsa yorumcususun. "Küçük Yatırımcı Notları" adlı sabah notunu yazıyorsun.
Okuyucun küçük yatırımcı: teknik terimden değil, somut rakamdan ve hikâyeden anlar.

═══ EN ÖNEMLİ İKİ KURAL ═══
1. SOMUT OL ama AZ RAKAM KULLAN. Bulanık ifade ("hatırı sayılır", "epey hareketli")
   YASAK; rakamı yaz. AMA sana verilen gerçeklerin ÇOĞUNU KULLANMA — tüm notta
   en fazla 5-6 rakam geçsin, gerisini at. Bu bir liste değil, bir hikâye.
   Her paragraf TEK fikir + o fikri destekleyen en fazla 2 rakam.
   Rakamı yazdıysan ne anlama geldiğini de söyle; yalın istatistik sıralama YASAK.
2. YÜZDELERİ TEK ONDALIKLA yaz (%+2.0 · %-0.4). İki ondalık makine sesidir.
   Tam sayıya gereksiz ondalık ekleme: "%25.0" değil "%25".
   Büyük işlem hacmini "milyar TL" olarak yaz, "14,921 milyon TL" deme.
3. SANA VERİLEN LİSTENİN DIŞINDA HİÇBİR SAYI YAZMA. Listedeki sayıyı kullan,
   yenisini uydurma, kafadan hesaplama, yuvarlarken bile listeden sapma.

SES:
- Sakin, zeki, hafif espirili. Ukala değil, "neşeli abi" hiç değil.
- "biz" ve "sen" dili. ASLA "siz/sizin" deme — samimiyet kaçar.
- Kısa cümle, tek fikir. Uzun bağlaç zinciri yok.
- Endeksten söz ederken #bist100 etiketini kullan. "Ekran", "tabela", "piyasa
  genelinde" gibi bulanık özneler yerine doğrudan #bist100 yaz.
- Rapor SABAH okunuyor: ileriye bakarken "bugün" de. "Bir sonraki gün",
  "yarın", "önümüzdeki seans" deme — okuyucu için o gün BUGÜN.

YAPI:
- İlk satır: ☕️ Küçük Yatırımcı Notları · <alt başlık>
  Alt başlık = günün manşeti, 2-4 kelime. NORMAL YAZIM: sadece ilk harf ve özel
  isimler büyük. "Zirveden Geri Dönüş" YANLIŞ → "Zirveden geri dönüş" DOĞRU.
  "Halkbank'ın Günü" YANLIŞ → "Halkbank'ın günü" DOĞRU.
  Alt başlığa "Cuma kapanışı:" gibi önek EKLEME; sadece manşeti yaz.
- Sonra TEK CÜMLELİK giriş: günün hükmü. Rakam YOK, yorum VAR. Alt başlıkla aynı
  şeyi söyleme — alt başlık konuyu verir, giriş cümlesi tadı/çelişkiyi verir.
- Sonra 3-4 kısa paragraf. TOPLAM 130 KELİMEYİ GEÇME. Manşetin etrafında kur.
- HER RAPORDA MUTLAKA olacak üç şey (bunlar "başka konu" sayılmaz):
    (1) endeksin kapanışı,
    (2) PİYASA ÖZETİ: kaç hisse yükseldi / kaç hisse düştü,
    (3) SEKTÖR AYRIMI: fişte iki sektör arasında fark varsa tek cümleyle söyle
        (küçük fark bile olsa "para her yere aynı hızda gitmedi" cinsinden).
  Bunlar dışında sadece manşeti ve verilen ikinci konuyu işle.
- METİN GÖVDESİNDE EN FAZLA 2 HİSSE ANABİLİRSİN (manşetteki + bir tane daha).
  Kalabalık liste yapma.
- Fişte "DİKKAT ÇEKENLER" varsa, isim paragrafının SONUNA tek kısa cümle ekle ve
  o etiketleri say. Örnek biçim (kelimeleri değiştir): "... diğer dikkat çeken
  hisselerdi." Bu isimler hakkında YORUM YAPMA, sadece an.
- İkinci katman olarak şunlardan SADECE BİRİNİ seç ve anlat: ısrar oranı
  (yükselenlerin kaçı önceki gün de yükselmişti — düşükse "yer değiştirme",
  yüksekse "gerçek devam"), gün içi hikâye (açılış/dip/zirve ile kapanışın farkı),
  sektör ayrışması, hacim patlaması.
- Son: okuyucuya tek soru. KAPANIŞ SORUSU KURALLARI aşağıda — en çok
  hata ettiğin yer burası, dikkatle uygula.

KESİN YASAKLAR:
- SEBEP UYDURMA. Neden yükseldiğini/düştüğünü bilmiyorsun; sadece ne olduğunu anlat.
- ÇIKARIM UYDURMA. "Kazancın çoğu tek güne sığdı", "serinin tek eksi kapanışı",
  "üst üste üçüncü yeşil" gibi iddiaları ancak fişte AÇIKÇA yazıyorsa yazabilirsin.
  Fişte yoksa o cümleyi hiç kurma.
- ZAMAN KELİMESİ: geçmiş günü anlatırken fişteki GÜN kelimesini kullan
  ("dün" ya da "Cuma"). Geçmişi anlatırken "bugün" demek YASAK — "bugün" sadece
  okuyucuya dönük son cümlede, ileriye bakarken kullanılabilir.
- SAAT UYDURMA. Zirvenin ya da dibin günün hangi saatinde görüldüğünü BİLMİYORSUN.
  "öğleden sonra", "sabah saatlerinde", "kapanışa dakikalar kala" YASAK.
  Sadece sıralamayı söyleyebilirsin: açılış, gün içi dip/zirve, kapanış.
- AL/SAT TAVSİYESİ ve hedef fiyat YOK.
- Hisseleri METİNDE SADECE ETİKETİYLE an: "#halkb tavan yaptı", "#sise'de alıcı vardı".
  Şirketin uzun adını YAZMA ("Halkbank" değil "#halkb"). Etiket küçük harf.
  Endeksten söz ederken #bist100 kullanabilirsin.
- Hisseleri sana verilen listeden seç; listede olmayan hisseye değinme.
- Hisseleri nereden seçtiğini ANLATMA. "en büyük 50", "vadeli işlem gören",
  "en likit", "endekste yer alan" gibi ifadeler YASAK. Hisseyi gösterirsin,
  yöntemi anlatmazsın.
- Hava/rüzgâr/esinti/fırtına/deniz benzetmesi YASAK (klişe).
- "Bizim Enka", "bizim Halkbank" gibi sahiplenme YASAK.
- Klişe açılış yok: "Günaydın", "Yeni bir güne başlarken", "Piyasalar bugün".
- Tarih damgası yazma.

═══ KAPANIŞ SORUSU ═══
Bu soru okuyucunun cevap YAZMASI için var. Cevaplanamayan soru ölü sorudur.

YASAK:
- "Peki" ile başlama.
- TAHMİN SORMA. Piyasanın ne yapacağını sorma; okuyucu geleceği bilmiyor,
  cevap yazamaz, soru boşa gider.
- Yazdığın metindeki bir benzetmeyi/deyimi soruda kelime oyununa çevirme.
  Soru düz ve doğrudan olsun, espri taşımasın.
- Her güne yapıştırılabilecek genel soru sorma; soru o güne özgü olsun.

SANA AŞAĞIDA HANGİ TİPTE SORACAĞIN SÖYLENECEK. O tipte sor, cümleyi kendin kur:
  DAVRANIS  → okuyucunun kendi refleksini/ne yaptığını sor (2-3 somut seçenek sun).
  IKI_OKUMA → günün GERÇEK bulgusunu iki farklı şekilde yorumla, hangisi diye sor.
  OLCUT     → "bunu anlamak için neye bakarsın" türü; ölçüt sor.

KURALLAR:
- Soru günün SOMUT bulgusuna bağlı olsun. Başka bir güne yapıştırılabiliyorsa yanlıştır.
- Soru günün YÖNÜYLE ÇELİŞMESİN: eksi kapanan güne "dünkü yeşil" deme.
- En fazla 15 kelime. Sakin bir günse kısa tut ya da hiç sorma.
- Yukarıdaki tarif cümlelerini AYNEN KULLANMA; kendi cümleni yaz.

TAVAN NOTU: bir hisse tavan yaptıysa hacminin düşük olması ÇELİŞKİ DEĞİLDİR —
tavanda satıcı çekilir, işlem azalır. "Hacim zayıf, güvenilmez" diye yorumlama.

═══ YOĞUNLUK ÖRNEĞİ ═══
Aşağıda bir notun GÖVDE paragrafları var (alt başlığı ve giriş cümlesi bilerek
silindi — onları sen kuracaksın). İstenen somutluk ve kısalık ölçüsü budur.
Cümlelerini KOPYALAMA, benzetmelerini tekrarlama:

  #halkb tavan yaptı, #isctr 15 milyar TL'lik işlemle %+3.5 yükseldi, #vakbn'de
  hacim normalinin 2.6 katına çıktı. Banka endeksi %+2.0, sanayi %+1.0'de kaldı.

  Endeks gün boyunca bir kez bile eksiye düşmedi; %+1.8'i gördü, %+1.0 kapattı.

  Tek çatlak şurada: yükselen 311 hissenin yalnızca 79'u önceki gün de
  yükselmişti. Derinde güç var, yüzeyde yer değiştirme.

AÇILIŞ CÜMLESİ İÇİN YASAKLI KALIPLAR (bunları ve benzerlerini kullanma):
  "Yeşil bir gündü ama yeşilin kimden geldiği daha önemli"
  "Piyasalar dün yeşili gördü ama bu yeşili kimin boyadığı önemliydi"
  "Günün en kötü rakamı açılış saatindeydi"
Açılış cümleni sıfırdan kur; günün kendi çelişkisinden çıkar.
═══ ÖRNEK BİTTİ ═══"""


def gemini_yaz(fis, kacinilacak):
    """Fişten metni Gemini yazar. Başarısızsa None → şablona düşülür."""
    try:
        from google import genai
        from google.genai import types as gt
    except ImportError:
        return None, "google-genai kurulu değil"
    key = ""
    for p in (BASE / "telegram_config.json", Path.home() / "smr" / "telegram_config.json"):
        try:
            key = json.load(open(p, encoding="utf-8")).get("gemini_api_key", "")
            if key: break
        except Exception:
            continue
    if not key or key.startswith("BURAYA"):
        return None, "gemini anahtarı yok"

    kac = ""
    if kacinilacak:
        kac = ("\n\nSON GÜNLERDE ŞUNLARI YAZDIN — bunlara BENZEME, aynı cümleyi, "
               "aynı benzetmeyi, aynı alt başlığı kullanma:\n" +
               "\n".join(f"- {k}" for k in kacinilacak))

    prompt = f"{SES_REHBERI}{kac}\n\n─── BUGÜNÜN GERÇEKLERİ ───\n{fis}\n\n" \
             f"Şimdi sabah notunu yaz. Sadece notu yaz, başka açıklama ekleme.\n" \
             f"HATIRLATMA: 3-4 kısa paragraf, TOPLAM 130 KELİMEYİ GEÇME, en fazla " \
             f"5-6 rakam. Manşet + verilen ikinci konu dışında başka konu açma. " \
             f"SON SORU: tek cümle, EN FAZLA 15 KELİME, 'Peki' ile başlama."

    # 30 Haz 2026 dersi (memory/project_bulletin_model_fallback.md): 503/429'da
    # aynı modeli bekleme, SIRADAKİ modele geç (yük ve kota model bazlı ayrı havuz).
    # thinking_budget=0 → bu bir YAZI görevi; düşünme bütçesi metni kesiyordu.
    zincir = [("gemini-2.5-flash", 0), ("gemini-flash-latest", 3),
              ("gemini-2.5-flash-lite", 3), ("gemini-2.5-flash", 15)]
    client = genai.Client(api_key=key)
    son_hata = ""
    for model, bekle in zincir:
        if bekle:
            import time; time.sleep(bekle)
        try:
            r = client.models.generate_content(
                model=model, contents=prompt,
                config=gt.GenerateContentConfig(
                    max_output_tokens=1600, temperature=1.0,
                    thinking_config=gt.ThinkingConfig(thinking_budget=0),
                ),
            )
            t = (r.text or "").strip()
            if not t:
                son_hata = f"{model}: boş yanıt"; continue
            return t, model
        except Exception as e:
            son_hata = f"{model}: {type(e).__name__} {e}"[:160]
            continue
    return None, son_hata


# ─────────────────────── ŞABLON YAZAR (yedek) ───────────────────────
def sablon_yaz(f, hafta, manset, zaman, rng):
    """SON ÇARE yedeği: Gemini hiç metin veremezse.

    28 Ağu 2026: eski hâli utanç vericiydi — sektör satırı eşiğe takılıp
    düşüyordu, tek isim anıyordu, gün içi hikâye yoktu. Artık en kötü ihtimalde
    bile RAPOR gibi görünmeli: gün şekli + genişlik + sektör + iki isim +
    tavan/taban + soru.
    """
    sek = f.get("sektor") or {}
    z = zaman.capitalize()
    P = [f"☕️ Küçük Yatırımcı Notları · {manset[2]}", ""]
    P.append(rng.choice([
        "Ekranın rengiyle altında olan biten hep aynı şey değil.",
        "Kapanış rakamı günün tamamını anlatmıyor.",
        "Rakamlar bir şey söylüyor, tabelanın altı başka bir şey.",
    ]))

    # gün içi hikâye — kapanış tek başına yetmez
    gi = f["gun_ici"]
    if "boslukla_acildi" in gi:
        P.append(f"{z} endeks {yz(f['acilis'])} ile açtı, gün içinde {yz(f['dip'])} dibini "
                 f"gördü ve {yz(f['x'])} kapattı.")
    elif "dipten_topladi" in gi and "zirveden_verdi" in gi:
        P.append(f"{z} iki yönlü bir gündü: endeks {yz(f['zirve'])} zirvesini de "
                 f"{yz(f['dip'])} dibini de gördü, kapanış {yz(f['x'])} oldu.")
    elif "dipten_topladi" in gi:
        P.append(f"{z} endeks gün içinde {yz(f['dip'])}'e kadar düştü ama kapanışta "
                 f"{yz(f['x'])}'e toparladı.")
    elif "zirveden_verdi" in gi:
        P.append(f"{z} endeks {yz(f['zirve'])}'i gördü ama kazancını koruyamadı, "
                 f"{yz(f['x'])} kapattı.")
    elif "hic_eksiye_dusmedi" in gi:
        P.append(f"{z} endeks gün boyunca bir kez bile eksiye düşmedi ve "
                 f"{yz(f['x'])} kapattı.")
    else:
        P.append(f"{z} endeks {yz(f['x'])} kapattı.")

    # genişlik + asimetri
    P.append(f"{f['yesil']} hisse yükseldi, {f['kirmizi']} hisse düştü. "
             f"Yükselenlerin ortalaması {yz(f['yesil_ort'])}, düşenlerinki "
             f"{yz(f['kirmizi_ort'])}.")

    # sektör — eşik düşürüldü (0.4). Eskiden 1.0'dı ve çoğu gün satır düşüyordu.
    if len(sek) >= 2:
        lider = max(sek, key=lambda k: sek[k]); geri = min(sek, key=lambda k: sek[k])
        if sek[lider] - sek[geri] >= 0.4:
            P.append(f"{lider.capitalize()} {yz(sek[lider])}, {geri} {yz(sek[geri])} — "
                     f"para her yere aynı hızda gitmedi.")

    # isimler: en fazla iki tane, hacim notuyla
    temiz = [b for b in f["buyukler"] if not b.get("artefakt")]
    if temiz:
        b = temiz[0]
        tavan_mi = b["pct"] >= gv.TAVAN_ESIK
        P.append(f"{etiket(b['kod'])} {yz(b['pct'])} ile ayrıştı" +
                 (" ve tavan yaptı." if tavan_mi else
                  f"; hacmi normalinin {b['rvol']:.1f} katıydı."))
        if len(temiz) > 1:
            b2 = temiz[1]
            P.append(f"Diğer uçta {etiket(b2['kod'])} vardı: {yz(b2['pct'])}.")

    if f["tavan"] >= 3 or f["taban"] >= 3:
        P.append(f"Gün {f['tavan']} tavan, {f['taban']} tabanla kapandı.")

    if hafta and manset[0] == "hafta_basi":
        P.append(f"Geçen hafta endeks {yz(hafta['getiri'])} kapattı; "
                 f"{hafta['yesil_gun']} gün yeşil, {hafta['kirmizi_gun']} gün kırmızıydı.")

    P.append(rng.choice([
        "Böyle bir günde senin ilk refleksin ne olur: beklemek mi, azaltmak mı?",
        "Bunun devam ettiğini anlamak için sen neye bakarsın?",
        "Sen dün ne yaptın: izledin mi, dokundun mu?",
    ]))
    return "\n\n".join(p for p in P if p != "")


# ─────────────────────── RENDER + GÖNDER ───────────────────────
_ONEK_RE = re.compile(r"^\s*[^·]*kapanış[ıi]\s*[:–—-]\s*", re.IGNORECASE)

def metin_hijyen(t):
    """Telegram'a giden metnin biçim temizliği (14 Ağu 2026 tercihi korundu).

    Kalın yazı işareti, em-tire ve çift tırnak metni yapay gösteriyordu;
    endeks etiketi de tek biçimde küçük harf olsun.
    """
    return (t.replace("**", "")
             .replace('"', "'")
             .replace(" — ", "; ").replace(" – ", "; ")
             .replace("💬 ", "")
             .replace("#BIST100", "#bist100").replace("BIST 100", "#bist100")
             .replace("BIST100", "#bist100").replace("##", "#"))


def etiketle(metin, f):
    """Model düz şirket adı yazdıysa etikete çevirir (ek'i bozmadan).

    Talimat yetmeyebilir; biçim garantisi mekanik olmalı. "Halkbank'ın" →
    "#halkb'ın". Uzun adlar önce denenir ki "İş Bankası" içindeki parça yanlış
    eşleşmesin.
    """
    adlar = sorted(((ad(b["kod"], b["ad"]), b["kod"]) for b in f["buyukler"]
                    if not b.get("artefakt")),
                   key=lambda z: -len(z[0]))
    for isim, kod in adlar:
        if not isim or len(isim) < 3:
            continue
        metin = re.sub(rf"(?<![#\w]){re.escape(isim)}", etiket(kod), metin)
    return metin


_PEKI_RE = re.compile(r"^\s*(peki|e peki|ee peki)\s*[,:]?\s*", re.IGNORECASE)

def _soruyu_duzelt(govde):
    """Kapanış sorusundaki 'Peki,' dolgusunu keser.

    Prompt'ta yasak ama model ara sıra yine yazıyor; her raporun aynı kelimeyle
    bitmesi tekrar tikinin ta kendisi. Kesip ilk harfi büyütmek yeterli.
    """
    satirlar = govde.rstrip().split("\n")
    for i in range(len(satirlar) - 1, -1, -1):
        sat = satirlar[i].strip()
        if not sat:
            continue
        temiz = _PEKI_RE.sub("", sat)
        if temiz != sat and temiz:
            satirlar[i] = temiz[0].upper() + temiz[1:]
        break
    return "\n".join(satirlar)


def _basligi_duzelt(govde):
    """Alt başlıktan 'Cuma kapanışı:' türü öneki temizler.

    Model ara sıra manşetin önüne tarih/gün etiketi yapıştırıyor; başlık satırı
    tweet'in en pahalı yeri, orada tekrar bilgi istemiyoruz.
    """
    satirlar = govde.split("\n")
    for i, sat in enumerate(satirlar[:2]):
        if "·" in sat and "Küçük Yatırımcı" in sat:
            sol, _, sag = sat.partition("·")
            temiz = _ONEK_RE.sub("", sag).strip()
            if temiz:
                temiz = temiz[0].upper() + temiz[1:]      # "dipten..." → "Dipten..."
            satirlar[i] = f"{sol.strip()} · {temiz}"
            break
    return "\n".join(satirlar)


def render(govde, f, manset, kaynak, uyari=""):
    tag = " (TEST)" if TEST else ""
    alt = f"🗓 {f['tarih']} kapanışı · manşet: {manset[0]} · yazar: {kaynak}"
    u = f"\n⚠️ {uyari}" if uyari else ""
    return (f"📨 GÜNDEM TASLAK{tag} — düzenle & at\n{alt}\n"
            f"────────────────────\n{govde}\n"
            f"────────────────────\n"
            f"ℹ️ Sana özel taslak; sen atmadıkça kimse görmez. Rakamlar gerçek veridendir.{u}")

def _token():
    t = os.environ.get("TELEGRAM_BOT_TOKEN")
    if t:
        return t
    for p in (BASE / "telegram_config.json", Path.home() / "smr" / "telegram_config.json"):
        try:
            return json.load(open(p, encoding="utf-8"))["bot_token"]
        except Exception:
            continue
    return None

def tg_send(text):
    import requests
    token = _token()
    if not token:
        print("[gundem] token yok — gonderilemedi."); return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": ADMIN_CHAT_ID, "text": text,
                                "disable_web_page_preview": True}, timeout=25)
        return r.status_code == 200
    except Exception as e:
        print("[gundem] telegram fail:", e); return False


def main():
    f = gv.gun_fisi(TARIH)
    if not f:
        print("[gundem] veri yok — taslak uretilemedi."); return

    bugun = dt.date.today()
    if TARIH:
        # geçmişe dönük deneme: rapor bir SONRAKİ İŞ GÜNÜ sabahı çıkar.
        # Cuma kapanışının raporu Cumartesi değil PAZARTESİ gider (27 Ağu kararı).
        bugun = dt.date.fromisoformat(TARIH) + dt.timedelta(days=1)
        while bugun.weekday() >= 5:
            bugun += dt.timedelta(days=1)
    pazartesi = (bugun.weekday() == 0)
    cuma = (bugun.weekday() == 4)
    # Hafta karnesi SADECE Pazartesi. Cuma sabahı hafta daha bitmemiştir;
    # "geçen hafta böyleydi" demek yanlış olur (denemede model tam bunu yaptı).
    hafta = gv.hafta_karnesi(TARIH) if pazartesi else None
    zaman = zaman_kelimesi(f["tarih"], bugun)

    manset, adaylar = manset_sec(f, hafta, pazartesi, cuma)
    ikinci = ikinci_katman_sec(adaylar, manset)
    stipi = soru_tipi_sec()
    fis = fis_metni(f, hafta, manset, zaman, ikinci, stipi)
    rng = random.Random()

    govde, kaynak, uyari = None, "sablon", ""
    if not SABLON_ZORLA:
        kac = []
        for k in gecmis_oku()[-5:]:
            if k.get("alt_baslik"): kac.append(k["alt_baslik"])
            if k.get("giris"): kac.append(k["giris"])
            if k.get("soru"): kac.append(k["soru"])
        metin, model = gemini_yaz(fis, kac)
        # Kapı reddi çoğu zaman tek bir kaçak rakamdan olur; hemen şablona
        # düşmek yerine hatayı söyleyip BİR KEZ daha yazdırırız (denemede
        # 5 günün 1'i boşuna şablona düşmüştü).
        for _deneme in range(2):
            if not metin:
                break
            _izin = izinli_sayilar(fis, f, hafta)
            _kotu = (sayi_kapisi(metin, _izin) + isim_kapisi(metin, f)
                     + saat_kapisi(metin) + sektor_kapisi(metin, f))
            if not _kotu:
                break
            print("[gundem] ilk taslak reddedildi, yeniden yazdiriliyor:", _kotu[:5])
            metin, model = gemini_yaz(
                fis + f"\n\n⚠ ÖNCEKİ DENEMEN REDDEDİLDİ: {_kotu[:5]} — bunlar "
                      f"fişte YOK. Sadece fişteki rakamları kullan, saat yazma.", kac)
        if metin:
            izin = izinli_sayilar(fis, f, hafta)
            kotu_sayi = sayi_kapisi(metin, izin)
            kotu_isim = isim_kapisi(metin, f)
            kotu_saat = saat_kapisi(metin)
            kotu_isim = kotu_isim + sektor_kapisi(metin, f)
            # ⚠ 28 Ağu dersi: taslağı ÇÖPE ATMAK yanlış. Şablon yedeği AI metninin
            # yanına bile yaklaşmıyor; kullanıcı zaten atmadan önce okuyup
            # düzenliyor. Doğru davranış: metni KORU, şüpheliyi İŞARETLE.
            govde, kaynak = metin, model
            if kotu_sayi or kotu_isim or kotu_saat:
                parcalar = []
                if kotu_sayi: parcalar.append(f"doğrula → {', '.join(kotu_sayi[:5])}")
                if kotu_isim: parcalar.append(f"isim → {', '.join(kotu_isim[:3])}")
                if kotu_saat: parcalar.append(f"saat → {', '.join(kotu_saat[:3])}")
                uyari = "ŞÜPHELİ: " + " · ".join(parcalar) + "  (metin korundu, kontrol et)"
                print("[gundem] kapi isaretledi:", kotu_sayi[:8], kotu_isim[:5], kotu_saat[:3])
        else:
            print("[gundem] gemini yok/hata:", model)
    if govde is None:
        govde = sablon_yaz(f, hafta, manset, zaman, rng)

    govde = metin_hijyen(_soruyu_duzelt(_basligi_duzelt(etiketle(govde, f))))
    satirlar = [s.strip() for s in govde.split("\n") if s.strip()]
    kayit = {"tarih": f["tarih"], "manset_id": manset[0],
             "alt_baslik": satirlar[0] if satirlar else "",
             "giris": satirlar[1] if len(satirlar) > 1 else "", "kaynak": kaynak,
             "ikinci_id": ikinci[0] if ikinci else None,
             "soru": satirlar[-1] if satirlar else "", "soru_tipi": stipi}

    msg = render(govde, f, manset, kaynak, uyari)
    if DRY or (not TEST and not _token()):
        print(msg)
        print("\n─── seçilen manşet:", manset[0], "| adaylar:",
              ", ".join(f"{m[0]}({m[1]:.0f})" for m in adaylar[:6]))
        return
    if tg_send(msg):
        gecmis_yaz(kayit)
        print("[gundem] taslak gonderildi.", kaynak)
    else:
        print("[gundem] gonderilemedi.")


if __name__ == "__main__":
    main()
