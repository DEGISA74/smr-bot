# -*- coding: utf-8 -*-
"""8502'de çalışan, yalnız ABD ilk-200 evrenini açan Patron Terminal kabuğu."""
from __future__ import annotations

import os
import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get("SMR_US_DATA_ROOT", ROOT / "us200_data"))
DAILY_DIR = DATA_ROOT / "daily"
HOURLY_DIR = DATA_ROOT / "hourly"
FOUR_HOUR_DIR = DATA_ROOT / "four_hour"

DAILY_DIR.mkdir(parents=True, exist_ok=True)
HOURLY_DIR.mkdir(parents=True, exist_ok=True)
FOUR_HOUR_DIR.mkdir(parents=True, exist_ok=True)

# Bu değişkenler app.py ve ortak motorlar import edilmeden önce yerleşmelidir.
os.environ["SMR_MARKET_PROFILE"] = "US200"
os.environ["SMR_CACHE_DIR"] = str(DAILY_DIR)
os.environ["SMR_1H_DIR"] = str(HOURLY_DIR)
os.environ["SMR_4H_DIR"] = str(FOUR_HOUR_DIR)
os.environ["SMR_DB_FILE"] = str(DATA_ROOT / "patron_us200.db")
os.environ["SMR_US_MIRROR_READONLY"] = "1"

runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
