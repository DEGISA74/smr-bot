# Magic Ribbon — BIST Seans Mumu Hattı

## Karar (31 Ağustos 2026)

Magic Ribbon, eski `veriler_4s/` serisini kullanmaz. BIST100 için yalnız
TradingView/borsapy 5 dakikalık veriden iki sabit seans mumu üretir:

- 09:55–14:00
- 14:00–18:10

Bu seçim, ekranda görünen eski `13:30` zaman damgasının barın başlangıcını
anlatmasına rağmen kullanıcı açısından belirsiz kalmasını ortadan kaldırır.
Her sonuçta son seans ile fiilî kapanış (`18:10`) ayrı görünür.

## Veri ve korumalar

- Hat Yahoo, `veriler_4s/`, günlük/saatlik fetcher ve mevcut throttle'lara
  dokunmaz.
- 5 dakikalık akış tek tek ve en az 3,2 saniye arayla yenilenir.
- Gün içindeki herhangi bir eksik 5 dakikalık bölüm ilgili günü reddeder.
  TradingView'in işlem oluşmayan 18:00 aralığını atlaması tek istisnadır;
  18:05 kapanış mumu mevcutsa OHLC ve hacim uydurulmadan hesaplanır.
- İşlem günü 18:15 sonrasında aynı güne ait ikinci seans mumu yoksa önceki gün
  "güncel" kabul edilmez.
- Yerel Windows görevi `SMR_MagicRibbon_Refresh`, hafta içi 18:20'de
  `run_magic_ribbon.sh` üzerinden yalnız bu kasayı yeniler. Başarılı yenileme,
  sadece `veriler_magic_ribbon_seans/` klasörünü VPS'e aktarır.

## Ölçüm ve ekran kararı

İlk kayıt 100 BIST100 hissesi ve yaklaşık 49 tam seansla sınırlıdır. %0,20
gidiş-dönüş maliyet varsayımıyla sinyalin genel evrene göre ortalama getirisi:

| Vade | Sinyal sayısı | Alfa |
|---|---:|---:|
| Yaklaşık T+5 | 578 | -%0,280 |
| Yaklaşık T+10 | 474 | -%0,077 |
| Yaklaşık T+20 | 303 | +%0,024 |

Vade yönleri tutarlı değildir; bu ayrım kanıtı sayılmaz. Bu yüzden
`MAGIC_RIBBON_BIST_SESSION_RENDER_ENABLED = False` kalır: ham sinyaller iki
ayrı deftere kaydedilir ve ileri test devam eder, ancak aday listesi ekrana
açılmaz. İkinci farklı piyasa rejiminde, daha uzun geçmişle üç vadede tutarlı
pozitif ayrım oluşmadan bayrak açılmaz.

## Denetim — 1 Eylül 2026 (Claude)

Üç bulgu; ilk ikisi düzeltildi, üçüncüsü ölçümün kendisini şüpheli çıkardı.

**1. İleri test defteri boştu.** Değişiklikten sonra hiç Master Scan koşmadığı
için `magic_ribbon_session_log` ve `scan_signals` sıfır satırdı; VPS'te tablo
henüz oluşmamıştı bile. Ekranı kapalı tutmanın tek gerekçesi "kayıt birikiyor"
olduğu hâlde kayıt birikmiyordu. Defter 1 Eylül'de başlatıldı (Master Scan
çalıştırılmadan, yalnız Magic Ribbon yolu — gün içi tur akşamki 19:55
otomasyonunu iptal ettiği için, bkz. Master Scan otomasyon tuzağı).

**2. Tek sembol tüm aktarımı öldürüyordu.** Yenileyici 100 sembolden biri
düşünce hata dönüyor, sarmalayıcı da yalnız tam başarıda VPS'e gönderiyordu →
lokal kasa ilerlerken sunucu sessizce geride kalıyordu. Ölçüt artık **başarı
oranı %90**; altında sistemik arıza sayılıp aktarım durur.

**3. Her sembolün serisinde delik var — ve ölçüm bu deliğe karşı kırılgan.**
Bozuk günü reddetme kuralı doğru çalışıyor ama arkasında tuzak bırakıyordu:
kalan mumlar hesapta **yan yana** sayılıyordu. 100 sembolün 100'ünde eksik
işlem günü var; kapsama medyanı **%94,6**, en kötü BSOKE **%69,2** (54 günün
16'sı yok). Delikli seride "eğim" gerçek eğim değil, "10 mum sonra" da 5 iş
günü değil.

Ölçüm delik-farkında hale getirilip iki kova üretildi (HAM / KESİNTİSİZ):

| Vade | HAM alfa | KESİNTİSİZ alfa | Kesintisiz N |
|---|---:|---:|---:|
| T+5 | -%0,280 | +%0,273 | 407 |
| T+10 | -%0,077 | +%0,789 | 227 |
| T+20 | +%0,024 | +%1,867 | 13 |

**Bu iyi haber DEĞİL.** Filtre sadece sinyali değil TABANI da düzeltiyor
(T+10 tabanı -%0,343 → +%0,843) ve giriş aylarının dağılımı bozuluyor:
Haziran'ın %65'i, Ağustos'un %64,5'i kalırken **Temmuz'un yalnız %34,6'sı**
kalıyor. Yani filtre "temiz sinyal" değil ağırlıkla **kötü ayı** eliyor —
takvim yanlılığı. Üstelik kesintisiz alfanın t değeri **1,33**, üstelik bu
örtüşen pencereler yok sayıldığı için **iyimser bir üst sınır**; gerçek değer
daha küçük. Ham kovada t = -0,14.

**Hüküm: iki okuma da kanıt değil.** Delik düzeltmesi ölçümü dürüstleştirir,
edge üretmez. Bayrak kapalı kalır — bu bulgu kararı zayıflatmaz, güçlendirir.

**Üretime giren delik kapısı:** şeridi besleyen mumlar (CoraWave 10+3 ≈ 12 bar,
LazyLine 15 ≈ 13 bar, eğim için +1) yüzünden **son 20 mum tek kesintisiz blokta
olmalı**; değilse hisse aday listesine girmez. Sayı sezgiyle değil göstergenin
geriye bakışından türetildi. 1 Eylül canlı turunda 19 hisse bu kapıdan elendi,
aday 13 → 10 düştü — **TUPRS dahil**, üstelik "yeni hizalanma" etiketiyle.
Ayrıca her kayda `kapsama` ve `eksik_gun` yazılıyor: ileride "delikli hisse
daha mı kötü tutuyor" sorusu sorulabilsin diye.

## İlgili dosyalar

- `magic_ribbon_session_data.py`: kaynak, seans mumları ve tazelik kapısı
- `magic_ribbon_core.py`: Fast/Slow hesaplama ve ileri-test kaydı
- `magic_ribbon_refresh.py` + `run_magic_ribbon.sh`: düşük tempolu yenileme
- `magic_ribbon_session_backtest.py`: kısa tarihçeli ölçüm
- `magic_ribbon_refresh_task.xml`: yerel Windows görev tanımı
