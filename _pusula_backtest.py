# -*- coding: utf-8 -*-
"""
_pusula_backtest.py — PİYASA PUSULASI ARKETİPLERİ İŞE YARIYOR MU?
==================================================================
Soru: pusula_engine'in 17 arketipinden hangisi ileri getiriyi AYIRIYOR?
Motor sezgiyle yazıldı, hiç ölçülmedi (AJAN_KURALLARI §2.1).

Yöntem (`_4s_filtre_backtest.py` kalıbı, look-ahead YOK):
  1. scan_signals × signal_returns → T+5 / T+10 / T+20 tek satırda
  2. Her sinyal için günlük seri SİNYAL GÜNÜNE KADAR kesilir
  3. O günün feature snapshot'ı (f_rsi / f_sfp_* / f_cmf_dual) scan_signals'tan
     okunur — sinyal anında kaydedilmiş değer, sonradan hesaplanmış değil
  4. cizgi_yapi.gorunum kesilmiş seriyle çağrılır (çizgi/formasyon dalları için)
  5. synthesize_market_compass → archetype
  6. Arketip bazında ortalama getiri / isabet / örneklem, üç vadede birden

ÖLÇÜLEMEYEN DAL: RSI_POZ_DIV — `f_rsi_div_pos` kolonu scan_signals'ta YOK ve
terazi karşı-oyu geçmişe dönük üretilemiyor. Raporda ayrıca belirtilir.

Kullanım:
    python _pusula_backtest.py                    # 2026 başından, çizgi dahil
    python _pusula_backtest.py --hizli            # çizgi hesabı KAPALI (~10x hızlı)
    python _pusula_backtest.py --baslangic 2026-03-01
    python _pusula_backtest.py --limit 500        # ilk N sinyal (deneme)
"""
from __future__ import annotations

import os
import sqlite3
import sys
import warnings
from collections import defaultdict

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import pusula_engine as pe  # noqa: E402

try:
    import cizgi_yapi
except Exception:
    cizgi_yapi = None

DB = os.path.join(BASE, "patron.db")
GUNLUK_DIR = os.path.join(BASE, "veriler")

# Motorun gerçekten okuduğu feature anahtarları (grep ile doğrulandı)
FEAT_KOLONLARI = ["f_rsi", "f_sfp_bear", "f_sfp_bull", "f_cmf_dual"]

VADELER = (5, 10, 20)


def _arg(ad, varsayilan=None):
    if ad in sys.argv:
        i = sys.argv.index(ad)
        if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
            return sys.argv[i + 1]
        return True
    return varsayilan


def sinyalleri_al(baslangic: str, limit=None) -> pd.DataFrame:
    """Üç vadenin getirisi de olgunlaşmış sinyaller + o günün feature snapshot'ı."""
    feat_sec = ", ".join(f"s.{k}" for k in FEAT_KOLONLARI)
    q = f"""
        SELECT s.id, s.scan_date, s.symbol, s.scan_type, {feat_sec},
               MAX(CASE WHEN r.day_offset = 5  THEN r.return_pct END) AS r5,
               MAX(CASE WHEN r.day_offset = 10 THEN r.return_pct END) AS r10,
               MAX(CASE WHEN r.day_offset = 20 THEN r.return_pct END) AS r20
        FROM scan_signals s
        JOIN signal_returns r ON r.signal_id = s.id
        WHERE s.scan_date >= ?
          AND s.symbol LIKE '%.IS'
          AND r.day_offset IN (5, 10, 20)
        GROUP BY s.id
        HAVING r5 IS NOT NULL AND r10 IS NOT NULL AND r20 IS NOT NULL
        ORDER BY s.scan_date
    """
    with sqlite3.connect(DB) as c:
        df = pd.read_sql(q, c, params=(baslangic,))
    if limit:
        df = df.head(int(limit))
    return df


def gunluk_depo_yukle(semboller) -> dict:
    depo = {}
    for sym in semboller:
        yol = os.path.join(GUNLUK_DIR, f"{sym}_1d.parquet")
        if not os.path.exists(yol):
            continue
        try:
            d = pd.read_parquet(yol)
            if d is None or d.empty:
                continue
            idx = pd.to_datetime(d.index)
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_localize(None)
            d = d.copy()
            d.index = idx
            depo[sym] = d.sort_index()
        except Exception:
            continue
    return depo


def ozet(baslik, gruplar, baseline, min_n=1):
    """gruplar: {arketip: {'r5': [...], 'r10': [...], 'r20': [...]}}"""
    print(f"\n{baslik}")
    print("=" * 92)
    print(f"{'arketip':<24}{'N':>7}{'T+5':>11}{'T+10':>11}{'T+20':>11}"
          f"{'isabet20':>11}{'hüküm':>16}")
    print("-" * 92)

    satirlar = []
    for ad, v in gruplar.items():
        n = len(v["r20"])
        if n < min_n:
            continue
        f5 = np.mean(v["r5"]) - baseline["r5"]
        f10 = np.mean(v["r10"]) - baseline["r10"]
        f20 = np.mean(v["r20"]) - baseline["r20"]
        isabet = (np.array(v["r20"]) > 0).mean() * 100
        # Tutarlılık: üç vade de aynı işaretli mi? (AJAN_KURALLARI §2.1)
        isaretler = [np.sign(x) for x in (f5, f10, f20)]
        if n < 30:
            hukum = "örneklem az"
        elif all(s > 0 for s in isaretler):
            hukum = "TUTARLI +"
        elif all(s < 0 for s in isaretler):
            hukum = "TUTARLI -"
        elif max(abs(f5), abs(f10), abs(f20)) < 0.35:
            hukum = "ayrım yok"
        else:
            hukum = "İŞARET DEĞİŞİR"
        satirlar.append((n, ad, f5, f10, f20, isabet, hukum))

    for n, ad, f5, f10, f20, isabet, hukum in sorted(satirlar, key=lambda x: -x[0]):
        print(f"{ad:<24}{n:>7}{f5:>+10.2f}{f10:>+11.2f}{f20:>+11.2f}"
              f"{isabet:>10.1f}%{hukum:>16}")
    print("-" * 92)
    print(f"{'BASELINE (ham ort.)':<24}{'':>7}{baseline['r5']:>+10.2f}"
          f"{baseline['r10']:>+11.2f}{baseline['r20']:>+11.2f}")


def main() -> int:
    baslangic = _arg("--baslangic", "2026-01-01")
    hizli = bool(_arg("--hizli"))
    limit = _arg("--limit")

    print("PUSULA ARKETİP KARNESİ")
    print(f"başlangıç: {baslangic} · çizgi hesabı: {'KAPALI (--hizli)' if hizli else 'AÇIK'}")
    print("=" * 92)

    df = sinyalleri_al(baslangic, limit)
    if df.empty:
        print("Sinyal yok.")
        return 1
    print(f"Üç vadesi de olgunlaşmış sinyal: {len(df):,}")

    depo = gunluk_depo_yukle(sorted(df["symbol"].unique()))
    df = df[df["symbol"].isin(depo.keys())].copy()
    print(f"Günlük verisi olan               : {len(df):,}")
    if df.empty:
        return 1

    baseline = {f"r{v}": float(df[f"r{v}"].mean()) for v in VADELER}

    gruplar = defaultdict(lambda: {"r5": [], "r10": [], "r20": []})
    atlanan = 0
    toplam = len(df)

    for i, (_, r) in enumerate(df.iterrows(), 1):
        if i % 250 == 0:
            print(f"  ... {i:,}/{toplam:,}")
        sym = r["symbol"]
        tarih = pd.Timestamp(str(r["scan_date"])) + pd.Timedelta(hours=23, minutes=59)
        d = depo[sym]
        kesik = d.loc[d.index <= tarih]
        if len(kesik) < 210:          # 200 SMA dalları için yeterli geçmiş
            atlanan += 1
            continue

        feat = {k: r[k] for k in FEAT_KOLONLARI if pd.notna(r.get(k))}

        cizgi_view = None
        if not hizli and cizgi_yapi is not None:
            try:
                cizgi_view = cizgi_yapi.gorunum(kesik, ticker=sym, timeframe="1d")
            except Exception:
                cizgi_view = None

        try:
            out = pe.synthesize_market_compass(sym, kesik, cizgi_view=cizgi_view, feat=feat)
        except Exception:
            atlanan += 1
            continue

        ark = out.get("archetype") or "YOK"
        for v in VADELER:
            gruplar[ark][f"r{v}"].append(float(r[f"r{v}"]))

    ozet("ARKETİP BAZINDA — ortalamaya fark (puan)", gruplar, baseline)

    if atlanan:
        print(f"\n(geçmişi yetersiz / hata nedeniyle atlanan: {atlanan:,})")

    print("""
OKUMA
  TUTARLI +/-   : üç vadede de aynı işaret → gerçek ayrım adayı
  İŞARET DEĞİŞİR: vadeye göre yön değişiyor → gürültü imzası
  ayrım yok     : üç vadede de |fark| < 0.35 puan → etiket bilgi taşımıyor
  örneklem az   : N < 30 → hüküm verilmez, hipotez kalır

NOT: RSI_POZ_DIV dalı ölçülemedi (f_rsi_div_pos kolonu scan_signals'ta yok).
KARAR KURALI (AJAN_KURALLARI §2.1-2.2): ayrım yok / işaret değişir → sustur.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
