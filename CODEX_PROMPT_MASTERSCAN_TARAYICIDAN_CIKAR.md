# Codex'e verilecek başlangıç mesajı

> Aşağıdaki bloğu **olduğu gibi** yeni bir Codex oturumuna yapıştır.

---

```
Patron Terminal deposunda çalışıyorsun. Sana verilen iş: Master Scan'i
tarayıcıdan çıkarmak.

## ÖNCE OKU (bu sırayla, atlama)

1. `date +"%Y-%m-%d %H:%M %A"` çalıştır — iş emrindeki tarihler 1 Eylül 2026
   akşamına göre yazıldı, bugünün ne olduğunu kendin doğrula.
2. `AJAN_KURALLARI.md` — bu depoda çalışmanın kuralları (ölçüm, dil, git, VPS,
   iki-ajan çakışması, iş bitirme kontrol listesi). Bağlayıcı.
3. `CODEX_GOREV_MASTERSCAN_TARAYICIDAN_CIKAR.md` — ASIL İŞ EMRİ. Hikâye,
   ölçülmüş mevcut yapı, üç aşama, on tuzak, kontrol listesi, yasaklar.
4. `memory/project_master_scan_otomasyon_tuzagi.md` — bu arızanın iki kez
   nasıl ısırdığı ve 28 Ağustos'ta neden yarım kapatıldığı.

## İŞ NE

Master Scan bugün bir ekran sayfasının içinde yaşıyor: binlerce hisseyi tarayan
toplu bir iş, ancak tarayıcıda sayfa açık durursa çalışabiliyor. Sayfa ölürse
tarama ölür. Etrafındaki altı parçalık aygıt yığını (görünmez tarayıcı, onu
açan betik, tamamlanma damgası, çalışma kilidi, iki ayrı nöbetçi) yalnızca
"ekran başında oturan bir insan" taklidi yapmak için var.

27 Ağustos ve 1 Eylül'de akşam turu iki kez sessizce kayboldu. Her seferinde
üstüne bir yama daha kondu. Bu görev yama koymuyor — yangının çıktığı yeri
kaldırıyor.

Üç aşama (detayı iş emrinde):
  A) Sırayı yöneten "şef" kodu app.py'den `master_scan_engine.py`'ye çıkar.
     Davranış DEĞİŞMEZ. Hakem: `python golden_record.py` sıfır fark.
  B) `master_scan_kos.py` — ekransız koşucu. Tarayıcı yok, sayfa yok.
  C) `master_scan_kiyas.py` — iki yolun sonucunu tip ve sembol düzeyinde
     karşılaştıran araç.

## SINIRLAR (iş emrinde uzun hâli var)

- Aşama D (zamanlayıcıyı yeni yola çevirmek) BU GÖREVDE YOK. Görünmez tarayıcı
  zinciri olduğu gibi kalır. En az 3 işlem günü paralel koşu kanıtı gelmeden
  anahtar çevrilmez.
- Hesap fonksiyonlarına (scanners / scan_pipeline / scoring_core / ict_core /
  indicators) DOKUNMA. Bu iş yer değiştirme, iyileştirme değil.
- `kapanis_master_otomasyon.py` ve `gorev_bekcisi.py`'ye DOKUNMA — Claude
  1 Eylül gecesi oralara girdi (commit 83d4f73), canlıda.
- Tarama sırasını değiştirme. Kıyas buna dayanıyor.
- Kıyas ölçütünü sonuca bakarak gevşetme.
- Sessiz `except: pass` yazma. Bu görevin tamamı sessiz arızaya karşı.

## İLK İŞİN

`git status --short` çalıştır. 1 Eylül akşamı `app.py` ve `analysis_core.py`'de
senin commit'lenmemiş işin duruyordu (137 ekleme / 65 silme, GENEL ÖZET
paneli). Hâlâ duruyorsa ÖNCE onu bitir ve commit et. İki iş aynı dosyada açık
kalırsa ikisi de kaybolur.

Sonra Aşama A'ya başla.

## NASIL RAPOR VER

Claude bu işi denetleyecek. Bitirdiğinde şunları ver:
- Hangi aşamalar bitti, hangileri bitmedi (yarım bıraktıysan neden)
- `python golden_record.py` çıktısı — sıfır fark yazmalı
- `python -c "import ast; ast.parse(open('app.py',encoding='utf-8').read())"`
- Ekrandan elle Master Scan koştuğunda eskisi gibi çalıştığının kanıtı
- `master_scan_kos.py --kuru` log çıktısı
- Çökme testi: koşucuyu ortadan öldürdüğünde çıkış kodunun 0 DÖNMEDİĞİ
- Dokunduğun her dosyanın listesi + neden dokunduğun

Emin olmadığın bir yerde tahmin etme, sor. Bu projede en pahalı hata
"sessizce farklı sonuç üretmek".
```
