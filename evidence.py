# -*- coding: utf-8 -*-
"""
evidence.py — KANIT TABLOLARI (backtest-temelli tier + puan haritaları)
=======================================================================
app.py bölme projesi Adım 3 (4 Tem 2026) — bu tablolar app.py'den BİREBİR
taşındı (davranış değişikliği YOK, sadece adres değişti). Tek kaynak burası;
app.py `from evidence import ...` ile kullanır.

İçerik:
  • SCANNER_TIER_MAP   — scan_type → (tier, hit, ret, ad, vade notu). 20g bazlı.
  • ER_BACKTEST_SCORE  — Erken Radar senaryo puanları (iki-rejim backtest)
  • ER_ELIT_SCORE_MIN  — ELİT panel gösterim eşiği
  • SCANNER_PLAIN_DESC — taramaların jargonsuz açıklamaları
  • GUC_SCORE + _guc_score + _guc_plain — güç etiketi → backtest-temelli puan

Kalibrasyon günlüğü tabloların üzerindeki yorumlarda. Güncelleme kuralı:
sayı DEĞİŞTİRMEDEN önce signal_results ölçümü (bkz. memory/project_scoring_roadmap.md).
"""

# ===============================================================
# 6 Haz 2026 — SCANNER TIER MAP (signal_results backtest)
# Kaynak: backtest hit_10g + avg_ret_10g + sample size.
# AI prompt'a koşullu emit edilir — sadece bu ticker tier'lı bir scanner'dan flag aldıysa.
# ===============================================================
# 15 Haz 2026 GÜNCELLEME — 29.023 sinyal backtest panelinden yeniden kalibre edildi.
# Değişiklikler:
#   • er_A1: TIER_3_ORTA → TIER_1_ELIT (hit %66.7, exp %+4.02, 12/99 — kategori şampiyonu Geri Dönüş)
#   • er_B8: TIER_3_ORTA → TIER_2_GUVENILIR (hit %69.2, exp %+3.70, 26/46 — kategori şampiyonu Sıkışma)
#   • nadir_firsat (Royal Flush): KALDIRILDI — TIER_VADE_UZUN hipotezi REDDEDİLDİ
#     (5g/10g/20g hit %24-34, exp -%0.35, 71 sinyal — yeterli kanıt, AI'dan ÇIKAR)
#   • ICT Sniper, Radar 1: Zaten map'te yoktu (AI'da emit edilmiyor) — backtest negatif beklenti doğruladı,
#     map'e eklemiyoruz. UI'da çalışmaya devam, AI context'inde yok.
# ===============================================================
SCANNER_TIER_MAP = {
    # scan_type: (tier, hit_20g_pct, avg_ret_20g_pct, display_name, vade_notu)
    # ⚡ 24 HAZ 2026 KALİBRASYON — 9.529 olgunlaşmış 20g sonucu (signal_results, 29 Nis-19 Haz,
    #    split-kırpık -50..200). Eski elle-girilmiş 10g sayıları boğa-dönemi/küçük örnekti, gerçekle
    #    uyuşmuyordu. Artık BAZ METRİK = 20g (olgun, büyük N). Eşikler:
    #    TIER_1 (20g hit≥62 AND ret≥4.5, N≥25) · TIER_2 (hit≥53 AND ret≥2.5, VEYA köprü ret≥3.5 & hit≥50 & N≥50)
    #    TIER_3_ORTA (pozitif ama altı) · negatif ret → KALDIRILDI (alt yorumda).
    # ── TIER_1_ELIT — 20g'de gerçekten kazandıran 4 senaryo ──
    'er_D1':         ('TIER_1_ELIT',     76.0,  7.04, '⚠ Erken Radar D1 (Tek Güçlü Sinyal)',      '20g · hit %76 ret %+7.0 · N=50 · 24 Haz: TIER_2→1, 20g\'de en güçlü'),
    'er_A2':         ('TIER_1_ELIT',     70.0,  6.59, '🔄 Erken Radar A2 (Hacimli Tepki)',        '20g · hit %70 ret %+6.6 · N=37 · 24 Haz: TIER_3→1'),
    'er_A8':         ('TIER_1_ELIT',     64.0,  6.53, '⭐ Erken Radar A8 (Ucuz+Hacimli Atak)',     '20g · hit %64 ret %+6.5 · N=39 · 10g zayıf ama 20g güçlü'),
    'er_B8':         ('TIER_1_ELIT',     71.0,  4.90, '📐 Erken Radar B8 (Sıkışma Sonu)',         '20g · hit %71 ret %+4.9 · N=28 · KATEGORİ ŞAMPİYONU Sıkışma · TIER_2→1'),
    # ── TIER_2_GUVENILIR — sağlam destekleyici, ana hikaye olabilir ──
    'er_B11':        ('TIER_2_GUVENILIR',56.0,  6.25, '📐 Erken Radar B11 (Tepe Yakını Sıkışma)', '20g · hit %56 ret %+6.3 · N=160 · 24 Haz: TIER_3→2 (büyük örnek + güçlü ret)'),
    'er_A5':         ('TIER_2_GUVENILIR',58.0,  5.73, '🔄 Erken Radar A5 (Toparlanma Başlıyor)',  '20g · hit %58 ret %+5.7 · N=52 · TIER_3→2'),
    'er_C3':         ('TIER_2_GUVENILIR',56.0,  5.62, '🚀 Erken Radar C3 (Pullback+Hacimli Alım)', '20g · hit %56 ret %+5.6 · N=50'),
    'er_C2':         ('TIER_2_GUVENILIR',55.0,  4.43, '🚀 Erken Radar C2 (Ortalama Testi)',       '20g · hit %55 ret %+4.4 · N=545 · büyük örnek, sağlam'),
    'er_D2':         ('TIER_2_GUVENILIR',56.0,  4.11, '⚠ Erken Radar D2 (Karışık ama Dayanıklı)', '20g · hit %56 ret %+4.1 · N=96 · iki rejimde pozitif'),
    'er_A7':         ('TIER_2_GUVENILIR',53.0,  3.77, '🔄 Erken Radar A7 (Hacimli Toparlanma)',   '20g · hit %53 ret %+3.8 · N=266'),
    'prelaunch_bos': ('TIER_2_GUVENILIR',50.0,  3.58, '🚀 Pre-Launch BOS',                       '20g · hit %50 ret %+3.6 · N=70 · 15 Haz TIER_1 idi, tazede hit %50 — köprü kuralı TIER_2'),
    'er_A3':         ('TIER_2_GUVENILIR',56.0,  3.44, '🔄 Erken Radar A3 (İkili Dip)',            '20g · hit %56 ret %+3.4 · N=299'),
    'er_A4':         ('TIER_2_GUVENILIR',57.0,  2.76, '🔄 Erken Radar A4 (Yön Değiştiriyor)',     '20g · hit %57 ret %+2.8 · N=433 · TIER_3→2 (büyük örnek)'),
    # ── TIER_3_ORTA — pozitif ama zayıf; ana hikaye DEĞİL, diğer teyitler aranır ──
    'er_C8':         ('TIER_3_ORTA',     48.0,  3.93, '🚀 Erken Radar C8 (Yukarı Kanal Testi)',   '20g · hit %48 ret %+3.9 · N=143 · ret iyi ama hit zayıf'),
    'er_C5':         ('TIER_3_ORTA',     41.0,  2.42, '🚀 Erken Radar C5 (Bayrak Formasyonu)',    '20g · hit %41 ret %+2.4 · N=128 · hit düşük'),
    'er_C9':         ('TIER_3_ORTA',     51.0,  1.81, '🔄 Erken Radar C9 (Pullback Başlıyor)',    '20g · hit %51 ret %+1.8 · N=366 · 24 Haz: TIER_2→3 (10g negatif)'),
    'er_B5':         ('TIER_3_ORTA',     53.0,  1.83, '📐 Erken Radar B5 (Üçgen Daralma)',        '20g · hit %53 ret %+1.8 · N=135'),
    'er_C11':        ('TIER_3_ORTA',     45.0,  1.78, '🚀 Erken Radar C11 (Trendde Momentum)',    '20g · hit %45 ret %+1.8 · N=77 · TIER_2→3'),
    'er_D3':         ('TIER_3_ORTA',     50.0,  1.75, '⚠ Erken Radar D3 (Erken Aşama)',           '20g · hit %50 ret %+1.8 · N=570 · TIER_2→3 · ucuz bölge, dönüş teyidi yok'),
    'er_C7':         ('TIER_3_ORTA',     47.0,  1.72, '🚀 Erken Radar C7 (Sağlam Trend Yapısı)',  '20g · hit %47 ret %+1.7 · N=409 · 10g negatif, MA hizalı görünüm'),
    'er_A9':         ('TIER_3_ORTA',     49.0,  1.17, '🔄 Erken Radar A9 (Ucuz Bölgede Bekleyiş)','20g · hit %49 ret %+1.2 · N=718 · TIER_2→3 (büyük örnek vasat)'),
    'er_C6':         ('TIER_3_ORTA',     47.0,  1.08, '🚀 Erken Radar C6 (Piyasa Lideri)',        '20g · hit %47 ret %+1.1 · N=43'),
    'er_A6':         ('TIER_3_ORTA',     58.0,  0.57, '🔄 Erken Radar A6 (Dipte Sıkışma)',        '20g · hit %58 ret %+0.6 · N=45'),
    'er_D4':         ('TIER_3_ORTA',     49.0,  0.29, '⚠ Erken Radar D4 (Kurumsal Satış Riski)',  '20g · hit %49 ret %+0.3 · N=177 · ret ~0'),
    'er_A1':         ('TIER_3_ORTA',     38.5, -1.67, '🔄 Erken Radar A1 (Dipte Sessizlik)',      '20g · hit %38.5 ret -%1.7 PF 0.72 · N=91 (4 Tem ölçüm) · 10g güçlü (+%2.9, N=111) ama 20g\'de eriyor — terfi YOK, 28 Tem tam olgunlukta ZAYIF\'a düşürme değerlendir'),
    # ── KALDIRILDI (24 Haz 2026) — 20g getirisi NEGATİF, AI context'inden çıkarıldı ──
    # • er_B1 (Mükemmel Sıkışma): 15 Haz TIER_1 idi → tazede 20g hit %38 ret -%0.74, 10g hit %35 ret -%1.4
    #   (N20=69). İki vadede de negatif — kanıtla TIER_1\'den ZAYIF\'a düştü. UI\'da kalır, AI\'dan çıkar.
    # • er_D5 (Trend Bozuldu): 20g hit %38 ret -%0.54 (N37) — negatif, kaldırıldı.
    # Önceki kaldırılanlar (hâlâ geçerli): Royal Flush (nadir_firsat, hit %24-34), ICT Sniper (hit %39),
    # Radar 1 (hit %40.5), er_C1, guclu_donus — hepsi negatif/sıfır beklenti. UI\'da çalışır, AI\'dan gizli.
    # Q4 2026\'da örnek olgunlaşınca yeniden değerlendir.
}

# Erken Radar — iki-rejim (Mayıs boğa + Haziran ayı) backtest PUANI (19 Haz 2026, scanner_karne).
# Yüksek = gerçekten kazandırıyor. ELİT panel + mini panel SIRALAMA + FİLTRE bunu kullanır.
# Eski yıldız sistemi tasarımcının ilk tahminiydi (er_C6 5★ ama vasat, er_D2 2★ ama #2 kazanan) —
# bu puan gerçek getiri + iki-rejim dayanıklılık kanıtı. Altın madenlerini ÖNE çıkarır.
ER_BACKTEST_SCORE = {
    'B8': 100, 'D2': 74, 'C2': 71, 'C5': 66, 'C6': 60, 'A2': 58, 'B5': 53, 'A1': 53,
    'A7': 46, 'D1': 46, 'A8': 36, 'D4': 35, 'D5': 34, 'A3': 29, 'A4': 26, 'A5': 24,
    'C8': 19, 'B11': 15, 'D3': 13, 'C11': 13, 'A9': 12, 'C3': 11, 'C9': 10, 'C7': 6,
    'C1': 4, 'A6': 0, 'B1': 0,
}
ER_ELIT_SCORE_MIN = 45   # ELİT panelde gösterim eşiği — bunun altı "gold mine değil", gizlenir

# Tarama açıklamaları — SADE, GERÇEK, jargonsuz (19 Haz 2026 — "hiç bilmeyen biri bile anlasın").
# Her cümle taramanın GERÇEKTE ne aradığını anlatır; pazarlama/fluff yok.
SCANNER_PLAIN_DESC = {
    'platin':    'Hem borsadan güçlü, hem yükseliş trendinde, hem de henüz aşırı pahalı değil — sistemin en seçici, en nadir sinyali.',
    'altin':     'Borsanın genelinden daha iyi gidiyor ve işlem hacmi artıyor; üstelik fiyatı henüz son ayların ucuz bölgesinde.',
    'prelaunch': 'Uzun süre dar bir bantta sıkıştıktan sonra önemli bir direnç seviyesini yukarı kırdı — büyük hareket yeni başlıyor olabilir.',
    'harmonik':  'Fiyat, geçmişte sık sık yön değiştirdiği bir "dönüş bölgesine" belirli bir grafik kalıbıyla ulaştı.',
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


# Güç-filtreli taramalarda 🟢/🟡/🔴 → backtest-temelli efektif puan (19 Haz 2026).
# harmonik: iki-rejim ölçümü (tepe %90-100 / orta / dip %35) → güç gerçekten ayrıştırıyor.
# vip/prelaunch: ham düşük ama güçlü subset belirgin daha iyi (segmentasyon kanıtı).
GUC_SCORE = {
    'harmonik_confluence': {'🟢': 88, '🟡': 63, '🔴': 35},
    'vip_formasyon':       {'🟢': 58, '🟡': 40, '🔴': 22},
    'prelaunch_bos':       {'🟢': 48, '🟡': 30, '🔴': 12},
}

def _guc_score(scanner, guc):
    m = GUC_SCORE.get(scanner, {})
    if '🟢' in str(guc): return m.get('🟢', 50)
    if '🔴' in str(guc): return m.get('🔴', 20)
    return m.get('🟡', 40)
