# CROSSOVER / ÖNCÜ MA SİNYALLERİ — ÖRNEKLEM-DIŞI
train 2024-06-14→2025-11-04 (n=70,064) · test >2025-11-04 (n=80,472)
spread = sinyalin 10g getiri ayırma gücü; her iki dönemde + ve anlamlı = DAYANIKLI

## BASELINE — CMF (kıyas çıtası)
  cmf20                train %+2.06 · test %+1.60   ✅ DAYANIKLI

## ÖNCÜ (leading) MA sinyalleri
  sikisma_5_20         train %-1.35 · test %-0.35   ⚠ zayıf
  egim_e20             train %-0.34 · test %+0.05   ❌ TERS (curve-fit)
  fiyat_vs_e20         train %-0.02 · test %+0.24   ❌ TERS (curve-fit)
  taze_kesisim_5_13    train %-1.64 · test %-0.32   ⚠ zayıf

## BASELINE crossover'lar (lagging)
  dizilim_9_21         train %+0.26 · test %-0.16   ❌ TERS (curve-fit)
  golden_50_200        train %-6.50 · test %+0.99   ❌ TERS (curve-fit)

## Okuma
- CMF çıtasını GEÇEN + iki dönemde tutan bir sinyal varsa değerli; yoksa CMF zaten yeterli.
- Öncü sinyaller gecikmeli crossover'ları geçmeli (hipotez); veri ne diyor bak.