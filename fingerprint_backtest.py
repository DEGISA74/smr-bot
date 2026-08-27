# -*- coding: utf-8 -*-
"""
fingerprint_backtest.py — KAZANAN PARMAK İZİ (feature YÖRÜNGE/gelişim) KEŞFİ
============================================================================
Soru: Sinyal ANINDA çekirdek indikatörlerin DEĞER+EĞİM'i (gelişimi), kazanan
(üst çeyrek 10g getiri) ile kaybedeni (alt çeyrek) ayırıyor mu? Ortak parmak izi var mı?

Çekirdek 5 dik eksen: CMF (çapa, rejim-dayanıklı) + OBV-eğim + RSI + RVOL +
Sıkışma(BB genişliği/60g medyan). 52H + rejim koşullayıcı.
feature_karne.py tek-feature STATİK ölçer; bu YÖRÜNGE (değer + 5g değişim).

⚠️ LOOK-AHEAD YOK: her indikatör nedensel; sembol serisi bir kez hesaplanır,
sinyal tarihinde okunur (sadece geçmiş veri).

⚠️ 2 Tem 2026 ilk koşu: XU100 çalkantılı/range-bound → İKİ REJİM YOK (rejim testi
yapılamadı). Havuz fingerprint zayıf + mean-reversion (dövülmüş sekiyor); CMF çapası
kazananı AYIRMADI. TEMMUZ SONU gerçek trend gelince yeniden koş (iki-rejim testi mümkün).

Çalıştır:  python fingerprint_backtest.py
Bağımlılık: backtest_runner.py (load_parquet/load_xu100), patron.db, veriler/*.parquet
"""
import sys, io, sqlite3
from collections import defaultdict
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass
import numpy as np, pandas as pd
import backtest_runner as B
from signal_policy import measurement_regime_series

SLOPE_N  = 5     # eğim: değer(D) − değer(D−5)
MIN_HIST = 60    # sinyal öncesi min bar (stabil indikatör)
def strip(s): return s.replace('.IS','') if s else s

def indicators(df):
    """5 dik indikatör + 52H — hepsi nedensel/vektörel (look-ahead yok)."""
    c,h,l,v = df['Close'],df['High'],df['Low'],df['Volume'].astype(float)
    out = pd.DataFrame(index=df.index)
    rng=(h-l).replace(0,np.nan)                                  # CMF(20)
    out['cmf']=((((c-l)-(h-c))/rng*v).fillna(0)).rolling(20).sum()/v.rolling(20).sum().replace(0,np.nan)
    d=c.diff(); up=d.clip(lower=0); dn=(-d).clip(lower=0)        # RSI(14)
    ru=up.ewm(alpha=1/14,adjust=False).mean(); rd=dn.ewm(alpha=1/14,adjust=False).mean()
    out['rsi']=100-100/(1+ru/rd.replace(0,np.nan))
    obv=(np.sign(c.diff().fillna(0))*v).cumsum()                 # OBV eğim (5g birikim / 20g ort hacim)
    out['obv_sl']=(obv-obv.shift(SLOPE_N))/(v.rolling(20).mean().replace(0,np.nan)*SLOPE_N)
    out['rvol']=v/v.rolling(20).mean().replace(0,np.nan)         # RVOL
    sma=c.rolling(20).mean(); sd=c.rolling(20).std()             # Sıkışma: BB genişliği / 60g medyan
    bbw=(4*sd)/sma
    out['squeeze']=bbw/bbw.rolling(60,min_periods=20).median()
    hi=h.rolling(252,min_periods=60).max(); lo=l.rolling(252,min_periods=60).min()   # 52H konum
    out['pos52']=(c-lo)/(hi-lo).replace(0,np.nan)
    return out

VAL_COLS=['cmf','rsi','obv_sl','rvol','squeeze','pos52']
SLOPE_COLS=['cmf','rsi','rvol','squeeze']

def main():
    xu=B.load_xu100()
    xu_regime=measurement_regime_series(xu)
    def regime(ts):
        i=xu.index.get_indexer([ts],method='pad')
        if i[0]<0: return None
        r=xu_regime.iloc[i[0]]
        return None if pd.isna(r) else ('BOGA' if r=='YUKSELEN' else 'AYI')

    c=sqlite3.connect('patron.db').cursor()
    sig=defaultdict(list)
    for sym,sd,r10 in c.execute('SELECT symbol,signal_date,ret_10g FROM signal_results WHERE ret_10g IS NOT NULL'):
        sig[(strip(sym),sd)].append(r10)
    sig={k:float(np.mean(v)) for k,v in sig.items()}
    print("benzersiz olgun sinyal-gün:", len(sig))

    bysym=defaultdict(list)
    for (sym,sd),r in sig.items(): bysym[sym].append((sd,r))

    rows=[]; miss=0
    for sym,items in bysym.items():
        df=B.load_parquet(sym)
        if df is None or len(df)<MIN_HIST: miss+=len(items); continue
        ind=indicators(df)
        for sd,r in items:
            ts=pd.Timestamp(sd)
            p=df.index.get_indexer([ts],method='pad')[0]
            if p<MIN_HIST: miss+=1; continue
            reg=regime(ts)
            if reg is None: continue
            row={'reg':reg,'ret':r}; ok=True
            for col in VAL_COLS:
                val=ind[col].iloc[p]
                if pd.isna(val): ok=False; break
                row[col]=float(val)
            if not ok: miss+=1; continue
            for col in SLOPE_COLS:
                prev=ind[col].iloc[p-SLOPE_N] if p-SLOPE_N>=0 else np.nan
                row[col+'_sl']=float(ind[col].iloc[p]-prev) if not pd.isna(prev) else np.nan
            rows.append(row)
    D=pd.DataFrame(rows)
    print("işlenen sinyal:", len(D), "| atlanan:", miss)
    print("rejim dağılımı:", D['reg'].value_counts().to_dict())
    print("⚠️ rejim ~tek kovaysa iki-rejim testi geçersiz — çalkantılı pencere (Temmuz sonu tekrar).")

    METRICS=VAL_COLS+[x+'_sl' for x in SLOPE_COLS]
    def report(sub,tag):
        if len(sub)<20:
            print(f"\n=== {tag}: n={len(sub)} çok az, atlandı ==="); return {}
        q_lo,q_hi=sub['ret'].quantile([0.25,0.75])
        win=sub[sub['ret']>=q_hi]; los=sub[sub['ret']<=q_lo]
        print(f"\n=== {tag}  (n={len(sub)}, kazanan n={len(win)} ret≥{q_hi:.1f}, kaybeden n={len(los)} ret≤{q_lo:.1f}) ===")
        print("%-12s %9s %9s %9s" % ('metrik','KAZANAN','KAYBEDEN','FARK'))
        res={}
        for m in METRICS:
            w=win[m].mean(); l=los[m].mean(); res[m]=w-l
            print("%-12s %9.3f %9.3f %+9.3f" % (m, w, l, w-l))
        return res

    rb=report(D[D['reg']=='BOGA'],'BOĞA rejimi')
    ra=report(D[D['reg']=='AYI'],'AYI rejimi')
    print("\n=== SAĞLAM PARMAK İZİ (iki rejimde de AYNI yön) ===")
    for m in METRICS:
        if m in rb and m in ra and rb[m]*ra[m]>0:
            print("  ✅ %-12s boğa %+.3f · ayı %+.3f" % (m, rb[m], ra[m]))
        elif rb and ra:
            print("  ✗  %-12s boğa %+.3f · ayı %+.3f (rejime bağlı)" % (m, rb.get(m,0), ra.get(m,0)))

if __name__ == '__main__':
    main()
