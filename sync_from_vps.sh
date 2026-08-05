#!/bin/bash
# VPS -> lokal: yalnız onaylı sürüm paketi; hash doğrulaması ve aktif işaret en son.
ROOT="/c/Users/LENOVO/OneDrive/Desktop/Patron Terminal"
cd "$ROOT" || exit 1
LOG="$ROOT/logs/sync_vps.log"
VPS="wm11tr@34.153.19.220"

dow=$(date +%u); hm=$((10#$(date +%H%M)))
[ "$dow" -gt 5 ] && exit 0
[ "$hm" -lt 700 ] && exit 0
[ "$hm" -gt 1900 ] && exit 0

LOCK="$ROOT/health/bist_store/.sync.lock"
mkdir -p "$ROOT/health/bist_store/inbox"
if [ -f "$LOCK" ]; then
    age=$(( ($(date +%s) - $(stat -c %Y "$LOCK")) / 60 ))
    [ "$age" -lt 8 ] && exit 0
fi
touch "$LOCK"

BASE=$("$ROOT/.venv/Scripts/python.exe" -c "from bist_data_store import active_version_id; print(active_version_id())")
STAMP=$(date +%Y%m%d_%H%M%S)
REMOTE="health/bist_store/outbox/sync_${STAMP}.zip"
LOCAL="$ROOT/health/bist_store/inbox/sync_${STAMP}.zip"

ssh -o ConnectTimeout=20 -o BatchMode=yes "$VPS" \
  "cd ~/smr && venv/bin/python bist_exchange.py export '$REMOTE' --base '$BASE'" \
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
