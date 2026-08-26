"""
ER B8 — İdeal vade taraması (standalone). Her gün (1..30) için getiri/isabet/PF.
Amaç: "5-10-20 mi, yoksa ideal vade 12 mi?" sorusunu eğriyle göster.
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
SCAN = "er_B8"
LOOKBACK = 90
MAXD = 30


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


def main():
    conn = sqlite3.connect(DB)
    sigs = pd.read_sql(
        "SELECT * FROM scan_signals WHERE scan_type=? AND scan_date >= date('now', ?)",
        conn, params=(SCAN, f"-{LOOKBACK} days"))
    conn.close()
    xu = load_pq("XU100.IS")

    # her sinyal için: entry + giriş indexi + parquet
    trades = []  # (close_series, idx, entry, sig_ts)
    for _, sig in sigs.iterrows():
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
        trades.append((df["Close"], idx, entry, sig_ts))

    print(f"\n=== ER B8 — İdeal vade taraması · {len(trades)} sinyal ===\n")
    print(f"{'Gün':>3} | {'N':>3} | {'İsabet':>7} | {'Ort.Get':>8} | {'Kazanç':>7} | {'Kayıp':>7} | {'PF':>5} | {'α':>6}")
    print("-" * 66)

    best_ret = (None, -999)
    best_pf = (None, -999)
    best_exp = (None, -999)

    for d in range(1, MAXD + 1):
        rets, alphas = [], []
        for close, idx, entry, sig_ts in trades:
            f = idx + d
            if f >= len(close):
                continue
            r = (float(close.iloc[f]) - entry) / entry * 100
            rets.append(r)
            if xu is not None and not xu.empty:
                xi = xu.index.searchsorted(sig_ts)
                xf = xi + d
                if xi < len(xu) and xf < len(xu):
                    e0 = float(xu["Close"].iloc[xi]); e1 = float(xu["Close"].iloc[xf])
                    if e0 > 0:
                        alphas.append(r - (e1 - e0) / e0 * 100)
        if len(rets) < 5:
            continue
        s = pd.Series(rets)
        pos = s[s > 0]; neg = s[s <= 0]
        n = len(s)
        hit = len(pos) / n * 100
        avg = s.mean()
        aw = pos.mean() if len(pos) else 0.0
        al = neg.mean() if len(neg) else 0.0
        pf = (pos.sum() / abs(neg.sum())) if abs(neg.sum()) > 0 else 99.0
        a = pd.Series(alphas).mean() if alphas else float("nan")
        exp = (hit / 100) * aw + (1 - hit / 100) * al
        print(f"{d:>3} | {n:>3} | %{hit:>5.1f} | %{avg:>+6.2f} | %{aw:>+5.1f} | %{al:>+5.1f} | {pf:>5.2f} | %{a:>+4.1f}")
        if avg > best_ret[1]:
            best_ret = (d, avg)
        if pf > best_pf[1]:
            best_pf = (d, pf)
        if exp > best_exp[1]:
            best_exp = (d, exp)

    print("-" * 66)
    print(f"En yüksek ORT. GETİRİ : {best_ret[0]}. gün (%{best_ret[1]:+.2f})")
    print(f"En yüksek PROFIT FACTOR: {best_pf[0]}. gün ({best_pf[1]:.2f})")
    print(f"En yüksek BEKLENTİ     : {best_exp[0]}. gün (%{best_exp[1]:+.2f})")


if __name__ == "__main__":
    main()
