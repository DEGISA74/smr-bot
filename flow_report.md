# BAŞKA FLOW SİNYALLERİ — ÖRNEKLEM-DIŞI + CMF korelasyon
train 2024-06-14→2025-11-04 (n=70,064) · test >2025-11-04 (n=80,472)

sinyal         train    test   CMF-kor  durum
cmf20 (çıta)   +2.06   +1.60      1.00  👑
ad_slope       +1.85   +1.57     +1.00  ✅ DAYANIKLI (ama CMF kopyası)
udvr           +1.80   +0.75     +0.57  ✅ DAYANIKLI · ORTOGONAL
force          +0.75   -0.24     +0.47  ❌ ters
vpt_slope      +1.43   +0.68     +0.63  ✅ DAYANIKLI (ama CMF kopyası)
cmf10          +1.20   +1.17     +0.78  ✅ DAYANIKLI (ama CMF kopyası)
cmf40          +1.02   +0.92     +0.80  ✅ DAYANIKLI (ama CMF kopyası)
vol_exp        +0.06   -1.47     +0.20  ❌ ters

## Okuma
- Değerli sinyal = DAYANIKLI (iki dönem +) VE ORTOGONAL (CMF-kor < 0.6 → yeni bilgi).
- Dayanıklı ama CMF-kor yüksek = CMF'i tekrarlıyor, katkı yok.