# DEVİR NOTU + İŞ EMRİ — MASTER SCAN REFORMU
**Yazan:** Claude (denetçi) · **Tarih:** 26 Ağustos 2026 gecesi
**Alıcı:** Codex · **Kullanıcı:** uykuda olabilir, sabah dönecek

> Bu dosya iki bölümdür.
> **BÖLÜM A = devir.** Şimdi yapılacak küçük ve kapalı iş.
> **BÖLÜM B = asıl iş.** Kullanıcının İKİNCİ EMRİ gelmeden BAŞLAMA.

---

# BÖLÜM A — DEVİR NOTU (önce bunu oku)

## A1. Bugün ne oldu

Kullanıcı Master Scan taramalarından memnun değildi. Üç ajan sırayla görüş verdi:

1. **Codex** kodu, canlı veritabanını ve backtestleri okudu → SWOT + kadro önerisi
2. **Antigravity** onun üzerine 3 masalı mimari önerdi
3. **Claude** iki raporun sayılarını `patron.db` üzerinde tek tek saydı → denetim hükmü çıkardı

Sonra ikinci tur oldu: Codex hükmü körü körüne onaylamadı, bağımsız yeniden saydı, **bir hatayı yakaladı**. Claude o itirazı sınadı, **kendi hükmündeki iki maddeyi geri çekti**, uygulama alanını taradı ve **üçüncü bir hata** buldu.

Sonuç: BÖLÜM B'deki revize hüküm. Üç ajan mutabık.

## A2. Ağacın şu anki hali — ÖNCE BUNU BİL

- **Commit'lenmemiş çok iş var.** Ağaçta çok sayıda takip edilmeyen dosya duruyor. Bu klasörde daha önce bir kez dal değişimi yüzünden beş günlük iş kıl payı kurtarıldı. Bir şeyi ezmeden önce sahibini belirle.
- **`Tarama Merkezi + Toplu Terazi` yarım:** 5 aşamanın 4'ü lokalde bitmiş, **VPS'e gitmemiş**. Kalan iş = eski toplu render bloklarını kaldırmak. Bayrağı: `TARAMA_MERKEZI_V2`.
- **`evidence.py` canlıda yanlış etiket taşıyor** (aşağıda A4).

## A3. Uyulacak kurallar (değişmedi)

- `AJAN_KURALLARI.md` — oturum başında oku
- İki ajan protokolü: **oku-sonra-düzenle** · **3 kapı** (golden_record + lokal ast.parse + VPS py_compile) · **ezmeden önce VPS↔lokal diff** · `git pull` YAPMA (deploy scp)
- Hesap değiştiyse `python golden_record.py` yeşil olmadan deploy yok
- Yeni hesap kodu `app.py`'ye yazılmaz → ayrı modül

## A4. DEVİR İŞİ — tek dosya, kapalı alan: `evidence.py`

Bu iş bilerek küçük seçildi: **tek dosya, çakışmasız, bir oturumda biter.**

### İş 1 — Tavan rozetini düşür (canlıda yanlış duruyor)

`evidence.py` şu an Tavan ailesine **"🟢 İKİ REJİMDE POZİTİF"** rozeti veriyor:
- `GUCLU_SCANNERS` = `{tavan_top30, tavan_alarm, zirve_devam, zirve_sikisma}`
- `ALFA_T20` = `tavan_top30: 2.6` · `tavan_alarm: 2.5`

**Ölçüm bunu çürüttü** (26 Ağu; ertesi açılış + tekrar sayım temiz + SMA50 rejimi):

| Tarama | T+20 yükselen | T+20 düşen |
|---|---|---|
| tavan_top30 | +%3,14 | **−%2,27** |
| tavan_alarm | +%5,82 | **−%1,80** |

Dört ayrı rejim tanımında da denendi — **hiçbirinde iki rejimde pozitif değil**.

→ `tavan_top30` ve `tavan_alarm` `GUCLU_SCANNERS`'tan çıkar, `TEK_REJIM_SCANNERS`'a geçer. `ALFA_T20` değerleri rejim ayrımı taşımadığı için yanıltıyor; ya rejim kırılımlı hale getir ya da o iki satırı kaldır.

⚠ `zirve_devam` ve `zirve_sikisma` bu turda ölçülmedi (örneklem küçük, N<100). **Dokunma.**

### İş 2 — Altı taramayı karar yüzeyinden çek

| Tarama | Kodda şu an | Yapılacak |
|---|---|---|
| `tekli_altin` | `ZAYIF_SCANNERS` | ✅ zaten çekilmiş, iş yok |
| `radar2` · `altin_setup` | `TEK_REJIM_SCANNERS` | ➕ susturulacak |
| `radar1` · `platin_setup` | **hiçbir listede yok** | ➕ susturulacak |
| `guclu_donus` | 17 Ağu'da elemeden geri alınmış | ➕ susturulacak (gerekçe B1c) |

### ⚠️ İş 3 — HANGİ DÜĞMEYİ KULLANACAĞINI KARIŞTIRMA

`evidence.py`'de iki ayrı mekanizma var:

- `ELENEN_TARAMALAR` / `elendi_mi()` → **SERT eleme**: hiç çalıştırma, hiç ölç, hiç göster
- `is_ai_suppressed()` (`DAHA_ZAYIF_SCANNERS` + `ZAYIF_SCANNERS`) → **YUMUŞAK susturma**: karar yüzeyinden çek, tarama ve `scan_signals` yazımı sürsün

**Kullanılacak olan: YUMUŞAK.** Karar "sustur, silme, ölçüm sürsün" idi.

Sert listeye eklersen ölçümü de öldürürsün — `evidence.py`'nin kendi notu bunu itiraf ediyor:

> *"elenenler artık ölçülmüyor da; 'acaba düzeldi mi' sorusu cevapsız kalır."*

Rejim değiştiğinde bu altısını yeniden ölçebilmemiz lazım. Sert liste o kapıyı kapatır.

### İş 4 — Doğrula ve dur

1. `python golden_record.py` → yeşil olmalı (bu değişiklik hesap değiştirmiyor, sıfır fark bekleniyor)
2. `python -c "import ast; ast.parse(open('evidence.py',encoding='utf-8').read())"`
3. **VPS'e DEPLOY ETME.** Bu turda deploy yok.
4. Yaptığını tek paragrafta yaz.

## A5. 🛑 DUR

Bölüm A bitince **DUR ve kullanıcının İKİNCİ EMRİNİ BEKLE.**

Bölüm B'ye kendiliğinden geçme. Orada çakışma alanı var (A2 + B5); yarım bırakılırsa iki ajan aynı satırları ters yönde çeker.

---

# BÖLÜM B — ASIL İŞ
# MASTER SCAN REFORMU — İKİNCİ TUR DENETİM HÜKMÜ (REVİZE)

**26 Ağustos 2026 · Claude · Codex ve Antigravity yanıtları + kod tabanı incelemesi sonrası**
*Bu metin 1. tur hükmünün yerini alır. Ölçüm sırasında hiçbir dosyaya dokunulmadı.*

## B0. Bu turda ne değişti

Codex'in iki itirazı sınandı: **biri Claude'u haklı, biri haksız çıkardı.** Ardından uygulama alanı tarandı ve **D1 listesinde ikinci bir hata** bulundu, o da ölçülüp kapatıldı.

---

## B0b. BÖLÜM A SONUCU — yapıldı ve denetlendi (26 Ağu gecesi)

Codex Bölüm A'yı uyguladı, Claude diff'i denetledi. **Sonuç: temiz.**

| Kontrol | Sonuç |
|---|---|
| Altı tarama yumuşak susturuldu | ✅ `is_ai_suppressed=True`, `elendi_mi=False` |
| Tavan rozeti düştü | ✅ 🟢 → 🟡, `ALFA_T20` değerleri kalktı |
| `zirve_devam` / `zirve_sikisma` | ✅ dokunulmadı |
| `ELENEN_KLASIK` | ✅ değişmedi (5 tarama) |
| `golden_record.py` | ✅ "BİREBİR AYNI", çıkış kodu 0 |

Codex talimatın ötesinde bir düzeltme daha yaptı ve **doğru yaptı**: `radar2`, `altin_setup`, `guclu_donus` `TEK_REJIM_SCANNERS`'tan da çıkarıldı — yoksa aynı tarama iki kümede durur, etiket sırasına bağlı belirsizlik kalırdı.

### ⚠️ AÇIK: D1'in ekran yarısı teslim edilmedi

Hükümde "karar **yüzeyinden** çekilir" yazıyordu ve mekanizma olarak `is_ai_suppressed` gösterilmişti. Kod tarandı: o fonksiyon yalnız **iki yerde** okunuyor — AI metnini süzen yer ve tek bir Erken Radar rozetinin yazısı.

**Ekran panelleri onu hiç sormuyor.** Radar 1'in ~597 ismi, Altın, Platin, Tekli Altın ve Güçlü Dönüş listeleri sağ sütunda **görünmeye devam ediyor**.

Bu kod tabanında ekrandan çıkarmanın tek mevcut yolu sert eleme (tarama anında tablonun boşaltılması) — o da ölçümü öldürüyor. Yani **"ekrandan çık ama ölçüm sürsün" diyen bir mekanizma sistemde YOK.** Talimat bunu var sayarak yazılmıştı; kusur talimatta, uygulamada değil.

**KARAR (kullanıcı, 26 Ağu):** Ekran yarısı **şimdi yapılmayacak**, İş 6/7'de tamamlanacak. Gerekçe: yeni Tarama Merkezi zaten karar yüzeyi olacak, havuzuna bu taramaları almıyor ve eski paneller o iş sırasında kaldırılacak. Aynı işi iki kez yapmaya gerek yok.

**Ara dönemde beklenen davranış:** AI bu altı taramayı duymaz, ekran gösterir. Bu bilinen ve kabul edilmiş bir ara durumdur — hata değildir.

### Küçük not (acil değil)

Tavan çıkınca 🟢 "İKİ REJİMDE POZİTİF" rozeti yalnız `zirve_devam` ve `zirve_sikisma`'da kaldı — ve bu ikisi bu turda **ölçülmedi** (örneklem <100). Sistemin en güçlü rozeti şu an doğrulanmamış iki taramaya yaslanıyor. Bir sonraki ölçüm turunda sıraya alınmalı.

---

## B1. GERİ ÇEKİLENLER VE DÜZELTİLENLER

### B1a. Yuvarlama kusuru
Güçlü Dönüş → Radar 1 örtüşmesi %100 yazılmıştı; gerçek değer **%99,7**.

### B1b. Tavan'ın 🟢 rozeti düştü (Codex haklı)
Ayrıntı B2b'de. `evidence.py` düzeltmesi Bölüm A'da.

### B1c. Güçlü Dönüş: önce çıkarıldı, sonra ölçülüp GERİ KONDU

İlk hükümde Güçlü Dönüş D1'e konmuştu. Sonra `evidence.py`'de projenin kendi notu bulundu:

> *"Güçlü Dönüş **geri alındı** (17 Ağustos, aynı gün): korunanlarla aynı tarih aralığına (18 Haz–17 Tem) kısılınca alfası **−%2,27'den +%0,34'e** dönüyor. Elenmesi veriyle desteklenmiyordu; ilk ölçümdeki −%2,27 **dönem etkisi** taşıyordu."*

Bu, Codex'in örtüşme sayısında yakaladığı **pencere tuzağının aynısıydı** — bu kez hatayı Claude yapmıştı. Karar askıya alındı ve ortak-pencere ölçümü koşuldu.

**Sonuç: 17 Ağustos'un geri alma gerekçesi ayakta kalmıyor.**

Aynı pencere (18 Haz–17 Tem), işlem yapılabilir cetvelle (ertesi açılış), tekrar sayım temizlenmiş, SMA50 rejimi, T+20:

| | Alfa | N |
|---|---|---|
| Güçlü Dönüş | **−%1,08** | 279 |
| **Aynı günlerde korunan taramalar** | **+%2,87** | 557 |
| Fark | **−3,95 puan** | |

"Dönem kötüydü" denmişti. Ama **aynı günlerde** korunan taramalar +%2,87 yapmış. Dönem kötü değildi; Güçlü Dönüş katılmadı.

Bugüne uzatınca aynı yön: Güçlü Dönüş −%1,51 · korunanlar +%0,86 · fark **−2,37**.
Kısa vade de aynı: T+5'te **iki rejimde de eksi** (−%0,03 yükselen / −%1,85 düşen).

**⚠ Şerh:** Buradaki −%1,08 ile 17 Ağustos'un +%0,34'ü aynı pencerede farklı çıkıyor; sebep büyük olasılıkla giriş cetveli (o ölçüm kapanış, bu ölçüm ertesi açılış). **Bu taramanın kaderi iki kez ters döndü** — örneklem kırılgan. Codex kendi yöntemiyle bir kez daha üretsin; üçüncü kez dönmesin.

**Hüküm: Güçlü Dönüş D1'e geri girdi. D1 = 6 tarama.**

---

## B2. CODEX'İN İTİRAZLARI

### B2a. Örtüşme sayısı — ikisi de doğru, ilke önemli

| Pencere | Güçlü Dönüş N | Tekli Altın'la örtüşme |
|---|---|---|
| Tüm kayıt (29 Nisan'dan) | 2.090 | **%37,1** ← Codex |
| Tekli Altın'ın doğduğu günden (23 Haz) | 1.228 | **%63,1** ← Claude |

Güçlü Dönüş 67 gün yaşıyor, Tekli Altın 42 gün. Güçlü Dönüş'ün **862 sinyali, Tekli Altın'ın hiç var olmadığı günlere** düşüyor.

"Birbirini tekrar ediyorlar mı?" sorusunun cevabı **ortak penceredir**. Ama itirazın asıl değeri sayıda değil ilkede — ve o ilke B1c'de Claude'u da vurdu.

**Radar 1 ailesi (ortak pencere, 25 Haz+):**

| Tarama | Radar 1'in içinde |
|---|---|
| Radar 2 | %100,0 |
| Platin | %100,0 |
| Güçlü Dönüş | %99,7 |
| Tekli Altın | %98,9 |
| Altın | %98,7 |

Ek: Platin'in %70,4'ü Altın'ın içinde.
**Radar 1 günde 597 hisse işaretliyor, o gün taranan toplam 602.** Seçicilik yok.

### B2b. Tavan rozeti — CODEX HAKLI

**Tavan Top30, T+20, rejim tanımına duyarlılık (kapanış cetveli):**

| Rejim tanımı | Yükselen | Düşen | Hüküm |
|---|---|---|---|
| 20 günlük değişim (mevcut araç) | +%3,22 | −%1,91 | tek rejim |
| XU100 > 50 günlük ortalama | +%2,51 | −%0,08 | tek rejim |
| 10 günlük değişim | +%3,55 | −%0,22 | tek rejim |
| 60 günlük değişim | +%2,74 | −%3,10 | tek rejim |

**Hiçbir tanımda iki rejimde de pozitif değil.** Rozet düştü.

### B2c. Giriş cetveli — 4 sorunun cevabı

Kayıtlı giriş fiyatı **sinyal gününün kapanışı** (AKBNK 18 Haz: kayıt 81,10 = o gün kapanış 81,10). Endeks de aynı gün kapanışından ölçülüyor → **hisse ve endeks aynı terazide** (soru 1 ve 2: evet).

Ama bu teraz **işlem yapılabilir değil**: Master Scan 19:55'te koşuyor, o kapanıştan alınamaz. Eski alfa rakamlarının hepsi iyimser.

**Ek uyarı — tavan kilidi:** Yeniden ölçüm ertesi açılışla yapıldı ama **ham açılışla**. `signal_policy.resolve_next_open_entry` daha dürüst: tavan kilidini uyguluyor, kilitli seansları en fazla 3 gün atlıyor. **Tavan ailesinin aşağıdaki rakamları hâlâ iyimser.**

---

## B3. ÖLÇÜM — ertesi açılış + tekrar sayım temiz

### T+5 (kısa masa)

| Tarama | Yükselen | Düşen | N (yük/düş) | Hüküm |
|---|---|---|---|---|
| **Erken Radar C6** | +%0,51 | +%0,46 | 29 / 91 | **iki rejimde de artı** |
| Tavan Alarm | +%1,10 | −%0,80 | 121 / 165 | tek rejim |
| Erken Radar B11 | +%0,74 | −%0,06 | 75 / 171 | tek rejim |
| Pre-Launch BOS | +%0,10 | −%0,14 | 81 / 82 | tek rejim |
| Minervini | −%0,05 | −%0,94 | 22 / 58 | örnek zayıf |
| Güçlü Dönüş | −%0,03 | −%1,85 | 186 / 457 | iki rejimde de eksi |
| Tavan Top30 | −%0,11 | −%1,22 | 366 / 457 | iki rejimde de eksi |

### T+20 (sabır masası)

| Tarama | Yükselen | Düşen | N (yük/düş) | Hüküm |
|---|---|---|---|---|
| **Pre-Launch BOS** | +%1,95 | +%1,92 | 66 / 47 | **iki rejimde de artı** |
| Tavan Alarm | +%5,82 | −%1,80 | 90 / 98 | tek rejim |
| Tavan Top30 | +%3,14 | −%2,27 | 301 / 245 | tek rejim |
| Erken Radar B11 | +%11,07 | −%1,24 | 56 / 165 | tek rejim |
| Erken Radar C6 | +%4,29 | −%1,44 | 21 / 89 | örnek zayıf |
| Güçlü Dönüş | −%1,20 | −%1,62 | 105 / 292 | iki rejimde de eksi |

### Doğrulanan sayımlar
- `scan_signals` 108.184 satır · `signal_returns` 617.049 satır
- **Getiri satırlarının tamamı olay-başlangıcı kayıtlarına bağlı** → ölçüm hattı ardışık günleri saymıyor
- Radar 1: ham 36.161 · **gerçek olay 1.246** · günde 597/602 hisse

---

## B4. BU TURUN ÜÇ BULGUSU

**B4a. C6 her elekten geçen tek tarama.** Kapanış cetveli, açılış cetveli, tekrar temizliği, iki rejim — hepsinde artı. Ama üstünlüğü işlem yapılabilir cetvelde **+%0,71'den +%0,51'e** düştü ve yükselen rejim örneği **29 olay**. D4'ün 300 olay kapısı bu yüzden gerekli.

**B4b. Pre-Launch BOS'un yeri yanlış — üç ajan da yanlış koymuştu.** Codex "ikincil 4–5 günlük", Antigravity "1–5 gün destek" demişti, Claude itiraz etmemişti. Ölçüm üçünü de reddediyor: **5 günde sıfır, 20 günde iki rejimde de artı** (+%1,95 / +%1,92). Örneklem küçük (66/47) → rozet yok, **sabır masasının birinci adayı**.

**B4c. Erken Radar B11'in iki-rejim görüntüsü tekrar sayımdan geliyordu.** Temizlemeden önce +%11,96 / +%2,01; temizleyince **+%11,07 / −%1,24**. B11 orta vade çekirdeği olamaz.

---

## B5. ÇAKIŞMA HARİTASI — sınırlar

| Dosya / yer | Durum |
|---|---|
| `evidence.py` | ✅ **temiz alan** → Bölüm A'da yapılıyor |
| `app.py:1941` `_toplu_terazi_candidate_pool` | 🔴 **çakışma** |
| `app.py:19889` civarı toplu render | 🔴 **çakışma** |
| `tarama_merkezi.py` | 🟠 **tasarım ekseni çatışması** |

**🔴 Aday havuzu:** Tarama Merkezi'nin karar masası **Gold Mine + Güçlü Dönüş + Minervini + Wilder RSI**'dan besleniyor (Radar 1/2 tasarım gereği dışarıda). D1 Gold Mine'ı besleyen taramaları sustururken bu havuz daralır; **yeni ekran açıldığı gün boş kalabilir.** D1 uygulanınca havuzun ne kadar kaldığı ÖLÇÜLMELİ.

**🔴 Toplu render:** Tarama Merkezi'nin kalan tek işi *"eski toplu render bloklarını kaldır"* (📊 KARNELİ TARAMALAR + Gold Mine vitrini + tier açılır kutuları). D2 de tam o yüzeyi bölmek istiyor. Biri siliyor, öteki yeniden düzenliyor — **aynı satırlar.**

**🟠 Eksen çatışması:** Yeni ekran **karar durumuna** göre bölünmüş (Öncelikli LONG · Teyit Bekleyenler · Yeni Sinyaller · Risk Masası · Katalog). D2 **vadeye** göre bölmek istiyor (1–5 gün · 10–20 gün). Üst üste konursa 5 sekme × 2 masa = 10 kutu.

**Çözüm sırası:** *Tarama Merkezi'ni önce canlıya al → vade eksenini onun sekmelerinin içine yerleştir.* Sıfırdan ikinci ekran kurulmayacak.

---

## B6. BAĞLAYICI KARARLAR

| | Karar | Durum |
|---|---|---|
| **D1** | **Radar 1, Radar 2, Altın, Platin, Tekli Altın, Güçlü Dönüş** karar yüzeyinden çekilir — `is_ai_suppressed` (YUMUŞAK), `ELENEN` değil | ✅ **Bölüm A'da yapılıyor** |
| **D2** | Kısa masa (1–5 gün) / sabır masası (10–20 gün) ayrımı | ⏸ **Tarama Merkezi canlıya alınana kadar bekler** |
| **D3** | Son kullanma tarihi: Minervini 3 gün · C6 5 gün · Tavan 20 gün | ✅ etiket kalır, **Tavan rozeti kalkar** |
| **D4** | C6, **300 bağımsız olaya** ulaşmadan çekirdek ilan edilmez | ✅ üç ajan mutabık |
| **D5** | Yeni dip motoru sıfırdan ölçülür, 7 maddelik kapıdan geçer | ✅ üç ajan mutabık |
| **D6** | Rejim tanımı **XU100 > 50 günlük ortalama** olarak mühürlenir, bir daha tartışılmaz | 🆕 kullanıcı onayı bekliyor |
| **D7** | Karneler **ertesi açılış + tavan kilidi** cetveliyle ölçülür (`resolve_next_open_entry` mevcut, dört dosyaya bağlı) | 🆕 kullanıcı onayı bekliyor |
| **D8** | Pre-Launch BOS kısa masadan sabır masasına taşınır (aday, rozet yok) | 🆕 kullanıcı onayı bekliyor |
| **D9** | `evidence.py` Tavan 🟢 rozeti + `ALFA_T20` düzeltilir | ✅ **Bölüm A'da yapılıyor** |

### D5 — yeni dip motorunun kabul kapısı (7 madde)

Antigravity'nin önerdiği "teyitli dip likidite / 4 saatlik dönüş" motorunun **bugün sıfır karnesi var**. Ona iliştirilen +%2,36 / +%8,74 rakamları **Güç-Devam aynasına** aitti; Codex aynı raporda "bu bir dip avı değildir" diye uyarmıştı. Ödünç karne geçersiz.

Kabul kapısı:
1. En az **üç farklı vade**
2. **İki ayrı piyasa koşulu**
3. İlk beş günde **+%3'e mi, önce −%2,5'e mi** ulaştı?
4. XU100'e karşı gerçek üstünlük
5. Aynı giriş cetveli: hem hisse hem endeks için **ertesi işlem günü açılışı**
6. Yeterli **bağımsız olay** (tekrar sayım temiz)
7. Daha sonra görülmemiş dönemde **ikinci doğrulama**

---

## B7. SIRA

1. ✅ **Bölüm A** — `evidence.py` (devir işi) — YAPILDI + DENETLENDİ (B0b)
2. 🛑 **DUR** — kullanıcının ikinci emri
3. **İş 1** — D1 sonrası aday havuzu ölçümü (B5'teki risk)
4. **İş 2** — D7: karnelerin ertesi açılış + tavan kilidiyle yeniden okunması
5. **İş 3** — D6: rejim tanımının mühürlenmesi (XU100 > 50 günlük ortalama)
6. **İş 4** — D3/D4/D8: vade + son kullanma etiketleri, Pre-Launch'ın taşınması
7. **İş 5** — yeni dip motorunun test betiği (canlı dosyaya dokunmaz)
8. **İş 6** — Tarama Merkezi'nin canlıya alınması
   → ⚠ **D1'in EKRAN YARISI BURADA TAMAMLANIR** (B0b): eski toplu render blokları
   kaldırılınca Radar 1 / Altın / Platin / Tekli Altın / Güçlü Dönüş ekrandan da
   çıkmış olur. Kaldırma sırasında bu altısının gerçekten görünmez olduğunu
   DOĞRULA; olmuyorsa yeni ekrana çizim-anı süzgeci ekle (`is_ai_suppressed`).
9. **İş 7** — D2: vade masaları, Tarama Merkezi'nin sekmelerinin içine
   → Ekran kartları son kullanma tarihini `evidence.py` içindeki
   `SCANNER_VADE_POLICY` tek kaynağından okumalı; politika yalnız tanımlı
   kalmamalı, her kartın vade/masa/etiket alanını ve kapanma tarihini beslemeli.

---

## B7b. AÇIK KONU — SABİT VADE vs GERÇEK HAREKET SÜRESİ (kullanıcı itirazı, 27 Ağu)

**İtiraz:** "Sabit vadeler içime sinmiyor. Her hissenin, her yukarı çıkışın süresi
farklı. Bazısı 5-6 günlük tepki verir, bazısı 12 gün sürer, bazısı dinlene
dinlene 50 gün çıkar."

**İtiraz haklı — ama iki ayrı şey karışıyor:**

| | Vade | Neden |
|---|---|---|
| **ÖLÇMEK** (taramaları kıyaslamak) | **SABİT** | Vadeyi sonradan seçmek hiledir. `ideal_day`'in 1–28 gün içinden en iyisini seçmesi tam bu — her taramaya kendi en parlak gününü verirsen hepsi kazanan çıkar. Sabit vade = aynı terazi. |
| **TUTMAK** (pozisyon yönetmek) | **DEĞİŞKEN** | 6 günlük tepkiyle 50 günlük trend aynı süreyle yönetilemez. |

Sabit vade "pozisyonu 5 günde kapat" demek DEĞİL; "taramaları kıyaslarken
herkese aynı cetveli uygula" demek.

### Değişken süreyi dürüstçe ölçmenin yolu

**İlke: süre değişebilir, KURAL önceden konur.**

1. **Üç sınır yöntemi:** kâr hedefi + zarar sınırı + azami bekleme önceden
   belirlenir, sonra "hangisine önce değdi" sorulur. Biri 4 günde hedefe gider,
   öteki 23 günde — ikisi de aynı kuralla ölçülmüştür. Kimse sonradan
   "aslında 27. güne baksaydık" diyemez.
   → Bu yöntem D5'in kabul kapısında (madde 3) ZATEN VAR ama oraya sıkışmış.
     Bütün taramalara yayılması gerekiyor.
2. **En iyi/en kötü nokta kaydı:** her sinyalin yolda gördüğü en yüksek ve en
   düşük seviye ayrı kaydedilir. Ortalama +%1 yapan ama yolda −%9 gören bir
   tarama, sabit vadenin göstermediği bir karaktere sahiptir.

### KARAR

- **İş 2 sabit vadeyle (T+3/T+5/T+20) devam eder** — orada amaç taramaları
  kıyaslamak, cetvel tek olmalı.
- **Değişken vade AYRI BİR İŞ PAKETİ** (İş 8 adayı). Önce kural yazılır,
  sonra ölçülür. Asla tersi.
- `ideal_day` mantığı bu paket tamamlanana kadar karar üretmez.

---

## B7c. İŞ 9 ADAYI — YENİ TARAMA ARAŞTIRMASI (27 Ağu, karne sonrası)

Mühürlü karne (İş 3 çıktısı) yön verdi. Detay: `memory/project_yukselis_adayi_arastirma.md`.

**Bulgu: kısa vade boş.** T+3'te eşiği geçen ~25 hücrede **iki rejimde de pozitif tek bir
BOĞA taraması yok**. Eşiği geçen iki boğa taraması var, ikisi de T+20'de:
`radar2` +%2,00/+%0,24 (N 218/396) · `liderlik_aday` +%1,46/+%0,29 (N 200/402).
İkisi de ilk beş günde para kaybettiriyor.

⚠ `er_D4`/`er_D5` T+3-T+5'te "iki rejimde pozitif" görünür — **AYI yönlüdürler**,
getirileri sinyal yönüne çevrilmiştir. Yükseliş adayı değildirler.

**Yorum:** ayakta kalan iki taramanın ikisi de liderlik/göreli güç ailesinden. Sistem
kendi verisiyle çapraz kesit momentumunu yeniden keşfetmiş — bankaların en çok
kullandığı faktör. Batan taraflar dip avı ve tepki yakalama.

**Sıradaki 3 aday (öncelik sırasıyla):**
1. **Bilanço sonrası sürüklenme** — hiç test edilmedi, literatürde en dayanıklı anomali,
   vadesi 1-60 gün. Takvim var; "sürpriz" verisi yok ama açıklama günü fiyat+hacim
   tepkisi meşru vekil.
2. **`liderlik_aday` derinleştirme** — zaten kazanıyor, üstünde kimse çalışmamış.
3. **Endeks giriş-çıkış olayları** — tarihli, önceden duyurulu, mekanik.

**Kural:** üçü de aynı kapıdan — üç vade + iki rejim + ertesi açılış (tavan kilitli) +
rejim başına ≥150 olay. **Yeni 3 günlük tarama aramaya girilmeyecek**, veri aksini
söyleyene kadar.

---

## B8. ÖLÇÜM SINIRI

Tüm bulgular **67 işlem gününden** (Mayıs–Ağustos 2026; 30 yükselen, 37 düşen). C6'nın yükselen-rejim örneği 29, Pre-Launch'ın düşen-rejim örneği 47. Tavan ailesinin rakamları tavan kilidi uygulanmadığı için hâlâ iyimser.

**Hiçbiri kalıcı kural değildir.** Rejim değiştiğinde tamamı yeniden koşulur.

### Bu turun kalıcı dersi

Codex'in pencere itirazı **iki kez** haklı çıktı: bir kez kendi sayısında, bir kez Claude'un Güçlü Dönüş hatasında.

> **Bundan sonra hiçbir tarama, ömrü farklı bir taramayla ortak pencere eşitlenmeden karşılaştırılmayacak.**
>
> **Ve hiçbir karne, işlem yapılabilir giriş cetveli kullanılmadan mühürlenmeyecek.**
