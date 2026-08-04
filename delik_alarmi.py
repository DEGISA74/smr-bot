#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""delik_alarmi.py — Günlük veri deliği alarmı, TAKVİMLE UYUMLU (4 Ağu 2026).

Amaç: "dün işlem günüydü ama X hissede bar yok" durumunu her sabah otomatik yakalamak.
TTKOM 4 Ağu olayı (07-31 + 08-03 barları sessizce uçtu) elle bulundu — bu bir daha
gözden kaçmasın.

TAKVİM PATRONDUR:
  - Alarm önce `bist_calendar`'a sorar: hedef gün gerçek işlem günü müydü?
  - Tatil / hafta sonu → o gün bar zaten olmayacak → SESSİZ kalır (yanlış alarm yok).
  - Yarım gün (arefe) → işlem günü sayılır, kontrol edilir.

SAHTE ALARM ELEME (çoğunluk kuralı):
  - Bir gün için hisselerin çoğunda bar varsa → o gün piyasa AÇIKTI.
  - O günü içermeyen hisseler "şüpheli". ISBTR gibi gerçekten işlem görmeyenler
    --fix modunda İsyatirim'e sorulunca elenir (İsyatirim'de de yoksa gerçek tatil).

Mod:
  python delik_alarmi.py            → SADECE tespit + Telegram özeti (veri yazMAZ)
  python delik_alarmi.py --fix      → ayrıca İsyatirim'den doldurur (sağlam sarmalayıcı)
  python delik_alarmi.py --dry      → sadece ekrana yaz, Telegram YOK, yazma YOK
  python delik_alarmi.py --gun 3    → son 3 işlem gününü kontrol et (varsayılan 2)
"""

from __future__ import annotations

import os
import sys
import json
import glob
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, date, timedelta, timezone

import pandas as pd

# Windows konsolu (cp1254) emoji'de patlıyor — çıktıyı UTF-8'e sabitle (Linux'ta zaten UTF-8)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT    = Path(__file__).parent
VERILER = ROOT / "veriler"
CFG     = ROOT / "telegram_config.json"
ADMIN_ID = 1034525990
TR = timezone(timedelta(hours=3))

MAJORITY = 0.80   # bir günü hisselerin ≥%80'i içeriyorsa o gün piyasa açıktı


# ---- Telegram (gorev_bekcisi ile aynı kalıp) --------------------------------
def tg(text: str) -> bool:
    try:
        token = json.loads(CFG.read_text(encoding="utf-8"))["bot_token"]
        data = urllib.parse.urlencode({"chat_id": ADMIN_ID, "text": text}).encode()
        url = "https://api.telegram.org/bot%s/sendMessage" % token
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=25).read()
        return True
    except Exception as e:
        print("[delik] telegram gonderilemedi:", e)
        return False


# ---- Takvim: son N işlem günü ------------------------------------------------
def son_islem_gunleri(n: int = 2) -> list[str]:
    """Bugünden geriye doğru, TAKVİME göre son n işlem gününü döndürür (YYYY-MM-DD).
    Bugün henüz kapanmadıysa (saat < 18:45) bugünü dahil etmez."""
    try:
        from bist_calendar import is_trading_day
    except Exception:
        # takvim yoksa: hafta sonunu ele, tatil bilinmez (güvenli taraf: sadece hafta içi)
        is_trading_day = lambda d: d.weekday() < 5

    now = datetime.now(TR)
    d = now.date()
    # Bugün işlem günü ama seans daha bitmediyse bugünü atla
    if is_trading_day(d) and now.hour < 18:
        d = d - timedelta(days=1)
    out = []
    guard = 0
    while len(out) < n and guard < 30:
        if is_trading_day(d):
            out.append(d.isoformat())
        d = d - timedelta(days=1)
        guard += 1
    return out


# ---- Depo tarama -------------------------------------------------------------
def _stock_files() -> list[Path]:
    fs = []
    for f in glob.glob(str(VERILER / "*.IS_1d.parquet")):
        sym = os.path.basename(f).replace(".IS_1d.parquet", "")
        if sym.startswith("X"):   # endeks: İsyatirim vermez, ayrı gapfill'i var → atla
            continue
        fs.append(Path(f))
    return fs


def tespit(gunler: list[str]) -> dict:
    """Her hedef gün için: o günü içeren hisse sayısı + içermeyen (şüpheli) liste."""
    files = _stock_files()
    date_sets = {}
    for f in files:
        try:
            idx = pd.read_parquet(f, columns=["Close"]).index.astype(str).str.slice(0, 10)
            date_sets[f] = set(idx)
        except Exception:
            date_sets[f] = None  # okunamayan dosya = ayrı sorun
    toplam = len([v for v in date_sets.values() if v is not None])
    rapor = {}
    for g in gunler:
        var = [f for f, s in date_sets.items() if s is not None and g in s]
        yok = [f for f, s in date_sets.items() if s is not None and g not in s]
        acik = (len(var) / toplam) >= MAJORITY if toplam else False
        rapor[g] = {
            "piyasa_acik": acik,
            "var_sayi": len(var),
            "toplam": toplam,
            "supheli": [os.path.basename(f).replace(".IS_1d.parquet", "") for f in yok],
            "supheli_files": yok,
        }
    okunamayan = [os.path.basename(f).replace(".IS_1d.parquet", "")
                  for f, s in date_sets.items() if s is None]
    return {"gunler": rapor, "okunamayan": okunamayan}


# ---- Şüpheliyi İsyatirim'e sor: gerçek delik mi, gerçek tatil mi? -----------
def incele(sym: str, gun: str, yaz: bool) -> str:
    """Şüpheli hisse-günü İsyatirim'e sorar.
      - İsyatirim'de VAR → gerçek delik. yaz=True ise parquet'e ekler.
      - İsyatirim'de YOK → hisse o gün gerçekten işlem görmemiş (tatil/askı).
    Dönüş: 'dolduruldu' | 'gercek_delik' | 'gercek_tatil' | 'zaten_var' | 'hata'."""
    from isyatirim_saglik import robust_isyatirim
    f = VERILER / f"{sym}.IS_1d.parquet"
    try:
        cur = pd.read_parquet(f)
    except Exception:
        return "hata"
    if gun in cur.index.astype(str).str.slice(0, 10).values:
        return "zaten_var"
    df, _ = robust_isyatirim(f"{sym}.IS", period_days=20, want_dates=[gun])
    if df is None or df.empty:
        return "gercek_tatil"
    add = df.loc[df.index.astype(str).str.slice(0, 10) == gun]
    if add.empty:
        return "gercek_tatil"
    if not yaz:
        return "gercek_delik"     # doğrulandı ama yazılmadı (sadece alarm)
    merged = pd.concat([cur, add])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    if len(merged) < len(cur):    # gerileme koruması
        return "hata"
    merged.to_parquet(f, compression="snappy")
    return "dolduruldu"


# ---- Ana akış ----------------------------------------------------------------
def main():
    argv = sys.argv[1:]
    dry  = "--dry" in argv
    fix  = "--fix" in argv
    n    = 2
    if "--gun" in argv:
        try: n = int(argv[argv.index("--gun") + 1])
        except Exception: n = 2

    gunler = son_islem_gunleri(n)
    if not gunler:
        print("[delik] kontrol edilecek işlem günü yok (tatil?) — sessiz.")
        return

    r = tespit(gunler)
    satirlar = []
    gercek_delik_toplam = 0

    for g in gunler:
        blok = r["gunler"][g]
        if not blok["piyasa_acik"]:
            satirlar.append(f"• {g}: piyasa KAPALIYDI (hisselerin çoğunda yok) — atlandı")
            continue
        supheli = blok["supheli"]
        if not supheli:
            satirlar.append(f"• {g}: ✅ tüm hisselerde bar var")
            continue

        if dry:
            # çevrimdışı hızlı test: İsyatirim'e sormaz, ham şüphelileri döker
            gercek_delik_toplam += len(supheli)
            satirlar.append(f"• {g}: (doğrulanmadı) {len(supheli)} şüpheli → "
                            + ", ".join(supheli[:12]) + (" …" if len(supheli) > 12 else ""))
            continue

        # DOĞRULA: her şüpheliyi İsyatirim'e sor (gerçek delik ↔ gerçek tatil)
        delik, dolduruldu, tatil, hata = [], [], [], []
        for sym in supheli:
            sonuc = incele(sym, g, yaz=fix)
            if sonuc == "dolduruldu":   dolduruldu.append(sym)
            elif sonuc == "gercek_delik": delik.append(sym)
            elif sonuc == "gercek_tatil": tatil.append(sym)
            elif sonuc == "hata":       hata.append(sym)
        gercek = len(delik) + len(dolduruldu) + len(hata)
        gercek_delik_toplam += gercek
        if gercek == 0:
            satirlar.append(f"• {g}: ✅ {len(tatil)} şüpheli vardı ama hepsi gerçek tatil (o gün işlem görmemiş)")
            continue
        parts = [f"• {g}:"]
        if delik:      parts.append(f"⚠ {len(delik)} GERÇEK DELİK ({', '.join(delik[:8])})")
        if dolduruldu: parts.append(f"🔧 {len(dolduruldu)} dolduruldu ({', '.join(dolduruldu[:8])})")
        if hata:       parts.append(f"⛔ {len(hata)} HATA ({', '.join(hata[:8])})")
        if tatil:      parts.append(f"⚪ {len(tatil)} gerçek tatil (sessiz)")
        satirlar.append("  ".join(parts))

    if r["okunamayan"]:
        satirlar.append(f"• ⛔ OKUNAMAYAN dosya: {', '.join(r['okunamayan'][:12])}")
        gercek_delik_toplam += len(r["okunamayan"])

    baslik = "🕳 VERİ DELİĞİ ALARMI" if gercek_delik_toplam else "✅ VERİ DELİĞİ KONTROL — temiz"
    mesaj = baslik + "\n" + "\n".join(satirlar)
    print(mesaj)

    # Telegram: sadece gerçek delik varsa VE dry değilse gönder (temizse sessiz)
    if gercek_delik_toplam and not dry:
        tg(mesaj)


if __name__ == "__main__":
    main()
