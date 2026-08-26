# 🔬 UNIVERSE EDGE — REJİM-DAYANIKLI SİNYAL KEŞFİ
_150K satır · 2 yıl · fwd_ret_10g · boğa/ayı = XU100 SMA50_

**Rejim dağılımı:** boğa n=106,926 (taban +1.47) · ayı n=42,979 (taban +3.13)

## Sürekli metrikler — rejim ayrımı (üst−alt çeyreklik, 10g)

| Metrik | Genel | 🐂 Boğa | 🐻 Ayı | Karar | n |
|---|--:|--:|--:|---|--:|
| `cmf20` | +1.78 | +2.40 | +0.72 | 🟢 REJİM-DAYANIKLI | 149,924 |
| `p52` | +0.77 | +1.24 | +0.51 | 🟢 REJİM-DAYANIKLI | 150,536 |
| `lowvol_60` | -0.46 | -0.43 | -1.24 | 🟡 tek-rejim (biri zayıf) | 150,536 |
| `mfi_slope` | -0.19 | -0.05 | +0.00 | ÖLÜ (edge yok) | 149,879 |
| `rsi_slope` | -0.21 | -0.01 | +0.35 | ÖLÜ (edge yok) | 149,890 |
| `obv_slope` | +1.03 | +1.96 | -0.04 | 🔴 SERAP (yön rejime göre değişiyor) | 149,924 |
| `mfi` | +0.89 | +1.96 | -0.04 | 🔴 SERAP (yön rejime göre değişiyor) | 149,898 |
| `rsi` | +0.92 | +2.20 | -0.26 | 🔴 SERAP (yön rejime göre değişiyor) | 149,914 |
| `mom_12_1` | +0.42 | +0.40 | -0.62 | 🔴 SERAP (yön rejime göre değişiyor) | 50,492 |
| `rev_21` | +0.87 | +2.27 | -0.42 | 🔴 SERAP (yön rejime göre değişiyor) | 150,536 |
| `cmf_slope` | +0.16 | +0.48 | -0.45 | ÖLÜ (edge yok) | 149,907 |
| `illiq_21` | +1.70 | +2.47 | -0.48 | 🔴 SERAP (yön rejime göre değişiyor) | 149,016 |
| `p52_slope` | -0.05 | +0.85 | -0.75 | 🔴 SERAP (yön rejime göre değişiyor) | 150,536 |

## Kategorik metrikler — en iyi/en kötü kova (10g, rejim ayrı)

- **`cmf`** · 🐂 en iyi **pos** (+2.77) / en kötü turning_up (+0.88) · 🐻 en iyi **pos** (+3.62) / en kötü strong_neg (+2.87)
- **`rsi_dual`** · 🐂 en iyi **asiri_alim** (+2.84) / en kötü asiri_satim (+0.77) · 🐻 en iyi **zayiflayan** (+3.12) / en kötü asiri_alim (+2.89)
- **`mfi_dual`** · 🐂 en iyi **asiri_alim** (+2.32) / en kötü asiri_satim (+0.43) · 🐻 en iyi **notr** (+3.48) / en kötü asiri_alim (+2.59)
- **`obv_div`** · 🐂 en iyi **notr** (+4.71) / en kötü teyit_asagi (+0.48) · 🐻 en iyi **notr** (+20.05) / en kötü ayi_uyumsuzluk (+2.85)
- **`vp`** · 🐂 en iyi **akumulasyon** (+1.65) / en kötü denge (+1.21) · 🐻 en iyi **akumulasyon** (+3.16) / en kötü denge (+2.65)

## Kombinasyon testi — dayanıklı sinyaller birleşince

Dayanıklı sürekli metrikler: ['cmf20', 'p52']

- Baseline (tüm evren): ret +1.89
- Sadece CMF20 yüksek: n=59,970 · ret +2.39 · hit 54.3% [🐂+2.15 / 🐻+3.15]
- Sadece P52 yüksek: n=59,970 · ret +2.06 · hit 51.8% [🐂+1.73 / 🐻+3.16]
- **CMF20 + P52 birlikte yüksek**: n=37,411 · ret +2.32 · hit 53.3% [🐂+2.06 / 🐻+3.17]