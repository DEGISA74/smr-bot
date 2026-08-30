# VERİ HATTI — DURUM & ÇALIŞANI BOZMA REHBERİ
> Son güncelleme: **6 Ağustos 2026** · Amaç: BIST fiyat/hacim verisinin nasıl aktığını, bugün neyin değiştiğini ve **neye dokunulmayacağını** tek yerde tutmak. Codex ↔ Claude ortak referansı.

---

## 1. VERİ NASIL AKIYOR (gerçek mimari)

```
┌─────────────────────────────────────────────────────────────────┐
│  VPS (34.153.19.220, ~/smr)  =  BIST verisinin TEK OTORİTESİ     │
│                                                                   │
│  fetcher.py (cron)                                                │
│    → Yahoo (fiyat) + İş Yatırım (yalnız hacim)                    │
│    → promote_batch → "onaylı sürüm kasası" (bist_data_store)      │
└───────────────────────────┬───────────────────────────────────────┘
                            │  SMR_Sync_VPS (her 5 dk, sync_from_vps.sh)
                            │  onaylı sürüm paketini çeker
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  LOKAL (bu makine)                                               │
│    veriler/*_1d.parquet  =  DEPO (ayna)                          │
│    app.py (Streamlit :8501)  → depoyu OKUR (Ayna Modu)          │
│    ⚠ SMR_Fetcher_BIST görevi DISABLED — lokal Yahoo'ya GİTMEZ   │
└─────────────────────────────────────────────────────────────────┘
```

- **TEK GERÇEK KURAL:** BIST günlük verisini **VPS fetcher yazar**, herkes (app, bot, lokal) **onaylı sürümü okur**. App/bot canlıya gidip **yazmaz** (Ayna Modu, `SMR_MIRROR_READONLY=1`).
- Okuma yolu: `data_layer.get_safe_historical_data()` → BIST kolu → `_read_bist_approved_version()`.
- Fiyat = **Yahoo** (ham OHLC). Hacim = **İş Yatırım** (Yahoo BIST hacmi bozuk). İş Yatırım fiyata ASLA dokunmaz.

---

## 2. BUGÜN (6 Ağu) NE DEĞİŞTİ — ÖLÇÜMLE

**Sorun:** Ekranda açık hisse donmuş/yanlış fiyat gösteriyordu (CWENE 43.98 vs 46). Kök neden arandı, **ölçüldü:**

| Bulgu | Ölçüm | Sonuç |
|---|---|---|
| VPS yahoo turu gerçekte kaç sn? | **700–1100 sn** (history'deki "318sn" yanıltıcı) | çok yavaş |
| Yavaşlatan `YAHOO_MAX_PER_MINUTE=120` mi? | 500'e çıkarıldı ama tur hâlâ yavaş; polisi TAM kapatınca **daha da yavaş (1106sn)** | polis DEĞİL |
| Asıl neden? | Çıplak 20 hisse 1.1sn AMA 615 sürekli → Yahoo **IP-başı throttle** eder | **çok sık/çok istek** |
| Toplu paket (`yf.download`) çözer mi? | **0.6x — DAHA YAVAŞ** ölçüldü | batch fikri ÖLDÜ |
| İş Yatırım (hacim) durumu? | 4.2 sn/hisse + %25–99 fail (saate göre) | yavaş+titrek, ayrı dert |

**Çözüm = ACİL LİSTE MODELİ** (daha az/seyrek iste, batch değil):

- `fetcher.py` → `_build_hot_list()` + `run_acil_liste()` + CLI `acil`.
- **Her 5 dk:** ACİL (~36: endeks+BIST30+favoriler, HER tur) + EVREN dilimi (en bayat 60, dönüşümlü). Evren ~50 dk'da tam döner. Yük ≈ 20 istek/dk → throttle bölgesinden uzak.
- Acil liste kaynağı: `data_layer.priority_bist_indices` (5 endeks + BIST30) + `db_layer.load_watchlist_db()` favoriler (BIST olmayan GC=F/SPCX elenir).

---

## 3. CANLI vs KAPALI — GÜNCEL DURUM

| Ne | Durum | Nerede |
|---|---|---|
| Acil-liste fetcher (`run_acil_liste`) | ✅ **CANLI** | VPS `fetcher.py` |
| Cron: `*/5 ... fetcher.py acil` (eski `*/10 yahoo` gitti) | ✅ **CANLI** | VPS crontab |
| `YAHOO_MAX_PER_MINUTE=500` (koruyucu pacing) | ✅ CANLI | VPS crontab (yahoo/acil/kapanis satırları) |
| Açık-hisse canlı yaması (`_maybe_live_patch_hot`) | ❌ **KAPALI** (`_HOT_LIVE_PATCH_ENABLED=False`) | `data_layer.py` (lokal+VPS) |
| Ayna Modu (app depoyu okur, yazmaz) | ✅ CANLI | `data_layer.py` |
| İş Yatırım hacim cron (`*/30 isyatirim`) | ✅ CANLI (değişmedi) | VPS crontab |
| kapanis cron | ✅ CANLI (değişmedi) | VPS crontab |

> **Açık-hisse yaması neden kapalı?** Yahoo `fast_info` BIST'te güvenilmez — bazen günün erken saatinde donuyor (EREGL 42.02 @10:25 vs gerçek 41.38; depo 41.40 DOĞRUydu). Acil model depoyu 5 dk'da tazeliyor → yama gereksiz + taze depoyu bozuyordu. `set_hot_ticker` (app.py) zararsız kalıyor. Geri açmak: flag `True`.

---

## 4. ⛔ ÇALIŞANI BOZMA — GUARDRAIL'LER

1. **`_HOT_LIVE_PATCH_ENABLED`'ı `True` YAPMA** — Yahoo fast_info donuyor, taze depoyu bozar. Ölçüldü, kapatıldı.
2. **`yf.download` toplu paket DENEME** — 0.6x, daha yavaş. Ölçüldü, reddedildi.
3. **Yahoo'yu DÖVME** — full 615'i sık koşturmak (test dahil) IP'yi **saatlerce** throttle eder. Ölçüm/test için 20-50 hisselik küçük parti kullan, tekrar tekrar 615 koşturma.
4. **`fetcher.py yahoo` (615) SIK ÇALIŞTIRMA** — cron artık `acil`. Full tur yalnız gerekiyorsa elle, seyrek.
5. **flock KİLİTLERİNİ KALDIRMA** — `fetcher-price.lock` / `fetcher-volume.lock` süreç birikmesini (pileup) engelliyor. Tur 5dk'yı aşarsa sonraki tick atlar, üst üste binmez.
6. **İş Yatırım'ı PARALELLEŞTİRME** — `isyatirimhisse` thread-safe DEĞİL. `MAX_WORKERS_ISYATIRIM=1` mecburi.
7. **Lokal `SMR_Fetcher_BIST`'i AÇMA** — bilerek kapalı. Lokal depo VPS'ten sync ile beslenir; lokal Yahoo'ya gitmek çift kaynak/çakışma yaratır.
8. **App/bot BIST'e CANLI YAZDIRMA** — Ayna Modu (`SMR_MIRROR_READONLY=1`) korunur. Yazıcı yalnız fetcher.

---

## 5. DEPLOY KURALLARI (VPS)

- **3 kapı:** (1) `python golden_record.py` yeşil (hesap değiştiyse), (2) lokal `ast.parse`, (3) VPS `venv/bin/python -m py_compile` → `sudo systemctl restart patron-radar free-showcase`.
- **EZMEDEN ÖNCE DIFF:** `scp` ile VPS dosyasını çek, lokalle `diff --strip-trailing-cr` at. Sadece amaçladığın değişiklik görünmeli.
- ⚠️ **`app.py` CERRAHİ deploy gerektirir:** lokal app.py'de ~142 satır **deploy edilmemiş WIP** var (Smart Money panel redesign vb.). Lokal app.py'yi olduğu gibi atma → sadece hedef değişikliği VPS kopyasına uygula, geri yolla.
- `fetcher.py` / `data_layer.py`: bugün lokal==VPS'ti, doğrudan deploy edilebildi (yine de diff-first).
- **Crontab yedekleri:** VPS `~/smr/crontab_backup_20260806*.txt`. Değiştirmeden önce `crontab -l > yedek`.
- SSH: `ssh wm11tr@34.153.19.220 "komut"` (root değil).

---

## 6. AÇIK İŞLER

- 🔜 **Yarın sabah:** acil turunun gerçek süresini doğrula → `~/smr/logs/fetcher.log` içinde `ACİL LİSTE TURU` / `tur bitti (Xsn)`. Throttle geçince **~30sn** beklenir. Değilse `cold_chunk`'ı (şu an 60) ayarla.
- ⏳ **İş Yatırım hacim** yavaş/titrek — fiyatı ETKİLEMEZ (ayrı kilit, volume-only). Düşük öncelik; istenirse kapsamı daralt (hot list hacmi) ya da seyrek bırak.
- ⏳ **Favori olmayan açık hisse:** depo ~50 dk'da bir tazelenir (cold rotation). Önemli hisseyi **favoriye alınca** hot listeye girer → 5 dk'ya iner.
- ⏳ `cold_chunk` ince ayarı (60 → daha az/çok), throttle davranışına göre.

---

## 7. HIZLI KOMUTLAR

```bash
# Acil turu süresi (VPS)
ssh wm11tr@34.153.19.220 "grep -E 'ACİL LİSTE TURU|tur bitti' ~/smr/logs/fetcher.log | tail -4"

# Acil liste içeriği (fetch yok)
ssh wm11tr@34.153.19.220 "cd ~/smr && venv/bin/python -c 'from fetcher import _build_hot_list, load_bist_tickers as L; print(_build_hot_list(L()))'"

# Fetcher tur geçmişi
ssh wm11tr@34.153.19.220 "tail -8 ~/smr/logs/fetcher_history.jsonl"

# Aktif cron (fetcher satırları)
ssh wm11tr@34.153.19.220 "crontab -l | grep 'fetcher.py'"
```

---

## 8. AKŞAM KESİNLEŞTİRME + VERİ BEKÇİSİ (18 Ağu 2026)

**Olay:** PC uzun süre kapalı kaldı → lokal ayna 14:00'da dondu. Ama araştırınca
PC'yle ilgisi olmayan **iki VPS arızası** da çıktı, ikisi de aylardır sessizdi:

| Arıza | Ölçüm | Sonuç |
|---|---|---|
| `kapanis` turu borsa kapanmadan bitiyor | 17:40 başlar, tam tur ~12 dk → **17:52'de biter**, seans 18:10'da kapanır | 192 hissenin "kapanışı" gün-içi fiyat |
| İş Yatırım hacim gün içi çöküyor | 17:00→%95,6 · 17:39→%87,6 · 18:04→%94,1 · 18:31→%99,2 · 18:54→%95,1 · **21:52→%4,7** | hacim Yahoo'ya (BIST'te bozuk) düşüyor |
| Kimse haber almıyor | iki arıza da yalnız `fetcher.log`'a yazıldı | bekçi yoktu |

**Kurulan 3 kapı:**

1. **`fetcher.py kapanis_final`** (yeni mod) — süre kutusuz tam Yahoo turu.
   Cron: `50 15 * * 1-5` (**18:50 TR**), yani `kapanis` penceresi (→18:40) bittikten
   sonra. Fiyat kapanış kesinleştikten SONRA tek sefer çekilir.
2. **Akşam hacim turları** — cron `30 18` + `15 19` (**21:30 + 22:15 TR**)
   `fetcher.py isyatirim`. İkincisi birincinin fail'lerini toplar. İkisi de
   20:00 Master Scan'inden SONRA: tur, tarama sürerken sürüm değiştirmesin.
   Ölçüm (17–18 Ağu, 613 hisse): İş Yatırım **16:00–19:00 TR arası çöküyor**
   (ok=5…82), **22:00 civarı dönüyor** (ok=584). Bu yüzden hacim turunu erkene
   almak işe yaramaz; 20:00'de elde olan Yahoo'nun kapanış-sonrası tam gün
   hacmidir (İş Yatırım'dan farkı ölçüldü: **%1,5**).
3. **VERİ BEKÇİSİ** (`gorev_bekcisi.py` içinde `VERI_KAPILARI`) — onaylı sürümün
   manifestini SALT OKUR, eşiğin altındaysa **Telegram**:
   - 💰 *Kapanış fiyatı*: bugünün barı olan hisse ≥ **%92** (deadline 19:30 TR)
   - 📊 *Hacim kapsamı*: `Volume=isyatirim` resmî ana kaynak, `Volume=borsapy` kontrollü yedek; birlikte kullanılabilir oran ≥ **%80** (deadline 23:15 TR)
   - Gün sonu özetine "BIST VERİSİ" satırları eklendi (yeşil tik = bekçi canlı).

**20:00 OTOMATİK MASTER SCAN İLE İLİŞKİSİ (okumadan dokunma):**
`kapanis_master_otomasyon.py` saat 20:00'de **kontrol** eder, tarama saati değildir.
Kapı **fiyata** bakar (`scan_ready = price_ready`, temiz hisse ≥ %95); hacim oranı
(`FINAL_VOLUME_MIN_RATIO = 0.85`) yalnız rapor/istek içindir, **taramayı rehin almaz**
(kodda açık yorum var). 18 Ağu'da tarama 20:00 yerine **22:28'de** çalıştı — sebep
hacim değil, **lokal aynanın 14:00'da donmuş olmasıydı** (PC kapalı → senkron yok →
fiyatlar bayat → kapı bekledi). Telafi senkronu + 18:50 kapanış turu bunu kapattı;
düzen artık 20:00'de taramayı başlatacak durumda.

**⚠️ ÖNEMLİ SIRA KURALI — fiyat turu hacmi de ezer.** Yahoo turu dokunduğu hissenin
Volume'ünü `yahoo_provisional`'a düşürür (ölçüldü: THYAO 27,17mn İş Yatırım →
26,77mn Yahoo, %1,5 fark). Bu yüzden **günün SON turu hacim turu olmalı**.
Yeni cron sırası buna göre: fiyat 18:50 → hacim 20:15 → hacim 22:15.

**Lokal senkron artık susmuyor:** `sync_from_vps.sh`'deki saat kapısı (hafta içi
07:00–19:00) kaldırıldı. Yeni kural: hafta içi **07:00–23:30** normal pencere
(akşam turları bu pencereye girer); pencere dışında `depo_tazelik.py` (yeni, lokal)
manifeste bakar — beklenen son işlem gününün barı hisselerin <%90'ındaysa **telafi
turu** koşar. Kontrol lokalde yapılır, VPS'e yük binmez. Windows görevi
`StartWhenAvailable=true` → PC açılır açılmaz kaçan tur çalışır.

Test/ayar: `python depo_tazelik.py` (0=bayat, 1=taze) · `SMR_TAZELIK_ESIK=1.1` ile
telafi yolu zorlanır.

**YAZIM KAPISI (19 Ağu 2026):** tarama, sinyal yazmadan önce deponun beklenen seansı
taşıdığını doğrular (`depo_tazelik.yazim_izni` → `scan_pipeline.log_scan_signal`).
Bayatsa hiçbir satır yazılmaz + ekranda kırmızı uyarı. Sebep: 18 Ağu'da donuk aynayla
koşan tarama 1.437 sinyali öğlen fiyatıyla yazmıştı (gün silindi). Kaçış:
`SMR_BAYAT_YAZIM_IZNI=1`. Gün içi tur sayımı: `python tur_denetimi.py`.

> İlgili hafıza: `memory/project_acik_hisse_canli_yama.md` · `memory/project_lokal_veri_mimarisi.md`

---

## 9. KAPANIŞ TURU SESSİZ KAÇIYORDU — CRON ÇAKIŞMASI (19 Ağu 2026)

**Bulgu:** `kapanis_final` (günün kesin fiyat turu) 18 Ağu'da kurulduğundan beri
**hiç çalışmamış**. Sebep kod değil, **cron çakışması**: `50 15` (kapanis_final) ile
`*/5 7-15` (acil) aynı dakikaya düşüyor ve **ikisi de `fetcher-price.lock`** kullanıyor.
`flock -n` beklemez → kapıyı hangisi önce tutarsa diğeri **sessizce, log'a tek satır
yazmadan** ölür. Acil tur kazanıyordu.

**Ölçüm (19 Ağu):** tur elle koşturulunca 613/613 ok, **264 hissenin fiyatı değişti** →
o kadar hissenin "kapanışı" gün-içi fiyattı. `logs/fetcher.log`'da `KAPANIS FINAL`
ibaresi hiç geçmiyordu (arama ile doğrulandı).

**Fix:** `kapanis_final` → **`5 16` (19:05 TR)**. Son acil tur 15:55 UTC'de başlar,
~150sn sürer → 6+ dk pay kalır. Gün sonu sırası korunuyor: **fiyat 19:05 → hacim 21:30
→ hacim 22:15**. Cron yedeği: VPS `~/smr/crontab_backup_20260819_kapanis_final.txt`.

**DERS — aynı kilidi paylaşan iki cron'u asla aynı dakikaya koyma.** `flock -n` sessizdir;
arıza log'a bile düşmez. Kontrol: `grep -c 'KAPANIS FINAL' ~/smr/logs/fetcher.log`
(gün başına 1 olmalı).

**SAATLİK TARAF (aynı gün):** PC uzun kapalı kalırsa saatlik depo öğleden sonrayı
kaçırır ve `run_saatlik.sh`'deki `18:30 sonrası çalışma` kapısı yüzünden **kendi kendine
telafi edemez**. Elle telafi (kapanıştan sonra çalışır, en doğru saat):
```bash
export PYTHONIOENCODING=utf-8
.venv/Scripts/python.exe intraday_4s.py XU100 XU030 XBANK
.venv/Scripts/python.exe intraday_4s.py --liste 250
```
19 Ağu telafisi: kapsam listesinde TAMAM 0 → **217/253**. Kalan 36 sembol 3 Ağu/28 Tem'de
takılı — bunlar **ayrı ve eski bir dert** (Yahoo bu tickerlarda 1h'i "730 gün dışında"
diye reddediyor), PC kapalılığıyla ilgisi yok.

---

## 10. AKŞAM ZİNCİRİ — 3 SESSİZ ARIZA + YENİ SIRA (19 Ağu 2026)

**(a) `SMR_Finalize_Volume` 8+ gündür HER GÜN reddediliyordu.** Görev 18:35'te
koşuyordu; `finalize_volume.py` 613 hisseyi İş Yatırım'dan çekip **≥%85 kapsama**
istiyor. 18:35 tam İş Yatırım'ın çöküş penceresi (16:00–19:00 TR) → ölçülen kapsama
**%0–48**, sonuç hep `Hacim turu RED`. Yani "kesin hacim" hiç yazılmadı.
**Fix: görev → 22:50** (VPS'in 22:15 hacim cron'undan sonra, 23:30 senkron penceresi
içinde). İş Yatırım ~22:00'de toparlıyor (ölçüm: ok=584/613).

**(b) Saatlik son tur kapanışı kaçırabiliyordu.** `run_saatlik.sh` kapısı 18:30'du ve
görev :23/:53'te tetikleniyor → kapanıştan (18:10) sonra garanti tam tur yoktu.
**Fix: kapı 18:45 + göreve hafta içi 18:35 tetikleyicisi.** Tur ~4 dk → **18:40'ta biter**.

**(c) Master Scan'in 20:00 emniyet ağı KURULDUĞUNDAN BERİ ÖLÜYDÜ.**
`master_scan_headless_session.ps1` içinde:
`Start-Sleep -Seconds [math]::Ceiling(...)` → PowerShell **argüman modunda**
`[math]::Ceiling(...)`'ı METİN sayar, `-Seconds`'a bağlayamaz; `$ErrorActionPreference
= 'Stop'` ile script tam orada ölür. Görev 19:55'te tetiklendiği için koşul
(`şimdi < 20:00`) HER GÜN doğruydu → görünmez tarayıcı oturumu **hiç açılmadı**.
Taramalar yine de oluyordu çünkü kullanıcının ekranı açıktı; **ekran kapalı bir
akşamda tarama hiç çalışmazdı.** Fix: ifade parantezi (`([math]::Ceiling(...))`).
Doğrulama: oturum açıldı, tarama **20:17'de** başladı.

> **DERS:** PowerShell'de `-Param [tip]::Metot(...)` sessizce metin olur — daima
> parantezle sar. Ve "çalışıyor gibi görünen" otomasyonu **ürettiği çıktıdan**
> doğrula (profil klasörü boştu, kimse bakmamıştı).

**AKŞAM SIRASI (19 Ağu itibarıyla):**
`18:35 saatlik son tur` → `19:05 kapanis_final (fiyat)` → `20:00 Master Scan` →
`21:30 hacim` → `22:15 hacim` → `22:50 finalize_volume (İş Yatırım + kontrollü borsapy yedeği)`.
Fiyat turu hacmi `yahoo_provisional`'a düşürdüğü için **hacim işleri en sonda** kalır.

## 9. AKŞAM SAATLERİ ÖLÇÜME GÖRE YENİDEN KURULDU (26 Ağu 2026)

**Dayanak:** kapanış oturma probu 24 iş günü topladı (02.07–04.08, 7.990 ölçüm) ve
20 gün hedefini doldurup kendini emekliye ayırdı. Rapor 31 Tem'den beri 19 günlük
hâliyle bekliyordu; yeniden koşuldu → `logs/settle_report_final_20260826.txt`.

**Ölçüm:** kapanış oturması medyan gün **18:36** · %95 gün **19:49** · en kötü gün
**20:58**. (borsapy ↔ İş Yatırım nihai kapanış **73/73 %100 uyumlu**, borsapy 71 kez
ERKEN oturuyor → konsensüs kaynağı olarak bağlanmaya hazır, henüz bağlanmadı.)

**Neden sorun:** kapı `scan_ready = price_ready` **kapsamaya** bakıyor (bugünün barı
olan hisse ≥%95) — barın oturmuş mu provizyon mu olduğuna DEĞİL. 18:50'de paketlenen
tur kapsamayı doldurabiliyor, kapı memnun oluyor ve tarama **provizyon fiyatla**
`scan_signals`'a entry_price yazıyor. Fiyatlar ertesi sabah incremental turla
kendini onarıyor ama yazılmış entry_price kalıcı — 18 Ağu kazasının hasar biçimi.

**Değişen (Windows Görev Zamanlayıcı):**

| Görev | Önce | Sonra | Gerekçe |
|---|---|---|---|
| `SMR_Settle_Kapanis` | 18:50 | **19:50** | %95 oturma çizgisi 19:49'un üstü; tur ≤5,5 dk → 20:00 kapısına ≥4 dk pay |
| `SMR_Backtest_Daily` | 19:30 | **21:00** | tarama penceresi (20:00→~20:20, medyan tur 19,2 dk) bittikten sonra, 21:30 hacim cron'undan önce |
| `SMR_Finalize_Volume` | limit PT10M | **PT45M** | ölçülen tam tur **16,2 dk** (4 Ağu logu) — 10 dk limiti işi HER GÜN öldürüyordu |
| `SMR_Backtest_Daily` + `SMR_Finalize_Volume` | pilde dur = açık | **kapalı** | dizüstü; fiş çıkınca iş yarıda kesiliyordu (0x40010004) |

Her iki görevin tetikleyicisi **hafta içi (Pzt-Cum)** olarak da netleştirildi.

**Dokunulmayan (bilerek):** `SMR_MasterScan_AutoStart` 19:55 / kapı 20:00 —
`kapanis_master_otomasyon.py` içinde `CHECK_HOUR = 20` SABİT KODLU, saat değiştirmek
kod değişikliği demek. Gerek de yok: kapı hazır değilse zaten kendisi bir settle turu
İSTİYOR (log kanıtı: 24/25 Ağu'da 18:5x'teki planlı turdan sonra ~20:01'de ikinci bir
tur daha koştu). 19:50'ye çekmekle her iki yol da artık oturma çizgisinin üstünde.
`SMR_Finalize_Volume` SAATİ 22:50 kalıyor — İş Yatırım ~22:00'de toparlıyor (§8 ölçümü).

**Sıra kuralı korundu:** fiyat 19:05 (VPS) → fiyat 19:50 (lokal) → tarama 20:00 →
backtest 21:00 → **hacim 21:30 → hacim 22:15 → finalize 22:50**. Günün son turu yine
hacim turu; tarama sürerken sürüm değiştiren tur yok.

**Raporun önerdiği 21:08 UYGULANMADI** — o sayı "en kötü gün + 10 dk" ile tek başına
hesaplandı, bu zincirin sıra kuralını bilmiyor. 21:08'e çekilen bir fiyat turu 21:30
hacim turuyla ve 20:00 taramasıyla çakışırdı. Kapsanan: günlerin ~%95'i. Kalan ~%5
(20:58'e kadar sarkan günler) için zincirin tamamının geceye kaydırılması gerekir —
ayrı karar.

### 9b. borsapy KONSENSÜS KAPISI BAĞLANDI (26 Ağu 2026)

Saat kaydırma günlerin ~%95'ini kapatıyor ama kalan payı kapatan şey saat değil,
**ikinci bir kaynağın teyidi**. Ölçüm bunu destekliyor: borsapy ↔ İş Yatırım nihai
kapanış **73/73 (%100)** aynı, borsapy **71 kez ERKEN** oturuyor, **hiç geç kalmıyor**.

**Nereye bağlandı:** `settle_kapanis.py` — o ana kadar depodaki satırı ezmeye aday
göstermek için **yalnız Yahoo'ya** bakıyordu.

**Kural (yalnız DEĞİŞECEK satırlara sorulur, tipik ~40 hisse):**

| borsapy ne diyor | Sonuç | Kaynak etiketi |
|---|---|---|
| Aynı günü aynı fiyatla doğruluyor (±%0,15) | aday **kalır** | `yahoo_borsapy_konsensus` |
| Farklı söylüyor | aday **DÜŞER** — biri henüz oturmamış, paketlenmez | — |
| Veri yok / hata (tek tekrar sonrası) | **eski davranış** (Yahoo'ya güven) | `yahoo_settled` |

Süre bütçesi **120 sn**; aşılırsa kalan adaylar eski davranışla geçer. Görev limiti
PT8M → **PT12M**. Kapatma: `SMR_SETTLE_KONSENSUS=0`.

**Test (izole, yan etkisiz):** 8 likit hissede borsapy ↔ depo **7/7 birebir**
(fark 0,000), 1 anlık boş dönüş → tek tekrar eklendi (aynı hisse ardından 3/3
doğru). 5 sahte adayla üç dal da doğrulandı: birebir uyan 2 + tolerans içi 1
**teyitli**, %3 sapmalı (provizyon taklidi) **düştü**, borsapy'de olmayan sembol
**eski davranışa** geçti. Hız ~1,8 sn/hisse.

**Kapsam sınırı — bilerek:** borsapy yalnız bu teyit kapısına bağlandı, genel
çekim kaynağı yapılmadı. Ölçüm borsapy'nin *doğruluğunu* kanıtlıyor, *hızını*
değil (~1,8 sn/hisse, İş Yatırım kadar yavaş) — 625 hisselik turda kullanılamaz.

**Açık:** VPS'in `fetcher.py kapanis_final` turu **19:05 TR**'de koşuyor, yani o da
%95 oturma çizgisinin (19:49) altında. Lokal 19:50 turu sonradan düzeltiyor; aynı
konsensüs kapısını VPS turuna da koymak ayrı bir iş.

### 9c. AYNI KAPI VPS TURUNA DA KONDU (26 Ağu 2026)

§9b'nin sonundaki açık kapatıldı: `fetcher.py kapanis_final` (VPS cron **19:05 TR**)
de aynı konsensüs kapısından geçiyor. Kanca: `run()` fonksiyonuna `candidate_filter`
parametresi eklendi — adaylar sürüm kapısına gitmeden önce süzülüyor. Yalnız
`kapanis_final` kullanıyor; diğer turlar `None` ile eski yoldan geçiyor. Süzgeç
patlarsa adaylar **dokunulmadan** geçer (try/except).

**Ölçek sorunu nasıl çözüldü:** tur 615 sembol çekiyor, borsapy ~1,8 sn/sembol —
hepsini sormak imkânsız. Kural: **depodaki kapanışla AYNI olan sembol borsapy'ye
SORULMAZ.** Yalnız gerçekten *ezilecek* satırlar sorulur (5 thread, süre bütçesi
900 sn). Endeksler atlanır (borsapy endeks fiyatı referans değil).

**Veto biçimi:** sembol aday listesinden SİLİNMEZ, yalnız **son bar düşürülür**;
önceki günler paketlenmeye devam eder. Sebep: `promote_batch` boş spec'i "fail"
sayıyor ve `max_reject_ratio=0.20` turun tamamını reddettirebilir.

**Yeni gün engellenmez:** depoda o gün hiç yoksa dokunulmaz. Bar olmaması provizyon
bardan kötüdür (kapsama kapısını da düşürür); 21:30/22:15 turları ve sabah
incremental'ı zaten düzeltiyor. Kapı yalnız **EZME** işlemini veto eder.

Kapatma: `SMR_KAPANIS_KONSENSUS=0`.

**🔴 BEYAZ LİSTE TUZAĞI (yakalandı, düzeltildi).** `bist_data_store.PRICE_SOURCES`
bir **beyaz liste**: `{yahoo, yahoo_settled, borsapy_gapfill, repair_yahoo}`.
Listede olmayan bir `price_source` adı "yetkisiz fiyat kaynağı" sayılıp adayı
**bütünüyle** reddettiriyor. 9b'nin ilk hâlinde teyitli adaylara
`price_source="yahoo_borsapy_konsensus"` yazılıyordu → teyit ettiğimiz satırların
hepsi sessizce çöpe giderdi. Etiket `yahoo_settled`/`yahoo` olarak bırakıldı,
konsensüs bilgisi **log'a** yazılıyor. Yeni bir kaynak adı gerekirse
`bist_data_store.py`'nin beyaz listesi de güncellenip **VPS'e gönderilmeli**
(o dosya `deploy.sh` DOSYALAR listesinde YOK).

**Test — VPS'in kendisinde koşuldu** (Py3.10, borsapy 0.10.0): depoyla aynı olan
sembol **sorulmadı** · %0,05 farklı **teyitlendi** · %3 farklı (provizyon taklidi)
**veto** yedi (12→11 satır) · endeks **atlandı** · sembol kaybı yok, geçersiz etiket
yok. 2 sn. Aynı test lokalde de aynı sonucu verdi.

### 9d. CLAUDE HANDOFF — CUMA HACİM KURTARMA (29 AĞU 2026)

**Olay:** 28 Ağu Cuma akşamı İş Yatırım hacim turu 21:30 ve 22:15 TR'de
623 adayın yalnızca 5'ini tamamladı; 618'i timeout/circuit-breaker nedeniyle
başarısız oldu. Fiyat kanalı sağlamdı. Bu nedenle hacim eksikliği veri yokluğu
değil, tek sağlayıcıya bağlılık arızasıydı.

**İstenen sınır:** Geçmiş tarama sonuçları, sinyal getirileri ve Telegram çıktıları
yeniden hesaplanmadı veya değiştirilmedi. Yalnızca ham veri deposunun ileriye dönük
hacim katmanı ve bekçi/otomasyon akışı düzeltildi.

**Yapılan değişiklikler:**

- `finalize_volume.py`: önce İş Yatırım deneniyor; eksik veya başarısızsa yalnızca
  beklenen son işlem günü için doğrudan TradingView sağlayıcısı üzerinden borsapy
  deneniyor. Hedef gün, kapanış yakınlığı ve bariz ölçek anomalisi geçilmeden aday
  kabul edilmiyor; mevcut ölçek kapısı son 20 pozitif hacim medyanının 0,125x–8x
  dışını reddediyor. Bu kapı kaynak doğrulaması değil, bariz ölçek hatası sigortasıdır.
- `bist_data_store.py`: `borsapy` kontrollü yedek olarak tanınıyor; aktif manifestte
  resmî `verified` ile karıştırılmıyor.
- `gorev_bekcisi.py`: alarm etiketi İş Yatırım/borsapy yedeğini gösteriyor,
  bekleme penceresi 23:15 TR'ye taşındı ve manuel kurtarma da aynı finalizer'ı
  kullanıyor.
- `VERI_CEKME_PROTOKOL.md`: İş Yatırım → borsapy/TradingView sıralaması ve
  aynı gün/kapanış doğrulaması belgelendi.
- VPS'e hafta içi her gün 22:50 TR'de, aynı kilitle çalışan otomatik finalizer
  cron'u eklendi. 23:15 TR bekçi kontrolü başarısızlığı alarm olarak bırakıyor;
  sessizce düşük kaliteli veriyi kesinleştirmiyor.

**Canlı Cuma doğrulaması:**

- Aktif sürüm: `v-20260829T195804-954b8b54`.
- 613 adayın 613'ü kabul edildi; 611 kayıt değişti, 0 kayıt reddedildi.
- Kapsama **%98,4** oldu; kaynak turunda 17 İş Yatırım + 596 borsapy sonucu
  kullanıldı, 10 sembol çözümsüz kaldı.
- Cuma fiyat barı bulunan hisselerde 611/611 hacim pozitif ve hedef gün/kapanış
  kapısından geçti; bunların 594'ü borsapy kontrollü yedeği olduğu için resmî
  doğrulanmış sayılmıyor. 12 sembolde Cuma fiyat barı olmadığı için yalnız hacim enjekte edilmedi;
  bu semboller bilerek açık bırakıldı.
- Aktif Cuma kayıtlarının kaynak dağılımı: 16 İş Yatırım, 594 borsapy,
  1 Yahoo provizyonu. Bekçi testi 611/616 = **%99,2** verdi.
- VPS modül derleme, bütünlük, HTTP 200 ve timer kontrolleri geçti. `gorev-bekcisi`
  oneshot servisinin çalışıp kapanması normal; timer aktif ve bekliyor.

**Claude için karar özeti:** Cuma hacim kurtarma tamamlandı. İş Yatırım ana yol
olarak kaldı; borsapy/TradingView yalnız kontrollü yedek. Resmî
Borsa İstanbul DataStore erişimi mümkün hâle gelirse uzun vadeli birincil tarihsel
kaynak olarak ayrıca değerlendirilmeli. Geçmiş sonuçlara dokunulmayacak; düzeltilen
ham hacimler yalnız bundan sonraki analiz ve taramalarda kullanılacak.

**İlgili dosyalar:** `bist_data_store.py`, `finalize_volume.py`,
`gorev_bekcisi.py`, `kapanis_master_otomasyon.py`, `analysis_core.py`, `app.py`,
`smr_core.py`, `deploy.sh`, `VERI_CEKME_PROTOKOL.md`, `VERI_HATTI_DURUM.md`,
`DOSYA_HARITASI.md`.
İlk handoff sırasında git commit'i yapılmamıştı; canlı VPS yedekleri deploy sırasında alındı.

### 9e. BORSAPY HACİM DENETİMİ — HÜKÜM (30 AĞU 2026)

**Denetim tasarımı:** Canlı sürümün borsapy yedeği devreye girmeden hemen önceki
değişmez parquet sürümü kontrol grubu yapıldı; böylece yeni borsapy değerleri
kendi kendini doğrulamadı. 20 likit + sabit tohumla seçilmiş 30 rastgele hisse,
son 120 işlem günü, borsapy + canlı İş Yatırım + parquet üçgeninde karşılaştırıldı.
Toplam 50/50 sembol ve 5.886 pozitif hacim satırı canlı iki-kaynak kıyasına girdi.

**Ölçülen sonuçlar:**

- Borsapy ↔ canlı İş Yatırım: 5.886 satırın 5.330'u (%90,55) ±%1 içinde,
  5.724'ü (%97,25) ±%5 içinde, 5.854'ü (%99,46) ±%10 içinde kaldı.
- 162 satır ±%5'i, 32 satır ±%10'u aştı. 4 sembolde %25 üzeri, 2 sembolde
  %100 üzeri fark görüldü. En büyük fark SKBNK 27.07.2026'da %906,6,
  BRSAN 15.04.2026'da %118,5 oldu.
- Parquet ↔ canlı İş Yatırım kıyası da kusursuz değildi: ±%5 uyum %96,33,
  ±%10 uyum %98,34; en büyük fark SASA'da %4.070 oldu. Önceki kontrol
  manifestinde 628 sembolün 617'si `yahoo_provisional` etiketliydi; bu nedenle
  parquet tek başına altın referans kabul edilemez.
- SASA parquet'inde 12.03–17.06 arasında 50 sıfır hacimli gün bulundu; aynı
  günlerde borsapy ↔ İş Yatırım kıyası en fazla %2,9 fark gösterdi. Bu, borsapy'nin
  bazı bozuk parquet satırlarını yakalayabildiğini gösterir; tek başına her zaman
  doğru olduğunu göstermez.
- Sabit oranlı farklar da görüldü: ULKER'de yaklaşık %5,15, TOASO'da %7,40,
  AFYON'da %10,01 gibi. Bunlar rastgele gürültü değil, kaynakların düzeltme/
  serileştirme kuralının farklı olabileceğini gösteren yapısal farklardır.

**Cuma özel kontrolü:** 28.08.2026 Cuma için mevcut İş Yatırım önbelleği bulunan
50 örnek hissenin 23'ünde aktif sürüm borsapy değeri İş Yatırım ile ±%1 içinde
kaldı; en büyük fark %0,18 oldu. Bu olumlu bir işarettir, fakat 623 hissenin
tamamı için aynı gün bağımsız ikinci kaynak doğrulaması değildir.

**Kesin hüküm:** Borsapy/TradingView hacmi **genel yön ve günlük oran analizinde
iyi bir kontrollü yedek**, fakat **tarihsel hacim için kesin/resmî kaynak değildir**.
Mevcut tarih/kapanış kapısı hacim rakamındaki %100–900 arası uç sapmaları tek
başına yakalayamaz. Bu nedenle:

1. İş Yatırım veya resmî Borsa İstanbul DataStore bulunan günlerde borsapy onun
   yerine geçmez.
2. İş Yatırım cevap vermezse borsapy yalnız güncel gün için aday kalır; eklenen
   ölçek anomali kapısından geçse bile aktif manifestte `controlled_fallback`
   olarak kalır, `verified` sayılmaz ve backtest geçmişine sessizce yazılmaz.
3. Borsapy’nin tarihsel backtest ana kaynağı yapılması reddedildi. Eklenen kapı
   bariz ölçek hatalarını yakalar; kaynaklar arası küçük/yapısal farkları garanti
   etmez.
4. Resmî Borsa İstanbul DataStore erişimi açılırsa son hakem olarak onunla
   yeniden karşılaştırılmalı; Borsa İstanbul tarihsel günlük işlem hacmi ve
   işlem miktarını DataStore üzerinden sunduğunu belirtir.

Denetim çıktısı: `logs/volume_source_audit_20260830.json`. Denetim sırasında
aktif sürüm, eski sinyaller ve Telegram sonuçları değiştirilmedi; yalnızca
okuma amaçlı rapor aracı ve bu handoff notu eklendi. Bundan sonra kaynak kalitesi
aktif manifestte `official` / `controlled_fallback` olarak ayrıca görünür.
