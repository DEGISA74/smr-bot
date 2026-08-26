# -*- coding: utf-8 -*-
"""
trajectory_v3.py — TUM sinyalleri (akilli para + yabanci + fiyat/hacim) ekleyerek
"kanit buyumesi" testi. SALT RAPOR (patron.db/parquet degismez).
3 surum: v1(eski 5), v2(israr+RSI+RS), v3(HEPSI, mevcut olana gore oran).
Codex disiplini: event=symbol+event_start_date, karar T+3, giris T+4 acilis,
getiri sonrasi, +%30 HEM high-dokunus HEM kapanis-bazli, look-ahead sadece <=D.
"""
import sqlite3, glob, os, json
import numpy as np, pandas as pd

BASE=r"C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal"; VDIR=BASE+r"\veriler"
OUT=r"C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal\gelişmiş tarama"
FOLLOW=3; HOLD=20; TGT=30.0; NOISE={"radar1"}
def log(*a):
    print(*a); import sys; sys.stdout.flush()

# ---- parquet ----
pqmap={os.path.basename(f)[:-len(".IS_1d.parquet")]:f for f in glob.glob(os.path.join(VDIR,"*.IS_1d.parquet"))}
_c={}
def px(sym):
    if sym in _c: return _c[sym]
    f=pqmap.get(sym)
    if not f: _c[sym]=None; return None
    d=pd.read_parquet(f).sort_index(); d.index=pd.to_datetime(d.index).strftime("%Y-%m-%d")
    # gostergeler
    cl,hi,lo,vol=d["Close"],d["High"],d["Low"],d["Volume"]
    dd=cl.diff()
    up=dd.clip(lower=0).rolling(14).mean(); dn=(-dd.clip(upper=0)).rolling(14).mean()
    d["rsi"]=100-100/(1+up/dn.replace(0,np.nan))
    d["sma20"]=cl.rolling(20).mean()
    d["obv"]=(np.sign(dd).fillna(0)*vol).cumsum()
    mfv=(((cl-lo)-(hi-cl))/(hi-lo).replace(0,np.nan))*vol
    d["cmf"]=mfv.rolling(20).sum()/vol.rolling(20).sum().replace(0,np.nan)
    tp=(hi+lo+cl)/3; rmf=tp*vol; pmf=rmf.where(tp>tp.shift(1),0); nmf=rmf.where(tp<tp.shift(1),0)
    d["mfi"]=100-100/(1+pmf.rolling(14).sum()/nmf.rolling(14).sum().replace(0,np.nan))
    d["volr"]=vol/vol.rolling(20).mean()
    tr=pd.concat([hi-lo,(hi-cl.shift()).abs(),(lo-cl.shift()).abs()],axis=1).max(axis=1)
    d["atr"]=tr.rolling(14).mean()
    _c[sym]=d; return d

xu=pd.read_parquet(os.path.join(VDIR,"XU100.IS_1d.parquet")).sort_index()
xu.index=pd.to_datetime(xu.index).strftime("%Y-%m-%d"); CAL=list(xu.index); POS={d:i for i,d in enumerate(CAL)}; xuc=xu["Close"]
def cs(dt,n):
    i=POS.get(dt); return CAL[i+n] if (i is not None and 0<=i+n<len(CAL)) else None

# ---- stored features (June+) ----
con=sqlite3.connect(BASE+r"\patron.db")
SF=['f_smart_money_score','f_smart_structural_score','f_smart_tactical_score','f_master_score',
    'f_spike_dominance','f_breakout_state','f_rel_obv_state','f_udvr_state','f_force_index_dual',
    'f_mfi_dual','f_cmf_dual']
ss=pd.read_sql_query("SELECT event_id,symbol,event_start_date,scan_date,scan_type,"+",".join(SF)+" FROM scan_signals",con)
yb=pd.read_sql_query("SELECT report_date,symbol,direction,bps_change,streak_days FROM mkk_yabanci",con)
con.close()
ss["symbol"]=ss["symbol"].str.strip().str.replace(".IS","",regex=False); ss["scan_type"]=ss["scan_type"].str.strip()
yb["symbol"]=yb["symbol"].str.strip().str.replace(".IS","",regex=False)
REL={'underperform_strong':-2,'underperform_mild':-1,'inline':0,'outperform_mild':1,'outperform_strong':2}
UDV={'strong_seller':-2,'seller':-1,'balanced':0,'buyer':1,'strong_buyer':2}
FDX={'strong_neg':-3,'neg':-2,'turning_down':-1,'neutral':0,'turning_up':1,'pos':2,'strong_pos':3}
MFD={'cooling_smart_exit':-2,'overbought_both':-1,'early_overbought':0,'neutral':0,'early_oversold':1,'oversold_both':1,'smart_money_recovery':2}
def ordmap(col,v):
    if v is None or (isinstance(v,float) and np.isnan(v)): return None
    if col=='f_rel_obv_state': return REL.get(v)
    if col=='f_udvr_state': return UDV.get(v)
    if col in('f_force_index_dual','f_cmf_dual'): return FDX.get(v)
    if col=='f_mfi_dual': return MFD.get(v)
    return v  # numeric
# symbol+date -> feature dict (ilk non-null)
feat={}
for (sym,dt),g in ss.groupby(["symbol","scan_date"]):
    dd={}
    for c in SF:
        nn=g[c].dropna()
        dd[c]=nn.iloc[0] if len(nn) else None
    feat.setdefault(sym,{})[dt]=dd
# yabanci: symbol -> {date: (dir,bps,streak)}
ybm={}
for _,r in yb.iterrows():
    ybm.setdefault(r["symbol"],{})[r["report_date"][:10]]=(r["direction"],r["bps_change"],r["streak_days"])

# ---- component tanimlari ----
COMPS=["rsi","rs","obv","cmf","mfi","volr","ma20","atr_move",   # A: fiyat/hacim
       "sm_score","struct_tact","rel_obv","udvr","fidx","mfi_dual","spike","breakout","master",  # B: akilli para
       "yab_in","yab_streak","yab_bps",   # C: yabanci
       "israr","genislik"]                 # korunan

rows=[]; skip={"no_pq":0,"nodec":0,"nomat":0,"short":0}
for (sym,T0),g in ss.groupby(["symbol","event_start_date"]):
    p=px(sym)
    if p is None: skip["no_pq"]+=1; continue
    D=cs(T0,FOLLOW); e_d=cs(T0,FOLLOW+1); x_d=cs(T0,FOLLOW+1+HOLD)
    if D is None or e_d is None: skip["nodec"]+=1; continue
    if x_d is None: skip["nomat"]+=1; continue
    if T0 not in p.index or e_d not in p.index or D not in p.index: skip["short"]+=1; continue
    win=g[(g["scan_date"]>=T0)&(g["scan_date"]<=D)]; days=sorted(win["scan_date"].unique()); last=days[-1]
    comp={}  # name -> True(buyudu)/False/None(yok)
    def num(col,a,b):
        return (None if (pd.isna(p.at[a,col]) or pd.isna(p.at[b,col])) else (p.at[b,col]>p.at[a,col]))
    comp["rsi"]=num("rsi",T0,D)
    rs0=p.at[T0,"Close"]/xuc.get(T0,np.nan); rsD=p.at[D,"Close"]/xuc.get(D,np.nan)
    comp["rs"]=None if (np.isnan(rs0) or np.isnan(rsD)) else (rsD>rs0)
    comp["obv"]=num("obv",T0,D); comp["cmf"]=num("cmf",T0,D); comp["mfi"]=num("mfi",T0,D); comp["volr"]=num("volr",T0,D)
    comp["ma20"]=None if pd.isna(p.at[D,"sma20"]) else (p.at[D,"Close"]>p.at[D,"sma20"])
    atr=p.at[D,"atr"]; comp["atr_move"]=None if (pd.isna(atr) or atr==0) else ((p.at[D,"Close"]-p.at[cs(D,-1),"Close"])/atr>0.5 if cs(D,-1) in p.index else None)
    # B: stored, T0 vs son scan gunu
    f0=feat.get(sym,{}).get(T0); fL=feat.get(sym,{}).get(last)
    def sgrow(col):
        if not f0 or not fL: return None
        a=ordmap(col,f0.get(col)); b=ordmap(col,fL.get(col))
        return None if (a is None or b is None) else (b>a)
    comp["sm_score"]=sgrow("f_smart_money_score")
    st=sgrow("f_smart_structural_score"); ta=sgrow("f_smart_tactical_score")
    comp["struct_tact"]=None if (st is None and ta is None) else bool(st or ta)
    comp["rel_obv"]=sgrow("f_rel_obv_state"); comp["udvr"]=sgrow("f_udvr_state")
    comp["fidx"]=sgrow("f_force_index_dual"); comp["mfi_dual"]=sgrow("f_mfi_dual")
    comp["spike"]=sgrow("f_spike_dominance"); comp["master"]=sgrow("f_master_score")
    bkD=ordmap("f_breakout_state",fL.get("f_breakout_state")) if fL else None
    comp["breakout"]=None if bkD is None else (bkD>0)
    # C: yabanci T0..D
    ym=ybm.get(sym,{}); winy=[ym[d] for d in ym if T0<=d<=D]
    comp["yab_in"]=(len([1 for dr,_,_ in winy if dr=="in"])>0) if winy else None
    strv=[s for _,_,s in winy if s is not None]; comp["yab_streak"]=(max(strv)>=2) if strv else None
    bpv=[b for _,b,_ in winy if b is not None]; comp["yab_bps"]=(max(bpv)>0) if bpv else None
    # korunan
    setT0=set(win[win["scan_date"]==T0]["scan_type"])-NOISE; setL=set(win[win["scan_date"]==last]["scan_type"])-NOISE
    comp["israr"]=(len([d for d in days if d!=T0])>=2)
    comp["genislik"]=(len(setL)-len(setT0))>0
    # skorlar
    v1=sum(1 for k in ["genislik","israr","rsi","rs"] if comp.get(k)) + (1 if len(setL-setT0)>0 else 0)
    v2=sum(1 for k in ["israr","rsi","rs"] if comp.get(k))
    avail=[k for k in COMPS if comp.get(k) is not None]; grown=[k for k in avail if comp[k]]
    v3=len(grown)/len(avail) if avail else 0.0
    # sonuc
    entry=p.at[e_d,"Open"] if not pd.isna(p.at[e_d,"Open"]) else p.at[e_d,"Close"]
    seg=p.loc[e_d:x_d]
    if len(seg)<2 or entry<=0: continue
    mfe=(seg["High"].max()/entry-1)*100; mae=(seg["Low"].min()/entry-1)*100
    postret=(seg["Close"].iloc[-1]/entry-1)*100
    hit30_hi=1 if mfe>=TGT else 0
    hit30_cl=1 if (seg["Close"].max()/entry-1)*100>=TGT else 0
    clean30=1 if (hit30_hi and mae>-15) else 0
    xs=xuc.loc[e_d:x_d]; xr=(xs.iloc[-1]/xs.iloc[0]-1)*100 if len(xs)>=2 else np.nan
    row=dict(symbol=sym,T0=T0,month=T0[:7],v1=v1,v2=v2,v3=round(v3,3),n_avail=len(avail),
             postret=round(postret,2),alpha=round(postret-xr,2) if not np.isnan(xr) else None,
             mfe=round(mfe,1),mae=round(mae,1),hit30_hi=hit30_hi,hit30_cl=hit30_cl,clean30=clean30)
    for k in COMPS: row["c_"+k]=(1 if comp.get(k) else (0 if comp.get(k) is not None else None))
    rows.append(row)

df=pd.DataFrame(rows); df.to_csv(OUT+r"\trajectory_v3_events.csv",index=False,encoding="utf-8-sig")
log(f"Event: {len(df)}  atlanan={skip}  | feature_source=reconstructed_v1 (B: stored June+)")
base_hi=100*df["hit30_hi"].mean(); base_cl=100*df["hit30_cl"].mean()
log(f"BAZ +%30: high-dokunus=%{base_hi:.1f}  kapanis-bazli=%{base_cl:.1f}  | N={len(df)}\n")

# ---- HER SINYALIN TEK TEK LIFT'i (asil merak edilen) ----
log("="*104); log("HER SINYAL TEK TEK: 'buyudu' olan event'lerde +%30 (high & kapanis) ve lift"); log("="*104)
log(f'{"sinyal":<14}{"N_var":>7}{"N_buyudu":>9}{"hit30_hi%":>10}{"lift_hi":>8}{"hit30_cl%":>10}{"postret":>9}')
comp_out={}
for k in COMPS:
    col="c_"+k; sub=df[df[col]==1]; navail=df[col].notna().sum()
    if len(sub)<25: continue
    h=100*sub["hit30_hi"].mean(); hc=100*sub["hit30_cl"].mean(); lift=h/base_hi if base_hi>0 else 0
    comp_out[k]=dict(N=len(sub),hit_hi=round(h,1),lift=round(lift,2),hit_cl=round(hc,1),postret=round(sub["postret"].mean(),2))
    log(f'{k:<14}{navail:>7}{len(sub):>9}{h:>10.1f}{lift:>8.2f}{hc:>10.1f}{sub["postret"].mean():>9.2f}')

# ---- v1/v2/v3 kiyas: 3 grup + monotonluk ----
def summ(s):
    return None if len(s)==0 else dict(N=len(s),hit_hi=round(100*s["hit30_hi"].mean(),1),
        hit_cl=round(100*s["hit30_cl"].mean(),1),postret=round(s["postret"].mean(),2),
        alpha=round(s["alpha"].mean(),2),mfe=round(s["mfe"].mean(),1),mae=round(s["mae"].mean(),1))
def show3(name,groups):
    log(f"\n--- {name} (3 grup) ---")
    log(f'{"grup":<10}{"N":>6}{"hit30_hi%":>10}{"lift":>7}{"hit30_cl%":>10}{"postret":>9}{"alpha":>8}{"MFE":>7}{"MAE":>7}')
    for gn,s in groups:
        m=summ(s)
        if m: log(f'{gn:<10}{m["N"]:>6}{m["hit_hi"]:>10}{m["hit_hi"]/base_hi:>7.2f}{m["hit_cl"]:>10}{m["postret"]:>9}{str(m["alpha"]):>8}{m["mfe"]:>7}{m["mae"]:>7}')
show3("v1 (eski 5 bilesen)",[("zayif",df[df.v1<=1]),("sabit",df[df.v1==2]),("buyuyen",df[df.v1>=3])])
show3("v2 (israr+RSI+RS)",[("zayif",df[df.v2<=0]),("sabit",df[df.v2==1]),("buyuyen",df[df.v2>=2])])
q1,q2=df["v3"].quantile([0.5,0.8])
show3(f"v3 HEPSI (oran; kesim {q1:.2f}/{q2:.2f})",[("zayif",df[df.v3<=q1]),("orta",df[(df.v3>q1)&(df.v3<=q2)]),("buyuyen",df[df.v3>q2])])

# ---- walk-forward v3 buyuyen ----
log("\n--- WALK-FORWARD (T0 ay, v3 en ust %20 vs baz) ---")
log(f'{"ay":<10}{"N_top":>7}{"hit_top%":>9}{"hit_baz%":>9}{"lift":>7}')
wf={}
for mon in sorted(df["month"].unique()):
    sub=df[df["month"]==mon];
    if len(sub)<20: continue
    thr=sub["v3"].quantile(0.8); top=sub[sub["v3"]>thr]
    bh=100*sub["hit30_hi"].mean(); th=100*top["hit30_hi"].mean() if len(top) else 0
    wf[mon]=dict(N=len(top),top=round(th,1),baz=round(bh,1),lift=round(th/bh,2) if bh else 0)
    log(f'{mon:<10}{len(top):>7}{th:>9.1f}{bh:>9.1f}{th/bh if bh else 0:>7.2f}')

# ---- KUMELENME kontrolu (Codex #2): OZATD tasiyor mu? ----
log("\n--- KUMELENME: v3 en ust %20 grubunda hisse dagilimi ---")
top_all=df[df["v3"]>df["v3"].quantile(0.8)]
vc=top_all["symbol"].value_counts()
log(f'  ust grup event: {len(top_all)} | benzersiz hisse: {top_all["symbol"].nunique()} | en cok: {dict(vc.head(5))}')
log(f'  top-3 hissenin ust grup +%30(hi) hit payi: {top_all[top_all.symbol.isin(vc.head(3).index)]["hit30_hi"].sum()}/{top_all["hit30_hi"].sum()}')
# OZATD haric lift
noz=df[~df.symbol.isin(["OZATD"])]; bnoz=100*noz["hit30_hi"].mean()
topn=noz[noz.v3>noz.v3.quantile(0.8)]
log(f'  OZATD HARIC: baz=%{bnoz:.1f} ust-grup=%{100*topn["hit30_hi"].mean():.1f} lift={100*topn["hit30_hi"].mean()/bnoz:.2f}')

json.dump({"base_hi":round(base_hi,2),"base_cl":round(base_cl,2),"components":comp_out,"wf":wf},
          open(OUT+r"\trajectory_v3_report.json","w",encoding="utf-8"),ensure_ascii=False,indent=2)
log(f"\nCIKTI: trajectory_v3_events.csv + trajectory_v3_report.json | patron.db/parquet DEGISMEDI")
