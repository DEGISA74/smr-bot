# VARSAYIMLAR SİCİLİ — ST-EP yeniden inşası

**Tarih:** 23 Ağustos 2026 · **İlgili belge:** `docs/st_ep_spec.md`

Bu dosyanın amacı tek: **uydurma ile bilgiyi ayırmak.** Spec'teki her `# VARSAYIM: V-xx` etiketi burada açılır.
Kural: bir sayı ya bir kaynaktan gelir, ya matematikten türetilir, ya da burada varsayım olarak yazılır.
Dördüncü seçenek (sessizce koymak) yok.

## Nasıl okunur

- **Risk:** varsayım yanlışsa ne bozulur.
- **Nasıl yıkılır:** varsayımı çürütecek somut test. "Bakarız" değil, ölçülebilir bir şey.
- **Sınıf:**
  - 🟢 **Düşük** — yanlış olsa da sonucu az değiştirir, veya kolayca kalibre edilir
  - 🟡 **Orta** — sonucu belirgin değiştirir, testte ayrıca izlenmeli
  - 🔴 **Yüksek** — yanlışsa modülün anlamı gider

---

| ID | Nerede | Varsayım | Neden böyle seçildi | Risk | Nasıl yıkılır | Sınıf |
|---|---|---|---|---|---|---|
| **V-01** | §1.4 | Volatilite cetveli tüm 6 ölçek için **tek** ve **20 barlık** Yang-Zhang | İzotropi iddiası ortak cetvel gerektirir; 20 gözlem YZ için asgari makul örneklem | Rejim hızlı değişirse 47 barlık pencerenin eğimi güncel volatiliteyle bölünür → ölçek kayar | `n_vol ∈ {20, 60}` iki varyant aynı testte koşulur; oy dağılımları ve hipotez sonuçları anlamlı ayrışıyorsa varsayım zayıftır | 🟡 |
| **V-02** | §2.2 | Oy metriği uç-noktadan-uç-noktaya `Δln` (OLS eğimi değil) | Net yer değiştirme kavramına birebir karşılık; OLS daha yumuşak ama daha geç | Tek bir sıçrama barı (tavan, temettü) 3 ve 7 barlık oyları tek başına çevirebilir | İki varyant (uç-nokta / OLS) yan yana koşulur; `tavan_taban` ve `supheli_sicrama` bayraklı barlarda oy farkı ölçülür | 🟡 |
| **V-03** | §2.3 | Ölü bölge eşiği θ, referans örneklemde `\|z\| ≤ θ` oranı **%25** olacak şekilde kalibre edilir ve dondurulur | Getiriye bakmadan yapılan bir **dağılım** kalibrasyonu; kâr optimizasyonu değil, bu yüzden overfit riski taşımaz | %25 keyfîdir. Daha geniş ölü bölge sinyal sayısını düşürür, daha dar gürültü üretir | `θ` üç değerde (%15 / %25 / %35 ölü bölge) koşulur; hipotez sonuçları θ'ya karşı düz bir yüzey mi, sivri bir tepe mi? Sivriyse sonuç θ'ya bağımlıdır = güvenilmez | 🟡 |
| **V-04** | §2.5 | Kanaat = **tüm** ölçeklerin ortalama \|z\|'si (yalnız çoğunluk yönündekiler değil), etiket 252 barlık yüzdelikten | Çelişkili ama şiddetli piyasada "ortada büyük hareket var" bilgisi korunur | Kullanıcı "kanaat"i yön güveni sanabilir; oysa şiddet ölçüyor. Bu yüzden ham alan adı `siddet` yapıldı | İki tanım (`tüm` vs `çoğunluk`) ayrı kolon olarak üretilir; hangisi hipotez 1'de daha ayrıştırıcı, sayıyla görülür | 🟢 |
| **V-05** | §2.6 | 11 durumun eşikleri (±3, ±5), "dönüş" için 3 barlık geriye bakış ve **öncelik sırası** | Yazar isimleri veriyor, sayıları vermiyor. ±5 = "6 oydan 5'i" tarifiyle uyumlu; ±3 = çoğunluk | Eşikler kaydırılınca durum dağılımı ciddi değişir; özellikle "Dönüş Onaylandı" nadirleşir/yaygınlaşır | Durum frekans tablosu yayınlanır. Bir durum örneklemin <%0.5'i veya >%40'ıysa eşik yanlıştır (bilgi taşımıyor demektir) | 🔴 |
| **V-06** | §3.2 | "Çok mumlu bileşik" ayrı bir ön-işlem değil, ER formülünün kendisidir | Alternatifi (tek-bar veriminin ortalaması) netleşme özelliğini yok eder — verimlilik tanımına aykırı | Yazarın kastı gerçekten sentetik birleşik mum olabilir; o zaman sayılarımız farklı çıkar | `bilesik_govde_orani` (F1) zaten ayrı kolon; iki metriğin korelasyonu ölçülür. Düşükse ikisi farklı şey ölçüyordur ve seçim önemlidir | 🟡 |
| **V-07** | §3.2 | Birincil verimlilik ölçeği `n = 19` | Orta bant; 3/7 tek olayla zıplar, 47 eski bilgiyi taşır; 19 trend motorunun da orta ölçeği | Yazar başka bir ölçeği birincil sayıyor olabilir | 6 ölçeğin hepsi kolon olarak var; hipotez 1 her ölçek için ayrı koşulur, hangisinin ayrıştırıcı olduğu ölçülür | 🟢 |
| **V-08** | §3.4 | 19 durum = 3×4 grid'in 20 hücresi eksi 1; birleşen hücre **"karşı yön + Zayıf" = Gürültü** | 12 isim 3 trend hâli × 4 dilime oturuyor, 20 çıkıyor; yazar 19 diyor. Birleşmesi anlamlı tek hücre bu | Yanlış hücreyi birleştirmiş olabiliriz; o zaman bir durumun adı yanlış yerde durur | Ölçülebilir değil (yazarın niyeti bilinemez). Ama **karar mantığına etkisi yok** — 20 hücre olarak da test edilebilir. Test 20 hücreyle koşulacak, sunum 19 ile yapılacak | 🟢 |
| **V-09** | §3.5 | İvme etiketleri: EMA(5)/EMA(20) oranı, eşikler 1.15 / 0.90; "Hızlanıyor" ile "Keskinleşiyor" **volatilite yönüyle** ayrılır | Yazar dört isim veriyor, tanım vermiyor. İki ismin farklı şey ifade etmesi için ayırt edici bir eksen gerekiyordu | Eşikler keyfî; "Keskinleşiyor" tanımı tamamen bizim yorumumuz | Dört etiketin frekansı ve hipotez 1'deki ayrıştırıcılığı ölçülür. "Keskinleşiyor" ile "Hızlanıyor" arasında getiri farkı yoksa ayrım gereksizdir, birleştirilir | 🟡 |
| **V-10** | §3.3, §4.2 | Yüzdelik geriye bakış penceresi **252 bar**, asgari 120 | Bir tam yıl; %5'lik dilimi ~12 gözlemle temsil eder | Kısa geçmişli hisselerde (halka arz) alan boş kalır; rejim değişiminde geç uyum | 126 / 252 / 504 üç pencerede koşulur. Sonuçlar pencereye karşı kararlıysa varsayım güvenli | 🟡 |
| **V-11** | §4.3 | Hacim profili bin sayısı `clip(round(0.75·n), 10, 40)` | Sabit bin, uzun pencerede çözünürlük kaybettirir | POC konumu bin sayısına duyarlıdır; az bin → POC kabalaşır, çok bin → gürültülü tepe | POC'ları iki bin şemasıyla (sabit 20 vs adaptif) hesaplayıp `poc_konsensus` dağılımı karşılaştırılır | 🟡 |
| **V-12** | §4.3 | POC yığılma toleransı = **0.5 × ATR(14)**, konsensüs = en kalabalık kümedeki POC sayısı | Tick (fiyat seviyesine bağımlı) ve sabit yüzde (volatiliteye kör) elenmişti; ATR enstrümanın kendi ölçüsü | Katsayı 0.5 keyfî. Büyütülürse herkes 6/6 olur (bilgi ölür), küçültülürse herkes 1/6 olur | Katsayı `{0.25, 0.5, 1.0}` taranır; `poc_konsensus` dağılımı düz mü uçlara mı yığılıyor? Ayrıca §11.1'deki gerçek örnek (5/6, POC'lar 2.52–2.64) bir tutarlılık kontrolüdür | 🟡 |
| **V-13** | §4.4 | **S1–S19 senaryolarının tetik koşullarının tamamı** | Yazar yalnızca isimleri veriyor. Koşullar isimlerin anlamından türetildi | **Sicilin en riskli maddesi.** 19 senaryonun anlamı tamamen bizim yorumumuza bağlı; yazarın kastıyla örtüşmeyebilir | Ölçülemez (yazarın tanımı yok). Yapılacak: her senaryonun **frekansı** ve **tek başına** öngörü gücü ayrı ayrı raporlanır. Frekansı <%0.1 olan senaryo test edilemez → "ölçülemedi" diye işaretlenir, sessizce iyi sayılmaz | 🔴 |
| **V-14** | §5.1 | `b_momentum = tanh(z_7 − z_19)` — momentum, iki ölçek farkı olarak tanımlandı | Yazar momentumu ayrı boyut sayıyor ama tanımlamıyor. Yeni gösterge eklemek yerine mevcut ölçeklerden türetildi | Momentum böylece trendden **bağımsız değil**; iki boyut aynı z'leri paylaşıyor → "5 bağımsız boyut" iddiası zayıflar | `corr(b_trend, b_momentum)` ölçülür ve raporlanır. 0.7'nin üstündeyse iki boyut tek boyuttur, ağırlık buna göre düzeltilmelidir | 🟡 |
| **V-15** | §5.2 | Sequential mod kapı eşikleri (0.34 / 0.50 / 0.25 / işaret uyumu) | "Her aşama geçerse bir sonraki sayılır" tarifinin en sade hâli | Kapılar sertse sinyal neredeyse hiç oluşmaz; gevşekse ensemble'dan farkı kalmaz | İki modun sinyal sayısı ve örtüşme oranı raporlanır. Örtüşme >%90 ise iki mod aynıdır, biri silinir | 🟡 |
| **V-16** | §5.3 | Adaptif histerezis: `esik = 0.20 + 0.60·R`, `R` = EMA(10) rejim gücü | Tarif sözel; formül bize ait. `τ0` tabanı, `λ` rejim direncini temsil eder | Yanlış kalibrasyon iki uçtan birine götürür: ya histerezis hiç çalışmaz, ya rejim hiç dönmez | **Hipotez 3 tam olarak bunu test ediyor:** sabit eşiğe karşı sinyal sayısı ve sonuç farkı. Yan etki olarak `λ ∈ {0.3, 0.6, 0.9}` taranır | 🔴 |
| **V-17** | §5.4 | Konfluens eşiği `\|P_ens\| ≥ 0.50` + tüm boyutlar aynı işaretli | Panelde "5/5 boyut hemfikir" görülüyor; büyüklük eşiği bize ait | Eşik yüksekse işaret hiç basılmaz, düşükse her bar basılır | Konfluens frekansı raporlanır. Hedef mertebe: barların %1–3'ü. Dışındaysa eşik yanlıştır | 🟡 |
| **V-18** | §11.2(a) | İzotropi `√n` (istatistiksel) normalizasyonuyla kurulur; yazarın X/Y koordinat (açı) yaklaşımı yalnız **rapor** alanı olarak taşınır | 6 ölçeğin eşit söz hakkı olması tasarım şartımız; açı yaklaşımı uzun pencereleri susturur | Yazarın gerçek yöntemi farklıysa sayılarımız onunkine benzemez (zaten benzemesi hedef değil) | Ölçek başına **oy oranı** (kaç barda `oy_n ≠ 0`) raporlanır. `√n` normalizasyonu altında 6 ölçek yakın oranlar vermeli; vermiyorsa normalizasyon iddiası çürür | 🟡 |
| **V-19** | §11.2(b) | Ekranda görülen `251.15`, verimliliğin kendi 252 barlık ortalamasına oranının yüzdesidir | Fiyat değil, dilim değil; büyüklük mertebesi (2.5×) bu okumayla tutarlı | Yanlış olabilir — başka bir ölçek olabilir. Ama yalnız gösterim alanı, karar mantığına girmiyor | Doğrulanamaz. Zararsız: alan sadece raporlanır | 🟢 |
| **V-20** | §11.2(d) | Sinyal Sağlığı eşikleri: `≥70 TEYİTLİ`, `40–70 KORUNUYOR`, `<40 ZAYIFLIYOR` | `70` panelden okundu (`>70 canlı`); `40` bize ait | `40` yanlışsa "zayıflama" uyarısı ya çok erken ya çok geç gelir | **Hipotez 4:** ZAYIFLIYOR'a düşen açık sinyaller, düşmeyenlere göre daha kötü mü sonlanıyor? Eşik `{30, 40, 50}` taranır | 🟡 |

---

## Varsayım olmayan, kaynaklı olan şeyler (karıştırılmasın)

Bunlar sicile **girmez** çünkü doğrulandı:

| Konu | Kaynak |
|---|---|
| Yang-Zhang formülü ve `k = 0.34/(1.34+(n+1)/(n−1))` sabiti | Yang & Zhang (2000), web'den doğrulandı |
| Kaufman verimlilik oranının tanımı | KAMA literatürü, web'den doğrulandı |
| İşlem sınıflandırma doğruluğu %81.4 / %85 / %93 | Ellis-Michaely-O'Hara (2000), Odders-White (2000), Lee-Radhakrishna (2000) |
| yfinance geçerli aralıkları ve `4h`'ın geçerli aralık **olmadığı**; 1m≈7g, 15m/30m≈59g, 1h≈729g sınırları | yfinance dokümantasyonu/kaynağı, web'den doğrulandı |
| BIST seans süreleri (480 dk / arefe 150 dk) ve arefe hacim katsayısı 0.3125 | Bu deponun kendi `bist_calendar.py` dosyası |
| Hacim ayrıştırmasının `alış + satış = toplam`, `delta = alış − satış` yapısı | Yazarın kendi ekran görüntüsündeki sayılar (`22.74 + 5.68 = 28.42`) |
| Dilim eşikleri: üst %5 / üst %25 / orta %50 / alt %25 | Yazarın kendi ürün tarifi |
| 6 ölçek: 3, 7, 13, 19, 29, 47 | Yazarın kendi ürün tarifi + ekran görüntüsündeki tablo |
| POC ölçek uyumunun `k/6` biçiminde yayınlandığı | Ekran görüntüsü (`5 / 6 Ölçek Uyumu`) |

---

## Sicilin özeti

- **Toplam varsayım:** 20
- 🔴 **Yüksek riskli: 3** — V-05 (trend durum eşikleri), V-13 (19 hacim senaryosunun tüm koşulları), V-16 (histerezis formülü)
- 🟡 Orta: 13 · 🟢 Düşük: 4

**En kırılgan nokta V-13.** Hacim senaryolarının isimleri yazarın, koşulları tamamen bizim.
Bu, "aynı sistemi kurduk" diyemeyeceğimiz alandır. Görev D'de bu modülün sonuçları
**ayrı bir başlık altında** ve "koşullar bizim tanımımızdır" uyarısıyla raporlanacak.

---

## EK — Prototipten gelen ilk ölçümler (23 Ağu 2026)

Aşağıdakiler **sentetik veriyle** (rastgele yürüyüş + sıçramalar) yapılan ilk kontrollerdir.
Gerçek BIST verisiyle tekrarlanmadan kesin sayılmazlar, ama bazı varsayımlara ilk kanıtı verdiler.

| Varsayım | Ölçüm | Sonuç |
|---|---|---|
| **V-18** (√n normalizasyonu ölçekleri eşitler mi?) | Ölçek başına "oy verme" oranı: n=3 → %69.0, n=7 → %73.1, n=13 → %68.6, n=19 → %69.0, n=29 → %70.9, n=47 → %73.4 | ✅ **Destekleniyor.** Altı ölçek birbirine çok yakın oranda oy veriyor. Uzun pencerelerin susması sorunu görülmedi — normalizasyon iddiası tutuyor |
| **V-03** (ölü bölge eşiği θ) | %25 ölü bölge hedefiyle kalibrasyon → **θ = 0.273** | ✅ Spec'te "teorik değer 0.32 olurdu, kalın kuyruk yüzünden daha küçük çıkar" yazmıştık. Ölçüm **0.273** verdi — tahmin doğru yönde çıktı. Varsayılan 0.30 olarak duruyor; gerçek BIST kalibrasyonu yapılınca dondurulacak |
| **V-13** (19 hacim senaryosu) | 600 barlık sentetik seride S1, S7, S8, S9, S13, S14, S17, S18 **hiç tetiklenmedi** | ⚠ **Uyarı doğrulandı.** Senaryoların yarısına yakını nadir. Nadir olması kötü değil ama **test edilemez** olmaları sorun: örneklem yoksa "işe yarıyor mu" sorusu cevapsız kalır. Gerçek veride frekans ölçümü şart |
| **Modül E (MEM)** | MEM, `baski_ens_100` ile **sayısal olarak aynı** çıktı | ❌ **Gereksiz.** Eşit ağırlıkta MEM = ensemble baskı skorunun birebir aynısı. Tek özgün çıktısı altındaki osilatör. Görev B'deki "MEM eklenmesin" yargısını doğruluyor |
