# 🧪 AİLE-GRUPLU ABLATION RAPORU
_Üretim: 2026-07-28 · aile net-tavan: AÇIK (üretim terazi davranışı) · işlem maliyeti: 40 bp (%0.40)_

> ⚠️ **VERİ TEK REJİM VE YALNIZCA 2 BAĞIMSIZ TARİH.** Eski tarihlerde bazı aileler hiç kaydedilmediği için test yalnızca dört ailenin de gözlendiği ortak dönemi kullanır. Verdict MUTLAK değil — yalnızca erken sıralama ipucu.

**Örneklem:** aynı hisse-gün tek kayıt; XU100 işlem takviminde 10 seans aralıklı tarihler; DEV/VAL benzersiz günlerden ayrıldı. 27,631 tekil satır → 10,361 dört ailesi de gözlenmiş satır → 1,205 çakışmasız satır · 2 tarih.

**Okuma:** Δ net alpha10 = FULL üst kohort net fazla getirisi − aile çıkarılmış üst kohort net fazla getirisi. **Pozitif değer = aile çıkınca sonuç kötüleşti, aile katkı sağlıyor.** Net değerlerden işlem maliyeti düşülmüştür.

**Çakışmasız tarihler:** 2026-06-16, 2026-06-30


## TÜM EVREN  (n=1205)

| Config | top n | ham 5g | ham 10g | ham 20g | XU100 10g | alpha 10g | net 10g | net alpha 10g | net hit10% | Δ net alpha10 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| FULL | 241 | 0.25 | **-1.19** | -2.22 | -1.49 | 0.3 | -1.59 | **-0.1** | 36.93 | — |
| −akis | 241 | 0.37 | **-1.0** | -2.29 | -1.43 | 0.43 | -1.4 | **0.03** | 36.93 | -0.13 |
| −cmf | 241 | 0.23 | **-1.4** | -2.31 | -1.56 | 0.15 | -1.8 | **-0.25** | 35.27 | +0.15 |
| −momentum | 241 | 0.37 | **-1.2** | -2.39 | -1.49 | 0.29 | -1.6 | **-0.11** | 37.76 | +0.01 |
| −yapi | 241 | 0.2 | **-0.91** | -2.26 | -1.65 | 0.74 | -1.31 | **0.34** | 37.34 | -0.44 |

## KONTROL — YÜKSEK LİKİDİTE  (n=145)

_Yetersiz veri._

## KONTROL — ORTA LİKİDİTE  (n=332)

| Config | top n | ham 5g | ham 10g | ham 20g | XU100 10g | alpha 10g | net 10g | net alpha 10g | net hit10% | Δ net alpha10 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| FULL | 67 | -1.14 | **-2.2** | None | -0.3 | -1.9 | -2.6 | **-2.3** | 32.84 | — |
| −akis | 67 | -1.2 | **-2.4** | None | -0.3 | -2.11 | -2.8 | **-2.51** | 29.85 | +0.21 |
| −cmf | 67 | -1.14 | **-2.51** | None | -0.3 | -2.21 | -2.91 | **-2.61** | 26.87 | +0.31 |
| −momentum | 67 | -1.21 | **-2.28** | None | -0.3 | -1.99 | -2.68 | **-2.39** | 31.34 | +0.09 |
| −yapi | 67 | -0.5 | **-1.34** | None | -0.3 | -1.05 | -1.74 | **-1.45** | 34.33 | -0.85 |

## KONTROL — YÜKSEK + ORTA LİKİDİTE  (n=477)

| Config | top n | ham 5g | ham 10g | ham 20g | XU100 10g | alpha 10g | net 10g | net alpha 10g | net hit10% | Δ net alpha10 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| FULL | 96 | -0.22 | **-1.26** | None | -0.3 | -0.96 | -1.66 | **-1.36** | 37.5 | — |
| −akis | 96 | 0.18 | **-0.6** | None | -0.3 | -0.31 | -1.0 | **-0.71** | 39.58 | -0.65 |
| −cmf | 96 | -0.6 | **-1.33** | None | -0.3 | -1.03 | -1.73 | **-1.43** | 36.46 | +0.07 |
| −momentum | 96 | -0.37 | **-1.27** | None | -0.3 | -0.97 | -1.67 | **-1.37** | 37.5 | +0.01 |
| −yapi | 96 | 0.58 | **-0.15** | None | -0.3 | 0.15 | -0.55 | **-0.25** | 41.67 | -1.11 |

## KONTROL — DEV — ilk 1 gün (2026-06-16 → 2026-06-16)  (n=603)

| Config | top n | ham 5g | ham 10g | ham 20g | XU100 10g | alpha 10g | net 10g | net alpha 10g | net hit10% | Δ net alpha10 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| FULL | 121 | 0.18 | **-1.47** | -2.56 | -2.56 | 1.09 | -1.87 | **0.69** | 35.54 | — |
| −akis | 121 | 0.46 | **-0.98** | -2.21 | -2.56 | 1.58 | -1.38 | **1.18** | 37.19 | -0.49 |
| −cmf | 121 | 0.29 | **-2.09** | -2.95 | -2.56 | 0.47 | -2.49 | **0.07** | 32.23 | +0.62 |
| −momentum | 121 | 0.56 | **-1.16** | -2.45 | -2.56 | 1.4 | -1.56 | **1.0** | 39.67 | -0.31 |
| −yapi | 121 | -0.53 | **-2.32** | -2.97 | -2.56 | 0.25 | -2.72 | **-0.15** | 32.23 | +0.84 |

## KONTROL — VAL — son 1 gün (2026-06-30 → 2026-06-30)  (n=602)

| Config | top n | ham 5g | ham 10g | ham 20g | XU100 10g | alpha 10g | net 10g | net alpha 10g | net hit10% | Δ net alpha10 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| FULL | 121 | 0.06 | **-1.42** | -11.57 | -0.3 | -1.12 | -1.82 | **-1.52** | 35.54 | — |
| −akis | 121 | 0.21 | **-0.99** | -11.57 | -0.3 | -0.69 | -1.39 | **-1.09** | 37.19 | -0.43 |
| −cmf | 121 | -0.31 | **-1.67** | -11.57 | -0.3 | -1.37 | -2.07 | **-1.77** | 35.54 | +0.25 |
| −momentum | 121 | 0.11 | **-1.16** | -11.57 | -0.3 | -0.86 | -1.56 | **-1.26** | 37.19 | -0.26 |
| −yapi | 121 | 0.82 | **-0.28** | -11.57 | -0.3 | 0.01 | -0.68 | **-0.39** | 38.84 | -1.13 |

## 📋 Açık kararlar (verdict'ten önce çöz)

- 1) KOHORT SEÇİMİ: 'kompozit üst %20'yi seç' mi yoksa 'decile-spread korelasyon' mu birincil metrik? Şu an ikisi de raporlanıyor, verdict üst-%20 kohort deltası.
- 2) f_rsi_mfi_bouquet oyu: İş 4'te yönsüz diye skordan çıktı → burada NÖTR. Doğru mu, yoksa dual-türev olarak momentum ailesine mi katılsın?
- 3) f_spike_dominance yön semantiği belirsiz (0-31 sayaç) → şimdilik NÖTR. Borsacıya sor: baskınlık kimin lehine sayılıyor?
- 4) XU100 FAZLA GETİRİ: yerel XU100 kapanışından aynı 5/10/20 işlem günlük benchmark getirisi hesaplanır; net alpha = hisse getirisi − XU100 − işlem maliyeti.
- 5) AĞIRLIKLAR: akis üye 0.5 + net-tavan 0.5, cmf 1.0, momentum/yapı 0.5 — terazi ile aynı. Üretim karşılaştırmasında net-tavan daima AÇIK tutulur.
- 6) VERİ TEK REJİM; ortak aile dönemi 2026-06-16'da başlıyor. Yalnızca iki çakışmasız tarih var — mutlak verdict verme, erken sıralama ipucu olarak oku.