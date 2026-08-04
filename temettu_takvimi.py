#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""temettu_takvimi.py — Temettü günü etiketi (4 Ağu 2026).

Sistem HAM fiyat kullanır (AUTO_ADJUST=False) — yani temettü dağıtılınca fiyat gerçekten
düşer (aracıda da düşer). Bu bilinçli bir tercih: seviyeler (destek/direnç) aracıyla tutsun.
AMA sistem bu düşüşü "temettü kaynaklı" diye İŞARETLEMİYOR → panelde/AI'da yanlışlıkla
"satış baskısı" gibi okunabiliyor. Bu modül o etiketi sağlar.

Kaynak: Yahoo'nun temettü kayıtları (`yf.Ticker(sym).dividends`) — temettü ödeme (ex-div)
tarihleri + tutarları. Haftalık cache'lenir (her render'da Yahoo'ya gidilmez).

Kullanım:
    from temettu_takvimi import son_temettu_notu, is_temettu_gunu
    not_ = son_temettu_notu("TTKOM.IS", df)   # son ~7 barda temettü varsa açıklama, yoksa None
    if is_temettu_gunu("TTKOM.IS", "2026-08-04"): ...
"""

from __future__ import annotations

import os
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pandas as pd

ROOT  = Path(__file__).parent
CACHE = ROOT / "health" / "temettu"
CACHE.mkdir(parents=True, exist_ok=True)
TR = timezone(timedelta(hours=3))

CACHE_GUN = int(os.environ.get("TEMETTU_CACHE_GUN", "7"))   # cache kaç günde bir tazelenir


def _cache_file(ticker: str) -> Path:
    return CACHE / f"{ticker.replace('.IS','')}.json"


def _cache_taze(p: Path) -> bool:
    if not p.exists():
        return False
    try:
        yas = datetime.now(TR) - datetime.fromtimestamp(p.stat().st_mtime, TR)
        return yas < timedelta(days=CACHE_GUN)
    except Exception:
        return False


def get_temettuler(ticker: str, refresh: bool = False) -> dict:
    """Ticker'ın temettü tarihlerini döndürür: {'YYYY-MM-DD': tutar_float}. Haftalık cache."""
    p = _cache_file(ticker)
    if not refresh and _cache_taze(p):
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("divs", {})
        except Exception:
            pass
    divs = {}
    try:
        import yfinance as yf
        sym = ticker if ticker.endswith(".IS") or ("." in ticker) or ticker.startswith("^") else f"{ticker}.IS"
        s = yf.Ticker(sym).dividends
        if s is not None and len(s):
            for dt, amt in s.items():
                try:
                    divs[str(pd.Timestamp(dt).date())] = round(float(amt), 6)
                except Exception:
                    continue
    except Exception:
        pass
    # cache'e yaz (boş olsa bile — her render'da Yahoo'ya gitmemek için)
    try:
        p.write_text(json.dumps({"fetched": datetime.now(TR).isoformat(), "divs": divs}),
                     encoding="utf-8")
    except Exception:
        pass
    return divs


def is_temettu_gunu(ticker: str, gun) -> bool:
    """gun (YYYY-MM-DD veya tarih) o ticker için temettü (ex-div) günü mü?"""
    g = str(gun)[:10]
    return g in get_temettuler(ticker)


def son_temettu_notu(ticker: str, df=None, gun_sayisi: int = 7) -> str | None:
    """Son `gun_sayisi` işlem barının içinde temettü günü varsa açıklama döndürür, yoksa None.

    df verilirse onun son barlarının tarihlerine bakar (en doğru). df yoksa takvim
    gününe göre son gun_sayisi günü tarar.
    """
    divs = get_temettuler(ticker)
    if not divs:
        return None

    if df is not None and not getattr(df, "empty", True):
        son_tarihler = [str(d)[:10] for d in df.index[-gun_sayisi:]]
    else:
        bugun = datetime.now(TR).date()
        son_tarihler = [(bugun - timedelta(days=i)).isoformat() for i in range(gun_sayisi)]

    vurus = [(g, divs[g]) for g in son_tarihler if g in divs]
    if not vurus:
        return None
    g, tutar = vurus[-1]  # en yakın temettü

    # Etkiyi yüzde olarak da ver (df varsa o günün kapanışına göre)
    yuzde = None
    if df is not None:
        try:
            kapanis = float(df.loc[df.index.astype(str).str.slice(0, 10) == g, "Close"].iloc[-1])
            if kapanis > 0:
                yuzde = round(tutar / (kapanis + tutar) * 100, 1)
        except Exception:
            pass

    if yuzde is not None:
        return (f"💰 {g} temettü günü: {tutar:.4f} TL/pay — bu tarihteki düşüşün ~%{yuzde}'i "
                f"temettü kaynaklı (satış sinyali DEĞİL, fiyat ham gösteriliyor).")
    return (f"💰 {g} temettü günü: {tutar:.4f} TL/pay — o günkü düşüşün bir kısmı temettü "
            f"kaynaklı (satış sinyali DEĞİL).")


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "TTKOM.IS"
    divs = get_temettuler(sym, refresh=True)
    print(f"{sym}: {len(divs)} temettü kaydı")
    for g in sorted(divs)[-5:]:
        print(f"  {g}: {divs[g]:.4f} TL")
    # sentetik test: son barlarda temettü varmış gibi
    try:
        from data_layer import get_safe_historical_data
        df = get_safe_historical_data(sym, period="1y")
        print("son_temettu_notu:", son_temettu_notu(sym, df) or "(son 7 barda temettü yok)")
    except Exception as e:
        print("df testi atlandı:", e)
