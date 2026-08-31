# -*- coding: utf-8 -*-
"""Magic Ribbon BIST seans-mumu kasasını kapanıştan sonra yeniler.

Yahoo kullanmaz. TradingView/borsapy üzerinden yalnız 5 dakikalık fiyat verisini
tek tek, düşük tempoyla alır; ham veri ve üretilmiş seans mumları ayrı kasalara
yazılır. VPS'e aktarımı bu scriptin değil ``run_magic_ribbon.sh`` sarmalayıcısının
görevidir.
"""
from __future__ import annotations

import argparse
import sys

from magic_ribbon_core import load_bist100_symbols
from magic_ribbon_session_data import refresh_symbols


def main() -> int:
    parser = argparse.ArgumentParser(description="Magic Ribbon BIST seans-mumu yenileyici")
    parser.add_argument("--bootstrap", action="store_true", help="Her sembol için son yaklaşık 55 seansı yeniden kur")
    parser.add_argument("--limit", type=int, default=0, help="Deneme için ilk N BIST100 sembolü")
    parser.add_argument("--symbol", action="append", default=[], help="Yalnız belirtilen sembol; tekrar kullanılabilir")
    args = parser.parse_args()

    symbols = sorted({str(value).upper().replace(".IS", "") for value in args.symbol if str(value).strip()})
    if not symbols:
        symbols = sorted(load_bist100_symbols())
    if args.limit > 0:
        symbols = symbols[:args.limit]
    if not symbols:
        print("Magic Ribbon yenileme durdu: BIST100 listesi boş.")
        return 2

    outcome = refresh_symbols(symbols, bootstrap=bool(args.bootstrap))
    ok_rows = outcome["ok"]
    failures = outcome["failed"]
    print(f"Magic Ribbon seans verisi: {len(ok_rows)}/{outcome['total']} sembol yenilendi.")
    if ok_rows:
        latest = max(str(row.get("last_session") or "") for row in ok_rows)
        print(f"Son tam seans mumu: {latest}")
    if failures:
        for item in failures:
            print(f"HATA {item['symbol']}: {item['reason']}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    raise SystemExit(main())
