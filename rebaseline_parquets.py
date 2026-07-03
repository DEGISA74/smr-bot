#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rebaseline_parquets.py — TEK SEFERLİK TAM YENİDEN BAZLAMA (3 Tem 2026).

Politika TEK KAYNAK'ta birleştirildikten (data_policy.AUTO_ADJUST=False) sonra BİR KEZ
çalıştır: tüm BIST (*.IS) parquet'lerini Yahoo'dan HAM (auto_adjust=False) yeniden
indirir → 2 Tem'den beri biriken "karışık politika" bozulmasını TAMAMEN siler.

- Fiyat (OHLC): Yahoo'dan taze ham → aracı fiyatıyla birebir, süreksizlik yok.
- Hacim (Volume): eski parquet'teki İsyatirim-düzeltilmiş hacim KORUNUR (>0 olan günler).
- Yedek: veriler/_rebaseline_backups/ (geri dönülebilir).

Çalıştır:
  python rebaseline_parquets.py                 # tüm *.IS
  python rebaseline_parquets.py TUPRS AKBNK      # sadece verilenler (test)
  python rebaseline_parquets.py --limit 30       # ilk 30 (deneme)
"""
import sys, os, glob, shutil, time, datetime
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import pandas as pd, yfinance as yf
import logging; logging.getLogger("yfinance").setLevel(logging.CRITICAL)
from data_policy import AUTO_ADJUST, drop_adj_close

VER = "veriler"
BK  = os.path.join(VER, "_rebaseline_backups")
os.makedirs(BK, exist_ok=True)
OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def rebaseline_one(sym):
    fp = os.path.join(VER, f"{sym}_1d.parquet")
    d = yf.download(sym, period="1y", interval="1d", progress=False,
                    auto_adjust=AUTO_ADJUST, threads=False)
    d = drop_adj_close(d)
    if d is None or getattr(d, "empty", True):
        return "fail"
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    if "Close" not in d.columns:
        return "fail"
    d = d[[c for c in OHLCV if c in d.columns]].copy()
    if "Volume" not in d.columns:
        d["Volume"] = 0.0
    if d.index.tz is not None:
        d.index = d.index.tz_localize(None)
    d.index.name = "Date"
    # İsyatirim-düzeltilmiş hacmi koru: eski parquet'te >0 olan hacim yeniyi ezmesin
    if os.path.exists(fp):
        try:
            old = pd.read_parquet(fp)
            if "Volume" in old.columns:
                ov = old["Volume"].reindex(d.index)
                d["Volume"] = ov.where(ov.fillna(0) > 0, d["Volume"])
        except Exception:
            pass
        shutil.copy(fp, os.path.join(BK, f"{sym}.parquet"))
    d.to_parquet(fp)
    return "ok"


def main():
    argv = sys.argv[1:]
    limit = None
    if "--limit" in argv:
        i = argv.index("--limit")
        try:
            limit = int(argv[i + 1]); del argv[i:i + 2]
        except Exception:
            del argv[i]
    for a in list(argv):
        if a.startswith("--limit="):
            try:
                limit = int(a.split("=", 1)[1])
            except Exception:
                pass
            argv.remove(a)
    pos = [a for a in argv if not a.startswith("-")]

    if pos:
        syms = [a.upper() if a.upper().endswith(".IS") else f"{a.upper()}.IS" for a in pos]
    else:
        syms = sorted(os.path.basename(f).replace("_1d.parquet", "")
                      for f in glob.glob(os.path.join(VER, "*_1d.parquet"))
                      if os.path.basename(f).endswith(".IS_1d.parquet"))
    if limit:
        syms = syms[:limit]

    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] REBASELINE {len(syms)} hisse "
          f"(auto_adjust={AUTO_ADJUST}) — yedek: {BK}")
    ok = fail = 0
    fails = []
    for i, sym in enumerate(syms, 1):
        try:
            r = rebaseline_one(sym)
        except Exception:
            r = "fail"
        if r == "ok":
            ok += 1
        else:
            fail += 1; fails.append(sym)
        if i % 50 == 0:
            print(f"  {i}/{len(syms)} · ok={ok} fail={fail}")
        time.sleep(0.15)   # Yahoo'ya nazik ol (ban koruması)
    print(f"SONUÇ: ok={ok} · fail={fail}")
    if fails:
        print("BAŞARISIZ:", " ".join(fails[:40]) + (" ..." if len(fails) > 40 else ""))


if __name__ == "__main__":
    main()
