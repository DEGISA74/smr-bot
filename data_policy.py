#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VERİ POLİTİKASI — TEK GERÇEK KAYNAK (3 Tem 2026).

Tüm boru hattı (fetcher.py + app.py + repair_parquets.py + rebaseline) Yahoo'dan
veri çekerken buradaki AUTO_ADJUST'ı kullanır. Böylece "aynı parquet'e iki farklı
politikayla yazma" (2 Tem bozulmasının kökü) BİR DAHA yaşanamaz.

AUTO_ADJUST = False → HAM/GERÇEK fiyat (aracıdaki + TradingView'deki fiyatla birebir).
  Kullanıcı kararı 3 Tem 2026: seviyeler (POC/destek/direnç) aracıyla tutsun diye ham.
  Güncel/son fiyat True/False'ta zaten AYNI; fark yalnızca geçmiş temettü düzeltmesinde.

⚠️ Bunu değiştirirsen TÜM parquet'leri rebaseline_parquets.py ile yeniden indir
   (yoksa eski politikayla yazılmış barlar yenileriyle karışır → süreksizlik).
"""

AUTO_ADJUST = False

# yf.download(auto_adjust=False) fazladan 'Adj Close' kolonu döndürür — şema
# (OHLCV) sabit kalsın diye her indirmeden sonra bunu düşürürüz.
def drop_adj_close(df):
    """'Adj Close' kolonunu güvenle düşür — hangi seviyede olursa olsun.
    yf.download tek-ticker → MultiIndex (alan, ticker) [Adj Close level 0'da];
    group_by='ticker' → (ticker, alan) [Adj Close level 1'de]; düz → tek kolon."""
    if df is None or getattr(df, "empty", True):
        return df
    try:
        import pandas as _pd
        cols = df.columns
        if isinstance(cols, _pd.MultiIndex):
            keep = [c for c in cols if not any(str(lvl).strip().lower() == "adj close" for lvl in c)]
            return df.loc[:, keep]
        return df.drop(columns=[c for c in cols if str(c).strip().lower() == "adj close"],
                       errors="ignore")
    except Exception:
        return df
