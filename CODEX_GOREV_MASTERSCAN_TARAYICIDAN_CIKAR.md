# CODEX GÖREVİ — Master Scan'i tarayıcıdan çıkar

**Bu belge yazıldı:** 1 Eylül 2026, Salı · 22:15
**Hazırlayan:** Claude (denetçi) · **Yapan:** Codex · **Denetleyen:** Claude
**Kapsam:** Aşama A + B + C. **Aşama D (anahtarı çevirme) BU GÖREVDE YOK** —
kanıt gelmeden geçilmez.

---

## ⏱ BAŞLAMADAN ÖNCE

```bash
date +"%Y-%m-%d %H:%M %A"
git log --oneline -5
git status --short
```

⚠️ **1 Eylül akşamı `app.py` ve `analysis_core.py`'de senin commit'lenmemiş işin
duruyordu** (137 ekleme / 65 silme, GENEL ÖZET paneli). Bu göreve başlamadan
önce onu bitir ve commit et. İki iş aynı dosyada açık kalırsa ikisi de kaybolur.

**Satır numaraları kayar.** Aşağıdaki her numarayı kullanmadan önce doğrula:
```bash
grep -nE "^def _ms_execute_pending_phase2|^def _ms_run_phase2_step|^_MS_PHASE1_STEPS" app.py
```

---

## A. HİKÂYE — bu iş neden var

Bunu okumadan koda dokunma. Yapılacak işin şekli bu hikâyeden çıkıyor.

### Olan biten

Master Scan, **bir ekran uygulamasının içinde** yaşıyor. Binlerce hisseyi tarayan
toplu bir iş, ancak birileri tarayıcıda sayfayı açık tutarsa çalışabiliyor.

Bu yüzden etrafına bir aygıt yığını kuruldu — hepsi **"ekran başında oturan bir
insan" taklidi** yapmak için:

| Parça | Gerçekte ne yapıyor |
|---|---|
| `master_scan_headless_session.ps1` + görünmez Brave | sahte kullanıcı |
| `SMR_MasterScan_AutoStart` (19:55) | sahte kullanıcıyı uyandırır |
| `logs/kapanis_master_scan_completion.json` | "sahte kullanıcı bitirdi mi?" |
| `logs/kapanis_master_scan_running.json` | "başka bir sahte kullanıcı zaten yapıyor mu?" |
| `SMR_MasterScan_Watchdog` (22:15) | "sahte kullanıcı iş çıkardı mı?" |
| `gorev_bekcisi.master_scan_denetle` (1 Eyl) | "sahte kullanıcı hiç geldi mi?" |

**Bu altı parçanın hiçbiri, tarama düz bir betik olsaydı var olmazdı.**

### İki kere ısırdı

**27 Ağustos:** gün içinde elle koşulan bir tur, tamamlanma dosyasına bugünün
tarihiyle `partial` damgası bıraktı. Akşam betiği onu gördü, "iş yapılmış" deyip
tarayıcıyı öldürdü. Tarama hiç koşmadı, görev sonuç kodu **0** döndü. Tuzak
bulundu, `memory/project_master_scan_otomasyon_tuzagi.md` yazıldı.

**28 Ağustos:** tuzak "kapatıldı" — ama **yalnız PowerShell betiğinde**. Aynı
kural uygulamanın kendi kapısına (`kapanis_master_otomasyon.is_scan_completed_today`)
konmadı. Notun sonuna "artık elle tarama koşabilirsin" yazıldı. **O cümle yanlıştı.**

**1 Eylül:** 15:00'te elle koşulan bir doğrulama turu yine `partial` bıraktı.
19:55 görevi koştu, tarayıcı 20:00'de açıldı, sayfa çizildi, **20:00–20:54 arası
565 saniye işlemci yandı ve veritabanına tek satır yazılmadı.** 20:54'te bellek
sıkıştı (kullanılabilir 0,58 GB), Brave görünmez sekmeyi attı. Tarama 21:21'de
elle başlatılana kadar hiç koşmadı. Kimse fark etmedi.

Aynı gece iki yama daha yazıldı (commit `83d4f73`): app tarafı kapı düzeltildi,
nöbetçiye "akşam taraması koştu mu" kapısı eklendi. **İkisi de doğru ama ikisi de
itfaiye.** Yangının çıktığı yer duruyor.

### Kullanıcının hükmü

> *"Neden basit bir çalı yangını için itfaiye istasyonu kuruyoruz?"*

Haklı. Bu görev itfaiyeyi büyütmüyor — **yangının kaynağını kaldırıyor.**

### Yol zaten yarıya kadar döşenmiş

`master_scan_vps_shadow.py` — **13 Ağustos 2026**, 152 satır. Kendi açıklaması:

> *"VPS Master Scan geçişinin ilk, yazmayan gölge aşaması... Gerçek tarama motoru
> ancak aynı veriyle lokal sonuçlar birkaç gün birebir karşılaştırıldıktan sonra
> buraya bağlanacaktır."*

19 gündür orada duruyor. Bu görev o cümlenin devamı.

---

## B. MEVCUT YAPI — ölçülmüş gerçekler

**İyi haber: iş sandığından temiz.** Faz 2 zaten sözlük-tabanlı bir bağlamla
çalışan adım fonksiyonlarına ayrılmış durumda.

### Taramanın iskeleti

| Parça | Yer (≈) | Not |
|---|---|---|
| `_MS_PHASE1_STEPS` | app.py 3066 | 13 adım listesi |
| Faz 1 gövdesi | app.py 15655–16090 | ~436 satır |
| `_ms_phase2_steps` | app.py 16060 | 9 adım listesi |
| `_ms_faz2_baglam` | app.py 16064 | **Faz 2'nin tüm girdisi tek sözlükte** |
| `_ms_execute_pending_phase2(ctx, progress, bar)` | app.py 3502 | Faz 2 döngüsü, ~19 satır |
| `_ms_run_phase2_step(step, ctx)` | app.py 3158 | tek adım |
| `_ms_finalize_master_scan(ctx, progress, bar, holiday_ph)` | app.py 3347 | kapanış işleri |
| `master_scan_progress.MasterScanProgress` | ayrı modül, 118 satır | **zaten modül** |

### Ekran bağımlılığı — ölçüldü

Faz 1 gövdesindeki **52 `st.` çağrısının 39'u `st.session_state`** — yani düz bir
sözlükle karşılanır. Gerçek ekran çağrısı yalnız 13 tane:

```
39  st.session_state   → düz sözlük
 3  st.toast           → log satırı
 2  st.warning         → log satırı
 2  st.error           → log satırı + hata
 2  st.empty           → no-op
 1  st.progress        → log satırı
 1  st.stop            → istisna/return
 1  st.button          → ekransızda YOK
 1  st.cache_data      → dokunma (dekoratör)
```

### Ekransız yükleme zaten çözülmüş

`golden_record.py` app.py'nin tanımlarını **ekran hiç açmadan** yüklüyor
(`_install_stubs`, satır ~92). Her oturumda koşuyor ve çalışıyor. **Köprü var,
yeniden icat etme** — ama aynen kopyalama, farkı C bölümünde yazılı.

### Kıyas zemini

`patron.db` → `scan_runs` tablosu: `scan_date, scan_type, row_count, category,
recorded_at`. Sağlıklı bir akşam turu:

```
31 Ağu : 57 tarama · 1662 sinyal
 1 Eyl : 57 tarama · 1578 sinyal
```

Adım süreleri: `master_scan_timing_profile.json` (⚠ `MAX_SAMPLES = 8` — liste
uzunluğuna bakarak "kaç tur koştu" çıkarma, kırpılıyor).

---

## C. YAPILACAK İŞ

### Aşama A — Şefi ekrandan ayır (davranış DEĞİŞMEZ)

Yeni modül: **`master_scan_engine.py`**.

Taşınacak: Faz 1 gövdesi + `_ms_execute_pending_phase2` + `_ms_run_phase2_step`
+ `_ms_finalize_master_scan`. Yani **sırayı yöneten şef**. Hesap fonksiyonlarına
(scanners, scan_pipeline, scoring_core…) **dokunma** — onlar zaten modülde.

Modül ekranı tanımaz. Dışarıdan iki şey alır:

1. **`durum`** — `st.session_state` yerine geçen sözlük benzeri nesne.
2. **`bildir(seviye, metin)`** — `st.toast/warning/error/progress` yerine tek
   fonksiyon. Streamlit tarafında ekrana yazar, ekransız tarafında log'a.

`app.py` bu modülü **import edip çağırır**; kendi gövdesinde tarama kodu kalmaz.
Streamlit tarafında `durum = st.session_state`, `bildir = ekrana yazan sarmalayıcı`.

**Bu aşamada tek bir hesap satırı bile değişmemeli.** Aşama A'nın tek amacı yeri
değiştirmek, davranışı değil.

**Kapı:** `python golden_record.py` → **sıfır fark**. Vermezse taşıma bozuktur.

### Aşama B — Ekransız koşucu

Yeni betik: **`master_scan_kos.py`**.

```
python master_scan_kos.py --kategori "BIST 500 " [--kuru]
```

Yaptığı: sahte Streamlit kurar (golden'ın yolu), `master_scan_engine`'i çağırır,
`durum` olarak düz sözlük, `bildir` olarak log yazıcı verir. Faz 1 ve Faz 2'yi
**arka arkaya, tek süreçte** koşturur — ekransızda faz bölmesinin sebebi yok
(bölme, kullanıcı sonuçları erken görsün diyeydi).

Bitince: aynı kapanış işleri (frontend JSON, VPS senkronu, günlük karne,
`mark_scan_completed`, `release_scan_start`). **Hiçbirini atlama.**

Çıkış kodu: başarı 0, kısmi 1, çökme 2. Log: `logs/master_scan_kos.log`.
**Sessizce ölmesi yasak** — bu görevin varlık sebebi o.

`--kuru`: her şeyi koşar ama patron.db'ye YAZMAZ, tamamlanma dosyasına
DOKUNMAZ. Kıyas için gerekli (aşağıda).

### Aşama C — Kıyas aracı

Yeni betik: **`master_scan_kiyas.py`**.

İki turu karşılaştırır. Ölçüt sırayla:

1. **Tarama tipi kümesi** — aynı 57 tip var mı? Eksik/fazla tip = **KALDI**.
2. **Satır sayıları** — her tip için `row_count` birebir mi?
3. **Sembol kümeleri** — `scan_signals`'ta her tipin sembolleri aynı mı?
4. **Sayısal alanlar** — `score`, `entry_price`, `stop_level` farkı.

Rapor: `logs/master_scan_kiyas_<tarih>.md` + JSON.
**Geçme ölçütü: 1-3 birebir aynı; 4'te tolerans sıfır.**
Fark varsa nerede olduğunu tip+sembol düzeyinde yazsın — "farklı" demek yetmez.

⚠ **Vade sonradan seçilmez:** ölçütü şimdi yaz, kıyas sonucunu görünce
gevşetme. Fark çıkarsa sebebi bulunur, eşik indirilmez.

---

## C-EK. KÜÇÜK AYRI İŞ — "Magic Ribbon -4S" sekmesi neden boş olduğunu söylesin

**Bu, yukarıdaki üç aşamadan bağımsız. Farklı dosya, çakışma yok. Önce bunu
yapabilirsin — kısa.**

### Durum (1 Eyl 2026 akşamı ölçüldü — arıza DEĞİL)

Kullanıcı "Magic Ribbon sıfır sonuç çıkarmış" dedi. Çıkarmamış:

```
magic_ribbon_session_log · 1 Eyl : 13 satır
  ├─ 10 satır  bar_time 31.08.2026 18:10   (sabah 09:19 testinden, o saatte doğru)
  └─  3 satır  bar_time 01.09.2026 18:10   (akşam turu — ENKAI, ISMEN, ODAS,
                                            üçü de YENİ HİZALANMA, tetik yaşı 0)
```

Motor çalışıyor, adayları buluyor, veritabanına yazıyor. **Ekrana boş tablo
gitmesinin sebebi bilinçli bir karar:**

`app.py` ~15838:
```
st.session_state.magic_ribbon_session_data = (
    _mr_df if MAGIC_RIBBON_BIST_SESSION_RENDER_ENABLED else pd.DataFrame()
)
```

`magic_ribbon_core.py` ~46'da bayrak **False** ve gerekçesi hemen üstünde yazılı:
31 Ağu ilk ölçüm gürültü; 1 Eyl delik-farkında ölçüm artıya döndü ama filtre
tabanı da oynatıyor, Temmuz'un yalnız %34,6'sı kalıyor (takvim yanlılığı),
t = 1,33. Hüküm: *"Ayrım kanıtı yokken aday listesi ekrana çıkmaz; ham sinyaller
ileri test için kaydedilmeye devam eder. İkinci rejimde yeniden ölçülmeden
True yapılmayacak."*

Bu doğru bir karar ve [[feedback-rozet-olcumden-gecer]] kuralının ta kendisi.

⚠ Ayrıca: eski 4 saatlik Magic Ribbon **emekli oldu**, yerini bu seans-mumu
sürümü aldı. `magic_ribbon_log` tablosunun 31 Ağu'da donmuş olması normaldir,
geçmişi için duruyor. Onu "çalışmıyor" sanıp diriltmeye kalkma.

### Asıl kusur

**Ekran neden boş olduğunu söylemiyor.** Kullanıcı üç ihtimali ayırt edemiyor:

1. "bugün aday bulamadı"
2. "bozuk"
3. "ölçümde, bilerek kapalı"  ← gerçek olan bu

Bu, 1 Eylül'de Katalog'dan kaldırdığımız 4 ölü satırın **aynı sınıfı**: sistem
bir şeyi bilerek göstermiyor ama bunu kullanıcıya söylemiyor.

### Yapılacak

`trajectory_tarama_merkezi.py` (~1732, `session_getter("magic_ribbon_session_data")`).

Bayrak **False** iken sekme boş tablo yerine şunu göstersin:
- Açık bir ibare: **"ölçümde — aday listesi henüz ekrana çıkmıyor"**
- Tek cümle gerekçe: ayrım kanıtı ikinci rejimde doğrulanmadı
- Bugün kaç ham sinyal kaydedildiği (ileri test sürüyor, boşuna koşmuyor)

Bayrak True olduğunda hiçbir şey değişmemeli — normal tablo çıksın.

**Yasak:** bayrağı True yapma. Ölçüm kararı senin değil.
**Yasak:** sayıyı ekrana yazmak için yeni bir sorgu yolu icat etme; tek kaynak
`magic_ribbon_session_log`.

---

## D. TUZAKLAR — bunlar seni ısıracak

1. **`st.session_state` düz sözlük OLMALI, golden'ın falsy stub'ı DEĞİL.**
   golden bilinmeyen her anahtara sahte nesne döndürür; tarama gerçek okuma-yazma
   yapar. Stub'ı yükleme için kullan, çalıştırma için düz `dict` ver.

2. **`st.rerun()` ekransızda anlamsız.** Faz 2 döngüsünün sonunda var. Ekransız
   yolda `return` olmalı; istisna fırlatmamalı.

3. **`st.stop()`** Faz 1'de kesinti penceresi için kullanılıyor. Ekransızda
   kesinti penceresi YOK — o dal hiç çalışmamalı.

4. **`@st.cache_data` dekoratörlü fonksiyonlar.** golden bunlarla başa çıkıyor;
   aynı yolu izle. Dekoratörü kaldırmaya kalkma — Streamlit tarafını bozar.

5. **Bayat veri yazım kapısı (`_bayat_yazim_kapisi`) BYPASS EDİLMEZ.** Ekransız
   koşucu kapanış sonrası koşar, sorun çıkmaz. Ama "test ederken engel oluyor"
   diye kapıyı gevşetme — 26-31 Ağustos hacim arızasının sebebi tam buydu.

6. **Tarama sırasını değiştirme.** Kıyas buna dayanıyor. "Daha mantıklı olur"
   diye adım taşıma yok.

7. **`mark_scan_completed` + `release_scan_start` çağrılmaya devam etmeli.**
   Nöbetçi ve 22:15 watchdog bunlara bakıyor.

8. **`patron.db` yazma kilidi.** 21:00'de `backtest_runner.py`, 22:50'de
   finalizer yazıyor. Ekransız koşucu bunlarla çakışabilir; SQLite kilit hatasında
   çökmek yerine bekleyip yeniden denemeli.

9. **İki ajan.** Claude 1 Eylül gecesi `kapanis_master_otomasyon.py` ve
   `gorev_bekcisi.py` dosyalarına dokundu (commit `83d4f73`, VPS'e gitti).
   Bu görevde o iki dosyaya **dokunma**.

10. **Seans içinde tam tur koşturma.** Gün içi tur artık akşamı bozmuyor
    (`83d4f73`) ama seans içi veri yarımdır, kıyas için işe yaramaz.

---

## E. BİTİRME KONTROL LİSTESİ

- [ ] Aşama A sonrası `python golden_record.py` → **sıfır fark**
- [ ] `python -c "import ast; ast.parse(open('app.py',encoding='utf-8').read())"`
- [ ] Streamlit TAM restart → ekrandan elle Master Scan → eskisi gibi çalışıyor
- [ ] `master_scan_kos.py --kuru` çökmeden bitiyor, log okunabilir
- [ ] `master_scan_kos.py` gerçek tur → `scan_runs`'a ~57 tip yazıyor
- [ ] `master_scan_kiyas.py` çalışıyor, fark olmadığında "AYNI" diyor
- [ ] Çökme testi: koşucuyu ortadan öldür → çıkış kodu 0 DÖNMÜYOR, log'da iz var
- [ ] En az **3 işlem günü** paralel koşu + kıyas raporu
- [ ] Aşama D'ye GEÇME — kanıtı Claude'a getir
- [ ] C-EK: bayrak False iken sekme "ölçümde" diyor, True iken normal tablo çıkıyor

---

## F. YAPMA

- ❌ **Zamanlayıcıyı değiştirme.** `SMR_MasterScan_AutoStart` ve görünmez tarayıcı
  zinciri bu görevde **olduğu gibi kalır**. Kanıt gelmeden anahtar çevrilmez.
- ❌ Hesap fonksiyonlarını (scanners / scan_pipeline / scoring_core / ict_core)
  "nasılsa taşıyorum" diye elden geçirme. Bu görev **yer değiştirme**, iyileştirme değil.
- ❌ Faz bölmesini ekran tarafından kaldırma. Ekranda kalıyor — kullanıcı sonuçları
  erken görmek istiyor. Bölmesizlik yalnız ekransız yolda.
- ❌ `kapanis_master_otomasyon.py` ve `gorev_bekcisi.py`'ye dokunma.
- ❌ Kıyas ölçütünü sonuca bakarak gevşetme.
- ❌ Sessiz `except: pass` yazma. Bu görevin tamamı **sessiz arızaya** karşı.

---

## G. NEDEN BU SIRA

Aşama A tek başına riskli görünür ama en güvenlisi: eski yol hâlâ ayakta,
golden hakem, bir şey bozulursa hemen görülür. Aşama B eski yola dokunmadan
yeni bir kapı açar. Aşama C hakemi kurar. **Anahtar en sona kalır, çünkü bu
projede en pahalı hata "sessizce farklı sonuç üretmek".**

İlgili notlar: `memory/project_master_scan_otomasyon_tuzagi.md` ·
`memory/feedback_teshisi_kaynaktan_dogrula.md` ·
`memory/feedback_yeni_kod_ayri_modul.md` · `AJAN_KURALLARI.md`
