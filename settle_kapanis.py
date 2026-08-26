#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lokal Yahoo kapanışını VPS'e gönderilecek BIST düzeltme adayına çevirir.

Bu program ana parquet'lere yazmaz. Yalnız aktif sürümle bağlanmış bir aday ZIP
üretir; VPS paketi kendi güncel sürümüyle karşılaştırıp doğrular ve kabul ederse
yeni sürüm yayınlar.
"""
import glob
import os
import sys
import time
import warnings
from pathlib import Path

import pandas as pd
import yfinance as yf

from bist_data_store import active_version_id, read_active
from bist_exchange import create_candidate_package

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
VER = ROOT / "veriler"


# ── borsapy KONSENSÜS KAPISI (26 Ağu 2026) ───────────────────────────────────
# Ölçüldü (settle_report, 21 gün): borsapy ile İş Yatırım'ın NİHAİ kapanışı
# 73/73 (%100) aynı; borsapy 71 kez ERKEN oturuyor, 2 kez aynı anda, HİÇ geç
# kalmıyor. Yani borsapy "bu fiyat oturdu mu?" sorusuna Yahoo'dan daha erken
# ve şimdiye dek hatasız cevap veriyor.
#
# Neden gerek: bu program Yahoo'nun son barını "oturmuş" sayıp depodaki satırı
# ezmeye aday gösteriyordu. Oysa kapanış medyan 18:36'da, %95 gün 19:49'da, en
# kötü gün 20:58'de oturuyor — yani tur erken koşarsa PROVİZYON fiyat "settled"
# diye paketlenip depoyu eziyor. Saat seçimi bunun ~%95'ini kapatıyor; kalan
# payı kapatan şey saat değil, İKİNCİ BİR KAYNAĞIN TEYİDİ.
#
# Kural: yalnız DEĞİŞECEK satırlar (tipik ~40 hisse) borsapy'ye sorulur.
#   borsapy aynı günü aynı fiyatla doğruluyorsa  → aday kalır (konsensüs)
#   borsapy farklı söylüyorsa                    → aday DÜŞER (biri henüz oturmamış;
#                                                  bir sonraki tur veya sabah
#                                                  incremental'ı düzeltir)
#   borsapy veri vermiyorsa/hata                 → eski davranış (Yahoo'ya güven)
# Kapatma: SMR_SETTLE_KONSENSUS=0
KONSENSUS_ACIK = os.environ.get("SMR_SETTLE_KONSENSUS", "1") != "0"
KONSENSUS_TOLERANS = 0.0015        # %0,15 — ölçümde birebir uyuyorlar, bu pay yuvarlama için
KONSENSUS_SURE_BUTCESI = 120.0     # sn; aşılırsa kalan adaylar eski davranışla geçer


def _borsapy_kapanis(sym, gun, _deneme=2):
    """Verilen günün borsapy kapanışı (yoksa None). Sembol 'XXXX.IS' formatında.

    borsapy TradingView WebSocket'i kullanıyor ve ARADA BİR boş dönüyor (test:
    TUPRS bir turda boş, hemen ardından 3/3 doğru). Tek tekrar bunu yakalar;
    yakalamazsa çağıran taraf eski davranışa (Yahoo'ya güven) düşer."""
    for _k in range(_deneme):
        _sonuc = _borsapy_kapanis_tek(sym, gun)
        if _sonuc is not None:
            return _sonuc
        if _k + 1 < _deneme:
            time.sleep(0.6)
    return None


def _borsapy_kapanis_tek(sym, gun):
    try:
        from provider_traffic import acquire_slot, record_success, record_failure
        acquire_slot("borsapy", priority="post_close", max_wait=30)
        import borsapy as _bp
        h = _bp.Ticker(sym.replace(".IS", "")).history(period="3d")
        if h is None or h.empty or "Close" not in h.columns:
            record_failure("borsapy", kind="empty", error=f"{sym} empty")
            return None
        record_success("borsapy")
        idx = pd.DatetimeIndex([pd.Timestamp(t).tz_localize(None).normalize()
                                if pd.Timestamp(t).tz else pd.Timestamp(t).normalize()
                                for t in h.index])
        h = h.set_axis(idx)
        if gun not in h.index:
            return None
        return float(h.at[gun, "Close"])
    except Exception:
        return None


def _konsensus_sug(candidates):
    """Adayları borsapy ile teyit eder. (kalan_adaylar, teyitli, dusen, sorulmayan)."""
    if not KONSENSUS_ACIK or not candidates:
        return candidates, 0, 0, len(candidates)
    baslangic = time.time()
    kalan, teyitli, dusen, sorulmayan = {}, 0, 0, 0
    for sym, pay in candidates.items():
        if time.time() - baslangic > KONSENSUS_SURE_BUTCESI:
            kalan[sym] = pay; sorulmayan += 1          # bütçe bitti → eski davranış
            continue
        gun = pay["price_df"].index[-1]
        yahoo_close = float(pay["price_df"]["Close"].iloc[-1])
        bp_close = _borsapy_kapanis(sym, gun)
        if bp_close is None:
            kalan[sym] = pay; sorulmayan += 1          # kaynak yok → eski davranış
            continue
        pay_sinir = max(0.005, abs(bp_close) * KONSENSUS_TOLERANS)
        if abs(bp_close - yahoo_close) <= pay_sinir:
            pay["price_source"] = "yahoo_borsapy_konsensus"
            kalan[sym] = pay; teyitli += 1
        else:
            dusen += 1                                  # biri henüz oturmamış → paketleme
    return kalan, teyitli, dusen, sorulmayan


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def main() -> int:
    syms = sorted(Path(f).name.replace("_1d.parquet", "")
                  for f in glob.glob(str(VER / "*.IS_1d.parquet")))
    candidates = {}
    checked = 0
    for grp in chunks(syms, 100):
        try:
            from provider_traffic import acquire_slot, record_success, record_failure
            acquire_slot("yahoo", priority="post_close", max_wait=60.0)
            data = yf.download(grp, period="3d", interval="1d", progress=False,
                               auto_adjust=False, group_by="ticker", threads=True,
                               timeout=20)
            if data is None or data.empty:
                record_failure("yahoo", kind="empty", error="empty_batch")
                continue
            record_success("yahoo")
        except Exception:
            try:
                record_failure("yahoo", kind="error", error="batch_exception")
            except Exception:
                pass
            continue
        for sym in grp:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    if sym not in data.columns.get_level_values(0):
                        continue
                    yd = data[sym].dropna(subset=["Close"])
                elif len(grp) == 1 and sym == grp[0]:
                    yd = data.dropna(subset=["Close"])
                else:
                    continue
                if yd.empty:
                    continue
                ld = pd.Timestamp(yd.index[-1])
                ld = (ld.tz_localize(None) if ld.tz else ld).normalize()
                old = read_active(sym)
                checked += 1
                if old is None or old.empty or ld not in old.index:
                    continue
                row = yd.loc[[yd.index[-1]], ["Open", "High", "Low", "Close"]].copy()
                row.index = pd.DatetimeIndex([ld])
                changed = any(
                    abs(float(old.at[ld, col]) - float(row[col].iloc[-1])) > 0.0001
                    for col in ("Open", "High", "Low", "Close")
                    if col in old.columns and pd.notna(row[col].iloc[-1]))
                if changed:
                    candidates[sym] = {"price_df": row,
                                       "price_source": "yahoo_settled"}
            except Exception:
                continue
        time.sleep(0.4)

    if not candidates:
        print(f"settle_kapanis: {checked} kontrol edildi, aday değişiklik yok.")
        return 0

    ham = len(candidates)
    candidates, teyitli, dusen, sorulmayan = _konsensus_sug(candidates)
    if KONSENSUS_ACIK:
        print(f"konsensus: {ham} aday -> borsapy teyitli {teyitli} · "
              f"celiskili {dusen} (paketlenmedi) · sorulmayan {sorulmayan}")
    if not candidates:
        print(f"settle_kapanis: {checked} kontrol edildi, "
              f"{ham} adayin hepsi konsensus kapisinda kaldi.")
        return 0
    package = create_candidate_package(
        candidates, source="local_yahoo_settled",
        reason="post_close_settlement", parent_version=active_version_id())
    # Shell yalnız bu tek satırdan paket yolunu alır.
    print(f"CANDIDATE_PACKAGE={package}")
    print(f"settle_kapanis: {checked} kontrol edildi, {len(candidates)} aday paketlendi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
