#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""isyatirim_saglik.py — İsyatirim çekişini SAĞLAM hale getiren sarmalayıcı (4 Ağu 2026).

İsyatirim telefonu sık meşgul çalar (bir restore denemesinde 6'da 1 geldi). Bu modül
dört basit koruma ekler — üretim fetcher'ına DOKUNMAZ, ayrı bir katmandır:

  1. TEKRAR DENE (backoff): birkaç kez dener, arasında kısa bekler.
  2. SİGORTA (circuit breaker): üst üste çok başarısızlık olursa "İsyatirim şu an küs"
     deyip N dakika hiç uğramaz (boşa saldırıp kilitlenmez). Cooldown bitince tekrar dener.
  3. SON İYİ CEVABI SAKLA: bugün gelmezse en son sağlam cevabı diskten döndürür (boş bırakmaz).
  4. SADECE İSTENENİ DOĞRULA: want_dates verilirse, o günleri içermeyen kısmi cevabı kabul etmez.

Kullanım:
    from isyatirim_saglik import robust_isyatirim
    df = robust_isyatirim("TTKOM.IS", period_days=20, want_dates=["2026-07-31"])
"""

from __future__ import annotations

import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pandas as pd

ROOT   = Path(__file__).parent
HEALTH = ROOT / "health"
CACHE  = ROOT / "health" / "isy_cache"
HEALTH.mkdir(exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)
BREAKER_FILE = HEALTH / "isy_breaker.json"

TR = timezone(timedelta(hours=3))

# --- Ayarlar (env ile ezilebilir) ---
TRIES          = int(os.environ.get("ISY_TRIES", "3"))       # kaç kez dene
SLEEP_BETWEEN  = float(os.environ.get("ISY_SLEEP", "2"))     # denemeler arası sn
FAIL_THRESHOLD = int(os.environ.get("ISY_FAIL_THRESHOLD", "8"))   # kaç üst üste fail → sigorta atar
COOLDOWN_MIN   = int(os.environ.get("ISY_COOLDOWN_MIN", "10"))    # sigorta atınca kaç dk küs


def _now() -> datetime:
    return datetime.now(TR)


def _load_breaker() -> dict:
    try:
        return json.loads(BREAKER_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"consecutive_fails": 0, "cooldown_until": None}


def _save_breaker(st: dict) -> None:
    try:
        BREAKER_FILE.write_text(json.dumps(st), encoding="utf-8")
    except Exception:
        pass


def _in_cooldown(st: dict) -> bool:
    cu = st.get("cooldown_until")
    if not cu:
        return False
    try:
        return _now() < datetime.fromisoformat(cu)
    except Exception:
        return False


def _cache_path(symbol: str) -> Path:
    return CACHE / f"{symbol.replace('.IS','')}.parquet"


def _read_cache(symbol: str):
    p = _cache_path(symbol)
    if p.exists():
        try:
            return pd.read_parquet(p)
        except Exception:
            return None
    return None


def _write_cache(symbol: str, df) -> None:
    try:
        df.to_parquet(_cache_path(symbol), compression="snappy")
    except Exception:
        pass


def _has_dates(df, want_dates) -> bool:
    if not want_dates:
        return True
    if df is None or df.empty:
        return False
    idx = set(df.index.astype(str).str.slice(0, 10))
    return all(str(d)[:10] in idx for d in want_dates)


def robust_isyatirim(symbol: str, period_days: int = 20, want_dates=None,
                     tries: int = TRIES, allow_stale: bool = True):
    """İsyatirim'den SAĞLAM çekiş. Başarısızsa: cooldown'a düşer + (izinliyse) son iyi cache'i döndürür.

    Dönüş: (df, kaynak)  → kaynak ∈ {"canli", "cache", "yok"}
    """
    # 5 Ağu 2026: bütün üretim çağrıları süreçler-arası ortak trafik kapısında.
    # Eski imza korunuyor; mevcut tüketiciler değişmeden merkezi bütçeye bağlanır.
    from isyatirim_gateway import robust_isyatirim as _gateway
    return _gateway(symbol, period_days=period_days, want_dates=want_dates,
                    tries=tries, allow_stale=allow_stale)

    from fetcher import fetch_isyatirim  # eski uygulama — geri dönüş referansı

    st = _load_breaker()

    # SİGORTA açıksa hiç ağa gitme
    if _in_cooldown(st):
        if allow_stale:
            c = _read_cache(symbol)
            if c is not None and _has_dates(c, want_dates):
                return c, "cache"
        return None, "yok"

    last_good = None
    for i in range(max(1, tries)):
        try:
            df = fetch_isyatirim(symbol, period_days=period_days)
        except Exception:
            df = None
        if df is not None and not df.empty:
            last_good = df
            if _has_dates(df, want_dates):
                # BAŞARI → sigortayı sıfırla, cache'i tazele
                _write_cache(symbol, df)
                _save_breaker({"consecutive_fails": 0, "cooldown_until": None})
                return df, "canli"
        if i < tries - 1:
            time.sleep(SLEEP_BETWEEN)

    # BURAYA DÜŞTÜYSE: istenen gün gelmedi → başarısızlık say
    st["consecutive_fails"] = st.get("consecutive_fails", 0) + 1
    if st["consecutive_fails"] >= FAIL_THRESHOLD:
        st["cooldown_until"] = (_now() + timedelta(minutes=COOLDOWN_MIN)).isoformat()
        st["consecutive_fails"] = 0  # cooldown sonrası temiz başla
    _save_breaker(st)

    # kısmi ama dolu cevabı yine de cache'le (want_dates yoksa zaten başarıydı)
    if last_good is not None and not want_dates:
        _write_cache(symbol, last_good)
        return last_good, "canli"

    if allow_stale:
        c = _read_cache(symbol)
        if c is not None and _has_dates(c, want_dates):
            return c, "cache"
    return None, "yok"


def breaker_status() -> str:
    from isyatirim_gateway import breaker_status as _gateway_status
    return _gateway_status()

    st = _load_breaker()
    if _in_cooldown(st):
        return f"SİGORTA AÇIK (cooldown: {st.get('cooldown_until')})"
    return f"normal (üst üste fail: {st.get('consecutive_fails', 0)})"


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "TTKOM.IS"
    df, kaynak = robust_isyatirim(sym, period_days=20)
    print(f"{sym}: kaynak={kaynak}, sigorta={breaker_status()}")
    if df is not None:
        print(df.tail(3)[["Close", "Volume"]].to_string())
