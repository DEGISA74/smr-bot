"""Çok ölçekli motorun hız ve bellek ölçümü — 800 hisselik tarama yükünü kaldırıyor mu?

Sentetik veriyle çalışır (gerçek parquet'e ihtiyaç duymaz), böylece her makinede tekrarlanabilir.
Çalıştırma:  python smr_lab/bench_multiscale.py [hisse_sayisi] [bar_sayisi]
"""
from __future__ import annotations

import sys
import time
import tracemalloc
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smr_lab.multiscale_engine import regime_hysteresis, run_engine  # noqa: E402


def _fake(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0004, 0.021, n)
    c = 40 * np.exp(np.cumsum(r))
    o = c * np.exp(rng.normal(0, 0.005, n))
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 0.007, n)))
    l = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 0.007, n)))
    v = rng.lognormal(13.5, 0.7, n)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v},
                        index=pd.bdate_range("2019-01-02", periods=n))


def main(n_stock: int = 800, n_bar: int = 1250) -> None:
    print(f"Ölçüm: {n_stock} hisse × {n_bar} bar (≈5 yıl günlük)\n")

    df = _fake(n_bar, 0)
    tracemalloc.start()
    t0 = time.perf_counter()
    res = run_engine(df)
    tek = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"  tek hisse           : {tek*1000:7.1f} ms")
    print(f"  tepe bellek (1 hisse): {peak/1e6:7.1f} MB")
    print(f"  çıktı kolonu        : {len(res.columns)}")

    k = 25
    t0 = time.perf_counter()
    for i in range(k):
        run_engine(_fake(n_bar, i + 1))
    ort = (time.perf_counter() - t0) / k
    print(f"\n  {k} hisse ortalaması : {ort*1000:7.1f} ms/hisse")
    print(f"  → {n_stock} hisse tahmini: {ort*n_stock:7.1f} sn (tek çekirdek, seri)")

    # histerezis döngüsünün payı
    p = res["baski_ens"]
    t0 = time.perf_counter()
    for _ in range(20):
        regime_hysteresis(p)
    h = (time.perf_counter() - t0) / 20
    print(f"\n  histerezis durum makinesi: {h*1000:6.2f} ms/hisse "
          f"(toplamın %{100*h/ort:.1f}'i) — tek satır döngüsü burada")
    print(f"  → {n_stock} hisse için histerezis payı: {h*n_stock:.1f} sn")


if __name__ == "__main__":
    a = int(sys.argv[1]) if len(sys.argv) > 1 else 800
    b = int(sys.argv[2]) if len(sys.argv) > 2 else 1250
    main(a, b)
