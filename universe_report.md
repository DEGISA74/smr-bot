# UNIVERSE SNAPSHOT — FEATURE GÜÇ RAPORU
Üretim: 2026-06-21 23:05 · değerlendirilen satır: 150,536 · hisse: 591 · gün: 284
Evren tabanı (10g): ort getiri %+1.94 · yukarı oranı %52.6

## Sürekli feature gücü (10g ileri getiri, en güçlü → zayıf)
feature        spread    üst%5    alt%5  monoton        n
illiq_21       +1.70    +2.75    +1.06     hayır  149,016
obv_slope      +1.03    +2.63    +1.60     hayır  149,924
rsi            +0.92    +2.67    +1.75     hayır  149,914
mfi            +0.89    +2.45    +1.56     hayır  149,898
rev_21         +0.87    +2.63    +1.75     hayır  150,536
p52            +0.77    +2.75    +1.97     hayır  150,536
lowvol_60      -0.46    +1.65    +2.11     hayır  150,536
mom_12_1       +0.42    +2.45    +2.03     hayır   50,492
rsi_slope      -0.21    +1.51    +1.73     hayır  149,890
mfi_slope      -0.19    +1.55    +1.75     hayır  149,879
cmf_slope      +0.16    +1.93    +1.78     hayır  149,907
p52_slope      -0.05    +1.68    +1.73     hayır  150,536

## Kategorik feature gücü (10g)
- obv_div: spread %6.99 · en iyi notr (+8.5%) · en kötü boga_uyumsuzluk (+1.5%) · n=150,536
- cmf: spread %1.57 · en iyi pos (+3.0%) · en kötü turning_up (+1.4%) · n=149,828
- rsi_dual: spread %1.48 · en iyi asiri_alim (+2.8%) · en kötü guclenen (+1.4%) · n=149,833
- mfi_dual: spread %0.90 · en iyi asiri_alim (+2.4%) · en kötü guclenen (+1.5%) · n=149,828
- vp: spread %0.42 · en iyi akumulasyon (+2.1%) · en kötü denge (+1.6%) · n=150,089

## Korelasyon (kopya/ölü ağırlık avı)
- Yüksek korele çift (biri elenebilir): yok — hepsi ayrı bilgi taşıyor

## Ay bazında evren tabanı (10g) — rejim var mı
  2025-05: % -0.96  (n=8,825)
  2025-06: % +4.35  (n=10,635)
  2025-07: % +7.38  (n=12,386)
  2025-08: % +1.65  (n=11,839)
  2025-09: % +0.52  (n=12,440)
  2025-10: % +1.56  (n=12,760)
  2025-11: % -1.27  (n=11,600)
  2025-12: % +1.06  (n=12,760)
  2026-01: % +4.08  (n=12,213)
  2026-02: % -1.76  (n=11,644)
  2026-03: % +2.44  (n=12,266)
  2026-04: % +5.29  (n=12,300)
  2026-05: % -0.86  (n=8,237)

## REJİM AYRIMI (XU100 SMA50: boğa vs ayı) — spread her rejimde de tutuyor mu?
rejim        n   taban       cmf  mom_12_1  lowvol_6    rev_21  illiq_21   obv_div       p52
ayı     42,979   +3.13     +0.75     -0.62     -1.24     -0.42     -0.48    +17.20     +0.51
boğa   106,926   +1.47     +1.89     +0.40     -0.43     +2.27     +2.47     +4.23     +1.24
→ Spread her iki rejimde de pozitif/yüksekse REJİM-DAYANIKLI; sadece boğada yüksekse serap.
  NOT: lowvol/rev'de NEGATİF spread anomaliyi DOĞRULAR (düşük dilim kazanır).

## Okuma
- En güçlü ayrıştırıcı: **illiq_21** (üst %5 vs alt %5 farkı %+1.70, monoton DEĞİL).
- 'spread' ≈ 0 olan feature ölü ağırlıktır → AI/skordan eleme adayı.
- Bu yalnızca tek-değişkenli güç; eşik/rejim kalibrasyonu ayrı segment-backtest ister (extrapolation-yasak).