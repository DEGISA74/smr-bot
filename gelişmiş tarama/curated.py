# -*- coding: utf-8 -*-
"""
curated.py — v3'te kazanan sinyallerden TEMIZ skor. v1(1.55x) gecebiliyor mu?
DURUST UYARI: kazanan sinyaller AYNI veriden secildi => bu skor IN-SAMPLE, lift iyimser.
Gercek dogrulama forward-collection'da. Burada yon+istikrar+kumelenme kontrolu yapiyoruz.
Girdi: trajectory_v3_events.csv (yeniden hesap yok).
"""
import pandas as pd, numpy as np
OUT=r"C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal\gelişmiş tarama"
df=pd.read_csv(OUT+r"\trajectory_v3_events.csv")
base_hi=100*df["hit30_hi"].mean(); base_cl=100*df["hit30_cl"].mean()
def g(c): return df["c_"+c]

# --- curated bilesenler ---
# cekirdek (tum donem, saglam, buyuk N): rs + ma20 + atr_move
df["cur_core"]=g("rs").fillna(0)+g("ma20").fillna(0)+g("atr_move").fillna(0)   # 0..3
# yabanci = giris veya streak
foreign=((g("yab_in")==1)|(g("yab_streak")==1))
foreign_avail=g("yab_in").notna()|g("yab_streak").notna()
# tam curated: cekirdek + fidx + breakout + yabanci + obv(zayif) — mevcut olana gore ORAN
parts={"rs":g("rs"),"ma20":g("ma20"),"atr_move":g("atr_move"),"fidx":g("fidx"),
       "breakout":g("breakout"),"foreign":foreign.where(foreign_avail),"obv":g("obv")}
avail=pd.DataFrame({k:v.notna() for k,v in parts.items()}).sum(axis=1)
grown=pd.DataFrame({k:(v==1) for k,v in parts.items()}).sum(axis=1)
df["cur_full"]=(grown/avail.replace(0,np.nan)).fillna(0)

def summ(s):
    return None if len(s)==0 else (100*s["hit30_hi"].mean(),100*s["hit30_cl"].mean(),
        s["postret"].mean(),s["alpha"].mean(),len(s))
def line(lbl,s):
    m=summ(s)
    if m: print(f'{lbl:<22}{m[4]:>6}{m[0]:>10.1f}{m[0]/base_hi:>8.2f}{m[1]:>10.1f}{m[2]:>9.2f}{str(round(m[3],2)):>8}')

print(f"BAZ +%30 high=%{base_hi:.1f} kapanis=%{base_cl:.1f} | N={len(df)}  (IN-SAMPLE curation — iyimser)")
print(f'{"grup":<22}{"N":>6}{"hit_hi%":>10}{"lift":>8}{"hit_cl%":>10}{"postret":>9}{"alpha":>8}')

print("\n--- cur_core = rs+ma20+atr (0..3), monotonluk ---")
for s in [0,1,2,3]: line(f"core={s}",df[df.cur_core==s])
line("core>=2 (UST)",df[df.cur_core>=2])

print("\n--- cur_full (oran), esik terciller ---")
q5,q8=df["cur_full"].quantile([0.5,0.8])
line(f"full<= {q5:.2f}",df[df.cur_full<=q5])
line(f"full {q5:.2f}-{q8:.2f}",df[(df.cur_full>q5)&(df.cur_full<=q8)])
line(f"full> {q8:.2f} (UST)",df[df.cur_full>q8])

print("\n--- KIYAS: v1 vs cur_core>=2 vs cur_full ust%20 ---")
line("v1 buyuyen (>=3)",df[df.v1>=3])
line("cur_core>=2",df[df.cur_core>=2])
line("cur_full ust%20",df[df.cur_full>df.cur_full.quantile(0.8)])

# --- walk-forward (cur_core>=2) ---
print("\n--- WALK-FORWARD cur_core>=2 vs baz ---")
print(f'{"ay":<10}{"N":>6}{"hit_top%":>9}{"hit_baz%":>9}{"lift":>7}')
for mon in sorted(df.month.unique()):
    sub=df[df.month==mon]
    if len(sub)<20: continue
    top=sub[sub.cur_core>=2]; bh=100*sub.hit30_hi.mean(); th=100*top.hit30_hi.mean() if len(top) else 0
    print(f'{mon:<10}{len(top):>6}{th:>9.1f}{bh:>9.1f}{th/bh if bh else 0:>7.2f}')

# --- kumelenme + OZATD haric (cur_core>=2) ---
top=df[df.cur_core>=2]
print(f"\nKUMELENME cur_core>=2: N={len(top)} benzersiz_hisse={top.symbol.nunique()} "
      f"en_cok={dict(top.symbol.value_counts().head(4))}")
noz=df[df.symbol!="OZATD"]; bn=100*noz.hit30_hi.mean(); tn=noz[noz.cur_core>=2]
print(f"OZATD haric: baz=%{bn:.1f} cur_core>=2=%{100*tn.hit30_hi.mean():.1f} lift={100*tn.hit30_hi.mean()/bn:.2f}")

# --- kahramanlar cur_core kacinci? ---
print("\nKahramanlar cur_core skoru:")
her=df[df.symbol.isin(["OZATD","KTLEV","TUPRS","ASTOR","EREGL"])].sort_values("postret",ascending=False)
for _,r in her.head(8).iterrows():
    print(f'  {r["symbol"]:6} T0={r["T0"]} cur_core={int(r["cur_core"])} cur_full={r["cur_full"]:.2f} postret=%{r["postret"]} hit30={r["hit30_hi"]}')
