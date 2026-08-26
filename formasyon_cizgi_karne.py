#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FAZ 0 — ÇİZGİ KARNESİ (formasyon_v2 hat kalitesi denetimi)
==========================================================
AMAÇ: Motorun grafiğe çizdiği HATLARIN (boyun çizgisi, fincan ağzı,
üst/alt sınır) geometrik kalitesini BAĞIMSIZ ölç. Motorun kendi
quality_score'una GÜVENMEZ — pivotları kendisi çıkarır, çizginin kaç
gerçek dönüş noktasına değdiğini ve ne kadar eğimli olduğunu kendi sayar.

KULLANICI ETİKETLİ ALTIN SET (24 Tem 2026 anlık kareleri):
  DAGI/AKSA/FORTE   -> MÜKEMMEL fincan-kulp boyun çizgisi (yatay)
  AKGRT/YKSLN       -> MÜKEMMEL çizilmiş üçgen/kama sınır hatları
  AKMGY             -> iyi çizgi
  FZLGY/ARTMS/BORLS/DCTTR -> BERBAT boyun (dik çapraz; olsa olsa kama kenarı)
      => motor bu berbat boynu ÜRETMEMELİ (OBO 27 Tem'de zaten söküldü).

Veriyi resim tarihine (24 Tem) keser ki resimlerdeki formasyonu birebir
üretsin. Bugünkü veri fiyat ilerlediği için formasyonlar oynanmış olur.

ÇIKTI:
  * formasyon_cizgi_karne_raporu.md  (insan-okur karne tablosu)
  * formasyon_cizgi_karne.json       (ham metrikler)
  * --render verilirse: _etiket_tobo/karne_render/*.png (göz denetimi)

Sadece OKUR; canlı sisteme/DB'ye YAZMAZ. Motoru DEĞİŞTİRMEZ.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
try:  # Windows konsolu cp1254 — Türkçe/unicode çıktı için utf-8'e sabitle
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import formasyon_v2 as fv

ROOT = Path(__file__).resolve().parent
CUTOFF = "2026-07-24"  # altın-set resimlerinin anlık kare tarihi

# --- KARNE EŞİKLERİ (gerekçe: teşhis notu — "güzel çizgi = 3+ temas + role uygun eğim") ---
NECK_FLAT_MAX_DRIFT_PCT = 4.0   # boyun/fincan ağzı: uçtan uca en fazla %4 kayabilir (yatay sayılır)
SLOPED_MIN_DRIFT_PCT = 5.0      # üçgen/kamanın eğimli kenarı: en az %5 gerçek eğim
FLAT_SIDE_MAX_DRIFT_PCT = 4.0   # üçgenin yatay kenarı: en fazla %4
TOUCH_TOL_ATR = 1.20            # pivot çizgiye bu kadar ATR içindeyse "değdi"
TOUCH_TOL_PCT = 1.5             # ya da fiyatın %1.5'i (ikisinin büyüğü)
MIN_NECK_TOUCH_TOBO = 3         # TOBO/OBO boynu en az 3 temas ister
MIN_NECK_TOUCH_CUP = 2          # fincan ağzı iki dudak (sol+sağ) ile tanımlı

NECK_ROLES = {"fincan_ağzı", "boyun_çizgisi"}
UPPER_ROLES = {"üst_sınır"}
LOWER_ROLES = {"alt_sınır"}

# ticker -> (insan etiketi, beklenen davranış tipi)
#   POZITIF_NECK  : yatay boyun/fincan ağzı çizmeli, hat PASS olmalı
#   POZITIF_LINE  : üçgen/kama sınır hatları çizmeli, iki hat da PASS
#   NEGATIF_NECK  : berbat boyun; motor bunu ÇİZMEMELİ (boş ya da PASS-hat)
GOLDEN: dict[str, dict[str, str]] = {
    "DAGI":  {"verdict": "MÜKEMMEL fincan boyun", "kind": "POZITIF_NECK"},
    "AKSA":  {"verdict": "MÜKEMMEL fincan boyun", "kind": "POZITIF_NECK"},
    "FORTE": {"verdict": "MÜKEMMEL fincan boyun", "kind": "POZITIF_NECK"},
    "AKGRT": {"verdict": "MÜKEMMEL üçgen/kama çizgi", "kind": "POZITIF_LINE"},
    "YKSLN": {"verdict": "MÜKEMMEL üçgen/kama çizgi", "kind": "POZITIF_LINE"},
    "AKMGY": {"verdict": "iyi üçgen/kama çizgi", "kind": "POZITIF_LINE"},
    "FZLGY": {"verdict": "BERBAT OBO boyun (dik çapraz)", "kind": "NEGATIF_NECK"},
    "ARTMS": {"verdict": "BERBAT OBO boyun (dik çapraz)", "kind": "NEGATIF_NECK"},
    "BORLS": {"verdict": "BERBAT OBO boyun (dik çapraz)", "kind": "NEGATIF_NECK"},
    "DCTTR": {"verdict": "BERBAT OBO boyun (dik çapraz)", "kind": "NEGATIF_NECK"},
}


def _load_cut(ticker: str) -> Optional[pd.DataFrame]:
    path = ROOT / "veriler" / f"{ticker}.IS_1d.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    return df[df.index <= CUTOFF]


def _line_price_at(line: fv.PatternLine, bar: int) -> float:
    if line.end_bar == line.start_bar:
        return float(line.start_price)
    t = (bar - line.start_bar) / (line.end_bar - line.start_bar)
    return float(line.start_price + (line.end_price - line.start_price) * t)


def _drift_pct(line: fv.PatternLine) -> float:
    """Hattın uçtan uca toplam eğimi (yüzde). Yatay ~0, dik çapraz büyük."""
    mid = (abs(line.start_price) + abs(line.end_price)) / 2 or 1e-9
    return abs(line.end_price - line.start_price) / mid * 100.0


def _independent_touches(
    line: fv.PatternLine, pivots: list, atr: float, kind: str
) -> int:
    """Çizgiye BAĞIMSIZ dokunan gerçek dönüş noktası sayısı.
    kind='H' -> tepe pivotları (direnç/boyun/fincan ağzı), 'L' -> dip pivotları."""
    tol = max(atr * TOUCH_TOL_ATR, (abs(line.start_price) or 1e-9) * TOUCH_TOL_PCT / 100.0)
    count = 0
    for p in pivots:
        if p.kind != kind:
            continue
        if not (line.start_bar - 2 <= p.bar <= line.end_bar + 2):
            continue
        if abs(p.price - _line_price_at(line, p.bar)) <= tol:
            count += 1
    return count


def _grade_line(
    line: fv.PatternLine, pivots: list, atr: float
) -> dict[str, Any]:
    drift = _drift_pct(line)
    role = line.role
    if role in NECK_ROLES:
        kind = "H"  # boyun/fincan ağzı üst dönüşlere oturur
        touches = _independent_touches(line, pivots, atr, kind)
        min_touch = MIN_NECK_TOUCH_CUP if role == "fincan_ağzı" else MIN_NECK_TOUCH_TOBO
        flat_ok = drift <= NECK_FLAT_MAX_DRIFT_PCT
        touch_ok = touches >= min_touch
        passed = flat_ok and touch_ok
        why = []
        if not flat_ok:
            why.append(f"dik ({drift:.1f}%>{NECK_FLAT_MAX_DRIFT_PCT}%) — boyun değil, kama kenarı")
        if not touch_ok:
            why.append(f"az temas ({touches}<{min_touch})")
        return {"role": role, "drift_pct": round(drift, 2), "touches": touches,
                "min_touch": min_touch, "pass": passed, "why": "; ".join(why)}
    # üçgen/kama sınırları
    kind = "H" if role in UPPER_ROLES else "L"
    touches = _independent_touches(line, pivots, atr, kind)
    touch_ok = touches >= 3
    passed = touch_ok  # eğim yön sınıflaması ayrı raporlanır, PASS temas sayısına bağlı
    return {"role": role, "drift_pct": round(drift, 2), "touches": touches,
            "min_touch": 3, "pass": passed,
            "why": "" if touch_ok else f"az temas ({touches}<3)"}


def _grade_ticker(ticker: str, meta: dict[str, str]) -> dict[str, Any]:
    cut = _load_cut(ticker)
    if cut is None or cut.empty:
        return {"ticker": ticker, **meta, "error": "veri yok"}
    rep = fv.analyze_formations(cut, ticker=ticker, timeframe="1d")
    clean, _, ok = fv._clean_frame(cut, "1d")
    pivots = fv._extract_pivots(clean, "1d") if ok else []
    atr_s = fv._atr(clean) if ok else pd.Series([0.0])
    atr = fv._finite(atr_s.iloc[-1], 0.0) if len(atr_s) else 0.0

    top = rep.patterns[0] if rep.patterns else None
    result: dict[str, Any] = {
        "ticker": ticker,
        "verdict_insan": meta["verdict"],
        "kind": meta["kind"],
        "engine_pattern": top.pattern if top else None,
        "engine_stage": top.stage if top else None,
        "engine_score": round(top.quality_score, 1) if top else None,
        "lines": [],
    }

    if top is not None:
        for ln in top.lines:
            result["lines"].append(_grade_line(ln, pivots, atr))

    # --- MOTOR ↔ İNSAN uyumu ---
    if meta["kind"] == "NEGATIF_NECK":
        # motor berbat boyun çizmemeli: ya formasyon yok, ya çizdiği boyun PASS
        bad_neck = any(
            (l["role"] in NECK_ROLES and not l["pass"]) for l in result["lines"]
        )
        result["agree"] = not bad_neck
        result["agree_why"] = ("motor berbat boyun çizmedi (doğru)"
                               if not bad_neck else "motor hâlâ dik boyun çiziyor")
    else:
        # pozitif: beklenen aile + tüm hatlar PASS olmalı
        has_lines = bool(result["lines"])
        all_pass = has_lines and all(l["pass"] for l in result["lines"])
        result["agree"] = all_pass
        if not has_lines:
            result["agree_why"] = "motor hiç hat çizmedi (formasyonu kaçırdı)"
        elif all_pass:
            result["agree_why"] = "tüm hatlar geometri sınavını geçti"
        else:
            fails = [f"{l['role']}: {l['why']}" for l in result["lines"] if not l["pass"]]
            result["agree_why"] = "zayıf hat -> " + " | ".join(fails)
    return result


def _render_all(rows: list[dict[str, Any]]) -> list[str]:
    out_dir = ROOT / "_etiket_tobo" / "karne_render"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for r in rows:
        t = r["ticker"]
        cut = _load_cut(t)
        if cut is None or cut.empty:
            continue
        rep = fv.analyze_formations(cut, ticker=t, timeframe="1d")
        if not rep.patterns:
            continue
        try:
            for p in fv.render_report_charts(cut, rep, out_dir):
                paths.append(str(p))
        except Exception as exc:  # render kırılganlığı karneyi bozmasın
            print(f"[render atlandı] {t}: {type(exc).__name__}: {exc}")
    return paths


def _write_report(rows: list[dict[str, Any]]) -> None:
    lines = ["# Formasyon Çizgi Karnesi (Faz 0)", "",
             f"Kesim tarihi: **{CUTOFF}** · Motor: `{fv.ENGINE_VERSION}`", ""]
    n_agree = sum(1 for r in rows if r.get("agree"))
    lines.append(f"**Motor ↔ İnsan uyumu: {n_agree}/{len(rows)}**")
    lines.append("")
    lines.append("| Hisse | İnsan etiketi | Motor | Aşama | Skor | Hatlar (rol · eğim% · temas · geç/kal) | Uyum |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in rows:
        if r.get("error"):
            lines.append(f"| {r['ticker']} | {r.get('verdict_insan','?')} | HATA | | | {r['error']} | — |")
            continue
        hat = "<br>".join(
            f"{l['role']} · {l['drift_pct']}% · {l['touches']}t · "
            f"{'✅' if l['pass'] else '❌ ' + l['why']}"
            for l in r["lines"]
        ) or "(hat yok)"
        uyum = "✅" if r.get("agree") else "❌"
        lines.append(
            f"| **{r['ticker']}** | {r['verdict_insan']} | {r['engine_pattern'] or '—'} | "
            f"{r['engine_stage'] or '—'} | {r['engine_score'] or '—'} | {hat} | {uyum} {r.get('agree_why','')} |"
        )
    (ROOT / "formasyon_cizgi_karne_raporu.md").write_text("\n".join(lines), encoding="utf-8")


BASELINE_AGREE = 10  # 24 Tem altın-set: 10/10. Faz 2'de bu düşerse regresyon var.


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Formasyon çizgi kalitesi karnesi (Faz 0)")
    ap.add_argument("--render", action="store_true", help="Göz denetimi için PNG üret")
    ap.add_argument("--gate", action="store_true",
                    help="Nöbetçi modu: uyum baz değerin (%d) altına düşerse exit 1" % BASELINE_AGREE)
    args = ap.parse_args(argv)

    rows = [_grade_ticker(t, meta) for t, meta in GOLDEN.items()]
    (ROOT / "formasyon_cizgi_karne.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    _write_report(rows)

    n_agree = sum(1 for r in rows if r.get("agree"))
    print(f"KARNE: motor↔insan uyumu {n_agree}/{len(rows)}")
    for r in rows:
        mark = "OK " if r.get("agree") else "XX "
        print(f"  {mark}{r['ticker']:6} {str(r.get('engine_pattern')):14} "
              f"-> {r.get('agree_why','')}")
    if args.render:
        paths = _render_all(rows)
        print(f"RENDER: {len(paths)} PNG -> _etiket_tobo/karne_render/")

    if args.gate and n_agree < BASELINE_AGREE:
        print(f"❌ REGRESYON: uyum {n_agree} < baz {BASELINE_AGREE} — çizgi kalitesi bozuldu!")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
