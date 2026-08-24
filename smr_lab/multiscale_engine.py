"""Çok ölçekli yapı motoru — ST-EP 5.1 mimarisinin izole yeniden inşası.

Bu dosya `docs/st_ep_spec.md` spesifikasyonunu uygular. Mevcut SMR kodundan tamamen
bağımsızdır: hiçbir SMR modülünü içeri almaz, hiçbir dosyaya/veritabanına yazmaz.
Girdisi tek bir OHLCV tablosu, çıktısı satır bazlı bir sonuç tablosudur.

KURAL 1 — Geleceğe bakış yok. `t` satırındaki her değer yalnızca `0..t` barlarından hesaplanır.
KURAL 2 — Satır döngüsü yok (tek istisna: histerezis durum makinesi, gerekçesi kendi
          fonksiyonunun açıklamasında). 800 hisselik taramayı kaldırması hedeflenir.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

# ── SABİTLER (spec §1–§5) ───────────────────────────────────────────────────
SCALES: tuple[int, ...] = (3, 7, 13, 19, 29, 47)   # spec §2.1 — asal pencereler
PRIMARY_SCALE = 19                                  # spec §3.2 — birincil verimlilik ölçeği
VOL_WINDOW = 20                                     # spec §1.4 — ortak volatilite cetveli
PCT_WINDOW = 252                                    # spec §3.3 — yüzdelik geriye bakış
PCT_MIN = 120                                       # spec §3.3 — asgari yüzdelik penceresi
ATR_WINDOW = 14
THETA_DEFAULT = 0.30                                # spec §2.3 — ölü bölge (# VARSAYIM: V-03)
POC_BIN_PCT = 0.0025                                # log-fiyat ızgara adımı (bkz. _price_bins)
POC_TOL_ATR = 0.5                                   # spec §4.3 — POC yığılma toleransı (V-12)
HYST_TAU0 = 0.20                                    # spec §5.3 (V-16)
HYST_LAMBDA = 0.60                                  # spec §5.3 (V-16)
HEALTH_STRONG = 70.0                                # spec §11.2d (V-20)
HEALTH_WEAK = 40.0                                  # spec §11.2d (V-20)

MIN_BARS_FULL = 301                                 # spec §8 — tam güç
MIN_BARS_MIN = 168                                  # spec §8 — asgari

_OHLC = ("Open", "High", "Low", "Close")


# ═══════════════════════════════════════════════════════════════════════════
# 0. GİRDİ DOĞRULAMA
# ═══════════════════════════════════════════════════════════════════════════
def validate_ohlcv(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Girdi tablosunun spec §1.2 sözleşmesine uyup uymadığını denetler ve temiz bir kopya döndürür.
    Sessiz düzeltme yapmaz: tekrar eden tarih hata fırlatır, hacimsiz seri bayrakla işaretlenir."""
    if df is None or len(df) == 0:
        raise ValueError("Boş DataFrame")
    missing = [c for c in _OHLC if c not in df.columns]
    if missing:
        raise ValueError(f"Eksik kolon(lar): {missing}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("Index DatetimeIndex olmalı")
    if df.index.duplicated().any():
        # Sessiz düzeltme YOK — tekrar eden tarih veri katmanı hatasıdır, gizlenmemeli (spec §7).
        raise ValueError("Index'te tekrar eden tarih var — veri katmanı düzeltmeli")
    if not df.index.is_monotonic_increasing:
        raise ValueError("Index artan sıralı değil")

    out = df.loc[:, [c for c in _OHLC if c in df.columns]].astype("float64").copy()
    if "Volume" in df.columns:
        out["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").astype("float64")
    else:
        out["Volume"] = np.nan

    v = out["Volume"]
    hacim_gecerli = bool((v.fillna(0) > 0).mean() >= 0.10)   # spec §7: seri %90+ boş ise hacim yok

    meta = {
        "hacim_gecerli": hacim_gecerli,
        "n_bar": int(len(out)),
        "tam_guc": bool(len(out) >= MIN_BARS_FULL),
        "asgari_saglandi": bool(len(out) >= MIN_BARS_MIN),
    }
    return out, meta


# ═══════════════════════════════════════════════════════════════════════════
# 1. ORTAK TEMELLER — volatilite cetveli, gerçek aralık, yüzdelik
# ═══════════════════════════════════════════════════════════════════════════
def true_range(df: pd.DataFrame) -> pd.Series:
    """Bir mumun gerçekten kat ettiği fiyat aralığını (gap dâhil) ölçer.
    Verimlilik paydası ve ATR bundan türer; kapanış-kapanış farkından her zaman büyük veya eşittir."""
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    return pd.concat([(h - l).abs(), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def yang_zhang_sigma(df: pd.DataFrame, window: int = VOL_WINDOW) -> pd.Series:
    """Yang-Zhang (2000) volatilite tahmincisi — tüm ölçeklerin paylaştığı ortak cetvel.
    Gecelik boşluğu ve sürüklenmeyi hesaba kattığı için BIST'in seans arası sıçramalarında sadece
    kapanış kullanan tahmincilerden daha az yanıltır."""
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    pc = c.shift(1)

    ln_o = np.log(o / pc)          # gecelik
    ln_c = np.log(c / o)           # açılış→kapanış
    rs = (np.log(h / c) * np.log(h / o)) + (np.log(l / c) * np.log(l / o))   # Rogers-Satchell

    n = int(window)
    k = 0.34 / (1.34 + (n + 1) / (n - 1))

    var_o = ln_o.rolling(n, min_periods=n).var(ddof=1)
    var_c = ln_c.rolling(n, min_periods=n).var(ddof=1)
    var_rs = rs.rolling(n, min_periods=n).mean()

    var_yz = var_o + k * var_c + (1.0 - k) * var_rs
    var_yz = var_yz.clip(lower=0.0)                       # spec §7: sayısal yuvarlama koruması
    sigma = np.sqrt(var_yz)
    return sigma.replace(0.0, np.nan)                     # sıfıra bölünme yok


def rolling_pct(s: pd.Series, window: int = PCT_WINDOW, min_periods: int = PCT_MIN) -> pd.Series:
    """Bir değerin kendi son N barlık geçmişindeki yüzdelik dilimini verir (sadece geçmiş, t dâhil).
    Sabit eşik yerine bunu kullanmak, farklı fiyat ve oynaklıktaki hisseleri aynı dille konuşturur."""
    return s.rolling(window, min_periods=min_periods).rank(pct=True)


# ═══════════════════════════════════════════════════════════════════════════
# 2. MODÜL A — TREND MOTORU
# ═══════════════════════════════════════════════════════════════════════════
def isotropic_slopes(close: pd.Series, sigma: pd.Series,
                     scales: tuple[int, ...] = SCALES) -> dict[int, pd.Series]:
    """Her ölçek için "bu hareket saf tesadüfe göre kaç standart sapma" sorusunun cevabını üretir.
    √n bölmesi sayesinde 3 barlık ve 47 barlık pencere aynı ölçekte karşılaştırılabilir hâle gelir."""
    out = {}
    for n in scales:
        drift = np.log(close / close.shift(n))
        out[n] = drift / (sigma * np.sqrt(n))
    return out


def trend_engine(close: pd.Series, sigma: pd.Series, theta: float = THETA_DEFAULT,
                 scales: tuple[int, ...] = SCALES) -> pd.DataFrame:
    """Altı ölçeğin oylarını toplayıp uyum skorunu, şiddeti ve 11 trend durumundan birini üretir.
    Durumlar birbirini dışlar; spec §2.6'daki öncelik merdiveni sırasıyla uygulanır."""
    z = isotropic_slopes(close, sigma, scales)
    idx = close.index

    votes = {n: np.sign(np.where(np.abs(z[n]) > theta, z[n], 0.0)) for n in scales}
    vote_mat = np.column_stack([votes[n] for n in scales])
    valid = np.column_stack([z[n].notna().to_numpy() for n in scales]).all(axis=1)

    score = np.where(valid, np.nansum(vote_mat, axis=1), np.nan)
    absz = np.column_stack([np.abs(z[n].to_numpy()) for n in scales])
    with warnings.catch_warnings():          # ısınma barlarında tüm dilim NaN — beklenen
        warnings.simplefilter('ignore', RuntimeWarning)
        strength = np.where(valid, np.nanmean(absz, axis=1), np.nan)

    s = pd.Series(score, index=idx)
    prev_min3 = s.shift(1).rolling(3, min_periods=1).min()
    prev_max3 = s.shift(1).rolling(3, min_periods=1).max()

    v_short = (vote_mat[:, 0] == 1) & (vote_mat[:, 1] == 1)     # oy_3, oy_7 pozitif
    v_short_dn = (vote_mat[:, 0] == -1) & (vote_mat[:, 1] == -1)
    v_wide_up = (vote_mat[:, 4] == 1) & (vote_mat[:, 5] == 1)   # oy_29, oy_47 pozitif
    v_wide_dn = (vote_mat[:, 4] == -1) & (vote_mat[:, 5] == -1)

    sc = s.to_numpy()
    conds = [
        sc >= 5,
        sc <= -5,
        (sc >= 3) & (prev_min3.to_numpy() <= -3),
        (sc <= -3) & (prev_max3.to_numpy() >= 3),
        v_short & v_wide_dn,
        v_short_dn & v_wide_up,
        (np.abs(sc) <= 1) & v_wide_up,
        (np.abs(sc) <= 1) & v_wide_dn,
        sc >= 3,
        sc <= -3,
    ]
    names = [
        "Güçlü Yükseliş", "Güçlü Düşüş",
        "Dönüş Onaylandı ▲", "Dönüş Onaylandı ▼",
        "Dönüş Oluşuyor ▲", "Dönüş Oluşuyor ▼",
        "Gizli Eğilim ▲", "Gizli Eğilim ▼",
        "Yükseliş Aktif", "Düşüş Aktif",
    ]
    conds = [np.where(np.isnan(sc), False, c) for c in conds]
    state = np.select(conds, names, default="Belirgin Trend Yok")
    state = np.where(np.isnan(sc), None, state)

    res = pd.DataFrame(index=idx)
    for n in scales:
        res[f"z_{n}"] = z[n]
        res[f"oy_{n}"] = pd.Series(votes[n], index=idx).where(z[n].notna())
    res["uyum_skoru"] = s
    res["siddet"] = pd.Series(strength, index=idx)
    res["kanaat_dilim"] = rolling_pct(res["siddet"])
    res["kanaat"] = pd.cut(res["kanaat_dilim"], [-0.01, 0.25, 0.75, 1.01],
                           labels=["DÜŞÜK", "ORTA", "YÜKSEK"]).astype(object)
    res["trend_durum"] = state
    res["trend_yon"] = np.sign(s).astype("float64")
    return res


# ═══════════════════════════════════════════════════════════════════════════
# 3. MODÜL B — VERİMLİLİK MOTORU
# ═══════════════════════════════════════════════════════════════════════════
def efficiency_engine(df: pd.DataFrame, tr: pd.Series, sigma: pd.Series,
                      trend_yon: pd.Series, uyum: pd.Series,
                      scales: tuple[int, ...] = SCALES) -> pd.DataFrame:
    """Fiyatın kat ettiği toplam yolun ne kadarını net yöne çevirdiğini ölçer (−1 … +1 arası).
    Aynı %10'u düz giderek kazanan hisse ile 20 kez inip çıkarak kazanan hisseyi ayırt eder."""
    c = df["Close"]
    idx = df.index
    res = pd.DataFrame(index=idx)

    for n in scales:
        net = c - c.shift(n)
        path = tr.rolling(n, min_periods=n).sum()
        res[f"ver_{n}"] = (net / path.replace(0.0, np.nan)).clip(-1.0, 1.0)

    ver = res[f"ver_{PRIMARY_SCALE}"]
    res["verim"] = ver
    res["verim_dilim"] = rolling_pct(ver.abs())
    res["verim_goreli"] = (ver.abs() / ver.abs().rolling(PCT_WINDOW, min_periods=PCT_MIN).mean()) * 100.0

    d = res["verim_dilim"]
    res["verim_etiket"] = np.select(
        [d.isna().to_numpy(), (d >= 0.95).to_numpy(), (d >= 0.75).to_numpy(), (d >= 0.25).to_numpy()],
        [None, "Aşırı", "Güçlü", "Normal"], default="Zayıf")

    # bileşik gövde/menzil oranı — yalnız rapor (spec §3.2 F1)
    win = PRIMARY_SCALE
    o_first = df["Open"].shift(win - 1)
    h_max = df["High"].rolling(win, min_periods=win).max()
    l_min = df["Low"].rolling(win, min_periods=win).min()
    res["bilesik_govde_orani"] = ((c - o_first) / (h_max - l_min).replace(0.0, np.nan)).clip(-1, 1)

    # ivme: kısa/uzun ortalama verim + volatilite yönü (spec §3.5)
    kisa = ver.abs().ewm(span=5, adjust=False).mean()
    uzun = ver.abs().ewm(span=20, adjust=False).mean()
    oran = kisa / uzun.replace(0.0, np.nan)
    vol_dusuyor = (sigma < sigma.shift(5)).to_numpy()
    res["verim_ivme"] = np.select(
        [oran.isna().to_numpy(),
         (oran >= 1.15).to_numpy() & ~vol_dusuyor,
         (oran >= 1.15).to_numpy() & vol_dusuyor,
         (oran <= 0.90).to_numpy()],
        [None, "Hızlanıyor", "Keskinleşiyor", "Zayıflıyor"], default="Sabit")

    # 19 durumluk trend × verim matrisi (spec §3.4)
    trendsiz = (uyum.abs() <= 2).to_numpy() | trend_yon.isna().to_numpy()
    yon_up = (trend_yon > 0).to_numpy() & ~trendsiz
    yon_dn = (trend_yon < 0).to_numpy() & ~trendsiz
    ayni = (np.sign(ver.to_numpy()) == trend_yon.to_numpy())
    tier = res["verim_etiket"].to_numpy()
    zayif = tier == "Zayıf"

    def _lbl(base):
        return np.select([tier == "Aşırı", tier == "Güçlü", tier == "Normal", tier == "Zayıf"],
                         [base[0], base[1], base[2], base[3]], default=None)

    up_ayni = _lbl(["Boğa Dalgası", "Teyitli Yükseliş", "Aktif Yükseliş", "Yükseliş Duraksıyor"])
    dn_ayni = _lbl(["Ayı Dalgası", "Teyitli Düşüş", "Aktif Düşüş", "Düşüş Duraksıyor"])
    up_karsi = _lbl(["Güçlü Karşı Hareket (boğaya)", "Satış Baskısı (boğada)", "Hafif Direnç (boğada)", "Gürültü"])
    dn_karsi = _lbl(["Güçlü Karşı Hareket (ayıya)", "Alış Baskısı (ayıda)", "Hafif Direnç (ayıda)", "Gürültü"])
    yonsuz = _lbl(["Aşırı Hareket (yönsüz)", "Güçlü Hareket (yönsüz)", "Normal Hareket", "Kararsız"])

    durum = np.select(
        [trendsiz,
         yon_up & ayni, yon_up & ~ayni,
         yon_dn & ayni, yon_dn & ~ayni],
        [yonsuz, up_ayni, up_karsi, dn_ayni, dn_karsi], default=None)
    durum = np.where(pd.isna(tier), None, durum)
    # "karşı yön + Zayıf" iki trendde de tek isim: Gürültü (spec §3.4, V-08)
    durum = np.where(zayif & ~trendsiz & ~ayni, "Gürültü", durum)
    res["trend_verim_durum"] = durum
    return res


# ═══════════════════════════════════════════════════════════════════════════
# 4. MODÜL C — HACİM ZEKÂSI
# ═══════════════════════════════════════════════════════════════════════════
def _price_bins(df: pd.DataFrame, bin_pct: float = POC_BIN_PCT):
    """Fiyatları, veriden BAĞIMSIZ sabit bir logaritmik ızgaraya oturtur (adım varsayılan binde 2.5).
    Izgaranın veriye bakmaması şart: pencereye göre değişen kenarlar geçmişi geriye dönük oynatırdı."""
    lo = np.log(df["Low"].to_numpy())
    hi = np.log(df["High"].to_numpy())
    base = int(np.floor(np.nanmin(lo) / bin_pct)) - 1
    top = int(np.ceil(np.nanmax(hi) / bin_pct)) + 1
    nb = top - base
    if nb > 6000:            # aşırı geniş fiyat aralığı → çözünürlüğü kabaca düşür
        bin_pct = bin_pct * (nb / 6000.0)
        base = int(np.floor(np.nanmin(lo) / bin_pct)) - 1
        top = int(np.ceil(np.nanmax(hi) / bin_pct)) + 1
        nb = top - base
    edges = (np.arange(base, top + 1, dtype="float64")) * bin_pct
    return edges, base, bin_pct, nb


def _poc_by_scale(df: pd.DataFrame, scales: tuple[int, ...] = SCALES,
                  bin_pct: float = POC_BIN_PCT) -> pd.DataFrame:
    """Her ölçek için hacmin en çok yığıldığı fiyatı (kontrol noktası / POC) vektörel olarak bulur.
    Her mumun hacmi, geçtiği fiyat dilimlerine oranla dağıtılır; sonra kümülatif toplamla pencere farkı alınır."""
    idx = df.index
    n_rows = len(df)
    out = pd.DataFrame(index=idx)

    v = df["Volume"].fillna(0.0).to_numpy()
    if not np.isfinite(v).any() or v.sum() <= 0:
        for n in scales:
            out[f"poc_{n}"] = np.nan
        return out

    edges, base, bin_pct, nb = _price_bins(df, bin_pct)
    lo = df["Low"].to_numpy()
    hi = df["High"].to_numpy()
    e_lo = np.exp(edges[:-1])
    e_hi = np.exp(edges[1:])

    top = np.minimum(hi[:, None], e_hi[None, :])
    bot = np.maximum(lo[:, None], e_lo[None, :])
    ov = np.clip(top - bot, 0.0, None)
    rng = (hi - lo)
    with np.errstate(divide="ignore", invalid="ignore"):
        w = ov / rng[:, None]
    flat = ~(rng > 0)
    w[flat, :] = 0.0
    M = (v[:, None] * w).astype("float32")

    if flat.any():   # doji / tavan kilidi: hacmin tamamı kapanışın dilimine (spec §7)
        cidx = np.clip(np.floor(np.log(df["Close"].to_numpy()[flat]) / bin_pct).astype(int) - base, 0, nb - 1)
        M[np.where(flat)[0], cidx] = v[flat].astype("float32")

    C = np.cumsum(M, axis=0)
    centers = np.exp((edges[:-1] + edges[1:]) / 2.0)

    for n in scales:
        poc = np.full(n_rows, np.nan)
        if n_rows > n:
            W = C[n:, :] - C[:-n, :]
            tot = W.sum(axis=1)
            am = np.argmax(W, axis=1)
            vals = np.where(tot > 0, centers[am], np.nan)
            poc[n:] = vals
        out[f"poc_{n}"] = poc
    return out


def volume_engine(df: pd.DataFrame, tr: pd.Series, hacim_gecerli: bool,
                  scales: tuple[int, ...] = SCALES, poc: bool = True) -> pd.DataFrame:
    """Her mumun hacmini kapanışın bar içindeki konumuna göre tahmini alış/satışa ayırır ve
    hacmi altı ölçekte kendi geçmişinin yüzdelik dilimine oturtur. Hacim yoksa tüm alanlar boş döner."""
    idx = df.index
    res = pd.DataFrame(index=idx)
    if not hacim_gecerli:
        for col in ("alis_hacim", "satis_hacim", "delta", "delta_dilim",
                    "hacim_bolge", "poc_merkez", "poc_konsensus", "poc_uzaklik_atr", "bar_konum"):
            res[col] = np.nan
        res["hacim_bolge"] = None
        return res

    h, l, c, v = df["High"], df["Low"], df["Close"], df["Volume"].fillna(0.0)
    rng = (h - l)
    poz = ((c - l) / rng.replace(0.0, np.nan)).fillna(0.5).clip(0, 1)   # spec §7: doji → nötr 0.5
    res["bar_konum"] = poz
    res["alis_hacim"] = v * poz
    res["satis_hacim"] = v * (1.0 - poz)
    res["delta"] = res["alis_hacim"] - res["satis_hacim"]
    res["delta_dilim"] = rolling_pct(res["delta"].abs())

    dilimler = []
    for n in scales:
        vs = v.rolling(n, min_periods=n).sum()
        d = rolling_pct(vs)
        res[f"hacim_dilim_{n}"] = d
        dilimler.append(d)
    med = pd.concat(dilimler, axis=1).median(axis=1)
    res["hacim_dilim_medyan"] = med
    res["hacim_bolge"] = np.select(
        [med.isna().to_numpy(), (med >= 0.95).to_numpy(), (med >= 0.75).to_numpy(), (med >= 0.25).to_numpy()],
        [None, "Patlayıcı", "Yüksek", "Orta"], default="Çok Düşük")

    if not poc:
        # HIZLI YOL: ayak izi/POC hesabı toplam sürenin ~%37'si. Yalnız son satır lazımsa
        # (canlı tarama) kapatılabilir; S10 senaryosu bu modda tetiklenemez.
        for n in scales:
            res[f"poc_{n}"] = np.nan
        for col in ("poc_konsensus", "poc_merkez", "poc_uzaklik_atr"):
            res[col] = np.nan
        return res

    pocs = _poc_by_scale(df, scales)
    res = pd.concat([res, pocs], axis=1)

    atr = tr.rolling(ATR_WINDOW, min_periods=ATR_WINDOW).mean()
    P = pocs.to_numpy()                       # (N, 6)
    tol_arr = (POC_TOL_ATR * atr).to_numpy()
    tol_gecersiz = ~np.isfinite(tol_arr)      # ATR henüz yok → uyum ÖLÇÜLEMEZ (0 değil, boş)
    tol = tol_arr[:, None]
    near = (np.abs(P[:, :, None] - P[:, None, :]) <= tol[:, :, None])
    near &= np.isfinite(P)[:, :, None] & np.isfinite(P)[:, None, :]
    counts = near.sum(axis=2)                 # her POC için kaç komşu
    best = np.argmax(counts, axis=1)
    rows = np.arange(len(df))
    cons = counts[rows, best].astype("float64")
    member = near[rows, best, :]
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)   # ısınma: POC'ların tamamı NaN
        merkez = np.where(member, P, np.nan)
        merkez = np.nanmean(merkez, axis=1)
    olcum_yok = (~np.isfinite(P).any(axis=1)) | tol_gecersiz
    cons[olcum_yok] = np.nan
    merkez[olcum_yok] = np.nan
    res["poc_konsensus"] = cons
    res["poc_merkez"] = merkez
    atr_safe = atr.replace(0.0, np.nan).to_numpy()      # sabit fiyat → sıfıra bölme yok
    res["poc_uzaklik_atr"] = (df["Close"].to_numpy() - merkez) / atr_safe
    return res


def volume_scenarios(df: pd.DataFrame, vol: pd.DataFrame, trend_yon: pd.Series,
                     uyum: pd.Series, verim_etiket: pd.Series) -> pd.Series:
    """Hacim, yön, verim ve POC bilgisini spec §4.4'teki 19 senaryodan birine sınıflar.
    Sıra bağlayıcıdır: önce nadir ve keskin olaylar, en sonda varsayılan "Dengeli Akış"."""
    n = len(df)
    if "delta" not in vol.columns or vol["delta"].isna().all():
        return pd.Series([None] * n, index=df.index)

    Z = vol["hacim_bolge"].to_numpy()
    z_patlayici = Z == "Patlayıcı"
    z_yuksek = np.isin(Z, ["Patlayıcı", "Yüksek"])
    z_orta_alt = np.isin(Z, ["Orta", "Çok Düşük"])
    z_dusuk = Z == "Çok Düşük"
    z_orta = Z == "Orta"

    E = verim_etiket.to_numpy()
    e_asiri = E == "Aşırı"
    e_guclu = np.isin(E, ["Aşırı", "Güçlü"])
    e_normal_ust = np.isin(E, ["Aşırı", "Güçlü", "Normal"])
    e_zayif = E == "Zayıf"

    D = np.sign(vol["delta"].to_numpy())
    d_pct = vol["delta_dilim"].to_numpy()
    T = trend_yon.to_numpy()
    poz = vol["bar_konum"].to_numpy()
    S = uyum.to_numpy()

    c = df["Close"].to_numpy()
    hi20 = df["High"].rolling(20, min_periods=20).max().to_numpy()
    lo20 = df["Low"].rolling(20, min_periods=20).min().to_numpy()
    yeni_zirve = c >= hi20
    yeni_dip = c <= lo20

    delta_s = pd.Series(vol["delta"].to_numpy(), index=df.index)
    kd5 = np.sign(delta_s.rolling(5, min_periods=5).sum().to_numpy())
    ayni5 = (np.sign(delta_s).rolling(5, min_periods=5).sum().abs().to_numpy() >= 4)
    d_artiyor = (pd.Series(d_pct, index=df.index).diff(5) > 0).to_numpy()
    z_artiyor = (vol["hacim_dilim_medyan"].diff(5) > 0).to_numpy()
    son_negatif = (df["Close"].diff() < 0).to_numpy()
    poc_yakin = np.abs(vol["poc_uzaklik_atr"].to_numpy()) <= 0.5
    poc_cons = vol["poc_konsensus"].to_numpy()

    conds = [
        z_patlayici & e_asiri & (T < 0) & (poz >= 0.66),                          # S13
        z_patlayici & e_asiri & (T > 0) & (poz <= 0.33),                          # S14
        (yeni_zirve | yeni_dip) & (D * np.where(yeni_zirve, 1, -1) < 0) & (d_pct >= 0.60),  # S9
        z_yuksek & e_guclu & ((c > hi20) | (c < lo20)),                           # S7
        z_yuksek & e_guclu & (D == T) & (d_pct >= 0.75) & (np.abs(S) >= 4),       # S1
        z_yuksek & (D == -T) & (T != 0) & (d_pct >= 0.75),                        # S4
        z_yuksek & (D > 0) & e_zayif,                                             # S11
        z_yuksek & (D < 0) & e_zayif,                                             # S12
        z_patlayici & (d_pct <= 0.25) & e_zayif,                                  # S18
        d_artiyor & z_artiyor & (D == -T) & (T != 0),                             # S5
        ayni5 & e_zayif,                                                          # S17
        z_orta_alt & (kd5 > 0) & (np.abs(S) <= 2),                                # S15
        z_orta_alt & (kd5 < 0) & (np.abs(S) <= 2),                                # S16
        poc_yakin & (poc_cons >= 4) & z_orta,                                     # S10
        z_dusuk & e_zayif & (np.abs(S) <= 1),                                     # S8
        (T != 0) & (D == T) & z_orta & e_normal_ust,                              # S2
        (T != 0) & (D == T) & z_dusuk,                                            # S3
        (T > 0) & son_negatif & z_orta_alt & (d_pct <= 0.5),                      # S6
    ]
    names = [
        "S13 Satış Kapitülasyonu", "S14 Alış Tükenmesi", "S9 Hacim Uyumsuzluğu",
        "S7 Kırılım Adayı", "S1 Tam Alış/Satış Onayı", "S4 Büyük Karşı Hacim",
        "S11 Talep Emilimi", "S12 Arz Dağıtımı", "S18 Yüksek Hacimli Çekişme",
        "S5 Baskı Artıyor", "S17 Baskı Birikiyor", "S15 Sessiz Toplama",
        "S16 Sessiz Dağıtım", "S10 Adil Değer Mıknatısı", "S8 Ölü Piyasa",
        "S2 Sağlıklı Destek", "S3 Zayıf Destek", "S6 Normal Düzeltme",
    ]
    conds = [np.where(pd.isna(cc), False, cc) for cc in conds]
    out = np.select(conds, names, default="S19 Dengeli Akış")
    gecersiz = pd.isna(Z) | pd.isna(E)
    out = np.where(gecersiz, None, out)
    return pd.Series(out, index=df.index)


# ═══════════════════════════════════════════════════════════════════════════
# 5. MODÜL D — TRADE FINDER (baskı sentezi)
# ═══════════════════════════════════════════════════════════════════════════
def pressure_engine(trend: pd.DataFrame, eff: pd.DataFrame, vol: pd.DataFrame) -> pd.DataFrame:
    """Beş boyutu (trend, kanaat, verim, momentum, hacim) tek bir −100…+100 baskı skoruna indirger.
    İki toplama modu yan yana üretilir: eşit ağırlıklı (hızlı) ve kapılı (durağan) — hangisinin
    daha iyi olduğu ölçülmeden seçilmez."""
    idx = trend.index
    b_trend = trend["uyum_skoru"] / 6.0
    kdil = trend["kanaat_dilim"]
    b_kanaat = (2.0 * kdil - 1.0) * np.sign(trend["uyum_skoru"])
    b_verim = eff["verim"]
    b_mom = np.tanh(trend[f"z_{7}"] - trend[f"z_{PRIMARY_SCALE}"])
    if "delta" in vol.columns and not vol["delta"].isna().all():
        b_hacim = np.sign(vol["delta"]) * (2.0 * vol["delta_dilim"] - 1.0)
    else:
        b_hacim = pd.Series(np.nan, index=idx)

    B = pd.concat([b_trend, b_kanaat, b_verim, b_mom, b_hacim], axis=1)
    B.columns = ["b_trend", "b_kanaat", "b_verim", "b_momentum", "b_hacim"]

    res = B.copy()
    res["baski_ens"] = B.mean(axis=1, skipna=True)

    sgn = np.sign(B["b_trend"])
    ayni = (np.sign(B).eq(sgn, axis=0) & B.notna())
    res["boyut_uyum"] = ayni.sum(axis=1)

    g1 = (B["b_trend"].abs() >= 0.34)
    g2 = (kdil >= 0.50)
    g3 = (B["b_verim"].abs() >= 0.25)
    g4 = (np.sign(B["b_momentum"]) == sgn)
    g5 = (B["b_hacim"].isna()) | (np.sign(B["b_hacim"]) == sgn)
    gate = (g1 & g2 & g3 & g4 & g5).astype("float64")
    res["baski_seq"] = sgn * gate * B.abs().mean(axis=1, skipna=True)

    res["baski_ens_100"] = res["baski_ens"] * 100.0
    res["baski_seq_100"] = res["baski_seq"] * 100.0
    res.loc[B["b_trend"].isna(), ["baski_ens", "baski_seq", "baski_ens_100", "baski_seq_100"]] = np.nan
    return res


def regime_hysteresis(p: pd.Series, tau0: float = HYST_TAU0,
                      lam: float = HYST_LAMBDA, span: int = 10) -> pd.DataFrame:
    """Yön değiştirme eşiğini yürürlükteki rejimin gücüne bağlar: rejim güçlüyken devrilmek zorlaşır.
    Bu bir DURUM MAKİNESİDİR — her adım bir öncekine bağlı olduğu için vektörleştirilemez;
    tek satır döngüsü buradadır ve maliyeti ölçülmüştür (bkz. `smr_lab/bench_multiscale.py`)."""
    arr = p.to_numpy(dtype="float64")
    n = len(arr)
    rejim = np.zeros(n)
    esik = np.full(n, np.nan)
    guc = np.full(n, np.nan)
    alpha = 2.0 / (span + 1.0)

    cur = 0.0
    r_ema = 0.0
    for i in range(n):
        x = arr[i]
        if not np.isfinite(x):
            rejim[i] = cur
            esik[i] = tau0 + lam * r_ema
            guc[i] = r_ema
            continue
        th = tau0 + lam * r_ema
        esik[i] = th
        if cur == 0.0:
            if abs(x) > tau0:
                cur = np.sign(x)
        elif np.sign(x) != cur and abs(x) > th:
            cur = np.sign(x)
        rejim[i] = cur
        katki = abs(x) if (cur != 0 and np.sign(x) == cur) else 0.0
        r_ema = (1 - alpha) * r_ema + alpha * katki
        guc[i] = r_ema

    return pd.DataFrame({"rejim": rejim, "rejim_esik": esik, "rejim_guc": guc}, index=p.index)


# ═══════════════════════════════════════════════════════════════════════════
# 6. MODÜL E + F — MEM ve sinyal sağlığı
# ═══════════════════════════════════════════════════════════════════════════
def mem_engine(B: pd.DataFrame) -> pd.DataFrame:
    """Beş boyutun eşit ağırlıklı ortalamasını −100…+100 arası tek bir sistem okumasına indirir ve
    bunun üzerine (fiyatın değil) klasik MACD üçlüsünü kurar. Ağırlıklar bilerek eşittir: tek bir
    backtest yokken ağırlık ayarlamak, aşırı uydurmanın kestirme yoludur.

    ⚠ ÖLÇÜLDÜ (23 Ağu 2026): eşit ağırlıkta MEM, `baski_ens_100` ile SAYISAL OLARAK AYNIDIR —
    ikisi de aynı beş boyutun eşit ağırlıklı ortalamasıdır. Yani MEM'in tek özgün çıktısı
    altındaki osilatördür; ayrı bir "sistem skoru" olarak sunulması bilgi eklemez."""
    cols = ["b_trend", "b_kanaat", "b_verim", "b_momentum", "b_hacim"]
    mem = B[cols].mean(axis=1, skipna=True) * 100.0
    out = pd.DataFrame({"MEM": mem})
    out["MEM_macd"] = mem.ewm(span=12, adjust=False).mean() - mem.ewm(span=26, adjust=False).mean()
    out["MEM_sinyal"] = out["MEM_macd"].ewm(span=9, adjust=False).mean()
    out["MEM_hist"] = out["MEM_macd"] - out["MEM_sinyal"]
    return out


def signal_health(baski: pd.Series, rejim: pd.Series) -> pd.DataFrame:
    """Yürürlükteki sinyalin hâlâ ayakta mı yoksa içi boşalıyor mu olduğunu canlı izler.
    "Trend hâlâ aktif ama sağlık düşüyor" ayrımı, tek bir skorun veremeyeceği bir çıkış bilgisidir."""
    s = baski * 100.0
    ayni = (np.sign(baski) == rejim) & (rejim != 0)
    saglik = np.where(ayni, s.abs(), -s.abs())
    saglik = np.where(baski.isna().to_numpy() | (rejim == 0).to_numpy(), np.nan, saglik)

    durum = np.select(
        [np.isnan(saglik), saglik >= HEALTH_STRONG, saglik >= HEALTH_WEAK, saglik >= 0],
        [None, "TEYİTLİ", "KORUNUYOR", "ZAYIFLIYOR"], default="ÇELİŞKİLİ")
    return pd.DataFrame({"saglik": saglik, "saglik_durum": durum}, index=baski.index)


# ═══════════════════════════════════════════════════════════════════════════
# 7. ANA GİRİŞ NOKTASI
# ═══════════════════════════════════════════════════════════════════════════
def run_engine(df: pd.DataFrame, theta: float = THETA_DEFAULT,
               scales: tuple[int, ...] = SCALES, poc: bool = True) -> pd.DataFrame:
    """Tek bir OHLCV tablosundan satır bazlı tüm motor çıktısını üretir (t satırı yalnız 0..t'den).
    Dönen tablo `karar` şemasındadır: her değer o mum kapandığında donar, bir daha değişmez.
    `poc=False` ayak izi hesabını atlar (süre ~%37 düşer, S10 senaryosu devre dışı kalır)."""
    d, meta = validate_ohlcv(df)
    tr = true_range(d)
    sigma = yang_zhang_sigma(d)

    trend = trend_engine(d["Close"], sigma, theta=theta, scales=scales)
    eff = efficiency_engine(d, tr, sigma, trend["trend_yon"], trend["uyum_skoru"], scales=scales)
    vol = volume_engine(d, tr, meta["hacim_gecerli"], scales=scales, poc=poc)
    senaryo = volume_scenarios(d, vol, trend["trend_yon"], trend["uyum_skoru"], eff["verim_etiket"])
    press = pressure_engine(trend, eff, vol)
    hyst = regime_hysteresis(press["baski_ens"])
    mem = mem_engine(press)
    health = signal_health(press["baski_ens"], hyst["rejim"])

    res = pd.concat([trend, eff, vol, press, hyst, mem, health], axis=1)
    res["hacim_senaryo"] = senaryo
    res["hacim_gecerli"] = meta["hacim_gecerli"]
    res["sigma_yz"] = sigma
    res["atr"] = tr.rolling(ATR_WINDOW, min_periods=ATR_WINDOW).mean()

    # konfluens işareti (spec §5.4)
    res["konfluens"] = ((res["boyut_uyum"] >= 5) & (res["baski_ens"].abs() >= 0.50) &
                        (np.sign(res["baski_ens"]) == res["rejim"])).astype("float64")
    res.loc[res["baski_ens"].isna(), "konfluens"] = np.nan

    res.attrs["meta"] = meta
    return res


def to_live(res: pd.DataFrame) -> pd.DataFrame:
    """Karar tablosunu "canlı mumda ekranda ne görünür" haline çevirir: her satır bir öncekinin değeri.
    Yazarın "canlı mum hesaba katılmaz" kuralının birebir karşılığıdır; backtest bunu DEĞİL, karar
    tablosunu kullanır (giriş t+1 açılışında doldurulur)."""
    return res.shift(1)


# ═══════════════════════════════════════════════════════════════════════════
# 8. KALİBRASYON — ölü bölge eşiği (spec §2.3, V-03)
# ═══════════════════════════════════════════════════════════════════════════
def calibrate_theta(frames, hedef_olu_bolge: float = 0.25,
                    scales: tuple[int, ...] = SCALES) -> float:
    """Ölü bölge eşiğini GETİRİYE HİÇ BAKMADAN, sadece eğim dağılımından belirler.
    Hedef: barların belirli bir oranı (varsayılan %25) "oy yok" bölgesinde kalsın; bu bir dağılım
    kalibrasyonudur, kâr optimizasyonu değildir — bir kez yapılır ve dondurulur."""
    havuz = []
    for df in frames:
        try:
            d, _ = validate_ohlcv(df)
        except Exception:
            continue
        sigma = yang_zhang_sigma(d)
        z = isotropic_slopes(d["Close"], sigma, scales)
        for n in scales:
            havuz.append(z[n].abs().dropna().to_numpy())
    if not havuz:
        raise ValueError("Kalibrasyon için geçerli seri bulunamadı")
    allz = np.concatenate(havuz)
    return float(np.quantile(allz, hedef_olu_bolge))
