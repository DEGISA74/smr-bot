# -*- coding: utf-8 -*-
"""
trajectory_backtest.py — "KANIT BUYUMESI" hipotezi testi (SALT RAPOR)
Codex + Claude mutabakati. patron.db / app.py / parquet DEGISMEZ (yalniz okur).

TEZ: Ilk sinyalden (T0) sonra kaniti GENISLEYEN + HIZLANAN + ISRAR eden event'ler,
zayiflayan/duz event'lerden daha cok "buyuk kazanan"a donusur.

DISIPLIN:
  * Gozlem birimi = event_id (symbol + event_start_date). Ayni olay tek sayilir.
  * T0 = event_start_date. Karar = T0 + 3 islem gunu (D). Giris = D+1 ACILIS.
  * Getiri D SONRASI olculur (T+3 oncesi hareket "dogrulama" olarak ayri raporlanir).
  * Sag kuyruk: +%30 hedef + MFE + MAE (ayni-bar belirsizligi icin muhafazakar).
  * Baz oran vs guclu-ivme grubu => LIFT.
  * Oynaklik kontrolu (grup sadece 'oynak hisse' secmiyor mu?).
  * Gostergeler fiyattan GERI HESAP => feature_source = reconstructed_v1.
  * Walk-forward: T0 ayina gore (Mayis/Haziran/Temmuz) ayri raporlanir.
"""
import sqlite3, json, glob, os, re
import numpy as np
import pandas as pd

BASE = r"C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal"
DB   = BASE + r"\patron.db"
VDIR = BASE + r"\veriler"
OUT  = r"C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal\gelişmiş tarama"
FOLLOW = 3      # T+1..T+3 takip penceresi (islem gunu)
HOLD   = 20     # karar sonrasi tutma penceresi (islem gunu)
TARGET = 30.0   # sag kuyruk hedefi %
NOISE  = {"radar1"}   # crowding/genislik SAYIMINDA haric


def log(*a):
    print(*a); import sys; sys.stdout.flush()


# ---------- parquet indeksi ----------
pq_files = glob.glob(os.path.join(VDIR, "*.IS_1d.parquet"))
pq_map = {}
for f in pq_files:
    base = os.path.basename(f)[:-len(".IS_1d.parquet")]  # "OZATD"
    pq_map[base] = f
_cache = {}
def load_px(sym_base):
    if sym_base in _cache:
        return _cache[sym_base]
    f = pq_map.get(sym_base)
    if not f:
        _cache[sym_base] = None; return None
    d = pd.read_parquet(f).sort_index()
    d.index = pd.to_datetime(d.index).strftime("%Y-%m-%d")
    _cache[sym_base] = d
    return d

# XU100 (takvim + baz + RS)
xu = pd.read_parquet(os.path.join(VDIR, "XU100.IS_1d.parquet")).sort_index()
xu.index = pd.to_datetime(xu.index).strftime("%Y-%m-%d")
CAL = list(xu.index)               # islem gunu takvimi
CAL_POS = {d: i for i, d in enumerate(CAL)}
xu_close = xu["Close"]


def rsi14(close):
    d = close.diff()
    up = d.clip(lower=0).rolling(14).mean()
    dn = (-d.clip(upper=0)).rolling(14).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def cal_shift(date, n):
    """date'ten n islem gunu sonraki takvim tarihi (yoksa None)."""
    i = CAL_POS.get(date)
    if i is None or i + n >= len(CAL) or i + n < 0:
        return None
    return CAL[i + n]


# ---------- event'leri cek ----------
con = sqlite3.connect(DB)
ss = pd.read_sql_query(
    "SELECT event_id, symbol, event_start_date, scan_date, event_day, scan_type "
    "FROM scan_signals", con)
con.close()
ss["symbol"] = ss["symbol"].str.strip().str.replace(".IS", "", regex=False)
ss["scan_type"] = ss["scan_type"].str.strip()

rows = []
skipped = {"no_pq": 0, "no_decision": 0, "not_matured": 0, "short_hist": 0}
# GOZLEM BIRIMI = symbol + event_start_date (ayni olay farkli event_id'lerde birlesir — Codex)
for (sym, T0), g in ss.groupby(["symbol", "event_start_date"]):
    eid = f"{sym}_{T0}"
    px = load_px(sym)
    if px is None:
        skipped["no_pq"] += 1; continue
    D = cal_shift(T0, FOLLOW)                 # karar gunu
    entry_d = cal_shift(T0, FOLLOW + 1)       # giris = D+1 acilis
    exit_d = cal_shift(T0, FOLLOW + 1 + HOLD)
    if D is None or entry_d is None:
        skipped["no_decision"] += 1; continue
    if exit_d is None:
        skipped["not_matured"] += 1; continue   # 20g olgunlasmamis
    if T0 not in px.index or entry_d not in px.index:
        skipped["short_hist"] += 1; continue

    # --- TRAJEKTORI (T0..D arasi) ---
    win = g[(g["scan_date"] >= T0) & (g["scan_date"] <= D)]
    days = sorted(win["scan_date"].unique())
    # genislik: T0 gunu vs son takip gunu, radar1 haric distinct tarama
    def nscan(dt):
        s = set(win[win["scan_date"] == dt]["scan_type"]) - NOISE
        return len(s), s
    n0, set0 = nscan(T0)
    last_day = days[-1]
    nL, setL = nscan(last_day)
    breadth_growth = nL - n0
    new_joined = len(setL - set0)
    persistence = len([d for d in days if d != T0])   # T0 sonrasi kac gun tekrar

    # --- HIZ (fiyattan geri hesap) ---
    px["rsi"] = rsi14(px["Close"])
    px["sma20"] = px["Close"].rolling(20).mean()
    def at(col, dt):
        return px.at[dt, col] if dt in px.index and not pd.isna(px.at[dt, col]) else np.nan
    rsi_T0, rsi_D = at("rsi", T0), at("rsi", D if D in px.index else last_day)
    rsi_slope = (rsi_D - rsi_T0) if not (np.isnan(rsi_T0) or np.isnan(rsi_D)) else np.nan
    # RS vs XU100 slope (normalize)
    def rs(dt):
        if dt in px.index and dt in xu_close.index and xu_close[dt] > 0:
            return px.at[dt, "Close"] / xu_close[dt]
        return np.nan
    rs_T0, rs_D = rs(T0), rs(D if D in px.index else last_day)
    rs_slope = (rs_D / rs_T0 - 1) * 100 if not (np.isnan(rs_T0) or np.isnan(rs_D)) and rs_T0 else np.nan
    # oynaklik kontrolu: 20g gunluk getiri std (T0'da)
    vol20 = px["Close"].pct_change().rolling(20).std().get(T0, np.nan) * 100

    # --- TRAJEKTORI SKORU (0-5) — basit, seffaf, once-tanimli ---
    tscore = 0
    tscore += 1 if breadth_growth > 0 else 0
    tscore += 1 if new_joined > 0 else 0
    tscore += 1 if persistence >= 2 else 0
    tscore += 1 if (not np.isnan(rsi_slope) and rsi_slope > 0) else 0
    tscore += 1 if (not np.isnan(rs_slope) and rs_slope > 0) else 0

    # --- SONUC (D SONRASI, giris=entry_d acilis) ---
    entry = px.at[entry_d, "Open"] if not pd.isna(px.at[entry_d, "Open"]) else px.at[entry_d, "Close"]
    seg = px.loc[entry_d:exit_d]
    if len(seg) < 2 or entry <= 0:
        continue
    hi = seg["High"].max(); lo = seg["Low"].min(); last = seg["Close"].iloc[-1]
    mfe = (hi / entry - 1) * 100
    mae = (lo / entry - 1) * 100
    post_ret = (last / entry - 1) * 100
    hit30 = 1 if mfe >= TARGET else 0
    # temiz +30: hedefe ulasti VE yol boyu MAE > -15 (muhafazakar ayni-bar)
    clean30 = 1 if (hit30 and mae > -15) else 0
    # BIST100 alpha (D sonrasi ayni pencere)
    xseg = xu_close.loc[entry_d:exit_d] if entry_d in xu_close.index else None
    xu_ret = (xseg.iloc[-1] / xseg.iloc[0] - 1) * 100 if xseg is not None and len(xseg) >= 2 else np.nan
    post_alpha = post_ret - xu_ret if not np.isnan(xu_ret) else np.nan
    # T0->D "dogrulama" hareketi (karara girmez, ayri raporlanir)
    conf_move = (px.at[D, "Close"] / px.at[T0, "Close"] - 1) * 100 if (D in px.index and T0 in px.index) else np.nan

    rows.append(dict(
        event_id=eid, symbol=sym, T0=T0, month=T0[:7],
        breadth_growth=breadth_growth, new_joined=new_joined, persistence=persistence,
        rsi_slope=round(rsi_slope, 1) if not np.isnan(rsi_slope) else None,
        rs_slope=round(rs_slope, 2) if not np.isnan(rs_slope) else None,
        vol20=round(vol20, 2) if not np.isnan(vol20) else None,
        tscore=tscore,
        conf_move=round(conf_move, 1) if not np.isnan(conf_move) else None,
        post_ret=round(post_ret, 2), post_alpha=round(post_alpha, 2) if not np.isnan(post_alpha) else None,
        mfe=round(mfe, 1), mae=round(mae, 1), hit30=hit30, clean30=clean30,
    ))

df = pd.DataFrame(rows)
df.to_csv(OUT + r"\trajectory_events.csv", index=False, encoding="utf-8-sig")
log(f"Toplam event islendi: {len(df)}  (atlanan: {skipped})")
log(f"feature_source = reconstructed_v1 (gostergeler fiyattan geri hesap)\n")


# ---------- GRUPLAMA: 3 grup (Codex) ----------
def grp(s):
    # erisilebilir aralik 0..3 (genislik bu veride olu) -> 3 grup
    if s <= 1: return "1_zayiflayan"
    if s == 2: return "2_sabit"
    return "3_buyuyen"   # skor >= 3
df["grup"] = df["tscore"].apply(grp)


def summarize(sub):
    n = len(sub)
    if n == 0: return None
    return dict(
        N=n,
        post_ret=round(sub["post_ret"].mean(), 2),
        post_alpha=round(sub["post_alpha"].mean(), 2),
        hit30=round(100 * sub["hit30"].mean(), 1),
        clean30=round(100 * sub["clean30"].mean(), 1),
        mfe=round(sub["mfe"].mean(), 1),
        mae=round(sub["mae"].mean(), 1),
        vol20=round(sub["vol20"].mean(), 2),
    )


base_hit = 100 * df["hit30"].mean()
log("=" * 96)
log(f"BAZ ORAN (tum event'ler): +%{TARGET:.0f} hedefe ulasma = %{base_hit:.1f}  | N={len(df)}")
log("=" * 96)

log("\n--- TRAJEKTORI SKORUNA GORE (0..5) — monotonluk testi ---")
log(f'{"skor":<6}{"N":>6}{"post_ret":>10}{"post_alpha":>11}{"hit30%":>8}{"clean30%":>9}{"MFE":>7}{"MAE":>7}{"vol20":>7}{"lift":>6}')
for s in range(6):
    m = summarize(df[df["tscore"] == s])
    if m:
        lift = m["hit30"] / base_hit if base_hit > 0 else 0
        log(f'{s:<6}{m["N"]:>6}{m["post_ret"]:>10}{str(m["post_alpha"]):>11}{m["hit30"]:>8}'
            f'{m["clean30"]:>9}{m["mfe"]:>7}{m["mae"]:>7}{m["vol20"]:>7}{lift:>6.2f}')

log("\n--- 3 GRUP (Codex) ---")
log(f'{"grup":<14}{"N":>6}{"post_ret":>10}{"post_alpha":>11}{"hit30%":>8}{"clean30%":>9}{"MFE":>7}{"MAE":>7}{"vol20":>7}{"lift":>6}')
group_out = {}
for gname in ["1_zayiflayan", "2_sabit", "3_buyuyen"]:
    m = summarize(df[df["grup"] == gname])
    group_out[gname] = m
    if m:
        lift = m["hit30"] / base_hit if base_hit > 0 else 0
        log(f'{gname:<14}{m["N"]:>6}{m["post_ret"]:>10}{str(m["post_alpha"]):>11}{m["hit30"]:>8}'
            f'{m["clean30"]:>9}{m["mfe"]:>7}{m["mae"]:>7}{m["vol20"]:>7}{lift:>6.2f}')

# ---------- WALK-FORWARD: T0 ayina gore ----------
log("\n--- WALK-FORWARD (T0 ayina gore, 3.grup vs baz) ---")
log(f'{"ay":<10}{"N_top":>7}{"hit30_top":>10}{"hit30_baz":>10}{"lift":>7}{"post_ret_top":>13}')
wf = {}
for mon in sorted(df["month"].unique()):
    sub = df[df["month"] == mon]
    top = sub[sub["grup"] == "3_buyuyen"]
    if len(sub) < 20: continue
    bh = 100 * sub["hit30"].mean()
    th = 100 * top["hit30"].mean() if len(top) else 0
    lift = th / bh if bh > 0 else 0
    wf[mon] = dict(N_top=len(top), hit30_top=round(th, 1), hit30_baz=round(bh, 1), lift=round(lift, 2))
    log(f'{mon:<10}{len(top):>7}{th:>10.1f}{bh:>10.1f}{lift:>7.2f}{top["post_ret"].mean() if len(top) else 0:>13.2f}')

# ---------- KAHRAMANLAR bu testte 3.grupta mi? ----------
log("\n--- Kahraman event'ler hangi skorda? (survivor-bias kontrolu, kurala DAHIL EDILMEDI) ---")
heroes = df[df["symbol"].isin(["OZATD", "KTLEV", "TUPRS", "ASTOR", "EREGL", "OZGYO", "KLSER"])]
for _, r in heroes.sort_values("post_ret", ascending=False).head(12).iterrows():
    log(f'  {r["symbol"]:7} T0={r["T0"]} skor={r["tscore"]} grup={r["grup"]:12} '
        f'genislik+{r["breadth_growth"]} israr={r["persistence"]} post_ret=%{r["post_ret"]} MFE=%{r["mfe"]}')

with open(OUT + r"\trajectory_report.json", "w", encoding="utf-8") as f:
    json.dump({"baz_hit30": round(base_hit, 2), "gruplar": group_out, "walk_forward": wf,
               "N": len(df), "atlanan": skipped}, f, ensure_ascii=False, indent=2)
log(f"\nCIKTI: {OUT}\\trajectory_events.csv + trajectory_report.json")
log("Not: patron.db / app.py / parquet DEGISMEDI (salt-okur).")
