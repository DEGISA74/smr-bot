#!/usr/bin/env bash
# S&P 200 kapanış sonrası veri turu. Cron eklenirse aynı anda ikinci turu engeller.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-all}"
LOG_DIR="$ROOT/logs"
LOCK_FILE="$ROOT/us200_data/.fetch.lock"

case "$MODE" in
  daily|hourly|all) ;;
  *) echo "Kullanim: $0 [daily|hourly|all]" >&2; exit 2 ;;
esac

mkdir -p "$LOG_DIR" "$ROOT/us200_data"
exec flock -n "$LOCK_FILE" \
  env US200_MIN_DELAY=2.5 US200_MAX_DELAY=4.0 \
  "$ROOT/venv/bin/python" "$ROOT/us200_fetcher.py" --mode "$MODE" \
  >> "$LOG_DIR/us200_fetch.log" 2>&1
