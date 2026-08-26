# STRATEJİ LAB — CMF+momentum winrate geliştirme
örneklem 50,236 satır · gün 93

## A) SEPET BÜYÜKLÜĞÜ (mom>0 gate, CMF rank, 10g)
top 5                    win %  63 · beklenti %+6.50 · kazanç %+16.9/kayıp %-11.5 · XU geç %  58 · n=463
top 10                   win %  61 · beklenti %+4.80 · kazanç %+14.3/kayıp % -9.9 · XU geç %  56 · n=917
top 15                   win %  59 · beklenti %+4.22 · kazanç %+13.9/kayıp % -9.4 · XU geç %  55 · n=1,367
top 20                   win %  56 · beklenti %+3.70 · kazanç %+13.9/kayıp % -9.4 · XU geç %  53 · n=1,817

## B) TUTMA SÜRESİ (top 10, mom>0, CMF rank)
5 gün                    win %  56 · beklenti %+2.34 · kazanç % +9.4/kayıp % -6.7 · XU geç %  52 · n=917
10 gün                   win %  61 · beklenti %+4.80 · kazanç %+14.3/kayıp % -9.9 · XU geç %  56 · n=917
20 gün                   win %  55 · beklenti %+7.00 · kazanç %+23.6/kayıp %-13.1 · XU geç %  50 · n=917

## C) GATE: ham momentum vs risk-ayarlı (mom/vol), top10, 10g
ham mom>0                win %  61 · beklenti %+4.80 · kazanç %+14.3/kayıp % -9.9 · XU geç %  56 · n=917
risk-ayarlı (üst yarı)   win %  58 · beklenti %+4.30 · kazanç %+14.3/kayıp % -9.7 · XU geç %  55 · n=911

## Okuma
- Winrate TEK başına yetmez: beklenti (+) ve XU geç birlikte yükselmeli.
- 'kazanç/kayıp' asimetrisi: yüksek winrate ama büyük kayıp = tuzak.