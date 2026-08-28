"""GÜN İÇİ GÖSTERGE SIRALAMASI — "10 isim çıktı, hangisini alayım?"

Aynı gün, aynı taramanın çıkardığı listenin İÇİNDE sıralama yapar. Gün etkisi
tasarım gereği düşer: üst üçte bir ile alt üçte bir AYNI gün, AYNI taramadan.
Salt-okunur laboratuvar; canlı tarama/politika/ekran/VPS'e dokunmaz.

MÜHÜRLER — sonuçlar GÖRÜLMEDEN yazıldı (28 Ağu 2026):
  1. Taramalar: göstergesi >=%95 dolu olan ÜÇÜ (altin_setup, platin_setup,
     tekli_altin). Diğer 41 taramada gösterge delikli, girmiyorlar.
  2. Göstergeler: 8 adet, aile temsiliyle seçildi, sonuçtan seçilmedi:
     f_52h_pos · f_rsi · f_master_score · f_cmf_dual · f_smart_money_score
     · f_adv_tl · f_hv_oran · f_dd_zirveden
  3. Grup = (tarama, tarama günü), en az 6 isim. Üst 1/3 ile alt 1/3 kıyaslanır.
  4. Getiri: birincil = -10/+10 sınırlı, 20 seans (Aşama 2 çapası).
     ikincil = sınırsız T+20 ham. İkisi de raporlanır.
  5. İstatistik: grup başına (üst 1/3 ort. − alt 1/3 ort.), sonra gruplar
     üzerinden ortalama + işaret testi. Serbestlik derecesi GRUP sayısıdır,
     olay sayısı değil — ikisi de yazılır.
  6. KABUL BARI (şimdi ilan edildi): işaret HER İKİ rejimde de aynı yönde
     olacak VE havuzda işaret testi p<0,05 VE rejim başına grup >=20.
     Bunu geçmeyen "BELİRSİZ"dir. Kazanan gösterge sonradan seçilmez;
     sekizinin de sonucu yazılır.
"""
from __future__ import annotations
import sqlite3, warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from pathlib import Path
from scipy import stats
import stop_kalibrasyon_lab as L

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "logs" / "gun_ici_siralama.csv"
SCANNERS = ("altin_setup", "platin_setup", "tekli_altin")
FEATURES = ("f_52h_pos", "f_rsi", "f_master_score", "f_cmf_dual",
            "f_smart_money_score", "f_adv_tl", "f_hv_oran", "f_dd_zirveden")
CMF_ORDER = {"strong_neg": 1, "neg": 2, "turning_down": 3, "neutral": 4,
             "turning_up": 5, "pos": 6, "strong_pos": 7}
MIN_GROUP = 6
MIN_GROUPS_PER_REGIME = 20


def _returns():
    B, W, C, meta = L._load()
    meta = meta.rename(columns={"index": "event_id"})
    valid = ~np.isnan(C)
    last = valid.shape[1] - 1 - np.argmax(valid[:, ::-1], axis=1)
    bounded, _, _ = L.simulate(B, W, C, 10.0, 10.0)
    raw = C[np.arange(len(C)), last]
    return pd.DataFrame({"event_id": meta.event_id.to_numpy(),
                         "regime": meta.regime.to_numpy(),
                         "sinirli": bounded, "ham": raw})


def _features():
    con = sqlite3.connect("file:patron.db?mode=ro", uri=True)
    q = ("SELECT id AS event_id, scan_date, scan_type, symbol, "
         + ",".join(FEATURES) +
         " FROM scan_signals WHERE is_event_start=1 AND scan_date>='2026-05-01'"
         " AND scan_type IN ('altin_setup','platin_setup','tekli_altin')")
    d = pd.read_sql_query(q, con)
    d["f_cmf_dual"] = d["f_cmf_dual"].map(CMF_ORDER)
    for c in FEATURES:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d


def main():
    d = _features().merge(_returns(), on="event_id", how="inner")
    print(f"eslesen olay: {len(d):,} · tarama {d.scan_type.nunique()} · gun {d.scan_date.nunique()}")
    rows = []
    for scanner in SCANNERS:
        s = d[d.scan_type == scanner]
        for feat in FEATURES:
            for metric in ("sinirli", "ham"):
                per_group = []
                for (day, reg), g in s.groupby(["scan_date", "regime"]):
                    g = g.dropna(subset=[feat, metric])
                    if len(g) < MIN_GROUP:
                        continue
                    r = g[feat].rank(method="average")
                    k = len(g) // 3
                    ust = g.loc[r.nlargest(k).index, metric]
                    alt = g.loc[r.nsmallest(k).index, metric]
                    per_group.append({"gun": day, "rejim": reg,
                                      "fark": ust.mean() - alt.mean(),
                                      "n": len(g)})
                p = pd.DataFrame(per_group)
                if p.empty:
                    continue
                for reg in ("YUKSELEN", "DUSEN", "HAVUZ"):
                    q = p if reg == "HAVUZ" else p[p.rejim == reg]
                    if q.empty:
                        continue
                    fark = q.fark.to_numpy()
                    poz = int((fark > 0).sum())
                    sign_p = (stats.binomtest(poz, len(fark), 0.5).pvalue
                              if len(fark) > 1 else None)
                    rows.append({"tarama": scanner, "gosterge": feat, "getiri": metric,
                                 "rejim": reg, "grup": len(fark),
                                 "olay": int(q.n.sum()),
                                 "ort_fark": float(fark.mean()),
                                 "ortanca_fark": float(np.median(fark)),
                                 "pozitif_grup_orani": poz / len(fark) * 100,
                                 "isaret_testi_p": sign_p})
    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)
    print(f"YAZILDI: {OUT} ({len(out)} satir)")
    return out


if __name__ == "__main__":
    main()
