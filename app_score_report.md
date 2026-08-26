# APP SKOR DENETİMİ — f_master_score (gerçek app rakamı, drift yok)
Join örneği: 11,337 satır (10g) · tarih 2026-06-03→2026-07-14 · hisse 1130 · taban 10g %-1.88
⚠ f_smart_money_score / f_ict_model / f_sentiment_score = VERİ YOK (hiç yazılmamış) → ölçülemez.
⚠ ret_20g henüz olgunlaşmadı (skorlar 3 Haz'dan beri yazılıyor) → 10g pencere.

## f_master_score → 10g getiri (quintile, düşük→yüksek skor)
  Q1: % -1.90  (n=2,450)
  Q2: % -1.52  (n=2,184)
  Q3: % -2.90  (n=2,241)
  Q4: % -1.50  (n=2,260)
  Q5: % -1.58  (n=2,202)
→ spread (üst-alt): %+0.32 · monoton: HAYIR · n=11,337

## Aynı örnekte ham feature gücü (kıyas)
- 52H konum: spread %+3.76 (mono e, n=11,337)
- RSI: spread %+0.38 (mono h, n=11,335)

## REJİM AYRIMI — master_score üst-yarı vs alt-yarı (medyan böl)
rejim         n     üst½     alt½     fark
ayı       5,484    -1.33    -2.17    +0.85
boğa      5,853    -1.94    -2.06    +0.12

→ Her iki rejimde de fark POZİTİF ise master_score REJİM-DAYANIKLI;

## Okuma
- Pozitif + monoton spread = master_score gerçekten ayrıştırıyor; ≈0 = ölü.
- Bu yalnızca tarama-tetikli alt küme (seçim yanlılığı) + 10g + küçük örnek → İLK okuma.
- smart_money/ict/sentiment ölçmek için önce o kolonların yazımı düzeltilmeli (ayrı iş).

## Altın / Platin / VIP → rozet katkı denetimi
Her satır yalnız bağımsız olay başlangıcıdır. “Aynı-gün farkı”, rozetli hissenin 10 seans getirisinden o gün diğer taramaların bulduğu hisselerin ortalamasını çıkarır.

| Rozet | Bağımsız olay | 10 seans ort. | Pozitif oran | Aynı-gün eşleşme | Aynı-gün farkı | Yalnız rozet |
|---|---:|---:|---:|---:|---:|---:|
| altin_setup | 570 | %-2.67 | %30.7 | 570 | %-0.50 | 105 |
| platin_setup | 6 | %-2.98 | %33.3 | 6 | %-0.65 | 1 |
| vip_formasyon | 732 | %-0.85 | %37.7 | 732 | %-0.03 | 317 |

Not: Bu eşleştirme nedensellik kanıtı değildir; fakat rozetlerin günlük tekrarlarla şişmeden ve aynı piyasa günü koşulunda bağımsız katkısını görünür kılar.