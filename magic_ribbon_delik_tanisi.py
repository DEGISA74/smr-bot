# -*- coding: utf-8 -*-
"""Delik filtresi neden isareti cevirdi? Takvim yanliligi sinavi."""
import collections
import numpy as np
import pandas as pd
from magic_ribbon_core import load_bist100_symbols
from magic_ribbon_session_backtest import _read, _return, HORIZONS, DEFAULT_COST
from magic_ribbon_session_data import contiguous_prev_mask, session_block_ids

H = 20  # T+10
ham_ay = collections.Counter(); temiz_ay = collections.Counter()
ham_get = []; temiz_get = []
ham_sin = []; temiz_sin = []

for symbol in sorted(load_bist100_symbols()):
    frame = _read(symbol)
    if frame is None:
        continue
    mask = contiguous_prev_mask(frame).to_numpy(dtype=bool)
    blocks = session_block_ids(frame).to_numpy()
    valid = frame[["fast_line", "slow_line"]].notna().all(axis=1).to_numpy(dtype=bool)
    trig = frame["up_trigger"].to_numpy(dtype=bool)
    idx = frame.index
    for i in range(len(frame) - 1):
        if not valid[i]:
            continue
        e = i + 1
        entry = float(frame["Open"].iloc[e])
        if not np.isfinite(entry) or entry <= 0:
            continue
        x = e + H - 1
        if x >= len(frame):
            continue
        v = _return(entry, float(frame["Close"].iloc[x]), DEFAULT_COST)
        ay = idx[e].strftime("%Y-%m")
        temiz = bool(mask[i]) and blocks[i] == blocks[x]
        ham_ay[ay] += 1; ham_get.append(v)
        if trig[i]:
            ham_sin.append(v)
        if temiz:
            temiz_ay[ay] += 1; temiz_get.append(v)
            if trig[i]:
                temiz_sin.append(v)

print("=== T+10 GIRIS AYI DAGILIMI (taban penceresi) ===")
print(f"{'ay':<9} {'ham':>7} {'kesintisiz':>11} {'kalan %':>9}")
for ay in sorted(ham_ay):
    h, t = ham_ay[ay], temiz_ay.get(ay, 0)
    print(f"{ay:<9} {h:>7} {t:>11} {100*t/h:>8.1f}%")

def ozet(ad, a):
    s = pd.Series(a, dtype=float)
    se = s.std(ddof=1) / np.sqrt(len(s)) if len(s) > 1 else float("nan")
    print(f"{ad:<22} N={len(s):>5}  ort={s.mean():>7.3f}  std={s.std(ddof=1):>6.2f}  "
          f"std.hata={se:>5.3f}  t={s.mean()/se if se else float('nan'):>6.2f}")

print("\n=== T+10 GETIRI OZETI ===")
ozet("ham sinyal", ham_sin); ozet("ham taban", ham_get)
ozet("kesintisiz sinyal", temiz_sin); ozet("kesintisiz taban", temiz_get)

# Alfanin standart hatasi (bagimsizlik VARSAYIMIYLA - ust sinir iyimser)
for ad, s_, t_ in (("ham", ham_sin, ham_get), ("kesintisiz", temiz_sin, temiz_get)):
    s = pd.Series(s_, dtype=float); t = pd.Series(t_, dtype=float)
    se = np.sqrt(s.var(ddof=1)/len(s) + t.var(ddof=1)/len(t))
    d = s.mean() - t.mean()
    print(f"{ad:<12} alfa={d:>7.3f}  std.hata={se:>5.3f}  t={d/se:>6.2f}  "
          f"(ortusen pencere yok sayildi - GERCEK t bundan KUCUK)")
