#!/bin/bash
# Lokal yalnız aday üretir; ana BIST verisinin tek yazıcısı VPS'tir.
ROOT="/c/Users/LENOVO/OneDrive/Desktop/Patron Terminal"
cd "$ROOT" || exit 1
LOG="$ROOT/logs/settle.log"
VPS="wm11tr@34.153.19.220"
dow=$(date +%u); hm=$((10#$(date +%H%M)))
[ "$dow" -gt 5 ] && exit 0
[ "$hm" -lt 1830 ] && exit 0
export PYTHONIOENCODING=utf-8

OUT=$("$ROOT/.venv/Scripts/python.exe" "$ROOT/settle_kapanis.py" 2>&1)
RC=$?
printf '%s\n' "$OUT" >> "$LOG"
[ "$RC" -ne 0 ] && exit "$RC"
PACKAGE=$(printf '%s\n' "$OUT" | sed -n 's/^CANDIDATE_PACKAGE=//p' | tail -n 1)
[ -z "$PACKAGE" ] && exit 0
[ ! -f "$PACKAGE" ] && { echo "$(date '+%F %T') aday paketi bulunamadı" >> "$LOG"; exit 2; }

REMOTE="candidate_$(date +%Y%m%d_%H%M%S).zip"
scp -q -o ConnectTimeout=20 -o BatchMode=yes "$PACKAGE" "$VPS:~/smr/health/bist_store/inbox/$REMOTE"
[ "$?" -ne 0 ] && { echo "$(date '+%F %T') aday gönderilemedi" >> "$LOG"; exit 3; }
ssh -o ConnectTimeout=20 -o BatchMode=yes "$VPS" \
  "cd ~/smr && venv/bin/python bist_exchange.py apply-candidate health/bist_store/inbox/$REMOTE" \
  >> "$LOG" 2>&1
RC=$?
echo "$(date '+%F %T') aday VPS kapısından geçti (rc=$RC)" >> "$LOG"
exit "$RC"
