# -*- coding: utf-8 -*-
"""Magic Ribbon BIST seans-mumu kasasını kapanıştan sonra yeniler.

Yahoo kullanmaz. TradingView/borsapy üzerinden yalnız 5 dakikalık fiyat verisini
tek tek, düşük tempoyla alır; ham veri ve üretilmiş seans mumları ayrı kasalara
yazılır. VPS'e aktarımı bu scriptin değil ``run_magic_ribbon.sh`` sarmalayıcısının
görevidir.

1 Eyl 2026 — KISMİ BAŞARI KURALI. Eskiden 100 sembolden biri bile düşse script
"hatalı bitti" diyor, sarmalayıcı da VPS'e hiçbir şey göndermiyordu: lokal kasa
ilerlerken sunucudaki sessizce geride kalıyordu. Artık ölçüt tek sembol değil
BAŞARI ORANI. Eşiğin üstünde kalan tur normal sayılır ve aktarım sürer; düşen
semboller eski mumlarını korur, üç günden fazla yenilenemezlerse
``get_magic_ribbon_session_data`` bayatlık kapısı onları taramadan zaten düşürür.
Eşiğin altı ise sistemik arıza (akış kapalı, ban, ağ) demektir — o turda VPS'e
dokunulmaz, sunucu son sağlam kopyasıyla kalır.
"""
from __future__ import annotations

import argparse
import sys

from magic_ribbon_core import load_bist100_symbols
from magic_ribbon_session_data import refresh_symbols

# Turun "sağlıklı" sayılması için yenilenmesi gereken en düşük sembol oranı.
DEFAULT_MIN_SUCCESS_RATIO = 0.90


def main() -> int:
    parser = argparse.ArgumentParser(description="Magic Ribbon BIST seans-mumu yenileyici")
    parser.add_argument("--bootstrap", action="store_true", help="Her sembol için son yaklaşık 55 seansı yeniden kur")
    parser.add_argument("--limit", type=int, default=0, help="Deneme için ilk N BIST100 sembolü")
    parser.add_argument("--symbol", action="append", default=[], help="Yalnız belirtilen sembol; tekrar kullanılabilir")
    parser.add_argument(
        "--min-oran", type=float, default=DEFAULT_MIN_SUCCESS_RATIO,
        help="Turun sağlıklı sayılması için gereken en düşük başarı oranı (0-1). Altında VPS'e gönderilmez.",
    )
    args = parser.parse_args()

    symbols = sorted({str(value).upper().replace(".IS", "") for value in args.symbol if str(value).strip()})
    if not symbols:
        symbols = sorted(load_bist100_symbols())
    if args.limit > 0:
        symbols = symbols[:args.limit]
    if not symbols:
        print("Magic Ribbon yenileme durdu: BIST100 listesi boş.")
        return 2

    esik = min(max(float(args.min_oran), 0.0), 1.0)
    outcome = refresh_symbols(symbols, bootstrap=bool(args.bootstrap))
    ok_rows = outcome["ok"]
    failures = outcome["failed"]
    toplam = int(outcome["total"]) or 1
    oran = len(ok_rows) / toplam

    print(f"Magic Ribbon seans verisi: {len(ok_rows)}/{outcome['total']} sembol yenilendi (%{oran * 100:.1f}).")
    if ok_rows:
        latest = max(str(row.get("last_session") or "") for row in ok_rows)
        print(f"Son tam seans mumu: {latest}")
    for item in failures:
        print(f"ATLANDI {item['symbol']}: {item['reason']}")

    if oran + 1e-9 < esik:
        print(
            f"Başarı oranı eşiğin altında (gereken %{esik * 100:.0f}) — sistemik arıza sayıldı, "
            "kasa VPS'e gönderilmeyecek."
        )
        return 1
    if failures:
        print(
            f"{len(failures)} sembol bu turda yenilenemedi; eski mumları duruyor ve bayatlarsa "
            "tarama onları kendiliğinden düşürür. Tur sağlıklı sayıldı, aktarım sürecek."
        )
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    raise SystemExit(main())
