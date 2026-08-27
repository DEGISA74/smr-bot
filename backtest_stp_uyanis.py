#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DEVASA BACKTEST — STP UYANIŞ (Uzun Baskı Sonrası STP Yukarı Kesiş)
==================================================================
STP = analysis_core ile AYNI çizgi: typical_price((H+L+C)/3) 6-periyot EWMA.
Olay = fiyat STP'yi YUKARI keser (dün ≤ STP, bugün > STP).

5 VARYANT (kümülatif — Codex önerisi + gürültü filtresi):
  V1  Çıplak STP yukarı kesiş
  V2  + kesişten ÖNCE ≥15 gün STP altında kalma (uzun baskı)
  V3  + hacim ≥ 1.5× (bugün HARİÇ önceki 20g ort)
  V4  + boğa mumu (engulfing / morning star / piercing / hammer)
  V5  + anlamlı kesiş (STP'yi ≥%0.5 geçti — 1 kuruş gürültüsü değil)

Her olay için ileri getiri: T+1, T+2, T+3, T+4, T+5, T+10, T+20 (giriş = kesiş günü kapanışı).
İKİ REJİM: XU100 > SMA50 → boğa · altı → ayı (olay tarihine göre).
SEGMENT: baskı-süresi kovaları (15-19 / 20-29 / 30+ gün) — "en uzun baskı gerçekten daha mı iyi?".
ONAYLI vs FAILED: kesişten sonra 2 gün STP üstünde kaldı + kesiş mumu dibini kırmadı mı? (iki-aşama değeri).
BASELINE: tüm hisse-günlerinin ort ileri getirisi (sinyal bunu geçiyor mu?).

Sadece OKUR; canlı sisteme yazmaz. Çıktı: rapor (md) + ham (json).
[[feedback-extrapolation-yasak]] — kümülatiften kural uydurma, segmente + iki rejim bak.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from signal_policy import (
    MEASUREMENT_REGIME_FALLING,
    MEASUREMENT_REGIME_RISING,
    measurement_regime_series,
)

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
VERI = ROOT / "veriler"
HORIZONS = [1, 2, 3, 4, 5, 10, 20]
DAYS_BELOW_MIN = 15
VOL_MULT = 1.5
MEANINGFUL_PCT = 0.005  # STP'yi %0.5 geçme


def _stp(df: pd.DataFrame) -> pd.Series:
    typ = (df["High"] + df["Low"] + df["Close"]) / 3.0
    return typ.ewm(span=6, adjust=False).mean()


def _bullish_candle(o, h, l, c, i) -> str:
    """Bar i'de boğa mumu tipi ('' = yok). engulfing/morning/piercing/hammer."""
    if i < 2:
        return ""
    o0, c0 = o[i - 1], c[i - 1]      # önceki
    o1, c1, h1, l1 = o[i], c[i], h[i], l[i]  # bugün
    body = abs(c1 - o1)
    green = c1 > o1
    red_prev = c0 < o0
    # Engulfing
    if red_prev and green and o1 <= c0 and c1 >= o0 and body > 0:
        return "engulfing"
    # Morning Star (3 mum)
    o2, c2 = o[i - 2], c[i - 2]
    if c2 < o2 and abs(c0 - o0) < abs(c2 - o2) * 0.6 and green and c1 > (o2 + c2) / 2:
        return "morning"
    # Piercing Line
    if red_prev and green and o1 < c0 and c1 > (o0 + c0) / 2 and c1 < o0:
        return "piercing"
    # Hammer (tek mum, uzun alt fitil)
    lower = min(o1, c1) - l1
    upper = h1 - max(o1, c1)
    if body > 0 and lower > body * 2 and upper < body * 0.6:
        return "hammer"
    return ""


def _regime_series(xu: pd.DataFrame) -> pd.Series:
    return measurement_regime_series(xu).map({
        MEASUREMENT_REGIME_RISING: "boğa",
        MEASUREMENT_REGIME_FALLING: "ayı",
    })


def _collect_events(regime: pd.Series) -> tuple[list[dict], list[float]]:
    events = []
    baseline_fwd = {k: [] for k in HORIZONS}
    files = sorted(VERI.glob("*.IS_1d.parquet"))
    for p in files:
        t = p.name[: -len(".IS_1d.parquet")]
        try:
            df = pd.read_parquet(p)
        except Exception:
            continue
        if len(df) < 60:
            continue
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if len(df) < 60:
            continue
        o = df["Open"].to_numpy(float); h = df["High"].to_numpy(float)
        l = df["Low"].to_numpy(float); c = df["Close"].to_numpy(float)
        v = df["Volume"].to_numpy(float) if "Volume" in df.columns else np.zeros(len(df))
        stp = _stp(df).to_numpy(float)
        vol_avg20 = pd.Series(v).shift(1).rolling(20).mean().to_numpy(float)  # bugün hariç
        n = len(df)
        # baseline: her hisse-günü ileri getirisi
        for i in range(1, n):
            for k in HORIZONS:
                if i + k < n and c[i] > 0:
                    baseline_fwd[k].append((c[i + k] / c[i] - 1) * 100)
        # olaylar
        for i in range(21, n - 1):
            if not (c[i - 1] <= stp[i - 1] and c[i] > stp[i]):
                continue
            # kesişten önce kaç gün altta
            db = 0
            j = i - 1
            while j >= 0 and c[j] < stp[j]:
                db += 1; j -= 1
            vr = (v[i] / vol_avg20[i]) if (vol_avg20[i] and vol_avg20[i] > 0) else np.nan
            meaningful = (c[i] - stp[i]) / stp[i] if stp[i] > 0 else 0.0
            candle = _bullish_candle(o, h, l, c, i)
            dt = df.index[i]
            reg = regime.get(dt, "?")
            fwd = {k: ((c[i + k] / c[i] - 1) * 100 if (i + k < n and c[i] > 0) else np.nan) for k in HORIZONS}
            # ONAY: sonraki 2 gün STP üstünde + kesiş mumu dibini kırmadı
            held = None
            fwd_conf = {k: np.nan for k in HORIZONS}
            if i + 2 < n:
                above2 = (c[i + 1] > stp[i + 1]) and (c[i + 2] > stp[i + 2])
                no_break = min(l[i + 1], l[i + 2]) >= l[i]
                held = bool(above2 and no_break)
                if held and c[i + 2] > 0:  # GERÇEKÇİ giriş = onay günü (t+2) kapanışı
                    fwd_conf = {k: ((c[i + 2 + k] / c[i + 2] - 1) * 100 if i + 2 + k < n else np.nan)
                                for k in HORIZONS}
            events.append({
                "ticker": t, "date": str(dt.date()), "days_below": db,
                "vol_ratio": None if np.isnan(vr) else round(float(vr), 2),
                "meaningful_pct": round(float(meaningful) * 100, 2),
                "candle": candle, "regime": reg, "held": held,
                **{f"f{k}": (None if np.isnan(x) else round(float(x), 2)) for k, x in fwd.items()},
                **{f"fc{k}": (None if np.isnan(x) else round(float(x), 2)) for k, x in fwd_conf.items()},
            })
    base = {k: (float(np.median(baseline_fwd[k])), float(np.mean(baseline_fwd[k])), len(baseline_fwd[k]))
            for k in HORIZONS}
    return events, base


def _stats(rows: list[dict], k: int) -> dict | None:
    vals = [r[f"f{k}"] for r in rows if r.get(f"f{k}") is not None]
    if not vals:
        return None
    a = np.array(vals, float)
    return {"n": len(a), "hit": round(float((a > 0).mean()) * 100, 1),
            "med": round(float(np.median(a)), 2), "mean": round(float(np.mean(a)), 2)}


def _variant(events: list[dict], name: str, pred) -> list[dict]:
    return [e for e in events if pred(e)]


def _line(rows: list[dict]) -> str:
    cells = []
    for k in HORIZONS:
        s = _stats(rows, k)
        cells.append(f"{s['hit']:>4}/{s['med']:>+5}" if s else "  -  ")
    n = len(rows)
    return f"N={n:<5} " + " | ".join(f"T+{k}:{c}" for k, c in zip(HORIZONS, cells))


def main() -> int:
    xu = pd.read_parquet(VERI / "XU100.IS_1d.parquet")
    regime = _regime_series(xu)
    print("Olaylar toplanıyor (618 hisse)...")
    events, base = _collect_events(regime)
    print(f"Toplam STP yukarı-kesiş olayı: {len(events)}\n")

    variants = [
        ("V1 çıplak kesiş", lambda e: True),
        ("V2 +≥15g baskı", lambda e: e["days_below"] >= DAYS_BELOW_MIN),
        ("V3 +hacim≥1.5x", lambda e: e["days_below"] >= DAYS_BELOW_MIN and (e["vol_ratio"] or 0) >= VOL_MULT),
        ("V4 +boğa mumu", lambda e: e["days_below"] >= DAYS_BELOW_MIN and (e["vol_ratio"] or 0) >= VOL_MULT and e["candle"]),
        ("V5 +anlamlı kesiş", lambda e: e["days_below"] >= DAYS_BELOW_MIN and (e["vol_ratio"] or 0) >= VOL_MULT and e["candle"] and e["meaningful_pct"] >= MEANINGFUL_PCT * 100),
    ]

    lines = ["# STP UYANIŞ — DEVASA BACKTEST", "",
             "Giriş = kesiş günü kapanışı. Hücre = **hit% / medyan getiri%**. STP = 6-EWMA typical price.", "",
             f"**BASELINE (tüm hisse-günleri):** " +
             " | ".join(f"T+{k}: hit —/med {base[k][0]:+.2f}% (N={base[k][2]})" for k in HORIZONS), ""]

    def _block(title, evs):
        lines.append(f"## {title}")
        lines.append("| Varyant | " + " | ".join(f"T+{k}" for k in HORIZONS) + " | N |")
        lines.append("|" + "---|" * (len(HORIZONS) + 2))
        for name, pred in variants:
            rows = _variant(evs, name, pred)
            cells = []
            for k in HORIZONS:
                s = _stats(rows, k)
                cells.append(f"{s['hit']}/{s['med']:+}" if s else "-")
            lines.append(f"| {name} | " + " | ".join(cells) + f" | {len(rows)} |")
        lines.append("")

    _block("TÜM REJİMLER", events)
    _block("BOĞA REJİMİ (XU100>SMA50)", [e for e in events if e["regime"] == "boğa"])
    _block("AYI REJİMİ (XU100<SMA50)", [e for e in events if e["regime"] == "ayı"])

    # SEGMENT: baskı süresi kovaları (V4 tabanında)
    lines.append("## SEGMENT — Baskı Süresi (V4: hacim+mum sabit)")
    lines.append("| Baskı günü | " + " | ".join(f"T+{k}" for k in HORIZONS) + " | N |")
    lines.append("|" + "---|" * (len(HORIZONS) + 2))
    v4 = _variant(events, "v4", variants[3][1])
    for lbl, lo, hi in [("15-19g", 15, 19), ("20-29g", 20, 29), ("30+g", 30, 9999)]:
        rows = [e for e in v4 if lo <= e["days_below"] <= hi]
        cells = []
        for k in HORIZONS:
            s = _stats(rows, k)
            cells.append(f"{s['hit']}/{s['med']:+}" if s else "-")
        lines.append(f"| {lbl} | " + " | ".join(cells) + f" | {len(rows)} |")
    lines.append("")

    # ONAYLI vs FAILED (V4 tabanında) — iki-aşama değeri
    lines.append("## ONAYLI vs FAILED (V4 — 2 gün STP üstünde kaldı mı?)")
    lines.append("| Grup | " + " | ".join(f"T+{k}" for k in HORIZONS) + " | N |")
    lines.append("|" + "---|" * (len(HORIZONS) + 2))
    for lbl, want in [("ONAYLI (tuttu)", True), ("FAILED (düştü)", False)]:
        rows = [e for e in v4 if e["held"] is want]
        cells = []
        for k in HORIZONS:
            s = _stats(rows, k)
            cells.append(f"{s['hit']}/{s['med']:+}" if s else "-")
        lines.append(f"| {lbl} | " + " | ".join(cells) + f" | {len(rows)} |")
    lines.append("")

    # GERÇEKÇİ ONAYLI — giriş onay günü (t+2) kapanışı (ileri-bakış yok)
    lines.append("## ONAYLI — GERÇEKÇİ GİRİŞ (onay günü t+2 kapanışından, V4)")
    lines.append("| Grup | " + " | ".join(f"T+{k}" for k in HORIZONS) + " | N |")
    lines.append("|" + "---|" * (len(HORIZONS) + 2))
    conf_rows = [{**e, **{f"f{k}": e.get(f"fc{k}") for k in HORIZONS}} for e in v4 if e["held"] is True]
    cells = []
    for k in HORIZONS:
        s = _stats(conf_rows, k)
        cells.append(f"{s['hit']}/{s['med']:+}" if s else "-")
    lines.append(f"| ONAYLI (t+2 giriş) | " + " | ".join(cells) + f" | {len(conf_rows)} |")
    lines.append("")

    (ROOT / "backtest_stp_uyanis.md").write_text("\n".join(lines), encoding="utf-8")
    (ROOT / "backtest_stp_uyanis.json").write_text(
        json.dumps({"n": len(events), "baseline": base, "events": events[:5000]},
                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # KONSOL ÖZET
    print("=== TÜM REJİMLER (hit% / medyan%) ===")
    for name, pred in variants:
        print(f"  {name:20} " + _line(_variant(events, name, pred)))
    print("\n=== ONAYLI vs FAILED (V4, giriş=kesiş günü — teşhis) ===")
    for lbl, want in [("ONAYLI", True), ("FAILED", False)]:
        print(f"  {lbl:20} " + _line([e for e in v4 if e['held'] is want]))
    print("\n=== ONAYLI — GERÇEKÇİ giriş (onay günü t+2, ileri-bakış YOK) ===")
    print(f"  {'ONAYLI t+2 giriş':20} " + _line(conf_rows))
    print(f"\nBASELINE T+1/T+5/T+20 medyan: {base[1][0]:+.2f}% / {base[5][0]:+.2f}% / {base[20][0]:+.2f}%")
    print("\nRapor: backtest_stp_uyanis.md · Ham: backtest_stp_uyanis.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
