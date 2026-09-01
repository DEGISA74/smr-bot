#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
infographic.py — Hisse görsel infografik üreticisi (SMR PRO/ELITE görsel ürün motoru, v1).
Tek ticker → marka-stili koyu HTML (2 sütun) → chromium ile PNG → (Telegram).

v1 kapsam: üst stat+hook · sol lensler+Görev 4 kartları · sağ Fib mum + momentum.
Görev 4 metni v1'de KURAL-TEMELLİ (AI entegrasyonu sonraki adım). Detay:
memory/project_gorev4_infografik.md
"""
import os, sys, sqlite3, math, hashlib
import numpy as np, pandas as pd
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
VERILER = os.path.join(BASE, 'veriler')
DB = os.path.join(BASE, 'patron.db')

# ── marka paleti (koyu) ──
BG = '#0a1019'; CARD = '#111a28'; CARD2 = '#0d1623'; LINE = '#1e2c40'
TXT = '#e6edf6'; MUT = '#8aa0bb'; UP = '#2ec177'; DN = '#f0556a'; INFO = '#4aa3ff'
FIB = '#7e8aa3'; GOLD = '#e0a72e'


def _split_adj(df):
    df = df.copy().sort_index(); pc = [c for c in ['Open', 'High', 'Low', 'Close'] if c in df.columns]
    for _ in range(10):
        cl = df['Close'].ffill().values; f = False
        for i in range(1, len(cl)):
            if cl[i-1] <= 0 or cl[i] <= 0:
                continue
            r = cl[i-1] / cl[i]
            if r >= 1.20:
                for col in pc:
                    df.iloc[:i, df.columns.get_loc(col)] = df.iloc[:i][col].values / r
                f = True; break
        if not f:
            break
    return df


def load(ticker):
    ct = ticker if ticker.endswith('.IS') else f'{ticker}.IS'
    for cand in (f'{VERILER}/{ct}_1d.parquet', f'{VERILER}/{ticker}_1d.parquet'):
        if os.path.exists(cand):
            return _split_adj(pd.read_parquet(cand))
    return None


def cmf(df, n=20):
    rng = (df['High'] - df['Low']).replace(0, np.nan)
    mfv = (((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / rng * df['Volume']).fillna(0)
    v = df['Volume'].rolling(n).sum()
    return (mfv.rolling(n).sum() / v).replace([np.inf, -np.inf], np.nan)


def rsi(c, n=14):
    d = c.diff(); g = d.where(d > 0, 0).rolling(n).mean(); l = (-d.where(d < 0, 0)).rolling(n).mean()
    return float((100 - 100 / (1 + g / l)).iloc[-1])


def rsi_history(c, n=14):
    """Aynı RSI hesabının zaman serisi — gelişim bandı için tek kaynak."""
    d = c.diff(); g = d.where(d > 0, 0).rolling(n).mean(); l = (-d.where(d < 0, 0)).rolling(n).mean()
    return 100 - 100 / (1 + g / l)


def para_akisi_pct(ticker):
    try:
        c = sqlite3.connect(DB)
        r = c.execute("SELECT cmf_pct FROM factor_rank WHERE symbol=? ORDER BY rank_date DESC LIMIT 1",
                      (ticker if ticker.endswith('.IS') else f'{ticker}.IS',)).fetchone()
        c.close()
        return float(r[0]) if r and r[0] is not None else None
    except Exception:
        return None


def compute(ticker, df):
    c = df['Close']; v = df['Volume']
    last = float(c.iloc[-1]); prev = float(c.iloc[-2]); chg = (last/prev - 1) * 100
    ema = {p: float(c.ewm(span=p, adjust=False).mean().iloc[-1]) for p in (5, 8, 13, 144)}
    sma = {p: float(c.rolling(p).mean().iloc[-1]) for p in (50, 100, 200)}
    win = df.tail(252); hi52 = float(win['High'].max()); lo52 = float(win['Low'].min())
    pos52 = (last - lo52) / (hi52 - lo52) * 100 if hi52 > lo52 else 50
    r14 = rsi(c); _rsi_hist = rsi_history(c).dropna()
    _rsi_track = None
    if len(_rsi_hist) >= 14:
        _rsi_track = dict(now=float(_rsi_hist.iloc[-1]),
                          five=float(_rsi_hist.iloc[-5]),
                          fourteen=float(_rsi_hist.iloc[-14]),
                          avg50=float(_rsi_hist.tail(50).mean()))
    cmf20 = float(cmf(df, 20).iloc[-1])
    mom = (float(c.iloc[-21]) / float(c.iloc[-231]) - 1) * 100 if len(c) >= 235 else None
    mom_5 = (float(c.iloc[-1]) / float(c.iloc[-6]) - 1) * 100 if len(c) >= 6 else None
    mom_20 = (float(c.iloc[-1]) / float(c.iloc[-21]) - 1) * 100 if len(c) >= 21 else None
    # MA hizalama → pozisyon eğilimi
    above = sum(1 for m in (ema[5], ema[13], sma[50], sma[200]) if last > m)
    poz = 'Long eğilimli' if above >= 3 else ('Short eğilimli' if above <= 1 else 'Nötr')
    # genel sağlık (basit kompozit 0-100)
    health = int(np.clip(50 + (cmf20 * 120) + (r14 - 50) * 0.4 + (above - 2) * 8, 5, 95))
    pa_pct = para_akisi_pct(ticker)
    # Fibonacci — son 120 barda swing high/low (SADECE betimleyici seviyeler — hedef/stop YOK)
    seg = df.tail(120); sh = float(seg['High'].max()); sl = float(seg['Low'].min())
    fib = {p: sl + (sh - sl) * p for p in (0.0, 0.382, 0.5, 0.618, 0.786, 1.0)}

    # ── ZENGİNLEŞTİRME (22 Haz 2026): smart-money sinyalleri ──────────────
    # OBV durumu + CMF çift-pencere state + Δ5g (pusula motoruyla BİREBİR)
    try:
        import compass_panel as _cp
        _f = _cp.forces(df)
        obv_title, obv_force, cmf_state, d5 = _f['title'], _f['y'], _f['cmf_state'], _f['cmf_force']
        d5_signed = _f['d5']
    except Exception:
        obv_title, obv_force, cmf_state, d5, d5_signed = '', 0.0, 'neutral', 0.0, 0.0
    # POC (son 120) — hacmin en yoğun olduğu fiyat
    seg2 = df.tail(120)
    try:
        _pr = ((seg2['High'] + seg2['Low']) / 2).values; _vo = seg2['Volume'].values
        _hist, _edges = np.histogram(_pr, bins=24, weights=_vo); _bi = int(np.argmax(_hist))
        poc_val = float((_edges[_bi] + _edges[_bi + 1]) / 2)
    except Exception:
        poc_val = None
    # VWAP (son 120, kümülatif)
    try:
        _tp = (seg2['High'] + seg2['Low'] + seg2['Close']) / 3
        vwap_val = float((_tp * seg2['Volume']).cumsum().iloc[-1] / seg2['Volume'].cumsum().iloc[-1])
    except Exception:
        vwap_val = None
    # Yapı (HH/HL/LH/LL) — son 10 bar vs önceki 10-30
    try:
        _rh = float(df['High'].iloc[-10:].max()); _ph = float(df['High'].iloc[-30:-10].max())
        _rl = float(df['Low'].iloc[-10:].min()); _pl = float(df['Low'].iloc[-30:-10].min())
        if _rh > _ph and _rl > _pl:   structure = 'HH_HL'
        elif _rh < _ph and _rl < _pl: structure = 'LH_LL'
        elif _rl > _pl:               structure = 'HL'
        elif _rh < _ph:               structure = 'LH'
        else:                         structure = 'MIX'
    except Exception:
        structure = 'MIX'
    # RS (XU100'e karşı, 126g)
    rs = None
    try:
        _idx = load('XU100')
        if _idx is not None:
            _sc = c; _ic = _idx['Close']
            _cm = _sc.index.intersection(_ic.index)
            if len(_cm) >= 130:
                _sc = _sc.reindex(_cm); _ic = _ic.reindex(_cm)
                rs = float((_sc.iloc[-1] / _sc.iloc[-127]) / (_ic.iloc[-1] / _ic.iloc[-127]))
    except Exception:
        pass
    # RVOL (bugünkü hacim / 20g ortalama)
    try:
        _v20 = float(v.tail(20).mean()); rvol = float(v.iloc[-1] / _v20) if _v20 > 0 else None
    except Exception:
        rvol = None

    return dict(ticker=ticker.replace('.IS', ''), last=last, chg=chg, ema=ema, sma=sma,
                hi52=hi52, lo52=lo52, pos52=pos52, rsi=r14, rsi_track=_rsi_track, cmf=cmf20, mom=mom,
                mom_5=mom_5, mom_20=mom_20, poz=poz,
                health=health, pa_pct=pa_pct, fib=fib, sh=sh, sl=sl, df=df,
                obv_title=obv_title, obv_force=obv_force, cmf_state=cmf_state, cmf_force=d5,
                d5_signed=d5_signed, poc_val=poc_val, vwap_val=vwap_val, structure=structure,
                rs=rs, rvol=rvol)


# ── ZENGİNLEŞTİRİLMİŞ Görev 4 (v2, 22 Haz 2026) — smart-money sentezi ──────────
#    OBV durumu + CMF çift-pencere + Δ5g + POC/VWAP + yapı (HH/HL) + RS + RVOL.
#    Sonuç kartı: trend vs para-akışı çelişki/teyit. al/sat/hedef/stop YASAK.
_STRUCT_TXT = {
    'HH_HL': 'yükselen dip-tepe (HH/HL) — yükseliş yapısı',
    'LH_LL': 'alçalan dip-tepe (LH/LL) — düşüş yapısı',
    'HL': 'yükselen dip (HL), tepe henüz teyitsiz — dönüş denemesi',
    'LH': 'alçalan tepe (LH) — yükseliş zayıflıyor',
    'MIX': 'karışık yapı (net dip-tepe dizisi yok)',
}
_CMF_STATE_TXT = {
    'strong_pos':   ('CMF çift-pencere pozitif (5g+20g alımda)', 1),
    'pos':          ('CMF pozitif (20g alımda)', 1),
    'turning_up':   ("CMF 'kafa çeviriyor' — toparlanma (5g pozitife döndü)", 1),
    'neutral':      ('CMF nötr', 0),
    'turning_down': ("CMF 'kafa çeviriyor' — zayıflama (5g negatife döndü)", -1),
    'neg':          ('CMF negatif (20g satışta)', -1),
    'strong_neg':   ('CMF çift-pencere negatif (5g+20g satışta)', -1),
}


# ── HOOK havuzu (15 Tem 2026) — üst mavi şerit ───────────────────────────────
# Öncesi: durum başına TEK sabit cümle → her görselin şeridi 4 cümle arasında
# dönüyordu, aynı kalıp = algoritma "spam" sayıyor (Oturum 23 tweet dersi).
# Şimdi durum başına havuz; seçim ticker+gün ile deterministik (aşağıya bak).
_HOOKS = {
    'giris': [
        'Para girişi görülüyor, yapı toparlanıyor gibi',
        'Hacim fiyatı destekliyor; sessiz bir toplama izlenimi var',
        'Alıcı taraf ağır basıyor, fiyat henüz aceleci değil',
        'Fiyat kıpırdamadan önce para giriyor gibi',
        'Büyük para tarafında birikim izi var',
        'Akış alıcıda; yapı da yavaşça düzeliyor',
    ],
    'zayif': [
        'Fiyat yukarı ama para çıkıyor gibi, zayıf sinyal',
        'Yükseliş var, hacim desteği yok; temkinli tablo',
        'Fiyat tırmanıyor ama arkasında para yok gibi',
        'Yukarı hareket hacimsiz; kırılgan görünüm',
        'Fiyat ile para akışı ters yöne bakıyor',
        'Yükselişe büyük para eşlik etmiyor gibi',
    ],
    'cikis': [
        'Para çıkışı görülüyor, zayıf görünüm',
        'Satıcı taraf ağır basıyor',
        'Hacim de fiyat da aşağıyı gösteriyor',
        'Para çıkıyor; toparlanma işareti yok',
        'Dağıtım izi var, tablo zayıf',
        'Alıcı ilgisi çekilmiş görünüyor',
    ],
    'denge': [
        'Denge bölgesi, yön belirsiz',
        'Alıcı ve satıcı başa baş; yön arıyor',
        'Tablo kararsız, net bir taraf yok',
        'Sıkışma sürüyor, yön henüz seçilmemiş',
        'Ne alıcı ne satıcı üstün; bekleme hali',
        'Yön netleşmemiş; sinyaller karışık',
    ],
}


def _pick_hook(state, d):
    """Aynı hisse + aynı gün → HEP aynı hook (görsel yeniden üretilince oynamaz),
    hisseden hisseye ve günden güne değişir. random DEĞİL — md5, çünkü process
    içi rastgelelik aynı görseli iki kez üretince farklı sonuç verirdi."""
    pool = _HOOKS.get(state) or _HOOKS['denge']
    try:
        _dk = d['df'].index[-1].strftime('%Y%m%d')
    except Exception:
        _dk = ''
    _key = f"{d.get('ticker', '')}|{_dk}|{state}"
    _i = int(hashlib.md5(_key.encode('utf-8')).hexdigest()[:8], 16) % len(pool)
    return pool[_i]


def _obv_phrase(title):
    t = title or ''
    if 'SAĞLIKLI TREND' in t:    return ('OBV trende eşlik ediyor — hacim yükselişi destekliyor gibi', 1)
    if 'GÜÇLÜ GİZLİ GİRİŞ' in t:  return ('OBV gizli giriş gösteriyor — fiyat düşse de birikim sürüyor gibi', 1)
    if 'DÜŞÜŞE DİRENÇ' in t:      return ('OBV düşüşe direnç gösteriyor — geri çekilmeler alımla karşılanıyor gibi', 1)
    if 'Olası Toplama' in t:     return ('OBV olası toplama işaret ediyor — hafif birikim, büyük onay eksik', 1)
    if 'Toparlanma' in t:        return ("OBV 'kafa çeviriyor' (toparlanma) — kısa vade yukarı döndü", 0)
    if 'Zayıflama' in t:         return ("OBV 'kafa çeviriyor' (zayıflama) — kısa vadede para çıkışı başladı", 0)
    if 'GİZLİ ÇIKIŞ' in t or 'Dağıtım' in t:
        return ('OBV gizli çıkış / dağıtım — yükseliş hacimsiz, para çıkıyor', -1)
    if 'ŞÜPHELİ' in t or 'SAHTE GÜÇ' in t or 'ZAYIF TEYİT' in t:
        return ('OBV çelişkili — net yön yok (şüpheli)', 0)
    return ('OBV nötr — hacimde net yön yok', 0)


def gorev4(d):
    last, sma50 = d['last'], d['sma'][50]
    df = d['df']
    # ── SMA50 tamponu (15 Tem 2026) — kıl payı geçiş "eğilim" sayılmasın.
    #    ±%1.5 içi = hizasında/kararsız. Öncesi: fiyat SMA50'yi binde 7 geçince
    #    bütün dil "yukarı eğilim"e dönüyordu; 3 kuruşluk oynama görseli ters çeviriyordu.
    _SMA_BAND = 1.5
    dist50 = ((last / sma50 - 1) * 100) if sma50 else 0.0
    up50 = dist50 > _SMA_BAND
    dn50 = dist50 < -_SMA_BAND
    at50 = not up50 and not dn50
    near = min(d['fib'].items(), key=lambda kv: abs(kv[1] - last))
    above = sum(1 for m in (d['ema'][5], d['ema'][13], d['sma'][50], d['sma'][200]) if last > m)
    dizilim = ('tüm ortalamalar fiyatın altında (boğa dizilimi)' if above == 4 else
               'ortalamaların çoğu fiyatın altında' if above >= 3 else
               'ortalamaların çoğu fiyatın üzerinde (ayı dizilimi)' if above <= 1 else 'ortalamalar iç içe')
    dist_hi = (d['hi52'] / last - 1) * 100; dist_lo = (last / d['lo52'] - 1) * 100
    pos_txt = ('pahalı bölge, yukarıda alan dar' if d['pos52'] >= 80 else
               'ucuz bölge, yukarı alan geniş' if d['pos52'] <= 35 else 'orta bölge')
    rvol = d.get('rvol')
    vol_txt = ('ortalamanın belirgin üzerinde' if rvol and rvol > 1.8 else
               'ortalamanın üzerinde' if rvol and rvol > 1.1 else
               'ortalama civarında' if rvol and rvol > 0.7 else 'ortalamanın altında')
    son_kapanis = ('üst' if (df['Close'].iloc[-1] - df['Low'].iloc[-1]) >
                   (df['High'].iloc[-1] - df['Close'].iloc[-1]) else 'alt')
    mom_txt = (f"son ~11 ayda %{d['mom']:+.0f}; " if d['mom'] is not None else "")
    rsi_zone = ('aşırı alım' if d['rsi'] >= 70 else 'aşırı satım' if d['rsi'] <= 30 else
                'nötr üstü' if d['rsi'] >= 50 else 'nötr altı')
    rs = d.get('rs')
    rs_txt = (f"endekse göre {'önde' if rs > 1.05 else ('geride' if rs < 0.95 else 'başa baş')} (RS {rs:.2f}×); "
              if rs else "")
    struct_txt = _STRUCT_TXT.get(d.get('structure', 'MIX'), 'karışık yapı')

    # OBV + CMF + Δ5g okumaları + yön skorları
    obv_read, obv_dir = _obv_phrase(d.get('obv_title', ''))
    cmf_read, cmf_dir = _CMF_STATE_TXT.get(d.get('cmf_state', 'neutral'), ('CMF nötr', 0))
    d5 = d.get('d5_signed', 0.0); d5_dir = 1 if d5 > 0 else (-1 if d5 < 0 else 0)
    d5_txt = ('son 5 günün baskısı alıcıda (Δ5g+)' if d5_dir > 0 else
              'son 5 günün baskısı satıcıda (Δ5g−)' if d5_dir < 0 else 'son 5 gün dengede')

    # POC / VWAP konumu
    poc = d.get('poc_val'); vwap = d.get('vwap_val')
    poc_txt = ''
    if poc:
        pd_ = (last / poc - 1) * 100
        if abs(pd_) < 2:
            poc_txt = f"Fiyat POC ({poc:.2f}) hizasında — değer bölgesinde. "
        elif pd_ > 8:
            poc_txt = f"Fiyat POC'un (%{pd_:.0f}) üzerinde — değer bölgesinden uzak; geri çekilmede {poc:.2f} mıknatıs olabilir. "
        elif pd_ > 0:
            poc_txt = f"Fiyat POC'un (%{pd_:.0f}) hemen üzerinde. "
        else:
            poc_txt = f"Fiyat POC'un (%{abs(pd_):.0f}) altında — değer bölgesinin altında. "
    vwap_txt = ('VWAP üstünde' if (vwap and last >= vwap) else 'VWAP altında' if vwap else '')

    g = {}
    if obv_dir > 0 and cmf_dir >= 0:             _hstate = 'giris'
    elif up50 and (obv_dir < 0 or cmf_dir < 0):  _hstate = 'zayif'
    elif obv_dir < 0 and cmf_dir < 0:            _hstate = 'cikis'
    else:                                        _hstate = 'denge'
    g['hook'] = _pick_hook(_hstate, d)

    # ── GENEL: büyük resim — SADE (25 Haz 2026: jargon yok, kısa) ──
    _eg = 'yukarı' if up50 else ('aşağı' if dn50 else 'yatay, fiyat SMA50 hizasında')
    if d['pos52'] >= 80:
        _kon = f"52 haftanın zirvesine yakın (%{d['pos52']:.0f}), pahalı tarafta, yukarıda alan dar"
    elif d['pos52'] <= 35:
        _kon = f"52 haftanın dibine yakın (%{d['pos52']:.0f}), ucuz tarafta, yukarıda alan geniş"
    else:
        _kon = f"52 haftalık bandın ortasında (%{d['pos52']:.0f})"
    _rs_pp = ('endeksin önünde' if rs > 1.05 else 'endeksin gerisinde' if rs < 0.95 else 'endeksle başa baş') if rs else ''
    _mom_pp = (f"son yılda %{abs(d['mom']):.0f} {'kazanmış' if d['mom'] >= 0 else 'kaybetmiş'}") if d['mom'] is not None else ''
    _gmid = ", ".join(p for p in (_rs_pp, _mom_pp) if p)
    _gmid = (_gmid[:1].upper() + _gmid[1:] + ". ") if _gmid else ""
    # Hacim sayısı manşete: "belirgin üzerinde" soluk kalıyordu, 5.5× çarpıcı.
    _vol_pp = f"İşlem hacmi {vol_txt}" + (f" ({rvol:.1f}× normal)" if rvol else "") + "."
    g['GENEL'] = (
        f"Eğilim {_eg}. Fiyat {_kon}. " + _gmid + _vol_pp)

    # ── TEKNİK: seviyeler ve gerginlik ──
    g['TEKNİK'] = (
        f"Fiyat Fibonacci %{int(near[0]*100)} seviyesinde ({near[1]:.2f}), SMA50'nin "
        f"%{(last/sma50-1)*100:+.1f} {'üzerinde' if last >= sma50 else 'altında'}"
        f"{(', ' + vwap_txt) if vwap_txt else ''}. "
        f"Kısa ortalamalar {'yukarı' if d['ema'][5] > d['ema'][13] else 'aşağı'} eğimli, "
        f"RSI {d['rsi']:.0f} ({rsi_zone}). {poc_txt}").strip()

    # ── AKILLI PARA: pusulanın GEREKÇESİ (15 Tem 2026) ──
    # Öncesi: pusula "para giriyor" diyordu, sinyal kutusu "para giriyor" diyordu,
    # bu kart da "para giriyor" diyordu → sol kolon aynı cümleyi 3 kez kuruyordu.
    # Şimdi tekrar yerine pusulanın hangi üç kanıta baktığını açıklıyor.
    # 29 Tem 2026 — günlük iki lense 'bugün' konur (kullanıcı: bu bölüm bugünün
    # okuması olduğu anlaşılsın). d5 leg'i zaten '5 gün' penceresi → dokunulmaz.
    _leg_obv = ('hacim bugün fiyatı destekliyor' if obv_dir > 0 else
                'hacim bugün fiyattan kopmuş' if obv_dir < 0 else 'hacimde bugün net yön yok')
    _leg_cmf = ('para akışı bugün alıcı tarafta' if cmf_dir > 0 else
                'para akışı bugün satıcı tarafta' if cmf_dir < 0 else 'para akışı bugün nötr')
    _leg_d5 = ('son 5 günün baskısı alıcıda' if d5_dir > 0 else
               'son 5 günün baskısı satıcıda' if d5_dir < 0 else 'son 5 gün dengede')
    # Toplam DEĞİL sayım: +1/-1 toplamı çelişkiyi (1 yukarı + 2 aşağı) "zayıf"
    # gibi gösteriyordu. Yön sayıları ayrı sayılır.
    _pos3 = sum(1 for x in (obv_dir, cmf_dir, d5_dir) if x > 0)
    _neg3 = sum(1 for x in (obv_dir, cmf_dir, d5_dir) if x < 0)
    if _pos3 == 3 or _neg3 == 3:   _uyum = 'Üçü de aynı yöne işaret ediyor'
    elif _pos3 and _neg3:          _uyum = 'Kanıtlar birbirini tutmuyor'
    elif _pos3 == 2 or _neg3 == 2: _uyum = 'İkisi aynı yöne işaret ediyor, biri kararsız'
    elif _pos3 == 1 or _neg3 == 1: _uyum = 'Tek kanıt yön veriyor, diğer ikisi kararsız'
    else:                          _uyum = 'Üçü de kararsız, net yön yok'
    _p3 = f"fiyat günü {'tepeye' if son_kapanis == 'üst' else 'dibe'} yakın kapatıyor"
    _p4 = ('Kısaca büyük para sessizce topluyor.' if (obv_dir > 0 and cmf_dir >= 0) else
           'Kısaca satış baskısı öne çıkıyor.' if (obv_dir < 0 and cmf_dir <= 0) else
           'Büyük oyuncularda ne net alım ne net satış var, kararsız.')
    g['AKILLI PARA'] = (f"Pusula üç kanıta bakıyor: {_leg_obv}, {_leg_cmf}, {_leg_d5}. "
                        f"{_uyum}; {_p3}. {_p4}")

    # ── SONUÇ + UYARI (çelişki/risk varsa UYARI çıkar, yoksa hiç) ──
    trend_bull = up50 and above >= 3 and d.get('structure') in ('HH_HL', 'HL')
    trend_bear = dn50 and above <= 1 and d.get('structure') in ('LH_LL', 'LH')
    flow = obv_dir * 1.0 + cmf_dir * 1.0 + d5_dir * 0.5
    # ── DÖNÜŞ ADAYI (15 Tem 2026) ──
    # Öncesi: "net boğa" sayılmak için fiyatın 4 ortalamanın da üstünde olması
    # şarttı. Uzun vadeli düşüşten yeni dönen hisse (SMA50 geri alınmış ama
    # SMA200 hâlâ tepede) hiçbir dala uymuyor → çöp kutusuna düşüp "güçlü sinyal
    # yok" damgası yiyordu. Halbuki aranan hisse tipi tam olarak bu.
    _sma200 = d.get('sma', {}).get(200)
    donus_adayi = (not dn50) and flow >= 1.0 and bool(_sma200) and last < _sma200 \
        and d.get('structure') in ('LH_LL', 'LH', 'MIX')
    conflict = None
    if up50 and flow <= -1.0:
        sonuc = "Fiyat yukarı eğilimde ama para akışı bunu desteklemiyor. Tablo net değil."
        conflict = ("Yükselişe rağmen para çıkıyor gibi (yukarı giderken büyük para satıyor). Fiyat bir "
                    "süre daha gidebilir, ama hacim desteği zayıfladığı için kırılganlık artabilir; henüz onay yok.")
    elif dn50 and flow >= 1.0:
        sonuc = "Fiyat aşağı eğilimde ama para girişi var. Tablo net değil."
        conflict = ("Düşüşe rağmen para giriyor gibi (düşerken büyük para sessizce topluyor). Dönüş "
                    "olasılığı izlenebilir ama fiyat bunu henüz doğrulamadı.")
    elif trend_bull and flow >= 1.5:
        sonuc = "Fiyat de para akışı da aynı yönde, yukarı. Tablo tutarlı, sinyaller çelişmiyor."
    elif trend_bear and flow <= -1.5:
        sonuc = "Fiyat aşağı, para çıkışı sürüyor; ikisi de aynı yönde. Zayıf görünüm net."
    elif donus_adayi:
        _geri = "SMA50'yi geri aldı" if up50 else "SMA50 hizasına geldi"
        sonuc = (f"Uzun vadeli düşüş yapısı henüz kırılmadı, fakat fiyat {_geri} ve para girişi "
                 f"buna eşlik ediyor. Dönüş adayı; yapı bunu henüz onaylamadı.")
    else:
        # İddiasız çöp kutusu: buraya düşen hissede "aynı yöne bakmıyor" demek
        # çoğu zaman yanlıştı (kanıtlar aynı yöne bakıyor olabilir, sadece
        # yukarıdaki dalların şartlarını karşılamıyor).
        sonuc = "Sinyaller tek bir yönde birleşmiş değil. Tablo henüz net değil."
    g['SONUÇ'] = sonuc

    risk = []
    if d['pos52'] >= 80: risk.append('pahalı bölge')
    if d['rsi'] >= 70: risk.append('aşırı alım bölgesinde')
    if up50 and d['rsi'] < 50: risk.append('yükselişe göre momentum zayıf')
    _u = []
    if conflict: _u.append(conflict)
    if risk: _u.append('Risk: ' + ', '.join(risk) + '.')
    if _u:
        g['UYARI'] = ' '.join(_u)

    # Güvenlik ağı: kalan uzun tire (—) → ; (metinde zaten kullanmıyoruz)
    for _k in list(g.keys()):
        g[_k] = g[_k].replace(' — ', '; ').replace('—', ';')
    return g


# ── SVG yardımcıları ──
def svg_fib(d, W=440, H=290):
    df = d['df'].tail(46); c = df['Close']; o = df['Open']; h = df['High']; l = df['Low']
    n = len(df); X0, X1, Y0, Y1 = 6, W-44, 8, H-8
    lo = d['sl'] * 0.99; hi = d['sh'] * 1.01
    xs = lambda i: X0 + i * ((X1-X0)/(n-1))
    ys = lambda p: Y1 - (p-lo)/(hi-lo)*(Y1-Y0)
    s = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg">']
    # Fibonacci seviyeleri (betimleyici — al/sat değil)
    for p, lvl in d['fib'].items():
        y = ys(lvl)
        s.append(f'<line x1="{X0}" y1="{y:.1f}" x2="{X1}" y2="{y:.1f}" stroke="{FIB}" stroke-width="0.7" stroke-dasharray="2 3" opacity="0.5"/>')
        s.append(f'<text x="{X1+3}" y="{y+3:.1f}" font-size="9" fill="{FIB}">%{int(p*100)}</text>')
    # mumlar
    cw = ((X1-X0)/(n-1))*0.6
    for i in range(n):
        up = c.iloc[i] >= o.iloc[i]; col = UP if up else DN; x = xs(i)
        s.append(f'<line x1="{x:.1f}" y1="{ys(h.iloc[i]):.1f}" x2="{x:.1f}" y2="{ys(l.iloc[i]):.1f}" stroke="{col}" stroke-width="1"/>')
        yt = ys(max(o.iloc[i], c.iloc[i])); yb = ys(min(o.iloc[i], c.iloc[i]))
        s.append(f'<rect x="{x-cw/2:.1f}" y="{yt:.1f}" width="{cw:.1f}" height="{max(1.4,yb-yt):.1f}" fill="{col}" rx="1"/>')
    # ema13 + sma50 çizgileri
    for span, col, kind in ((13, INFO, 'ema'), (50, GOLD, 'sma')):
        e = c.ewm(span=span, adjust=False).mean() if kind == 'ema' else c.rolling(span, min_periods=1).mean()
        pts = ' '.join(f'{xs(i):.1f},{ys(e.iloc[i]):.1f}' for i in range(n))
        s.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="1.4" opacity="0.85"/>')
    s.append('</svg>')
    return ''.join(s)


def svg_sentiment(d, W=440, H=110):
    df = d['df'].tail(30); c = df['Close'].values
    em = df['Close'].ewm(span=8, adjust=False).mean().values
    n = len(c); X0, X1, Y0, Y1 = 6, W-6, 8, H-8
    lo = min(c.min(), em.min()); hi = max(c.max(), em.max())
    xs = lambda i: X0 + i*((X1-X0)/(n-1))
    ys = lambda p: Y1 - (p-lo)/(hi-lo+1e-9)*(Y1-Y0)
    s = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg">']
    s.append(f'<polyline points="{" ".join(f"{xs(i):.1f},{ys(c[i]):.1f}" for i in range(n))}" fill="none" stroke="{TXT}" stroke-width="1.4" opacity="0.85"/>')
    s.append(f'<polyline points="{" ".join(f"{xs(i):.1f},{ys(em[i]):.1f}" for i in range(n))}" fill="none" stroke="{GOLD}" stroke-width="1.6"/>')
    s.append('</svg>')
    return ''.join(s)


def bar_52h(d):
    pos = max(2, min(98, d['pos52']))
    return (f'<div style="background:{CARD2};border:1px solid {LINE};border-radius:8px;padding:8px 12px;margin-top:8px;">'
            f'<div style="display:flex;justify-content:space-between;font-size:10.5px;color:{MUT};margin-bottom:5px;">'
            f'<span>52H Düşük {d["lo52"]:.2f}</span><span>%{d["pos52"]:.0f} konumda</span><span>52H Yüksek {d["hi52"]:.2f}</span></div>'
            f'<div style="position:relative;height:6px;border-radius:3px;background:linear-gradient(90deg,{DN}66,{LINE},{UP}66);">'
            f'<div style="position:absolute;left:{pos}%;top:-3px;width:3px;height:12px;background:{TXT};border-radius:2px;"></div></div></div>')


def ma_table(d):
    mas = [('EMA 5', d['ema'][5]), ('EMA 8', d['ema'][8]), ('EMA 13', d['ema'][13]),
           ('SMA 50', d['sma'][50]), ('SMA 100', d['sma'][100]), ('SMA 200', d['sma'][200])]
    cells = ''
    for nm, val in mas:
        sup = d['last'] > val; col = UP if sup else DN; lbl = 'Destek' if sup else 'Direnç'
        cells += (f'<div style="background:{CARD2};border:1px solid {LINE};border-radius:7px;padding:6px 8px;">'
                  f'<div style="font-size:10px;color:{MUT};">{nm}</div>'
                  f'<div style="font-size:13px;font-weight:700;">{val:.2f}</div>'
                  f'<div style="font-size:10px;color:{col};">{lbl}</div></div>')
    return f'<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:6px;margin-top:8px;">{cells}</div>'


def akilli_para_panel(d):
    rows = [('Para akışı (CMF)', 'Pozitif' if d['cmf'] > 0 else 'Negatif' if d['cmf'] < 0 else 'Nötr',
             UP if d['cmf'] > 0 else DN if d['cmf'] < 0 else MUT),
            ('Konum (52H)', f"%{d['pos52']:.0f}", INFO),
            ('RSI', f"{d['rsi']:.0f} · {'aşırı alım' if d['rsi']>=70 else 'aşırı satım' if d['rsi']<=30 else 'nötr'}",
             DN if d['rsi'] >= 70 else UP if d['rsi'] <= 30 else MUT),
            ('Trend (SMA50)', 'Üzerinde' if d['last'] > d['sma'][50] else 'Altında',
             UP if d['last'] > d['sma'][50] else DN)]
    body = ''
    for lbl, val, col in rows:
        body += (f'<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid {LINE};">'
                 f'<span style="font-size:11.5px;color:{MUT};">{lbl}</span>'
                 f'<span style="font-size:11.5px;font-weight:700;color:{col};">{val}</span></div>')
    return (f'<div style="background:{CARD};border:1px solid {LINE};border-radius:10px;padding:8px 12px;margin-bottom:8px;">'
            f'<div style="font-size:12px;font-weight:700;color:{INFO};margin-bottom:4px;">AKILLI PARA ÖZETİ</div>{body}</div>')


def svg_momentum(d, W=440, H=120):
    df = d['df'].tail(30)
    rng = (df['High'] - df['Low']).replace(0, np.nan)
    mfv = (((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / rng * df['Volume']).fillna(0)
    vals = mfv.values; n = len(vals)
    mx = max(abs(vals.min()), abs(vals.max()), 1)
    X0, X1, MID = 6, W-6, H/2
    xs = lambda i: X0 + i*((X1-X0)/(n-1))
    bw = ((X1-X0)/n)*0.6
    s = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg">']
    s.append(f'<line x1="{X0}" y1="{MID}" x2="{X1}" y2="{MID}" stroke="{LINE}" stroke-width="1"/>')
    for i in range(n):
        hh = abs(vals[i])/mx*(H/2-6); col = INFO if vals[i] >= 0 else DN
        y = MID-hh if vals[i] >= 0 else MID
        s.append(f'<rect x="{xs(i)-bw/2:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(1,hh):.1f}" fill="{col}" opacity="0.85" rx="1"/>')
    # fiyat çizgisi (normalize)
    c = df['Close'].values; cmin, cmax = c.min(), c.max()
    pts = ' '.join(f'{xs(i):.1f},{(H-8 - (c[i]-cmin)/(cmax-cmin+1e-9)*(H-16)):.1f}' for i in range(n))
    s.append(f'<polyline points="{pts}" fill="none" stroke="{TXT}" stroke-width="1.4" opacity="0.85"/>')
    s.append('</svg>')
    return ''.join(s)


def lens_bar(label, val_txt, pct, col):
    return (f'<div style="background:{CARD2};border:1px solid {LINE};border-radius:8px;padding:8px 10px;">'
            f'<div style="font-size:11px;color:{MUT};margin-bottom:3px;">{label}</div>'
            f'<div style="font-size:14px;font-weight:700;color:{col};margin-bottom:5px;">{val_txt}</div>'
            f'<div style="height:4px;border-radius:2px;background:{LINE};"><div style="height:4px;width:{pct}%;border-radius:2px;background:{col};"></div></div></div>')


def card(title, body):
    return (f'<div style="background:{CARD};border:1px solid {LINE};border-radius:10px;padding:10px 12px;margin-bottom:8px;">'
            f'<div style="font-size:12px;font-weight:700;color:{MUT};margin-bottom:4px;">{title}</div>'
            f'<div style="font-size:12.5px;line-height:1.55;color:{TXT};">{body}</div></div>')


def build_html(d):
    g = gorev4(d)
    chg_col = UP if d['chg'] >= 0 else DN
    pa = f"Üst %{100-d['pa_pct']:.0f}" if d['pa_pct'] is not None else "—"
    pa_pct = d['pa_pct'] if d['pa_pct'] is not None else 50
    sm_col = UP if d['cmf'] > 0 else (DN if d['cmf'] < 0 else MUT)
    poz_col = UP if 'Long' in d['poz'] else (DN if 'Short' in d['poz'] else MUT)
    lenses = (lens_bar('Genel sağlık', f"{d['health']}/100", d['health'], UP if d['health'] >= 55 else GOLD)
              + lens_bar('Para akışı', pa, pa_pct, INFO)
              + lens_bar('Pozisyon', d['poz'], 70 if 'Long' in d['poz'] else 30, poz_col)
              + lens_bar('Smart money', 'Pozitif' if d['cmf'] > 0 else 'Negatif' if d['cmf'] < 0 else 'Nötr', 62, sm_col))
    cards = (card('Genel yorum', g['Genel yorum']) + card('Teknik görünüm', g['Teknik görünüm'])
             + card('Smart money izi', g['Smart money izi']) + card('Özet', g['Özet']))
    return f'''<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',Arial,sans-serif;}}
body{{background:{BG};width:920px;padding:16px;color:{TXT};}}
</style></head><body>
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">
  <div style="display:flex;align-items:center;gap:10px;">
    <div style="width:40px;height:40px;border-radius:8px;background:{INFO}22;display:flex;align-items:center;justify-content:center;font-weight:700;color:{INFO};">{d['ticker'][:2]}</div>
    <div><div style="font-size:17px;font-weight:700;">{d['ticker']} · Teknik Görünüm</div>
    <div style="font-size:12px;color:{MUT};">SMART MONEY RADAR · günlük özet</div></div>
  </div>
  <div style="background:#0c2238;border:1px solid {LINE};border-radius:10px;padding:8px 16px;text-align:right;">
    <div style="font-size:11px;color:{MUT};letter-spacing:1px;">FİYAT</div>
    <div style="font-size:24px;font-weight:800;">{d['last']:.2f}</div>
    <div style="font-size:12px;color:{chg_col};">{'▲' if d['chg']>=0 else '▼'} %{abs(d['chg']):.2f}</div>
  </div>
</div>
<div style="background:{INFO}1a;border:1px solid {INFO}55;border-radius:8px;padding:8px 14px;margin-bottom:12px;font-weight:700;color:{INFO};">{g['hook']}</div>
<div style="display:grid;grid-template-columns:310px 1fr;gap:12px;align-items:start;">
  <div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-bottom:8px;">{lenses}</div>
    {akilli_para_panel(d)}
    {cards}
  </div>
  <div style="background:{CARD};border:1px solid {LINE};border-radius:10px;padding:10px 12px;">
    <div style="font-size:12px;font-weight:700;color:{MUT};margin-bottom:4px;">Teknik yapı · Fibonacci + ortalamalar</div>
    {svg_fib(d)}
    {bar_52h(d)}
    {ma_table(d)}
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px;">
      <div><div style="font-size:11px;font-weight:700;color:{MUT};margin-bottom:2px;">Para akış ivmesi (CMF)</div>{svg_momentum(d)}</div>
      <div><div style="font-size:11px;font-weight:700;color:{MUT};margin-bottom:2px;">Fiyat ↔ eğilim (sentiment)</div>{svg_sentiment(d)}</div>
    </div>
  </div>
</div>
<div style="font-size:10.5px;color:{MUT};margin-top:10px;text-align:center;">Eğitim amaçlıdır, yatırım tavsiyesi değildir — kesinlik değil olasılık. · smartmoneyradar.app</div>
</body></html>'''


def render_png(html, out_png):
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print('playwright yok: pip install playwright && playwright install chromium'); return False
    tmp = out_png.replace('.png', '.html')
    open(tmp, 'w', encoding='utf-8').write(html)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 952, 'height': 700}, device_scale_factor=2)
        pg.goto('file://' + os.path.abspath(tmp))
        pg.wait_for_timeout(300)
        pg.locator('body').screenshot(path=out_png)
        b.close()
    return True


if __name__ == '__main__':
    tk = sys.argv[1] if len(sys.argv) > 1 else 'SAHOL'
    df = load(tk)
    if df is None or len(df) < 60:
        print(f'{tk}: veri yok/yetersiz'); sys.exit(1)
    d = compute(tk, df)
    html = build_html(d)
    out = os.path.join(BASE, f'infographic_{d["ticker"]}.png')
    print(f'{d["ticker"]} · fiyat {d["last"]:.2f} · CMF {d["cmf"]:+.3f} · sağlık {d["health"]} · '
          f'RSI {d["rsi"]:.0f} · 52H %{d["pos52"]:.0f}')
    if render_png(html, out):
        print(f'✅ PNG: {out}')
    else:
        open(out.replace('.png', '.html'), 'w', encoding='utf-8').write(html)
        print(f'HTML yazıldı (PNG için playwright gerekli): {out.replace(".png",".html")}')
