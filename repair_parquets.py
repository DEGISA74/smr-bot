# -*- coding: utf-8 -*-
"""
repair_parquets.py — PARQUET DÜZELTME-BAZI ONARIMI (yeniden bazlama)
====================================================================
KÖK SORUN (2 Tem 2026): fetcher.py Yahoo'yu `auto_adjust=False` (HAM) yazıyor,
app.py `auto_adjust=True` (DÜZELTİLMİŞ) yazıyor → aynı parquet'e karışık bar →
temettü tarihlerinde süreksizlik → _apply_split_adjustments yanlış tetikleniyor →
Open/Close farklı ölçeğe kayıyor + hayalet sıçrama. ~108 hisse böyle bozulmuştu.

BU BETİK: bozuk imzalı (Open/Close ölçek sapması VEYA hayalet >%30 sıçrama, HERHANGİ
tarihte) hisseleri Yahoo'dan TAM TEMİZ (data_policy.AUTO_ADJUST, 1y) yeniden indirir =
yeniden bazlama. fresh de imzalıysa (gerçek kurumsal olay) DOKUNMAZ. Yedekler.
NOT (3 Tem 2026): politika artık TEK KAYNAK (data_policy.AUTO_ADJUST=False). Bu betik
sadece güvenlik ağı; birikmiş bozulmayı tamamen silmek için önce rebaseline_parquets.py.

Haftalık cron olarak koş (birikimi sıfırlar) VEYA heartbeat 'OPEN/CLOSE ÖLÇEK
UYUMSUZ' uyarısı verince elle. Çalıştır:  python repair_parquets.py
"""
import sys, io, os, glob, shutil, datetime
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import pandas as pd, yfinance as yf
import logging; logging.getLogger("yfinance").setLevel(logging.CRITICAL)
from data_policy import AUTO_ADJUST, drop_adj_close  # 3 Tem 2026 — TEK KAYNAK (fetcher/app ile aynı ham politika)

VER = "veriler"
BK  = os.path.join("veriler", "_repair_backups")
os.makedirs(BK, exist_ok=True)

def is_corrupt(df):
    """Düzeltme-bazı bozulması imzası (tüm geçmiş)."""
    if df is None or len(df) < 10:
        return False
    # 6 Tem 2026 — OHLC BÜTÜNLÜK: Open/Close, [Low,High] dışına çıkarsa HAM+DÜZELTİLMİŞ
    # karışması ("Frankenstein" bar — ham Open + düzeltilmiş HLC). Eski %40 oran eşiği
    # bu ~%7-10'luk temettü karışmasını KAÇIRIYORDU; bu kontrol tek bar bile yakalar.
    if {"Open", "High", "Low", "Close"}.issubset(df.columns):
        _o, _h, _l, _c = df["Open"], df["High"], df["Low"], df["Close"]
        if int(((_o > _h * 1.001) | (_o < _l * 0.999) |
                (_c > _h * 1.001) | (_c < _l * 0.999)).sum()) >= 1:
            return True
    r = (df["Open"] / df["Close"]).replace([float("inf")], 0).dropna()
    if ((r < 0.7) | (r > 1.4)).sum() >= 3:       # Open, Close'dan farklı bazda
        return True
    j = df["Close"].pct_change(fill_method=None).abs()
    if (j > 0.30).sum() >= 1:                     # BIST limiti %10 → >%30 = yapay
        return True
    return False

def main():
    corrupt = []
    for f in glob.glob(os.path.join(VER, "*_1d.parquet")):
        sym = os.path.basename(f).replace("_1d.parquet", "")
        if not sym.endswith(".IS"):               # futures/endeks/özel hariç
            continue
        try:
            if is_corrupt(pd.read_parquet(f)):
                corrupt.append(sym)
        except Exception:
            pass
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] bozuk imzalı: {len(corrupt)}")
    ok = skip = fail = 0
    for sym in sorted(corrupt):
        fp = os.path.join(VER, f"{sym}_1d.parquet")
        try:
            d = yf.download(sym, period="1y", interval="1d", progress=False,
                            auto_adjust=AUTO_ADJUST, threads=False)
            d = drop_adj_close(d)
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.get_level_values(0)
            if d is None or d.empty or "Close" not in d.columns:
                fail += 1; continue
            d = d[["Open", "High", "Low", "Close", "Volume"]].copy()
            if d.index.tz is not None:
                d.index = d.index.tz_localize(None)
            d.index.name = "Date"
            if is_corrupt(d):                     # fresh de imzalı → gerçek olay, dokunma
                skip += 1; continue
            shutil.copy(fp, os.path.join(BK, f"{sym}.parquet"))
            d.to_parquet(fp)
            ok += 1
        except Exception:
            fail += 1
    print(f"SONUÇ: onarıldı={ok} · atlandı(gerçek olay)={skip} · başarısız={fail}")

if __name__ == "__main__":
    main()
