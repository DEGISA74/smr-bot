# -*- coding: utf-8 -*-
"""BİRLEŞİK FORMASYON MOTORU — canlı modül (20 Tem 2026, insan-etiketli kalibrasyon).

Kullanıcı ilkesi: 'Önce DÜZ ANA ÇİZGİYİ bul (direnç VEYA destek — marifet bu). Altında TOBO mu
fincan mı üçgen mi — detay.' Tek çizgi-bulucu + şekil sınıflayıcı (tobo/fincan/yük.üçgen/alç.üçgen/
taban) + ORTAK durum makinesi (erken/yakın/kırıldı/uzamış/2×fail) + gösterim katmanı (display_level).

SAF hesap: veri df dışarıdan gelir (get_safe_historical_data), bu modül parquet/UI'a dokunmaz.
Kalibrasyon kaynağı + 37 insan etiketi + BAKIŞ AÇISI KODEKSİ →
memory/project_formation_recalibration.md. Public giriş: analyze(df, ticker=None).

⚠️ Bu motor ŞEKİL DOĞRULUĞU için kalibre (insan gözüyle uyumlu) — return-backtest'i HENÜZ YOK
(tek zaman-kesiti, 20 Tem). Canlıda scan_signals'a yazıp temiz backtest biriktirilecek.
"""
import numpy as np
import pandas as pd
import pattern_core
from scanners import _is_index_symbol, _validate_cup_shape

CFG = dict(
    band=0.045,          # küme içi temas toleransı
    pierce=0.05,         # delme yasağı payı (band'den BÜYÜK olmalı — KLSER dersi: %3.2 spreadli
                         # gerçek dudak çifti %3 delme payına takılıp hattı parçalıyordu)
    touch_fresh=70,      # kırılmamış hatta son temas tazeliği
    span_min=20,         # ilk-son temas min aralık
    break_recent=120,    # kırılmış hatta kırılım tazeliği
    yakin_dist=15.0, ls_delme=-4.7, break_band=6.0,   # TOBO v5 durum kalibrasyonu (ortak)
    tri_base_max=0.25,   # üçgen tabanı derinlik tavanı (FONET %21 gerçek üçgen)
    cup_handle_lo=0.78,  # fincan okuması için fiyat hattın ≥%78'inde (kulp bölgesi)
)


# ── YAŞAM DÖNGÜSÜ ROZETİ (21 Tem 2026) — TEK KAYNAK sözlük ──────────────────
# scan_chart_patterns (scan_pipeline) chart_d['stage'] üretir; app.py formasyon
# kartı bunu okuyup tutarlı rozet basar. İleride Erken Radar da bu dile bağlanabilir.
# İlke: rozet "girme" demez, formasyonun HANGİ AŞAMADA olduğunu tarif eder.
STAGE_BADGE = {
    'form':      ('🌱', 'Oluşuyor',    '#38bdf8'),  # henüz kırılmadı, izle
    'break':     ('🚀', 'Tetiklendi',  '#22c55e'),  # kırılım taze — asıl an
    'retest':    ('🎯', 'Teyit',       '#10b981'),  # boyna geri döndü, destek testi
    'extended':  ('🟠', 'Fazla uzadı', '#f59e0b'),  # hareket olmuş, girmek geç
    'completed': ('🏁', 'Tamamlandı',  '#94a3b8'),  # hedefe ulaştı — fırsat değil, geçmişi açıklar
    'failed':    ('🔴', 'Geçersiz',    '#ef4444'),  # kırılım bozuldu
}


def stage_badge(stage):
    """Yaşam döngüsü aşamasını (emoji, etiket, renk) üçlüsüne çevirir; bilinmeyen → None."""
    return STAGE_BADGE.get(stage)


def _swings(high, low, lb=8):
    ah = high.astype(float); al = low.astype(float); n = len(ah); sh = []; sl = []
    for i in range(lb, n - lb):
        if ah[i] >= ah[i-lb:i+lb+1].max() - 1e-9: sh.append((i, ah[i]))
        if al[i] <= al[i-lb:i+lb+1].min() + 1e-9: sl.append((i, al[i]))
    return sh, sl


def _prep(df):
    c = df['Close'].values.astype(float); h = df['High'].values.astype(float); l = df['Low'].values.astype(float)
    sh, sl = _swings(h, l)
    sh, sl = pattern_core.prune_pivots(sh, sl, pattern_core.adaptive_threshold(c))
    bt = len(df)
    shy = [(i, v) for i, v in sh if i >= bt - 252]
    sly = [(i, v) for i, v in sl if i >= bt - 252]
    return c, bt, shy, sly


def find_necklines(h, c, bt, shy, cfg):
    """Yatay ana direnç hatları — en taze temastan GERİYE yürü, delme çıkan yerde kes."""
    out = []
    used = set()
    for si, sv in sorted(shy, key=lambda x: -x[1]):
        if si in used: continue
        cl = [(i, v) for i, v in shy if abs(v - sv) / sv <= cfg['band']]
        if len(cl) < 2: continue
        cl = sorted(cl, key=lambda x: x[0])
        kept = [cl[-1]]                                   # çekirdek: en taze temas
        for j in range(len(cl) - 2, -1, -1):              # geriye yürü
            lvl_try = min(v for _, v in kept + [cl[j]])
            seg = h[cl[j][0]:kept[-1][0] + 1]
            if seg.size and float(seg.max()) > lvl_try * (1 + cfg['pierce']):
                break                                     # delme — hattın geçerli kısmı biter
            kept.insert(0, cl[j])
        if len(kept) < 2: continue
        level = min(v for _, v in kept)                   # gövde/alt kenar (EKGYO 21.98 dersi)
        fs, ls_t = kept[0][0], kept[-1][0]
        if ls_t - fs < cfg['span_min']: continue
        curr = c[-1]
        if curr <= level * (1 + cfg['pierce']):
            if bt - ls_t > cfg['touch_fresh']: continue   # kırılmamış → temas taze olmalı
        else:
            above = np.where(c[ls_t:] > level * 1.03)[0]
            if not above.size or (bt - (ls_t + int(above[0]))) > cfg['break_recent']: continue
        for i, _ in kept: used.add(i)
        out.append(dict(level=level, touches=kept, fs=fs, ls_t=ls_t))
    # Önce DEDUP (aynı seviyenin kopyaları içinde çok-temaslı kazanır: ENTRA 5.22-3T > 5.25-2T),
    # sonra ANA HAT = EN YÜKSEK geçerli hat (KLMSN 38.86 > 36.3 iç yapısı).
    out.sort(key=lambda d: (-len(d['touches']), -d['ls_t']))
    dedup = []
    for d in out:
        if not any(abs(d['level'] - e['level']) / e['level'] < 0.015 for e in dedup):
            dedup.append(d)
    dedup.sort(key=lambda d: -d['level'])
    for d in dedup:
        tv = [v for _, v in d['touches']]
        d['drift'] = tv[-1] / tv[0] - 1        # temas sürüklenmesi (+kama / -daralan işareti)
    return dedup


def _rim_complete(h, bt, level, rim_i, dwell_min=5):
    """Sağ dudak TAMAMLANDI mı? (kullanıcı onaylı) — son temas çevresinde (-10..+15 bar) fiyat hattın
    %3 bandında ≥5 bar kalmış olmalı. Tek sivri dokunuş (BIGCH/CMBTN fitili) dudak DEĞİLDİR:
    kulp yok → FAIL-2 olamaz, YAKIN da verilmez. LOGO haftalarca oyalandı → tamam."""
    w = h[max(0, rim_i - 10):min(bt, rim_i + 15)]
    return int(np.sum(w >= level * 0.97)) >= dwell_min


def live_floor_display(lo, bt, struct_level, curr):
    """GÖSTERİM KATMANI (kullanıcı onaylı) — yapıya/duruma DOKUNMAZ, yalnız ÇİZİLECEK çizgiyi canlı
    tabana indirir. Son 30 barın en alçak İYİ-SAVUNULAN dibi (≥3 bar %1.5 içinde = ARCLK'nin 2-barlık
    taze kırılım devamı GİRMEZ), fiyat ona ≤%4 yakın, yapısal seviyenin altında."""
    seg = lo[max(0, bt - 30):]
    for v in sorted(set(float(x) for x in seg)):
        if int(np.sum(np.abs(seg - v) / v <= 0.015)) >= 3:
            if v < struct_level and abs(curr - v) / v <= 0.04:
                return round(v, 2)
            break
    return round(struct_level, 2)


def find_support_lines(lo, c, bt, sly, cfg):
    """DÜZ DESTEK hattı — boyun ilkesinin aynası (alçalan üçgende ana hat DESTEKtir)."""
    out = []
    used = set()
    for si, sv in sorted(sly, key=lambda x: x[1]):            # en derin dipten başla
        if si in used: continue
        # canlı dtri kalibrasyonu (ARCLK): destek kümesinin TÜM üyeleri ≤160 bar taze —
        # eski dip pencereyi geriye esnetip tepe-fit'ine outlier sokuyordu (ARCLK 136 direği)
        cl = [(i, v) for i, v in sly
              if abs(v - sv) / sv <= cfg['band'] and bt - i <= 160]
        if len(cl) < 2: continue
        cl = sorted(cl, key=lambda x: x[0])
        kept = [cl[-1]]
        for j in range(len(cl) - 2, -1, -1):                  # geriye yürü — aşağı delme keser
            lvl_try = max(v for _, v in kept + [cl[j]])
            seg = lo[cl[j][0]:kept[-1][0] + 1]
            if seg.size and float(seg.min()) < lvl_try * (1 - cfg['pierce']):
                break
            kept.insert(0, cl[j])
        if len(kept) < 2: continue
        # SEVİYE = güncel fiyata EN YAKIN SAVUNULAN taban (canlı floor; ≥2 bar-temas = spike değil).
        # ANHYT: 94.5 tek fitil (savunma yok) elenir, 98.9 fiyata en yakın savunulan taban.
        fs0 = kept[0][0]; curr0 = c[-1]
        defended = [tv for _, tv in kept
                    if int(np.sum(np.abs(lo[fs0:] - tv) / tv <= 0.015)) >= 2]
        pool = defended if defended else [v for _, v in kept]
        level = min(pool, key=lambda tv: abs(tv - curr0))
        fs, ls_t = kept[0][0], kept[-1][0]
        if ls_t - fs < cfg['span_min']: continue
        curr = c[-1]
        if curr >= level * (1 - cfg['pierce']):
            if bt - ls_t > cfg['touch_fresh']: continue       # kırılmamış → temas taze
        else:
            below = np.where(c[ls_t:] < level * 0.97)[0]
            if not below.size or (bt - (ls_t + int(below[0]))) > cfg['break_recent']: continue
        for i, _ in kept: used.add(i)
        out.append(dict(level=level, touches=kept, fs=fs, ls_t=ls_t))
    out.sort(key=lambda d: (-len(d['touches']), -d['ls_t']))
    dedup = []
    for d in out:
        if not any(abs(d['level'] - e['level']) / e['level'] < 0.015 for e in dedup):
            dedup.append(d)
    dedup.sort(key=lambda d: d['level'])                      # ana destek = EN ALÇAK geçerli hat
    return dedup


def classify_dtri(c, bt, shy, sl_nl):
    """Destek hattı üstünde ALÇALAN direnç → alçalan üçgen (ARCLK kalibre):
    son tepe < ilk×0.97 + eğim<0 + R²≥0.70 (katı-monoton değil — ARCLK 117→121→117)."""
    fs = sl_nl['fs']
    tops = sorted([(i, v) for i, v in shy if i >= fs], key=lambda x: x[0])
    if len(tops) < 2: return None
    # LH'nin özü ZİRVEDEN iniş: pencere ilk teması çukur-çıkışıysa 'son<ilk' bozuluyordu (ARCLK)
    if not (tops[-1][1] < max(v for _, v in tops) * 0.97): return None
    # SON İKİ TEPE DE alçalıyor olmalı (LH dizisi sürüyor) — BAHKM 136.5→135.8 düz (boğa yapısı)
    if len(tops) >= 3 and not (tops[-1][1] < tops[-2][1] * 0.99): return None
    x = np.array([i for i, _ in tops], float); y = np.array([v for _, v in tops], float)
    cf = np.polyfit(x, y, 1)
    if cf[0] >= 0: return None
    yp = np.polyval(cf, x); sst = np.sum((y - y.mean()) ** 2)
    r2 = 1 - np.sum((y - yp) ** 2) / sst if sst > 0 else 0
    if r2 < 0.70: return None
    return dict(res_now=float(np.polyval(cf, bt - 1)), r2=round(float(r2), 2),
                n_tops=len(tops))


def classify_base(c, lo, bt, shy, sly, nl, cfg, hh=None):
    """Hat altı taban. SIRA: fincan (yuvarlaklık) → tobo (temas-arası baş) → üçgen (sığ monoton)."""
    fs, level, curr = nl['fs'], nl['level'], c[-1]
    lows = sorted([(i, v) for i, v in sly if i >= fs], key=lambda x: x[0])

    # --- 1 FİNCAN: yuvarlaklık — dudaklar HERHANGİ İKİ temas olabilir (HUNER dersi).
    # İmza: _validate_cup_shape(arr, left_i, dip_i, right_i, r2)
    def _bowl_ok(b0, b1):
        bowl = c[b0:b1 + 1]
        if len(bowl) < 30: return None
        try:
            dip_rel = int(np.argmin(bowl))
            depth = (bowl[0] - bowl.min()) / bowl[0]        # canlı iskelet: derinlik 0.12-0.55
            if not (0.12 <= depth <= 0.55): return None
            # CANLI DERS (16 Haz): fit 5g EMA üzerinde — volatil dip ham polinomu bozuyor (TOASO).
            smooth = pd.Series(bowl).ewm(span=5, adjust=False).mean().values
            x = np.linspace(0, 1, len(bowl))
            cf2 = np.polyfit(x, smooth, 2)
            yp = np.polyval(cf2, x)
            sst = np.sum((smooth - smooth.mean()) ** 2)
            r2u = 1 - np.sum((smooth - yp) ** 2) / sst if sst > 0 else 0.0
            if cf2[0] > 0 and _validate_cup_shape(bowl, b0, b0 + dip_rel, b1, r2u):
                return dict(dip=float(bowl.min()), r2=round(float(r2u), 2))
        except Exception:
            pass
        return None
    t_sorted = sorted(nl['touches'], key=lambda x: x[0])
    # 1a TAM fincan: iki dudak = iki hat teması (LOGO) — dudak-tamamlanma _rim_complete ile
    for bi in range(len(t_sorted) - 1, 0, -1):
        for ai in range(bi):
            r = _bowl_ok(t_sorted[ai][0], min(bt - 1, t_sorted[bi][0]))
            if r:
                _ri = t_sorted[bi][0]
                _cm = _rim_complete(hh, bt, level, _ri) if hh is not None else True
                return 'fincan', dict(r, complete=_cm, rim_i=_ri)
    # 1b OLUŞAN fincan: sol dudak = eski temas, sağ yaka HÂLÂ tırmanıyor (BIOEN/BIGCH/AKBNK/ALBRK)
    if curr < level:
        for ai in range(len(t_sorted)):
            r = _bowl_ok(t_sorted[ai][0], bt - 1)
            if r:
                return 'fincan', dict(r, complete=False)
    # --- 2 TOBO: baş iki hat teması ARASINDA + net en derin + ön-trend (v5 çekirdeği)
    if len(lows) >= 1:
        t_idx = [i for i, _ in nl['touches']]
        for hd_i, hd_v in sorted(lows, key=lambda x: x[1]):
            if not (any(t < hd_i for t in t_idx) and any(t > hd_i for t in t_idx)):
                continue                                   # ln...baş...rn yapısal şart
            others = [v for i, v in lows if i != hd_i]
            if others and hd_v >= min(others) * 0.97: break # baş net derin değil → taban dene
            pre = [(i, v) for i, v in sly if i < hd_i]
            if not pre: break
            ls_i, ls_v = pre[-1]
            if not pattern_core.tobo_pretrend_ok(c, ls_i, hd_v): continue
            return 'tobo', dict(hd_i=hd_i, head=hd_v, ls_i=ls_i, ls=ls_v)
    # --- 2b TABAN: hat altında DÜZ çok-dipli sıkışma (KLMSN 28.4×5). Dipler dar bantta (≤%6).
    if len(lows) >= 2:
        t_idx = [i for i, _ in nl['touches']]
        span_lows = [(i, v) for i, v in lows if i <= nl['ls_t']]
        if len(span_lows) >= 2:
            vs = [v for _, v in span_lows]
            # mono-yükselen dipler taban DEĞİL üçgendir (FONET'i taban çalıyordu) → üçgene bırak
            _mono_rise = (all(span_lows[k][1] < span_lows[k+1][1] for k in range(len(span_lows)-1))
                          and vs[-1] > vs[0] * 1.03)
            if not _mono_rise and (max(vs) - min(vs)) / min(vs) <= 0.06 \
               and any(i < span_lows[-1][0] for i in t_idx):
                return 'taban', dict(dip=float(min(vs)))
    # --- 3 ÜÇGEN: pencere başından monoton yükselen dipler + SIĞ taban (derin çukur ≠ üçgen)
    if len(lows) >= 2:
        mono = all(lows[k][1] < lows[k+1][1] for k in range(len(lows)-1))
        rise = lows[-1][1] > lows[0][1] * 1.03
        deep = (level - min(v for _, v in lows)) / level
        if mono and rise and deep <= cfg['tri_base_max']:
            x = np.array([i for i, _ in lows], float); y = np.array([v for _, v in lows], float)
            cf = np.polyfit(x, y, 1); yp = np.polyval(cf, x)
            sst = np.sum((y - y.mean())**2)
            r2 = 1 - np.sum((y - yp)**2)/sst if sst > 0 else 1.0
            if cf[0] > 0 and r2 >= 0.75:
                return 'ucgen', dict(r2=round(float(r2), 2))
    return None, {}


def state_machine(c, lo, bt, sly, nl, shape, info, cfg):
    """Ortak durum: FAIL / KIRILDI / UZAMIS / YAKIN / ERKEN."""
    level, curr, ls_t = nl['level'], c[-1], nl['ls_t']
    dist = (level - curr) / level * 100
    # ÖNCE KIRILIM (POLHO kuralı): hat yukarı kırıldıysa formasyon BAŞARILI bitti —
    # sonrasındaki dönüş formasyonla ilgisiz, FAIL sorgulanmaz.
    if dist <= 0:
        return 'KIRILDI' if -dist <= cfg['break_band'] else 'UZAMIS'
    seg = lo[ls_t:]
    mn = float(seg.min()) if seg.size else curr
    if shape == 'fincan':
        # FAIL-2 (LOGO): TAM fincanda kulp aşağı koptu — kulp dibi hat×0.81 altına indi
        if info.get('complete') and mn < level * 0.81: return 'FAIL'
    else:
        # FAIL-1 (ENTRA): tamamlanmadan sağ yapı bozuldu (Dow ara-dip kırığı)
        _floor_start = info.get('hd_i', nl['fs'])
        inter = [v for i, v in sly if _floor_start < i < ls_t]
        base_floor = info.get('head') or info.get('dip')
        if inter and mn < min(inter) * 0.99: return 'FAIL'
        if base_floor and mn < base_floor * 0.995: return 'FAIL'
    if dist <= cfg['yakin_dist']:
        if shape == 'tobo' and info.get('ls'):
            if (mn / info['ls'] - 1) * 100 < cfg['ls_delme']: return 'ERKEN'
        # Fincan: dudak tamamlanmadıysa kulp yok → YAKIN verilmez (kullanıcı onaylı)
        if shape == 'fincan' and not info.get('complete', True): return 'ERKEN'
        return 'YAKIN'
    return 'ERKEN'


# KULLANICI ETİKET DÜZELTMELERİ ("metrik değil benim etiketim esas"). Hiçbir ölçülebilir eksen
# ayırmadı (DOAS oyalanma 9 bar vs RAPOR-BAHKM 3). Etiket = otorite; etiket birikince yeniden kalibre.
USER_STATE_OVERRIDE = {'BOBET': 'ERKEN', 'DOAS': 'ERKEN'}


def analyze(df, ticker=None):
    """Public giriş. df: OHLC (get_safe_historical_data çıktısı). Döner: formasyon dict veya None.
    Şekil (tobo/fincan/ucgen/dtri/taban) + durum (ERKEN/YAKIN/KIRILDI/UZAMIS/FAIL) + level +
    (dtri'de) display_level + touches/fs/ls_t/info."""
    if df is None or len(df) < 60 or not {'Close', 'High', 'Low'}.issubset(df.columns):
        return None
    df = df.tail(500)
    try:
        c, bt, shy, sly = _prep(df)
    except Exception:
        return None
    lo = df['Low'].values.astype(float); h = df['High'].values.astype(float)
    _tk = str(ticker).upper().replace('.IS', '') if ticker else None
    # ALÇALAN ÜÇGEN ÖNCE (strict): düz DESTEK hattı + pencere tepeleri NET alçalıyor.
    for sl_nl in find_support_lines(lo, c, bt, sly, CFG):
        d = classify_dtri(c, bt, shy, sl_nl)
        if d:
            level, curr = sl_nl['level'], c[-1]
            db = (curr - level) / level * 100
            if db < -CFG['break_band']:
                continue          # kırık-eski destek: dtri iddia etme (BIGCH), akış fincan/tobo'ya
            # AYI-FAIL (ANHYT): destek KIRILDI ama fiyat GERİ ALDI = tuzak (boğa FAIL'lerinin aynası)
            _mn_d = float(lo[sl_nl['ls_t']:].min()) if lo[sl_nl['ls_t']:].size else curr
            if _mn_d < level * 0.99 and curr > level * 1.02:
                st = 'FAIL'
            elif db < 2.0: st = 'KIRILDI'
            elif curr <= d['res_now'] * 1.01 and db <= 15: st = 'YAKIN'
            else: st = 'ERKEN'
            return dict(shape='dtri', state=st, level=round(level, 2),
                        display_level=live_floor_display(lo, bt, level, curr),
                        touches=sl_nl['touches'], fs=sl_nl['fs'], ls_t=sl_nl['ls_t'], info=d)
    for nl in find_necklines(h, c, bt, shy, CFG):
        # KAMA/DARALAN reddi: temaslar POZİTİF sürükleniyor + pencere dipleri monoton yükseliyor
        # = sıkışan yapı (GEDIK yükselen kama). Negatif sürüklenme bant içi doğal oynama (FONET).
        _wl = sorted([(i, v) for i, v in sly if i >= nl['fs']], key=lambda x: x[0])
        if nl.get('drift', 0) > 0.015 and len(_wl) >= 2 and \
           all(_wl[k][1] < _wl[k+1][1] for k in range(len(_wl)-1)):
            continue
        # SIKIŞMA-KORİDORU vetosu (MARBL): pencere dipleri TEK YÜKSELEN DOĞRU üstündeyse
        # (n≥4 + R²≥0.8 + uç-uca ≥+%5) taban değil daralan koridordur.
        if len(_wl) >= 4:
            _x = np.array([i for i, _ in _wl], float); _y = np.array([v for _, v in _wl], float)
            _cf = np.polyfit(_x, _y, 1); _yp = np.polyval(_cf, _x)
            _sst = np.sum((_y - _y.mean()) ** 2)
            _r2l = 1 - np.sum((_y - _yp) ** 2) / _sst if _sst > 0 else 1.0
            if _cf[0] > 0 and _r2l >= 0.80 and (_y[-1] / _y[0] - 1) >= 0.05:
                continue
        shape, info = classify_base(c, lo, bt, shy, sly, nl, CFG, hh=h)
        if shape:
            st = state_machine(c, lo, bt, sly, nl, shape, info, CFG)
            if _tk and USER_STATE_OVERRIDE.get(_tk) and st == 'YAKIN':
                st = USER_STATE_OVERRIDE[_tk]
            return dict(shape=shape, state=st, level=round(nl['level'], 2),
                        touches=nl['touches'], fs=nl['fs'], ls_t=nl['ls_t'], info=info)
    return None
