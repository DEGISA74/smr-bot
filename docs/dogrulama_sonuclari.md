# DOĞRULAMA SONUÇLARI — ST-EP 5.1 Çok Ölçekli Motor

**Tarih:** 24 Ağustos 2026  
**Veri Kapsamı:** 605 BIST Sembolü, 174.701 Bar (Günlük)  
**İşlem Maliyeti:** 20 bps (komisyon + kayma)  
**Karar Kuralları:** `docs/dogrulama_plani.md` Bölüm 7'ye tam sadık kalınarak değerlendirilmiştir.

---

## 1. ÖZET SAYILAR (H1: Uyum >= +5 & Kanaat = YÜKSEK)

| Vade | İsabet (Winrate) | Kazanç/Kayıp (RR) | Beklenen Getiri (Net) | Kâr Faktörü | %95 Güven Aralığı (Tarih Kümeli Alfa) | Kıyas (Aynı Gün Tüm Evren) | Karar |
|---|---|---|---|---|---|---|---|
| **5 Gün** | %45.3 | 1.21 | +%0.02 | 1.00 | [-%0.38, +%0.95] | KF: 1.14 / Net: +%0.40 | ❌ **Kanıt Yok** (Sıfırı içeriyor) |
| **10 Gün** | %46.6 | 1.44 | +%1.36 | 1.26 | [-%0.39, +%1.51] | KF: 1.31 / Net: +%1.15 | ❌ **Kanıt Yok** (Sıfırı içeriyor) |
| **20 Gün** | %44.2 | 1.72 | +%2.58 | 1.36 | [-%0.17, +%2.69] | KF: 1.46 / Net: +%2.26 | ❌ **Kanıt Yok** (Sıfırı içeriyor) |

> **İstatistiksel Yargı:** 20 günlük vadede ortalama alfa +%3.02 görünse de, tarihe göre kümelenmiş %95 güven aralığı **[-0.17%, +2.69%]** olup **SIFIRI İÇERMEKTEDİR**. Kâr faktörü (1.36), aynı günlerdeki rastgele evren ortalamasından (1.46) daha düşüktür.

### Likidite Dilimleri (20 Günlük)
- **Q5 (En Yüksek Likidite - BIST30):** Alfa +%2.06, Güven Aralığı [-%0.32, +%3.61] ❌ (Sıfırı içeriyor)
- **Q4 (Orta-Yüksek):** Alfa +%10.19, Güven Aralığı [+%5.56, +%10.29], KF: 2.52 ✅ (Tek izole dilim)
- **Q3 (Orta):** Alfa +%0.11, Güven Aralığı [-%0.12, +%3.95] ❌ (Sıfırı içeriyor)
- **Q2 (Düşük):** Alfa +%1.34, Güven Aralığı [-%2.27, +%1.42] ❌ (Sıfırı içeriyor)
- **Q1 (En Düşük):** Alfa +%0.45, Güven Aralığı [-%1.16, +%1.65] ❌ (Sıfırı içeriyor)

*Kural:* 5 likidite diliminin en az 3'ünde tutarlılık şartı **sağlanamadı** (yalnızca Q4'te pozitif).

---

## 2. H4 SİNYAL SAĞLIĞI TESTİ (Çıkış Kuralı Olabilir mi?)

| Durum | Gözlem (n) | 20g Medyan Alfa | 20g İsabet | 20g Kâr Faktörü | Beklenen Getiri |
|---|---|---|---|---|---|
| **TEYİTLİ** | 1.432 | -%5.01 | %43.6 | 1.28 | +%2.30 |
| **KORUNUYOR** | 20.085 | -%3.41 | %45.4 | 1.20 | +%1.14 |
| **ZAYIFLIYOR** | 71.328 | -%3.27 | %46.6 | 1.31 | +%1.53 |
| **ÇELİŞKİLİ** | 36.105 | -%3.35 | %45.4 | 1.26 | +%1.31 |

> **Bulgu:** "TEYİTLİ" sinyaller, "ZAYIFLIYOR" durumuna düşen sinyallerden daha kötü sonuçlanmıştır (Medyan alfa -%5.01 vs -%3.27). Sinyal Sağlığı bir çıkış metriği olarak **çalışmamaktadır**, gecikmeli tepki vermektedir.

---

## 3. H0 FREKANS VE VARSAYIM DENETİMİ

- **19 Hacim Senaryosu:** S8 (n=3), S18 (n=14), S13 (n=42), S14 (n=132) gibi senaryolar pratikte neredeyse hiç tetiklenmemiştir (<%0.1). Ölçülemez kural borcu yaratmaktadır.
- **MEM (Tek Skor):** Ensemble baskı skoruyla sayısal olarak aynıdır; bağımsız bilgi üretmemektedir.

---

## 4. BÖLÜM 7 KARAR KURALLARINA GÖRE NİHAİ TABLO

| Modül / Parça | Karar | Gerekçe |
|---|---|---|
| **6 Ölçekli Trend & Uyum (H1)** | ❌ **REDDEDİLDİ** | Güven aralığı sıfırı içeriyor, kâr faktörü (1.36) evrenden (1.46) zayıf, 5 dilimin 4'ünde kanıt yok. |
| **Sinyal Sağlığı / Erime (H4)** | ❌ **REDDEDİLDİ** | Zayıflayan sinyaller teyitlilerden daha iyi performans verdi; çıkış filtresi olamaz. |
| **19 Hacim Senaryosu & MEM** | ❌ **ÇÖPE (Reddedildi)** | Senaryoların çoğu istatistiksel olarak ölçülemez frekansta (<1000 bar), MEM gereksiz kopya. |
| **Kaufman Hareket Verimliliği (ER)** | ⏸ **BEKLEMEDE / İZLEMEDE** | Saf gösterge olarak SMR analiz katmanında (tek başına yön üretmeden) incelenebilir. |
