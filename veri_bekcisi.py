# ============================================================================
# veri_bekcisi.py — TEK KAPI VERİ DOĞRULAMA (13 Tem 2026)
# ============================================================================
# Amaç: veri katmanından panellere giden HER günlük OHLCV df'i tek kapıdan
# geçirmek. Bozuk veri ekrana ÇIKAMAZ — ya taze çekimle düzelir ya da hiç
# servis edilmez (panel "veri yok" der, yanlış grafik ASLA çizilmez).
#
# Tarihçe (bu kapının geriye dönük yakalayacağı gerçek vakalar):
#   - 6 Tem 2026 TUPRS paneli + 8 Tem XU100 "11.54" — Yahoo anlık bozuk seri
#     cache'e dondu, fiyat kartı zehirlendi (o gün sigorta SADECE
#     fetch_stock_info'ya takıldı; grafik panelleri korumasız kaldı).
#   - 13 Tem 2026 EREGL — başlık 40.86 doğru, Para Akış grafiği ~9.5-11
#     (yanlış ölçekli seri bellek deposunda). Bu dosyanın doğum sebebi.
#   - 5 Haz 2026 İsyatirim doji bug — 5 mumun 4'ü Open==Close.
#   - 6 Tem 2026 "Frankenstein bar" — ham Open + düzeltilmiş HLC → Open>High.
#
# Kural: bu modül data_layer'ı IMPORT ETMEZ (döngüsel import). Referans
# fiyatlar ve parquet yolu parametre olarak data_layer'dan gelir.
# ============================================================================
import os
import datetime as _dt
from functools import lru_cache

import numpy as np
import pandas as pd

# ── Eşikler ─────────────────────────────────────────────────────────────────
TOL_REF_ORAN   = 1.25  # servis edilen son kapanış TÜM referanslardan 1.25x+
                       # ayrışırsa bozuk. BIST günlük tavan/taban ±%10 →
                       # 1.25 yanlış alarm vermez; gerçek vakalar 2x-4x ölçekli.
OHLC_SON_N     = 30    # aralık-dışı O/C kontrolü son N bar
OHLC_MAX_IHLAL = 2     # son 30 barda 2'den fazla Open/Close, High-Low
                       # aralığının dışındaysa → Frankenstein bar
DOJI_SON_N     = 5
DOJI_MIN_SAYI  = 4     # son 5 barın 4+ tanesi birebir Open==Close ise şüpheli
DOJI_MIN_FIYAT = 5.0   # ucuz hissede (tick kaba) doğal doji olabilir → muaf
ZIPLAMA_ORAN   = 2.0   # tek günde 2x+ hareket = düzeltilmemiş bölünme
                       # (BIST'te günlük limit ±%10 → doğal yolla imkânsız)
BAYAT_GUN      = 12    # son bar 12+ takvim günü eskiyse bayat
                       # (uzun bayram tatili ~9-10 gün → 12 güvenli)
MUM_TOLERANS   = 0.005 # servis edilen mumun Open/Close'u diskteki dosyadan
                       # %0.5+ ayrışırsa MUM AYRIŞMASI (renk/dizilim bozulur)

# Son sorunlar kaydı — UI kırmızı şerit basmak için okur.
# {ticker: (datetime, [sorun metinleri])}
SORUNLAR = {}

_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "logs", "veri_bekcisi.log")


def dogrula(df, ticker, ref_fiyatlar=None, ref_mumlar=None):
    """Günlük OHLCV df'i 6 kontrolden geçirir.

    ref_fiyatlar: {'disk': float, 'canli': float} — 0/None olanlar atlanır.
    ref_mumlar:   son_mumlar_disk() çıktısı — diskteki son 5 mumun
                  (tarih, open, close) üçlüleri; None ise kontrol atlanır.
    Döner: (ok: bool, sorunlar: list[str])
    Bekçi kendi hatasında AKIŞI KESMEZ → (True, []) döner.
    """
    try:
        if df is None or len(df) == 0:
            return True, []  # boş df'in bekçisi olmaz — panel zaten "veri yok" der
        gerekli = {'Open', 'High', 'Low', 'Close'}
        if not gerekli.issubset(set(df.columns)):
            return True, []  # OHLC olmayan özel df'lere karışma

        sorunlar = []
        son = df.tail(OHLC_SON_N)
        o = son['Open'].astype(float);  h = son['High'].astype(float)
        l = son['Low'].astype(float);   c = son['Close'].astype(float)
        son_kapanis = float(df['Close'].dropna().iloc[-1])

        # 1) REFERANS AYRIŞMASI — grafiğin fiyatı gerçek dünyayla uyuşuyor mu?
        #    Sadece TÜM referanslardan aynı anda ayrışıyorsa bozuk sayılır
        #    (disk ile uyumluysa canlı fiyat glitch'i bizi yanıltmasın).
        if ref_fiyatlar and son_kapanis > 0:
            refler = {k: float(v) for k, v in ref_fiyatlar.items()
                      if v is not None and float(v) > 0}
            if refler:
                ayrisan = {}
                for ad, ref in refler.items():
                    oran = max(son_kapanis, ref) / min(son_kapanis, ref)
                    if oran > TOL_REF_ORAN:
                        ayrisan[ad] = f"{ad}={ref:.2f} ({oran:.2f}x)"
                if len(ayrisan) == len(refler):
                    sorunlar.append(
                        f"REFERANS AYRIŞMASI: seri kapanışı {son_kapanis:.2f}, "
                        f"gerçek: {', '.join(ayrisan.values())}")

        # 2) FRANKENSTEIN BAR — Open/Close, High-Low aralığının dışında
        eps = (h - l).abs().median() * 1e-6 + 1e-9
        ihlal = int(((o > h + eps) | (o < l - eps) |
                     (c > h + eps) | (c < l - eps)).sum())
        if ihlal > OHLC_MAX_IHLAL:
            sorunlar.append(
                f"FRANKENSTEIN BAR: son {len(son)} barın {ihlal} tanesinde "
                f"Open/Close gün aralığının dışında")

        # 3) DOJİ SALGINI — son 5 barın 4+ tanesinde Open==Close AMA gün içi
        #    aralık VAR (H>L). İsyatirim bug'ının parmak izi budur: açılış
        #    kapanışla doldurulmuş, High/Low sağlam. O=H=L=C tek-fiyat barları
        #    MEŞRU (tavan/taban kilidi, 1-lot hisseler: DUNYH/ISKUR vakası) — sayılmaz.
        if son_kapanis > DOJI_MIN_FIYAT and len(df) >= DOJI_SON_N:
            s5 = df.tail(DOJI_SON_N)
            _o5 = s5['Open'].astype(float); _c5 = s5['Close'].astype(float)
            _h5 = s5['High'].astype(float); _l5 = s5['Low'].astype(float)
            doji = int(((_o5 == _c5) & (_h5 > _l5)).sum())
            if doji >= DOJI_MIN_SAYI:
                sorunlar.append(
                    f"DOJİ SALGINI: son {DOJI_SON_N} barın {doji} tanesinde "
                    f"Open==Close ama gün içi aralık var (veri bug'ı)")

        # 4) BÖLÜNME ZIPLAMASI — tek günde 2x+ (kripto/emtia muaf:
        #    kontrat rulosu / aşırı oynaklık doğal olabilir)
        if "-USD" not in ticker and "=F" not in ticker:
            ck = son['Close'].astype(float).replace(0, np.nan).dropna()
            if len(ck) >= 2:
                oranlar = (ck / ck.shift(1)).dropna()
                zipla = oranlar[(oranlar > ZIPLAMA_ORAN)
                                | (oranlar < 1.0 / ZIPLAMA_ORAN)]
                if len(zipla) > 0:
                    sorunlar.append(
                        f"BÖLÜNME ZIPLAMASI: {len(zipla)} günde fiyat tek "
                        f"seansta 2x+ hareket etmiş (düzeltilmemiş bölünme?)")

        # 6) MUM AYRIŞMASI — servis edilen son mumlar diskteki dosyayla aynı mı?
        #    (13 Tem 2026 akşam vakası: bellekteki kopya diskle ayrıştı, son
        #    KAPANIŞ doğru kaldı ama AÇILIŞLAR kaydı → mum renkleri/dizilimi
        #    yanlış çizildi. Renk = Open-Close farkı; sadece kapanışa bakan
        #    kontrol #1 bunu göremez.)
        if ref_mumlar:
            try:
                _disk = {t: (float(o_), float(c_)) for t, o_, c_ in ref_mumlar}
                _ayrik = []
                for _ts_raw, _row in df.tail(7).iterrows():
                    _ts = pd.Timestamp(_ts_raw)
                    if _ts.tz is not None:
                        _ts = _ts.tz_convert(None)
                    _key = _ts.normalize().date().isoformat()
                    if _key not in _disk:
                        continue
                    _do, _dc = _disk[_key]
                    for _ad, _dv, _sv in (('Open',  _do, float(_row['Open'])),
                                          ('Close', _dc, float(_row['Close']))):
                        if _dv > 0 and abs(_sv - _dv) / _dv > MUM_TOLERANS:
                            _ayrik.append(
                                f"{_key} {_ad}: disk={_dv:.2f} servis={_sv:.2f}")
                if _ayrik:
                    sorunlar.append("MUM AYRIŞMASI (bellek!=disk): "
                                    + "; ".join(_ayrik[:3]))
            except Exception:
                pass

        # 5) BAYAT VERİ — son bar çok eski
        try:
            son_tarih = pd.Timestamp(df.index[-1])
            if son_tarih.tz is not None:
                son_tarih = son_tarih.tz_convert(None)
            yas = (pd.Timestamp.today().normalize()
                   - son_tarih.normalize()).days
            if yas > BAYAT_GUN:
                sorunlar.append(
                    f"BAYAT VERİ: son bar {yas} gün eski ({son_tarih.date()})")
        except Exception:
            pass

        return (len(sorunlar) == 0), sorunlar
    except Exception:
        return True, []  # bekçi çökerse veri akışını kesme


@lru_cache(maxsize=4096)
def _disk_kapanis_cached(path, mtime):
    """Parquet'ten SADECE son kapanışı okur. mtime anahtarda → dosya
    değişince cache kendiliğinden tazelenir; render başına tek disk okuması."""
    try:
        c = pd.read_parquet(path, columns=['Close'])['Close'].dropna()
        return float(c.iloc[-1]) if len(c) else 0.0
    except Exception:
        return 0.0


def disk_kapanis(path):
    """Diskteki parquet'in son kapanışı (tek-kaynak fiyat kuralı)."""
    try:
        if path and os.path.exists(path):
            return _disk_kapanis_cached(path, os.path.getmtime(path))
    except Exception:
        pass
    return 0.0


@lru_cache(maxsize=4096)
def _son_mumlar_cached(path, mtime):
    """Parquet'ten son 5 mumun (tarih, Open, Close) üçlülerini okur.
    mtime anahtarda → dosya yeniden yazılınca kendiliğinden tazelenir."""
    try:
        d = pd.read_parquet(path, columns=['Open', 'Close']).dropna().tail(5)
        out = []
        for ts, row in d.iterrows():
            t = pd.Timestamp(ts)
            if t.tz is not None:
                t = t.tz_convert(None)
            out.append((t.normalize().date().isoformat(),
                        float(row['Open']), float(row['Close'])))
        return tuple(out)
    except Exception:
        return None


def son_mumlar_disk(path):
    """Diskteki son 5 mum — MUM AYRIŞMASI kontrolünün referansı."""
    try:
        if path and os.path.exists(path):
            return _son_mumlar_cached(path, os.path.getmtime(path))
    except Exception:
        pass
    return None


def kaydet(ticker, sorunlar):
    """Bloklanan ticker'ı kayda geçir: UI şeridi + kalıcı log."""
    simdi = _dt.datetime.now()
    SORUNLAR[ticker] = (simdi, list(sorunlar))
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            for s in sorunlar:
                f.write(f"{simdi:%Y-%m-%d %H:%M:%S} | {ticker} | {s}\n")
    except Exception:
        pass


def temizle(ticker):
    """Ticker sağlıklı döndüyse eski sorun kaydını sil."""
    SORUNLAR.pop(ticker, None)


def son_blok_yeni_mi(ticker, saniye=600):
    """Bu ticker son N saniye içinde bloklandı mı? (SOĞUMA: kalıcı bozuk
    ticker her görüşte Yahoo'ya taze çekim tetiklemesin — 10dk'da 1 dener)"""
    try:
        kayit = SORUNLAR.get(ticker)
        if not kayit:
            return False
        return (_dt.datetime.now() - kayit[0]).total_seconds() < saniye
    except Exception:
        return False
