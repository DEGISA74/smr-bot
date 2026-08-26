#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ÇİZGİ KIRILIM ALARMI — seans bitmeden Telegram (20 Ağu 2026).

Ne yapar: 17:30'da elimizdeki parquet deposuyla YILDIZ PAZAR hisselerini tarar,
üçgen/kama sınır çizgisi kırılanları ve kırılıma yaklaşanları ADMIN'e yollar.
Hisseye özel SAATLİK OHLCV'yi almaya çalışır (saatlik_kapi tek yetkili — kapıdan
geçmezse "saatlik veri yok" der, uydurmaz).

Kullanım:
  python cizgi_alarm.py            # gerçek gönderim (ADMIN)
  python cizgi_alarm.py --test     # aynı şey, mesaj başına [TEST] etiketi
  python cizgi_alarm.py --kuru     # hiçbir şey göndermez, ekrana yazar

Cron (VPS, hafta içi 17:30 TR = 14:30 UTC):
  30 14 * * 1-5 cd /home/wm11tr/smr && flock -n /tmp/cizgi_alarm.lock \
      ./venv/bin/python cizgi_alarm.py >> logs/cizgi_alarm.log 2>&1

⚠️ Bu taramanın GETİRİSİ ÖLÇÜLMEDİ. Mesaj yalnız "yapı kırıldı mı" der.
Detay: memory/project_cizgi_yapi.md
"""
from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime

import pandas as pd
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import cizgi_yapi as cy  # noqa: E402

VERI = os.path.join(BASE, "veriler")
PAZAR_DOSYA = os.path.join(BASE, "kapanis_oncesi_resmi_durum.json")
STATE = os.path.join(BASE, "cizgi_alarm_state.json")
ADMIN_ID = "1034525990"
TEST = "--test" in sys.argv
KURU = "--kuru" in sys.argv

# Hangi aşamalar mesaj üretir
KIRDI = ("KIRILIM_DOĞRULANDI", "KIRILIM_ADAYI", "YENİDEN_TEST")
YAKIN = ("YAKIN",)
HACIM_DETAY_ESIK = 1.50      # bu katın üstünde lot sayısı + karşılaştırma eklenir
# Kırılım kaç gün TAZE sayılır? Bunsuz ilk çalışmada geçen ayın kırılımları da
# "yeni" görünüp toplu mesaj yağmuru olur (20 Ağu 2026 kuru çalışma dersi).
TAZE_GUN = 3
# Derin veri (her kaynaktan OHLCV) hangi hisseler icin toplanir:
# kirilim basladiysa VEYA cizgiye bu kadar veya daha az kaldiysa (%).
DERIN_MESAFE = 1.0


# ───────────────────────────── yardımcılar ──────────────────────────────
def _token():
    for p in ("/home/wm11tr/weektweet/.env", "/home/wm11tr/insider/.env"):
        try:
            for satir in open(p, encoding="utf-8"):
                if satir.startswith("TELEGRAM_BOT_TOKEN="):
                    return satir.split("=", 1)[1].strip()
        except Exception:
            pass
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def tg_gonder(metin: str) -> bool:
    if KURU:
        print("--- KURU ÇALIŞMA, gönderilmedi ---")
        print(metin)
        return True
    tok = _token()
    if not tok:
        print("token yok — gönderilemedi")
        return False
    try:
        r = requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % tok,
            json={"chat_id": ADMIN_ID, "text": metin,
                  "disable_web_page_preview": True},
            timeout=25,
        )
        if r.status_code != 200:
            print("telegram HTTP", r.status_code, r.text[:200])
            return False
        return True
    except Exception as exc:
        print("telegram hata:", exc)
        return False


def yildiz_pazar() -> set:
    """Resmi BİST pazar tablosundan Yıldız Pazar listesi."""
    try:
        d = json.load(open(PAZAR_DOSYA, encoding="utf-8"))
        return {k for k, v in (d.get("markets") or {}).items() if v == "YILDIZ"}
    except Exception as exc:
        print("pazar dosyası okunamadı:", exc)
        return set()


def _sayi(v) -> str:
    try:
        v = float(v)
    except Exception:
        return "—"
    return ("%.2f" % v) if v < 1000 else ("{:,.0f}".format(v).replace(",", "."))


def _lot(v) -> str:
    try:
        v = float(v)
    except Exception:
        return "—"
    if v >= 1_000_000:
        return "%.1f milyon" % (v / 1_000_000)
    return "%.0f bin" % (v / 1_000)


def gunluk_hacim(df) -> tuple:
    """(bugünün hacmi, önceki 20 günün ortalaması, oran)"""
    try:
        bugun = float(df["Volume"].iloc[-1])
        ort = float(df["Volume"].tail(21).head(20).mean())
        return bugun, ort, (bugun / ort if ort > 0 else 0.0)
    except Exception:
        return 0.0, 0.0, 0.0


def derin_mi(r: dict) -> bool:
    """Derin veri toplanacak mı? Kullanıcı kuralı (20 Ağu 2026): kırılım
    başladıysa VEYA çizgiye %1 veya daha az kaldıysa — o hisseye özel, o ana
    kadar ulaşılabilen HER kaynaktan OHLCV toplanır."""
    if r["stage"] in KIRDI:
        return True
    m = r.get("mesafe")
    return m is not None and m <= DERIN_MESAFE


def _yahoo_intraday(sembol: str, aralik: str):
    """Yahoo gün içi barları. Döner: (df|None, not_metni)."""
    try:
        import yfinance as yf
    except Exception:
        return None, "yfinance yok"
    try:
        h = yf.Ticker(sembol).history(period="2d", interval=aralik, auto_adjust=False)
        if h is None or h.empty:
            return None, "boş döndü"
        bugun = pd.Timestamp.now().date()
        h = h[[t.date() == bugun for t in h.index]]
        if h.empty:
            return None, "bugüne ait bar yok"
        return h, ""
    except Exception as exc:
        return None, str(exc)[:60]


def _isyatirim_bugun(sembol: str):
    """İş Yatırım'dan bugünün kesin BIST hacmi. Döner: (hacim|None, not)."""
    if not sembol.endswith(".IS"):
        return None, "BIST dışı"
    try:
        from isyatirim_gateway import robust_isyatirim
    except Exception:
        return None, "kapı modülü yok"
    try:
        # allow_stale=True: kapı katı modda BUGÜNÜ içermeyen veriyi tamamen
        # eliyordu ("veri gelmedi"). Tazeliği aşağıda KENDİMİZ kontrol edip
        # "en son 20.08 verisi var" diye açıkça söylemek daha dürüst.
        df, kaynak = robust_isyatirim(sembol, period_days=5, tries=1,
                                      allow_stale=True, priority="alarm",
                                      max_wait=20.0)
        if df is None or getattr(df, "empty", True):
            return None, "veri gelmedi"
        bugun = pd.Timestamp.now().date()
        son = df.iloc[-1]
        son_tarih = pd.Timestamp(df.index[-1]).date()
        if son_tarih != bugun:
            return None, "en son %s verisi var" % son_tarih.strftime("%d.%m")
        for kolon in ("Volume", "volume", "HACIM"):
            if kolon in df.columns:
                return float(son[kolon]), ("canlı" if kaynak == "canli" else str(kaynak))
        return None, "hacim sütunu yok"
    except Exception as exc:
        return None, str(exc)[:60]


def derin_veri_blogu(r: dict, depo_df) -> str:
    """O ana kadar ulaşılabilen HER kaynaktan OHLCV + alınamayanların gerekçesi."""
    sembol, kisa = r["sembol"], r["kisa"]
    simdi = datetime.now().strftime("%H:%M")
    satir = ["📊 VERİ (%s itibarıyla)" % simdi]
    eksik = []

    # 1) Depo — günlük mum (fetcher 5 dakikada bir tazeliyor)
    try:
        s = depo_df.iloc[-1]
        yazilma = ""
        try:
            yol = os.path.join(VERI, sembol + "_1d.parquet")
            yazilma = " · dosya %s'de yazıldı" % datetime.fromtimestamp(
                os.path.getmtime(yol)).strftime("%H:%M")
        except Exception:
            pass
        satir.append("• Günlük mum (depo%s): açılış %s · yüksek %s · düşük %s · "
                     "son %s · %s lot"
                     % (yazilma, _sayi(s["Open"]), _sayi(s["High"]),
                        _sayi(s["Low"]), _sayi(s["Close"]), _lot(s["Volume"])))
    except Exception:
        eksik.append("⚠ Günlük mum depodan okunamadı.")

    # 2) Yahoo saatlik
    h60, not60 = _yahoo_intraday(sembol, "60m")
    if h60 is not None:
        try:
            ilk = float(h60["Open"].iloc[0]); son_f = float(h60["Close"].iloc[-1])
            deg = ((son_f - ilk) / ilk * 100) if ilk else 0.0
            satir.append("• Saatlik (Yahoo): %d bar, son %s · gün içi %+.1f%% · %s lot"
                         % (len(h60), str(h60.index[-1])[11:16], deg,
                            _lot(float(h60["Volume"].sum()))))
            bitis = str(h60.index[-1])[11:16]
            if bitis < "17:00":
                eksik.append("⚠ Saatlik veri yalnız %s'ye kadar alınabildi." % bitis)
        except Exception:
            eksik.append("⚠ Saatlik veri işlenemedi.")
    else:
        eksik.append("⚠ Saatlik veri alınamadı (Yahoo: %s)." % not60)

    # 3) Yahoo 5 dakikalık — en taze fiyat damgası
    h5, not5 = _yahoo_intraday(sembol, "5m")
    if h5 is not None:
        try:
            satir.append("• Son işlem (Yahoo 5dk): %s · damga %s"
                         % (_sayi(float(h5["Close"].iloc[-1])),
                            str(h5.index[-1])[11:16]))
        except Exception:
            pass
    else:
        eksik.append("⚠ Dakikalık fiyat alınamadı (%s)." % not5)

    # 4) İş Yatırım — BIST kesin hacmi
    isy, isy_not = _isyatirim_bugun(sembol)
    if isy is not None:
        satir.append("• İş Yatırım hacmi: %s lot (BIST kesin hacim, %s)"
                     % (_lot(isy), isy_not))
    else:
        eksik.append("⚠ Hacim verisi İş Yatırım'dan alınamadı (%s)." % isy_not)

    # 5) Yerel saatlik depo (saatlik_kapi TEK YETKİLİ). Yahoo saatliğiyle
    # karışmasın diye ayrı etiket: bu, bizim kendi saatlik arşivimiz.
    sk_satir = saatlik_satir(kisa).replace("🕐 Saatlik veri", "Yerel saatlik arşiv") \
                                  .replace("🕐 Saatlik (", "Yerel saatlik arşiv (")
    if sk_satir.startswith("Yerel saatlik arşiv ("):
        satir.append("• " + sk_satir)
    else:
        eksik.append("⚠ " + sk_satir)

    return "\n".join(satir + ([""] + eksik if eksik else []))


def saatlik_satir(ticker: str) -> str:
    """Hisseye özel saatlik OHLCV. saatlik_kapi TEK YETKİLİ — geçmezse söyler."""
    try:
        import saatlik_kapi as sk
    except Exception:
        return "🕐 Saatlik veri okunamadı (kapı modülü yok)."
    try:
        df, durum = sk.saatlik_oku(ticker)
    except Exception as exc:
        return "🕐 Saatlik veri okunamadı (%s)." % exc
    if df is None or getattr(df, "empty", True):
        neden = (durum or {}).get("durum", "YOK")
        if neden == "KAPSAM_DISI":
            return "🕐 Saatlik veri yok — bu hisse saatlik listede değil."
        if neden == "BAYAT":
            return "🕐 Saatlik veri bayat, kullanılmadı."
        return "🕐 Saatlik veri yok (%s)." % neden
    try:
        son = df.iloc[-1]
        saat = str(df.index[-1])[11:16]
        gun_hacim = float(df["Volume"].sum())
        ilk_acilis = float(df["Open"].iloc[0])
        kapanis = float(son["Close"])
        yon = "+" if kapanis >= ilk_acilis else ""
        deg = (kapanis - ilk_acilis) / ilk_acilis * 100 if ilk_acilis else 0.0
        return ("🕐 Saatlik (%d bar, son %s): kapanış %s · gün içi %s%.1f%% · "
                "saatlik toplam hacim %s lot"
                % (len(df), saat, _sayi(kapanis), yon, deg, _lot(gun_hacim)))
    except Exception:
        return "🕐 Saatlik veri okunamadı."


# ───────────────────────────── mesaj kurma ──────────────────────────────
def mesaj_kirilim(r: dict, df) -> str:
    bugun_h, ort_h, oran = gunluk_hacim(df)
    yukari = r["yon"] == "bullish"
    ok = "" if yukari else "  ⚠ AŞAĞI"
    yon_kelime = "yukarı" if yukari else "aşağı"
    fark = ((r["fiyat"] - r["tetik"]) / r["tetik"] * 100) if r["tetik"] else 0.0
    taraf = "üstünde" if fark >= 0 else "altında"
    gecersiz_yon = "altı" if yukari else "üstü"

    satir = ["📐 ÇİZGİ KIRILIMI — %s%s" % (r["kisa"], ok), ""]
    satir.append("%s %s kırıldı." % (r["ad"], yon_kelime))
    satir.append("%d günlük yapı · %s'de başladı · %d temas noktası"
                 % (r["bar"], r["bas_tarih"], r["temas"]))
    satir.append("")
    satir.append("Kırılım çizgisi: %s" % _sayi(r["tetik"]))
    satir.append("Şu anki fiyat:  %s  (%%%.1f %s)" % (_sayi(r["fiyat"]), abs(fark), taraf))
    satir.append("Yapı bozulursa: %s %s" % (_sayi(r["gecersiz"]), gecersiz_yon))
    satir.append("")
    if oran >= HACIM_DETAY_ESIK:
        satir.append("🔊 Hacim ortalamanın %.2f katı — %s lot, son 20 günün "
                     "ortalaması %s lot." % (oran, _lot(bugun_h), _lot(ort_h)))
    elif oran > 0:
        satir.append("🔊 Hacim ortalamanın %.2f katı — sıradan." % oran)
    else:
        satir.append("🔊 Hacim bilgisi alınamadı.")
    satir.append("")
    satir.append(derin_veri_blogu(r, df))
    satir.append("")
    satir.append("Bu bir %s önerisi değil: yapı kırıldı, o kadar."
                 % ("alım" if yukari else "satış"))
    satir.append("Bu taramanın getirisi henüz ölçülmedi.")
    return "\n".join(satir)


def mesaj_yakin(r: dict, df) -> str:
    _, _, oran = gunluk_hacim(df)
    mes = r.get("mesafe")
    satir = ["🔥 KIRILIMA YAKIN — %s" % r["kisa"], ""]
    satir.append("%s · %d günlük yapı · %d temas" % (r["ad"], r["bar"], r["temas"]))
    if mes is not None:
        satir.append("Çizgiye %%%.1f kaldı." % mes)
    satir.append("")
    satir.append("Kırılım çizgisi: %s" % _sayi(r["tetik"]))
    satir.append("Şu anki fiyat:  %s" % _sayi(r["fiyat"]))
    satir.append("Yapı bozulursa: %s" % _sayi(r["gecersiz"]))
    satir.append("")
    satir.append("🔊 Hacim ortalamanın %.2f katı — %s."
                 % (oran, "hareketli" if oran >= HACIM_DETAY_ESIK else "henüz sakin"))
    if derin_mi(r):
        satir.append("")
        satir.append(derin_veri_blogu(r, df))
    else:
        satir.append(saatlik_satir(r["kisa"]))
    satir.append("")
    satir.append("Henüz kırılmadı. Kırarsa ayrıca haber verilecek.")
    return "\n".join(satir)


def taze_mi(r: dict) -> bool:
    """Kırılım son TAZE_GUN gün içinde mi? Eski kırılım 'yeni alarm' değildir."""
    t = r.get("kirilim_tarih")
    if not t:
        return True                      # yakın/oluşuyor için tazelik aranmaz
    try:
        return (pd.Timestamp.now().normalize() - pd.Timestamp(t)).days <= TAZE_GUN
    except Exception:
        return False


def mesaj_ilk_kurulum(kirdi: list, yakin: list, tarih: str) -> str:
    satir = ["📐 Çizgi kırılım alarmı kuruldu — %s" % tarih, "",
             "Bundan sonra YENİ kırılımlar tek tek bildirilecek.",
             "Şu an açık olan yapılar (geçmiş kırılımlar, tek seferlik özet):", ""]
    for r in kirdi[:15]:
        satir.append("• %s — %s, %d günlük yapı, kırılım %s"
                     % (r["kisa"], r["ad"], r["bar"], r.get("kirilim_tarih") or "—"))
    if yakin:
        satir.append("")
        satir.append("Kırılıma yakın:")
        for r in yakin[:10]:
            m = r.get("mesafe")
            satir.append("• %s — %s%s" % (r["kisa"], r["ad"],
                                          (" · %%%.1f kaldı" % m) if m is not None else ""))
    satir.append("")
    satir.append("Kapsam: Yıldız Pazar · her hafta içi 17:30.")
    return "\n".join(satir)


def mesaj_sessiz(yakinlar: list, tarih: str) -> str:
    satir = ["📐 Çizgi yapıları — %s" % tarih, "", "Bugün yeni kırılım yok."]
    if yakinlar:
        ozet = " · ".join("%s %%%.1f" % (x["kisa"], x["mesafe"])
                          for x in yakinlar[:5] if x.get("mesafe") is not None)
        satir.append("İzlemede: %d yapı kırılıma yakın (%s)" % (len(yakinlar), ozet))
    return "\n".join(satir)


# ───────────────────────────── durum hafızası ───────────────────────────
def state_oku() -> dict:
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def state_yaz(d: dict):
    try:
        json.dump(d, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as exc:
        print("state yazılamadı:", exc)


# ───────────────────────────────── ana akış ─────────────────────────────
def main() -> int:
    basla = datetime.now()
    print("=" * 60)
    print("ÇİZGİ ALARMI —", basla.strftime("%Y-%m-%d %H:%M:%S"))

    evren = yildiz_pazar()
    if not evren:
        print("Yıldız Pazar listesi boş — çıkılıyor."); return 1
    print("Yıldız Pazar:", len(evren), "hisse")

    # lik_taban=0: kullanıcı kararı — pazar zaten filtre, ayrıca ciro sınırı yok.
    sonuc = cy.tara_evren(VERI, lik_taban=0, semboller=evren)
    print("elekten geçen yapı:", len(sonuc), "| süre %.0f sn"
          % (datetime.now() - basla).total_seconds())
    try:
        cy.kaydet(sonuc)
    except Exception:
        pass

    kirdi = [r for r in sonuc if r["stage"] in KIRDI]
    yakin = [r for r in sonuc if r["stage"] in YAKIN]
    yakin.sort(key=lambda r: (r["mesafe"] if r["mesafe"] is not None else 999))

    st = state_oku()
    bugun = str(pd.Timestamp.now().date())
    ilk_kurulum = not st
    yeni_st = {}
    gonderilecek = []

    for r in kirdi + yakin:
        anahtar = r["sembol"]
        imza = "%s|%s" % (r["stage"], r.get("kirilim_tarih") or "")
        yeni_st[anahtar] = imza
        if st.get(anahtar) == imza:
            continue                     # aynı hisse aynı durumda → tekrar yollama
        if not taze_mi(r):
            continue                     # geçen haftanın kırılımı "yeni" değildir
        gonderilecek.append(r)

    # İLK KURULUM: hafıza boşken her açık yapı "yeni" görünür → mesaj yağmuru.
    # Tek seferlik özet yollanır, hafıza tohumlanır, ertesi günden itibaren normal.
    if ilk_kurulum:
        metin = mesaj_ilk_kurulum(kirdi, yakin, basla.strftime("%d.%m.%Y"))
        if TEST:
            metin = "[TEST]\n" + metin
        tg_gonder(metin)
        if not KURU:
            state_yaz(yeni_st)
        print("İLK KURULUM — özet yollandı, hafıza tohumlandı (%d yapı)" % len(yeni_st))
        return 0

    print("yeni alarm:", len(gonderilecek), "| kırdı:", len(kirdi), "| yakın:", len(yakin))

    yollandi = 0
    for r in gonderilecek:
        try:
            yol = os.path.join(VERI, r["sembol"] + "_1d.parquet")
            df = pd.read_parquet(yol)
            df = df[~df.index.duplicated()].sort_index()
        except Exception:
            df = None
        metin = (mesaj_kirilim(r, df) if r["stage"] in KIRDI
                 else mesaj_yakin(r, df))
        if TEST:
            metin = "[TEST]\n" + metin
        if tg_gonder(metin):
            yollandi += 1
        print("  -> %s (%s)" % (r["kisa"], r["stage"]))

    if not gonderilecek:
        metin = mesaj_sessiz(yakin, basla.strftime("%d.%m.%Y"))
        if TEST:
            metin = "[TEST]\n" + metin
        if tg_gonder(metin):
            yollandi += 1

    if not KURU:
        state_yaz(yeni_st)
    sure = (datetime.now() - basla).total_seconds()
    print("BİTTİ — %d mesaj · %.0f sn · %s" % (yollandi, sure, bugun))
    return 0


if __name__ == "__main__":
    sys.exit(main())
