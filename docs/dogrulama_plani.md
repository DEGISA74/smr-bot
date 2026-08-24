# DOĞRULAMA PLANI — kenar var mı, yoksa güzel cümleler mi?

**Tarih:** 23 Ağustos 2026 · **Durum:** ön-kayıt (pre-registration). Hiçbir test henüz koşulmadı.
**Kural:** Bu dosya **veriye bakmadan önce** yazıldı. Aşağıdaki hipotezler, ölçütler ve karar kuralları
sonuçları gördükten sonra değiştirilemez. Değiştirilirse, değişiklik tarihi ve gerekçesi en alta yazılır.

---

## 0. Neden bu kadar disiplin?

Motor her mum için 11 trend durumu × 19 verim durumu × 19 hacim senaryosu üretiyor: **3.971 hücre.**
800 hisse × 5 yıl ≈ 1 milyon gözlem. Bu veriyi "hangi hücre iyi getiri veriyor" diye taramak,
**ortada hiçbir gerçek etki olmasa bile** yaklaşık 200 hücreyi "anlamlı" gösterir. Sırf tesadüften.

Bu yüzden: **önce 5 soru yazıyoruz, sonra veriye bakıyoruz.** Tarama yok.

---

## 1. MEVCUT ALTYAPIYA NASIL BAĞLANACAK (dokunmadan, sarmalayarak)

`backtest_runner.py` **değiştirilmeyecek**. İki sebeple doğrudan da kullanılamaz:

1. O dosya `scan_signals` tablosundaki **ayrık sinyalleri** değerlendirir (tarama X, gün Y, hisse Z).
   Bizim motorumuz ayrık sinyal değil, **her mum için bir durum** üretir. Girdi şekli farklı.
2. Bizim testimiz `patron.db`'ye yazmamalı — canlı karneyi kirletir.

**Çözüm: `smr_lab/validate_multiscale.py` (yeni, izole).** Mevcut dosyadan sadece **okuyarak** yararlanır
(içeri alma güvenli: o dosyanın çalıştırma bloğu koruma altında, içeri alınca hiçbir şey çalışmaz):

| Neyi ödünç alıyoruz | Nereden | Neden yeniden yazmıyoruz |
|---|---|---|
| Parquet okuma (`.IS_1d` / `_1d` son ek denemesi) | `backtest_runner.load_parquet` | Dosya adı kuralı tek yerde kalsın |
| XU100 kıyas serisi | `backtest_runner.load_xu100` | Alfa hesabı aynı kıyasla yapılsın |
| Kurumsal işlem (bölünme) eşiği `%15` | `backtest_runner.KURUMSAL_ESIK` | **Kritik** — §4.1'e bak |
| Veri tutarsızlık eşiği `1.25x` | `backtest_runner.TUTARSIZLIK_ORAN` | Aynı bekçi |
| İleri pencereler `[5, 10, 20]` | `backtest_runner.FORWARD_WINDOWS` | Sonuçlar mevcut karneyle kıyaslanabilsin |

**Yazacağımız yeni parça:** olay-çalışması (event study) çatısı.
Her (hisse, tarih) satırı için motorun durumu + ileri getirisi yan yana konur; gruplanır; sınanır.
Çıktı `smr_lab/out/` altına JSON+CSV olarak yazılır — `patron.db`'ye **tek satır bile** yazılmaz.

---

## 2. GİRİŞ / ÇIKIŞ TANIMI (tek ve değişmez)

- **Karar barı:** `t` (motorun `karar` tablosundaki satır — o mum kapandığında donmuş değer).
- **Giriş:** `t+1` **açılışı**. Kapanışa girmek de meşru olurdu ama BIST kapanış seansı yapısı nedeniyle iyimserdir.
- **Çıkış:** `t+1+N` kapanışı, `N ∈ {5, 10, 20}` seans.
- **Getiri:** `(çıkış / giriş) − 1`.
- **Alfa:** aynı pencerede XU100'ün getirisi düşülür. **Ana ölçüt alfadır**, ham getiri değil —
  yoksa boğa piyasasında her şey "işe yarıyor" görünür.
- **Giriş yapılamayan durumlar** (tavan açılışı, işlem yok, bölünme penceresi) → o gözlem **ölçülemedi**
  diye işaretlenir, sıfır sayılmaz, atılmaz. §4'e bak.

---

## 3. ÖN-KAYITLI HİPOTEZLER (beş tane, fazlası yok)

### H0 — Tanımlayıcı rapor (hipotez değil, ön şart)
Her durumun **frekansı** yayınlanır: 11 trend durumu, 19 verim durumu, 19 hacim senaryosu, sağlık durumları.
- **Kural:** Bir durumun örneklemi **< 1.000 gözlem** ise o durum **"ölçülemedi"** diye işaretlenir ve
  hiçbir hipoteze girmez. Sessizce "iyi" sayılmaz, sessizce atılmaz.
- Beklenti (sentetik ön ölçüm): S1, S7, S8, S9, S13, S14, S17, S18 nadir çıkabilir. Gerçek veri karar verecek.

### H1 — Uyum ve kanaat gerçekten ayrıştırıyor mu?
> **İddia:** `uyum_skoru ∈ {+5, +6}` **ve** `kanaat = YÜKSEK` iken 20 seanslık **alfa dağılımı**,
> aynı tarihlerde rastgele giren birinin dağılımından farklıdır.

- **Kıyas grubu:** aynı gün, aynı evrenden rastgele seçilmiş hisseler (tarih eşleştirmeli).
  Böylece piyasa yönü iki tarafta da aynıdır; ölçtüğümüz şey saf seçim kabiliyeti olur.
- **Ölçüt:** ortalama alfa, medyan alfa, isabet oranı, kâr faktörü, işlem başına maliyet düşülmüş beklenen getiri,
  ve **en kötü %5 dilim** (kuyruğu gizlemeyelim).
- **Yön testi ayrıca:** `{−5, −6}` için aynısı (aşağı yönde de çalışıyor mu, yoksa sadece boğa mı?).

### H2 — Hacim uyumsuzluğu trend sonu öncüsü mü?
> **İddia:** Yükseliş rejimindeyken **S9 (Hacim Uyumsuzluğu)** görülen barlardan sonra,
> rejimin 10 seans içinde dönme olasılığı, görülmeyen barlara göre **yüksektir**.

- **Ölçüt:** koşullu dönüş oranı farkı + bunu takip eden 10/20 seanslık alfa.
- **Not:** Bu, S1–S19'un tümünü test etmek **değildir**. Sadece S9 test edilir çünkü tarifte
  açıkça "öncü" iddiası taşıyan tek senaryo odur. Diğerleri H0'da yalnızca frekansla raporlanır.

### H3 — Adaptif histerezis gerçekten işe yarıyor mu?
> **İddia:** Histerezis, yatay piyasada sinyal sayısını düşürür **ve** bu düşüş kârlılığa olumlu yansır.

İki ayrı soru, ikisi de ayrı ayrı ölçülür:
- **(a) Sayı:** yatay alt-örneklemde (`|uyum_skoru| ≤ 2` günlerinin çoğunlukta olduğu dönemler)
  rejim dönüş sayısı, sabit eşikli kontrolden az mı?
- **(b) Kâr:** aynı alt-örneklemde işlem başına beklenen alfa, maliyet düşüldükten sonra daha iyi mi?
- **Kontrol grubu:** `λ = 0` (histerezissiz, sabit `τ0` eşiği). Aynı motor, tek fark bu.
- **Uyarı:** (a) doğru çıkıp (b) yanlış çıkabilir — bu bir başarısızlık değil, **gecikmenin bedelidir**
  ve öyle raporlanır.

### H4 — Sinyal sağlığı çıkış bilgisi taşıyor mu? *(görselden çıkan modül)*
> **İddia:** Açık bir rejim içindeyken sağlık **ZAYIFLIYOR**'a düşen barlardan sonraki 10 seanslık alfa,
> **TEYİTLİ** kalanlardan **düşüktür**.

- Bu, SMR için en yüksek pratik değeri olan hipotez: doğruysa doğrudan bir **çıkış kuralı** doğar.
- **Ölçüt:** iki grubun alfa farkı + "ZAYIFLIYOR'da çıkan" ile "sonuna kadar tutan" iki basit
  kural setinin kümülatif sonucu.

### H5 — Verimlilik boyutu bir şey EKLİYOR mu?
> **İddia:** Trend tek başına bilindiğinde, verimlilik ölçüsü ileri alfayı açıklamaya **ek katkı** yapar.

- **Yöntem:** iç içe model karşılaştırması. Model A: alfa ~ uyum_skoru. Model B: alfa ~ uyum_skoru + verim.
  B'nin A'ya göre açıklama gücü artışı, tarih-kümelenmiş standart hatalarla sınanır.
- **Neden bu hipotez var:** Görev B, verimliliği "gerçekten yeni tek şey" diye işaretledi.
  Yeni olması işe yaradığı anlamına gelmez. **En sıkı sınanması gereken madde budur.**

---

## 4. İSTATİSTİK — tuzaklar ve zorunlu önlemler

### 4.1 ⚠ Bölünme/temettü zehri (en büyük tehlike)

`data_policy.AUTO_ADJUST = False` — yani parquet'lerdeki geçmiş fiyatlar **bölünme için düzeltilmemiş**.
Bu bilinçli bir karar (seviyeler aracıdaki fiyatla tutsun diye) ve seviye ölçmek için doğru,
ama **getiri ölçmek için zehir**: mevcut karnede tek bir bölünme satırının bir taramanın ortalamasını
tek başına "elit" yaptığı **zaten ölçülmüş**.

**Zorunlu:** `KURUMSAL_ESIK = %15` bekçisi bizim testimizde de aynen uygulanır. Bölünme penceresine
düşen gözlem **ölçülemedi** olur. Ayrıca motorun `sigma`'sı da bu barlarda bozulur → bu barlar
ayrı bir bayrakla işaretlenip duyarlılık analizinde ayrıca raporlanır.

### 4.2 ⚠ Örtüşen pencereler — görünen örneklem gerçek değil

20 seanslık getiriyi her gün yeniden ölçersen, ardışık 20 gözlem **neredeyse aynı** olayı sayar.
1 milyon satırlık örneklem, 20 günlük pencerede **etkin olarak ~50.000** bağımsız gözlemdir.
Bunu görmezden gelen bir t-testi, olmayan anlamlılığı **var gösterir**.

**Zorunlu üç önlem:**
1. **Tarihe göre kümelenmiş standart hata.** Aynı gün tüm BIST birlikte hareket eder;
   800 hissenin aynı günkü getirisi 800 bağımsız gözlem değildir.
2. **Blok önyükleme (block bootstrap)**, blok uzunluğu ≥ ileri pencere (20 seans).
3. **Çapraz kontrol:** örtüşmeyen alt-örneklem (her 20 günde bir gözlem) ile aynı test tekrarlanır.
   İki sonuç ayrışıyorsa, **örtüşmeyen sonuç geçerlidir**.

### 4.3 Örneklem büyüklüğü — kaç gözlem lazım?

BIST'te 20 seanslık getirinin standart sapması kabaca **%10** mertebesindedir
(günlük ≈ %2.2 × √20 ≈ %9.8 — bu kaba bir mertebe tahminidir, gerçek değer testte ölçülüp raporlanacak).

Anlamlı bulmak istediğimiz en küçük etki: **20 seanslık +%1.5 alfa** (maliyetten sonra anlamlı sayılabilecek eşik).

| Senaryo | Gereken gözlem (grup başına) |
|---|---|
| Tek test, %5 anlamlılık, %80 güç | **≈ 670** |
| 5 hipoteze Bonferroni (α = 0.01), %80 güç | **≈ 1.000** |
| Örtüşme düzeltmesi sonrası (etkin/ham ≈ 1/20) | ham örneklemde **≈ 20.000 satır** |

**Karar:** H0'daki "< 1.000 gözlem → ölçülemedi" kuralı buradan gelir. Ve örtüşme yüzünden
ham satır sayısı 20.000'in altındaki hiçbir hücre için "kenar var" denmez.

### 4.4 Çoklu karşılaştırma düzeltmesi

- **H1–H5 ailesi (5 birincil test):** **Bonferroni**, `α = 0.05 / 5 = 0.01`.
  Az sayıda, önceden yazılmış hipotez için doğru araç budur.
- **H0'daki tanımlayıcı frekans tablosu:** hipotez testi **yapılmaz** (sadece sayı raporlanır),
  dolayısıyla düzeltmeye gerek yoktur.
- **Keşif amaçlı hücre taraması yapılacaksa** (ki bu planda **yok**), tek geçerli yol
  Benjamini-Hochberg yanlış keşif oranı kontrolüdür ve sonuçlar **"keşif"** etiketiyle,
  ayrı bir başlık altında, "doğrulanmadı" ibaresiyle yayınlanır.
- Referans: 3.971 hücrenin tamamı %5 ile taransaydı Bonferroni eşiği `α = 1.26e-5` olurdu,
  bu da **|z| ≈ 4.37** demektir. Böyle bir çıta pratikte hiçbir hücrenin geçemeyeceği bir çıtadır —
  hücre taramasının neden yapılmadığının sayısal cevabı budur.

### 4.5 Ölçüt seti (sadece isabet oranı ASLA yeterli değil)

Her hipotez için **hepsi birlikte** raporlanır:

| Ölçüt | Neden |
|---|---|
| Ortalama ve **medyan** alfa | Ortalama tek bir uçtan bozulabilir; medyan onu ele verir |
| İsabet oranı | Tek başına anlamsız, ama RR ile birlikte anlamlı |
| Kazanç/kayıp oranı (RR) | %40 isabet + 3:1 RR kazandırır; %70 isabet + 0.3:1 batırır |
| **Beklenen getiri** = (isabet × ort. kazanç) − (1−isabet) × ort. kayıp − maliyet | Tek gerçek karar ölçütü |
| Kâr faktörü | Mevcut karneyle kıyaslanabilirlik (Pre-Launch BOS 1.86 referans) |
| En kötü %5 dilim ve en büyük geri çekilme | Kuyruk riski gizlenmesin |
| Gözlem sayısı + etkin gözlem sayısı | Örtüşme düzeltmesi görünür olsun |

**Maliyet:** komisyon + yayılım + kayma tek bir parametre olarak (`islem_maliyeti_bps`) girilir ve
**sonuç tablosu üç maliyet seviyesinde** (0 / gerçekçi / kötümser) yayınlanır. Uydurma tek bir sayı kullanılmaz.

---

## 5. BIST'E ÖZEL KONULAR

### 5.1 Tavan / taban seansları
- `t+1` açılışı tavanda açılırsa **giriş yapılamaz**. Mevcut altyapı bunu zaten `entry_gap_pct` /
  `entry_status` / `entry_delay` alanlarıyla izliyor; aynı mantık uygulanır.
- Tavan barlarında `High == Low` olur → bizim motorda bar içi konum nötr (0.5) kabul edilir,
  yani **hacim deltası sıfırlanır**. Bu doğru davranıştır ama tavan günlerinin hacim senaryolarını
  sistematik olarak "dengeli" göstereceği unutulmamalı. Tavan/taban barları **ayrı alt-örneklem** olarak raporlanır.

### 5.2 Düşük likidite ve ayak izinin güvenilirliği
Hacim profili, hacmin fiyat seviyelerine dağılımını varsayar. Günde birkaç yüz işlem gören bir hissede
bu dağılım tesadüftür. **Eşik uydurmuyoruz — ölçüyoruz:**
- Evren, ortalama günlük TL işlem hacmine göre **beşe bölünür** (en likit %20 … en likit olmayan %20).
- H1–H5 **her dilim için ayrı** raporlanır.
- Beklenti: POC/ayak izi bileşenlerinin katkısı alt dilimlerde kaybolur. Kaybolduğu yer, gerçek eşiktir.

### 5.3 Seans yapısı ve 47 barın takvim karşılığı
Kaynak: bu deponun kendi takvim dosyası — normal seans 10:00–18:00 (**480 dk**), arefe 10:00–12:30 (**150 dk**).

| Vade | Bar / işlem günü | 47 bar ≈ | Motor tam güç (301 bar) ≈ |
|---|---|---|---|
| Günlük | 1 | 47 işlem günü (**≈ 2.2 ay**) | 301 işlem günü (**≈ 14 ay**) |
| 4 saat* | **2** | 23.5 işlem günü (**≈ 1.1 ay**) | 150 işlem günü (**≈ 7 ay**) |
| 1 saat | 8 | 5.9 işlem günü (**≈ 1.2 hafta**) | 37.6 işlem günü (**≈ 1.8 ay**) |
| 15 dakika | 32 | 1.5 işlem günü | 9.4 işlem günü (**≈ 2 hafta**) |

\* 4 saat yerel bir vade değildir; 1 saatlikten türetilir ve **10:00'a çapalanmalıdır**, yoksa günde 2 yerine 3 kırık bar oluşur.

**Arefe günleri bar sayısını bozar** (150 dk → 15 dakikalıkta 10 bar, 1 saatlikte 2-3 bar).
Motor takvimi umursamaz, bar sayar. Bu yüzden vade testlerinde arefe günleri işaretlenip
duyarlılık analizinde ayrıca raporlanır.

---

## 6. VADE KARŞILAŞTIRMASI — hangi vadede kenar var?

Aynı hipotezler dört vadede koşulur. **Ama örneklemler eşit değil ve bu dürüstçe yazılmalı:**

| Vade | Veri erişimi | Gerçekçi test kapsamı | Not |
|---|---|---|---|
| Günlük | Kendi parquet arşivin (yıllar) | 800 hisse × 5 yıl — **ana test** | Tek sağlam test budur |
| 1 saat | Sağlayıcıda ~729 gün | ~200 hisse × 2 yıl (indirme yükü) | İkincil |
| 4 saat | 1 saatlikten türetilir | 1 saatlikle aynı kapsam | Çapa şart |
| 15 dakika | Sağlayıcıda **~59 gün** | ~59 gün — **5 yıllık test imkânsız** | Ayrı ve küçük bir çalışma; ana testle kıyaslanamaz |

**Bağlayıcı kural:** 15 dakikalık sonuç, günlük sonuçla **aynı tabloda yan yana konmaz**.
59 günlük bir örneklemden çıkan sayıyı 5 yıllık bir sayının yanına koymak, ikisini eşit göstermektir.
Ayrı başlık, ayrı uyarı.

---

## 7. KARAR KURALLARI (sonuçları görmeden yazıldı)

Her hipotez için ne çıkarsa ne yapacağımız **şimdi** belirlendi:

| Sonuç | Karar |
|---|---|
| Maliyet sonrası alfa ≥ **+%3** (20 seans), Bonferroni'den geçiyor, likidite dilimlerinin en az 3'ünde tutarlı | **Üretime aday.** Önce panelde gösterilmeden `scan_signals`'a yazılır, 3 ay canlı izlenir |
| Alfa pozitif ama düzeltmeden geçmiyor, veya tek likidite diliminde | **Beklemede.** Örneklem büyüsün, tekrar ölç. AI metnine, panele, bota **girmez** |
| Alfa ≈ 0 veya negatif | **Reddedildi.** Modül yazıldığı yerde kalır, kullanılmaz. Sicile "ölçüldü, çıkmadı" diye işlenir |
| Örneklem < 1.000 (veya ham < 20.000) | **Ölçülemedi.** İyi de kötü de denmez |

**Ve en önemlisi:** H1–H5'in hepsi başarısız olursa, doğru cevap "başka bir hücre bulalım" değil,
**"bu mimari SMR'ye kenar katmıyor"** demektir ve öyle raporlanacaktır.

---

## 8. ÇALIŞTIRMA SIRASI

1. `smr_lab/validate_multiscale.py --frekans` → H0 tanımlayıcı rapor (hipotez testi yok)
2. Ölü bölge eşiği kalibrasyonu (`--kalibre`), **getiriye bakmadan**, sonuç dondurulur
3. H1 → H5, her biri tek koşu, sonuçlar `smr_lab/out/` altına
4. Duyarlılık: `n_vol ∈ {20,60}`, `θ` üç değerde, yüzdelik penceresi `{126, 252, 504}`
5. Vade karşılaştırması (günlük ana test; 1s/4s ikincil; 15dk ayrı ve küçük)
6. Rapor: `docs/dogrulama_sonuclari.md` — **karar kuralı tablosuyla birlikte**

**Bu plan koşulmadan hiçbir çıktı panele, AI metnine veya bota girmez.**

---

## 9. TESTİN KENDİSİ DOĞRU MU? — negatif kontrol (23 Ağu 2026)

Bir doğrulama çatısının ilk sınavı, **kenar olmayan yerde "kenar yok" diyebilmesidir.**
`smr_lab/validate_multiscale.py` 40 adet **rastgele yürüyüş** serisiyle (28.000 satır) koşuldu —
bu veride tanım gereği hiçbir öngörülebilirlik yoktur.

Sonuç (H1, 20 seans): ortalama alfa **%0.68**, tarihe göre kümelenmiş %95 güven aralığı
**[−%0.70, +%1.24]** → **sıfırı içeriyor → "kanıt yok".** Doğru cevap.

Kümeleme yapılmasaydı bu 2.285 gözlemli grup rahatlıkla "anlamlı" görünebilirdi.
Yani §4.2'deki örtüşme/kümelenme önlemi **süs değil**, sonucu doğrudan değiştiren bir bileşen.

⚠ Bu bir performans testi **değildir**. Yalnızca ölçüm aletinin kalibre olduğunu gösterir.
Gerçek soru hâlâ cevapsız ve ancak senin verinle cevaplanır.
