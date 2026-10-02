#!/usr/bin/env python3
"""
islem_gunu_mu.py — cron kapısı: "bugün BIST işlem günü mü?" (2 Eki 2026)

Kullanım (crontab):  <venv python> islem_gunu_mu.py && <iş komutu>
  çıkış 0 → işlem günü (normal veya yarım gün) → iş çalışır
  çıkış 1 → kapalı (hafta sonu / resmî veya dini bayram) → iş HİÇ başlamaz
  --dun   → gece yarısından sonra koşan işler için: dünün (TR) durumuna bakar
Tek kaynak: bist_calendar (2026 Pay Piyasası resmî tatil tablosuyla doğrulandı).
Takvim okunamazsa çıkış 0 (iş yine çalışsın — kaçırmak, gereksiz çalışmaktan kötü).
Test: ISLEM_GUNU_TARIH=2026-10-29 python islem_gunu_mu.py; echo $?
"""
import os
import sys
from datetime import date, datetime, timedelta

try:
    import pytz
    _TR = pytz.timezone("Europe/Istanbul")
    bugun = datetime.now(_TR).date()
except Exception:
    bugun = (datetime.utcnow() + timedelta(hours=3)).date()

if os.environ.get("ISLEM_GUNU_TARIH"):
    bugun = date.fromisoformat(os.environ["ISLEM_GUNU_TARIH"])
if "--dun" in sys.argv:
    bugun -= timedelta(days=1)

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from bist_calendar import is_trading_day
    sys.exit(0 if is_trading_day(bugun) else 1)
except Exception as e:
    print(f"[islem_gunu_mu] takvim okunamadı ({e}) — iş çalıştırılıyor", file=sys.stderr)
    sys.exit(0)
