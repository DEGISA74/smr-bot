# -*- coding: utf-8 -*-
"""
genel_ozet_verdict_backtest.py — GENEL ÖZET paneli 4-oy verdicti backtest'i
===========================================================================
Amaç (13 Tem 2026): Panelin manşet kararı (YUKARI/AŞAĞI ★ oylaması,
analysis_core.compute_genel_ozet_pack) hiç ölçülmedi. Bu script aynı 4 oyu
geçmiş her gün için yeniden hesaplar, 5/10/20 gün ileri getiriyi ölçer ve
7 varyantı yan yana kıyaslar (CMF 5. oy · RSI güç-devamı · yapı simetrisi ·
MFI 6. oy · CMF veto · kombolar).

Panel oylarının birebir kopyası (compute_genel_ozet_pack + PA-DNA):
  hacim : 5g kümülatif Volume_Delta işareti (CLV paylaştırma)
  obv   : PA-DNA obv_direction (5g/14g dual pencere + fiyat trendi çapraz kuralı)
  yapı  : son 3 barda HH+HL → +1, yoksa 0  (mevcut halde ASLA -1 veremez!)
  rsi   : Cutler RSI(14): <30→+1 · 30-60→+1 · >70→-1 · 60-70→0

Ek durum tabloları (panel italic satırlarının renk doğrulaması):
  cmf_dual (Force Compass, THR=0.05) · rsi_dual (confluence 5g/14g) ·
  cum_delta_dual (5g/20g %) · mfi_dual (5g/14g, scan_pipeline eşikleri)

Çıktı: genel_ozet_verdict_backtest.json + genel_ozet_verdict_report.md
Kullanım: python genel_ozet_verdict_backtest.py [--sample N] [--step S]
"""
import sys, os, glob, json, argparse
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indicators import (calculate_volume_delta, compute_mfi,
                        compute_force_index_dual, compute_relative_obv_state)

BASE = os.path.dirname(os.path.abspath(__file__))
VERI_DIR = os.path.join(BASE, "veriler")
OUT_JSON = os.path.join(BASE, "genel_ozet_verdict_backtest.json")
OUT_MD   = os.path.join(BASE, "genel_ozet_verdict_report.md")

HORIZONS = (5, 10, 20)
WARMUP = 60          # rolling pencereler otursun
CMF_THR = 0.05       # Force Compass _THR ile birebir

VARIANTS = [
    "V0_mevcut",        # bugünkü panel: hacim+obv+yapı(0/1)+rsi_mevcut
    "V1_yapi_simetri",  # yapı oyu LL+LH'de -1 verebilsin
    "V2_rsi_guc",       # rsi oyu güç-devamı: >60→+1, <40→-1
    "V3_rsi_dual",      # rsi oyu çift-pencere state'ten (karne yönleri)
    "V4_cmf_5oy",       # V0 + CMF dual 5. oy (turning_up = tuzak → -1)
    "V5_cmf_veto",      # V0 ama CMF güçlü satış/tuzakta yukarı verdict iptal
    "V6_kombo",         # yapı simetri + rsi güç + CMF oy (5 oy)
    "V7_kombo_mfi",     # V6 + MFI dual 6. oy
    "V8_cift_onay",     # SADECE çift-onaylı oylar (karne + bu test aynı yön):
                        # yapı± + rsi_dual{ob_both+1, dip_rec-1} + cmf{sp+1, sn-1, tu-1}
                        # + mfi{os_both+1, early_ob-1}  (6 oy)
    # ── 13 Tem öğleden sonra turu (V9-V12) ────────────────────────────
    "V9a_obv_yok",      # V8 eksi OBV oyu (ölçülmemiş tek oyun sınavı — 5 oy)
    "V9b_rel_obv",      # V8 ama OBV yerine rel-OBV (karne: sadece underperform_strong sinyalli)
    "V10_karne_agirlik",# V8 oyları karne gücüne göre ağırlıklı: rsi_dual×2 + cmf×2, diğerleri ×1
    "V11_fi_ters",      # V8 + Force Index diverjansı TERS yönde 7. oy
                        # (karne: bearish div +2.58 KAZANDIRIYOR, bullish div -3.15 KAYBETTİRİYOR)
    "V12_agirlik_fi",   # V10 ağırlıkları + FI ters oyu (en iddialı kombo)
    "V13_agirlik_obvsuz", # İki bağımsız kazananın birleşimi: V9a (OBV çıkınca
                          # ayrım +1.09) + V10 (ağırlık, yukarı +1.90) — final aday
]


# ── Cutler RSI (panel/PA-DNA ile birebir: rolling MEAN, Wilder değil) ──
def _cutler_rsi(close, period):
    d = close.diff()
    g = d.where(d > 0, 0.0).rolling(period).mean()
    l = (-d.where(d < 0, 0.0)).rolling(period).mean()
    return 100 - (100 / (1 + (g / l)))


def _cmf_series(df, period):
    """compute_cmf'in seri hali (formül birebir: MFM×V rolling toplam / V toplam)."""
    hl = df["High"] - df["Low"]
    mfm = np.where(hl > 0, ((df["Close"] - df["Low"]) - (df["High"] - df["Close"])) / hl, 0.0)
    mfv = pd.Series(mfm, index=df.index) * df["Volume"]
    vs = df["Volume"].rolling(period).sum()
    return mfv.rolling(period).sum() / vs.replace(0, np.nan)


def _bucket(net, n_votes):
    """Net oyu (up-dn) oy sayısından bağımsız kıyaslanabilir kovaya çevir."""
    f = net / n_votes
    if f >= 0.75:  return "GÜÇLÜ YUKARI"
    if f >= 0.50:  return "YUKARI"
    if f > 0:      return "HAFİF YUKARI"
    if f <= -0.75: return "GÜÇLÜ AŞAĞI"
    if f <= -0.50: return "AŞAĞI"
    if f < 0:      return "HAFİF AŞAĞI"
    return "NÖTR"


def _v0_label(up, dn):
    """Panelin bugünkü etiket haritası — compute_genel_ozet_pack ile birebir."""
    if up == 4: return "YUKARI ★"
    if dn == 4: return "AŞAĞI ★"
    if up >= 3: return "YUKARI"
    if dn >= 3: return "AŞAĞI"
    if up == 2 and dn == 2: return "ÇELİŞKİLİ"
    if up >= 2 and dn < 2:  return "HAFİF YUKARI"
    if dn >= 2 and up < 2:  return "HAFİF AŞAĞI"
    return "KARARSIZ"


def eval_ticker(df, step=2, bench=None):
    """Bir hissenin tüm geçmiş günleri için: varyant kovaları + durumlar + ileri getiriler."""
    out = []
    # Hayalet bar temizliği (panel ile aynı: V=0 + flat OHLC)
    v0 = df["Volume"].fillna(0)
    flat = ((df["Open"] - df["Close"]).abs() < 1e-9) & \
           ((df["High"] - df["Close"]).abs() < 1e-9) & \
           ((df["Low"] - df["Close"]).abs() < 1e-9)
    df = df[~((v0 <= 0) & flat)].copy()
    n = len(df)
    if n < WARMUP + 15:
        return out

    c = df["Close"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    v = df["Volume"].astype(float)

    # ── Seriler (hepsi vektörize) ─────────────────────────────────
    vd = calculate_volume_delta(df)["Volume_Delta"].astype(float)
    vd5 = vd.rolling(5).sum()
    vd20 = vd.rolling(20).sum()
    vsum5 = v.rolling(5).sum()
    vsum20 = v.rolling(20).sum()

    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    obv_sma20 = obv.rolling(20).mean()

    rsi14 = _cutler_rsi(c, 14)
    rsi5 = _cutler_rsi(c, 5)

    cmf5 = _cmf_series(df, 5)
    cmf20 = _cmf_series(df, 20)

    mfi5 = compute_mfi(df, period=5)
    mfi14 = compute_mfi(df, period=14)

    hh_hl = (h > h.shift(1)) & (h.shift(1) > h.shift(2)) & \
            (l > l.shift(1)) & (l.shift(1) > l.shift(2))
    lh_ll = (h < h.shift(1)) & (h.shift(1) < h.shift(2)) & \
            (l < l.shift(1)) & (l.shift(1) < l.shift(2))

    ca = c.values
    for i in range(WARMUP, n - HORIZONS[0], step):
        cp0 = ca[i]
        if cp0 <= 0 or v.iloc[i] <= 0:
            continue
        fwd = {}
        for hz in HORIZONS:
            fwd[hz] = (ca[i + hz] / cp0 - 1) * 100 if i + hz < n else None
        if fwd[5] is None or fwd[10] is None:
            continue  # 5g+10g şart (20g opsiyonel)

        # ── OY 1: hacim (5g kümülatif delta) ──────────────────────
        _v5 = vd5.iloc[i]
        s_hacim = 1 if _v5 > 0 else (-1 if _v5 < 0 else 0)

        # ── OY 2: OBV direction (PA-DNA kural sırası birebir) ─────
        o_now, o_5, o_14 = obv.iloc[i], obv.iloc[i - 5], obv.iloc[i - 14]
        o5_up, o14_up = o_now > o_5, o_now > o_14
        p_up = ca[i] > ca[i - 5]                      # p_tr YUKARI/AŞAĞI
        o_strong = o_now > obv_sma20.iloc[i]
        if (not p_up) and o5_up and o14_up:   s_obv = 1    # gizli giriş
        elif p_up and not o5_up and not o14_up: s_obv = -1  # gizli çıkış
        elif o5_up and not o14_up:            s_obv = 1    # toparlanma
        elif (not o5_up) and o14_up:          s_obv = 0    # zayıflama
        elif o_strong:                        s_obv = 1
        else:                                 s_obv = 0

        # ── OY 3: yapı ─────────────────────────────────────────────
        s_yapi_mevcut = 1 if hh_hl.iloc[i] else 0
        s_yapi_sim = 1 if hh_hl.iloc[i] else (-1 if lh_ll.iloc[i] else 0)

        # ── OY 4: RSI (mevcut / güç-devamı / dual) ────────────────
        r = rsi14.iloc[i]
        if not np.isfinite(r):
            continue
        if r < 30:    s_rsi_mev = 1
        elif r <= 60: s_rsi_mev = 1
        elif r > 70:  s_rsi_mev = -1
        else:         s_rsi_mev = 0
        s_rsi_guc = 1 if r > 60 else (-1 if r < 40 else 0)

        # rsi_dual state (confluence eşikleri birebir)
        r5 = rsi5.iloc[i]
        rsi_dual = "neutral"
        if np.isfinite(r5):
            if   r5 >= 80 and r >= 70: rsi_dual = "overbought_both"
            elif r5 <= 20 and r <= 30: rsi_dual = "oversold_both"
            elif r5 >= 80 and r < 60:  rsi_dual = "early_overbought"
            elif r5 <= 20 and r > 40:  rsi_dual = "early_oversold"
            elif r5 < 50  and r >= 70: rsi_dual = "cooling_overheat"
            elif r5 > 50  and r <= 30: rsi_dual = "dip_recovery"
        # Karne yönleri: devam=iyi, dönüş=tuzak
        s_rsi_dual = {"overbought_both": 1, "early_oversold": 1,
                      "dip_recovery": -1, "cooling_overheat": -1,
                      "early_overbought": -1}.get(rsi_dual, 0)

        # ── CMF dual state (Force Compass eşikleri birebir) ───────
        c5v, c20v = cmf5.iloc[i], cmf20.iloc[i]
        cmf_state = "neutral"
        if np.isfinite(c5v) and np.isfinite(c20v):
            if   c5v >  CMF_THR and c20v >  CMF_THR: cmf_state = "strong_pos"
            elif c5v < -CMF_THR and c20v < -CMF_THR: cmf_state = "strong_neg"
            elif c5v >  CMF_THR and c20v < -CMF_THR: cmf_state = "turning_up"
            elif c5v < -CMF_THR and c20v >  CMF_THR: cmf_state = "turning_down"
            elif c20v >  CMF_THR:                    cmf_state = "pos"
            elif c20v < -CMF_THR:                    cmf_state = "neg"
        s_cmf = {"strong_pos": 1, "strong_neg": -1, "turning_up": -1}.get(cmf_state, 0)

        # ── cum_delta dual state (confluence %5 eşikleri birebir) ─
        t5, t20 = vsum5.iloc[i], vsum20.iloc[i]
        p5 = (vd5.iloc[i] / t5 * 100) if t5 > 0 else 0
        p20 = (vd20.iloc[i] / t20 * 100) if t20 > 0 else 0
        cum_state = "neutral"
        if   p5 > 5 and p20 > 5:   cum_state = "strong_pos"
        elif p5 < -5 and p20 < -5: cum_state = "strong_neg"
        elif p5 > 0 and p20 < 0:   cum_state = "turning_up"
        elif p5 < 0 and p20 > 0:   cum_state = "turning_down"

        # ── MFI dual state (scan_pipeline eşikleri birebir) ───────
        m5, m14 = mfi5.iloc[i], mfi14.iloc[i]
        mfi_state = "neutral"
        if np.isfinite(m5) and np.isfinite(m14):
            if   m5 >= 80 and m14 >= 75: mfi_state = "overbought_both"
            elif m5 <= 20 and m14 <= 25: mfi_state = "oversold_both"
            elif m5 >= 80 and m14 < 60:  mfi_state = "early_overbought"
            elif m5 <= 20 and m14 > 40:  mfi_state = "early_oversold"
            elif m5 < 50  and m14 >= 75: mfi_state = "cooling_smart_exit"
            elif m5 > 50  and m14 <= 25: mfi_state = "smart_money_recovery"
        s_mfi = {"oversold_both": 1, "early_overbought": -1}.get(mfi_state, 0)

        # V8 için ÇİFT-ONAY oy haritaları — sadece karne + tam-evren testinin
        # AYNI yönü gösterdiği durumlar oy kullanır; çelişenler 0 (dokunma kuralı)
        s_rsi_v8 = {"overbought_both": 1, "dip_recovery": -1}.get(rsi_dual, 0)
        s_cmf_v8 = {"strong_pos": 1, "strong_neg": -1, "turning_up": -1}.get(cmf_state, 0)
        s_mfi_v8 = {"oversold_both": 1, "early_overbought": -1}.get(mfi_state, 0)

        # ── V9-V12 oyları (13 Tem öğleden sonra) — üretim fonksiyonları
        # BİREBİR import edildi (kopya yok): indicators.compute_force_index_dual
        # + compute_relative_obv_state, point-in-time dilimle çağrılır.
        fi_div = "none"
        s_fi_ters = 0
        try:
            _fi_r = compute_force_index_dual(df.iloc[: i + 1])
            if _fi_r and _fi_r.get('divergence'):
                fi_div = _fi_r['divergence']
                # KARNE BULGUSU (n=417/451): Elder ders kitabının TERSİ —
                # bearish div +2.58 kazandırıyor, bullish div -3.15 kaybettiriyor
                s_fi_ters = 1 if fi_div == 'bearish' else (-1 if fi_div == 'bullish' else 0)
        except Exception:
            pass
        relobv_state = "none"
        s_relobv = 0
        try:
            if bench is not None:
                _ro = compute_relative_obv_state(df.iloc[: i + 1], bench.loc[: df.index[i]])
                if _ro and _ro.get('state'):
                    relobv_state = _ro['state']
                    # Karne: sadece NEGATİF taraf sinyalli (underperform_strong
                    # -2.78 / hit %26); pozitif kovalar düz → oy yok
                    s_relobv = -1 if relobv_state == 'underperform_strong' else 0
        except Exception:
            pass

        # ── 8 varyantın oyları ─────────────────────────────────────
        variants = {
            "V0_mevcut":       [s_hacim, s_obv, s_yapi_mevcut, s_rsi_mev],
            "V1_yapi_simetri": [s_hacim, s_obv, s_yapi_sim,    s_rsi_mev],
            "V2_rsi_guc":      [s_hacim, s_obv, s_yapi_mevcut, s_rsi_guc],
            "V3_rsi_dual":     [s_hacim, s_obv, s_yapi_mevcut, s_rsi_dual],
            "V4_cmf_5oy":      [s_hacim, s_obv, s_yapi_mevcut, s_rsi_mev, s_cmf],
            "V6_kombo":        [s_hacim, s_obv, s_yapi_sim,    s_rsi_guc, s_cmf],
            "V7_kombo_mfi":    [s_hacim, s_obv, s_yapi_sim,    s_rsi_guc, s_cmf, s_mfi],
            "V8_cift_onay":    [s_hacim, s_obv, s_yapi_sim,    s_rsi_v8, s_cmf_v8, s_mfi_v8],
            # V9a: OBV oyu çıkarıldı (5 oy) — OBV taşıyor mu sınavı
            "V9a_obv_yok":     [s_hacim, s_yapi_sim, s_rsi_v8, s_cmf_v8, s_mfi_v8],
            # V9b: OBV yerine rel-OBV (sadece güçlü-negatif uyarısı)
            "V9b_rel_obv":     [s_hacim, s_relobv, s_yapi_sim, s_rsi_v8, s_cmf_v8, s_mfi_v8],
            # V10: karne-ağırlıklı — çift-pencere şampiyonları (rsi_dual +5.4,
            # cmf_dual +4.99) 2'şer oy; ağırlık = oyu iki kez saymak
            "V10_karne_agirlik": [s_hacim, s_obv, s_yapi_sim,
                                  s_rsi_v8, s_rsi_v8, s_cmf_v8, s_cmf_v8, s_mfi_v8],
            # V11: V8 + Force Index diverjansı TERS 7. oy
            "V11_fi_ters":     [s_hacim, s_obv, s_yapi_sim, s_rsi_v8, s_cmf_v8, s_mfi_v8, s_fi_ters],
            # V12: ağırlıklı + FI ters (en iddialı)
            "V12_agirlik_fi":  [s_hacim, s_obv, s_yapi_sim,
                                s_rsi_v8, s_rsi_v8, s_cmf_v8, s_cmf_v8, s_mfi_v8, s_fi_ters],
            # V13: OBV'siz + ağırlıklı (iki bağımsız kazananın birleşimi)
            "V13_agirlik_obvsuz": [s_hacim, s_yapi_sim,
                                   s_rsi_v8, s_rsi_v8, s_cmf_v8, s_cmf_v8, s_mfi_v8],
        }
        row = {"fwd": fwd}
        for vn, sigs in variants.items():
            up = sum(1 for s in sigs if s > 0)
            dn = sum(1 for s in sigs if s < 0)
            row[vn] = _bucket(up - dn, len(sigs))
        # V5: V0 net + CMF veto
        up0 = sum(1 for s in variants["V0_mevcut"] if s > 0)
        dn0 = sum(1 for s in variants["V0_mevcut"] if s < 0)
        net0 = up0 - dn0
        if net0 > 0 and cmf_state in ("strong_neg", "turning_up"):
            net0 = 0
        row["V5_cmf_veto"] = _bucket(net0, 4)
        # V0 orijinal panel etiketi (bugünkü haritayla)
        row["v0_panel_label"] = _v0_label(up0, dn0)
        # Durum tabloları
        row["cmf_dual"] = cmf_state
        row["rsi_dual"] = rsi_dual
        row["cum_dual"] = cum_state
        row["mfi_dual"] = mfi_state
        row["fi_div"] = fi_div
        row["relobv"] = relobv_state
        out.append(row)
    return out


def _agg(rows, key):
    """rows içinde key kovalarına göre n / hit / ort. getiri (5-10-20g)."""
    from collections import defaultdict
    acc = defaultdict(lambda: {h: [] for h in HORIZONS})
    for r in rows:
        b = r[key]
        for hz in HORIZONS:
            if r["fwd"][hz] is not None:
                acc[b][hz].append(r["fwd"][hz])
    table = {}
    for b, d in acc.items():
        e = {"n": len(d[5])}
        for hz in HORIZONS:
            a = d[hz]
            if a:
                arr = np.array(a)
                e[f"hit{hz}"] = round(float((arr > 0).mean() * 100), 1)
                e[f"ret{hz}"] = round(float(arr.mean()), 2)
            else:
                e[f"hit{hz}"] = None
                e[f"ret{hz}"] = None
        table[b] = e
    return table


BUCKET_ORDER = ["GÜÇLÜ YUKARI", "YUKARI", "HAFİF YUKARI", "NÖTR",
                "HAFİF AŞAĞI", "AŞAĞI", "GÜÇLÜ AŞAĞI"]
V0_ORDER = ["YUKARI ★", "YUKARI", "HAFİF YUKARI", "KARARSIZ", "ÇELİŞKİLİ",
            "HAFİF AŞAĞI", "AŞAĞI", "AŞAĞI ★"]


def _md_table(table, order):
    lines = ["| Kova | n | hit5% | hit10% | hit20% | ret5 | ret10 | ret20 |",
             "|---|--:|--:|--:|--:|--:|--:|--:|"]
    for b in order:
        if b not in table:
            continue
        e = table[b]
        def _f(x, pct=False):
            return "—" if x is None else (f"{x}" if pct else f"{x:+.2f}")
        lines.append(f"| {b} | {e['n']} | {_f(e['hit5'], True)} | {_f(e['hit10'], True)} "
                     f"| {_f(e['hit20'], True)} | {_f(e['ret5'])} | {_f(e['ret10'])} | {_f(e['ret20'])} |")
    # tabloda olup order'da olmayan kova kalmasın
    for b in table:
        if b not in order:
            e = table[b]
            lines.append(f"| {b} | {e['n']} | {e['hit5']} | {e['hit10']} | {e['hit20']} "
                         f"| {e['ret5']} | {e['ret10']} | {e['ret20']} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0, help="sadece ilk N hisse (hızlı test)")
    ap.add_argument("--step", type=int, default=2, help="gün örnekleme adımı (overlap azaltma)")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(VERI_DIR, "*.IS_1d.parquet")))
    # Endeksleri at (panelin kendi kuralıyla aynı prefix seti)
    files = [f for f in files
             if not os.path.basename(f).split(".")[0].startswith(("XU", "XB", "XT", "XY"))]
    global OUT_JSON, OUT_MD
    if args.sample:
        files = files[: args.sample]
        # ÖNEMLİ: örneklem koşusu (backtest_health smoke) ÜRETİM json'unu
        # EZMESİN — panel kanıt rozeti tam-evren dosyasından okur.
        OUT_JSON = OUT_JSON.replace(".json", ".sample.json")
        OUT_MD = OUT_MD.replace(".md", ".sample.md")

    # Benchmark (rel-OBV için) — bir kez yüklenir
    bench = None
    try:
        bench = pd.read_parquet(os.path.join(VERI_DIR, "XU100.IS_1d.parquet")).sort_index()
    except Exception:
        print("⚠ XU100 benchmark yüklenemedi — rel-OBV oyları 0 kalır")

    all_rows = []
    n_ok = 0
    for k, f in enumerate(files):
        try:
            df = pd.read_parquet(f)
            need = {"Open", "High", "Low", "Close", "Volume"}
            if not need.issubset(df.columns):
                continue
            rows = eval_ticker(df, step=args.step, bench=bench)
            if rows:
                all_rows.extend(rows)
                n_ok += 1
        except Exception:
            continue
        if (k + 1) % 100 == 0:
            print(f"  ... {k + 1}/{len(files)} hisse tarandı, {len(all_rows)} örnek")

    print(f"\nToplam: {n_ok} hisse, {len(all_rows)} örnek (step={args.step})")
    if not all_rows:
        print("Örnek yok — çıkılıyor.")
        return

    result = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
              "n_tickers": n_ok, "n_samples": len(all_rows), "step": args.step,
              "variants": {}, "states": {}, "v0_panel_labels": {}}

    md = [f"# GENEL ÖZET Verdict Backtest — {result['generated']}",
          f"\n{n_ok} hisse · {len(all_rows)} örnek · step={args.step} · "
          f"hit = ileri getiri > 0 olan örnek oranı\n",
          "⚠️ Tek rejim uyarısı: veri ~1 yıl (BIST 2025-26). Sonuçlar bu dönemin "
          "piyasa karakterini yansıtır; farklı rejimde yeniden ölçülmeli.\n"]

    # V0 orijinal panel etiketleri
    t = _agg(all_rows, "v0_panel_label")
    result["v0_panel_labels"] = t
    md.append("## V0 — Panelin bugünkü etiketleri (orijinal harita)\n")
    md.append(_md_table(t, V0_ORDER) + "\n")

    # Varyant kıyası
    for vn in VARIANTS:
        t = _agg(all_rows, vn)
        result["variants"][vn] = t
        md.append(f"## {vn}\n")
        md.append(_md_table(t, BUCKET_ORDER) + "\n")

    # Özet kıyas tablosu: her varyantın YUKARI tarafı (GÜÇLÜ+normal) vs AŞAĞI tarafı
    md.append("## ÖZET — varyant başına yukarı-kova performansı (GÜÇLÜ YUKARI + YUKARI birleşik)\n")
    md.append("| Varyant | n(yukarı) | hit10% | ret10 | n(aşağı) | hit10%(aşağı) | ret10(aşağı) | Ayrım (ret10 fark) |")
    md.append("|---|--:|--:|--:|--:|--:|--:|--:|")
    for vn in VARIANTS:
        t = result["variants"][vn]
        def _merge(buckets):
            ns, r10, h10 = 0, [], []
            for b in buckets:
                if b in t and t[b]["n"] > 0 and t[b]["ret10"] is not None:
                    ns += t[b]["n"]
                    r10.append((t[b]["ret10"], t[b]["n"]))
                    h10.append((t[b]["hit10"], t[b]["n"]))
            if ns == 0:
                return 0, None, None
            ret = sum(r * n for r, n in r10) / ns
            hit = sum(h * n for h, n in h10) / ns
            return ns, round(hit, 1), round(ret, 2)
        n_up, h_up, r_up = _merge(["GÜÇLÜ YUKARI", "YUKARI"])
        n_dn, h_dn, r_dn = _merge(["GÜÇLÜ AŞAĞI", "AŞAĞI"])
        spread = (r_up - r_dn) if (r_up is not None and r_dn is not None) else None
        md.append(f"| {vn} | {n_up} | {h_up if h_up is not None else '—'} | "
                  f"{f'{r_up:+.2f}' if r_up is not None else '—'} | {n_dn} | "
                  f"{h_dn if h_dn is not None else '—'} | "
                  f"{f'{r_dn:+.2f}' if r_dn is not None else '—'} | "
                  f"{f'{spread:+.2f}' if spread is not None else '—'} |")
    md.append("")

    # Durum tabloları (renk/harita doğrulaması)
    STATE_KEYS = {"cmf_dual": "CMF çift pencere (Force Compass)",
                  "rsi_dual": "RSI çift pencere (confluence satırı)",
                  "cum_dual": "Kümülatif delta çift pencere (confluence satırı)",
                  "mfi_dual": "MFI çift pencere (panelde kullanılmıyor — aday)",
                  "fi_div":   "Force Index diverjansı (karne TERS bulgusunun tam-evren teyidi)",
                  "relobv":   "Relative OBV (hisse vs XU100 hacim akışı)"}
    for key, title in STATE_KEYS.items():
        t = _agg(all_rows, key)
        result["states"][key] = t
        md.append(f"## DURUM — {title}\n")
        order = sorted(t, key=lambda b: -(t[b]["ret10"] if t[b]["ret10"] is not None else -99))
        md.append(_md_table(t, order) + "\n")

    with open(OUT_JSON, "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=1)
    with open(OUT_MD, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"Yazıldı: {os.path.basename(OUT_JSON)} + {os.path.basename(OUT_MD)}")


if __name__ == "__main__":
    main()
