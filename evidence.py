# -*- coding: utf-8 -*-
"""
evidence.py — KANIT TABLOLARI (backtest-temelli tier + puan haritaları)
=======================================================================
app.py bölme projesi Adım 3 (4 Tem 2026) — bu tablolar app.py'den BİREBİR
taşındı (davranış değişikliği YOK, sadece adres değişti). Tek kaynak burası;
app.py `from evidence import ...` ile kullanır.

İçerik:
  • SCANNER_TIER_MAP   — scan_type → (tier, hit, ret, ad, vade notu). 20g bazlı.
  • ER_ELIT_SCORE_MIN  — ELİT panel gösterim eşiği
  • SCANNER_PLAIN_DESC — taramaların jargonsuz açıklamaları
  • _guc_plain         — güç etiketinin kullanıcıya dönük sade karşılığı

Kalibrasyon günlüğü tabloların üzerindeki yorumlarda. Güncelleme kuralı:
sayı DEĞİŞTİRMEDEN önce signal_results ölçümü (bkz. memory/project_scoring_roadmap.md).
"""

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
    'er_B11':        ('TIER_2_GUVENILIR',48.6,  4.52, '📐 Erken Radar B11 (Tepe Yakını Sıkışma)', '20g · hit %49 ret %+4.5 · N=529 · eksi piyasada +4.5 getiren tek büyük-örnekli tarama; hit köprü eşiğinin 1.4p altında ama piyasa hit\'i ~%37 iken — rejim payıyla TIER_2'),
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
