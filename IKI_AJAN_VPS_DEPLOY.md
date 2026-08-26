# İKİ AJAN VPS DEPLOY PROTOKOLÜ (Claude + Codex)

**Amaç:** İki ajan (Claude + Codex) aynı çekirdek dosyaları (app.py, scan_pipeline.py, smr_core.py) düzenliyor + ikisi de VPS'e deploy edebiliyor. Koordinasyon olmazsa biri diğerinin işini ezer (lokalde veya VPS'te). Bu protokol clobber'ı (ezme) önler. **VPS'e her deploy'dan ÖNCE bu 6 adımı sırayla uygula.** Kanonik komut detayları: `deploy_runbook.md` (varsa) + aşağıdaki komutlar.

VPS: `wm11tr@34.153.19.220`, repo `~/smr/`. Servisler: `patron-radar`, `free-showcase` (web) · `smr-bot` (bot).

---

---

## ⚡ ÖNCE BUNU DENE: `./deploy.sh` (17 Ağu 2026 — Claude + Codex ORTAK)

**Aşağıdaki 6 adımı artık ELLE yapma.** `deploy.sh` hepsini sırayla, aynı kurallarla uygular:

```bash
./deploy.sh                 # KURU ÇALIŞMA — ne gideceğini gösterir, HİÇBİR ŞEY göndermez
./deploy.sh --go            # gerçek deploy
./deploy.sh --go --bot      # smr-bot'u da restart et (smr_core değiştiyse)
./deploy.sh --go --only=app.py,evidence.py
```

Script'in yaptıkları (bu dosyadaki protokolün birebir karşılığı):

| Adım | Script ne yapar |
|---|---|
| 1 | Bağımlılık paketindeki her dosyanın lokal↔VPS hash'ini kıyaslar, **sadece gerçekten değişeni** seçer. Satır sonu (CRLF/LF) farkını yok sayar — sahte "değişti" çıkmaz. |
| 2 | **Ezme kontrolü:** VPS'te olup lokalde OLMAYAN blok varsa **DURUR** (diğer ajanın taze deploy'u olabilir). Geçmek için bilerek `--force`. |
| 3 | Değişen her `.py` için lokal `ast.parse`. Hata varsa hiç göndermez. |
| — | `golden_record.json`'dan yeni dosya varsa **uyarır** (golden'ı script koşmaz, sen koş). |
| 4 | VPS'te `_yedek/<tarih-saat>/` altına yedek alır, sonra scp'ler. |
| 5 | VPS'te `py_compile` (Py3.10 katı) + `venv/bin/python -c "import app"` smoke testi. **İkisinden biri patlarsa yedekten GERİ ALIR.** |
| 6 | `patron-radar` + `free-showcase` restart, health 200 olana kadar bekler (max ~96sn). **200 gelmezse geri alır ve eski sürümü ayağa kaldırır.** |

Son 10 yedek tutulur, eskiler silinir. Elle geri alma komutu her başarılı deploy'un sonunda yazdırılır.

**Script kullanılmayacaksa** (ör. sadece frontend dosyası gidiyorsa) aşağıdaki 6 adımı elle uygula — kurallar aynı.

**Script'e dosya eklemek:** `deploy.sh` içindeki `DOSYALAR` listesine ekle. Yeni bir modül import ettiysen bu ŞART — yoksa VPS'te `import app` patlar (script bunu yakalar ve geri alır, ama boşuna tur atmış olursun).

---

## 1. OKU-SONRA-DÜZENLE
Bir dosyayı düzenlemeden ÖNCE diskteki GÜNCEL halini oku (hafızandaki eski snapshot'tan değil). Diğer ajan o dosyaya dokunmuş olabilir; güncelden çalışırsan onun üstüne yazar, ezmezsin.

## 2. 3 KAPI — hepsi yeşil olmadan deploy YOK
```bash
python golden_record.py          # "BİREBİR AYNI / sıfır fark" (hesap değiştiyse zorunlu)
python -c "import ast; ast.parse(open('DOSYA',encoding='utf-8').read())"   # lokal syntax
# VPS py_compile (adım 5'te, scp sonrası)
```
Not: VPS Python 3.10 lokalden KATIDIR — lokalde geçen VPS'te patlayabilir; py_compile şart.

## 3. EZMEDEN ÖNCE VPS↔LOKAL DIFF (EN KRİTİK)
Her dosya için VPS sürümünü çek, lokalle diff'le:
```bash
scp wm11tr@34.153.19.220:~/smr/DOSYA /tmp/vps_DOSYA
diff /tmp/vps_DOSYA DOSYA | grep "^<"    # VPS'te olup lokalde OLMAYAN satırlar
```
`^<` satır çıkarsa:
- Çoğu = senin değiştirdiğin satırın eski hali → normal.
- **AMA diğer ajanın taze deploy'u da olabilir** → körlemesine "eski kod" sanma. Emin değilsen **DUR, kullanıcıya sor.**
- Adım 1'e (oku-sonra-düzenle) uyduysan bunlar zaten lokalinde olur, VPS-only çıkmaz.

## 4. BAĞIMLILIK PAKETİ — app.py TEK BAŞINA GİTMEZ
Beraber giden: 12 bölme modülü (data_layer, indicators, db_layer, evidence, scanners, pattern_core, ict_core, scoring_core, scan_pipeline, charts, analysis_core) + veri_bekcisi + formasyon_core / formasyon_v2 / formasyon_v2_app + smr_core + infografik_build + terazi_core + sampiyonlar_ligi + verdict-JSON + **bir dosyada YENİ import ettiğin modül** (örn. stp_uyanis_core). Yeni importu göndermezsen app patlar.
**ŞART:** app.py'yi deploy ederken TÜM modül-seviye importlarının (yeni olmasa bile) VPS'te çözüldüğünü doğrula: `ssh wm11tr@34.153.19.220 "cd ~/smr && source venv/bin/activate && python3 -c 'import app'"` — ImportError verirse eksik modülü de gönder. (11 Ağu vakası: app.py `import sampiyonlar_ligi` [31 Tem'den beri] ama modül VPS'e hiç gitmemişti → app kırılma riski.)

## 5. DEPLOY = SCP-ONLY + YEDEK + RESTART
VPS'te `git pull` **YAPMA** (canlı hotfix ezilir). Sıra:
```bash
ssh wm11tr@34.153.19.220 "cd ~/smr && cp DOSYA DOSYA.bak_$(date +%H%M%S)"   # yedek
scp DOSYA wm11tr@34.153.19.220:~/smr/DOSYA                                    # gönder
ssh wm11tr@34.153.19.220 "cd ~/smr && source venv/bin/activate; python3 -m py_compile DOSYA"   # VPS kapı
ssh wm11tr@34.153.19.220 "sudo systemctl restart patron-radar free-showcase"  # web (bot değiştiyse: + smr-bot)
ssh wm11tr@34.153.19.220 "systemctl is-active patron-radar; curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8501/patron/"
```
HTTP 200 doğrula. İlk 000 = soğuk başlangıç; 15sn bekle, tekrar dene.

## 6. DEPLOY'U DUYUR
Deploy edince açıkça yaz: "X dosyalarını VPS'e attım, restart oldu, HTTP 200." VPS oynadı; diğer ajan bilsin, sonraki diff'te şaşırmasın.

---

## ALTIN KURAL
Aynı anda İKİ ajan aynı dosyayı deploy ETMESİN. Sıra-sıra: bir ajan düzenler → hemen deploy eder (bu 6 adım) → sonra diğerine devreder. **Şüphe = DUR + sor.** Ezmek geri alınamaz (yedek .bak'lar var ama karışıklık pahalı).
