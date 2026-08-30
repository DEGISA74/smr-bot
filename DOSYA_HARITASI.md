# 🗺️ PATRON TERMINAL — DOSYA HARİTASI (İÇİNDEKİLER / YOL GÖSTERİCİ)

> **Ne için var:** "Şurayı değiştir" dendiğinde hangi dosyaya gidileceğini saniyede bulmak için.
> Her dosyanın NE İŞE YARADIĞI + NE ZAMAN DOKUNULACAĞI yazılı. Satır no'ları kayar; dosya
> KİMLİĞİ kalıcıdır. Son güncelleme: 30 Ağu 2026.
>
> **Hızlı refleks:** UI/ekran/panel görünümü → `app.py`. Hesap/formül → ilgili `*_core.py` modülü.
> Veri gelmiyor/yanlış → `data_layer.py` + `veri_bekcisi.py` + `fetcher.py`. Bot/Telegram → `smr_*`.
> Tavan → `tavan_*`. "Bu tarama işe yarıyor mu" → backtest/karne script'leri.

---

## 5 AĞUSTOS 2026 — BIST GÜNLÜK VERİ KASASI

> Bu bölüm, aşağıdaki eski parquet anlatımlarından daha günceldir. BIST günlükte
> sağlayıcı cevabı doğrudan `veriler/` dosyasına yazılmaz; ayrıntı için
> `VERI_CEKME_PROTOKOL.md` tek kaynaktır.

- `bist_data_store.py` — geçici aday, doğrulama, değişmez nesne, manifest, aktif sürüm, karantina ve geri dönüş.
- `bist_exchange.py` — lokal aday paketini VPS’te uygulama ve onaylı sürümü hash kontrollü lokale aktarma.
- `provider_traffic.py` — Yahoo/İş Yatırım/borsapy için ortak hız, dakika bütçesi, öncelik ve 403/429 sigortası.
- `isyatirim_gateway.py` — İş Yatırım’ın tek HTTP kapısı; gerçek zaman aşımı, son sağlam cevap ve ortak bekleme.
- `bist_data_status.py` / `bist_data_monitor.py` — sağlık raporu ve bağımsız izleme ekranı.
- `bist_data_store_selftest.py` — üretime dokunmayan sürüm/karantina/rollback/paket/senkron testi.
- `bist_bootstrap_audit.py` — mevcut kasayı ilk sürüm öncesi yazmadan kontrol eder.
- `settle_kapanis.py` — parquet yazmaz; lokal Yahoo kapanışını sürüme bağlı aday ZIP yapar.
- `run_settle.sh` — yalnız aday ZIP’i VPS kabul kapısına yollar; toplu parquet push kaldırıldı.
- `sync_from_vps.sh` — onaylı manifest + değişen nesneleri doğrular, aktif işareti en son değiştirir.
- `finalize_volume.py` — kapanış sonrası önce İş Yatırım, eksiklerde yalnız son işlem günü için anomali kontrollü borsapy yedeği; yeterli kapsama ve kalite olmadan marker/sürüm üretmez.
- `volume_source_audit.py` — salt-okur borsapy / İş Yatırım / parquet üçgen denetimi; üretim verisini değiştirmez.
- `fix_recent_close.py` — eski doğrudan yazıcı emekliye ayrıldı.

Okuyucular: `data_layer.py` (uygulama + Master Scan) ve `smr_core.py` (bot) BIST günlükte yalnız aktif sürümü okur; ağ, silme ve yazma yapmaz.

---

## A. ANA UYGULAMA + 12 BÖLME MODÜLÜ (Streamlit — canlı ürünün kalbi)

Mimari kural (4 Tem 2026): **app.py sadece UI + render + import eder; hesap kodu ayrı modüllerde.**
Import akışı döngüsüz: `app.py → analysis_core/charts → scan_pipeline → scoring_core → ict_core/scanners → pattern_core/indicators → data_layer/db_layer/evidence`.

### `app.py` (~20.700 satır) — ANA UI + AI PROMPT
Streamlit uygulamasının tamamı; kullanıcının gördüğü HER panel burada render edilir (33 render fonksiyonu). Hesaplar modüllerden import edilir, burada sadece görselleştirilir. Sol sütun (`_render_left_col`, ~%82) tek-hisse detay panellerini, sağ sütun (`_render_right_col`, ~%20) tarama sonuçlarını çizer. Kanıt Terazisi kartı (`_render_kanit_terazisi_card`), Smart Money Hacim paneli (`render_smart_volume_panel`), GENEL ÖZET (`_render_genel_ozet_panel`), Teknik Yol Haritası (`render_roadmap_8_panel`), SİNYAL ÖZETİ matrisi, ICT paneli (`render_ict_deep_panel`) hepsi buradadır. B35 bölümündeki ~4K satırlık AI Prompt if-bloğu (Gemini'ye giden metin) da app.py'de. Master Scan butonu (`💎 TÜM PİYASAYI TARA`) ve 14 adımlık orkestrasyon buradadır. ÜYE MODU bloğu (MEMBER_MODE/SHOWCASE_MODE flag'leri, quota/tier gizleme) admin-dışı web sürümü içindir. Endeks dil normalleştiricisi (`_endeks_dil` / `_index_has_tl_ciro`) XU100'de hisse panelini "piyasa geneli" diline çevirir. Bölüm haritası (B1-B37 köşe taşları) ve fonksiyon-satır eşlemesi için → `CLAUDE.md`. **Ne zaman dokun:** ekranda görünen HER şey (renk, metin, panel düzeni, sıralama, AI prompt metni). ⚠️ Yeni HESAP kodu buraya YAZILMAZ — ayrı modüle.

### `data_layer.py` (~2.200 satır) — VERİ KATMANI
Tüm veri çekmenin tek kapısı; `get_batch_data_cached` (toplu) ve `get_safe_historical_data` (tek-hisse) aileleri burada. Yahoo (OHLC) + İsyatirim (Volume override) hibrit + borsapy gap-fill + binance (kripto) + futures hacim düzeltmesi + split/tatil temizliği yapar. Parquet cache mantığı (`veriler/*.parquet`), `is_yahoo_update_needed` tazelik kararı, `apply_volume_projection` (gün-içi hacim projeksiyonu + endeks TL ciro override) burada. `INDEX_CIRO_TARGETS` + `compute_index_tl_ciro_series` + `load_index_components` = XU100 bileşen-toplamı TL ciro çekirdeği (18 Tem 2026). `fetch_stock_info` (saçma-değer sigortalı), endeks fetch, BIST evren listeleri (`raw_bist_stocks`, `priority_bist_indices`), `_normalize_bist_ticker`, `CACHE_DIR`/`_DEAD_SYMBOLS` sabitleri burada. Bilinçli Streamlit'e bağımlı (`@st.cache_data`) — kimliği bu. **Ne zaman dokun:** veri gelmiyor/yanlış/bayat, cache sorunu, yeni veri kaynağı, evren listesine hisse ekleme, endeks hacmi.

### `indicators.py` (~1.900 satır) — SAF GÖSTERGE HESAPLARI
25 saf teknik-analiz fonksiyonu; hiçbiri Streamlit/DB/ağ kullanmaz (girdi DataFrame, çıktı değer/dict). CMF/MFI/Force Index/UDVR/Relative-OBV (çift pencere), HARSI, LazyBear sıkışma, SuperTrend/Fibonacci/Z-Score aileleri burada. Hacim profili (POC/full/multi-TF/naked POC), aVWAP, Darvas kutusu, klasik mum kalıpları, S/R kümeleri, arz-talep bölgeleri, piyasa rejimi, 52H güç etiketi, spike dominance. `compute_flow_momentum` (para akış ivmesi barlarının TEK kaynağı, 6 tüketici dosya bunu çağırır). Hepsi golden_record fotoğraf kapsamında (değişiklik = sıfır fark kontrolü). **Ne zaman dokun:** bir göstergenin FORMÜLÜ değişecekse (CMF eşiği, POC hesabı, RSI penceresi vb.).

### `scoring_core.py` (~1.500 satır) — SAF SKORLAMA MOTORLARI
9 skor motoru: Smart Money skoru, Sentiment skoru, Master skor (`calculate_master_score`, return_breakdown=True ile alt-skorlar), SMC elements, yapısal/tactical split skorlar, risk profili, likidite-manipülasyon, breakout state, tech card. Girdi genelde ticker → veriyi data_layer'dan çeker. `SMC_IFVG_BB_AI_ENABLED=False` (iFVG/BB flag'leri Eylül backtest'e kadar AI'dan çekili). Orkestrasyon (feature hesabı, log, batch) burada DEĞİL — scan_pipeline'da. **Ne zaman dokun:** Master/Sentiment/Smart Money skorunun bileşen ağırlığı veya hesap mantığı.

### `scan_pipeline.py` (~3.450 satır) — MASTER SCAN BORU HATTI
Tüm toplu tarama mantığı: `_compute_signal_features` (feature düğümü — bir hissenin tüm f_* flag'lerini üretir), `log_scan_signal`/`log_erken_radar` (patron.db'ye yazar), `backfill_signal_returns` (getiri hesabı), tüm `scan_*_batch` aileleri, chart_patterns/golden_pattern_agent/golden_trio. `_is_index_symbol` + İkili Tepe/Dip endeks toleransı (18 Tem). Master Scan butonuna basınca çalışan 14 adım buradaki fonksiyonları çağırır. **Ne zaman dokun:** yeni tarama ekleme, feature flag ekleme, Master Scan adımı değiştirme, DB'ye yeni kolon yazma.

### `scanners.py` (~2.150 satır) — TEK-HİSSE TARAYICI ÇEKİRDEKLERİ
12 saf tarayıcı + Erken Radar ailesi (36 senaryo `ERKEN_RADAR_SCENARIOS` + `_er_*` yardımcılar + `evaluate_erken_radar`). Gizli Birikim, Radar1/2, ICT Setup, Güçlü Dönüş, Pre-Launch BOS, Nadir Fırsat çekirdekleri. 5 formasyon şekil-doğrulayıcı (cup/tobo/double-bottom/double-top/wedge — `_detect_*`). `_is_index_symbol` endeks tespiti. Girdi: sembol + hazır DataFrame; çıktı: sinyal dict veya None (veri çekme/DB YOK). **Ne zaman dokun:** bir taramanın TETİK KURALI (hangi hisse yakalanıyor), Erken Radar senaryosu ekleme/değiştirme, formasyon şekil eşiği.

### `ict_core.py` (~2.400 satır) — ICT + PA-DNA + MINERVINI + HARMONİK
`calculate_ict_deep_analysis` (ana ICT: OB/FVG/bias/zone/model_score), `calculate_price_action_dna` (PA-DNA + smart_volume dict: POC/VA/delta/OBV/RVOL), `calculate_minervini_sepa`, harmonik XABCD + confluence, `compute_sfp_flags` (SFP tuzakları — terazi + PA-DNA ortak kaynak). 14 maddelik ICT paketi: swing tespiti, kırılım onayı, DENGE bölgesi, sweep, FVG mitigasyon. Eşikler (`IC` sözlüğü) BAŞLANGIÇ değeri — Eylül 2026 karnesiyle kalibre edilecek. ⚠️ Kullanıcı notu: BIST'te ICT'ye fazla güvenilmez (backtest'te ICT Sniper zayıf çıktı) — ICT paneli görsel bilgi, ana hüküm değil. **Ne zaman dokun:** ICT panelinin İÇERİĞİ/hesabı, harmonik formasyon, Minervini SEPA, SFP.

### `analysis_core.py` (~2.100 satır) — PANEL + AI PROMPT ANALİZ MOTORLARI
14 analiz motoru tek-hisse panellerini ve AI prompt'unu besler: 8-maddelik yol haritası (`calculate_8_point_roadmap` — factor_scores döner), haftalık çerçeve, OBV uyumsuzluk, STP, kırılım, MTF hizalama, sentetik sentiment, ICT dönüş, gelişmiş seviyeler, ER prompt metni, risk profili, tarama tier/güç (`get_active_scanner_tiers` — evidence.py haritasını okur), hacim kalite etiketi. `_classic_map` = hangi taramaların tier rozeti alacağı. **Ne zaman dokun:** Yol Haritası skoru, MTF vade uyumu, tarama tier gösterimi, haftalık çerçeve.

### `charts.py` (~860 satır) — ANA FİYAT GRAFİĞİ
`_main_price_chart_plotly` = tek fonksiyon, interaktif Plotly candlestick. SMC katmanları (OB/FVG/BOS), EMA144, SMA50/100/200, hacim, VWAP σ-bantları, aVWAP 52H, POC/naked POC çizgileri. **Ne zaman dokun:** ana grafiğin görünümü, hangi çizgi/bölge çiziliyor, SMC katman rengi.

### `evidence.py` (~120 satır) — KANIT TABLOLARI (TIER HARİTASI)
`SCANNER_TIER_MAP` (scan_type → tier/hit/ret/ad/not — 20g backtest bazlı), `ER_BACKTEST_SCORE` (Erken Radar senaryo puanları), `ER_ELIT_SCORE_MIN`, `SCANNER_PLAIN_DESC` (jargonsuz açıklamalar), `GUC_SCORE`. Hangi taramanın "elit/güvenilir/zayıf" sayılacağının TEK kaynağı. ⚠️ Sayı değiştirmeden ÖNCE signal_results ölçümü şart (ekstrapolasyon yasağı). 18 Tem revizyonu: TIER_1 boş, minervini+er_B11 TIER_2. **Ne zaman dokun:** bir taramanın tier'ını değiştirme (28 Tem doğrulama randevusu), ELİT/GOLD MINE sıralaması.

### `db_layer.py` (~610 satır) — VERİTABANI ÇEKİRDEĞİ
patron.db şeması: `init_db` (tüm tablolar — scan_signals + signal_returns + signal_results), `log_error` (errors.log), MKK yabancı (RSS fetch + sinyal), watchlist (yükle/ekle/sil), `get_scanner_optimal_windows`, `get_scenario_ages_batch`. **Ne zaman dokun:** DB şeması, yeni tablo, MKK yabancı verisi, watchlist.

### `pattern_core.py` (~300 satır) — FORMASYON MOTORU YARDIMCILARI
Formasyon geliştirme paketi (14 madde): adaptif pivot eşiği, ön-trend şartı (TOBO/Fincan), kulp süresi, hacim imzası (sönümlenme + dip tükenmesi + dönüş hacmi), kırılım sonrası retest/sahte-kırılım tespiti. `PC` sözlüğündeki eşikler BAŞLANGIÇ — Eylül backtest'le kalibre edilecek. **Ne zaman dokun:** fincan-kulp/TOBO/çift-dip formasyon kalitesi, retest mantığı.

### `terazi_core.py` (~370 satır) — KANIT TERAZİSİ ÇEKİRDEĞİ
Ekranın TEK sentez hükmünü üretir (boğa/ayı oyları toplar → hüküm + güven + çelişki + karşı-sinyal). `votes_from_features` (RSI/CMF/SFP/yabancı oyları), `votes_from_genel_ozet` (hacim/OBV/yapı/MFI), `semsiye_votes` (er_D4/D5 düşüş uyarısı), `sistemik_gun` (piyasa-şoku modu), `gun_karakteri`+`sok_degerlendir` (şok günü), `rsi_uc_rozeti`, `dokum_ozeti` (skor bileşen ayrışması). İLKE: ham sinyal = OY, türev skor = LENS (çift sayım yasağı). Ağırlıklar backtest'ten (rsi_kova, sert_gun). Render app.py'de. **Ne zaman dokun:** terazi hükmü nasıl kuruluyor, hangi oy kaç ağırlıkta, karne dili.

### `veri_bekcisi.py` (~290 satır) — TEK KAPI VERİ DOĞRULAMA
Veri katmanından panellere giden HER OHLCV df'i tek kapıdan geçirir; bozuk veri ekrana ÇIKAMAZ (5 kontrol: referans ayrışması ±1.25x / Frankenstein bar / doji salgını / bölünme zıplaması / bayat veri + hacim çökmesi). Bozuksa depo boşaltılıp taze denenir, olmadı boş df + kırmızı şerit + `logs/veri_bekcisi.log`. data_layer'ı IMPORT ETMEZ (döngüsel). EREGL 40.86-vs-9.4 vakasından doğdu. **Ne zaman dokun:** yeni veri-bozukluğu tipi yakalama, false-positive uyarı.

### `zamanlama_core.py` (~290 satır) — 4 SAATLİK MOMENTUM FİLTRESİ (ŞU AN KAPALI)
25 Ağu 2026'da eklendi. `veriler_4s/` deposundan 4S RSI + WaveTrend okuyup "tepeden alma freni" üretir. ⛔ **ÖLÇÜLDÜ, AYRIM ÇIKMADI** → `app.py` içinde `ZAMANLAMA_4S_ENABLED = False` ile susturuldu (1.692 sinyal × 3 vade; dengeli grup N=1455 üç vadede de ~0, diğerleri vadeye göre işaret değiştiriyor). Modül duruyor; ikinci rejimde `python _4s_filtre_backtest.py` koşup tablo tutarlılaşırsa bayrak açılır. İçindeki 4 koruma bayraktan bağımsız doğrudur: yarım bar reddi, 3 günlük bayat eşiği, fail-closed saatlik_kapi, `gunluk_kapi_gecti` (iki kademe üst basamağı). **Ne zaman dokun:** 4S eşikleri, bayrağı geri açma, günlük kapı ölçütü.

### `pusula_engine.py` (~610 satır) — PİYASA PUSULASI: ANLATI + ÖLÇÜLMÜŞ KARNE
24 Ağu 2026'da eklendi, **25 Ağu'da ÖLÇÜLDÜ** (`_pusula_backtest.py`, 3.985 sinyal). Fiyat kartı altındaki Piyasa Pusulası için 17 dallı arketip anlatısı üretir; `_synthesize_raw` durumu tespit eder, `synthesize_market_compass` sarmalayıcısı her arketipe **ölçülmüş karnesini** ekler (`ARKETIP_KARNE` sözlüğü → `note` sonuna yazılır, panelde "Ne anlama geliyor?" kutusunda görünür). Ölçüm 5 dalın TERS konuştuğunu gösterdi ("Taze Yükseliş" -3,20/isabet %27; 200 SMA çifti simetrik ters) → başlıklar durum tarifine, tahmin cümleleri gözlem diline çevrildi. Kazananlar: momentum run +10,38 · pullback +5,58. ⚠ TEK REJİM — rejim değişince backtest'i yeniden koş, SADECE `ARKETIP_KARNE`'yi güncelle. AI prompt bağlantısı YOK (ekran-only). **Ne zaman dokun:** arketip kuralları, karne sözlüğü, anlatı dili.

---

## B. VERİ HATTI (arka plan — parquet üretimi + politika + onarım)

### `fetcher.py` (~505 satır) — ARKA PLAN VERİ ÇEKİCİ (cron 10dk)
Tüm BIST'i dönüşümlü kaynaktan (yfinance ↔ isyatirim ↔ borsapy) çeker, `veriler/*.parquet`'e atomic yazar; başarısızsa eski korunur. Hacim koruması (kaynak rotasyonu birbirinin hacmini ezmesin), kapanış penceresi (18:15-18:45 5dk tur), endeks TL ciro override (`override_index_ciro` → data_layer'dan). VPS'te systemd/cron. **Ne zaman dokun:** veri çekme sıklığı, kaynak sırası, yeni endeks TL cirosu (INDEX_CIRO_TARGETS'a ekleme).

### `data_policy.py` (~36 satır) — VERİ POLİTİKASI TEK KAYNAK
`AUTO_ADJUST=False` (HAM fiyat — seviyeler aracıyla tutsun). fetcher + app + repair + rebaseline hepsi bunu okur; "karışık politika bozulması" (2 Tem) bir daha olamaz. **Ne zaman dokun:** neredeyse hiç (temel veri politikası kararı).

### `bist_calendar.py` (~240 satır) — BORSA TAKVİMİ
Milli/dini tatiller, arefe yarım günleri, `is_trading_day`/`is_half_day`/`get_session_hours`/`get_rvol_day_factor`. **Ne zaman dokun:** yeni tatil, seans saati, arefe hacim normalizasyonu.

### Parquet onarım/bakım script'leri (tek-seferlik veya periyodik)
- `repair_parquets.py` — bozuk parquet düzeltme-bazı onarımı (>%30 sıçrama imzası; taze de imzalıysa dokunmaz = gerçek olay). Bozulma şüphesinde koş.
- `rebaseline_parquets.py` — TEK SEFERLİK tam yeniden bazlama (politika birleşince bir kez koşuldu).
- `fix_recent_close.py` — provizyon kapanışı oturmuş İsyatirim ile düzeltir (fetcher erken çalışıp bayat Close yakalarsa).
- `finalize_volume.py` — kapanış sonrası İş Yatırım hacmini, eksik sembollerde kontrollü borsapy yedeğiyle tamamlamayı aday sürüm olarak yürütür; doğrudan parquet'e yazmaz.
- `compare_sources.py` — yfinance vs İsyatirim tutarlılık testi (anomali raporu).

---

## C. TELEGRAM BOT (Streamlit'siz — smartmoneyradar Telegram kanalları)

### `smr_core.py` (~4.970 satır) — BOT ANALİZ MOTORU
Streamlit'ten TAMAMEN bağımsız analiz motoru; smr_bot bunu çağırır. `get_data`/`get_stock_info`, PA sinyali, `calculate_ict_analysis`, grafik PNG üretimi, `build_ai_prompt` (PRO) + `build_ai_prompt_gorev1` (ELITE) Gemini prompt'ları. `_base_data_block` (ortak veri bloğu — endeks modu dahil), `_apply_lean_prompt` (üslup kesimi) + `_LEAN_SAFE_RULES` (kesilmeyen doğruluk kuralları), 3 deterministik kapı (uyarı/-meli/endeks). RSI kova + şok günü senkronu (terazi_core'la aynı dil). app.py ile mantık paralel ama AYRI kod (senkron manuel). **Ne zaman dokun:** bültenin AI metni, PRO/ELITE kart formatı, prompt kuralı.

### `smr_bot.py` (~2.535 satır) — TELEGRAM BOT ARAYÜZÜ
Telegram komutları + günlük bülten gönderimi; smr_core'u çağırır (Playwright/Streamlit yok). mplfinance grafik + ICT Bottom Line + AI Görev 3 kartı. `/bulten` admin komutu, `/hediye` PRO hediye, deterministik uyarı/-meli kapıları (`_enforce_*`). **Ne zaman dokun:** bot komutu, gönderim zamanı, kart üretim akışı, sızıntı koruması.

### `smr_tickers.py` (~240 satır) — BOT TİCKER LİSTESİ
Kullanıcının Telegram'da yazabileceği geçerli semboller (`#KCHOL` vb.). app.py ASSET_GROUPS ile senkron tutulmalı. **Ne zaman dokun:** bota yeni sembol ekleme.

### Görsel/infografik üreticileri
- `infografik_build.py` (~480 satır) — gerçek paneller + Plotly grafik → tek PNG (Görev 4 görsel ürün, chromium screenshot). PRO/ELITE görsel.
- `infografik_telegram.py` — infografik_build.render()'ı çağırıp PNG'yi Telegram'a atar (kod kopyası yok).
- `infographic.py` (~610 satır) — v1 tek-ticker HTML→PNG infografik (eski sürüm).
- `compass_panel.py` — PARA AKIŞI pusulası standalone (app'in Force Compass'ı birebir; HTML snippet).

---

## D. TAVAN AİLESİ (T+1 tavan riski tahmini — ayrı ürün)

- `tavan_engine.py` (~310 satır) — **TAVAN SKORLAMA TEK KAYNAK.** Rejim×kalıp ağırlıkları, confluence eşiği 30, manipülasyon filtresi. Kanonik davranış = canlı app.py B38. Skor mantığı DEĞİŞECEKSE burası + app.py B38 birlikte (drift_guard doğrular).
- `tavan_scanner.py` — CLI: T günü → T+1 tavan riski skoru + CSV (engine'i çağırır).
- `tavan_tracker.py` — canlı forward isabet + "dünkü adaylar ne yaptı" retrospektifi (drift erken yakalama).
- `tavan_telegram.py` (~450 satır) — TAVAN ADAYLARI → SMR Free kanalı (09:45). app motorunun standalone kopyası.
- `tavan_gunluk.py` — VPS cron tek giriş: dünü ölç + retro + bugünü tara + mesaj üret.
- `tavan_calibrate.py` — ağırlık ÖNERİsi üretir (auto-apply YOK, walk-forward overfit koruması). İnsan karar verir.
- `tavan_drift_guard.py` — tavan_engine ⇔ app.py B38 birebir aynı mı (AST ile, her koşuda PASS/FAIL).
- `reconstruct_tavan_para.py` — kayıp günlerin (19-29 Haz NameError) tavan/para sinyallerini parquet'ten geri üretir.

---

## E. ÖLÇÜM / BACKTEST / DENETİM (kanıt üretenler — "işe yarıyor mu?")

### `golden_record.py` (~620 satır) — EMNİYET KEMERİ ⚡
app.py'deki hesap fonksiyonlarının davranışını "fotoğraflar" (5 hisse × 69 fonksiyon); her değişiklikten sonra aynı donmuş veriyle aynı sonucu verdiğini doğrular. `python golden_record.py` = sıfır fark bekle; `--init` = bilinçli değişiklik sonrası yeniden bazla. HER hesap değişikliğinden sonra ZORUNLU koşulur. Referans `golden_record.json` (lokal, git'te değil). **Ne zaman dokun:** yeni fonksiyon fotoğraf kapsamına alınacaksa (PROBES listesi).

### `backtest_runner.py` — İLERİ GETİRİ MOTORU
patron.db (scan_signals) + parquet → signal_results tablosu (5g/10g/20g getiri, hit, stop, max gain/loss). İki bekçi (kurumsal işlem/bölünme + baz uyumu — zehirli getiri önleme). 19:30 cron. **Ne zaman dokun:** getiri hesabı, backtest bekçisi.

### `universe_snapshot.py` (~1.070 satır) — TÜM EVREN GÜNLÜK FOTOĞRAF
HER hisse HER gün ölçülür (tarama seçsin/seçmesin) → seçim yanlılığı olmadan "hangi skor gerçekten öngörüyor". Feature formülleri scanner_karne ile birebir (--verify). Standalone, canlıya dokunmaz. **Ne zaman dokun:** yeni feature'ın evren-geneli edge'ini ölçme.

### Karne / denetim script'leri
- `scanner_karne.py` (~630 satır) — measure→filter tek komut: her taramanın gerçek 10g hit/ret'i + rejim kırılımı (boğa/ayı ay ayrımı). Tier kararı girdisi.
- `feature_karne.py` (~230 satır) — her f_* feature'ın tek tek karnesi (scan_signals × signal_results JOIN).
- `goldmine_meta.py` — GOLD MINE vitrini işe yarıyor mu (rank getiriyi öngörüyor mu).
- `app_score_audit.py` — app'in kendi kaydettiği kompozit skorların getiri karnesi (drift yok).
- `flag_health_audit.py` (~190 satır) — HAFTALIK flag sağlık (yeni-ölü/tek-değer/gürültü/çelişki). Task Scheduler Pazar 20:00.
- `heartbeat_monitor.py` (~180 satır) — "denetimleri denetleyen bekçi", ölü-adam anahtarı (her gün Telegram; gelmezse bekçi ölmüş).
- `settle_probe.py` + `settle_report.py` — kapanış oturma zamanı ölçümü (18:05-21:00, ≥20 gün → kesin saat).
- `vade_sweep.py` / `vade_sweep_all.py` — ideal vade eğrisi (5-10-20 mi 12 mi).
- `MerdivenTarama.py` — 10 yıllık "merdiven vs testere" karakter analizi (araştırma).

### Backtest çıktı JSON'ları (script'ler yazar, app/panel okur)
`golden_record.json` (emniyet referansı) · `backtest_results.json` · `genel_ozet_verdict_backtest.json` (Smart Money verdict karnesi) · `panel_verdict_backtest.json` · `rsi_kova_backtest.json` · `sert_gun_backtest.json` · `tavan_weights_onerilen.json`. **Rejim değişince** ilgili backtest yeniden koşulur.

---

## F. WEB SİTE + ÜYELİK (smartmoneyradar.app)

- `free_gate.py` (~190 satır) — anonim tadımlık kapısı (çerez + IP, 24s'de 1 ücretsiz analiz; endeks serbest). systemd `free-gate`.
- `produce_light_stock.py` — light site (frontend_light) tek-hisse JSON üretici (app.js'in latest.json şekline uyumlu).
- `produce_stock_json.py` — BIST100 bireysel JSON üretici (eski/sabit liste).
- `firsat_radari.py` — likit tahtalarda formasyon-önce boyun kırılım tarayıcı + Telegram (cron 19:20).
- `weekly_list.py` — haftalık CMF top-10 + geçen haftanın gerçek sonucu → admin Telegram (Pazar).
- `leads.py` — toplanan e-posta özeti (kaç kişi, kaynak).

---

## G. VERİ DOSYALARI & CONFIG

- `patron.db` (SQLite) — Master Scan sonuçları: scan_signals + signal_returns + signal_results + analysis_log + goldmine_log + mkk_yabanci. Git'te DEĞİL (haftalık yedek: backup_patron_db.ps1).
- `signals.db` — bot sinyalleri.
- `veriler/*.parquet` — her hissenin günlük OHLCV cache'i (SYMBOL.IS_1d.parquet). `.index_components.json` = XU100 bileşen listesi cache.
- `email_leads.json` / `members.json` / `member_usage.json` / `usage_tracker.json` — üyelik/lead verisi.
- `telegram_config.json` — bot token/chat id'leri.
- `.streamlit/config.toml` — tema (koyu, primary #10b981).
- `.claude/launch.json` — preview dev-server tanımları (patron-8501 vb.).

---

## H. DÖKÜMANTASYON

- `CLAUDE.md` — proje navigasyon + bölüm haritası (B1-B37) + fonksiyon-satır eşlemesi + oturum notları + kalıcı yasaklar. **Her oturum başı okunur.** Kısa kalır kuralı.
- `DOSYA_HARİTASI.md` — BU DOSYA (dosya-bazlı yol gösterici).
- `memory/` (55 md) — kalıcı hafıza: `SMR_SISTEM_OZETI.md` (tek kaynak mimari), `deploy_runbook.md` (VPS deploy adımları + geçmiş #1-#17), `MEMORY.md` (index), proje/feedback/reference md'leri. Sistemin TAMAMI için → `memory/SMR_SISTEM_OZETI.md`.
- Kök `*_report.md` — script çıktıları (scanner_karne_report, feature_karne_report, rsi_kova_report vb.), okunur, elle düzenlenmez.

---

## I. DEPLOY & YÖNETİM

- VPS: `wm11tr@34.153.19.220` · dizin `~/smr/` · canlı: http://34.153.19.220/patron/ + smartmoneyradar.app
- Servisler: `patron-radar` (Streamlit) · `free-showcase` · `smr-bot` · `free-gate` · `insider-bot` · cron'lar (fetcher/tavan/backtest).
- **app.py TEK BAŞINA GİTMEZ** — 12 bölme modülü + terazi_core + veri_bekcisi + smr_core + infografik_build birlikte. Tam scp + 3 kapı (golden yeşil / lokal ast.parse / VPS py3.10 compile) → `memory/deploy_runbook.md`.
- Deploy öncesi ZORUNLU: `python golden_record.py` sıfır fark + diff-önce kuralı (VPS'te commit'siz hotfix var mı).
