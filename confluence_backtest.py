# -*- coding: utf-8 -*-
"""
confluence_backtest.py — KESİŞEN TARAMA (CONFLUENCE) KEŞİF BACKTEST'İ
=====================================================================
Soru: Aynı gün aynı hisseyi birden çok BAĞIMSIZ aile işaret edince
(kesişim), tek aileden daha mı iyi getiri? Hangi İKİLİ iyi, hangisi kötü?

Yöntem: aile-bazlı (ER'in 30+ senaryosu TEK aile — ham sayı tuzağı) +
kombo vs tekil vs piyasa(alpha). Yeni loglama YOK — scan_signals × signal_results.
Metrik: KATKI = kombo_alpha − en iyi tekil_alpha (pozitifse kesişim değer katıyor).

⚠️ 2 Tem 2026 ilk koşu: naif "daha çok = iyi" tezi ÇÖKTÜ (ER+Harmonik katkı 0,
ER+GizliBirikim −4.15). Umut veren: Formasyon+GucluDonus (+0.83), ER+Formasyon (+0.42)
— ama TEK ÇALKANTILI rejim. TEMMUZ SONU 2. rejimde yeniden koş.

Çalıştır:  python confluence_backtest.py
Bağımlılık: backtest_runner.py (load_xu100), patron.db, veriler/XU100.IS
"""
import sys, io, sqlite3
from collections import defaultdict
from itertools import combinations
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass
import pandas as pd
import backtest_runner as B

HORIZON = 'ret_10g'   # birincil ufuk (5g/20g de basılır)
N_SOLID = 50          # >=50 = güvenilir
N_TENT  = 20          # 20-49 = geçici; <20 gizle

def fam(st):
    """scan_type → bağımsız metodoloji ailesi (ER senaryoları tek aileye çöker)."""
    if st.startswith('er_'): return 'ErkenRadar'
    m={'harmonik_confluence':'Harmonik','minervini':'Minervini','gizli_birikim':'GizliBirikim',
       'vip_formasyon':'Formasyon','guclu_donus':'GucluDonus','prelaunch_bos':'PreLaunch',
       'tekli_altin':'Altin','altin_setup':'Altin','platin_setup':'Altin',
       'nadir_firsat':'RoyalNadir','rs_leaders':'RS','para_akisi_lider':'ParaAkisi',
       'tavan_top30':'Tavan','tavan_alarm':'Tavan','ict_sniper':'ICTSniper',
       'radar1':'Radar1','radar2':'Radar2'}
    return m.get(st, st)
KANITLI={'Harmonik','ErkenRadar','Minervini','GizliBirikim','PreLaunch'}  # backtest-kanıtlı aileler
def strip(s): return s.replace('.IS','') if s else s

def main():
    xu = B.load_xu100()
    xu_idx = xu.index
    def xu_fwd(date_str, h):
        try: ts = pd.Timestamp(date_str)
        except Exception: return None
        i = int(xu_idx.searchsorted(ts))
        if i>=len(xu): return None
        j = i+h
        if j>=len(xu): return None
        e=float(xu['Close'].iloc[i]); x=float(xu['Close'].iloc[j])
        return (x-e)/e*100 if e>0 else None
    HMAP={'ret_5g':5,'ret_10g':10,'ret_20g':20}

    c=sqlite3.connect('patron.db').cursor()
    byday=defaultdict(set)
    for sym,d,st in c.execute('SELECT symbol,scan_date,scan_type FROM scan_signals'):
        byday[(strip(sym),d)].add(fam(st))
    rets=defaultdict(dict)
    for sym,sd,r5,r10,r20 in c.execute('SELECT symbol,signal_date,ret_5g,ret_10g,ret_20g FROM signal_results'):
        k=(strip(sym),sd)
        for h,v in (('ret_5g',r5),('ret_10g',r10),('ret_20g',r20)):
            if v is not None and h not in rets[k]: rets[k][h]=v

    def collect(pred):
        acc={h:{'ret':[],'alp':[]} for h in HMAP}
        for (sym,d),fams in byday.items():
            if not pred(fams): continue
            rr=rets.get((sym,d))
            if not rr: continue
            for h,hd in HMAP.items():
                if h in rr:
                    sr=rr[h]; xr=xu_fwd(d,hd)
                    acc[h]['ret'].append(sr)
                    if xr is not None: acc[h]['alp'].append(sr-xr)
        return acc
    def summ(acc,h):
        r=acc[h]['ret']; a=acc[h]['alp']
        if not r: return None
        hit=round(100*sum(1 for x in r if x>0)/len(r),1)
        return {'n':len(r),'ret':round(sum(r)/len(r),2),
                'alp':round(sum(a)/len(a),2) if a else None,'hit':hit}

    gh=summ(collect(lambda f:True),HORIZON)
    print("=== GLOBAL taban (tüm sinyal-günleri, %s) ===" % HORIZON)
    print("  n=%d  ret=%s  alpha=%s  hit=%s%%" % (gh['n'],gh['ret'],gh['alp'],gh['hit']))

    fams_all=set()
    for v in byday.values(): fams_all|=v
    single={F:summ(collect(lambda f,F=F: F in f),HORIZON) for F in fams_all}

    print("\n=== AİLE-İKİLİ KESİŞİMLERİ (%s) — KATKI=kombo_alpha − en iyi tekil ===" % HORIZON)
    print("%-26s %5s %7s %7s | %7s %7s %6s" % ('ikili','n','ret','alpha','tekilEn','katki','hit'))
    print('-'*80)
    res=[]
    for A,Bx in combinations(sorted(fams_all),2):
        s=summ(collect(lambda f,A=A,Bx=Bx: A in f and Bx in f),HORIZON)
        if not s or s['n']<N_TENT: continue
        sa=single[A]['alp'] if single[A] and single[A]['alp'] is not None else -99
        sb=single[Bx]['alp'] if single[Bx] and single[Bx]['alp'] is not None else -99
        best_single=max(sa,sb)
        katki=round((s['alp'] if s['alp'] is not None else -99)-best_single,2)
        proven='★' if (A in KANITLI and Bx in KANITLI) else ' '
        tier='SOLID' if s['n']>=N_SOLID else 'gecici'
        res.append((s['alp'] if s['alp'] is not None else -99, A,Bx,s,best_single,katki,proven,tier))
    res.sort(reverse=True)
    for alp,A,Bx,s,bs,katki,proven,tier in res:
        print("%1s%-25s %5d %+7.2f %+7.2f | %+7.2f %+7.2f %5.1f  %s" % (
            proven, A+'+'+Bx, s['n'], s['ret'], s['alp'] if s['alp'] is not None else 0,
            bs, katki, s['hit'], tier))
    print("\n★ = iki taraf da kanıtlı aile | SOLID n>=%d, gecici n>=%d" % (N_SOLID,N_TENT))

if __name__ == '__main__':
    main()
