# AJAN KURALLARI — Patron Terminal Çalışma Anayasası

**Kim okur:** Claude · Codex · Antigravity. Üçü de bu projede kod yazıyor, üçü de
VPS'e deploy edebiliyor. Bu dosya üçünü de bağlar.

**Ne zaman okunur:** Her oturumun başında. Bir kural ihlal edildiğinde geri dönülür.

**Bu dosya ne DEĞİL:** Mimari/navigasyon değil (→ `CLAUDE.md` · `AGENTS.md` ·
`DOSYA_HARITASI.md`). Deploy adımları değil (→ `IKI_AJAN_VPS_DEPLOY.md` · `deploy.sh`).
Veri hattı durumu değil (→ `VERI_HATTI_DURUM.md`). Burası **nasıl çalışılır** dosyası.

**Öncelik sırası çelişkide:** kullanıcının o anki açık talimatı > bu dosya >
`CLAUDE.md` / `AGENTS.md` > kendi sezgin.

---

## 0. ROLLER

| Ajan | Rol |
|---|---|
| **Claude** | Geliştirici **+ DENETÇİ**. Diğer iki ajanın işini denetler, ölçer, gerekirse müdahale eder ve geri alır. Denetim sonucunu gerekçesiyle kullanıcıya raporlar. |
| **Codex** | Geliştirici. Tarama reformu ve boru hattı işleri ağırlıklı. |
| **Antigravity** | Geliştirici. Panel/UI ve motor işleri ağırlıklı. |

**Denetçi yetkisi:** Claude, ölçülmemiş veya kurallara aykırı bir özelliği
**kapatabilir** (bayrakla), **geri alabilir** (yedekten) veya **AI prompt'tan
çıkarabilir** — ama önce gerekçeyi kanıtla birlikte kullanıcıya sunar.
Denetçi de denetlenir: Claude'un işi de bu dosyadaki kurallara tabidir.

**Denetim rapora değil koda bakar.** Bir ajanın "yaptım" demesi kanıt değildir.
25 Ağu 2026'da üç koruma "var" göründü, üçü de yoktu — hepsi ancak
**çalıştırıp sayarak** ortaya çıktı.

---

## 1. ALTIN KURALLAR

1. **Ölçmediysen ekrana koyma, AI'a hiç koyma.**
2. **Yön ≠ Eylem.** Sistem "ne oluyor" der, "al/sat" demez.
3. **Sessiz arıza en pahalı hatadır.** Şüphede DUR ve sor, sessizce geçme.
4. **Çalışanı bozma.** Yeni özellik, çalışan veri hattını riske atmaz.
5. **Kullanıcı kod bilmiyor.** Ona terim değil hikâye anlat.
6. **Aynı dosyada iki ajan varsa önce oku, sonra düzenle.**

---

## 2. ÖLÇÜM KURALLARI

### 2.1 Rozet/etiket ölçümden geçer
Yeni bir rozet, etiket, skor bileşeni veya AI prompt satırı **ekrana girmeden
önce** tek soru yanıtlanır: *"bu etiket ileri getiriyi AYIRIYOR MU?"*

- Kalıp dosya: **`_4s_filtre_backtest.py`** (kökte). Yeni sinyal için yarım
  saatte uyarlanır.
- Yöntem: `scan_signals × signal_returns` JOIN + etiketi geçmişe dönük yeniden
  hesapla. **Seriyi sinyal gününe kadar KES** — look-ahead yasak.
- **En az 3 vade** ölç (T+5 / T+10 / T+20). Tek vadeden hüküm verme.
- **İşaret değişimi = gürültü.** Gerçek edge vadeyle zayıflar, yön değiştirmez.
- Hücre sayısını say: 3 durum × 3 vade × 3 tablo = 27 → birkaçı rastgele
  çarpıcı görünür.

### 2.2 Ayrım yoksa sil değil, sustur
Projedeki kalıp bir bayraktır: `SMC_IFVG_BB_AI_ENABLED`, `SMC_YOPEN_AI_ENABLED`,
`ZAMANLAMA_4S_ENABLED`. Bayrağın **hemen üstüne ölçüm sonucunu yaz** — altı ay
sonra bakan "niye kapalı" diye sormasın. `scan_signals`'a yazım SÜRSÜN ki rejim
değişince yeniden ölçülebilsin.

### 2.3 Extrapolasyon yasak
Kümülatif sonuçtan kural uydurma; segmente bak. Tek rejimden çıkan sonuç
geçicidir — rejim değişince yeniden ölç. Küçük örneklemde (N<150) çıkan
çarpıcı sonuç, kural değil hipotezdir.

### 2.4 Ölçülmüş zararı taşıma
İki rejimde de negatif alfa veren tarama/flag AI prompt'ta kalamaz. Ölçüm
mevcutsa yeniden ölçme, **uygula**. (Bkz. `memory/project_endeks_alti_alfa.md`)

---

## 3. DİL KURALLARI

### 3.1 Yön ≠ Eylem (en sık ihlal edilen kural)
**YASAK:** "GİRİŞ UYGUN" · "Tavsiye Dar Stop: 297.01 TL" · "alım fırsatı" ·
"yeni alımlarda temkinli olunmalı" · "stop kabul edilerek" · "dar stoplu giriş
planı olarak vurgula" · hedef fiyat · pozisyon önerisi.

**SERBEST:** "4 saatlik momentum aşırı alım bölgesinde (RSI 71)" · "fiyat 50
günlük ortalamanın 3 gündür üstünde" · "para akışı 5 ve 20 günde ayrışıyor".

Kural şu: **gözlem + geçmiş karnesi** ver, eylemi kullanıcı seçsin. Çıktı
Telegram ve Twitter'a gidiyor — orada verilen her seviye, tavsiye sayılır.

### 3.2 İsim dürüstlüğü
Bir şeye ölçtüğünden büyük ad verme.
- ❌ 8 hisseye "PİYASA GENİŞLİĞİ" (gerçek breadth 500+ hissedir)
- ❌ 12 hücrenin 9'una "TAM BOĞA HİZALANMASI / tüm vadelerde"
- ❌ hacim/ortalama oranına "para GİRİŞİ" (o ölçü yönsüzdür)
- ✅ "Ağır Toplar Barometresi" · "GENİŞ BOĞA UYUMU (9/12)" · "YOĞUN/SEYREK"

### 3.3 Kullanıcıya anlatım
Kullanıcı kod yazmıyor. Yapı: **Sorun → Niye sorun → Çözüm → Sonuç → Test et.**
- Kod fragmanı, fonksiyon adı, satır numarası **verme**.
- Terim kullanacaksan yanına Türkçe karşılığını koy.
- Rakama **birim** koy (yüzde, lira, gün, adet).
- Türkçe benzetme kullan ("santral tıkandı", "kapı açık kaldı").
- Kontrol sorusu: *"Bunu kod bilmeyen biri okursa olayı anlar mı?"*

### 3.4 Belge yalanı yasak
Docstring/yorum satırında **var olmayan bir koruma vaat etme.** 25 Ağu 2026'da
`zamanlama_core.py` başında "Yarım Bar Koruması" yazıyordu, kodda yoktu.
Önce kodu yaz, çalıştır, doğrula — sonra belgele.

---

## 4. KOD KURALLARI

- **Yeni hesap kodu `app.py`'ye YAZILMAZ.** Ayrı modüle (`*_core.py` kalıbı);
  app.py sadece import eder ve render eder. Mevcut kodu taşıma (büyük refactor)
  YASAK.
- **Yeni modül import ettiysen `deploy.sh` içindeki `DOSYALAR` listesine EKLE.**
  Yoksa VPS'te `import app` patlar ve panel çöker.
- **Çıplak import yerine korumalı import** kullan (yeni/opsiyonel modüllerde):
  `try: from x import y / except ImportError: <no-op fallback>`.
- **`except Exception: pass` yasak — özellikle bekçilerde.** Bekçi konuşamıyorsa
  **REDDET** (fail-closed). Sessizce geçirmek, korumanın olmamasından kötüdür:
  koruma var sanılır.
- **Satır sonları CRLF** — dosya yazarken koru (`newline='\r\n'`). Yoksa tüm
  dosya değişmiş görünür, diff gürültüsü olur, ezme kontrolü yanılır.
- **Ölü kod bırakma.** Bir sözlükten anahtar okuyorsan, o anahtarın gerçekten
  döndüğünü **çalıştırarak** doğrula. (25 Ağu: `ana_cumle` isteniyordu, motor
  `title` döndürüyordu → blok hep boş, AI hiç görmedi, try/except yutuyordu.)
- **Tek kaynak.** Aynı hesabı iki yerde yapma; ortak fonksiyona al.

---

## 5. VERİ KURALLARI

- **Bayat veri hüküm veremez.** Her depo okuması tazelik kontrolünden geçer.
  Eşik veriye göre: günlük veride 1 gün, 4 saatlikte 3 takvim günü.
- **Kapanmamış bar hesaba girmez.** Seans sürerken günün son barı yarımdır;
  onunla hesaplanan gösterge gün içinde fikir değiştirir.
- **Bekçi DOĞRU depoyu denetlesin.** 25 Ağu: `saatlik_kapi` (`veriler_saatlik/`)
  ile 4S verisi (`veriler_4s/`) denetleniyordu — VPS'te 231 taze dosyanın hepsi
  elendi, özellik sessizce ölüydü. Vekil bekçi kullanma.
- **Çalışan veri hattını riske atma.** Yeni bir özellik için kaynağa (Yahoo vb.)
  yük bindirmeden önce throttle etkisini düşün — o throttle asıl hattı vurur.
- **Fiyat tek kaynaktan.** Detay: `VERI_HATTI_DURUM.md` + `memory/project_price_single_source.md`.

---

## 6. GIT & COMMIT KURALLARI

- **Dal:** `main` üzerinde çalışılır (projenin yerleşik akışı).
- **Commit ve push YALNIZCA kullanıcı isteyince.** Kendiliğinden commit atma.
- **Commit mesajı gerekçe taşır.** "Şunu değiştirdim" yetmez; **niye** ve
  **hangi ölçümle** yazılır. Ölçüm varsa tabloyu mesaja koy — git geçmişi
  altı ay sonra tek kanıt olur.
- **Ne commit edilir:** kaynak kod, ölçüm araçları, doküman.
  **Ne edilmez:** `*_backup_*.py`, `.bak`, tarama çıktıları
  (`public/frontend/*.json`), `patron.db`, parquet depoları.
- Commit mesajı sonuna: `Co-Authored-By: <ajan adı>`.
- **Büyük değişiklikten önce yedek al:** `cp dosya.py dosya_backup_pre_<is>_<tarih>.py`
  (yedek commit edilmez, lokalde kalır).

---

## 7. VPS & DEPLOY

Tam protokol: **`IKI_AJAN_VPS_DEPLOY.md`** · Komut: **`./deploy.sh`**
(kuru çalışma argümansız, göndermek için `--go`).

Değişmez kurallar:

- **VPS'te `git pull` YASAK.** Oradaki git aylarca geride; pull çekersen canlı
  düzeltmeler ezilir. Deploy **SCP-ONLY** (`deploy.sh` bunu yapar).
- **3 kapı, hepsi yeşil olmadan deploy yok:**
  1. `python golden_record.py` → 5×69 + terazi **sıfır fark**
  2. lokal `ast.parse`
  3. VPS `python3 -m py_compile` (VPS Python 3.10 — lokalden ESKİ ve KATI)
- **Ezmeden önce diff.** `deploy.sh` ezme kontrolü yapar; DURDURURSA incele.
  `--force` yalnızca farkın **senin bilinçli silmen** olduğunu doğruladıktan sonra.
- **app.py TEK BAŞINA GİTMEZ** — bağımlı modüllerle birlikte gider
  (`deploy.sh` paketi yönetir).
- **Modül dosyasına dokunduysan** lokalde Streamlit TAM restart gerekir
  (hot-reload modülü yenilemez).
- **Cron eklemek kalıcı yapılandırmadır → kullanıcıya SOR.** Eklenirse
  `flock -n` ZORUNLU (kilitsiz cron 29 süreç biriktirmişti).
- Deploy sonrası: servis durumu + health 200 + log kontrolü.

---

## 8. ÜÇ AJAN ÇAKIŞMA PROTOKOLÜ

1. **OKU-SONRA-DÜZENLE.** Dosyayı düzenlemeden önce **diskteki güncel halini**
   oku; hafızandaki eski görüntüden çalışma.
2. **Oturum başında `git pull`** (lokalde). VPS'te asla.
3. **Aynı dosyada aynı anda iki ajan çalışmaz.** Kullanıcı hangi ajanın neyi
   aldığını söyler; belirsizse SOR.
4. **İş bitince DEVİR NOTU** bırak: ne değişti, **niye** değişti, ne
   dokunulmamalı, hangi bayrak açık/kapalı.
5. **Başka ajanın işini geri alıyorsan gerekçeyi kanıtla.** "Bence yanlış"
   yetmez; ölçüm veya çalıştırma çıktısı gerekir.
6. **Şüphe = DUR + sor.** Özellikle sahibi belirsiz yarım kalmış iş (WIP) varsa.

---

## 9. KALICI YASAKLAR

- **Sektör endeksi + piyasa rejimi → tek hisse AI prompt'una EKLENMEZ.**
  (Endeksin kendisi analiz konusuysa serbest.)
- **Weinstein Stage Analysis → yapılmayacak** (analysis paralysis).
- **Dip avı tarayıcısı YAZILMAZ.** Üç ayrı ölçümde reddedildi: dönüş günü
  çalışması (−3,75% alfa), kurulum felsefesi (dip avı −1,81 vs güçten devam
  +2,68), STP uyanış backtest (negatif). "Dip yakalama" isteği gelirse ÖNCE
  `memory/project-donus-el-kitabi.md`.
- **`CLAUDE.md` / `AGENTS.md` uzatılmaz** — detay `memory/` altına.
- **Kullanıcı söylemeden tıklanabilir yönlendirme eklenmez.**
- **Broker bazlı takas motoru yazılmaz** — veri kaynağı yok (BIST 1 Oca 2025'ten
  beri ücretli lisans, İş Yatırım sayfası 404).

---

## 10. BİLİNEN TUZAKLAR (hepsi gerçek vaka)

| Tuzak | Vaka | Korunma |
|---|---|---|
| **Belge yalanı** | Docstring "yarım bar koruması var" diyordu, kod yoktu | Çalıştır, çıktıyı say |
| **Vekil bekçi** | Saatlik depo bekçisi 4S verisini deniyordu → VPS'te 231 dosyanın hepsi elendi | Bekçi kendi deposuna baksın |
| **Ölü kod** | AI bloğu `ana_cumle` istiyordu, motor `title` döndürüyordu → hep boş | Anahtarları çalıştırarak doğrula |
| **Tek vade yanılgısı** | T+20'de "+0,57 çalışıyor", T+10'da "−0,52" | En az 3 vade |
| **İsim şişirme** | 8 hisseye "piyasa genişliği" | Ölçtüğün kadar ad ver |
| **Sessiz except** | Bekçi hata verince kapı açık kalıyordu | Fail-closed |
| **Elenen sinyalin geri dönüşü** | `in_days` üç ölçümde en kötü bacaktı, elmas rozeti tetikliyordu | Karneyi kontrol et |
| **Sessiz disk dolması** | Log döngüsü diski %100 yaptı, veri akışı 25 dk sessizce durdu | Alarm + periyodik bakış |
| **Kilitsiz cron** | 29 süreç birikti | `flock -n` |
| **Sabit gösterge konumu** | CMF 0,104 iken bar %75 — endeks düşerken "alıcılar ezici" | Barı gerçek değerden çiz |
| **Aynı ölçü, iki eşik** | %90 hacim bir panelde "düşük", diğerinde "normal" | Eşik tek fonksiyonda |
| **Karışık birim** | Para akışı etiketinin yanında fiyat yüzdesi | Her rakamın birimini yaz |

---

## 11. İŞ BİTİRME KONTROL LİSTESİ

**Ne zaman:** iş BİTİNCE — kod değiştirmediysen oturum başında koşmak zorunlu
değil. Yine de yararlı: değiştirmeden önce bir kez koşarsan, sonraki sonucu
neyle kıyaslayacağını bilirsin (bugünün mü, senden önceki durumun mu).
Denetim bir bulgu verirse **önce oku, düzeltmeye atlama** — `_hesap_denetimi.py`
içindeki BEYAZ_LISTE'de gerekçesiyle işaretlenmiş bilinen yanlış alarmlar var.

Bir işi "bitti" demeden önce:

- [ ] Kod çalıştırıldı, **çıktı görüldü** (sadece okunmadı)
- [ ] Yeni etiket/rozet varsa **ölçüldü** (3 vade)
- [ ] Dil kontrolü: emir cümlesi yok, isim dürüst
- [ ] Yeni modül varsa `deploy.sh` listesinde
- [ ] `python _hesap_denetimi.py` — tanımsız değişken / sabit gösterge konumu / ölü atama
- [ ] `golden_record.py` sıfır fark
- [ ] Lokal `ast.parse` + VPS `py_compile`
- [ ] Deploy sonrası health 200 + log temiz
- [ ] `DOSYA_HARITASI.md` güncellendi (yeni/kaldırılan modül)
- [ ] Kalıcı bilgi `memory/` altına yazıldı
- [ ] Devir notu bırakıldı

---

## 12. BU DOSYAYI GÜNCELLEME

Yeni bir kural, ancak **gerçek bir vakadan** doğduğunda eklenir — varsayımdan
değil. Eklerken vakayı da yaz (tarih + ne olduğu), yoksa kural zamanla
gerekçesini kaybeder ve ilk zorlandığında atlanır.

Son güncelleme: **25 Ağustos 2026** — 4S denetimi, pusula/lokomotif temizliği,
üç ajan rol tanımı.
