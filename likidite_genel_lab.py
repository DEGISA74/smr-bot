"""LİKİDİTE KURALI — TÜM taramalarda geçerli mi?

Likidite depoda saklı gostergeye BAGLI DEGIL: 20 gunluk ortalama TL hacim
fiyat verisinden dogrudan hesaplanir. Bu sayede gosterge deligi olan 41 tarama
da olculebilir. Sinyal gunu DAHIL, girisin oncesi -> ileriye bakma yok.
Salt-okunur laboratuvar.

MÜHÜRLER (sonuclar gorulmeden):
  - Grup = (tarama, tarama gunu), en az 6 isim
  - Ust 1/3 (en likit) eksi alt 1/3, ayni gun ayni tarama
  - Getiri: -10/+10 sinirli 20 seans (birincil) + sinirsiz T+20 (ikincil)
  - Rejim ayri; kabul: iki rejimde de ayni isaret + isaret testi p<0,05
  - Tum taramalar raporlanir, kazanan sonradan secilmez
"""
import sqlite3, warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from scipy import stats
import stop_kalibrasyon_lab as L
from bist_data_store import active_version_id, load_manifest, read_active

MIN_GROUP = 6
LOOKBACK = 20

def main():
    B,W,C,meta = L._load(); meta = meta.rename(columns={"index":"event_id"})
    bd,_,_ = L.simulate(B,W,C,10.0,10.0)
    valid = ~np.isnan(C); last = valid.shape[1]-1-np.argmax(valid[:,::-1],axis=1)
    r = pd.DataFrame({"event_id":meta.event_id.to_numpy(),"scan_type":meta.scan_type.to_numpy(),
                      "regime":meta.regime.to_numpy(),"sinirli":bd,"ham":C[np.arange(len(C)),last]})
    con = sqlite3.connect("file:patron.db?mode=ro",uri=True)
    ev = pd.read_sql_query("SELECT id AS event_id, scan_date, symbol FROM scan_signals WHERE is_event_start=1",con)
    d = r.merge(ev,on="event_id",how="inner")
    d["symbol"] = d.symbol.astype(str).str.upper().str.replace(".IS","",regex=False)
    d["scan_date"] = pd.to_datetime(d.scan_date).dt.normalize()
    print(f"olay: {len(d):,} · tarama: {d.scan_type.nunique()} · gun: {d.scan_date.nunique()}",flush=True)

    V = active_version_id(); man = load_manifest(V) or {}
    lik = {}
    for s in man.get("symbols",{}):
        x = read_active(s,V)
        if x is None or getattr(x,"empty",True): continue
        if isinstance(x.columns,pd.MultiIndex): x.columns = x.columns.get_level_values(0)
        x = x.loc[:,~x.columns.duplicated()].copy(); x.columns=[str(c).capitalize() for c in x.columns]
        if not {"Close","Volume"}.issubset(x.columns): continue
        i = pd.to_datetime(x.index,errors="coerce")
        if getattr(i,"tz",None) is not None: i = i.tz_localize(None)
        x.index = i.normalize(); x = x[~x.index.isna()].sort_index()
        tl = (pd.to_numeric(x["Close"],errors="coerce")*pd.to_numeric(x["Volume"],errors="coerce"))
        lik[s.upper().replace(".IS","")] = tl.rolling(LOOKBACK,min_periods=5).mean()
    print(f"likidite hesaplanan hisse: {len(lik):,}",flush=True)

    def _lik(sym,dt):
        s = lik.get(sym)
        if s is None: return np.nan
        p = int(s.index.searchsorted(dt,side="right"))-1
        return float(s.iloc[p]) if p>=0 else np.nan
    d["likidite"] = [ _lik(s,dt) for s,dt in zip(d.symbol,d.scan_date) ]
    d = d.dropna(subset=["likidite"])
    print(f"likidite eslesen olay: {len(d):,}",flush=True)

    rows=[]
    for sc,g0 in d.groupby("scan_type"):
        for metric in ("sinirli","ham"):
            per=[]
            for (dt,rg),g in g0.groupby(["scan_date","regime"]):
                g=g.dropna(subset=[metric])
                if len(g)<MIN_GROUP: continue
                rk=g.likidite.rank(method="average"); k=len(g)//3
                per.append({"rejim":rg,
                            "fark":g.loc[rk.nlargest(k).index,metric].mean()-g.loc[rk.nsmallest(k).index,metric].mean(),
                            "ust":g.loc[rk.nlargest(k).index,metric].mean(),
                            "liste":g[metric].mean(),"n":len(g)})
            p=pd.DataFrame(per)
            if p.empty: continue
            for rg in ("YUKSELEN","DUSEN","HAVUZ"):
                q = p if rg=="HAVUZ" else p[p.rejim==rg]
                if len(q)<3: continue
                f=q.fark.to_numpy()
                rows.append({"tarama":sc,"getiri":metric,"rejim":rg,"grup":len(f),"olay":int(q.n.sum()),
                             "ort_fark":float(f.mean()),"ust3_getiri":float(q.ust.mean()),
                             "liste_getiri":float(q.liste.mean()),
                             "poz_gun":float((f>0).mean()*100),
                             "p":float(stats.binomtest(int((f>0).sum()),len(f),0.5).pvalue)})
    out=pd.DataFrame(rows); out.to_csv("logs/likidite_genel.csv",index=False)
    print(f"YAZILDI: logs/likidite_genel.csv ({len(out)} satir)")

if __name__=="__main__":
    main()
