# FAZ 0 — DERİN FİYAT ARŞİVİ · İŞ EMRİ
**Yazan:** Claude · **Tarih:** 28 Ağustos 2026 · **Alıcı:** Codex
**Kapsam:** yalnız ADIM 1 ve ADIM 2. Adım 3'e (replay motoru) GEÇME.

---

## NEDEN

Bütün ölçümlerimiz **tek döneme** sıkışmış: elimizdeki fiyat arşivi 290 seans,
3 Temmuz 2025'te başlıyor. 52 haftalık göstergeler 250 seans ısınma istiyor →
ikinci bir dönem üretilemiyor.

28 Ağustos'ta bu duvara **üç ayrı ölçümde** çarpıldı:
- likidite kuralı (ikinci dönemde doğrulanamadı)
- evren tabanı (tek dönem, döneme özgü)
- tarama karnesi (207 hücrenin tamamı tek dönemden)

Derin arşiv bu üçünün de önünü açar.

---

## ⛔ DEĞİŞMEZ İLKELER

1. **CANLI FİYAT KASASINA DOKUNMA.** `health/bist_store/` altındaki hiçbir dosyaya
   YAZMA. Kasanın sürüm/manifest/bütünlük makinesi var; araştırma için oynanmaz.
   Bozulursa canlı tarama, bot ve ekran birden düşer.
2. **AYRI ARŞİV.** Kendi klasörü, kendi manifesti, kendi adlandırması.
   Öneri: `arsiv_derin/` (kök altında, `.gitignore`'a ekle — GB'larca veri git'e girmez).
3. **Canlı veri hattını yorma.** Fetcher cron'ları 10 dakikada bir koşuyor
   (`*/5 7-15` acil + `0,30 7-15` İş Yatırım). İndirmeni **seans dışına** al
   (BIST 18:00'de kapanır) veya hafta sonu koş. Gün-sonu veri çekiyorsun,
   acelesi yok.
4. **Kesintiye dayanıklı ol.** Tek seferde 628 sembol × 3 yıl çekme. Parti parti
   (20-50 sembol) yaz, nerede kaldığını dosyaya kaydet, yeniden başlayınca
   kaldığı yerden devam etsin. Kesilirse baştan başlamak kabul edilemez.
5. **Hız sınırı.** Yahoo için `YAHOO_MAX_PER_MINUTE` benzeri bir tavan koy;
   mevcut cron'lar 500 kullanıyor, sen **daha düşük tut** (canlı hat öncelikli).

---

## ADIM 1 — İNDİRME

**Hedef:** 2023-01-01 → bugün · ~900 seans · kasadaki 628 sembol (BIST + endeksler)
**Kaynak:** Yahoo (gün-sonu OHLCV). İş Yatırım hacim yaması bu adımda GEREKMİYOR —
geçmişe ne kadar veri verdiği ayrı bir soru, Adım 2'de raporla, uygulama.

Sembol listesi: canlı kasanın manifestinden al (`bist_data_store.load_manifest`),
uydurma liste kullanma.

**Çıktı:** `arsiv_derin/` altında sembol başına dosya + bir manifest
(hangi sembol, kaç seans, hangi tarih aralığı, ne zaman çekildi).

---

## ADIM 2 — ÜÇ DÜRÜSTLÜK RAPORU

Arşiv **kullanılmadan önce** üçü de çıkarılacak. Sayılarla, tahminle değil.

### 1. DERİNLİK
Her sembol kaç seans geriye gidiyor? Delikler nerede?
Çıktı: dağılım (min/ortanca/maks) + eksik-gün listesi olan semboller.
⚠ 2023'te borsada olmayan hisseler doğal olarak kısa olacak — bu normal, ayır.

### 2. HAYATTA KALMA YANLILIĞI
Bugünkü 628 sembol **2023'ün evreni değil.** O tarihten sonra borsadan çıkmış /
işlem sırası kapanmış hisseler arşivde YOK. Bu, geçmişe dönük her ölçümü
iyimser yönde bozar (batanlar listede yok).
**Ölç:** kaç sembol eksik olabilir, tespit edebiliyor musun, edemiyorsan bunu
açıkça "ölçülemedi" diye yaz. Rakam uydurma.

### 3. DÜZELTME KAYMASI
Bugünkü bölünme/temettü düzeltmesi geçmişe uygulanıyor; 2023'te ekranda o
fiyatlar YOKTU. Bu kapatılamaz — sadece damgalanır.
**Ölç:** kaç sembolde bölünme/bedelsiz var, hangi tarihlerde, kaç sembolün
2023 fiyatı bugünkü düzeltmeyle kaç kat farklı.

### Ayrıca raporla
- **Disk kullanımı** — arşiv ne kadar yer kapladı
- **Süre** — indirme ne kadar sürdü, hangi hızla
- **Başarısızlıklar** — kaç sembol çekilemedi, neden

---

## 🛑 ADIM 2 BİTMEDEN ADIM 3'E GEÇME

Replay motorunu Claude **önermiyor**: bugünün kodu eski veriye uygulanır,
bugünün düzeltilmiş fiyatları kullanılır, çıkmış hisseler eksiktir. Çıkacak
"ikinci dönem" hiçbir zaman temiz olmayacak.

Arşiv yine de değerli — çünkü tek-dönem darlığı bugün üç ayrı yerde önümüze
çıktı. **Faz 0 bu yüzden yapılıyor; replay kararı AYRI ve sonra.**

---

## DOKUNULMAYACAKLAR
`health/bist_store/` · `patron.db` · `app.py` · `smr_core.py` · `evidence.py` ·
`scanners.py` · ekran · VPS · fetcher cron'ları

**Rapor ver ve DUR.**
