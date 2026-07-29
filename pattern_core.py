"""PATTERN CORE — formasyon motoru yardımcıları (4 Tem 2026)

Yeni hesap kodu app.py'ye yazılmaz kuralı gereği (feedback_yeni_kod_ayri_modul)
formasyon geliştirme paketi bu modülde toplanır; app.py sadece çağırır.

Kapsam (14 maddelik geliştirme, 4 Tem 2026):
- Uyarlanabilir pivot eşiği (hisse volatilitesine göre)  → adaptive_threshold + prune_pivots
- Ön-trend şartı (TOBO: önceki düşüş, Fincan: önceki yükseliş) → tobo_pretrend_ok / cup_pretrend_ok
- Kulp süresi kuralı → handle_dur_ok
- Hacim imzası (sönümlenme + dip tükenmesi + dönüş hacmi) → volume_signature
- Kırılım sonrası RETEST / sahte-kırılım tespiti → detect_post_breakout
- Popup hacim teyidi doğru pivottan → dip_bounce_volume

⚠️ EŞİK KALİBRASYONU: PC sözlüğündeki tüm değerler BAŞLANGIÇ değeridir.
Eylül/Ekim 2026 signal_returns JOIN backtest'i ile segmente ölçülüp kalibre
edilecek (feedback_extrapolation_yasak — sezgiyle oynama, ölçümle değiştir).
"""

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Tüm yeni eşikler tek yerde (backtest kalibrasyonu için)
# ---------------------------------------------------------------------------
PC = dict(
    # Uyarlanabilir pivot eşiği
    adapt_win=60,        # volatilite ölçüm penceresi (bar)
    adapt_mult=1.5,      # günlük ortalama salınım × çarpan
    adapt_lo=0.025,      # eşik alt sınırı (%2.5)
    adapt_hi=0.08,       # eşik üst sınırı (%8)
    # OLUŞAN formasyonda boyuna max uzaklık (Çift Dip ile aynı kural)
    form_max_dist=12.0,  # yüzde
    # Fincan kalibrasyonu (18 Tem 2026, insan-etiketli 8 fincan — ölçülmüş)
    cup_rim=0.085,       # rim hizası ≤ (0.06 idi; AKSEN %8.3 gerçek fincan eleniyordu)
    cup_handle_lo=0.81,  # kulp > rim × bu (0.82 idi; EREGL 0.818 kıl payı eleniyordu)
    cup_ext_recent=120,  # "uzamış" durum: kırılım son bu kadar barda ise göster (yoksa bayat)
    # Ön-trend şartları
    pretrend_min_hist=40,    # bundan az geçmiş varsa eleme YAPMA (yeni veri)
    pretrend_cup_win=90,     # fincan: sol rim öncesi pencere
    pretrend_cup_gain=0.20,  # fincan: pencere dibinden rime min yükseliş
    pretrend_tobo_win=60,    # TOBO: sol omuz öncesi pencere
    pretrend_tobo_drop=0.15, # TOBO: baş, önceki tepeden min bu kadar aşağıda
    # Kulp süresi
    handle_min_bars=3,
    handle_max_abs=25,       # mutlak üst sınır (bar)
    handle_max_frac=1 / 3.0, # kupa süresinin oranı (büyük kupada esner)
    # Hacim imzası
    vol_dip_ratio=0.85,      # dip hacmi < 20g ort × bu → tükenme ✓
    vol_bounce_ratio=1.15,   # dönüş hacmi > 20g ort × bu → teyit ✓
    vol_fade_bonus=5,
    vol_dip_bonus=5,
    vol_break_bonus=10,
    vol_break_ratio=1.5,     # kırılım günü hacim ≥ 20g ort × bu
    # Retest
    retest_lookback=20,      # kırılımı geriye kaç bar içinde ara
    retest_band=0.03,        # boynun üstünde +%3'e kadar = retest bölgesi
    retest_hold=0.995,       # retest için fiyat boynun EN AZ bu katında (≈üstünde)
                             #   tutunmalı — boynun altına düşen = kırılım geri
                             #   alındı, destek testi DEĞİL (THYAO 326<333 vakası)
    retest_min_age=2,        # kırılımdan en az bu kadar gün geçmiş olmalı
    retest_fail=0.97,        # boyun × bu altında kapanış = sahte kırılım
    retest_vol_ratio=1.3,    # kırılım barında min hacim oranı
)


# ---------------------------------------------------------------------------
# 10 — Uyarlanabilir hassasiyet
# ---------------------------------------------------------------------------
def adaptive_threshold(close):
    """Hissenin son dönem günlük ortalama salınımından pivot-anlamlılık eşiği.

    Sakin hissede eşik düşer (küçük ama gerçek omuzlar görünür), oynak
    hissede yükselir (gürültü pivot sayılmaz). clamp: %2.5 – %8.
    """
    try:
        arr = np.asarray(close, dtype=float)[-PC['adapt_win']:]
        if len(arr) < 20:
            return 0.04
        rets = np.abs(np.diff(arr) / arr[:-1])
        thr = float(np.nanmean(rets)) * PC['adapt_mult']
        return float(min(max(thr, PC['adapt_lo']), PC['adapt_hi']))
    except Exception:
        return 0.04


def prune_pivots(sw_h, sw_l, min_move):
    """Aralarındaki hareket eşikten küçük pivot çiftlerini eler (zigzag birleşmesi).

    sw_h/sw_l: [(bar_index, fiyat)] listeleri. Kronolojik birleştirilir;
    komşu iki pivot arası yüzde hareket < min_move ise:
      - aynı tip (H-H / L-L): daha az ekstrem olan atılır
      - zıt tip: küçük karşı-salınımın iki pivotu da atılır (uçlar korunur —
        ilk/son pivot formasyon tazelik kontrolünde kullanılır)
    Döndürür: (sw_h2, sw_l2) — aynı formatta, budanmış.
    """
    piv = sorted([(i, v, 'H') for i, v in sw_h] + [(i, v, 'L') for i, v in sw_l],
                 key=lambda x: x[0])
    changed = True
    while changed and len(piv) > 3:
        changed = False
        for k in range(len(piv) - 1):
            i1, v1, t1 = piv[k]
            i2, v2, t2 = piv[k + 1]
            if abs(v2 - v1) / max(abs(v1), 1e-9) >= min_move:
                continue
            if t1 == t2:
                if t1 == 'H':
                    drop = [k] if v1 < v2 else [k + 1]
                else:
                    drop = [k] if v1 > v2 else [k + 1]
            else:
                if k + 1 == len(piv) - 1:
                    drop = [k]            # son pivotu koru (tazelik)
                elif k == 0:
                    drop = [k + 1]        # ilk pivotu koru
                else:
                    drop = [k, k + 1]
            for d in sorted(drop, reverse=True):
                piv.pop(d)
            changed = True
            break
    sw_h2 = [(i, v) for i, v, t in piv if t == 'H']
    sw_l2 = [(i, v) for i, v, t in piv if t == 'L']
    return sw_h2, sw_l2


# ---------------------------------------------------------------------------
# 6 — Ön-trend şartları
# ---------------------------------------------------------------------------
def cup_pretrend_ok(close_arr, left_rim_i):
    """Fincan-Kulp klasik tanımda DEVAM formasyonudur: kupa öncesi belirgin
    yükseliş olmalı (O'Neil ~%30; başlangıç eşiği %20, backtest kalibre eder).
    Yeterli geçmiş yoksa (yeni hisse / pencere başı) eleme YAPILMAZ."""
    try:
        lo = max(0, left_rim_i - PC['pretrend_cup_win'])
        if left_rim_i - lo < PC['pretrend_min_hist']:
            return True
        seg = np.asarray(close_arr[lo:left_rim_i + 1], dtype=float)
        base = float(np.nanmin(seg))
        if base <= 0:
            return True
        return float(close_arr[left_rim_i]) >= base * (1 + PC['pretrend_cup_gain'])
    except Exception:
        return True


def tobo_pretrend_ok(close_arr, left_sh_i, head_v):
    """TOBO bir DİP DÖNÜŞ formasyonudur: öncesinde düşüş olmalı.
    Kural: baş, sol omuz öncesi penceredeki tepeden en az %15 aşağıda.
    Yeterli geçmiş yoksa eleme YAPILMAZ."""
    try:
        lo = max(0, left_sh_i - PC['pretrend_tobo_win'])
        if left_sh_i - lo < PC['pretrend_min_hist']:
            return True
        prior_max = float(np.nanmax(np.asarray(close_arr[lo:left_sh_i + 1], dtype=float)))
        if prior_max <= 0:
            return True
        return float(head_v) <= prior_max * (1 - PC['pretrend_tobo_drop'])
    except Exception:
        return True


# ---------------------------------------------------------------------------
# 8 — Kulp süresi kuralı
# ---------------------------------------------------------------------------
def handle_dur_ok(right_rim_i, bar_total, cup_dur):
    """Kulp (sağ rimden bugüne) kupaya oranla kısa olmalı:
    3 bar ≤ kulp ≤ max(25, kupa/3). Uzun sürüklenme kulp değil yeni yapıdır."""
    hd = (bar_total - 1) - right_rim_i
    max_h = max(PC['handle_max_abs'], int(cup_dur * PC['handle_max_frac']))
    return PC['handle_min_bars'] <= hd <= max_h


# ---------------------------------------------------------------------------
# 9 — Hacim imzası (Fincan + TOBO ortak)
# ---------------------------------------------------------------------------
def volume_signature(vol_arr, start_i, end_i, dip_i):
    """Formasyon hacim imzası — 3 bağımsız ölçüm, hiçbiri tek başına elemez.

    fade_ok     : formasyonun 2. yarısı ort. hacmi < 1. yarısı (satıcı sönüyor)
    dip_ok      : son dipte hacim < 20g ort × 0.85 (tükenme)
    bounce_ok   : dipten sonraki ≤3 barın ort. hacmi > 20g ort × 1.15 (dönüş)
    bonus       : base_score'a eklenecek toplam (kırılım bonusu ayrı — caller
                  _detect_breakout_state hacim oranıyla breakout_bonus() ekler)
    Döndürür: dict(fade_ok, dip_ok, bounce_ok, bonus) — ölçülemeyen alan None.
    """
    out = dict(fade_ok=None, dip_ok=None, bounce_ok=None, bonus=0)
    try:
        v = np.asarray(vol_arr, dtype=float)
        n = len(v)
        s, e = max(0, int(start_i)), min(n - 1, int(end_i))
        di = min(n - 1, max(0, int(dip_i)))
        if e - s >= 10:
            half = s + (e - s) // 2
            first, second = np.nanmean(v[s:half]), np.nanmean(v[half:e + 1])
            if first > 0:
                out['fade_ok'] = bool(second < first)
        v20 = np.nanmean(v[max(0, di - 20):di]) if di >= 5 else np.nan
        if v20 and v20 > 0 and not np.isnan(v20):
            out['dip_ok'] = bool(v[di] < v20 * PC['vol_dip_ratio'])
            after = v[di + 1:di + 4]
            if len(after) > 0:
                out['bounce_ok'] = bool(np.nanmean(after) > v20 * PC['vol_bounce_ratio'])
        out['bonus'] = (PC['vol_fade_bonus'] * bool(out['fade_ok'])
                        + PC['vol_dip_bonus'] * bool(out['dip_ok']))
    except Exception:
        pass
    return out


def breakout_bonus(breakout_state, breakout_vol_ratio):
    """Kırılım gerçekleşmiş (state≥2) ve hacim ≥1.5× ise bonus."""
    try:
        if int(breakout_state) >= 2 and float(breakout_vol_ratio) >= PC['vol_break_ratio']:
            return PC['vol_break_bonus']
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------------------
# 12 — Kırılım sonrası durum: RETEST / sahte kırılım
# ---------------------------------------------------------------------------
def detect_post_breakout(close_s, vol_s, boundary):
    """Son retest_lookback bar içinde boyun kırılmış mı; şu an neredeyiz?

    Döndürür: dict(broke=bool, days_since=int, retest=bool, failed=bool)
      broke   : hacimli kırılım kapanışı bulundu (ilk kırılım barı esas alınır)
      retest  : kırılımdan ≥2 gün sonra fiyat boynun ±%3 bandına geri çekildi
                (boyun artık destek — klasik giriş bölgesi)
      failed  : kırılım sonrası fiyat boynun %3+ altına kapandı (sahte kırılım
                → formasyon gösterilmemeli)
    """
    out = dict(broke=False, days_since=0, retest=False, failed=False)
    try:
        c = np.asarray(close_s, dtype=float)
        v = np.asarray(vol_s, dtype=float)
        n = len(c)
        b = float(boundary)
        if n < 25 or b <= 0:
            return out
        break_i = None
        for idx in range(max(21, n - PC['retest_lookback']), n - 1):
            if c[idx] > b * 1.01:
                v20 = np.nanmean(v[idx - 20:idx])
                if v20 > 0 and v[idx] > v20 * PC['retest_vol_ratio']:
                    break_i = idx
                    break
        if break_i is None:
            return out
        out['broke'] = True
        out['days_since'] = n - 1 - break_i
        cur = c[-1]
        if cur < b * PC['retest_fail']:
            out['failed'] = True
            return out
        # Retest = kırılım sonrası fiyat boyna GERİ DÖNÜP boynu DESTEK olarak
        # test ediyor → fiyat boynun üstünde (ya da ≈üzerinde) tutunmalı.
        # Boynun altına düşmüş fiyat "destek testi" değil, kaybedilmiş kırılımdır.
        if (out['days_since'] >= PC['retest_min_age']
                and b * PC['retest_hold'] <= cur <= b * (1 + PC['retest_band'])):
            out['retest'] = True
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# 1 — Popup hacim teyidi: DOĞRU pivottan (son dip + sonrası)
# ---------------------------------------------------------------------------
def dip_bounce_volume(df, dip_date):
    """Formasyonun son DİP pivotunda hacim tükenmesi + dipten sonra dönüş hacmi.

    Eski kod tepe pivotunda 'dip hacmi' ölçüyordu (bug). Doğrusu:
      dip_ok    : dip barında hacim < 20g ort × 0.85
      bounce_ok : dipten sonraki ≤3 barın ort. hacmi > 20g ort × 1.15
    Döndürür: (dip_ok, bounce_ok) — ölçülemezse (None, None).
    """
    try:
        vol = df['Volume'].squeeze()
        if len(vol) < 22:
            return None, None
        di = df.index.get_indexer([pd.Timestamp(dip_date)], method='nearest')[0]
        v = vol.values.astype(float)
        v20 = np.nanmean(v[max(0, di - 20):di]) if di >= 5 else np.nan
        if not v20 or np.isnan(v20) or v20 <= 0:
            return None, None
        dip_ok = bool(v[di] < v20 * PC['vol_dip_ratio'])
        after = v[di + 1:di + 4]
        bounce_ok = bool(np.nanmean(after) > v20 * PC['vol_bounce_ratio']) if len(after) else None
        return dip_ok, bounce_ok
    except Exception:
        return None, None


def last_low_pivot_date(chart_data):
    """chart_data pivot listesinden son 'L' (dip) pivotunun tarihini döndürür."""
    try:
        types = chart_data.get('pivot_types') or []
        dates = chart_data.get('pivot_dates') or []
        for k in range(len(types) - 1, -1, -1):
            if types[k] == 'L' and k < len(dates):
                return dates[k]
    except Exception:
        pass
    return None
