# STRATEJİ BACKTEST — Haftalık CMF-top-10 (10g)
gün 284 · evren-taban 10g %+1.89 · taban winrate %53

strateji                                n  ort.ret  winrate  XU100 geç
CMF-top (ham, yan koşulsuz)         2,661   +4.19      55%        44%
+ trend (p52≥50)                    2,647   +4.13      55%        44%
+ aşırı-alım değil (rsi<70)         2,646   +2.31      51%        41%
+ uzamamış (p52 30-75)              2,627   +2.07      53%        40%
+ düşük-vol (gün-içi alt yarı)      2,651   +2.55      56%        43%
+ momentum>0 (12-1ay)                 917   +4.80      61%        56%
+ trend & aşırı-alım değil          2,633   +2.56      52%        42%

## Rejim ayrımı (ham CMF-top)
  ayı: winrate %58 · ort %+5.11 · XU100 geç %47 (n=1,020)
  boğa: winrate %54 · ort %+3.90 · XU100 geç %52 (n=1,320)

## Okuma
- 'winrate' = pozitif çıkan pay; 'XU100 geç' = endeksi yenen pay (asıl ölçü).
- Bir yan-koşul winrate'i belirgin artırıyorsa → canlı listeye o filtreyi ekleriz.