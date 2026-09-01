#!/bin/bash
# Magic Ribbon için bağımsız, kapanış-sonrası TradingView yenileme hattı.
# Yahoo/günlük/saatlik hatlarına dokunmaz; yalnız seans-mumu kasasını üretir.
# 1 Eyl 2026: yenileyici artik tek sembol dusunce degil, basari orani %90'in
# altina inince hata doner. Kismi turlar da VPS'e gider; bayat sembolu tarama eler.
ROOT="/c/Users/LENOVO/OneDrive/Desktop/Patron Terminal"
cd "$ROOT" || exit 1
LOG="$ROOT/logs/magic_ribbon_refresh.log"
LOCK="$ROOT/veriler_magic_ribbon_seans/.magic_ribbon_refresh.lock"

dow=$(date +%u); hm=$((10#$(date +%H%M)))
[ "$dow" -gt 5 ] && exit 0
[ "$hm" -lt 1815 ] && exit 0
[ "$hm" -gt 1945 ] && exit 0

mkdir -p "$ROOT/veriler_magic_ribbon_seans"
if [ -f "$LOCK" ]; then
    onceki=$(cat "$LOCK" 2>/dev/null)
    if [ -n "$onceki" ] && kill -0 "$onceki" 2>/dev/null; then
        echo "$(date '+%F %T') onceki Magic Ribbon turu hala calisiyor (pid $onceki) - atlandi" >> "$LOG"
        exit 0
    fi
    rm -f "$LOCK"
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

export PYTHONIOENCODING=utf-8
if "$ROOT/.venv/Scripts/python.exe" "$ROOT/magic_ribbon_refresh.py" >> "$LOG" 2>&1; then
    if tar -czf - veriler_magic_ribbon_seans 2>/dev/null | \
        ssh -o ConnectTimeout=20 -o BatchMode=yes wm11tr@34.153.19.220 \
        "mkdir -p ~/smr && tar -xzf - -C ~/smr" >> "$LOG" 2>&1; then
        echo "$(date '+%F %T') Magic Ribbon seans kasasi VPS'e gonderildi" >> "$LOG"
    else
        echo "$(date '+%F %T') Magic Ribbon VPS aktarimi basarisiz (lokal kasa korundu)" >> "$LOG"
    fi
else
    echo "$(date '+%F %T') Magic Ribbon turu esigin altinda kaldi (cok sembol dustu); VPS'e veri gonderilmedi" >> "$LOG"
fi
