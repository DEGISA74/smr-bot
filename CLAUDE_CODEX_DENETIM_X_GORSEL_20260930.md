# Claude → Codex · Denetim Raporu · X Görsel Düzeni (30 Eyl 2026)

> Amaç: Bu değişikliği bağımsız gözle denetle. Aşağıdaki "Denetim listesi" maddelerini
> tek tek doğrula; bulguyu bu dosyanın sonuna "CODEX BULGULARI" başlığıyla ekle.
> Hiçbir maddeyi düzeltmeden önce kullanıcıya sor (AJAN_KURALLARI).

## 1. Ne yapıldı (tek cümle)
App içindeki "📊 Görsel Analiz" kutusunun ürettiği görsel, X'te paylaşılmaya uygun yeni
bir düzene geçti: üstte tek cümle hüküm, solda büyük rakam kartları, sağda mum grafiği +
52H çubuğu, altta app'in "Para Akış İvmesi & Fiyat" ve "Sentiment & Fiyat" grafikleri.

## 2. Değişen dosyalar
| Dosya | Değişiklik |
|---|---|
| `infografik_build.py` | `build_widget_html` başına `if X_LAYOUT: return build_x_html(ticker)` · `render()` üstüne yeni blok: `X_LAYOUT`, `_x_*` yardımcıları, `build_x_html` |
| `clean_chart_plotly.py` | `build_fig(..., width=720)` parametresi · `build_ivme_fig(..., width, height, big)` · YENİ `build_sentiment_pair_fig` |

Varsayılanlar ESKİ değerler → mevcut çağıranlar (bot, `build_html`) davranış değiştirmez.
**Yeni hesap yok**, yalnız görselleştirme. Golden record koşulmadı (hesap değişmedi).

## 3. Veri kaynakları (app ile TEK KAYNAK olmalı — denetimin ana konusu)
| Görseldeki öğe | Kaynak |
|---|---|
| Alt iki grafik | `sentiment_chart_core.calculate_sentiment_chart(t,'daily','BIST')` → `build_sentiment_pair_fig`. Eksen kuralı app.py `render_synthetic_sentiment_panel` ile birebir (endeks ±max(20,⌈maks×1.15/5⌉×5), hisse ±max(30,maks×1.05)). Model None dönerse eski `build_ivme_fig`'e düşer. |
| Hacim / ortalama | app.py ~14415 yöntemi: bugün HARİÇ son 20 bar ort. → `seans_profili.rvol_paydasi` ile seansın geçen payına indirgenir; `yeterli=False` → "seans yeni başladı", kısmi barda rozet ("seansın %15'i"). |
| Fiyat · değişim · 5g/20g CMF · SMA50 kaç gün · RSI uç rozeti · kapanış gücü | `data_layer.get_safe_historical_data(t,'1y')`, sondaki Volume≤0 barlar atılarak (analysis_core ~1840 kuralı). CMF = `indicators.compute_cmf`. Rozet = `terazi_core.rsi_uc_rozeti(close, is_index)`. |
| Yön zaman haritası | app.py ~13553 eşiği: CMF > +0,05 alıcı / < −0,05 satıcı / arası denge; ana trend = SMA50 üstü/altı. |
| Mum grafiği + 52H | DEĞİŞMEDİ: `cc.build_fig` (parquet `cc.load`) + `ig.compute` (hi52/lo52/pos52). |
| RSI 1g–5g–14g satırı | `ig.compute` → `d['rsi_track']` (parquet). |

## 4. Deploy (VPS) — dikkat: kısmi gönderim
- 30 Eyl 10:54 elle gönderildi (deploy.sh adımları elle: yedek → scp → py_compile → import → VPS'te `build_x_html('XU100')` deneme → restart `patron-radar free-showcase` → health 200).
- Yedek: `~/smr/_yedek/20260930_105407_xgorsel`
- **Gönderilen = VPS'teki eski dosya + YALNIZ bu iş.** Lokal dosyalarda bekleyen, commit'lenmemiş BAŞKA değişiklikler bilerek gönderilmedi ve bu commit'e de alınmadı:
  - `infografik_build.py`: `get_narrative_name` (24 Eyl) · climax_bottom oyu +1→−1 (27 Eyl) · `ig.BENCHMARK` (VPS `infographic.py`'de BENCHMARK YOK → gönderilirse bot RS/Beta sessizce kaybolur) · "60.000 satır" metni
  - `clean_chart_plotly.py`: `SMR_CACHE_DIR` ortam değişkeni
- Commit de aynı yöntemle yapıldı (HEAD + yalnız bu iş; çalışma klasörü dokunulmadı).

## 5. Denetim listesi (Codex doğrulasın)
1. **Grafik eşitliği:** XU100 ve bir hisse (THYAO) için app panelindeki "Para Akış İvmesi & Fiyat" / "Sentiment & Fiyat" ile görseldeki alt grafikler aynı barları ve çizgileri gösteriyor mu? (Tarih etiketleri bilerek seyrek.)
2. **Hacim eşitliği:** Aynı anda app'in Akıllı Para / GENEL ÖZET hacim oyu ile görseldeki "Hacim / ortalama" aynı mı? Seans içi ve kapanış sonrası ayrı bak.
3. **Veri karışıklığı riski:** Üst kartlar `get_safe_historical_data`'dan, mum grafiği + RSI 1g–5g–14g + 52H `ig`/`cc` parquet'ten geliyor. Seans içinde iki kaynağın son barı farklı olabilir (ör. görselde fiyat 12.190 iken 52H konumu parquet'e göre). Kabul edilebilir mi, yoksa hepsi tek kaynağa mı alınmalı?
4. **Başlık cümlesi** (`_x_header`): 5g CMF + RSI rozeti + SMA50'den kuruluyor. Uç durumlar: alıcı+dip, satıcı+tepe, CMF None, SMA50 verisi yok → cümle anlamlı mı? AL/SAT/hedef dili YOK olmalı.
5. **"5 gün para akışı −%51,8"**: değer CMF5×100. Etiket "%" ile para akışı yüzdesi gibi okunuyor — app alıcı-satıcı tablosu da aynı biçimi kullanıyor, ama doğru mu?
6. **Sabit 1100px + JS zoom:** Streamlit components iframe'inde `zoom` her tarayıcıda çalışıyor mu (Brave/Chrome evet; Firefox yeni sürümler)? iframe yüksekliği app.py'de 1050 sabit → dar ekranda altta boş alan kalıyor; sorun mu?
7. **Endeks dışı / emtia / US:** `calculate_sentiment_chart` BIST profili ile çağrılıyor. US hisse veya `GC=F` seçilince ne oluyor — fallback eski grafiğe düşüyor mu, hata yok mu?
8. **Performans:** Görsel üretimi eskiden 3 figür, şimdi 2 figür + sentiment modeli + `get_safe_historical_data`. Arka plan thread'inde (`_ig_start_bg`) süre ölç; 120 sn sınırına yaklaşıyor mu?
9. **Bot etkilenmedi mi:** `infografik_telegram.py` → `render_bytes` → `build_html` eski düzende kalmalı. Doğrula.
10. **Bekleyen lokal farklar** (Bölüm 4): climax oy değişikliği ve `ig.BENCHMARK` kullanıcı onaylı mı, ne zaman ve hangi bağımlılıkla (`infographic.py`, `ticker_short_names.py`) gönderilecek?

## 6. Bilinen küçük kusurlar (düzeltilmedi)
- Sol alt grafikte "−20" eksen etiketi tarih satırına çok yakın.
- Seans başında `ig.load` parquet'inde hacim=0, fiyat=dünkü kapanış olan boş bugün barı var (veri hattı konusu); mum grafiği ve RSI 1g–5g–14g bundan etkilenebilir.

## 7. Geri alma
- Lokal/kod: `infografik_build.py` → `X_LAYOUT = False` (eski düzen döner).
- VPS: `ssh wm11tr@34.153.19.220 "cd ~/smr && cp _yedek/20260930_105407_xgorsel/* . && sudo systemctl restart patron-radar free-showcase"`

---
## CODEX BULGULARI
_(Codex kullanıcıya sözlü raporladı; özet aşağıda, Claude aktardı)_
1. XU100/THYAO üretimi çalışıyor, 120 sn sınırına yaklaşmıyor. ✅
2. Bot PNG'si eski `build_html` yolunda. ✅
3. Üst kartlar güvenli veri katmanından, mum/RSI/52H parquet'ten → seans içinde ayrışabilir. → **DÜZELTİLDİ (tur 2)**
4. "5 gün para akışı −%51,8" yanıltıcı (CMF×100, para yüzdesi değil). → **DÜZELTİLDİ**
5. BIST dışı sembollerde sentiment modeli `market_profile='BIST'` ile çağrılıyor. → **DÜZELTİLDİ**
6. 1100 px sabit genişlik + 1050 px iframe → kaydırma/boşluk riski; tarayıcı testi yok. → **iframe 1100'e çıkarıldı**; tarayıcı testi yalnız Edge/Brave.
7. Codex rapor dosyasını onaysız değiştirmedi. ✅

## TUR 2 — Claude düzeltmeleri (30 Eyl, 11:56–12:10)
| # | Ne | Nasıl |
|---|---|---|
| 5 | Piyasa profili | `_x_sentiment_profile`: BIST hisse/endeks → 'BIST' · ABD hissesi → 'US200' · emtia/kripto/döviz → model YOK, eski `build_ivme_fig` (app'in seçtiği yolun aynısı). Bar ekseni endeks kuralı app'teki gibi yalnız XU/XB/XT/XY/^. |
| 4 | CMF etiketi | "5 gün para akışı (CMF)" · değer kendi ölçeğinde (−0,35), yüzde değil. |
| 3 | Tek kaynak | Fiyat · değişim · 52H · RSI 1g–5g–14g artık `ig.compute(ticker, get_safe_historical_data)` → kartlarla aynı veri. Mum grafiği hâlâ parquet (bot da kullanıyor, dokunulmadı). |
| 6 | iframe | app.py `_comp.html(... height=1100)` (tek satır). |

**Tur 2'de bulunan YENİ hatalar (Codex denetlemeli):**
- **A · Hacim çifte düzeltme (görselde düzeltildi, APP'TE AÇIK).** `data_layer.apply_volume_projection` seans içi son barı zaten TAM GÜN TAHMİNİNE çeviriyor (`is_last_bar_projected` → True). Görsel ayrıca `seans_profili.rvol_paydasi` ile paydayı küçültüyordu → XU100 0,77× yerine **2,64×**. Görselde: tahmin varsa payda küçültülmez; tahmin yoksa ve BIST ise küçültülür; BIST dışında düzeltme yok ("gün tamamlanmadı" notu). ⚠ **app.py ~14418 (Akıllı Para hacim oyu) AYNI çifte düzeltmeyi yapıyor olabilir** — `_gs_df` `get_safe_historical_data`'dan geliyor (analysis_core ~1800) ve payda `_seans_rvol_paydasi` ile ayrıca küçültülüyor. Seans içinde hacim oyu şişik "evet" verebilir. Düzeltilmedi — kullanıcı onayı gerekli.
- **B · kaleido alt süreci ana süreci öldürüyordu (Linux).** `_render_batch_once` alt süreci Linux'ta ayrı grup açmadan başlatıyordu; `_kill_tree` → `os.killpg(getpgid(child))` = ÇAĞIRANIN grubu → Streamlit/cron süreci kendini SIGKILL'liyordu (VPS'te GC=F denemesi exit 137). Düzeltme: `start_new_session=(os.name != "nt")`. Windows değişmedi. Bot cron'u (`infografik_telegram.py XU100`, 16:05 UTC) aynı yoldan geçiyor → bu akşamdan itibaren düzeltmeli koşar.

**VPS (tur 2):** yedek `~/smr/_yedek/20260930_115616_xgorsel2` (infografik_build.py + app.py). Gönderilen = VPS dosyası + yalnız tur 2 farkları (app.py'de tek satır). Test (VPS, ayrı süreç): GC=F / XU100 / THYAO / GC=F → hepsi 2 görsel, hata yok. Render süresi VPS'te 7–46 sn oynuyor (2 çekirdek, Chrome soğuk açılış; artık Chrome kalıntısı yok). Restart → health 200, patron-radar / free-showcase / smr-bot active.

## TUR 3 — SMART MONEY RADAR ALGORİTMİK OKUMA (30 Eyl, 12:50)
- Başlık altındaki alt satır (kartları tekrar ediyordu) KALDIRILDI → yerine 3 cümlelik **kural tabanlı özet** kutusu (`_x_ozet`, AI değil; aynı veri → aynı metin): (1) SMA50 konumu + 5g/20g CMF ilişkisi, şiddet kovalı (±0,05 / 0,15 / 0,30); (2) ana yöne ters ("Ama") ya da destekleyen ("Üstelik") gözlemler: app para akış barları (3 gün küçülme/büyüme, işaret dönüşü), RSI uç rozeti, son kapanış yeri (+hacim ≥1,5×), hacim-fiyat uyumu, sentiment-fiyat ayrışması; (3) duruma özel koşul + hisseye özel SMA50 seviyesi.
- Kutunun sağ üstünde **app Kanıt Terazisi** satırı: görsel `<!--XTERAZI-->` yer tutucu bırakır, app.py `_finalize_infografik_slot` `x_terazi_satir(_ter_ig)` ile doldurur (ekran_v2 enjeksiyon kalıbı).
- Veri toplama `_x_data`'ya ayrıldı (özet grafik çizmeden üretilebilsin).
- X düzenine ayrı uyarı kutusu `_x_notice` (2 tam satır, iki yana yaslı, kullanıcı metni). Eski `_notice_badge` bot/eski düzende aynen.
- app.py iframe 1100 → 1200.
- VPS: yedek `_yedek/20260930_125021_xgorsel3`; VPS'te `get_narrative_name` YOK → özet adı ticker koduna düşer (XU100 yerine "BIST100" yazmaz) — bekleyen `ticker_short_names` işi gidince düzelir.
- **Denetlenecek:** özet cümleleri AL/SAT dili içermemeli; "Ama/Üstelik" seçimi yön mantığı; 3 cümlenin hisse evreninde kalıp tekrarına düşüp düşmediği (8 hisse denendi); terazi satırı ile app kartının aynı hükmü göstermesi.

**Açık kalanlar:** (A) app hacim oyu çifte düzeltme · Firefox/Safari zoom testi · render süresi VPS'te 60 sn'yi geçerse app'in 120 sn sınırına yaklaşır — izlenmeli.
