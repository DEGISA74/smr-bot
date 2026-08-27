# -*- coding: utf-8 -*-
"""
evidence.py — KANIT TABLOLARI (backtest-temelli tier + puan haritaları)
=======================================================================
app.py bölme projesi Adım 3 (4 Tem 2026) — bu tablolar app.py'den BİREBİR
taşındı (davranış değişikliği YOK, sadece adres değişti). Tek kaynak burası;
app.py `from evidence import ...` ile kullanır.

İçerik:
  • SCANNER_TIER_MAP   — scan_type → (tier, hit, ret, ad, vade notu). 20g bazlı.
  • SCANNER_VADE_POLICY — tarama → karar masası, seans vadesi, etiket ve son kullanma kuralı
  • ER_ELIT_SCORE_MIN  — ELİT panel gösterim eşiği
  • SCANNER_PLAIN_DESC — taramaların jargonsuz açıklamaları
  • _guc_plain         — güç etiketinin kullanıcıya dönük sade karşılığı

Kalibrasyon günlüğü tabloların üzerindeki yorumlarda. Güncelleme kuralı:
sayı DEĞİŞTİRMEDEN önce signal_results ölçümü (bkz. memory/project_scoring_roadmap.md).
"""

from datetime import date, datetime, timedelta

try:
    from bist_calendar import is_trading_day as _is_bist_trading_day
except ImportError:  # yalnız bağımsız test ortamı için güvenli geri dönüş
    def _is_bist_trading_day(value=None):
        if value is None:
            return True
        return getattr(value, "weekday", lambda: 0)() < 5

# ===============================================================
# 6 Haz 2026 — SCANNER TIER MAP (signal_results backtest)
# Kaynak: backtest hit_10g + avg_ret_10g + sample size.
# AI prompt'a koşullu emit edilir — sadece bu ticker tier'lı bir scanner'dan flag aldıysa.
# ===============================================================
# 18 Tem 2026 TAM REVİZYON — TEMİZ DB ile ilk kalibrasyon (zehir temizliği SONRASI).
# Kaynak: signal_results × scan_signals JOIN, BAZ METRİK 20g. Pencere Nis-Tem 2026
# (Mayıs boğa + Haz-Tem düşüş → tüm piyasa ortalamaları eksi; rakamlar bu yüzden mütevazı).
# Bulgu: eski 4 TIER_1'in DÖRDÜ de temiz veride koltuğunu kaybetti; TIER_2'lerin 8/9'u eşik altı.
# Eski sayılar zehirli ölçümden (bozuk parquet + bölünme) + boğa-ayı kalibrasyonundandı
# (detay: memory/project-backtest-zehir-temizligi.md).
# ⚠ 28 TEM 2026 DOĞRULAMA RANDEVUSU: Temmuz 20g verisi olgunlaşınca bu harita yeniden
#   kontrol edilecek. Rejim-düzeltmeli oku — pencere düşüş ağırlıklı, ters yönde
#   karamsarlık da hata olur.
# ===============================================================
SCANNER_TIER_MAP = {
    # scan_type: (tier, hit_20g_pct, avg_ret_20g_pct, display_name, vade_notu)
    # Eşikler (24 Haz tanımı, değişmedi): TIER_1 (20g hit≥62 & ret≥4.5, N≥25) ·
    # TIER_2 (hit≥53 & ret≥2.5, VEYA köprü ret≥3.5 & hit≥50 & N≥50) ·
    # TIER_3_ORTA (pozitif ama altı) · negatif ret → KALDIRILDI (alt yorumda).
    # ── TIER_1_ELIT — BOŞ (18 Tem): temiz veride eşiği karşılayan YOK. 28 Tem'de yeniden bak. ──
    # ── TIER_2_GUVENILIR — sağlam destekleyici, ana hikaye olabilir ──
    'minervini':     ('TIER_2_GUVENILIR',75.0, 40.18, '🏔 Minervini SEPA',                        '20g · hit %75 ret %+40.2 · ⚠ N=20 KÜÇÜK (10g: %81/+17.2 N=37; ideal 18g: %+30.1 N=26) · sonuç güçlü, uzun vade örneği TIER_1 için yetersiz'),
    'er_B11':        ('TIER_2_GUVENILIR',48.6,  4.52, '📐 Tepede Yay Geriliyor', '20g · hit %49 ret %+4.5 · N=529 · eksi piyasada +4.5 getiren tek büyük-örnekli tarama; hit köprü eşiğinin 1.4p altında ama piyasa hit\'i ~%37 iken — rejim payıyla TIER_2'),
    # ── TIER_3_ORTA — pozitif ama zayıf; ana hikaye DEĞİL, diğer teyitler aranır (temiz 20g ret sırasıyla) ──
    'er_C8':         ('TIER_3_ORTA',     44.9,  2.44, '🚀 Erken Radar C8 (Yukarı Kanal Testi)',   '20g · hit %45 ret %+2.4 · N=352'),
    'er_C6':         ('TIER_3_ORTA',     43.8,  2.16, '🚀 Erken Radar C6 (Piyasa Lideri)',        '20g · hit %44 ret %+2.2 · N=105 · 10g\'de belirgin güçlü (%55/+2.4 N=132, her ay piyasa üstü) — kısa vade karakterli'),
    'er_C11':        ('TIER_3_ORTA',     41.4,  2.05, '🚀 Erken Radar C11 (Trendde Momentum)',    '20g · hit %41 ret %+2.1 · N=302'),
    'er_C2':         ('TIER_3_ORTA',     44.6,  1.83, '🚀 Erken Radar C2 (Ortalama Testi)',       '20g · hit %45 ret %+1.8 · N=1319 · en büyük örnek, istikrarlı pozitif'),
    'er_A3':         ('TIER_3_ORTA',     43.8,  1.73, '🔄 Erken Radar A3 (İkili Dip)',            '20g · hit %44 ret %+1.7 · N=575'),
    'er_B5':         ('TIER_3_ORTA',     40.8,  1.47, '📐 Erken Radar B5 (Üçgen Daralma)',        '20g · hit %41 ret %+1.5 · N=360'),
    'er_A7':         ('TIER_3_ORTA',     44.8,  1.44, '🔄 Erken Radar A7 (Hacimli Toparlanma)',   '20g · hit %45 ret %+1.4 · N=614'),
    'er_B8':         ('TIER_3_ORTA',     50.5,  1.09, '📐 Erken Radar B8 (Sıkışma Sonu)',         '20g · hit %51 ret %+1.1 · N=91 · 18 Tem: TIER_1→3 (eski %71/+4.9 iddiası temiz veride erimiş)'),
    'er_A2':         ('TIER_3_ORTA',     46.7,  0.82, '🔄 Erken Radar A2 (Hacimli Tepki)',        '20g · hit %47 ret %+0.8 · N=90 · 18 Tem: TIER_1→3 (eski %70/+6.6 iddiası temiz veride erimiş; Tem 10g N=8 hit %0)'),
    'er_A4':         ('TIER_3_ORTA',     47.0,  0.82, '🔄 Erken Radar A4 (Yön Değiştiriyor)',     '20g · hit %47 ret %+0.8 · N=824 · 18 Tem: TIER_2→3'),
    'er_A5':         ('TIER_3_ORTA',     34.8,  0.29, '🔄 Erken Radar A5 (Toparlanma Başlıyor)',  '20g · hit %35 ret %+0.3 · N=276 · 18 Tem: TIER_2→3 (sınırda pozitif)'),
    # ── KALDIRILANLAR (18 Tem 2026) — temiz 20g getirisi NEGATİF, AI context'inden çıkarıldı ──
    # • er_D1 (eski TIER_1!): %38.0 / -2.17 (N=274) — eski "hit %76 ret +7.0" iddiası zehirli ölçümdenmiş.
    # • er_A8 (eski TIER_1!): %35.2 / -1.75 (N=142).
    # • prelaunch_bos (eski TIER_2, UI'da turuncu vitrindeydi): %31.4 / -2.98 (N=255) — vitrin hakkı yok.
    # • er_C9: %36.0/-2.04 (N=1076) · er_C7: %35.4/-1.44 (N=1135) · er_A9: %37.7/-0.71 (N=1875)
    # • er_A6: %26.4/-2.94 (N=330) · er_C5: %33.8/-0.56 (N=278; 5g'de parlak ama 20g'ye taşınmıyor)
    # • er_C3: %39.8/-0.27 (N=241) · er_D2: %41.2/-0.14 (N=272) · er_D3: %41.1/-0.11 (N=1316)
    # • er_A1: %38.5/-1.47 (N=109) — 28 Tem kararı ERKEN uygulandı (4 Tem + 13 Tem + 16 Tem hep negatifti).
    # • er_D4: long olarak %65.6/-2.94 (N=675) → boğa haritasından ÇIKTI. AMA düşüş uyarısı olarak
    #   %65-66 isabet (10g ve 20g) = ölçülmüş ŞEMSİYE — er_D5 ile birlikte (20g %66.4/-4.40 N=241)
    #   ekran reformu 1b "şemsiye rozeti" işinin kanıt tabanı. Boğa tier'ı olarak ASLA geri alma.
    # Önceki kaldırılanlar (hâlâ geçerli): er_B1, Royal Flush (nadir_firsat), ICT Sniper, Radar 1,
    # er_C1, guclu_donus — negatif/sıfır beklenti. UI'da çalışır, AI'dan gizli.
}

# ===============================================================
# 17 AGU 2026 — ALFA KARNESI (endeks-kiyasli, IKI REJIM)
# Kaynak: `alfa_karne.py` · 21.596 sinyal T+20 · Oca-Tem 2026
#   alfa = sinyal getirisi - AYNI GUN baslayan XU100 getirisi
#   Gun-kumelenmesi duzeltilmis t-testi (ayni gun taranan hisseler tek oy):
#   15 zayif taramanin 14'u t<-2 ile GERCEKTEN negatif.
#
# GENEL: yukselen tape +0,55 alfa · dusen tape -3,68 alfa.
#   Acigin tamami dusen piyasadan: zayif tape'te dibe dusmus hisse seciyoruz,
#   endeks buyuklerle toparliyor, biz katilmiyoruz.
#
# KARAR (kullanici onayi 17 Agu):
#   • DAHA ZAYIF + ZAYIF listesi AI prompt'a ve KARAR panellerine CIKMAZ.
#   • Master Scan taramasi + scan_signals kaydi DEVAM EDER — pipeline'dan
#     cikarilirsa olcum akisi da durur, bir sonraki rejimde korlesiriz.
#   • Kod SILINMEDI. Gercek boga rejiminde yeniden olculecek.
#
# ⚠ SINIR: 43 ayri tarama gunu var ama T+20 pencereleri ortusuyor →
#   etkin bagimsiz gozlem ~6-9 donem, hepsi tek rejim yayindan. "Zayif tape'te
#   zarar veriyor" KANITLANDI; "her kosulda bozuk" KANITLANMADI.
#   Rejim degisince `python alfa_karne.py` kos ve bu listeyi guncelle.
#   Detay: memory/project_endeks_alti_alfa.md
# ===============================================================

# ===============================================================
# 17 AGU 2026 — ELEME (kullanici karari: "27'sini cikar, her yerden")
# Kaynak: alfa_karne.py · 60 tarama · T+20 alfa (endekse gore fark)
# Olcut: 🔴 ELE (alfa -1..-2,5) + ⛔ KESIN ELE (alfa <= -2,5)
# Bunlar SINYAL URETMEZ: senaryo motorunda atlanir, Master Scan adimi kosmaz,
# panel cizilmez. Kod silinmedi — geri almak icin bu listeyi bosalt.
# NOT: elenenler artik ölçülmüyor da; "acaba duzeldi mi" sorusu cevapsiz kalir.
# Bilinerek kabul edildi (kullanici: "ugrasmayalim bunlarin yukuyle").
# ===============================================================
ELENEN_KLASIK = frozenset({
    'ict_sniper', 'rs_leaders', 'vip_formasyon',
    'nadir_firsat', 'harmonik_confluence',
})
# guclu_donus GERİ ALINDI (17 Agu, ayni gun): korunanlarla ayni tarih araligina
# (18 Haz-17 Tem) kisilinca alfasi -2,27 → +0,34'e donuyor. Elenmesi veriyle
# desteklenmiyordu; ilk olcumdeki -2,27 donem etkisi tasiyordu.

# Erken Radar senaryolari (ERKEN_RADAR_SCENARIOS anahtarlari — 'er_' onsuz)
ELENEN_ER_SENARYO = frozenset({
    'A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8', 'A9',
    'B1', 'B5',
    'C1', 'C3', 'C5', 'C7', 'C9', 'C11',
    'D1', 'D3', 'D4', 'D5',
})

ELENEN_TARAMALAR = ELENEN_KLASIK | frozenset(f"er_{_x}" for _x in ELENEN_ER_SENARYO)


def elendi_mi(scan_type) -> bool:
    """Bu tarama tamamen elendi mi? (True = hic calistirma, hic gosterme)"""
    return scan_type in ELENEN_TARAMALAR


# Tek anahtar: False yaparsan bastirma tamamen kalkar (geri alinabilir).
ZAYIF_TARAMA_AI_BASTIR = True

# DAHA ZAYIF — iki rejimde de negatif VE alfa <= -2,5 (ya da tek basina <= -4,0)
DAHA_ZAYIF_SCANNERS = frozenset({
    'ict_sniper',        # -4,89  (t -3,24)
    'er_B1',             # -4,83  (t -2,60)
    'er_A2',             # -4,24  (yuk +0,54 / dus -7,01)
    'vip_formasyon',     # -3,87  (t -4,16)
    'er_A1',             # -3,68  (t -6,33) — eskiden "Geri Donus SAMPIYONU"
    'er_A8',             # -3,16  (t -2,20) — eskiden "isabet %100"
    'er_A6',             # -2,91  (t -3,95)
    'rs_leaders',        # -2,88  (t -3,95) — en buyuk toplam acik, N=1692
    'er_D3',             # -2,76  (t -4,10)
    'er_A9',             # -2,73  (t -5,24)
    'er_A4',             # -2,53  (t -4,48)
})

# ZAYIF — iki rejimde de negatif (siddeti daha az)
ZAYIF_SCANNERS = frozenset({
    'tekli_altin',           # -0,98 gun-bazli (t -2,15)
    'er_A3',                 # -2,48 (t -4,38)
    'harmonik_confluence',   # -2,44 (t -2,47)
    'er_B5',                 # -1,33 (t -1,67) ⚠ istatistiksel olarak BELIRSIZ
    # 26 Agu 2026 — karar yuzeyinden yumusak cekildi; olcum/scan_signals surer.
    'radar1',
    'altin_setup',
    'platin_setup',
    'guclu_donus',
})

# GUCLU — iki rejimde de pozitif; zirve ailesi bu turda olculmedi, dokunulmaz
GUCLU_SCANNERS = frozenset({'zirve_devam', 'zirve_sikisma'})

# TEK REJIM — sadece yukselen tape'te pozitif (rejim kapisi TARTISILIYOR:
# CLAUDE.md "piyasa rejimi scanner filtresi olarak kullanilmaz" yasagiyla cakisiyor)
TEK_REJIM_SCANNERS = frozenset({
    'tavan_top30', 'er_C2', 'er_C8',
    'nadir_firsat', 'er_C3', 'er_C5', 'er_C7', 'er_C9', 'er_C11',
    'er_D2', 'er_D4', 'er_D5', 'er_D1', 'er_A5', 'er_A7', 'er_C1',
})


# ===============================================================
# 27 AGU 2026 — IS 4: VADE + SON KULLANMA + KARAR MASASI
#
# Bu tablo tarama filtresi degildir. Bir sinyalin hangi masada okunacagini,
# kac seans acik kalacagini ve hangi ihtiyat notuyla tasinacagini tek yerde
# tutar. Radar 2 burada "zayif" diye susturulmaz: 3-5 gunluk masadan cekilir,
# T+20 sabir masasinda aday olarak tutulur. Karne degil, vade etiketi degisir.
#
# `vade_gun` islem seansi sayisidir; takvim gunu degildir. `rozet` yalniz
# olcum esigi gecilmis ve etikete izin verilen hallerde True olabilir. Tavan
# Alarm'in iki rejim sonucu pozitif gorunse de N=65/124 oldugu icin False'tur.
# ===============================================================
SCANNER_VADE_POLICY = {
    # Kisa vade: Minervini 3 seanslik, C6 5 seanslik gecici adaydir.
    'minervini': {
        'vade_gun': 3,
        'masa': 'KISA',
        'durum': 'KISA_GECICI_ADAY',
        'etiket': '🟡 KISA MASASI · T+3',
        'rozet': False,
        'minimum_olay': None,
        'not': '3 seanslik kisa vade; mevcut orneklem cekirdek etiketi icin yeterli degil.',
    },
    'er_C6': {
        'vade_gun': 5,
        'masa': 'KISA',
        'durum': 'BIRINCI_SIRADA_GECICI_ADAY',
        'etiket': '🟡 KISA MASASI · T+5 GEÇİCİ ADAY',
        'rozet': False,
        'minimum_olay': 300,
        'not': 'Birinci siradaki gecici aday; yeni cetvelde T+5 dusen rejim negatiftir, 300 bagimsiz olay kapisi bekleniyor.',
    },
    # Sabir masasi: Tavan ailesi 20 seansliktir; iki yeni bulgu da burada.
    'tavan_top30': {
        'vade_gun': 20,
        'masa': 'SABIR',
        'durum': 'TEK_REJIM_BELIRSIZ',
        'etiket': '🟡 SABIR MASASI · T+20 TEK REJIM',
        'rozet': False,
        'minimum_olay': 150,
        'not': 'T+20 yukselen rejimde pozitif, dusen rejimde negatif; rozet yok.',
    },
    'tavan_alarm': {
        'vade_gun': 20,
        'masa': 'SABIR',
        'durum': 'BELIRSIZ_ORNEKLEM',
        'etiket': '⚪ SABIR MASASI · T+20 BİLMİYORUZ',
        'rozet': False,
        'minimum_olay': 150,
        'not': 'T+20 alfa iki rejimde de pozitif gorunuyor; rejim N=65/124 esigin altinda, rozet verilmez.',
    },
    'prelaunch_bos': {
        'vade_gun': 20,
        'masa': 'SABIR',
        'durum': 'NEGATIF_KARNE',
        'etiket': '⚪ SABIR MASASI · T+20 NEGATİF KARNE',
        'rozet': False,
        'minimum_olay': 150,
        'not': 'Kisa masadan tasindi; yeni acilis cetvelinde T+20 alfa iki rejimde de negatif, aday rozeti yok.',
    },
    'radar2': {
        'vade_gun': 20,
        'masa': 'SABIR',
        'durum': 'YANLIS_VADE_ADAYI',
        'etiket': '🟡 SABIR MASASI · T+20 ADAY',
        'rozet': False,
        'minimum_olay': 150,
        'not': '3-5 seansta negatif; T+20 iki rejimde pozitif. Zayif diye susturulmaz, kisa masaya girmez.',
    },
    'liderlik_aday': {
        'vade_gun': 20,
        'masa': 'SABIR',
        'durum': 'ADAY',
        'etiket': '🟡 SABIR MASASI · T+20 ADAY',
        'rozet': False,
        'minimum_olay': 150,
        'not': 'T+20 alfa iki rejimde pozitif; sabir masasinda aday olarak izlenir.',
    },
    'er_B11': {
        'vade_gun': 20,
        'masa': 'SABIR',
        'durum': 'BELIRSIZ_ORNEKLEM',
        'etiket': '⚪ SABIR MASASI · T+20 BİLMİYORUZ',
        'rozet': False,
        'minimum_olay': 150,
        'not': 'T+20 iki rejimde pozitif gorunuyor; rejim N=73/118 esigin altinda, rozet verilmez.',
    },
}

# Radar 2'nin durumu "zayif" degil, "yanlis vade"dir. Pipeline ve scan_signals
# aynen calisir; yalnizca KISA masasina alinmaz.
YANLIS_VADE_SCANNERS = frozenset({'radar2'})

# Karar katmanina girmeyen diger taramalar icin 20 seanslik genel kapanma
# siniri kullanilir. Bu, "ideal karar vadesi" degil, sinyalin sonsuza kadar
# tasinmamasi icin katalog/olcum alanindaki emniyet son kullanmasidir.
SCANNER_VADE_DEFAULT = {
    'vade_gun': 20,
    'masa': 'KATALOG',
    'durum': 'VADE_OLCUMU_BEKLIYOR',
    'etiket': '⚪ KATALOG · T+20 GÖZLEM · VADE BEKLİYOR',
    'rozet': False,
    'minimum_olay': None,
    'vade_kaynagi': 'GENEL_GOZLEM_SINIRI',
    'not': 'Karar vadesi bu turda muhurlenmedi; T+20 yalniz katalog son kullanma siniridir.',
}


def _coerce_date(value):
    """Tarih benzeri girdiyi timezone'dan bagimsiz date nesnesine cevirir."""
    if value is None:
        return None
    if hasattr(value, 'to_pydatetime'):
        try:
            value = value.to_pydatetime()
        except Exception:
            pass
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def scanner_vade_metadata(scan_type, signal_date=None, session_dates=None) -> dict:
    """Taramanin vade/masa/etiket alanlarini ve varsa son kullanma tarihini doner.

    `session_dates` verilirse tam fiyat kasasinin seans takvimi kullanilir;
    verilmezse BIST takvimindeki acik seanslar sayilir. Tarih verilmeden de
    alanlar doner, fakat son kullanma tarihi bilincli olarak None kalir.
    """
    key = str(scan_type or '').strip()
    policy = dict(SCANNER_VADE_POLICY.get(key, SCANNER_VADE_DEFAULT))
    horizon = policy.get('vade_gun')
    out = {
        'scan_type': key,
        **policy,
        'vade': f"T+{int(horizon)}" if horizon is not None else None,
        'son_kullanma_tarihi': scanner_son_kullanma_tarihi(
            key, signal_date, session_dates=session_dates
        ),
    }
    # Ingilizce anahtar, dis veri/gelecek UI baglantilarinda ayni alanin
    # yanlis adla tekrar hesaplanmasini onlemek icin yalnizca takma addir.
    out['expiry_date'] = out['son_kullanma_tarihi']
    out['alanlar_dolu'] = (
        out['vade_gun'] is not None
        and (signal_date is None or out['son_kullanma_tarihi'] is not None)
    )
    return out


def scanner_vade_gun(scan_type):
    """Taramanin muhurlu karar vadesini seans olarak doner; yoksa None."""
    return scanner_vade_metadata(scan_type).get('vade_gun')


def scanner_son_kullanma_tarihi(scan_type, signal_date, session_dates=None):
    """Sinyal tarihinden sonra muhurlu vadedeki seans tarihini ISO olarak doner."""
    key = str(scan_type or '').strip()
    horizon = SCANNER_VADE_POLICY.get(key, SCANNER_VADE_DEFAULT).get('vade_gun')
    base = _coerce_date(signal_date)
    if horizon is None or base is None:
        return None
    if session_dates is not None:
        dates = sorted({d for d in (_coerce_date(x) for x in session_dates) if d and d > base})
        if len(dates) >= int(horizon):
            return dates[int(horizon) - 1].isoformat()
        return None
    current = base
    remaining = int(horizon)
    while remaining:
        current += timedelta(days=1)
        if _is_bist_trading_day(current):
            remaining -= 1
    return current.isoformat()


def scanner_karar_masasi(scan_type):
    """Taramanin KISA/SABIR/KATALOG yerini doner."""
    return scanner_vade_metadata(scan_type).get('masa', 'KATALOG')


def scanner_vade_etiketi(scan_type):
    """Taramanin olcumle uyumlu, rozet iddiasi icermeyen vade etiketini doner."""
    return scanner_vade_metadata(scan_type).get('etiket', '')


def is_short_horizon_scanner(scan_type) -> bool:
    """Yalniz muhurlu KISA masasindaki taramalar icin True."""
    key = str(scan_type or '').strip()
    return key not in YANLIS_VADE_SCANNERS and scanner_karar_masasi(key) == 'KISA'


def is_patience_candidate(scan_type) -> bool:
    """SABIR masasinda aday olarak tasinacak taramalar icin True."""
    meta = scanner_vade_metadata(scan_type)
    return meta.get('masa') == 'SABIR' and meta.get('durum') in {
        'ADAY', 'YANLIS_VADE_ADAYI', 'BELIRSIZ_ORNEKLEM', 'TEK_REJIM_BELIRSIZ'
    }


# Olculmus T+20 alfa degerleri (panel rozetinde gosterilir). Kaynak: alfa_karne.py
ALFA_T20 = {
    'ict_sniper': -4.9, 'er_B1': -4.8, 'er_A2': -4.2, 'vip_formasyon': -3.9,
    'er_A1': -3.7, 'er_A8': -3.2, 'er_A6': -2.9, 'rs_leaders': -2.9,
    'er_D3': -2.8, 'er_A9': -2.7, 'er_A4': -2.5, 'harmonik_confluence': -2.1,
    'er_A3': -2.0, 'er_B5': -1.9, 'tekli_altin': -0.9,
    'er_B11': 2.4,
}


def alfa_deger(scan_type):
    """Olculmus T+20 alfa (endekse gore fark, yuzde). Bilinmiyorsa None."""
    return ALFA_T20.get(scan_type)


def is_ai_suppressed(scan_type) -> bool:
    """Bu tarama AI prompt'a ve karar panellerine cikmali mi? (True = CIKMASIN)

    Olcum: iki rejimde de negatif alfa. Master Scan taramasi ve scan_signals
    kaydi bundan ETKILENMEZ — sadece karar yuzeyinden cekilir.
    """
    if not ZAYIF_TARAMA_AI_BASTIR:
        return False
    return scan_type in DAHA_ZAYIF_SCANNERS or scan_type in ZAYIF_SCANNERS


def alfa_etiketi(scan_type) -> str:
    """Panelde/logda gosterilebilir kisa etiket. Bilinmiyorsa ''."""
    # Zayiflik uyarisi vade etiketinden daha onceliklidir. Ayni tarama
    # ileride iki listeye de girerse uyarinin kaybolmamasi icin iki bilgi
    # birlikte gosterilir.
    _vade = scanner_vade_etiketi(scan_type) if scan_type in SCANNER_VADE_POLICY else ""

    def _with_vade(_uyari):
        if _vade and _uyari:
            return f"{_uyari} · {_vade}"
        return _uyari or _vade

    if scan_type in DAHA_ZAYIF_SCANNERS: return _with_vade("⛔ DAHA ZAYIF")
    if scan_type in ZAYIF_SCANNERS:      return _with_vade("🔴 ZAYIF")
    if scan_type in GUCLU_SCANNERS:      return _with_vade("🟢 IKI REJIMDE POZITIF")
    if scan_type in TEK_REJIM_SCANNERS:  return _with_vade("🟡 SADECE YUKSELEN TAPE'TE")
    return _vade


ER_ELIT_SCORE_MIN = 45   # Güncel vitrin gösterim eşiği; puanın kendisi backtest özetinden gelir.

# Tarama açıklamaları — SADE, GERÇEK, jargonsuz (19 Haz 2026 — "hiç bilmeyen biri bile anlasın").
# Her cümle taramanın GERÇEKTE ne aradığını anlatır; pazarlama/fluff yok.
SCANNER_PLAIN_DESC = {
    'platin':    'Hem borsadan güçlü, hem yükseliş trendinde, hem de henüz aşırı pahalı değil — sistemin en seçici, en nadir sinyali.',
    'altin':     'Borsanın genelinden daha iyi gidiyor ve işlem hacmi artıyor; üstelik fiyatı henüz son ayların ucuz bölgesinde.',
    'prelaunch': 'Uzun süre dar bir bantta sıkıştıktan sonra önemli bir direnç seviyesini yukarı kırdı — büyük hareket yeni başlıyor olabilir.',
    'harmonik':  'Fiyat bir grafik kalıbının dönüş bölgesine ulaştı; geçmiş karne zayıf olduğu için bu yalnız gözlem bilgisidir.',
    'vip':       'Borsadan güçlü bir hissede tanınmış bir formasyon (fincan-kulp, üçgen gibi) oluştu.',
    'erken':     'Hisse asıl harekete geçmeden önce, hareketin ilk küçük işaretlerini verdi.',
    'gizli':     'Fiyat sakin dururken işlem hacmi sessizce artıyor — birileri belli etmeden topluyor olabilir.',
    'minervini': 'Hisse istikrarlı bir yükseliş trendinde; kısa ve uzun vadeli fiyat ortalamalarının hepsinin üstünde.',
    'rs':        'Son dönemde borsa endeksinden belirgin daha çok kazandırdı — piyasanın önünde gidiyor.',
}

def _guc_plain(guc):
    """🟢/🔴 yerine son kullanıcının anlayacağı sade konum ifadesi."""
    if '🟢' in guc: return ("şu an güçlü konumda", "#22c55e")
    if '🔴' in guc: return ("şu an zayıf/dipte konumda", "#ef4444")
    return ("şu an orta konumda", "#f59e0b")
