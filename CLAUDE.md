# Patron Terminal — CLAUDE.md
# Hızlı navigasyon. Sistemin TAMAMI: `memory/SMR_SISTEM_OZETI.md` (tek kaynak).
# Son güncelleme: 12 Haz 2026 Oturum 21 (flag bug avı: TEFAS çelişki + CMF ölü + rel_obv şişik düzeltildi · 19:00 bülten faz_X fix · /bulten admin komutu)

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
- **Veri:** **Yahoo (OHLC) + İsyatirim (Volume override)** hibrit + parquet cache. Detay: `memory/SMR_SISTEM_OZETI.md` → "VERİ KATMANI" bölümü
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

## Son Oturum Notu — 12 Haz 2026 Oturum 21 (flag bug avı + bülten kurtarma)

**Stratejik soru ("app.py'yi nasıl ileri taşırız, analysis-paralysis olmadan") → eyleme döndü.** Teşhis: sorun feature eksikliği değil, **validasyon borcu** — son 2 oturumda 29 flag eklendi, çoğu "Eylül backtest beklemede". Doktrin: (1) tek kâhin = `signal_returns × scan_signals` JOIN, (2) ~3 haftada bir budama (ret≤0 / hit<%50 → AI'dan çık), (3) ölçülemeyen flag AI'a kalıcı girmez. Kullanıcı "envanter çıkar" (A) dedi → bugünkü Master Scan flag-doluluk taraması **3 bozuk + 1 ölü sistem** ortaya döktü:

- **TEFAS konsensus çelişkisi (CANLI ZARAR):** `f_tefas_konsensus_alim` VE `_satim` her hisseye 1 basıyordu (843/843). Kök: `_BIST_TOTAL_` makro sorgu + 3 günlük veri + `fund_aum_mn` tümü None → 5g penceresi sahte, AUM materiality ölü. AI'a "her hisse hem alım hem satım" gidiyordu. **Fix:** `_compute_tefas_signals`'a veri-güvenilirlik kapısı (<5 gün veya AUM yoksa → **NULL**). `kap_events`=0 satır → buyback/threshold/insider zaten ölü (veri yok). `mkk_yabanci`=132 satır → çalışıyor.
- **`f_cmf_dual` %0 boş + spike bit-2 ölü:** `compute_cmf` **float** döner ama çağrı `.iloc[-1]` yapıyordu (satır ~1696) → sessiz AttributeError. `.iloc` kaldırıldı. Diğer 9 çağrı zaten float kullanıyor (izole).
- **`f_rel_obv` %57 "outperform_strong":** slope `abs(OBV[-1])` keyfi paydaya bölünüyordu → sıfıra yakın payda yüzdeyi patlatıyor, benchmark ortak olduğu için herkese yayılıyor. **Fix:** payda → ortalama günlük hacim (kararlı ölçek). Sadece GENEL ÖZET'te (AI prompt'ta değil).
- **near_ifvg %63 / breaker %53 = gürültü:** ±%2 + tüm tarihsel zone. Eşik sezgiyle ayarlamak [[feedback-extrapolation-yasak]] çiğner → `SMC_IFVG_BB_AI_ENABLED=False` toggle ile AI prompt + kompozit skordan çekildi, **scan_signals yazımı korundu** (Eylül backtest ölçsün; hit≥%55+ret≥%3 verirse geri aç).
- **Bugünün 1317 stale satırı temizlendi** (843 çelişkili + 483 şişik kolon NULL). AI canlı recompute ettiği için (`_compute_signal_features`, ~25664) fix'ler anında geçerli, yeniden tarama gerekmedi.

**19:00 bülten hatası — PRO+ELITE fallback aldı.** Gerçek sebep "yoğunluk" DEĞİL → **`faz_X` NameError** (smr_core). 12 Haz'da eklenen "REJİM DEĞİŞİMİ rozeti okuma" bloğundaki `{faz_X}`/`{faz_Y}` literal placeholder'ları f-string içinde değişken sanılıp PRO+ELITE prompt'larını patlattı. **Fix:** çift-parantez escape (`{{faz_X}}`). Canlı render + re-send doğrulandı (PRO 4441 kr, ELITE 3703 kr, Gemini 200). smr_core'da CMF/rel_obv/tefas YOK (app.py-only) → paralel bug yok.

**Yeni: `/bulten` admin komutu (smr_bot).** 19:00 otomatik gönderim patlarsa admin elle tetikler → `send_daily_bulletin(context)`. ⚠️ **Olay:** `git add smr_bot.py` sırasında kullanıcının commit'lenmemiş "SIZINTI KORUMASI v2" WIP'i (Gemini çıktısından prompt-leak strip, `call_gemini_gorev3` içinde) bu commit'e karıştı ve canlıya gitti — kullanıcı "canlı kalsın" dedi (iyi defansif iş, over-strip görülmedi). Ders → [[feedback-validate-before-ship]].

Commits: `cecf58a` (faz_X) · `8926ee7` (3 flag fix) · `2a3c432` (/bulten + leak WIP). VPS deploy: `reset --hard origin/main` (stale scp mod'ları stash'te) + smr-bot/patron-radar restart. Backup'lar: `app_backup_pre_flag_fixes_12haz.py` · `smr_core_backup_pre_fazX_fix.py`.

---

## Önceki Oturum Notu — 10 Haz 2026 Oturum 20 (SMC kurumsal 4 ek + KURUMSAL TAKİP 8 STRONG flag + FİYAT kartı redesign)

**Büyük bir oturum — 3 ana iş.**

**1) FİYAT kartı (sağ üst) baştan tasarlandı.** Eski "FİYAT solda + YAPI 3-katmanlı sağda" düzeni kaldırıldı (kullanıcı: "yapı zaten başka yerlerde var"). Yeni: dikey stack — üstte kompakt header (FİYAT label + XBANK ticker büyük | fiyat + %değişim sağda), ortada **SİNYAL ÖZETİ matrisi** (5 farklı lens: 🎯 Genel Sağlık · 🧭 Pozisyon Eğilimi · 🗺 Yol Haritası · 🌟 Erken Radar · 🏛 Smart Money). Her satır icon + tam label + horizontal bar (severity rengiyle dinamik) + skor overlay (beyaz + text-shadow her bg'de okunur). ICT satırı **bias-aware** — bearish 5/5 ↓ artık kırmızı gösteriliyor (öncesinde yeşildi, ASTOR örneği bug ortaya çıkardı). Teknik Görünüm gauge başlığı **"GENEL SAĞLIK (Teknik Skor)"** olarak yeniden adlandırıldı (kullanıcı: "Master Skor yerine teknik veri lensi diyelim"). Alt RSI/MOMENTUM şeridi de yarıya küçültüldü. CANLI SİNYALLER chip'i artık `Teknik 36 · NEGATİF` formatında (kaynak netleşti). [[ASTOR ICT bias bug fix dahil]]

**2) SMC grafiği kurumsal 4 ek (BIST backtest beklemede).** `_main_price_chart_plotly` genişledi: (a) **VWAP σ-bands** (±1σ/±2σ Citadel/Renaissance mean-reversion bölgesi, soft mor fill), (b) **Q/Y Opening** (Y-Open slate dashdot + Q-Open amber dashdot — JPM/GS kalibrasyon ref), (c) **FVG Mitigation State** (önceden mitigated FVG'leri SİLİYORDUK — AI yanlış destek diyordu, BUG. Şimdi state'li: 'fresh' (dolu kutu) / 'tested' (ince) / 'inverted' (zıt renk + 'iFVG↓' etiket — rolü ters dönmüş, eski destek artık direnç), (d) **Order Block tipleme** ('fresh' / 'mitigation' / 'breaker' BB — failed OB rolü tersi, 'BB↓'/'BB↑' etiket). 4 yeni flag scan_signals'a eklendi (`f_at_vwap_minus_2sigma`, `f_at_y_open`, `f_near_ifvg`, `f_breaker_block_active`). AI prompt institutional_ref'te koşullu emit, hepsi DESTEKLEYİCİ seviyede ("BIST backtest beklemede" ibaresi). Sağ kenar etiketleri için **çakışma önleme** (POC + nPOC + VWAP + aVWAP↑/↓ + Y/Q open + σ-bands → 12+ etiket, y'ye göre stagger + arrow ile orijinal seviye gösterimi).

**3) KURUMSAL TAKİP (TEFAS + KAP) — 8 STRONG flag + Anchor.** Yeni boyut. Kullanıcı sorusu: "yabancıdan başka, yerli fonlar XBANK biriktiriyorsa nasıl bilinecek?" Çerçeve: 3 bağımsız kanaldan **kayda-değer-filtreli** sinyal. Kullanıcı sertçe doğruladı: "STRONG olarak işaretlediğin hepsi iyi, hadi yap." **TEFAS Fund Flow** (3 STRONG): konsensus_alim (≥3 fon 5g ≥%10 pozitif, AUM ≥100mn, ≥%1.5 portföy), konsensus_satim (≥%20 toplam çıkış), yeni_giris (≥2 büyük fon AUM ≥500mn ilk pozisyon). **KAP Hisse Geri Alım** (2 STRONG): buyback_aktif (5g icra), buyback_dip_aliyor (+52H dibine ≤%10). **KAP Pay Sahipliği** (2 STRONG): threshold_asildi (%5/%10/%25/%33/%50 statütör), insider_first_buy (yönetici 6 ay sessiz + ≥5mn TL). **Convergence ELIT** (1): `f_kurumsal_anchor` = 3+ STRONG aynı yönde → AI G1'de MUTLAKA merkez ("🏛 KURUMSAL ANCHOR"). 2 cache tablosu: `tefas_holdings` (günlük portföy snap) + `kap_events`. Master Scan adım 0.5: BIST kategorisinde TEFAS fetch, her durumda KAP fetch (`https://www.tefas.gov.tr/api/DB/BindHistoryAllocation` + `https://www.kap.org.tr/tr/api/disclosures`). Endpoint kararlılığı için defansif kod (başarısızsa flag NULL kalır). Detay: [[project-kurumsal-takip]]. Commit `10cad67`. Backup: `app_backup_oturum20_kurumsal_20260610_0938.py`.

**Eylül 2026 backtest planı:** 8 SMC kurumsal flag + 8 KURUMSAL TAKİP flag → toplam **16 yeni feature** scan_signals'a yazılacak. 3 ay sonra `signal_returns` JOIN ile gerçek BIST hit/ret katkısı ölçülecek; hit ≥%65 + ret ≥%5 = TIER_1_ELIT, hit <%50 = AI'dan kaldır.

**4) Akıllı Para 5'lik genişleme + Hacim Momentumu sadeleştirme + ICT Sniper rozeti.** Oturum 20 sonu (10 Haz akşam). Beşli plan: **(#1)** MKK Yabancı Net Alış — İş Yatırım RSS feed (`arastirma.isyatirim.com.tr/.../gunluk-yabanci-oranlari/feed/`) günlük top-3 giriş/çıkış + streak. 4 flag + `mkk_yabanci` tablo. **(#2)** Relative OBV (hisse vs endeks) — Mansfield RS'in hacim katmanı, 5 state. **(#3)** YAPISAL vs TACTICAL ayrımı — tüm akıllı para flag'leri 2 kovaya, ayrı 0-100 skor, 4 makro senaryo verdict. **(#4)** UDVR (Up/Down Volume Ratio) — klasik Wyckoff Effort-vs-Result, 5 state + climax detection ELIT. **(#6)** Force Index Dual + Divergence — Elder Triple Screen FI(2)+FI(13), 7 state + bullish/bearish divergence (TUPRS gerçek testte yakaladı). GENEL ÖZET'e 4 yeni satır eklendi. MFI 9 senaryo sade Türkçeye çevrildi (etiket+1 cümle, semboller temiz). Klasik Taramalar tablo altına koşullu ICT Sniper rejim rozeti ('XU100 SMA50 altı + 5g negatif + <100 sinyal' tetik). Toplam 13 yeni feature flag scan_signals'a, Eylül 2026 ortası `signal_returns` JOIN ile gerçek BIST katkısı ölçülecek. Commits: `687dd6d`+`3b93016`+`b839d11`+`744c9c9`+`3533854`+`ee141b1`+`10deb01`.

---

## Önceki Oturum Notu — 6 Haz 2026 Oturum 18 (POC/VWAP kanıt-tabanlı yeniden yapı)

**3 + 3 katmanlı POC iyileştirmesi.** Helpers: `calculate_multi_tf_pocs` (20g/60g/250g + confluence), `calculate_anchored_vwap(df, anchor_idx)` (52H zirve+dip); mevcut `detect_naked_poc` aktive edildi. Ana grafiğe (`_main_price_chart_plotly`): 3 POC çizgisi farklı stil + confluence zarfı, 2 aVWAP eğrisi + anchor üçgenleri, en yakın 3 naked POC dashed cyan. AI prompt YAML `institutional_ref` blok 5 yeni KOŞULLU alan — hiçbiri varsayılan değil: `poc_mtf_confluence` (spread<%2), `avwap_52h_*` (|dist|<%3), `naked_poc_yakin` (|dist|<%2), `poc_magnet_active`. Görmüyorsa AI yoksayar, "veri yok" demez. 5 İLKE kural 4 EK NOT'a backtest kalibrasyon matrisi gömüldü (kanıt tabanı).

**Backtest (`backtest_poc_retest.py`):** Standalone — 593 BIST hisse, 84.832 event, %43.4 baseline retest. **Segmente analiz** kritikti: ilk turda `Denge+below = %70+` extrapole etmiştim, segmente bakınca **%50.9** çıktı (yanlış). Gerçek lider hücreler: `Akümülasyon|Up|below %67.6`, `Denge|Up|below %62.0`. **Pattern: trend yönü vp_sekil'den daha belirleyici.** `poc_magnet` kuralı yeniden yazıldı: `trend_up (fiyat>SMA50 + 10g eğim>%1) + 20g POC altı + |dist|≥%3 + (vp=akümülasyon/denge)` → GÜÇLÜ/ORTA tier ayrımıyla emit. Yeni feedback memory: [[feedback-extrapolation-yasak]].

**scan_signals: 7 → 10 feature.** Eklenen 3 INTEGER kolon: `f_poc_magnet`, `f_poc_confluence`, `f_avwap_test_zone`. `_compute_signal_features` artık 10 feature hesaplıyor. `log_scan_signal` alias-tolerant + fallback hazır. Ekim 2026 ortası live backtest hatırlatması MEMORY.md'de.

**Görsel polish (Oturum başında):** GENEL ÖZET başlık rozeti (ticker code + cyan-mor gradient) + FİYAT hücresi vurgulandı (flex 1.45×, cyan→mor gradient ring, glow, beyaz değer). Detay: [[project-poc-features]].

---

## Önceki Oturum Notu — 5 Haz 2026 Oturum 17 (Streamlit Cloud → VPS self-host)

**Streamlit Cloud bırakıldı (private repo desteği yok — OAuth App'i sadece public scope istiyor).** App artık VPS'te self-hosted: **http://34.153.19.220/patron/** — systemd `patron-radar.service` + nginx reverse-proxy `/patron/` (websocket pass-through, `^~` prefix öncelik — yoksa regex `\.(js|css)$` location'ı static asset'leri yakalayıp 404 veriyor). 2GB swap eklendi (RAM 958Mi yetmezdi). `~/smr/venv` → streamlit 1.58 + tüm deps. Yönetim: `sudo systemctl {status,restart} patron-radar` + `tail -f ~/smr/logs/patron-radar.log`. Nginx config: `/etc/nginx/sites-enabled/smr` direkt dosya (symlink değil) — sites-available'a yazınca enabled'a manuel kopyalanmalı. **Kod update workflow:** `scp app.py wm11tr@34.153.19.220:~/smr/ && ssh ... "sudo systemctl restart patron-radar"`.

**Py3.10 f-string fix (app.py:22933):** `\"` escape vardı (Py3.12+ OK, VPS 3.10 SyntaxError). `_skor_html` değişkenine çekildi. Lokalde de geriye uyumlu. Commit `ca29137`. **VPS deploy öncesi `python -c "import ast; ast.parse(open('app.py').read())"` ile syntax check alışkanlık edinilmeli** — lokal Python sürümün VPS'ten yeni, sessizce takılır.

**GitHub:** `patron-radar` (private) artık yedek mirror — sadece `app.py` + `requirements.txt`. Asıl repo `smr-bot`. Commit `1b91a00`.

## Önceki Oturum Notu — 5 Haz 2026 Oturum 16

**Auto-refresh (app.py:9 + ~23215):** `streamlit-autorefresh 1.0.1` venv'e kuruldu. `_render_left_col` başına 10 dk gate eklendi: `is_trading_day` + `get_session_hours` ile sadece BIST seans saatleri içinde tetiklenir. Refresh anında `get_batch_data_cached.clear()` → açık hisse %100 taze. Hisse değiştiğinde key resetlenir. TTL 900 korundu.

**52H bar → MA tablosu içine gömüldü (~23445 + 23694):** Standalone 52H kart silindi, HTML snippet `session_state['_52h_strip_html']`'ye yazıldı. MA kartı `display:flex column` ile 2 katman: üstte 52H range bar (6px kompakt), altta MA cells. Tek border.

**SMC expander üst boşluğu kapatıldı (~23480):** Sebep — `st_autorefresh` görünmez iframe boş `element-container` bırakıyordu. 3 katmanlı CSS fix: iframe display:none + `:has()` element-container squash + boş markdownContainer sıfırla + expander margin-top:0 + öncesine -1.4rem pull-up.

**İsyatirim endeks hacim → vazgeçildi:** Test → `fetch_index_data` sadece `INDEX, DATE, VALUE` döner (hacim YOK). XU100 hesaplama endeksi. Diğer "hacim sorunlu" gruplar (kripto/emtia/FX) ya doğru ya tanım gereği yok.

**🚨 KRİTİK: İsyatirim Open=Close doji bug fix (app.py:2142 + 2192):** Commit 591ad72'de (dün) `_fetch_bist_ohlcv_isyatirim` API'den HGDG_ACILIS kaldırıldığında Open'ı Close ile dolduruyordu. Caller'lar TÜM OHLCV override yapıyordu → Yahoo'nun gerçek Open'ı doji ile eziliyordu → "5 mumdan 4'ü doji". Test: HGDG_MIN/MAX hâlâ var, sadece Open kayıp. Fix: override listesinden `Open` çıkarıldı, Yahoo Open korundu. 627 parquet cache silindi. ICT/candlestick/body analizleri tekrar doğru.

**G3 Anti-Kalıp v3 (app.py B35 ~22641 + 22685):** Twitter geri bildirimi "bişey anladıysam arap". v2 6.5/10. v3 → 6 yeni kural: K1 "YOK bulgular yasak" (Bollinger sıkışma yok / klasik uyumsuzluk yok cümleleri silinir), K2 "nötr/sıfır metrik yasak" (%0 / 1.0x atılır), K3 "hesaplama yapısı anlatma, sonuç anlat" (100/100, 0/100, %58 component breakdown yasak), K4 "mikro intraday detay yasak" (CP %30 alt dilim), K5 "açılım MAX 3 kelime" ("para akışı (CMF)" ✓, "Sermaye giriş çıkış dengesini ölçen para akışı endeksi (CMF)" ✗), K6 "tek yön mutabakatı" (5 madde tek yön, çelişiyorsa açık geçiş). Net: ~+50 satır prompt. Backup: `app_backup_pre_g3_v3.py`. smr_core senkron ertelendi.

---

## Önceki Oturum Notu — 4 Haz 2026 Oturum 15

**G3 Anti-Kalıp v2 (app.py + smr_core.py):** "AI yorumları robotik, hep aynı şey" geri bildirimi → Görev 3 prompt'unu A+B karma ile yeniden yazdım. (A) Anchor-ilk kuralı, (B) sıkıştırma. Eklenen 5 mekanizma: yasaklı açılış kalıpları (8 spesifik), fiil zinciri yasağı (10 fiil, madde içi max 1), kelime salatası yasağı (4 kalıp), jargon tekrar yasağı (2. geçişten itibaren açılım yok), 4 alternatif "📌 İzlenecek" formülü (klasik kalıp yasak). app.py G3 5-madde + akıcı paragraf yapısı korundu, sadece cümle tavanı (M1-M4=3, M5=4) + dil kuralı. smr_core.py PRO 7-madde + alt-başlık yapısı **aynen** korundu, sadece anti-kalıp bloğu eklendi (PRO+ELITE ikisine de). VPS deploy + restart başarılı. Backup: `smr_core_backup_pre_g3_antikalip.py`. Net: app.py +56 sat, smr_core +68 sat. Test çıktısı 6.5/10 (4 sızıntı: "fısıldıyor" KATMAN 1 ihmali, M1 yarı şablon, M5 kelime salatası, fiil zinciri — kullanıcı kabul etti, ikinci iter ertelendi).

**app.py stale data uyarısı false-positive fix:** Sat 2163 — `is_yahoo_update_needed` veya `_volume_is_stale` "tazele" diyor → Yahoo retry boş dönüyor → kod `days=0` ile stale flag set ediyor → UI "0 gün eski (son güncelleme: bugün)" diyor. Mantıksız. Fix: `if _stale_days.days >= 1` koşulu eklendi. Bugünkü veri var, intraday tazeleme başarısız → sessiz; gerçek 1+ gün eskime → eskisi gibi uyarı.

**🆕 Insider Tracker (yeni proje, VPS'te):** US House PTR (Periodic Transaction Report) → Gemini → Telegram (SMR Pro+Elite). `~/insider/` dizini, systemd `insider-bot.service`, saatte 1 tarama. Veri kaynağı: `https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip` (eski `ftp/xml/` 404). Filtre: 9 isim whitelist (Pelosi, Gottheimer, Crenshaw, Khanna, McCaul, Hern, Greene, Carter, Higgins), min $100k, max 30g delay, sadece Purchase, Self+Joint owner. Gemini-flash-latest ile PDF→structured JSON. OCR YOK. Maliyet: $0. Detay: `memory/project_insider_tracker.md` (varsa).

---

## Önceki Oturum Notu — 3 Haz 2026 Oturum 14

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

- ⚠️ **DÜZELTİLDİ (12 Haz Oturum 21) — Eylül backtest feature'larının çoğu BOZUK/ÖLÜ çıktı:** Bugünkü flag-doluluk taraması: **TEFAS 8 flag** ya çelişkili (alim+satim ikisi de 1) ya ölü (`kap_events`=0 satır → buyback/threshold/insider hep 0; `f_kurumsal_anchor` hiç ateşlemedi). `_compute_tefas_signals` artık veri güvenilmezse NULL döner → **TEFAS flag'leri Eylül backtest'e kadar büyük olasılıkla NULL kalacak** (endpoint/parsing düzeltilene dek). `f_near_ifvg`/`f_breaker_block_active` = gürültü (>%50) → AI prompt + kompozit skordan çekildi (`SMC_IFVG_BB_AI_ENABLED=False`) ama scan_signals'a yazılmaya devam → Eylül'de yine ölçülebilir. **Sağlam çıkan SMC kurumsal:** `f_at_vwap_minus_2sigma` (%1), `f_at_y_open` (selektif) — bunlar Eylül backtest'e değer. `mkk_yabanci` (132 satır) + `f_rel_obv` (normalize fix sonrası) çalışıyor.
- ✅ **TEFAS + KAP KALDIRILDI (12 Haz Oturum 21, commit `11ad6a3`):** Canlı endpoint testi sonrası tamamen söküldü (~469 satır). KAP `/api/disclosures` → **status 666** (bot-block, HTML); TEFAS pytefas çalışıyor ama yeni API **hisse-bazlı veri vermiyor** (sadece makro fon allocation = kalıcı rejim yasağı) + AUM kolonu yanlış (`market_cap` yerine `portfolio_size`). 6 fonksiyon + `_KURUMSAL_THRESHOLDS` + Master Scan fetch + skor + AI emit + prompt metni silindi. **Korundu:** MKK yabancı (hisse-bazlı, çalışıyor), rel_obv, UDVR, Force Index. scan_signals `f_tefas_*`/`f_buyback_*`/`f_kurumsal_anchor` kolonları NULL kalır (schema bozulmadı). Backup: `app_backup_pre_tefas_kap_removal.py`. İleride güvenilir KAP kaynağı bulunursa buyback/insider yeniden değerlendirilebilir.
- ✅ **Feature snapshot scanner-side yazım** — TAMAMLANDI (3 Haz Oturum 14): `_compute_signal_features` helper + `log_scan_signal` fallback. Sonraki Master Scan'den kolonlar dolacak.
- ✅ **Bot tarafına AI Prompt v2 senkron** — TAMAMLANDI (3 Haz Oturum 14): smr_core.py PRO (build_ai_prompt) + ELITE (build_ai_prompt_gorev1) → Z-Score + POC/VWAP + KESİN YASAK 3 blok konsolide (1 birleşik Rehber), anti-kalıp mekanik kural (PRO+ELITE), ELITE ANLATIM KURALI Oturum 14 formatına geçti (insani cümle + parantezde kısaltma + İLHAM + anti-kopya). Net: 3282→3115 satır (-167). Backup: `smr_core_backup_pre_prompt_v2.py`. ⚠️ VPS deploy gerek: `git push` + `ssh wm11tr@34.153.19.220 "cd ~/smr && git pull && sudo systemctl restart smr-bot"`.
- ⏳ **base_powers reranking** — `eval_20g ≥ 30` eşiği bekleniyor. Şu an Royal Flush 11/71. Eylül 2026 itibariyle olgunlaşır.
- ⏳ **Hayalet Bar Plan B** — `_strip_holiday_bars` ortadaki V=0 barları da silsin (2-3 hafta backtest sonrası karar)
- ⏳ **CMF Dual-Window Phase 3** — `get_obv_divergence_status` + `calculate_price_action_dna` + `process_single_accumulation` + Tile 6'ya yay
- ⏳ **GitHub PAT yenileme** — 31 Ağustos 2026 (Ağustos başında hatırlat)
