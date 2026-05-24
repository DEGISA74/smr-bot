"""
compare_sources.py — yfinance vs isyatirim tüm BIST hisselerinde tutarlılık testi.

Çıktı:
  - data_consistency_report.csv : tüm hisseler için yan yana fiyat karşılaştırma
  - Konsola: anomali (>%1 fark) tablosu
"""

from __future__ import annotations
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd

from fetcher import fetch_yfinance, fetch_isyatirim, load_bist_tickers

ROOT = Path(__file__).parent

def compare_one(symbol):
    """Tek hisse için yf vs isy son fiyatlarını karşılaştırır."""
    if symbol.startswith('X'):
        return None  # endeks atla
    try:
        yf_df  = fetch_yfinance(symbol, 30)   # 30 gün yeter
        isy_df = fetch_isyatirim(symbol, 30)
        if yf_df is None and isy_df is None:
            return {'symbol': symbol, 'status': 'both_fail'}
        if yf_df is None:
            return {'symbol': symbol, 'status': 'yf_only_fail',
                    'isy_close': float(isy_df['Close'].iloc[-1])}
        if isy_df is None:
            return {'symbol': symbol, 'status': 'isy_only_fail',
                    'yf_close': float(yf_df['Close'].iloc[-1])}
        # Drop NaN rows
        yf_clean  = yf_df.dropna()
        isy_clean = isy_df.dropna()
        if yf_clean.empty or isy_clean.empty:
            return {'symbol': symbol, 'status': 'empty_after_dropna'}
        yf_close   = float(yf_clean['Close'].iloc[-1])
        isy_close  = float(isy_clean['Close'].iloc[-1])
        yf_vol     = float(yf_clean['Volume'].iloc[-1])
        isy_vol    = float(isy_clean['Volume'].iloc[-1])
        close_diff = (yf_close - isy_close) / yf_close * 100 if yf_close else 0
        vol_diff   = (yf_vol - isy_vol) / yf_vol * 100 if yf_vol > 0 else 0
        return {
            'symbol':       symbol,
            'status':       'ok',
            'yf_close':     round(yf_close, 4),
            'isy_close':    round(isy_close, 4),
            'close_diff':   round(close_diff, 4),
            'yf_volume':    int(yf_vol),
            'isy_volume':   int(isy_vol),
            'vol_diff':     round(vol_diff, 2),
            'yf_bars':      len(yf_clean),
            'isy_bars':     len(isy_clean),
            'yf_last_date': str(yf_clean.index[-1].date()),
            'isy_last_date': str(isy_clean.index[-1].date()),
        }
    except Exception as e:
        return {'symbol': symbol, 'status': f'exception:{type(e).__name__}'}


def main():
    tickers = [t for t in load_bist_tickers() if not t.startswith('X')]
    print(f"[1/3] {len(tickers)} hisse karşılaştırılıyor...")

    results = []
    start = time.time()

    # isyatirim 1 worker zorunlu — bu yüzden compare'i sıralı yap
    for i, sym in enumerate(tickers, 1):
        r = compare_one(sym)
        if r:
            results.append(r)
        if i % 50 == 0:
            print(f"  Progress: {i}/{len(tickers)}  ({time.time()-start:.0f}sn)")

    df = pd.DataFrame(results)
    out = ROOT / "data_consistency_report.csv"
    df.to_csv(out, index=False, encoding='utf-8-sig')
    print(f"\n[2/3] CSV yazıldı → {out}")

    # Özet
    print(f"\n[3/3] ÖZET:")
    print(f"  Toplam            : {len(df)}")
    sc = df['status'].value_counts()
    for s, c in sc.items():
        print(f"  {s:<20}: {c}")

    ok_df = df[df['status'] == 'ok'].copy()
    if not ok_df.empty:
        print(f"\n  Close diff   max  : %{ok_df['close_diff'].abs().max():.3f}")
        print(f"  Close diff   ort  : %{ok_df['close_diff'].abs().mean():.4f}")
        print(f"  Volume diff  max  : %{ok_df['vol_diff'].abs().max():.2f}")
        print(f"  Volume diff  ort  : %{ok_df['vol_diff'].abs().mean():.2f}")

        # Anomaliler
        anom_close = ok_df[ok_df['close_diff'].abs() > 1.0].sort_values('close_diff', key=abs, ascending=False)
        if not anom_close.empty:
            print(f"\n  ⚠️ FİYAT FARKI >%1 ({len(anom_close)} hisse):")
            print(anom_close[['symbol','yf_close','isy_close','close_diff','yf_last_date','isy_last_date']].head(20).to_string(index=False))
        else:
            print(f"\n  ✅ Fiyat anomalisi YOK (>%1 fark hiç yok)")

        # Volume anomalileri
        anom_vol = ok_df[ok_df['vol_diff'].abs() > 50].sort_values('vol_diff', key=abs, ascending=False)
        if not anom_vol.empty:
            print(f"\n  ⚠️ HACİM FARKI >%50 ({len(anom_vol)} hisse) — yfinance bug olasılığı:")
            print(anom_vol[['symbol','yf_volume','isy_volume','vol_diff']].head(15).to_string(index=False))

    print(f"\nToplam süre: {time.time()-start:.0f} sn")


if __name__ == "__main__":
    main()
