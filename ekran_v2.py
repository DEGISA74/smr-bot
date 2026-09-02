# -*- coding: utf-8 -*-
"""
ekran_v2.py — 🧪 YENİ EKRAN DENEMESİ (23 Tem 2026)

Mevcut ekrana DOKUNMAZ. Sol kolonun en tepesine kapalı bir açılır bar olarak
girer; içinde önerilen yeni yerleşimin tek-parça HTML iskeleti çizilir:

    1 · Şimdi ne oluyor   → hüküm + güven + en güçlü 3 kanıt + en ciddi 2 karşı kanıt
    2 · Bugün ne değişti  → dünkü snapshot'a göre fark
    3 · Ne zaman geçersiz → hükmü bozacak seviye (geçici: en yakın MA/VA sınırı)

Beğenilirse asıl ekran buna dönüşür, beğenilmezse tek satır import silinerek
geri alınır. SAF hesap + HTML üretimi — Streamlit çağrısı YOK (render app.py'de).

⚠ VPS scp listesine DAHİL (app.py import ediyor).
Tek kaynak / karar günlüğü → memory/project_ekran_hiyerarsi_reformu.md
"""

BG      = "#0d1829"
BORDER  = "#1e3a5f"
CYAN    = "#38bdf8"
TXT     = "#e2e8f0"
MUTED   = "#94a3b8"
UP      = "#22c55e"
DOWN    = "#f87171"
WARN    = "#f59e0b"
PURPLE  = "#a78bfa"


# ─────────────────────────────────────────────────────────────────────────────
# 1) GEÇERSİZLİK ÇİZGİSİ — hükmü bozacak seviye
# ─────────────────────────────────────────────────────────────────────────────
def gecersizlik_cizgisi(df, yon, vah=None, val=None):
    """Hükmü bozacak EN YAKIN seviye (geçici tanım — 1 Ağu 2026 ölçümüne kadar).

    Kalıcı çözüm hüküm-kalıcılık ölçümünden gelecek (kuyruk 2). O gelene kadar
    burada betimleyici bir vekil kullanılır: aşağı hükümde fiyatın ÜSTÜNDEKİ en
    yakın hareketli ortalama (veya VAH), yukarı hükümde ALTINDAKİ en yakın.

    Döner: ilk eşik + varsa aynı yöndeki ikinci teyit seviyesi veya None.
    """
    try:
        if df is None or len(df) < 20 or yon not in ('yukari', 'asagi'):
            return None
        c = 'Close' if 'Close' in df.columns else ('close' if 'close' in df.columns else None)
        if c is None:
            return None
        s = df[c].dropna()
        if s.empty:
            return None
        px = float(s.iloc[-1])
        if px <= 0:
            return None

        adaylar = []
        for ad, n, tip in (("EMA5", 5, 'e'), ("EMA8", 8, 'e'), ("EMA13", 13, 'e'),
                           ("SMA50", 50, 's'), ("SMA100", 100, 's'), ("SMA200", 200, 's')):
            if len(s) < n:
                continue
            v = (s.ewm(span=n, adjust=False).mean().iloc[-1] if tip == 'e'
                 else s.rolling(n).mean().iloc[-1])
            if v and v == v:
                adaylar.append((ad, float(v)))
        if vah:
            adaylar.append(("Değer Alanı üst sınırı", float(vah)))
        if val:
            adaylar.append(("Değer Alanı alt sınırı", float(val)))
        if not adaylar:
            return None

        # aşağı hüküm → üstteki en yakın seviye kırılırsa hüküm bozulur (ve tersi)
        if yon == 'asagi':
            uygun = [(a, v) for a, v in adaylar if v > px]
            uygun.sort(key=lambda x: x[1])
        else:
            uygun = [(a, v) for a, v in adaylar if v < px]
            uygun.sort(key=lambda x: -x[1])
        if not uygun:
            return None
        ad, v = uygun[0]
        teyit_ad, teyit_v = uygun[1] if len(uygun) > 1 else (None, None)
        return {'seviye': round(v, 2), 'ad': ad,
                'mesafe_pct': round((v - px) / px * 100.0, 1),
                'teyit_seviye': round(teyit_v, 2) if teyit_v is not None else None,
                'teyit_ad': teyit_ad}
    except Exception:
        return None


def gecersizlik_serit(df, yon, *, vah=None, val=None,
                      formasyon_px=None, formasyon_ad=None):
    """Fiyat kartı şeridi + AI metni için TEK KAYNAK geçersizlik verisi (2 Eyl 2026).

    NEDEN VAR: aynı çizgi app.py'de iki kez daha elle hesaplanıyordu ve ikisi de
    seviyenin fiyatın hangi TARAFINDA kaldığını kontrol etmiyordu. Dal seçimi
    "fiyat 50 günlük ortalamanın üstünde mi" sorusuna bağlıydı, sonra körlemesine
    21 günlük ortalama yazılıyordu. Fiyat 50 günlüğün üstünde ama 21 günlüğün
    ALTINDAYKEN çizgi fiyatın üstüne düşüyor, kart ise ona hâlâ uzun pozisyon
    stopu diyordu (1 Eyl 2026 XU100: stop 14.261, fiyat 14.229).

    KURAL: yön terazinin hükmünden gelir, fiyat-ortalama kıyasından değil.
    'yukari' hükümde geçersizlik çizgisi fiyatın ALTINDA, 'asagi' hükümde
    ÜSTÜNDE olmak zorundadır — yanlış tarafta kalan aday hükmü bozacak seviye
    değildir. Hüküm dengedeyse bozulacak bir tez yoktur → None döner, şerit
    çizilmez ("sorun yoksa gösterme").

    Yeni eşik/skor/hesap YOK: seviye adayları gecersizlik_cizgisi merdiveninden
    gelir (EMA 5/8/13 · SMA 50/100/200 · değer alanı sınırları).

    Döner: {'px','ad','mesafe_pct','teyit_px','teyit_ad','baslik','px_str',
            'teyit_str','sebep'} veya None.
    """
    if yon not in ('yukari', 'asagi'):
        return None
    try:
        if df is None or len(df) < 20:
            return None
        c = 'Close' if 'Close' in df.columns else ('close' if 'close' in df.columns else None)
        if c is None:
            return None
        s = df[c].dropna()
        if s.empty:
            return None
        px = float(s.iloc[-1])
        if px <= 0:
            return None
    except Exception:
        return None

    def _fmt(v):
        return f"{int(v):,}".replace(",", ".") if v >= 1000 else f"{v:.2f}"

    def _dogru_taraf(v):
        return v < px if yon == 'yukari' else v > px

    seviye = ad = None
    teyit_px = teyit_ad = None

    # Formasyon iptal çizgisi önceliklidir — ama YALNIZ doğru taraftaysa.
    # (Eski kod bunu taraf kontrolü yapmadan kullanıyordu.)
    try:
        if formasyon_px is not None:
            _fpx = float(formasyon_px)
            if _fpx > 0 and _dogru_taraf(_fpx):
                seviye = _fpx
                ad = f"{formasyon_ad or 'Formasyon'} iptal çizgisi"
    except (TypeError, ValueError):
        seviye = ad = None

    if seviye is None:
        _g = gecersizlik_cizgisi(df, yon, vah=vah, val=val)
        if not _g:
            return None
        seviye, ad = float(_g['seviye']), str(_g['ad'])
        teyit_px, teyit_ad = _g.get('teyit_seviye'), _g.get('teyit_ad')

    mesafe = (seviye - px) / px * 100.0
    return {
        'px': round(seviye, 2),
        'ad': ad,
        'mesafe_pct': round(mesafe, 1),
        'teyit_px': round(float(teyit_px), 2) if teyit_px is not None else None,
        'teyit_ad': teyit_ad,
        # Yön dili: yukarı hükümde altı kırılırsa stop, aşağı hükümde üstü
        # kırılırsa hüküm iptal. Aynı ayrım GENEL ÖZET panelinde de var.
        'baslik': "STOP" if yon == 'yukari' else "İPTAL",
        'px_str': _fmt(seviye),
        'teyit_str': (f"{_fmt(float(teyit_px))} ({teyit_ad})"
                      if teyit_px is not None else ""),
        'sebep': (f"{ad} · fiyatın %{abs(mesafe):.1f} "
                  f"{'altında' if mesafe < 0 else 'üstünde'}"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2) BUGÜN NE DEĞİŞTİ — dünkü snapshot'a göre fark
# ─────────────────────────────────────────────────────────────────────────────
def ne_degisti(bugun, onceki, kisa_vade=None, sok_serit=None):
    """analysis_log'un son iki satırından okunabilir fark cümleleri üretir.

    bugun/onceki: {'guven':..., 'kanit_skoru':..., 'master_score':..., 'tarih':...}
    kisa_vade   : _compute_kisa_vade_warnings çıktısı (liste)
    sok_serit   : terazi_core.sok_degerlendir şeridi (varsa en üste konur)

    Ölçülemeyen fark uydurulmaz — kayıt yoksa boş liste döner.
    """
    out = []
    try:
        if sok_serit:
            out.append(("sok", str(sok_serit)))
        # ⚠ 23 Tem 2026 — SKOR DEĞİŞİMLERİ ÇIKARILDI. "teknik skor 81→85" günlük
        # titreme, OLAY değil; kullanıcı "gerçekten ne değişti?" diye sordu, haklı.
        # "Bugün ne değişti" artık yalnız GERÇEK olayları taşır: şok günü + kısa
        # vade bozulmaları (STP kırılması, OBV dağıtıma dönmesi, climax...).
        # Yön dönüşü ileride eklenebilir (dün YUKARI → bugün AŞAĞI), ama sayısal
        # skor deltası ASLA — o gürültüydü.
        for w in (kisa_vade or [])[:4]:
            out.append(("uyari", str(w)))
    except Exception:
        pass
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 3) HTML İSKELETİ
# ─────────────────────────────────────────────────────────────────────────────
def _hucre(etiket, deger, alt, renk):
    """İKİ satır: başlık + değer. Üçüncü (açıklama) satır KALDIRILDI — dar
    hücrede zaten kesiliyordu ("ortalamanın al...") ve hücre başına ~14px
    dikey yer yiyordu. Açıklama tooltip'e taşındı, bilgi kaybolmadı."""
    return (f"<div title=\"{alt}\" style='background:rgba(255,255,255,0.03);"
            f"border-radius:6px;padding:5px 7px;min-width:0;'>"
            f"<div style='color:{MUTED};font-size:0.57rem;font-weight:700;"
            f"letter-spacing:0.03em;'>{etiket}</div>"
            f"<div style='color:{renk};font-size:0.92rem;font-weight:800;"
            f"line-height:1.2;white-space:nowrap;'>{deger}</div></div>")


def olcum_seridi(o):
    """Ölçüm şeridi — hükmün dayandığı ham sayılar. Yorum yok, ölçüm var.

    SIRA ÖNEMLİ: önce ÖLÇÜLMÜŞ ayırıcılar (52H konum, SMA50 — dört bağımsız
    çalışmada kazananı kaybedenden ayıran iki şey), sonra bağlam (RSI, hacim).
    Bağlam hücreleri tahmin gücü için değil, "bu hisse nerede" için duruyor.
    """
    h = []
    try:
        p52 = o.get('poz52')
        if p52 is not None:
            # Etiket EKRANDA GÖRÜNEN yuvarlanmış sayıdan üretilir — "%80 · orta bant"
            # gibi kendi içinde çelişen hücre çıkmasın (79.5 → görünen 80).
            p = round(float(p52))
            renk = UP if p >= 70 else (DOWN if p <= 25 else TXT)
            alt = ("zirveye yakın" if p >= 80 else
                   ("dip bölgesi" if p <= 20 else "orta bant"))
            h.append(_hucre("52H KONUM", f"%{p:.0f}", alt, renk))

        sm = o.get('sma50_pct')
        if sm is not None:
            s = float(sm)
            renk = UP if s > 0 else DOWN
            h.append(_hucre("SMA50", f"{s:+.1f}%",
                            "ortalamanın üstünde" if s > 0 else "ortalamanın altında", renk))

        rsi = o.get('rsi')
        if rsi is not None:
            r = float(rsi)
            renk = DOWN if r >= 70 else (UP if r <= 30 else TXT)
            alt = ("aşırı alım bölgesi" if r >= 70 else
                   ("aşırı satım bölgesi" if r <= 30 else
                    ("nötrün altında" if r < 50 else "nötrün üstünde")))
            h.append(_hucre("RSI", f"{r:.0f}", alt, renk))

        rv = o.get('rvol')
        if rv is not None:
            v = float(rv)
            renk = WARN if v >= 2.0 else (TXT if v >= 0.8 else MUTED)
            alt = ("hacim patlaması" if v >= 2.0 else
                   ("normalin üstü" if v >= 1.2 else
                    ("normal" if v >= 0.8 else "işlem zayıf")))
            h.append(_hucre("HACİM", f"{v:.1f}x", alt, renk))

        # ── 23 Tem 2026: DEĞER ALANI ve TEKNİK SKOR hücreleri KALDIRILDI ──
        # DEĞER ALANI hiç ölçülmedi; gerçekten önemli olduğu durumda zaten
        # "⚠ KAPI" rozetinde çıkıyor (risk_odul_kapisi). TEKNİK SKOR ise
        # güvenilmez olduğu ölçüldü (TUPRS 25 Haz: +%23,5'i başlatan günde
        # 0/100 verdi). Kalanlar ölçülmüş ayırıcılar + iki bağlam hücresi.
        # Geri istenirse: gosterilecek listesine 'va_pos'/'master' eklemek yeter.
        va = None
        if va:
            # ict_core Türkçe döner (ÜSTÜNDE/ALTINDA/İÇİNDE); İngilizce anahtarlar
            # eski/dış çağrılar için korunur. Türkçe küçültme YAPILMAZ ('I' bozulur).
            _m = {"ÜSTÜNDE": ("DEĞER ÜSTÜ", "pahalı bölge", WARN),
                  "İÇİNDE":  ("DEĞER İÇİ",  "adil bölge",   TXT),
                  "ALTINDA": ("DEĞER ALTI", "ucuz bölge",   CYAN),
                  "ABOVE":   ("DEĞER ÜSTÜ", "pahalı bölge", WARN),
                  "INSIDE":  ("DEĞER İÇİ",  "adil bölge",   TXT),
                  "BELOW":   ("DEĞER ALTI", "ucuz bölge",   CYAN)}
            _e, _a, _c = _m.get(str(va).strip(), (str(va), "değer alanı", TXT))
            h.append(_hucre("DEĞER ALANI", _e, _a, _c))

        ms = None       # TEKNİK SKOR hücresi kaldırıldı (yukarıdaki nota bak)
        if ms is not None:
            m = float(ms)
            renk = UP if m >= 60 else (DOWN if m < 40 else TXT)
            h.append(_hucre("TEKNİK SKOR", f"{m:.0f}",
                            "güçlü" if m >= 60 else ("zayıf" if m < 40 else "orta"), renk))
    except Exception:
        pass
    if not h:
        return ""
    # Tek sıra: 6 hücre yan yana. İki sıraya bölmek dikey boşluk üretiyordu.
    return (f"<div style='display:grid;grid-template-columns:repeat({len(h)},minmax(0,1fr));"
            f"gap:5px;'>{''.join(h)}</div>")


# ─────────────────────────────────────────────────────────────────────────────
# 4) ANA KART — yatay yayılır, dikeyde kısa (sol sütun ~%82 geniş)
#    Üst blok HEP AÇIK, ~45 saniyede okunur. Gerekçelerin tam metni ayrıdır
#    (build_detay_html) — app.py onu katlanır bölmeye koyar.
# ─────────────────────────────────────────────────────────────────────────────
def _oy_etiket(v):
    """Oy → SADECE isim + ölçülmüşlük işareti.

    ⚠ Bir dönem gerekçenin ilk 3 kelimesi ipucu olarak eklendi; cümleyi
    ortasından kestiği için "· Son 5 günün" gibi anlamsız parçalar çıktı.
    İsimler zaten betimleyici ("Para akışı güçlü negatif") — tam gerekçe
    tooltip'te ve Gerekçeler bölmesinde yaşıyor, kırpılmış parça gürültü.
    """
    ad = str(v.get('ad', '')).strip()
    neden = str(v.get('neden', '') or '').strip()
    olc = v.get('olculmus')
    if olc:
        isaret, renk = "●", (UP if v.get('yon') == 'boga' else DOWN)
    else:
        isaret, renk = "◐", MUTED
    olcsuz = "" if olc else (f" <span style='color:{MUTED};font-size:0.62rem;'>"
                             f"· ölçülmemiş</span>")
    return (f"<div title=\"{neden}\" style='font-size:0.75rem;color:{TXT if olc else MUTED};"
            f"margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>"
            f"<span style='color:{renk};'>{isaret}</span> {ad}{olcsuz}</div>")


def _sade_olay(kod):
    """Şifreli kısa-vade kodunu HİÇ BİLMEYEN birinin anlayacağı cümleye çevirir.
    Kod tanınmazsa olduğu gibi döner (bilgi kaybolmasın)."""
    k = str(kod)
    kl = k.lower()
    if "stp" in kl:
        return "Kısa vadeli trend aşağı döndü"
    if "dağıtım" in kl or "dagitim" in kl:
        return "Hacimde satıcı ağır basmaya başladı"
    if "obv" in kl or "zayıf" in kl or "zayif" in kl:
        return "Alıcının hacim desteği zayıflıyor"
    if "climax" in kl:
        return "Yoğun satış (panik) işareti"
    if "tepe" in kl or "z=" in kl:
        return "Fiyat kısa vadede fazla gerildi — tepe riski"
    if "momentum" in kl:
        return "Yükseliş hızı kesiliyor"
    return k


def _sade_kapi(kapi):
    """'KAPI ZAYIF' → herkesin anlayacağı cümle."""
    et = str(kapi.get('etiket', '')).upper()
    if et == "ZAYIF":
        return "Şu an alım için kazanç alanı dar, kaybetme alanı geniş"
    if et in ("TEMKİNLİ", "TEMKINLI"):
        return "Kazanç ile risk şu an başabaş — acele giriş için elverişsiz"
    if et == "KAPALI":
        return "Şu an giriş için uygun değil"
    return kapi.get('sebep', '') or f"Giriş kapısı: {et.lower()}"


def _sutun(baslik, renk, ic):
    return (f"<div style='min-width:0;'><div style='color:{renk};font-size:0.63rem;"
            f"font-weight:700;letter-spacing:0.04em;margin-bottom:5px;'>{baslik}</div>"
            f"{ic}</div>")


def _terazi_cubugu(boga_w, ayi_w):
    t = (boga_w or 0) + (ayi_w or 0)
    if t <= 0:
        return ""
    pb = boga_w / t * 100
    return (f"<div><div style='display:flex;height:8px;border-radius:4px;overflow:hidden;"
            f"background:#1e293b;'><div style='width:{pb:.0f}%;background:{UP};'></div>"
            f"<div style='width:{100 - pb:.0f}%;background:{DOWN};'></div></div>"
            f"<div style='display:flex;justify-content:space-between;font-size:0.6rem;"
            f"color:{MUTED};margin-top:2px;'><span>boğa {boga_w:g}</span>"
            f"<span>ayı {ayi_w:g}</span></div></div>")


def _gecersizlik_cetveli(g, yon):
    """Cümle değil CETVEL: fiyat nerede, kırılma nerede, arada ne kadar var."""
    if not g:
        return (f"<div style='color:{MUTED};font-size:0.68rem;opacity:0.7;'>"
                f"hüküm dengede — seviye hesaplanamadı</div>")
    uzak = abs(float(g['mesafe_pct']))
    kon = 60 if yon == 'asagi' else 40
    hedef = min(94, max(6, kon + (28 if yon == 'asagi' else -28)))
    kirilma_renk = UP if yon == 'asagi' else DOWN
    return (
        f"<div style='position:relative;height:30px;'>"
        f"<div style='position:absolute;top:9px;left:0;right:0;height:3px;"
        f"background:#1e293b;border-radius:2px;'></div>"
        f"<div style='position:absolute;top:9px;left:0;width:{kon}%;height:3px;"
        f"background:{DOWN if yon == 'asagi' else UP};border-radius:2px;'></div>"
        f"<div style='position:absolute;left:{kon}%;top:4px;width:3px;height:13px;"
        f"background:{TXT};'></div>"
        f"<div style='position:absolute;left:{hedef}%;top:2px;width:2px;height:17px;"
        f"background:{kirilma_renk};'></div>"
        f"<div style='position:absolute;left:{max(0, kon - 7)}%;top:20px;color:{TXT};"
        f"font-size:0.62rem;'>şimdi</div>"
        f"<div style='position:absolute;left:{max(0, hedef - 9)}%;top:20px;"
        f"color:{kirilma_renk};font-size:0.62rem;white-space:nowrap;'>"
        f"{g['seviye']} · {g['ad']}</div>"
        f"<div style='position:absolute;right:0;top:0;color:{MUTED};font-size:0.62rem;'>"
        f"%{uzak:.1f} uzakta</div></div>")


def build_html(ticker, fiyat, degisim_pct, terazi, karne=None,
               degisimler=None, gecersizlik=None, n_kapali=0,
               olcumler=None, taramalar=None, kapi=None, sessiz_lens=0):
    """ANA KART üst bloğu — dört sütun + tek alt satır. Dikeyde kısa kalır."""
    ter = terazi or {}
    votes = ter.get('votes') or []
    yon = ter.get('yon')

    if ter.get('sistemik'):
        hkm, hkm_kucuk, hkm_renk = "ASKIDA", "askıda", PURPLE
    elif yon == 'yukari':
        hkm, hkm_kucuk, hkm_renk = "YUKARI", "yukarı", UP
    elif yon == 'asagi':
        hkm, hkm_kucuk, hkm_renk = "AŞAĞI", "aşağı", DOWN
    else:
        hkm, hkm_kucuk, hkm_renk = "DENGEDE", "dengede", MUTED

    n_boga = sum(1 for v in votes if v.get('yon') == 'boga')
    n_ayi = sum(1 for v in votes if v.get('yon') == 'ayi')
    n_top, kazanan = n_boga + n_ayi, max(n_boga, n_ayi)
    if ter.get('tek_sinyal'):
        guven = "tek sinyal — yetersiz"
    elif ter.get('celiski') and ter.get('guven') is not None:
        guven = f"güven %{ter['guven']} · çelişkili · {n_top} lensin {kazanan}'i {hkm_kucuk}"
    elif n_top:
        guven = f"oybirliği · {n_top} lens {hkm_kucuk}"
    else:
        guven = ""

    ayilar = [v for v in votes if v.get('yon') == 'ayi'][:5]
    bogalar = [v for v in votes if v.get('yon') == 'boga'][:5]
    _bos = f"<div style='color:{MUTED};font-size:0.7rem;'>oy yok</div>"

    s_hukum = (f"<div style='min-width:0;'>"
               f"<div style='color:{hkm_renk};font-size:1.6rem;font-weight:800;"
               f"line-height:1;margin-bottom:5px;'>{hkm}</div>"
               + _terazi_cubugu(ter.get('boga_w', 0), ter.get('ayi_w', 0))
               + f"<div style='color:{MUTED};font-size:0.61rem;margin-top:4px;"
               f"line-height:1.35;'>{guven}</div></div>")

    # "BUGÜN NE DEĞİŞTİ" artık ANA SATIRDA dördüncü sütun — her olay sade Türkçe.
    _sat = []
    _renk = {'sok': PURPLE, 'uyari': DOWN, 'skor': TXT}
    for t, m in (degisimler or [])[:4]:
        cümle = _sade_olay(m) if t == 'uyari' else str(m)
        _sat.append(f"<div style='font-size:0.72rem;color:{TXT};margin-bottom:3px;"
                    f"line-height:1.35;'><span style='color:{_renk.get(t, TXT)};'>▾</span> "
                    f"{cümle}</div>")
    if kapi:
        _sat.append(f"<div style='font-size:0.72rem;color:{TXT};margin-bottom:3px;"
                    f"line-height:1.35;'><span style='color:{WARN};'>⚠</span> "
                    f"{_sade_kapi(kapi)}</div>")
    if not _sat:
        _sat.append(f"<div style='font-size:0.72rem;color:{MUTED};'>"
                    f"Düne göre önemli değişiklik yok.</div>")

    # ── ANA SATIR: 4 sütun — hüküm | aşağı | yukarı | bugün ne değişti
    ana_satir = (
        f"<div style='display:grid;grid-template-columns:0.85fr 1fr 1fr 1.25fr;"
        f"gap:12px;align-items:start;'>"
        f"{s_hukum}"
        + _sutun("AŞAĞI DİYENLER", DOWN,
                 "".join(_oy_etiket(v) for v in ayilar) or _bos)
        + _sutun("YUKARI DİYENLER", UP,
                 "".join(_oy_etiket(v) for v in bogalar) or _bos)
        + _sutun("BUGÜN NE DEĞİŞTİ", CYAN, "".join(_sat))
        + "</div>")

    karne_txt = ""
    if karne and karne.get('ret10') is not None:
        _h = karne.get('hit10')
        if _h is not None and float(_h) >= 55.0:
            karne_txt = (f"<span style='color:{MUTED};font-size:0.65rem;margin-left:10px;'>"
                         f"geçmiş: 10g {float(karne['ret10']):+.1f}% · isabet %{float(_h):.0f}"
                         f"</span>")

    # Başlık: sol tarafta başlık, sağda DARALTILMIŞ ölçüm hücreleri.
    olcum_str = olcum_seridi(olcumler or {})
    baslik = (f"<div style='display:flex;justify-content:space-between;"
              f"align-items:center;gap:14px;margin-bottom:8px;'>"
              f"<span style='white-space:nowrap;'>"
              f"<span style='color:{CYAN};font-size:0.72rem;font-weight:700;'>"
              f"ŞİMDİ NE OLUYOR</span>"
              f"<span style='color:{TXT};font-size:0.72rem;font-weight:700;'> — {ticker}</span>"
              f"{karne_txt}</span>"
              f"<span style='flex:0 1 auto;max-width:46%;'>{olcum_str}</span></div>")

    # GEÇERSİZLİK ÇİZGİSİ — EN TEPEYE, başlığın hemen altında ince tam-en şerit.
    gec_serit = (
        f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:9px;"
        f"padding-bottom:8px;border-bottom:1px solid rgba(255,255,255,0.07);'>"
        f"<span style='color:{MUTED};font-size:0.58rem;font-weight:700;"
        f"letter-spacing:0.04em;white-space:nowrap;flex:none;'>GEÇERSİZLİK</span>"
        f"<span style='flex:1;'>{_gecersizlik_cetveli(gecersizlik, yon)}</span></div>")

    return (f"<div style='background:rgba(56,189,248,0.045);border:1px solid {CYAN};"
            f"border-radius:10px;padding:9px 12px;'>"
            f"{baslik}{gec_serit}{ana_satir}</div>")


def build_detay_html(terazi):
    """Gerekçelerin TAM metni — app.py katlanır bölmeye koyar (varsayılan kapalı).
    İki kolona dökülür ki dikeyde uzamasın."""
    votes = (terazi or {}).get('votes') or []
    if not votes:
        return ""
    sat = []
    for v in votes:
        renk = UP if v.get('yon') == 'boga' else DOWN
        isaret = "●" if v.get('olculmus') else "◐"
        agr = v.get('agirlik', 0) or 0
        sat.append(
            f"<div style='margin-bottom:10px;break-inside:avoid;'>"
            f"<span style='color:{renk};'>{isaret}</span> "
            f"<b style='color:{TXT};font-size:0.79rem;'>{v.get('ad', '')}</b> "
            f"<span style='color:{MUTED};font-size:0.65rem;'>({agr:g} oy"
            + ("" if v.get('olculmus') else " · ölçülmemiş") + ")</span>"
            f"<div style='color:{MUTED};font-size:0.74rem;line-height:1.5;"
            f"margin-top:2px;'>{v.get('neden', '') or '—'}</div></div>")
    return f"<div style='columns:2;column-gap:26px;'>{''.join(sat)}</div>"




# ─────────────────────────────────────────────────────────────────────────────
# 5) İNFOGRAFİK ENJEKSİYON PARÇALARI (23 Tem 2026)
#    ANA KART kutusu kaldırıldı; 5 parça Görsel Analiz başlığına girer. Bu üç
#    fonksiyon, infografiğin koyu zemininde (px cinsinden, rem değil) çalışacak
#    KOMPAKT HTML döndürür. app.py _render_infografik_inapp bunları placeholder'a
#    string-replace eder. Terazi verisi ana thread'ten gelir (tek kaynak).
# ─────────────────────────────────────────────────────────────────────────────
def ig_yon(terazi):
    """Başlık yanına: ana yapı + kısa vade + dönüş teyidi."""
    ter = terazi or {}
    yon = ter.get('yon')
    if ter.get('sistemik'):
        ana, ana_ok, ana_renk = "ASKIDA", "○", PURPLE
    elif yon == 'yukari':
        ana, ana_ok, ana_renk = "YUKARI", "↑", UP
    elif yon == 'asagi':
        ana, ana_ok, ana_renk = "AŞAĞI", "↓", DOWN
    else:
        ana, ana_ok, ana_renk = "DENGEDE", "→", MUTED

    sok = str(ter.get('sok_serit') or '').lower()
    hacimli_dusus = any(
        'hacimli sert düşüş' in str(v.get('ad', '')).lower()
        for v in (ter.get('votes') or []))
    yukari_hamle = 'sert yukarı' in sok
    asagi_hamle = hacimli_dusus or ('bugün -' in sok and 'sert hareket' in sok)

    if ter.get('sistemik'):
        kisa, kisa_ok, kisa_renk = "PİYASA ŞOKU", "○", PURPLE
        donus, donus_ok, donus_renk = "HÜKÜM ASKIDA", "○", PURPLE
    elif yukari_hamle:
        kisa = "TEPKİ" if yon == 'asagi' else "İVME"
        kisa_ok, kisa_renk = "↑", UP
        donus = "TEYİT BEKLİYOR" if yon == 'asagi' else "YAPI UYUMLU"
        donus_ok, donus_renk = ("○", WARN) if yon == 'asagi' else ("●", UP)
    elif asagi_hamle:
        kisa = "DÜZELTME" if yon == 'yukari' else "BASKI"
        kisa_ok, kisa_renk = "↓", DOWN
        donus = "YAPI TESTTE" if yon == 'yukari' else "TEYİT YOK"
        donus_ok, donus_renk = "○", WARN if yon == 'yukari' else MUTED
    else:
        kisa, kisa_ok, kisa_renk = "SAKİN", "→", MUTED
        donus = ("YAPI UYUMLU" if yon == 'yukari' else
                 "TEYİT YOK" if yon == 'asagi' else "YÖN BEKLİYOR")
        donus_ok = "●" if yon == 'yukari' else "○"
        donus_renk = UP if yon == 'yukari' else MUTED

    def _satir(ad, ok, deger, renk, gun=None):
        # gün eki: başlığın yanına minik tire + pencere (ör. "KISA VADE -5g")
        _g = (f" <span style='font-size:9px;color:{MUTED};font-weight:600;'>-{gun}</span>"
              if gun else "")
        return (f"<div style='display:grid;grid-template-columns:96px 14px auto;gap:3px;"
                f"align-items:center;line-height:1.25;'>"
                f"<span style='font-size:10px;color:{MUTED};font-weight:700;'>{ad}{_g}</span>"
                f"<span style='font-size:14px;color:{renk};font-weight:900;'>{ok}</span>"
                f"<span style='font-size:12px;color:{renk};font-weight:800;white-space:nowrap;'>{deger}</span></div>")

    return (f"<div style='background:#0c1725;border:1px solid #1e3a5f;border-radius:10px;"
            f"padding:8px 16px;display:flex;flex-direction:column;gap:3px;justify-content:center;'>"
            f"{_satir('KISA VADE', kisa_ok, kisa, kisa_renk, '5g')}"
            f"{_satir('ANA YAPI', ana_ok, ana, ana_renk, '50g')}"
            f"{_satir('DÖNÜŞ', donus_ok, donus, donus_renk)}</div>")


def ig_gecersizlik(gecersizlik, yon):
    """Fiyatın yanına: ilk eşik + varsa ikinci teyit seviyesi."""
    if not gecersizlik:
        return ""
    uzak = abs(float(gecersizlik['mesafe_pct']))
    ok = "▲" if yon == 'asagi' else "▼"
    renk = UP if yon == 'asagi' else DOWN
    baslik = "İLK EŞİK" if yon == 'asagi' else "İLK DESTEK"
    teyit = gecersizlik.get('teyit_seviye')
    alt = (f"TEYİT <span style='color:{WARN};font-weight:800;'>{teyit}</span>"
           if teyit is not None and yon == 'asagi' else
           f"ALT SINIR <span style='color:{DOWN};font-weight:800;'>{teyit}</span>"
           if teyit is not None else
           f"{gecersizlik['ad']} · %{uzak:.1f}")
    return (f"<div style='background:#0c2238;border:1px solid #1e3a5f;border-radius:10px;"
            f"padding:8px 12px;display:flex;flex-direction:column;justify-content:center;'>"
            f"<div style='font-size:9px;color:{MUTED};font-weight:700;letter-spacing:.04em;'>"
            f"{baslik}</div>"
            f"<div style='font-size:16px;font-weight:800;color:{renk};line-height:1.15;'>"
            f"{ok} {gecersizlik['seviye']}</div>"
            f"<div style='font-size:9px;color:{MUTED};'>{alt}</div></div>")


def _ig_oy(v):
    # 29 Tem 2026 — başlıklar kaldırıldığı için RENK ayırt eder: aşağı faktör
    # KIRMIZI, yukarı faktör YEŞİL. ●/◐ ölçülmüşlük işareti korunur.
    ad = str(v.get('ad', '')).strip()
    renk = UP if v.get('yon') == 'boga' else DOWN
    isaret = "●" if v.get('olculmus') else "◐"
    return (f"<span title=\"{v.get('neden','')}\" style='display:inline-block;"
            f"background:rgba(255,255,255,0.05);border-radius:14px;padding:2px 9px;"
            f"margin:0 5px 4px 0;font-size:11px;font-weight:600;color:{renk};'>"
            f"<span>{isaret}</span> {ad}</span>")


def _ig_degis_cip(t, m):
    """'Bugün ne değişti' olayı → kompakt MAVİ çip. 29 Tem 2026: başlık kalktığı
    için her çipin başına 'bugün' eklenir (kullanıcı: 'anlayalım'). Değişimler
    hepsi mavi — aşağı/yukarı faktör renginden ayrışsın."""
    cümle = _sade_olay(m) if t == 'uyari' else str(m)
    if cümle and cümle[:1].isalpha():
        cümle = "bugün " + cümle[0].lower() + cümle[1:]
    elif cümle:
        cümle = "bugün " + cümle
    return (f"<span style='display:inline-block;background:rgba(56,189,248,0.08);"
            f"border-radius:14px;padding:2px 9px;margin:0 5px 4px 0;font-size:11px;"
            f"font-weight:600;color:{CYAN};'><span>▾</span> {cümle}</span>")


def ig_oylar(terazi, degisimler=None):
    """BAŞLIKSIZ tek sıra (29 Tem 2026 — kullanıcı: 'başlıklar olmasın').
    Renk ayırt eder: aşağı faktörler KIRMIZI · yukarı faktörler YEŞİL · bugünkü
    değişimler MAVİ ('bugün' önekli). Grup boşsa AYNI RENKTE '... faktör yok'
    (yeşil 'yukarı faktör yok' / kırmızı 'aşağı faktör yok'). Gruplar arası ince
    dikey ayraç grubu belli eder — başlık gerekmez."""
    votes = (terazi or {}).get('votes') or []
    ayilar = [v for v in votes if v.get('yon') == 'ayi'][:5]
    bogalar = [v for v in votes if v.get('yon') == 'boga'][:5]
    _degis = [_ig_degis_cip(t, m) for t, m in (degisimler or [])[:3]]
    if not _degis:
        _degis = [f"<span style='font-size:11px;font-weight:600;color:{CYAN};'>"
                  f"▾ bugün önemli bir değişiklik yok</span>"]

    # gruplar arası hafif dikey ayraç, tek akışta grubu belli eder
    _ayr = (f"<span style='display:inline-block;width:1px;height:13px;"
            f"background:#2a4058;margin:0 8px -2px;'></span>")

    _asagi = ("".join(_ig_oy(v) for v in ayilar) or
              f"<span style='font-size:11px;font-weight:600;color:{DOWN};'>"
              f"● aşağı faktör yok</span>")
    _yukari = ("".join(_ig_oy(v) for v in bogalar) or
               f"<span style='font-size:11px;font-weight:600;color:{UP};'>"
               f"● yukarı faktör yok</span>")
    faktorler = _asagi + _ayr + _yukari + _ayr + "".join(_degis)

    return (
        f"<div style='background:#0f1b2d;border:1px solid #1e3a5f;border-radius:8px;"
        f"padding:8px 14px;margin-bottom:12px;line-height:1.9;'>{faktorler}</div>")
