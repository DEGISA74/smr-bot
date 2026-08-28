"""Filtrelenmis sinyaller ikinci pencerede TABANI geciyor mu?
Taban: ayni pencerede, ayni sinirlarla, kasadaki tum BIST hisseleri."""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
import stop_kalibrasyon_lab as L
from bist_data_store import active_version_id, load_manifest, read_active

j = pd.read_csv("logs/momentum_filtre_testi.csv", index_col=0)
B,W,C,meta=L._load(); meta=meta.rename(columns={'index':'event_id'})
ev=pd.read_csv("logs/degisken_vade_asama1_events.csv",usecols=['event_id','symbol','entry_date','day'])
e1=ev[ev.day==1].drop_duplicates('event_id').set_index('event_id')
mx=ev.groupby('event_id').day.max()
meta['symbol']=meta.event_id.map(e1.symbol)
meta['edate']=pd.to_datetime(meta.event_id.map(e1.entry_date))
meta['tam']=meta.event_id.map(mx)==20
ret,_,_=L.simulate(B,W,C,10.0,10.0)
d=pd.DataFrame({'s':meta.symbol.to_numpy(),'e':meta.edate.to_numpy(),'r':ret,'tam':meta.tam.to_numpy()})
d=d[d.tam]
cut=d.e.quantile(0.5); h2=d[d.e>cut]
iyi=set(j[j.karne>=j.karne.median()].index)
w2=sorted(h2.e.unique())
print(f"2. pencere: {pd.Timestamp(w2[0]).date()} .. {pd.Timestamp(w2[-1]).date()} · {len(w2)} giris gunu",flush=True)
print(f"tum sinyaller N={len(h2)} ortalama {h2.r.mean():+.2f}",flush=True)
f=h2[h2.s.isin(iyi)]
print(f"FILTRELI (karne ust yari) N={len(f)} ortalama {f.r.mean():+.2f}",flush=True)

V=active_version_id(); man=load_manifest(V) or {}
syms=[s for s in sorted(man.get("symbols",{}).keys())]
def prep(x):
    if x is None or getattr(x,'empty',True): return None
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.loc[:,~x.columns.duplicated()].copy(); x.columns=[str(c).capitalize() for c in x.columns]
    if not {"Open","High","Low","Close"}.issubset(x.columns): return None
    i=pd.to_datetime(x.index,errors='coerce')
    if getattr(i,'tz',None) is not None: i=i.tz_localize(None)
    x.index=i.normalize(); x=x[~x.index.isna()]
    return x[~x.index.duplicated(keep='last')].sort_index()
base=[]; H=20
for s in syms:
    dd=prep(read_active(s,V))
    if dd is None or dd.empty: continue
    o=dd["Open"].to_numpy(float); hi=dd["High"].to_numpy(float)
    lo=dd["Low"].to_numpy(float); cl=dd["Close"].to_numpy(float)
    for dt in w2:
        dt=pd.Timestamp(dt)
        p=int(dd.index.searchsorted(dt,side="left"))
        if p>=len(dd.index) or dd.index[p]!=dt: continue
        if p+H>len(dd): continue
        e=o[p]
        if not np.isfinite(e) or e<=0: continue
        b=np.maximum.accumulate((hi[p:p+H]/e-1)*100)
        w=np.minimum.accumulate((lo[p:p+H]/e-1)*100)
        c=(cl[p:p+H]/e-1)*100
        si=np.argmax(w<=-10.0) if (w<=-10.0).any() else -1
        ti=np.argmax(b>=10.0) if (b>=10.0).any() else -1
        if si>=0 and (ti<0 or si<=ti): base.append(-10.0)
        elif ti>=0: base.append(10.0)
        else: base.append(c[-1])
base=np.asarray(base)
print(f"TABAN (ayni pencere, tum BIST) N={len(base)} ortalama {base.mean():+.2f} ortanca {np.median(base):+.2f}",flush=True)
print()
print(f"FILTRELI - TABAN farki: {f.r.mean()-base.mean():+.2f} puan")
print(f"FILTRESIZ - TABAN farki: {h2.r.mean()-base.mean():+.2f} puan")
