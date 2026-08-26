"""
ER B8 — EMA8 yürüyen stop deneyi (standalone, app.py'ye dokunmaz)

Soru: "10 günde %60.9 ile 46 hisseye eşit girsem ne olurdu" tablosu hiç STOP
kullanmıyor (10 gün körlemesine tut). Peki kaybedenleri EMA8'in altına sarkınca
keseydik (yürüyen stop) ne değişirdi?

Yöntem:
  - backtest_runner ile AYNI sinyal seti (er_B8, son 90g, ≥10g olgunlaşmış)
  - AYNI parquet verisi, AYNI entry mantığı
  - Önce baseline'ı yeniden üret (tabloyu doğrula) → sonra stop uygula
"""
import sys
try:  # Windows konsolu (cp1254) ① / α gibi karakterlerde çökmesin
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import sqlite3
from pathlib import Path
from datetime import datetime
import pandas as pd
import pytz

BASE = Path(__file__).parent
DB = BASE / "patron.db"
PQ = BASE / "veriler"
TZ = pytz.timezone("Europe/Istanbul")
SCAN = "er_B8"
LOOKBACK = 90


def load_pq(sym):
    for sfx in (".IS_1d.parquet", "_1d.parquet"):
        p = PQ / f"{sym}{sfx}"
        if p.exists():
            try:
                df = pd.read_parquet(p)
                df.index = pd.to_datetime(df.index)
                return df.sort_index()
            except Exception:
                return None
    return None


def get_signals():
    conn = sqlite3.connect(DB)
    df = pd.read_sql(
        "SELECT * FROM scan_signals WHERE scan_type=? AND scan_date >= date('now', ?)",
        conn, params=(SCAN, f"-{LOOKBACK} days"))
    conn.close()
    return df


def summarize(rets, alphas, label):
    rets = pd.Series(rets, dtype=float)
    alphas = pd.Series(alphas, dtype=float)
    n = len(rets)
    if n == 0:
        print(f"  {label}: veri yok")
        return
    pos = rets[rets > 0]
    neg = rets[rets <= 0]
    hit = len(pos) / n * 100
    avg = rets.mean()
    aw = pos.mean() if len(pos) else 0.0
    al = neg.mean() if len(neg) else 0.0
    gp = pos.sum()
    gl = abs(neg.sum())
    pf = (gp / gl) if gl > 0 else float("inf")
    alpha = alphas.mean() if len(alphas) else float("nan")
    print(f"  {label}")
    print(f"     N sinyal        : {n}")
    print(f"     İsabet (hit%)   : %{hit:.1f}")
    print(f"     Ort. getiri     : %{avg:+.2f}")
    print(f"     Ort. kazanç     : %{aw:+.2f}")
    print(f"     Ort. kayıp      : %{al:+.2f}")
    print(f"     Profit Factor   : {pf:.2f}")
    print(f"     Piyasa farkı(α) : %{alpha:+.2f}")
    print()


def main():
    today = datetime.now(TZ).date()
    sigs = get_signals()
    xu = load_pq("XU100.IS")

    base_rets, base_alpha = [], []          # 10g sabit, stop yok (tabloyu üret)
    ema8_cap_rets, ema8_cap_alpha = [], []  # EMA8 stop, max 10g kapak
    ema8_free_rets, ema8_free_alpha = [], []# EMA8 stop, serbest (max 40g)
    ema5_free_rets, ema5_free_alpha = [], []# EMA5 stop (daha sıkı), serbest
    ema13_cap_rets, ema13_cap_alpha = [], []  # EMA13 stop, max 10g kapak
    ema13_free_rets, ema13_free_alpha = [], []# EMA13 stop, serbest (max 40g)

    used = 0
    for _, sig in sigs.iterrows():
        sym = sig["symbol"]
        df = load_pq(sym)
        if df is None or df.empty or "Close" not in df:
            continue
        sig_ts = pd.Timestamp(sig["scan_date"])
        idx = df.index.searchsorted(sig_ts)
        if idx >= len(df):
            continue
        # 10g olgunlaşma şartı (tabloyla aynı set)
        if idx + 10 >= len(df):
            continue
        entry = sig.get("entry_price")
        entry = float(entry) if entry and not pd.isna(entry) else float(df["Close"].iloc[idx])
        if entry <= 0:
            continue

        close = df["Close"]
        ema8 = close.ewm(span=8, adjust=False).mean()
        ema5 = close.ewm(span=5, adjust=False).mean()
        ema13 = close.ewm(span=13, adjust=False).mean()
        used += 1

        def xu_ret(exit_idx):
            if xu is None or xu.empty:
                return None
            xi = xu.index.searchsorted(sig_ts)
            xf = xi + (exit_idx - idx)
            if xi >= len(xu) or xf >= len(xu):
                return None
            e0 = float(xu["Close"].iloc[xi]); e1 = float(xu["Close"].iloc[xf])
            return (e1 - e0) / e0 * 100 if e0 > 0 else None

        # ---- baseline: 10g sabit kapanış, stop yok ----
        ex = idx + 10
        r = (float(close.iloc[ex]) - entry) / entry * 100
        base_rets.append(r)
        xr = xu_ret(ex)
        if xr is not None:
            base_alpha.append(r - xr)

        # ---- EMA tabanlı yürüyen stop: kapanış EMA altına inince çık ----
        def run_stop(ema, maxhold):
            exit_idx = idx + maxhold
            for d in range(idx + 1, min(idx + maxhold, len(df) - 1) + 1):
                if float(close.iloc[d]) < float(ema.iloc[d]):
                    exit_idx = d
                    break
            exit_idx = min(exit_idx, len(df) - 1)
            rr = (float(close.iloc[exit_idx]) - entry) / entry * 100
            xrr = xu_ret(exit_idx)
            return rr, (rr - xrr if xrr is not None else None)

        r1, a1 = run_stop(ema8, 10)
        ema8_cap_rets.append(r1)
        if a1 is not None:
            ema8_cap_alpha.append(a1)

        r2, a2 = run_stop(ema8, 40)
        ema8_free_rets.append(r2)
        if a2 is not None:
            ema8_free_alpha.append(a2)

        r3, a3 = run_stop(ema5, 40)
        ema5_free_rets.append(r3)
        if a3 is not None:
            ema5_free_alpha.append(a3)

        r4, a4 = run_stop(ema13, 10)
        ema13_cap_rets.append(r4)
        if a4 is not None:
            ema13_cap_alpha.append(a4)

        r5, a5 = run_stop(ema13, 40)
        ema13_free_rets.append(r5)
        if a5 is not None:
            ema13_free_alpha.append(a5)

    print(f"\n=== ER B8 — Sıkışma Sonu · kullanılan sinyal: {used} ===\n")
    summarize(base_rets, base_alpha, "① BASELINE (10g sabit, stop YOK) — tabloyu doğrula")
    summarize(ema8_cap_rets, ema8_cap_alpha, "② EMA8 yürüyen stop, max 10g kapak (elma-elmaya)")
    summarize(ema8_free_rets, ema8_free_alpha, "③ EMA8 yürüyen stop, SERBEST (kazanan koşsun, max 40g)")
    summarize(ema5_free_rets, ema5_free_alpha, "④ EMA5 yürüyen stop, SERBEST (daha sıkı)")
    summarize(ema13_cap_rets, ema13_cap_alpha, "⑤ EMA13 yürüyen stop, max 10g kapak (elma-elmaya)")
    summarize(ema13_free_rets, ema13_free_alpha, "⑥ EMA13 yürüyen stop, SERBEST (kazanan koşsun, max 40g)")


if __name__ == "__main__":
    main()
