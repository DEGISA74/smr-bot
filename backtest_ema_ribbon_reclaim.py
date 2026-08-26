"""
SMR — EMA Ribbon Reclaim Backtest (standalone)

Strateji:
  - EMA 5/8/13 günlük üstel hareketli ortalama
  - Fiyat üçünün de ALTINDA en az 3 ardışık gün
  - 4. günde tek bar üçünü de yukarı keser + üstünde kapatır → KAPANIŞTA AL
  - Rejim filtresi: XU100 Close > XU100 SMA50 (giriş günü)
  - TP: +0.50% (next day intraday) | SL: -1.00% (next day intraday)
  - Her ikisi de aynı barda tetiklenirse → SL kabul (pesimist)
  - Hiçbiri tetiklenmezse → ertesi gün Close'da kapat

Evren: BIST30
Kaynak: veriler/*.IS_1d.parquet
"""
import sys, io, json
from pathlib import Path
import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).parent
PARQ = BASE / "veriler"

BIST30 = [
    "AKBNK", "ARCLK", "ASELS", "BIMAS", "CCOLA", "EKGYO", "ENKAI", "EREGL",
    "FROTO", "GARAN", "GUBRF", "HALKB", "ISCTR", "KCHOL", "KRDMD", "PETKM",
    "PGSUS", "SAHOL", "SASA", "SISE", "TCELL", "THYAO", "TKFEN", "TOASO",
    "TTKOM", "TTRAK", "TUPRS", "VAKBN", "YKBNK",
    # ASTOR (BIST30 üyeliği son dönemde değişti, dahil ediyorum)
    "ASTOR",
]

LOOKBACK_DAYS = 252  # son ~12 ay

TP_PCT = 0.005   # +0.50%
SL_PCT = 0.010   # -1.00%


def load(symbol: str) -> pd.DataFrame | None:
    p = PARQ / f"{symbol}.IS_1d.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    # MultiIndex kolon varsa düzleştir
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    needed = {"Open", "High", "Low", "Close"}
    if not needed.issubset(df.columns):
        return None
    return df[["Open", "High", "Low", "Close"]].dropna()


def run_backtest():
    # XU100 + SMA50
    xu = load("XU100")
    if xu is None or len(xu) < 60:
        print("XU100 verisi yok"); return
    xu["sma50"] = xu["Close"].rolling(50).mean()
    xu_close = xu["Close"]; xu_sma50 = xu["sma50"]

    all_signals = []
    per_ticker = {}

    for sym in BIST30:
        df = load(sym)
        if df is None or len(df) < 60:
            per_ticker[sym] = {"status": "veri_yok"}
            continue

        df = df.copy()
        df["ema5"]  = df["Close"].ewm(span=5,  adjust=False).mean()
        df["ema8"]  = df["Close"].ewm(span=8,  adjust=False).mean()
        df["ema13"] = df["Close"].ewm(span=13, adjust=False).mean()

        # son LOOKBACK_DAYS pencere
        df = df.iloc[-(LOOKBACK_DAYS + 20):]  # +20 ısınma

        ticker_signals = []
        idxs = df.index.tolist()
        for i in range(4, len(df) - 1):  # i+1 lazım
            today = df.iloc[i]
            d_1 = df.iloc[i - 1]
            d_2 = df.iloc[i - 2]
            d_3 = df.iloc[i - 3]

            # Önceki 3 gün TÜM EMA'ların altında
            below = lambda r: (r["Close"] < r["ema5"]) and (r["Close"] < r["ema8"]) and (r["Close"] < r["ema13"])
            if not (below(d_1) and below(d_2) and below(d_3)):
                continue
            # Bugün TÜM EMA'ların üstünde kapanış (reclaim)
            above_today = (today["Close"] > today["ema5"]) and (today["Close"] > today["ema8"]) and (today["Close"] > today["ema13"])
            if not above_today:
                continue
            # Bugün açılışta zaten üstte olmasın (yani gerçek bir kesişim olsun) → opsiyonel sertleştirme
            # Burada gevşek bırakıyoruz: kapanış reclaim'i yeterli.

            # Rejim filtresi: XU100 > SMA50 (bugünün tarihinde)
            d = idxs[i]
            if d not in xu_close.index:
                # en yakın geçmiş tarih
                xu_pos = xu_close.index.get_indexer([d], method="ffill")[0]
                if xu_pos < 0:
                    continue
                xu_c = float(xu_close.iloc[xu_pos]); xu_s = float(xu_sma50.iloc[xu_pos])
            else:
                xu_c = float(xu_close.loc[d]); xu_s = float(xu_sma50.loc[d])
            if pd.isna(xu_s):
                continue
            regime_ok = xu_c > xu_s

            entry = float(today["Close"])
            tp_price = entry * (1 + TP_PCT)
            sl_price = entry * (1 - SL_PCT)

            # Ertesi gün intraday
            nxt = df.iloc[i + 1]
            nxt_high = float(nxt["High"]); nxt_low = float(nxt["Low"]); nxt_close = float(nxt["Close"])

            hit_tp = nxt_high >= tp_price
            hit_sl = nxt_low <= sl_price

            if hit_tp and hit_sl:
                outcome = "SL"  # pesimist
                ret = -SL_PCT
            elif hit_tp:
                outcome = "TP"
                ret = TP_PCT
            elif hit_sl:
                outcome = "SL"
                ret = -SL_PCT
            else:
                outcome = "FLAT"
                ret = (nxt_close - entry) / entry

            sig = {
                "ticker": sym,
                "date": str(d.date()),
                "entry": round(entry, 4),
                "regime_ok": regime_ok,
                "outcome": outcome,
                "ret_pct": round(ret * 100, 3),
                "next_high_pct": round((nxt_high - entry) / entry * 100, 3),
                "next_low_pct":  round((nxt_low  - entry) / entry * 100, 3),
                "next_close_pct": round((nxt_close - entry) / entry * 100, 3),
            }
            ticker_signals.append(sig)
            all_signals.append(sig)

        per_ticker[sym] = {
            "total": len(ticker_signals),
            "with_regime": sum(1 for s in ticker_signals if s["regime_ok"]),
        }

    # ── Özet istatistikler ──
    def stats(signals: list) -> dict:
        if not signals:
            return {"n": 0}
        n = len(signals)
        wins = sum(1 for s in signals if s["outcome"] == "TP")
        losses = sum(1 for s in signals if s["outcome"] == "SL")
        flats = sum(1 for s in signals if s["outcome"] == "FLAT")
        win_rate = wins / n
        avg_ret = sum(s["ret_pct"] for s in signals) / n
        flat_avg = (sum(s["ret_pct"] for s in signals if s["outcome"] == "FLAT") / flats) if flats else 0.0
        # Beklenen değer
        # Breakeven win rate: SL=1%, TP=0.5% → tek vurguda 1/(1+2)= %66.7 ama flat'lar ekleniyor
        return {
            "n": n,
            "TP": wins, "SL": losses, "FLAT": flats,
            "win_rate_TP_only": round(wins / n * 100, 1),
            "win_rate_TP_or_flat_pos": round(
                sum(1 for s in signals if s["ret_pct"] > 0) / n * 100, 1),
            "loss_rate_SL_or_flat_neg": round(
                sum(1 for s in signals if s["ret_pct"] < 0) / n * 100, 1),
            "avg_ret_pct": round(avg_ret, 3),
            "flat_avg_ret_pct": round(flat_avg, 3),
            "best": max(s["ret_pct"] for s in signals),
            "worst": min(s["ret_pct"] for s in signals),
        }

    summary = {
        "TUM_SINYALLER": stats(all_signals),
        "REJIM_OK (XU100 > SMA50)": stats([s for s in all_signals if s["regime_ok"]]),
        "REJIM_KOTU (XU100 <= SMA50)": stats([s for s in all_signals if not s["regime_ok"]]),
    }

    print("=" * 70)
    print(f"EMA Ribbon Reclaim Backtest — BIST30 — son {LOOKBACK_DAYS} işlem günü")
    print(f"TP=+{TP_PCT*100:.2f}% | SL=-{SL_PCT*100:.2f}% | Ertesi gün intraday")
    print(f"Pesimist varsayım: TP+SL aynı barda → SL")
    print("=" * 70)
    for label, s in summary.items():
        print(f"\n── {label} ──")
        for k, v in s.items():
            print(f"  {k:<28} {v}")

    print("\n── HİSSE BAZINDA SİNYAL SAYISI ──")
    for sym, info in sorted(per_ticker.items()):
        if isinstance(info, dict) and "total" in info:
            print(f"  {sym:<7} total={info['total']:>3}  rejim_ok={info['with_regime']:>3}")
        else:
            print(f"  {sym:<7} {info}")

    out = BASE / "backtest_ema_ribbon_reclaim.json"
    out.write_text(json.dumps({
        "params": {"TP_PCT": TP_PCT, "SL_PCT": SL_PCT, "LOOKBACK_DAYS": LOOKBACK_DAYS},
        "summary": summary,
        "per_ticker": per_ticker,
        "signals": all_signals,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDetay JSON: {out.name}")


if __name__ == "__main__":
    run_backtest()
