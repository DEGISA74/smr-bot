# -*- coding: utf-8 -*-
"""
sinyal_agirlik_saglik.py — AĞIRLIK VERMEDEN ÖNCE SAĞLIK TESTİ (23 Tem 2026)

SORU: Her terazi oyuna ölçülen katkısına göre ağırlık verelim mi? Vermeden
önce şunu bilmeliyiz: ölçtüğümüz katkı GERÇEK mi, yoksa 2 aylık tek dönemin
GÜRÜLTÜSÜ mü?

TEST — VERİYİ İKİYE BÖL:
  · İlk yarı (eski günler) → her oyun/taramanın alfasını ölç, SIRALA
  · İkinci yarı (yeni günler) → aynı ölçüm
  · İki sıralama TUTUYOR mu? (Spearman rank korelasyonu)

OKUMA:
  · korelasyon YÜKSEK (+) → sıra kalıcı, ağırlık verilebilir (temkinli)
  · korelasyon SIFIR/NEGATİF → sıra gürültü, AĞIRLIK VERME

Kaynak: patron.db signal_exits (bugün kuruldu). Her sinyalin 3 çıkış kuralına
göre alfası var. En kararlı kural olan "A_3gun"u kullanır (eşiği yok, en dürüst).

⚠ Bu testin kendisi de tek dönem içinde bölünüyor — "iki farklı REJİM'de tutar
  mı" sorusunu cevaplamaz, onu ancak rejim değişince öğreniriz. Bu test yalnız
  "aynı dönem içinde bile kararlı mı" sorusunu eler. [[feedback-extrapolation-yasak]]
"""
import os
import sqlite3

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "patron.db")
KURAL = "A_3gun"          # eşiksiz, en az uydurmalı çıkış
MIN_N_YARIM = 40          # bir taramanın HER İKİ yarıda da en az bu kadar sinyali olmalı


def spearman(a, b):
    """İki sıralama arasındaki rank korelasyonu (scipy'siz)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    ra = pd.Series(a).rank().values
    rb = pd.Series(b).rank().values
    if len(ra) < 3 or ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def main():
    conn = sqlite3.connect(DB)
    d = pd.read_sql(
        "SELECT scan_type, signal_date, alpha FROM signal_exits "
        "WHERE rule=? AND alpha IS NOT NULL", conn, params=(KURAL,))
    conn.close()
    d["signal_date"] = pd.to_datetime(d["signal_date"])
    print(f"kayıt: {len(d):,} | tarama tipi: {d.scan_type.nunique()} | "
          f"tarih: {d.signal_date.min().date()} → {d.signal_date.max().date()}")

    # ORTA TARİH — soldan/sağdan yarıya böl (gün sayısına göre, satır sayısına değil)
    gunler = sorted(d["signal_date"].unique())
    orta = gunler[len(gunler) // 2]
    ilk = d[d.signal_date < orta]
    son = d[d.signal_date >= orta]
    print(f"ilk yarı:  {ilk.signal_date.min().date()} → {ilk.signal_date.max().date()}"
          f"  ({len(ilk):,} kayıt)")
    print(f"ikinci yarı: {son.signal_date.min().date()} → {son.signal_date.max().date()}"
          f"  ({len(son):,} kayıt)\n")

    g1 = ilk.groupby("scan_type")["alpha"].agg(["mean", "size"])
    g2 = son.groupby("scan_type")["alpha"].agg(["mean", "size"])
    ortak = g1.join(g2, lsuffix="_1", rsuffix="_2").dropna()
    ortak = ortak[(ortak["size_1"] >= MIN_N_YARIM) & (ortak["size_2"] >= MIN_N_YARIM)]
    print(f"her iki yarıda ≥{MIN_N_YARIM} sinyali olan tarama: {len(ortak)}")
    if len(ortak) < 4:
        print("YETERSİZ — sağlıklı test için çok az tarama. AĞIRLIK VERME.")
        return

    ortak = ortak.sort_values("mean_1", ascending=False)
    ortak["sıra_ilk"] = ortak["mean_1"].rank(ascending=False).astype(int)
    ortak["sıra_son"] = ortak["mean_2"].rank(ascending=False).astype(int)
    ortak["kayma"] = (ortak["sıra_ilk"] - ortak["sıra_son"]).abs()

    pd.set_option("display.width", 200)
    print("\n" + "=" * 84)
    print(f"TARAMA SIRALAMASI — ilk yarı vs ikinci yarı (çıkış: {KURAL})")
    print("=" * 84)
    goster = ortak[["mean_1", "sıra_ilk", "mean_2", "sıra_son", "kayma"]].copy()
    goster.columns = ["ilk_alfa", "ilk_sıra", "son_alfa", "son_sıra", "sıra_kayması"]
    print(goster.round(3).to_string())

    rho = spearman(ortak["mean_1"], ortak["mean_2"])
    ort_kayma = ortak["kayma"].mean()
    isaret_tutan = int((np.sign(ortak["mean_1"]) == np.sign(ortak["mean_2"])).sum())

    print("\n" + "-" * 84)
    print(f"Spearman sıra korelasyonu : {rho:+.3f}")
    print(f"ortalama sıra kayması     : {ort_kayma:.1f} basamak ({len(ortak)} tarama içinde)")
    print(f"işareti (+/−) tutan       : {isaret_tutan}/{len(ortak)} tarama")
    print("-" * 84)

    print("\nHÜKÜM:")
    if rho >= 0.5 and isaret_tutan >= 0.7 * len(ortak):
        print("  ✅ SIRA KALICI — ağırlık verilebilir. Yine de TEMKİNLİ:")
        print("     kaba kova (güçlü/orta/zayıf) + ortalamaya çekme + canlı okuma.")
    elif rho >= 0.2:
        print("  ⚠ ZAYIF KALICILIK — sıra kısmen tutuyor ama güvenilmez.")
        print("     Ağırlık VERME; sadece en uçları (en iyi 2 / en kötü 2) işaretle.")
    else:
        print("  ❌ SIRA GÜRÜLTÜ — ilk yarı ile ikinci yarı örtüşmüyor.")
        print("     AĞIRLIK VERME. Ölçtüğümüz katkı bu dönemde tesadüf.")
    print("\n  ⚠ Her hâlükârda: bu tek dönem. Rejim değişince yeniden koş.")


if __name__ == "__main__":
    main()
