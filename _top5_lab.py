"""Sunum sorusu: 30 isim cikinca en likit KAC tanesini gosterelim?
En likit 3 / 5 / 10 / ust-1/3 / tum liste karsilastirmasi."""
import sqlite3, warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from scipy import stats
import stop_kalibrasyon_lab as L
from bist_data_store import active_version_id, load_manifest, read_active
B,W,C,meta=L._load(); meta=meta.rename(columns={"index":"event_id"})
bd,_,_=L.simulate(B,W,C,10.0,10.0)
r=pd.DataFrame({"event_id":meta.event_id.to_numpy(),"scan_type":meta.scan_type.to_numpy(),
                "regime":meta.regime.to_numpy(),"ret":bd})
con=sqlite3.connect("file:patron.db?mode=ro",uri=True)
ev=pd.read_sql_query("SELECT id AS event_id, scan_date, symbol FROM scan_signals WHERE is_event_start=1",con)
d=r.merge(ev,on="event_id"); d["symbol"]=d.symbol.astype(str).str.upper().str.replace(".IS","",regex=False)
d["scan_date"]=pd.to_datetime(d.scan_date).dt.normalize()
V=active_version_id(); man=load_manifest(V) or {}
lik={}
for s in man.get("symbols",{}):
    x=read_active(s,V)
    if x is None or getattr(x,"empty",True): continue
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.loc[:,~x.columns.duplicated()].copy(); x.columns=[str(c).capitalize() for c in x.columns]
    if not {"Close","Volume"}.issubset(x.columns): continue
    i=pd.to_datetime(x.index,errors="coerce")
    if getattr(i,"tz",None) is not None: i=i.tz_localize(None)
    x.index=i.normalize(); x=x[~x.index.isna()].sort_index()
    lik[s.upper().replace(".IS","")]=(pd.to_numeric(x["Close"],errors="coerce")*pd.to_numeric(x["Volume"],errors="coerce")).rolling(20,min_periods=5).mean()
def _l(sym,dt):
    s=lik.get(sym)
    if s is None: return np.nan
    p=int(s.index.searchsorted(dt,side="right"))-1
    return float(s.iloc[p]) if p>=0 else np.nan
d["lik"]=[_l(s,t) for s,t in zip(d.symbol,d.scan_date)]
d=d.dropna(subset=["lik","ret"])
# yalniz kalabalik listeler: >=15 isim (30-isimli senaryoya yakin)
res={k:[] for k in ("tum","top3","top4","top5","top10","ust3te1")}
gun=0
for (sc,dt),g in d.groupby(["scan_type","scan_date"]):
    if len(g)<15: continue
    gun+=1
    rk=g.lik.rank(method="average",ascending=False)
    res["tum"].append(g.ret.mean())
    for k,lbl in ((3,"top3"),(4,"top4"),(5,"top5"),(10,"top10")):
        res[lbl].append(g.loc[rk.nsmallest(k).index,"ret"].mean())
    res["ust3te1"].append(g.loc[rk.nsmallest(len(g)//3).index,"ret"].mean())
print(f"degerlendirilen liste (>=15 isimli tarama-gunu): {gun}")
base=np.array(res["tum"])
print("%-10s %8s %9s %10s %8s"%("secim","ortalama","tum liste","fark","p"))
for k in ("top3","top4","top5","top10","ust3te1","tum"):
    v=np.array(res[k]); f=v-base
    p=stats.binomtest(int((f>0).sum()),len(f),0.5).pvalue if k!="tum" else 1.0
    print("%-10s %+8.2f %+9.2f %+10.2f %8.3f"%(k,v.mean(),base.mean(),f.mean(),p))
