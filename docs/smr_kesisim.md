# SMR ↔ ST-EP KESİŞİM ANALİZİ

**Tarih:** 23 Ağustos 2026 · **Yöntem:** mevcut dosyalar **yalnızca okundu**, hiçbirine dokunulmadı.
**Soru:** Bu mimarinin ne kadarı SMR'de zaten var, ne kadarı gerçekten yeni?

---

## 0. Önce iki düzeltme

**(a) Görev tarifindeki `backtest.py` bu depoda yok.** Backtest altyapısının gerçek adı `backtest_runner.py`
(ileri getiri değerlendirmesi, 5/10/20 günlük pencereler, `patron.db` içindeki `scan_signals` + `signal_returns`
tabloları üzerinden). Görev D bu dosyanın etrafına sarmalanacak.

**(b) Master Skor ağırlıkları tarifte yazandan farklı.** Görev metninde "Trend %40, Momentum %30, ICT %15, Radar2 %15"
deniyor. Koddaki gerçek durum: **ICT ağırlığı sıfır.** ICT hesaplanıyor, raporlanıyor, ama canlı skora katkısı `0`.
Kalan ağırlıklar `0.85`'e bölünerek 0-100 ölçeği korunuyor. Yani fiilî ağırlıklar:
**Trend ≈ %47, Momentum ≈ %35, Radar2 ≈ %18, ICT %0.**
Bu, aşağıdaki eşleştirme tablosunun okunuşunu değiştirir: ST-EP'nin beş boyutundan biri (yapı/ICT)
SMR'de zaten *bilerek* susturulmuş durumda.

---

## 1. BEŞ BOYUT — EŞLEŞTİRME TABLOSU

| ST-EP boyutu | SMR'de karşılığı | Ne kadar aynı? | Gerçek fark |
|---|---|---|---|
| **1. Trend (6 ölçekli oy)** | SMA50/100/200 + EMA89/144 konumu, Minervini trend şablonu, çok-vadeli uyum matrisi (4H/G/H/A), Master Skor trend bileşeni | **Fikir aynı, ölçüm farklı** | SMR trendi *seviyeye göre* okur (fiyat ortalamanın üstünde mi). ST-EP *eğimin şiddetine* göre okur ve altı ölçeği **ortak volatilite cetveliyle** karşılaştırılabilir kılar. `−6…+6` gibi tek ve sıralı bir sayı SMR'de yok |
| **2. Kanaat (şiddet)** | Konviksiyon skoru (piyasa rejimi içinde), Z-Score | **Kısmen var** | SMR'nin konviksiyonu 0-100 karma bir skor; ST-EP'ninki doğrudan "hareket kaç sigma" ölçüsü. Farklı şeyler, isim benziyor |
| **3. Verimlilik (hareket kalitesi)** | Para Akış İvmesi (90 günlük oynaklığa normalize), Force Index ikilisi, ICT yer değiştirme durumu, gövde/menzil oranları | **Burada gerçek boşluk var** | "Net yol / toplam kat edilen yol" oranı (Kaufman verimlilik oranı) SMR'de **hiçbir yerde yok**. En yakını Para Akış İvmesi ama o *ivme* ölçer, *verim* değil. **Bu, listedeki en net yeni fikir** |
| **4. Momentum** | RSI, MFI, HARSI, Para Akış İvmesi, momentum bileşeni (%35) | **Fazlasıyla var** | ST-EP momentumu iki ölçeğin farkı olarak tanımlıyor — SMR'dekinden daha zayıf. Buraya yeni bir şey gelmiyor |
| **5. Hacim — alış/satış ayrıştırma** | Hacim Deltası hesabı | **BİREBİR AYNI** | SMR'nin formülü: alış oranı = (kapanış − dip) / menzil, alış hacmi = hacim × oran, delta = alış − satış. ST-EP'nin panelindeki sayılar da tam bu ilişkiyi veriyor. **Aynı formül, farklı isim.** Yeni bir şey yok |
| **5b. Hacim senaryoları (S1–S19)** | Hacim 4-Parça Hükmü (yön / katılım / süreklilik / fiyat teyidi) + Akıllı Hacim başlık motoru | **Büyük ölçüde var** | SMR aynı bilgiyi 19 isim yerine 4 eksende veriyor — üstelik daha okunaklı. **Tek gerçek fark:** SMR sabit eşik kullanıyor (göreli hacim 1.5 / 0.8), ST-EP yüzdelik dilim kullanıyor |
| **5c. Ayak izi / POC** | Hacim profili POC, POC+VAH+VAL, çok-vadeli POC yığını (20/60/250 gün) + yakınsama, çıplak POC, çapalı VWAP | **Var, hatta daha olgun** | SMR'de değer alanı, çıplak POC ve çapalı VWAP var — ST-EP tarifinde bunlar yok. ST-EP'nin farkı POC'u 6 ölçekte hesaplayıp uyumu `k/6` diye yayınlaması. **Marjinal fark** |
| **6. Adaptif histerezis** | **Yok** | **Tamamen yeni** | Depoda histerezis benzeri hiçbir mekanizma yok. Sinyaller eşiği geçince açılıyor, rejime bağlı bir direnç katmanı bulunmuyor |
| **7. MEM (tek sistem skoru)** | Master Skor, Akıllı Para Skoru, Sentiment Skoru, Kanıt Skoru, tarama tier haritası | **Fazlasıyla var** | SMR'de tek skor değil, beş ayrı skor var. ST-EP'nin MEM'i bunlardan daha basit. **Buraya yeni bir şey gelmiyor** |
| **8. Sinyal Sağlığı (çıkış izleme)** | Senaryo yaşı (0-2g taze / 3-7g orta / 8g+ eski) | **Neredeyse yeni** | SMR sinyalin **kaç günlük** olduğunu biliyor, ama **hâlâ ayakta mı** olduğunu bilmiyor. ST-EP açık sinyalin gücünün erimesini canlı izliyor. **İkinci gerçek yeni fikir** |
| **9. Anlatı üretimi** | Yapay zekâ (Gemini) ile üretilen metin | Farklı yaklaşım | ST-EP'nin anlatısı **şablon** (kural → cümle, 7 dil). SMR'ninki üretken model. ST-EP'ninki daha ucuz ve tutarlı, SMR'ninki daha zengin. Rakip değiller |

---

## 2. ÇAKIŞMA UYARILARI (kod eklenecekse dikkat)

| Nerede | Ne çakışıyor | Sonuç |
|---|---|---|
| **Hacim deltası** | ST-EP Modül C'nin ayrıştırma adımı, SMR'nin Hacim Deltası hesabının **aynısı** | Yeni motorda **ikinci bir kopya yazılmamalı**. Prototip kendi içinde vektörel hesaplayacak (izolasyon şartı), ama üretime girerse mevcut hesap tek kaynak kalmalı |
| **POC ailesi** | Çok-vadeli POC + yakınsama SMR'de zaten var (3 ölçek), ST-EP 6 ölçek istiyor | Yeni bir POC motoru yazmak yerine mevcut pencereyi 6'ya çıkarmak yeterli olabilir. Önce **6 ölçek gerçekten 3'ten fazla bilgi veriyor mu** ölçülmeli |
| **Fiyat Hareketi DNA'sı** | 6 aylık veriyle çalışıyor; hacim deltası, hacim profili (POC/VAH/VAL) ve çıplak POC'u zaten içeride hesaplıyor | ST-EP Modül C'nin çıktılarının **çoğu buradan okunabilir**. Yeni motor bunu görmezden gelirse aynı hesap iki kez yapılır → panel yavaşlar |
| **Destek/direnç zinciri** | Bot tarafında zincir: SMA50/100/200 → EMA89/144 → dünün tepesi/dibi → Fibonacci → ICT altın oran, yakınlık toleransı ±%1.5. Panel tarafında ayrı bir SuperTrend + Fibonacci zinciri | ST-EP'nin POC merkezi buraya **yeni bir seviye türü** olarak girer, mevcut zinciri değiştirmez. Ama tolerans mantıkları farklı (±%1.5 sabit yüzde ↔ 0.5×ATR). **Ölçü birliği yoksa iki panel çelişir** |
| **Çok-vadeli uyum** | Zaten 4 vadede trend/momentum/hacim uyumu hesaplanıyor | ST-EP'nin 6 ölçeği bunun **tek vade içindeki** karşılığı. İkisi yan yana durursa kullanıcı "hangisi doğru" diye sorar. Biri diğerini beslemeli, ikisi ayrı ayrı gösterilmemeli |
| **Skor enflasyonu** | Master Skor + Akıllı Para + Sentiment + Kanıt Skoru + tier + güç etiketi zaten var | MEM'i **altıncı skor** olarak eklemek net zarardır. Eklenecekse birini emekliye ayırmak gerekir |

---

## 3. DÜRÜST YARGI

**Bu mimarinin SMR'ye gerçekten kattığı şey üç tane. Fazlası değil.**

1. **Hareket verimliliği (net yol / toplam yol).** SMR'de bu ölçü yok. En yakın akrabası ivme ölçüyor, verim değil.
   Bir hisse aynı %10'u düz giderek de, 20 kez inip çıkarak da kazanabilir — SMR şu an ikisini ayırt edemiyor.
   **Katma değeri yüksek, maliyeti düşük.**
2. **Sinyalin sağlığını canlı izlemek.** SMR sinyalin yaşını biliyor, gücünün eridiğini bilmiyor.
   Senin sistemin giriş bulmakta güçlü, çıkışta zayıf — bu doğrudan o boşluğa oturuyor.
   **Katma değeri yüksek, maliyeti orta.**
3. **Sabit eşik yerine yüzdelik dilim.** SMR'nin hacim okuması sabit eşikli (göreli hacim 1.5 üstü "yoğun").
   Bu, hisseden hisseye adil değil. Yüzdelik dilime geçmek **mevcut hacim modülünün içinde** yapılabilecek,
   yeni motor gerektirmeyen bir iyileştirme. **Katma değeri orta, maliyeti çok düşük.**

**Gerisi büyük ölçüde var olanın farklı isimle tekrarı:**
- Hacim ayrıştırması birebir aynı formül.
- Hacim senaryoları, mevcut 4-parça hükmünün 19 isme bölünmüş hâli — ve açıkçası 4 eksen 19 isimden **daha okunaklı**.
- POC ailesi SMR'de daha olgun (değer alanı, çıplak POC, çapalı VWAP ST-EP tarifinde yok).
- MEM, mevcut beş skorun yanına altıncısı olur. Buna ihtiyaç yok.
- Momentum tanımı SMR'dekinden zayıf.

**Ve bir uyarı:** 19 hacim senaryosunun tetik koşullarının tamamı bizim uydurmamız (yazar isim veriyor, kural vermiyor).
Yani "SMR'ye 19 senaryo ekleyelim" demek, **ölçülmemiş 19 yeni kural eklemek** demektir.
Senin son iki oturumdaki dersin tam da buydu: doğrulanmamış bayrak birikmesi. Bu modül o borcu ikiye katlar.

**Öneri sırası:** (1) verimlilik ölçüsü, (2) sinyal sağlığı, (3) yüzdelik dilime geçiş.
19 senaryo ve MEM **yapılmasın** — ölçülene kadar değil, hiç.

---

## 4. DOKUNMADIĞIM AMA DÜZELTİLMESİ GEREKEN 2 ŞEY (ESKİ / YENİ önerisi)

Kural gereği kendim değiştirmedim. Onayını bekliyorum.

### 4.1 Çok-vadeli uyum matrisinde 4 saatlik veri hiç gelmiyor olabilir

**Dosya:** `analysis_core.py`, satır ~855 (`calculate_multi_timeframe_alignment` içinde)

**Sorun:** Veri sağlayıcının geçerli vade listesi şudur: `1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo`.
**`4h` bu listede yok.** İstek `try/except` içinde olduğu için hata sessizce yutuluyor ve `timeframes` sözlüğüne
`'4H'` anahtarı hiç eklenmiyor. Sonuç: matris 4 vade yerine 3 vadeyle (Günlük/Haftalık/Aylık) hesaplanıyor,
ama arayüzde 4 vadelik gibi sunuluyor olabilir.

**ESKİ:**
```python
_df_4h = _yf_download_with_retry(ticker, period="60d", interval="4h")
if _df_4h is not None and not _df_4h.empty and len(_df_4h) > 30:
```

**YENİ (önerilen):**
```python
# 4h yerel bir vade DEĞİL — 1h çekip 4 saatlik barlara topla.
_df_1h = _yf_download_with_retry(ticker, period="60d", interval="1h")
if _df_1h is not None and not _df_1h.empty:
    if isinstance(_df_1h.columns, pd.MultiIndex):
        _df_1h.columns = _df_1h.columns.get_level_values(0)
    _df_4h = _df_1h.resample('4h', origin='start_day', offset='10h').agg(
        {'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}
    ).dropna()
if _df_4h is not None and not _df_4h.empty and len(_df_4h) > 30:
```

**Neden bu düzeltme:** `offset='10h'` BIST açılışına (10:00) çapa atar, böylece 8 saatlik seans günde
**tam 2 bar** üretir. Çapa konmazsa gün başına 3 kırık bar oluşur ve 4 saatlik trend okuması bozulur.
**Uyarı:** Bu değişiklik matrise gerçekten 4. satırı ekler — yani **arayüzdeki uyum yüzdesi değişir**.
Önce bir hissede yan yana kıyaslamak isteyebilirsin.

### 4.2 ⚠ Depoda **4 modül eksik** — temiz kurulumda uygulama hiç açılmaz

Bu bir öneri değil, **doğrulanmış bir bulgu**. `app.py`'nin en üstünde (satır 48-52) beş modül
**korumasız** (try/except olmadan) içeri alınıyor. Bunlardan **dördü depoda yok**:

| Modül | Depoda | app.py'de içeri alınıyor |
|---|---|---|
| `tarama_merkezi.py` | ✅ var | evet |
| `terazi_core.py` | ❌ **yok** | evet (satır 48) — ayrıca kod içinde en az 4 yerde kullanılıyor |
| `ekran_v2.py` | ❌ **yok** | evet (satır 50) |
| `formasyon_core.py` | ❌ **yok** | evet (satır 51) |
| `formasyon_v2_app.py` | ❌ **yok** | evet (satır 52) |

**Ne anlama geliyor:** Bu depoyu temiz bir makineye (veya sunucuya) klonlayıp çalıştırmayı denersen,
uygulama **ilk satırda durur** — panel hiç açılmaz. Şu an çalışıyor olmasının tek sebebi,
bu dosyaların senin bilgisayarında ve sunucuda **elle** duruyor olması. Yani depo, çalışan sistemin
tam bir kopyası değil.

**Risk:** Sunucuda bir kez `git reset --hard` çekilirse veya makine değişirse sistem ayağa kalkmaz.
`CLAUDE.md` ayrıca bu dosyalardan birini "dağıtım listesine dâhil" diye işaretliyor —
yani unutulmuş değil, bilinçli olarak dışarıda tutulmuş da olabilir; ama o hâlde içeri alma korumasız olmamalı.

**İki seçenek var, ikisi de kod değişikliği:**
- **A (temiz):** dört dosya depoya eklensin. Kalıcı çözüm.
- **B (yama):** içeri alma satırları korumaya alınsın, dosya yoksa uygulama o özellik kapalı şekilde açılsın.

**B için ESKİ:**
```python
import terazi_core  # 17 Tem 2026 EKRAN REFORMU 1a — Kanıt Terazisi saf hesap çekirdeği
```
**B için YENİ:**
```python
try:
    import terazi_core
    _TERAZI_OK = True
except ImportError:
    terazi_core = None
    _TERAZI_OK = False     # kullanım yerlerinde bu bayrak kontrol edilmeli
```

**Tavsiyem A.** B yaparsan dört ayrı kullanım noktasına bayrak kontrolü eklemek gerekir — daha çok iş, daha çok risk.
Bu benim işim değil, **senin kararın**; dokunmadım.
