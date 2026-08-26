# -*- coding: utf-8 -*-
"""
cikis_sonuclari_yaz.py — ÇIKIŞ KURALI SONUÇLARINI patron.db'YE YAZ (23 Tem 2026)

NEDEN: signal_results şimdiye kadar SADECE sabit 5/10/20 günlük getiriyi tutuyordu.
Ölçüm gösterdi ki 20 gün beklemek en kötü seçenek — taramalar o cetvelle sıfır
görünüyor. Artık her sinyal, GERÇEKÇİ ÇIKIŞ KURALLARIYLA da değerlendirilip
`signal_exits` tablosuna yazılır. Tier haritası bundan sonra bu tablodan okunabilir.

ÜÇ SABİT KURAL (aranmaz, seçilir — 1.200 kombinasyon denemek aşırı uydurma olurdu):
  A_3gun    · 3 gün sonra kapanıştan çık   (hiç eşiği yok)
  B_6_3     · +%6 hedef / -%3 stop         (aynı bar ikisi de olursa STOP sayılır)
  C_cmf5    · CMF 5 günlük negatife dönünce ertesi kapanışta çık

Her satır: getiri + XU100'e göre alfa + kaç gün tutuldu.
Idempotent — aynı sinyal+kural bir kez yazılır, tekrar koşmak güvenlidir.

Kullanım:  python cikis_sonuclari_yaz.py            (yalnız eksikleri işler)
           python cikis_sonuclari_yaz.py --hepsi    (tümünü yeniden yazar)
"""
import os
import sys
import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd

import cikis_kurali_backtest as C

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "patron.db")

KURALLAR = [
    ("A_3gun", lambda y, ap: C._kapanis(y, 2)),
    ("B_6_3",  lambda y, ap: C._hedef_stop(y, 6, 3)),
    ("C_cmf5", lambda y, ap: C._gosterge(y, C._israrli(ap["cmf5"] < 0, 1))),
]


def tabloyu_kur(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS signal_exits (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id    INTEGER NOT NULL,
        symbol       TEXT    NOT NULL,
        scan_type    TEXT    NOT NULL,
        signal_date  TEXT    NOT NULL,
        rule         TEXT    NOT NULL,   -- A_3gun | B_6_3 | C_cmf5
        exit_day     INTEGER,            -- girişten kaç gün sonra çıkıldı
        exit_ret     REAL,               -- işlemin getirisi (%)
        alpha        REAL,               -- XU100'e göre fark (%)
        evaluated_at TEXT,
        UNIQUE(signal_id, rule)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_signal_exits_tip "
                 "ON signal_exits(scan_type, rule)")
    conn.commit()


def main():
    hepsi = "--hepsi" in sys.argv
    C.cmf_dogrula()

    conn = sqlite3.connect(DB, timeout=60)
    tabloyu_kur(conn)

    sorgu = ("SELECT id, scan_date, symbol, scan_type FROM scan_signals "
             "WHERE bias='bullish'")
    if not hepsi:
        sorgu += " AND id NOT IN (SELECT DISTINCT signal_id FROM signal_exits)"
    sinyaller = pd.read_sql(sorgu, conn)
    sinyaller["scan_date"] = pd.to_datetime(sinyaller["scan_date"])
    sinyaller = sinyaller[~sinyaller["symbol"].str.upper()
                          .str.startswith(("XU", "XB", "XT", "XY"))]
    print(f"işlenecek sinyal: {len(sinyaller):,}"
          + ("" if hepsi else "  (yalnız eksikler)"))
    if sinyaller.empty:
        print("yeni sinyal yok — çıkılıyor."); conn.close(); return

    bench = C._oku("XU100")
    if bench is None:
        print("XU100 verisi yok, alfa hesaplanamaz — DURDU."); conn.close(); return
    bc = bench["Close"].astype(float)

    veri, apser = {}, {}
    for s in sorted(sinyaller["symbol"].unique()):
        d = C._oku(s)
        if d is None or len(d) < 60:
            continue
        veri[s] = d
        apser[s] = C.akilli_para_serileri(d)
    print(f"veri bulunan hisse: {len(veri)}")

    simdi = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    satirlar, atlanan = [], 0

    for sg in sinyaller.itertuples(index=False):
        d = veri.get(sg.symbol)
        if d is None:
            atlanan += 1; continue
        idx = d.index
        pos = idx.searchsorted(sg.scan_date)
        gir = pos + 1 if (pos < len(idx) and idx[pos] == sg.scan_date) else pos
        if gir + 2 >= len(idx):
            atlanan += 1; continue            # henüz olgunlaşmamış sinyal
        girfiy = float(d["Close"].iloc[gir])
        if girfiy <= 0:
            atlanan += 1; continue
        son = min(gir + C.MAX_GUN, len(idx) - 1)
        dilim = d.iloc[gir + 1: son + 1]
        if dilim.empty:
            atlanan += 1; continue

        yol = pd.DataFrame({
            "yuk": (dilim["High"].astype(float) / girfiy - 1) * 100,
            "dus": (dilim["Low"].astype(float) / girfiy - 1) * 100,
            "kap": (dilim["Close"].astype(float) / girfiy - 1) * 100,
        }, index=dilim.index)
        ap = apser[sg.symbol].loc[dilim.index]

        try:
            b0 = float(bc.loc[:idx[gir]].iloc[-1])
            piyasa = (bc.reindex(yol.index, method="ffill") / b0 - 1) * 100
        except Exception:
            piyasa = pd.Series(0.0, index=yol.index)

        gun_str = sg.scan_date.strftime("%Y-%m-%d")
        for ad, fn in KURALLAR:
            cik = fn(yol, ap)
            if cik is None:
                gun, r = len(yol) - 1, float(yol["kap"].iloc[-1])
            else:
                gun, r = cik
                gun = min(int(gun), len(yol) - 1)
            piy = float(piyasa.iloc[gun]) if not pd.isna(piyasa.iloc[gun]) else 0.0
            satirlar.append((sg.id, sg.symbol, sg.scan_type, gun_str, ad,
                             gun + 1, round(r, 4), round(r - piy, 4), simdi))

    print(f"atlanan (veri yok / olgunlaşmamış): {atlanan:,}")
    conn.executemany(
        "INSERT OR IGNORE INTO signal_exits "
        "(signal_id, symbol, scan_type, signal_date, rule, exit_day, exit_ret, alpha, evaluated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)", satirlar)
    conn.commit()

    toplam = conn.execute("SELECT COUNT(*) FROM signal_exits").fetchone()[0]
    print(f"yazılan satır: {len(satirlar):,} | tablo toplamı: {toplam:,}")

    print("\n--- kural bazında özet (tablodan okundu) ---")
    ozet = pd.read_sql(
        "SELECT rule, COUNT(*) n, ROUND(AVG(exit_ret),3) ort_getiri, "
        "ROUND(AVG(alpha),3) ort_alfa, ROUND(AVG(exit_day),2) ort_gun "
        "FROM signal_exits GROUP BY rule", conn)
    print(ozet.to_string(index=False))
    conn.close()


if __name__ == "__main__":
    main()
