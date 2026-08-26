"""
SMR — RSI Kova Backtest'i (standalone) — EKRAN REFORMU 2b ön şartı (17 Tem 2026)

Soru: "RSI(14) uç bölgedeyken (<25 / >75) sonraki 5/10/20 günde ne oluyor?"
FİYAT kartındaki iki-modlu RSI rozetinin EŞİĞİ ve KARNE SATIRI bu ölçümden çıkar
(sezgiyle eşik seçmek yasak — feedback_extrapolation_yasak).

Yöntem notları:
  - RSI formülü scan_pipeline._compute_signal_features f_rsi ile BİREBİR AYNI
    (SMA rolling(14) gain/loss) — karne, ekranda loglanan sayıyla aynı dili konuşsun.
  - Hisse (*.IS, X-endeksler hariç) ve ENDEKS (XBANK/XU100/...) AYRI ölçülür
    (endekste RSI<25 çok daha nadir ve muhtemelen daha anlamlı).
  - İki sayım: her-gün (all) + kovaya GİRİŞ günü (entry — dünkü RSI eşik dışıydı).
    Rozet "taze uç / yerleşik uç" ayrımı entry istatistiğinden beslenecek.
  - Zehir bekçisi (15 Tem dersi): olay penceresinde tek günde |%15|+ (endekste |%8|+)
    hareket varsa bölünme/bozuk-bar şüphesi → olay atılır.

Kaynak:    veriler/*_1d.parquet
Çıktı:     rsi_kova_backtest.json + rsi_kova_report.md + konsol özeti

Çalıştırma:
    python rsi_kova_backtest.py
    python rsi_kova_backtest.py --days 750 --sample 30   # smoke (backtest_health)
"""

import sys
import io
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

# UTF-8 stdout zorunlu (Windows cp1254 emoji uyumsuzluğunu çöz)
try:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR    = Path(__file__).parent
PARQUET_DIR = BASE_DIR / "veriler"
OUTPUT_JSON = BASE_DIR / "rsi_kova_backtest.json"
OUTPUT_MD   = BASE_DIR / "rsi_kova_report.md"

# Kovalar: (ad, alt_dahil, üst_hariç). 30-70 = referans (baseline).
BUCKETS = [
    ("<15",   0.0, 15.0),
    ("15-20", 15.0, 20.0),
    ("20-25", 20.0, 25.0),
    ("25-30", 25.0, 30.0),
    ("30-70", 30.0, 70.0),   # referans kovası
    ("70-75", 70.0, 75.0),
    ("75-80", 75.0, 80.0),
    (">80",   80.0, 100.01),
]
FWD_DAYS = (5, 10, 20)


def compute_rsi14(close: pd.Series) -> pd.Series:
    """scan_pipeline f_rsi ile birebir aynı: SMA rolling(14) gain/loss."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # loss=0 (hiç düşüş yok) → RSI 100
    rsi = rsi.where(~(loss == 0), 100.0)
    return rsi


def load_parquet(fp: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_parquet(fp)
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        need = {"Open", "High", "Low", "Close", "Volume"}
        if not need.issubset(df.columns):
            return None
        df = df[df["Close"] > 0]
        return df if len(df) >= 60 else None
    except Exception:
        return None


def backtest_one(df: pd.DataFrame, scan_days: int, max_abs_1g: float):
    """Tek sembol: her kova için (all + entry) 5/10/20g ileri getiri olayları döner.
    max_abs_1g: zehir bekçisi eşiği (hisse 0.15, endeks 0.08)."""
    c = df["Close"].astype(float)
    rsi = compute_rsi14(c)
    ret1 = c.pct_change()
    # zehir bekçisi: şüpheli günler (bölünme/bozuk bar)
    bad = (ret1.abs() > max_abs_1g).to_numpy()
    n = len(c)
    start = max(20, n - scan_days - max(FWD_DAYS))
    cv = c.to_numpy(); rv = rsi.to_numpy()
    events = []  # (bucket, is_entry, fwd5, fwd10, fwd20)
    for t in range(start, n - max(FWD_DAYS)):
        r = rv[t]
        if np.isnan(r):
            continue
        bname = None
        for name, lo, hi in BUCKETS:
            if lo <= r < hi:
                bname = name
                break
        if bname is None:
            continue
        # zehir penceresi: olay günü ± ileri pencere içinde şüpheli bar varsa atla
        if bad[max(0, t - 1): t + max(FWD_DAYS) + 1].any():
            continue
        # entry: dünkü RSI bu kovanın DIŞINDA mıydı (uç kovalarda anlamlı)
        r_prev = rv[t - 1] if t >= 1 else np.nan
        lo, hi = next((b[1], b[2]) for b in BUCKETS if b[0] == bname)
        is_entry = (not np.isnan(r_prev)) and not (lo <= r_prev < hi)
        fw = []
        ok = True
        for k in FWD_DAYS:
            base = cv[t]
            if base <= 0 or np.isnan(cv[t + k]):
                ok = False
                break
            fw.append((cv[t + k] / base - 1.0) * 100.0)
        if not ok:
            continue
        events.append((bname, is_entry, fw[0], fw[1], fw[2]))
    return events


def summarize(all_events):
    """events: list[(bucket, is_entry, f5, f10, f20)] → kova × (all/entry) istatistik."""
    out = {}
    df = pd.DataFrame(all_events, columns=["bucket", "entry", "f5", "f10", "f20"])
    for name, _, _ in BUCKETS:
        sub = df[df["bucket"] == name]
        row = {}
        for mode, msub in (("all", sub), ("entry", sub[sub["entry"]])):
            if len(msub) == 0:
                row[mode] = None
                continue
            row[mode] = {
                "n": int(len(msub)),
                **{f"avg_{k}g": round(float(msub[f"f{k}"].mean()), 2) for k in FWD_DAYS},
                **{f"med_{k}g": round(float(msub[f"f{k}"].median()), 2) for k in FWD_DAYS},
                **{f"hit_{k}g": round(float((msub[f"f{k}"] > 0).mean() * 100), 1) for k in FWD_DAYS},
            }
        out[name] = row
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=750, help="sembol başına taranan son N bar (~3 yıl)")
    ap.add_argument("--sample", type=int, default=0, help="sadece ilk N hisse (smoke test)")
    args = ap.parse_args()

    files = sorted(PARQUET_DIR.glob("*_1d.parquet"))
    hisse_files, endeks_files = [], []
    for f in files:
        sym = f.name.replace("_1d.parquet", "")
        if sym.startswith("X") and sym.endswith(".IS"):
            endeks_files.append(f)          # XBANK/XU100/XUSIN...
        elif sym.endswith(".IS"):
            hisse_files.append(f)           # BIST hisse
        # .IS olmayanlar (AAPL, GC=F...) kapsam dışı — BIST karnesi
    if args.sample:
        hisse_files = hisse_files[: args.sample]

    results = {}
    for grup, flist, guard in (("hisse", hisse_files, 0.15), ("endeks", endeks_files, 0.08)):
        ev, n_sym = [], 0
        for fp in flist:
            df = load_parquet(fp)
            if df is None:
                continue
            e = backtest_one(df, args.days, guard)
            if e:
                ev.extend(e)
                n_sym += 1
        results[grup] = {"n_symbols": n_sym, "n_events": len(ev), "buckets": summarize(ev)}
        print(f"[{grup}] {n_sym} sembol, {len(ev):,} olay")

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": {"days": args.days, "sample": args.sample, "fwd_days": list(FWD_DAYS),
                   "rsi_formula": "SMA rolling(14) — scan_pipeline f_rsi ile aynı",
                   "poison_guard": "hisse |1g|>%15, endeks |1g|>%8 → olay atılır"},
        "results": results,
    }
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── Markdown rapor + konsol özeti ──────────────────────────────────────
    lines = ["# RSI Kova Backtest'i — Ekran Reformu 2b (17 Tem 2026)",
             f"\nÜretim: {payload['generated_utc']} · pencere: son {args.days} bar · "
             f"ileri: {FWD_DAYS} gün · formül: f_rsi ile aynı (SMA-14)\n"]
    for grup in ("hisse", "endeks"):
        r = results[grup]
        lines.append(f"\n## {grup.upper()} ({r['n_symbols']} sembol · {r['n_events']:,} olay)\n")
        lines.append("| Kova | Mod | N | 5g ort | 10g ort | 20g ort | 10g medyan | 10g isabet |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for name, _, _ in BUCKETS:
            row = r["buckets"].get(name) or {}
            for mode in ("all", "entry"):
                s = row.get(mode)
                if not s:
                    continue
                tag = "her gün" if mode == "all" else "giriş günü"
                lines.append(
                    f"| {name} | {tag} | {s['n']:,} | {s['avg_5g']:+.2f}% | {s['avg_10g']:+.2f}% "
                    f"| {s['avg_20g']:+.2f}% | {s['med_10g']:+.2f}% | %{s['hit_10g']} |")
    lines.append("\n> Okuma notu: 30-70 kovası REFERANSTIR — uç kova ancak referanstan belirgin"
                 "\n> ayrışıyorsa rozeti hak eder. 'Giriş günü' satırı taze-uç davranışıdır.\n")
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nJSON: {OUTPUT_JSON.name} · Rapor: {OUTPUT_MD.name}")

    # Konsol: hisse 10g hızlı özet
    print("\nHİSSE — 10g ortalama (her gün / giriş günü):")
    for name, _, _ in BUCKETS:
        row = results["hisse"]["buckets"].get(name) or {}
        a, e = row.get("all"), row.get("entry")
        if a:
            etxt = f" | giriş: {e['avg_10g']:+.2f}% (N={e['n']:,})" if e else ""
            print(f"  {name:>6}: {a['avg_10g']:+.2f}% isabet %{a['hit_10g']} (N={a['n']:,}){etxt}")


if __name__ == "__main__":
    main()
