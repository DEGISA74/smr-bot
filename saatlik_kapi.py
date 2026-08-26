# -*- coding: utf-8 -*-
"""
saatlik_kapi.py — SAATLİK VERİ KAPISI (19 Ağu 2026)

NEDEN VAR:
Saatlik depo (veriler_saatlik/) 580 dosya taşıyor ama bunların yalnız ~218'i
güncel. 313 dosya 3 Ağustos'ta donmuş durumda — bunlar arıza değil, KAPSAM DIŞI:
saatlik fetcher bilerek yalnız en likit 250 hisseyi + 3 endeksi besliyor
(run_saatlik.sh · intraday_4s.likit_liste). Sorun, okuyan tarafın bunu bilmemesi:
kapsam dışı ölü bir dosya, geçerli veri gibi görünüyordu.

İkinci sorun: 18 Ağu'da taze dosyaların son barı 13:30'da durmuştu. "Bugünün
tarihi var mı?" diye bakan kod, 17:45'te 13:30'da donmuş yarım günü "bugünün
verisi" sayıyordu. Ekranda "saatlik teyit" yazarken arkada yarım gün vardı.

BU MODÜL TEK YETKİLİDİR. Saatlik parquet okuyan her yer önce buraya sorar:
    durum = saatlik_durum("THYAO")
    if not durum["ok"]:  -> saatlik hesap YAPILMAZ (sessiz günlüğe düşme YOK,
                            çağıran taraf durum["not"] cümlesini ekranda gösterir)

Durum kodları:
    TAMAM       → o günün beklenen barları yerinde, saatlik hesap serbest
    SEANS_ONCESI→ günün ilk barı henüz kapanmadı (eksiklik değil, erken)
    DEPO_YOK    → bu makinede saatlik depo yok (VPS: saatlik fetcher lokalde koşar)
    KARANTINA   → saatlik geçmiş günlükle uzlaşmıyor (bölünme sonrası bazlanmamış)
    YARIM       → bar var ama geride (13:30'da donmuş gibi) → kullanma
    BAYAT       → o güne ait hiç bar yok (dosya eski bir günde duruyor)
    KAPSAM_DISI → hisse 250'lik saatlik listede değil, hiç güncellenmiyor
    YOK         → dosya yok
    BOZUK       → dosya okunamadı / boş

CLI:
    python saatlik_kapi.py            → tüm deponun karnesi
    python saatlik_kapi.py THYAO ...  → tek tek durum
"""
import os
import json
import glob
from datetime import datetime

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DEPO = os.environ.get("SMR_HOURLY_DIR", os.path.join(BASE, "veriler_saatlik"))
KAPSAM_DOSYA = os.path.join(DEPO, ".kapsam.json")

# run_saatlik.sh ile AYNI kapsam: --liste 250 + ayrı çekilen 3 endeks.
# Buradaki sayı fetcher'daki ile aynı kalmalı; değişirse ikisi birlikte değişir.
KAPSAM_N = int(os.environ.get("SMR_HOURLY_N", "250"))
KAPSAM_ENDEKS = {"XU100", "XU030", "XBANK"}

# Yahoo saatlik barı damgasından ~1 saat sonra kapanır, yayına birkaç dakika
# sonra düşer. Bu tolerans olmadan her tur "yarım" görünür.
GECIKME_TOLERANS_DK = int(os.environ.get("SMR_HOURLY_GRACE", "20"))

_SEANS_BASI = "09:30"          # Yahoo'nun BIST saatlik damga ızgarası buradan başlar
_kapsam_memo = {"gun": None, "set": None}
_durum_memo = {}               # {(sym, gun): (epoch, durum)}


# ------------------------------------------------------------------ kapsam
_depo_memo = {}


def _depo_var():
    """Bu makinede saatlik depo var mı? (60 sn'de bir bakılır — her çağrıda değil)"""
    import time as _t
    _now = _t.time()
    if _depo_memo.get("t", 0) + 60 > _now:
        return _depo_memo["v"]
    try:
        v = os.path.isdir(DEPO) and bool(glob.glob(os.path.join(DEPO, "*.IS_1h.parquet")))
    except Exception:
        v = False
    _depo_memo["t"], _depo_memo["v"] = _now, v
    return v


_karantina_memo = {}


def _karantina():
    """Uzlaşma kapısının ürettiği karantina listesi (60 sn'de bir okunur).

    Dosya yoksa boş döner — yani karantina kapısı sessizce KAPALI olur, ama
    bu bilinçli: liste üretilmemişse kimseyi haksız yere engellemeyiz. Listeyi
    tazelemek `saatlik_uzlasma.py --yaz` işidir (saatlik turda koşar).
    """
    import time as _t
    _now = _t.time()
    if _karantina_memo.get("t", 0) + 60 > _now:
        return _karantina_memo["v"]
    try:
        from saatlik_uzlasma import karantina_oku
        v = karantina_oku() or {}
    except Exception:
        v = {}
    _karantina_memo["t"], _karantina_memo["v"] = _now, v
    return v


def _likit_250():
    """Saatlik fetcher'ın kullandığı listeyi AYNI fonksiyondan üretir (drift yok)."""
    from intraday_4s import likit_liste
    return set(likit_liste(KAPSAM_N))


def kapsam_listesi(force=False):
    """Saatlik depoda GÜNCEL TUTULAN sembol kümesi (250 likit + 3 endeks).

    Günde bir hesaplanır, `.kapsam.json`'a yazılır. Liste dışındaki dosya
    depoda dursa bile veri sayılmaz.
    """
    bugun = datetime.now().strftime("%Y-%m-%d")
    if not force and _kapsam_memo["gun"] == bugun and _kapsam_memo["set"]:
        return _kapsam_memo["set"]
    kume = None
    if not force and os.path.exists(KAPSAM_DOSYA):
        try:
            with open(KAPSAM_DOSYA, "r", encoding="utf-8") as f:
                _j = json.load(f)
            if _j.get("gun") == bugun and _j.get("liste"):
                kume = set(_j["liste"])
        except Exception:
            kume = None
    if kume is None:
        try:
            kume = _likit_250() | KAPSAM_ENDEKS
        except Exception:
            # Liste üretilemiyorsa kapıyı ardına kadar açmak yanlış olur; ama tüm
            # sistemi de durdurmamak gerek → dünkü kayıtlı liste varsa o kullanılır.
            try:
                with open(KAPSAM_DOSYA, "r", encoding="utf-8") as f:
                    kume = set(json.load(f).get("liste") or [])
            except Exception:
                kume = set()
        if kume:
            try:
                os.makedirs(DEPO, exist_ok=True)
                with open(KAPSAM_DOSYA, "w", encoding="utf-8") as f:
                    json.dump({"gun": bugun, "liste": sorted(kume)}, f)
            except Exception:
                pass
    kume = kume | KAPSAM_ENDEKS
    _kapsam_memo["gun"], _kapsam_memo["set"] = bugun, kume
    return kume


# ------------------------------------------------------------- beklenen bar
def _seans_kapanis(gun):
    try:
        from bist_calendar import get_session_hours
        _s = get_session_hours(gun)
        return _s[1] if _s else None
    except Exception:
        return "18:00"


def beklenen_damgalar(gun):
    """O günün seansında oluşması gereken saatlik bar damgaları ('09:30' ...)."""
    kapanis = _seans_kapanis(gun)
    if not kapanis:
        return []            # kapalı gün
    _kh, _km = (int(x) for x in kapanis.split(":"))
    _kap = _kh * 60 + _km
    _bh, _bm = (int(x) for x in _SEANS_BASI.split(":"))
    out, t = [], _bh * 60 + _bm
    while t < _kap:
        out.append("%02d:%02d" % (t // 60, t % 60))
        t += 60
    return out


def beklenen_bar_sayisi(gun, simdi=None):
    """Şu ana kadar YAYINLANMIŞ olması gereken bar sayısı.

    Geçmiş gün / seans bitmişse günün tamamı beklenir. Seans sürüyorsa yalnız
    kapanmış barlar beklenir (bar damgasından 1 saat + gecikme toleransı sonra).
    """
    damgalar = beklenen_damgalar(gun)
    if not damgalar:
        return 0
    simdi = simdi or datetime.now()
    if gun < simdi.date():
        return len(damgalar)
    _now = simdi.hour * 60 + simdi.minute
    n = 0
    for d in damgalar:
        _h, _m = (int(x) for x in d.split(":"))
        if _h * 60 + _m + 60 + GECIKME_TOLERANS_DK <= _now:
            n += 1
    return n


# --------------------------------------------------------------- ana kapı
def _sym(ticker):
    return str(ticker).upper().replace(".IS", "").strip()


def _yol(ticker):
    return os.path.join(DEPO, "%s.IS_1h.parquet" % _sym(ticker))


def _oku_ham(yol):
    d = pd.read_parquet(yol)
    ix = pd.to_datetime(d.index)
    if getattr(ix, "tz", None) is not None:
        ix = ix.tz_convert("Europe/Istanbul").tz_localize(None)
    return d.set_index(ix).sort_index()


def saatlik_durum(ticker, for_date=None, simdi=None, ttl=120):
    """Bu hissenin saatlik verisi ŞU AN kullanılabilir mi?

    Döner: {'ok','durum','not','sym','son_bar','bar','beklenen','son_gun'}
    'not' = ekranda gösterilebilecek tek cümlelik Türkçe açıklama.
    """
    import time as _t
    sym = _sym(ticker)
    simdi = simdi or datetime.now()
    gun = for_date or simdi.date()
    key = (sym, str(gun))
    _m = _durum_memo.get(key)
    if ttl and _m and (_t.time() - _m[0]) < ttl:
        return _m[1]

    def _ret(ok, durum, not_, **kw):
        d = {"ok": ok, "durum": durum, "not": not_, "sym": sym,
             "son_bar": kw.get("son_bar"), "bar": kw.get("bar", 0),
             "beklenen": kw.get("beklenen", 0), "son_gun": kw.get("son_gun")}
        _durum_memo[key] = (_t.time(), d)
        return d

    # VPS'te saatlik depo HİÇ YOK (saatlik fetcher yalnız lokalde koşuyor).
    # Bu durumda "hisse listede yok" demek yanıltıcı olur — sebep hisse değil,
    # sunucu. Gerekçe doğru yazılsın diye ayrı durum (19 Ağu 2026).
    if not _depo_var():
        return _ret(False, "DEPO_YOK", "bu sunucuda saatlik veri deposu yok")
    # KARANTINA (tam gün uzlaşma kapısı, 19 Ağu 2026): saatlik geçmişi günlükle
    # uzlaşmayan hisse — bölünme/bedelsiz sonrası saatlik depo yeniden bazlanmamış.
    # Bugünkü fiyatı doğru olsa bile geçmişi bozuk olduğu için saatliğe dayalı
    # hiçbir hesap yapılmaz. Liste: saatlik_uzlasma.py üretir.
    _kar = _karantina().get(sym)
    if _kar:
        return _ret(False, "KARANTINA",
                    "saatlik geçmiş günlükle uzlaşmıyor (%%%.0f fark, %s) — onarılana kadar kapalı"
                    % (float(_kar.get("sapma_pct") or 0), _kar.get("tip") or "?"))
    if sym not in kapsam_listesi():
        return _ret(False, "KAPSAM_DISI",
                    "bu hisse saatlik listede yok (yalnız en likit 250 hisse güncelleniyor)")
    yol = _yol(sym)
    if not os.path.exists(yol):
        return _ret(False, "YOK", "saatlik dosya yok")
    try:
        h = _oku_ham(yol)
    except Exception:
        return _ret(False, "BOZUK", "saatlik dosya okunamadı")
    if h is None or h.empty:
        return _ret(False, "BOZUK", "saatlik dosya boş")

    son_gun = h.index[-1].date()
    beklenen = beklenen_bar_sayisi(gun, simdi)
    gun_ix = [t for t in h.index if t.date() == gun]
    bar = len(gun_ix)
    son_bar = max(gun_ix).strftime("%H:%M") if gun_ix else None

    if beklenen == 0:
        # Veri eksik değil — günün ilk barı henüz KAPANMADI. Ayrı durum kodu,
        # yoksa sabah 09:15'te tüm depo "yarım" görünüp karneyi yanıltıyor.
        return _ret(False, "SEANS_ONCESI", "seansın ilk saatlik barı henüz kapanmadı",
                    bar=bar, beklenen=0, son_gun=son_gun, son_bar=son_bar)
    if bar == 0:
        _fark = (gun - son_gun).days
        return _ret(False, "BAYAT",
                    "saatlik veri %s tarihinde duruyor (%d gün geride)"
                    % (son_gun.strftime("%d.%m"), _fark),
                    bar=0, beklenen=beklenen, son_gun=son_gun)
    if bar < beklenen:
        return _ret(False, "YARIM",
                    "saatlik veri %s'da duruyor (%d bar beklenirken %d bar var)"
                    % (son_bar, beklenen, bar),
                    bar=bar, beklenen=beklenen, son_gun=son_gun, son_bar=son_bar)
    return _ret(True, "TAMAM", "saatlik veri %s itibarıyla tamam" % son_bar,
                bar=bar, beklenen=beklenen, son_gun=son_gun, son_bar=son_bar)


def saatlik_oku(ticker, for_date=None, simdi=None):
    """Kapıdan geçerse (o günün barları, durum); geçmezse (None, durum)."""
    d = saatlik_durum(ticker, for_date=for_date, simdi=simdi)
    if not d["ok"]:
        return None, d
    gun = for_date or (simdi or datetime.now()).date()
    try:
        h = _oku_ham(_yol(ticker))
        return h[[t.date() == gun for t in h.index]], d
    except Exception:
        return None, d


def kapsamda_mi(ticker):
    return _sym(ticker) in kapsam_listesi()


# ------------------------------------------------------------------- karne
def karne(simdi=None):
    simdi = simdi or datetime.now()
    kapsam = kapsam_listesi()
    sayac, ornek = {}, {}
    for f in glob.glob(os.path.join(DEPO, "*.IS_1h.parquet")):
        sym = os.path.basename(f).replace(".IS_1h.parquet", "")
        d = saatlik_durum(sym, simdi=simdi, ttl=0)
        sayac[d["durum"]] = sayac.get(d["durum"], 0) + 1
        ornek.setdefault(d["durum"], []).append(sym)
    return {"kapsam": len(kapsam), "sayac": sayac, "ornek": ornek}


if __name__ == "__main__":
    import sys
    arg = sys.argv[1:]
    if arg:
        for a in arg:
            d = saatlik_durum(a, ttl=0)
            print("%-8s %-12s ok=%-5s bar=%s/%s son=%s | %s"
                  % (d["sym"], d["durum"], d["ok"], d["bar"], d["beklenen"],
                     d["son_bar"], d["not"]))
    else:
        k = karne()
        print("saatlik kapsam listesi: %d sembol" % k["kapsam"])
        print("depodaki dosya durumlari (%s):" % datetime.now().strftime("%d.%m %H:%M"))
        for durum, n in sorted(k["sayac"].items(), key=lambda x: -x[1]):
            print("  %-12s %4d   or: %s" % (durum, n, ", ".join(k["ornek"][durum][:5])))
