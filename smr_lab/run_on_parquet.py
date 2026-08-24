"""Motoru KENDİ verinle çalıştırır — parquet önbelleğinden okur, hiçbir şeye yazmaz.

Kullanım:
    python smr_lab/run_on_parquet.py THYAO.IS
    python smr_lab/run_on_parquet.py THYAO.IS ASELS.IS EREGL.IS
    python smr_lab/run_on_parquet.py --kalibre THYAO.IS ASELS.IS ...   (ölü bölge eşiğini ölçer)

Salt okunur: `veriler/<TICKER>_1d.parquet` dosyalarını okur, hiçbir dosyayı değiştirmez.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smr_lab.multiscale_engine import calibrate_theta, run_engine  # noqa: E402

VERI = Path(__file__).resolve().parent.parent / "veriler"
GOSTER = ["uyum_skoru", "kanaat", "trend_durum", "verim", "verim_etiket", "verim_ivme",
          "trend_verim_durum", "hacim_bolge", "poc_konsensus", "poc_merkez",
          "hacim_senaryo", "baski_ens_100", "boyut_uyum", "rejim", "saglik", "saglik_durum",
          "konfluens"]


def yukle(ticker: str) -> pd.DataFrame | None:
    for ad in (f"{ticker}_1d.parquet", f"{ticker}.IS_1d.parquet", f"{ticker.upper()}_1d.parquet"):
        p = VERI / ad
        if p.exists():
            df = pd.read_parquet(p)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df
    return None


def main(argv: list[str]) -> int:
    kalibre = "--kalibre" in argv
    tickers = [a for a in argv if not a.startswith("--")]
    if not tickers:
        print(__doc__)
        return 1
    if not VERI.exists():
        print(f"HATA: '{VERI}' klasörü yok. Bu betik kendi bilgisayarında/sunucunda çalıştırılmalı.")
        return 2

    frames = {}
    for t in tickers:
        df = yukle(t)
        if df is None:
            print(f"  ! {t}: parquet bulunamadı, atlandı")
            continue
        frames[t] = df

    if not frames:
        print("Hiçbir sembol yüklenemedi.")
        return 2

    if kalibre:
        th = calibrate_theta(list(frames.values()))
        print(f"\nÖNERİLEN ÖLÜ BÖLGE EŞİĞİ (theta): {th:.4f}")
        print(f"  ({len(frames)} sembolün eğim dağılımından, %25 ölü bölge hedefiyle;")
        print("   getiri verisine HİÇ bakılmadı — bu bir dağılım kalibrasyonudur)")
        return 0

    for t, df in frames.items():
        try:
            res = run_engine(df)
        except Exception as e:
            print(f"  ! {t}: {e}")
            continue
        meta = res.attrs["meta"]
        son = res.dropna(subset=["uyum_skoru"]).tail(1)
        print(f"\n{'='*70}\n{t}   ({meta['n_bar']} bar, "
              f"tam güç: {'evet' if meta['tam_guc'] else 'HAYIR'}, "
              f"hacim: {'var' if meta['hacim_gecerli'] else 'YOK'})")
        if son.empty:
            print("  yeterli veri yok")
            continue
        for k in GOSTER:
            if k in son.columns:
                v = son[k].iloc[0]
                if isinstance(v, float):
                    v = "—" if pd.isna(v) else f"{v:,.2f}"
                print(f"  {k:22s}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
