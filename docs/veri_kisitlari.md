# VERİ VE PERFORMANS KISITLARI

**Tarih:** 23 Ağustos 2026 · Kısa not. Sayılar ya bu depodan ya ölçümden ya da doğrulanmış kaynaktan.

---

## 1. Fiyat verisi nereden geliyor, sınırı ne?

### 1.1 Günlük veri (ana kaynak)
BIST günlük veri bu sistemde **sağlayıcıdan doğrudan değil**, yerel parquet arşivinden okunuyor
(sürümlenmiş kasa + tek kapı veri bekçisi). Bu, motor için **iyi haber**: hız limiti sorunu yok,
tekrarlanabilirlik yüksek, aynı veriyle aynı sonuç çıkıyor.

**Ama bir tuzak var ve büyük:** `AUTO_ADJUST = False`. Yani geçmiş fiyatlar **bölünme ve temettü için
düzeltilmemiş**. Bu bilinçli bir karar — destek/direnç seviyeleri aracıdaki fiyatla birebir tutsun diye.
Seviye ölçmek için doğru. **Getiri ölçmek için zehir.**

| Ne bozulur | Nasıl |
|---|---|
| Motorun eğim ölçüsü | Bölünme günü %9900'lük sahte hareket → o bar ve sonraki 47 bar boyunca eğim uçar |
| Verimlilik | Sahte sıçrama paydayı da payı da bozar |
| Backtest getirisi | Tek satır bir taramanın ortalamasını tek başına "elit" yapabilir — **bu zaten yaşandı ve ölçüldü** |

**Zorunlu önlem:** mevcut altyapıdaki `%15` kurumsal işlem bekçisi doğrulamada aynen uygulanacak
(BIST günlük limiti ±%10 olduğu için %15 üstü hareket kurumsal işlemdir; eşiğin gerekçesi zaten
ölçülmüş: %13–15 bandında sıfır meşru gözlem var). Bu pencereye düşen gözlem **"ölçülemedi"** olur.

### 1.2 Gün içi (intraday) veri
Sağlayıcının geçerli vadeleri: `1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo` — **`4h` YOK.**

| Vade | Geriye ne kadar gidebilir | Motorun 301 barlık ihtiyacı karşılanıyor mu |
|---|---|---|
| 1 dakika | ~7 gün | Evet (bar bol) ama gürültü |
| 15 / 30 dakika | **~59 gün** | Evet (~1.900 bar) ama **5 yıllık test imkânsız** |
| 1 saat | **~729 gün** | Evet, rahat |
| 4 saat | *(yerel değil)* | 1 saatlikten türetilir, 10:00'a çapalanmalı |
| Günlük | Arşiv | Evet |

**Ne demek:** Gün içi testler yapılabilir ama **küçük ve ayrı bir çalışmadır**. 59 günlük bir örneklemden
çıkan sayıyı 5 yıllık bir sayının yanına koymak dürüst değildir.

### 1.3 ⚠ Mevcut kodda bulunan hata
Çok-vadeli uyum matrisi `4h` vadesini doğrudan istiyor. Bu geçerli bir vade olmadığı için istek
başarısız oluyor ve hata sessizce yutuluyor → **matriste 4 saatlik satır hiç oluşmuyor olabilir.**
Dokunulmadı; düzeltme önerisi `docs/smr_kesisim.md` bölüm 4.1'de eski/yeni blok olarak duruyor.

---

## 2. "Intrabar" (alt zaman dilimi) yaklaşımının bizdeki karşılığı

Yazarın hacim ayrıştırmasında iki yöntemi var: **geometri** (mumun şeklinden tahmin) ve
**intrabar** (alt zaman diliminden gerçek mumları sınıflandırıp toplama).

**Bizde intrabar YOK.** Süslemeden söylüyorum:

- Günlük barı 15 dakikalık barlardan kurmak için 5 yıllık 15 dakikalık veri gerekir. Sağlayıcı **59 gün** veriyor.
- 1 saatlik veriyle (729 gün) günlük barı 8 parçaya bölmek teknik olarak mümkün, ama:
  - Yalnızca **son 2 yılı** kapsar — ana testimiz 5 yıl.
  - 800 hisse × 2 yıl × 8 bar = **~3.2 milyon bar** indirmek gerekir; hız limitleri altında bu günler sürer.
  - Kazanç belirsiz: 8 parçalı ayrıştırma, gerçek emir akışına 1 parçalıdan **biraz** daha yakındır, çok değil.

**Karar:** intrabar yolu **açılmıyor**. Geometri yöntemi kullanılıyor ve çıktısı **sıralayıcı** olarak
etiketleniyor ("bu bar geçen bardan daha alıcılı") — mutlak bir emir akışı iddiası olarak değil.
Gerekçenin sayısal tarafı: en yakın literatürde, **işlem düzeyinde ve kotasyon verisiyle** yapılan
sınıflandırmanın doğruluğu %81–93 aralığında. Bizim problemimiz (tek mumun dört sayısı) bundan
**kesinlikle daha zor**, ve bar geometrisi için yayımlanmış bir doğruluk ölçümü bulunamadı.

---

## 3. HESAP YÜKÜ — ölçüldü, tahmin değil

Ölçüm: `smr_lab/bench_multiscale.py`, tek çekirdek, sentetik veri, 1.250 bar (≈5 yıl günlük).

| Ölçüm | Değer |
|---|---|
| Tek hisse, tam motor | **~152 ms** |
| Tek hisse, ayak izi/POC kapalı | **~95 ms** |
| Tepe bellek (tek hisse) | **~29 MB** |
| Çıktı kolonu | 77 |
| **800 hisse, seri, POC açık** | **~128 saniye** |
| **800 hisse, seri, POC kapalı** | **~76 saniye** |
| Histerezis durum makinesi (tek satır döngüsü) | 3.1 ms/hisse — toplamın **%2.1'i** |

**Nerede zaman gidiyor:** ayak izi/POC hesabı toplamın ~%37'si (55 ms), hacim modülü 58 ms,
verimlilik 18 ms, trend 14 ms.

**Pratik sonuç:**
- Master Scan'e eklenirse **2 dakika** ek yük demek. Kabul edilebilir ama bedava değil.
- **Canlı taramada yalnız son satır gerekiyorsa** ayak izi kapatılabilir (`poc=False`) → yük %40 düşer.
- Paralelleştirme (4 çekirdek) yükü ~30 saniyeye indirir; motor hisse bazında bağımsız olduğu için
  paralelleştirmesi kolaydır (ortak durum yok).
- Bellek sorun değil: hisseler sırayla işlenir, aynı anda tek hissenin matrisi bellekte durur.

**Bir uyarı:** bu ölçüm **sentetik veriyle** yapıldı. Gerçek parquet'lerde bar sayısı ve fiyat aralığı
değişkendir; fiyatı çok geniş aralıkta gezinmiş hisselerde ayak izi ızgarası büyür ve süre artar
(motor 6.000 dilimde çözünürlüğü otomatik düşürerek bunu sınırlıyor).

---

## 4. Hacmi olmayan / hacmi yanlış olan enstrümanlar

| Grup | Durum | Motorun davranışı |
|---|---|---|
| BIST hisse | Hacim gerçek | Tam çalışır |
| ABD hisse | Hacim gerçek | Tam çalışır |
| Kripto | Hacim gerçek (borsaya özel) | Tam çalışır |
| **FX** | Hacim **yok** (0/boş) | `hacim_gecerli = False` → hacim alanları boş, baskı skoru 4 boyutla hesaplanır. "Hacim çok düşük" **yazmaz** |
| **Endeks (XU100 vb.)** | Hacim ya yok ya hesap ürünü | Hacim modülü kullanılmamalı |
| **Vadeli emtia (sürekli sembol)** | Hacim **var görünüp yanlış** olabilir | Motor bunu tespit **edemez** — veri katmanının işi. Bu sistemde daha önce yaşandı ve düzeltildi |

Son satır önemli: motor "hacim var mı" diye bakar, "hacim doğru mu" diye bakamaz. Doğruluk yukarıdaki katmanın sorumluluğu.
