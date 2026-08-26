# TREND × FLOW ÇELİŞKİ BACKTEST (gorev4 mekanizma testi)
Üretim: 2026-06-23 15:09 · satır: 50,236 · tarih: 2026-01-13 → 2026-05-22
Trend(yavaş)=mom_12_1>0 · Flow(hızlı)=cmf20>0 · * = çelişki (divergence) kovaları

## TÜM DÖNEM
kova                      N   ret5%  ret10%  ret20%  hit10%
T↑F↑ uyumlu-yukarı   14,226   +1.12   +2.04   +3.60    53.0
T↑F↓ güce dağıtım*   20,793   +0.67   +1.62   +3.50    52.3
T↓F↑ gizli birikim*   4,618   +1.15   +2.16   +2.59    53.4
T↓F↓ uyumlu-aşağı    10,599   +1.02   +1.93   +3.24    53.8
(evren tabanı 10g: +1.85%)

## REJİM: ayı
kova                      N   ret5%  ret10%  ret20%  hit10%
T↑F↑ uyumlu-yukarı    3,316   +2.19   +4.64   +9.06    62.9
T↑F↓ güce dağıtım*    5,904   +1.83   +3.82   +8.31    64.8
T↓F↑ gizli birikim*     671   +2.11   +6.55  +11.54    68.4
T↓F↓ uyumlu-aşağı     2,428   +2.09   +5.49  +11.54    68.2

## REJİM: boğa
kova                      N   ret5%  ret10%  ret20%  hit10%
T↑F↑ uyumlu-yukarı   10,901   +0.79   +1.25   +1.94    49.9
T↑F↓ güce dağıtım*   14,881   +0.21   +0.74   +1.59    47.4
T↓F↑ gizli birikim*   3,946   +0.98   +1.42   +1.07    50.8
T↓F↓ uyumlu-aşağı     8,169   +0.70   +0.88   +0.78    49.5

## OOS: train (< 2026-03-18)
kova                      N   ret5%  ret10%  ret20%  hit10%
T↑F↑ uyumlu-yukarı    7,046   +0.94   +1.37   +2.36    51.1
T↑F↓ güce dağıtım*   10,295   +0.19   +0.33   +1.71    45.6
T↓F↑ gizli birikim*   2,590   +0.71   +0.95   +0.30    53.3
T↓F↓ uyumlu-aşağı     5,624   +0.50   +0.43   +0.76    48.0

## OOS: test (>= 2026-03-18)
kova                      N   ret5%  ret10%  ret20%  hit10%
T↑F↑ uyumlu-yukarı    7,180   +1.29   +2.70   +4.82    54.8
T↑F↓ güce dağıtım*   10,498   +1.14   +2.89   +5.25    59.0
T↓F↑ gizli birikim*   2,028   +1.70   +3.71   +5.52    53.5
T↓F↓ uyumlu-aşağı     4,975   +1.62   +3.63   +6.04    60.4

## OKUMA
- Hipotez 1: T↑F↓ (güce dağıtım) < T↑F↑ → uptrend'de para çıkışı KÖTÜ habercisi.
- Hipotez 2: T↓F↑ (gizli birikim) > T↓F↓ → downtrend'de para girişi İYİ habercisi (dönüş).
- KARAR: fark hem İKİ REJİMDE hem TEST'te (OOS) tutuyorsa mekanizma GERÇEK; sadece train/boğada ise serap.