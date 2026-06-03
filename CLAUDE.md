# Patron Terminal — CLAUDE.md
# Hızlı navigasyon. Sistemin TAMAMI: `memory/SMR_SISTEM_OZETI.md` (tek kaynak).
# Son güncelleme: 3 Haz 2026 Oturum 14 (AI Prompt v2 + Backtest UI + Feature Snapshot)

---

## 📑 Hangi soru için hangi bölüm?

| Sorum | Bak |
|---|---|
| "B12 / B22 hangi satırda başlıyor?" | [Bölüm Haritası](#bölüm-haritası--b1-b37-köşe-taşı-yorumları-grep-ile-doğrulandı) |
| "`calculate_X` / `scan_Y` / `render_Z` hangi satırda?" | [En sık fonksiyonlar](#en-sık-ihtiyaç-duyulan-fonksiyonlar) |
| "Master Scan'in 7. adımı ne?" / "Akış değişti, nereye not?" | [Master Scan akışı](#master-scan-akışı-b34-satır-17789-17904) |
| "Sol/sağ kolon container yüksekliği?" | [UI ana taşıyıcılar](#ui-ana-taşıyıcılar) |
| "AI prompt'a sektör/rejim eklenir mi?" / "Weinstein?" | [Kalıcı Yasaklar](#-kalıcı-yasaklar-memoryde-detay) |
| "Geçen oturumda ne yapıldı?" / "AI Prompt v2 neydi?" | [Son Oturum Notu](#son-oturum-notu--3-haz-2026-oturum-14) |
| "Oturum 9 / 11 / 12'de ne refactor edildi?" | [Önceki Oturumlar](#önceki-oturumlar-kısa-kronoloji--detay-smr_sistem_ozetimd) |
| "Hayalet Bar Plan B / CMF Phase 3 ne durumda?" | [Açık Konular](#açık-konular-devam-eden) |
| "Bu dosyaya ne zaman / ne yazayım?" | [NE ZAMAN güncelle](#-bu-dosyayı-ne-zaman-güncelle) |
| "Mimari / bot kuralları / deploy / VPS / DB / launch durumu?" | → `memory/SMR_SISTEM_OZETI.md` (bu dosya değil) |
| "Bot durumu / smr_core / smr_bot / Telegram kanalları?" | → `memory/project_bot_status.md` |
| "Formasyon motoru / zigzag / pattern detector geçmişi?" | → `memory/project_pattern_engine.md` |

---

## ⚠️ Bu dosyayı NE ZAMAN güncelle?

| Durum | Nereyi güncelle |
|---|---|
| Yeni Bölüm (B38+) eklendi | Buradaki Bölüm Haritası |
| Master Scan akışı değişti | Buradaki Master Scan listesi |
| Genel mimari / oturum notu / bot kuralı | `memory/SMR_SISTEM_OZETI.md` (BURASI DEĞİL) |
| AI prompt sistemi değişti | `memory/SMR_SISTEM_OZETI.md` "Son güncelleme" |

**Satır numarası uyarısı:** Aşağıdaki tablolarda satır numaraları **kayabilir**. Kullanmadan önce `grep -nE "^def FONKSIYON" app.py` ile doğrula. Bölüm Haritası daha güvenilir (köşe taşı yorumları).

---

## Genel Mimari
- **Dil/Framework:** Python + Streamlit
- **Ana dosya:** `app.py` (~25,040 satır, 37+ bölüm)
- **Yardımcı modüller:**
  - `bist_calendar.py` — BIST işlem takvimi (tatil/arefe/RVOL normalizer)
  - `backtest_runner.py` — Forward returns + XU100 alpha (standalone, Task Scheduler 19:30)
  - `backup_patron_db.ps1` — Haftalık DB yedeği (Task Scheduler Pazar 21:00)
- **Veri:** Yahoo Finance (`yfinance`) + parquet cache (`get_batch_data_cached`)
- **DB:** patron.db (Master Scan + scan_signals + signal_returns + signal_results), signals.db (bot)

---

## Bölüm Haritası — B1-B37 (köşe taşı yorumları, grep ile doğrulandı)

| # | Satır | Başlık |
|---|---|---|
| B1 | 6 | Bağımlılıklar + import |
| B2 | 50 | Tarama cache sistemi |
| B3 | 631 | Veritabanı (SQLite) |
| B4 | 856 | Varlık listeleri + kategoriler |
| B5 | 1039 | Session state + callback |
| B6 | 1085 | Veri çekme + önbellekleme |
| B7 | 2128 | Teknik analiz fonksiyonları |
| B8 | 2281 | Formasyon tarama (chart patterns) |
| B9 | 3258 | STP sinyal taraması |
| B10 | 3303 | Gizli Birikim |
| B11 | 3471 | Radar 1 + Radar 2 |
| B12 | 3838 | Hacim analiz modülleri |
| B13 | 4097 | Kırılım taramaları |
| B14 | 4324 | Temel skor + Master skor |
| B15 | 4395 | Güçlü Dönüş |
| B16 | 4620 | Pre-Launch BOS |
| B17 | 5038 | Arz/Talep bölgeleri |
| B18 | 5142 | ICT Setup |
| B19 | 5317 | Royal Flush Nadir Fırsat |
| B20 | 5451 | Minervini SEPA + RS Momentum |
| B21 | 6051 | Sentiment skor |
| B22 | 6284 | ICT Derin Analiz + PA-DNA |
| B23 | 7865 | Banner / rozet render |
| B24 | 8119 | Harmonik (XABCD) |
| B25 | 8491 | Harmonik Confluence |
| B26 | 8722 | SuperTrend + Fibonacci + Z-Score |
| B27 | 8981 | Piyasa rejimi + Konviksiyon |
| B28 | 9342 | Grafik + görselleştirme |
| B29 | 10379 | Detay kartı + panel render |
| **B37** | **12360** | **Erken Radar Senaryo Motoru** (sıra dışı — sonradan eklendi) |
| B30 | 13942 | 8 maddelik hibrit yol haritası |
| B31 | 15074 | Roadmap + birleşik sinyal paneli |
| B32 | 16189 | Genel Özet + sağlık sinyalleri |
| B33 | 17390 | Elit Tarama (Altın/Platin) |
| B34 | 17738 | **Ana sayfa panel UI + Master Scan butonu** |
| B35 | ~21280 | **AI Prompt sistemi** |
| B36 | ~22420 | Streamlit giriş noktası |

---

## En sık ihtiyaç duyulan fonksiyonlar

| Fonksiyon | Yaklaşık satır | Ne yapar |
|---|---|---|
| `init_db` | 696 | patron.db şema (scan_signals + signal_returns) — **B4-2 ile 17 kolon** |
| `log_scan_signal` | 736 | scan_signals'a yazar; obv_status + 7 feature snapshot (alias-tolerant) |
| `backfill_signal_returns` | ~930 | Master Scan step 0 — granüler 1-20g getiri |
| `get_batch_data_cached` | 501 | **Ana veri çekme** — parquet cache + yfinance |
| `calculate_ict_deep_analysis` | ~6810 | Ana ICT analiz (OB/FVG/bias/zone/model_score) |
| `calculate_master_score` | ~3162 | Master skor (return_breakdown=True opt) |
| `calculate_smart_money_score` | ~11333 | `@st.cache_data(ttl=600)`, _er_data içerir |
| `calculate_8_point_roadmap` | ~14048 | `@st.cache_data(ttl=600)`, cat param |
| `evaluate_erken_radar` | ~13127 | Tek hisse Erken Radar; matched_count + primary + confirms |
| `get_scenario_ages_batch` | 13114 | Senaryo yaşları (Erken Radar için) |
| `scan_erken_radar_batch` | 12157 | Master Scan step 13 |
| `_main_price_chart_plotly` | 9798 | Ana fiyat grafiği (Plotly/SMC) — tek sürüm |
| `render_smart_volume_panel` | 10635 | Smart Money Tile grid (6 tile + 3 büyük kart) |
| `_render_genel_ozet_panel` | 16193 | **GENEL ÖZET sol panel** (shape-driven) |
| `render_ict_deep_panel` | 13219 | Ana ICT deep panel |
| `_render_left_col` | 20442 | Sol sütun (hisse detay panelleri) |
| `_render_right_col` | 21909 | Sağ sütun (tarama sonuçları) |

---

## Master Scan akışı (B34, satır ~17789-17904)

```
0.  backfill_signal_returns                  (%5)   [step 0, 28 May 2026]
1.  get_batch_data_cached.clear() + reload
2.  scan_ict_batch
3.  scan_nadir_firsat_batch
4.  get_golden_trio_batch_scan               → golden + platin + tekli
5.  scan_hidden_accumulation
6.  analyze_market_intelligence + radar2_scan
7.  scan_harmonic_confluence_batch
8.  scan_minervini_batch
9.  scan_rs_momentum_leaders
10. scan_guclu_donus_batch
11. scan_prelaunch_bos
12. scan_golden_pattern_agent
13. scan_erken_radar_batch + log
14. Site frontend JSON export (BIST only)
```

---

## UI ana taşıyıcılar

- **Sol sütun (col_left):** satır ~22780 → `st.container(height=2500, border=False)`
- **Sağ sütun (col_right):** satır ~23412 → `st.container(height=1800, border=False)`
- `st.columns([75, 25])` — sol %75, sağ %25
- Master Scan butonu (B34): satır ~17764 `💎 TÜM PİYASAYI TARA`

---

## ❌ KALICI YASAKLAR (memory'de detay)

- **Sektör endeksi + piyasa rejimi → AI prompt'a EKLENMEZ** (`memory/feedback_sektor_rejim_yasak.md`)
- **Weinstein Stage Analysis → YAPILMAYACAK** (analysis paralysis — 30 May 2026 reddedildi)
- `detect_market_regime` UI'da var (sağ kart Makro) — ama scanner filtresi VEYA AI context'i olarak kullanılmaz
- **CLAUDE.md uzatma yasağı:** Bu dosya kısa kalır. Detay → `memory/SMR_SISTEM_OZETI.md`

---

## Son Oturum Notu — 3 Haz 2026 Oturum 14

**AI Prompt v2 refactor (9 madde):** Hook "ama/ancak" zorunluluğu kaldırıldı, endeks YAML smart_money/obv_cmf koşullu skip, G5 silindi (sıra 4→2→3→1), sentiment_karne YAML kaldırıldı (panel summary korundu), Smart Money + OBV/CMF yorum kuralları konsolide (3→1 + 3→1), G3 jargon filtresi "ilk geçiş G3'te" netleşti (28 zorunlu → 11+15 opsiyonel), anti-kalıp mekanik kural (paragraf-iskeleti + görev-içi tekrarsızlık + açılış öz-denetimi), `_sms_str`'e senaryo yaşı enjekte (0-2g taze / 3-7g orta / 8g+ eski), persona compose edilebilir (`_active_scenarios` listesi 9 senaryo flag'i + `_compose_note` → Hidden Acc + Pre-Launch + 5★ Erken Radar artık kaybolmuyor). ANLATIM KURALI'na 12 insani benzetme geri eklendi (cümle + parantezde kısaltma formatı). Backup: `app_backup_pre_prompt_v2.py`. Net: 25,030 → 25,040 satır (+10).

**Backtest UI:** "Sinyal Sayısı" sütunu kaldırıldı. Her hit% hücresinin altına `Gerçek sinyal: X` italic gri satır (5g/10g/20g ayrı N gösterir — artık VIP Formasyon "0/210" yanılsaması yok).

**B4-2 Feature Snapshot:** `scan_signals`'a 7 yeni kolon (idempotent ALTER): `f_52h_pos`, `f_rsi`, `f_cmf_dual`, `f_omi_sigma`, `f_squeeze_days`, `f_vp_shape`, `f_master_score`. `log_scan_signal` çoklu alias ile okur (geriye uyumlu). **AMA scanner df'leri henüz feature üretmiyor → NULL.** İkinci adım: her scanner'a feature enjeksiyon (hatırlatma MEMORY.md'de).

**Backup otomasyonu:** `backup_patron_db.ps1` + Windows Task Scheduler `SMR_DB_Weekly_Backup` (Pazar 21:00, 84 gün retention). patron.db git'te değil → tek koruma katmanı.

**CLAUDE.md sıkıştırıldı:** 53K → ~7K karakter. Eski içerik `CLAUDE_backup_before_compress.md`'de.

---

## Önceki Oturumlar (kısa kronoloji — detay: SMR_SISTEM_OZETI.md)

- **Oturum 13 (3 Haz):** AI Prompt B35 anti-kalıp refactor (Golden Trio kalıp tekrarı), KARA LİSTE + ANTİ-KOPYA havuzu (12 anchor)
- **Oturum 12 (1-2 Haz):** GENEL ÖZET kurumsal görsel overhaul (5-mum şerit, arrow stack, traffic lights, Force Compass) + CMF Dual-Window Phase 1+2
- **Oturum 11 (1 Haz):** B5 HVN/LVN expansion + VP Şekil + CP Yanıltıcı + sağ üst kart 3-katmanlı YAPI + dinamik MA tablosu
- **Oturum 10 (31 May):** Royal Flush RS proxy fix + 52H konum filtresi + Erken Radar A2/A8 dedup + backfill_signal_returns 120sn→3sn
- **Oturum 9 (31 May):** AI Prompt YAML restructure (24.855→22.110 token), 14 ana dal, persona is_golden dalı, few-shot örnek
- **Oturum 8 (30 May):** Formasyon şekil doğrulama + yeni formasyonlar (Çift Dip W, Wedge, M Top) + tatil hayalet-bar fix
- **Oturum 7 (29-30 May):** SWOT W3/W5/W9 + ölü kod (-830 satır) + compute_cmf/log_error helper'ları + sessiz bug avı

---

## Açık konular (devam eden)

- ✅ **Feature snapshot scanner-side yazım** — TAMAMLANDI (3 Haz Oturum 14): `_compute_signal_features` helper + `log_scan_signal` fallback. Sonraki Master Scan'den kolonlar dolacak.
- ✅ **Bot tarafına AI Prompt v2 senkron** — TAMAMLANDI (3 Haz Oturum 14): smr_core.py PRO (build_ai_prompt) + ELITE (build_ai_prompt_gorev1) → Z-Score + POC/VWAP + KESİN YASAK 3 blok konsolide (1 birleşik Rehber), anti-kalıp mekanik kural (PRO+ELITE), ELITE ANLATIM KURALI Oturum 14 formatına geçti (insani cümle + parantezde kısaltma + İLHAM + anti-kopya). Net: 3282→3115 satır (-167). Backup: `smr_core_backup_pre_prompt_v2.py`. ⚠️ VPS deploy gerek: `git push` + `ssh wm11tr@34.153.19.220 "cd ~/smr && git pull && sudo systemctl restart smr-bot"`.
- ⏳ **base_powers reranking** — `eval_20g ≥ 30` eşiği bekleniyor. Şu an Royal Flush 11/71. Eylül 2026 itibariyle olgunlaşır.
- ⏳ **Hayalet Bar Plan B** — `_strip_holiday_bars` ortadaki V=0 barları da silsin (2-3 hafta backtest sonrası karar)
- ⏳ **CMF Dual-Window Phase 3** — `get_obv_divergence_status` + `calculate_price_action_dna` + `process_single_accumulation` + Tile 6'ya yay
- ⏳ **GitHub PAT yenileme** — 31 Ağustos 2026 (Ağustos başında hatırlat)
