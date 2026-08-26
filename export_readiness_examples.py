"""Dedupe edilmiş başarılı HAZIR örneklerini PNG olarak dışa aktarır."""
import json
from pathlib import Path
import matplotlib.pyplot as plt
import pandas as pd

ROOT=Path(__file__).parent
REPORT=Path(r"C:\Users\LENOVO\.codex\visualizations\2026\08\09\019fe809-8c77-7483-b984-d1693656e6ad\formation_readiness_strict_50.json")
OUT=ROOT/"_etiket_tobo"/"kati_inceleme_adaylari"

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    raw=json.loads(REPORT.read_text(encoding="utf-8"))["signals"]
    raw=[r for r in raw if r["alert"]=="HAZIR" and r.get("return_20") is not None and r["return_20"]>0]
    raw.sort(key=lambda r:(r["return_20"],r.get("return_40") or -999),reverse=True)
    chosen=[]
    for r in raw:
        day=pd.Timestamp(r["time"])
        if any(r["ticker"]==x["ticker"] and r["pattern"]==x["pattern"] and abs((day-pd.Timestamp(x["time"])).days)<70 for x in chosen): continue
        chosen.append(r)
        if len(chosen)==20: break
    for n,r in enumerate(chosen,1):
        df=pd.read_parquet(ROOT/"veriler"/f"{r['ticker']}.IS_1d.parquet"); df.index=pd.to_datetime(df.index)
        pos=df.index.searchsorted(pd.Timestamp(r["time"])); view=df.iloc[max(0,pos-90):min(len(df),pos+45)]
        price=float(df["Close"].iloc[pos]); neck=price/(1-r["distance_pct"]/100)
        fig,ax=plt.subplots(figsize=(14,7)); ax.plot(view.index,view["Close"],lw=1.4,color="#187bff"); ax.axhline(neck,color="#00a6c7",lw=1.5); ax.axvline(df.index[pos],color="#ff8a00",ls="--"); ax.set_title(f"{n:02d} {r['ticker']} | {r['pattern']} | HAZIR | {r['duration_bars']} bar | 20b %+{r['return_20']:.2f}"); ax.grid(alpha=.2); fig.tight_layout(); fig.savefig(OUT/f"{n:02d}_{r['ticker']}_{r['pattern']}.png",dpi=160); plt.close(fig)
    (OUT/"secim.json").write_text(json.dumps(chosen,ensure_ascii=False,indent=2),encoding="utf-8")
    print(OUT, len(chosen))
if __name__=="__main__": main()
