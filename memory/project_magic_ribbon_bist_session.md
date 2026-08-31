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

## İlgili dosyalar

- `magic_ribbon_session_data.py`: kaynak, seans mumları ve tazelik kapısı
- `magic_ribbon_core.py`: Fast/Slow hesaplama ve ileri-test kaydı
- `magic_ribbon_refresh.py` + `run_magic_ribbon.sh`: düşük tempolu yenileme
- `magic_ribbon_session_backtest.py`: kısa tarihçeli ölçüm
- `magic_ribbon_refresh_task.xml`: yerel Windows görev tanımı
