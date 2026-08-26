#!/usr/bin/env bash
# VPS'te yalnız gölge veri kapısını her hafta içi 20:05/20:20/20:35 İstanbul saatinde çalıştırır.
# Sunucu UTC kullandığı için karşılığı 17:05/17:20/17:35'tir. Sinyal veya patron.db yazmaz.
set -euo pipefail
ROOT="${1:-$HOME/smr}"
ENTRY="5,20,35 17 * * 1-5 cd ${ROOT} && /usr/bin/flock -n ${ROOT}/master_scan_shadow_cron.lock ${ROOT}/venv/bin/python master_scan_vps_shadow.py --json >> ${ROOT}/logs/master_scan_shadow.log 2>&1"
DB_ENTRY="5 17 * * 1-5 cd ${ROOT} && /usr/bin/flock -n ${ROOT}/master_scan_shadow_db.lock ${ROOT}/venv/bin/python master_scan_shadow_db.py >> ${ROOT}/logs/master_scan_shadow_db.log 2>&1"
CURRENT="$(crontab -l 2>/dev/null || true)"
if grep -Fq "master_scan_vps_shadow.py" <<<"$CURRENT"; then
  echo "OK: gölge veri kapısı zaten var"
else
  CURRENT="${CURRENT}"$'\n'"${ENTRY}"
fi
if grep -Fq "master_scan_shadow_db.py" <<<"$CURRENT"; then
  echo "OK: gölge DB zamanlayıcısı zaten var"
else
  CURRENT="${CURRENT}"$'\n'"${DB_ENTRY}"
fi
printf '%s\n' "$CURRENT" | crontab -
echo "OK: VPS gölge Master Scan zamanlayıcısı kuruldu"
