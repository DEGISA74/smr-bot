# -*- coding: utf-8 -*-
"""
Codex'in test planı — confluence / kesişim / crowding, WALK-FORWARD (out-of-sample).
Ana soru: Pozitif-kenarlı taramaların KESİŞİMİ, EN İYİ TEK taramadan
istikrarlı ek alpha üretiyor mu? Yoksa çok-taramada-görünmek crowding cezası mı?

Metot:
- Gözlem birimi = benzersiz (symbol, signal_date). Aynı gün 5 tarama = 1 gözlem.
- Alpha = o gün taranan evrenin ortalamasından sapma (cross-sectional demean) => rejimden arınık.
- TRAIN penceresinde scanner edge tablosu kur (pozitif-kenarlı = train alpha>0, N yeterli).
- TEST penceresinde single / best-single / kesişim / crowding'i OUT-OF-SAMPLE ölç.
"""
import sqlite3
import numpy as np
import pandas as pd

DB = r"C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal\patron.db"
NOISE = {"radar1"}          # tüm evreni işaretliyor => gürültü, edge/kesişim dışı
OFFSETS = [5, 10, 20]
MIN_N_EDGE = 40             # train'de pozitif-kenar için min örneklem
np.random.seed(7)

con = sqlite3.connect(DB)
sr = pd.read_sql_query(
    "SELECT scan_type, symbol, signal_date, day_offset, return_pct "
    "FROM signal_returns WHERE day_offset IN (5,10,20)", con)
con.close()
sr["scan_type"] = sr["scan_type"].str.strip()
sr["month"] = sr["signal_date"].str[:7]

# --- per (symbol,date,offset): getiri = taramalar arası ortalama (aynı olay) ---
ret = (sr.groupby(["symbol", "signal_date", "month", "day_offset"])["return_pct"]
         .mean().reset_index())
retw = ret.pivot_table(index=["symbol", "signal_date", "month"],
                       columns="day_offset", values="return_pct").reset_index()
retw = retw.rename(columns={5: "r5", 10: "r10", 20: "r20"})

# --- cross-sectional demean (o gün taranan evrenin ortalaması) => alpha ---
for c in ["r5", "r10", "r20"]:
    daily = retw.groupby("signal_date")[c].transform("mean")
    retw["a" + c[1:]] = retw[c] - daily   # a5,a10,a20 = alpha

# --- her (symbol,date): hangi taramalar (gürültü hariç) + toplam tarama sayısı ---
mem = (sr[~sr["scan_type"].isin(NOISE)]
       .groupby(["symbol", "signal_date"])["scan_type"]
       .agg(lambda s: sorted(set(s))).reset_index().rename(columns={"scan_type": "scanners"}))
mem["n_scan"] = mem["scanners"].apply(len)
# toplam (gürültü dahil) sayı — crowding testi için
mem_all = (sr.groupby(["symbol", "signal_date"])["scan_type"]
           .nunique().reset_index().rename(columns={"scan_type": "n_scan_all"}))

base = retw.merge(mem, on=["symbol", "signal_date"], how="left") \
           .merge(mem_all, on=["symbol", "signal_date"], how="left")
base["scanners"] = base["scanners"].apply(lambda x: x if isinstance(x, list) else [])
base["n_scan"] = base["n_scan"].fillna(0).astype(int)


def metrics(df, col="a5"):
    """alpha kolonundan özet: N, ort alpha, ham getiri, isabet, PF, bootstrap %95 CI."""
    x = df[col].dropna().values
    raw = df["r" + col[1:]].dropna().values
    n = len(x)
    if n == 0:
        return None
    wins = raw[raw > 0]; losses = raw[raw < 0]
    pf = (wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
    # bootstrap CI of mean alpha
    if n >= 5:
        boot = [np.random.choice(x, n, replace=True).mean() for _ in range(1000)]
        lo, hi = np.percentile(boot, [2.5, 97.5])
    else:
        lo = hi = float("nan")
    return dict(N=n, alpha=round(float(x.mean()), 2), raw=round(float(raw.mean()), 2),
                hit=round(100.0 * (raw > 0).mean(), 1),
                avg_win=round(float(wins.mean()), 2) if len(wins) else 0.0,
                avg_loss=round(float(losses.mean()), 2) if len(losses) else 0.0,
                pf=round(pf, 2), ci_lo=round(float(lo), 2), ci_hi=round(float(hi), 2))


def show(title, m):
    if m is None:
        print(f"  {title:<34} (veri yok)")
        return
    print(f"  {title:<34} N={m['N']:>5}  alpha5={m['alpha']:>6}  ham5={m['raw']:>6}  "
          f"isabet={m['hit']:>5}%  PF={m['pf']:>4}  CI[{m['ci_lo']:>6},{m['ci_hi']:>6}]")


def edge_table(train, min_n=MIN_N_EDGE):
    """train'de her taramanın 5g alpha'sı — pozitif-kenar seçimi (SADECE train)."""
    rows = []
    ex = sr[(sr["month"].isin(train)) & (~sr["scan_type"].isin(NOISE))]
    ex = ex[ex["day_offset"] == 5]
    # her taramanın işaretlediği (symbol,date) -> o gözlemin a5'i
    j = ex.merge(base[["symbol", "signal_date", "a5"]], on=["symbol", "signal_date"], how="left")
    for st, g in j.groupby("scan_type"):
        a = g["a5"].dropna()
        if len(a) >= min_n:
            rows.append((st, len(a), round(a.mean(), 3)))
    tab = pd.DataFrame(rows, columns=["scan", "N", "alpha5"]).sort_values("alpha5", ascending=False)
    return tab


def run_window(train_months, test_month):
    print("\n" + "=" * 78)
    print(f"WALK-FORWARD  |  TRAIN={train_months}  ->  TEST=[{test_month}]  (out-of-sample)")
    print("=" * 78)
    et = edge_table(train_months)
    pos = et[et["alpha5"] > 0]["scan"].tolist()
    print(f"TRAIN'de pozitif-kenarlı taramalar (alpha5>0, N>={MIN_N_EDGE}):")
    print("  " + (", ".join(f"{r.scan}(+{r.alpha5})" for r in et[et.alpha5 > 0].itertuples()) or "YOK"))
    print(f"  [en kötü 3 train: " + ", ".join(f"{r.scan}({r.alpha5})" for r in et.tail(3).itertuples()) + "]")

    test = base[base["month"] == test_month].copy()
    test["pos_hit"] = test["scanners"].apply(lambda ss: [s for s in ss if s in pos])
    test["n_pos"] = test["pos_hit"].apply(len)

    print(f"\nTEST [{test_month}] out-of-sample sonuçlar (alpha = evren-günü ortalamasından sapma):")
    # A: hiç pozitif-kenar taraması işaretlememiş (kontrol)
    show("A. pozitif-kenar YOK (kontrol)", metrics(test[test["n_pos"] == 0]))
    # B: en az 1 pozitif-kenar (aday havuzu = 'best single' proxy)
    show("B. >=1 pozitif-kenar (havuz)", metrics(test[test["n_pos"] >= 1]))
    # C: KESİŞİM = en az 2 pozitif-kenar taraması
    show("C. KESISIM >=2 pozitif-kenar", metrics(test[test["n_pos"] >= 2]))
    show("   (kesisim >=3)", metrics(test[test["n_pos"] >= 3]))
    # D: crowding — toplam tarama sayısına göre (gürültü dahil)
    print("  --- crowding (toplam tarama sayisi, gurultu dahil) ---")
    for lo, hi, lab in [(1, 2, "1-2"), (3, 4, "3-4"), (5, 99, "5+")]:
        show(f"D. toplam tarama {lab}", metrics(test[(test["n_scan_all"] >= lo) & (test["n_scan_all"] <= hi)]))

    # Kesişim EK KATKI: havuz (B) vs kesişim (C) alpha farkı
    b = metrics(test[test["n_pos"] >= 1]); c = metrics(test[test["n_pos"] >= 2])
    if b and c:
        print(f"\n  >> KESISIM EK KATKISI: havuz alpha5={b['alpha']} -> kesisim alpha5={c['alpha']} "
              f"(fark {round(c['alpha']-b['alpha'],2)})  | kesisim CI_lo={c['ci_lo']}")
    return et


# --- iki out-of-sample penceresi (May-Haz -> Tem  ve  May -> Haz) ---
run_window(["2026-05", "2026-06"], "2026-07")
run_window(["2026-05"], "2026-06")

# --- pozitif-kenar taramaların TEKİL out-of-sample tutarlılığı (Temmuz) ---
print("\n" + "=" * 78)
print("BONUS: ad? gecen pozitif-kenar taramalarin TEKIL Temmuz (OOS) performansi")
print("=" * 78)
et_full = edge_table(["2026-05", "2026-06"])
pos = et_full[et_full["alpha5"] > 0]["scan"].tolist()
jul = sr[(sr["month"] == "2026-07") & (sr["day_offset"] == 5)]
jj = jul.merge(base[["symbol", "signal_date", "a5", "r5"]], on=["symbol", "signal_date"], how="left")
for st in pos:
    g = jj[jj["scan_type"] == st]
    m = metrics(g.rename(columns={"a5": "a5"}).assign(a5=g["a5"], r5=g["r5"]))
    show(f"{st}", m)
