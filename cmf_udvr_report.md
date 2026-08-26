# CMF + UDVR KOMBİNASYON — ÖRNEKLEM-DIŞI (top 10, 10g)
train ≤2025-11-04 · test >2025-11-04

dönem  sıralama    winrate  beklenti  XU geç
TRAIN  CMF             55%    +5.40     41%
TRAIN  UDVR            55%    +4.47     42%
TRAIN  CMF+UDVR        54%    +4.96     43%

TEST   CMF             57%    +4.53     52%
TEST   UDVR            59%    +4.14     50%
TEST   CMF+UDVR        59%    +5.27     53%

## Karar
- TEST döneminde CMF+UDVR > CMF ise → UDVR gerçek katkı, motora ekle.
- TEST'te eşit/düşükse → seyreltiyor, CMF tek başına yeterli (flow için eksiksiz).