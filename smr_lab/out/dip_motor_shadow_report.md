# İş 5 — Yeni Dip Motoru Gölge Testi

**Genel hüküm:** EKRANA ALINAMAZ KANITLANDI · FİKİR ÇÜRÜTÜLMEDİ.

- Veri: 627 günlük dosya · 229 dosyada 4S doğrulama · 398 dosya (63.5%) 4S kapsamı dışında kaldı
- Dönem: 2025-01-24 → 2026-08-27 · doğrulama başlangıcı: 2026-06-01
- Ham aday: 79 · bağımsız ve olgun olay: **72**
- Giriş: ertesi işlem yapılabilir açılış; tavan kilidi en fazla 3 seans atlandı; hisse ve XU100 aynı giriş gününde ölçüldü.
- Rejim: XU100_CLOSE_VS_SMA50; yalnız ölçüm bölmesi, canlı tarama filtresi değil.

## Ana bulgu — dağılım

Dört sabit vadenin birlikte okunduğu altı bağımsız dönem×rejim kesitinin tamamında XU100'e karşı alfa ortancası negatiftir. Bu dağılım, birkaç uç getirinin şişirdiği ortalamalardan daha güçlü kanıttır:

| Kesit | Alfa ortancası |
|---|---:|
| Tüm × yükselen | -2.83% |
| Tüm × düşen | -3.58% |
| Eğitim × yükselen | -2.76% |
| Eğitim × düşen | -3.14% |
| Doğrulama × yükselen | -5.68% |
| Doğrulama × düşen | -6.20% |

## Sabitlenen hipotez eşikleri

15 seans dip penceresi · hacim ≥ 1.5× önceki 20 seans medyanı · RSI farkı ≥ 2.0 puan · 4S önceki 3 mini tepe kırılımı · olay aralığı 10 seans · hedef +%3.0 / zarar −%2.5.

## Yedi maddelik kabul kapısı

| Kapı | Sonuç |
|---|---|
| 1_three_horizons | **GEÇTİ** |
| 2_two_regimes | **GEÇTİ** |
| 3_first_five_target_vs_stop | **KALDI** |
| 4_xu100_alpha | **KALDI** |
| 5_same_entry_table | **GEÇTİ** |
| 6_independent_events | **KALDI** |
| 7_unseen_validation | **KALDI** |

## Toplu sonuç

| Grup | Olay | Hedef önce | Zarar önce | T+3 alfa ort/med | T+5 alfa ort/med | T+20 alfa ort/med |
|---|---:|---:|---:|---:|---:|---:|
| Tüm olgun olaylar | 72 | 26 | 27 | -1.48/-1.90 | -1.54/-3.20 | +2.32/-5.45 |
| Eğitim dönemi | 64 | 24 | 24 | -1.24/-1.72 | -0.98/-3.00 | +2.73/-5.10 |
| Görülmemiş doğrulama | 8 | 2 | 3 | -3.34/-4.65 | -6.03/-5.32 | -0.95/-6.65 |

## Rejim kırılımı — tüm olgun olaylar

| Rejim | Olay | Hedef önce | Zarar önce | T+3 alfa ort/med | T+5 alfa ort/med | T+10 alfa ort/med | T+20 alfa ort/med |
|---|---:|---:|---:|---:|---:|---:|---:|
| Yükselen | 44 | 15 | 16 | -1.82/-1.47 | -1.91/-2.97 | -1.99/-3.99 | -0.49/-4.69 |
| Düşen | 22 | 10 | 9 | +1.41/-2.21 | +1.24/-3.57 | +1.52/-3.80 | +11.85/-5.75 |

## Görülmemiş doğrulama — rejim kırılımı

| Rejim | Olay | Hedef önce | Zarar önce | T+3 alfa ort/med | T+5 alfa ort/med | T+10 alfa ort/med | T+20 alfa ort/med |
|---|---:|---:|---:|---:|---:|---:|---:|
| Yükselen | 3 | 0 | 1 | -10.33/-12.42 | -9.10/-9.27 | -4.01/-3.00 | +3.31/+4.11 |
| Düşen | 5 | 2 | 2 | +0.86/-3.41 | -4.19/-3.30 | -3.83/-8.12 | -3.51/-6.80 |

## Güvenlik hükmü

Bu betik patron.db'ye, scan_signals'a, app.py'ye ve canlı ekrana yazmaz. Kapı geçmediyse hipotez yalnız laboratuvar çıktısıdır; eşikler gevşetilmez.

**Yeniden koşma şartı:** 4S/saatlik veri deposunun kapsamı düzeltildiğinde aynı sabit eşiklerle test yeniden çalıştırılacak. Mevcut sonuç, evrenin yaklaşık üçte ikisi 4S verisi olmadığı için fikri çürütmez.
