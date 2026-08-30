# Claude Devir Raporu — BIST Hacim Kaynağı ve Kontrollü Borsapy Yedeği

Tarih: 30 Ağustos 2026
Kapsam: Cuma hacim kurtarma, borsapy güvenilirlik denetimi ve ileriye dönük veri hattı

## 1. Karar özeti

İş Yatırım hacmin ana kaynağı olarak korunuyor. İş Yatırım cevap vermediğinde
borsapy/TradingView yalnızca beklenen son işlem günü için kontrollü yedek olarak
kullanılıyor. Borsapy verisi aktif sürüme girebilir; ancak manifestte `official`
değil `controlled_fallback` olarak işaretleniyor. Tarihsel hacim backtestlerinin
ana kaynağı yapılmıyor.

Geçmiş tarama sonuçları, sinyal getirileri, Telegram çıktıları, patron.db ve
eski parquet geçmişi değiştirilmedi. Yeni hacim sürümleri yalnız bundan sonraki
analiz ve taramalarda kullanılacak.

## 2. Kanıt ve hüküm

Salt-okur denetimde canlı İş Yatırım, borsapy/TradingView ve eski değişmez parquet
üçgeni kullanıldı. Yeni borsapy değerlerinin kendisini doğrulama grubuna koymamak
için aktif sürümün ebeveyni kontrol grubu seçildi.

- 20 likit + sabit tohumla 30 rastgele olmak üzere 50/50 sembol denetlendi.
- 120 işlem günlük pencerede 5.886 pozitif satır karşılaştırıldı.
- Borsapy ↔ canlı İş Yatırım: %90,55'i ±%1, %97,25'i ±%5, %99,46'sı ±%10 içinde.
- 32 satır ±%10'u aştı; en büyük fark SKBNK 27.07.2026'da %906,6 oldu.
- Parquet ↔ canlı İş Yatırım: ±%5 uyum %96,33, ±%10 uyum %98,34; SASA'da %4.070 uç fark görüldü.
- 28.08.2026 Cuma için İş Yatırım önbelleği bulunan 50 örneğin 23'ü borsapy ile
  ±%1 içindeydi; en büyük fark %0,18 oldu. Bu, 623 hissenin tam bağımsız teyidi değildir.

Hüküm: Borsapy günlük yön ve oran gözleminde işe yarayan kontrollü bir yedektir;
resmî tarihsel hacim kaynağı değildir. Tarih/kapanış uyumu tek başına yeterli
değildir; bu nedenle bariz hacim ölçeği hatalarını reddeden ikinci kapı eklendi.

## 3. Uygulanan kod değişiklikleri

- `finalize_volume.py`: İş Yatırım ana yol, borsapy yedek yol. Borsapy yalnızca
  son işlem gününü aday yapıyor; kapanış eşleşmesi ve pozitif hacim kontrolünden
  sonra son 20 pozitif hacim medyanına göre 0,125x–8x dışındaki bariz ölçek
  anomalilerini reddediyor. Kapsama eşiği %85 korunuyor.
- `bist_data_store.py`: ortak `official`, `controlled_fallback`, `provisional`
  kaynak sınıfları eklendi. Borsapy ile gelen sembol kalite durumunu artık
  manifestte `controlled_fallback` olarak taşıyor; eski gün için borsapy adayını
  ayrıca reddediyor.
- `gorev_bekcisi.py`: %80 sağlık kapısı artık İş Yatırım + kontrollü borsapy
  kullanılabilir kapsamını sayıyor; raporda resmî ve kontrollü adetler ayrılıyor.
  Son bekçi kontrolü 23:15 Türkiye saati.
- `kapanis_master_otomasyon.py`: Master Scan hacim fotoğrafı resmî ve kontrollü
  kapsamı ayrı sayıyor; eski tüketiciler için toplam kullanılabilir sayı korunuyor.
- `analysis_core.py` ve `app.py`: borsapy kaynaklı son seans artık
  `FINAL_BORSAPY_CONTROLLED`; AI'a resmî hacim gibi yazmaması söyleniyor.
- `smr_core.py`: Telegram teknik kartı aktif manifestteki hacim kaynağını AI veri
  bloğuna taşıyor; borsapy hacmi kurum niyeti veya kesin/resmî kanıt olarak sunulmuyor.
- `deploy.sh`: `finalize_volume.py` dağıtım paketine eklendi; sonraki deploy'larda
  lokal değişikliklerin VPS'te atlanması önlendi.

## 4. Dokümantasyon

- `VERI_CEKME_PROTOKOL.md`: kaynak sırası, güncel gün sınırı, %85 kapısı ve
  kontrollü fallback statüsü belgelendi.
- `VERI_HATTI_DURUM.md`: Cuma kurtarma kaydı ve borsapy hükmü kodun son
  davranışıyla hizalandı; ölçek kapısının kaynak doğrulaması olmadığı açıklandı.
- `DOSYA_HARITASI.md`: eski 18:15/yalnız İş Yatırım açıklaması düzeltildi ve
  `volume_source_audit.py` eklendi.

## 5. Değiştirilmeyenler

- Geçmiş sinyal/getiri tabloları, `patron.db`, `signals.db` ve eski parquetler.
- `golden_record.json`, site JSON çıktıları ve trajectory JSON'ları.
- `fetcher.py`: borsapy normal hacim turuna açılmadı; kontrollü fallback yalnız
  finalizer üzerinden çalışıyor.
- `requirements.txt`: borsapy zaten mevcut; yeni bağımlılık eklenmedi.

Aktif sürüm manifestleri ve sağlık JSON'ları elle düzenlenmiyor; finalizer bunları
atomik olarak üretir. `logs/volume_source_audit_20260830.json` salt-okur denetim
çıktısıdır. `volume_source_audit.py` üretim verisine yazmayan ölçüm aracıdır.

## 6. Doğrulama

- İlgili Python dosyalarında `py_compile`: başarılı.
- `bist_data_store_selftest.py`: başarılı; staging, kabul, eski-bar kilidi,
  karantina, rollback, paket, senkron ve 429 sigortası geçti.
- Kaynak sınıfı smoke testi: İş Yatırım `official`, borsapy `controlled_fallback`,
  Yahoo `provisional` döndü.
- Sentetik aday testi: borsapy eski günü değiştirmedi; son işlem gününü
  `controlled_fallback` olarak işaretledi.
- `golden_record.py`: 5×69 ölçüm + Terazi senaryoları + toplu Terazi sözleşmesi,
  sıfır fark; hata kaydı 0.
- `_hesap_denetimi.py`: 0 tanımsız değişken, 0 sabit konum; rapordaki ölü
  atamalar mevcut ve zararsız kalıntılar olarak bırakıldı.
- `deploy.sh` dry-run: ilgili VPS dosyalarında clobber riski yok; syntax kapısı geçti.

## 7. Operasyon takvimi

İşlem gününde VPS cron akışı 21:30 ve 22:15 hacim denemeleri, 22:50 finalizer,
23:15 bekçi kontrolü şeklinde. Normal finalizer bitişi yaklaşık 23:03, resmî
sağlık hükmü 23:15 Türkiye saati. PC açık değilse VPS ve Telegram hattı çalışır;
PC yalnız yerel onaylı aynanın senkronu ve yerel Streamlit ekranı için gerekir.

## 8. Claude için dikkat noktaları

1. Borsapy'yi tarihsel backtest ana kaynağına çevirmeyin.
2. `controlled_fallback` etiketini `official` veya `verified` diye yeniden
   adlandırmayın.
3. Borsapy kapsamını yükseltmeden önce aynı gün bağımsız İş Yatırım veya resmî
   Borsa İstanbul DataStore teyidi arayın.
4. Geçmiş sinyal/getiri ve Telegram sonuçlarını yeniden yazmayın.
5. Kaynak sınıfı değişirse Streamlit, Master Scan, Telegram, bekçi ve deploy
   paketini birlikte kontrol edin.

## 9. Canlı dağıtım kanıtı

30.08.2026 tarihinde yalnız 7 çalışma Python dosyası SCP tabanlı dağıtımla VPS'e
gönderildi. Deploy öncesi ezme kontrolü temizdi. VPS yedeği
`~/smr/_yedek/20260830_124440` altında alındı.

- VPS Python 3.10 `py_compile`: başarılı.
- Modül bütünlüğü: 30 proje modülü mevcut.
- `patron-radar`: active.
- `free-showcase`: active.
- `smr-bot`: active.
- `gorev-bekcisi.timer`: active.
- Lokal/VPS SHA-256 hashleri: 7/7 eşleşti.
- HTTP sağlık kontrolü: 200.
- Cron: İş Yatırım 21:30 ve 22:15 TR turları ile 22:50 TR finalizer korunuyor.

`deploy.sh` kendisi VPS çalışma ağacına gönderilmedi; lokal dağıtım aracı ve git
dosyası olarak kaldı. `volume_source_audit.py` ölçüm aracı olarak kaynak ağacında
tutulur; üretim servisine import edilmez.
