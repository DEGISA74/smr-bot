# ST-EP 5.1 — Mühendislik Spesifikasyonu (yeniden inşa)

**Durum:** Taslak v1 · **Tarih:** 23 Ağustos 2026 · **Yazan:** Claude Code (kantitatif araştırma görevi)
**Kapsam:** Yalnızca belge. Bu dosya hiçbir mevcut SMR dosyasını değiştirmez.

---

## 0. Bu belge nedir, ne değildir

**Nedir:** TradingView'de `ata_sabanci` kullanıcısının yayımladığı "Smart Trader (ST-EP 5.1)" göstergesinin
**kamuya açık ürün tarifinin** mühendislik diline çevrilmiş hâli. Tarifte sözel bırakılmış her yer için
en az iki aday formül yazıldı, artıları/eksileri karşılaştırıldı, biri seçildi ve seçim gerekçesi yazıldı.

**Ne değildir:**
- Orijinal script'in kodu değildir. Script kapalı kaynak (korumalı). **Kodu aranmadı, indirilmedi, decompile edilmedi.**
- Orijinalin birebir kopyası olduğu **iddia edilmiyor**. Aşağıdaki her formül bizim kendi matematiğimizdir.
  Aynı isimli çıktıların aynı sayıları üreteceğine dair hiçbir garanti yoktur ve böyle bir iddia bu belgede yer almaz.
- Bir strateji değildir. Hiçbir yerde "bu iyi çalışır" yazmaz — **hiçbir backtest sonucu henüz yok** (bkz. Görev D).

**Kaynak durumu:** Yazarın kendi beyanı: gösterge al/sat sinyali üretmez, "durum tarifi" yapar.
Yayımlanmış backtest, kazanma oranı veya örneklem büyüklüğü **yok**. Yani elimizdeki tek şey bir *tasarım fikri*;
kenarı olup olmadığı bilinmiyor. Bu belgenin görevi fikri ölçülebilir hâle getirmek, doğrulamak değil.

**Varsayım disiplini:** Emin olmadığımız her yer `# VARSAYIM: V-xx` etiketiyle işaretlendi ve
`docs/varsayimlar.md` dosyasında tek tek toplandı. Etiketsiz her satır ya kaynağa ya da matematiğe dayanır.

---

## 1. ORTAK TEMELLER

### 1.1 Notasyon

| Sembol | Anlam |
|---|---|
| `t` | Değerlendirilen mumun indeksi (0 = en eski, N-1 = en yeni) |
| `O_t, H_t, L_t, C_t, V_t` | O mumun açılış / en yüksek / en düşük / kapanış / hacmi |
| `r_t` | Logaritmik getiri: `ln(C_t / C_{t-1})` |
| `n` | Ölçek (pencere) uzunluğu, mum sayısı — `n ∈ {3, 7, 13, 19, 29, 47}` |
| `σ_t` | t anındaki tek-mum volatilite tahmini (Yang-Zhang, §1.4) |
| `TR_t` | True Range: `max(H_t − L_t, \|H_t − C_{t-1}\|, \|L_t − C_{t-1}\|)` |
| `θ` | Ölü bölge eşiği (trend oyu için) |
| `S_t` | Uyum skoru (Alignment Score), −6 … +6 |

### 1.2 Girdi veri sözleşmesi

Motorun tek girdisi bir OHLCV `DataFrame`'dir:

| Alan | Tip | Zorunlu | Kural |
|---|---|---|---|
| index | `DatetimeIndex` | evet | Artan sıralı, **tekrarsız**, boşluk olabilir (tatil/seans) |
| `Open` | float64 | evet | > 0 |
| `High` | float64 | evet | `High ≥ max(Open, Close)` |
| `Low` | float64 | evet | `Low ≤ min(Open, Close)` ve `Low > 0` |
| `Close` | float64 | evet | > 0 |
| `Volume` | float64 | hayır* | ≥ 0 veya NaN |

\* `Volume` yoksa veya tamamı 0/NaN ise **Modül C (hacim) devre dışı kalır** ve ilgili tüm çıktılar `None` döner.
Motor hata vermez, sessizce de geçmez — çıktıya `hacim_gecerli = False` bayrağı koyar. (Gerekçe: FX serilerinde
hacim yoktur; sıfır hacmi "düşük ilgi" diye okumak sahte sinyal üretir.)

Motor girdiyi **düzeltmez**. Bölünme/temettü düzeltmesi, tatil barı temizliği, hacim override'ı
SMR'nin mevcut veri katmanının işidir (`data_layer.get_safe_historical_data` + `veri_bekcisi`).
Motor sadece sözleşmeyi doğrular; ihlal varsa ilgili satırı `NaN` yapar (§7).

### 1.3 Geleceğe bakış yasağı (anti-repaint) — formal tanım

Yazarın tarifi: *"Tüm sinyaller son kapanmış mumdan hesaplanıyor, canlı mum hesaba katılmıyor."*
Bunun çevrimdışı (DataFrame üzerinde) karşılığı iki ayrı şeydir ve **ikisini de ayrı kolon olarak üretiyoruz**:

1. **`karar_*` (kapanışta donan değer):** `karar[t] = f(X_0 … X_t)`.
   Yani t mumu kapandığı anda hesaplanır ve bir daha **asla değişmez**.
   Geleceğe bakış yoktur: t'den sonraki hiçbir bar kullanılmaz.
2. **`canli_*` (canlı barda ekranda görünen değer):** `canli[t] = karar[t-1]`.
   t mumu henüz açıkken ekranda duran değer budur — yazarın "canlı mum hesaba katılmıyor" dediği şey.

**Backtest kuralı (bağlayıcı):** giriş sinyali `karar[t]`'den okunur, **işlem `t+1` açılışında** doldurulur.
`karar[t]`'i `C_t` ile doldurmak da matematiksel olarak meşrudur (kapanışa emir veren biri için) ama
BIST kapanış seansı yapısı nedeniyle bu iyimserdir; varsayılan `t+1` açılışıdır.

**Testle kanıt:** `f(X_0…X_t)` fonksiyonu, veri setinin sonuna bar eklenip çıkarılmasına **duyarsız** olmalıdır.
`tests/test_no_lookahead.py` bunu şöyle kanıtlar: seriyi rastgele bir `k` noktasından kes,
`0…k` üzerinde hesapla; sonra tam seri üzerinde hesapla; ilk `k` satır **bit düzeyinde aynı** olmalı.

### 1.4 Ortak ölçü birimi: Yang-Zhang volatilitesi

Farklı enstrümanların (THYAO, BTC, EURUSD) ve farklı vadelerin eğimlerini karşılaştırabilmek için
ortak bir "cetvel" gerekir. Yazar bunun için Yang-Zhang (2000) tahmincisini belirtiyor. Formül:

```
σ²_YZ = σ²_gece + k·σ²_ac_kapa + (1−k)·σ²_RS

σ²_gece    = 1/(n−1) · Σ (o_i − ō)²        ,  o_i = ln(O_i / C_{i−1})
σ²_ac_kapa = 1/(n−1) · Σ (c_i − c̄)²        ,  c_i = ln(C_i / O_i)
σ²_RS      = 1/n · Σ [ ln(H_i/C_i)·ln(H_i/O_i) + ln(L_i/C_i)·ln(L_i/O_i) ]
k          = 0.34 / (1.34 + (n+1)/(n−1))
```

Neden bu tahminci: (a) sıfır olmayan sürüklenme (drift) altında yanlı değildir,
(b) gecelik fiyat sıçramalarını (gap) hesaba katar, (c) sadece kapanış kullanan tahminciye göre
aynı veriden daha az gürültüyle volatilite çıkarır. BIST için (b) kritik: seans arası boşluk büyüktür.

**Ölçüm penceresi seçimi — 2 aday:**

| Aday | Tanım | Artı | Eksi |
|---|---|---|---|
| **A1. Ölçek başına σ** | Her `n` için σ, o pencerenin kendi n barından hesaplanır | Her ölçek kendi rejimini görür | `n=3` için 3 gözlemli varyans → gürültü kabul edilemez; ayrıca cetvel ölçekten ölçeğe değişince "karşılaştırılabilirlik" iddiası çöker |
| **A2. Tek ortak σ** ✅ | Tüm ölçekler için σ, sabit `n_vol = 20` barlık pencereden hesaplanır | Tek cetvel → 6 ölçek gerçekten karşılaştırılabilir; 20 gözlem YZ için yeterli | 47 barlık pencerenin drift'i, son 20 barın volatilitesine bölünür — rejim değiştiyse ölçek kayar |

**Seçim: A2, `n_vol = 20`.** Gerekçe: izotropi iddiasının tamamı "hepsi aynı cetvelle ölçülüyor" varsayımına
dayanır; cetveli ölçekle birlikte değiştirirsek elde 6 bağımsız oy değil, 6 farklı birimde 6 sayı kalır.
A2'nin eksisi (rejim kayması) gerçektir ama yönlüdür ve ölçülebilir: `n_vol ∈ {20, 60}` duyarlılık testi
Görev D'ye ön-kayıtlı olarak yazıldı. `# VARSAYIM: V-01`

### 1.5 "Isotropic Coordinate System" ne demek — 2 okuma

Yazar eğim ölçümünün bir normalizasyondan geçtiğini söylüyor ama formülü vermiyor.
İzotropi = "her yönde aynı ölçek". Fiyat grafiğinde iki eksen vardır: zaman (bar) ve fiyat.
Bu iki ekseni aynı birime çekmenin iki yolu var:

| Aday | Formül | Yorum | Değerlendirme |
|---|---|---|---|
| **B1. Geometrik izotropi** | `egim = Δln(P) / (n · σ)` | "Bar başına kaç σ yol alındı" | Rastgele yürüyüşte `Δln(P) ~ σ√n` olduğundan bu büyüklük `1/√n` ile küçülür → uzun pencereler sistematik olarak küçük değer üretir, **her ölçek için ayrı eşik** gerekir. İzotropi iddiasını bozar. |
| **B2. İstatistiksel izotropi** ✅ | `z_n = Δln(P) / (σ · √n)` | "Bu hareket, saf tesadüfe göre kaç standart sapma?" | Sürüklenmesiz rastgele yürüyüşte `z_n ~ N(0,1)` — **her `n` için, her enstrüman için, her vade için aynı dağılım**. Tek eşik 6 ölçeğin hepsinde geçerli olur. |

> **↪ §11.2(a) ile güncellendi:** yazarın panelinde `X-Koordinat` / `Y-Koordinat` alanları görüldü →
> gerçek uygulama büyük olasılıkla **en-boy oranı (açı)** izotropisidir. Kararımız değişmedi, gerekçesi §11.2(a).

**Seçim: B2.** Gerekçe: 6 pencerenin "bağımsız oy" verebilmesi için oyların aynı ölçekte olması şart.
√n normalizasyonu bunu teorik olarak garanti eder (√n kuralı difüzyon ölçeklemesinden gelir, uydurma değil);
n normalizasyonu etmez. Not: gerçek getiriler kalın kuyrukludur ve otokorelasyonludur, dolayısıyla
`z ~ N(0,1)` tam doğru değildir — **yaklaşıktır**. Bu yüzden eşikler teoriden değil, geniş bir örneklemin
dağılımından kalibre edilecek (§2.3).

**Tanım (nihai):**
```
z_n(t) = ln(C_t / C_{t−n}) / ( σ_YZ(t, 20) · √n )
```

---

## 2. MODÜL A — TREND MOTORU (Yön)

### 2.1 Pencere seti: 3, 7, 13, 19, 29, 47

Yazarın gerekçesi: hepsi asal, hiçbiri diğerinin katı değil, "ölçekler bağımsız bilgi taşısın".
Eski sürümde 49 varmış, 49 = 7×7 olduğu için 47 ile değiştirilmiş.

**Dürüst değerlendirme (bu bizim yorumumuz, yazarın değil):** Asallık burada *matematiksel bir bağımsızlık
sağlamaz*. Örtüşen pencerelerin korelasyonu asallıkla değil, **örtüşme oranıyla** belirlenir:
`corr(z_3, z_7)` yaklaşık `√(3/7) ≈ 0.65`'tir çünkü 3 barlık pencere, 7 barlık pencerenin içindedir.
47 yerine 49 seçilse bu korelasyon 0.001 bile değişmezdi. Asallık, katsayı örtüşmelerinde
(örn. 7 ve 14 gibi tam kat pencerelerde ortaya çıkan periyodik rezonans) küçük bir fayda sağlar,
ama "bağımsız bilgi" iddiasını taşımaz. **Pencereler bağımsız değildir; hiyerarşiktir.**
Bu, tasarımı geçersiz kılmaz — sadece "6 bağımsız oy" cümlesini "6 iç içe ölçek" olarak düzeltir.
Sonucu: uyum skorunun `−6…+6` aralığı **üniform dağılmaz**; uçlara yığılır. Bu, Görev D'de ölçülecek.

Pencere setini **değiştirmiyoruz** — amaç yazarın fikrini test etmek, kendi setimizi uydurmak değil.

### 2.2 Oy metriği — 3 aday

Sözel tarif: *"o ölçekte mevcut hareket fırsatına göre ne kadar yol alındığı"*.

| Aday | Formül | Artı | Eksi |
|---|---|---|---|
| **C1. Kaufman verimlilik oranı (işaretli)** | `(C_t − C_{t−n}) / Σ\|C_i − C_{i−1}\|` | Sezgisel: "kat edilen yolun ne kadarı net" | [0,1] sınırlı → uç değer ayrımı yok; volatilite ile normalize edilmediği için enstrümanlar arası karşılaştırılamaz. **Bu, Modül B'nin (verimlilik) tanımıdır** — Modül A'da kullanılırsa iki modül aynı sayıyı ölçer |
| **C2. Aralığa göre net** | `(C_t − C_{t−n}) / (max H − min L)` | Gövde/aralık sezgisi, gapleri kapsar | Aynı sınırlılık sorunu; ayrıca payda tek bir uç bar tarafından belirlenir → dayanıksız |
| **C3. σ-normalize eğim (z)** ✅ | `ln(C_t/C_{t−n}) / (σ_YZ·√n)` | Sınırsız → kanaat için büyüklük bilgisi taşır; ölçek/enstrüman/vade bağımsız; yazarın "izotropik + Yang-Zhang" beyanıyla **birebir tutarlı** | Kalın kuyruk nedeniyle N(0,1) yaklaşıktır; σ=0 durumunda tanımsız (§7) |

**Seçim: C3.** Gerekçe iki katmanlı: (i) yazarın kendi beyanı (izotropik normalizasyon + Yang-Zhang)
sadece C3 ile tutarlıdır — C1/C2 volatilite tahmincisine ihtiyaç duymaz, o hâlde Yang-Zhang'ın orada işi olmazdı;
(ii) mimari gerekçe: Modül A **yön**, Modül B **kalite** ölçmelidir. C1'i A'ya koyarsak iki modül aynı şeyi
iki isimle ölçer ve "5 boyut" aslında 4 olur.

**Sağlamlık varyantı (ön-kayıtlı, Görev D):** uç noktadan-uç noktaya `Δln` yerine
pencere içindeki `ln(C)` serisine OLS doğru uydurup eğimi kullanmak. Tek bar gürültüsüne daha dayanıklı,
buna karşılık daha geç döner. İki varyant **aynı testte yan yana** koşulacak, sonradan seçilmeyecek. `# VARSAYIM: V-02`

### 2.3 Ölü bölge eşiği θ ve oy

```
oy_n(t) = +1   eğer z_n(t) >  +θ
          −1   eğer z_n(t) <  −θ
           0   aksi hâlde
```

θ = 0 seçilirse "0 oy" hiç oluşmaz ve yazarın "Gizli Eğilim / Belirgin Trend Yok" durumları tanımsız kalır.
θ > 0 bir **tasarım parametresidir** ve seçimi keyfîdir.

**Kalibrasyon kuralı (getiriye bakmadan):** θ, geniş bir referans örneklemde (BIST 800 hisse × 5 yıl, günlük)
`|z| ≤ θ` olan bar oranı **%25** olacak şekilde seçilir. Yani ölü bölge, tarihsel dağılımın ortadaki dörtte biridir.
Bu bir *dağılım* kalibrasyonudur, bir *kâr* kalibrasyonu değildir — getiri verisine hiç bakılmaz,
dolayısıyla aşırı uydurma (overfitting) riski taşımaz. Kalibrasyon **bir kez** yapılır ve **dondurulur**.
Ön-hesap değeri: `N(0,1)` altında bu θ ≈ 0.32 olurdu; gerçek kalın kuyruklu dağılımda muhtemelen daha küçük çıkar.
Gerçek değer kalibrasyonla belirlenecek. `# VARSAYIM: V-03`

### 2.4 Uyum skoru

```
S_t = Σ_{n ∈ {3,7,13,19,29,47}} oy_n(t)        →  S_t ∈ {−6, …, +6}
```

### 2.5 Kanaat (Conviction)

Yazar: *"Oylar sadece işaretle değil büyüklükle de ağırlıklanıyor → KANAAT: YÜKSEK / ORTA / DÜŞÜK."*

```
K_t = ( Σ_n |z_n(t)| ) / 6            # ortalama mutlak izotropik eğim
```

Alternatif olarak yalnızca **kazanan taraftaki** oyların büyüklüğü kullanılabilirdi
(`Σ_{oy_n = sign(S)} |z_n|`). Karşılaştırma:

| Aday | Artı | Eksi |
|---|---|---|
| **D1. Tüm ölçeklerin ortalama \|z\|** ✅ | Çelişkili ama şiddetli piyasada (kısa vade sert yukarı, uzun vade sert aşağı) yüksek kanaat verir — ki bu **doğru** bilgidir: ortada büyük bir hareket var | "Kanaat" kelimesi kullanıcıya "yön kanaati" gibi okunabilir; oysa bu "hareket şiddeti" |
| D2. Sadece çoğunluk yönündeki \|z\| | "Yön kanaati" olarak daha doğrudan okunur | Karşı taraftaki şiddeti görmezden gelir → tepe/dip dönüşlerinde yanıltıcı biçimde yüksek çıkar |

**Seçim: D1**, ama çıktıda ismi **`kanaat`** değil **`siddet`** (şiddet) alanı olarak da yayınlanır ve
`kanaat` etiketi bunun yüzdelik dilimidir. Böylece kullanıcıya giden kelime ile matematik ayrışır.

**Etiketleme:** `K_t`'nin kendi geçmişindeki (252 bar, §3.3 ile aynı kural) yüzdelik dilimi:
`≥ %75 → YÜKSEK`, `%25–75 → ORTA`, `< %25 → DÜŞÜK`. Sabit eşik yerine yüzdelik seçildi çünkü
`K` teorik olarak N(0,1)'in mutlak ortalamasına (≈0.8) yakın olsa da pratikte enstrümana göre kayar. `# VARSAYIM: V-04`

### 2.6 11 durum — öncelik merdiveni

Durumlar **birbirini dışlar**. Aşağıdaki sıra bağlayıcıdır: ilk eşleşen kazanır.
`S_t` = güncel skor, `S_{t−1}` = önceki bar skoru, `oy_29`, `oy_47` = iki geniş pencerenin oyu.

| # | Durum | Koşul |
|---|---|---|
| 1 | **Güçlü Yükseliş** | `S_t ≥ +5` |
| 2 | **Güçlü Düşüş** | `S_t ≤ −5` |
| 3 | **Dönüş Onaylandı ▲** | `S_t ≥ +3` **ve** `min(S_{t−1..t−3}) ≤ −3` |
| 4 | **Dönüş Onaylandı ▼** | `S_t ≤ −3` **ve** `max(S_{t−1..t−3}) ≥ +3` |
| 5 | **Dönüş Oluşuyor ▲** | `oy_3 = oy_7 = +1` **ve** `oy_29 = oy_47 = −1` |
| 6 | **Dönüş Oluşuyor ▼** | `oy_3 = oy_7 = −1` **ve** `oy_29 = oy_47 = +1` |
| 7 | **Gizli Eğilim ▲** | `\|S_t\| ≤ 1` **ve** `oy_29 = oy_47 = +1` |
| 8 | **Gizli Eğilim ▼** | `\|S_t\| ≤ 1` **ve** `oy_29 = oy_47 = −1` |
| 9 | **Yükseliş Aktif** | `S_t ≥ +3` |
| 10 | **Düşüş Aktif** | `S_t ≤ −3` |
| 11 | **Belirgin Trend Yok** | yukarıdakilerin hiçbiri |

Yazar 11 durum sayıyor ve isimlerini veriyor; **eşik sayıları ve öncelik sırası bize aittir.**
"Dönüş"ün 3 barlık geriye bakışla tanımlanması ve ±3/±5 eşikleri tasarım kararıdır. `# VARSAYIM: V-05`

Dikkat: 3 ve 4 numaralı kurallar `S_{t−1..t−3}`'e bakar — bunlar **geçmiş** barlardır, geleceğe bakış yoktur.

### 2.7 Modül A — çıktı sözleşmesi

| Alan | Tip | Aralık | Açıklama |
|---|---|---|---|
| `z_3 … z_47` | float | ℝ | Ölçek başına izotropik eğim (6 kolon) |
| `oy_3 … oy_47` | int8 | −1/0/+1 | Ölçek başına oy (6 kolon) |
| `uyum_skoru` | int8 | −6…+6 | `S_t` |
| `siddet` | float | ≥ 0 | `K_t` — ortalama \|z\| |
| `kanaat` | kategori | YÜKSEK/ORTA/DÜŞÜK | `K_t`'nin 252 barlık yüzdelik dilimi |
| `trend_durum` | kategori | 11 durumdan biri | §2.6 |
| `trend_yon` | int8 | −1/0/+1 | `sign(S_t)` (sadeleştirilmiş yön, Modül B/C için) |

---

## 3. MODÜL B — VERİMLİLİK MOTORU (Hareket Kalitesi)

### 3.1 Verimlilik metriği — 3 aday

Sözel tarif: *"Bir mum grubunun toplam hareket fırsatının ne kadarını net yöne çevirdiği."*

| Aday | Formül | Artı | Eksi |
|---|---|---|---|
| **E1. Kaufman ER (kapanış yolu)** | `(C_t − C_{t−n}) / Σ_{i}\|C_i − C_{i−1}\|` | Literatürde tanımlı, bilinen davranış (Kaufman, KAMA) | Payda yalnızca kapanıştan kapanışa yolu sayar → **bar içi hareketi ve gapleri görmez**. BIST'te seans arası boşluk büyükse "fırsat" olduğundan az sayılır |
| **E2. Aralığa göre net** | `(C_t − C_{t−n}) / (max H − min L)` | Bar içini kapsar, hesabı ucuz | Payda tek bir uç bar tarafından belirlenir (bir tavan günü tüm pencereyi bozar); ayrıca zikzak cezalandırılmaz — 20 kez inip çıkan bir seri ile düz giden bir seri aynı değeri alabilir |
| **E3. Gerçek aralık yolu (TR)** ✅ | `(C_t − C_{t−n}) / Σ_{i} TR_i` | Bar içi hareketi **ve** gapleri sayar; zikzağı cezalandırır; `\|değer\| ≤ 1` **matematiksel olarak garantilidir** çünkü `TR_i ≥ \|C_i − C_{i−1}\|` ve üçgen eşitsizliğiyle `Σ TR_i ≥ \|C_t − C_{t−n}\|` | Yüksek bar-içi oynaklığı olan hisselerde sistematik olarak daha düşük değer üretir (bu bir hata değil, doğru ceza) |

**Seçim: E3.** Gerekçe: "hareket fırsatı" ifadesinin en dürüst karşılığı, fiyatın gerçekten dolaştığı toplam yoldur —
kapanış-kapanış yolu bunun alt sınırıdır. Ayrıca `[−1, +1]` sınırının **kanıtlanabilir** olması,
yüzdelik dilime sokmadan önce sayının patolojik davranmayacağını garanti eder.

**İşaret:** metrik işaretli tutulur (`VER_n ∈ [−1, +1]`). Dilim hesabında **mutlak değeri** kullanılır
(`|VER_n|` — hareketin kalitesi), yön ayrıca `sign(VER_n)` olarak taşınır. Bu ayrım, §3.4'teki
"trendle aynı yön / karşı yön" ayrımını mümkün kılar.

### 3.2 "Çok mumlu bileşik" — 3 aday

Yazar tek mum gürültüsünü azaltmak için çok mumlu bileşik kullanıldığını söylüyor ama tanımlamıyor.

| Aday | Tanım | Artı | Eksi |
|---|---|---|---|
| **F1. Sentetik birleşik mum** | n barı tek mum yap (`O` = ilk açılış, `H` = max, `L` = min, `C` = son kapanış), verimlilik = `(C−O)/(H−L)` | Görsel sezgi güçlü ("gövde/fitil oranı") | Payda yine tek uç bara bağlı; ara yolu tamamen unutur → E2'nin bütün eksileri |
| **F2. Tek-bar veriminin hareketli ortalaması** | `mean_i( (C_i−O_i)/(H_i−L_i) )` | Gürültüyü gerçekten düşürür | **Netleşme (netting) özelliğini yok eder**: 3 gün +%3, 3 gün −%3 giden seri yüksek ortalama verim gösterir, oysa net yol sıfırdır. Verimlilik kavramının tanımına aykırı |
| **F3. n-barlık ER (E3 formülü)** ✅ | §3.1'deki `VER_n` — payda n barın toplam TR'si | Bileşikleştirme zaten formülün içinde: pay net yolu, payda toplam yolu ölçer; tek bar gürültüsü paydada erir | n küçükse (3) hâlâ gürültülüdür — bu yüzden 6 ölçekte birden ölçülür |

**Seçim: F3.** "Çok mumlu bileşik", ayrı bir ön-işlem adımı değil, metriğin kendisidir.
F1 ayrıca **raporlanır** (`bilesik_govde_orani`) çünkü kullanıcıya anlatırken güçlü bir görsel karşılığı var,
ama karar mantığına girmez. `# VARSAYIM: V-06`

**Hangi ölçek "verimlilik"tir?** Yazar tek bir verimlilik yüzdesinden söz ediyor.
6 ölçekte de hesaplıyoruz; **birincil** olarak `n = 19` (orta ölçek) yayınlanır, altısı da kolon olarak durur.
Gerekçe: 3 ve 7 tek olayla zıplar, 47 haftalar önceki bilgiyi taşır; 13/19 orta banttır ve 19,
trend motorunun da orta ölçeğidir → iki modül aynı zaman ufkundan konuşur. `# VARSAYIM: V-07`

### 3.3 Yüzdelik dilim (percentile) — sabit eşik yok

Yazarın açık beyanı: sabit eşik yok, değer enstrümanın kendi yakın geçmişine göre yüzdelik dilime oturtuluyor.

**Geriye bakış penceresi — 3 aday:**

| Aday | Artı | Eksi |
|---|---|---|
| **G1. 252 bar (günlükte ≈ 1 yıl)** ✅ | Bir tam mevsimsel döngü; 252 gözlem %5'lik dilimi ~12 gözlemle temsil eder (kabul edilebilir) | Rejim bir yıl içinde değişirse dilim geç uyum sağlar |
| G2. 60 bar | Rejime hızlı uyum | %5'lik dilim 3 gözleme düşer → "Aşırı" etiketi gürültüden ibaret olur |
| G3. Genişleyen pencere (tüm geçmiş) | Örneklem büyür, kararlı | Halka arz sonrası kısa geçmişte anlamsız; ayrıca 5 yıl önceki rejimi bugüne eşit ağırlıkla taşır |

**Seçim: G1 = 252 bar**, asgari 120 bar (altında yüzdelik üretilmez, `NaN` döner).
Yüzdelik **sadece geçmiş** kullanılarak hesaplanır: `dilim(t) = P(|VER| ≤ |VER_t| | son 252 bar, t dâhil, t+1 hariç)`.
Bu, geleceğe bakış yasağının en kolay ihlal edilen yeridir — vektörel uygulamada `rank(pct=True)` bütün seriye
uygulanırsa **sessizce gelecek sızar**. Uygulamada `rolling(252).apply(rank)` veya eşdeğeri kullanılacak,
test bunu doğrulayacak.

**Dilim eşikleri (yazarın verdiği isimlerle):**

| Dilim | Koşul | Etiket |
|---|---|---|
| Üst %5 | `dilim ≥ 0.95` | **Aşırı** |
| Üst %25 | `0.75 ≤ dilim < 0.95` | **Güçlü** |
| Orta %50 | `0.25 ≤ dilim < 0.75` | **Normal** |
| Alt %25 | `dilim < 0.25` | **Zayıf** |

Bu eşikler yazarın tarifinden **doğrudan** gelir (üst %5 / üst %25 / orta %50 / alt %25); varsayım değildir.

### 3.4 19 eşleşme durumu — trend × verimlilik matrisi

Yazar 19 durum ve 12 isim veriyor. 12 isim, 3 trend hâli × 4 dilim yapısına oturuyor ama 19 sayısını vermiyor.
**Yeniden inşa:** trend yönü ∈ {yukarı, aşağı, yok} × verimlilik yönü ∈ {trendle aynı, trende karşı} × 4 dilim.
- Trend yok → yön ayrımı anlamsız → **4 durum**
- Trend yukarı → aynı yön 4 + karşı yön 4 = 8
- Trend aşağı → 8
Toplam 20. Yazar 19 diyor. **Tek makul birleşme:** "karşı yön + Zayıf dilim" hücresi, trend yukarıda da
aşağıda da aynı şeydir (yönü belirsiz, şiddeti yok) — ikisi tek **Gürültü** durumunda birleşir. 20 − 1 = **19**. `# VARSAYIM: V-08`

| Trend | Verim yönü | Dilim | Durum |
|---|---|---|---|
| Yukarı | Aynı (+) | Aşırı | Boğa Dalgası |
| Yukarı | Aynı (+) | Güçlü | Teyitli Yükseliş |
| Yukarı | Aynı (+) | Normal | Aktif Yükseliş |
| Yukarı | Aynı (+) | Zayıf | Yükseliş Duraksıyor |
| Yukarı | Karşı (−) | Aşırı | Güçlü Karşı Hareket (boğaya) |
| Yukarı | Karşı (−) | Güçlü | Satış Baskısı (boğada) |
| Yukarı | Karşı (−) | Normal | Hafif Direnç (boğada) |
| Aşağı | Aynı (−) | Aşırı | Ayı Dalgası |
| Aşağı | Aynı (−) | Güçlü | Teyitli Düşüş |
| Aşağı | Aynı (−) | Normal | Aktif Düşüş |
| Aşağı | Aynı (−) | Zayıf | Düşüş Duraksıyor |
| Aşağı | Karşı (+) | Aşırı | Güçlü Karşı Hareket (ayıya) |
| Aşağı | Karşı (+) | Güçlü | Alış Baskısı (ayıda) |
| Aşağı | Karşı (+) | Normal | Hafif Direnç (ayıda) |
| Yukarı/Aşağı | Karşı | Zayıf | **Gürültü** (birleşik hücre) |
| Yok | — | Aşırı | Aşırı Hareket (yönsüz) |
| Yok | — | Güçlü | Güçlü Hareket (yönsüz) |
| Yok | — | Normal | Normal Hareket |
| Yok | — | Zayıf | Kararsız |

"Trend yok" = `trend_durum ∈ {Belirgin Trend Yok, Gizli Eğilim ▲/▼}` **veya** `|S_t| ≤ 2`.
"Aynı/karşı" = `sign(VER_19)` ile `trend_yon` karşılaştırması.

### 3.5 Verimlilik ivmesi

Yazar: *"kısa vadeli ortalama verimlilik ile uzun vadeli ortalamanın karşılaştırması → Hızlanıyor / Keskinleşiyor / Sabit / Zayıflıyor."*

```
kisa  = EMA(|VER_19|, 5)
uzun  = EMA(|VER_19|, 20)
oran  = kisa / uzun
```

| Durum | Koşul |
|---|---|
| **Hızlanıyor** | `oran ≥ 1.15` **ve** `σ_YZ` son 5 barda artıyor → daha çok hareket, daha çok verim |
| **Keskinleşiyor** | `oran ≥ 1.15` **ve** `σ_YZ` son 5 barda azalıyor → aynı yol, daha az gürültü |
| **Sabit** | `0.90 < oran < 1.15` |
| **Zayıflıyor** | `oran ≤ 0.90` |

5/20 EMA çiftleri ve 1.15/0.90 eşikleri **bize aittir** (yazar sayı vermiyor). `# VARSAYIM: V-09`
"Hızlanıyor" ile "Keskinleşiyor"un volatilite yönüyle ayrılması da bizim yorumumuzdur:
iki isim ancak farklı şeyler ifade ediyorsa iki isim olmayı hak eder.

### 3.6 Modül B — çıktı sözleşmesi

| Alan | Tip | Aralık | Açıklama |
|---|---|---|---|
| `ver_3 … ver_47` | float | [−1,+1] | Ölçek başına işaretli verimlilik |
| `verim` | float | [−1,+1] | Birincil (`ver_19`) |
| `verim_dilim` | float | [0,1] | 252 barlık yüzdelik (mutlak değer üzerinden) |
| `verim_etiket` | kategori | Aşırı/Güçlü/Normal/Zayıf | §3.3 |
| `verim_ivme` | kategori | Hızlanıyor/Keskinleşiyor/Sabit/Zayıflıyor | §3.5 |
| `bilesik_govde_orani` | float | [−1,+1] | F1 (yalnız rapor) |
| `verim_goreli` | float | ≈ 0–400 | `\|VER_19\| / ort(\|VER_19\|,252) × 100` — panelde görünen ölçek (§11.2b) |
| `trend_verim_durum` | kategori | 19 durumdan biri | §3.4 |

---

## 4. MODÜL C — HACİM ZEKÂSI (Piyasa Baskısı)

### 4.1 Alış/satış ayrıştırması — 3 aday ve dürüst hata payı

| Aday | Formül | Artı | Eksi |
|---|---|---|---|
| **H1. Geometri (kapanış konumu)** ✅ | `alis_orani = (C − L)/(H − L)`, `alis = V·oran`, `satis = V·(1−oran)`, `delta = V·(2·oran − 1)` | Tek bardan hesaplanır, her enstrümanda çalışır, ucuz. `2·oran − 1` ifadesi Chaikin'in CLV'sinin aynısıdır | **Emir akışı değildir.** Kapanışın nerede olduğu, alıcı-satıcı hacim dağılımı hakkında yalnızca *ima* taşır |
| H2. Tik kuralı (bar yönü) | `C_t > C_{t−1}` → tüm hacim alış | Basit | Bilgi kaybı devasa: bar içi dengeyi tamamen atar; yatay barlarda tanımsız |
| H3. Alt zaman dilimi (intrabar) | 15dk barları toplayıp sınıflandır | Gerçek dağılıma en yakın | **Bizde yok** (bkz. `docs/veri_kisitlari.md`): yfinance 15dk verisi ~59 gün, 1sa ~729 gün ile sınırlı; BIST için 5 yıllık günlük backtest'e intrabar sağlanamaz |

> **↪ §11.1 ile doğrulandı:** yazarın panelindeki `Toplam / Alış / Satış / Δ` sayıları bu formülle
> **birebir tutarlı** çıktı (`22.74 + 5.68 = 28.42`, `22.74 − 5.68 = +17.05`).

**Seçim: H1**, ve çıktı **ordinal** (sıralayıcı) olarak etiketlenir, "delta" olarak değil.

**Hata payı — kaynaklı:** Bar geometrisinden alış/satış ayrıştırmasının doğruluğu için yayımlanmış bir
karşılaştırma bulamadık. En yakın literatür **işlem düzeyinde** (her bir işlemi tek tek sınıflandırma,
üstelik alış/satış kotasyonu elde varken) sınıflandırma doğruluğunu ölçüyor:

- Odders-White (2000), NYSE TORQ verisiyle: Lee-Ready algoritması işlemlerin **~%85'ini** doğru sınıflandırıyor (≈%15 hata).
- Ellis, Michaely & O'Hara (2000), Nasdaq verisiyle: **%81.4** doğru (≈%18.6 hata).
- Lee & Radhakrishna (2000): **%93** doğru.

Bu üç sayı **bizim probleminizden daha kolay bir problem** içindir: onlarda tek tek işlemler ve kotasyonlar var.
Bizde tek bir günlük mumun 4 sayısı var. Dolayısıyla bar-geometrisi tahmincisinin doğruluğu
**yukarıdaki sayılardan daha iyi olamaz**, muhtemelen belirgin biçimde daha kötüdür — ama **bu ne kadar kötü
olduğunu bilmiyoruz ve uyduramayız.** Sonuç: `delta`'yı mutlak bir "kurumsal alım" kanıtı olarak sunmak
yanlış olur; sıralayıcı bir baskı göstergesi olarak kullanmak savunulabilir.
Görev D'de bunun **kendi başına** öngörü gücü ayrıca test edilecek.

### 4.2 Hacim yüzdelik dilimi ve yoğunluk bölgesi

Aynı 6 ölçekte, ölçek hacmi = `Σ V` (n bar). Her ölçeğin kendi 252 barlık geçmişine göre yüzdelik dilimi alınır;
**altı dilimin medyanı** hacim yoğunluk bölgesini verir (yazarın tarifi birebir bu).

| Bölge | Koşul (6 dilimin medyanı) |
|---|---|
| **Patlayıcı** | `≥ 0.95` |
| **Yüksek** | `0.75 ≤ · < 0.95` |
| **Orta** | `0.25 ≤ · < 0.75` |
| **Çok Düşük** | `< 0.25` |

Geriye bakış penceresi §3.3 ile aynı (252/asgari 120) — tutarlılık için. `# VARSAYIM: V-10`

**BIST'e özel düzeltme (zorunlu):** arefe (yarım) günlerde seans 480 dk yerine 150 dk'dır
(`bist_calendar.NORMAL_SESSION_MINUTES = 480`, `AREFE_SESSION_MINUTES = 150`).
Ham hacmi düzeltmeden yüzdelik dilime sokmak, her arefe gününü sahte "Çok Düşük" yapar.
Kural: günlük veride `V_düzeltilmiş = V / get_rvol_day_factor(gün)`. Bu, SMR'de zaten var olan katsayıdır
(0.3125), uydurma değil.

### 4.3 Ayak izi ve POC konsensüsü

Her ölçek `n` için o pencerede hacim-fiyat profili çıkarılır ve `POC_n` = en çok hacmin yığıldığı fiyat.
Hacim, mumun `[L, H]` aralığına **orantısal** dağıtılır (SMR'nin `indicators.calculate_volume_profile_poc`
fonksiyonundaki yaklaşımın aynısı — yeniden icat etmiyoruz, aynı mantığı vektörel yazıyoruz).

**Bin sayısı:** sabit bin sayısı, uzun pencerede çözünürlüğü düşürür.
Kural: `bins = clip(round(n × 0.75), 10, 40)` → n=3'te 10, n=47'de 35. `# VARSAYIM: V-11`

**"Aynı seviyeye yığılma" toleransı — 3 aday:**

| Aday | Tanım | Değerlendirme |
|---|---|---|
| I1. Tick / kuruş | Sabit mutlak fark | BIST'te 3 TL'lik hisse ile 300 TL'lik hisse aynı toleransı paylaşamaz. **Elenir** |
| I2. Fiyatın yüzdesi | `\|POC_a − POC_b\| / P < %1` | Basit, ölçek bağımsız; ama volatil hissede %1 gürültü, sakin hissede %1 uçurum |
| **I3. ATR'nin katı** ✅ | `\|POC_a − POC_b\| < 0.5 × ATR_14` | Toleransı enstrümanın kendi günlük hareket ölçüsüne bağlar — sakin hissede dar, volatilde geniş. §1.4'teki "ortak cetvel" felsefesiyle tutarlı |

**Seçim: I3**, katsayı `0.5`. Konsensüs = 6 POC'tan kaç tanesi, en kalabalık kümenin içinde
(küme merkezi ± 0.5·ATR). `poc_konsensus ∈ {1..6}`. `# VARSAYIM: V-12`

Ayrıca yayımlanan alanlar: `poc_3 … poc_47`, `poc_merkez` (en kalabalık kümenin hacim-ağırlıklı ortası),
`poc_uzaklik_atr` (`(C − poc_merkez)/ATR_14`).

### 4.4 S1–S19 senaryo sınıflandırması

Yazar 19 senaryonun **isimlerini** veriyor, **tetik koşullarını vermiyor.**
Aşağıdaki koşullar tamamen bizim yeniden inşamızdır — isimlerin anlamından türetildi. `# VARSAYIM: V-13`
Senaryolar birbirini dışlar, sıra bağlayıcıdır (ilk eşleşen kazanır).

Kısaltmalar: `T` = `trend_yon`, `Z` = hacim bölgesi, `D` = `sign(delta)` ve `d` = delta'nın 252 barlık
mutlak yüzdelik dilimi, `E` = `verim_etiket`, `poz` = kapanışın bar içi konumu `(C−L)/(H−L)`,
`kd` = kümülatif 5 barlık delta yönü.

| # | Senaryo | Koşul |
|---|---|---|
| S13 | Satış Kapitülasyonu | `Z = Patlayıcı` ve `E = Aşırı` ve `T = −1` ve `poz ≥ 0.66` (uzun alt fitil) |
| S14 | Alış Tükenmesi | `Z = Patlayıcı` ve `E = Aşırı` ve `T = +1` ve `poz ≤ 0.33` (uzun üst fitil) |
| S9 | Hacim Uyumsuzluğu | Fiyat 20 barın yeni zirvesi/dibi **ve** `D` ters yönde **ve** `d ≥ 0.60` |
| S7 | Kırılım Adayı | `Z ≥ Yüksek` ve `E ≥ Güçlü` ve fiyat 20 barlık değer alanı (VAH/VAL) dışına çıkmış |
| S1 | Tam Alış/Satış Onayı | `Z ≥ Yüksek` ve `E ≥ Güçlü` ve `D = T` ve `d ≥ 0.75` ve `\|S_t\| ≥ 4` |
| S4 | Büyük Karşı Hacim | `Z ≥ Yüksek` ve `D = −T` ve `d ≥ 0.75` |
| S11 | Talep Emilimi | `Z ≥ Yüksek` ve `D = +1` ve `E = Zayıf` (hacim var, fiyat gitmiyor) |
| S12 | Arz Dağıtımı | `Z ≥ Yüksek` ve `D = −1` ve `E = Zayıf` |
| S18 | Yüksek Hacimli Çekişme | `Z = Patlayıcı` ve `d ≤ 0.25` (delta dengeli) ve `E = Zayıf` |
| S5 | Baskı Artıyor | `d` son 5 barda artıyor **ve** hacim dilimi son 5 barda artıyor **ve** `D = −T` |
| S17 | Baskı Birikiyor | `kd` 5 bar boyunca tek yönde **ve** `E = Zayıf` (henüz kırılım yok) |
| S15 | Sessiz Toplama | `Z ≤ Orta` ve `kd = +1` (5 bar) ve `\|S_t\| ≤ 2` |
| S16 | Sessiz Dağıtım | `Z ≤ Orta` ve `kd = −1` (5 bar) ve `\|S_t\| ≤ 2` |
| S10 | Adil Değer Mıknatısı | `\|poc_uzaklik_atr\| ≤ 0.5` ve `poc_konsensus ≥ 4` ve `Z = Orta` |
| S8 | Ölü Piyasa | `Z = Çok Düşük` ve `E = Zayıf` ve `\|S_t\| ≤ 1` |
| S2 | Sağlıklı Destek | `T ≠ 0` ve `D = T` ve `Z = Orta` ve `E ≥ Normal` |
| S3 | Zayıf Destek | `T ≠ 0` ve `D = T` ve `Z = Çok Düşük` |
| S6 | Normal Düzeltme | `T = +1` ve son bar negatif ve `Z ≤ Orta` ve `d ≤ 0.5` |
| S19 | Dengeli Akış | Hiçbiri (varsayılan) |

**Neden bu sıra:** önce nadir ve keskin olaylar (kapitülasyon, tükenme, uyumsuzluk), sonra hacim-yön teyitleri,
en sonda yavaş/pasif durumlar ve varsayılan. Aksi sıra, "Dengeli Akış"ın her şeyi yutmasına yol açar.

### 4.5 Modül C — çıktı sözleşmesi

| Alan | Tip | Açıklama |
|---|---|---|
| `hacim_gecerli` | bool | `Volume` kullanılabilir mi (FX'te False) |
| `alis_hacim`, `satis_hacim` | float | H1 ayrıştırması |
| `delta`, `delta_dilim` | float | `alis − satis` ve 252 barlık mutlak dilimi |
| `hacim_dilim_3 … _47` | float | Ölçek başına hacim yüzdelik |
| `hacim_bolge` | kategori | Patlayıcı/Yüksek/Orta/Çok Düşük (6 dilimin medyanı) |
| `poc_3 … poc_47`, `poc_merkez` | float | Ayak izi kontrol noktaları |
| `poc_konsensus` | int8 | 1..6 |
| `poc_uzaklik_atr` | float | Fiyatın POC merkezine ATR cinsinden uzaklığı |
| `hacim_senaryo` | kategori | S1..S19 (§4.4) |

---

## 5. MODÜL D — TRADE FINDER (Baskı Sentezi)

### 5.1 Beş boyutun ortak ölçeğe indirgenmesi

Beş boyut: trend, kanaat, verimlilik, momentum, hacim. Her biri `[−1, +1]` aralığına çekilir:

| Boyut | Ham kaynak | Ortak ölçeğe çevirme |
|---|---|---|
| `b_trend` | `S_t` | `S_t / 6` |
| `b_kanaat` | `K_t` dilimi | `2·dilim(K_t) − 1` — yönsüz büyüklük, `sign(S_t)` ile çarpılır |
| `b_verim` | `VER_19` | doğrudan (zaten `[−1,+1]`) |
| `b_momentum` | `z_7 − z_19` | `tanh(z_7 − z_19)` — kısa ölçeğin orta ölçeğe göre ivmesi |
| `b_hacim` | `delta_dilim` × `sign(delta)` | `sign(delta) · (2·delta_dilim − 1)`; `hacim_gecerli = False` ise **NaN** ve boyut düşürülür |

`b_momentum` tanımı bize aittir — yazar "momentum"u ayrı boyut sayıyor ama tanımlamıyor.
Trend motorunun içindeki iki ölçek farkı olarak tanımlamak, yeni bir gösterge eklemeden
"ivme" bilgisini üretir; alternatifi klasik RSI/MACD eklemekti, o da mevcut SMR ile örtüşürdü. `# VARSAYIM: V-14`

### 5.2 İki toplama modu

**Ensemble (eşit ağırlık, taşımasız):**
```
P_ens(t) = mean( b_trend, b_kanaat, b_verim, b_momentum, b_hacim )      # NaN'ler atlanır
```
Önceki barlardan hiçbir taşıma yok → hızlı fikir değiştirir.

**Sequential (kapılı):** her aşama geçerse bir sonraki sayılır.
```
g1 = 1 if |b_trend|    ≥ 0.34 else 0        # en az 2 oy fazlası
g2 = 1 if dilim(K_t)   ≥ 0.50 else 0
g3 = 1 if |b_verim|    ≥ 0.25 else 0
g4 = 1 if sign(b_momentum) = sign(b_trend) else 0
g5 = 1 if (NaN(b_hacim)) or (sign(b_hacim) = sign(b_trend)) else 0

P_seq(t) = sign(b_trend) · (g1·g2·g3·g4·g5) · mean(|b_*| geçerli olanlar)
```
Bir kapı kapanırsa skor **tam sıfır** olur → çok daha durağan, çok daha az sinyal.
Kapı eşikleri bize aittir. `# VARSAYIM: V-15`

> **↪ §11.2(c):** panelde skor `±100` ölçeğinde (`+94`) ve ayrıca `5 / 5` boyut uyum sayacı var.
> Çıktıya `baski_ens_100`, `baski_seq_100`, `boyut_uyum` alanları eklendi.

İki mod da **ayrı kolon** olarak yayınlanır (`baski_ens`, `baski_seq`). Hangisinin daha iyi olduğu
Görev D'de ölçülecek; **şimdi seçilmeyecek**.

### 5.3 Adaptif histerezis

Yazarın tarifi: yön değiştirme eşiği mevcut rejime bağlı; karşı okuma hem eşiği hem de yerini almaya
çalıştığı rejimi geçmeli; rejim zayıfladıkça devrilmesi kolaylaşıyor.

```
R_t = EMA( |P_{t}| · 1[sign(P_t) = rejim_t] , span = 10 )     # yürürlükteki rejimin gücü
esik(t) = τ0 + λ · R_{t−1}

rejim_t = −rejim_{t−1}   eğer  sign(P_t) ≠ rejim_{t−1}  ve  |P_t| > esik(t)
          rejim_{t−1}    aksi hâlde
```
`τ0 = 0.20` (taban eşik), `λ = 0.60` (rejim direnci). `# VARSAYIM: V-16`

**Neden işe yarar (ve neyi kaybettirir):** Yatay piyasada `P` sıfır etrafında salınır, rejim gücü `R` düşer,
eşik `τ0`'a yaklaşır — ama `P` de küçük olduğundan devrilme olmaz → **zıplama azalır**.
Güçlü trendde `R` büyür, eşik yükselir → gerçek dönüşün kaydedilmesi **geç kalır**.
Bu bir tercih, bir üstünlük değil: sinyal sayısını düşürmenin bedeli gecikmedir.
Görev D'nin 3. hipotezi tam olarak bunu ölçer.

### 5.4 Konfluens işareti

```
konfluens(t) = 1  eğer  beş boyutun geçerli olanlarının hepsi aynı işaretli
                  ve  |P_ens(t)| ≥ 0.50
                  ve  rejim_t = sign(P_ens(t))
```
`0.50` eşiği bize aittir. `# VARSAYIM: V-17`

---

## 6. MODÜL E — MEM (Master Evaluation Matrix) ve osilatörü

```
MEM_t = 100 · ( w1·b_trend + w2·b_kanaat + w3·b_verim + w4·b_momentum + w5·b_hacim )
```
Başlangıç ağırlıkları **eşit** (`w = 1/5` her biri, geçerli boyutlar arasında yeniden normalize edilir).
Eşit ağırlık bilinçli bir seçimdir: ağırlıkları veriye bakarak ayarlamak, henüz tek bir backtest bile
yokken aşırı uydurmanın kestirme yoludur. Ağırlık değişikliği ancak Görev D sonuçlarından sonra,
ön-kayıtlı bir kuralla yapılabilir.

**Osilatör (fiyattan değil MEM'den):**
```
MEM_hizli  = EMA(MEM, 12)
MEM_yavas  = EMA(MEM, 26)
MEM_macd   = MEM_hizli − MEM_yavas
MEM_sinyal = EMA(MEM_macd, 9)
MEM_hist   = MEM_macd − MEM_sinyal
```
12/26/9 klasik MACD parametreleridir; yazar da "MACD çizgisi, sinyal çizgisi ve histogram" diyor.
Bu bir varsayım değil, adı geçen göstergenin standart tanımıdır — ama **MEM üzerinde** uygulanması
onu klasik MACD'den farklı bir şey yapar ve klasik MACD sezgileri buraya taşınamaz.

---

> **Not:** Panelden keşfedilen **Modül F — Sinyal Sağlığı** §11.2(d)'de tanımlandı.

## 7. KENAR DURUMLARI

Her satır bir kural. "Sessizce geç" hiçbir yerde yok — ya değer üretilir ya `NaN` + bayrak.

| Durum | Nasıl tespit edilir | Davranış |
|---|---|---|
| **`High == Low` (doji/tavan-taban kilit)** | `H − L < 1e-12` | `poz` (bar içi konum) tanımsız → `0.5` kabul edilir (nötr), `delta = 0`. `TR` yine hesaplanır (gap payı sıfır olmayabilir). Bar sayılır, atılmaz |
| **`Volume = 0`** | — | O bar hacim profiline **katkı vermez**; hacim dilimine 0 olarak girer. Ardışık ≥ 3 sıfır hacim → `hacim_gecerli = False` (o pencere için) |
| **`Volume` tamamen NaN/0 (FX)** | Serinin > %90'ı | `hacim_gecerli = False`, Modül C çıktılarının tamamı `None`, Modül D 4 boyutla çalışır |
| **Tavan/taban kapanışı (BIST ±%10)** | `\|C_t/C_{t−1} − 1\| ≥ 0.095` **ve** `H == L` | `verim` bu barda 1.0'a yapışır (doğru), ama `poz` yapay olarak nötrlenir. Bar `tavan_taban = True` ile işaretlenir; Görev D'de bu barlar ayrı alt-örneklem olarak raporlanır |
| **Bölünme/temettü sıçraması** | `\|r_t\| > 5·σ_YZ` **ve** `V_t < 0.5 × medyan(V)` | Motor **düzeltmez** — düzeltme veri katmanının işidir. Bar `supheli_sicrama = True` ile işaretlenir; `z_n` hesapları etkilenir, bu bayrakla filtrelenebilir |
| **Halka arz sonrası kısa geçmiş** | `len(df) < 168` | Yüzdelik dilim gerektiren tüm alanlar `NaN`; `z`/`ver` alanları asgari bar sayısı sağlanan ölçekler için üretilir. Motor **çalışır ama eksik döner**, hata vermez |
| **Seans arası boşluk / tatil** | index'te takvim boşluğu | Motor takvimi **umursamaz**, bar sayısıyla çalışır. YZ'nin "gecelik" bileşeni boşluğu zaten yakalar. Uzun tatiller `σ_gece`'yi şişirir → `z` küçülür (muhafazakâr yönde hata) |
| **Hayalet bar (V=0 tatil barı)** | `V = 0` ve `O=H=L=C` | Girdi sözleşmesi ihlali sayılmaz ama `hayalet = True` işaretlenir; SMR'nin `_strip_holiday_bars` mekanizması bunları zaten temizliyor |
| **`σ_YZ = 0`** | 20 barın tamamı sabit fiyat | `z_n = 0`, `kanaat = DÜŞÜK`, `trend_durum = Belirgin Trend Yok`. Sıfıra bölme yok |
| **`σ²_RS < 0`** | Sayısal yuvarlama | `max(0, ·)` ile kırpılır; YZ toplamı yine negatifse `NaN` |
| **Tekrarlı index** | `index.duplicated().any()` | **Hata fırlatılır** — sessiz düzeltme yapılmaz, çünkü tekrar eden tarih veri katmanı bug'ıdır ve gizlenmemelidir |

---

## 8. MİNİMUM VERİ GEREKSİNİMİ (hesaplandı)

| Bileşen | Gereken bar |
|---|---|
| `σ_YZ` (20 bar + gecelik için 1 önceki kapanış) | 21 |
| `z_47` / `ver_47` (47 barlık pencere + 1) | 48 |
| Yüzdelik dilim (252 gözlem, her biri 48 bar geriye uzanan) | 252 |

- **Tam güç:** `48 + 252 = 300` bar. `karar[t]` şeması t barını da kullandığı için ilk tam güçlü satır 300. indekstedir → **en az 300 bar** gerekir. Emniyet payıyla **301**.
- **Asgari (dilim penceresi 120'ye düşürülmüş, düşük güven):** `48 + 120 = 168` bar.
- **168 barın altında:** yüzdelik gerektiren her alan `NaN`; Modül A'nın `z`/`oy`/`uyum_skoru` alanları 48 bardan itibaren üretilir, ama `kanaat`, `verim_etiket`, `hacim_bolge`, `hacim_senaryo`, `baski_*`, `MEM` **üretilmez**.

**BIST'te bu ne kadar takvim süresi eder** (kaynak: `bist_calendar.py` — normal seans 10:00–18:00 = 480 dk, arefe 150 dk):

| Vade | Bar / işlem günü | 47 bar = | 301 bar = |
|---|---|---|---|
| Günlük | 1 | 47 işlem günü ≈ **2.2 takvim ayı** | 301 işlem günü ≈ **14 takvim ayı** |
| 4 saat* | 2 | 23.5 işlem günü ≈ **1.1 ay** | 150 işlem günü ≈ **7 ay** |
| 1 saat | 8 | 5.9 işlem günü ≈ **1.2 hafta** | 37.6 işlem günü ≈ **1.8 ay** |
| 15 dakika | 32 | 1.5 işlem günü | 9.4 işlem günü ≈ **2 hafta** |

\* 4 saat **Yahoo'da yerel bir vade değil** (geçerli aralıklar: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo).
1 saatlik veriden yeniden örneklenmelidir ve örnekleme başlangıcı 10:00'a sabitlenmelidir, yoksa günde 2 yerine 3 bar oluşur.
**Uyarı — mevcut kodda bulundu:** `analysis_core.py:855` satırında `interval="4h"` ile veri isteniyor;
bu geçerli bir Yahoo aralığı olmadığı için istek büyük olasılıkla başarısız oluyor ve `try/except` içinde
sessizce yutuluyor → çok-vadeli uyum matrisinden **4H satırı hiç gelmiyor** olabilir.
Bu bir bulgudur, düzeltme değil — dokunmadım, Görev B'de ESKİ/YENİ önerisi olarak sunulacak.

Arefe günleri bar sayısını bozar (150 dk → 15dk'da 10 bar, 1sa'te 2-3 bar). Bar-sayısıyla çalışan motor
bunu görmez; vade karşılaştırmasında (Görev D) arefe günleri ayrıca işaretlenmelidir.

---

## 9. BİLİNEN ZAYIFLIKLAR

### 9.1 Durum patlaması ve çoklu karşılaştırma

11 trend durumu × 19 verim durumu × 19 hacim senaryosu = **3.971 hücre**.

- BIST'te 800 hisse × 5 yıl günlük ≈ 1.000.000 bar-gözlem. Hücre başına **ortalama ~250** gözlem.
- Ama dağılım düz değil, uçlara yığılır: "Dengeli Akış + Normal + Belirgin Trend Yok" hücresi
  on binlerce gözlem alırken, "Kapitülasyon + Boğa Dalgası + Dönüş Onaylandı" hücresi **onlu sayılarda** kalır.
- **Tehlike:** 3.971 hücreyi %5 anlamlılıkla test edersek, hiçbir gerçek etki yokken bile
  **beklenen ~199 hücre "anlamlı" çıkar.** İçlerinden en iyi görüneni seçip "kanıt bulduk" demek,
  saf gürültüyü kanıt sanmaktır.
- **Zorunlu karşı önlem:** ya Bonferroni (`α = 0.05/3971 = 1.26e-5` → iki yönlü testte |z| ≈ **4.37** gerekir),
  ya Benjamini-Hochberg yanlış keşif oranı kontrolü. Görev D'de hangisi, hangi hipotez ailesine uygulanacak yazıldı.
- **Pratik sonuç:** 3.971 hücrenin tamamını test etmek anlamsızdır. Test edilecek hücreler **önceden**,
  az sayıda ve gerekçeli seçilmelidir (Görev D'nin ön-kayıtlı hipotez listesi).

### 9.2 Hacmi olmayan enstrümanlar

- **FX (`EURUSD=X` vb.):** Yahoo hacim vermez (0 veya NaN). Modül C tamamen anlamsızdır.
  "Hacim çok düşük" etiketi burada **sahte sinyaldir**. Motor bu yüzden `hacim_gecerli` bayrağı taşır ve
  Modül D boyut sayısını düşürür.
- **Vadeli/continuous emtia (`GC=F` vb.):** SMR'nin kendi geçmişinde bu tam olarak yaşandı —
  Yahoo continuous sembolü gerçek hacmin binde birini veriyordu (CLAUDE.md, Oturum 23-A).
  Yani hacim "var" görünüp yanlış olabilir. Motor bunu tespit edemez; veri katmanının işidir.
- **Endeksler (XU100):** endeks hacmi ya yok ya hesap ürünüdür. Modül C endekste kullanılmamalıdır.

### 9.3 Geometri ile alış/satış ayrıştırması bir tahmindir, delta değildir

§4.1'de kaynaklarıyla açıklandı. Özet: elimizdeki en yakın literatür **işlem düzeyinde, kotasyon verisiyle**
%81–93 doğruluk bildiriyor; bizim problemimiz (tek mumun 4 sayısından çıkarım) bundan **kesinlikle daha zordur**
ve bar-geometrisi için yayımlanmış bir doğruluk ölçümü bulunamadı. Dolayısıyla:
- `delta` **sıralayıcı** (bu bar geçen bardan daha alıcılı mı) olarak kullanılabilir,
- `delta` **mutlak** ("bugün 4.2 milyon lot kurumsal alım geldi") olarak **kullanılamaz**,
- ve Görev D'de `delta`'nın tek başına öngörü gücü, diğer boyutlardan bağımsız olarak ölçülmelidir.
  Ölçülmeden AI metinlerine "kurumsal alım" cümlesi olarak girmemelidir.

### 9.4 Ölçekler bağımsız değil

§2.1'de açıklandı: 6 pencere iç içedir, `corr(z_3, z_7) ≈ √(3/7)`. "6 bağımsız oy" ifadesi yanlıştır.
Sonuç: `uyum_skoru = ±6` göründüğü kadar nadir değildir ve "6 farklı kaynak aynı şeyi söylüyor" sezgisi
istatistiksel olarak desteklenmez. Skorun gerçek dağılımı Görev D'de ölçülüp raporlanacak.

### 9.5 Yazarın kendi beyanı: doğrulama yok

Yayımlanmış hiçbir backtest, kazanma oranı, örneklem büyüklüğü yok. Bu bir suçlama değil —
yazar zaten "al/sat sinyali değil, durum tarifi" diyor. Ama bizim açımızdan anlamı net:
**bu mimarinin kenarı olduğuna dair elimizde sıfır kanıt var.** Görev C'nin prototipi bir *hipotez üreticisidir*,
bir strateji değil. Görev D bitmeden hiçbir çıktısı AI metnine, panele veya bota girmemelidir.

---

## 10. KAYNAKÇA

- Yang, D. & Zhang, Q. (2000), "Drift-Independent Volatility Estimation Based on High, Low, Open, and Close Prices", *Journal of Business* 73(3). Formül ve `k` sabiti doğrulandı.
- Kaufman, P. — Efficiency Ratio / KAMA tanımı: net değişimin, dönem içi bar-bar toplam değişime oranı.
- Odders-White, E. (2000), "On the occurrence and consequences of inaccurate trade classification", *Journal of Financial Markets* — Lee/Ready ≈ %85 doğru.
- Ellis, K., Michaely, R. & O'Hara, M. (2000), "The Accuracy of Trade Classification Rules: Evidence from Nasdaq", *JFQA* — %81.4 doğru.
- Lee, C. & Radhakrishna, B. (2000) — %93 doğru (farklı örneklem/tanım).
- Lee, C. & Ready, M. (1991) — algoritmanın kendisi.
- yfinance geçerli aralıklar ve geçmiş sınırları: 1m ≈ 7 gün, 15m/30m ≈ 59 gün, 1h ≈ 729 gün; `4h` geçerli aralık **değil**.
- `bist_calendar.py` (bu depo): normal seans 480 dk (10:00–18:00), arefe 150 dk (10:00–12:30), arefe hacim katsayısı 0.3125.

---

## 11. KAMUYA AÇIK EKRAN GÖRÜNTÜSÜNDEN GELEN DÜZELTMELER

**Kaynak:** Yazarın kendi pazarlama görseli (ürün ekran görüntüsü + tanıtım metni). Kod değil, panel çıktısı.
**Okuma uyarısı:** Sayılar bir ekran görüntüsünden gözle okundu; birkaç hane yanlış okunmuş olabilir.
Aşağıdaki her satır "gözlem → ne doğruladı / ne değiştirdi" biçimindedir.

### 11.1 Doğrulananlar (spec değişmiyor)

| Gözlem (panelde yazan) | Neyi doğruluyor |
|---|---|
| `HACİM · Geometri` başlığı, `Toplam 28.42M · Alış 22.74M · Satış 5.68M · ▲ +17.05M` | §4.1'deki **H1 (geometri)** seçimi birebir doğru. Sayılar iç tutarlı: `22.74 + 5.68 = 28.42` ve `22.74 − 5.68 = 17.06 ≈ +17.05`. Yani panel de `alış = V·oran`, `satış = V·(1−oran)`, `delta = alış − satış` kuruyor. Buradaki alış oranı `22.74/28.42 = 0.800`. Ayrıca başlığın "Geometri" demesi, intrabar yönteminin **seçenek** olduğunu ve varsayılanın geometri olduğunu gösteriyor |
| `HACİM ATOM` tablosunda satırlar `3 / 7 / 13 / 19 / 29 / 47` | Aynı 6 ölçek hacimde de kullanılıyor — §4.2 doğru |
| Tablonun sağ sütunundaki ölçek başına tek fiyat: `2.64 · 2.54 · 2.55 · 2.53 · 2.52 · 2.52` ve yanında kümülatif hacim | Bunlar §4.3'teki **ölçek başına POC**. Ölçek büyüdükçe hacim büyüyor (8.1k → 32.7M), yani pencere kümülatifi |
| `HACİM ANALİZİ` satırında `5 / 6 Ölçek Uyumu` | §4.3'teki **`poc_konsensus ∈ {1..6}`** birebir doğru. Üstelik sayılar tutuyor: `{2.52, 2.52, 2.53, 2.54, 2.55}` bir küme (genişlik 0.03), `2.64` dışarıda → **5/6**. Bizim `0.5×ATR` toleransımız 2.72 TL'lik bir hissede makul ATR aralığında (≈0.05–0.20 TL) tam olarak bu ayrımı üretir. Bu bir *kanıt* değil, ama toleransın büyüklük mertebesi doğru |
| `Uyum: +6` + `YÜKSEK` + `Güçlü Yükseliş Trendi` | §2.4 uyum skoru, §2.5 kanaat, §2.6 durum isimleri doğru |
| `Keskinleşiyor` etiketi | §3.5'teki dört ivme etiketinden biri — liste doğru |
| `✓ Tam Alış Onayı` + `● Patlayıcı Hacim` | S1 senaryosu ve hacim bölgesi **ayrı** alanlar (§4.5 çıktı sözleşmesi böyle kurulmuştu) |
| Anlatı: *"Hacim, yakın geçmişin en üst diliminde"* | §3.3/§4.2'deki **yüzdelik dilim** yaklaşımı doğru; sabit eşik yok |
| Grafikte pembe ▼ / yeşil ▲ üçgenler | §5.4 konfluens işareti |
| Tanıtım metni: *"7 dilde"* | Anlatı katmanı **şablon tabanlı** (kural → cümle), yapay zekâ değil. SMR'nin AI anlatı katmanıyla doğrudan rakip değil — Görev B'de not edilecek |

### 11.2 Değişiklik gerektirenler

**(a) `X-Koordinat` / `Y-Koordinat` — §1.5'i genişletiyor.**
Panelin en üst satırında `X-Koordinat 11.7307` ve `Y-Koordinat 1` yazıyor. Yani "isotropic coordinate system"
soyut bir laf değil, ekranda **iki eksen ölçek katsayısı** olarak yayınlanıyor. Bu, §1.5'e üçüncü bir aday ekler:

| Aday | Formül | Değerlendirme |
|---|---|---|
| **B3. En-boy oranı izotropisi** | Zaman ekseni `X` bar, fiyat ekseni `Y` fiyat birimi = **aynı görsel uzunluk**. Eğim bir **açı**: `açı = atan( (Δln P / Y) / (n / X) )` | Yazarın uygulaması büyük olasılıkla budur (ekranda X ve Y ayrı ayrı yayınlanıyor). Enstrümanlar arası karşılaştırmayı çözer. **Ama ölçekler arası çözmez:** `Y` tek bir sayıysa, per-bar eğimin varyansı `1/√n` ile küçülür → tek bir açı eşiği kısa pencereleri kayırır, uzun pencereler neredeyse hiç oy vermez |

**Kararımız değişmiyor ama genişliyor:** oy mantığı §1.5-B2 (`z = Δln P / (σ·√n)`) üzerinde kalır —
çünkü 6 ölçeğin **eşit söz hakkı** olması bizim için tasarım şartıdır. Buna ek olarak, yazarın ekrandaki
diline karşılık gelsin diye **açı gösterimi de yayınlanır**: `aci_n = degrees(atan(z_n / √n · X/Y))` yerine
sadeleştirilmiş `aci_n = degrees(atan(z_n))` — tek bir monoton dönüşüm, karar mantığına girmez, sadece rapor.
Hangi normalizasyonun daha dengeli oy dağılımı ürettiği (uzun pencerelerin oy oranı) Görev D'de **ölçülecek**;
sonradan seçilmeyecek, iki varyant yan yana koşacak. `# VARSAYIM: V-18`

**(b) Verimlilik ekranda `[0,1]` değil, `251.15` — §3.6'ya alan ekliyor.**
Panelde `⚡ BOĞA DALGASI ▲ 251.15 Keskinleşiyor` yazıyor. 251.15 bir fiyat değil (hisse 2.72 TL),
bir yüzdelik dilim de değil. En makul okuma: verimliliğin **kendi ortalamasına göre** yüzde cinsinden hâli
(≈ normalin 2.5 katı). Spec'e rapor amaçlı alan eklenir, karar mantığı değişmez:
```
verim_goreli = |VER_19| / ortalama(|VER_19|, 252 bar) × 100
```
`# VARSAYIM: V-19` — bu okuma doğrulanamaz; sadece ekrandaki sayının büyüklük mertebesiyle tutarlıdır.

**(c) Baskı skoru `±100` ölçeğinde ve bir "boyut uyumu" sayacı var — §5'i değiştiriyor.**
Panelde `TRADE FINDER · ⬆ Güçlü Boğa ▲ +94   5 / 5` ve altında *"Boğa baskısı: 5/5 boyut hemfikir"* yazıyor.
Buna göre §5.2'nin çıktısı şöyle güncellenir:
```
baski_ens_100 = 100 · P_ens          # −100 … +100
baski_seq_100 = 100 · P_seq
boyut_uyum    = aynı işaretli geçerli boyut sayısı   # 0..5
```
`boyut_uyum` §5.4'teki konfluens koşulunun zaten içindeydi; artık **ayrı bir sayı olarak yayınlanır**
(kullanıcıya "5/5" diye gösterilebilsin diye).

**(d) YENİ MODÜL: Sinyal Sağlığı — spec'te hiç yoktu.**
Panelde ayrı bir satır var: `SİNYAL SAĞLIĞI · Canlı İzleme → ✓ Boğa Teyidi   >70 canlı`.
Tanıtım metni de bunu vurguluyor: *"Trend sinyali hâlâ aktifken, anlatı satırı yapısal zayıflamayı çoktan göstermiş."*
Yani yürürlükteki sinyalin **hâlâ ayakta olup olmadığını** izleyen ayrı bir bakım katmanı var.

```
MODÜL F — SİNYAL SAĞLIĞI
saglik(t) = 100 · | P_ens(t) |     (yürürlükteki rejim yönündeyse pozitif, ters yöndeyse negatif)

durum:
  TEYİTLİ    : rejim yönünde ve saglik ≥ 70
  KORUNUYOR  : rejim yönünde ve 40 ≤ saglik < 70
  ZAYIFLIYOR : rejim yönünde ve saglik < 40   →  "yapısal zayıflama" uyarısı
  ÇELİŞKİLİ  : P_ens işareti rejimin tersi (ama histerezis eşiği henüz aşılmadı)
```
`70 / 40` eşikleri, panelde görünen `>70` ifadesinden alındı; `40` bize ait. `# VARSAYIM: V-20`

**Bu modül neden önemli:** ST-EP'nin asıl satış argümanı burada. "Trend hâlâ AKTİF ama sağlık düşüyor"
ayrımı, tek bir skorun veremeyeceği bir bilgidir ve **ölçülebilir bir hipotezdir**:
*"ZAYIFLIYOR durumuna geçen açık sinyaller, geçmeyenlere göre daha kötü sonlanıyor mu?"*
Görev D'ye 4. hipotez olarak eklenecek.

### 11.3 Görselin **kanıtlamadığı** şey

Ekran görüntüsünde tek bir örnek var ve o örnek **seçilmiş** bir örnektir (pazarlama görseli).
Grafikte görünen üçgenlerin bir kısmı iyi noktalarda duruyor gibi görünüyor — bu **hiçbir şey kanıtlamaz**:
- Örneklem büyüklüğü 1.
- Başarısız örneklerin görsele konmayacağı açıktır (hayatta kalan yanlılığı).
- Grafikte işaretlerin **hangi barda** basıldığı (kapanışta mı, sonradan mı) görselden anlaşılmaz.

Dolayısıyla görsel, **formülleri doğrulamak** için değerlidir (ve gerçekten değerli oldu, §11.1),
**performansı doğrulamak** için hiçbir değeri yoktur. Kenar var mı sorusunun cevabı hâlâ Görev D'de.
