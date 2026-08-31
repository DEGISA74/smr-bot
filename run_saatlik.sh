#!/bin/bash
# Saatlik fetcher zamanlama sarmalayıcısı (3 Ağu 2026) — seans-saati + kilit kapılı.
# Task Scheduler 30dk'da bir çağırır; script kendini yönetir. Günlük sisteme dokunmaz.
ROOT="/c/Users/LENOVO/OneDrive/Desktop/Patron Terminal"
cd "$ROOT" || exit 1
LOG="$ROOT/logs/saatlik.log"

# seans + hafta içi kapısı (saatlik bar seans içinde oluşur; kapanışta bir tur da yeter)
dow=$(date +%u); hm=$((10#$(date +%H%M)))
[ "$dow" -gt 5 ] && exit 0
[ "$hm" -lt 1000 ] && exit 0          # 10:00 öncesi (seans açılmadan) yok
[ "$hm" -gt 1845 ] && exit 0          # 18:45 sonrası yok (19 Ağu 2026: kapanış 18:10 → son tur 18:35 tetikleyicisiyle 18:45 öncesi biter)

# kilit: önceki tur bitmediyse atla (fetcher ~5-10dk sürebilir)
# ⚠ 26 Ağu 2026 — KİLİT SAHTEYDİ. Ölçüt "dosya 25 dk'dan eskiyse serbest"ti; tur
# 25 dk'yı aştığında yeni tur başlıyor, ikisi AYNI Yahoo kotasını paylaşınca tur
# daha da uzuyor → yığılma. Canlı yakalandı: 17:54 + 18:25 + 18:36 turları aynı
# anda koşuyordu, tur 25 dk yerine ~75 dk sürüyordu ve 26 Ağu'da 11:23-18:29
# arasında (7 saat, seansın tamamı) HİÇ tur bitmedi → saatlik depoda 35 hisse
# bayat kaldı, panel "saat saat akıştan" yerine "gün kapanışlarından" dedi.
# Artık ölçüt YAŞ değil, önceki turun SÜRECİ HÂLÂ YAŞIYOR MU.
# (Aynı hastalığın VPS'teki hâli için: memory/project_fetcher_cron_pileup.md)
LOCK="$ROOT/veriler_saatlik/.saatlik.lock"
if [ -f "$LOCK" ]; then
    onceki=$(cat "$LOCK" 2>/dev/null)
    yas=$(( ($(date +%s) - $(stat -c %Y "$LOCK")) / 60 ))
    if [ -n "$onceki" ] && kill -0 "$onceki" 2>/dev/null; then
        # Yeni kilit "hayatta mi" sorduğu için, gerçekten TIKANMIŞ bir tur artık
        # sonsuza kadar sırayı kapatabilir (eski koddaki 25 dk'lık kaçış deliği yok).
        # Sessiz ölmesin: normal tur ~25 dk; 90 dk'yı geçen tur arızadır, log'a
        # aranabilir bir damga bırak. Öldürmüyoruz — parquet yazımı atomik değil.
        if [ "$yas" -gt 90 ]; then
            echo "$(date '+%F %T') UYARI TIKANMIS TUR: pid $onceki $yas dk'dir suruyor - saatlik veri tazelenmiyor" >> "$LOG"
        else
            echo "$(date '+%F %T') onceki tur hala calisiyor (pid $onceki, $yas dk) - atlandi" >> "$LOG"
        fi
        exit 0
    fi
    echo "$(date '+%F %T') sahipsiz kilit temizlendi (pid ${onceki:-yok}, $yas dk)" >> "$LOG"
    rm -f "$LOCK"
fi
echo $$ > "$LOCK"
# yarıda kesilirse (kapanma/kill) kilit ortada kalmasın
trap 'rm -f "$LOCK"' EXIT
# intraday_4s.py: tek çekişten hem veriler_saatlik/ (1h) hem veriler_4s/ (4h) — likit ilk 150
# PYTHONIOENCODING: → gibi karakterler cp1254'te print'i çökertmesin (tüm liste bitsin)
export PYTHONIOENCODING=utf-8
# ⚠ ENDEKS FIX (10 Ağu 2026): likit_liste XU/XB/XT/XY endekslerini DIŞLIYOR → XU100 saatliği
# hiç güncellenmiyordu (3 Ağu'da takılı kaldı). Trajectory otomasyonu XU100 saatliğini referans
# saat + benchmark olarak ZORUNLU ister. Ayrı ve ÖNCE çek: liste throttle olsa bile taze kalsın.
"$ROOT/.venv/Scripts/python.exe" "$ROOT/intraday_4s.py" XU100 XU030 XBANK >> "$LOG" 2>&1
# 14 Ağu 2026: 150 → 250. Ölçüldü: sembol başına ~0,66 sn, 250 sembol ≈ 3 dk.
# 30 dk'lık pencerede bol yer var (kilit riski yok). Saatlik OBV düzeltmesinin
# kapsaması %21 → ~%43. Log'da tur süresi 10 dk'yı aşarsa geri çek.
"$ROOT/.venv/Scripts/python.exe" "$ROOT/intraday_4s.py" --liste 250 >> "$LOG" 2>&1
# 19 Ağu 2026 — KAPSAM DOSYASI: okuyan taraf (app) hangi sembollerin gerçekten
# güncellendiğini buradan öğrenir. Turda tazelenmezse app günün ilk açılışında
# listeyi kendisi hesaplar (~2,3 sn) — o gecikme kullanıcıya binmesin.
"$ROOT/.venv/Scripts/python.exe" -c "import saatlik_kapi as k; print('kapsam', len(k.kapsam_listesi(force=True)))" >> "$LOG" 2>&1
# 19 Ağu 2026 — TAM GÜN UZLAŞMA KAPISI: saatlik geçmişi günlükle uzlaşmayan
# hisseleri (bölünme/bedelsiz sonrası bazlanmamış) karantinaya alır. ~13 sn.
# Her turda koşar — bölünme düzeltmesi gün içinde inebiliyor (ORGE, 19 Ağu 21:55).
"$ROOT/.venv/Scripts/python.exe" "$ROOT/saatlik_uzlasma.py" --yaz >> "$LOG" 2>&1
# 31 Ağu 2026 — 4S DEPOSU VPS'E İTİLİR. Sebep: Magic Ribbon (BIST100 4S yukarı
# hizalanma) canlı uygulamada bu depoyu okur, ama VPS'te 4S üreten HİÇBİR görev
# yok — depo 25 Ağu'da donmuş kalmıştı ve tazelik kapısı 100 hissenin hepsini
# eliyordu (canlıda 0 aday, lokalde 38). Bu zincirin tek üreticisi burası.
# Yalnız BU TURDA tazelenen dosyalar gider (12 MB'lık depoyu her tur itmeyiz).
# VPS ulaşılamazsa saatlik tur ÖLMEZ — sadece log'a düşer.
_4S_YENI=$(find veriler_4s -name '*_4h.parquet' -newermt '-100 minutes' 2>/dev/null)
if [ -n "$_4S_YENI" ]; then
    _4S_ADET=$(echo "$_4S_YENI" | wc -l)
    if echo "$_4S_YENI" | tar -czf - -T - 2>/dev/null \n         | ssh -o ConnectTimeout=20 -o BatchMode=yes wm11tr@34.153.19.220 \n               "mkdir -p ~/smr/veriler_4s && tar -xzf - -C ~/smr" >> "$LOG" 2>&1; then
        echo "$(date '+%F %T') 4S -> VPS: $_4S_ADET dosya gonderildi" >> "$LOG"
    else
        echo "$(date '+%F %T') 4S -> VPS itme BASARISIZ (tur etkilenmedi)" >> "$LOG"
    fi
fi
echo "$(date '+%F %T') saatlik+4h turu bitti" >> "$LOG"
rm -f "$LOCK"
exit 0
