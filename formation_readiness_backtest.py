"""Formasyonun olgunluk evrelerini, yapının süresine göre ileriye bakmadan ölçer."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

from formation_readiness import analyze_formation_readiness


BUCKETS = (
    ("STANDART", 10, 100, (5, 10, 20, 40)),
    ("GENIS", 101, 220, (5, 20, 40, 80)),
    ("BUYUK", 221, 360, (5, 30, 60, 120)),
)


def _bucket(duration: int):
    for name, low, high, horizons in BUCKETS:
        if low <= duration <= high:
            return name, horizons
    return None


def _one(path: Path, step: int) -> list[dict]:
    df = pd.read_parquet(path).sort_index()
    df.index = pd.to_datetime(df.index)
    ticker = path.name.replace(".IS_1d.parquet", "")
    rows, seen = [], set()
    for bar in range(70, len(df), step):
        history = df.iloc[:bar + 1]
        for item in analyze_formation_readiness(history):
            if item.alert not in {"ERKEN_BILDIRIM", "HAZIR", "ONAY_BEKLE"}:
                continue
            # Eski, çoktan uzamış kırılımı bugün yeni onay gibi sayma.
            if item.alert == "ONAY_BEKLE" and not (-2.0 <= item.distance_to_neckline_pct <= 0.0):
                continue
            start = pd.Timestamp(item.structure_start)
            duration = int(bar - df.index.searchsorted(start))
            selected = _bucket(duration)
            if not selected:
                continue
            bucket, horizons = selected
            key = (item.notification_key if hasattr(item, "notification_key") else item.as_dict()["notification_key"])
            if key in seen:
                continue
            seen.add(key)
            entry = float(df["Close"].iloc[bar])
            row = {"ticker": ticker, "time": str(df.index[bar])[:10], "pattern": item.pattern, "alert": item.alert, "bucket": bucket, "duration_bars": duration, "score": item.score, "distance_pct": item.distance_to_neckline_pct}
            for horizon in horizons:
                if bar + horizon >= len(df):
                    row[f"return_{horizon}"] = None
                    continue
                row[f"return_{horizon}"] = round((float(df["Close"].iloc[bar + horizon]) / entry - 1) * 100, 4)
            rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    paths = sorted(p for p in (Path(__file__).parent / "veriler").glob("*.IS_1d.parquet") if not p.name.startswith("XU"))[:args.limit]
    rows, failures = [], []
    for path in paths:
        try:
            rows.extend(_one(path, args.step))
        except Exception as exc:
            failures.append({"file": path.name, "reason": str(exc)})
    groups = defaultdict(list)
    for row in rows:
        groups[(row["bucket"], row["alert"])].append(row)
    summary = {}
    for key, values in groups.items():
        outcomes = {name: [r[name] for r in values if r.get(name) is not None] for name in sorted({k for r in values for k in r if k.startswith("return_")})}
        summary["/".join(key)] = {"signals": len(values), **{name: {"avg": round(sum(v)/len(v),4), "win_rate": round(sum(x>0 for x in v)/len(v)*100,2)} for name,v in outcomes.items() if v}}
    args.output.write_text(json.dumps({"buckets": BUCKETS, "files": len(paths), "step": args.step, "lookahead_guard": "Her evrede yalnız o güne kadarki mumlar kullanıldı.", "summary": summary, "failures": failures, "signals": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
