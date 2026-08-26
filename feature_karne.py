# -*- coding: utf-8 -*-
"""
feature_karne.py — FEATURE BAZINDA KANIT KARNESİ
================================================
Tek kâhin: scan_signals (62 feature flag) × signal_results (ret/hit) JOIN.

Mevcut backtest paneli SCANNER bazında ölçüyor (scan_type). Bu araç
her bir `f_*` FEATURE'ı tek tek ölçer: hangi feature değeri kazandırıyor,
hangisi ölü/gürültü.

Doktrin (CLAUDE.md): ölçülemeyen flag AI'a kalıcı girmez; ret<=0 olan budanır.

Çalıştır:  python feature_karne.py
Çıktı:     konsol özeti + feature_karne_report.md
"""
import sqlite3
import sys
import datetime as _dt

try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows konsol Türkçe/ok karakteri
except Exception:
    pass

DB = "patron.db"
MIN_BUCKET_N = 30      # bir kovanın "anlamlı" sayılması için min sinyal
MIN_FEATURE_N = 150    # bir feature'ın hiç ölçülmesi için min toplam dolu satır
HORIZON = "ret_10g"    # ana ufuk (5/10/20 hepsi raporlanır)


def _pf(returns):
    """Profit factor: pozitif getiri toplamı / |negatif getiri toplamı|."""
    pos = sum(r for r in returns if r > 0)
    neg = sum(-r for r in returns if r < 0)
    if neg == 0:
        return float("inf") if pos > 0 else 0.0
    return pos / neg


def _classify(con, feat):
    """Feature tipini ve dolu satır sayısını döndür."""
    cur = con.cursor()
    cur.execute(f"SELECT COUNT(*) FROM scan_signals WHERE {feat} IS NOT NULL")
    n = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(DISTINCT {feat}) FROM scan_signals WHERE {feat} IS NOT NULL")
    distinct = cur.fetchone()[0]
    # numerik mi metin mi?
    cur.execute(f"SELECT typeof({feat}) t, COUNT(*) c FROM scan_signals "
                f"WHERE {feat} IS NOT NULL GROUP BY t ORDER BY c DESC LIMIT 1")
    row = cur.fetchone()
    typ = row[0] if row else "null"
    is_num = typ in ("integer", "real")
    return n, distinct, is_num


def _buckets_for(con, feat, is_num, distinct):
    """Bu feature için SQL CASE kova ifadesi üret (kova_adi etiketli)."""
    if not is_num:
        # metin kategori → ham değer
        return f"CAST({feat} AS TEXT)"
    if distinct <= 6:
        # binary / az-değerli integer → ham değer
        return f"CAST({feat} AS TEXT)"
    # sürekli numerik → çeyreklik benzeri sabit dilim (feature'a göre uyarlanır)
    # genel amaçlı 5 dilim: değer dağılımından dinamik eşik
    cur = con.cursor()
    qs = []
    for q in (0.2, 0.4, 0.6, 0.8):
        cur.execute(
            f"SELECT {feat} FROM scan_signals WHERE {feat} IS NOT NULL "
            f"ORDER BY {feat} LIMIT 1 OFFSET "
            f"(SELECT CAST(COUNT(*)*{q} AS INT) FROM scan_signals WHERE {feat} IS NOT NULL)")
        r = cur.fetchone()
        qs.append(r[0] if r else None)
    qs = [x for x in qs if x is not None]
    qs = sorted(set(qs))
    if len(qs) < 2:
        return f"CAST({feat} AS TEXT)"
    # CASE ifadesi
    parts = []
    for i, edge in enumerate(qs):
        parts.append(f"WHEN {feat} < {edge} THEN '{i}_<{round(edge,2)}'")
    parts.append(f"ELSE '{len(qs)}_>={round(qs[-1],2)}'")
    return "CASE " + " ".join(parts) + " END"


def measure_feature(con, feat):
    """Bir feature için kova karnesi + ayrım gücü döndür."""
    n, distinct, is_num = _classify(con, feat)
    if n < MIN_FEATURE_N:
        return {"feat": feat, "n": n, "verdict": "AZ VERİ", "buckets": []}
    if distinct <= 1:
        return {"feat": feat, "n": n, "verdict": "ÖLÜ (tek değer)", "buckets": []}

    bucket_expr = _buckets_for(con, feat, is_num, distinct)
    cur = con.cursor()
    cur.execute(f"""
        SELECT {bucket_expr} AS kova,
               COUNT(*) n,
               ROUND(AVG(r.hit_5g)*100,1)  h5,
               ROUND(AVG(r.hit_10g)*100,1) h10,
               ROUND(AVG(r.hit_20g)*100,1) h20,
               ROUND(AVG(r.ret_5g),2)  r5,
               ROUND(AVG(r.ret_10g),2) r10,
               ROUND(AVG(r.ret_20g),2) r20
        FROM scan_signals s JOIN signal_results r ON r.signal_id = s.id
        WHERE r.ret_10g IS NOT NULL AND {feat.replace(feat, 's.'+feat)} IS NOT NULL
        GROUP BY kova
        ORDER BY kova
    """.replace("s.s.", "s."))
    rows = cur.fetchall()

    # her kova için PF (ayrı sorgu, ret_10g listesi)
    buckets = []
    for kova, bn, h5, h10, h20, r5, r10, r20 in rows:
        buckets.append({
            "kova": kova, "n": bn, "h5": h5, "h10": h10, "h20": h20,
            "r5": r5, "r10": r10, "r20": r20,
        })

    # ayrım gücü: anlamlı kovalar arası ret_10g yayılımı
    sig = [b for b in buckets if b["n"] >= MIN_BUCKET_N and b["r10"] is not None]
    if len(sig) >= 2:
        rmax = max(b["r10"] for b in sig)
        rmin = min(b["r10"] for b in sig)
        spread = round(rmax - rmin, 2)
        best = max(sig, key=lambda b: b["r10"])
        worst = min(sig, key=lambda b: b["r10"])
    else:
        spread, best, worst = 0.0, None, None

    if spread >= 2.5:
        verdict = "GÜÇLÜ AYRIM"
    elif spread >= 1.0:
        verdict = "ORTA AYRIM"
    elif len(sig) >= 2:
        verdict = "ZAYIF (ayrım yok)"
    else:
        verdict = "AZ VERİ (kova<30)"

    return {"feat": feat, "n": n, "distinct": distinct, "is_num": is_num,
            "verdict": verdict, "spread": spread, "best": best, "worst": worst,
            "buckets": buckets}


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # feature listesi
    cur.execute("PRAGMA table_info(scan_signals)")
    feats = [c[1] for c in cur.fetchall() if c[1].startswith("f_")]

    # baseline
    cur.execute("""SELECT COUNT(*), ROUND(AVG(hit_10g)*100,1), ROUND(AVG(ret_10g),2),
                          ROUND(AVG(ret_5g),2), ROUND(AVG(ret_20g),2)
                   FROM signal_results WHERE ret_10g IS NOT NULL""")
    base_n, base_h10, base_r10, base_r5, base_r20 = cur.fetchone()

    results = [measure_feature(con, f) for f in feats]
    # ayrım gücüne göre sırala
    def _key(r):
        return r.get("spread", 0.0) or 0.0
    ranked = sorted(results, key=_key, reverse=True)

    # ---- MARKDOWN RAPOR ----
    out = []
    out.append(f"# 📊 FEATURE KANIT KARNESİ")
    out.append(f"_Üretim: {_dt.date.today()} · JOIN: scan_signals × signal_results_\n")
    out.append(f"**Baseline (tüm sinyaller):** n={base_n} · "
               f"isabet_10g %{base_h10} · ret_5g {base_r5} · "
               f"**ret_10g {base_r10}** · ret_20g {base_r20}\n")
    out.append("Bir feature'ın değeri: kovaları baseline'dan ne kadar **ayırıyor**. "
               "`spread` = anlamlı kovalar (n≥30) arası 10g getiri farkı.\n")

    # özet tablo
    out.append("## Sıralı Özet (ayrım gücüne göre)\n")
    out.append("| Feature | Dolu n | Yayılım(10g) | En iyi kova | En kötü kova | Karne |")
    out.append("|---|--:|--:|---|---|---|")
    for r in ranked:
        if not r["buckets"]:
            out.append(f"| `{r['feat']}` | {r['n']} | — | — | — | **{r['verdict']}** |")
            continue
        b, w = r.get("best"), r.get("worst")
        bs = f"{b['kova']} (h%{b['h10']}/{b['r10']:+})" if b else "—"
        ws = f"{w['kova']} ({w['r10']:+})" if w else "—"
        out.append(f"| `{r['feat']}` | {r['n']} | **{r.get('spread',0):+}** | "
                   f"{bs} | {ws} | {r['verdict']} |")

    # detay: sadece GÜÇLÜ/ORTA ayrım gösterenler
    out.append("\n## Detay — ayrım gösteren feature'lar\n")
    for r in ranked:
        if r["verdict"] not in ("GÜÇLÜ AYRIM", "ORTA AYRIM"):
            continue
        out.append(f"### `{r['feat']}` — {r['verdict']} (yayılım {r['spread']:+})\n")
        out.append("| Kova | n | h5% | h10% | h20% | ret5 | ret10 | ret20 |")
        out.append("|---|--:|--:|--:|--:|--:|--:|--:|")
        for b in r["buckets"]:
            flag = " 🔴" if (b["r10"] is not None and b["r10"] <= 0) else ""
            out.append(f"| {b['kova']} | {b['n']} | {b['h5']} | {b['h10']} | "
                       f"{b['h20']} | {b['r5']} | **{b['r10']}**{flag} | {b['r20']} |")
        out.append("")

    # ölü / az veri listesi
    dead = [r for r in ranked if r["verdict"].startswith(("ÖLÜ", "AZ VERİ"))]
    if dead:
        out.append("## ⚰️ Ölü / Ölçülemez (temizlik adayı — #2 ile çapraz kontrol et)\n")
        for r in dead:
            out.append(f"- `{r['feat']}` — n={r['n']} — **{r['verdict']}**")

    report = "\n".join(out)
    with open("feature_karne_report.md", "w", encoding="utf-8") as f:
        f.write(report)

    # ---- KONSOL ÖZET ----
    print(f"Baseline: n={base_n} · isabet_10g %{base_h10} · ret_10g {base_r10}\n")
    print(f"{'FEATURE':<26}{'n':>7}{'yayılım':>9}  karne")
    print("-" * 70)
    for r in ranked:
        sp = r.get("spread", 0.0) or 0.0
        print(f"{r['feat']:<26}{r['n']:>7}{sp:>+9.2f}  {r['verdict']}")
    print(f"\n→ Tam rapor: feature_karne_report.md")
    con.close()


if __name__ == "__main__":
    main()
