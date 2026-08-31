# CODEX GÖREVİ — Master Scan Faz Bölme (Aşama B · C · D)

**Tarih:** 1 Eylül 2026 · **Hazırlayan:** Claude (denetçi) · **Yapan:** Codex
**Önceki commit:** `61b91f8` (Aşama A tamamlandı)
**Denetim:** İş bitince Claude denetleyecek — diff + golden + gerçek Master Scan koşusu.

---

## 0. TEK CÜMLEYLE İŞ

Master Scan 16 dakika sürüyor ve bu sürenin **tamamında ekranda hiçbir şey yok**.
Kullanıcı asıl **Tarama Merkezi**'ne bakıyor; o ise yalnızca **5 dakikalık** taramaya
muhtaç. Kalan 11 dakika Tarama Merkezi'ne **hiç girmiyor**.

**Hedef:** Tarama Merkezi ~5. dakikada ekrana gelsin, kalan taramalar
**hiçbiri kaldırılmadan** arkadan koşmaya devam etsin.

---

## 1. NEDEN ŞU AN ERKEN GÖSTERİLEMİYOR (yapısal sebep)

Streamlit betiği yukarıdan aşağı çalıştırır. Kritik satırlar:

| Ne | Yaklaşık satır |
|---|---|
| Master Scan bloğu başlangıcı | `app.py:15109` (`if (not _MM_MEMBER_VIEW) and (_manual_master_scan or _auto_master_scan):`) |
| Master Scan bloğu sonu | ~`app.py:15800` |
| Sol kolon render | `app.py:23312` (`with col_left:`) |
| Sağ kolon render | `app.py:26187` (`with col_right:`) |

Ekran, tarama bloğu **bittikten sonra** çizilir. Bu yüzden:

> ⚠ **Adımları yeniden sıralamak TEK BAŞINA HİÇBİR ŞEY ÇÖZMEZ.**
> Faz 2, ekran çizildikten **sonra** (yani ~26187'den sonra) çalışmak zorunda.

Blokta `st.rerun()` **yok** ve olmamalı da — vaktiyle vardı, Erken Radar adımını
öldürüyordu, kaldırılmış (kodda yorumu duruyor).

---

## 2. ÖLÇÜLMÜŞ GERÇEKLER (varsayım değil — 20 koşunun profilinden)

Kaynak: `master_scan_timing_profile.json` → `categories["BIST 500"]["steps"]`
Aşağıdaki değerler **son 8 turun ortancası** (n=8 olan adımlar; n<8 olanlar artık koşmuyor).

```
TOPLAM ~961 sn ≈ 16 dakika

golden          355 sn  %37     ← Golden Trio (Aşama A'da formasyon buradan AYRILDI)
radar1          253 sn  %26
hidden_accum     88 sn   %9
toplu_terazi     56 sn   %6
radar2           56 sn   %6
early_radar      48 sn   %5
rsi_divergence   20 sn
strong_reversal  18 sn
data             17 sn
minervini        16 sn
tavan            15 sn
flow_leaders      8 sn
stp_uyanis        5 sn
prelaunch         5 sn
mkk               2 sn
backfill          1 sn
index_health      0 sn
top20 / weak_pair 0 sn
formasyon       ~42 sn  ← Aşama A ile ayrıldı; 100 hissede 7 sn ölçüldü, ×6
```

⚠ `harmonic`, `rs_leaders`, `vip_and_patterns` artık **koşmuyor** (17 Ağu elemesi).
Profildeki eski değerlerine bakıp hesap yapma.

---

## 3. BAĞIMLILIK ZİNCİRİ — İŞİN KALBİ

Bu zinciri Claude koddan çıkarıp doğruladı. **Buna güven, yeniden keşfetme.**

```
Tarama Merkezi sekme 1-4  ←  toplu_terazi_data
toplu_terazi_data         ←  _master_batch_snapshot
                          +  _master_benchmark_snapshot
                          +  _master_formasyon_snapshot   (ZORUNLU, aşağıya bak)
                          +  aday havuzu
aday havuzu (_toplu_terazi_candidate_pool, app.py:2003)
                          ←  _compute_goldmine_entries()
                          +  wilder_divergence_data   (rsi_divergence adımı)
                          +  minervini_data           (minervini adımı)
_compute_goldmine_entries (app.py:1302)
                          ←  erken_radar_data      (early_radar adımı)
                          +  prelaunch_bos_data    (prelaunch adımı)
                          +  accum_data            (hidden_accum adımı)
```

🔴 **EN ÖNEMLİ TUZAK:** `_compute_toplu_terazi_snapshot` (app.py:2089) içinde
`app.py:2115` civarında şu var:

```python
if not formation_ready:
    _base['message'] = "Master Scan formasyon fotoğrafı hazır değil."
    return _base
```

Yani **formasyon fotoğrafı olmadan Toplu Terazi hiç çalışmaz**, boş döner.
Aşama A tam da bunun için yapıldı: formasyon bloğu `golden` adımının içindeydi,
Golden Trio'nun 313 saniyesini beklemeden Toplu Terazi üretilemiyordu.

**Golden Trio, radar1, radar2, tavan, strong_reversal, flow_leaders, stp_uyanis
zincirin İÇİNDE DEĞİL.** Hepsi faz 2'ye gidebilir.

---

## 4. FAZ BÖLÜNMESİ (uygulanacak liste)

### FAZ 1 — Tarama Merkezi (~295 sn ≈ 5 dk)

| Adım anahtarı | Neden faz 1 |
|---|---|
| `index_health` | temel |
| `backfill` | temel |
| `mkk` | temel |
| `data` | ortak veri fotoğrafı — her şeyin girdisi |
| `magic_ribbon` | Tarama Merkezi sekme 7 (⏱ 4S Yukarı) |
| `hidden_accum` | → `accum_data` → Gold Mine → aday havuzu |
| `formasyon` | → `_master_formasyon_snapshot` → **Toplu Terazi ZORUNLU girdisi** + Katalog "Formasyon Motoru" |
| `cizgi_yapi` | Tarama Merkezi sekme 8 (📐 Çizgi Yapısı) |
| `minervini` | aday havuzu girdisi + Katalog |
| `rsi_divergence` | → `wilder_divergence_data` → aday havuzu |
| `prelaunch` | → `prelaunch_bos_data` → Gold Mine |
| `early_radar` | → `erken_radar_data` → Gold Mine **+ sekme 6 (Olası Short, er_D4/D5)** |
| `toplu_terazi` | Tarama Merkezi sekme 1-4 |

### FAZ 2 — kalan her şey (~11 dk), HİÇBİRİ KALDIRILMADAN

`golden` (Golden Trio) · `radar2` · `weak_pair` · `radar1` ·
`strong_reversal` · `tavan` · `flow_leaders` · `stp_uyanis` · `top20`

Ayrıca faz 2 sonundaki mevcut işler **aynen korunacak**:
site JSON export · patron.db→VPS sync · `save_scan_result` snapshot ·
`write_daily_karne` · `mark_scan_completed` · `generate_prompt=False` ·
`_master_scan_running=False`.

---

## 5. AŞAMA B — adımları fazlara ayır (henüz davranış değişmez)

Amaç: faz 1 adımları blokta **önce**, faz 2 adımları **sonra** ve
**tek bir yerde toplu** olsun. Bu aşamada hâlâ tek geçiş; kullanıcı bir fark görmez.

**Yapılacak:**
1. Faz 1 adımlarını yukarıdaki sıraya getir (`data` sonrası).
2. Faz 2 adımlarını **kesintisiz tek blok** hâline getir.
3. `_ms_progress_steps` listesini yeni sıraya göre güncelle.

**Dikkat:**
- `_master_formasyon_snapshot` / `_master_formasyon_ready` `app.py:15246-15247`'de
  ilklendiriliyor — faz 1 adımlarından **önce** kalmalı.
- `radar2` faz 2'ye giderken: `liderlik_yolculugu_data`
  (`scan_leadership_lifecycle(radar2_data, erken_radar)`) **radar2'ye muhtaç** →
  o da faz 2'de kalmalı. Katalog sekmesinde görünür, faz 2'de dolar.
- `flow_leaders` yalnız BIST kategorisinde koşuyor (`_ms_is_bist` koşulu) — koru.

**Doğrulama:** `python golden_record.py` → sıfır fark bekleniyor.

---

## 6. AŞAMA C — faz sınırı (asıl iş)

**Kurulacak akış:**

1. Master Scan bloğu (15109) **yalnız faz 1**'i koşar, sonra:
   - `st.session_state._ms_faz2_bekliyor = [kalan adım anahtarları]`
   - `st.session_state._ms_faz2_baglam = {...}` → faz 2'nin ihtiyacı olan her şey
     (`scan_list`, `_cat`, `_master_batch_snapshot`, `_master_benchmark_snapshot`,
      `_master_snapshot_as_of`, `_ms_is_bist` …)
   - `_master_scan_running` bayrağını **faz 2 bitene kadar açık tut**
2. Betik akmaya devam eder → **ekran çizilir** → kullanıcı Tarama Merkezi'ni görür
3. Betiğin **EN DİBİNDE** (26187'deki `with col_right:` bloğundan sonra):
   ```
   if st.session_state.get('_ms_faz2_bekliyor'):
       <faz 2 adımlarını sırayla koş, her biri bitince listeden düş>
       <bitince: bağlamı temizle, snapshot/karne/completed işlerini yap>
       st.rerun()
   ```
4. `st.rerun()` sonrası sayfa tam veriyle yeniden çizilir.

**Kritik kurallar:**
- Rerun **Master Scan'i yeniden tetiklemesin** — `_manual_master_scan` /
  `_auto_master_scan` koşulunu mutlaka koru/kilitle. Sonsuz döngü riski burada.
- Faz 2 adımları **birer birer** listeden düşsün ki yarıda kalırsa nerede
  kaldığı bilinsin (Aşama D bunu kullanacak).
- Katalog sekmesi faz 1'de eksik olacak → boş bırakma, **"hesaplanıyor" yuvası**
  koy. Kodda hazır kalıp var: `_tavan_adaylari_slot` / `_sinyal_ozet_slot`
  (`st.empty()` yuvası, içerik sonradan basılıyor). Aynısını kullan.

---

## 7. AŞAMA D — kesinti uyarısı (kullanıcının açık isteği)

Faz 2 koşarken kullanıcı ekranda bir şeye tıklarsa Streamlit betiği keser.
Kullanıcının istediği davranış **aynen şu**:

> Tıklandığında **önce açılır pencere** gelsin:
> *"Şu anda devam ederseniz Master Scan'in şu kısımları yarım kalacak:
> [henüz yapılmamış taramaların listesi]. Devam etmek istiyor musunuz?"*
> Kullanıcı **evet** derse yarım bıraksın ve **o gün için yapılmayan taramalara
> not düşsün.**

**Uygulama notları:**
- Liste `_ms_faz2_bekliyor` içinden gelecek — teknik anahtar değil,
  **kullanıcının gördüğü isimler** yazılsın (ör. "💎 Altın + Platin fırsatlar",
  "🧠 Ön filtre — Radar 1"). Eşleme `_ms_progress_steps` etiketlerinden yapılabilir.
- Pencere için `@st.dialog` kullan (kodda örneği var: `_formasyon_v2_dialog`,
  `_kapanis_master_soft_notice`).
- "Devam et" seçilirse: `_ms_faz2_bekliyor` temizlenmeden **eksik kaldı** olarak
  işaretlensin ve gün kaydına yazılsın. Mevcut kanal: `patron_db_guard.write_daily_karne`
  ve `kapanis_master_otomasyon.mark_scan_completed(critical_failures=[...])` —
  ikincisi zaten "kısmi tamamlandı" kavramını biliyor, onu kullan, yeni kavram uydurma.
- "Vazgeç" seçilirse tıklama yutulsun, faz 2 devam etsin.

---

## 8. BU KODA ÖZGÜ TUZAKLAR (bugün canlı yaşandı)

1. 🔴 **golden bu işi doğrulayamaz.** `golden_record.py` fonksiyon tanımlarını ve
   hesapları ölçer ama **Master Scan bloğunu çalıştırmaz**. Aşama B'de golden yeterli;
   **C ve D için tek gerçek doğrulama, gerçek bir Master Scan koşusudur.**
2. 🔴 **Seans içinde sinyal yazılamaz.** `_bayat_yazim_kapisi` (scan_pipeline.py:688)
   seans sürerken yazmayı reddeder. Test için ya kapanıştan sonra koş, ya da
   `SMR_BAYAT_YAZIM_IZNI=1` ile geçici aç. **Bu değişkenle canlı patron.db'ye test
   satırı yazma** — 31 Ağu'da tam bunu yaptım, `UNIQUE(scan_date, symbol)` yüzünden
   akşamki gerçek turun satırlarını engelleyecekti, silmek zorunda kaldım.
3. 🔴 **`--init` refleksi tehlikeli.** golden fark gösterince önce **farkın ne
   olduğunu anla**. 31 Ağu'da seans-içi yanlış alarm vardı, `--init` deseydik
   "0 satır normaldir" diye mühürlenecekti. (O yanlış alarm `dd517f9` ile düzeltildi.)
4. ⚠ **radar1 "gizli" DEĞİL.** Radar 2 panelinin kapısı (skor ≥4 kesişimi) ve tek
   hisse paneli R1 skorunu oradan okur. Ayrıca evren cetveli (son 30 günde 11.431
   sinyal; ikinci sıradaki tarama 4.442). **Silme, atlama, "nasılsa görünmüyor" deme.**
   Gerekçe `app.py`'de radar1 adımının başına yazıldı.
5. ⚠ **Yeni hesap kodu app.py'ye yazılmaz** (CLAUDE.md kalıcı kuralı) — ayrı modüle.
   Bu görev çoğunlukla **akış** işi, yeni hesap gerekmiyor; gerekirse ayrı modül aç.
6. ⚠ **Deploy:** `./deploy.sh` kuru çalışma → sonra `--go`. İki ajan aynı dizindeyse
   `--only=dosya1,dosya2` kullan. `--force` sadece diff'leyip sahibini doğruladıktan
   sonra. VPS'te `git pull` YAPMA (SCP-ONLY).

---

## 9. BİTİRME KONTROL LİSTESİ

- [ ] Aşama B sonrası `python golden_record.py` → sıfır fark
- [ ] Aşama C sonrası **gerçek Master Scan koşusu** (kapanıştan sonra) → faz 1
      ekranı geliyor mu, faz 2 tamamlanıyor mu, rerun döngüye girmiyor mu
- [ ] Faz 1 süresi ölçüldü mü (hedef ~5 dk) — `master_scan_timing_profile.json`
- [ ] Faz 2 sonunda **eskisiyle aynı** çıktılar üretiliyor mu: site JSON export,
      patron.db→VPS sync, snapshot kaydı, günlük karne, `mark_scan_completed`
- [ ] Katalog sekmesi faz 1'de "hesaplanıyor" gösteriyor, faz 2'de doluyor
- [ ] Aşama D penceresi: liste doğru mu, "devam" eksik notu düşüyor mu
- [ ] Kesinti sonrası **ertesi gün** tarama normal koşuyor mu (takılı bayrak kalmasın)
- [ ] `python -c "import ast; ast.parse(open('app.py',encoding='utf-8').read())"`
- [ ] deploy kuru çalışma → ezme kontrolü temiz → `--go`

---

## 10. YAPMA

- ❌ Hiçbir taramayı kaldırma, atlama, "zaten zayıf" diye eleme.
  **Kullanıcının açık talimatı:** tüm taramalar mevcut formatıyla koşmaya devam edecek.
- ❌ Faz 2'yi ayrı bir iş parçacığına (thread) atma. Streamlit oturum durumu
  thread-güvenli değil; `st.*` çağrıları ScriptRunContext ister. Betiğin dibinde
  sıralı koşsun.
- ❌ Master Scan bloğunun içine `st.rerun()` koyma (eski hatası, Erken Radar öldü).
- ❌ golden `--init`'i farkı anlamadan çalıştırma.
- ❌ Ölçmediğin bir süre iddiası yazma; profil dosyası tek kaynak.

---

## 11. AŞAMA A'DA NE YAPILDI (bitmiş iş — tekrarlama)

Commit `61b91f8`:
- Formasyon bloğu (`scan_chart_patterns` + birleşik log + `kirilima_yakin_form` +
  `formasyon_master_data`) `golden` adımından **ayrıldı**, kendi
  `_scan_progress("formasyon", …)` adımı oldu ve `golden`'dan **öne** alındı.
- `_ms_progress_steps` listesine `"formasyon"` eklendi.
- radar1 ve KATMAN 5 yorumları düzeltildi (yanlış "UI'da gizli" ifadesi kaldırıldı).
- golden: sıfır fark.

Yeni sıra şu an:
`… data → magic_ribbon → hidden_accum → radar2 → formasyon → golden → cizgi_yapi → minervini → weak_pair → radar1 → rsi_divergence → strong_reversal → tavan → flow_leaders → prelaunch → early_radar → stp_uyanis → toplu_terazi → top20`

Aşama B'nin işi bu sırayı §4'teki faz listesine göre yeniden dizmek.
