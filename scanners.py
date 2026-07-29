# -*- coding: utf-8 -*-
"""
scanners.py — TEK-HİSSE TARAYICI ÇEKİRDEKLERİ
=============================================
app.py bölme projesi Adım 6a (6 Tem 2026) — 12 saf fonksiyon BİREBİR taşındı
(davranış değişikliği YOK). Girdi: sembol + hazır DataFrame (+ endeks serisi);
çıktı: sinyal dict'i veya None. Veri çekme / Streamlit / DB YOK — toplu tarama
döngüleri (scan_*_batch) hâlâ app.py'de, veri katmanıyla birlikte taşınacak.

İçerik: Gizli Birikim · Radar1 · Radar2 · ICT Setup · Güçlü Dönüş ·
Pre-Launch BOS · Nadir Fırsat çekirdekleri + 5 formasyon şekil-doğrulayıcı
(cup/tobo/double-bottom/double-top/wedge). Fotoğraf: golden_record sc_* hedefleri.
"""
import math
import re
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from indicators import check_lazybear_squeeze, compute_cmf, detect_darvas_box
from deepening_policy import b11_pilot_profile, leadership_profile

def _validate_cup_shape(cup_arr, left_i, dip_i, right_i, r2, min_r2=0.71, cent_hi=0.77):
    """Fincan-kulp gövde (fincan) şekil doğrulaması — Dengeli profil (30 May 2026).

    Gerçek bir fincan: (1) dip yaklaşık ORTADA, (2) sol yarı inişli + sağ yarı
    çıkışlı, (3) güçlü U-fit (R²). Bu üçü sağlanmazsa düşüş-sonrası-sıçrama veya
    asimetrik salınım 'fincan' diye etiketlenir (SISE tipi sahte pozitif).

    cup_arr   : sol rim'den sağ rim'e kapanış dizisi (offset 0 = left_i)
    left_i/dip_i/right_i : mutlak bar indeksleri (sol rim / dip / sağ rim)
    r2        : çağıran tarafça hesaplanmış polinom U-fit R²
    Döndürür  : True = geçerli fincan gövdesi.

    ⚙️ KALİBRASYON (18 Tem 2026 devamı, insan-etiketli 8 fincan): min_r2 0.78→0.71
    (POLHO/ALARK R²0.73 gerçek fincanlar eleniyordu), cent_hi 0.70→0.77 (POLHO dip
    0.76'da — sağa kaymış ama gerçek). Diğer şartlar (slope) AYNI. Detay:
    memory/project_formation_recalibration.md.
    """
    if r2 < min_r2:
        return False
    span = right_i - left_i
    if span <= 0:
        return False
    # 1) Dip merkeziyeti — kenardaki dip fincan değildir
    cent = (dip_i - left_i) / span
    if not (0.30 <= cent <= cent_hi):
        return False
    # 2) Sol yarı inişli + sağ yarı çıkışlı (V/sürüklenme eler)
    rel = dip_i - left_i
    lh = cup_arr[:max(2, rel + 1)]
    rh = cup_arr[rel:]
    if len(lh) >= 3 and len(rh) >= 3:
        lslope = np.polyfit(np.arange(len(lh)), lh, 1)[0]
        rslope = np.polyfit(np.arange(len(rh)), rh, 1)[0]
        if not (lslope < 0 and rslope > 0):
            return False
    return True


def _validate_tobo_shape(sl1_i, sl1_v, sl2_i, sl2_v, sl3_i, sl3_v,
                         sh1_i, sh1_v, sh2_i, sh2_v, bar_total):
    """TOBO (Ters OBO) şekil doğrulaması — Dengeli profil (30 May 2026).

    Gerçek bir TOBO: (1) baş omuzlardan belirgin derin, (2) sol ve sağ kanat
    zamanca orantılı (tek yana sıkışmış değil), (3) boyun çizgisi yatay
    OLMAK ZORUNDA değil — hafif eğimli boyunlar da geçerlidir.

    Eski mantık boyunu ±%6 YATAY olmaya zorluyordu → eğimli boyunlu gerçek
    TOBO'lar reddediliyordu. Ayrıca 5-bar sol + 60-bar sağ omuz gibi aşırı
    asimetrik yapılar geçebiliyordu.

    Döndürür: (ok, neck_now)
      ok       : True = geçerli TOBO iskeleti
      neck_now : eğimli boyun çizgisinin SON bardaki (kırılım referansı) değeri
    """
    # 1) Baş derinlik tabanı — baş omuzlardan en az %8 daha derin
    if not (sl2_v < sl1_v * 0.92 and sl2_v < sl3_v * 0.92):
        return False, None
    # 2) Zaman simetrisi — sol kanat / sağ kanat oranı 0.4–2.5
    t_left  = sl2_i - sl1_i
    t_right = sl3_i - sl2_i
    if t_left <= 0 or t_right <= 0:
        return False, None
    ratio = t_left / t_right
    if not (0.4 <= ratio <= 2.5):
        return False, None
    # 3) Eğimli boyun desteği — boyun yatay zorunlu DEĞİL; sadece aşırı eğim eler
    if abs(sh1_v - sh2_v) / sh1_v > 0.15:
        return False, None
    # Boyun çizgisi: iki boyun tepesinden geçen doğru, son bara ekstrapole edilir
    nslope = (sh2_v - sh1_v) / (sh2_i - sh1_i) if sh2_i != sh1_i else 0.0
    neck_now = sh2_v + nslope * (bar_total - 1 - sh2_i)
    # Ekstrapolasyon güvenliği — boyun iki tepe aralığından çok sapmasın
    nmin = min(sh1_v, sh2_v) * 0.90
    nmax = max(sh1_v, sh2_v) * 1.12
    neck_now = max(nmin, min(nmax, neck_now))
    return True, neck_now


def _is_index_symbol(sym):
    """Endeks sembolü mü? '^GSPC' aileleri + BIST endeksleri ('XU100.IS' vb.).
    X-prefix TEK BAŞINA yetmez — ABD'de XOM gibi X'le başlayan HİSSE var;
    o yüzden X'liler sadece .IS uzantısıyla endeks sayılır."""
    s = str(sym).upper()
    return s.startswith('^') or (s.endswith('.IS') and s.startswith(('XU', 'XB', 'XT', 'XY')))


def _detect_double_bottom(sw_l_y, sw_h_y, curr_price, bar_total,
                          eq_tol=0.04, min_depth=0.12, max_depth=0.40,
                          min_dur=25, max_dur=180, max_form_dist=12.0, is_index=False):
    """Çift Dip (W) tespiti — swing tabanlı (30 May 2026).

    W formasyonu: Dip1 → Orta Tepe (boyun) → Dip2 (≈Dip1). İki ~eşit dip ve
    aralarındaki belirgin tepe; boyun kırılımı yukarı dönüş sinyalidir. Fincan
    ve TOBO ile örtüşmez (onlar tek dip / 3 dip; bu 2 dip).

    Şekil garantileri (sahte W'leri eler):
    - İki dip birbirine ±%4 yakın (eq_tol) — kademeli düşüşü W sanmayı önler
    - Orta tepe diplerden %12–%40 arası (min/max_depth) — düz taban VE
      "büyük çöküş + toparlanma"yı (W değil) birlikte eler
    - Süre 25–180 bar; ikinci dip son 60 günde (taze)
    - Fiyat ikinci dibin üstünde + R/R ≥ 1.0
    - OLUŞAN durumda fiyat boyna ≤%12 uzakta (max_form_dist): "erken ama alakalı".
      Bu, W'yi nadir/kaliteli tutan ANA filtredir (yoksa ~%35 hisse W görünür).

    Döndürür: dict(d1_i,d1_v,neck_i,neck_v,d2_i,d2_v,target,state,dist,dur)
              veya None.  state: 'break' (boyun kırılımı) / 'form' (oluşuyor).
    """
    # 18 Tem 2026 — ENDEKS SIKI TOLERANS (yön simetrisi: double_top ile aynı kural).
    # ±%4 hisse oynaklığı için; endeks bileşenlerin ortalaması olduğundan yapısal
    # olarak az oynar → endekste eşitlik eşiği %2'ye iner.
    if is_index:
        eq_tol = min(eq_tol, 0.02)
    if len(sw_l_y) < 2 or len(sw_h_y) < 1:
        return None
    for j in range(len(sw_l_y) - 1, 0, -1):
        d2_i, d2_v = sw_l_y[j]
        if bar_total - d2_i > 60:            # ikinci dip taze olmalı
            continue
        for k in range(j - 1, -1, -1):
            d1_i, d1_v = sw_l_y[k]
            dur = d2_i - d1_i
            if not (min_dur <= dur <= max_dur):
                continue
            if abs(d1_v - d2_v) / d1_v > eq_tol:   # iki dip ~eşit
                continue
            mid_h = [(i, v) for i, v in sw_h_y if d1_i < i < d2_i]
            if not mid_h:
                continue
            neck_i, neck_v = max(mid_h, key=lambda x: x[1])
            base = min(d1_v, d2_v)
            depth = (neck_v - base) / base
            if not (min_depth <= depth <= max_depth):  # orta tepe belirgin ama aşırı değil
                continue
            # 18 Tem: iki dip arasında MATERYAL daha derin swing low varsa bu bir TOBO
            # başıdır (2 omuz + baş), W değil. W'nin TOBO omuzlarını "2 eşit dip" sanıp
            # ateşlemesini (aday havuzunda TOBO'yu çalmasını) + genel aşırı-tespiti keser.
            _mid_lows = [v for i, v in sw_l_y if d1_i < i < d2_i]
            if _mid_lows and min(_mid_lows) < base * 0.97:
                continue
            if curr_price < d2_v * 0.98:            # fiyat ikinci dibin altında değil
                continue
            target = neck_v + (neck_v - base)
            risk = max(curr_price - d2_v * 0.98, 0.01)
            if (target - curr_price) / risk < 1.0:
                continue
            dist = ((neck_v - curr_price) / neck_v * 100) if curr_price < neck_v else 0
            breaking = neck_v * 0.97 <= curr_price <= neck_v * 1.10
            forming  = (curr_price > d2_v * 1.01 and curr_price < neck_v * 0.97
                        and dist <= max_form_dist)   # erken ama boyna yakın
            if not (breaking or forming):
                continue
            return dict(d1_i=d1_i, d1_v=d1_v, neck_i=neck_i, neck_v=neck_v,
                        d2_i=d2_i, d2_v=d2_v, target=target,
                        state="break" if breaking else "form", dist=dist, dur=dur)
    return None


def _detect_double_top(sw_h_y, sw_l_y, curr_price, bar_total,
                       eq_tol=0.04, min_depth=0.12, max_depth=0.40,
                       min_dur=25, max_dur=180, max_form_dist=12.0, is_index=False):
    """İkili Tepe (M) tespiti — Çift Dip'in (W) AYI simetriği (30 May 2026).

    M formasyonu: Tepe1 → Orta Vadi (boyun) → Tepe2 (≈Tepe1). İki ~eşit tepe ve
    aralarındaki belirgin vadi; boyun çizgisi AŞAĞI kırılınca düşüş dönüşü
    (tepe/satış uyarısı). Çift Dip ile örtüşmez (o boğa, bu ayı).

    Şekil garantileri (sahte M'leri eler):
    - İki tepe birbirine ±%4 yakın (kademeli yükselişi M sanmayı önler)
    - Orta vadi tepelerden %12–%40 aşağıda (düz tavanı VE aşırı V'yi eler)
    - Süre 25–180 bar; ikinci tepe son 60 günde (taze)
    - Fiyat ikinci tepenin altında + R/R ≥ 1.0 (aşağı hedef)
    - OLUŞAN durumda fiyat boyna ≤%12 uzakta (erken ama alakalı)

    Döndürür: dict(t1_i,t1_v,neck_i,neck_v,t2_i,t2_v,target,state,dist,dur)
              veya None.  state: 'break' (boyun aşağı kırıldı) / 'form' (oluşuyor).
    """
    # 18 Tem 2026 — ENDEKS SIKI TOLERANS: ±%4 hisse oynaklığına göre; endeks bileşen
    # ortalaması olduğundan yapısal olarak az oynar → %3 fark endekste "eşit tepe" değil
    # ALÇALAN TEPEDİR (XU100 15.204-vs-14.725 vakası). Endekste eşitlik eşiği %2.
    if is_index:
        eq_tol = min(eq_tol, 0.02)
    if len(sw_h_y) < 2 or len(sw_l_y) < 1:
        return None
    for j in range(len(sw_h_y) - 1, 0, -1):
        t2_i, t2_v = sw_h_y[j]
        if bar_total - t2_i > 60:            # ikinci tepe taze olmalı
            continue
        for k in range(j - 1, -1, -1):
            t1_i, t1_v = sw_h_y[k]
            dur = t2_i - t1_i
            if not (min_dur <= dur <= max_dur):
                continue
            if abs(t1_v - t2_v) / t1_v > eq_tol:   # iki tepe ~eşit
                continue
            mid_l = [(i, v) for i, v in sw_l_y if t1_i < i < t2_i]
            if not mid_l:
                continue
            neck_i, neck_v = min(mid_l, key=lambda x: x[1])   # orta vadi = en düşük
            top = max(t1_v, t2_v)
            depth = (top - neck_v) / top
            if not (min_depth <= depth <= max_depth):  # orta vadi belirgin ama aşırı değil
                continue
            # 18 Tem: iki tepe arasında MATERYAL daha yüksek swing high varsa bu M değil
            # (OBO başı / üçlü tepe). W ile simetrik kural; genel aşırı-tespiti keser.
            _mid_highs = [v for i, v in sw_h_y if t1_i < i < t2_i]
            if _mid_highs and max(_mid_highs) > top * 1.03:
                continue
            if curr_price > t2_v * 1.02:            # fiyat ikinci tepenin üstünde değil
                continue
            target = neck_v - (top - neck_v)        # aşağı hedef
            risk = max(t2_v * 1.02 - curr_price, 0.01)
            if (curr_price - target) / risk < 1.0:
                continue
            dist = ((curr_price - neck_v) / neck_v * 100) if curr_price > neck_v else 0
            breaking = neck_v * 0.90 <= curr_price <= neck_v * 1.03
            forming  = (curr_price < t2_v * 0.99 and curr_price > neck_v * 1.03
                        and dist <= max_form_dist)   # erken ama boyna yakın
            if not (breaking or forming):
                continue
            return dict(t1_i=t1_i, t1_v=t1_v, neck_i=neck_i, neck_v=neck_v,
                        t2_i=t2_i, t2_v=t2_v, target=float(max(target, 0.01)),
                        state="break" if breaking else "form", dist=dist, dur=dur)
    return None


def _detect_wedge(sw_h_y, sw_l_y, close, high, low, volume, curr_price, bar_total,
                  min_dur=25, max_dur=200, min_r2=0.50, conv_ratio=0.66):
    """Düşen Kama (boğa) / Yükselen Kama (ayı) tespiti — regresyon tabanlı (30 May 2026).

    BIST'te en çok kazandıran formasyonlardan biri ama yanlış tespiti kolay.
    KAMA'yı kanaldan/üçgenden ayıran kritik özellik = YAKINSAMA (convergence):
    iki trend çizgisi aynı yöne eğimli AMA birbirine yaklaşır.

    Düşen Kama (boğa dönüş): üst & alt çizgi AŞAĞI eğimli; üst daha hızlı düşer
      (s_hi < s_lo < 0). Daralan kanal, düşen tepeler+dipler. Kırılım YUKARI.
    Yükselen Kama (ayı dönüş): üst & alt çizgi YUKARI eğimli; alt daha hızlı
      yükselir (s_lo > s_hi > 0). Kırılım AŞAĞI.

    Sahte eleme garantileri:
    - En az 3 tepe + 3 dip (2 nokta gürültüye açık)
    - Her iki çizgi R² ≥ 0.50 (dağınık pivot = kama değil)
    - Yakınsama: son genişlik ≤ baş genişlik × conv_ratio (paralel kanalı eler)
    - Eğim işaretleri net (yatay çizgi = üçgen, kama değil)
    - Süre 25–200 bar; son pivot 60 günden taze
    - Hacim oluşum boyunca düşer (kama imzası) → bonus, zorunlu değil

    Döndürür: dict(kind, slope_hi, slope_lo, up_line, lo_line, target, state,
                   dist, dur, r2_hi, r2_lo, vol_drop) veya None.
      kind: 'falling' (boğa) | 'rising' (ayı)
      state: 'break' (kırılım gerçekleşti) | 'form' (oluşuyor, kırılıma yakın)
    """
    if len(sw_h_y) < 3 or len(sw_l_y) < 3:
        return None

    def _fit(points):
        x = np.array([i for i, _ in points], dtype=float)
        y = np.array([v for _, v in points], dtype=float)
        if len(x) < 2 or x.max() == x.min():
            return None
        coef = np.polyfit(x, y, 1)
        yp = np.polyval(coef, x)
        ss_res = np.sum((y - yp) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        return coef, r2

    # Formasyon penceresi: son pivotlardan geriye, 25–200 bar
    last_h_i = sw_h_y[-1][0]; last_l_i = sw_l_y[-1][0]
    last_piv = max(last_h_i, last_l_i)
    if bar_total - 1 - last_piv > 60:
        return None
    win_start = last_piv - max_dur
    hi_pts = [(i, v) for i, v in sw_h_y if i >= win_start]
    lo_pts = [(i, v) for i, v in sw_l_y if i >= win_start]
    if len(hi_pts) < 3 or len(lo_pts) < 3:
        return None

    first_i = min(hi_pts[0][0], lo_pts[0][0])
    dur = last_piv - first_i
    if not (min_dur <= dur <= max_dur):
        return None

    fit_hi = _fit(hi_pts); fit_lo = _fit(lo_pts)
    if fit_hi is None or fit_lo is None:
        return None
    (coef_hi, r2_hi) = fit_hi
    (coef_lo, r2_lo) = fit_lo
    if r2_hi < min_r2 or r2_lo < min_r2:
        return None
    s_hi = coef_hi[0]; s_lo = coef_lo[0]

    # Çizgi değerleri: baş ve son
    x_start = first_i; x_end = bar_total - 1
    hi_start = np.polyval(coef_hi, x_start); hi_end = np.polyval(coef_hi, x_end)
    lo_start = np.polyval(coef_lo, x_start); lo_end = np.polyval(coef_lo, x_end)
    w_start = hi_start - lo_start
    w_end   = hi_end - lo_end
    if w_start <= 0 or w_end <= 0:
        return None
    # Yakınsama zorunlu: aralık daralmalı (kanal değil)
    if not (w_end <= w_start * conv_ratio):
        return None

    # Hacim oluşum boyunca düşüyor mu? (kama imzası, bonus)
    vol_drop = False
    try:
        _seg = volume.iloc[first_i:bar_total]
        if len(_seg) >= 10:
            _half = len(_seg) // 2
            vol_drop = float(_seg.iloc[_half:].mean()) < float(_seg.iloc[:_half].mean())
    except Exception:
        pass

    _slope_eps = 1e-9
    if s_hi < -_slope_eps and s_lo < -_slope_eps and s_hi < s_lo:
        # DÜŞEN KAMA (boğa) — üst direnç çizgisi kırılımı yukarı
        kind = "falling"
        up_line = float(hi_end)   # kırılım çizgisi = üst
        lo_line = float(lo_end)
        # Hedef: kama tabanının yüksekliği kadar (ilk genişlik), üst çizgiden
        target = up_line + w_start
        dist = ((up_line - curr_price) / up_line * 100) if curr_price < up_line else 0
        breaking = curr_price >= up_line * 0.985 and curr_price <= up_line * 1.10
        forming  = (curr_price < up_line * 0.985 and curr_price > lo_line * 0.98
                    and dist <= 12.0)
        if not (breaking or forming):
            return None
        risk = max(curr_price - lo_line * 0.98, 0.01)
        if (target - curr_price) / risk < 1.0:
            return None
        return dict(kind=kind, slope_hi=s_hi, slope_lo=s_lo,
                    up_line=up_line, lo_line=lo_line, target=float(target),
                    up_start=float(hi_start), lo_start=float(lo_start),
                    state="break" if breaking else "form", dist=dist, dur=dur,
                    r2_hi=r2_hi, r2_lo=r2_lo, vol_drop=vol_drop,
                    first_i=first_i)

    if s_hi > _slope_eps and s_lo > _slope_eps and s_lo > s_hi:
        # YÜKSELEN KAMA (ayı) — alt destek çizgisi kırılımı aşağı
        kind = "rising"
        up_line = float(hi_end)
        lo_line = float(lo_end)   # kırılım çizgisi = alt
        target = lo_line - w_start   # aşağı hedef
        dist = ((curr_price - lo_line) / lo_line * 100) if curr_price > lo_line else 0
        breaking = curr_price <= lo_line * 1.015 and curr_price >= lo_line * 0.90
        forming  = (curr_price > lo_line * 1.015 and curr_price < up_line * 1.02
                    and dist <= 12.0)
        if not (breaking or forming):
            return None
        return dict(kind=kind, slope_hi=s_hi, slope_lo=s_lo,
                    up_line=up_line, lo_line=lo_line, target=float(max(target, 0.01)),
                    up_start=float(hi_start), lo_start=float(lo_start),
                    state="break" if breaking else "form", dist=dist, dur=dur,
                    r2_hi=r2_hi, r2_lo=r2_lo, vol_drop=vol_drop,
                    first_i=first_i)

    return None


def process_single_accumulation(symbol, df, benchmark_series):
    try:
        if df.empty or 'Close' not in df.columns: return None
        df = df.dropna(subset=['Close'])
        if len(df) < 60: return None

        close = df['Close']
        open_ = df['Open']
        high = df['High']
        low = df['Low']
        volume = df['Volume'] if 'Volume' in df.columns else pd.Series([1]*len(df), index=df.index)
        
        # --- 1. SAVAŞ'IN GÜVENLİK KALKANI (SON 2 GÜN KURALI) ---
        price_now = float(close.iloc[-1])
        if len(close) > 2:
            price_2_days_ago = float(close.iloc[-3]) 
            # Son 2 gün toplam %3'ten fazla düştüyse (0.97 altı) ELE.
            if price_now < (price_2_days_ago * 0.97): 
                return None 

        # --- 2. HACİM KONTROLÜ (Artık Global Olarak Hesaplanıyor) ---
        volume_for_check = float(volume.iloc[-1])

        # --- 3. MEVCUT MANTIK (TOPLAMA & FORCE INDEX) ---
        # FIX (30 May 2026): span 2 → 13 (Elder klasik Force Index). span=2 neredeyse
        # ham veriydi; tek günlük fiyat×hacim sıçraması skoru domine edip dağıtımı
        # toplama gibi gösterebiliyordu. 13 EMA gerçek birikimli baskıyı yansıtır.
        delta = close.diff()
        force_index = delta * volume
        mf_smooth = force_index.ewm(span=13, adjust=False).mean()

        last_10_mf = mf_smooth.tail(10)
        last_10_close = close.tail(10)
        
        if len(last_10_mf) < 10: return None
        
        pos_days_count = (last_10_mf > 0).sum()
        if pos_days_count < 7: return None 

        price_start = float(last_10_close.iloc[0]) 
        if price_start == 0: return None
        
        change_pct = (price_now - price_start) / price_start
        avg_mf = float(last_10_mf.mean())
        
        if avg_mf <= 0: return None
        if change_pct > 0.05: return None 
        # --- HALÜSİNASYON ÖNLEYİCİ (RSI FİLTRESİ) ---
        # Eğer RSI > 60 ise, fiyat tepededir. Bu 'Toplama' değil, 'Dağıtım'dır.
        # Bu yüzden RSI şişikse, bu hisseyi listeden atıyoruz.
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi_check = 100 - (100 / (1 + (gain / loss))).iloc[-1]
        
        if rsi_check > 60: return None # Şişkin hisseyi yok say.

        # --- 4. MANSFIELD RS (GÜÇ) ---
        # FIX (31 May 2026): Bench veri yoksa RS kanıtı eksik → sinyali REDDET.
        # Eskiden bench None ise rs_score=0 ile devam ediyordu (sessiz bilgi kaybı).
        if benchmark_series is None:
            return None
        rs_status = "Zayıf"
        rs_score = 0
        try:
            common_idx = close.index.intersection(benchmark_series.index)
            if len(common_idx) <= 50:
                return None  # Yeterli ortak veri yoksa RS ölçülemez → reddet
            stock_aligned = close.loc[common_idx]
            bench_aligned = benchmark_series.loc[common_idx]
            rs_ratio = stock_aligned / bench_aligned
            rs_ma = rs_ratio.rolling(50).mean()
            mansfield = ((rs_ratio / rs_ma) - 1) * 10
            curr_rs = float(mansfield.iloc[-1])
            if curr_rs > 0:
                rs_status = "GÜÇLÜ (Endeks Üstü)"
                rs_score = 1
                if curr_rs > float(mansfield.iloc[-5]):
                    rs_status += " 🚀"
                    rs_score = 2
        except Exception:
            return None  # RS hesabı patladıysa sinyal verme

        # --- 5. POCKET PIVOT (ZAMAN AYARLI KONTROL) ---
        is_pocket_pivot = False
        pp_desc = "-"
        
        is_down_day = close < open_
        down_volumes = volume.where(is_down_day, 0)
        # FIX (30 May 2026): 3 gün → 10 gün (O'Neil klasik Pocket Pivot tanımı).
        # Bugünkü up-day hacmi, son 10 günün en yüksek düşüş-günü hacmini aşmalı.
        max_down_vol_10 = down_volumes.iloc[-11:-1].max()
        
        is_up_day = float(close.iloc[-1]) > float(open_.iloc[-1])
        
        if is_up_day and (volume_for_check > max_down_vol_10):
            is_pocket_pivot = True
            if float(volume.iloc[-1]) < max_down_vol_10:
                pp_desc = "⚡ PIVOT (Hacim Hızı Yüksek)"
            else:
                pp_desc = "⚡ POCKET PIVOT (Onaylı)"

        # --- YENİ EKLENEN: LAZYBEAR SQUEEZE KONTROLÜ ---
        is_sq = check_lazybear_squeeze(df)
        if is_sq:
            quality_label = "A KALİTE (Sıkışmış)"
        else:
            quality_label = "B KALİTE (Normal)"

        # --- CMF(20) TEYİT KATMANI (FIX 30 May 2026) ---
        # Force Index momentum ölçer; CMF birikim (bar-içi alıcı baskısı) ölçer.
        # İkisi de pozitifse toplama güçlü; çelişirse şüpheli → skor downgrade.
        cmf_20 = compute_cmf(df, period=20)
        cmf_confirms = cmf_20 > 0.05
        cmf_conflict = cmf_20 < -0.05

        # --- 52H YILLIK KONUM (FIX 31 May 2026) ---
        # Yıllık dipte birikim = güçlü tez (alıcı düşük seviyelerden topluyor).
        # Yıllık zirvede birikim = şüpheli (zirvede toplama olmaz, dağıtım olur).
        y_pos = 50.0  # default — nötr
        try:
            _y_close = close.iloc[-252:] if len(close) >= 252 else close
            _y_hi = float(_y_close.max())
            _y_lo = float(_y_close.min())
            if _y_hi > _y_lo:
                y_pos = (price_now - _y_lo) / (_y_hi - _y_lo) * 100
        except Exception:
            y_pos = 50.0

        # --- SKORLAMA (FIX 30 May 2026: çarpım → toplamsal 0-100 normalize) ---
        # Eski: avg_mf × 5/10 × (1+rs_score) → bonuslar skoru 11× şişiriyordu,
        # tek yüksek-hacim barı olan hisse gerçek toplama yapandan üste çıkıyordu.
        # Yeni: her bileşen sabit aralıkta, toplamı ~0-100.
        # 1) Israr (pozitif MF gün sayısı 7-10) → 0-30
        s_israr = max(0.0, min(30.0, (pos_days_count - 6) * 7.5))
        # 2) Gizlilik (yatay/aşağıyken giriş daha değerli) → 10 veya 20
        s_gizli = 20.0 if change_pct < 0 else 10.0
        # 3) Mansfield RS → 0/8/15
        s_rs = 15.0 if rs_score >= 2 else (8.0 if rs_score >= 1 else 0.0)
        # 4) Pocket Pivot → 0/10/15
        if is_pocket_pivot:
            s_pivot = 15.0 if "Onaylı" in pp_desc else 10.0
        else:
            s_pivot = 0.0
        # 5) Squeeze → 0/10
        s_squeeze = 10.0 if is_sq else 0.0
        # 6) CMF teyit/çelişki → +10 / -15
        s_cmf = 10.0 if cmf_confirms else (-15.0 if cmf_conflict else 0.0)
        # 7) 52H Konum (FIX 31 May 2026): dipte +10, alt %50 0, üst %75+ -10
        if y_pos <= 30:
            s_52h = 10.0      # Yıllık dipte → birikim tezi güçlü
        elif y_pos >= 75:
            s_52h = -10.0     # Yıllık zirvede → birikim şüpheli (dağıtım riski)
        else:
            s_52h = 0.0       # Orta bölge → nötr

        final_score = s_israr + s_gizli + s_rs + s_pivot + s_squeeze + s_cmf + s_52h
        final_score = max(0.0, min(100.0, final_score))

        if avg_mf > 1_000_000: mf_str = f"{avg_mf/1_000_000:.1f}M"
        elif avg_mf > 1_000: mf_str = f"{avg_mf/1_000:.0f}K"
        else: mf_str = f"{int(avg_mf)}"

        return {
            "Sembol": symbol,
            "Fiyat": f"{price_now:.2f}",
            "Degisim_Raw": change_pct,
            "Degisim_Str": f"%{change_pct*100:.1f}",
            "MF_Gucu_Goster": mf_str,
            "Gun_Sayisi": f"{pos_days_count}/10",
            "Skor": final_score,
            "CMF": round(cmf_20, 3),
            "RS_Durumu": rs_status,
            "Pivot_Sinyali": pp_desc,
            "Pocket_Pivot": is_pocket_pivot,
            "Kalite": quality_label,
            "Yillik_Konum": round(y_pos, 0),
            "Hacim": float(volume.iloc[-1])
        }
    except Exception: return None


def process_single_radar1(symbol, df, bench_series=None):
    try:
        if df.empty or 'Close' not in df.columns: return None
        df = df.dropna(subset=['Close'])
        if len(df) < 60: return None
        
        close = df['Close']; high = df['High']; low = df['Low']
        volume = df['Volume'] if 'Volume' in df.columns else pd.Series([0]*len(df))
        
        # Göstergeler
        ema5 = close.ewm(span=5, adjust=False).mean()
        ema20 = close.ewm(span=20, adjust=False).mean()
        sma20 = close.rolling(20).mean(); std20 = close.rolling(20).std()
        
        # Bollinger Squeeze Hesabı
        bb_width = ((sma20 + 2*std20) - (sma20 - 2*std20)) / (sma20 - 0.0001)

        # MACD Hesabı
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        hist = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        
        # RSI Hesabı
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss)))
        
        # ADX Hesabı (Trend Gücü)
        try:
            plus_dm = high.diff(); minus_dm = low.diff()
            plus_dm[plus_dm < 0] = 0; minus_dm[minus_dm > 0] = 0
            tr1 = pd.DataFrame(high - low); tr2 = pd.DataFrame(abs(high - close.shift(1))); tr3 = pd.DataFrame(abs(low - close.shift(1)))
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr_adx = tr.rolling(14).mean()
            plus_di = 100 * (plus_dm.ewm(alpha=1/14).mean() / atr_adx)
            minus_di = 100 * (abs(minus_dm).ewm(alpha=1/14).mean() / atr_adx)
            dx = (abs(plus_di - minus_di) / abs(plus_di + minus_di)) * 100
            curr_adx = float(dx.rolling(14).mean().iloc[-1])
        except: curr_adx = 20

        score = 0; reasons = []; details = {}
        curr_c = float(close.iloc[-1]); curr_vol = float(volume.iloc[-1])
        avg_vol = float(volume.rolling(5).mean().iloc[-1]) if len(volume) > 5 else 1.0
        
        # --- PUANLAMA (7 MADDE) ---
        
        # 1. Squeeze (Patlama Hazırlığı)
        if bb_width.iloc[-1] <= bb_width.tail(60).min() * 1.1: score += 1; reasons.append("🚀 Squeeze"); details['Squeeze'] = True
        else: details['Squeeze'] = False
        
        # 2. Trend (Kısa Vade Yükseliş)
        trend_condition = (ema5.iloc[-1] > ema20.iloc[-1] * 1.01) 
            
        if trend_condition: 
                score += 1
                reasons.append("⚡ Trend")
                details['Trend'] = True
        else: 
                details['Trend'] = False
        
        # 3. MACD (Momentum Artışı)
        if hist.iloc[-1] > hist.iloc[-2]: score += 1; reasons.append("🟢 MACD"); details['MACD'] = True
        else: details['MACD'] = False
        
        # 4. Hacim (İlgi Var mı?)
        if curr_vol > avg_vol * 1.2: score += 1; reasons.append("🔊 Hacim"); details['Hacim'] = True
        else: details['Hacim'] = False
        
        # 5. Breakout (Zirveye Yakınlık)
        if curr_c >= high.tail(20).max() * 0.98: score += 1; reasons.append("🔨 Breakout"); details['Breakout'] = True
        else: details['Breakout'] = False
        
        # 6. RSI Güçlü (İvme)
        rsi_c = float(rsi.iloc[-1])
        if 30 < rsi_c < 65 and rsi_c > float(rsi.iloc[-2]): score += 1; reasons.append("⚓ RSI Güçlü"); details['RSI Güçlü'] = (True, rsi_c)
        else: details['RSI Güçlü'] = (False, rsi_c)
        
        # 7. ADX (Trendin Gücü Yerinde mi?)
        if curr_adx > 25:
            score += 1; reasons.append(f"💪 Güçlü Trend"); details['ADX Durumu'] = (True, curr_adx)
        else:
            details['ADX Durumu'] = (False, curr_adx)

        # 8. Mansfield RS (Endekse Göre Göreceli Güç)
        mansfield_r1 = 0.0
        if bench_series is not None and len(close) > 60:
            try:
                common = close.index.intersection(bench_series.index)
                if len(common) > 55:
                    rs_r = close.reindex(common) / bench_series.reindex(common)
                    rs_m = rs_r.rolling(50).mean()
                    m = ((rs_r / rs_m) - 1) * 10
                    mansfield_r1 = float(m.iloc[-1]) if not np.isnan(m.iloc[-1]) else 0.0
            except: pass
        if mansfield_r1 > 0: score += 1; reasons.append("📈 RS Lider"); details['Mansfield RS'] = (True, mansfield_r1)
        elif mansfield_r1 < 0: details['Mansfield RS'] = (False, mansfield_r1)
        else: details['Mansfield RS'] = (False, 0.0)

        return { "Sembol": symbol, "Fiyat": f"{curr_c:.2f}", "Skor": score, "Nedenler": " | ".join(reasons), "Detaylar": details }
    except: return None


def process_single_radar2(symbol, df, idx, min_price, max_price, min_avg_vol_m):
    try:
        if df.empty or 'Close' not in df.columns: return None
        df = df.dropna(subset=['Close'])
        if len(df) < 120: return None
        
        close = df['Close']; high = df['High']; low = df['Low']
        volume = df['Volume'] if 'Volume' in df.columns else pd.Series([0]*len(df))
        curr_c = float(close.iloc[-1])
        
        # Filtreler
        if curr_c < min_price or curr_c > max_price: return None
        avg_vol_20 = float(volume.rolling(20).mean().iloc[-1])
        if avg_vol_20 < min_avg_vol_m * 1e6: return None
        
        # Trend Ortalamaları
        sma20 = close.rolling(20).mean(); sma50 = close.rolling(50).mean()
        sma100 = close.rolling(100).mean(); sma200 = close.rolling(200).mean()
        
        trend = "Yatay"
        if not np.isnan(sma200.iloc[-1]):
            if curr_c > sma50.iloc[-1] > sma100.iloc[-1] > sma200.iloc[-1] and sma200.iloc[-1] > sma200.iloc[-20]: trend = "Boğa"
            elif curr_c < sma200.iloc[-1] and sma200.iloc[-1] < sma200.iloc[-20]: trend = "Ayı"
        
        # RSI ve MACD (Sadece Setup için histogram hesabı kalıyor, puanlamadan çıkacak)
        delta = close.diff(); gain = (delta.where(delta > 0, 0)).rolling(14).mean(); loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss))); rsi_c = float(rsi.iloc[-1])
        ema12 = close.ewm(span=12, adjust=False).mean(); ema26 = close.ewm(span=26, adjust=False).mean()
        hist = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        
        # Breakout Oranı
        recent_high_60 = float(high.rolling(60).max().iloc[-1])
        breakout_ratio = curr_c / recent_high_60 if recent_high_60 > 0 else 0
        
        # Mansfield RS Skoru (Endeks)
        rs_score = 0.0
        if idx is not None and len(close) > 60 and len(idx) > 60:
            common_index = close.index.intersection(idx.index)
            if len(common_index) > 55:
                cs = close.reindex(common_index); isx = idx.reindex(common_index)
                rs_ratio = cs / isx
                rs_ma = rs_ratio.rolling(50).mean()
                mansfield = ((rs_ratio / rs_ma) - 1) * 10
                rs_score = float(mansfield.iloc[-1]) if not np.isnan(mansfield.iloc[-1]) else 0.0
        
        # --- YENİ EKLENEN: ICHIMOKU BULUTU (Kumo) ---
        # Bulut şu anki fiyatın altında mı? (Trend Desteği)
        # Ichimoku değerleri 26 periyot ileri ötelenir. Yani bugünün bulutu, 26 gün önceki verilerle çizilir.
        tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
        kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
        
        # Span A (Bugün için değeri 26 gün önceki hesaptan gelir)
        span_a_calc = (tenkan + kijun) / 2
        # Span B (Bugün için değeri 26 gün önceki hesaptan gelir)
        span_b_calc = (high.rolling(52).max() + low.rolling(52).min()) / 2
        
        # Bugünün bulut sınırları (Veri setinin sonundan 26 önceki değerler)
        cloud_a = float(span_a_calc.iloc[-26])
        cloud_b = float(span_b_calc.iloc[-26])
        is_above_cloud = curr_c > max(cloud_a, cloud_b)
        # -----------------------------------------------

        setup = "-"; tags = []; score = 0; details = {}
        avg_vol_20 = max(avg_vol_20, 1); vol_spike = volume.iloc[-1] > avg_vol_20 * 1.3
        
        # Setup Tespiti
        if trend == "Boğa" and breakout_ratio >= 0.97: setup = "Breakout"; tags.append("Zirve")
        if trend == "Boğa" and setup == "-":
            if sma20.iloc[-1] <= curr_c <= sma50.iloc[-1] * 1.02 and 40 <= rsi_c <= 55: setup = "Pullback"; tags.append("Düzeltme")
            if volume.iloc[-1] < avg_vol_20 * 0.9: score += 0; tags.append("Sığ Satış")
        if setup == "-":
            if rsi.iloc[-2] < 30 <= rsi_c and hist.iloc[-1] > hist.iloc[-2]: setup = "Dip Dönüşü"; tags.append("Dip Dönüşü")
        
        # --- PUANLAMA (7 Madde) ---
        
        # 1. Hacim Patlaması
        if vol_spike: score += 1; tags.append("Hacim+"); details['Hacim Patlaması'] = True
        else: details['Hacim Patlaması'] = False

        # 2. RS (Endeks Gücü)
        if rs_score > 0: score += 1; tags.append("RS+"); details['RS (S&P500)'] = True
        else: details['RS (S&P500)'] = False
        
        # 3. Boğa Trendi (SMA Dizilimi)
        if trend == "Boğa": score += 1; details['Boğa Trendi'] = True
        else:
            if trend == "Ayı": score -= 1
            details['Boğa Trendi'] = False
            
        # 4. Ichimoku Bulutu (YENİ - MACD YERİNE GELDİ)
        if is_above_cloud: score += 1; details['Ichimoku'] = True
        else: details['Ichimoku'] = False

        # 5. 60 Günlük Zirveye Yakınlık
        details['60G Zirve'] = breakout_ratio >= 0.90
        if details['60G Zirve']: score += 1

        # 6. RSI Uygun Bölge (Aşırı şişmemiş)
        is_rsi_suitable = (40 <= rsi_c <= 65) # Biraz genişlettim
        details['RSI Bölgesi'] = (is_rsi_suitable, rsi_c)
        if is_rsi_suitable: score += 1
        
        # 7. Setup Puanı (Yukarıda hesaplandı, max 2 puan ama biz varlığını kontrol edelim)
        # Setup varsa ekstra güvenilirdir.
        if setup != "-": score += 1
        
        # ── Darvas Kutu ───────────────────────────────────────────
        _dbox = detect_darvas_box(df)
        _d_quality = _dbox['quality']        if _dbox else None
        _d_status  = _dbox['status']         if _dbox else None
        _d_top     = _dbox['box_top']        if _dbox else None
        _d_bottom  = _dbox['box_bottom']     if _dbox else None
        _d_age     = _dbox['box_age']        if _dbox else None
        _d_class   = _dbox['breakout_class'] if _dbox else None
        _leader_profile = leadership_profile(df, idx)

        return {
            "Sembol": symbol, "Fiyat": round(curr_c, 2), "Trend": trend,
            "Setup": setup, "Skor": score, "RS": round(rs_score * 100, 1),
            "Etiketler": " | ".join(tags), "Detaylar": details,
            "Darvas_Quality": _d_quality, "Darvas_Status": _d_status,
            "Darvas_Top": _d_top, "Darvas_Bottom": _d_bottom,
            "Darvas_Age": _d_age, "Darvas_Class": _d_class,
            "Liderlik_Yasi": _leader_profile.get("leader_age", 0),
            "Liderlik_RS20": _leader_profile.get("rs20_pct", 0.0),
            "Gec_Kalma": _leader_profile.get("late_state", "VERİ YETERSİZ"),
            "Liderlik_Detay": _leader_profile.get("detail", ""),
        }
    except: return None


def calculate_guclu_donus_adaylari(ticker, df, bist100_close=None):
    """
    🔄 GÜÇLÜ DÖNÜŞ+ ADAYLARI (v9 — 7 Bağımsız Kriter, min 5/7)
    ZORUNLU  : 50 ≤ RSI ≤ 65  (bant dışı = direkt elenme)
    PUANLANAN: 7 bağımsız kriter — her biri farklı bir boyut ölçer
      P1. Fiyat > EMA13              : Kısa vade trend
      P2. EMA13 eğimi + (son 5g)     : Trend kalitesi / ivme
      P3. RS vs BIST100 > 0 (20g)    : Göreceli güç — endeksten güçlü mü?
      P4. RSI > RSI EMA9             : Momentum ivmeleniyor
      P5. OBV son 5g ↑               : Akıllı para yönü
      P6. Hacim 5+/10g ort. üstünde  : Kurumsal aktivite frekansı
      P7. Fiyat > Yıllık VWAP        : Kurumsal ortalama maliyet üstünde
    BONUS (puan ekler, zorunlu değil):
      +1   Geçen ay stop avı 🔍
      +0.5 Haftalık yükseliş ↑
    MİNİMUM: 5/7 baz puan (bonus sayılmaz)
    """
    try:
        if df is None or df.empty or len(df) < 30:
            return None

        close  = df['Close']

        if 'Volume' not in df.columns or df['Volume'].isnull().all():
            return None
        volume = df['Volume']

        # ── ZORUNLU: 50 ≤ RSI ≤ 65 ──────────────────────────────────────
        delta   = close.diff()
        gain    = delta.where(delta > 0, 0).rolling(14).mean()
        loss    = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi     = 100 - (100 / (1 + gain / loss))
        rsi_val = float(rsi.iloc[-1])
        if pd.isna(rsi_val) or rsi_val < 50 or rsi_val > 65:
            return None   # bant dışı → direkt elenme

        # ── PUANLAMA ─────────────────────────────────────────────────────
        score  = 0
        passed = []
        missed = []

        # P1: Fiyat > EMA13  [Kısa vade trend]
        ema13 = close.ewm(span=13, adjust=False).mean()
        if float(close.iloc[-1]) > float(ema13.iloc[-1]):
            score += 1; passed.append("EMA13↑")
        else:
            missed.append("EMA13")

        # P2: EMA13 eğimi pozitif (son 5 günde yükseliyor)  [Trend kalitesi]
        if len(ema13) >= 6 and float(ema13.iloc[-1]) > float(ema13.iloc[-6]):
            score += 1; passed.append("EMA eğimi↑")
        else:
            missed.append("EMA eğimi")

        # P3: Göreceli Güç — hisse son 20g BIST100'den güçlü mü?  [Farklı boyut]
        rs_pozitif = False
        rs_pct     = 0.0
        if bist100_close is not None and len(bist100_close) >= 21:
            try:
                # Ortak tarihleri hizala
                _merged = close.align(bist100_close, join='inner')
                _hisse_c, _bist_c = _merged[0], _merged[1]
                if len(_hisse_c) >= 21:
                    hisse_ret = float(_hisse_c.iloc[-1]) / float(_hisse_c.iloc[-21]) - 1
                    bist_ret  = float(_bist_c.iloc[-1])  / float(_bist_c.iloc[-21])  - 1
                    rs_pct    = round((hisse_ret - bist_ret) * 100, 1)
                    rs_pozitif = rs_pct > 2.0   # en az %2 endeks üstü güç
            except Exception:
                rs_pozitif = False
        if rs_pozitif:
            score += 1; passed.append(f"RS+{rs_pct:.1f}%")
        else:
            missed.append(f"RS({rs_pct:+.1f}%)")

        # P4: RSI > RSI EMA9 (momentum ivmeleniyor)  [Momentum]
        rsi_ema9 = rsi.ewm(span=9, adjust=False).mean()
        if float(rsi.iloc[-1]) > float(rsi_ema9.iloc[-1]):
            score += 1; passed.append("RSI ivme")
        else:
            missed.append("RSI ivme")

        # P5: OBV son 5 günde yükseliyor  [Akıllı para yönü]
        obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
        if float(obv.iloc[-1]) > float(obv.iloc[-6]):
            score += 1; passed.append("OBV↑")
        else:
            missed.append("OBV")

        # P6: Son 10 günde 5+ kez hacim ortalaması üstünde  [Kurumsal aktivite]
        avg_vol20 = volume.rolling(20).mean()
        if pd.isna(avg_vol20.iloc[-1]):
            return None
        vol_arr = volume.values
        avg_arr = avg_vol20.values
        vol_10g = sum(1 for i in range(-10, 0)
                      if not np.isnan(avg_arr[i]) and avg_arr[i] > 0
                      and float(vol_arr[i]) > float(avg_arr[i]))
        if vol_10g >= 5:
            score += 1; passed.append(f"Hacim {vol_10g}/10g")
        else:
            missed.append(f"Hacim {vol_10g}/10g")

        # P7: Fiyat > Yıllık VWAP  [Kurumsal referans fiyat]
        try:
            vwap_annual = (close * volume).cumsum() / volume.cumsum()
            vwap_val    = float(vwap_annual.iloc[-1])
            if not np.isnan(vwap_val) and float(close.iloc[-1]) > vwap_val:
                score += 1; passed.append("VWAP↑")
            else:
                missed.append("VWAP")
        except Exception:
            missed.append("VWAP")

        # ── BONUS ────────────────────────────────────────────────────────
        weekly_up   = len(close) >= 6 and float(close.iloc[-1]) > float(close.iloc[-6])
        sweep_month = False
        if len(close) >= 42:
            c1 = float(close.iloc[-42])
            c2 = float(close.iloc[-21])
            sweep_month = (c2 < c1) and (float(close.iloc[-1]) > c2)

        bonus = 0.0
        if sweep_month: bonus += 1.0
        if weekly_up:   bonus += 0.5

        total_score = score + bonus

        # ── MINIMUM PUAN KONTROLÜ (6/7 baz) ─────────────────────────────
        _MIN_SCORE = 6
        if score < _MIN_SCORE:
            return None

        # ── AÇIKLAMA ─────────────────────────────────────────────────────
        hacim_kat = round(float(vol_arr[-1]) / float(avg_arr[-1]), 2) if float(avg_arr[-1]) > 0 else 0.0

        _ac_parts = []
        if sweep_month:
            _ac_parts.append("⚡ stop avı")
        _ac_parts.append(f"RSI {rsi_val:.0f} · {score}/7 kriter")
        _ac_parts.append(" + ".join(passed))
        if missed:
            _ac_parts.append(f"eksik: {', '.join(missed)}")
        if weekly_up:
            _ac_parts.append("haftalık ↑")

        return {
            "Sembol"     : ticker,
            "Fiyat"      : float(close.iloc[-1]),
            "RSI"        : round(rsi_val, 1),
            "Skor"       : score,
            "ToplamSkor" : total_score,
            "RS_Pct"     : rs_pct,
            "Z-Score"    : 0.0,
            "Z_Cheap"    : False,
            "Hacim_Kat"  : hacim_kat,
            "Hacim_10g"  : vol_10g,
            "Sweep_Ay"   : sweep_month,
            "Weekly_Up"  : weekly_up,
            "RSI_Div"    : "✅",
            "Passed"     : passed,
            "Missed"     : missed,
            "Aciklama"   : " · ".join(_ac_parts),
        }

    except Exception:
        return None


def calculate_prelaunch_bos(ticker, df, bist100_close=None):
    """
    Sert Eleme:
      1. Son 3 gün içinde 45-günlük swing high kırıldı (BOS)
      2. BOS öncesi 15-25 günde ≥5 gün Bollinger/Keltner sıkışması (Squeeze)
    Puanlama (100 üzerinden, min geçer: 55):
      Hacim BOS günü ≥1.5x→+25, 1.2-1.5x→+15
      RS > BIST100 (10g)→+20
      RSI 50-65→+20, 65-70→+10, >70→ELENME
      BOS'tan uzaklık <3%→+20, 3-6%→+10
      SMA50 üzerinde→+15
      BOS Day0→0, Day1→-5, Day2→-10, Day3→-15
    """
    try:
        if df is None or len(df) < 50:
            return None

        close  = df['Close']
        high   = df['High']
        volume = df['Volume']

        # ── 1. BOS: Son 3 günde 45-günlük swing high kırıldı mı? ──────────
        bos_day  = None
        bos_level = None
        for lookback in range(1, 4):          # Day 0=bugün, Day1, Day2, Day3
            idx = -(lookback)
            swing_window = close.iloc[:idx - 1] if abs(idx - 1) < len(close) else close.iloc[:-1]
            if len(swing_window) < 45:
                continue
            swing_high_45 = swing_window.iloc[-45:].max()
            if close.iloc[idx] > swing_high_45:
                bos_day   = lookback - 1     # 0-indexed
                bos_level = swing_high_45
                break

        if bos_day is None:
            return None

        # ── 2. Squeeze: BOS öncesi 15-25 günde ≥5 gün sıkışma ────────────
        pre_bos_end   = -(bos_day + 1) if bos_day > 0 else -1
        pre_bos_start = pre_bos_end - 25

        sq_slice_close = close.iloc[pre_bos_start:pre_bos_end] if pre_bos_end != 0 else close.iloc[pre_bos_start:]
        sq_slice_high  = high.iloc[pre_bos_start:pre_bos_end]  if pre_bos_end != 0 else high.iloc[pre_bos_start:]
        sq_slice_low   = df['Low'].iloc[pre_bos_start:pre_bos_end] if pre_bos_end != 0 else df['Low'].iloc[pre_bos_start:]

        if len(sq_slice_close) < 10:
            return None

        # Bollinger Bands (10 günlük — 25 günlük dilimden yeterli geçerli değer üretir)
        bb_mid = sq_slice_close.rolling(10).mean()
        bb_std = sq_slice_close.rolling(10).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_width = (bb_upper - bb_lower) / bb_mid.replace(0, 1e-9)

        # Keltner Channel
        atr_sq = (sq_slice_high - sq_slice_low).rolling(5).mean()
        kc_width = (2 * atr_sq) / bb_mid.replace(0, 1e-9)

        squeeze_days = int((bb_width < kc_width).sum())
        if squeeze_days < 3:
            return None

        # ── 3. RSI: >70 elenme ────────────────────────────────────────────
        delta  = close.diff()
        gain   = delta.where(delta > 0, 0).rolling(14).mean()
        loss   = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi_val = float((100 - 100 / (1 + gain / loss.replace(0, 1e-9))).iloc[-1])

        if rsi_val > 70:
            return None

        # ── 4. Puanlama ───────────────────────────────────────────────────
        score = 0
        aciklama_parts = []

        # Hacim (BOS günü)
        bos_idx    = -(bos_day + 1) if bos_day > 0 else -1
        vol_bos    = float(volume.iloc[bos_idx])
        vol_avg20  = float(volume.iloc[-25:-5].mean()) if len(volume) >= 25 else float(volume.mean())
        vol_ratio  = vol_bos / vol_avg20 if vol_avg20 > 0 else 0
        if vol_ratio >= 1.5:
            score += 25
            aciklama_parts.append(f"Hacim {vol_ratio:.1f}x")
        elif vol_ratio >= 1.2:
            score += 15
            aciklama_parts.append(f"Hacim {vol_ratio:.1f}x")
        else:
            aciklama_parts.append(f"Hacim {vol_ratio:.1f}x (zayıf)")

        # RS vs BIST100 (son 10 gün)
        if bist100_close is not None and len(bist100_close) >= 11:
            stock_ret = float(close.iloc[-1] / close.iloc[-11] - 1)
            idx_ret   = float(bist100_close.iloc[-1] / bist100_close.iloc[-11] - 1)
            rs_diff   = (stock_ret - idx_ret) * 100
            if stock_ret > idx_ret:
                score += 20
                aciklama_parts.append(f"RS+{rs_diff:.1f}%")
        else:
            rs_diff = 0.0

        # RSI
        if rsi_val <= 65:
            score += 20
        elif rsi_val <= 70:
            score += 10
        aciklama_parts.append(f"RSI {rsi_val:.0f}")

        # BOS'tan uzaklık
        current_price = float(close.iloc[-1])
        bos_dist_pct  = (current_price - bos_level) / bos_level * 100
        if bos_dist_pct < 3:
            score += 20
            aciklama_parts.append(f"BOS yakın +{bos_dist_pct:.1f}%")
        elif bos_dist_pct < 6:
            score += 10
            aciklama_parts.append(f"BOS +{bos_dist_pct:.1f}%")
        else:
            aciklama_parts.append(f"BOS uzak +{bos_dist_pct:.1f}%")

        # SMA50
        sma50 = float(close.rolling(50).mean().iloc[-1])
        if current_price > sma50:
            score += 15

        # BOS Day cezası
        score -= bos_day * 5

        if score < 55:
            return None

        return {
            'Sembol':      ticker,
            'Fiyat':       round(current_price, 2),
            'Skor':        min(score, 100),
            'BOS_Day':     bos_day,
            'BOS_Level':   round(float(bos_level), 2),
            'Hacim_Kat':   round(vol_ratio, 2),
            'RSI':         round(rsi_val, 1),
            'RS_Pct':      round(rs_diff if bist100_close is not None else 0.0, 2),
            'Squeeze_Gun': squeeze_days,
            'BOS_Dist':    round(bos_dist_pct, 2),
            'Aciklama':    ' · '.join(aciklama_parts),
        }

    except Exception:
        return None


def process_single_ict_setup(symbol, df):
    """
    ICT Sniper — BIST Günlük Uyarlaması
    Klasik ICT'nin BIST günlük verisinde çalışan özü:
      1. SMA50 üstünde (kısa vade trend sağlam)
      2. Son 30 barda swing low sweep + recovery (stop avı + dönüş)
      3. Sweep sonrası kapanış swept seviyenin üstünde
      4. Son 10 günde pozitif momentum (close bugün > close 10 gün önce)
      5. RRR ≥ 1.5 (yakın direnç hedef)
    """
    try:
        if df.empty or len(df) < 60: return None

        close = df['Close']; high = df['High']; low = df['Low']
        current_price = float(close.iloc[-1])
        n = len(df)

        # ── 1. TREND FİLTRESİ: SMA50 üstünde olmalı ──────────────────────────
        sma50 = float(close.rolling(50).mean().iloc[-1])
        if current_price <= sma50: return None

        # ── 2. MOMENTUM: Son 10 günde yükseliş ────────────────────────────────
        if float(close.iloc[-1]) <= float(close.iloc[-6]): return None

        # ── 3. HACİM HAZIRLIĞI: 20 günlük ortalama hacim ─────────────────────
        has_vol = 'Volume' in df.columns and not df['Volume'].isnull().all()
        vol_arr = df['Volume'].values if has_vol else None
        avg_vol_arr = df['Volume'].rolling(20).mean().values if has_vol else None

        # ── 4. REFERANS DIPLARI: Pencere [-90:-30] arası swing low'lar ────────
        #    (sweep taramasının dışında kalan "eski dip" seviyeleri)
        ref_lows = []
        lookback = 5  # her yanda 5 bar
        ref_start = max(lookback, n - 90)
        ref_end   = max(lookback, n - 30)
        low_arr   = low.values
        high_arr  = high.values
        for i in range(ref_start, ref_end):
            lo = float(low_arr[i])
            window = low_arr[max(0, i - lookback): i + lookback + 1]
            if lo <= float(window.min()) + 1e-9:
                ref_lows.append((i, lo))

        if not ref_lows: return None

        # ── 5. SWEEP + RECOVERY + HACİM TEYİDİ: Son 30 barda ────────────────
        #    a) Herhangi bir ref_low'un altına inilmiş (sweep)
        #    b) Sweep gününde hacim 20g ort. × 1.5 üstünde (kurumsal baskı)
        #    c) O gün veya sonrasında kapanış swept seviyenin ÜSTÜNDE (recovery)
        sweep_found   = False
        sweep_ref_low = None
        sweep_bar_idx = None

        for ref_i, ref_lo in ref_lows:
            sweep_window_start = n - 30
            for j in range(sweep_window_start, n):
                if float(low_arr[j]) < ref_lo:           # sweep gerçekleşti
                    # Hacim teyidi: sweep gününde yeterli hacim var mı?
                    if has_vol and avg_vol_arr is not None:
                        avg_v = float(avg_vol_arr[j]) if not np.isnan(avg_vol_arr[j]) else 0
                        sweep_vol = float(vol_arr[j])
                        if avg_v > 0 and sweep_vol < avg_v * 2.0:
                            continue                       # Hacimsiz sweep → geçersiz
                    # Recovery: j veya sonraki barlarda kapanış > ref_lo
                    for k in range(j, min(j + 6, n)):
                        if float(close.iloc[k]) > ref_lo:
                            sweep_found   = True
                            sweep_ref_low = ref_lo
                            sweep_bar_idx = k
                            break
                if sweep_found: break
            if sweep_found: break

        if not sweep_found: return None

        # ── 5. STOP / HEDEF / RRR ─────────────────────────────────────────────
        # ATR tabanlı dinamik stop (sabit % yerine volatiliteye göre)
        atr_period = 14
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs()
        ], axis=1).max(axis=1)
        atr14 = float(tr.rolling(atr_period).mean().iloc[-1])

        # Stop: sweep seviyesinin altı — en az 1.0×ATR, en fazla 2.0×ATR mesafe
        atr_buffer  = max(atr14 * 1.0, min(atr14 * 2.0, sweep_ref_low * 0.02))
        stop_loss   = sweep_ref_low - atr_buffer
        entry_price = current_price
        risk        = entry_price - stop_loss
        if risk <= 0: return None

        # Hedef: mevcut fiyatın üzerindeki en yakın swing high (son 90 bar)
        sw_highs = []
        for i in range(max(lookback, n - 90), n - lookback):
            hi = float(high_arr[i])
            window = high_arr[max(0, i - lookback): i + lookback + 1]
            if hi >= float(window.max()) - 1e-9:
                sw_highs.append(hi)

        targets = [h for h in sw_highs if h > entry_price * 1.01]
        if not targets: return None
        target_price = min(targets)                  # En yakın direnç

        rrr = (target_price - entry_price) / risk
        if rrr < 2.0: return None

        # ── 6. SKOR & AÇIKLAMA ───────────────────────────────────────────────
        skor = 75
        if rrr >= 2.5: skor += 15
        elif rrr >= 2.0: skor += 10
        elif rrr >= 1.5: skor += 5
        skor = min(100, skor)

        days_since_sweep = n - 1 - sweep_bar_idx
        aciklama = (
            f"Stop avı yapıldı ({days_since_sweep} gün önce), fiyat toparlandı ve "
            f"SMA50 üstünde momentum devam ediyor — giriş bölgesinde set-up hazır"
        )

        return {
            "Sembol":    symbol,
            "Fiyat":     current_price,
            "Yön":       "YÜKSELİŞ YÖNLÜ",
            "İkon":      "🎯",
            "Renk":      "#16a34a",
            "Durum":     f"RRR: {rrr:.1f} | Stop: {stop_loss:.2f} | Hedef: {target_price:.2f}",
            "Aciklama":  aciklama,
            "Stop_Loss": f"{stop_loss:.2f}",
            "Skor":      skor
        }

    except Exception:
        return None


def _nadir_firsat_single_fast(symbol, df, bench_series=None):
    """
    Tek sembol için Royal Flush kontrolü — batch DataFrame'den inline hesaplar.
    calculate_ict_deep_analysis / calculate_price_action_dna çağırmaz → hızlı.
    5/5 Kriter:
      1. BOS : son lokal swing high kırıldı (bullish yapı)
      2. RS  : ALPHA > %1.5 (hisse 20g getirisi − endeks 20g getirisi)
              FIX (31 May 2026): Eskiden mutlak getiri kontrol ediliyordu (yanlış);
              şimdi endeks farkı (gerçek outperform).
      3. VWAP sapması < %10
      4. Hacim canlanması (3-gün veya 2-gün patlaması)
      5. RSI < 65
    """
    try:
        if df is None or df.empty or len(df) < 60:
            return None

        close = df['Close']; high = df['High']
        low   = df['Low'];   vol  = df['Volume']
        curr  = float(close.iloc[-1])

        # ── 1. BOS: son swing high'ı kır ──
        sh = None
        for i in range(len(close) - 3, max(len(close) - 61, 2), -1):
            if (high.iloc[i] > high.iloc[i-1] and high.iloc[i] > high.iloc[i-2]
                    and high.iloc[i] > high.iloc[i+1] and high.iloc[i] > high.iloc[i+2]):
                sh = float(high.iloc[i])
                break
        if sh is None or curr <= sh:
            return None

        # ── 2. RS Alpha: hisse 20g − endeks 20g getirisi > %1.5 (gerçek outperform) ──
        if len(close) < 21:
            return None
        stock_ret20 = (float(close.iloc[-1]) / float(close.iloc[-21]) - 1) * 100
        # Endeks verisi yoksa sinyali reddet — RS kanıtı olmadan Royal Flush olmaz
        if bench_series is None or len(bench_series) < 21:
            return None
        try:
            common_idx = close.index.intersection(bench_series.index)
            if len(common_idx) < 21:
                return None
            _b = bench_series.loc[common_idx]
            bench_ret20 = (float(_b.iloc[-1]) / float(_b.iloc[-21]) - 1) * 100
        except Exception:
            return None
        alpha = stock_ret20 - bench_ret20
        if alpha <= 1.5:
            return None

        # ── 3. VWAP sapması < %10 ──
        typical  = (high + low + close) / 3
        vwap_val = float(
            (typical * vol).rolling(20).sum().iloc[-1] /
            vol.rolling(20).sum().iloc[-1]
        ) if vol.rolling(20).sum().iloc[-1] > 0 else 0
        if vwap_val <= 0:
            return None
        vwap_diff = (curr - vwap_val) / vwap_val * 100
        if vwap_diff >= 10:
            return None

        # ── 4. Hacim canlanması ──
        if len(vol) < 22:
            return None
        ort20    = vol.iloc[-22:-2].mean()
        son3_ort = vol.iloc[-3:].mean()
        son2_ort = vol.iloc[-2:].mean()
        onc5_ort = vol.iloc[-7:-2].mean()
        if not ((son3_ort > ort20 * 1.3) or (son2_ort > onc5_ort * 1.3)):
            return None

        # ── 5. RSI < 65 ──
        dd  = close.diff()
        gg  = dd.where(dd > 0, 0).rolling(14).mean()
        ll  = (-dd.where(dd < 0, 0)).rolling(14).mean()
        rsi = float((100 - (100 / (1 + gg / ll))).iloc[-1])
        if rsi >= 65:
            return None

        # ── 6. 52H KONUM (FIX 31 May 2026) ──
        # Royal Flush mantığı: "henüz hareket etmemiş hisseye 5 dizilim geldi".
        # Yıllık zirveye çok yakınsa hisse ZATEN yukarıda → bu fırsat değil,
        # trend takibi. EGEPO/RYSAS gibi yıllık zirvede tetiklenmeleri eler.
        year_window = close.iloc[-252:] if len(close) >= 252 else close
        year_high   = float(year_window.max())
        if year_high > 0 and (curr / year_high) > 0.85:
            return None

        # Yıllık konum % (bilgi amaçlı çıktıya yazılır)
        year_low = float(year_window.min())
        yearly_pos_pct = ((curr - year_low) / (year_high - year_low) * 100) if year_high > year_low else 50

        return {
            'Sembol':   symbol,
            'Fiyat':    round(curr, 2),
            'Durum':    '6/6 | BOS+Alpha+VWAP+VOL+RSI+52H',
            'Aciklama': (
                f"BOS yapı kırılımı teyitli · "
                f"Alpha +{alpha:.1f}% (hisse {stock_ret20:+.1f}% vs endeks {bench_ret20:+.1f}%) · "
                f"VWAP sapması %{vwap_diff:.1f} (şişmemiş) · "
                f"RSI {rsi:.0f} (aşırı alım yok) · "
                f"Hacim canlandı · "
                f"52H konum %{yearly_pos_pct:.0f} (zirvenin %15+ uzağında, fırsat penceresi)"
            ),
        }
    except Exception:
        return None


# ============================================================================
# ADIM 6b (7 Tem 2026) — ERKEN RADAR AİLESİ (app.py'den BİREBİR taşındı)
# Senaryo sözlüğü + ~40 yapı-taşı yardımcısı + evaluate_erken_radar +
# _er_build_context + _er_kisa_aciklama. Toplu döngü (scan_erken_radar_batch)
# hâlâ app.py'de (veri katmanına bağlı — Adım 6c).
# ============================================================================
from db_layer import log_error

def _er_kisa_aciklama(scenario_id):
    """Senaryonun sade açıklaması — kod bilmeyen son kullanıcının anlayacağı dilde.
    Örn. B8 → 'Hisse 10+ gündür çok dar bantta sıkışıyor ama son 3 günde hacim canlanmaya başladı.'
    İlk cümle çok kısaysa (genel) ikinci cümleyi de ekler."""
    try:
        d = ERKEN_RADAR_SCENARIOS.get(str(scenario_id), {}).get('description', '')
        if not d:
            return ''
        parts = [p.strip() for p in d.replace(' — ', '. ').split('. ') if p.strip()]
        if not parts:
            return ''
        out = parts[0]
        if len(out) < 45 and len(parts) > 1:   # ilk cümle genel/kısaysa ikinciyi de al
            out = out.rstrip('.') + '. ' + parts[1]
        if not out.endswith(('.', '?', '!')):
            out += '.'
        return out
    except Exception:
        return ''


def _er_rsi(close, period=14):
    """Wilder RSI."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def _er_swing_lows(low_series, lookback=30):
    """Son N gün içinde swing low'ları bul. Pivot: 2 yan + 2 yan. (numpy hızlı)"""
    try:
        arr = low_series.values if hasattr(low_series, 'values') else np.asarray(low_series)
    except Exception:
        return []
    n = len(arr)
    start = max(2, n - lookback)
    lows = []
    for i in range(start, n - 2):
        v = arr[i]
        if v < arr[i-1] and v < arr[i-2] and v < arr[i+1] and v < arr[i+2]:
            lows.append((i, float(v)))
    return lows


def _er_swing_highs(high_series, lookback=30):
    """Son N gün içinde swing high'ları bul. (numpy hızlı)"""
    try:
        arr = high_series.values if hasattr(high_series, 'values') else np.asarray(high_series)
    except Exception:
        return []
    n = len(arr)
    start = max(2, n - lookback)
    highs = []
    for i in range(start, n - 2):
        v = arr[i]
        if v > arr[i-1] and v > arr[i-2] and v > arr[i+1] and v > arr[i+2]:
            highs.append((i, float(v)))
    return highs


def _er_strong_bullish_div(low_series, rsi_series):
    """Güçlü Pozitif: Fiyat LL + RSI HL"""
    lows = _er_swing_lows(low_series, 30)
    if len(lows) < 2:
        return False
    p1_idx, p1_val = lows[-2]; p2_idx, p2_val = lows[-1]
    if p2_val >= p1_val:
        return False
    try:
        return float(rsi_series.iloc[p2_idx]) > float(rsi_series.iloc[p1_idx])
    except Exception:
        return False


def _er_medium_bullish_div(low_series, rsi_series, tol=0.02):
    """Orta Pozitif: Fiyat yatay (±2%) + RSI HL"""
    lows = _er_swing_lows(low_series, 30)
    if len(lows) < 2:
        return False
    p1_idx, p1_val = lows[-2]; p2_idx, p2_val = lows[-1]
    if p1_val <= 0:
        return False
    if abs(p2_val - p1_val) / p1_val > tol:
        return False
    try:
        return float(rsi_series.iloc[p2_idx]) > float(rsi_series.iloc[p1_idx])
    except Exception:
        return False


def _er_weak_bullish_div(low_series, rsi_series, rsi_tol=2.0):
    """Zayıf Pozitif: Fiyat LL + RSI yatay (±2 puan)"""
    lows = _er_swing_lows(low_series, 30)
    if len(lows) < 2:
        return False
    p1_idx, p1_val = lows[-2]; p2_idx, p2_val = lows[-1]
    if p2_val >= p1_val:
        return False
    try:
        return abs(float(rsi_series.iloc[p2_idx]) - float(rsi_series.iloc[p1_idx])) <= rsi_tol
    except Exception:
        return False


def _er_bearish_div(high_series, rsi_series):
    """Negatif: Fiyat HH + RSI LH"""
    highs = _er_swing_highs(high_series, 30)
    if len(highs) < 2:
        return False
    p1_idx, p1_val = highs[-2]; p2_idx, p2_val = highs[-1]
    if p2_val <= p1_val:
        return False
    try:
        return float(rsi_series.iloc[p2_idx]) < float(rsi_series.iloc[p1_idx])
    except Exception:
        return False


def _er_volume_dried(volume):
    """Son 5g < 20g ort × 0.80"""
    if len(volume) < 21:
        return False
    v5 = float(volume.iloc[-5:].mean())
    v20 = float(volume.rolling(20).mean().iloc[-1])
    return v20 > 0 and v5 < v20 * 0.80


def _er_volume_rising_slow(volume):
    """Son 10g trend pozitif, %5-30 artış (linear regression)"""
    if len(volume) < 11:
        return False
    try:
        v_recent = volume.iloc[-10:].values
        x = np.arange(10)
        slope, _ = np.polyfit(x, v_recent, 1)
        avg = float(v_recent.mean())
        if avg <= 0:
            return False
        rise_pct = (slope * 10) / avg
        return 0.05 <= rise_pct <= 0.30
    except Exception:
        return False


def _er_pocket_pivot(df):
    """Yeşil mum + hacim > son 10g'nin en yüksek kırmızı mum hacmi (numpy)"""
    if len(df) < 11:
        return False
    try:
        closes = df['Close'].values
        opens  = df['Open'].values
        vols   = df['Volume'].values
        today_c, today_o, today_v = closes[-1], opens[-1], vols[-1]
        if today_c <= today_o:
            return False
        # Son 10 gün (bugün hariç)
        c10, o10, v10 = closes[-11:-1], opens[-11:-1], vols[-11:-1]
        red_mask = c10 < o10
        if not red_mask.any():
            return today_v > float(v10.max())
        return today_v > float(v10[red_mask].max())
    except Exception:
        return False


def _er_distribution_day(df, day_offset=-1):
    """Belirtilen günde distribution day mi? Kırmızı + hacim 20g × 1.5+ (numpy)"""
    if len(df) < 21 + abs(day_offset):
        return False
    try:
        closes = df['Close'].values
        opens  = df['Open'].values
        vols   = df['Volume'].values
        c, o, v = closes[day_offset], opens[day_offset], vols[day_offset]
        end = len(df) + day_offset
        start = end - 20
        if start < 0:
            return False
        va = float(vols[start:end].mean())
        return c < o and v > va * 1.5
    except Exception:
        return False


def _er_distribution_count(df, days=5):
    """Son N günde distribution day sayısı (numpy vektorize)"""
    if len(df) < 21:
        return 0
    try:
        closes = df['Close'].values
        opens  = df['Open'].values
        vols   = df['Volume'].values
        n = len(df)
        count = 0
        for i in range(-days, 0):
            end = n + i
            start = end - 20
            if start < 0:
                continue
            c, o, v = closes[i], opens[i], vols[i]
            va = vols[start:end].mean()
            if c < o and v > va * 1.5:
                count += 1
        return count
    except Exception:
        return 0


def _er_updown_vol_ratio(df, days=20):
    """Yeşil mum hacim toplamı / kırmızı mum hacim toplamı"""
    if len(df) < days:
        return 1.0
    try:
        recent = df.iloc[-days:]
        green = recent[recent['Close'] > recent['Open']]
        red = recent[recent['Close'] < recent['Open']]
        g_v = float(green['Volume'].sum()) if not green.empty else 0
        r_v = float(red['Volume'].sum()) if not red.empty else 0
        if r_v <= 0:
            return 99.0 if g_v > 0 else 1.0
        return g_v / r_v
    except Exception:
        return 1.0


def _er_bb_width(close, period=20):
    """BB width = (4*std) / mid — 2 üst + 2 alt"""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return (std * 4) / mid


def _er_bb_squeeze(close, threshold=0.85):
    """BB width şu an 20g ortalamanın altında mı (threshold × avg)"""
    if len(close) < 41:
        return False
    try:
        bbw = _er_bb_width(close)
        wn = float(bbw.iloc[-1]); wa = float(bbw.rolling(20).mean().iloc[-1])
        return wa > 0 and wn < wa * threshold
    except Exception:
        return False


def _er_bb_squeeze_days(close, threshold=0.85):
    """Kaç gündür BB sıkışma aktif"""
    if len(close) < 41:
        return 0
    try:
        bbw = _er_bb_width(close)
        wa_series = bbw.rolling(20).mean()
        days = 0
        for i in range(len(bbw) - 1, -1, -1):
            wn = float(bbw.iloc[i]); wa = float(wa_series.iloc[i])
            if wa > 0 and wn < wa * threshold:
                days += 1
            else:
                break
        return days
    except Exception:
        return 0


def _er_atr_contracting(df, threshold=0.7):
    """ATR son 5g < önceki 20g'nin %X'i"""
    if len(df) < 26:
        return False
    try:
        h = df['High']; l = df['Low']; c = df['Close']
        tr1 = h - l
        tr2 = (h - c.shift()).abs()
        tr3 = (l - c.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr5 = float(tr.iloc[-5:].mean())
        atr20 = float(tr.iloc[-25:-5].mean())
        return atr20 > 0 and atr5 < atr20 * threshold
    except Exception:
        return False


def _er_position_60(df):
    """60g range içinde fiyatın yüzdelik konumu (0-1)"""
    if len(df) < 60:
        return 0.5
    try:
        h60 = float(df['High'].iloc[-60:].max())
        l60 = float(df['Low'].iloc[-60:].min())
        rng = h60 - l60
        if rng <= 0:
            return 0.5
        return (float(df['Close'].iloc[-1]) - l60) / rng
    except Exception:
        return 0.5


def _er_recent_fall(df, lookback=60, threshold=0.15):
    """Son N günde belirli %'ten fazla düştü mü (zirveden)"""
    if len(df) < lookback:
        return False
    try:
        h = float(df['High'].iloc[-lookback:].max())
        c = float(df['Close'].iloc[-1])
        return h > 0 and (h - c) / h > threshold
    except Exception:
        return False


def _er_fall_stopped(df, days=10):
    """Son N gün yatay/hafif yukarı"""
    if len(df) < days:
        return False
    try:
        c_r = df['Close'].iloc[-days:]
        change = (float(c_r.iloc[-1]) / float(c_r.iloc[0])) - 1
        return change > -0.05
    except Exception:
        return False


def _er_lateral_range(df, days=20, threshold=0.10):
    """Son N gün range darlığı"""
    if len(df) < days:
        return False
    try:
        last = df.iloc[-days:]
        h = float(last['High'].max()); l = float(last['Low'].min())
        return l > 0 and (h - l) / l < threshold
    except Exception:
        return False


def _er_above_sma(df, period):
    if len(df) < period:
        return False
    try:
        sma = df['Close'].rolling(period).mean()
        return float(df['Close'].iloc[-1]) > float(sma.iloc[-1])
    except Exception:
        return False


def _er_sma50_rising(df):
    if len(df) < 56:
        return False
    try:
        sma50 = df['Close'].rolling(50).mean()
        return float(sma50.iloc[-1]) > float(sma50.iloc[-6])
    except Exception:
        return False


def _er_pullback(df, max_pct=0.08, min_pct=0.03, recent_days=10):
    """Son N günde belirli aralıkta pullback yapmış mı (zirveden)"""
    if len(df) < recent_days:
        return False
    try:
        h = float(df['High'].iloc[-recent_days:].max())
        c = float(df['Close'].iloc[-1])
        if h <= 0:
            return False
        pb = (h - c) / h
        return min_pct <= pb <= max_pct
    except Exception:
        return False


def _er_pullback_to_sma(df, period, tol=0.02):
    """Fiyat SMA(period)'a ±tol içinde yakın mı"""
    if len(df) < period:
        return False
    try:
        sma = float(df['Close'].rolling(period).mean().iloc[-1])
        c = float(df['Close'].iloc[-1])
        return sma > 0 and abs(c - sma) / sma < tol
    except Exception:
        return False


def _er_double_bottom(df, lookback=40, tolerance=0.04):
    """Çift dip: 2 swing low yakın seviyede"""
    if len(df) < lookback:
        return False
    lows = _er_swing_lows(df['Low'], lookback=lookback)
    if len(lows) < 2:
        return False
    p1_idx, p1_val = lows[-2]; p2_idx, p2_val = lows[-1]
    if p2_idx - p1_idx < 5 or p2_idx - p1_idx > 35:
        return False
    if p1_val <= 0:
        return False
    return abs(p2_val - p1_val) / p1_val < tolerance


def _er_flag_pattern(df):
    """Bayrak: önceki 10g sert yükseliş + son 5g yatay/hafif aşağı"""
    if len(df) < 16:
        return False
    try:
        earlier = float(df['Close'].iloc[-16])
        flag_start = float(df['Close'].iloc[-6])
        current = float(df['Close'].iloc[-1])
        if earlier <= 0 or flag_start <= 0:
            return False
        flagpole = (flag_start - earlier) / earlier
        flag_move = (current - flag_start) / flag_start
        return flagpole > 0.10 and -0.05 < flag_move < 0.02
    except Exception:
        return False


def _er_triangle_contraction(df, lookback=20):
    """Üçgen: HH azalıyor + LL artıyor (son N gün içinde)"""
    if len(df) < lookback:
        return False
    try:
        recent_high = df['High'].iloc[-lookback:]
        recent_low = df['Low'].iloc[-lookback:]
        highs = _er_swing_highs(recent_high, lookback=lookback)
        lows = _er_swing_lows(recent_low, lookback=lookback)
        if len(highs) < 2 or len(lows) < 2:
            return False
        h1 = highs[-2][1]; h2 = highs[-1][1]
        l1 = lows[-2][1]; l2 = lows[-1][1]
        return h2 < h1 and l2 > l1
    except Exception:
        return False


def _er_rs_pct(close, bench_close, days, _aligned=None):
    """RS farkı (yüzde): hisse getirisi - endeks getirisi.
    _aligned: pre-aligned (s_arr, b_arr) numpy tuple (opsiyonel, hızlı yol)"""
    if _aligned is not None:
        s_arr, b_arr = _aligned
        n = len(s_arr)
        if n < days+1:
            return 0.0
        try:
            sr = (s_arr[-1] / s_arr[-days-1]) - 1
            br = (b_arr[-1] / b_arr[-days-1]) - 1
            return float((sr - br) * 100)
        except Exception:
            return 0.0
    # Fallback (eski yol, scan_erken_radar_batch dışından çağrılırsa)
    if bench_close is None or len(close) < days+1 or len(bench_close) < days+1:
        return 0.0
    try:
        common = close.index.intersection(bench_close.index)
        if len(common) < days+1:
            return 0.0
        s = close.loc[common]; b = bench_close.loc[common]
        sr = (float(s.iloc[-1]) / float(s.iloc[-days-1])) - 1
        br = (float(b.iloc[-1]) / float(b.iloc[-days-1])) - 1
        return (sr - br) * 100
    except Exception:
        return 0.0


def _er_rs_new_high(close, bench_close, lookback=20):
    """RS oranı son N gün'ün en yüksek %98'inde"""
    if bench_close is None:
        return False
    try:
        common = close.index.intersection(bench_close.index)
        if len(common) < lookback:
            return False
        rs_ratio = close.loc[common] / bench_close.loc[common]
        if len(rs_ratio) < lookback:
            return False
        recent_max = float(rs_ratio.iloc[-lookback:].max())
        current = float(rs_ratio.iloc[-1])
        return recent_max > 0 and current >= recent_max * 0.98
    except Exception:
        return False


def _er_index_falling(bench_close, days=10):
    """Endeks son N gün'de düşmüş"""
    if bench_close is None or len(bench_close) < days+1:
        return False
    try:
        return float(bench_close.iloc[-1]) < float(bench_close.iloc[-days-1])
    except Exception:
        return False


def _er_build_context(df, bench_df=None):
    """Tüm sinyalleri tek seferde hesapla, dict döner. RS için bench bir kez align edilir."""
    if df is None or len(df) < 60:
        return None
    try:
        close = df['Close']
        volume = df['Volume']
        low = df['Low']
        high = df['High']
        rsi_series = _er_rsi(close)
        rsi_now = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0
        bench_close = bench_df['Close'] if bench_df is not None else None
        pos60 = _er_position_60(df)

        # ── RS HIZLI YOL: bench'i bir kez align et, numpy array olarak sakla ──
        _aligned = None
        _rs_ratio_arr = None
        if bench_close is not None:
            try:
                _common = close.index.intersection(bench_close.index)
                if len(_common) >= 21:
                    _s_arr = close.loc[_common].values.astype(float)
                    _b_arr = bench_close.loc[_common].values.astype(float)
                    _aligned = (_s_arr, _b_arr)
                    # RS ratio (Mansfield ham veri) — _er_rs_new_high için
                    _rs_ratio_arr = _s_arr / _b_arr
            except Exception:
                _aligned = None

        return {
            # RSI zonları
            'rsi_now': rsi_now,
            'rsi_25_35': 25 <= rsi_now <= 35,
            'rsi_30_45': 30 <= rsi_now <= 45,
            'rsi_30_50': 30 <= rsi_now <= 50,
            'rsi_35_50': 35 <= rsi_now <= 50,
            'rsi_40_55': 40 <= rsi_now <= 55,
            'rsi_42_58': 42 <= rsi_now <= 58,
            'rsi_45_55': 45 <= rsi_now <= 55,
            'rsi_45_60': 45 <= rsi_now <= 60,
            'rsi_45_65': 45 <= rsi_now <= 65,
            'rsi_50_60': 50 <= rsi_now <= 60,
            'rsi_50_65': 50 <= rsi_now <= 65,
            # Trend
            'above_sma20': _er_above_sma(df, 20),
            'above_sma50': _er_above_sma(df, 50),
            'sma50_rising': _er_sma50_rising(df),
            # Hacim
            'vol_dried': _er_volume_dried(volume),
            'vol_rising_slow': _er_volume_rising_slow(volume),
            'pocket_pivot': _er_pocket_pivot(df),
            'distribution_day': _er_distribution_day(df, -1),
            'distribution_count_5d': _er_distribution_count(df, 5),
            'updown_ratio_bullish': _er_updown_vol_ratio(df) > 1.2,
            # Volatilite
            'bb_squeeze': _er_bb_squeeze(close),
            'bb_squeeze_days': _er_bb_squeeze_days(close),
            'atr_contracting': _er_atr_contracting(df),
            # Konum
            'pos_60': pos60,
            'pos_60_lower_30': pos60 < 0.30,
            'pos_60_lower_50': pos60 < 0.50,
            'pos_60_upper_80': pos60 > 0.80,
            'pos_60_mid': 0.40 < pos60 < 0.70,
            'recent_fall_15': _er_recent_fall(df, 60, 0.15),
            'recent_fall_25': _er_recent_fall(df, 60, 0.25),
            'fall_stopped': _er_fall_stopped(df),
            # Yapı
            'lateral_20': _er_lateral_range(df, 20, 0.10),
            'lateral_10': _er_lateral_range(df, 10, 0.06),
            'pullback_3_8': _er_pullback(df, 0.08, 0.03, 10),
            'pullback_to_sma20': _er_pullback_to_sma(df, 20, 0.02),
            'pullback_to_sma50': _er_pullback_to_sma(df, 50, 0.03),
            'double_bottom': _er_double_bottom(df),
            'flag_pattern': _er_flag_pattern(df),
            'triangle': _er_triangle_contraction(df),
            # Divergence
            'strong_bull_div': _er_strong_bullish_div(low, rsi_series),
            'medium_bull_div': _er_medium_bullish_div(low, rsi_series),
            'weak_bull_div': _er_weak_bullish_div(low, rsi_series),
            'bearish_div': _er_bearish_div(high, rsi_series),
            # RS (cached aligned — common_index sadece 1 kez hesaplandı, 8x daha hızlı)
            'rs_turning':             _er_rs_turning_fast(_aligned),
            'rs_crossover':           _er_rs_crossover_fast(_aligned),
            'rs_persistent_positive': _er_rs_persistent_positive_fast(_aligned),
            'rs_accelerating':        _er_rs_accelerating_fast(_aligned),
            'rs_new_high':            _er_rs_new_high_fast(_rs_ratio_arr),
            'rs_healthy_pullback':    _er_rs_healthy_pullback_fast(_aligned),
            'rs_negative_60':         _er_rs_negative_60_fast(_aligned),
            'rs_20d_pct':             _er_rs_pct(close, bench_close, 20, _aligned=_aligned) if _aligned is not None else 0.0,
            # Piyasa
            'index_falling': _er_index_falling(bench_close),
        }
    except Exception:
        return None


def _er_rs_pct_fast(_aligned, days):
    """Aligned numpy arr ile direkt hesap (common_index gerektirmez)"""
    if _aligned is None:
        return 0.0
    s_arr, b_arr = _aligned
    if len(s_arr) < days+1:
        return 0.0
    try:
        sr = (s_arr[-1] / s_arr[-days-1]) - 1
        br = (b_arr[-1] / b_arr[-days-1]) - 1
        return float((sr - br) * 100)
    except Exception:
        return 0.0


def _er_rs_turning_fast(_aligned):
    return _er_rs_pct_fast(_aligned, 20) < 0 and _er_rs_pct_fast(_aligned, 5) > 0


def _er_rs_crossover_fast(_aligned):
    return _er_rs_pct_fast(_aligned, 20) < 0 and _er_rs_pct_fast(_aligned, 10) > 0


def _er_rs_persistent_positive_fast(_aligned):
    if _aligned is None:
        return False
    for d in (5, 10, 20, 60):
        if _er_rs_pct_fast(_aligned, d) <= 0:
            return False
    return True


def _er_rs_accelerating_fast(_aligned):
    if _aligned is None:
        return False
    rs5 = _er_rs_pct_fast(_aligned, 5)
    rs10 = _er_rs_pct_fast(_aligned, 10)
    rs20 = _er_rs_pct_fast(_aligned, 20)
    return rs5 > rs10 > rs20 and rs5 > 0


def _er_rs_new_high_fast(_rs_ratio_arr, lookback=20):
    """RS ratio (close/bench) son N gün'ün tepesinde mi"""
    if _rs_ratio_arr is None or len(_rs_ratio_arr) < lookback:
        return False
    try:
        recent_max = float(_rs_ratio_arr[-lookback:].max())
        current = float(_rs_ratio_arr[-1])
        return recent_max > 0 and current >= recent_max * 0.98
    except Exception:
        return False


def _er_rs_healthy_pullback_fast(_aligned):
    rs20 = _er_rs_pct_fast(_aligned, 20)
    rs5  = _er_rs_pct_fast(_aligned, 5)
    return rs20 > 0 and -3 < rs5 < 0


def _er_rs_negative_60_fast(_aligned):
    return _er_rs_pct_fast(_aligned, 60) < -5


ERKEN_RADAR_SCENARIOS = {
    # === A. GERİ DÖNÜŞ FIRSATLARI (9) ===
    'A1': {'name': 'Dipte Sessizlik', 'category': 'A', 'stars': 5,
           'description': 'Hisse uzun süredir düşüşte ama son günlerde satıcı baskısı azaldı. Aynı zamanda fiyat dipler yapsa da iç güç (momentum) artıyor — bu klasik bir dipten dönüş zemini.',
           'detect': lambda c: c['rsi_30_45'] and c['strong_bull_div'] and c['vol_dried'] and c['fall_stopped'] and c['recent_fall_15']},
    'A2': {'name': 'Hacimli Tepki', 'category': 'A', 'stars': 4,
           'description': 'Hisse orta seviyede düşüş (RSI 45-50) sonrası bugün son 10 günün en büyük satış gününden yüksek hacimle yeşil kapattı. Düşüşten sonra net kurumsal tepki sinyali.',
           # FIX (31 May 2026): A8 ile RSI 35-45 örtüşmesi giderildi. A2 artık SADECE
           # RSI 45-50 aralığında tetiklenir (rsi_35_50 True + rsi_30_45 False).
           'detect': lambda c: c['rsi_35_50'] and not c['rsi_30_45'] and c['pocket_pivot'] and c['recent_fall_15']},
    'A3': {'name': 'İkili Dip / İkinci Test', 'category': 'A', 'stars': 4,
           'description': 'Hisse aynı dip seviyesini ikinci kez test etti ama bu sefer satıcı baskısı belirgin şekilde daha düşük. Çift dip, klasik dönüş örüntülerinden biri.',
           'detect': lambda c: c['double_bottom'] and c['rsi_30_50'] and c['vol_dried']},
    'A4': {'name': 'Yön Değiştiriyor', 'category': 'A', 'stars': 4,
           'description': 'BIST endeksi düşerken bu hisse direnç gösteriyor ve endeksten daha iyi performans göstermeye başladı. Akıllı para sessizce sahipleniyor olabilir.',
           'detect': lambda c: c['index_falling'] and c['rs_turning'] and c['rsi_35_50']},
    'A5': {'name': 'Toparlanma Başlıyor', 'category': 'A', 'stars': 3,
           'description': 'Hisse düşüş hızını kaybetti, yatayda durdu ve hafif alım sinyalleri var. Henüz güçlü teyit yok ama ilk pozitif işaretler beliriyor.',
           'detect': lambda c: c['rsi_35_50'] and c['fall_stopped'] and c['pos_60_lower_50'] and (c['weak_bull_div'] or c['vol_rising_slow'])},
    'A6': {'name': 'Dipte Sıkışma', 'category': 'A', 'stars': 4,
           'description': 'Hisse dipte sıkışmış, volatilitesi düşmüş. Düşüş tükendi gibi ama yön henüz belli değil — yakında bir taraf belirleyecek.',
           'detect': lambda c: c['rsi_30_45'] and c['bb_squeeze'] and c['pos_60_lower_30'] and c['fall_stopped']},
    'A7': {'name': 'Hacimli Toparlanma', 'category': 'A', 'stars': 4,
           'description': 'Son 20 günde yeşil mum günlerinin toplam hacmi kırmızı mum günlerinden belirgin fazla. Alıcı baskısı satıcıdan ağır basıyor.',
           'detect': lambda c: c['rsi_35_50'] and c['updown_ratio_bullish'] and c['pos_60_lower_50']},
    'A8': {'name': 'Ucuz + Hacimli Atak', 'category': 'A', 'stars': 5,
           'description': 'Hisse düşüş bölgesinde (RSI 30-45) iken bugün son 10 günün en büyük satış gününden yüksek hacimle yeşil kapattı. Düşüşten sonra net kurumsal alım sinyali.',
           'detect': lambda c: c['rsi_30_45'] and c['pocket_pivot'] and c['recent_fall_15']},
    'A9': {'name': 'Ucuz Bölgede Bekleyiş', 'category': 'A', 'stars': 3,
           'description': 'Hisse aşırı satım bölgesinde ve hacim kurudu (satıcı kalmadı). Henüz dönüş tetiği yok ama zemin oluşmuş — sabırlı izleme aşaması.',
           'detect': lambda c: (c['rsi_25_35'] or c['rsi_30_45']) and c['vol_dried'] and not c['strong_bull_div'] and not c['medium_bull_div']},

    # === B. SIKIŞMA FIRSATLARI (11) ===
    'B1': {'name': 'Mükemmel Sıkışma', 'category': 'B', 'stars': 5,
           'description': 'Fiyatın hareket bandı 5+ gündür belirgin daraldı, hacim azaldı, RSI nötr ve hisse endeksten güçlü. Tüm kriterler aynı anda "yay gibi geriliyor" diyor — sert hareket pencereye yakın.',
           'detect': lambda c: c['rsi_45_55'] and c['bb_squeeze'] and c['bb_squeeze_days'] >= 5 and c['vol_dried'] and c['rs_20d_pct'] > 0},
    'B2': {'name': 'Sessiz Birikim', 'category': 'B', 'stars': 5,
           'description': 'Fiyat 20 gündür yatay seyrediyor ama hacim sessizce artıyor ve hisse endekse karşı güç kazanıyor. Klasik "kalabalık fark etmeden büyük alıcılar pozisyon alıyor" örüntüsü.',
           'detect': lambda c: c['lateral_20'] and c['vol_rising_slow'] and (c['rs_turning'] or c['rs_crossover'] or c['rs_persistent_positive'])},
    'B3': {'name': 'Klasik Sıkışma', 'category': 'B', 'stars': 4,
           'description': 'Fiyat 20+ gündür dar bantta, hacim azalmış, volatilite düşmüş. Kırılım için klasik baz zemini — yön hangi tarafa kırılırsa hızlı gidebilir.',
           'detect': lambda c: c['lateral_20'] and c['vol_dried'] and c['atr_contracting']},
    'B4': {'name': 'Yatayda Hacim Patlaması', 'category': 'B', 'stars': 5,
           'description': 'Hisse yatay seyrediyordu ama bugün son 10 günün en büyük satış gününden bile yüksek hacimle yeşil kapattı. Yatay seyrin sonu — büyük alıcı belirdi.',
           'detect': lambda c: c['lateral_20'] and c['pocket_pivot']},
    'B5': {'name': 'Üçgen Daralma', 'category': 'B', 'stars': 4,
           'description': 'Tepe seviyeleri alçalırken dip seviyeleri yükseliyor — fiyat üçgen içinde sıkışıyor. Üçgenler genellikle kırılımla sonuçlanır, yön yakında belli olacak.',
           'detect': lambda c: c['triangle'] and c['vol_dried']},
    'B6': {'name': 'Yatay + Endekse Direnç', 'category': 'B', 'stars': 4,
           'description': 'BIST endeksi düşerken bu hisse yatay duruyor ve endeksten net daha iyi performans gösteriyor. Piyasa baskısına direnç = güçlü hisse işareti.',
           'detect': lambda c: c['lateral_20'] and c['index_falling'] and c['rs_20d_pct'] > 0},
    'B7': {'name': 'Yatay + Hafif Pullback', 'category': 'B', 'stars': 3,
           'description': 'Hisse genel yatay seyrinde son birkaç günde hafif geri çekildi. Yapı bozulmamış, sadece soluklanıyor — küçük pullback alım fırsatı olabilir.',
           'detect': lambda c: c['lateral_20'] and c['pullback_3_8'] and c['rsi_45_55']},
    'B8': {'name': 'Sıkışma Sonu', 'category': 'B', 'stars': 4,
           'description': 'Hisse 10+ gündür çok dar bantta sıkışıyor ama son 3 günde hacim canlanmaya başladı. Sıkışma uzadıkça enerji birikiyor — patlama yakın olabilir.',
           'detect': lambda c: c['bb_squeeze_days'] >= 10 and c['vol_rising_slow']},
    'B9': {'name': 'Alt Sınır Testi', 'category': 'B', 'stars': 3,
           'description': 'Yatay seyirde fiyat alt sınıra dokundu ve oradan toparlandı. Alıcılar bu seviyeyi savundu — destek teyit edildi.',
           'detect': lambda c: c['lateral_20'] and c['pos_60_mid'] and c['fall_stopped'] and c['updown_ratio_bullish']},
    'B10': {'name': 'Yatay + Momentum Çelişkisi', 'category': 'B', 'stars': 5,
            'description': 'Fiyat 20 gündür yatay ama momentum (RSI) içeride güçleniyor. Görünen sakinliğe rağmen iç güç birikiyor — dönüş yakın olabilir.',
            'detect': lambda c: c['lateral_20'] and (c['strong_bull_div'] or c['medium_bull_div']) and c['vol_dried']},
    'B11': {'name': 'Tepe Yakını Sıkışma', 'category': 'B', 'stars': 4,
            'description': 'Hisse 60 günlük zirvesinin yakınında sıkışıyor ve endekse karşı güçlü. Tepe yakını sıkışmalar genellikle yukarı kırılımla sonuçlanır.',
            'detect': lambda c: c['pos_60_upper_80'] and c['bb_squeeze'] and c['rs_20d_pct'] > 0},

    # === C. TREND DEVAMI FIRSATLARI (11) ===
    'C1': {'name': 'İdeal Pullback', 'category': 'C', 'stars': 5,
           'description': 'Hisse yukarı trendde devam ediyor (50 günlük ortalama yukarı eğimli, fiyat üstünde). Şu an %3-7 arası sağlıklı bir düzeltme yaşıyor ve hacim düşük. Trend devamı için klasik alım fırsatı.',
           'detect': lambda c: c['above_sma50'] and c['sma50_rising'] and c['rsi_50_60'] and c['pullback_3_8'] and c['vol_dried']},
    'C2': {'name': 'Ortalama Testi', 'category': 'C', 'stars': 4,
           'description': 'Hisse yukarı trendde devam ediyor. Fiyat geri çekildi ve 20 veya 50 günlük hareketli ortalamadan destek aldı. Bu ortalamalar genellikle alıcıların devreye girdiği yerler — trend muhtemelen devam eder.',
           'detect': lambda c: c['above_sma50'] and c['sma50_rising'] and (c['pullback_to_sma20'] or c['pullback_to_sma50']) and c['rsi_45_60']},
    'C3': {'name': 'Pullback + Hacimli Alım', 'category': 'C', 'stars': 5,
           'description': 'Hisse yukarı trendde, son günlerde geri çekildi ama bugün son 10 günün en büyük satış gününden yüksek hacimle yeşil kapattı. Düzeltme bitti, kurumsal alım geri döndü — klasik devam sinyali.',
           'detect': lambda c: c['above_sma50'] and c['pullback_3_8'] and c['pocket_pivot']},
    'C4': {'name': 'Soluklanma', 'category': 'C', 'stars': 4,
           'description': 'Trend yukarı, fiyat 50 günlük ortalama üstünde ve ortalama yukarı eğimli. Son 3-5 gün yatay seyrediyor — kısa süreli dinlenme, trend devam ediyor.',
           'detect': lambda c: c['above_sma50'] and c['sma50_rising'] and c['lateral_10'] and c['rsi_50_65']},
    'C5': {'name': 'Bayrak Formasyonu', 'category': 'C', 'stars': 4,
           'description': 'Hisse son 10 günde sert yükseldi (%10+), ardından son 5 günde dar bir konsolidasyona girdi. Bayrak formasyonu — devam beklentisi yüksek.',
           'detect': lambda c: c['above_sma50'] and c['flag_pattern'] and c['rsi_45_65']},
    'C6': {'name': 'Piyasa Lideri', 'category': 'C', 'stars': 5,
           'description': 'Hisse endekse karşı son 20 günün zirvesinde — piyasada lider konumda. Şu an hafif sağlıklı bir geri çekilme yaşıyor — yeniden ivme kazanabilir.',
           'detect': lambda c: c['above_sma50'] and c['rs_new_high'] and c['rs_healthy_pullback']},
    'C7': {'name': 'Sağlam Trend Yapısı', 'category': 'C', 'stars': 4,
           'description': 'Tüm hareketli ortalamalar düzgün hizalı (fiyat > 20 > 50 günlük) ve tüm zaman dilimlerinde hisse endeksi yeniyor. Çok temiz, profesyonel trend görünümü.',
           'detect': lambda c: c['above_sma20'] and c['above_sma50'] and c['sma50_rising'] and c['rs_persistent_positive'] and c['rsi_45_65']},
    'C8': {'name': 'Yukarı Kanal Testi', 'category': 'C', 'stars': 4,
           'description': 'Hisse yukarı trendde, fiyat 20 günlük ortalamaya kadar geri çekildi ve oradan toparlandı. Kanal içinde sağlıklı düzeltme — yukarı devam beklenebilir.',
           'detect': lambda c: c['above_sma50'] and c['pullback_to_sma20'] and c['rsi_45_55'] and c['vol_dried']},
    'C9': {'name': 'Pullback Var', 'category': 'C', 'stars': 3,
           'description': 'Yukarı trend var ama henüz net bir geri çekilme testi olmadı. Pullback yeni başlıyor — pozisyon almak için biraz sabır gerekebilir.',
           'detect': lambda c: c['above_sma50'] and c['sma50_rising'] and c['rsi_45_60'] and not c['pullback_to_sma20'] and not c['pullback_to_sma50']},
    'C10': {'name': 'Trendde Sıkışma', 'category': 'C', 'stars': 4,
            'description': 'Yukarı trendde fiyat 10+ gündür dar bantta sıkışıyor. Trend bozulmadı, sadece nefes alıyor — kırılım yukarı yönde olma olasılığı yüksek.',
            'detect': lambda c: c['above_sma50'] and c['sma50_rising'] and c['bb_squeeze'] and c['lateral_10']},
    'C11': {'name': 'Trendde Momentum Sağlam', 'category': 'C', 'stars': 4,
            'description': "Yukarı trendde geri çekilmede iç momentum (RSI) düşmedi — yapı sağlam. Trend devam etme olasılığı yüksek.",
            'detect': lambda c: c['above_sma50'] and c['pullback_3_8'] and (c['strong_bull_div'] or c['medium_bull_div'])},

    # === D. UYARILAR (5) ===
    'D1': {'name': 'Tek Güçlü Sinyal', 'category': 'D', 'stars': 3,
           'description': 'Bugün hacimli bir alım günü oldu ama hisse 50 günlük ortalama altında ve diğer kriterler desteklemiyor. Tek başına güçlü sinyal ama riskli — temkinli yaklaş.',
           'detect': lambda c: c['pocket_pivot'] and not c['above_sma50'] and not c['vol_dried']},
    'D2': {'name': 'Karışık Sinyal', 'category': 'D', 'stars': 2,
           'description': 'Bazı pozitif sinyaller var ama bunun yanında zayıflama belirtileri de görünüyor. Tablo kararsız — net karar için daha fazla teyit beklemeli.',
           'detect': lambda c: (c['weak_bull_div'] or c['vol_rising_slow']) and c['bearish_div']},
    'D3': {'name': 'Erken Aşama', 'category': 'D', 'stars': 2,
           'description': 'Hisse ucuz bölgede ve hacim kurudu, ama henüz dönüş için yeterli teyit yok. Sabırlı izleme aşaması — beklemeli.',
           'detect': lambda c: c['rsi_30_50'] and not c['pos_60_upper_80'] and c['vol_dried'] and not c['strong_bull_div'] and not c['medium_bull_div'] and not c['weak_bull_div'] and not c['pocket_pivot'] and not c['bb_squeeze']},
    'D4': {'name': 'Kurumsal Satış Riski', 'category': 'D', 'stars': 'KIRMIZI',
           'description': 'Son 5 günde 2+ defa yüksek hacimli kırmızı kapanış gördük. Bu, kurumların pozisyon küçülttüğüne işaret eder — alımdan kaçın, mevcut pozisyonda dikkatli ol.',
           'detect': lambda c: c['distribution_count_5d'] >= 2},
    'D5': {'name': 'Trend Bozuldu', 'category': 'D', 'stars': 'KIRMIZI',
           'description': 'Fiyat 50 günlük ortalama altına düştü, bugün yüksek hacimli kırmızı kapanış var ve hisse endekse karşı uzun süredir zayıf. Yapı bozulmuş — alım için uygun değil.',
           'detect': lambda c: not c['above_sma50'] and c['distribution_day'] and c['rs_negative_60']},
}


def evaluate_erken_radar(ticker, df, bench_df=None):
    """36 senaryoyu test et, eşleşenleri döner.
    Çıktı: {primary, confirmations, red_flags, overall_quality, verdict, context, matched_count}
    """
    ctx = _er_build_context(df, bench_df)
    if ctx is None:
        return None
    matched = []
    red_flags = []
    for sid, sc in ERKEN_RADAR_SCENARIOS.items():
        try:
            if sc['detect'](ctx):
                entry = {
                    'id': sid,
                    'name': sc['name'],
                    'category': sc['category'],
                    'stars': sc['stars'],
                    'description': sc['description'],
                }
                if sc['stars'] == 'KIRMIZI':
                    red_flags.append(entry)
                else:
                    matched.append(entry)
        except Exception as _er_exc:
            # FIX (31 May 2026): Silent exception → log_error ile errors.log'a yaz.
            # Senaryo lambda detect'i hata verirse senaryo atlanmaya devam eder ama
            # artık görünür: hangi senaryo, hangi hisse, hangi hata.
            try:
                log_error(f"evaluate_erken_radar/{sid}", _er_exc, ctx={'ticker': ticker})
            except Exception:
                pass
            continue
    # Sırala: yıldız sayısı desc, sonra ID
    matched.sort(key=lambda x: (-(x['stars'] if isinstance(x['stars'], int) else 0), x['id']))
    primary = matched[0] if matched else None
    confirmations = matched[1:5] if len(matched) > 1 else []
    # Quality
    quality = 0
    star_to_qual = {5: 85, 4: 65, 3: 45, 2: 25}
    if primary:
        quality = star_to_qual.get(primary['stars'], 30)
        quality += min(len(confirmations) * 4, 15)
    if red_flags:
        quality = max(0, quality - 30)
    deepening = {}
    matched_ids = {item["id"] for item in matched}
    if "B11" in matched_ids:
        deepening["B11"] = b11_pilot_profile(df)
    if "C6" in matched_ids:
        deepening["C6"] = leadership_profile(df, bench_df)
    return {
        'primary': primary,
        'confirmations': confirmations,
        'red_flags': red_flags,
        'overall_quality': min(quality, 100),
        'verdict': primary['description'] if primary else ('Risk uyarısı var' if red_flags else 'Anlamlı senaryo yok'),
        'context': ctx,
        'matched_count': len(matched),
        'red_flag_count': len(red_flags),
        'deepening': deepening,
    }
