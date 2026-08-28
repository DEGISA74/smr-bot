"""Havuz karnesi duz momentumdan fazlasi mi? Sinyal-turevli hisse karnesi ile
ham 60 gunluk getiriyi AYNI ikinci pencerede yaristirir. Salt-okunur."""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from scipy import stats
import stop_kalibrasyon_lab as L
from bist_data_store import active_version_id, read_active

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
cut=d.e.quantile(0.5)
h1,h2=d[d.e<=cut],d[d.e>cut]
print(f"bolme {pd.Timestamp(cut).date()} · 1.pencere {len(h1)} olay · 2.pencere {len(h2)} olay",flush=True)

a=h1.groupby('s').agg(n1=('r','size'),karne=('r','mean'))
b=h2.groupby('s').agg(n2=('r','size'),m2=('r','mean'))
j=a.join(b,how='inner'); j=j[(j.n1>=2)&(j.n2>=2)]
print("ortak hisse:",len(j),flush=True)

V=active_version_id()
def prep(x):
    if x is None or getattr(x,'empty',True): return None
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.loc[:,~x.columns.duplicated()].copy(); x.columns=[str(c).capitalize() for c in x.columns]
    if 'Close' not in x.columns: return None
    i=pd.to_datetime(x.index,errors='coerce')
    if getattr(i,'tz',None) is not None: i=i.tz_localize(None)
    x.index=i.normalize(); x=x[~x.index.isna()]
    return x[~x.index.duplicated(keep='last')].sort_index()

mom={}
for s in j.index:
    dd=prep(read_active(s,V))
    if dd is None or dd.empty: continue
    p=int(dd.index.searchsorted(pd.Timestamp(cut),side='right'))-1
    if p<60: continue
    c=dd['Close'].to_numpy(float)
    if not np.isfinite(c[p]) or not np.isfinite(c[p-60]) or c[p-60]<=0: continue
    mom[s]=(c[p]/c[p-60]-1)*100
j['mom60']=pd.Series(mom)
j=j.dropna(subset=['mom60'])
print("momentum hesaplanan hisse:",len(j),flush=True)
print()
print("%-38s %8s %7s %10s"%("siralama olcutu","rho","p","ust-alt fark"))
for lbl,col in [("SINYAL-TUREVLI havuz karnesi","karne"),("HAM 60 gunluk getiri (momentum)","mom60")]:
    rho,p=stats.spearmanr(j[col],j.m2)
    u=j[j[col]>=j[col].median()].m2.mean(); al=j[j[col]<j[col].median()].m2.mean()
    print("%-38s %+8.3f %7.3f %+10.2f  (ust %+.2f / alt %+.2f)"%(lbl,rho,p,u-al,u,al))
rho,p=stats.spearmanr(j.karne,j.mom60)
print()
print("iki olcut birbirine ne kadar benziyor? rho=%+.3f p=%.4f"%(rho,p))
print()
print("CAPRAZ TABLO (ortalama 2.pencere getirisi):")
j['k_ust']=j.karne>=j.karne.median(); j['m_ust']=j.mom60>=j.mom60.median()
print(j.pivot_table(index='k_ust',columns='m_ust',values='m2',aggfunc=['mean','size']).round(2).to_string())
j.to_csv("logs/momentum_filtre_testi.csv")
