"""LİKİDİTE SIRALAMASI — "tarama 30 isim çıkardı, hangisine bakayım?"

SAF HESAP. Streamlit/app import etmez, ekran render etmez, hiçbir şey yazmaz.
Çağıran (app.py / smr_core.py) yalnız import edip sonucu gösterir.

── NEDEN (28 Ağu 2026 ölçümü) ────────────────────────────────────────────────
Bir taramanın AYNI GÜN çıkardığı listenin içinde, isimleri 20 günlük ortalama
TL hacme göre sıralayıp en likit olanları almak, listenin tamamını almaktan iyi:

  · 49 taramanın 37'sinde fark POZİTİF (rastgele ~24 beklenirdi), p=0,0005
  · Kalabalık listelerde (>=15 isim, 766 tarama-günü) seçim başına:
        tüm liste  -0,79 | en likit 10  -0,33 | üst 1/3  -0,21
        en likit 5 +0,08 | en likit 4   +0,19 | en likit 3 +0,39
    Yalnız ilk 3-5 mutlak ARTIYA geçiyor (evren tabanı -0,17).
  · İleriye doğru sınavda (ilk 15 gün eğitim, 27 gün sınav) iki rejimde de tuttu:
        düşen +1,13 (p=0,024) · yükselen +0,61 (p=0,041)
  · Aynı sınavda "güç" (52 hafta konumu) ERİDİ: yükselende +0,07, p=1,000.
    Uyarlanan "her gün en iyi göstergeyi seç" mekanizması sabit kuraldan KÖTÜ çıktı.

Kayıt: memory/project_gun_ici_siralama.md · logs/likidite_genel.csv

⚠ SINIRLAR
  · Tek dönem (May-Ağu 2026). İkinci dönem verisi YOK. Rejim değişince yeniden ölç.
  · `prelaunch_bos`ta kural TERS çalışıyor (-1,89, p=0,02) — DIŞARIDA (bkz. HARIC).
  · Bu bir sıralama yardımıdır; getiri vaadi, alım sinyali veya bağımsız oy DEĞİLDİR.
    Ekranda "en iyi N" değil "EN LİKİT N" yazılmalı — ölçtüğümüz şey budur.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

VARSAYILAN_ADET = 4          # kullanıcı kararı 28 Ağu 2026; ölçüldü: +0,19 mutlak
PENCERE_GUN = 20             # 20 günlük ortalama TL hacim
MIN_GUN = 5                  # bundan az veriyle likidite hesaplanmaz

# Ölçümde kuralın TERS çalıştığı taramalar — sıralama uygulanmaz.
HARIC = frozenset({"prelaunch_bos"})

# ENDEKSLER — tarama listelerine karışıyorlar (tekli_altin 27 Ağu: XU100 + XUSIN
# listedeydi). Hacimleri doğal olarak en yüksek olduğu için sıralamanın tepesine
# çıkıp "isim önerisi" gibi görünüyorlardı. Bu liste "hangi HİSSEYE bakayım"
# sorusuna cevap verir; endeks alınacak bir isim değildir → elenir.
# Fiyat kasasındaki X ile başlayan sembollerin TAMAMI (5) endekstir; gerçek BIST
# hissesi X ile başlamaz. Yeni endeks eklenirse buraya da eklenmeli.
ENDEKSLER = frozenset({"XU100", "XU030", "XBANK", "XUSIN", "XTUMY"})


def hisse_mi(sembol) -> bool:
    """Endeks değil, gerçek bir hisse mi?"""
    s = str(sembol or "").upper().replace(".IS", "")
    return bool(s) and s not in ENDEKSLER and not s.startswith("X")


def kural_gecerli(scan_type) -> bool:
    """Bu tarama için likidite sıralaması uygulanmalı mı?"""
    return str(scan_type or "") not in HARIC


def _seri(df) -> pd.Series | None:
    """OHLCV çerçevesinden 20 günlük ortalama TL hacim serisi."""
    if df is None or getattr(df, "empty", True):
        return None
    d = df.copy()
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d.loc[:, ~d.columns.duplicated()]
    d.columns = [str(c).capitalize() for c in d.columns]
    if not {"Close", "Volume"}.issubset(d.columns):
        return None
    idx = pd.to_datetime(d.index, errors="coerce")
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    d.index = idx.normalize()
    d = d[~d.index.isna()].sort_index()
    tl = pd.to_numeric(d["Close"], errors="coerce") * pd.to_numeric(d["Volume"], errors="coerce")
    return tl.rolling(PENCERE_GUN, min_periods=MIN_GUN).mean()


def likidite(sembol: str, tarih=None, okuyucu=None) -> float:
    """Tek hissenin `tarih` günü (dahil) itibarıyla 20g ortalama TL hacmi.
    Veri yoksa NaN döner — asla istisna atmaz. İleriye bakma yok: `tarih`ten
    sonraki barlar kullanılmaz."""
    try:
        if okuyucu is None:
            from bist_data_store import active_version_id, read_active
            okuyucu = lambda s: read_active(s, active_version_id())
        s = _seri(okuyucu(str(sembol).upper().replace(".IS", "")))
        if s is None or s.empty:
            return float("nan")
        if tarih is None:
            return float(s.iloc[-1])
        p = int(s.index.searchsorted(pd.Timestamp(tarih).normalize(), side="right")) - 1
        return float(s.iloc[p]) if p >= 0 else float("nan")
    except Exception:
        return float("nan")


def sirala(semboller, tarih=None, adet: int | None = None,
           scan_type=None, okuyucu=None) -> list[str]:
    """Listeyi likiditeye göre sırala, en likit `adet` ismi döndür.

    · `scan_type` HARIC listesindeyse liste OLDUĞU GİBİ döner (kural orada ters).
    · Likiditesi hesaplanamayan sembol sona atılır, ELENMEZ.
    · adet=None -> VARSAYILAN_ADET (4). adet=0 veya negatif -> tam sıralı liste.
    """
    isimler = [str(x).upper().replace(".IS", "") for x in (semboller or []) if str(x).strip()]
    isimler = [x for x in dict.fromkeys(isimler) if hisse_mi(x)]   # endeksler elenir
    if not isimler or not kural_gecerli(scan_type):
        return isimler
    n = VARSAYILAN_ADET if adet is None else int(adet)
    olcum = {s: likidite(s, tarih, okuyucu) for s in isimler}
    sirali = sorted(isimler,
                    key=lambda s: (np.isnan(olcum[s]), -(olcum[s] if not np.isnan(olcum[s]) else 0.0)))
    return sirali if n <= 0 else sirali[:n]


def etiket(adet: int | None = None) -> str:
    """Ekranda kullanılacak başlık. 'en iyi' DEĞİL — ölçtüğümüz şey likidite."""
    return f"EN LİKİT {VARSAYILAN_ADET if adet is None else int(adet)}"
