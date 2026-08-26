# Master Scan Giriş Senaryoları — 30 Eylül İzleme Notu

Güncelleme: 23 Ağustos 2026

## Mutabakat

- 30 Eylül 2026'ya kadar B11, C6, Zirve Devam ve Radar2'nin ana tarama eşikleri, sıralaması ve mevcut sinyal davranışı değiştirilmez.
- Üçlü yalnızca kalite/teyit katmanıdır: Göreceli Güç + MA20 + ATR gücü.
- **Anında giriş:** T0 sinyal kapanışı sonrası ilk işlem günü açılış.
- **Teyitli giriş:** T+3 kapanışında üçlü 3/3 korunuyorsa T+4 açılış.
- İki senaryo aynı olay anahtarını kullanır; teyitli giriş ikinci sinyal sayılmaz.
- Radar2 üçlü sonucu rozet olarak gösterilir; Radar2 tek başına C6 ile birleşmiş kabul edilmez.

## 30 Eylül'e kadar yalnızca gözlenecek ölçümler

- 5g / 10g / 20g doğal vade sonuçları.
- Anında ve teyitli giriş karşılaştırması.
- MA20'den ATR cinsinden uzaklık, son 5/10 seans getirisi ve aşırı uzama etiketi.
- MAE/MFE, gap, işlem maliyeti ve kayma etkisi.
- Radar2 → C6 geçişi; sembol eşleşmesi değil, olay-zaman eşleşmesi.
- Zirve Devam için ham aday → son filtre geçişi; üç Master Scan üst üste sıfırsa veri/filtre sağlık alarmı.

## Kanıt notları

- 7–21 Ağustos XU100 getirisi yaklaşık +%5,3; Zirve Devam'ın sıfır çekmesi otomatik olarak piyasa düşüşü diye açıklanamaz.
- 7 Ağustos–21 Ağustos canlı kohortunda Radar2 ve C6 kesişimi olay-zaman açısından zayıf; bu ilişki hipotez olarak kalır.
- Stop-loss eklendiğinde RR'nin otomatik artacağı varsayılmaz; stop, MAE/MFE ve gap sıralamasıyla ölçülür.
- N<30 olan vade/senaryo sonuçları karar verdirmez.

## Dağıtım durumu

- `app.py` ve `master_scan_giris_senaryolari.py` VPS'e yedekli olarak gönderildi.
- VPS'te Python derleme kapısı geçti; `patron-radar` ve `free-showcase` aktif, health HTTP 200.
- VPS senaryo fişi kendi `patron.db` ve parquet verisinden üretildi.
- VPS'te gerçek Master Scan yazımı gölge modda olduğu için yeni senaryo fişi cron'u eklenmedi; stale veriyi canlı birikim gibi göstermemek için bu karar bilinçlidir.

## Karar tarihi

30 Eylül'de her tarama için örneklem büyüklüğü, vade, win rate, RR, PF, BIST alfa, maliyet sonrası getiri ve Radar2→C6 olay geçişi birlikte değerlendirilir. O tarihe kadar ana filtreler dondurulmuştur.
