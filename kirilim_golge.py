# -*- coding: utf-8 -*-
"""KIRILIM TEYİDİ — günlük GÖLGE tarama + admin Telegram feed'i.

İKİ İŞ (biri birincil):
  1) BİRİNCİL — ölçüm: likit-200'de son barı tarar, 'tabandan hacimli kırılım'
     (AKFYE 8 Eyl tipi) ateşleyenleri patron.db/scan_signals'a `kirilim_teyidi`
     adıyla yazar. `alfa_karne` bunu otomatik ölçer. Panel/AI'da GÖRÜNMEZ.
  2) İKİNCİL — kullan: aynı listeyi admin'e Telegram DM atar (kullanıcının gözü
     bugünden aksiyon alsın; müşteriye gitmez). TG hatası ölçümü bozmaz (try/except).

SIRALAMA (backtest_kirilim_teyidi.py --siralama ile ölçüldü): "en likit ilk" YANLIŞ —
likidite alfa'yı TERS sıralıyor. Alfa'yı ayıran: mum gövdesi + 52h konum + hacim katı.
Feed güç skoruyla sıralar; likidite iki listeye böler:
  • RAHAT işlem görenler (üst 2/3 likidite)   • İNCE ama güçlü kırılım (alt 1/3)

Kullanım:
    python kirilim_golge.py            # DB yaz + TG gönder
    python kirilim_golge.py --kuru     # yazma/gönderme YOK, mesajı ekrana bas
    VPS cron: 14:40 UTC = 17:40 TR (10 Eyl: eski 23:30 TR çok geçti; kapanış-öncesi gün-içi bar)   [[project_kirilim_teyidi]]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import requests

from bist_data_store import active_version_id, load_manifest, read_active
from signal_policy import (
    assign_event_metadata_for_date,
    ensure_event_schema,
    register_scan_run,
)
from kirilim_core import SCAN_TYPE, last_bar_signal

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
DB = ROOT / "patron.db"
LIQ_N = 200
LIQ_WIN = 120
INCE_ESIK = 1.0 / 3.0     # likit-200'ün alt 1/3'ü = "ince"
ADMIN_ID = "1034525990"   # firsat_radari.py ile aynı admin


# ── Telegram (firsat_radari.py kalıbı) ───────────────────────────────────
def _token():
    for p in ("/home/wm11tr/weektweet/.env", "/home/wm11tr/insider/.env"):
        try:
            for line in open(p):
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    return line.split("=", 1)[1].strip()
        except Exception:
            pass
    import os
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def tg_send(chat_id, text):
    tok = _token()
    if not tok:
        print("(telegram token yok — gönderilmedi)")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=25,
        )
        if r.status_code != 200:
            print("telegram HTTP", r.status_code, r.text[:160])
            return False
        return True
    except Exception as e:
        print("telegram hata", e)
        return False


def _liquid(symbols, surum, n):
    """Medyan TL ciroya göre en likit n hisse — (sembol, df, ciro) döner."""
    skor = []
    for s in symbols:
        df = read_active(s, surum)
        if df is None or df.empty or "Volume" not in df.columns:
            continue
        tail = df.tail(LIQ_WIN)
        ciro = (pd.to_numeric(tail["Close"], errors="coerce")
                * pd.to_numeric(tail["Volume"], errors="coerce")).median()
        if pd.notna(ciro) and ciro > 0:
            skor.append((float(ciro), s, df))
    skor.sort(key=lambda x: x[0], reverse=True)
    return [(s, df, ciro) for ciro, s, df in skor[:n]]


def _guc_sirala(hits):
    """Güç skoru = mum gövdesi + 52h konum + hacim katı yüzdelik-sırasının ortalaması.
    Ölçekten bağımsız (keyfi ağırlık yok); None en kötü sayılır. Yüksek=iyi."""
    n = len(hits)
    if n == 0:
        return hits

    def _pct(key):
        vals = [(-1e9 if h.get(key) is None else h[key]) for h in hits]
        order = sorted(range(n), key=lambda i: vals[i])
        pr = [0.0] * n
        for rank, i in enumerate(order):
            pr[i] = rank / (n - 1) if n > 1 else 1.0
        return pr

    pb, pp, pv = _pct("body_pct"), _pct("pos52"), _pct("volx")
    for i, h in enumerate(hits):
        h["guc"] = (pb[i] + pp[i] + pv[i]) / 3.0
    return sorted(hits, key=lambda h: h["guc"], reverse=True)


def _tr(x, dec=2):
    """Türkçe sayı: ondalık virgül (22.06 -> '22,06')."""
    return f"{x:.{dec}f}".replace(".", ",")


def _satir(i, h):
    parts = []
    if h.get("body_pct") is not None:
        parts.append(f"Bugün %{_tr(h['body_pct'], 1)} yükselerek yukarı kırdı")
    if h.get("volx") is not None:
        parts.append(f"hacim normalin {_tr(h['volx'], 1)} katı")
    if h.get("pos52") is not None:
        parts.append(f"son 52 haftanın %{h['pos52']}'lik diliminde")
    return f"{i}. {h['sym']} — {_tr(h['close'])} TL\n   " + " · ".join(parts)


def _mesaj(run_str, rahat, ince):
    d = ".".join(reversed(run_str.split("-")[1:]))  # YYYY-MM-DD -> DD.MM
    L = [f"🎯 GÜNÜN KIRILIMLARI · {d}",
         "Uzun bir tabandan, yüksek hacimle yukarı kıran hisseler.",
         "(deneme aşamasında — en güçlü kırılım en üstte)", ""]
    L.append("💧 BOL HACİMLİ — al-satı kolay")
    L += ([_satir(i + 1, h) for i, h in enumerate(rahat)] if rahat
          else ["   (bugün yok)"])
    L.append("")
    L.append("🔎 GÜÇLÜ AMA İNCE — az işlem görüyor, dikkat")
    L += ([_satir(i + 1, h) for i, h in enumerate(ince)] if ince
          else ["   (bugün yok)"])
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kuru", action="store_true", help="yazma/gönderme YOK, ekrana bas")
    ap.add_argument("--test-tg", dest="test_tg", action="store_true",
                    help="admin'e [TEST] DM at ama DB'ye YAZMA (format önizleme)")
    a = ap.parse_args()

    surum = active_version_id()
    manifest = load_manifest()
    if not manifest:
        print("Aktif fiyat kasası yok — çıkılıyor.")
        return
    syms = [s for s in manifest["symbols"]
            if s.endswith(".IS") and not s.startswith("X")]
    uni = _liquid(syms, surum, LIQ_N)
    if not uni:
        print("Likit evren boş — çıkılıyor.")
        return

    # Likidite tabanı: likit-200'ün alt 1/3 cirosu = "ince" eşiği.
    ciro_list = sorted(c for _, _, c in uni)
    esik = ciro_list[int(len(ciro_list) * INCE_ESIK)]

    # Kasadaki en taze işlem günü = koşu tarihi. Bayat semboller atlanır.
    run_date = max(df.index[-1] for _, df, _ in uni)
    run_str = pd.Timestamp(run_date).strftime("%Y-%m-%d")

    hits, stale = [], 0
    for sym, df, ciro in uni:
        if df.index[-1] != run_date:
            stale += 1
            continue
        sig = last_bar_signal(df)
        if sig:
            hits.append({"sym": sym.replace(".IS", ""), "close": float(sig["close"]),
                         "body_pct": sig["body_pct"], "volx": sig["volx"],
                         "pos52": sig["pos52"], "ciro": ciro})

    hits = _guc_sirala(hits)
    rahat = [h for h in hits if h["ciro"] >= esik]
    ince = [h for h in hits if h["ciro"] < esik]

    print(f"KIRILIM TEYİDİ gölge | koşu {run_str} | likit {len(uni)} | "
          f"bayat {stale} | sinyal {len(hits)} (rahat {len(rahat)} / ince {len(ince)})")
    msg = _mesaj(run_str, rahat, ince)
    print("-" * 60)
    print(msg)
    print("-" * 60)

    if a.kuru:
        print("(kuru koşu — DB'ye YAZILMADI, TG gönderilmedi)")
        return

    if a.test_tg:
        ok = tg_send(ADMIN_ID, "[TEST — format önizleme, gün içi veri] \n" + msg)
        print(f"-> TEST Telegram: {'gönderildi' if ok else 'GÖNDERİLEMEDİ'} (DB'ye YAZILMADI)")
        return

    # BİRİNCİL: DB yazımı (ölçüm) — TG'den önce, TG hatası bunu bozamaz.
    rows = [(run_str, h["sym"], SCAN_TYPE, h["body_pct"], "bullish", h["close"], "BIST")
            for h in hits]
    con = sqlite3.connect(DB, timeout=60)
    try:
        ensure_event_schema(con)
        if rows:
            con.executemany(
                "INSERT OR IGNORE INTO scan_signals "
                "(scan_date, symbol, scan_type, score, bias, entry_price, category) "
                "VALUES (?,?,?,?,?,?,?)", rows)
        previous = register_scan_run(con, SCAN_TYPE, run_str, len(rows), "BIST")
        assign_event_metadata_for_date(con, SCAN_TYPE, run_str, previous)
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM scan_signals WHERE scan_type=?",
                        (SCAN_TYPE,)).fetchone()[0]
        print(f"-> DB: {len(rows)} sinyal yazıldı. kirilim_teyidi toplam {n} satır.")
    finally:
        con.close()

    # İKİNCİL: admin Telegram (sadece sinyal varsa; boş günlerde gürültü yapma).
    if hits:
        ok = tg_send(ADMIN_ID, msg)
        print(f"-> Telegram admin'e: {'gönderildi' if ok else 'GÖNDERİLEMEDİ'}")
    else:
        print("-> sinyal yok, Telegram atlanmadı (boş gün gürültüsü yok)")


if __name__ == "__main__":
    main()
