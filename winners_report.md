# KAZANANLARIN ORTAK NOKTASI — ÖRNEKLEM-DIŞI DOĞRULAMA
train: 2024-06-14→2025-11-04 (n=70,064, taban %+2.41) · test: >2025-11-04→2026-05-22 (n=80,472, taban %+1.53)

Spread = üst dilim − alt dilim 10g getiri farkı (kazananları ayırma gücü).

## Sürekli özellikler
  cmf20        train %+2.06 · test %+1.60   ✅ DAYANIKLI
  illiq_21     train %+2.43 · test %+0.91   ✅ DAYANIKLI
  obv_slope    train %+1.30 · test %+0.80   ✅ DAYANIKLI
  p52          train %+0.81 · test %+0.48   ✅ DAYANIKLI
  rev_21       train %+1.13 · test %+0.45   ✅ DAYANIKLI
  rsi          train %+1.46 · test %+0.37   ⚠ zayıf/karışık
  cmf_slope    train %-0.28 · test %+0.51   ❌ TERS DÖNDÜ (curve-fit)
  p52_slope    train %-0.44 · test %+0.21   ❌ TERS DÖNDÜ (curve-fit)
  mfi          train %+1.61 · test %+0.17   ⚠ zayıf/karışık
  rsi_slope    train %-0.63 · test %+0.11   ❌ TERS DÖNDÜ (curve-fit)
  mfi_slope    train %-0.34 · test %-0.08   ⚠ zayıf/karışık
  lowvol_60    train %-1.21 · test %-0.08   ⚠ zayıf/karışık
  sq           (yetersiz veri)
  mom_12_1     (yetersiz veri)

## Kategorik özellikler (en iyi−en kötü spread)
  cmf          train %+1.65 · test %+1.94   ✅ DAYANIKLI
  vp           train %+0.80 · test %+0.54   ✅ DAYANIKLI
  rsi_dual     train %+2.27 · test %+0.70   ✅ DAYANIKLI
  mfi_dual     train %+1.56 · test %+0.32   ⚠ zayıf/karışık
  obv_div      train %+21.58 · test %+1.46   ✅ DAYANIKLI

## Okuma
- ✅ DAYANIKLI = her iki yarıda da aynı yönde + anlamlı → GERÇEK kazanan-özelliği.
- ❌ TERS DÖNDÜ = bir yarıda artı, diğerinde eksi → o pencereye uydurulmuş, GÜVENME.
- Motoru sadece ✅ olanlarla güçlendir; gerisi gürültü.