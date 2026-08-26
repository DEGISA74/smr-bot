#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""depo_tazelik.py — LOKAL BIST deposu bayat mı? (18 Ağu 2026)

Neden var: `sync_from_vps.sh` içindeki saat kapısı (hafta içi 07:00–19:00) PC
akşam veya hafta sonu açıldığında senkronu tamamen susturuyordu. 18 Ağu'da PC
uzun süre kapalı kaldı, depo 14:00'da dondu ve kapanış fiyatları elle çekildi.
Bu modül kapının dışında "telafi turu gerekli mi?" sorusuna LOKAL veriyle cevap
verir — VPS'e hiç dokunmaz, dolayısıyla gece boyu bedava çalışır.

Ölçüt: onaylı sürümün manifestinde, BEKLENEN son işlem gününün barı hisselerin
en az %90'ında var mı? Yoksa depo bayattır.

Çıkış kodu (kabuk için):
  0 → BAYAT, senkron koşulmalı
  1 → TAZE, bir şey yapma
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "health" / "bist_store"
TR = timezone(timedelta(hours=3))          # Türkiye sabit UTC+3
KAPANIS_KESIN = (18, 45)                   # bu saatten önce bugünün barı beklenmez
# Esik ortam degiskeniyle ezilebilir: telafi yolunu test ederken
# SMR_TAZELIK_ESIK=1.1 vererek depo "bayat" gosterilir.
ESIK = float(os.environ.get("SMR_TAZELIK_ESIK", "0.90"))   # bu oranda son bar olmalı


def _islem_gunu(d: date) -> bool:
    """Takvim varsa ona sor; yoksa hafta içi say (takvim yoksa tatilde fazladan
    bir senkron koşarız — zararsız taraf)."""
    try:
        from bist_calendar import is_trading_day
        return bool(is_trading_day(d))
    except Exception:
        return d.weekday() < 5


def beklenen_gun(now: datetime | None = None) -> date:
    """Depoda bulunması GEREKEN son bar günü."""
    now = now or datetime.now(TR)
    d = now.date()
    kapandi = (now.hour, now.minute) >= KAPANIS_KESIN
    if _islem_gunu(d) and kapandi:
        return d
    for _ in range(10):                    # en fazla 10 gün geri (uzun tatil payı)
        d -= timedelta(days=1)
        if _islem_gunu(d):
            return d
    return d


def _sayim(hedef: date) -> tuple[int, int, int]:
    """(tam_o_gun, o_gunden_SONRASI, toplam). Manifest okunamazsa (0, 0, 0)."""
    try:
        vid = json.loads((STORE / "active.json").read_text(encoding="utf-8"))["version_id"]
        syms = json.loads((STORE / "manifests" / f"{vid}.json").read_text(encoding="utf-8"))["symbols"]
    except Exception:
        return 0, 0, 0                     # manifest okunamıyorsa bayat say
    hedef_s = str(hedef)
    esit = ileri = 0
    for m in syms.values():
        son = m.get("last") or ""
        if son == hedef_s:
            esit += 1
        elif son > hedef_s:
            ileri += 1
    return esit, ileri, len(syms)


def depo_durumu() -> tuple[date, int, int]:
    """(beklenen_gun, o_gunun_bari_olan, toplam_sembol)."""
    hedef = beklenen_gun()
    esit, _ileri, toplam = _sayim(hedef)
    return hedef, esit, toplam


# ── TARAMA YAZIM KAPISI (19 Ağu 2026) ────────────────────────────────────────
# 18 Ağu kazası: PC uzun süre kapalı kalınca lokal ayna 13:55'te dondu ve o akşamki
# tarama öğlen fiyatlarıyla 1.437 sinyal yazdı (giriş fiyatı ortalama %1,8, en fazla
# %15 sapmalı). Satırlar silindi. Bir daha oluşmasın diye tarama, sinyal yazmadan
# ÖNCE buraya sorar. Aynı ölçüt (beklenen seansın barı hisselerin ≥%90'ında var mı)
# telafi senkronunda da kullanılıyor — tek kaynak.
_IZIN_CACHE: dict[str, tuple[bool, str]] = {}


def _aktif_surum() -> str:
    try:
        return json.loads((STORE / "active.json").read_text(encoding="utf-8"))["version_id"]
    except Exception:
        return ""


def yazim_izni(now: datetime | None = None) -> tuple[bool, str]:
    """(izin_var_mi, gerekçe). Aynı sürüm için sonuç önbelleklenir — tarama
    boyunca onlarca kez çağrılır, her seferinde manifest okumak israf olur."""
    anahtar = _aktif_surum()
    if anahtar and anahtar in _IZIN_CACHE:
        return _IZIN_CACHE[anahtar]
    hedef, guncel, toplam = depo_durumu()
    oran = (guncel / toplam) if toplam else 0.0
    if oran >= ESIK:
        sonuc = (True, "")
    else:
        # 26 Ağu 2026 — MESAJ YALAN SÖYLÜYORDU. Kapı "beklenen gün"ü 18:45'ten önce
        # DÜN sayar; depoda ise bugünün (yarım) barı vardır. Tam eşitlik arandığı için
        # oran %0 çıkıyor ve ekran "depo bayat" diyordu — oysa depo geride değil İLERİDE.
        # Sınıflandırma ve çıkış kodu DEĞİŞMEDİ: seans içinde tarama yine yazmaz
        # (seans_profili.py bu korumaya dayanıyor). Değişen yalnız gerekçe metni.
        _esit, _ileri, _toplam = _sayim(hedef)
        if _toplam and (_ileri / _toplam) >= ESIK:
            sonuc = (False, "depo bayat DEĞİL — bugünün yarım barında; kapanmış seans %s "
                            "için tarama yazılmaz (kapanıştan sonra tekrar deneyin)" % hedef)
        else:
            sonuc = (False, "beklenen seans %s — hisselerin yalnız %%%.0f'inde bu günün barı var"
                     % (hedef, oran * 100))
    if anahtar:
        _IZIN_CACHE[anahtar] = sonuc
    return sonuc


def main() -> int:
    sessiz = "--quiet" in sys.argv[1:]
    hedef, guncel, toplam = depo_durumu()
    oran = (guncel / toplam) if toplam else 0.0
    bayat = oran < ESIK
    if not sessiz:
        # Windows konsolu cp1254: cikti ASCII kalir, ok/nokta gibi isaretler yok.
        _esit, _ileri, _toplam = _sayim(hedef)
        _not = ""
        if bayat and _toplam and (_ileri / _toplam) >= ESIK:
            _not = " (geride DEGIL, ILERIDE: bugunun yarim bari var)"
        print("beklenen gun: %s | %d/%d hissede bar (%%%.1f) = %s%s"
              % (hedef, guncel, toplam, oran * 100, "BAYAT" if bayat else "TAZE", _not))
    return 0 if bayat else 1


if __name__ == "__main__":
    raise SystemExit(main())
