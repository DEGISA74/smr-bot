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
_(buraya ekle)_
