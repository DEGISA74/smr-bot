# -*- coding: utf-8 -*-
"""
settle_report.py — OTURMA ZAMANI ANALİZİ (settle_probe.csv üzerinden)
====================================================================
Her (gün, hisse) için İsyatirim kapanışının NE ZAMAN stabilize olduğunu bulur
(= o saatten sonra bir daha değişmediği ilk poll). Günler arası birleştirip
KESİN "güvenli oturma saati" verir → settle-pass görevini oraya koyarız.

Kullanım: python settle_report.py   (istediğin zaman çalıştır; Temmuz sonu kesin)
"""
import sys, os, csv
from collections import defaultdict
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

LOG = os.path.join("logs", "settle_probe.csv")

def _to_min(hms):   # "HH:MM:SS" → dakika
    h, m, *_ = hms.split(":")
    return int(h) * 60 + int(m)

def main():
    if not os.path.exists(LOG):
        print("Henüz veri yok:", LOG); return
    # (date,ticker) -> list of (min, close)   ·   ayrı seri: İsyatirim ve borsapy
    series = defaultdict(list)          # İsyatirim
    series_bp = defaultdict(list)       # borsapy
    dates = set()
    with open(LOG, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["date"], row["ticker"])
            mn = None
            if row.get("isy_close"):
                try:
                    mn = _to_min(row["poll_time"])
                    series[key].append((mn, float(row["isy_close"])))
                    dates.add(row["date"])
                except Exception:
                    pass
            if row.get("borsapy_close"):
                try:
                    series_bp[key].append((_to_min(row["poll_time"]), float(row["borsapy_close"])))
                except Exception:
                    pass

    # her (gün,hisse): stabilize dakikası = son değişimden SONRAKİ ilk poll
    stab = defaultdict(list)   # ticker -> [stabilize_min...]
    day_stab = defaultdict(list)  # date -> [stabilize_min per ticker]
    for (d, tk), pts in series.items():
        pts.sort()
        if len(pts) < 2:
            continue
        final = pts[-1][1]
        # en son 'final'e eşit olmayan poll'un ZAMANI → ondan sonraki ilk poll = oturma
        last_change_idx = 0
        for i, (mn, c) in enumerate(pts):
            if abs(c - final) > 1e-6:
                last_change_idx = i
        # oturma = last_change'den sonraki ilk poll (yoksa ilk poll = baştan stabil)
        if last_change_idx + 1 < len(pts):
            settle_min = pts[last_change_idx + 1][0]
        else:
            settle_min = pts[-1][0]
        if abs(pts[0][1] - final) < 1e-6:
            settle_min = pts[0][0]   # baştan beri finaldeydi
        stab[tk].append(settle_min)
        day_stab[d].append(settle_min)

    def _hhmm(mn): return f"{mn//60:02d}:{mn%60:02d}"

    print("=" * 60)
    print(f"OTURMA ZAMANI RAPORU · ölçülen iş günü: {len(dates)}  ({min(dates) if dates else '-'}..{max(dates) if dates else '-'})")
    print("=" * 60)
    if len(dates) < 20:
        print(f"⚠️  Henüz {len(dates)} gün — kesin sonuç için ≥20 gün gerek (Temmuz sonu).\n")

    print("HİSSE BAZINDA oturma saati (medyan · en geç):")
    for tk in sorted(stab):
        v = sorted(stab[tk])
        med = v[len(v)//2]; mx = v[-1]
        print(f"  {tk:<7} n={len(v):<3} medyan {_hhmm(med)}  ·  en geç {_hhmm(mx)}")

    # GÜNLÜK: o gün TÜM sepetin oturduğu an = günün max'ı
    print("\nGÜNLÜK 'hepsi oturdu' saati (o günün en geç hissesi):")
    day_all = {}
    for d in sorted(day_stab):
        mx = max(day_stab[d])
        day_all[d] = mx
        print(f"  {d}: {_hhmm(mx)}")

    if day_all:
        allv = sorted(day_all.values())
        med_all = allv[len(allv)//2]
        p95 = allv[min(len(allv)-1, int(len(allv)*0.95))]
        mx_all = allv[-1]
        print("\n" + "=" * 60)
        print("KESİN SONUÇ (tüm sepet, günler arası):")
        print(f"  medyan gün: {_hhmm(med_all)}  ·  %95 gün: {_hhmm(p95)}  ·  en kötü gün: {_hhmm(mx_all)}")
        print(f"  → ÖNERİLEN settle-pass saati: {_hhmm(mx_all + 10)} (en kötü + 10dk güvenlik payı)")
        print("=" * 60)

    # ── 3-KAYNAK UYUM + borsapy oturma karşılaştırması ──
    def _final(pts): return pts[-1][1] if pts else None
    def _settle_min(pts, final):
        if not pts or final is None: return None
        pts = sorted(pts); lc = 0
        for i, (m, c) in enumerate(pts):
            if abs(c - final) > 1e-6: lc = i
        if abs(pts[0][1] - final) < 1e-6: return pts[0][0]
        return pts[lc + 1][0] if lc + 1 < len(pts) else pts[-1][0]
    agree = disagree = earlier = later = same = 0
    bp_days = set()
    for key in series:
        if key not in series_bp: continue
        fi, fb = _final(series[key]), _final(series_bp[key])
        if fi is None or fb is None: continue
        bp_days.add(key[0])
        if abs(fi - fb) <= max(0.02, abs(fi) * 0.002): agree += 1
        else: disagree += 1
        si, sb = _settle_min(series[key], fi), _settle_min(series_bp[key], fb)
        if si is not None and sb is not None:
            if sb < si - 2: earlier += 1
            elif sb > si + 2: later += 1
            else: same += 1
    if agree + disagree:
        print("\n" + "=" * 60)
        print("3-KAYNAK — borsapy vs İsyatirim (nihai kapanış uyumu):")
        print(f"  UYUMLU: {agree}/{agree + disagree} (%{100 * agree / (agree + disagree):.0f}) · ölçülen gün: {len(bp_days)}")
        print(f"  borsapy oturma: {earlier} ERKEN · {same} aynı · {later} GEÇ (İsyatirim'e göre)")
        print("  → borsapy hem UYUMLU hem ERKEN ise: konsensüs + erken-kapanış kaynağı olarak entegre et.")
        print("=" * 60)

if __name__ == "__main__":
    main()
