#!/bin/bash
# VPS -> lokal: yalnız onaylı sürüm paketi; hash doğrulaması ve aktif işaret en son.
ROOT="/c/Users/LENOVO/OneDrive/Desktop/Patron Terminal"
cd "$ROOT" || exit 1
LOG="$ROOT/logs/sync_vps.log"
VPS="wm11tr@34.153.19.220"

# 18 Agu 2026 - SAAT KAPISI YERINE TELAFI MANTIGI.
# Eski kapi (hafta ici 07:00-19:00) PC aksam veya hafta sonu acildiginda senkronu
# tamamen susturuyordu: PC uzun sure kapali kalinca depo 14:00'da dondu, kapanis
# fiyatlari elle cekildi. Yeni kural: (a) hafta ici 07:00-23:30 normal pencere -
# VPS'in aksam kesinlestirme turlari da (18:45 fiyat, 20:15/22:15 hacim) bu
# pencereye girer; (b) pencere disinda YALNIZ depo bayatsa telafi turu. Bayatlik
# kontrolu LOKAL manifestten yapilir, VPS'e hic yuk binmez.
dow=$(date +%u); hm=$((10#$(date +%H%M)))
if [ "$dow" -le 5 ] && [ "$hm" -ge 700 ] && [ "$hm" -le 2330 ]; then
    :
elif "$ROOT/.venv/Scripts/python.exe" "$ROOT/depo_tazelik.py" --quiet; then
    echo "$(date '+%F %T') TELAFI TURU: depo bayat, pencere disi senkron" >> "$LOG"
else
    exit 0
fi

LOCK="$ROOT/health/bist_store/.sync.lock"
mkdir -p "$ROOT/health/bist_store/inbox"
if [ -f "$LOCK" ]; then
    age=$(( ($(date +%s) - $(stat -c %Y "$LOCK")) / 60 ))
    [ "$age" -lt 8 ] && exit 0
fi
touch "$LOCK"

# Yerel favoriler VPS fetcher'ının 5 dakikalık acil listesine gider. Yalnız
# watchlist.symbol değerleri taşınır; patron.db'nin diğer tabloları asla kopyalanmaz.
WATCHLIST_LOCAL="$ROOT/health/local_watchlist_symbols.json"
WATCHLIST_REMOTE="health/local_watchlist_symbols.json"
if "$ROOT/.venv/Scripts/python.exe" "$ROOT/watchlist_sync.py" export \
  --db "$ROOT/patron.db" --file "$WATCHLIST_LOCAL" >> "$LOG" 2>&1 \
  && scp -q -o ConnectTimeout=20 -o BatchMode=yes "$WATCHLIST_LOCAL" "$VPS:~/smr/$WATCHLIST_REMOTE" \
  && ssh -o ConnectTimeout=20 -o BatchMode=yes "$VPS" \
    "cd ~/smr && venv/bin/python watchlist_sync.py apply --db patron.db --file '$WATCHLIST_REMOTE'" >> "$LOG" 2>&1; then
    echo "$(date '+%F %T') Yerel favoriler VPS'e eşitlendi" >> "$LOG"
else
    # Fiyat aynası, favori senkronundaki geçici bir arıza nedeniyle durmaz.
    echo "$(date '+%F %T') UYARI: Yerel favoriler VPS'e eşitlenemedi" >> "$LOG"
fi

BASE=$("$ROOT/.venv/Scripts/python.exe" -c "from bist_data_store import active_version_id; print(active_version_id())")
STAMP=$(date +%Y%m%d_%H%M%S)
REMOTE="health/bist_store/outbox/sync_${STAMP}.zip"
LOCAL="$ROOT/health/bist_store/inbox/sync_${STAMP}.zip"

# 14 Ağu 2026 — OUTBOX TEMİZLİĞİ. Lokal zip aşağıda siliniyordu (satır sonu),
# VPS'teki hiç silinmiyordu: 9 günde 893 dosya / 557 MB birikti ve 20 GB'lık disk
# dolunca senkron 25 dakika durdu ("No space left on device").
# Paket = objects/ içindeki verinin taşınmak üzere paketlenmiş KOPYASI; teslimden
# sonra değeri yok. 2 gün pay bırakılır (başarısız tur olursa elde kalsın).
# Export'un ssh oturumuna gömülü — 5 dakikada bir fazladan bağlantı açmasın.
ssh -o ConnectTimeout=20 -o BatchMode=yes "$VPS" \
  "cd ~/smr && find health/bist_store/outbox -name 'sync_*.zip' -mtime +2 -delete 2>/dev/null; venv/bin/python bist_exchange.py export '$REMOTE' --base '$BASE'" \
  >> "$LOG" 2>&1
RC=$?
if [ "$RC" -eq 0 ]; then
    scp -q -o ConnectTimeout=20 -o BatchMode=yes "$VPS:~/smr/$REMOTE" "$LOCAL"
    RC=$?
fi
if [ "$RC" -eq 0 ]; then
    "$ROOT/.venv/Scripts/python.exe" "$ROOT/bist_exchange.py" import "$LOCAL" >> "$LOG" 2>&1
    RC=$?
fi
# Lokal nesne eksik/bozuksa artımlı paket bilinçli olarak reddedilir. Bir kez tam
# onaylı paket isteyip tekrar doğrula; yine olmazsa aktif lokal sürüm değişmez.
if [ "$RC" -ne 0 ]; then
    REMOTE_FULL="health/bist_store/outbox/sync_${STAMP}_full.zip"
    LOCAL_FULL="$ROOT/health/bist_store/inbox/sync_${STAMP}_full.zip"
    ssh -o ConnectTimeout=20 -o BatchMode=yes "$VPS" \
      "cd ~/smr && venv/bin/python bist_exchange.py export '$REMOTE_FULL'" \
      >> "$LOG" 2>&1
    RC=$?
    if [ "$RC" -eq 0 ]; then
        scp -q -o ConnectTimeout=20 -o BatchMode=yes "$VPS:~/smr/$REMOTE_FULL" "$LOCAL_FULL"
        RC=$?
    fi
    if [ "$RC" -eq 0 ]; then
        "$ROOT/.venv/Scripts/python.exe" "$ROOT/bist_exchange.py" import "$LOCAL_FULL" >> "$LOG" 2>&1
        RC=$?
    fi
    rm -f "$LOCAL_FULL"
fi
echo "$(date '+%F %T') VPS->lokal onaylı sürüm (base=$BASE rc=$RC)" >> "$LOG"
rm -f "$LOCAL" "$LOCK"
exit "$RC"
