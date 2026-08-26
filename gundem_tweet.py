#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GÜNDEM TWEET — her işlem günü sabahı dünün BIST fotoğrafını "Küçük Yatırımcı
Notları" diliyle bir tweet TASLAĞINA döker ve admin'in Telegram DM'ine yollar.
(aforizma_tweet.py ikizi: yarı-otomatik — kullanıcı düzenleyip kendi atar.)

SOFİSTİKASYON (v1.5 — 3 Ağu 2026):
  - ÇOK-GÜNLÜK YAY: tek günü izole etmez. Dün yeşilse ama günlerdir düşüyorsa
    "dönüş denemesi", üst üste yeşilse "yükseliş sürüyor" diye ANLATIR (arc).
  - LİKİDİTE FARKINDALIĞI: en çok yükseleni naifçe kutlamaz. Lider ince/sığ bir
    tahtaysa ("DUNYH %+42") bunu göz kırparak söyler; derin/likit bir isim
    liderse gerçek lider diye över. (ADV = Close×Volume, 20g ort.)
  - GENİŞLİK: endeks yeşil ama katılım darsa "yanıltıcı" der.

İLKE: Sayı SADECE gerçek veriden. Sebep UYDURMAZ. Biz/sen dili, esprili, çarpıcı,
al-sat tavsiyesi YOK, tek konuşma sorusuyla biter (reply stratejisi).

CLI:
  --dry   : hesapla + göster, GÖNDERME (token gerekmez)
  --test  : kapıları yok say, hemen 1 taslak GÖNDER
  --arc X : belirli yayı zorla (donus/devam_yukari/yesil/gericekilme/devam_asagi/sert/yatay) — önizleme
"""
import os, sys, json, glob, random
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).parent
VERILER = os.environ.get("VERILER_DIR") or str(BASE / "veriler")
ADMIN_CHAT_ID = "1034525990"
SHALLOW_ADV_MN = 100.0         # 20g ort. işlem hacmi (mn TL) < bu → "sığ tahta" (medyan ~89mn; lideri övmek için net üstünde olmalı)

# ── ÖNE ÇIKAN SEÇİMİ (A: sektör ayrışması · B: düşen-bıçak filtreli dönüş adayı) ──
DEEP_CRASH_5G     = -25.0      # son 5g bundan çok düşüp bugün zıplayan = DÜŞEN BIÇAK → yıldız yapma (DÖNÜŞ EL KİTABI)
REVERSAL_MIN_PULL = -4.0       # dönüş adayı: bugünden önce en az bu kadar geri çekilmiş olmalı (yoksa "dönüş" değil)
REVERSAL_MIN_RVOL = 1.35       # hacim ivmesi: bugünkü hacim / 20g ort. bundan büyük olmalı (ilgi patlaması)
REVERSAL_MIN_TODAY = 1.5       # bugünkü yeşil teyit eşiği (%)
SECTOR_MIN_GAP    = 0.6        # iki sektör arası anlamlı ayrışma (puan) — altı = "aynı hızda koştular"
# Eldeki GERÇEK sektör endeksleri (parquet olarak var). İleride borsapy ile genişletilebilir.
SECTOR_INDICES = {"XBANK": "bankalar", "XUSIN": "sanayi şirketleri"}
SECTOR_TAGS = {"bankalar": "#XBANK", "sanayi şirketleri": "#XUSIN"}
INDEX_TICKERS  = {"XU100", "XU030", "XUSIN", "XBANK", "XTUMY"}
BIST100 = set("""AEFES AGHOL AGROT AHGAZ AKBNK AKCNS AKFGY AKFYE AKSA AKSEN ALARK ALBRK ALFAS
ARCLK ASELS ASTOR ASUZU AYDEM BAGFS BERA BFREN BIENY BIMAS BIOEN BOBET BRSAN BRYAT BUCIM CANTE
CCOLA CEMTS CIMSA CWENE DOAS DOHOL ECILC ECZYT EGEEN EKGYO ENJSA ENKAI EREGL EUPWR EUREN FROTO
GARAN GENIL GESAN GLYHO GSDHO GUBRF GWIND HALKB HEKTS IMASM IPEKE ISCTR ISDMR ISGYO ISMEN IZMDC
KARSN KAYSE KCAER KCHOL KONTR KONYA KORDS KOZAA KOZAL KRDMD KZBGY LOGO MAVI MGROS MIATK ODAS OTKAR
OYAKC PENTA PETKM PGSUS PSGYO QUAGR REEDR SAHOL SASA SMRTG SKBNK SELEC SISE SOKM TABGD TAVHL TCELL
THYAO TKFEN TOASO TSKB TTKOM TTRAK TUKAS TUPRS TURSG ULKER VAKBN VESBE VESTL YEOTK YKBNK YYLGD ZOREN""".split())

DRY  = "--dry" in sys.argv
TEST = "--test" in sys.argv
FORCE_ARC = None
if "--arc" in sys.argv:
    try: FORCE_ARC = sys.argv[sys.argv.index("--arc") + 1]
    except IndexError: pass


# ─────────── VERİ: dünün fotoğrafı + çok-günlük bağlam (gerçek) ───────────
def market_snapshot():
    import pandas as pd
    from collections import Counter
    xf = glob.glob(f"{VERILER}/XU100*.parquet")
    if not xf:
        return None
    xu = pd.read_parquet(xf[0])
    if len(xu) < 3:
        return None
    xdate = str(xu.index[-1].date())
    rets = (xu["Close"].pct_change().dropna() * 100).tolist()
    x = rets[-1]
    prior = rets[:-1]
    ds = us = 0                      # bugünden ÖNCEKİ ardışık düşen/yükselen gün
    for r in reversed(prior):
        if r < -0.3: ds += 1
        else: break
    for r in reversed(prior):
        if r > 0.3: us += 1
        else: break
    cum5 = (float(xu["Close"].iloc[-1]) / float(xu["Close"].iloc[-6]) - 1) * 100 if len(xu) >= 6 else x

    rows = []
    rev_cand = []                                 # B) BIST100 dönüş adayları (düşen-bıçak elenmiş)
    for f in glob.glob(f"{VERILER}/*.IS_1d.parquet"):
        base = os.path.basename(f).split("_")[0]
        tk = base.replace(".IS", "")
        if tk in INDEX_TICKERS or tk.startswith("XU"):
            continue
        try:
            d = pd.read_parquet(f)
            if len(d) < 2:
                continue
            cs = d["Close"]; vs = d["Volume"]
            c = cs.iloc[-1]; p = cs.iloc[-2]; v = vs.iloc[-1]
            # İşlem görmeyen / veri eksik gün = gerçek hareket DEĞİL (tahta kapalı, NaN kapanış):
            # bunları eleriz ki "kapalı tahta açılışı" yanlışlıkla günün yıldızı olmasın.
            if pd.isna(c) or pd.isna(p) or pd.isna(v) or c <= 0 or p <= 0 or v <= 0:
                continue
            adv = float((cs * vs).tail(20).mean()) / 1e6   # mn TL
            rows.append((base, str(d.index[-1].date()), float(c), float(p), adv))
            # ── B) DÖNÜŞ ADAYI: sadece BIST100, düşen bıçak elenir ──
            if tk in BIST100 and len(cs) >= 8:
                today  = (float(c) / float(p) - 1) * 100
                prior5 = (float(p) / float(cs.iloc[-7]) - 1) * 100          # bugünden önceki 5g hareketi
                av20   = float(vs.iloc[-21:-1].mean())
                rvol   = float(v / av20) if av20 > 0 else 0.0
                dret   = (cs.pct_change() * 100).iloc[-6:-1]                # son 5 günün günlük %'leri (bugün hariç)
                n_taban = int((dret <= -9.5).sum())                        # arka arkaya taban = çöküş (düşen bıçak)
                saglikli = (today >= REVERSAL_MIN_TODAY and prior5 <= REVERSAL_MIN_PULL
                            and rvol >= REVERSAL_MIN_RVOL and adv >= SHALLOW_ADV_MN
                            and prior5 > DEEP_CRASH_5G and n_taban <= 2)   # ← DÜŞEN-BIÇAK FİLTRESİ
                if saglikli:
                    rev_cand.append((tk, today, prior5, rvol, adv))
        except Exception:
            continue
    if not rows:
        return None
    common = Counter(r[1] for r in rows).most_common(1)[0][0]
    pc = [(t, (c / p - 1) * 100, adv) for t, dd, c, p, adv in rows if dd == common and p > 0]
    pc.sort(key=lambda z: z[1])
    up = sum(1 for _, v, _ in pc if v > 0.3)
    dn = sum(1 for _, v, _ in pc if v < -0.3)
    movers_up = pc[-5:][::-1]                     # en çok yükselen 5 (t, pct, adv)

    # ── A) SEKTÖR AYRIŞMASI: eldeki gerçek sektör endekslerini endekse görece kıyasla ──
    sect = []
    for sym, adi in SECTOR_INDICES.items():
        sf = [q for q in glob.glob(f"{VERILER}/{sym}*.parquet")
              if q.endswith(".parquet") and ".pre_" not in q and ".prev" not in q]
        if not sf:
            continue
        try:
            sd = pd.read_parquet(sf[0])
            if len(sd) < 2 or str(sd.index[-1].date()) != xdate:
                continue
            schg = (float(sd["Close"].iloc[-1]) / float(sd["Close"].iloc[-2]) - 1) * 100
            sect.append((adi, schg, schg - x))   # (isim, sektör %, endekse görece puan)
        except Exception:
            continue
    sect.sort(key=lambda z: z[2], reverse=True)  # endekse görece en güçlü önde

    # ── B) en iyi dönüş adayı: geri çekilme derinliği + hacim ivmesi ──
    rev_cand.sort(key=lambda z: min(-z[2], 20) * 0.5 + z[3] * 3, reverse=True)
    reversal = rev_cand[0] if rev_cand else None

    return {
        "xdate": xdate, "x": x, "cum5": cum5, "down_streak": ds, "up_streak": us,
        "n": len(pc), "up": up, "dn": dn, "flat": len(pc) - up - dn,
        "tavan": sum(1 for _, v, _ in pc if v > 9), "taban": sum(1 for _, v, _ in pc if v < -9),
        "broad_up": up >= dn * 1.6, "broad_dn": dn >= up * 1.6,
        "movers_up": movers_up, "top_loss": pc[0] if pc else None,
        "sectors": sect, "reversal": reversal,
    }


# ─────────── YAY (arc): çok-günlük bağlamı sınıflandır ───────────
def classify(s):
    x, ds, us = s["x"], s["down_streak"], s["up_streak"]
    if x <= -1.6:
        return "sert"
    if x < -0.4:
        return "devam_asagi" if ds >= 1 else ("gericekilme" if us >= 2 else "kirmizi")
    if x > 0.4:
        if ds >= 2:  return "donus"          # günlerce düştü, bugün ilk yeşil
        if us >= 1:  return "devam_yukari"    # üst üste yeşil
        return "yesil"
    return "yatay"


# ─────────── A) SEKTÖR AYRIŞMASI satırı ───────────
def sector_line(s, rng):
    sect = s.get("sectors") or []
    if len(sect) < 2:
        return None
    lead, lchg, lrel = sect[0]       # endekse görece en güçlü
    lag,  gchg, grel = sect[-1]      # en zayıf
    # Anlamlı ayrışma: lider pozitif + iki sektör net ayrık + lider endeksten kopuk değil
    if lchg <= 0 or (lchg - gchg) < SECTOR_MIN_GAP or lrel < -0.3:
        return None
    lead_tag = SECTOR_TAGS.get(lead, lead)
    lag_tag = SECTOR_TAGS.get(lag, lag)
    return rng.choice([
        f"Dün {lead_tag} (%{lchg:+.1f}) öne çıkarken {lag_tag} %{gchg:+.1f}'de kaldı. Endeks aynı yerdeyken sektörlerin aynı hızda gitmediğini görüyoruz.",
        f"Sektörler dün ayrıştı: {lead_tag} %{lchg:+.1f} ile {lag_tag}'ın (%{gchg:+.1f}) önüne geçti. Para yine her yere değil, seçerek gitti.",
    ])


# ─────────── B) DÖNÜŞ adayı / likidite farkındalıklı lider satırı ───────────
def movers_line(s, rng):
    # Önce sağlıklı DÖNÜŞ adayı (düşen bıçak zaten market_snapshot'ta elendi)
    rev = s.get("reversal")
    if rev:
        t, today, prior5, rvol, adv = rev
        return rng.choice([
            f"#{t} dikkat çeken isimlerden biriydi: yakın zirvesinden ~%{abs(prior5):.0f} geri çekilmişti; dün %{today:+.1f} yükseldi ve hacmi normalinin {rvol:.1f} katına çıktı. Bu henüz dönüşün kesinleştiği anlamına gelmez, ama alıcının yeniden iştahlandığına dair iz bırakır.",
            f"#{t} son günlerde ~%{abs(prior5):.0f} geri çekildikten sonra dün %{today:+.1f} yükseldi; hacim de normalinin {rvol:.1f} katına çıktı. Bizim için önemli olan, bu ilginin bugünkü seansta da korunup korunmadığı.",
        ])
    # Dönüş adayı yoksa: eski davranış — en çok yükselen + likidite farkındalığı
    mu = s.get("movers_up") or []
    if not mu:
        return None
    t, p, adv = mu[0]
    name = t.replace(".IS", "")
    if p > 15:   # BIST günlük limit ±%10; üstü = tahta yeniden açılışı / bedelsiz-rüçhan (kazanılan hareket değil)
        return (f"Listenin en tepesinde #{name} gibi %{p:+.0f}'lik sıra dışı bir rakam görebiliriz; ama buna hemen anlam yüklemeyelim. "
                f"BIST'te günlük limit ±%10'dur; bu tür sıçramalar genelde uzun süre kapalı kalmış bir tahtanın "
                f"yeniden açılması ya da bedelsiz/rüçhan gibi bir düzeltmeyle ilgilidir. Biz önce hareketin nedenini doğrulamalıyız.")
    if adv < SHALLOW_ADV_MN:
        return rng.choice([
            f"#{name} dün %{p:+.0f} ile dikkat çekti; ancak işlem derinliği zayıf. Böyle hareketlerde biz fiyat kadar tahtanın bu ilgiyi taşıyıp taşıyamayacağına da bakarız.",
            f"#{name} %{p:+.0f} ile tabelada öne çıktı, fakat işlem derinliği sınırlı. Bu nedenle tek günlük hareketi kalıcı yön diye okumadan önce hacmin devamını izlemeliyiz.",
        ])
    return rng.choice([
        f"#{name} dün %{p:+.0f} ile öne çıktı. İşlem derinliği bu hareketi takip etmeye değer kılıyor; yine de tek günü kalıcı bir rota diye okumayalım.",
        f"Dün #{name} dikkat çeken isimlerden biriydi (%{p:+.0f}). Biz böyle hareketlerde fiyatın yanı sıra, alıcının bugünkü seansta da kalıp kalmadığını izleriz.",
    ])


# ─────────── SES: yay'a göre insani + esprili + biz dili ───────────
HEADERS = ["📊 Piyasa Sabahı | Küçük Yatırımcı Notları"]

def build_body(s, arc, rng):
    x, up, dn = s["x"], s["up"], s["dn"]
    tav, tab, ds, us = s["tavan"], s["taban"], s["down_streak"], s["up_streak"]
    c5 = s["cum5"]
    tavs = f", {tav} tavan" if tav >= 4 else ""
    tabs = f", {tab} taban" if tab >= 4 else ""
    dar = (not s["broad_up"]) and up > 0     # katılım dar mı

    banks = {
        "donus": {
            "hook": [
                f"Günlerdir süren düşüşün ardından #BIST100 dün %{x:+.1f} yükseldi; {up} hisse de yeşil kapandı{tavs}. Bu, piyasanın nefes alma denemesi olabilir; dönüşün kalıcı olup olmadığını ise zaman gösterecek.",
                f"{ds} gündür zorlanan piyasa dün ilk kez toparlandı: #BIST100 %{x:+.1f}, {up} hisse yükseldi. Bir günlük yeşil moral verir; biz yine de teyit gelmeden büyük sonuçlara atlamayalım.",
            ],
            "insight": ["Dönüşler tek günde ilan edilmez; fiyatın ve katılımın birlikte güçlenmesini ister. Bizim işimiz telaş etmek değil, işareti sabırla okumak."],
            "q": [
                "💬 İlk yeşil günde biz neye bakmalıyız: kaçan tren korkusuna mı, yoksa teyide mi?",
                "💬 Sizce bu gerçek dönüş mü, yoksa düşüşün nefeslenmesi mi? Biz hangi işaretle ayrım yapmalıyız?",
            ],
        },
        "devam_yukari": {
            "hook": [
                f"#BIST100 dün %{x:+.1f} yükseldi; bu üst üste {us+1}. yeşil gün ve {up} hisse artıda kapandı{tavs}. Güzel bir tablo, fakat yükseliş sürerken de planımıza sadık kalmalıyız.",
                f"Dünkü hareketle #BIST100'de yükseliş devam etti (%{x:+.1f}); son 5 gündeki değişim ~%{c5:+.1f}. Morali korurken, her yükselişin aynı hızla sürmeyeceğini de unutmayalım.",
            ],
            "insight": ["Yükseliş günleri güven verir. Bizim için asıl değerli olan, o güvenin içinde bile riskimizi ve hedefimizi unutmamaktır."],
            "q": [
                "💬 Yükseliş sürerken biz en çok neyi unuturuz: kâr almayı mı, yoksa riski kısmayı mı?",
                "💬 Bizim için \"yeter\" çizgisi nerede başlıyor: fiyat hedefinde mi, yoksa planımızda mı?",
            ],
        },
        "yesil": {
            "hook": [
                f"#BIST100 dün %{x:+.1f} yükseldi; {up} hisse de yeşil kapandı{tavs}. Güzel bir nefes, fakat tek günlük hareketi hemen yeni trend diye okumayalım.",
                f"Dün tabela yeşile döndü: #BIST100 %{x:+.1f}, {up} hisse yükseldi. Moralimizi koruyalım; yönün güçlendiğini görmek için biraz daha zamana ihtiyacımız var.",
            ],
            "insight": ["Tek yeşil gün moral verir, ama yönü tek başına belirlemez. Biz hem fiyatın hem de katılımın devamını izlemeliyiz."],
            "q": ["💬 Sizce dünkü yeşil, kalıcı bir başlangıç mı yoksa kısa bir mola mı? Biz neye bakarak karar vermeliyiz?"],
        },
        "gericekilme": {
            "hook": [
                f"Yükselişin ardından #BIST100 dün %{x:+.1f} geriledi; {dn} hisse kırmızı kapandı. Bu sağlıklı bir nefeslenme de olabilir, daha dikkatli izlememiz gereken bir zayıflama da.",
                f"#BIST100'de dün %{x:+.1f}'lik bir geri çekilme vardı; {dn} hisse düştü. İlk kırmızı gün tek başına hüküm vermez, ama biz planımızı yeniden gözden geçiririz.",
            ],
            "insight": ["Kırmızı bir günde asıl mesele korkuyla karar vermemek. Bizim pusulamız fiyat değil, daha önce kurduğumuz plan olmalı."],
            "q": ["💬 Kârdaki bir pozisyonda ilk kırmızıyı görünce biz neye dayanmalıyız: korkuya mı, planımıza mı?"],
        },
        "devam_asagi": {
            "hook": [
                f"#BIST100'de düşüş dün de sürdü: %{x:+.1f}, {dn} hisse kırmızı{tabs}; üst üste {ds+1}. zayıf gün. Böyle zamanlarda acele etmek yerine sermayemizi ve planımızı korumak daha kıymetli.",
                f"Dünkü hareketle #BIST100'de geri çekilme devam etti (%{x:+.1f}); son 5 gündeki değişim ~%{c5:+.1f}. Ucuz görünen her fiyatın hemen fırsat olmadığını aklımızda tutalım.",
            ],
            "insight": ["Düşüşte sabır pasif kalmak değildir. Biz teyit gelene kadar riski yönetir, fırsatın olgunlaşmasını bekleriz."],
            "q": ["💬 Düşüş sürerken biz dibi yakalamaya mı, yoksa teyidi görmeye mi öncelik vermeliyiz?"],
        },
        "kirmizi": {
            "hook": [
                f"Tabela dün kızardı: #BIST100 %{x:+.1f}, {dn} hisse kırmızı{tabs}. Tek başına panik sebebi değil; ama ihmal edilecek bir gün de değildi.",
            ],
            "insight": ["Bir kırmızı gün her zaman kötü değildir; asıl karne, o günü nasıl karşıladığımızda yazılır. Biz psikolojimizin planın önüne geçmesine izin vermeyelim."],
            "q": ["💬 Kırmızı bir günde biz ne yapıyoruz: panikle mi hareket ediyoruz, yoksa planımızı mı gözden geçiriyoruz?"],
        },
        "sert": {
            "hook": [
                f"Dün #BIST100'de sert satış vardı: %{x:+.1f}, {tab} hisse tabanda. Böyle günlerde kahramanlık aramak yerine önce ayakta kalmayı ve riski sınırlamayı düşünmeliyiz.",
                f"#BIST100 dün %{x:+.1f} düştü; {dn} hisse kırmızı, {tab} hisse tabanda kapandı. Zor günlerde hiçbir şey yapmamak da bilinçli bir karar olabilir.",
            ],
            "insight": ["Sert düşüşte asıl risk, planı bırakıp anlık korkuyla hareket etmektir. Biz önce pozisyon boyutumuzu ve taşıyabileceğimiz riski hatırlamalıyız."],
            "q": ["💬 Böyle günlerde biz ilk olarak neyi korumalıyız: fırsat arama heyecanını mı, sermayemizi mi?"],
        },
        "yatay": {
            "hook": [
                f"#BIST100 dün neredeyse yerinde saydı (%{x:+.2f}). Fakat tabelanın altı daha farklıydı: {up} hisse yükselirken {dn} hisse düştü. Endeks yeşil görünse de bu hareket piyasanın geneline yayılmadı.",
                f"#BIST100 dün sınırlı bir değişimle kapandı (%{x:+.2f}); ancak {up} hisse yükselirken {dn} hisse düştü. Bu, endeksin rengiyle piyasanın geneline yayılan hareketin aynı şey olmadığını hatırlatıyor.",
            ],
            "insight": ["Bu yüzden yalnızca endeksin rengine bakmayalım. Biz kendi hissemizin fiyatını, hacmini ve yükselişin piyasaya ne kadar yayıldığını birlikte okumalıyız."],
            "q": [
                "💬 Böyle günlerde biz nereye bakmalıyız: endeksin rengine mi, yoksa elimizdeki hissenin davranışına mı?",
                "💬 Endeks sakin görünürken biz hangi soruyu sormalıyız: piyasa ne yaptı mı, yoksa kendi hissemiz ne yaptı mı?",
            ],
        },
    }
    b = banks.get(arc, banks["yatay"])
    hook = rng.choice(b["hook"])
    if dar and arc in ("yesil", "devam_yukari", "donus"):
        hook += f" Dünkü artıya rağmen katılım dar kaldı: {up} yükselene karşı {dn} düşen vardı."
    insight = rng.choice(b["insight"])
    q = rng.choice(b["q"]).format(up=up, dn=dn)
    sl = sector_line(s, rng)                      # A) sektör ayrışması
    ml = movers_line(s, rng)                      # B) dönüş adayı (yoksa lider)
    parts = [hook]
    if sl:
        parts.append(sl)
    if ml:
        parts.append(ml)
    parts.append(insight)
    parts.append(q)
    parts.append("Güzel ve yeşil bir seans olsun.")
    return "\n\n".join(parts)


def render(s, arc, rng):
    hdr = rng.choice(HEADERS)
    tag = " (TEST)" if TEST else ""
    return (
        f"📨 GÜNDEM TASLAK{tag} — düzenle & at\n"
        f"🗓 {s['xdate']} kapanışı · yay: {arc}\n"
        f"────────────────────\n"
        f"{hdr}\n\n{build_body(s, arc, rng)}\n"
        f"────────────────────\n"
        f"ℹ️ Sana özel taslak; sen atmadıkça kimse görmez. Rakamlar gerçek veridendir."
    )


# ─────────── Telegram ───────────
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
    s = market_snapshot()
    if not s:
        print("[gundem] veri yok — taslak uretilemedi."); return
    rng = random.Random()
    arc = FORCE_ARC or classify(s)
    msg = render(s, arc, rng)
    if DRY or (not TEST and not _token()):
        print(msg); return
    print("[gundem] taslak gonderildi." if tg_send(msg) else "[gundem] gonderilemedi.")


if __name__ == "__main__":
    main()
