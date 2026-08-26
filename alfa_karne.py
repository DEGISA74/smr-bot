"""alfa_karne.py — TARAMALARIMIZ ENDEKSİ YENİYOR MU? (tekrar koşulabilir)

Neden var: "getiri +%2" tek başına bir şey söylemez — endeks o gün +%4 yaptıysa
kaybettik demektir. Bu araç her sinyali AYNI GÜN başlayan XU100 getirisiyle
kıyaslar (alfa) ve rejime böler.

Kullanım:  python alfa_karne.py            (T+20, tüm taramalar)
           python alfa_karne.py --gun 10

Çıktı: konsol + logs/alfa_karne.md
⚠ Rejim değiştikçe YENİDEN KOŞ — tek rejimde çıkan sonuç kural olamaz.
"""
import sys, argparse, sqlite3
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import pandas as pd

KOK = Path(__file__).parent
DB = KOK / "patron.db"
BENCH = KOK / "veriler" / "XU100.IS_1d.parquet"
OUT = KOK / "logs" / "alfa_karne.md"
MIN_N = 100          # tarama bazında yorum eşiği
MIN_N_REJIM = 40     # rejim kırılımında eşik


def _bench_ileri(gun):
    d = pd.read_parquet(BENCH)
    d.index = pd.to_datetime(d.index).normalize()
    d = d[~d.index.duplicated(keep="last")].sort_index()
    c = d["Close"]
    return (c.shift(-gun) / c - 1) * 100, (c.pct_change(20) > 0).map({True: "YÜKSELEN", False: "DÜŞEN"})


def yukle(gun):
    ileri, rejim = _bench_ileri(gun)
    con = sqlite3.connect(DB)
    sig = pd.read_sql(
        "SELECT s.scan_date, s.scan_type, r.return_pct AS ret "
        "FROM scan_signals s JOIN signal_returns r ON r.signal_id=s.id "
        "WHERE r.return_pct IS NOT NULL AND r.day_offset=?", con, params=(gun,))
    sig["tarih"] = pd.to_datetime(sig.scan_date).dt.normalize()
    sig["bench"] = sig.tarih.map(ileri)
    sig["rejim"] = sig.tarih.map(rejim)
    sig = sig.dropna(subset=["bench", "rejim"])
    sig["alfa"] = sig.ret - sig.bench
    return sig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gun", type=int, default=20)
    a = ap.parse_args()
    sig = yukle(a.gun)

    L = [f"# ALFA KARNESİ — T+{a.gun}", "",
         f"- Sinyal: **{len(sig)}** · {sig.tarih.min().date()} → {sig.tarih.max().date()}",
         "- Alfa = sinyal getirisi − AYNI GÜN başlayan XU100 getirisi.",
         "- Rejim: sinyal günü XU100 20 günlük değişimi.", "",
         "## 1) Genel", "",
         "| Rejim | N | Sinyal % | XU100 % | ALFA |", "|---|---|---|---|---|"]
    for rj in ("YÜKSELEN", "DÜŞEN"):
        s = sig[sig.rejim == rj]
        if len(s):
            L.append(f"| {rj} | {len(s)} | {s.ret.mean():+.2f} | {s.bench.mean():+.2f} | **{s.alfa.mean():+.2f}** |")
    L.append(f"| TÜMÜ | {len(sig)} | {sig.ret.mean():+.2f} | {sig.bench.mean():+.2f} | **{sig.alfa.mean():+.2f}** |")

    L += ["", "## 2) Tarama × Rejim (her iki rejimde N≥%d)" % MIN_N_REJIM, "",
          "| Tarama | Yükselen alfa (N) | Düşen alfa (N) | Hüküm |", "|---|---|---|---|"]
    p = sig.pivot_table(index="scan_type", columns="rejim", values="alfa", aggfunc=["mean", "size"])
    if ("mean", "YÜKSELEN") in p.columns and ("mean", "DÜŞEN") in p.columns:
        t = pd.DataFrame({"ya": p[("mean", "YÜKSELEN")], "yn": p[("size", "YÜKSELEN")],
                          "da": p[("mean", "DÜŞEN")], "dn": p[("size", "DÜŞEN")]}).dropna()
        t = t[(t.yn >= MIN_N_REJIM) & (t.dn >= MIN_N_REJIM)]
        t["min"] = t[["ya", "da"]].min(axis=1)
        for ad, r in t.sort_values("min", ascending=False).iterrows():
            if r["min"] > 0.5:
                h = "🟢 İKİ REJİMDE DE POZİTİF"
            elif r["min"] > -1:
                h = "⚪ nötr"
            elif max(r.ya, r.da) > 0:
                h = "🟡 tek rejimde çalışıyor"
            else:
                h = "🔴 iki rejimde de negatif"
            L.append(f"| {ad} | {r.ya:+.2f} ({int(r.yn)}) | {r.da:+.2f} ({int(r.dn)}) | {h} |")

    L += ["", "## 3) Toplam açığı kim üretiyor (adet × alfa)", "",
          "| Tarama | N | Alfa | Toplam etki |", "|---|---|---|---|"]
    g = sig.groupby("scan_type").alfa.agg(["size", "mean"])
    g = g[g["size"] >= MIN_N]
    g["etki"] = g["size"] * g["mean"]
    g = g.sort_values("etki")
    for ad, r in pd.concat([g.head(6), g.tail(4)]).iterrows():
        L.append(f"| {ad} | {int(r['size'])} | {r['mean']:+.2f} | {r['etki']:+.0f} |")

    m = "\n".join(L)
    print(m)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(m, encoding="utf-8")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
