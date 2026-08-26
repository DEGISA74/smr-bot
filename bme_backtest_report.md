# Breakout Momentum Engine — BIST 100 4 Saatlik Backtest

## Doğrulama sınırı

Bu çalışma, gönderilen BME tanımındaki bileşenleri yerel saatlik fiyat dosyalarına uygulayan bağımsız prototiptir. Orijinal TradingView/Pine kodu, parametreleri, puan ağırlıkları ve gerçek event-state kuralı paylaşılmadığı için **orijinal göstergenin doğrulanmış performansı değildir**.

Sinyal puanı yalnız sinyal mumunun kapanışında bilinen bilgiyle hesaplanır. Retest ve FAIL ise kırılımdan sonraki en fazla 6 adet 4 saatlik mumda gözlenen durumdur; retest performansında giriş retesti doğrulayan mumun kapanışından başlar.

## Veri kapsamı

- Güncel BIST 100 listesi: 100 hisse; testte yeterli 4 saatlik geçmişi olan: 89 hisse.
- 4 saatlik veri aralığı: 2023-09-26 09:30:00+03:00 → 2026-08-14 13:30:00+03:00.
- Dışarıda kalan: 11 hisse (ALTNY, EFOR, GRTHO, KLRHO, KTLEV, OBAMS, ODINE, PAHOL, PASEU, PATEK, REEDR).
- 1 saatlik mumlar BIST seansı 09:30'a bağlanarak iki adet 4 saatlik muma dönüştürüldü; seansın son artığı ikinci muma katıldı.

## Sabit test kuralları

- Yapısal kırılım: önceki 20 adet 4 saatlik mumun en yüksek/en düşük seviyesinin kapanışla aşılması.
- Puan: yapı 20, EMA rejimi 15, ADX/DMI 10, RSI/ROC/MACD 15, RVOL/Z-hacim 15, sıkışma+ATR genişlemesi 10, mum kalitesi 10, kırılım mesafesi 5 puan.
- Eşik: normal ortamda 60; ADX 20 altındaysa veya ATR oynaklığı kendi geçmişinin üst %20'sindeyse 65. STRONG 70+, EXTREME 80+.
- Aynı yönde tekrar sayımı önlemek için 10 adet 4 saatlik mum bekleme kuralı kullanıldı.
- İleri getiriler: 1/5/10/20 işlem günü. Net sütunlar sonuçtan %0.20 toplam maliyet düşer; gerçek makas ve açığa satış maliyeti hisseye göre farklıdır.
- SHORT sonuçları matematiksel yön testidir; BIST'te her hissede sürekli uygulanabilir bir işlem kuralı sayılmaz.

## Referans: her uygun 4 saatlik mumdan yön almak

Hücre: net pozitif sonuç oranı / medyan net getiri. Bu, sinyalin piyasanın doğal yön sapmasını geçip geçmediğini görmek için referanstır.

| Yön | Gözlem | 1 gün | 5 gün | 10 gün | 20 gün |
|---|---:|---:|---:|---:|---:|
| LONG | 108622 | %46.3 / -0.20% (N=108622) | %51.0 / +0.14% (N=108622) | %52.4 / +0.48% (N=108622) | %53.4 / +1.02% (N=108622) |
| SHORT | 108622 | %45.8 / -0.20% (N=108622) | %46.0 / -0.54% (N=108622) | %45.7 / -0.88% (N=108622) | %45.3 / -1.42% (N=108622) |

## Ana sinyaller

Hücre: net pozitif sonuç oranı / medyan net getiri. MFE/MAE = ilk 5 işlem gününde medyan en iyi / en kötü fiyat hareketi. FAIL = kırılımdan sonra 6 adet 4 saatlik mum içinde seviyenin 0,5 ATR ötesinde kapanışla bozulması. Retest = aynı pencerede seviyeye dokunup yapıyı kapanışta koruması.

| Sinyal | Olay | 1 gün | 5 gün | 10 gün | 20 gün | 5g MFE / MAE | FAIL | Retest |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| LONG — TÜM | 2746 | %48.9 / -0.06% (N=2741) | %53.0 / +0.40% (N=2724) | %53.2 / +0.61% (N=2709) | %55.1 / +1.72% (N=2681) | +4.41% / -3.23% | %16.8 | %61.4 |
| LONG — QUALIFIED | 230 | %45.9 / -0.29% (N=229) | %50.7 / +0.07% (N=225) | %44.9 / -0.83% (N=225) | %49.6 / -0.16% (N=224) | +3.49% / -3.41% | %22.2 | %66.5 |
| LONG — STRONG | 722 | %52.5 / +0.16% (N=720) | %55.0 / +0.71% (N=718) | %54.9 / +0.90% (N=716) | %57.7 / +2.55% (N=704) | +4.56% / -2.94% | %17.3 | %66.6 |
| LONG — EXTREME | 1794 | %47.9 / -0.13% (N=1792) | %52.5 / +0.37% (N=1781) | %53.5 / +0.71% (N=1768) | %54.8 / +1.58% (N=1753) | +4.52% / -3.30% | %15.9 | %58.7 |
| SHORT — TÜM | 2450 | %50.2 / +0.01% (N=2448) | %47.3 / -0.42% (N=2431) | %46.8 / -0.65% (N=2420) | %47.9 / -0.66% (N=2350) | +3.75% / -3.55% | %18.2 | %64.9 |
| SHORT — QUALIFIED | 509 | %49.7 / -0.01% (N=509) | %51.7 / +0.36% (N=505) | %50.1 / +0.01% (N=503) | %51.0 / +0.95% (N=494) | +3.94% / -3.39% | %19.3 | %71.3 |
| SHORT — STRONG | 1121 | %49.6 / -0.02% (N=1120) | %45.3 / -0.64% (N=1114) | %46.4 / -0.62% (N=1110) | %47.2 / -0.92% (N=1081) | +3.57% / -3.63% | %18.0 | %67.4 |
| SHORT — EXTREME | 820 | %51.4 / +0.12% (N=819) | %47.2 / -0.47% (N=812) | %45.2 / -1.15% (N=807) | %47.0 / -0.92% (N=775) | +3.89% / -3.66% | %17.7 | %57.4 |

## Piyasa yönüne göre ayrım

Piyasa yönü, XU100'ün aynı günkü günlük kapanışta kendi 50 günlük EMA'sının üstünde/altında olmasına göre belirlendi. Bu yalnız sonuç segmentidir; BME puanına dahil edilmedi.

| Sinyal | Olay | 1 gün | 5 gün | 10 gün | 20 gün | 5g MFE / MAE | FAIL | Retest |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| LONG — XU100 BOĞA | 1042 | %46.7 / -0.20% (N=1037) | %51.5 / +0.18% (N=1027) | %51.5 / +0.33% (N=1027) | %52.9 / +0.89% (N=1027) | +4.17% / -3.16% | %16.5 | %61.6 |
| LONG — XU100 AYI/YATAY | 168 | %48.8 / -0.08% (N=168) | %47.8 / -0.38% (N=161) | %50.0 / -0.01% (N=146) | %52.5 / +1.19% (N=118) | +5.11% / -4.26% | %22.0 | %58.9 |
| SHORT — XU100 BOĞA | 596 | %54.0 / +0.23% (N=594) | %55.5 / +0.76% (N=589) | %52.5 / +0.72% (N=589) | %51.6 / +0.84% (N=589) | +4.27% / -2.73% | %13.9 | %68.6 |
| SHORT — XU100 AYI/YATAY | 422 | %44.3 / -0.48% (N=422) | %32.4 / -2.33% (N=410) | %30.8 / -3.29% (N=399) | %33.4 / -4.00% (N=329) | +2.85% / -5.03% | %26.5 | %59.5 |

## Retest sonrası gerçekçi giriş

Bu bölüm ilk kırılımı değil, retesti doğrulayan mumun kapanışından sonraki getiriyi ölçer. Bu nedenle ilk sinyal performansıyla doğrudan karşılaştırırken girişin daha geç olduğu unutulmamalıdır.

| Sinyal | Olay | 1 gün | 5 gün | 10 gün | 20 gün | 5g MFE / MAE | FAIL | Retest |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| LONG — TÜM | 1687 | %47.7 / -0.12% (N=1684) | %50.6 / +0.08% (N=1672) | %51.9 / +0.41% (N=1664) | %54.7 / +1.66% (N=1647) | +4.08% / -3.28% | %0.0 | %100.0 |
| LONG — QUALIFIED | 153 | %51.3 / +0.08% (N=152) | %44.3 / -0.79% (N=149) | %48.3 / -0.34% (N=149) | %49.7 / -0.12% (N=149) | +3.53% / -3.37% | %0.0 | %100.0 |
| LONG — STRONG | 481 | %49.7 / -0.03% (N=479) | %52.1 / +0.44% (N=478) | %53.1 / +0.71% (N=478) | %55.5 / +2.12% (N=472) | +4.25% / -3.13% | %0.0 | %100.0 |
| LONG — EXTREME | 1053 | %46.2 / -0.20% (N=1053) | %50.8 / +0.10% (N=1045) | %51.9 / +0.44% (N=1037) | %55.1 / +1.92% (N=1026) | +4.09% / -3.32% | %0.0 | %100.0 |
| SHORT — TÜM | 1589 | %50.3 / +0.02% (N=1589) | %45.4 / -0.58% (N=1578) | %46.6 / -0.58% (N=1570) | %48.9 / -0.30% (N=1529) | +3.39% / -3.61% | %0.0 | %100.0 |
| SHORT — QUALIFIED | 363 | %57.6 / +0.31% (N=363) | %53.2 / +0.36% (N=361) | %51.2 / +0.26% (N=361) | %53.8 / +0.99% (N=353) | +4.16% / -3.07% | %0.0 | %100.0 |
| SHORT — STRONG | 755 | %49.3 / -0.10% (N=755) | %42.1 / -1.05% (N=749) | %43.5 / -0.93% (N=745) | %46.6 / -0.72% (N=727) | +3.13% / -3.87% | %0.0 | %100.0 |
| SHORT — EXTREME | 471 | %46.3 / -0.15% (N=471) | %44.7 / -0.68% (N=468) | %48.1 / -0.46% (N=464) | %48.6 / -0.69% (N=449) | +3.28% / -3.70% | %0.0 | %100.0 |
