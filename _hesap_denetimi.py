# -*- coding: utf-8 -*-
"""
_hesap_denetimi.py — SESSİZ HESAP HATASI AVCISI
================================================
25 Ağu 2026'da pusula barlarında bulunan hata tiplerini TÜM projede arar.
Hiçbir şeyi düzeltmez; sadece rapor eder.

Aradığı 3 tip:
  1. TANIMSIZ DEĞİŞKEN — fonksiyonda okunuyor ama hiç atanmamış. try/except
     içindeyse sessizce yutulur, kullanıcı varsayılan/eski değeri görür.
     (Bugünkü vaka: _h_story_title başka fonksiyonda kullanılıyordu.)
  2. SABİT GÖSTERGE KONUMU — sürekli bir ölçüyü kategoriye indirip barı sabit
     yüzdeye koymak. (Bugünkü vaka: CMF 0,11 iken bar %75.)
  3. ÖLÜ ATAMA — değişkene değer yazılıyor ama o fonksiyonda hiç okunmuyor.
     Genelde silinmiş bir bloğun kalıntısı; hesap boşa yapılıyor.

Kullanım:
    python _hesap_denetimi.py                # app.py + tüm *_core.py
    python _hesap_denetimi.py app.py         # tek dosya
"""
from __future__ import annotations

import ast
import builtins
import glob
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
BUILTIN = set(dir(builtins)) | {"__name__", "__file__", "__doc__", "_"}

# Bar/gauge konumu olduğu belli olan değişken adları
KONUM_ADI = ("_pos", "pos_", "gauge", "_p =", "percent", "yuzde")

# ── BİLİNEN YANLIŞ ALARMLAR (25 Ağu 2026) ───────────────────────────────
# Bunlar incelendi ve GÜVENLİ bulundu. Rapora "bilinen" etiketiyle düşer;
# DÜZELTMEYE ÇALIŞMA. Yeni bir yanlış alarm doğrularsan buraya ekle ki
# sonraki ajan aynı yanılgıya düşmesin.
BEYAZ_LISTE_TANIMSIZ = {
    # `_, x = _bist_day_status() if '_bist_day_status' in globals() else (...)`
    # Kod kendini globals() ile koruyor — çağrı ancak tanımlıysa yapılıyor.
    ("smr_core.py", "_bist_day_status"),
}
BEYAZ_LISTE_SABIT = {
    # `_pos_main = 85 if _h_main_up else (15 if ...)` → bunlar YEDEK satır:
    # 50 günlük ortalama hesaplanamazsa devreye giren fallback. Asıl yol
    # fiyatın ortalamaya uzaklığından gerçek konumu üretiyor. SİLME.
    ("app.py", "_pos_main"),
}


def modul_isimleri(tree: ast.Module) -> set:
    """import + top-level atama + def/class adları."""
    ad = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                ad.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ad.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for n in ast.walk(node):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    ad.add(n.id)
        elif isinstance(node, (ast.If, ast.Try, ast.For, ast.While, ast.With)):
            for n in ast.walk(node):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    ad.add(n.id)
                elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    ad.add(n.name)
                elif isinstance(n, (ast.Import, ast.ImportFrom)):
                    for a in n.names:
                        ad.add((a.asname or a.name).split(".")[0])
    return ad


def arg_adlari(fn) -> set:
    a = fn.args
    ad = {x.arg for x in (a.args + a.posonlyargs + a.kwonlyargs)}
    if a.vararg:
        ad.add(a.vararg.arg)
    if a.kwarg:
        ad.add(a.kwarg.arg)
    return ad


def fonksiyonlar(tree, dis_kapsam=None, yol=""):
    """Her fonksiyon için (isim, node, görünür_isimler) üretir — closure dahil."""
    dis_kapsam = dis_kapsam or set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kendi = set()
            for n in ast.walk(node):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    kendi.add(n.id)
                elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    kendi.add(n.name)
                elif isinstance(n, ast.ExceptHandler) and n.name:
                    kendi.add(n.name)
                elif isinstance(n, (ast.Import, ast.ImportFrom)):
                    for a in n.names:
                        kendi.add((a.asname or a.name).split(".")[0])
                elif isinstance(n, ast.arg):
                    kendi.add(n.arg)
            gorunur = dis_kapsam | kendi | arg_adlari(node)
            tam = f"{yol}{node.name}"
            yield tam, node, gorunur
            yield from fonksiyonlar(node, gorunur, yol=f"{tam}.")
        else:
            yield from fonksiyonlar(node, dis_kapsam, yol=yol)


def denetle(dosya: str):
    src = open(dosya, encoding="utf-8").read()
    tree = ast.parse(src)
    satirlar = src.splitlines()
    mod = modul_isimleri(tree) | BUILTIN

    tanimsiz, olu = [], []
    for ad, node, gorunur in fonksiyonlar(tree, mod):
        # sadece bu fonksiyonun KENDİ gövdesindeki okumalar (iç fonksiyonlar hariç)
        ic_fn = {n for ch in ast.iter_child_nodes(node)
                 for n in ast.walk(ch)
                 if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef))}
        okunan, yazilan = {}, {}
        for n in ast.walk(node):
            if n in ic_fn or not isinstance(n, ast.Name):
                continue
            if isinstance(n.ctx, ast.Load):
                okunan.setdefault(n.id, n.lineno)
            elif isinstance(n.ctx, ast.Store):
                yazilan.setdefault(n.id, n.lineno)

        dosya_adi = os.path.basename(dosya)
        for isim, ln in okunan.items():
            if isim not in gorunur and not isim.startswith("__"):
                if (dosya_adi, isim) in BEYAZ_LISTE_TANIMSIZ:
                    continue          # bilinen yanlış alarm — incelendi, güvenli
                tanimsiz.append((ln, ad, isim))
        for isim, ln in yazilan.items():
            if isim.startswith("_") and isim not in okunan and len(isim) > 3:
                olu.append((ln, ad, isim))

    # SABİT KONUM: bar/gauge konumu literal sayıya eşitleniyor mu
    sabit = []
    for i, s in enumerate(satirlar, 1):
        t = s.strip()
        if not any(k in t for k in KONUM_ADI):
            continue
        # "_pos_x = 85 if ..." / 'return ..., "75%"' kalıpları
        if ("= 85 if" in t or "= 75 if" in t or "= 25 if" in t
                or '"75%"' in t or '"85%"' in t or '"25%"' in t):
            _dosya = os.path.basename(dosya)
            if any(_dosya == d and v in t for d, v in BEYAZ_LISTE_SABIT):
                continue              # bilinen yedek (fallback) satır — SİLME
            sabit.append((i, t[:96]))

    return tanimsiz, olu, sabit


def main():
    hedefler = sys.argv[1:] or (
        [os.path.join(BASE, "app.py")]
        + sorted(glob.glob(os.path.join(BASE, "*_core.py")))
        + [os.path.join(BASE, f) for f in ("indicators.py", "scan_pipeline.py",
                                           "scanners.py", "analysis_core.py")]
    )
    hedefler = [h for h in dict.fromkeys(hedefler) if os.path.exists(h)]

    print("HESAP DENETİMİ — sessiz hata avcısı")
    print("=" * 88)
    toplam = [0, 0, 0]
    for h in hedefler:
        tanimsiz, olu, sabit = denetle(h)
        ad = os.path.basename(h)
        if not (tanimsiz or olu or sabit):
            print(f"\n✅ {ad}: temiz")
            continue
        print(f"\n📄 {ad}")
        if tanimsiz:
            print(f"   🔴 TANIMSIZ DEĞİŞKEN ({len(tanimsiz)}) — okunuyor ama hiç atanmamış:")
            for ln, fn, isim in sorted(tanimsiz)[:12]:
                print(f"      satır {ln:>6}  {fn}()  →  {isim}")
            if len(tanimsiz) > 12:
                print(f"      ... +{len(tanimsiz)-12} tane daha")
        if sabit:
            print(f"   🟡 SABİT GÖSTERGE KONUMU ({len(sabit)}) — gerçek değer yerine kategori:")
            for ln, t in sabit[:8]:
                print(f"      satır {ln:>6}  {t}")
            if len(sabit) > 8:
                print(f"      ... +{len(sabit)-8} tane daha")
        if olu:
            print(f"   ⚪ ÖLÜ ATAMA ({len(olu)}) — hesaplanıyor ama okunmuyor:")
            for ln, fn, isim in sorted(olu)[:8]:
                print(f"      satır {ln:>6}  {fn}()  →  {isim}")
            if len(olu) > 8:
                print(f"      ... +{len(olu)-8} tane daha")
        toplam[0] += len(tanimsiz); toplam[1] += len(sabit); toplam[2] += len(olu)

    print("\n" + "=" * 88)
    print(f"TOPLAM: {toplam[0]} tanımsız · {toplam[1]} sabit konum · {toplam[2]} ölü atama")
    print("""
NOT: Tanımsız değişkenler EN CİDDİ olanlar — try içindeyse sessizce yutulur,
kullanıcı eski/varsayılan değeri görür. Ölü atamalar zararsız ama silinmiş bir
bloğun kalıntısı olabilir. Sabit konumlar elle bakılmalı: bazıları meşru
(gerçekten ikili durum), bazıları ölçüyü gizliyor.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
