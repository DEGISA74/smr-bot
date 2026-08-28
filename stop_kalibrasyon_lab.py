"""İŞ 8 AŞAMA 2 — üç sınır (hedef / stop / azami bekleme) kalibrasyonu.

Aşama 1'in olay x seans yol kaydını okur, ÜZERİNE sınır uygular. Salt-okunur
laboratuvar: canlı tarama, politika, evidence, ekran ve VPS'e dokunmaz.

MÜHÜRLER (sonuçlar GÖRÜLMEDEN yazıldı — 28 Ağu 2026):
  1. Stop merdiveni  : -5, -8, -10, -15 (girişten yüzde). -10 çapası Aşama 1'de
                       zaten adlandırıldı ("%35-38 oranında %10'dan fazla aleyhe").
  2. Hedef merdiveni : +5, +8, +10, +15. Stop merdiveninin simetriği; seçilmedi.
  3. Azami bekleme   : 20 seans (Aşama 1 veri sınırı, zaten sabit).
  4. Aynı gün belirsizliği: günlük barda hem stop hem hedef delinmişse STOP önce
                       sayılır (kötümser). Gün içi sıra bilinemez.
  5. Doldurma       : tam seviyeden. Boşluklu açılış kaymasi ÖLÇÜLMEZ (rapora şerh).
  6. Rejim başına N < 150 -> BELİRSİZ.
  7. Kazanan kombinasyon SEÇİLMEZ. Tüm ızgara raporlanır.
"""
from __future__ import annotations
import json, warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "logs" / "degisken_vade_asama1_events.csv"
OUT_CSV = ROOT / "logs" / "stop_kalibrasyon.csv"
OUT_MD = ROOT / "logs" / "stop_kalibrasyon.md"

STOPS = (5.0, 8.0, 10.0, 15.0)
TARGETS = (5.0, 8.0, 10.0, 15.0)
MAX_HOLD = 20
MIN_N = 150


def _load():
    df = pd.read_csv(SRC, usecols=["event_id", "scan_type", "regime", "bias", "day",
                                   "best_raw", "worst_raw", "close_raw"])
    df = df[(df.day >= 1) & (df.day <= MAX_HOLD)]
    codes, uniq = pd.factorize(df.event_id, sort=True)
    n = len(uniq)
    B = np.full((n, MAX_HOLD), np.nan)
    W = np.full((n, MAX_HOLD), np.nan)
    C = np.full((n, MAX_HOLD), np.nan)
    d = df.day.to_numpy() - 1
    B[codes, d] = df.best_raw.to_numpy()
    W[codes, d] = df.worst_raw.to_numpy()
    C[codes, d] = df.close_raw.to_numpy()
    meta = (df[df.day == 1].set_index("event_id").loc[uniq, ["scan_type", "regime", "bias"]]
            .reset_index())
    return B, W, C, meta


def _first_true(mask):
    """satir basina ilk True indeksi; yoksa -1"""
    any_ = mask.any(axis=1)
    idx = mask.argmax(axis=1)
    return np.where(any_, idx, -1)


def simulate(B, W, C, stop, target):
    valid = ~np.isnan(C)
    last = valid.shape[1] - 1 - np.argmax(valid[:, ::-1], axis=1)
    s_day = _first_true(np.nan_to_num(W, nan=0.0) <= -stop)
    t_day = _first_true(np.nan_to_num(B, nan=0.0) >= target)
    # gecerli gun sinirini asan tetikler yok sayilir
    s_day = np.where((s_day >= 0) & (s_day <= last), s_day, -1)
    t_day = np.where((t_day >= 0) & (t_day <= last), t_day, -1)
    stop_first = (s_day >= 0) & ((t_day < 0) | (s_day <= t_day))   # ayni gun -> STOP
    tgt_first = (~stop_first) & (t_day >= 0)
    ret = C[np.arange(len(C)), last].copy()
    ret[stop_first] = -stop
    ret[tgt_first] = target
    days = last + 1
    days = np.where(stop_first, s_day + 1, days)
    days = np.where(tgt_first, t_day + 1, days)
    kind = np.where(stop_first, "stop", np.where(tgt_first, "hedef", "sure"))
    return ret, days, kind


def _agg(ret, days, kind):
    n = len(ret)
    if n == 0:
        return {}
    return {"N": n, "ortanca": float(np.median(ret)), "ortalama": float(ret.mean()),
            "isabet": float((ret > 0).mean() * 100), "ort_tutma_gun": float(days.mean()),
            "stop_orani": float((kind == "stop").mean() * 100),
            "hedef_orani": float((kind == "hedef").mean() * 100),
            "sure_orani": float((kind == "sure").mean() * 100)}


def main():
    B, W, C, meta = _load()
    print(f"olay {len(meta):,} · tarama {meta.scan_type.nunique()} · yon {dict(meta.bias.value_counts())}")
    rows = []
    groups = [("__TUM_HAVUZ__", r, meta.regime.to_numpy() == r) for r in ("YUKSELEN", "DUSEN")]
    for sc in sorted(meta.scan_type.unique()):
        for r in ("YUKSELEN", "DUSEN"):
            groups.append((sc, r, (meta.scan_type.to_numpy() == sc) & (meta.regime.to_numpy() == r)))
    # sinirsiz referans
    for name, reg, m in groups:
        if not m.any():
            continue
        valid = ~np.isnan(C[m])
        last = valid.shape[1] - 1 - np.argmax(valid[:, ::-1], axis=1)
        ret = C[m][np.arange(m.sum()), last]
        a = _agg(ret, last + 1, np.full(m.sum(), "sure"))
        rows.append({"tarama": name, "rejim": reg, "stop": None, "hedef": None,
                     "durum": "YETERLİ ÖRNEKLEM" if a["N"] >= MIN_N else "BELİRSİZ", **a})
    for stop in STOPS:
        for target in TARGETS:
            ret_all, days_all, kind_all = simulate(B, W, C, stop, target)
            for name, reg, m in groups:
                if not m.any():
                    continue
                a = _agg(ret_all[m], days_all[m], kind_all[m])
                rows.append({"tarama": name, "rejim": reg, "stop": -stop, "hedef": target,
                             "durum": "YETERLİ ÖRNEKLEM" if a["N"] >= MIN_N else "BELİRSİZ", **a})
    out = pd.DataFrame(rows)
    out.to_csv(OUT_CSV, index=False)
    print(f"YAZILDI: {OUT_CSV}  ({len(out)} satir)")
    return out


if __name__ == "__main__":
    main()
