# Patron Terminal — CLAUDE.md
# app.py için hızlı navigasyon haritası (~22900+ satır — 27 May 2026 BIST takvim + Smart Money 3-kart + tatil fix sonrası)
# Bu dosyayı GÜNCELLEMEYİ UNUTMA: app.py'ye büyük değişiklik yapınca ilgili satır numarasını buraya yaz.

## Genel Mimari
- **Dil/Framework**: Python + Streamlit
- **Ana dosya**: `app.py` (~22900+ satır)
- **Yardımcı modül**: `bist_calendar.py` — BIST işlem takvimi (tatil/arefe/RVOL normalizer) — app.py + smr_core.py her ikisi de import eder
- **Veri kaynağı**: Yahoo Finance (`yfinance`), parquet cache (`get_batch_data_cached`)
- **DB**: SQLite (watchlist)

---

## Bölüm Haritası — 37 Bölümün Satır Rehberi

Kaynak kodda `# BÖLÜM N —` yorumlarıyla işaretlendi. Satır numaraları KESİN (grep ile doğrulandı).

| # | Satır | Başlık |
|---|---|---|
| B1 | 6 | BAĞIMLILIKLAR VE KÜTÜPHANE İÇE AKTARIMLARI |
| B2 | 50 | TARAMA CACHE SİSTEMİ |
| B3 | 631 | VERİTABANI (SQLite) |
| B4 | 856 | VARLIK LİSTELERİ VE KATEGORİLER |
| B5 | 1039 | SESSION STATE VE CALLBACK YÖNETİMİ |
| B6 | 1085 | VERİ ÇEKME VE ÖNBELLEKLEME MOTORU |
| B7 | 2128 | TEKNİK ANALİZ FONKSİYONLARI |
| B8 | 2281 | FORMASYON TARAMA SİSTEMİ (CHART PATTERNS) |
| B9 | 3258 | STP SİNYAL TARAMASI |
| B10 | 3303 | GİZLİ BİRİKİM TARAMASI (HIDDEN ACCUMULATION) |
| B11 | 3471 | RADAR 1 VE RADAR 2 TARAMALARI |
| B12 | 3838 | HACİM ANALİZ MODÜLLERİ |
| B13 | 4097 | KIRILIM TARAMALARI (BREAKOUT SCANNER) |
| B14 | 4324 | TEMEL SKOR VE MASTER SKOR |
| B15 | 4395 | GÜÇLÜ DÖNÜŞ ADAYLARI TARAMASI |
| B16 | 4620 | PRE-LAUNCH BOS TARAMASI |
| B17 | 5038 | ARZ/TALEP BÖLGELERİ TESPİTİ (SUPPLY/DEMAND ZONES) |
| B18 | 5142 | ICT SETUP TARAMASI |
| B19 | 5317 | ROYAL FLUSH NADİR FIRSAT TARAMASI |
| B20 | 5451 | MİNERVİNİ SEPA METODU VE RS MOMENTUM LİDERLERİ |
| B21 | 6051 | SENTİMENT SKOR SİSTEMİ |
| B22 | 6284 | ICT DERİN ANALİZ VE FİYAT HAREKETİ DNA |
| B23 | 7865 | BANNER / ROZET RENDER FONKSİYONLARI |
| B24 | 8119 | HARMONİK FORMASYONLARI (XABCD) TESPİTİ |
| B25 | 8491 | HARMONİK CONFLUENCE MOTORU |
| B26 | 8722 | SUPERTREND, FİBONACCİ VE Z-SCORE MOTORLARİ |
| B27 | 8981 | PİYASA REJİMİ VE KONVİKSİYON SKORU |
| B28 | 9342 | GRAFİK VE GÖRSELLEŞTİRME FONKSİYONLARI |
| B29 | 10379 | DETAY KARTI VE PANEL RENDER SİSTEMİ |
| **B37** | **12360** | **ERKEN RADAR SENARYO MOTORU** _(sonradan eklendi, sıra dışı)_ |
| B30 | 13942 | 8 MADDELİK HİBRİT YOL HARİTASI |
| B31 | 15074 | ROADMAP VE BİRLEŞİK SİNYAL PANELİ |
| B32 | 16189 | GENEL ÖZET VE SAĞLIK SİNYALLERİ PANELİ |
| B33 | 17390 | ELİT TARAMA SİSTEMİ (Altın/Platin Fırsat) |
| B34 | 17738 | ANA SAYFA PANEL UI (Tarama Sonuçları & Arayüz) |
| B35 | 18986 | AI PROMPT SİSTEMİ |
| B36 | 20073 | ANA STREAMLIT UYGULAMA GİRİŞ NOKTASI |

---

## Veri Katmanı (satır 1–630)

| Fonksiyon | Satır | Açıklama |
|---|---|---|
| `is_yahoo_update_needed` | 23 | Parquet cache stale mi? |
| `init_db` / `load_watchlist_db` | 203–228 | SQLite watchlist |
| `apply_volume_projection` | ~1170 | Hacim projeksiyon — **arefe** aware (27 May 2026): tatil→projeksiyon yok, arefe→12:30 close_min + lineer progress (30dk guard) |
| `get_benchmark_data` | 482 | Benchmark (BIST100 vs S&P) |
| `get_batch_data_cached` | 501 | **Ana veri çekme** — parquet cache + yfinance |
| `get_safe_historical_data` | 577 | Tek ticker güvenli fetch |

---

## Teknik Hesaplama Fonksiyonları (B7–B8, satır 2128–2281)

⚠️ `~` ile işaretli satır numaraları yaklaşık — kesin konum için Bölüm Haritası ile çapraz kontrol et.

| Fonksiyon | Satır | Açıklama |
|---|---|---|
| `calculate_harsi` | ~630 | HaRSI indikatörü |
| `check_lazybear_squeeze_breakout` | ~688 | Squeeze breakout |
| `get_ma_data_for_ui` | ~731 | MA verileri |
| `fetch_stock_info` | ~766 | Temel bilgi |
| `get_tech_card_data` | ~791 | Teknik kart verisi |
| `calculate_synthetic_sentiment` | ~874 | Sentetik sentiment |
| `get_obv_divergence_status` | **2110** | OBV diverjans + **Chaikin MF (20g) teyit** (27 May 2026) — 3 yeni çelişki başlığı (ŞÜPHELİ/SAHTE GÜÇ/ZAYIF TEYİT) |
| `find_smart_sr_levels` | ~1205 | Destek/Direnç seviyeleri |
| `calculate_volume_delta` | ~2416 | Delta hacim |
| `calculate_volume_profile_poc` | ~2432 | POC hesabı |
| `calculate_volume_profile` | ~2484 | Hacim profili |
| `get_fundamental_score` | ~2819 / ~3111 | Temel analiz skoru (2 versiyon!) |
| `calculate_master_score` | ~3162 | Master skor |
| `detect_supply_demand_zones` | ~3325 | Arz/Talep bölgeleri |
| `calculate_supertrend` | **8725** | SuperTrend (B26) |
| `calculate_fib_levels` | **8781** | Fibonacci seviyeleri (B26) |
| `calculate_z_score_live` | **8835** | Z-score (B26) |
| `detect_market_regime` | **8984** | Piyasa rejimi tespiti (B27) |
| `calculate_conviction_score` | **9123** | Konviksiyon skoru (B27) |
| `get_advanced_levels_data` | **9295** | Gelişmiş seviyeler (B27) |
| `calculate_grandmaster_score_single` | ~6188 | Grandmaster tek hisse (B20 sonu) |

---

## Scanner Fonksiyonları (B8–B20, satır 2281–6051)

⚠️ `~` ile işaretli satır numaraları yaklaşık — kesin bölüm başlangıçları için Bölüm Haritası'nı kullan.

| Fonksiyon | Satır | Açıklama |
|---|---|---|
| `process_single_stock_stp` | ~3260 | STP tek hisse işleme (B9) |
| `scan_stp_signals` | ~3285 | **STP tarama** |
| `process_single_accumulation` | ~3305 | Akümülasyon tek hisse (B10) |
| `scan_hidden_accumulation` | ~3430 | **Gizli akümülasyon tarama** |
| `process_single_radar1` | ~3473 | Radar1 tek hisse (B11) |
| `analyze_market_intelligence` | **3636** | **Radar1 tarama** (period "1y" — cache key uyumlu) |
| `process_single_radar2` | ~3640 | Radar2 tek hisse |
| `radar2_scan` | ~3840 | **Radar2 tarama** |
| `process_single_breakout` | ~4158 | Breakout tek hisse (B13) — **OMI filtresi** (27 May 2026): OBV momentum < -0.5σ ise breakout reddedilir |
| `agent3_breakout_scan` | **4247** | **Breakout tarama** (period "1y" — cache key uyumlu) |
| `process_single_confirmed` | ~4235 | Onaylı kırılım tek hisse |
| `scan_confirmed_breakouts` | ~4295 | **Onaylı kırılım tarama** |
| `calculate_royal_flush_3_0_setup` | ~5319 | RF3 hesaplama (B19) |
| `scan_rf3_batch` | ~5380 | **RF3 toplu tarama** |
| `scan_nadir_firsat_batch` | ~5345 | **Royal Flush Nadir Fırsat** (B19) |
| `process_single_kesin_donus` | ~4395 | Kesin dönüş tek hisse (B15) |
| `scan_kesin_donus_batch` | ~4450 | **Kesin dönüş tarama** |
| `scan_guclu_donus_batch` | ~4410 | **Güçlü Dönüş tarama** (B15) |
| `scan_prelaunch_bos` | ~4622 | **Pre-Launch BOS tarama** (B16) |
| `process_single_ict_setup` | ~5144 | ICT sniper tek hisse (B18) |
| `scan_ict_batch` | ~5260 | **ICT Sniper tarama** |
| `scan_minervini_batch` | ~5453 | **Minervini tarama** (B20) |
| `scan_rs_momentum_leaders` | **5997** | **RS Momentum tarama** (period "1y" — cache key uyumlu) |
| `scan_chart_patterns` | ~2285 | **Chart pattern tarama** (B8) |
| `scan_bear_traps` | ~2340 | **Bear trap tarama** |
| `scan_golden_pattern_agent` | ~2420 | **Altın Fırsat (AF) tarama** |
| `scan_grandmaster_batch` | ~6337 | **Grandmaster toplu tarama** |
| `compile_top_20_summary` | ~4200 | TOP20 derleme |
| `compile_confluence_hits` | ~4260 | Confluence derleme |
| `scan_harmonic_confluence_batch` | **8612** | **Harmonik Confluence tarama** (B25) |
| `scan_erken_radar_batch` | **12157** | **Erken Radar toplu tarama** (B37) |

---

## ICT Deep Analysis — KRİTİK BÖLGE (B22, satır 6284–7865)

| Fonksiyon | Satır | Açıklama |
|---|---|---|
| `get_deep_xray_data` | ~6560 | X-Ray veri |
| `detect_ict_reversal` | ~6580 | ICT reversal tespiti |
| `detect_price_action_with_context` | ~6640 | Price action tespiti |
| `calculate_ict_deep_analysis` | **~6810** | **ANA ICT analiz fonksiyonu** — OB, FVG, bias, zone, model_score, ob_age, fvg_age, struct_age |
| `calculate_price_action_dna` | ~7270 | Price Action DNA |

### `calculate_ict_deep_analysis` içindeki kritik bölümler:
- **OB ATR genişlik filtresi** (27 May 2026): `_ob_width_ok()` — OB genişliği > 1.8 × ATR(14) ise reddedilir. `_atr14` serisi ~6747'de hesaplanır, `_ob_width_ok` ~6759'da tanımlı.
- **sw_highs/sw_lows tespiti**: ~6870–6930
- **Bias belirleme**: ~6940–7020
- **Zone (premium/discount)**: ~7040–7070
- **Displacement**: ~7080–7120
- **Bottom Line hedefleri + cluster detection**: ~7130–7180 (`second_gap`, `deep_gap`)
- **FVG tespiti**: ~7185–7220
- **OB tespiti + validity** (demand above price fix + ATR width filter): bullish ~6810–6830, bearish ~6840–6860
- **Model score hesabı** (_m1–_m5): ~7275–7295

---

## Render (UI) Fonksiyonları (B23+B28+B29, satır 7865–12360)

| Fonksiyon | Satır | Bölüm | Açıklama |
|---|---|---|---|
| `render_golden_trio_banner` | ~7868 | B23 | Altın Üçlü banner |
| `render_royal_flush_3_0_banner` | ~7910 | B23 | RF3 banner |
| `render_royal_flush_banner` | ~7950 | B23 | RF banner |
| `calculate_harmonic_confluence` | 8494 | B25 | Harmonik confluence hesabı |
| `render_harmonic_confluence_banner` | 8562 | B25 | Harmonik banner |
| `render_nadir_firsat_banner` | 8666 | B25 | Royal Flush Nadir Fırsat banner |
| `_gauge_chart_b64` | 9347 | B28 | Gauge chart PNG üretici |
| `render_gauge_chart` | 9443 | B28 | Gauge chart Streamlit render |
| `_main_price_chart_b64` | 9606 | B28 | Ana fiyat grafiği (PNG) |
| `_main_price_chart_plotly` | 9798 | B28 | Ana fiyat grafiği (Plotly/SMC) |
| `_sparkline_b64` | 10192 | B28 | Mini sparkline grafik |
| `render_sentiment_card` | 10258 | B28 | Kurumsal ilgi (sentiment) kartı |
| `render_deep_xray_card` | 10335 | B28 | Derin teknik röntgen kartı |
| `render_detail_card_advanced` | 10384 | B29 | Detay kartı |
| `render_synthetic_sentiment_panel` | 10505 | B29 | Sentetik sentiment paneli |
| `render_smart_volume_panel` | 10635 | B29 | Smart Hacim Paneli |
| `render_price_action_panel` | 10994 | B29 | Price Action paneli |
| `calculate_smart_money_score` | 11333 | B29 | Smart Money skoru hesabı |
| `render_erken_radar_panel` | 11870 | B29 | Erken Radar panel (hisse detayı) |
| `render_ict_certification_card` | 12093 | B29 | ICT sertifikasyon kartı |
| `render_lorentzian_panel` | ~3889 | B12 | Lorentzian panel |

### `render_ict_deep_panel` → B37'de: satır 13219 (aşağıdaki tabloya bak)

---

## Genişletilmiş Render — Yol Haritası, Paneller, Özet (satır 12360–17738)

| Fonksiyon | Satır | Bölüm | Açıklama |
|---|---|---|---|
| `render_ict_deep_panel` | **13219** | B37 | **ANA ICT deep panel** — tüm ICT UI burası |
| `render_levels_card` | 13580 | B37 | Seviyeler kartı |
| `render_minervini_panel_v2` | 13673 | B37 | Minervini paneli |
| `_mini_pattern_chart_b64` | 13753 | B37 | Formasyon mini grafik |
| `calculate_multi_timeframe_alignment` | 13947 | B30 | MTF (Çok Zaman Dilimi) hizalama |
| `calculate_8_point_roadmap` | 14048 | B30 | 8-nokta roadmap hesabı |
| `_mini_harmonic_chart_b64` | 14574 | B30 | Harmonik mini grafik |
| `_build_harmonic_analysis` | 14668 | B30 | Harmonik analiz builder |
| `_harmonik_dialog` | 14800 | B30 | Harmonik formasyon popup |
| `_formasyon_dialog` | 14934 | B30 | VIP Formasyon popup |
| `render_roadmap_8_panel` | 15077 | B31 | 8-madde Roadmap paneli |
| `render_unified_signals_panel` | 15467 | B31 | Birleşik sinyal paneli |
| `_render_genel_ozet_panel` | 16193 | B32 | **GENEL ÖZET paneli** |
| `_render_health_signals_panel` | 17141 | B32 | Sağlık sinyalleri paneli |
| `get_golden_trio_batch_scan` | 17394 | B33 | **Elit Tarama** — Altın + Platin Fırsat batch |

### `render_ict_deep_panel` (satır 13219) içindeki kritik bölümler:
- **Header (model score badge + fiyat)**: ~13225–13250
- **ob_desc dinamik metin** (9 senaryo): ~13260–13320
- **mt_title / mt_desc (Alıcılar/Satıcılar Baskın)**: ~13330–13360
- **Bottom Line kartı**: ~13360–13390
- **Age badge'leri** (ob_age, fvg_age, struct_age): ~13225–13260

---

## UI / Sayfa Yapısı (satır 17311–22372)

| Bölüm | Satır | Açıklama |
|---|---|---|
| **Sidebar** | 17311 | Sol panel (B32 içinde) |
| **B34 başlangıcı** | 17738 | ANA SAYFA PANEL UI |
| **Kategori / Varlık / Master Scan** | 17744 | 3 kolonlu üst menü (col_cat, col_ass, col_btn) |
| **Master Scan butonu** | **17764** | `💎 TÜM PİYASAYI TARA (MASTER SCAN)` |
| Master scan adımları | 17789–17904 | 14 adım (önbellek → Erken Radar) |
| **B35 — AI Prompt** | **18986** | Senaryo bazlı AI prompt üretici |
| **B36 — Streamlit Giriş** | **20073** | Sayfa yapısı, tam ekran dialog, sol/sağ sütun |
| `_show_fullscreen_chart` | 20084 | SMC Derin Yapı dialog (tam ekran) |
| **`_render_left_col`** | **20442** | **Sol sütun** — tüm hisse detay panelleri |
| **`_render_right_col`** | **21909** | **Sağ sütun** — tarama sonuçları |

### Master Scan sırası (satır 17789–17904):
```
1.  get_batch_data_cached.clear() + reload  (~17792)
2.  scan_ict_batch                          (~17797)
3.  scan_nadir_firsat_batch                 (~17802)
4.  get_golden_trio_batch_scan              (~17806)  → golden + platin + tekli
5.  scan_hidden_accumulation                (~17825)
6.  analyze_market_intelligence + radar2_scan (~17830)
7.  scan_harmonic_confluence_batch          (~17837)
8.  scan_minervini_batch                    (~17842)
9.  scan_rs_momentum_leaders                (~17846)
10. scan_guclu_donus_batch                  (~17850)
11. scan_prelaunch_bos                      (~17854)
12. scan_golden_pattern_agent               (~17858)
13. scan_erken_radar_batch + log            (~17864)
14. (site frontend JSON export — BIST only) (~17877)
```

---

## Önemli Notlar

### Performans
- `get_batch_data_cached`: parquet dosyasına cache eder, Yahoo'dan sadece eksik/stale tickerlar çeker
- `max_workers=5` (ThreadPoolExecutor) — scanner içi paralel işlem, güvenle 10'a çıkarılabilir
- Master Scan 14 adım sıralı çalışıyor → paralel gruplara bölünebilir

### ICT Mantığı
- **Demand OB (Talep)**: mutlaka fiyatın ALTINDA olmalı (`ob_high >= curr_price` → gösterme)
- **Supply OB (Arz)**: mutlaka fiyatın ÜSTÜNDE olmalı (`ob_low <= curr_price` → gösterme)
- **OB age tiers**: 0–5g = Taze (yeşil), 6–15g = Orta (sarı), 16g+ = Eski (kırmızı)
- **Model score** (5 kriter): Bias Net + Doğru Bölge + OB Aktif + FVG Açık + Displacement (Güçlü+Hacim)
- **sw_highs/sw_lows**: 3-element tuple `(date_index, price, bar_position_i)`

### Erken Radar (B37, satır 12360–13942)
- 40+ senaryo — A/B/C/D kategorisi, ★★★★★ yıldız sistemi
- `evaluate_erken_radar` (13127): tek hisse senaryo değerlendirmesi
- `scan_erken_radar_batch` (12157): toplu tarama, sonuçları session_state'e yazar
- `log_erken_radar_signals` (12263): scan sonuçlarını signals.db'ye loglar
- `get_scenario_ages_batch` (12306): senaryoların kaç gündür aktif olduğu
- Site JSON export: Master Scan sonu BIST için 5★ sinyalleri `erken_radar.json`'a yazar

### Çözülen Sorunlar (27 May 2026 — Oturum 1: Sinyal Kalitesi)
- ✅ **OB Genişliği** — ATR(14) × 1.8 üst sınır filtresi eklendi (`calculate_ict_deep_analysis`, _ob_width_ok). Gürültü mumlar elendi, %30-40 ICT hassasiyet artışı bekleniyor.
- ✅ **Period Cache Key Mismatch** — 3 scanner "1y"a standartlaştı (analyze_market_intelligence, agent3_breakout_scan, scan_rs_momentum_leaders). Master Scan'de %25-35 hızlanma bekleniyor.
- ✅ **OBV Chaikin MF teyit katmanı** — get_obv_divergence_status'a CMF(20) eklendi. OBV-CMF çelişkisi olunca "ŞÜPHELİ GİRİŞ / SAHTE GÜÇ / ZAYIF TEYİT" başlığıyla işaretleniyor.
- ✅ **OMI Breakout filtresi** — process_single_breakout'a OBV Momentum Index eklendi. < -0.5σ ise fake breakout sayılıyor, çıktıya OMI ✓/⚡ rozeti.
- ✅ **Master Scan ilerleme barı %99'da takılma** — `progress(100)` disk save'den önce yapılıyor.

### Çözülen Sorunlar (27 May 2026 — Oturum 2: OBV/CMF/OMI Kapsamı + Smart Money)
- ✅ **process_single_confirmed OMI filtresi** — Onaylı kırılım taramasına da OMI filtresi eklendi (breakout'ta vardı, confirmed'da yoktu). Aynı mantık: < -0.5σ → reddedilir.
- ✅ **calculate_price_action_dna CMF katmanı** — PA-DNA'nın kendi OBV bloğuna CMF(20) hesabı eklendi; 3 çelişki senaryosunda başlık downgrade: "⚠️ ŞÜPHELİ GİRİŞ / SAHTE GÜÇ / ZAYIF TEYİT (CMF Çelişkili/OBV-CMF Çelişkisi/OBV güçlü CMF zayıf)".
- ✅ **`_obv_map` 3 yeni başlık** — `_render_genel_ozet_panel` içindeki `_obv_map` sözlüğüne yukarıdaki 3 CMF çelişki başlığı eklendi. Önceden fallback "nötr" gösteriyordu.
- ✅ **AI Prompt OMI + OB ATR Filtresi blokları** — AI Prompt'a "Kırılım Kalitesi Filtresi (OMI)" ve "Kurumsal İz Filtresi (OB ATR Genişlik)" başlıklı iki yeni açıklama bloğu eklendi.
- ✅ **TOP 20 tek-kaynak filtresi** — `compile_top_20_summary`'de: sadece 1 kaynaktan gelen hisseler Nadir Fırsat değilse eleniyor. Sahte sinyalleri azaltır.
- ✅ **Smart Money Hacim Analizi — 6-tile genişleme** — `render_smart_volume_panel` büyük güncelleme:
  - 5-tile → 6-tile grid (Tile 6: OBV+CMF durumu)
  - VA Proximity mini slider bar (sadece "İÇİNDE" konumunda)
  - 5G RVOL trend mini bar grafiği (↑ ivmeleniyor / ↓ yavaşlıyor / → sabit)
  - Panel konsensüs skoru header badge (0-100, 5 sinyal oylaması)
  - Tile 5 border-right fix, CMF regex genişletme (nötr yakalamak için), konsensüs kalibrasyon (1 pozitif=HAFİF ALIM, 2+=ALIM BASKISI)

### Çözülen Sorunlar (27 May 2026 — Oturum 3: BIST Takvim Modülü)
- ✅ **`bist_calendar.py` modülü** — Yeni bağımsız modül. 2026–2028 tam takvim (milli tatiller + Ramazan/Kurban Bayramı + arefe günleri). `AREFE_SESSION_RATIO = 150/480 ≈ 0.3125`. API: `is_trading_day / is_half_day / is_closed / get_rvol_day_factor / get_session_hours / get_day_label`. İki dosya da `ImportError` fallback ile korunuyor.
- ✅ **`apply_volume_projection` arefe desteği** — Tatil günü: projeksiyon yok (erken return). Arefe günü: ayrı dal, 12:30 close_min, lineer progress, 30dk guard (kısa seans olduğundan U-shape gereksiz).
- ✅ **RVOL normalizer (app.py ×2)** — İki RVOL hesabında (satır ~7450, ~7992): `avg_v` yerine `avg_v * _bist_rvol_factor()`. Arefe günü beklenen hacim %31 → RVOL 1.0 = arefe normali.
- ✅ **5G RVOL barları tarih-bazlı normalizer** — Her bar için `_bist_rvol_factor(_bar_dt5)` ile o günün faktörü; geçmişteki arefe günleri de doğru normalize edilir.
- ✅ **Smart Money Tile 4 arefe etiketi** — `"Yüksek Hacim (Arefe)"` + `"arefe kısa seans normalizer uygulandı (÷0.31)"` notu.
- ✅ **Master Scan tatil/arefe banner** — Kapalı gün: `st.warning`, arefe günü: `st.info` — scan çalışır ama kullanıcı bilgilendirilir.
- ✅ **smr_core.py RVOL normalizer** — Bot analizlerinde de arefe normalizer aktif. `rvol_tag`'e `" (Arefe kısa seans)"` notu eklendi. VPS deploy tamamlandı (commit `84c3d78`).

### Çözülen Sorunlar (27 May 2026 — Oturum 4+5: Smart Money Panel + Tatil Günü Fix)
- ✅ **Smart Money 3 Büyük Özet Kart** — `render_smart_volume_panel` (~10635): tile grid'in üstüne 3 kart eklendi (`_big_card_html` helper). c1=Anlık Baskı (delta+POC), c2=Akıllı Para (OBV+CMF), c3=Hacim Trendi (RVOL). Kart düzeni: q_label sol, ikon+verdict sağ (flex space-between), altında açıklama.
- ✅ **Tile görsel iyileştirmeleri** — Tile 1: `_poc_bar_html` POC uzaklık barı (±%15 skala). Tile 3: `_delta_strength_html` delta güç barı. Tile 5: `_delta_5d_dots_html` 5G kapanış yön dotları. Tile 6: "Kafa Çev. ↑/↓" → "Yön Değiştiriyor ↑/↓", "Hacimsiz" → "Zayıf İvme"; 80 karakter kırpma kaldırıldı. VA Proximity barı artık 3 konumda (İÇİNDE/ALTINDA/ÜSTÜNDE) gösteriliyor.
- ✅ **BIST Tatil Günü Çok Katmanlı Fix** — Bayram/tatil günlerinde yfinance 0-hacimli bar koyuyor → "Gün içi hacim oluşmadı" / RVOL=0 / delta=0 hataları. 5 katmanlı çözüm:
  1. **Kaynak fix** (`calculate_price_action_dna`, ~7255): `while` döngüsüyle trailing 0-volume barlar siliniyor → tüm tüketicilere otomatik yayılır.
  2. **Smart Money override** (`render_smart_volume_panel`, ~10944): `_today_closed` flag + son geçerli seans verisi yükleme + ⛔ chip.
  3. **Genel Özet override** (`_render_genel_ozet_panel`, ~16900): `_gs_today_closed` flag + son seans RVOL/cum_delta recalc + ⛔ chip başa (`try/except NameError` koruması).
  4. **AI Prompt** (`build_ai_prompt`, B35 ~19632): `_ai_holiday_note` promptun başına ekleniyor (kapalı gün etiketi + son seans tarihi + arefe ÷0.3125 uyarısı). Post-prompt `str.replace` 6 yerde "Bugüne ait" → "Son seansa ait" geçişi.
  5. **smr_core.py bot** (`_base_data_block` + `build_ai_prompt` + `build_ai_prompt_gorev1`): `_today_closed_sc` Vol override + `_bot_holiday_note` prepend (PRO + ELITE tier ikisi de).
- ✅ **Teknik Seviyeler panel** (`render_levels_card`, satır 13580): Üst başlık şeridi tamamen kaldırıldı. Grup etiketleri → "KISA VADE ORT." / "ORTA VADE ORT."
- ✅ **Panel taşıma** — `_render_health_signals_panel` (TEKNİK GÖRÜNÜM gauge) + ICT BOTTOM LINE bloğu sidebar'dan sağ sütuna taşındı (`_render_right_col`, CANLI SİNYALLER altına). Sidebar'da yorum satırı bırakıldı.
- ✅ **Container yükseklikleri** — Sol sütun (col_left): satır **22780**, `height=2500`. Sağ sütun (col_right): satır **23412**, `height=1800` (TEKNİK GÖRÜNÜM + ICT BOTTOM LINE eklenmesiyle yetmiyorsa artırılabilir).
- **Commits**: `a2fe029` (Smart Money render + holiday) · `15ad064` (Genel Özet + AI Prompt + PA-DNA holiday) · `2c87004` (smr_core.py bot holiday/arefe).

### ❌ KALICI KARAR — detect_market_regime HİÇBİR SCANNER'A BAĞLANMAZ
- **Neden:** Sistemin temel amacı dönüşleri yakalamak. Market regime filtresi, bir hisse çok zayıf rejimdeyken harekete başladığında (ki genelde tam da o zaman başlar) sinyali ezer → hareketin önemli kısmı kaçırılır. Bu fonksiyon UI/analiz amaçlı kullanılabilir ama scanner giriş filtresi olarak ASLA kullanılmaz.
- `detect_market_regime` fonksiyonu B27'de (satır 8984) kalabilir, silinmesine gerek yok. Sadece scanner filtresi yapılmaz.

### Bilinen Sorunlar / Eksikler
- Weinstein Stage Analysis: henüz eklenmedi
- Option 1 (max_workers 5→10): henüz uygulanmadı
- Master Scan paralelizasyonu: 14 adım sıralı, bağımsız olanlar gruplanabilir
- Backtesting motoru: signals.db'de sinyal geçmişi var ama hit rate hesaplanmıyor
- **TOP 20 `base_powers` reranking (BEKLEYEN)**: Backtestler tamamlandığında, `fetch_technical_engine_data` (~satır 5726) içindeki `base_powers` dict'i her taramanın gerçek hit rate'ine göre yeniden sıralanacak. Şu an skor ağırlıkları tahmini; en iyi backtest sonucu olan tarama en yüksek puanı alacak şekilde güncellenecek.
