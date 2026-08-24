"""Çok ölçekli motorun DOĞRULAMASI — `docs/dogrulama_plani.md`'nin çalışan iskeleti.

Salt okunur: `veriler/*.parquet` okur, `smr_lab/out/` altına yazar. `patron.db`'ye
TEK SATIR bile yazmaz, mevcut hiçbir dosyayı değiştirmez.

Kullanım:
    python smr_lab/validate_multiscale.py --frekans            # H0: durum frekansları
    python smr_lab/validate_multiscale.py --h1                 # H1: uyum+kanaat ayrıştırıyor mu
    python smr_lab/validate_multiscale.py --h4                 # H4: sinyal sağlığı çıkış bilgisi mi
    python smr_lab/validate_multiscale.py --frekans --limit 50 # sadece ilk 50 sembol (deneme)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from smr_lab.multiscale_engine import run_engine  # noqa: E402

VERI = BASE / "veriler"
OUT = BASE / "smr_lab" / "out"

# Mevcut altyapıdan ÖDÜNÇ alınan eşikler (dosyaya dokunulmadan içeri alınır).
try:
    from backtest_runner import FORWARD_WINDOWS, KURUMSAL_ESIK
except Exception:                                   # bağımlılık yoksa aynı değerlerle devam
    FORWARD_WINDOWS = [5, 10, 20]
    KURUMSAL_ESIK = 0.15

MIN_GOZLEM = 1000        # docs/dogrulama_plani.md §3 H0 — altında "ölçülemedi"
MIN_HAM = 20000          # örtüşme düzeltmesi sonrası ham satır tabanı


# ═══════════════════════════════════════════════════════════════════════════
def sembolleri_bul(limit: int | None = None) -> list[str]:
    """Parquet arşivindeki günlük BIST sembollerini listeler (endeksler hariç)."""
    if not VERI.exists():
        return []
    out = []
    for p in sorted(VERI.glob("*_1d.parquet")):
        ad = p.name.replace("_1d.parquet", "")
        if ad.upper().startswith(("XU", "XB", "XT", "XY", "XK", "XG", "XI", "^")):
            continue
        out.append(ad)
    return out[:limit] if limit else out


def _oku(ad: str) -> pd.DataFrame | None:
    p = VERI / f"{ad}_1d.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index)
        return df.sort_index()
    except Exception:
        return None


def ileri_getiri(df: pd.DataFrame, pencereler=FORWARD_WINDOWS) -> pd.DataFrame:
    """Giriş t+1 açılışı, çıkış t+1+N kapanışı. Bölünme penceresine düşen gözlem ÖLÇÜLEMEDİ olur.
    Sıfır saymak yerine boş bırakmak şart: tek bir düzeltilmemiş bölünme ortalamayı tek başına uçurur."""
    o = df["Open"].to_numpy()
    c = df["Close"].to_numpy()
    n = len(df)
    giris = np.full(n, np.nan)
    giris[:-1] = o[1:]                       # t satırının girişi t+1 açılışı

    gunluk = pd.Series(c, index=df.index).pct_change().abs().to_numpy()
    out = pd.DataFrame(index=df.index)
    out["giris"] = giris
    for N in pencereler:
        cikis = np.full(n, np.nan)
        ust = n - 1 - N
        if ust > 0:
            cikis[:ust] = c[1 + N: 1 + N + ust]
        r = cikis / giris - 1.0
        # bölünme bekçisi: giriş ile çıkış arasında %15+ tek günlük hareket varsa ölçülemez
        kirli = pd.Series(gunluk, index=df.index).rolling(N + 1).max().shift(-(N + 1)).to_numpy()
        r = np.where(np.isfinite(kirli) & (kirli >= KURUMSAL_ESIK), np.nan, r)
        out[f"ret_{N}"] = r
    return out


def panel_kur(limit: int | None = None, verbose: bool = True) -> pd.DataFrame:
    """Tüm semboller için motor çıktısı + ileri getiri + XU100 alfasını tek uzun tabloda birleştirir.
    Bu tablo bütün hipotezlerin ortak girdisidir; bir kez kurulup diske yazılır."""
    semboller = sembolleri_bul(limit)
    if not semboller:
        raise SystemExit(f"'{VERI}' altında parquet bulunamadı — bu betik kendi makinende çalıştırılmalı.")

    bench = _oku("XU100.IS")
    if bench is None:
        bench = _oku("XU100")
    b_ret = {}
    if bench is not None:
        b = ileri_getiri(bench)
        for N in FORWARD_WINDOWS:
            b_ret[N] = b[f"ret_{N}"]

    parcalar = []
    t0 = time.perf_counter()
    for i, s in enumerate(semboller, 1):
        df = _oku(s)
        if df is None or len(df) < 320:
            continue
        try:
            res = run_engine(df)
        except Exception:
            continue
        fwd = ileri_getiri(df)
        blok = pd.DataFrame({
            "sembol": s,
            "tarih": res.index,
            "uyum_skoru": res["uyum_skoru"].to_numpy(),
            "kanaat": res["kanaat"].to_numpy(),
            "trend_durum": res["trend_durum"].to_numpy(),
            "verim": res["verim"].to_numpy(),
            "verim_etiket": res["verim_etiket"].to_numpy(),
            "trend_verim_durum": res["trend_verim_durum"].to_numpy(),
            "hacim_bolge": res["hacim_bolge"].to_numpy(),
            "hacim_senaryo": res["hacim_senaryo"].to_numpy(),
            "poc_konsensus": res["poc_konsensus"].to_numpy(),
            "baski_ens": res["baski_ens"].to_numpy(),
            "rejim": res["rejim"].to_numpy(),
            "saglik_durum": res["saglik_durum"].to_numpy(),
            "konfluens": res["konfluens"].to_numpy(),
            "hacim_tl": (df["Close"] * df["Volume"]).to_numpy(),
        })
        for N in FORWARD_WINDOWS:
            r = fwd[f"ret_{N}"].to_numpy()
            blok[f"ret_{N}"] = r
            if N in b_ret:
                br = b_ret[N].reindex(res.index).to_numpy()
                blok[f"alfa_{N}"] = r - br
            else:
                blok[f"alfa_{N}"] = np.nan
        parcalar.append(blok)
        if verbose and i % 50 == 0:
            print(f"  {i}/{len(semboller)} sembol… ({time.perf_counter()-t0:.0f} sn)")

    panel = pd.concat(parcalar, ignore_index=True)
    panel["likidite_dilim"] = pd.qcut(
        panel.groupby("sembol")["hacim_tl"].transform("median"), 5,
        labels=["Q1 en düşük", "Q2", "Q3", "Q4", "Q5 en yüksek"], duplicates="drop")
    if verbose:
        print(f"  → panel: {len(panel):,} satır, {panel['sembol'].nunique()} sembol "
              f"({time.perf_counter()-t0:.0f} sn)")
    return panel


# ═══════════════════════════════════════════════════════════════════════════
def _blok_bootstrap(x: pd.Series, tarih: pd.Series, n_boot: int = 2000,
                    seed: int = 0) -> tuple[float, float, float]:
    """Ortalamanın güven aralığını TARİHE GÖRE kümeleyerek üretir (blok önyükleme).
    Aynı gün tüm BIST birlikte hareket eder — bunu görmezden gelen bir test olmayan anlamlılığı var gösterir."""
    d = pd.DataFrame({"x": x.to_numpy(), "t": tarih.to_numpy()}).dropna()
    if d.empty:
        return np.nan, np.nan, np.nan
    gun = d.groupby("t")["x"].mean()
    g = gun.to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(g), size=(n_boot, len(g)))
    dagilim = g[idx].mean(axis=1)
    return float(g.mean()), float(np.quantile(dagilim, 0.025)), float(np.quantile(dagilim, 0.975))


def _olcut_seti(alt: pd.DataFrame, N: int, maliyet_bps: float = 0.0) -> dict:
    """Bir gruba ait tüm ölçütleri birlikte üretir — isabet oranı TEK BAŞINA asla yeterli değildir."""
    a = alt[f"alfa_{N}"].dropna()
    r = alt[f"ret_{N}"].dropna()
    if len(a) == 0:
        return {"n": 0, "durum": "ölçülemedi"}
    mal = maliyet_bps / 10000.0
    net = r - mal
    kaz = net[net > 0]
    kay = net[net <= 0]
    isabet = float((net > 0).mean())
    ort_kaz = float(kaz.mean()) if len(kaz) else 0.0
    ort_kay = float(abs(kay.mean())) if len(kay) else 0.0
    ort, lo, hi = _blok_bootstrap(alt[f"alfa_{N}"], alt["tarih"])
    return {
        "n": int(len(a)),
        "etkin_n_gun": int(alt.loc[a.index, "tarih"].nunique()),
        "ort_alfa": round(float(a.mean()) * 100, 3),
        "ort_alfa_gun_kumeli": round(ort * 100, 3) if np.isfinite(ort) else None,
        "gua_alt_%95": round(lo * 100, 3) if np.isfinite(lo) else None,
        "gua_ust_%95": round(hi * 100, 3) if np.isfinite(hi) else None,
        "medyan_alfa": round(float(a.median()) * 100, 3),
        "ort_getiri": round(float(r.mean()) * 100, 3),
        "isabet_%": round(isabet * 100, 1),
        "ort_kazanc_%": round(ort_kaz * 100, 2),
        "ort_kayip_%": round(ort_kay * 100, 2),
        "rr": round(ort_kaz / ort_kay, 2) if ort_kay > 0 else None,
        "beklenen_getiri_%": round((isabet * ort_kaz - (1 - isabet) * ort_kay) * 100, 3),
        "kar_faktoru": round(float(kaz.sum() / abs(kay.sum())), 2) if len(kay) and kay.sum() != 0 else None,
        "en_kotu_%5": round(float(np.quantile(r, 0.05)) * 100, 2),
        "durum": "ölçüldü" if len(a) >= MIN_GOZLEM else "ölçülemedi (n<1000)",
    }


# ═══════════════════════════════════════════════════════════════════════════
def h0_frekans(panel: pd.DataFrame) -> dict:
    """H0 — tanımlayıcı rapor. Hipotez testi YOK, sadece frekans; hangi durum test edilebilir onu söyler."""
    rapor = {}
    for kol in ("trend_durum", "kanaat", "verim_etiket", "trend_verim_durum",
                "hacim_bolge", "hacim_senaryo", "saglik_durum"):
        vc = panel[kol].value_counts(dropna=False)
        rapor[kol] = {
            str(k): {"n": int(v), "oran_%": round(100 * v / len(panel), 2),
                     "test_edilebilir": bool(v >= MIN_HAM)}
            for k, v in vc.items()
        }
    return rapor


def h1_uyum_kanaat(panel: pd.DataFrame, maliyet_bps: float = 0.0) -> dict:
    """H1 — uyum +5/+6 ve kanaat YÜKSEK, rastgele girişten farklı mı? Kıyas TARİH EŞLEŞTİRMELİ:
    aynı günlerde tüm evrenin ortalaması alınır, böylece piyasa yönü iki tarafta da aynı olur."""
    out = {}
    for N in FORWARD_WINDOWS:
        kosul = (panel["uyum_skoru"] >= 5) & (panel["kanaat"] == "YÜKSEK")
        grup = panel[kosul]
        gunler = grup["tarih"].unique()
        kiyas = panel[panel["tarih"].isin(gunler)]
        out[f"{N}g"] = {
            "kosul": "uyum_skoru >= +5 ve kanaat = YÜKSEK",
            "grup": _olcut_seti(grup, N, maliyet_bps),
            "kiyas_ayni_gun_tum_evren": _olcut_seti(kiyas, N, maliyet_bps),
        }
        neg = panel[(panel["uyum_skoru"] <= -5) & (panel["kanaat"] == "YÜKSEK")]
        out[f"{N}g"]["asagi_yon"] = _olcut_seti(neg, N, maliyet_bps)
        if "likidite_dilim" in panel.columns:
            out[f"{N}g"]["likidite_dilimleri"] = {
                str(q): _olcut_seti(grup[grup["likidite_dilim"] == q], N, maliyet_bps)
                for q in panel["likidite_dilim"].dropna().unique()
            }
    return out


def h4_saglik(panel: pd.DataFrame, maliyet_bps: float = 0.0) -> dict:
    """H4 — sağlığı ZAYIFLIYOR'a düşen açık sinyaller, TEYİTLİ kalanlardan kötü mü sonlanıyor?
    Doğruysa doğrudan bir ÇIKIŞ kuralı doğar — SMR'nin en zayıf tarafı tam olarak orası."""
    out = {}
    acik = panel[panel["rejim"] != 0]
    for N in FORWARD_WINDOWS:
        out[f"{N}g"] = {
            d: _olcut_seti(acik[acik["saglik_durum"] == d], N, maliyet_bps)
            for d in ("TEYİTLİ", "KORUNUYOR", "ZAYIFLIYOR", "ÇELİŞKİLİ")
        }
    return out


# ═══════════════════════════════════════════════════════════════════════════
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Çok ölçekli motor doğrulaması (salt okunur)")
    ap.add_argument("--frekans", action="store_true", help="H0 tanımlayıcı frekans raporu")
    ap.add_argument("--h1", action="store_true", help="H1 uyum + kanaat testi")
    ap.add_argument("--h4", action="store_true", help="H4 sinyal sağlığı testi")
    ap.add_argument("--limit", type=int, default=None, help="sadece ilk N sembol")
    ap.add_argument("--maliyet-bps", type=float, default=0.0, help="işlem maliyeti (baz puan)")
    a = ap.parse_args(argv)

    if not (a.frekans or a.h1 or a.h4):
        ap.print_help()
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    print("Panel kuruluyor (motor tüm sembollerde koşuyor)…")
    panel = panel_kur(a.limit)
    panel.to_parquet(OUT / "panel.parquet")
    print(f"  panel kaydedildi → {OUT/'panel.parquet'}")

    sonuc = {"satir": int(len(panel)), "sembol": int(panel["sembol"].nunique()),
             "maliyet_bps": a.maliyet_bps}
    if a.frekans:
        sonuc["H0_frekans"] = h0_frekans(panel)
    if a.h1:
        sonuc["H1_uyum_kanaat"] = h1_uyum_kanaat(panel, a.maliyet_bps)
    if a.h4:
        sonuc["H4_saglik"] = h4_saglik(panel, a.maliyet_bps)

    p = OUT / "sonuclar.json"
    p.write_text(json.dumps(sonuc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSonuçlar → {p}")
    print("\n⚠ HATIRLATMA: H1–H5 ailesinde Bonferroni eşiği α = 0.01'dir (5 test).")
    print("  Güven aralığı sıfırı içeriyorsa sonuç 'kanıt yok'tur — 'az kaldı' değil.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
