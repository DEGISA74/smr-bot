# -*- coding: utf-8 -*-
"""
kurumsal_takvim.py — TEMETTÜ/BÖLÜNME TAKVİMİ (İsyatirim÷Yahoo oran yöntemi)
==========================================================================
İş #8 (borsacı geri bildirimi rec #8, 28 Tem 2026) — "kurumsal işlem günlerinde
canlı sinyali koru, sahte satış alarmı verme".

TEZ (memory/project-kurumsal-takvim-oran-tezi, 187 hissede doğrulandı):
  İsyatirim fiyatı DÜZELTİLMİŞ, Yahoo (auto_adjust=False) HAM. Oran = İsyatirim ÷
  Yahoo = o güne kadarki TÜM kurumsal işlemlerin kümülatif çarpanı (temettü DAHİL).
  Oran normalde TAŞ GİBİ SABİT (olay-dışı oynaklık ~%0.0007); bir temettü/bölünme
  günü BASAMAK yapar. Basamağın yeri = olay tarihi, boyu = çarpan.

DEDEKTÖR (tezgâhta doğrulandı — GARAN 2026-04-07 ×1.0404 bilinen değerle birebir,
temettü ödemeyende 0 sahte pozitif, dedup çalışıyor):
  1) log-oran serisi · 2) her gün önceki-K vs sonraki-K medyan farkı > eşik → aday
  3) bitişik adaylar TEK olaya kümelenir (dedup) · 4) KALICI BASAMAK şartı: yeni
  seviye ≥PERSIST gün tutmalı → tek/çift-gün veri kazaları elenir.

🚨 KIRMIZI ÇİZGİ: İsyatirim fiyatı SADECE oran hesabı için okunur — OHLC'ye,
parquet'e, panele, sinyale ASLA girmez (6 Tem Frankenstein bug'ı geri gelmesin).
Bu modül fiyat DÖNDÜRMEZ; sadece OLAY LİSTESİ (tarih, çarpan, tip) döndürür.

Bağımsız modül (streamlit YOK) — backtest_runner + cron kullanabilir.
Kullanım:
  from kurumsal_takvim import detect_corporate_actions, corporate_action_in_window
  events = detect_corporate_actions("GARAN")           # [{date, factor, type}]
  hit = corporate_action_in_window("GARAN", d0, d1)    # o pencerede olay var mı
"""
import json
import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# ── Dedektör parametreleri (tezgâhta kalibre edildi) ──
_K = 5              # basamak penceresi (önceki/sonraki K gün medyanı)
_THR = 0.008        # log-oran eşiği (~%0.8): temettü ~%1.9+ yakalanır, gürültü ~%0.07 elenir
_PERSIST = 4        # olay sonrası yeni seviye ≥ bu kadar gün korunmalı (tek-gün kaza reddi)
_SPLIT_MIN = 0.15   # |çarpan-1| bunun üstü → "bölünme/büyük", altı → "temettü"

_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "veriler", "kurumsal_takvim_cache.json")
_CACHE_TTL_HOURS = 24


# ---------------------------------------------------------------------------
# VERİ — İsyatirim (düzeltilmiş) ÷ Yahoo (ham). SADECE oran için.
# ---------------------------------------------------------------------------
def _fetch_ratio(symbol, start, end):
    """İsyatirim(düzeltilmiş) ÷ Yahoo(ham) günlük kapanış oran Serisi. Fiyat DÖNMEZ."""
    sym = symbol.replace(".IS", "").replace(".is", "").upper()
    # Yahoo HAM (auto_adjust=False → 'Close' düzeltilmemiş)
    try:
        from provider_traffic import acquire_slot, record_success, record_failure
        acquire_slot("yahoo", priority="repair", max_wait=60)
        import yfinance as yf
        y = yf.download(sym + ".IS", start=start, end=end, auto_adjust=False,
                        progress=False, threads=False)
        if y is None or y.empty:
            record_failure("yahoo", kind="empty", error=f"{sym} corporate ratio empty")
            return None
        record_success("yahoo")
        yc = y["Close"]
        if isinstance(yc, pd.DataFrame):
            yc = yc.iloc[:, 0]
        yc.index = pd.to_datetime(yc.index).tz_localize(None)
    except Exception as _yf_exc:
        try:
            record_failure("yahoo", kind="error", error=str(_yf_exc))
        except Exception:
            pass
        return None
    # İsyatirim DÜZELTİLMİŞ — app ile AYNI fetch (tek kaynak, kırmızı-çizgi korunur)
    try:
        from data_layer import _fetch_bist_ohlcv_isyatirim
        isy = _fetch_bist_ohlcv_isyatirim(sym, start, end)
    except Exception:
        return None
    if isy is None or isy.empty:
        return None
    ic = isy["Close"]
    ic.index = pd.to_datetime(ic.index).tz_localize(None)
    df = pd.DataFrame({"y": yc, "i": ic}).dropna()
    df = df[(df["y"] > 0) & (df["i"] > 0)]
    if len(df) < 3 * _K:
        return None
    return df["i"] / df["y"]


# ---------------------------------------------------------------------------
# DEDEKTÖR — kalıcı basamak + dedup
# ---------------------------------------------------------------------------
def _detect_from_ratio(r):
    """r: oran Serisi (DatetimeIndex). Döner: [(Timestamp, factor)]."""
    logr = np.log(r.values)
    n = len(logr)
    cands = []
    for t in range(_K, n - _K):
        d = np.median(logr[t:t + _K]) - np.median(logr[t - _K:t])
        if abs(d) > _THR:
            cands.append((t, d))
    events = []
    i = 0
    while i < len(cands):
        j = i
        while j + 1 < len(cands) and cands[j + 1][0] - cands[j][0] <= _K:
            j += 1
        t_best, d_best = max(cands[i:j + 1], key=lambda x: abs(x[1]))
        # KALICI BASAMAK ŞARTI — tek/çift-gün veri kazası reddi
        lvl_after = np.median(logr[t_best:min(n, t_best + _K)])
        lvl_persist = np.median(logr[min(n - 1, t_best + _PERSIST):min(n, t_best + _PERSIST + _K)])
        if abs(lvl_persist - lvl_after) <= _THR:
            events.append((r.index[t_best], float(np.exp(d_best))))
        i = j + 1
    return events


def _classify(factor):
    return "bolunme" if abs(factor - 1.0) > _SPLIT_MIN else "temettu"


# ---------------------------------------------------------------------------
# CACHE — günde 1 hesap yeter (kurumsal olaylar seyrek)
# ---------------------------------------------------------------------------
def _load_cache():
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache):
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# GENEL API
# ---------------------------------------------------------------------------
def _compute_events(symbol, start, end):
    """Ham hesap. Döner: olay listesi VEYA None (fetch başarısız — olay YOK'tan farklı!).
    Bu ayrım precompute için kritik: fetch patlarsa eski cache korunmalı, [] yazılmamalı."""
    r = _fetch_ratio(symbol, start, end)
    if r is None:
        return None
    return [{"date": dt.strftime("%Y-%m-%d"), "factor": round(f, 4), "type": _classify(f)}
            for dt, f in _detect_from_ratio(r)]


def detect_corporate_actions(symbol, start=None, end=None, use_cache=True):
    """symbol için kurumsal işlem olaylarını döndür (oran yöntemi, ON-DEMAND — fetch eder).
    Returns: list[dict] {date, factor, type}. Olay yoksa []. Veri alınamazsa [] (sessiz).
    ⚠ Fiyat DÖNMEZ; İsyatirim yalnız oran için okundu (kırmızı çizgi).
    NOT: üretim tüketicileri get_cached_events() kullanmalı (fetch YOK); bu fonksiyon
    manuel/test + precompute içindir."""
    sym = symbol.replace(".IS", "").replace(".is", "").upper()
    if end is None:
        end = datetime.today().strftime("%Y-%m-%d")
    if start is None:
        start = (datetime.today() - timedelta(days=550)).strftime("%Y-%m-%d")

    if use_cache:
        rec = _load_cache().get(sym)
        if rec:
            try:
                age_h = (datetime.now() - datetime.fromisoformat(rec["ts"])).total_seconds() / 3600
                if age_h < _CACHE_TTL_HOURS:
                    return rec["events"]
            except Exception:
                pass

    events = _compute_events(sym, start, end)
    if events is None:               # fetch başarısız → eski cache'i koru, [] yazma
        rec = _load_cache().get(sym)
        return rec["events"] if rec else []

    if use_cache:
        cache = _load_cache()
        cache[sym] = {"ts": datetime.now().isoformat(), "events": events}
        _save_cache(cache)
    return events


def load_all_cached():
    """TÜM cache'i {SEMBOL: [events]} olarak TEK okumada döndür. Backtest gibi çok
    sinyalli tüketiciler döngü BAŞINDA 1 kez çağırır → per-sinyal dosya okuma olmaz.
    FETCH YOK — sadece cache. Fiyat DÖNMEZ."""
    out = {}
    for sym, rec in _load_cache().items():
        try:
            out[sym] = rec.get("events", [])
        except Exception:
            out[sym] = []
    return out


def get_cached_events(symbol):
    """ÜRETİM TÜKETİCİSİ İÇİN — SADECE cache'ten okur, FETCH YOK, TTL bakmaz.
    precompute_universe cache'i taze tutar; backtest/canlı buradan okur → hızlı,
    rate-limit yok. Cache'te yoksa [] (henüz bilinmiyor = güvenli varsayılan: olay yok).
    Döner: list[dict] {date, factor, type}."""
    sym = symbol.replace(".IS", "").replace(".is", "").upper()
    rec = _load_cache().get(sym)
    return rec["events"] if rec else []


def corporate_action_in_window(symbol, win_start, win_end, cache_only=True):
    """[win_start, win_end] (dahil) aralığında kurumsal işlem var mı? Döner: dict|None.
    Sinyal koruması için: dict dönerse o pencerenin fiyat boşluğu MEKANİK — 'satış' sayma.
    cache_only=True (üretim): fetch YOK, cache'ten okur. False: gerekirse fetch eder."""
    def _d(x):
        if isinstance(x, str):
            return datetime.strptime(x[:10], "%Y-%m-%d").date()
        return pd.Timestamp(x).date()
    a, b = _d(win_start), _d(win_end)
    evs = get_cached_events(symbol) if cache_only else detect_corporate_actions(symbol)
    for ev in evs:
        if a <= _d(ev["date"]) <= b:
            return ev
    return None


def precompute_universe(symbols, start=None, end=None, sleep_s=1.6, max_retry=2,
                        stale_days=5, log=print):
    """GECE İŞİ — tüm evrenin kurumsal takvimini pace'li hesaplayıp cache'e yazar.
    Tüketiciler get_cached_events ile buradan okur (fetch yok). Dayanıklı:
    - taze cache (<stale_days) olan sembolü ATLAR (yükü geceye yay).
    - fetch timeout/boş → RETRY; hâlâ olmazsa eski cache'i KORUR ([] yazmaz).
    - İsyatirim rate-limit'i için semboller arası sleep_s bekler.
    Returns: dict özet {toplam, hesaplanan, atlanan_taze, basarisiz}."""
    if end is None:
        end = datetime.today().strftime("%Y-%m-%d")
    if start is None:
        start = (datetime.today() - timedelta(days=550)).strftime("%Y-%m-%d")
    import time
    syms = [s.replace(".IS", "").replace(".is", "").upper() for s in symbols]
    syms = sorted(set(syms))
    n = len(syms)
    hesaplanan = atlanan = basarisiz = 0
    for i, sym in enumerate(syms):
        cache = _load_cache()
        rec = cache.get(sym)
        if rec:
            try:
                age_d = (datetime.now() - datetime.fromisoformat(rec["ts"])).total_seconds() / 86400
                if age_d < stale_days:
                    atlanan += 1
                    continue
            except Exception:
                pass
        events = None
        for _try in range(max_retry):
            events = _compute_events(sym, start, end)
            if events is not None:
                break
            time.sleep(sleep_s)
        if events is None:
            basarisiz += 1
            log(f"  [{i+1}/{n}] {sym:8} FETCH BAŞARISIZ — eski cache korundu")
        else:
            cache = _load_cache()
            cache[sym] = {"ts": datetime.now().isoformat(), "events": events}
            _save_cache(cache)
            hesaplanan += 1
            log(f"  [{i+1}/{n}] {sym:8} {len(events)} olay")
        time.sleep(sleep_s)
    ozet = {"toplam": n, "hesaplanan": hesaplanan, "atlanan_taze": atlanan, "basarisiz": basarisiz}
    log(f"PRECOMPUTE bitti: {ozet}")
    return ozet


if __name__ == "__main__":
    import sys, argparse
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--precompute", action="store_true",
                    help="GECE İŞİ: tüm BIST evreninin takvimini cache'e yaz (pace'li)")
    ap.add_argument("--limit", type=int, default=0, help="precompute'ta ilk N hisse (test)")
    ap.add_argument("--stale-days", type=int, default=5)
    ap.add_argument("syms", nargs="*", help="tek tek sembol sorgusu (on-demand)")
    a = ap.parse_args()

    if a.precompute:
        try:
            from data_layer import raw_bist_stocks as _univ
        except Exception:
            _univ = a.syms
        _univ = [s for s in _univ if not str(s).upper().startswith(("XU", "XB", "XT", "XY", "^"))]
        if a.limit:
            _univ = _univ[:a.limit]
        precompute_universe(_univ, stale_days=a.stale_days)
    else:
        for s in (a.syms or ["GARAN", "AKBNK", "ASELS"]):
            ev = detect_corporate_actions(s, use_cache=False)
            print(f"{s:8} {len(ev)} olay: " +
                  (" · ".join(f"{e['date']} ×{e['factor']} [{e['type']}]" for e in ev) or "(yok)"))
