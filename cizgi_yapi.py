# -*- coding: utf-8 -*-
"""ÇİZGİ YAPISI MOTORU (20 Ağu 2026) — üçgen / kama / kanal için ZARF hattı.

Neden ayrı motor: `formasyon_v2` sınır hatlarını REGRESYONLA çiziyor, yani hattı
tepe/dip noktalarının ORTASINDAN geçiriyor. Ölçüldü (ons altın, 20 Ağu 2026):
dipler "alt sınır" dediği hattın %8,6 altına sarkabiliyordu — o hat bir destek
çizgisi değil, bir ortalama. Sonucu: VESTL'de tetik %32 sapıyor, CEMTS'te hüküm
tersine dönüyordu.

Bu modül hattı ZARF olarak çizer: hiçbir tepe/dip hattın dışında kalmaz, hat en
az iki noktaya DEĞER. İnsanın elle çizdiği trend çizgisinin ta kendisi.

⚠️ SINIR: Bu motorun ürettiği yapıların GETİRİSİ ÖLÇÜLMEDİ. Yalnızca "kırıldı mı,
kırılmadı mı" sorusunu cevaplar. Skora, Kanıt Terazisi'ne veya AI promptuna
BAĞLANMAZ — ayrı liste + tek hisse kutusu olarak yaşar.

Kalibrasyon kaynağı: kullanıcının 20 Ağu 2026'da elle etiketlediği 8 yapı
(INTEM/GRTHO/ESEN/TUCLK/PKENT/KBORU = üçgen, BOBET/TBORG = kama). Üçgen-kama
ayrımı bu 8 etiketle ölçülerek bulundu; detay aşağıda UCGEN_ORAN'da.

SAF hesap: veri df dışarıdan gelir, bu modül parquet/UI'a dokunmaz.
Public giriş: analiz(df) · gorunum(df, ticker) · ciz(df, yapi, yol)
"""
from __future__ import annotations

import itertools
import math
from typing import Any, Optional

import numpy as np
import pandas as pd

import formasyon_v2 as fv

ENGINE_VERSION = "cizgi-yapi-1.0"

# ── ÇİZİM AYARLARI ──────────────────────────────────────────────────────────
TOL = 0.004          # değme toleransı %0,4 — bu kadar yakın nokta "hatta değdi" sayılır
DONDUR = 20          # hat kurulurken son 20 bar dışarıda: kırılım hattı kendine büküyor
MIN_TEMAS = 2        # her hat en az 2 noktaya değmeli
PENCERELER = (250, 180, 140, 105, 80)

# ── SINIFLAMA EŞİKLERİ ──────────────────────────────────────────────────────
KAMA_TAVAN = 0.55    # ağız başlangıcın %55 altına indiyse KAMA (sıkışıyor)
KANAL_TAVAN = 1.15   # %115'e kadar KANAL; üstü genişleyen megafon → kapsam dışı
# ÜÇGEN AYRIMI (20 Ağu 2026, 8 insan etiketiyle ölçüldü): MUTLAK eğim AYIRMIYOR —
# kullanıcının üçgen dediği INTEM'in yumuşak kenarı %5,7 inerken, kama dediği
# SARKY'ninki %5,9 iniyordu. Ayıran ölçüt YUMUŞAK kenarın DİK kenara ORANI:
#   kullanıcının 6 üçgeni  → %0,4 – %5,1
#   kalan 24 yapı (kama)   → %6,5 – %51,2
# Arada temiz boşluk var; eşik ortasına kondu. ⚠️ 8 etiketle bulundu, boşluk dar.
UCGEN_ORAN = 0.06

# ── ELEK (ekran + liste AYNI eleği kullanır ki ikisi çelişmesin) ────────────
ELEK_MIN_TEMAS = 5   # toplam temas (üst + alt)
ELEK_MIN_BAR = 80    # yapı en az 80 gün — kullanıcı uzun vadeli çizimleri seçti
ELEK_MAX_AGIZ = 0.30 # ağız başlangıcın %30'unun altına inmiş olmalı
ELEK_KANAL = False   # kanal ailesi listeye girmez (ölçüldü: 156 bulgu = gürültü)

_DURUM_METNI = {
    "KIRILIM_DOĞRULANDI": ("Kırılım doğrulandı", "#ef4444", "#ffffff"),
    "KIRILIM_ADAYI": ("Kırılım bölgesinde — teyit yok", "#f97316", "#ffffff"),
    "YENİDEN_TEST": ("Kırılan çizgi yeniden test ediliyor", "#f97316", "#ffffff"),
    "YAKIN": ("Kırılıma yakın", "#f59e0b", "#1a1a1a"),
    "OLUŞUYOR": ("Yapı oluşuyor", "#64748b", "#ffffff"),
    "UZAMIŞ": ("Kırılım eskimiş", "#8b5cf6", "#ffffff"),
    "SÜRESİ_DOLDU": ("Süresi doldu — kırılım gelmedi", "#64748b", "#ffffff"),
    "TAMAMLANDI": ("Ölçülü hedefe ulaşmış", "#94a3b8", "#1a1a1a"),
}


def _zarf_hat(pts, alt: bool = True, tol: float = TOL):
    """Hiçbir noktayı dışarıda bırakmayan, en çok noktaya DEĞEN hat.

    alt=True  → destek hattı (hiçbir dip altında kalmaz)
    alt=False → direnç hattı (hiçbir tepe üstünde kalmaz)
    """
    best = None
    for a, b in itertools.combinations(pts, 2):
        if b.bar == a.bar:
            continue
        m = (b.price - a.price) / (b.bar - a.bar)
        c = a.price - m * a.bar
        ihlal = False
        temas = []
        for p in pts:
            y = m * p.bar + c
            if y <= 0:
                ihlal = True
                break
            d = (p.price - y) / y
            if (alt and d < -tol) or ((not alt) and d > tol):
                ihlal = True
                break
            if abs(d) <= tol:
                temas.append(p)
        if ihlal:
            continue
        skor = (len(temas), abs(b.bar - a.bar))
        if best is None or skor > best[0]:
            best = (skor, fv._LineFit(m, c, 1.0, 0.0), temas)
    return (best[1], best[2]) if best else (None, None)


def _son_bar_kismi(df: pd.DataFrame) -> bool:
    """Son bar BUGÜNE aitse yarım (seans sürüyor) kabul edilir.

    20 Ağu 2026 dersi: gün içi yarım bar GUBRF'de temas sayısını 5'ten 4'e
    düşürüp yapıyı elekten atıyordu; aynı gün GENKM'de eski motorun 98,7 puanlı
    bulgusu tamamen buharlaşmıştı. Çizgi gün içinde oynamamalı.
    """
    try:
        return pd.Timestamp(df.index[-1]).date() >= pd.Timestamp.now().date()
    except Exception:
        return False


def _tek_pencere(df: pd.DataFrame, pencere: int, timeframe: str = "1d",
                 kismi_son_bar: bool = False) -> Optional[dict]:
    """Tek bir pencerede çizgi yapısı arar.

    HATLAR kapanmış barlardan kurulur (çizim gün içinde oynamasın), HÜKÜM ise
    son bar dahil verilir (kırılım bugün olduysa bugün görünsün). Bar numaraları
    konumsaldır; sondan bar atmak önceki barların numarasını değiştirmez.
    """
    n_tam = len(df)
    n = n_tam - 1 if (kismi_son_bar and n_tam > 60) else n_tam
    if n < pencere + 10:
        return None
    df_hat = df.iloc[:n]
    piv = fv._extract_pivots(df_hat, timeframe)
    atr = fv._finite(fv._atr(df).iloc[-1], float(df["Close"].iloc[-1]) * 0.02)
    cut = max(0, n - pencere)
    kes = n - DONDUR
    H = [p for p in piv if p.kind == "H" and cut <= p.bar < kes]
    L = [p for p in piv if p.kind == "L" and cut <= p.bar < kes]
    if len(H) < 3 or len(L) < 3:
        return None
    ust, tu = _zarf_hat(H, alt=False)
    alt, tl = _zarf_hat(L, alt=True)
    if ust is None or alt is None:
        return None
    if len(tu) < MIN_TEMAS or len(tl) < MIN_TEMAS:
        return None

    bas = min(min(q.bar for q in tu), min(q.bar for q in tl))
    son_yapi = kes - 1
    ss = son_yapi - bas
    if ss < 25:
        return None
    # Her hat yapının HEM ilk HEM son yarısında sınanmış olmalı; yoksa hat bir
    # ucunda fiyattan kopuk boşta asılı kalır (CEMTS dersi, 20 Ağu 2026).
    orta = bas + ss / 2.0
    for temaslar in (tu, tl):
        if not any(q.bar <= orta for q in temaslar):
            return None
        if not any(q.bar > orta for q in temaslar):
            return None

    pref = float(np.median(df["Close"].iloc[bas:son_yapi + 1]))
    td = ust.slope * ss / max(pref, 1e-9)
    bd = alt.slope * ss / max(pref, 1e-9)
    if np.sign(td) != np.sign(bd) or td == 0:
        return None                       # zıt eğim = simetrik üçgen, bu motorun kapsamı dışı
    sg = ust.at(bas) - alt.at(bas)
    eg = ust.at(son_yapi) - alt.at(son_yapi)
    if sg <= 0:
        return None
    apeks_icerde = False
    if eg <= 0:
        # Hatlar yapı bitmeden kesişmiş: apeks GEÇMİŞ. Ağzı kesişimden hemen önce ölç.
        den0 = alt.slope - ust.slope
        if abs(den0) <= 1e-12:
            return None
        kesisim = (ust.intercept - alt.intercept) / den0
        son_yapi = int(max(bas + 25, min(son_yapi, math.floor(kesisim) - 3)))
        ss = son_yapi - bas
        if ss < 25:
            return None
        eg = ust.at(son_yapi) - alt.at(son_yapi)
        if eg <= 0:
            return None
        apeks_icerde = True
    oran = eg / sg
    if oran <= KAMA_TAVAN:
        aile = "KAMA"
    elif oran <= KANAL_TAVAN:
        aile = "KANAL"
    else:
        return None

    yum, dik = min(abs(td), abs(bd)), max(abs(td), abs(bd))
    kenar_orani = (yum / dik) if dik > 1e-9 else 1.0
    yon_asagi = td < 0

    if kenar_orani <= UCGEN_ORAN:
        aile = "UCGEN"
        if abs(td) < abs(bd):
            # düz direnç + yükselen dipler → yükselen üçgen (boğa), tetik ÜST hat
            ad, direction = "Yükselen Üçgen", "bullish"
            tetik_hat, karsi_hat = ust, alt
        else:
            # düz destek + alçalan tepeler → alçalan üçgen (ayı), tetik ALT hat
            ad, direction = "Alçalan Üçgen", "bearish"
            tetik_hat, karsi_hat = alt, ust
    else:
        _yon_ad = "Alçalan " if yon_asagi else "Yükselen "
        ad = _yon_ad + ("Kama" if aile == "KAMA" else "Kanal")
        direction = "bullish" if yon_asagi else "bearish"
        tetik_hat = ust if yon_asagi else alt
        karsi_hat = alt if yon_asagi else ust

    tetik = tetik_hat.at(n_tam - 1)     # hüküm BUGÜNÜN barında verilir
    fiyat = float(df["Close"].iloc[-1])
    # UZATMA GUARDı (20 Ağu 2026): dik hat, yapı bittikten sonraki 20 barda sıfırın
    # altına inebiliyordu (MAGEN tetik -38,65). Saçma seviye üretmektense yapıyı at.
    if not (fiyat * 0.20 <= tetik <= fiyat * 5.0):
        return None

    buf = atr * 0.50
    invalid = ((karsi_hat.at(n_tam - 1) - buf) if direction == "bullish"
               else (karsi_hat.at(n_tam - 1) + buf))
    apex = None
    if aile in ("KAMA", "UCGEN"):
        den = alt.slope - ust.slope
        if abs(den) > 1e-12:
            a = (ust.intercept - alt.intercept) / den
            if math.isfinite(a) and a > son_yapi:
                apex = a
    stage, bbar, sm = fv._boundary_state(
        df, tetik_hat, direction, atr, invalid, apex,
        search_start=max(1, int(bas + ss * 0.5)), max_break_age=30,
    )
    bars = np.arange(bas, son_yapi + 1)
    cs = df["Close"].to_numpy(dtype=float)[bas:son_yapi + 1]
    us = np.asarray([ust.at(i) for i in bars])
    ls = np.asarray([alt.at(i) for i in bars])
    tolp = max(atr * 0.75, pref * 0.012)
    koridor = float(np.mean((cs <= us + tolp) & (cs >= ls - tolp)))

    return dict(
        ad=ad, aile=aile, yon=direction, stage=stage,
        ust=ust, alt=alt, temas_ust=tu, temas_alt=tl,
        bas=bas, son_yapi=son_yapi, ss=ss, oran=oran,
        ust_drift=td * 100, alt_drift=bd * 100, kenar_orani=kenar_orani * 100,
        tetik=float(tetik), gecersiz=float(invalid), fiyat=fiyat,
        koridor=koridor * 100, apex=apex, kirilim_bar=bbar,
        mesafe=sm.get("distance_to_trigger_pct"), atr=atr,
        apeks_icerde=apeks_icerde, pencere=pencere,
        bas_tarih=str(df.index[bas].date()),
        kirilim_tarih=(str(df.index[bbar].date()) if bbar is not None else None),
    )


def elekten_gecti(yapi: dict) -> bool:
    """Ekran ve liste AYNI eleği kullanır ki tek hisse ile tarama çelişmesin."""
    if not yapi:
        return False
    if yapi["aile"] == "KANAL" and not ELEK_KANAL:
        return False
    if (len(yapi["temas_ust"]) + len(yapi["temas_alt"])) < ELEK_MIN_TEMAS:
        return False
    if yapi["ss"] < ELEK_MIN_BAR:
        return False
    if yapi["oran"] > ELEK_MAX_AGIZ:
        return False
    return True


def analiz(df: pd.DataFrame, timeframe: str = "1d",
           pencereler=PENCERELER, elek: bool = True) -> Optional[dict]:
    """Birden çok pencere dener, en sağlam yapıyı döndürür.
    Tercih sırası: sıkışan yapı (kama/üçgen) > kanal, sonra temas, sonra uzunluk."""
    if df is None or len(df) < 60:
        return None
    try:
        temiz, _issues, ok = fv._clean_frame(df, timeframe)
    except Exception:
        return None
    if not ok or temiz.empty:
        return None
    kismi = _son_bar_kismi(temiz)
    aday = []
    for w in pencereler:
        try:
            r = _tek_pencere(temiz, w, timeframe, kismi_son_bar=kismi)
        except Exception:
            r = None
        if r and (not elek or elekten_gecti(r)):
            aday.append(r)
    if not aday:
        return None
    return max(aday, key=lambda r: (0 if r["aile"] == "KANAL" else 1,
                                    len(r["temas_ust"]) + len(r["temas_alt"]),
                                    r["ss"]))


ENDEKS_SEMBOL = {"XU100", "XU030", "XU050", "XBANK", "XUSIN", "XUMAL",
                 "XU100D", "XGIDA", "XBANA"}
# Ekranda gösterilen aşamalar: geçmişte kalmışlar (bozuldu/uzamış/tamamlandı) listeye girmez.
KUTU_OLUSUYOR = ("OLUŞUYOR",)
KUTU_YAKIN = ("YAKIN", "KIRILIM_ADAYI")
KUTU_KIRDI = ("KIRILIM_DOĞRULANDI", "YENİDEN_TEST")


LIK_TABAN_VARSAYILAN = 25_000_000
# 20 Ağu 2026 ölçümü — eşiğin bedeli (bulgu / oluşuyor / yakın / kırdı):
#   eşiksiz 31/3/2/13 · 25mn 19/1/2/10 · 50mn 13/0/0/7 · 100mn 8/0/0/4
# 50mn üstünde ERKEN UYARI kutuları tamamen boşalıyor (panelin asıl amacı o).
# 25mn hem 1-8mn'lik ince tahtaları eler hem erken kutuları yaşatır.


def tara_evren(veriler_dizini: str, lik_taban: float = LIK_TABAN_VARSAYILAN,
               timeframe: str = "1d", semboller: Optional[set] = None) -> list:
    """Parquet deposundaki varlıkları tarar (Fırsat Radarı ile aynı yaklaşım).

    lik_taban yalnız BIST (.IS) sembollerine uygulanır — emtia/kripto/ABD hacmi
    TL değildir, o eşikle kıyaslanamaz.
    semboller verilirse yalnız o kısa adlar taranır (ör. Yıldız Pazar listesi).
    Döner: elekten geçmiş yapı listesi.
    """
    import glob
    import os

    sonuc = []
    desen = "*_1d.parquet" if timeframe == "1d" else "*_4h.parquet"
    sonek = "_1d.parquet" if timeframe == "1d" else "_4h.parquet"
    for yol in sorted(glob.glob(os.path.join(veriler_dizini, desen))):
        ad = os.path.basename(yol)[: -len(sonek)]
        kisa = ad.replace(".IS", "")
        if kisa in ENDEKS_SEMBOL:
            continue
        if semboller is not None and kisa not in semboller:
            continue
        try:
            d = pd.read_parquet(yol)
            d = d[~d.index.duplicated()].sort_index()
            if len(d) < 120:
                continue
            bist = ad.endswith(".IS")
            ciro = 0.0
            try:
                ciro = float((d["Close"] * d["Volume"]).tail(20).median())
            except Exception:
                ciro = 0.0
            if bist and lik_taban and ciro < lik_taban:
                continue
            r = analiz(d, timeframe=timeframe)
            if not r:
                continue
            sonuc.append(dict(
                sembol=ad, kisa=kisa, bist=bist, ciro=ciro,
                ad=r["ad"], aile=r["aile"], yon=r["yon"], stage=r["stage"],
                durum=durum_rozeti(r)[0], bar=r["ss"], bas_tarih=r["bas_tarih"],
                temas=len(r["temas_ust"]) + len(r["temas_alt"]),
                tetik=r["tetik"], gecersiz=r["gecersiz"], fiyat=r["fiyat"],
                agiz=round(r["oran"] * 100, 1),
                mesafe=(round(abs(float(r["mesafe"])), 1) if r["mesafe"] is not None else None),
                kirilim_tarih=r["kirilim_tarih"], son_tarih=str(d.index[-1].date()),
            ))
        except Exception:
            continue
    return sonuc


KAYIT_DOSYA = "cizgi_yapi_log.jsonl"


def kaydet(sonuclar: list, yol: Optional[str] = None) -> int:
    """Her taramada bulunanları günlük dosyaya yazar — İLERİDE ÖLÇEBİLMEK İÇİN.

    Bu motorun getirisi bugün ölçülmedi; ölçüm günü geldiğinde elde veri olsun
    diye her bulgu tarih damgasıyla saklanır (Fırsat Radarı'nın jsonl kalıbı).
    Aynı gün aynı sembol İKİ KEZ yazılmaz — tarama 15 dakikada bir tekrar
    çalışsa da satır çoğalmaz. Döner: yeni yazılan satır sayısı.
    """
    import json
    import os

    if not sonuclar:
        return 0
    yol = yol or os.path.join(os.path.dirname(os.path.abspath(__file__)), KAYIT_DOSYA)
    bugun = str(pd.Timestamp.now().date())
    varolan = set()
    try:
        if os.path.exists(yol):
            with open(yol, encoding="utf-8") as fh:
                for satir in fh:
                    try:
                        k = json.loads(satir)
                    except Exception:
                        continue
                    if k.get("tarih") == bugun:
                        varolan.add(k.get("sembol"))
    except Exception:
        varolan = set()

    yazilan = 0
    try:
        with open(yol, "a", encoding="utf-8") as fh:
            for r in sonuclar:
                if r.get("sembol") in varolan:
                    continue
                kayit = {
                    "tarih": bugun,
                    "motor": ENGINE_VERSION,
                    "sembol": r.get("sembol"),
                    "veri_tarihi": r.get("son_tarih"),
                    "yapi": r.get("ad"),
                    "aile": r.get("aile"),
                    "yon": r.get("yon"),
                    "asama": r.get("stage"),
                    "bar": r.get("bar"),
                    "temas": r.get("temas"),
                    "agiz": r.get("agiz"),
                    "bas_tarih": r.get("bas_tarih"),
                    "tetik": round(float(r.get("tetik") or 0), 4),
                    "fiyat": round(float(r.get("fiyat") or 0), 4),
                    "mesafe": r.get("mesafe"),
                    "kirilim_tarih": r.get("kirilim_tarih"),
                    "ciro": round(float(r.get("ciro") or 0), 0),
                }
                fh.write(json.dumps(kayit, ensure_ascii=False) + "\n")
                yazilan += 1
                varolan.add(r.get("sembol"))
    except Exception:
        return yazilan
    return yazilan


def kutula(sonuclar: list) -> tuple:
    """Tarama sonucunu üç kutuya böler: (oluşuyor, kırılıma yakın, kırdı)."""
    olus = [r for r in sonuclar if r["stage"] in KUTU_OLUSUYOR]
    yakin = [r for r in sonuclar if r["stage"] in KUTU_YAKIN]
    kirdi = [r for r in sonuclar if r["stage"] in KUTU_KIRDI]
    olus.sort(key=lambda r: (r["mesafe"] if r["mesafe"] is not None else 999))
    yakin.sort(key=lambda r: (r["mesafe"] if r["mesafe"] is not None else 999))
    kirdi.sort(key=lambda r: (r["kirilim_tarih"] or ""), reverse=True)
    return olus, yakin, kirdi


def durum_rozeti(yapi: dict) -> tuple:
    """(metin, arka_renk, yazı_rengi). 'GEÇERSİZ' kelimesi ne olduğunu anlatmıyor;
    üçgende yön ters kırıldıysa bunu açıkça söyler."""
    stage = yapi.get("stage", "")
    if stage == "GEÇERSİZ":
        if yapi.get("aile") == "UCGEN":
            if yapi.get("yon") == "bearish":
                return ("Yukarı kaçtı — aşağı kurgu bozuldu", "#10b981", "#ffffff")
            return ("Aşağı kaçtı — yukarı kurgu bozuldu", "#ef4444", "#ffffff")
        return ("Yapı bozuldu", "#64748b", "#ffffff")
    return _DURUM_METNI.get(stage, (stage or "—", "#64748b", "#ffffff"))


def ciz(df: pd.DataFrame, yapi: dict, yol, ticker: Optional[str] = None,
        timeframe: str = "1d"):
    """Motorun karar verdiği AYNI hat ve temas noktalarıyla denetim PNG'si üretir."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from pathlib import Path

    temiz, _i, ok = fv._clean_frame(df, timeframe)
    if not ok or temiz.empty:
        raise ValueError("Grafik için geçerli veri yok.")
    yol = Path(yol)
    yol.parent.mkdir(parents=True, exist_ok=True)
    n = len(temiz)
    vs = max(0, yapi["bas"] - 15)
    part = temiz.iloc[vs:]
    x = np.arange(vs, n)

    fig, ax = plt.subplots(figsize=(15, 7.2), dpi=135)
    fig.patch.set_facecolor("#07111f")
    ax.set_facecolor("#07111f")
    up = part["Close"].to_numpy() >= part["Open"].to_numpy()
    col = np.where(up, "#14b8a6", "#ef5350")
    ax.vlines(x, part["Low"], part["High"], color=col, linewidth=0.85, alpha=0.85)
    gov = max(float(part["Close"].median()) * 0.0012, 1e-9)
    for b, o, c_, k in zip(x, part["Open"], part["Close"], col):
        ax.add_patch(plt.Rectangle((b - 0.35, min(o, c_)), 0.7, max(abs(c_ - o), gov),
                                   facecolor=k, edgecolor=k, alpha=0.95))
    xs = np.arange(yapi["bas"], n)
    ust_tetik = yapi["ust"].at(n - 1) == yapi["tetik"]
    ax.plot(xs, [yapi["ust"].at(j) for j in xs], "-",
            color=("#22c55e" if ust_tetik else "#fde047"),
            lw=(3.0 if ust_tetik else 2.2),
            label=("üst sınır = TETİK" if ust_tetik else "üst sınır"))
    ax.plot(xs, [yapi["alt"].at(j) for j in xs], "-",
            color=("#22c55e" if not ust_tetik else "#a78bfa"),
            lw=(3.0 if not ust_tetik else 2.2),
            label=("alt sınır = TETİK" if not ust_tetik else "alt sınır"))
    for q in yapi["temas_ust"]:
        if q.bar >= vs:
            ax.plot(q.bar, q.price, "v", color="#fde047", ms=11, zorder=6)
    for q in yapi["temas_alt"]:
        if q.bar >= vs:
            ax.plot(q.bar, q.price, "^", color="#a78bfa", ms=11, zorder=6)
    ax.axvline(yapi["son_yapi"], color="#64748b", ls=":", lw=1.1, alpha=0.6)

    baslik = "{} — {} — {}".format(ticker or "", yapi["ad"], durum_rozeti(yapi)[0])
    alt_baslik = "{} başlangıç · {} gün · {}+{} temas · tetik {:,.2f}".format(
        yapi["bas_tarih"], yapi["ss"], len(yapi["temas_ust"]),
        len(yapi["temas_alt"]), yapi["tetik"])
    ax.set_title(baslik + "\n" + alt_baslik, color="#e2e8f0", fontsize=11.5,
                 fontweight="bold", pad=12, loc="left")
    step = max(1, (n - vs) // 11)
    ax.set_xticks(x[::step])
    ax.set_xticklabels([d.strftime("%d %b %y") for d in part.index[::step]],
                       color="#94a3b8", fontsize=8.5)
    ax.set_xlim(vs - 2, n + 1)
    lo = float(part["Low"].min())
    hi = float(part["High"].max())
    pad = (hi - lo) * 0.10
    ax.set_ylim(lo - pad, hi + pad)
    ax.tick_params(colors="#94a3b8")
    for s in ax.spines.values():
        s.set_color("#334155")
    ax.grid(alpha=0.10, color="#64748b")
    lg = ax.legend(loc="upper right", fontsize=9, frameon=True)
    lg.get_frame().set_facecolor("#0f172a")
    lg.get_frame().set_edgecolor("#334155")
    for t in lg.get_texts():
        t.set_color("#cbd5e1")
    fig.tight_layout()
    fig.savefig(yol, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return yol


def gorunum(df: pd.DataFrame, ticker: str, timeframe: str = "1d") -> dict:
    """Tek hisse kutusu için sunum sözlüğü (app.py yalnızca bunu render eder)."""
    try:
        yapi = analiz(df, timeframe=timeframe)
    except Exception as exc:
        return {"available": False, "issues": ["Çizgi yapısı analizi hatası: %s" % exc]}
    if not yapi:
        return {"available": False, "issues": ["Elekten geçen çizgi yapısı yok."]}

    chart_b64 = ""
    try:
        import base64
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix="smr_cizgi_yapi_") as tmp:
            p = Path(tmp) / "cizgi_yapi.png"
            ciz(df, yapi, p, ticker=ticker, timeframe=timeframe)
            chart_b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    except Exception:
        chart_b64 = ""

    metin, bg, fg = durum_rozeti(yapi)
    mesafe = yapi.get("mesafe")
    return {
        "available": True,
        "engine_version": ENGINE_VERSION,
        "pattern_label": yapi["ad"],
        "aile": yapi["aile"],
        "direction": yapi["yon"],
        "stage": yapi["stage"],
        "durum_metni": metin,
        "durum_bg": bg,
        "durum_fg": fg,
        "trigger": yapi["tetik"],
        "invalidation": yapi["gecersiz"],
        "current_price": yapi["fiyat"],
        "bar": yapi["ss"],
        "bas_tarih": yapi["bas_tarih"],
        "kirilim_tarih": yapi["kirilim_tarih"],
        "temas_ust": len(yapi["temas_ust"]),
        "temas_alt": len(yapi["temas_alt"]),
        "agiz_pct": round(yapi["oran"] * 100, 1),
        "koridor_pct": round(yapi["koridor"], 1),
        "mesafe_pct": (round(abs(float(mesafe)), 1) if mesafe is not None else None),
        "pencere": yapi["pencere"],
        "bas_bar": yapi["bas"],
        "chart_b64": chart_b64,
    }
