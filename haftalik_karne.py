#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HAFTALIK KARNE — Telegram liste motorlarinin gercek getirisi + XU100 alfasi.

Sonuc defterlerini (patron2/patron3/patron4/firsat_karne.db) okur, her liste icin
"bu hafta" (son 7 seans) ve "tum donem" alfasini (getiri - ayni donem XU100) hesaplar,
kisa bir karne mesaji uretir. --send ile yoneticiye Telegram'dan gonderir; argumansiz yazar.

Salt-okur: sonuc defterlerini uretmez (o isi aksam settle cron'lari yapar), sadece ozetler.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
VER = os.path.join(BASE, "veriler")
ADMIN_ID = "1034525990"
ISTANBUL = ZoneInfo("Europe/Istanbul")


def _token() -> str | None:
    for p in ("/home/wm11tr/weektweet/.env", "/home/wm11tr/insider/.env"):
        try:
            for line in open(p, encoding="utf-8"):
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    return line.split("=", 1)[1].strip()
        except Exception:
            pass
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def _send(text: str) -> bool:
    tok = _token()
    if not tok:
        print("token yok")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": ADMIN_ID, "text": text, "disable_web_page_preview": True},
            timeout=25,
        )
        if r.status_code != 200:
            print("telegram HTTP", r.status_code, r.text[:160])
            return False
        return True
    except Exception as e:
        print("telegram hata", e)
        return False


def _daily(sym: str):
    p = os.path.join(VER, f"{sym}.IS_1d.parquet")
    if not os.path.exists(p):
        return None
    f = pd.read_parquet(p)
    idx = pd.DatetimeIndex(pd.to_datetime(f.index))
    f.index = (idx.tz_localize(None) if idx.tz is not None else idx).normalize()
    return f[~f.index.duplicated(keep="last")].sort_index()


XU = _daily("XU100")


def _xu_between(d0, d1):
    try:
        return (float(XU.at[pd.Timestamp(d0).normalize(), "Close"]) /
                float(XU.at[pd.Timestamp(d1).normalize(), "Close"]))
    except Exception:
        return None


def _xu_ret(d0, d1):
    try:
        return (float(XU.at[pd.Timestamp(d1).normalize(), "Close"]) /
                float(XU.at[pd.Timestamp(d0).normalize(), "Close"]) - 1) * 100
    except Exception:
        return None


def _xu_horizon(d0, h):
    d0 = pd.Timestamp(d0).normalize()
    if d0 not in XU.index:
        return None
    later = XU.index[XU.index > d0]
    if len(later) < h:
        return None
    return (float(XU.at[later[h - 1], "Close"]) / float(XU.at[d0, "Close"]) - 1) * 100


def _summ(pairs):
    """pairs: [(getiri, alfa), ...] -> (N, ort_getiri, ort_alfa, pozitif%, endeksi_gecen%)"""
    pairs = [(g, a) for g, a in pairs if g is not None and a is not None]
    if not pairs:
        return None
    n = len(pairs)
    g = sum(p[0] for p in pairs) / n
    a = sum(p[1] for p in pairs) / n
    pos = 100.0 * sum(1 for p in pairs if p[0] > 0) / n
    beat = 100.0 * sum(1 for p in pairs if p[1] > 0) / n
    return n, g, a, pos, beat


def _verdict(alpha):
    if alpha is None:
        return "•"
    if alpha > 0.3:
        return "✅"
    if alpha < -0.3:
        return "🔴"
    return "⚪"


def _engine_pairs(db, engine, cutoff):
    """patron2/3.db results -> (this_week_pairs, all_pairs) close-to-close vs XU100."""
    if not os.path.exists(db):
        return [], []
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT signal_date, evaluation_date, close_to_close_return_pct "
        "FROM results WHERE engine=? ORDER BY signal_date", (engine,)).fetchall()
    con.close()
    allp, week = [], []
    for r in rows:
        g = r["close_to_close_return_pct"]
        x = _xu_ret(r["signal_date"], r["evaluation_date"])
        a = (g - x) if (g is not None and x is not None) else None
        allp.append((g, a))
        if str(r["signal_date"]) >= cutoff:
            week.append((g, a))
    return week, allp


def _firsat_pairs(db, cutoff, alarm_only):
    if not os.path.exists(db):
        return [], []
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    q = ("SELECT r.date, r.ret_h5_close FROM firsat_results r "
         "JOIN firsat_signals s ON s.date=r.date AND s.tk=r.tk AND s.kutu=r.kutu "
         "WHERE r.complete=1" + (" AND s.alarm_eligible=1" if alarm_only else ""))
    rows = con.execute(q).fetchall()
    con.close()
    allp, week = [], []
    for r in rows:
        g = r["ret_h5_close"]
        x = _xu_horizon(r["date"], 5)
        a = (g - x) if (g is not None and x is not None) else None
        allp.append((g, a))
        if str(r["date"]) >= cutoff:
            week.append((g, a))
    return week, allp


def build_message() -> str:
    today = datetime.now(ISTANBUL).date()
    cutoff = str(today - timedelta(days=8))
    lines = [
        f"📊 HAFTALIK KARNE — Telegram listeleri ({today.strftime('%d.%m.%Y')})",
        "Ölçü: liste getirisi − aynı dönem XU100 = ALFA (gerçek fayda). Alfa artı ise endeksi geçmiş.",
        "",
    ]
    rows = [
        ("🚀 Yüksek Getiri V3 (yayın)", lambda: _engine_pairs(os.path.join(BASE, "patron3.db"), "v3", cutoff)),
        ("📈 Yüksek Getiri V2 (gölge)", lambda: _engine_pairs(os.path.join(BASE, "patron2.db"), "v2", cutoff)),
        ("🏛 Tavan (ücretsiz)", lambda: _engine_pairs(os.path.join(BASE, "patron2.db"), "v1", cutoff)),
        ("🎯 Fırsat (alarm · T+5)", lambda: _firsat_pairs(os.path.join(BASE, "firsat_karne.db"), cutoff, True)),
    ]
    for label, fn in rows:
        week, allp = fn()
        sa = _summ(allp)
        if not sa:
            lines.append(f"{label}: veri yok")
            continue
        sw = _summ(week)
        v = _verdict(sa[2])
        wk = f"bu hafta alfa {sw[2]:+.1f}% (N={sw[0]})" if sw else "bu hafta yeni sinyal yok"
        lines.append(f"{label}  {v}")
        lines.append(f"   {wk} · tüm dönem alfa {sa[2]:+.1f}% (N={sa[0]}, endeksi geçen %{sa[4]:.0f})")
    lines += [
        "",
        "⚠️ Not: Örneklem küçük ve dönem çoğunlukla yükselen tape. Listelerin ‘zirve’ hareketi "
        "güçlü ama kapanışta erir → kâr zirvede alınmalı, tutmakla değil. Alfa negatifse liste "
        "o dönem endeksin altında kalmış demektir.",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Haftalık liste karnesi")
    ap.add_argument("--send", action="store_true", help="Yöneticiye Telegram'dan gönderir (varsayılan: yazar)")
    args = ap.parse_args()
    if XU is None:
        print("XU100 verisi yok; karne üretilemedi.")
        return 1
    msg = build_message()
    print(msg)
    if args.send:
        print("\n[gönderiliyor...]", "OK" if _send(msg) else "BAŞARISIZ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
