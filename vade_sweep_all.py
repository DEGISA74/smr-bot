"""
TÜM taramalar için ideal vade taraması (standalone).
Her scan_type için gün 1..28 getiri/isabet/PF/alpha → "tatlı nokta"yı bulur.

Tatlı nokta tanımı (aşırı-uyum tuzağına düşmemek için):
  - N >= MIN_N olan günler arasında
  - en yüksek ORTALAMA GETİRİ
  - ve alpha (piyasa farkı) > 0 olan günler tercih edilir (sinyalin kendi marifeti)
Ayrıca ham "en yüksek getiri günü" de gösterilir (kıyas + tuzak uyarısı için).
"""
import sys
try:  # Windows konsolu (cp1254) α gibi karakterlerde çökmesin
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import sqlite3
from pathlib import Path
import pandas as pd
import pytz

BASE = Path(__file__).parent
DB = BASE / "patron.db"
PQ = BASE / "veriler"
LOOKBACK = 90
MAXD = 28
MIN_N = 15          # bir günün güvenilir sayılması için min sinyal
MIN_TOTAL = 25      # taramanın değerlendirilmesi için min olgun sinyal

_pq_cache = {}


def load_pq(sym):
    if sym in _pq_cache:
        return _pq_cache[sym]
    res = None
    for sfx in (".IS_1d.parquet", "_1d.parquet"):
        p = PQ / f"{sym}{sfx}"
        if p.exists():
            try:
                df = pd.read_parquet(p)
                df.index = pd.to_datetime(df.index)
                res = df.sort_index()
            except Exception:
                res = None
            break
    _pq_cache[sym] = res
    return res


def main():
    conn = sqlite3.connect(DB)
    sigs = pd.read_sql(
        "SELECT * FROM scan_signals WHERE scan_date >= date('now', ?)",
        conn, params=(f"-{LOOKBACK} days",))
    conn.close()
    xu = load_pq("XU100.IS")

    rows = []
    for scan_type, grp in sigs.groupby("scan_type"):
        trades = []
        for _, sig in grp.iterrows():
            df = load_pq(sig["symbol"])
            if df is None or df.empty or "Close" not in df:
                continue
            sig_ts = pd.Timestamp(sig["scan_date"])
            idx = df.index.searchsorted(sig_ts)
            if idx >= len(df):
                continue
            entry = sig.get("entry_price")
            entry = float(entry) if entry and not pd.isna(entry) else float(df["Close"].iloc[idx])
            if entry <= 0:
                continue
            bias = str(sig.get("bias", "bullish") or "bullish").lower()
            sign = -1.0 if "bear" in bias else 1.0  # bearish → düşüş = kâr
            trades.append((df["Close"], idx, entry, sig_ts, sign))

        # en az 5g olgunlaşmış sinyal sayısı
        matured = sum(1 for c, i, *_ in trades if i + 5 < len(c))
        if matured < MIN_TOTAL:
            continue

        per_day = []
        for d in range(1, MAXD + 1):
            rets, alphas = [], []
            for close, idx, entry, sig_ts, sign in trades:
                f = idx + d
                if f >= len(close):
                    continue
                r = sign * (float(close.iloc[f]) - entry) / entry * 100
                rets.append(r)
                if xu is not None and not xu.empty:
                    xi = xu.index.searchsorted(sig_ts); xf = xi + d
                    if xi < len(xu) and xf < len(xu):
                        e0 = float(xu["Close"].iloc[xi]); e1 = float(xu["Close"].iloc[xf])
                        if e0 > 0:
                            alphas.append(r - sign * (e1 - e0) / e0 * 100)
            if len(rets) < MIN_N:
                continue
            s = pd.Series(rets)
            pos = s[s > 0]; neg = s[s <= 0]
            n = len(s)
            hit = len(pos) / n * 100
            avg = s.mean()
            pf = (pos.sum() / abs(neg.sum())) if abs(neg.sum()) > 0 else 99.0
            a = pd.Series(alphas).mean() if alphas else 0.0
            per_day.append({"d": d, "n": n, "hit": hit, "avg": avg, "pf": pf, "a": a})

        if not per_day:
            continue

        # İDEAL VADE = sinyalin kendi marifeti (alpha) zirve yaptığı gün
        sweet = max(per_day, key=lambda x: x["a"])
        # kıyas: ham en yüksek getiri günü (piyasa+sinyal birlikte, tuzak olabilir)
        raw = max(per_day, key=lambda x: x["avg"])
        has_edge = sweet["a"] > 0

        rows.append({
            "scan_type": scan_type, "matured": matured,
            "sweet_d": sweet["d"], "sweet_hit": sweet["hit"], "sweet_avg": sweet["avg"],
            "sweet_pf": sweet["pf"], "sweet_a": sweet["a"], "sweet_n": sweet["n"],
            "raw_d": raw["d"], "raw_avg": raw["avg"],
            "has_edge": has_edge,
        })

    # önce gerçek edge olanlar (α zirvesi > 0), alpha büyüklüğüne göre
    rows.sort(key=lambda r: (r["has_edge"], r["sweet_a"]), reverse=True)
    print(f"\n=== TÜM TARAMALAR — İdeal vade (α zirvesi = sinyalin kendi gücü) · {len(rows)} tarama ===\n")
    print(f"{'Tarama':<16} | {'Olgun':>5} | {'İdeal':>5} | {'İsabet':>7} | {'Getiri':>7} | {'PF':>5} | {'α(edge)':>7} | {'N':>3} | {'ham zirve':>9}")
    print("-" * 92)
    for r in rows:
        flag = "" if r["has_edge"] else "  ⚠ EDGE YOK (α hep ≤0)"
        print(f"{r['scan_type']:<16} | {r['matured']:>5} | {r['sweet_d']:>4}g | %{r['sweet_hit']:>5.1f} | "
              f"%{r['sweet_avg']:>+5.2f} | {r['sweet_pf']:>5.2f} | %{r['sweet_a']:>+5.1f} | {r['sweet_n']:>3} | "
              f"{r['raw_d']:>3}g %{r['raw_avg']:>+4.1f}{flag}")
    print("-" * 92)
    print("İdeal vade = α (piyasa farkı) en yüksek olduğu gün — sinyalin piyasanın ÜSTÜNE kattığı en büyük olduğu an.")
    print("⚠ EDGE YOK = hangi gün satsan da piyasayı geçemiyor (getiri sadece piyasa beta'sı).")


if __name__ == "__main__":
    main()
