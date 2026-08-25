# -*- coding: utf-8 -*-
"""
_4s_filtre_backtest.py — 4S ZAMANLAMA FİLTRESİ İŞE YARIYOR MU?
==============================================================
Soru: Günlük taramalardan çıkan sinyallerde, 4 saatlik momentum durumu
ileri getiriyi AYIRIYOR MU? Yani "4S şişkinken girme" freni gerçekten
kötü girişleri eliyor mu, yoksa süs mü?

Yöntem (look-ahead YOK):
  1. patron.db → scan_signals × signal_returns (day_offset=20) JOIN
  2. Her sinyal için sembolün 4S parquet'i SİNYAL GÜNÜNE KADAR kesilir
     (o günün kapanışına kadar olan barlar; sonrası görülmez)
  3. Kesilmiş seriyle zamanlama_core.evaluate_4s_timing çağrılır
  4. Durum bazında ortalama getiri / isabet / örneklem karşılaştırılır
  5. Aynı JOIN'de gunluk_kapi_gecti da ölçülür (iki kademe ayrı ayrı)

Kullanım:
    python _4s_filtre_backtest.py                # T+20, 2026 başından
    python _4s_filtre_backtest.py --gun 10       # T+10
    python _4s_filtre_backtest.py --baslangic 2026-03-01
"""
from __future__ import annotations

import os
import sqlite3
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import zamanlama_core as zc  # noqa: E402

DB = os.path.join(BASE, "patron.db")
DEPO_4S = os.path.join(BASE, "veriler_4s")


def _arg(ad, varsayilan):
    if ad in sys.argv:
        return sys.argv[sys.argv.index(ad) + 1]
    return varsayilan


def sinyalleri_al(gun_offset: int, baslangic: str) -> pd.DataFrame:
    """scan_signals × signal_returns — ileri getirisi olgunlaşmış sinyaller."""
    q = """
        SELECT s.id, s.scan_date, s.symbol, s.scan_type, s.entry_price,
               r.return_pct
        FROM scan_signals s
        JOIN signal_returns r
          ON r.signal_id = s.id AND r.day_offset = ?
        WHERE s.scan_date >= ?
          AND r.return_pct IS NOT NULL
          AND s.symbol LIKE '%.IS'
    """
    with sqlite3.connect(DB) as c:
        df = pd.read_sql(q, c, params=(gun_offset, baslangic))
    return df


def _4s_depo_yukle(semboller) -> dict:
    """Gerekli sembollerin 4S serilerini bir kez belleğe al (tekrar okuma yok)."""
    depo = {}
    for sym in semboller:
        yol = os.path.join(DEPO_4S, f"{sym}_4h.parquet")
        if not os.path.exists(yol):
            continue
        try:
            d = pd.read_parquet(yol)
            if d is None or d.empty:
                continue
            idx = pd.to_datetime(d.index)
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_localize(None)      # kıyas için saat dilimini düşür
            d = d.copy()
            d.index = idx
            depo[sym] = d.sort_index()
        except Exception:
            continue
    return depo


def _gunluk_depo_yukle(semboller) -> dict:
    """Günlük kapı için günlük parquet'ler (veriler/ altından)."""
    depo = {}
    gunluk_dir = os.path.join(BASE, "veriler")
    for sym in semboller:
        for ad in (f"{sym}_1d.parquet", f"{sym}.parquet"):
            yol = os.path.join(gunluk_dir, ad)
            if os.path.exists(yol):
                try:
                    d = pd.read_parquet(yol)
                    if d is not None and not d.empty:
                        idx = pd.to_datetime(d.index)
                        if getattr(idx, "tz", None) is not None:
                            idx = idx.tz_localize(None)
                        d = d.copy()
                        d.index = idx
                        depo[sym] = d.sort_index()
                except Exception:
                    pass
                break
    return depo


def ozet(baslik: str, gruplar: dict, baseline: float):
    print(f"\n{baslik}")
    print("-" * 78)
    print(f"{'durum':<24}{'N':>8}{'ort.getiri':>13}{'isabet':>10}{'baseline farkı':>16}")
    print("-" * 78)
    for ad, degerler in sorted(gruplar.items(), key=lambda x: -len(x[1])):
        if not degerler:
            continue
        a = np.array(degerler, dtype=float)
        ort = a.mean()
        isabet = (a > 0).mean() * 100
        print(f"{ad:<24}{len(a):>8}{ort:>12.2f}%{isabet:>9.1f}%{ort - baseline:>15.2f}")
    print("-" * 78)
    print(f"{'BASELINE (tüm sinyaller)':<24}{'':>8}{baseline:>12.2f}%")


def main() -> int:
    gun = int(_arg("--gun", "20"))
    baslangic = _arg("--baslangic", "2026-01-01")

    print(f"4S FİLTRE KARNESİ — T+{gun} · {baslangic} sonrası")
    print("=" * 78)

    df = sinyalleri_al(gun, baslangic)
    if df.empty:
        print("Sinyal bulunamadı.")
        return 1
    print(f"Ham sinyal (getirisi olgunlaşmış): {len(df):,}")

    semboller = sorted(df["symbol"].unique())
    depo4s = _4s_depo_yukle(semboller)
    depo1g = _gunluk_depo_yukle(semboller)
    print(f"4S verisi olan sembol: {len(depo4s)} / {len(semboller)}")
    print(f"Günlük verisi olan   : {len(depo1g)} / {len(semboller)}")

    df = df[df["symbol"].isin(depo4s.keys())].copy()
    print(f"Ölçüme giren sinyal  : {len(df):,}")
    if df.empty:
        return 1

    baseline = float(df["return_pct"].mean())

    g_4s = defaultdict(list)
    g_kapi = defaultdict(list)
    g_birlesik = defaultdict(list)
    atlanan = 0

    for _, r in df.iterrows():
        sym = r["symbol"]
        tarih = pd.Timestamp(str(r["scan_date"])) + pd.Timedelta(hours=23, minutes=59)
        ret = float(r["return_pct"])

        # --- 4S durumu (sinyal gününe kadar kesilmiş seri) ---
        d4 = depo4s[sym]
        kesik = d4.loc[d4.index <= tarih]
        if len(kesik) < 20:
            atlanan += 1
            continue
        durum = zc.evaluate_4s_timing(sym, df_4s=kesik).get("status", "YOK")
        g_4s[durum].append(ret)

        # --- Günlük kapı (aynı ana kadar kesilmiş günlük seri) ---
        d1 = depo1g.get(sym)
        if d1 is not None:
            k1 = d1.loc[d1.index <= tarih]
            kapi = zc.gunluk_kapi_gecti(k1) if len(k1) >= 50 else None
        else:
            kapi = None
        if kapi is not None:
            g_kapi["kapı GEÇTİ" if kapi else "kapı GEÇEMEDİ"].append(ret)
            if kapi:
                g_birlesik[f"kapi GECTI + {durum}"].append(ret)

    ozet(f"1) 4S DURUMUNA GÖRE (T+{gun})", g_4s, baseline)
    ozet(f"2) GÜNLÜK KAPIYA GÖRE (T+{gun})", g_kapi, baseline)
    ozet(f"3) İKİ KADEME BİRLİKTE (T+{gun})", g_birlesik, baseline)

    if atlanan:
        print(f"\n(4S geçmişi yetersiz olduğu için atlanan: {atlanan:,})")

    print("\nOKUMA: 'baseline farkı' pozitifse o grup ortalamayı yeniyor.")
    print("Fren işe yarıyorsa ŞİŞKİN grubu baseline'ın ALTINDA olmalı.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
