# -*- coding: utf-8 -*-
"""
scanner_edge_walkforward.py  —  SALT RAPOR aracı (patron.db'yi/app.py'yi DEĞİŞTİRMEZ)

Amaç: her taramanın walk-forward (out-of-sample) kenarını ölçmek.
Codex + Claude mutabakat şartları (hepsi bu dosyada uygulanır):
  * TÜM tarama tipleri analiz edilir; "çekirdek" liste koda gömülmez.
  * Gözlem birimi = benzersiz (symbol, signal_date).
  * Tercih edilen vade YALNIZCA TRAIN'den seçilir, TEST'te sabit tutulur.
  * 5/10/20 günlük sonuçların TAMAMI raporlanır.
  * Üç getiri metriği AYRI: (a) mutlak getiri, (b) BIST100 alpha, (c) günlük evren farkı.
  * Kaynak yalnızca patron.db + XU100 parquet; backtest_results.json KULLANILMAZ.
  * Stop/hedef verisi yok => R multiple / planlanan R:R HESAPLANMAZ.
  * Win rate, payoff ratio, profit factor aynı vade üzerinden.
  * Küçük örneklem ham alpha ile sıralanmaz => shrink (n/(n+K)) + bootstrap CI alt sınırı.
  * Shrink sabiti (K) A PRIORI sabit; TEST sonucuna göre AYARLANMAZ.
  * >=3 OOS penceresi hedeflenir; yoksa "KANIT YETERSIZ" etiketlenir.
  * Pozitif taramalar "kanıtlı" DEĞİL => "OOS pozitif / DOGRULANMAMIS".
  * Crowding puana girmez => ayrı risk etiketi.
  * Çıktı yalnız rapor: konsol + JSON + CSV. Hiçbir tabloya/koda yazmaz.
"""
import sqlite3, json, sys
import numpy as np
import pandas as pd

# ---- sabitler (A PRIORI, test sonucundan bagimsiz) ----
DB      = r"C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal\patron.db"
XU100   = r"C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal\veriler\XU100.IS_1d.parquet"
OUT_DIR = r"C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal\gelişmiş tarama"
OFFSETS   = [5, 10, 20]
SHRINK_K  = 50      # örneklem shrink prior gücü (sabit, TEST'e göre ayarlanmaz)
MIN_N     = 20      # bir hücre için asgari örneklem; altı => KANIT YETERSIZ
BOOT      = 1000
SEED      = 7
NOISE     = {"radar1"}   # tüm evreni işaretler => crowding SAYIMINDA haric (raporda belirtilir)
np.random.seed(SEED)


def log(*a): print(*a); sys.stdout.flush()


# ============ 1) VERİ YÜKLE (yalnız oku) ============
con = sqlite3.connect(DB)
sr = pd.read_sql_query(
    "SELECT scan_type, symbol, signal_date, day_offset, return_pct "
    "FROM signal_returns WHERE day_offset IN (5,10,20)", con)
con.close()
sr["scan_type"] = sr["scan_type"].str.strip()
sr["symbol"] = sr["symbol"].str.strip()

# XU100 ileri getiri (BIST100 alpha bazi) — kendi islem takvimi = borsa takvimi
xu = pd.read_parquet(XU100).sort_index()
xu.index = pd.to_datetime(xu.index).strftime("%Y-%m-%d")
xu_fwd = {}  # offset -> {date_str: fwd_ret_pct}
for off in OFFSETS:
    f = (xu["Close"].shift(-off) / xu["Close"] - 1.0) * 100.0
    xu_fwd[off] = f.to_dict()

# ============ 2) BENZERSIZ (symbol, signal_date) GOZLEMLERI ============
# getiri = o gun/o hisse icin taramalar arasi ortalama (ayni olay, tekil sayilir)
obs = (sr.groupby(["symbol", "signal_date", "day_offset"])["return_pct"]
         .mean().reset_index())
# gunluk evren ortalamasi (cross-sectional) => (c) evren farki icin
obs["univ_mean"] = obs.groupby(["signal_date", "day_offset"])["return_pct"].transform("mean")
obs["a_univ"] = obs["return_pct"] - obs["univ_mean"]            # (c) evren farki
obs["xu"] = obs.apply(lambda r: xu_fwd[int(r["day_offset"])].get(r["signal_date"], np.nan), axis=1)
obs["a_bist"] = obs["return_pct"] - obs["xu"]                    # (b) BIST100 alpha
obs["month"] = obs["signal_date"].str[:7]
obs["half"] = obs["month"] + obs["signal_date"].str[8:10].astype(int).apply(lambda d: "a" if d <= 15 else "b")

# hangi taramalar bir (symbol,date)'i isaretledi (crowding sayimi, radar1 haric)
mem = (sr[~sr["scan_type"].isin(NOISE)].groupby(["symbol", "signal_date"])["scan_type"]
       .agg(lambda s: sorted(set(s))).reset_index().rename(columns={"scan_type": "scanners"}))
mem["n_scan"] = mem["scanners"].apply(len)

# scanner -> isaretledigi (symbol,date) kumesi (radar1 dahil TUM taramalar analiz edilir)
scan_members = (sr.groupby("scan_type")
                  .apply(lambda g: set(zip(g["symbol"], g["signal_date"])))
                  .to_dict())
ALL_SCANNERS = sorted(scan_members.keys())


# ============ 3) METRİK MOTORU ============
def cell(df, metric):
    """df: bir taramanin bir donem/vade gozlemleri. metric: a_univ / a_bist / return_pct kolonu."""
    if df is None or len(df) == 0 or metric not in df.columns:
        return None
    d = df.dropna(subset=[metric])
    n = len(d)
    if n == 0:
        return None
    raw = d["return_pct"].values
    x = d[metric].values
    wins = raw[raw > 0]; losses = raw[raw < 0]
    payoff = (wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else None
    pf = (wins.sum() / abs(losses.sum())) if losses.sum() != 0 else None
    shrunk = (n / (n + SHRINK_K)) * float(x.mean())   # örneklem-küçültülmüş
    if n >= 5:
        bt = np.random.choice(x, (BOOT, n), replace=True).mean(axis=1)
        ci_lo, ci_hi = np.percentile(bt, [2.5, 97.5])
    else:
        ci_lo = ci_hi = None
    return dict(
        n=n,
        win_rate=round(100.0 * (raw > 0).mean(), 1),
        abs_ret=round(float(raw.mean()), 2),                 # (a) mutlak
        a_bist=round(float(d["a_bist"].mean()), 2) if d["a_bist"].notna().any() else None,  # (b)
        a_univ=round(float(d["a_univ"].mean()), 2),          # (c)
        avg_win=round(float(wins.mean()), 2) if len(wins) else 0.0,
        avg_loss=round(float(losses.mean()), 2) if len(losses) else 0.0,
        payoff=round(payoff, 2) if payoff else None,
        profit_factor=round(pf, 2) if pf else None,
        shrunk_a_univ=round(shrunk, 3),
        ci_lo=round(float(ci_lo), 2) if ci_lo is not None else None,
        ci_hi=round(float(ci_hi), 2) if ci_hi is not None else None,
    )


def obs_for(scanner, periods, offset, col_period="month"):
    """bir taramanin, verilen donemlerdeki, verilen vadedeki benzersiz gozlemleri."""
    members = scan_members[scanner]
    o = obs[(obs["day_offset"] == offset) & (obs[col_period].isin(periods))]
    if len(o) == 0:
        return o
    mask = [ (s, dt) in members for s, dt in zip(o["symbol"], o["signal_date"]) ]
    return o[pd.Series(mask, index=o.index, dtype=bool)]


# ============ 4) VADE SEÇİMİ (yalnız TRAIN) ============
def pick_vade_on_train(scanner, train_periods, col="month"):
    """TRAIN'de shrink-a_univ en yuksek (ve pozitif) offset. Yoksa None."""
    best, best_val = None, -1e9
    for off in OFFSETS:
        c = cell(obs_for(scanner, train_periods, off, col), "a_univ")
        if c and c["n"] >= MIN_N:
            if c["shrunk_a_univ"] > best_val:
                best_val, best = c["shrunk_a_univ"], off
    return best, (best_val if best is not None else None)


# ============ 5) WALK-FORWARD PENCERELERİ ============
MONTHLY = [
    (["2026-05"],            "2026-06"),
    (["2026-05", "2026-06"], "2026-07"),
]
# yarim-ay OOS istikrar pencereleri (expanding train, rolling test)
HALVES = ["2026-05a", "2026-05b", "2026-06a", "2026-06b", "2026-07a", "2026-07b"]
HALF_WF = []
for i in range(1, len(HALVES)):
    HALF_WF.append((HALVES[:i], HALVES[i]))   # train = onceki tum yarilar, test = bu yari


# ============ 6) RAPOR ÜRET ============
report = {"meta": {
    "kaynak": "patron.db + XU100.IS_1d.parquet (backtest_results.json KULLANILMADI)",
    "gozlem_birimi": "benzersiz (symbol, signal_date)",
    "olgun_aylar": ["2026-05", "2026-06", "2026-07"],
    "aylik_OOS_pencere_sayisi": len(MONTHLY),
    "OOS_yeterli_mi": len(MONTHLY) >= 3,
    "shrink_K": SHRINK_K, "min_N": MIN_N,
    "metrikler": {"a": "mutlak getiri", "b": "BIST100 alpha", "c": "gunluk evren farki"},
    "stop_hedef": "YOK => R multiple / planlanan R:R hesaplanmadi",
    "crowding_sayimi": "radar1 haric distinct scan_type sayisi",
}, "scanners": {}}

log("=" * 90)
log("SCANNER EDGE — WALK-FORWARD RAPORU  (SALT RAPOR; hicbir tablo/kod degismedi)")
log(f"Olgun aylar: Mayis/Haziran/Temmuz | Aylik OOS pencere: {len(MONTHLY)}  "
    f"=> {'YETERLI' if len(MONTHLY)>=3 else '3 ALTI => sonuclar DOGRULANMAMIS'}")
log("=" * 90)

for scn in ALL_SCANNERS:
    entry = {"tum_donem": {}, "monthly_wf": [], "half_oos": {}, "vade": {}, "etiket": None, "crowding": {}}

    # (i) tum donem 5/10/20 tam metrik
    for off in OFFSETS:
        c = cell(obs_for(scn, ["2026-05", "2026-06", "2026-07"], off, "month"), "a_univ")
        entry["tum_donem"][off] = c

    # (ii) aylik walk-forward: TRAIN'de vade sec, TEST'te sabit olc
    oos_pos_univ = 0; oos_pos_bist = 0; oos_windows = 0
    for train, test in MONTHLY:
        vade, tv = pick_vade_on_train(scn, train, "month")
        rec = {"train": train, "test": test, "secilen_vade": vade}
        if vade is None:
            rec["durum"] = "TRAIN'de yeterli ornek yok"
            entry["monthly_wf"].append(rec); continue
        te = cell(obs_for(scn, [test], vade, "month"), "a_univ")
        rec["test_metrik"] = te
        # her vade icin TEST tam tablo (raporda 5/10/20 hepsi)
        rec["test_5_10_20"] = {off: cell(obs_for(scn, [test], off, "month"), "a_univ") for off in OFFSETS}
        if te and te["n"] >= MIN_N:
            oos_windows += 1
            if te["a_univ"] > 0: oos_pos_univ += 1
            if te["a_bist"] is not None and te["a_bist"] > 0: oos_pos_bist += 1
        entry["monthly_wf"].append(rec)

    # (iii) yarim-ay OOS istikrar (kac pencerede pozitif)
    half_pos = 0; half_tot = 0
    for train, test in HALF_WF:
        vade, tv = pick_vade_on_train(scn, train, "half")
        if vade is None: continue
        te = cell(obs_for(scn, [test], vade, "half"), "a_univ")
        if te and te["n"] >= max(10, MIN_N // 2):
            half_tot += 1
            if te["a_univ"] > 0: half_pos += 1
    entry["half_oos"] = {"pozitif_pencere": half_pos, "toplam_pencere": half_tot}

    # (iv) etiketleme (Codex terfi kurali; 3 OOS alti => DOGRULANMAMIS)
    all_c = entry["tum_donem"].get(10) or entry["tum_donem"].get(5)
    pooled_n = all_c["n"] if all_c else 0
    if pooled_n < MIN_N:
        etiket = "KANIT YETERSIZ (ornek az)"
    elif oos_windows < 2:
        etiket = "KANIT YETERSIZ (OOS penceresi yok)"
    elif oos_pos_univ >= 2 and oos_windows >= 2:
        etiket = "OOS POZITIF / DOGRULANMAMIS (>=3 bagimsiz donem yok)"
    elif oos_pos_univ >= 1:
        etiket = "KARISIK / DOGRULANMAMIS"
    else:
        etiket = "OOS NEGATIF"
    entry["etiket"] = etiket
    entry["oos_ozet"] = {"aylik_pencere": oos_windows, "univ_pozitif": oos_pos_univ,
                         "bist_pozitif": oos_pos_bist}

    report["scanners"][scn] = entry

# ============ 7) CROWDING (ayri risk etiketi, puana girmez) ============
crowd = obs.merge(mem[["symbol", "signal_date", "n_scan"]], on=["symbol", "signal_date"], how="left")
crowd["n_scan"] = crowd["n_scan"].fillna(0).astype(int)
crowd["band"] = pd.cut(crowd["n_scan"], [-1, 2, 4, 99], labels=["dusuk(1-2)", "orta(3-4)", "gec_kalmislik(5+)"])
crow_rep = {}
for mon in ["2026-05", "2026-06", "2026-07"]:
    sub = crowd[(crowd["day_offset"] == 10) & (crowd["month"] == mon)]
    crow_rep[mon] = {str(b): {"n": int((sub["band"] == b).sum()),
                              "a_univ": round(float(sub[sub["band"] == b]["a_univ"].mean()), 2)}
                     for b in ["dusuk(1-2)", "orta(3-4)", "gec_kalmislik(5+)"] if (sub["band"] == b).any()}
report["crowding_10g"] = crow_rep


# ============ 8) YAZ (JSON + CSV + konsol ozet) ============
with open(OUT_DIR + r"\scanner_edge_report.json", "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

# CSV: tum donem 10g ana satir + etiket + vade tercihi (aylik son pencere)
rows = []
for scn, e in report["scanners"].items():
    c10 = e["tum_donem"].get(10) or {}
    last_wf = e["monthly_wf"][-1] if e["monthly_wf"] else {}
    rows.append({
        "tarama": scn, "etiket": e["etiket"],
        "N_10g": c10.get("n"), "win%_10g": c10.get("win_rate"),
        "mutlak%_10g": c10.get("abs_ret"), "bist_alpha_10g": c10.get("a_bist"),
        "evren_alpha_10g": c10.get("a_univ"), "shrunk_10g": c10.get("shrunk_a_univ"),
        "ci_lo_10g": c10.get("ci_lo"), "ci_hi_10g": c10.get("ci_hi"),
        "payoff_10g": c10.get("payoff"), "pf_10g": c10.get("profit_factor"),
        "son_wf_secilen_vade": last_wf.get("secilen_vade"),
        "aylik_oos_pozitif": e["oos_ozet"]["univ_pozitif"], "aylik_oos_pencere": e["oos_ozet"]["aylik_pencere"],
        "yarimay_pozitif": e["half_oos"]["pozitif_pencere"], "yarimay_toplam": e["half_oos"]["toplam_pencere"],
    })
df_csv = pd.DataFrame(rows).sort_values("shrunk_10g", ascending=False, na_position="last")
df_csv.to_csv(OUT_DIR + r"\scanner_edge_report.csv", index=False, encoding="utf-8-sig")

# ---- konsol: shrink-10g sirali ozet ----
log("\nTUM DONEM 10g — shrink-a_univ sirali (sadece N>=%d, siralama ham alpha DEGIL shrink):" % MIN_N)
log(f'{"tarama":<20}{"N":>5}{"win%":>6}{"mutlak":>8}{"bist_a":>8}{"evren_a":>8}{"shrunk":>8}{"ci_lo":>7}  etiket')
for _, r in df_csv.iterrows():
    if not r["N_10g"] or r["N_10g"] < MIN_N: continue
    log(f'{r["tarama"]:<20}{int(r["N_10g"]):>5}{r["win%_10g"]:>6}{r["mutlak%_10g"]:>8}'
        f'{str(r["bist_alpha_10g"]):>8}{r["evren_alpha_10g"]:>8}{r["shrunk_10g"]:>8}'
        f'{str(r["ci_lo_10g"]):>7}  {r["etiket"]}')

log("\nCROWDING (10g, radar1 haric) — puana girmez, risk etiketi:")
for mon, bands in report["crowding_10g"].items():
    log(f'  {mon}: ' + " | ".join(f'{b}: a_univ={v["a_univ"]} (N={v["n"]})' for b, v in bands.items()))

log("\nOOS DURUMU: aylik yalniz 2 pencere (<3) => tum pozitifler 'DOGRULANMAMIS'.")
log(f"\nCIKTILAR:\n  {OUT_DIR}\\scanner_edge_report.json\n  {OUT_DIR}\\scanner_edge_report.csv")
log("Not: patron.db, app.py, tarama fonksiyonlari ve UI DEGISMEDI (salt-okur rapor).")
