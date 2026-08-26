"""
SMR — POC Retest Backtest (standalone)

Soru: "Fiyat 20g POC'tan ≥ %3 uzaklaştığında, sonraki 10 gün içinde
       POC'a %1 yakına dönüş GERÇEKLEŞTİ Mİ?"

POC mıknatıs hipotezini sayısal olarak ölçer — BIST'te işe yarıyor mu?

Kaynak:    veriler/*.IS_1d.parquet
Çıktı:     backtest_poc_retest.json + konsol özeti

Çalıştırma:
    python backtest_poc_retest.py
    python backtest_poc_retest.py --lookback 20 --stretch 3.0 --retest 1.0 --window 10
    python backtest_poc_retest.py --days 504        # son 2 yıl (varsayılan ~1 yıl)
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
OUTPUT_FILE = BASE_DIR / "backtest_poc_retest.json"


# ─── POC hesap (app.py calculate_volume_profile_poc ile aynı mantık) ─────────
def calc_poc(window_df: pd.DataFrame, bins: int = 20) -> float:
    if len(window_df) < 2:
        return float("nan")
    lo = float(window_df["Low"].min())
    hi = float(window_df["High"].max())
    if hi <= lo:
        return float(window_df["Close"].iloc[-1])
    edges = np.linspace(lo, hi, bins + 1)
    vol_profile = np.zeros(bins)
    for _, row in window_df.iterrows():
        h = float(row["High"]); l = float(row["Low"]); v = float(row.get("Volume", 0))
        if v <= 0 or np.isnan(v):
            continue
        rng = h - l
        if rng <= 0:
            idx = int(np.clip(np.digitize((h + l) / 2, edges) - 1, 0, bins - 1))
            vol_profile[idx] += v
            continue
        for i in range(bins):
            bb, bt = edges[i], edges[i + 1]
            if h >= bb and l <= bt:
                overlap = min(h, bt) - max(l, bb)
                if overlap > 0:
                    vol_profile[i] += v * (overlap / rng)
    if vol_profile.sum() <= 0:
        return float(window_df["Close"].iloc[-1])
    idx = int(np.argmax(vol_profile))
    return float((edges[idx] + edges[idx + 1]) / 2.0)


def load_parquet(symbol_file: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_parquet(symbol_file)
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        # Beklenen kolonlar
        need = {"Open", "High", "Low", "Close", "Volume"}
        if not need.issubset(df.columns):
            return None
        return df
    except Exception:
        return None


# ─── Tek hisse backtest ────────────────────────────────────────────────────────
def calc_vp_sekil(window_df: pd.DataFrame, bins: int = 20) -> str:
    """app.py vp_sekil mantığıyla aynı: POC'un VA (~%70 hacim) içindeki konumu.
    Returns: 'Akümülasyon' | 'Denge' | 'Dağıtım' | 'Yok'"""
    if len(window_df) < 5:
        return "Yok"
    lo = float(window_df["Low"].min()); hi = float(window_df["High"].max())
    if hi <= lo:
        return "Yok"
    edges = np.linspace(lo, hi, bins + 1)
    vp = np.zeros(bins)
    for _, row in window_df.iterrows():
        h = float(row["High"]); l = float(row["Low"]); v = float(row.get("Volume", 0))
        if v <= 0 or np.isnan(v):
            continue
        rng = h - l
        if rng <= 0:
            idx = int(np.clip(np.digitize((h + l) / 2, edges) - 1, 0, bins - 1))
            vp[idx] += v; continue
        for i in range(bins):
            bb, bt = edges[i], edges[i + 1]
            if h >= bb and l <= bt:
                ov = min(h, bt) - max(l, bb)
                if ov > 0:
                    vp[i] += v * (ov / rng)
    if vp.sum() <= 0:
        return "Yok"
    poc_idx = int(np.argmax(vp))
    # Value Area: POC etrafında %70 hacim
    total = vp.sum(); target = total * 0.70
    included = {poc_idx}; cum = vp[poc_idx]
    lower, upper = poc_idx - 1, poc_idx + 1
    while cum < target:
        lv = vp[lower] if lower >= 0 else 0
        uv = vp[upper] if upper < bins else 0
        if lv == 0 and uv == 0: break
        if uv >= lv:
            included.add(upper); cum += uv; upper += 1
        else:
            included.add(lower); cum += lv; lower -= 1
    val_idx = min(included); vah_idx = max(included)
    val_p = float(edges[val_idx]); vah_p = float(edges[vah_idx + 1])
    poc_p = float((edges[poc_idx] + edges[poc_idx + 1]) / 2.0)
    va_w = max(vah_p - val_p, 1e-9)
    poc_off = (poc_p - (val_p + vah_p) / 2) / va_w  # -0.5 ... +0.5
    if poc_off < -0.15:  return "Akümülasyon"
    if poc_off >  0.15:  return "Dağıtım"
    return "Denge"


def backtest_one(df: pd.DataFrame, lookback: int, stretch_pct: float,
                 retest_pct: float, fwd_window: int, scan_days: int):
    """
    Her ilgili bar T için:
      - POC_T = son `lookback` günün POC'u
      - vp_sekil_T (Akümülasyon/Denge/Dağıtım)
      - trend_T  (Up/Down/Flat — fiyat vs SMA50 ve SMA50 eğimi)
      - Fiyat_T ile POC_T uzaklık |dist%|
      - dist >= stretch_pct ise EVENT
      - T+1..T+fwd_window içinde |fiyat - POC_T| / POC_T < retest_pct/100 ise HIT
    """
    out = {
        "events": 0, "hits": 0,
        "above_events": 0, "above_hits": 0,
        "below_events": 0, "below_hits": 0,
        "days_to_retest": [],
        # Segment kovaları: (vp_sekil, side) → {events, hits}
        # side: 'above' | 'below'
        "seg_vp_side": {},      # key: f"{vp}|{side}"
        "seg_trend_side": {},   # key: f"{trend}|{side}"
        "seg_vp_trend_side": {},# key: f"{vp}|{trend}|{side}"  (en granüler)
    }
    n = len(df)
    if n < lookback + fwd_window + 60:
        return out
    closes = df["Close"].values.astype(float)
    highs  = df["High"].values.astype(float)
    lows   = df["Low"].values.astype(float)
    sma50  = pd.Series(closes).rolling(50, min_periods=20).mean().values

    start = max(lookback, 60, n - scan_days)
    end   = n - fwd_window

    def _bump(d: dict, key: str, hit: bool):
        cell = d.setdefault(key, {"events": 0, "hits": 0})
        cell["events"] += 1
        if hit: cell["hits"] += 1

    for t in range(start, end):
        window = df.iloc[t - lookback + 1 : t + 1]
        poc = calc_poc(window)
        if np.isnan(poc) or poc <= 0:
            continue
        price_t = closes[t]
        dist_pct = (price_t - poc) / poc * 100.0
        if abs(dist_pct) < stretch_pct:
            continue
        is_above = dist_pct > 0
        side = "above" if is_above else "below"

        # vp_sekil — aynı 20g pencerede
        vp_lbl = calc_vp_sekil(window)
        # trend — SMA50 vs fiyat + SMA50 eğimi (10 günlük değişim)
        s50_t = sma50[t]
        if np.isnan(s50_t):
            trend_lbl = "Yok"
        else:
            s50_prev = sma50[t - 10] if t - 10 >= 0 and not np.isnan(sma50[t - 10]) else s50_t
            slope = (s50_t - s50_prev) / s50_prev * 100.0 if s50_prev > 0 else 0
            above_sma = price_t > s50_t
            if above_sma and slope > 1.0:    trend_lbl = "Up"
            elif (not above_sma) and slope < -1.0: trend_lbl = "Down"
            else:                            trend_lbl = "Flat"

        out["events"] += 1
        if is_above: out["above_events"] += 1
        else:        out["below_events"] += 1

        tol = poc * (retest_pct / 100.0)
        retest_day = None
        for k in range(1, fwd_window + 1):
            h_k = highs[t + k]; l_k = lows[t + k]
            if l_k <= poc + tol and h_k >= poc - tol:
                retest_day = k; break
        hit = retest_day is not None
        if hit:
            out["hits"] += 1
            out["days_to_retest"].append(retest_day)
            if is_above: out["above_hits"] += 1
            else:        out["below_hits"] += 1

        _bump(out["seg_vp_side"],       f"{vp_lbl}|{side}",              hit)
        _bump(out["seg_trend_side"],    f"{trend_lbl}|{side}",           hit)
        _bump(out["seg_vp_trend_side"], f"{vp_lbl}|{trend_lbl}|{side}",  hit)
    return out


def aggregate(rows: list[dict]) -> dict:
    """Birden çok hissenin event/hit toplamlarını birleştir."""
    agg = {
        "symbols": 0,
        "events": 0, "hits": 0,
        "above_events": 0, "above_hits": 0,
        "below_events": 0, "below_hits": 0,
        "days_list": [],
        "seg_vp_side": {},
        "seg_trend_side": {},
        "seg_vp_trend_side": {},
    }
    def _merge(dst, src):
        for k, cell in src.items():
            tgt = dst.setdefault(k, {"events": 0, "hits": 0})
            tgt["events"] += cell["events"]
            tgt["hits"]   += cell["hits"]
    for r in rows:
        if r["events"] == 0:
            continue
        agg["symbols"] += 1
        agg["events"] += r["events"]
        agg["hits"]   += r["hits"]
        agg["above_events"] += r["above_events"]
        agg["above_hits"]   += r["above_hits"]
        agg["below_events"] += r["below_events"]
        agg["below_hits"]   += r["below_hits"]
        agg["days_list"].extend(r["days_to_retest"])
        _merge(agg["seg_vp_side"],       r.get("seg_vp_side", {}))
        _merge(agg["seg_trend_side"],    r.get("seg_trend_side", {}))
        _merge(agg["seg_vp_trend_side"], r.get("seg_vp_trend_side", {}))
    return agg


def fmt_pct(num, den):
    if den == 0:
        return "—"
    return f"%{(num / den) * 100:.1f}"


def summarize(name: str, agg: dict) -> dict:
    days = agg["days_list"]
    hit_rate = (agg["hits"] / agg["events"] * 100) if agg["events"] else 0.0
    above_rate = (agg["above_hits"] / agg["above_events"] * 100) if agg["above_events"] else 0.0
    below_rate = (agg["below_hits"] / agg["below_events"] * 100) if agg["below_events"] else 0.0
    return {
        "segment":       name,
        "symbols":       agg["symbols"],
        "events":        agg["events"],
        "hits":          agg["hits"],
        "hit_rate_pct":  round(hit_rate, 2),
        "above_events":  agg["above_events"],
        "above_hit_rate_pct": round(above_rate, 2),
        "below_events":  agg["below_events"],
        "below_hit_rate_pct": round(below_rate, 2),
        "avg_days_to_retest":    round(float(np.mean(days)), 2) if days else None,
        "median_days_to_retest": round(float(np.median(days)), 2) if days else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, default=20, help="POC pencere (gun)")
    ap.add_argument("--stretch",  type=float, default=3.0, help="Event esigi - fiyat POC'tan yuzde X uzakta")
    ap.add_argument("--retest",   type=float, default=1.0, help="Retest toleransi - POC'a yuzde X yakin")
    ap.add_argument("--window",   type=int, default=10, help="Ileriye bakma penceresi (gun)")
    ap.add_argument("--days",     type=int, default=252, help="Geriye bakilan event araligi (gun)")
    args = ap.parse_args()

    if not PARQUET_DIR.exists():
        print(f"❌ Parquet klasörü yok: {PARQUET_DIR}")
        return 1

    files = sorted([p for p in PARQUET_DIR.glob("*.IS_1d.parquet")])
    print(f"📂 BIST sembol sayısı (.IS parquet): {len(files)}")
    print(f"⚙️  POC lookback={args.lookback}g · stretch≥%{args.stretch} · retest<%{args.retest} · forward={args.window}g · tarama={args.days}g")
    print()

    rows = []
    t0 = datetime.now()
    for i, fp in enumerate(files, 1):
        sym = fp.stem.replace("_1d", "")
        df = load_parquet(fp)
        if df is None or len(df) < args.lookback + args.window + 30:
            continue
        r = backtest_one(df, args.lookback, args.stretch, args.retest, args.window, args.days)
        r["symbol"] = sym
        rows.append(r)
        if i % 50 == 0:
            print(f"  [{i}/{len(files)}] işlendi…")

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n✅ {len(rows)} hisse tarandı · {elapsed:.1f}sn")

    # Tek segment — TÜM BIST .IS evreni
    overall = aggregate(rows)
    summary = summarize("ALL_BIST_IS", overall)

    # Konsol özeti
    print("\n" + "═" * 64)
    print("POC RETEST BACKTEST — SONUÇ")
    print("═" * 64)
    print(f"Hisseler aktif         : {summary['symbols']}")
    print(f"Toplam EVENT (stretch) : {summary['events']:,}")
    print(f"  ↳ HIT (retest)       : {summary['hits']:,}  ({summary['hit_rate_pct']}%)")
    print(f"  ↳ Yukarı stretch     : {summary['above_events']:,} event · hit %{summary['above_hit_rate_pct']}")
    print(f"  ↳ Aşağı  stretch     : {summary['below_events']:,} event · hit %{summary['below_hit_rate_pct']}")
    print(f"Ortalama retest süresi : {summary['avg_days_to_retest']}g  (medyan {summary['median_days_to_retest']}g)")
    print("═" * 64)

    def _print_segment(title, seg_dict, sort_key="hit_rate", min_ev=50):
        rows_ = []
        for k, cell in seg_dict.items():
            if cell["events"] < min_ev:
                continue
            hr = (cell["hits"] / cell["events"] * 100) if cell["events"] else 0
            rows_.append((k, cell["events"], cell["hits"], hr))
        if not rows_:
            return
        rows_.sort(key=lambda x: -x[3])
        print(f"\n📊 {title} (event ≥ {min_ev}):")
        print(f"  {'segment':<28} {'event':>8} {'hit':>6}  {'hit%':>6}")
        for k, ev, hi, hr in rows_:
            print(f"  {k:<28} {ev:>8,} {hi:>6,}  {hr:>5.1f}%")

    _print_segment("VP_SEKİL × YÖN",      overall["seg_vp_side"],       min_ev=200)
    _print_segment("TREND × YÖN",         overall["seg_trend_side"],    min_ev=200)
    _print_segment("VP × TREND × YÖN",    overall["seg_vp_trend_side"], min_ev=100)

    # Tek hisse top 10 / bottom 10 (event >= 5 filtresi)
    qualified = [r for r in rows if r["events"] >= 5]
    qualified.sort(key=lambda r: (r["hits"] / r["events"]) if r["events"] else 0, reverse=True)
    print("\n🏆 EN YÜKSEK HIT (event ≥ 5):")
    for r in qualified[:10]:
        rate = r["hits"] / r["events"] * 100
        print(f"  {r['symbol']:<14} {r['events']:>4} event · hit %{rate:5.1f}")
    print("\n📉 EN DÜŞÜK HIT (event ≥ 5):")
    for r in qualified[-10:]:
        rate = r["hits"] / r["events"] * 100
        print(f"  {r['symbol']:<14} {r['events']:>4} event · hit %{rate:5.1f}")

    # JSON çıktı — full döküm
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": {
            "lookback_days": args.lookback,
            "stretch_pct": args.stretch,
            "retest_pct": args.retest,
            "forward_window": args.window,
            "scan_days": args.days,
        },
        "summary": summary,
        "segments": {
            "vp_side":       overall["seg_vp_side"],
            "trend_side":    overall["seg_trend_side"],
            "vp_trend_side": overall["seg_vp_trend_side"],
        },
        "by_symbol": [
            {
                "symbol":      r["symbol"],
                "events":      r["events"],
                "hits":        r["hits"],
                "hit_rate_pct": round((r["hits"] / r["events"] * 100) if r["events"] else 0, 2),
                "above_events": r["above_events"],
                "above_hits":   r["above_hits"],
                "below_events": r["below_events"],
                "below_hits":   r["below_hits"],
                "avg_days":    round(float(np.mean(r["days_to_retest"])), 2) if r["days_to_retest"] else None,
            }
            for r in rows if r["events"] > 0
        ],
    }
    OUTPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n📝 Çıktı: {OUTPUT_FILE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
