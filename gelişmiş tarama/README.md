# Gelişmiş Tarama — "Kanıt Büyümesi / T+3 Trajectory" Araştırması

Bu klasör, "600 hisseden en iyi 10'u nasıl ayırırız" sorusunun araştırma kodları ve bulgularıdır.
Tüm scriptler **SALT RAPOR**: patron.db / app.py / parquet DEĞİŞMEZ, sadece okur.

## Varılan tek cümle
Kazananı ilk gün seçemiyoruz; ama ilk sinyalden (T0) sonra **relatif güç + MA20 üstü + ATR-güç** birlikte
büyüyorsa, hisse 20 günde +%30'a **1.8 kat** daha yatkın (in-sample). "Kaç taramada çıktığı" ise
kalabalıklaşma göstergesi, olumlu değil.

## Günlük çalışma kuralı

Master Scan, seans kapanışından hemen sonra değil; gecikmeli fiyat verisi ve veri doğrulaması tamamlandıktan sonra çalıştırılır.
Hedef saat **20:00**'dir. Veri erken hazır olursa **19:00–19:30** arası çalıştırılabilir; bazı günler doğrulama/gecikme nedeniyle işlem **23:00'e kadar** sarkabilir.
Collector kesin kapanış hesabını yalnızca hazır ve doğrulanmış veriyi gördüğünde yapar.

## Dosyalar
- `trajectory_automation.py` — veri hazır kapısı, Master Scan sonrası kapanış watcher'ı,
  saatlik veri tazelik kontrolü, settle çalıştırması ve mevcut Telegram botunun
  `scheduled_msgs` kuyruğuna bildirim bırakma katmanı. Master Scan'i kendisi çalıştırmaz.
- `scanner_edge_walkforward.py` — tarama başına walk-forward kenar (ortalama-alpha). Sonuç: kanıtlanmış
  pozitif-kenarlı tarama YOK (sadece er_C6 CI alt sınırı sıfır üstü). Çıktı: scanner_edge_report.csv/json
- `confluence_walkforward.py` — "kaç taramada çıktı" (confluence) testi. Sonuç: çokluk getiriyi DÜŞÜRÜYOR
  (crowding), pozitif değil.
- `trajectory_backtest.py` — v1 trajectory (5 bileşen). Çıktı: trajectory_events.csv/report.json
- `trajectory_v3.py` — 22 sinyal, 3 sürüm, HER sinyalin tek tek lift'i, kapanış-bazlı +%30, kümelenme +
  OZATD-hariç kontrolü. Çıktı: **trajectory_v3_events.csv** (her event'in tüm bayrakları + sonuçları)
- `curated.py` — v3 CSV'den curated skor (rs+ma20+atr). En iyi sonuç burada.
- `trajectory_forward_collector.py` — canlı forward collection: `close` kapanış snapshot'ı,
  `intraday` saatlik provizyonel snapshot, `settle` olgunlaşan T+4→5/10/20g sonucu. Salt-okur;
  yalnızca bu klasördeki `trajectory_forward_*.csv/json` çıktısını günceller.
- `trajectory_live_karne.py` — T+3'te ekrana taşınan grubun bütün T0 havuzunu gerçekten geçip
  geçmediğini ölçen canlı sicil. Eşik veya ağırlık değiştirmez.

Çalıştırma: `python <script>.py` (patron.db + veriler/*.parquet gerektirir; yollar BASE sabitinde).

## Yöntem (özet)
- Gözlem birimi = `groupby(symbol, event_start_date)` (aynı olay farklı event_id'lerde birleşir).
- T0=event başlangıcı. D=T0+3 işlem günü. Giriş=T0+4 AÇILIŞ. Çıkış=T0+4+20. Getiri KARAR SONRASI.
- Sonuç: 5, 10 ve 20 gün için ayrı MFE, MAE, hit30_hi (High≥+30), win rate, ortalama
  kazanç/kayıp, payoff ratio, profit factor ve alpha (−XU100). 20 günlük sağ-kuyruk sicili,
  yalnızca 20 gün gerçekten olgunlaştığında hesaplanır.
- Look-ahead: karar T+3 kapanış, giriş T+4 açılış; snapshot yalnız ≤D verisiyle.

## Ana bulgular
- Baz +%30: high %7.7, kapanış %5.9.
- **v1 trajectory:** büyüyen grup %11.9 = **1.55x** lift. Oynaklık üç grupta eşit (artefakt değil).
  WF: Mayıs 1.50 / Haziran 1.51 / Temmuz 1.61.
- **Her sinyal tek tek (baz %7.7):** kırılım 2.48x (N105), yabancı giriş 1.97x (145), ATR-güç 1.61x (1260),
  MA20 1.39x (3514), Force Index 1.31x (518), relatif güç 1.27x (3321). OBV 1.13, CMF 1.03 (zayıf).
  smart_money_score 0.93, master 0.93, udvr 0.79, mfi_dual 0.49 (GÜRÜLTÜ/ters).
- **Kitchen-sink cezası:** 22 sinyal hep birden (v3 üst%20) = 1.34x, v1'den KÖTÜ.
- **Curated kazandı — cur_core (rs↑+MA20+ATR, 0-3):** 0→%5.4, 1→%7.3, 2→%10.1, **3→%13.8 (1.80x)**.
  cur_core=3, v1'i (1.55x) ve v3'ü (1.34x) geçti. WF (core≥2): 1.23/1.52/1.67.
- **Sağlamlık:** cur_core≥2 grubunda 524 farklı hisse; OZATD tamamen çıkarılınca lift 1.45x (artefakt değil).

## Kritik uyarılar
1. cur_core'un 3 sinyali AYNI veriden seçildi → **in-sample, 1.80x iyimser.** Statü: "en güçlü hipotez",
   "kanıtlanmadı". Gerçek kanıt = forward collection.
2. Üç ay da düşen/yatay rejim. Mutlak getiri üst grupta bile negatif (−%2.5). Değer sağ kuyrukta +
   kaybedeni kes/kazananı tut yönetiminde.
3. Kayıtlı akıllı para bayrakları (f_*) sadece Haziran+ dolu (Mayıs boş). breakout N=105, yabancı N=145 az.

## Sıradaki adım — Forward Collection (canlı takip)
- TÜM olgunlaşmamış event'leri günlük topla (sadece top10 değil → seçim yanlılığı).
- Her gün T+1/T+2/T+3 snapshot: o gün bilinen feature + cur_core/v1/v3 skorları (shadow), karar tarihi,
  T+4 giriş, 5/10/20g sonuç, +%30 high & kapanış, MFE/MAE, feature_source=live_v1.
- Tanımları ŞİMDİ dondur; 1-2 ay biriktir; "in-sample 1.80x canlıda da tuttu mu" diye ölç.
- **Gün-içi pop-up için SAATLİK parquet gerekir** (veriler_saatlik/): backtest günlük kapanışla çalışır,
  ama canlı 14:10 "gidişat" snapshot'ı için o günün henüz kapanmamış saatlik verisi lazım. Gün-içi değer
  PROVİZYONEL (oturmamış); kesin skor akşam kapanışta. Ana günlük parquet'e YAZILMAZ (ayrı salt-okur katman).

## Forward collector kullanımı

```text
python trajectory_forward_collector.py --mode close
python trajectory_forward_collector.py --mode intraday
python trajectory_forward_collector.py --mode settle
```

`close` ve `settle` günlük onaylı parquet'i, `intraday` ise yalnızca saatlik parquet'i okur.
`intraday` sonucu `live_intraday`, kapanış sonucu `live_close` olarak etiketlenir; iki kaynak
birbirine karıştırılmaz. `--dry-run` çıktı dosyasına yazmadan kontrol sağlar.

## Tarama Merkezi (T+3 ekranı)

Uygulamadaki Tarama Merkezi, kapanış collector'ının `live_close` fotoğrafını salt-okur. Her hisse
tek karttır; ham tarama katalogu arkada kalır. Ekran sırası:

1. **Karar Hazır** — T+3 tamamlandı ve mevcut trajectory eşiği (v1≥3 veya cur_core≥2) korunuyor.
2. **Takipte Güçlenen** — T+1/T+2'de eşik görülüyor ve skor önceki kapanışa göre artıyor; karar değil, takip durumu.
3. **Bugün Yeni Yakalanan** — T0; mavi izleme havuzu. İlk günde "güçlü LONG" etiketi verilmez.
4. **Karşı Sinyalli / İzleme** — terazi çelişkisi veya takipte yeterli güç olmaması.
5. **Risk Masası** — yalnız mevcut terazi'nin sert sistemik veto kararı.

Tarama adı, adayın hikâyesini (dipten uyanış / sıkışma / sağlıklı devam / erken liderlik)
açıklar; sıralama puanı değildir. En az 5 taramada görünen hisseye yalnız "kalabalık" uyarısı
konur; yukarı taşınmaz. Renkler mevcut ekran diliyle aynıdır: yeşil karar, sarı izleme/teyit,
mavi yeni, kırmızı risk.

Forward koleksiyonun ilk günü olan 07 Ağustos'ta, daha eski bir teknik event geçmiş günleriyle
T+3 olmuş sayılmaz; o gün T0 kabul edilir. Ham Radar1/momentum seli ilk-gün vitrini değildir.
Bu geniş havuz sessizce takip edilir; yalnız o günün dar Master Scan adayları T0 kartı olur.
Geniş radar kaynakları ancak sonraki kapanışlarda gerçekten güçlenirse görünür.

## Otomatik çalışma

`trajectory_automation.py --mode watch-close` 19:00 civarında başlatılır ve 23:30'a kadar
her 5 dakikada bir günlük parquet ile `patron.db` içindeki Master Scan tarihini kontrol eder.
İkisi aynı güne geldiğinde ve iki kontrolde sabit kaldığında `close` ve `settle` otomatik
çalışır. `signals.db` içindeki mevcut zamanlanmış mesaj kuyruğuna tekil özet/uyarı bırakır;
Telegram botu bunu kendi 60 saniyelik döngüsünde gönderir.

Saatlik görev 60 dakikada bir `--mode intraday` çalıştırabilir. Saatlik XU100 parquet'i
beklenen işlem gününe yetişmemişse hesap yapmaz ve tekrar tekrar bildirim göndermez.
Saatlik katman yeni hisse taramaz; yalnızca 07 Ağustos 2026 Master Scan kohortuna ve sonrasında
oluşan Master Scan olaylarına ait mevcut sinyallerin skor değişimini izler.
Bildirimde skor değişiminin hızı, güçlü skorun kaç saat korunduğu ve en az 5 tarama görülen
olaylar için yalnızca uyarı amaçlı "kalabalık" etiketi bulunur; kalabalık sayısı puana eklenmez.
Bildirim her puan kıpırdanışında gitmez: yalnız aday ilk kez **Güçleniyor** veya **Karar Hazır**
aşamasına geçtiğinde gider. Saatlik bildirim provizyoneldir; kapanışta aynı aşama kesinleşir.

`--mode settle` ile sonuçlar olgunlaştıkça `trajectory_right_tail_report.csv/json` güncellenir.
Bu sicil, tarama başına ortalama getiriyi değil, %30+ hareket yakalama oranını, en yüksek MFE'yi,
örneklem sayısını ve Wilson alt sınırını gösterir. Küçük örneklemli taramalar otomatik olarak
başarılı ilan edilmez.

Aynı anda `trajectory_live_karne.csv/json` oluşur. Bu rapor, **Tüm T0 havuzu ↔ T+3 karar hazır**
karşılaştırmasını yalnız karar sonrası 5/10/20 günlük sonuçla yapar. RSI hızı, BIST100'e göre relatif
güç, ısrar, MA20 ve ATR hareketi de tek tek ölçülür. XU100'ün eksi/arti olduğu sonraki pencereler
ayrı karne kırılımıdır; aday filtresi veya puan değildir. `N<30` ise sonuç "veri yetersiz" sayılır;
eşik/ağırlık değiştirilmez. Ayrı bir "güç kaybında çık" kuralı bu sürümde yoktur.

Kontrol komutları:

```text
python trajectory_automation.py --mode status --dry-run
python trajectory_automation.py --mode watch-close
python trajectory_automation.py --mode intraday
python trajectory_automation.py --mode settle
python trajectory_automation.py --mode karne
```

Windows Görev Zamanlayıcı için hazır tanımlar:
`trajectory_close_watch_task.xml` (19:00 watcher) ve
`trajectory_intraday_task.xml` (10:00–18:30 arası saatlik kontrol).
