"""Aşama 2 tabanı: AYNI sınırları (stop/hedef/20 seans) kasadaki TÜM BIST
hisselerine, aynı 74 giriş gününde, uzun yönde uygular. Salt-okunur."""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from bist_data_store import active_version_id, load_manifest, read_active

STOPS=(5.0,8.0,10.0,15.0); TARGETS=(5.0,8.0,10.0,15.0); H=20
V=active_version_id(); man=load_manifest(V) or {}
syms=[s for s in sorted(man.get("symbols",{}).keys()) if not s.upper().startswith(("XU","^"))]
ev=pd.read_csv("logs/degisken_vade_asama1_events.csv",usecols=["entry_date","day"])
dates=sorted(pd.to_datetime(ev[ev.day==1].entry_date.unique()).normalize())
print(f"sembol {len(syms)} · giris gunu {len(dates)}",flush=True)

def prep(d):
    if d is None or getattr(d,"empty",True): return None
    if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
    d=d.loc[:,~d.columns.duplicated()].copy(); d.columns=[str(c).capitalize() for c in d.columns]
    if not {"Open","High","Low","Close"}.issubset(d.columns): return None
    i=pd.to_datetime(d.index,errors="coerce")
    if getattr(i,"tz",None) is not None: i=i.tz_localize(None)
    d.index=i.normalize(); d=d[~d.index.isna()]
    return d[~d.index.duplicated(keep="last")].sort_index()

B=[];W=[];C=[]
done=0
for s in syms:
    try: d=prep(read_active(s,V))
    except Exception: d=None
    if d is None or d.empty: continue
    o_=d["Open"].to_numpy(float); h_=d["High"].to_numpy(float)
    l_=d["Low"].to_numpy(float); c_=d["Close"].to_numpy(float)
    for dt in dates:
        p=int(d.index.searchsorted(dt,side="left"))
        if p>=len(d.index) or d.index[p]!=dt: continue
        e=o_[p]
        if not np.isfinite(e) or e<=0: continue
        n=min(H,len(d)-p)
        if n<1: continue
        b=np.full(H,np.nan); w=np.full(H,np.nan); c=np.full(H,np.nan)
        b[:n]=np.maximum.accumulate((h_[p:p+n]/e-1)*100)
        w[:n]=np.minimum.accumulate((l_[p:p+n]/e-1)*100)
        c[:n]=(c_[p:p+n]/e-1)*100
        B.append(b);W.append(w);C.append(c)
    done+=1
    if done%200==0: print(f"  ...{done}",flush=True)
B=np.asarray(B);W=np.asarray(W);C=np.asarray(C)
print("taban yolu:",B.shape,flush=True)

def first_true(m):
    a=m.any(axis=1); return np.where(a,m.argmax(axis=1),-1)
valid=~np.isnan(C); last=valid.shape[1]-1-np.argmax(valid[:,::-1],axis=1)
rows=[]
ret0=C[np.arange(len(C)),last]
rows.append({"stop":None,"hedef":None,"N":len(ret0),"ortanca":float(np.median(ret0)),
             "ortalama":float(ret0.mean()),"isabet":float((ret0>0).mean()*100),"ort_tutma_gun":float((last+1).mean())})
for st in STOPS:
    for tg in TARGETS:
        s=first_true(np.nan_to_num(W,nan=0.0)<=-st); t=first_true(np.nan_to_num(B,nan=0.0)>=tg)
        s=np.where((s>=0)&(s<=last),s,-1); t=np.where((t>=0)&(t<=last),t,-1)
        sf=(s>=0)&((t<0)|(s<=t)); tf=(~sf)&(t>=0)
        r=C[np.arange(len(C)),last].copy(); r[sf]=-st; r[tf]=tg
        dd=last+1; dd=np.where(sf,s+1,dd); dd=np.where(tf,t+1,dd)
        rows.append({"stop":-st,"hedef":tg,"N":len(r),"ortanca":float(np.median(r)),
                     "ortalama":float(r.mean()),"isabet":float((r>0).mean()*100),
                     "ort_tutma_gun":float(dd.mean()),"stop_orani":float(sf.mean()*100),
                     "hedef_orani":float(tf.mean()*100)})
out=pd.DataFrame(rows); out.to_csv("logs/stop_kalibrasyon_taban.csv",index=False)
print(out.round(2).to_string(index=False))
