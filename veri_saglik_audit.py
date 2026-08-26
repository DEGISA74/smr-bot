# -*- coding: utf-8 -*-
"""
veri_saglik_audit.py — TOPLU VERİ SAĞLIK DENETİMİ (27 Tem 2026, #12)
====================================================================
Amaç: 24 Tem XU100 olayının kör noktasını kapatmak. O sabahki denetim
"614 istek başarılı" diyordu ama XU100 kapanışının BOŞ olduğunu görmedi —
ERİŞİM ile DOĞRULUĞU birbirine karıştırıyordu. Bu script erişimi değil
BAR SAĞLIĞINI ölçer ve şu raporu üretir:

    Toplam dosya · 🟢 doğrulandı · 🔴 reddedildi · 🟡 bayat
    + kritik endeks (XU100 vb.) 🔴 ise → "YAYIN DURDURULMALI"

Her parquet için kontroller (veri_bekcisi + bist_calendar TEK KAYNAK):
  - Son kapanış boş/≤0 mu?               (24 Tem XU100 kör noktası)
  - Son barın herhangi bir fiyat alanı boş mu?
  - Son tarih BEKLENEN son işlem günü mü? (takvim — "dün" değil son seans)
  - Yapısal sorun var mı?                 (dogrula: Frankenstein / doji / bölünme…)
  - Kritik endekslerden biri 🔴 mı?        (XU100/XU030/XBANK/XTUMY/XUSIN)

Çalıştır:  python veri_saglik_audit.py          (rapor: logs/veri_saglik_report.md)
           python veri_saglik_audit.py --dry    (sadece konsol, dosya yazmaz)
"""
import os
import sys
import glob
import datetime as dt

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pandas as pd

import veri_bekcisi as _vb
try:
    import bist_calendar as _bc
    _CAL_OK = True
except Exception:
    _CAL_OK = False

BASE      = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("SMR_CACHE_DIR", os.path.join(BASE, "veriler"))
REPORT    = os.path.join(BASE, "logs", "veri_saglik_report.md")
DRY       = "--dry" in sys.argv

KRITIK = _vb.KRITIK_ENDEKSLER  # XU100/XU030/XBANK/XTUMY/XUSIN


def _beklenen_son_seans(now=None):
    """Şu an itibarıyla kapanmış en son BIST seansı (takvim tabanlı, 'dün' değil)."""
    now = now or dt.datetime.now()
    d = now.date()
    hm = now.hour * 100 + now.minute
    if _CAL_OK:
        if _bc.is_trading_day(d) and hm > 1830:
            return d
        prev = d - dt.timedelta(days=1)
        for _ in range(15):
            if _bc.is_trading_day(prev):
                return prev
            prev -= dt.timedelta(days=1)
        return prev
    # takvim yoksa: hafta sonunu kabaca atla
    prev = d if hm > 1830 else d - dt.timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= dt.timedelta(days=1)
    return prev


def _degerlendir(path, beklenen):
    """Tek parquet → ('green'|'yellow'|'red', sebepler, son_tarih)."""
    sebepler = []
    try:
        df = pd.read_parquet(path)
    except Exception as e:
        return 'red', [f'okunamadı: {str(e)[:40]}'], None
    if df is None or len(df) == 0:
        return 'red', ['boş dosya'], None

    son_tarih = pd.Timestamp(df.index[-1]).date()

    # 1) Son bar eksik mi? (boş kapanış / boş fiyat alanı — asıl kör nokta)
    try:
        sonbar = df.iloc[-1]
        eksik = [k for k in ('Open', 'High', 'Low', 'Close')
                 if k in df.columns and (pd.isna(sonbar[k]) or float(sonbar[k]) <= 0)]
        if eksik:
            sebepler.append(f"son bar boş alan: {', '.join(eksik)}")
    except Exception:
        pass

    # 2) Yapısal denetim (veri_bekcisi — Frankenstein / doji / bölünme / hacim / bayat)
    try:
        ok, yapisal = _vb.dogrula(df, os.path.basename(path).replace('_1d.parquet', ''))
        if not ok:
            sebepler.extend(yapisal)
    except Exception as e:
        sebepler.append(f"denetim hatası: {str(e)[:40]}")

    # Kırmızı tetik: son bar eksik VEYA yapısal SON BAR EKSİK/KAPANIŞ sorunu
    kirmizi = any('son bar boş' in s or 'SON BAR EKSİK' in s or 'KAPANIŞ TAMAMEN BOŞ' in s
                  for s in sebepler)
    if kirmizi:
        return 'red', sebepler, son_tarih

    # 3) Bayatlık: son tarih beklenen son seanstan eski mi?
    if son_tarih < beklenen:
        sebepler.append(f"bayat: son {son_tarih}, beklenen {beklenen}")
        return 'yellow', sebepler, son_tarih

    # Yapısal sorun (bayat değil ama Frankenstein/doji vb.) → yine reddet
    if sebepler:
        return 'red', sebepler, son_tarih

    return 'green', [], son_tarih


def collect_audit(now=None, cache_dir=None):
    """BIST günlük kasasının yazmayan sağlık fotoğrafını döndürür.

    ``veri_saglik_audit.py --dry`` ile kapanış otomasyonu aynı kapıları
    kullanır; biri temiz derken diğeri bozuk veriyle Master Scan başlatamaz.
    """
    beklenen = _beklenen_son_seans(now)
    read_dir = cache_dir or CACHE_DIR
    dosyalar = sorted(glob.glob(os.path.join(read_dir, "*_1d.parquet")))
    # kripto/emtia/FX hariç: BIST hisse + endeks odağı (.IS ve X-endeksleri)
    dosyalar = [p for p in dosyalar
                if p.upper().replace('\\', '/').split('/')[-1].endswith('.IS_1D.PARQUET')
                or '.IS_1d' in os.path.basename(p)]

    yesil, sari, kirmizi = [], [], []
    kritik_kirmizi = []

    for p in dosyalar:
        sym = os.path.basename(p).replace('_1d.parquet', '')
        durum, sebep, _st = _degerlendir(p, beklenen)
        if durum == 'green':
            yesil.append(sym)
        elif durum == 'yellow':
            sari.append((sym, "; ".join(sebep)))
        else:
            kirmizi.append((sym, "; ".join(sebep)))
            if _vb.kritik_endeks_mi(sym):
                kritik_kirmizi.append(sym)

    return {
        "beklenen": beklenen,
        "toplam": len(dosyalar),
        "yesil": yesil,
        "sari": sari,
        "kirmizi": kirmizi,
        "kritik_kirmizi": kritik_kirmizi,
        "yayin_durdur": bool(kritik_kirmizi),
    }


def main():
    sonuc = collect_audit()
    beklenen = sonuc["beklenen"]
    toplam = sonuc["toplam"]
    yesil = sonuc["yesil"]
    sari = sonuc["sari"]
    kirmizi = sonuc["kirmizi"]
    kritik_kirmizi = sonuc["kritik_kirmizi"]
    yayin_durdur = sonuc["yayin_durdur"]

    # ── RAPOR ────────────────────────────────────────────────────────────────
    L = []
    L.append(f"# 🛡 Veri Sağlık Denetimi — {dt.datetime.now():%Y-%m-%d %H:%M}")
    L.append("")
    L.append(f"- Beklenen son seans: **{beklenen}**  ·  Denetlenen dosya: **{toplam}**")
    L.append(f"- 🟢 Doğrulandı: **{len(yesil)}**")
    L.append(f"- 🟡 Bayat: **{len(sari)}**")
    L.append(f"- 🔴 Reddedildi: **{len(kirmizi)}**")
    L.append("")
    if yayin_durdur:
        L.append(f"## 🛑 YAYIN DURDURULMALI — kritik endeks reddedildi: "
                 f"{', '.join(kritik_kirmizi)}")
        L.append("Ortak terazi bozuk → tüm hisselerin endekse-göre gücü çöp. "
                 "Master Scan başlatılmamalı, ekran bu endeksi göstermemeli.")
        L.append("")
    else:
        L.append("## ✅ Kritik endeksler (XU100 vb.) sağlam — yayın serbest.")
        L.append("")
    if kirmizi:
        L.append("### 🔴 Reddedilenler")
        for sym, r in kirmizi:
            L.append(f"- **{sym}** — {r}")
        L.append("")
    if sari:
        L.append("### 🟡 Bayat dosyalar (taramadan çıkar, durdurma)")
        for sym, r in sari:
            L.append(f"- {sym} — {r}")
        L.append("")
    rapor = "\n".join(L)

    print(rapor)
    if not DRY:
        try:
            os.makedirs(os.path.dirname(REPORT), exist_ok=True)
            with open(REPORT, "w", encoding="utf-8") as f:
                f.write(rapor)
            print(f"\n[rapor] {REPORT}")
        except Exception as e:
            print(f"[rapor yazılamadı] {e}")

    # Exit kodu: kritik endeks kırmızıysa 2 (cron/otomasyon yakalasın), yoksa 0
    return 2 if yayin_durdur else 0


if __name__ == "__main__":
    sys.exit(main())
