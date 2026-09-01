# CODEX GÖREVİ — Faz 2 Kilitlenmesi + 3 Küçük İş

**Bu belge yazıldı:** 1 Eylül 2026, Salı · saat 11:50
**İŞE BAŞLAMA:** aynı gün veya sonrası — 1. madde **akşamki doğrulama koşusundan ÖNCE** bitmeli
**Hazırlayan:** Claude (denetçi) · **Yapan:** Codex
**Dayanak:** Aşama B/C/D denetimi — faz bölme işi doğru kurulmuş, golden sıfır fark.
Bu belge o denetimde çıkan **1 ciddi kusur** + 3 küçük işi kapsar.

---

## ⏱ BAŞLAMADAN ÖNCE

```bash
date +"%Y-%m-%d %H:%M %A"
git log --oneline -5
git status --short app.py
```

- Faz bölme işi hâlâ **commit'siz** olabilir (kasıtlı — gerçek koşu doğrulaması bekleniyor).
  `app.py`'de çalıştığını doğrula, üstüne yaz.
- Seans 10:00–18:00. **1. maddenin testi seans İÇİNDE yapılmalı** (aşağıda anlatıldı) —
  bu, diğer işlerin tersi. Sebebi §2.

---

## 1. 🔴 ASIL KUSUR — Faz 2 seans içinde SONSUZA KADAR kilitlenir

### Zincir

```
1. Faz 2 başlar, ilk adım `golden` — ölçülen süre 313 sn
2. 210. saniyede otomatik sayfa tazelemesi tetiklenir → Streamlit betiği KESER
3. Kesinti penceresi çıkar, kullanıcı "Taramayı sürdür" der
   → _ms_faz2_warning_ack = True
4. Betik baştan koşar, satır ~26402'ye gelir, `golden`'ı SIFIRDAN başlatır
5. Yine 210. saniyede tazeleme keser
6. ⚠ Artık pencere ÇIKMAZ (ack=True) — betik SESSİZCE yeniden koşar
7. `golden` bir daha sıfırdan başlar … sonsuz döngü
```

`golden` 313 saniye ister, 210 saniye alır. **Hiçbir zaman bitmez.**
Kullanıcı bunu görmez bile: ikinci kesintiden sonra uyarı da çıkmaz.

### Sayılar nereden

- Tazeleme aralığı: `app.py` ~21181, `_st_autorefresh(interval=210_000, ...)`
  (7 Ağu 2026'da 60 sn'den 210 sn'ye çıkarılmış — kodda gerekçesi var)
- `golden` süresi: `master_scan_timing_profile.json`, son 8 turun ortancası 355 sn.
  Aşama A'da formasyon (~42 sn) ayrıldığı için **golden tek başına ~313 sn**.
- Yani `golden` tek başına tazeleme aralığının **1,5 katı**.
  `radar1` (253 sn) da aralığın üstünde — o da asla bitmez.

### 🔴 NEDEN AKŞAMKİ KOŞU BUNU YAKALAYAMAZ

Otomatik tazeleme **yalnız BIST seans saatlerinde** çalışıyor:

```python
if _bist_is_trading_day(_now_tr):
    _sess = _bist_session_hours(_now_tr)
    if _sess and (_sh, _sm) <= _now_t <= (_eh, _em):
        _ar_count = _st_autorefresh(interval=210_000, ...)
```

Doğrulama koşusu 18:00 sonrası planlandı → tazeleme kapalı → kusur **görünmez**.
Bu yüzden ayrı ve seans-içi test şart.

Pratikte:
- **20:00 otomatik tarama: GÜVENDE** (seans dışı, tazeleme yok)
- **Elle başlatılan tarama (seans içi): KİLİTLENİR**

Elle tarama düğmesi duruyor ve kullanılıyor. Senaryo gerçek.

### Yapılacak

**Faz 2 sürerken otomatik tazelemeyi sustur.** Tek koşul yeter:
`_ms_faz2_bekliyor` doluysa `_st_autorefresh(...)` **çağrılmasın**.
Faz 2 bitince liste boşalır, tazeleme kendiliğinden geri gelir.

⚠ **İKİNCİ TAZELEME NOKTASI:** `app.py` ~3658'de bir `_st_autorefresh` daha var
(20:00 kapanış zamanlayıcısı, `_phase not in ("start","done")` koşullu).
Faz 2 sırasında `_phase` "start"/"done" olmalı, yani tetiklenmemeli —
**ama doğrula**, tetikleniyorsa onu da aynı koşulla sustur.

### Bunu nasıl test edersin

**A) Hızlı ve kesin (önerilen) — birim seviyesi:**
`_ms_faz2_bekliyor`'a elle sahte bir liste koy, sayfayı seans içinde aç,
`_st_autorefresh`'in çağrılmadığını doğrula (log/breakpoint/geçici print).
Liste boşken tekrar çağrıldığını doğrula. 16 dakika beklemeye gerek yok.

**B) Gerçek senaryo — seans içinde tam koşu:**
Seans saatlerinde Master Scan'i **elle** başlat ve faz 2'yi izle.
Düzeltmeden önce: `golden` asla bitmez, betik 3,5 dakikada bir başa döner.
Düzeltmeden sonra: faz 2 kesintisiz tamamlanır.
Not: seans içinde `_bayat_yazim_kapisi` sinyal yazmayı engeller — **sorun değil**,
burada ölçtüğün şey sinyal değil, faz 2'nin bitip bitmediği.

---

## 2. 🔴 Uyarı bir kez sorulup susuyor — 1. MADDE SONRASI ÖNEM KAZANDI

**Güncelleme (1 Eyl, 1. madde bittikten sonra yazıldı):** Bu maddeyi önce
"küçük iş" diye yazmıştım. 1. madde çözülünce **asıl risk buraya taşındı.**

Artık faz 2'yi kesen tek şey **gerçek kullanıcı tıklaması** (zamanlayıcılar
susturuldu). Ama `_ms_faz2_warning_ack` bir kez `True` olunca kalıcı:

```
1. tıklama  → pencere çıkar, "Taramayı sürdür" → ack = True
2. tıklama  → PENCERE YOK, betik sessizce kesilir, adım SIFIRDAN başlar
3. tıklama  → yine sessiz, yine sıfırdan
...
```

`golden` 313 saniye sürüyor. Kullanıcı bu süre içinde birkaç kez tıklarsa
(hisse değiştirmek, sekme açmak, favori eklemek — hepsi rerun tetikler)
**adım hiç bitmez.** 1. maddedeki kilitlenmenin aynısı, bu kez zamanlayıcı
değil insan eliyle. Ve yine **sessiz** — kullanıcı taramanın ilerlemediğini
görmüyor.

### Yapılacak (öneri, karar senin)

En az şu ikisinden biri:

- **Kesinti sayacı:** her kesintide say; 2. veya 3. kesintide `ack`'i sıfırla
  ve pencereyi tekrar göster — bu kez "tarama ilerlemiyor, N kez kesildi"
  uyarısıyla. Kullanıcı ya vazgeçer ya bırakır.
- **Adım-bazlı ack:** `ack`'i faz 2'nin tamamı için değil, **o anki adım**
  için tut. Adım değişince sıfırlansın.

Ayrıca düşün: kesinti anında **tamamlanmış adımlar korunuyor** (liste tek tek
düşüyor), yalnız o anki adım baştan başlıyor. Yani zarar bir adımla sınırlı —
ama o adım `golden` ise 313 saniye kaybediliyor.

**Sessizce ilerlemeyen bir tarama** bu projede en sevilmeyen hata türü
(bkz. 26-31 Ağu hacim arızası: sistem 6 gün ALERT yazdı, kimse görmedi).
Ne seçersen kodda gerekçesini yaz.

---

## 3. ℹ️ Pencere açıkken sayfa boşalıyor

Satır ~15551'deki `st.stop()` render'ı kesiyor; kesinti penceresi
kullanıcının verilerinin üstünde değil **boş sayfanın** üstünde çıkıyor.
Kozmetik. İsteğe bağlı — düzeltirsen dokunma alanını dar tut.

---

## 4. Katalogdaki 4 ölü satır (Tarama Merkezi işi)

`tarama_merkezi.py` → `CATALOG_MAP` 14 tarama listeliyor; bunların **4'ü
17 Ağu 2026'da elendi ve artık hiç koşmuyor**:

```
rs_leaders_data          → RS Momentum Liderleri
harmonic_confluence_data → Harmonik Confluence
ict_scan_data            → ICT Setup
nadir_firsat_scan_data   → Nadir Fırsat
```

Master Scan bunları boş tablo olarak set ediyor (`evidence.ELENEN_KLASIK`).
Sonuç: Katalog'da kalıcı boş 4 satır. Kullanıcı **"bugün bir şey bulamadı" ile
"bu tarama emekli" arasını ayırt edemiyor.**

Yapılacak: elenmiş taramalar ya listeden çıksın ya da açıkça
"⛔ emekli — 17 Ağu 2026 elemesi" diye işaretlensin. Tek kaynak
`evidence.ELENEN_TARAMALAR` — oradan oku, yeni liste uydurma.

---

## 5. BİTİRME KONTROL LİSTESİ

- [ ] 1. madde: `_ms_faz2_bekliyor` doluyken `_st_autorefresh` çağrılmıyor (test A)
- [ ] 1. madde: ~3658'deki ikinci tazeleme noktası kontrol edildi
- [ ] 1. madde: seans içinde gerçek koşu — faz 2 kesintisiz tamamlanıyor (test B)
- [ ] Faz 2 bitince tazeleme geri geliyor (liste boşalınca)
- [ ] `python golden_record.py` → sıfır fark
- [ ] `python -c "import ast; ast.parse(open('app.py',encoding='utf-8').read())"`
- [ ] Deploy: kuru çalışma → ezme kontrolü → `--go` (iki ajan varsa `--only`)

---

## 6. YAPMA

- ❌ Otomatik tazelemeyi **tamamen** kapatma — seans içi tek-hisse tazeliği
  ona bağlı (kodda 7 Ağu gerekçesi var). Yalnız faz 2 süresince sustur.
- ❌ `golden` veya `radar1` adımını hızlandırmak/bölmek için uğraşma —
  bu görevin konusu değil, kilitlenmenin sebebi süre değil **kesinti**.
- ❌ Faz 2'yi thread'e taşıma (ilk iş emri §10'da gerekçesi var).
- ❌ Elenmiş taramaları CATALOG_MAP'ten silerken `evidence.py` dışında
  ikinci bir "elenenler" listesi oluşturma — tek kaynak orası.

---

## 7. ÖNCEKİ İŞİN DENETİM SONUCU (bilgi — düzeltme gerekmiyor)

Aşama B/C/D denetlendi, **doğru kurulmuş**:
- Faz 2 satır 26402'de, iki kolon (23469/26344) çizildikten sonra çağrılıyor ✓
- Faz 1/Faz 2 listeleri iş emrine birebir uyuyor, hiçbir tarama kaldırılmamış ✓
- Kapanış işlerinin altısı da korunmuş (site JSON, VPS sync, snapshot, günlük
  karne, mark_scan_completed, bayraklar) ✓
- Adımlar birer birer düşürülüyor → kesintide "ne kaldı" doğru ✓
- Katalog bekleme yuvası var ✓
- Tarama kilidi `mark_scan_completed` içinden bırakılıyor, gerileme yok ✓
- golden: sıfır fark (Claude bağımsız koştu) ✓

**Claude'un yaptığı tek düzeltme:** kesinti penceresinin düğmeleri. Eskiden
vurgulu düğme "Devam et" yazıyor ve **taramayı öldürüyordu**, hemen üstünde
kalan taramalar listeliyken. Artık: `↩️ Taramayı sürdür` (vurgulu, solda) /
`⛔ Taramayı yarıda bırak` (vurgusuz, sağda). Mantık değişmedi.
