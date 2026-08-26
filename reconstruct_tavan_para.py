# -*- coding: utf-8 -*-
"""
reconstruct_tavan_para.py — Tavan + Para Akışı geçmiş sinyallerini parquet'lerden geri üretir.
=============================================================================================
19-29 Haz 2026 arası tavan_top30/tavan_alarm/para_akisi_lider HİÇ loglanmadı (NameError, def-sıra
bug'ı, 30 Haz düzeltildi). Parquet'lerde tüm tarih var → o günlerin sinyalleri geri üretilebilir.

YÖNTEM (sıfır sapma): app.py'deki GERÇEK skor fonksiyonlarını AST ile çıkar, exec et, sonra
pd.read_parquet'i "tarihe kadar kes" diye sarmalayıp orijinal _tav_compute_panel +
scan_para_akisi_liderleri'ni AYNEN çağır. "Son bar" mantığı doğal olarak "D günü" olur.

GÜVENLİK: önce bugünü (30 Haz) üret, canlı loglanan satırlarla karşılaştır. Tutarsa geçmişe yaz.

Kullanım:
  python reconstruct_tavan_para.py validate      # sadece bugünü doğrula, DB'ye YAZMAZ
  python reconstruct_tavan_para.py backfill       # 18-29 Haz geçmişi DB'ye yazar (onaydan sonra)
"""
import ast, sys, glob, os, math, sqlite3
import pandas as pd
import numpy as np
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

APP = "app.py"
DB = "patron.db"
VERILER = "veriler"

# ── 1) GERÇEK fonksiyonları app.py'den AST ile çıkar ───────────────────────────
_src = open(APP, encoding="utf-8").read()
_tree = ast.parse(_src)
_lines = _src.split("\n")
WANT_FUNCS = {"_tav_rsi","_tav_is_manipulated","_tav_features","_tav_score_A",
              "_tav_score_C","_tav_score_E","_tav_score_D","_tav_compute_panel",
              "_liquidity_manip","_apply_split_adjustments","scan_para_akisi_liderleri"}
WANT_ASSIGN = {"_TAV_REJIM_AGIRLIK"}
_segs = []
for n in _tree.body:
    if isinstance(n, ast.FunctionDef) and n.name in WANT_FUNCS:
        _segs.append((n.lineno, "\n".join(_lines[n.lineno-1:n.end_lineno])))
    elif isinstance(n, ast.Assign):
        tn = [t.id for t in n.targets if isinstance(t, ast.Name)]
        if any(t in WANT_ASSIGN for t in tn):
            _segs.append((n.lineno, "\n".join(_lines[n.lineno-1:n.end_lineno])))
_segs.sort()
_code = "\n\n".join(s for _, s in _segs)

CACHE_DIR = os.path.abspath(VERILER)
_NS = {"pd": pd, "np": np, "os": os, "glob": glob, "math": math, "CACHE_DIR": CACHE_DIR}
exec(_code, _NS)
print(f"[ok] {len(WANT_FUNCS)} fonksiyon + {len(WANT_ASSIGN)} sabit exec edildi")

# ── 2) Tarihe-kadar-kes sarmalayıcı + reconstruct ──────────────────────────────
_real_read = pd.read_parquet
def _sliced_reader(D):
    def r(path, *a, **k):
        df = _real_read(path, *a, **k)
        try:
            return df[df.index <= D]
        except Exception:
            return df
    return r

def _tickers():
    return [os.path.basename(f).replace(".IS_1d.parquet", "")
            for f in glob.glob(f"{VERILER}/*.IS_1d.parquet")]

def reconstruct(D):
    """D (str 'YYYY-MM-DD') gününe kadarki veriyle tavan + para df döner."""
    Dts = pd.Timestamp(D)
    pd.read_parquet = _sliced_reader(Dts)
    try:
        tav_df, rejim, chg, target = _NS["_tav_compute_panel"](_cache_key=f"recon_{D}")
        pal_df = _NS["scan_para_akisi_liderleri"](_tickers(), "BIST")
    finally:
        pd.read_parquet = _real_read
    return tav_df, pal_df, rejim


# ── 3) DOĞRULAMA: bugünü üret, canlı satırlarla karşılaştır ─────────────────────
def validate(D="2026-06-30"):
    tav, pal, rejim = reconstruct(D)
    print(f"\n=== RECON {D} (rejim={rejim}) ===")
    print(f"tavan: {len(tav)} satır | para: {len(pal)} satır")
    recon_top30 = tav.head(30)[["tk", "skor", "kat"]].copy()
    recon_top30["tk"] = recon_top30["tk"].str.upper()

    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("SELECT symbol,f_tavan_skor,f_tavan_kat FROM scan_signals "
                "WHERE scan_date=? AND scan_type='tavan_top30'", (D,))
    live = {r[0].replace(".IS", "").upper(): (r[1], r[2]) for r in cur.fetchall()}
    cur.execute("SELECT symbol FROM scan_signals WHERE scan_date=? AND scan_type='para_akisi_lider'", (D,))
    live_pal = {r[0].replace(".IS", "").upper() for r in cur.fetchall()}
    con.close()

    print(f"\n--- TAVAN TOP30 karşılaştırma (canlı {len(live)} satır) ---")
    match = miss = score_diff = 0
    for _, r in recon_top30.iterrows():
        tk = r["tk"]; rs = round(float(r["skor"]), 1)
        if tk in live:
            ls = round(float(live[tk][0]), 1) if live[tk][0] is not None else None
            ok = (ls is not None and abs(ls - rs) < 0.15)
            if ok: match += 1
            else: score_diff += 1; print(f"   SKOR FARK {tk}: recon {rs} vs canlı {ls}")
        else:
            miss += 1; print(f"   recon'da var canlıda YOK: {tk} ({rs})")
    print(f"   => eşleşen+skor-aynı: {match}/{len(recon_top30)} | skor farklı: {score_diff} | canlıda yok: {miss}")

    recon_pal = {str(s).replace(".IS", "").upper() for s in pal["Sembol"]} if not pal.empty else set()
    print(f"\n--- PARA AKIŞI karşılaştırma ---")
    print(f"   recon: {sorted(recon_pal)}")
    print(f"   canlı: {sorted(live_pal)}")
    print(f"   kesişim: {len(recon_pal & live_pal)}/{len(live_pal)}")

    ok = (match >= len(recon_top30) - 1 and len(recon_pal & live_pal) >= max(1, len(live_pal) - 1))
    print(f"\n{'[GEÇTİ] Reconstruction SADIK — geçmişe yazılabilir' if ok else '[DUR] Fark var, incele'}")
    return ok


# ── 4) BACKFILL: geçmiş günleri scan_signals'a yaz (INSERT OR IGNORE) ──────────
# Hedef pencere: tavan/para motorları eklendikten (18 Haz) sonra HİÇ loglanmayan günler.
# 30 Haz zaten canlı loglandı → hariç. INSERT OR IGNORE → tekrar çalışsa çift yazmaz.
BACKFILL_DATES = ["2026-06-18","2026-06-19","2026-06-22","2026-06-23",
                  "2026-06-24","2026-06-25","2026-06-26","2026-06-29"]
CATEGORY = "BIST 500"

def _rows_for_date(D):
    """D günü için (scan_type, symbol, entry_price, score, f_skor, f_kat, f_conf) listesi."""
    tav, pal, rejim = reconstruct(D)
    out = []
    if tav is not None and not tav.empty:
        top30 = tav.head(30)
        alarm = tav[tav["skor"] >= 150]
        for _, r in top30.iterrows():
            out.append(("tavan_top30", str(r["tk"]).upper(), float(r["fiyat"]),
                        round(float(r["skor"]),1), round(float(r["skor"]),1), str(r["kat"]),
                        int(r["confluence_n"]) if pd.notna(r["confluence_n"]) else None))
        for _, r in alarm.iterrows():
            out.append(("tavan_alarm", str(r["tk"]).upper(), float(r["fiyat"]),
                        round(float(r["skor"]),1), round(float(r["skor"]),1), str(r["kat"]),
                        int(r["confluence_n"]) if pd.notna(r["confluence_n"]) else None))
    if pal is not None and not pal.empty:
        for _, r in pal.iterrows():
            out.append(("para_akisi_lider", str(r["Sembol"]).upper(), float(r["Fiyat"]),
                        float(r["Skor"]), None, None, None))
    return out, rejim

def dryrun():
    print("\n=== KURU ÇALIŞMA (yazma YOK) — günlük üretim sayıları ===")
    tot = 0
    for D in BACKFILL_DATES:
        rows, rejim = _rows_for_date(D)
        from collections import Counter
        c = Counter(r[0] for r in rows)
        tot += len(rows)
        print(f"  {D} (rejim={rejim}): top30={c.get('tavan_top30',0)} "
              f"alarm={c.get('tavan_alarm',0)} para={c.get('para_akisi_lider',0)}  toplam={len(rows)}")
    print(f"\nGENEL TOPLAM yazılacak satır: {tot} ({len(BACKFILL_DATES)} gün)")

def backfill():
    con = sqlite3.connect(DB); cur = con.cursor()
    written = 0
    for D in BACKFILL_DATES:
        rows, rejim = _rows_for_date(D)
        for scan_type, sym, price, score, fskor, fkat, fconf in rows:
            cur.execute(
                """INSERT OR IGNORE INTO scan_signals
                   (scan_date, symbol, scan_type, score, bias, entry_price, stop_level,
                    category, obv_status, f_tavan_skor, f_tavan_kat, f_tavan_confluence_n)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (D, sym, scan_type, score, "bullish", price, None,
                 CATEGORY, None, fskor, fkat, fconf))
            written += cur.rowcount
    con.commit()
    print(f"[ok] backfill bitti — {written} yeni satır yazıldı")
    cur.execute("""SELECT scan_date,scan_type,COUNT(*) FROM scan_signals
                   WHERE scan_type IN ('tavan_top30','tavan_alarm','para_akisi_lider')
                   GROUP BY scan_date,scan_type ORDER BY scan_date""")
    print("\n=== scan_signals'taki tüm tavan/para satırları ===")
    for r in cur.fetchall(): print(f"   {r[0]} {r[1]}: {r[2]}")
    con.close()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "validate"
    if mode == "validate": validate()
    elif mode == "dryrun": dryrun()
    elif mode == "backfill": backfill()
    else: print("mod: validate | dryrun | backfill")
