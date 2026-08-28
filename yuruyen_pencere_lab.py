"""YÜRÜYEN PENCERE — gün içi sıralama kuralı ileriye doğru çalışıyor mu?

Kritik nokta: kuralın kendisinde tahmin edilen parametre YOK ("göstergeye göre
sırala, üst 1/3'ü al"). Riskli olan SEÇİM adımı — sekiz gösterge arasından
kazananı sonuçları gördükten sonra seçmiş olmam. Bu betik o adımı da ileriye
taşır: her gün, YALNIZ o güne kadarki veriyle en iyi göstergeyi seçer ve
o günün listesine uygular. Yani seçim dahil tüm yordam sınanır.

MÜHÜRLER — sonuçlar görülmeden yazıldı (28 Ağu 2026):
  1. Eğitim penceresi: ilk 15 tarama günü. Kalan günler ileri sınav.
  2. Seçim: eğitim penceresindeki (üst 1/3 − alt 1/3) ortalaması en yüksek
     gösterge. Beraberlik olursa alfabetik ilk.
  3. Kontroller: (a) listenin TAMAMI, (b) sabit f_52h_pos, (c) sabit f_adv_tl.
     Sabit kurallarda seçim riski yoktur; yürüyen seçimle kıyaslanır.
  4. Getiri: birincil -10/+10 sınırlı 20 seans; ikincil sınırsız T+20.
  5. KABUL BARI (şimdi ilan edildi): yürüyen seçim, ileri sınavda listenin
     tamamını HER İKİ rejimde de geçecek VE ileri gün sayısı >=20.
     Geçmezse BELİRSİZ. Sonuç ne çıkarsa yazılır.
"""
from __future__ import annotations
import sqlite3, warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from pathlib import Path
import stop_kalibrasyon_lab as L

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "logs" / "yuruyen_pencere.csv"
SCANNERS = ("altin_setup", "tekli_altin", "platin_setup")
FEATURES = ("f_52h_pos", "f_rsi", "f_master_score", "f_cmf_dual",
            "f_smart_money_score", "f_adv_tl", "f_hv_oran", "f_dd_zirveden")
CMF_ORDER = {"strong_neg": 1, "neg": 2, "turning_down": 3, "neutral": 4,
             "turning_up": 5, "pos": 6, "strong_pos": 7}
MIN_GROUP = 6
TRAIN_DAYS = 15


def _data():
    B, W, C, meta = L._load()
    meta = meta.rename(columns={"index": "event_id"})
    valid = ~np.isnan(C)
    last = valid.shape[1] - 1 - np.argmax(valid[:, ::-1], axis=1)
    bd, _, _ = L.simulate(B, W, C, 10.0, 10.0)
    r = pd.DataFrame({"event_id": meta.event_id.to_numpy(),
                      "regime": meta.regime.to_numpy(),
                      "sinirli": bd, "ham": C[np.arange(len(C)), last]})
    con = sqlite3.connect("file:patron.db?mode=ro", uri=True)
    d = pd.read_sql_query(
        "SELECT id AS event_id, scan_date, scan_type, symbol, " + ",".join(FEATURES) +
        " FROM scan_signals WHERE is_event_start=1 AND scan_date>='2026-05-01'"
        " AND scan_type IN ('altin_setup','platin_setup','tekli_altin')", con)
    d["f_cmf_dual"] = d["f_cmf_dual"].map(CMF_ORDER)
    for c in FEATURES:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.merge(r, on="event_id", how="inner")


def _ust_alt(g, feat, metric, k_only_top=False):
    g = g.dropna(subset=[feat, metric])
    if len(g) < MIN_GROUP:
        return None
    rk = g[feat].rank(method="average")
    k = len(g) // 3
    ust = g.loc[rk.nlargest(k).index, metric].mean()
    if k_only_top:
        return ust
    alt = g.loc[rk.nsmallest(k).index, metric].mean()
    return ust - alt


def main():
    d = _data()
    rows = []
    for scanner in SCANNERS:
        s = d[d.scan_type == scanner]
        days = sorted(s.scan_date.unique())
        if len(days) <= TRAIN_DAYS:
            print(f"{scanner}: yalnız {len(days)} gun, egitim penceresi doldurulamiyor — ATLANDI")
            continue
        for metric in ("sinirli", "ham"):
            for i in range(TRAIN_DAYS, len(days)):
                bugun, gecmis = days[i], days[:i]
                g = s[s.scan_date == bugun]
                if len(g.dropna(subset=[metric])) < MIN_GROUP:
                    continue
                skor = {}
                for f in FEATURES:
                    v = [x for x in (_ust_alt(s[s.scan_date == p], f, metric) for p in gecmis)
                         if x is not None and np.isfinite(x)]
                    if v:
                        skor[f] = float(np.mean(v))
                if not skor:
                    continue
                secilen = sorted(skor.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                rows.append({
                    "tarama": scanner, "getiri": metric, "gun": bugun,
                    "rejim": g.regime.mode().iloc[0], "liste_n": len(g),
                    "secilen": secilen,
                    "yuruyen_ust3": _ust_alt(g, secilen, metric, k_only_top=True),
                    "sabit_52h": _ust_alt(g, "f_52h_pos", metric, k_only_top=True),
                    "sabit_advtl": _ust_alt(g, "f_adv_tl", metric, k_only_top=True),
                    "tum_liste": g[metric].mean()})
    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)
    print(f"YAZILDI: {OUT} ({len(out)} satir)")
    return out


if __name__ == "__main__":
    main()
