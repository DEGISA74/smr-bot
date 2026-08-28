#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GÜNDEM VERİ — sabah raporunun GERÇEK KATMANI (27 Ağu 2026).

Saf hesap; tek satır metin üretmez. `gundem_tweet.py` bunu okur ve yazar.

NEDEN AYRI DOSYA: eski raporun robotik olmasının sebebi kelime kıtlığı değil,
HER GÜN AYNI 4 RAKAMI anlatmasıydı (endeks %, kaç yeşil/kırmızı, 2 sektör,
1 hisse). Burada her gün gerçekten değişen malzeme üretiyoruz: gün içi hikâye,
ısrar oranı, kazanan/kaybeden asimetrisi, hafta hâfızası, sığ tavan sayımı.

İKİ EVREN (27 Ağu 2026 kullanıcı kararı → memory/project_isim_verme_evreni.md):
  • HESAP  = BIST100 / piyasa geneli. Endeks yüzdesi, genişlik, tavan-taban.
  • İSİM   = BIST50. Adı yazılacak hisse SADECE buradan seçilir. Sığ tahta
             (KPEKS vakası) bir daha manşete çıkamaz.
  • Filtrenin adı metinde ASLA geçmez — o gundem_tweet.py'nin sorumluluğu.

HACİM KIYASI: her zaman PATLAMADAN ÖNCEKİ ortalamaya göre (Volume[-21:-1]).
Bugünü içeren 20g ortalama, ölçmeye çalıştığı patlamayla şişer → test kendi
kendini onaylar. KPEKS'i "derin tahta" diye onaylatan kusur tam olarak buydu.
"""
import os, sys, json, glob, datetime as dt
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE    = Path(__file__).parent
VERILER = os.environ.get("VERILER_DIR") or str(BASE / "veriler")
BIST50_CACHE  = BASE / "_bist50.json"     # borsapy erişilemezse son bilinen liste
BIST100_CACHE = BASE / "_bist100.json"    # DİKKAT ÇEKENLER evreni (isim verme değil)

# ── DİKKAT ÇEKENLER eşikleri (28 Ağu 2026, kullanıcı tarifi) ─────────────────
# Amaç: "algoritma dikkat çeken hisseleri yakaladı" satırı. Kullanıcı 27 Ağu'da
# elle #dohol + #astor eklemişti; ikisinin ortak imzası ölçüldü:
# tabela kırmızıyken YÜKSELEN + hacmi normalin üstünde AMA patlama değil +
# önceki günlerde de ilgi var (birikim) + kısa trendi artı.
# Elenenler: PATEK/ALTNY (patlama günler önce olmuş, sönüyor),
#            PAHOL/FENER (ölü tahta bir günde uçmuş).
DC_MIN_PCT      = 1.5    # o gün en az bu kadar yükselmiş olmalı
DC_RVOL_ALT     = 1.1    # bugünkü hacim çarpanı alt sınır
DC_RVOL_UST     = 3.0    # üst sınır — üstü "patlama", birikim değil
DC_ONCEKI_GUN   = 3      # kaç günlük geçmişe bakılır
DC_ONCEKI_ORT   = 1.0    # önceki günlerin ortalama hacim çarpanı en az
DC_ONCEKI_TEPE  = 2.5    # önceki günlerin HİÇBİRİ bunu geçmemeli (patlama sonrası)
DC_TREND_GUN    = 3      # son kaç günün getirisi artı olmalı
DC_ADET         = 3      # rapora en fazla kaç isim girer

SIG_ADV_MN   = 100.0   # patlama ÖNCESİ ort. günlük işlem < bu → sığ tahta
HAREKET_ESIK = 0.3     # ±bu yüzdenin altı "yatay" sayılır (yeşil/kırmızı sayımı)
TAVAN_ESIK   = 9.0     # BIST günlük limit ±%10; 9+ = fiilen tavan
SEKTOR_ENDEKS = {"XBANK": "bankalar", "XUSIN": "sanayi"}


# ─────────────────────── İSİM VERME EVRENİ (BIST50) ───────────────────────
def bist50():
    """BIST50 bileşenleri → {kod: şirket adı}. Canlı; erişilemezse son kayıt.

    borsapy endeksi Şub/May/Ağu/Kas'ta güncellendiğinde listeyi kendiliğinden
    tazeler — elle bakımlı statik liste TUTMUYORUZ (dış siteler eski/hatalı
    kompozisyon veriyor; uzmanpara 2026-08'de hâlâ ARCLK/DOAS/MAVI diyordu).
    """
    try:
        import borsapy
        comp = borsapy.Index("XU050").components
        d = {c["symbol"]: c.get("name", c["symbol"]) for c in comp if c.get("symbol")}
        if len(d) >= 40:                       # makul bir liste geldiyse kaydet
            try:
                json.dump(d, open(BIST50_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
            except Exception:
                pass
            return d
    except Exception:
        pass
    try:
        raw = json.load(open(BIST50_CACHE, encoding="utf-8"))
        return raw if isinstance(raw, dict) else {t: t for t in raw}
    except Exception:
        return {}


def bist100():
    """DİKKAT ÇEKENLER evreni. İsim verme evreni DEĞİL (o BIST50).

    Kullanıcının elle eklediği DOHOL, BIST100'de ama BIST50'de değildi — sistem
    bu yüzden onu hiç göremiyordu. Sığ tahta koruması burada da sürüyor: BIST100
    dışına çıkılmaz.
    """
    try:
        import borsapy
        comp = borsapy.Index("XU100").components
        d = {c["symbol"] for c in comp if c.get("symbol")}
        if len(d) >= 80:
            try:
                json.dump(sorted(d), open(BIST100_CACHE, "w", encoding="utf-8"),
                          ensure_ascii=False)
            except Exception:
                pass
            return d
    except Exception:
        pass
    try:
        return set(json.load(open(BIST100_CACHE, encoding="utf-8")))
    except Exception:
        return set()


def _dikkat_imzasi(c, v):
    """Tek hissenin DİKKAT ÇEKEN imzası. Uymuyorsa None.

    Dönen 'ivme' = bugünkü hacim çarpanı ÷ önceki günlerin ortalaması.
    Kullanıcı sıralamayı buna göre istedi: "hacim 1'ken 4'e çıkan ilk sıraya,
    1'ken 2.3'e çıkan ikinci sıraya."
    """
    n = DC_ONCEKI_GUN + 1
    if len(v) < 22 + n:
        return None
    bug = (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100
    if bug < DC_MIN_PCT:
        return None
    rv = []
    for i in range(len(v) - n, len(v)):
        av = float(v.iloc[i - 21:i - 1].mean())
        if av <= 0:
            return None
        rv.append(float(v.iloc[i]) / av)
    bugun_rv, onceki = rv[-1], rv[:-1]
    if not (DC_RVOL_ALT <= bugun_rv <= DC_RVOL_UST):
        return None
    if sum(onceki) / len(onceki) < DC_ONCEKI_ORT:
        return None
    if max(onceki) > DC_ONCEKI_TEPE:          # patlama zaten olmuş, sönüyor
        return None
    trend = (float(c.iloc[-1]) / float(c.iloc[-1 - DC_TREND_GUN]) - 1) * 100
    if trend <= 0:
        return None
    taban = max(sum(onceki) / len(onceki), 0.5)
    return {"pct": bug, "rvol": bugun_rv, "ivme": bugun_rv / taban, "trend": trend}


# ─────────────────────── GÜN İÇİ HİKÂYE ───────────────────────
def _gun_ici(ac, dip, zirve, kap):
    """Açılış/dip/zirve/kapanış'tan günün ŞEKLİNİ çıkarır.

    Eski sınıflandırma sadece KAPANIŞA bakıyordu; bu yüzden 24 ve 25 Ağustos
    aynı etiketi ('yatay') aldı — oysa biri artıda açıp kazandığını geri verdi,
    diğeri eksiye düşüp toparladı. Tam ters iki gün, aynı cümle.
    """
    ş = []
    if dip > 0.05:
        ş.append("hic_eksiye_dusmedi")
    if zirve < -0.05:
        ş.append("hic_artiya_gecmedi")
    if abs(ac) >= 1.2:
        ş.append("boslukla_acildi")
    # gün içi dipten toparlama: düşüşün ne kadarını geri aldı
    if dip < -0.4:
        toparlama = (kap - dip) / abs(dip)
        if toparlama >= 0.40:
            ş.append("dipten_topladi")
        elif kap - dip <= 0.15:
            ş.append("dibinde_kapatti")
    # zirveden geri verme
    if zirve > 0.4:
        veren = (zirve - kap) / zirve
        if veren >= 0.40:
            ş.append("zirveden_verdi")
        elif zirve - kap <= 0.15:
            ş.append("zirvesinde_kapatti")
    if ac > 0.3 and kap < 0:
        ş.append("artida_acti_eksi_kapatti")
    if ac < -0.3 and kap > 0:
        ş.append("eksi_acti_arti_kapatti")
    return ş


# ─────────────────────── ANA FİŞ ───────────────────────
def gun_fisi(tarih=None):
    """Bir işlem gününün tam gerçek fişi. tarih=None → depodaki son gün.

    DÖNEN SÖZLÜK metin içermez; sadece ölçülmüş sayı ve etiket.
    """
    import pandas as pd
    import numpy as np
    from collections import Counter

    xf = glob.glob(f"{VERILER}/XU100*.parquet")
    if not xf:
        return None
    xu = pd.read_parquet(xf[0])
    if tarih:
        xu = xu.loc[:tarih]
    elif len(xu) and str(xu.index[-1].date()) == str(dt.date.today()):
        # ⚠ YARIM BAR KAPISI: en yeni bar BUGÜNE aitse seans daha bitmemiştir.
        # Rapor her zaman TAMAMLANMIŞ bir günü anlatır. Canlıda 08:30'da bu
        # durum oluşmaz ama elle tetiklendiğinde yarım gün "dünkü kapanış"
        # diye anlatılırdı. (Aynı sınıftan hata geçmişi: bayat veri yazım kapısı.)
        xu = xu.iloc[:-1]
    if len(xu) < 8:
        return None
    D = str(xu.index[-1].date())
    if tarih and D != tarih:
        return None                            # o gün seans yok / veri yok

    c_, p_ = float(xu["Close"].iloc[-1]), float(xu["Close"].iloc[-2])
    o_, h_, l_ = float(xu["Open"].iloc[-1]), float(xu["High"].iloc[-1]), float(xu["Low"].iloc[-1])
    x, ac, dip, zirve = [(q / p_ - 1) * 100 for q in (c_, o_, l_, h_)]

    rets = (xu["Close"].pct_change() * 100).tolist()
    ds = us = 0
    for r in reversed(rets[:-1]):
        if r < -HAREKET_ESIK: ds += 1
        else: break
    for r in reversed(rets[:-1]):
        if r > HAREKET_ESIK: us += 1
        else: break
    cum5 = (c_ / float(xu["Close"].iloc[-6]) - 1) * 100 if len(xu) >= 6 else x

    B50 = bist50()
    B100 = bist100()
    dikkat = []
    up = dn = 0
    ups, dns = [], []
    israr = 0
    tavanlar, taban = [], 0
    buyukler = []                              # BIST50 — isim verilebilecek evren

    for f in glob.glob(f"{VERILER}/*.IS_1d.parquet"):
        tk = os.path.basename(f).split("_")[0].replace(".IS", "")
        if tk.startswith("X"):
            continue
        try:
            d = pd.read_parquet(f)
            d = d.loc[:D]                      # hedef güne kes (bugünün yarım barı dışarıda)
            if len(d) < 25 or str(d.index[-1].date()) != D:
                continue                       # o gün işlem görmemiş → sayıma girmez
            c, p, pp = [float(d["Close"].iloc[i]) for i in (-1, -2, -3)]
            v = float(d["Volume"].iloc[-1])
            if min(c, p, pp) <= 0 or v <= 0 or pd.isna(c):
                continue
            r  = (c / p - 1) * 100
            r0 = (p / pp - 1) * 100            # önceki günün hareketi (ısrar için)
            av20 = float(d["Volume"].iloc[-21:-1].mean())      # PATLAMA ÖNCESİ
            adv  = float((d["Close"] * d["Volume"]).iloc[-21:-1].mean()) / 1e6
            rvol = v / av20 if av20 > 0 else 0.0

            if r > HAREKET_ESIK:
                up += 1; ups.append(r)
                if r0 > HAREKET_ESIK:
                    israr += 1
            elif r < -HAREKET_ESIK:
                dn += 1; dns.append(r)
            if r >= TAVAN_ESIK:
                tavanlar.append((tk, adv))
            if r <= -TAVAN_ESIK:
                taban += 1
            if tk in B100:
                imza = _dikkat_imzasi(d["Close"], d["Volume"])
                if imza:
                    dikkat.append(dict(imza, kod=tk, tl_mn=float(c * v) / 1e6))
            if tk in B50:
                buyukler.append({
                    "kod": tk, "ad": B50[tk], "pct": r, "rvol": rvol,
                    "tl_mn": float(c * v) / 1e6, "onceki_pct": r0, "adv_mn": adv,
                    # BIST günlük limit ±%10. Üstü KAZANILMIŞ hareket değildir:
                    # uzun süre kapalı kalmış tahtanın yeniden açılması ya da
                    # bedelsiz/rüçhan düzeltmesidir. Manşete taşınmamalı, rakamı
                    # da alıntılanmamalı — "kaçırdık" diye hayıflanacak şey yok.
                    "artefakt": abs(r) > 15.0,
                })
        except Exception:
            continue

    if up + dn == 0:
        return None

    sektor = {}
    for sym, adi in SEKTOR_ENDEKS.items():
        try:
            sd = pd.read_parquet(f"{VERILER}/{sym}.IS_1d.parquet").loc[:D]
            if len(sd) >= 2 and str(sd.index[-1].date()) == D:
                sektor[adi] = (float(sd["Close"].iloc[-1]) / float(sd["Close"].iloc[-2]) - 1) * 100
        except Exception:
            continue

    sig_tavan = sum(1 for _, a in tavanlar if a < SIG_ADV_MN)
    b_up = sum(1 for b in buyukler if b["pct"] > HAREKET_ESIK)
    b_dn = sum(1 for b in buyukler if b["pct"] < -HAREKET_ESIK)

    return {
        "tarih": D,
        "hafta_gunu": xu.index[-1].weekday(),          # 0=Pzt
        # ── endeks ──
        "x": x, "acilis": ac, "dip": dip, "zirve": zirve, "cum5": cum5,
        "dusen_seri": ds, "yukselen_seri": us,
        "gun_ici": _gun_ici(ac, dip, zirve, x),
        # ── genişlik (piyasa geneli) ──
        "yesil": up, "kirmizi": dn,
        "yesil_ort": float(np.mean(ups)) if ups else 0.0,
        "kirmizi_ort": float(np.mean(dns)) if dns else 0.0,
        "tavan": len(tavanlar), "taban": taban, "sig_tavan": sig_tavan,
        "israr": israr,
        "israr_pct": 100.0 * israr / up if up else 0.0,
        # ── sektör ──
        "sektor": sektor,
        # ── isim verilebilecek evren (BIST50) ──
        # ivmesi en yüksek olan başa (kullanıcı sıralaması)
        "dikkat": sorted(dikkat, key=lambda z: -z["ivme"])[:DC_ADET],
        "buyukler": sorted(buyukler, key=lambda b: -abs(b["pct"])),
        "buyuk_yesil": b_up, "buyuk_kirmizi": b_dn, "buyuk_n": len(buyukler),
    }


# ─────────────────────── HAFTA KARNESİ (Pazartesi için) ───────────────────────
def hafta_karnesi(tarih=None):
    """Son 5 işlem gününün özeti: getiri, kaç yeşil/kırmızı gün, tek-gün payı.

    Pazartesi raporu 'dün'ü anlatamaz (dün = Pazar). Onun yerine Cuma kapanışı
    + geçen haftanın karnesi anlatılır → haftada bir gün rapor zaten farklı olur.
    """
    import pandas as pd
    xf = glob.glob(f"{VERILER}/XU100*.parquet")
    if not xf:
        return None
    xu = pd.read_parquet(xf[0])
    if tarih:
        xu = xu.loc[:tarih]
    if len(xu) < 7:
        return None
    son5 = xu["Close"].iloc[-6:]                      # 5 günlük değişim için 6 kapanış
    getiri = (float(son5.iloc[-1]) / float(son5.iloc[0]) - 1) * 100
    gunluk = (son5.pct_change().dropna() * 100)
    yesil_gun = int((gunluk > HAREKET_ESIK).sum())
    kirmizi_gun = int((gunluk < -HAREKET_ESIK).sum())
    en_iyi_i = int(gunluk.values.argmax()); en_kotu_i = int(gunluk.values.argmin())
    return {
        "getiri": getiri,
        "yesil_gun": yesil_gun, "kirmizi_gun": kirmizi_gun,
        "gun_sayisi": len(gunluk),
        "en_iyi_gun": float(gunluk.iloc[en_iyi_i]),
        "en_iyi_tarih": str(gunluk.index[en_iyi_i].date()),
        "en_kotu_gun": float(gunluk.iloc[en_kotu_i]),
        "en_kotu_tarih": str(gunluk.index[en_kotu_i].date()),
        # haftanın kazancının ne kadarı tek güne sığdı (>0.8 → "tek güne sığdı")
        "tek_gun_payi": (float(gunluk.iloc[en_iyi_i]) / getiri) if getiri > 0.3 else None,
    }


if __name__ == "__main__":
    t = sys.argv[1] if len(sys.argv) > 1 else None
    f = gun_fisi(t)
    if not f:
        print("veri yok"); sys.exit(1)
    print(json.dumps(f, ensure_ascii=False, indent=2, default=float))
    print("\nHAFTA:", json.dumps(hafta_karnesi(t), ensure_ascii=False, default=float))
