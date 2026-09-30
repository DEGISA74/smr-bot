#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""İnfografik BİRLEŞTİRİCİ (v3) — gerçek paneller + temiz Plotly grafik → tek PNG.
Üst: stat şeridi + FİYAT + HOOK · SOL: PARA AKIŞI pusulası + Görev 4 kartları ·
SAĞ: temiz mum grafiği (Plotly) + İvme/Denge (Plotly). chromium screenshot.
al/sat/hedef/stop YASAK — saf gözlem/eğitim."""
import os, sys, base64, re
import numpy as np
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
import infographic as ig            # load, compute, gorev4
import clean_chart_plotly as cc     # build_fig, build_ivme_fig
import compass_panel as cp          # build_compass_html
# NOT: playwright (chromium) sadece PNG export için → render() içinde lazy import.
# In-app st.html yolu build_widget_html kullanır, chromium gerektirmez.

BASE = os.path.dirname(os.path.abspath(__file__))
BG = '#0a1019'; CARD = '#111a28'; CARD2 = '#0d1623'; LINE = '#1e2c40'
TXT = '#e6edf6'; MUT = '#8aa0bb'; UP = '#2ec177'; DN = '#f0556a'; INFO = '#4aa3ff'; GOLD = '#e0a72e'

# 6 Tem 2026 — emtia ham sembolünü UI display adına çevir (app.py display haritasıyla aynı).
# Başlıkta "GC=F · Teknik Görünüm" yerine "ONS ALTIN · Teknik Görünüm" gösterilsin.
_CMDTY_DISP = {
    "GC=F": "ONS ALTIN", "SI=F": "GÜMÜŞ", "CL=F": "WTI PETROL",
    "BZ=F": "BRENT PETROL", "NG=F": "DOĞAL GAZ", "HG=F": "BAKIR",
}
def _disp_name(t):
    return _CMDTY_DISP.get(str(t).strip().upper(), t)

# 15 Tem 2026 — TEK uyarı kaldı. Öncesi 3 kez uyarıyorduk: soldaki gri
# DISCLAIMER_PANEL + bu rozet + en alttaki minik satır. Üçü görselin ~%10'unu
# yiyordu, ikisi silindi; kazanılan yer içeriğe gitti.
# 27 Ağu 2026 — rozet SAYFANIN DİBİNDEN SOL KOLONUN DİBİNE taşındı: metin aynı,
# yalnız punto dar kolona göre küçüldü. Fonksiyona çevrildi ki punto çağrıdan gelsin.
def _notice_badge(fs=17):
    return (
        f"<div style='background:{CARD2};border:1px solid {GOLD}44;border-radius:10px;"
        f"padding:10px 13px;flex:1;display:flex;align-items:center;'>"
        f"<span style='font-size:{fs}px;line-height:1.5;color:{MUT};letter-spacing:0.2px;'>"
        f"<b style='color:{GOLD};'>DİKKAT.</b> BU GÖRSELİN HER HAKKI MAHFUZDUR. #SMARTMONEYRADAR EĞİTİM AMAÇLIDIR, YATIRIM TAVSİYESİ DEĞİLDİR. "
        f"YAPAY ZEKA ÜRETİMİ DEĞİLDİR. 52.000 SATIRLIK ALGORİTMAMIN ÇIKTISIDIR. "
        f"<b style='color:{INFO};'>#SMARTMONEYRADAR</b></span></div>"
    )

NOTICE_BADGE = _notice_badge()   # geriye uyum (dışarıdan import edenler için)


# 20 Tem 2026 — KALEİDO İZOLE RENDER. Kök sorun: kaleido 1.3 + Chrome 150 + Windows'ta uzun süren
# Streamlit'te kalıcı browser BAYATLIYOR → 2. render'da browser-kapatma ASILIYOR ("Couldn't close or
# kill browser subprocess" / 120sn aşımı). Eski thread-wrapper thread'i öldüremediği için zombi Chrome
# + asılı thread BİRİKİYORDU. Çözüm: her render'ı TAZE ayrı süreçte koş → taze browser (bayatlama yok);
# asılırsa süreç ağacını taskkill /T ile öldür (zombi kalmaz, çağıran thread biter). Detay:
# memory/project_kaleido_headless_patch.md.
#
# 21 Tem 2026 — "kaleido render eksik kaldı: ['chart']" KÖK ÇÖZÜMÜ. Eski alt-süreç her figürü ayrı
# fig.to_image() ile çağırıyordu; kaleido 1.3'te HER to_image AYRI Chrome açar → 3 figür = 3 cold-start
# + 3 ayrı "unclean kill". En ağır figür (chart, ~150KB) yüklü sunucuda kaleido iç zaman aşımını aşıp
# HATA fırlatıyor, bare except onu YUTUYOR, hafif harsi/ivme kurtuluyor → "sadece chart eksik". Yeni yol:
# kaleido'nun gerçek batch API'si (write_fig_from_object) → TÜM figürler TEK session/TEK Chrome, mathjax
# kapalı (CDN beklemesi yok), per-figür 45s iç zaman aşımı, hatalar YUTULMAZ (errors.log → mesaja işlenir).
# Ölçüm: 3 figür 34s → 14s; chart artık öbürleriyle aynı browser'da, ayrı riske atılmıyor.
_KALEIDO_RENDER_TIMEOUT = 60   # saniye/deneme — TEK Chrome açılışı + tüm figürler batch (normal ~14sn)
_KALEIDO_RETRY = 2             # eksik kalan figürler TAZE süreçle bir kez daha denenir
_KALEIDO_FIG_TIMEOUT = 45      # kaleido'nun figür-başı iç zaman aşımı (< _RENDER_TIMEOUT: takılan tek figür düşer, öbürleri geçer)


# Alt süreç kaynağı (TAZE yorumlayıcı → taze kaleido global server; bayatlama yok). Tüm figürleri TEK
# Kaleido session'ında (write_fig_from_object) render eder → tek Chrome cold-start, tek kapanış. Her PNG'yi
# önce raw_ adına yazar, session dönünce atomik olarak out_ adına çevirir (parent yalnız out_ okur → yarım
# okuma yok). Hatalar errors.log'a yazılır. RAW string: içindeki '\n' iki karakter olarak dosyaya geçmeli.
_RENDER_SUBPROCESS_SRC = r"""
import sys, os, glob, traceback
import plotly.io as pio
import kaleido
_d, _sc, _to = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
_fig_dicts = []
for _j in sorted(glob.glob(os.path.join(_d, 'fig_*.json'))):
    _name = os.path.basename(_j)[4:-5]
    try:
        with open(_j, encoding='utf-8') as _fh:
            _fig = pio.from_json(_fh.read()).to_dict()
        _fig_dicts.append({'fig': _fig,
                           'path': os.path.join(_d, 'raw_' + _name + '.png'),
                           'opts': {'format': 'png', 'scale': _sc}})
    except Exception:
        pass
try:
    _errs = kaleido.write_fig_from_object_sync(
        _fig_dicts, kopts={'timeout': _to, 'mathjax': False})
    if _errs:
        with open(os.path.join(_d, 'errors.log'), 'w', encoding='utf-8') as _fh:
            _fh.write('\n'.join(repr(_e) for _e in _errs))
except Exception:
    with open(os.path.join(_d, 'errors.log'), 'w', encoding='utf-8') as _fh:
        _fh.write(traceback.format_exc())
for _r in glob.glob(os.path.join(_d, 'raw_*.png')):
    try:
        os.replace(_r, os.path.join(_d, os.path.basename(_r).replace('raw_', 'out_', 1)))
    except Exception:
        pass
"""


def _render_figs_batch(figs, scale=2):
    """{ad: fig} → {ad: png_bytes}. TÜM figürler TEK süreçte, TEK Kaleido session'ında (TEK Chrome
    açılışı) render edilir. Asılırsa süreç ağacı öldürülür; eksik kalanlar taze süreçle yeniden
    denenir. None figür atlanır. Hepsi denendikten sonra hâlâ eksik varsa sebep mesaja işlenir."""
    import time as _time
    pending = {k: f.to_json() for k, f in figs.items() if f is not None}
    out, last_err = {}, None
    for _attempt in range(_KALEIDO_RETRY):
        if not pending:
            break
        try:
            got, err = _render_batch_once(pending, scale)
        except Exception as _e:
            got, err = {}, repr(_e)
        out.update(got)
        if err:
            last_err = err
        pending = {k: v for k, v in pending.items() if k not in got}
        if pending:
            _time.sleep(1.0)   # öldürülen Chrome'un tam ölmesine kısa pay
    if pending:
        _detay = f" — sebep: {last_err}" if last_err else ""
        raise RuntimeError(f"kaleido render eksik kaldı: {sorted(pending)}{_detay}")
    return out


def _render_batch_once(fig_jsons, scale):
    """Tek deneme: alt süreç TÜM figürleri TEK Kaleido session'ında render eder, session dönünce
    her PNG'yi atomik rename ile out_ olarak bırakır. Parent out_ dosyalarını belirdikçe toplar —
    Chrome KAPANIŞINI BEKLEMEZ (asılan yer orası), hepsi gelince süreç ağacını öldürür. Kısmi başarı
    da döner (kalan retry'a kalır). Döner: (toplanan {ad: bytes}, eksikse errors.log metni | None)."""
    import subprocess, tempfile, os, sys, shutil, time
    _td = tempfile.mkdtemp(prefix="smr_ig_")
    _got, _err = {}, None
    try:
        for _name, _js in fig_jsons.items():
            with open(os.path.join(_td, f"fig_{_name}.json"), "w", encoding="utf-8") as _f:
                _f.write(_js)
        _script = os.path.join(_td, "render.py")
        with open(_script, "w", encoding="utf-8") as _f:
            _f.write(_RENDER_SUBPROCESS_SRC)
        _flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        # 30 Eyl 2026 — Linux'ta alt süreç KENDİ grubunda başlamalı: _kill_tree os.killpg ile
        # grubu öldürür; ayrı grup yoksa grup = ÇAĞIRANIN grubu → Streamlit/bot kendini
        # SIGKILL'liyordu (VPS'te GC=F denemesi exit 137). Windows yolu değişmedi.
        _proc = subprocess.Popen(
            [sys.executable, _script, _td, str(scale), str(_KALEIDO_FIG_TIMEOUT)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_flags,
            start_new_session=(os.name != "nt"),
        )
        _want = set(fig_jsons)
        _t0 = time.time()
        try:
            while time.time() - _t0 < _KALEIDO_RENDER_TIMEOUT:
                for _name in list(_want - set(_got)):
                    _p = os.path.join(_td, f"out_{_name}.png")
                    if os.path.exists(_p):
                        with open(_p, "rb") as _f:
                            _got[_name] = _f.read()
                if len(_got) == len(_want):
                    break                          # hepsi hazır — kapanışı bekleme
                if _proc.poll() is not None:
                    break                          # süreç bitti; aşağıda son toplama turu
                time.sleep(0.25)
            for _name in list(_want - set(_got)):  # yarış payı: son turda belirmiş olabilir
                _p = os.path.join(_td, f"out_{_name}.png")
                if os.path.exists(_p):
                    with open(_p, "rb") as _f:
                        _got[_name] = _f.read()
        finally:
            if _proc.poll() is None:               # asılı browser dahil süreç ağacını öldür
                _kill_tree(_proc.pid)
        if len(_got) < len(_want):                 # eksik varsa alt sürecin yazdığı sebebi al
            _elog = os.path.join(_td, "errors.log")
            if os.path.exists(_elog):
                try:
                    with open(_elog, encoding="utf-8") as _f:
                        _err = (_f.read().strip() or None)
                    if _err:
                        _err = _err.replace("\n", " ")[:400]
                except Exception:
                    pass
    finally:
        shutil.rmtree(_td, ignore_errors=True)
    return _got, _err


def _kill_tree(pid):
    import subprocess, os, signal
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass


def _fig_b64(fig):
    return base64.b64encode(_render_figs_batch({'f': fig}, 2)['f']).decode()


# 27 Ağu 2026 — ana grafik yüksekliği (eski 300). Sol kolon daraldı + bu büyüdü →
# "asıl önemli grafik" olan mum grafiği görselin merkezine oturdu (kullanıcı).
_CHART_H = 470


def _figs_b64_3lu(ticker):
    """İnfografiğin 3 figürünü (ana grafik + ivme + momentum) TEK Chrome açılışıyla render eder.
    Döner: (chart_b64, ivme_b64, harsi_block_html). Eski yol 3 ayrı render = 3 kat yavaş + kırılgandı."""
    _figs = _render_figs_batch({
        'chart': cc.build_fig(ticker, height=_CHART_H),
        'ivme':  cc.build_ivme_fig(ticker),
        'harsi': cc.build_harsi_fig(ticker),
    })
    _b64 = {k: base64.b64encode(v).decode() for k, v in _figs.items()}
    _hblk = (_ind_wrap('Momentum göstergesi',
                       f"<img src='data:image/png;base64,{_b64['harsi']}' style='width:100%;'/>")
             if 'harsi' in _b64 else '')
    return _b64.get('chart', ''), _b64.get('ivme', ''), _hblk


def _market_stats(ticker, df):
    """RS Gücü (vs XU100) + Beta + RVOL — XU100 parquet ile."""
    out = dict(rs=None, beta=None, rvol=None, rs_hist=None)
    try:
        v = df['Volume']; m = float(v.tail(20).mean())
        out['rvol'] = float(v.iloc[-1] / m) if m > 0 else None
    except Exception: pass
    try:
        idx = ig.load('XU100')
        if idx is not None:
            sc = df['Close']; ic = idx['Close']
            common = sc.index.intersection(ic.index)
            sc = sc.reindex(common); ic = ic.reindex(common)
            if len(common) >= 14:
                # 7 Ağu 2026 — RS TEK KAYNAK 10g (indicators.relative_strength_ratio;
                # app kartı ile aynı fonksiyon → pencere ayrışamaz).
                from indicators import relative_strength_ratio as _rsr
                _rsv = _rsr(sc.values, ic.values, window=10)
                if _rsv is not None:
                    out['rs'] = _rsv
                # 27 Ağu 2026 — RS üst şeritten SOL KOLONA indi (20g mini bar şeridi).
                # Seri aynı TEK KAYNAK formülle üretilir: her gün için o güne kadarki
                # 10 günlük pencere → tek değer ile şerit ASLA ayrışamaz (son bar = out['rs']).
                _sv = sc.values; _iv = ic.values
                if len(_sv) >= 30:
                    _hist = [_rsr(_sv[:len(_sv) - _k + 1], _iv[:len(_iv) - _k + 1], window=10)
                             for _k in range(20, 0, -1)]
                    if any(x is not None for x in _hist):
                        out['rs_hist'] = _hist
            if len(common) >= 60:
                sr = sc.pct_change().dropna(); ir = ic.pct_change().dropna()
                k = min(len(sr), len(ir), 252)
                sr = sr.iloc[-k:].values; ir = ir.iloc[-k:].values
                var = float(np.var(ir))
                if var > 0: out['beta'] = float(np.cov(sr, ir)[0, 1] / var)
    except Exception: pass
    # STP (sentetik eğilim = tipik fiyatın EMA6'sı) — fiyat üstünde/altında, kaç gün, kesti mi
    try:
        tp = (df['High'] + df['Low'] + df['Close']) / 3
        stp = tp.ewm(span=6, adjust=False).mean()
        st = (df['Close'].values - stp.values) >= 0     # True=üstünde
        above = bool(st[-1]); n = 1
        for i in range(len(st) - 2, -1, -1):
            if st[i] == above: n += 1
            else: break
        out['stp_above'] = above; out['stp_days'] = int(n); out['stp_crossed'] = (n == 1)
    except Exception: pass
    return out


def _statbox(d, ms):
    def cell(lbl, val, clr, sep):
        br = f"border-right:1px solid {LINE};" if sep else ""
        return (f"<div style='text-align:center;padding:2px 14px;{br}'>"
                f"<div style='font-size:10px;color:{MUT};letter-spacing:0.3px;'>{lbl}</div>"
                f"<div style='font-size:16px;font-weight:800;color:{clr};'>{val}</div></div>")
    sa = ms.get('stp_above'); sd = ms.get('stp_days'); sx = ms.get('stp_crossed')
    if sa is None:
        stp_v = '—'; stp_c = TXT
    else:
        ar = '↑' if sa else '↓'; stp_c = UP if sa else DN
        stp_v = f"{ar} kesti" if sx else f"{ar} {sd}g"
    # 29 Tem 2026 — sadeleştirme ("nokta atışı"): RSI + BETA hücreleri KALDIRILDI.
    # 27 Ağu 2026 — üst şerit TEK SATIR oldu (isim · fiyat · vade · rakam · özet):
    # RS GÜCÜ sol kolona 20g mini şerit olarak indi (_rs_mini_bars), MOMENTUM kaldırıldı.
    # Üst şeritte yalnız HACİM + STP kalır.
    items = []
    _rv = ms.get('rvol')
    if _rv is None:
        items.append(('HACİM', '—', TXT))
    else:
        _rv_c = UP if _rv > 1.2 else (DN if _rv < 0.8 else TXT)
        items.append(('HACİM', f"{_rv:.1f}×", _rv_c))
    items.append(('STP', stp_v, stp_c))
    cells = ''.join(cell(l, v, c, i < len(items) - 1) for i, (l, v, c) in enumerate(items))
    return (f"<div style='display:flex;align-items:center;border:1px solid {LINE};border-radius:10px;"
            f"background:{CARD2};padding:4px 2px;'>{cells}</div>")


def _rsi_band(d):
    """RSI(14) gelişimi — sol kolonda iki ana kanıt panelinin arasındaki ince bant."""
    try:
        _r = d.get('rsi_track') or {}
        _now = float(_r['now']); _five = float(_r['five']); _fourteen = float(_r['fourteen'])
        _avg50 = float(_r['avg50'])
        _eps = 1.0
        if _now <= _five - _eps and _five <= _fourteen - _eps:
            _trend, _clr = 'RSI zayıflıyor', DN
        elif _now >= _five + _eps and _five >= _fourteen + _eps:
            _trend, _clr = 'RSI güçleniyor', UP
        elif _now >= _five + _eps and _now <= _fourteen - _eps:
            _trend, _clr = 'RSI toparlanıyor', UP
        elif _now <= _five - _eps and _now >= _fourteen + _eps:
            _trend, _clr = 'RSI soğuyor', GOLD
        elif _now <= min(_five, _fourteen) - _eps:
            _trend, _clr = 'RSI zayıflıyor', DN
        elif _now >= max(_five, _fourteen) + _eps:
            _trend, _clr = 'RSI güçleniyor', UP
        else:
            _trend, _clr = 'RSI yatay', GOLD
        return (f"<div style='background:{CARD};border:1px solid {LINE};border-radius:8px;"
                f"padding:6px 9px;margin-bottom:7px;'>"
                f"<div style='font-size:10px;line-height:1.25;white-space:nowrap;'>"
                f"<span style='font-weight:800;color:{MUT};'>MOMENTUM · RSI</span>"
                f"<span style='color:{_clr};font-weight:800;margin-left:6px;'>{_trend}</span></div>"
                f"<div style='font-size:9.5px;line-height:1.35;color:{MUT};white-space:nowrap;'>"
                f"(1g–5g–14g: <span style='color:{TXT};font-weight:800;'>"
                f"{_now:.0f}–{_five:.0f}–{_fourteen:.0f}</span> · 50g ort: "
                f"<span style='color:{TXT};font-weight:800;'>{_avg50:.0f}</span>)</div></div>")
    except Exception:
        return ""


def _decision_box(df, d, ms, compact=False):
    """Görseldeki verilerden kısa-vade karar özeti üretir; AL/SAT emri değildir.

    compact=True (27 Ağu 2026): kutu ÜST ŞERİDİN son hücresi olarak basılır
    (eskiden sağ kolonun tepesinde tam genişlikteydi) → alt boşluk yok, kolonun
    boyuna uzar, punto bir tık küçük."""
    try:
        from smr_core import _genel_ozet_verdict_sc
        _v = _genel_ozet_verdict_sc(df, detail=True) or {}
    except Exception:
        _v = {}
    _net = str(_v.get('lbl') or 'KARARSIZ')
    _sigs = _v.get('sigs') or {}
    _trend_down = bool(d.get('last') is not None and d.get('sma', {}).get(50) is not None
                       and d['last'] < d['sma'][50])
    _trend_up = bool(d.get('last') is not None and d.get('sma', {}).get(50) is not None
                     and d['last'] > d['sma'][50])
    if _net in ('YUKARI ★', 'YUKARI', 'HAFİF YUKARI'):
        _short = 'Tepki yükselişi' if _trend_down else 'Kısa vadeli yukarı'
    elif _net in ('AŞAĞI ★', 'AŞAĞI', 'HAFİF AŞAĞI'):
        _short = 'Geri çekilme' if _trend_up else 'Kısa vadeli zayıflık'
    else:
        _short = 'Yatay / kararsız'

    _d5 = float(d.get('d5_signed') or 0)
    _buyer = 'Kısa vadede iyileşmiş' if _d5 > 0 else ('Zayıflıyor' if _d5 < 0 else 'Dengede')
    _rvol = ms.get('rvol')
    if _rvol is None:
        _volume = 'Veri yok'
    elif _rvol < 0.8:
        _volume = 'Normalin altında'
    elif _rvol <= 1.2:
        _volume = 'Yok denecek kadar normal'
    else:
        _volume = 'Var'
    _obv = int(_sigs.get('obv') or 0); _cmf = int(_sigs.get('cmf') or 0)
    _accum = 'İzi var' if _obv > 0 and _cmf > 0 else ('Dağıtım baskısı' if _obv < 0 and _cmf < 0 else 'Henüz yok')
    try:
        _h = float(df['High'].iloc[-1]); _l = float(df['Low'].iloc[-1]); _c = float(df['Close'].iloc[-1])
        _close_pct = ((_c - _l) / (_h - _l) * 100) if _h > _l else 50.0
    except Exception:
        _close_pct = 50.0
    _close = 'Satıcı tarafında' if _close_pct <= 35 else ('Alıcı tarafında' if _close_pct >= 65 else 'Dengede')
    if _trend_down and _net in ('YUKARI ★', 'YUKARI', 'HAFİF YUKARI'):
        _headline = 'Ana yapı aşağıda; son hareket tepki yükselişi gibi görünüyor.'
        _risk = 'Bu tepki yükselişi yeniden aşağı dönebilir.'
    elif _trend_up and _net in ('AŞAĞI ★', 'AŞAĞI', 'HAFİF AŞAĞI'):
        _headline = 'Ana yapı yukarıda, ancak kısa vadede geri çekilme var.'
        _risk = 'Geri çekilme derinleşebilir.'
    elif _trend_down:
        _headline = 'Ana yapı aşağıda ve kısa vadeli baskı sürüyor.'
        _risk = 'Hacim teyidi olmazsa hareket daha da zayıflayabilir.'
    elif _trend_up:
        _headline = 'Ana yapı yukarıda ve kısa vadeli hareket destekli.'
        _risk = 'Hacim desteği azalırsa yükseliş ivme kaybedebilir.'
    else:
        _headline = 'Ana yapı yatay; kısa vadede net bir yön oluşmuş değil.'
        _risk = 'Yeni bir teyit gelmezse hareket yatay kalabilir.'
    _buyer_txt = ('Son 5 günde alıcı ilgisi artmış' if _d5 > 0 else
                  'Son 5 günde alıcı ilgisi zayıflamış' if _d5 < 0 else
                  'Son 5 günde alıcı-satıcı dengesi belirgin değil')
    _flow_txt = ('20 günlük akışta birikim izi var' if _obv > 0 and _cmf > 0 else
                 '20 günlük akışta dağıtım baskısı var' if _obv < 0 and _cmf < 0 else
                 '20 günlük akış kalıcı birikimi henüz doğrulamıyor')
    _volume_txt = ('Hacim verisi yok' if _rvol is None else
                   'Hacim ortalamanın altında' if _rvol < 0.8 else
                   'Hacim normal seviyede' if _rvol <= 1.2 else
                   'Hacim yükselmiş')
    _close_txt = (' Seans satıcı tarafında kapandı.' if _close == 'Satıcı tarafında' else
                  ' Seans alıcı tarafında kapandı.' if _close == 'Alıcı tarafında' else '')
    _summary = (f"{_headline} {_buyer_txt}; {_flow_txt} ve {_volume_txt.lower()}."
                f"{_close_txt} {_risk}")
    _fs = '10.5px' if compact else '11px'
    _body = (f"<div style='font-size:{_fs};line-height:1.45;color:{TXT};"
             f"max-width:100%;'>{_summary}</div>")
    _outer = ("padding:8px 11px;height:100%;box-sizing:border-box;" if compact
              else "padding:9px 11px;margin-bottom:10px;width:100%;box-sizing:border-box;")
    return (f"<div style='background:{CARD};border:1px solid {INFO}66;border-radius:10px;{_outer}'>"
            f"<div style='font-size:11px;font-weight:800;color:{INFO};margin-bottom:4px;"
            f"letter-spacing:0.02em;'>SMART MONEY RADAR · ALGORİTMİK ÖZET</div>"
            f"{_body}</div>")


def _card(title, body):
    return (f"<div style='background:{CARD};border:1px solid {LINE};border-radius:10px;"
            f"padding:9px 12px;margin-bottom:7px;'>"
            f"<div style='font-size:12px;font-weight:700;color:{MUT};margin-bottom:3px;'>{title}</div>"
            f"<div style='font-size:15px;line-height:1.5;color:{TXT};'>{body}</div></div>")


def _card_warn(title, body):
    """UYARI kartı — kırmızı kenar+başlık (çelişki/risk varsa)."""
    return (f"<div style='background:{CARD};border:1px solid {DN}66;border-radius:10px;"
            f"padding:9px 12px;margin-bottom:7px;'>"
            f"<div style='font-size:12px;font-weight:700;color:{DN};margin-bottom:3px;'>{title}</div>"
            f"<div style='font-size:15px;line-height:1.5;color:{TXT};'>{body}</div></div>")


def _rs_mini_bars(hist):
    """RS GÜCÜ · 20g mini bar şeridi (27 Ağu 2026 — üst şeritten sol kolona indi).

    Taban kesikli çizgi = 1.00× (endeksle başa baş): üstü endeksi geçiyor, altı geride.
    Son gün barı diğerlerinden %30 GENİŞ + tam opak → "bugün ne oldu" ilk bakışta okunur.
    Seri _market_stats['rs_hist']'ten gelir (aynı tek-kaynak RS formülü)."""
    LBL = '#94a3b8'
    try:
        vals = [float(x) for x in (hist or []) if x is not None and x == x]
        if len(vals) < 5:
            return ""
        n = len(vals); W, H = 220, 60; mid = H / 2
        lim = max(1e-9, max(abs(v - 1.0) for v in vals) * 1.15)
        bw = W / n
        p = [f"<line x1='0' y1='{mid}' x2='{W}' y2='{mid}' stroke='#475569' "
             f"stroke-width='1' stroke-dasharray='3,3'/>"]
        for i, v in enumerate(vals):
            dv = v - 1.0
            bh = max(abs(dv) / lim * (mid - 3), 0.8)
            clr = UP if dv >= 0 else DN
            last = (i == n - 1)
            bxw = bw * min(0.7 * 1.30, 0.96) if last else bw * 0.7   # son gün %30 geniş
            bx = i * bw + (bw - bxw) / 2
            by = mid - bh if dv >= 0 else mid
            p.append(f"<rect x='{bx:.1f}' y='{by:.1f}' width='{bxw:.1f}' height='{bh:.1f}' "
                     f"fill='{clr}' opacity='{'1' if last else '0.72'}' rx='1'/>")
        _lv = vals[-1]; _lc = UP if _lv >= 1 else DN
        return (f"<div style='flex:1;min-width:0;'>"
                f"<div style='display:flex;justify-content:space-between;font-size:9px;"
                f"color:{LBL};margin-bottom:1px;gap:4px;'>"
                f"<span style='white-space:nowrap;'>RS gücü · 20g</span>"
                f"<span style='color:{_lc};font-weight:800;white-space:nowrap;'>bugün: {_lv:.2f}×</span></div>"
                f"<svg width='100%' height='{H}' viewBox='0 0 {W} {H}' preserveAspectRatio='none' "
                f"style='display:block;'>" + "".join(p) + "</svg></div>")
    except Exception:
        return ""


def _signal_box(df, d, ms=None):
    """GENEL ÖZET üst doğrulama bandı — 13 Tem 2026 V10 senkronu.
    ESKİ standalone 4-oy kopyası SİLİNDİ (backtest'te ters çalışıyordu, app ile
    çelişiyordu) → TEK KAYNAK: smr_core._genel_ozet_verdict_sc (app pack ile
    mantık eşitliği 5/5 kanıtlı, 600 hisse × 56K örnek V10 karne-ağırlıklı).
    Verdict + oy özeti + karne · 6 ok hücresi (RSI×2/CMF×2) · 5 mum + tarih ·
    trafik ışıkları · momentum + OBV gidişat çizgileri. AL/SAT dili YOK."""
    NEU = '#64748b'; LBL = '#94a3b8'
    # ── V10 verdicti — tek kaynak (lazy import: smr_core ağır modül) ───
    try:
        from smr_core import _genel_ozet_verdict_sc
        _v = _genel_ozet_verdict_sc(df, detail=True)
    except Exception:
        _v = None
    if _v:
        net = _v['lbl']; up = _v['up']; dn = _v['dn']; _karne = _v['karne']
        _s = _v['sigs']
        sig_hacim = _s['hacim']; sig_obv = _s['obv']; sig_yapi = _s['yapi']
        sig_rsi = _s['rsi']; sig_cmf = _s['cmf']; sig_mfi = _s['mfi']
    else:
        net, up, dn, _karne = "KARARSIZ", 0, 0, ""
        sig_hacim = sig_obv = sig_yapi = sig_rsi = sig_cmf = sig_mfi = 0
    nc = {"YUKARI ★": "#22c55e", "YUKARI": "#4ade80", "HAFİF YUKARI": "#86efac",
          "AŞAĞI ★": "#dc2626", "AŞAĞI": "#f87171", "HAFİF AŞAĞI": "#fca5a5"}.get(net, "#fbbf24")
    notr = 6 - up - dn
    oy_ozet = (f"<span style='color:{UP};font-weight:800;'>{up} yukarı ▲</span>"
               f"<span style='color:{NEU};'> · </span>"
               f"<span style='color:{DN};font-weight:800;'>{dn} aşağı ▼</span>"
               f"<span style='color:{NEU};'> · </span>"
               f"<span style='color:{NEU};font-weight:700;'>{notr} sessiz →</span>")
    karne_html = (f"<div style='font-size:10px;color:{NEU};font-style:italic;margin-top:3px;'>"
                  f"📊 Bu dağılımın geçmiş karnesi{_karne.replace(' · geçmiş karnesi', '')}</div>"
                  if _karne else "")

    # 27 Ağu 2026 — _cell() (YAPI/RSI çipi) SİLİNDİ: tek tüketicisi DESTEKLEYİCİ TEKNİK bloğuydu.
    # ── 3 BAĞIMSIZ AİLE (7 Ağu 2026 — kanıt aile reformu) ─────────────
    # A fiyat-hacim (hacim/obv/cmf/mfi) · B efor-sonuç (UDVR+Force Index) ·
    # C gerçek akış (yabancı net alım). Hüküm = çoğunluk yönü (beraberlik→nötr);
    # veri yoksa aile BASILMAZ (POC gibi koşullu). obv oyu zaten uyumsuzluk-duyarlı
    # (fiyat↑ + OBV↓ → -1) → #4 A ailesine gömülü. RSI+YAPI aile değil → altta çip.
    def _fam_dir(votes):
        vp = sum(1 for s in votes if s > 0); vn = sum(1 for s in votes if s < 0)
        return 1 if vp > vn else (-1 if vn > vp else 0)

    _fam = []   # (dir, terim, alt_yazı, hüküm_metni)
    # A — fiyat-hacim izi
    _a_dir = _fam_dir([sig_hacim, sig_obv, sig_cmf, sig_mfi])
    if _a_dir > 0:               _a_alt = "Fiyat-hacim verisinde para izi"
    elif _a_dir < 0 and sig_obv < 0: _a_alt = "Fiyat yükselirken OBV teyit etmiyor"
    elif _a_dir < 0:             _a_alt = "Fiyat-hacim verisi zayıflıyor"
    else:                        _a_alt = "Fiyat-hacim verisi yönsüz"
    _fam.append((_a_dir, "HACİM · OBV · CMF · MFI", _a_alt,
                 {1: "▲ birikim izi", -1: "▼ dağıtım izi", 0: "→ yönsüz"}[_a_dir]))
    # B — efor/sonuç (UDVR + Force Index)
    _b_votes = []
    try:
        from indicators import compute_updown_volume_ratio, compute_force_index_dual
        _ur = compute_updown_volume_ratio(df, period=20)
        if _ur:
            _cl = _ur.get('climax')
            if _cl == 'climax_top':      _b_votes.append(-1)
            elif _cl == 'climax_bottom': _b_votes.append(1)
            else: _b_votes.append({'strong_buyer': 1, 'buyer': 1, 'strong_seller': -1,
                                   'seller': -1, 'balanced': 0}.get(_ur.get('state'), 0))
        _fi = compute_force_index_dual(df)
        if _fi:
            _b_votes.append({'strong_pos': 1, 'pos': 1, 'turning_up': 1, 'strong_neg': -1,
                             'neg': -1, 'turning_down': -1, 'neutral': 0}.get(_fi.get('state'), 0))
    except Exception:
        _b_votes = []
    if _b_votes:
        _b_dir = _fam_dir(_b_votes)
        _fam.append((_b_dir, "UDVR · Force Index", "Harcanan çaba, sonuca dönüyor mu?",
                     {1: "▲ alıcı egemen", -1: "▼ satıcı egemen", 0: "→ sessiz"}[_b_dir]))
    # C — gerçek akış (yabancı net alım) — BIST dışı / veri yoksa BASILMAZ
    try:
        from db_layer import _compute_mkk_yabanci_signals
        _yb = _compute_mkk_yabanci_signals(d.get('ticker', ''))
    except Exception:
        _yb = None
    # Gerçek veri şartı: mkk tablosunda bu hisse için EN AZ 1 yönlü kayıt olmalı
    # (BIST ama kaydı yok → "nötr" değil "veri yok" → aile basılmaz).
    if _yb and (_yb.get('in_days') or _yb.get('out_days') or _yb.get('streak_days')):
        # AI PROMPT İLE UYUM (30 Haz karne): tek-gün GİRİŞ tek başına bullish DEĞİL
        # (ileri getiri TERS -%10, isabet %20). Sadece SÜREKLİLİK (streak/anchor)
        # pozitif; tek-gün giriş → temkinli/nötr; çıkış → negatif. app.py ~16146 ile aynı.
        if _yb.get('f_yabanci_anchor') or _yb.get('f_yabanci_streak'):
            _c_dir = 1
        elif _yb.get('f_yabanci_cikis'):
            _c_dir = -1
        else:
            _c_dir = 0
        _c_alt = ("Yabancı oran 3+ gün üst üste artıyor" if _c_dir > 0 else
                  "Son 5g net satış listesinde" if _c_dir < 0 else
                  "Tek-gün giriş var ama süreklilik yok")
        _fam.append((_c_dir, "Yabancı Net Alım", _c_alt,
                     {1: "▲ süreklilik", -1: "▼ net satım", 0: "→ temkinli"}[_c_dir]))

    # Hüküm — kaç aile aynı yönde
    _n = len(_fam)
    _nu = sum(1 for f in _fam if f[0] > 0); _nd = sum(1 for f in _fam if f[0] < 0)
    _dom = max(_nu, _nd); _dom_dir = 1 if _nu >= _nd else -1
    if _dom == _n and _n >= 2:
        _vlbl = "tam mutabakat — GÜÇLÜ"; _vclr = UP if _dom_dir > 0 else DN
    elif _dom <= 1 and _n >= 2:
        _vlbl = "tek aile teyidi — ZAYIF"; _vclr = GOLD
    else:
        _vlbl = "kısmi mutabakat — ORTA"; _vclr = GOLD
    _verdict_html = (
        f"<div style='display:flex;align-items:center;gap:9px;margin:5px 0 7px;'>"
        f"<div style='font-size:18px;font-weight:800;color:{_vclr};'>{_dom} / {_n}</div>"
        f"<div style='line-height:1.15;'>"
        f"<div style='font-size:11px;color:{TXT};font-weight:700;'>aile aynı yönde</div>"
        f"<div style='font-size:9.5px;color:{_vclr};'>{_vlbl}</div></div></div>") if _n else ""

    _fclr = {1: UP, -1: DN, 0: NEU}
    _fbg = {1: "#4ade8014", -1: "#f8717114", 0: "#64748b14"}
    _fbd = {1: "#4ade8040", -1: "#f8717140", 0: "#64748b40"}
    family_cards = "".join(
        f"<div style='background:{_fbg[fd]};border:1px solid {_fbd[fd]};border-radius:8px;"
        f"padding:6px 9px;margin-bottom:5px;'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
        f"<span style='font-size:11px;font-weight:800;color:{_fclr[fd]};'>{trm}</span>"
        f"<span style='font-size:10px;font-weight:700;color:{_fclr[fd]};'>{vt}</span></div>"
        f"<div style='font-size:9.5px;color:{MUT};margin-top:1px;'>{alt}</div></div>"
        for fd, trm, alt, vt in _fam)

    # 27 Ağu 2026 — "DESTEKLEYİCİ TEKNİK" (YAPI + RSI×2 çipleri) KALDIRILDI.
    # Yerine üst şeritten inen RS GÜCÜ 20g mini şeridi geçti (kullanıcı kararı).
    support_html = _rs_mini_bars((ms or {}).get('rs_hist'))

    # 27 Ağu 2026 — "son 5 mum" mini şeridi SİLİNDİ (kullanıcı): sol kolon daraldı,
    # yeri iki bar şeridine ve dipteki uyarı rozetine gitti.

    # ── AKILLI PARA İZLERİ — 3 sparkline (20 Tem 2026: çizgi→BAR + 3. sinyal CMF) ──
    # Sıfır-merkezli günlük histogram (GENEL ÖZET diliyle uyumlu; MACD-histogram mantığı).
    # 3 ayrı lens: (1) Momentum ivme deltası (2) OBV deltası = close YÖNÜ×hacim
    # (3) CMF = close'un gün-içi RANGE KONUMU → ilk ikisinin göremediği gizli dağıtımı yakalar.
    def _delta_bars(vals, pos_clr, neg_clr, title, pos_w, neg_w):
        try:
            if not vals or len(vals) < 5:
                return ""
            # 27 Ağu 2026 — tek şerit kaldı (Kapanış gücü) → yüksekliği %50 arttı (40→60):
            # barların gücü iyice belli olsun (kullanıcı).
            n = len(vals); w, h = 220, 60; mid = h / 2
            lim = max(1e-9, max(abs(x) for x in vals) * 1.1)
            bw = w / n
            p = [f"<line x1='0' y1='{mid}' x2='{w}' y2='{mid}' stroke='#475569' "
                 f"stroke-width='1' stroke-dasharray='3,3'/>"]
            for i, x in enumerate(vals):
                bh = max(abs(x) / lim * (mid - 3), 0.8)
                clr = pos_clr if x > 0 else neg_clr
                op = "1" if i == n - 1 else "0.72"          # 'bugün' (son) tam opak
                bx = i * bw + bw * 0.15
                by = mid - bh if x > 0 else mid
                p.append(f"<rect x='{bx:.1f}' y='{by:.1f}' width='{bw * 0.7:.1f}' "
                         f"height='{bh:.1f}' fill='{clr}' opacity='{op}' rx='1'/>")
            lc2 = pos_clr if vals[-1] > 0 else neg_clr
            son = pos_w if vals[-1] > 0 else neg_w
            return (f"<div style='flex:1;min-width:0;'>"
                    f"<div style='display:flex;justify-content:space-between;font-size:9px;"
                    f"color:{LBL};margin-bottom:1px;gap:4px;'><span style='white-space:nowrap;overflow:hidden;"
                    f"text-overflow:ellipsis;'>{title} · 20g</span>"
                    f"<span style='color:{lc2};font-weight:800;white-space:nowrap;'>bugün: {son}</span></div>"
                    f"<svg width='100%' height='{h}' viewBox='0 0 {w} {h}' preserveAspectRatio='none' "
                    f"style='display:block;'>" + "".join(p) + "</svg></div>")
        except Exception:
            return ""

    gidisat = ""
    try:
        _cvals = []
        if 'Volume' in df.columns and {'High', 'Low', 'Close'}.issubset(df.columns):
            _vv = df['Volume'].fillna(0).astype(float)
            _cc2 = df['Close'].astype(float)
            _oa = float(_vv.rolling(20).mean().iloc[-1])
            if _oa > 0:
                # CMF günlük (close'un gün-içi range konumu × hacim) — hacme normalize, sıfır-merkezli
                _hl = (df['High'].astype(float) - df['Low'].astype(float))
                _mfm = np.where(_hl > 0,
                                ((_cc2 - df['Low'].astype(float)) - (df['High'].astype(float) - _cc2)) / _hl,
                                0.0)
                _mfv = _vv * _mfm   # Series × np-array = Series (pozisyonel, _vv indeksinde); pd import gerekmez
                _cvals = [float(x) / _oa for x in _mfv.tail(20) if x == x]
        # 27 Ağu 2026 — "Momentum ivmesi" + "OBV gidişatı" şeritleri KALDIRILDI (kullanıcı):
        # sol kolonda yalnız Kapanış gücü kalır, buna karşılık barlar %50 daha yüksek.
        # "Kapanış gücü" (para akışı DEĞİL): metin zaten "para akışı" = 20g CMF toplamını kullanıyor;
        # bu sparkline BUGÜNKÜ günün range-içi kapanışı → aynı isim iki yönde çelişki görünüyordu.
        _l3 = _delta_bars(_cvals, "#22d3ee", "#fb7185", "Kapanış gücü", "alıcı baskın", "satıcı baskın")
        if _l3:
            gidisat = (f"<div style='display:flex;flex-direction:column;gap:6px;margin-top:8px;'>"
                       + _l3 + "</div>")
    except Exception:
        gidisat = ""

    # ── 5g/20g/50g/200g trafik ışıkları ───────────────────────────────
    tf = ""
    try:
        cl = df['Close'].astype(float); cn = float(cl.iloc[-1]); tfs = []
        if len(cl) >= 6:
            p5 = float(cl.iloc[-6]); tfs.append(("5g", 1 if cn > p5 else (-1 if cn < p5 else 0)))
        else: tfs.append(("5g", 0))
        for _lbl, _p in (("20g", 20), ("50g", 50)):
            if len(cl) >= _p:
                _sv = float(cl.rolling(_p).mean().iloc[-1]); tfs.append((_lbl, 1 if cn > _sv else -1))
            else: tfs.append((_lbl, 0))
        cells = []
        for lbl, dd in tfs:
            tc = UP if dd > 0 else (DN if dd < 0 else NEU)
            cells.append(f"<div style='display:flex;align-items:center;gap:5px;padding:1px 4px;line-height:1.1;"
                         f"justify-content:space-between;min-width:48px;'>"
                         f"<span style='font-size:9px;color:{LBL};font-weight:700;'>{lbl}</span>"
                         f"<span style='display:inline-block;width:9px;height:9px;border-radius:50%;"
                         f"background:{tc};box-shadow:0 0 4px {tc}99;'></span></div>")
        # 27 Ağu 2026 — 5 mum silinince ışıklar tek başına kaldı: DİKEY→YATAY şerit
        # (dar kolonda dikey sıra boşuna 3 satır yer yiyordu).
        tf = ("<div style='display:flex;flex-direction:row;gap:4px;justify-content:center;'>"
              + "".join(cells) + "</div>")
    except Exception:
        pass

    # ── LONG çubuğu (infografik genel-sağlık kompoziti) ───────────────
    hv = int(d.get('health', 50) or 50)
    hc = UP if hv >= 55 else (GOLD if hv >= 40 else DN)
    longbar = (f"<span style='display:inline-block;width:46px;height:5px;background:#1e293b;border-radius:3px;"
               f"position:relative;margin-right:5px;vertical-align:middle;'>"
               f"<span style='position:absolute;left:0;top:0;width:{min(hv,100)}%;height:100%;background:{hc};"
               f"border-radius:3px;box-shadow:0 0 4px {hc};'></span></span>"
               f"<span style='color:{hc};font-size:12px;'>{hv}/100</span>")

    return (f"<div style='background:rgba(56,189,248,0.07);border:1px solid {LINE};border-radius:10px;"
            f"padding:9px 11px;'>"
            f"<div style='font-family:\"JetBrains Mono\",ui-monospace,Consolas,monospace;"
            f"font-size:14px;font-weight:800;color:{TXT};'>KANIT DAĞILIMI · AKILLI PARA</div>"
            f"{_verdict_html}"
            f"{karne_html}"
            f"{family_cards}"
            f"<div style='margin-top:6px;'>{tf}</div>"
            # 27 Ağu 2026 — RS şeridi TAM GENİŞLİK: dar hücrede 20 bar okunmuyordu,
            # alttaki "Kapanış gücü" ile aynı ölçüde olsun (iki şerit tek dil).
            f"<div style='margin-top:8px;display:flex;'>{support_html}</div>"
            f"{gidisat}"
            f"</div>")


def _ind_wrap(title, inner):
    """İndikatör kabı — başlık + içerik (img veya inline svg)."""
    return (f"<div style='background:{CARD};border:1px solid {LINE};border-radius:10px;padding:8px;margin-bottom:8px;'>"
            f"<div style='font-size:12px;font-weight:700;color:{MUT};margin-bottom:5px;'>{title}</div>{inner}</div>")


def _ind_block_b64(title, fig):
    return _ind_wrap(title, f"<img src='data:image/png;base64,{_fig_b64(fig)}' style='width:100%;'/>") if fig is not None else ''


def _ind_block_svg(title, fig):
    return _ind_wrap(title, _fig_svg(fig)) if fig is not None else ''


# Görünen başlık etiketleri — sözlük ANAHTARLARI ('GENEL' vs) SABİT kalır (g[k] araması +
# 'if k in g' filtresi bunlara bağlı); sadece kartta GÖRÜNEN metin buradan değişir → kırılmaz.
_HDR = {'GENEL': 'GENEL DURUM', 'TEKNİK': 'İZLENECEK SEVİYELER', 'AKILLI PARA': 'AKILLI PARA İZLERİ'}

# 15 Tem 2026 — 'TEKNİK' render'a ALINDI. Öncesi: gorev4() her hissede
# Fibonacci/POC/VWAP/RSI metnini hesaplıyor, render listesi onu atlıyordu →
# görselde tek bir izlenecek seviye yoktu (grafikte çizili, metinde yok).
# SOL kolon: yorum kartları · SAĞ kolon: 'İZLENECEK SEVİYELER' grafiğin ALTINDA
# (seviyeler zaten o grafikte çizili) → hem doğru yer, hem iki kolon dengede.
# 20 Tem 2026 — 'SONUÇ' kartı ÇIKARILDI (kullanıcı kararı): sonuç/yönlendirme cümleleri
# yatırım tavsiyesi sayılabilir — hukuki risk. Kart üretimden düştü, gorev4 çıktısında kalsa da basılmaz.
_CARD_ORDER = ('GENEL', 'AKILLI PARA')


def build_html(ticker):
    df = ig.load(ticker)
    if df is None or len(df) < 60: return None
    d = ig.compute(ticker, df); g = ig.gorev4(d)
    tk = _disp_name(d['ticker'])
    chart, ivme, harsi_block = _figs_b64_3lu(ticker)   # 3 figür TEK Chrome açılışıyla (20 Tem)
    compass = cp.build_compass_html(ticker) or ""
    chg_clr = UP if d['chg'] >= 0 else DN; arrow = '▲' if d['chg'] >= 0 else '▼'
    _ms = _market_stats(ticker, df)
    stats = _statbox(d, _ms)
    cards = "".join(_card(_HDR.get(k, k), g[k]) for k in _CARD_ORDER if k in g)
    if 'UYARI' in g: cards += _card_warn('⚠ UYARI', g['UYARI'])
    decision = _decision_box(df, d, _ms, compact=True)
    levels = ''
    sbox = _signal_box(df, d, _ms)
    rsi_band = _rsi_band(d)
    notice = _notice_badge(fs=12)   # dar sol kolon → punto 17'den 12'ye
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',Arial,sans-serif;}}
body{{background:{BG};}}
img{{display:block;border-radius:8px;}}
</style></head><body>
<div id="infografik" style="width:980px;background:{BG};padding:16px;color:{TXT};">
  <!-- 27 Ağu 2026 — ÜST ŞERİT TEK SATIR: isim · fiyat · rakamlar · algoritmik özet.
       Özet buraya çıktı (eskiden sağ kolonun tepesindeydi) → sağ kolon doğrudan
       grafikle başlar, görsel bir grafik boyu kısalır. Eski mavi hook barı da
       kaldırıldı (ekran sürümünde 23 Tem'de kalkmıştı; ikisi ayrışmasın). -->
  <div style="display:flex;align-items:stretch;gap:10px;margin-bottom:12px;">
    <div style="flex:0 0 auto;display:flex;flex-direction:column;justify-content:center;">
      <div style="font-size:23px;font-weight:800;line-height:1.05;white-space:nowrap;">{tk}</div>
      <div style="font-size:11px;color:{MUT};letter-spacing:0.06em;white-space:nowrap;">TEKNİK GÖRÜNÜM</div>
    </div>
    <div style="flex:0 0 auto;background:#0c2238;border:1px solid {LINE};border-radius:10px;padding:8px 16px;text-align:right;display:flex;flex-direction:column;justify-content:center;">
      <div style="font-size:29px;font-weight:800;line-height:1.04;white-space:nowrap;">{d['last']:.2f}</div>
      <div style="font-size:16px;font-weight:700;color:{chg_clr};white-space:nowrap;">{arrow} %{abs(d['chg']):.2f}</div>
    </div>
    <div style="flex:0 0 auto;display:flex;align-items:center;">{stats}</div>
    <div style="flex:1;min-width:0;">{decision}</div>
  </div>
  <!-- 27 Ağu 2026: sol kolon 330→248 daraldı, kazanılan yer SAĞDAKİ GRAFİKLERE gitti.
       Uyarı rozeti sayfanın dibinden sol kolonun dibine indi (margin-top:auto). -->
  <div style="display:grid;grid-template-columns:248px 1fr;gap:12px;align-items:stretch;">
    <div style="display:flex;flex-direction:column;">{compass}<div style="height:8px;"></div>{rsi_band}{sbox}
      <div style="margin-top:auto;padding-top:8px;display:flex;">{notice}</div>
    </div>
    <!-- 27 Ağu 2026: ozet ust serite cikinca sag kolon kisaldi ve dipte delik kaldi.
         space-between → artan bosluk 3 grafik arasina esit dagilir (delik yerine nefes). -->
    <div style="display:flex;flex-direction:column;justify-content:space-between;">
      <div style="background:{CARD};border:1px solid {LINE};border-radius:10px;padding:8px;margin-bottom:8px;">
        <div style="font-size:12px;font-weight:700;color:{MUT};margin-bottom:5px;">Teknik yapı · mumlar + SMA50/EMA144/SMA100/SMA200 + POC + VWAP</div>
        <img src="data:image/png;base64,{chart}" style="width:100%;"/>
      </div>
      {levels}
      {harsi_block}
      <div style="background:{CARD};border:1px solid {LINE};border-radius:10px;padding:8px;margin-bottom:8px;">
        <div style="font-size:12px;font-weight:700;color:{MUT};margin-bottom:5px;">Para Akış İvmesi & Fiyat Dengesi</div>
        <img src="data:image/png;base64,{ivme}" style="width:100%;"/>
      </div>
    </div>
  </div>

</div>
</body></html>"""


def _fig_svg(fig):
    s = fig.to_image(format='svg').decode('utf-8')
    i = s.find('<svg')
    s = s[i:] if i >= 0 else s
    # responsive: kök <svg> width/height → %100 (viewBox korunur → kolona orantılı sığar)
    s = re.sub(r'(<svg\b[^>]*?)\swidth="\d+(?:\.\d+)?"\s+height="\d+(?:\.\d+)?"',
               r'\1 width="100%" height="auto"', s, count=1)
    return s


def build_widget_html(ticker):
    """show_widget için: Plotly figürleri inline SVG (base64 PNG değil), sadece iç div."""
    if X_LAYOUT:
        return build_x_html(ticker)
    df = ig.load(ticker)
    if df is None or len(df) < 60: return None
    d = ig.compute(ticker, df); g = ig.gorev4(d)
    tk = _disp_name(d['ticker'])
    # NOT: in-app'te inline SVG iframe'de oranını koruyamıyor (uzun render) → base64 PNG kullan.
    chart, ivme, harsi_block = _figs_b64_3lu(ticker)   # 3 figür TEK Chrome açılışıyla (20 Tem)
    compass = cp.build_compass_html(ticker) or ""
    chg_clr = UP if d['chg'] >= 0 else DN; arrow = '▲' if d['chg'] >= 0 else '▼'
    _ms = _market_stats(ticker, df)
    stats = _statbox(d, _ms)
    cards = "".join(_card(_HDR.get(k, k), g[k]) for k in _CARD_ORDER if k in g)
    if 'UYARI' in g: cards += _card_warn('⚠ UYARI', g['UYARI'])
    decision = _decision_box(df, d, _ms, compact=True)
    levels = ''
    sbox = _signal_box(df, d, _ms)
    rsi_band = _rsi_band(d)
    notice = _notice_badge(fs=12)   # dar sol kolon → punto 17'den 12'ye
    # 23 Tem 2026 — ANA KART parçaları buraya ENJEKTE edilir (app.py, ana thread).
    # Bu fonksiyon arka planda/terazisiz koştuğu için burada YALNIZ yer tutucu var;
    # terazi verisiyle doldurma _render_infografik_inapp'te yapılır (tek kaynak).
    #   <!--EKRANV2_YON-->        → başlık yanı (AŞAĞI/YUKARI + terazi barı)
    #   <!--EKRANV2_GECERSIZLIK--> → fiyatın yanı (ince geçersizlik şeridi)
    #   <!--EKRANV2_OYLAR-->      → başlık altı (aşağı/yukarı diyenler + ne değişti)
    return f"""<div style="background:{BG};padding:16px;color:{TXT};font-family:'Segoe UI',Arial,sans-serif;border-radius:12px;">
  <!-- 27 Ağu 2026 — ÜST ŞERİT TEK SATIR (PNG sürümüyle AYNI iskelet):
       isim · fiyat · vade kutusu · rakamlar · algoritmik özet. -->
  <div style="display:flex;align-items:stretch;gap:10px;margin-bottom:12px;">
    <div style="flex:0 0 auto;display:flex;flex-direction:column;justify-content:center;">
      <div style="font-size:23px;font-weight:800;line-height:1.05;white-space:nowrap;">{tk}</div>
      <div style="font-size:11px;color:{MUT};letter-spacing:0.06em;white-space:nowrap;">TEKNİK GÖRÜNÜM</div>
    </div>
    <div style="flex:0 0 auto;background:#0c2238;border:1px solid {LINE};border-radius:10px;padding:8px 16px;text-align:right;display:flex;flex-direction:column;justify-content:center;">
      <div style="font-size:29px;font-weight:800;line-height:1.04;white-space:nowrap;">{d['last']:.2f}</div>
      <div style="font-size:16px;font-weight:700;color:{chg_clr};white-space:nowrap;">{arrow} %{abs(d['chg']):.2f}</div>
    </div>
    <div style="flex:0 0 auto;display:flex;align-items:center;gap:10px;"><!--EKRANV2_YON--><!--EKRANV2_GECERSIZLIK--></div>
    <div style="flex:0 0 auto;display:flex;align-items:center;">{stats}</div>
    <div style="flex:1;min-width:0;">{decision}</div>
  </div>
  <!--EKRANV2_OYLAR-->
  <!-- 23 Tem 2026: hook barı kaldırıldı — "okunan bir bar değildi" (kullanıcı). -->
  <!-- 27 Ağu 2026: sol kolon 330→248 daraldı, kazanılan yer SAĞDAKİ GRAFİKLERE gitti.
       Uyarı rozeti sayfanın dibinden sol kolonun dibine indi (margin-top:auto). -->
  <div style="display:grid;grid-template-columns:248px 1fr;gap:12px;align-items:stretch;">
    <div style="display:flex;flex-direction:column;">{compass}<div style="height:8px;"></div>{rsi_band}{sbox}
      <div style="margin-top:auto;padding-top:8px;display:flex;">{notice}</div>
    </div>
    <!-- 27 Ağu 2026: ozet ust serite cikinca sag kolon kisaldi ve dipte delik kaldi.
         space-between → artan bosluk 3 grafik arasina esit dagilir (delik yerine nefes). -->
    <div style="display:flex;flex-direction:column;justify-content:space-between;">
      <div style="background:{CARD};border:1px solid {LINE};border-radius:10px;padding:8px;margin-bottom:8px;">
        <div style="font-size:12px;font-weight:700;color:{MUT};margin-bottom:5px;">Teknik yapı · mumlar + SMA50/EMA144/SMA100/SMA200 + POC + VWAP</div><img src="data:image/png;base64,{chart}" style="width:100%;display:block;border-radius:8px;"/>
      </div>
      {levels}
      {harsi_block}
      <div style="background:{CARD};border:1px solid {LINE};border-radius:10px;padding:8px;margin-bottom:8px;">
        <div style="font-size:12px;font-weight:700;color:{MUT};margin-bottom:5px;">Para Akış İvmesi & Fiyat Dengesi</div><img src="data:image/png;base64,{ivme}" style="width:100%;display:block;border-radius:8px;"/>
      </div>
    </div>
  </div>

</div>"""


# ═══════════════════════════════════════════════════════════════════════════
# 30 Eyl 2026 — X (Twitter) PAYLAŞIM DÜZENİ. Kullanıcı kolajından: üstte TEK
# CÜMLE hüküm, solda büyük rakam kartları (RSI uç rozeti · 5g akış · hacim ·
# yön haritası · kapanış gücü), sağda app'in mum grafiği + 52H çubuğu, altta
# app'in Para Akış İvmesi + Sentiment grafikleri BÜYÜK boy. Yeni hesap YOK:
# rakamlar compass_panel.forces · terazi_core.rsi_uc_rozeti · infographic.compute
# (ekrandakiyle aynı kaynaklar). Yalnız app içi widget'ı etkiler; bot PNG'si
# (build_html/render_bytes) eski düzende kalır.
# GERİ ALMA: X_LAYOUT = False → eski düzen.
# ═══════════════════════════════════════════════════════════════════════════
X_LAYOUT = True
_X_CARD = '#111a28'


def _x_tr_num(v):
    """12290.58 → '12.290,58' (Türkçe binlik/ondalık)."""
    return f"{float(v):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')


def _x_is_index(t):
    t = str(t).upper()
    return t.startswith(("XU", "XB", "XT", "XY", "^")) or t.endswith("=F") or "-USD" in t


def _x_sentiment_profile(t):
    """Sentiment modelinin piyasa profili — app render_synthetic_sentiment_panel'in
    seçtiği yolun aynısı (Codex denetimi 30 Eyl): BIST hisse/endeks → 'BIST',
    ABD hissesi → 'US200', emtia/kripto/döviz/yabancı endeks → None (model YOK,
    app de orada eski sentetik grafiği çizer → biz de build_ivme_fig'e düşeriz)."""
    u = str(t).strip().upper()
    base = u.removesuffix('.IS')
    try:
        from data_layer import _BIST_TICKER_SET
        if u.endswith('.IS') or base in _BIST_TICKER_SET:
            return 'BIST'
    except Exception:
        if u.endswith('.IS'):
            return 'BIST'
    if base.startswith(('XU', 'XB', 'XT', 'XY')):
        return 'BIST'
    if re.fullmatch(r'[A-Z]{1,5}([.-][A-Z]{1,2})?', u) and not u.endswith('-USD'):
        return 'US200'
    return None


def _x_side(v):
    """CMF → (etiket, renk) — app YÖNÜN ZAMAN HARİTASI ile aynı ±0.05 eşiği."""
    if v is None: return None, MUT
    if v > 0.05:  return 'alıcı', UP
    if v < -0.05: return 'satıcı', DN
    return 'denge', GOLD


def _x_sma50_run(df):
    """SMA50'nin hangi tarafında, kaç gündür. (above|None, gün)"""
    try:
        c = df['Close'].astype(float); diff = (c - c.rolling(50).mean()).values
        if not np.isfinite(diff[-1]): return None, 0
        above = bool(diff[-1] >= 0); n = 1
        for i in range(len(diff) - 2, -1, -1):
            if not np.isfinite(diff[i]) or (diff[i] >= 0) != above: break
            n += 1
        return above, n
    except Exception:
        return None, 0


def _x_header(tk, d, cmf5, rz, above50, rvol):
    side, sclr = _x_side(cmf5)
    lead = {'alıcı': 'Alıcı baskın', 'satıcı': 'Satıcı baskın'}.get(side, 'Alıcı-satıcı dengede')
    if rz and rz['mod'] == 'dip':
        tail = f"RSI {rz['rsi']:.0f} ile uç aşırı satımda"
        title = f"{lead}, {'ama ' if side == 'satıcı' else ''}{tail}"
    elif rz and rz['mod'] == 'tepe':
        tail = f"RSI {rz['rsi']:.0f} ile momentum ucunda"
        title = f"{lead}, {'ama ' if side == 'alıcı' else ''}{tail}"
    elif above50 is not None:
        title = f"{lead}, ana trend {'yukarı' if above50 else 'aşağı'}"
    else:
        title = lead
    parts = [{'alıcı': 'Kısa vade alıcıda', 'satıcı': 'Kısa vade satıcıda'}.get(side, 'Kısa vade dengede')]
    if above50 is not None:
        parts.append(f"ana trend {'yukarı' if above50 else 'aşağı'}")
    if rvol is not None:
        _rv = f"{rvol:.2f}".replace('.', ',')
        parts.append(f"hacim ortalamanın altında ({_rv}×)" if rvol < 0.8 else
                     f"hacim ortalamanın üstünde ({_rv}×)" if rvol > 1.2 else f"hacim normal ({_rv}×)")
    chg_clr = UP if d['chg'] >= 0 else DN; arrow = '▲' if d['chg'] >= 0 else '▼'
    price = _x_tr_num(d['last']); chg = f"{abs(d['chg']):.2f}".replace('.', ',')
    return (
        f"<div style='display:flex;align-items:center;gap:20px;margin-bottom:14px;'>"
        f"<div style='flex:0 0 auto;'>"
        f"<div style='font-size:34px;font-weight:800;line-height:1.05;white-space:nowrap;'>{tk}</div>"
        f"<div style='font-size:26px;font-weight:700;white-space:nowrap;'>{price} "
        f"<span style='color:{chg_clr};font-size:19px;'>{arrow} %{chg}</span></div></div>"
        f"<div style='flex:1;min-width:0;border-left:5px solid {sclr};padding-left:18px;'>"
        f"<div style='font-size:29px;font-weight:800;line-height:1.2;'>{title}</div>"
        f"<div style='font-size:16px;color:{MUT};margin-top:4px;'>{' · '.join(parts)}</div></div>"
        f"<div style='flex:0 0 auto;font-size:15px;font-weight:800;color:{INFO};text-align:right;line-height:1.25;'>"
        f"SMART MONEY<br>RADAR</div></div>")


def _x_rsi_card(rz, d):
    """RSI kartı — uç modda turuncu/mor çerçeveli büyük rozet, normalde sade kart.
    Altında 1g–5g–14g gidişatı (eski ayrı MOMENTUM · RSI bandı buraya gömüldü)."""
    trk = d.get('rsi_track') or {}
    try:
        n, f5, f14 = float(trk['now']), float(trk['five']), float(trk['fourteen'])
        yon = ('zayıflıyor' if n <= f5 - 1 and f5 <= f14 - 1 else
               'güçleniyor' if n >= f5 + 1 and f5 >= f14 + 1 else 'karışık')
        gidis = f"1g–5g–14g: {n:.0f}–{f5:.0f}–{f14:.0f} · RSI {yon}"
    except Exception:
        n = None; gidis = ''
    if rz:
        clr = rz['renk']; bg = '#2a1a0e' if rz['mod'] == 'dip' else '#1f1830'
        head = 'RSI · UÇ AŞIRI SATIM' if rz['mod'] == 'dip' else 'RSI · MOMENTUM UCU'
        karne = str(rz.get('karne') or '').lstrip('◆ ').strip()
        karne = karne[:1].upper() + karne[1:] if karne else ''
        val = f"{rz['rsi']:.0f}"
        return (f"<div style='background:{bg};border:2px solid {clr};border-radius:10px;padding:10px 13px;'>"
                f"<div style='font-size:14px;font-weight:800;color:{clr};letter-spacing:0.03em;'>{head}</div>"
                f"<div style='display:flex;align-items:center;gap:12px;margin-top:2px;'>"
                f"<span style='font-size:52px;font-weight:800;line-height:1;color:{clr};'>{val}</span>"
                f"<span style='font-size:14px;line-height:1.35;color:#e3d3b8;'>{karne}</span></div>"
                f"<div style='font-size:12px;color:{MUT};margin-top:6px;'>{gidis}</div></div>")
    if n is None:
        return ''
    return (f"<div style='background:{_X_CARD};border:1px solid {LINE};border-radius:10px;padding:10px 13px;'>"
            f"<div style='font-size:14px;font-weight:800;color:{MUT};'>RSI (14)</div>"
            f"<div style='font-size:40px;font-weight:800;line-height:1.1;color:{TXT};'>{n:.0f}</div>"
            f"<div style='font-size:12px;color:{MUT};margin-top:2px;'>{gidis}</div></div>")


def _x_metric(lbl, val, clr):
    return (f"<div style='flex:1;background:{_X_CARD};border:1px solid {LINE};border-radius:10px;padding:8px 12px;'>"
            f"<div style='font-size:13px;color:{MUT};'>{lbl}</div>"
            f"<div style='font-size:30px;font-weight:800;color:{clr};line-height:1.15;'>{val}</div></div>")


def _x_timemap(cmf5, cmf20, above50, days50):
    def side_txt(v):
        s, c = _x_side(v)
        return ({'alıcı': 'Alıcı önde', 'satıcı': 'Satıcı önde', 'denge': 'Net yön yok'}.get(s, 'Veri yok'), c)
    t5, c5 = side_txt(cmf5); t20, c20 = side_txt(cmf20)
    if above50 is None:
        tt, ct = 'Veri yok', MUT
    else:
        tt = f"{'Yukarı' if above50 else 'Aşağı'} · SMA50 {'üstü' if above50 else 'altı'} {days50} gün"
        ct = UP if above50 else DN

    def row(l, t, c):
        return (f"<div style='display:flex;justify-content:space-between;align-items:center;padding:5px 0;"
                f"border-top:1px solid {LINE};'><span style='font-size:14px;color:{MUT};'>{l}</span>"
                f"<span style='font-size:15px;font-weight:800;color:{c};'>{t}</span></div>")
    return (f"<div style='background:{_X_CARD};border:1px solid {LINE};border-radius:10px;padding:8px 12px;'>"
            f"<div style='font-size:13px;font-weight:800;color:{MUT};margin-bottom:4px;'>YÖNÜN ZAMAN HARİTASI</div>"
            + row('Son 5 gün', t5, c5) + row('Son 20 gün', t20, c20) + row('Ana trend', tt, ct) + "</div>")


def _x_close_strength(df):
    """Kapanış gücü · 20g — _signal_box'taki şeridin aynısı (CMF günlük / 20g ort hacim)."""
    try:
        vv = df['Volume'].fillna(0).astype(float); cc2 = df['Close'].astype(float)
        hi = df['High'].astype(float); lo = df['Low'].astype(float)
        oa = float(vv.rolling(20).mean().iloc[-1])
        if oa <= 0: return ''
        hl = hi - lo
        mfm = np.where(hl > 0, ((cc2 - lo) - (hi - cc2)) / hl, 0.0)
        vals = [float(x) / oa for x in (vv * mfm).tail(20) if x == x]
        if len(vals) < 5: return ''
        n = len(vals); W, H = 260, 90; mid = H / 2
        lim = max(1e-9, max(abs(x) for x in vals) * 1.1); bw = W / n
        p = [f"<line x1='0' y1='{mid}' x2='{W}' y2='{mid}' stroke='#475569' stroke-width='1' stroke-dasharray='3,3'/>"]
        for i, x in enumerate(vals):
            bh = max(abs(x) / lim * (mid - 3), 0.8); clr = '#22d3ee' if x > 0 else '#fb7185'
            p.append(f"<rect x='{i * bw + bw * 0.15:.1f}' y='{(mid - bh if x > 0 else mid):.1f}' "
                     f"width='{bw * 0.7:.1f}' height='{bh:.1f}' fill='{clr}' "
                     f"opacity='{'1' if i == n - 1 else '0.72'}' rx='1'/>")
        son = ('alıcı baskın', '#22d3ee') if vals[-1] > 0 else ('satıcı baskın', '#fb7185')
        return (f"<div style='background:{_X_CARD};border:1px solid {LINE};border-radius:10px;padding:8px 12px;'>"
                f"<div style='display:flex;justify-content:space-between;font-size:13px;margin-bottom:3px;'>"
                f"<span style='font-weight:800;color:{MUT};white-space:nowrap;'>KAPANIŞ GÜCÜ · 20G</span>"
                f"<span style='font-weight:800;color:{son[1]};white-space:nowrap;'>bugün: {son[0]}</span></div>"
                f"<svg width='100%' height='{H}' viewBox='0 0 {W} {H}' preserveAspectRatio='none' style='display:block;'>"
                + "".join(p) + "</svg></div>")
    except Exception:
        return ''


def _x_52h(d):
    try:
        pos = max(1.0, min(99.0, float(d['pos52'])))
        fmt = _x_tr_num
        return (f"<div style='margin-top:10px;'>"
                f"<div style='display:flex;justify-content:space-between;font-size:14px;color:{MUT};'>"
                f"<span>52H dip {fmt(d['lo52'])}</span>"
                f"<span style='color:{TXT};font-weight:800;'>Yıllık aralıkta %{float(d['pos52']):.0f}</span>"
                f"<span>52H tepe {fmt(d['hi52'])}</span></div>"
                f"<div style='position:relative;height:10px;border-radius:5px;margin-top:6px;"
                f"background:linear-gradient(90deg,{DN}66,{GOLD}44,{UP}66);'>"
                f"<div style='position:absolute;left:{pos:.1f}%;top:-5px;width:5px;height:20px;"
                f"margin-left:-2px;background:#fff;border-radius:2px;'></div></div></div>")
    except Exception:
        return ''


def build_x_html(ticker):
    """X paylaşım düzeni — app içi widget (st.html) için iç div döndürür."""
    df = ig.load(ticker)
    if df is None or len(df) < 60: return None
    d = ig.compute(ticker, df)
    tk = _disp_name(d['ticker'])
    is_idx = _x_is_index(ticker)
    # 30 Eyl 2026 — rakamlar APP'İN veri katmanından (get_safe_historical_data):
    # endekste TL ciro, seans içinde hacim TAM-SEANS TAHMİNİ (app 'tam-seans eşdeğeri' ile
    # aynı). Sondaki hacimsiz boş barlar atılır (analysis_core RVOL kuralı).
    adf = None
    try:
        from data_layer import get_safe_historical_data
        adf = get_safe_historical_data(ticker, period='1y')
        if adf is not None and len(adf) >= 30:
            _v = adf['Volume'].fillna(0).astype(float).values
            _n = 0
            while _n < min(10, len(_v) - 1) and _v[len(_v) - 1 - _n] <= 0:
                _n += 1
            if _n:
                adf = adf.iloc[:-_n].copy()
                adf.attrs['vol_projected'] = False   # son bar artık bugün değil → tahmin damgası geçersiz
        else:
            adf = None
    except Exception:
        adf = None
    src = adf if adf is not None else df
    cmf5 = cmf20 = vm = None
    try:
        from indicators import compute_cmf
        cmf5 = float(compute_cmf(src, period=5)); cmf20 = float(compute_cmf(src, period=20))
    except Exception:
        pass
    # Hacim / ortalama — payda = bugün HARİÇ son 20 bar ortalaması (app.py ~14418).
    # Seans içi yarım bar İKİ yoldan biriyle düzeltilir, İKİSİ BİRDEN ASLA:
    #  (a) data_layer son barın hacmini zaten tam-gün TAHMİNİNE çevirdiyse
    #      (is_last_bar_projected) → oran doğrudan; payda küçültülmez.
    #  (b) tahmin yoksa ve bar yarımsa → seans_profili paydayı geçen paya indirger (YALNIZ BIST;
    #      emtia/US farklı saatlerde işlem görür → düzeltme yok, 'gün tamamlanmadı' notu).
    # 30 Eyl 2026: önce ikisi birden uygulanıyordu → XU100 0,77× yerine 2,64× (çifte düzeltme).
    vm_not = ''
    try:
        from data_layer import is_last_bar_projected
        _proj, _prog = is_last_bar_projected(src) if adf is not None else (False, 1.0)
        _vv = src['Volume'].astype(float)
        _v20 = float(_vv.iloc[-21:-1].mean()); _vson = float(_vv.iloc[-1])
        _kd = {}
        _bist = _x_sentiment_profile(ticker) == 'BIST'   # seans profili BIST saatleriyle ölçüldü
        if not _proj and not _bist:
            try:
                import pandas as _pd
                _bugun = _pd.Timestamp.now(tz='Europe/Istanbul').date()
                if _pd.Timestamp(src.index[-1]).date() == _bugun:
                    vm_not = 'gün tamamlanmadı'
            except Exception:
                pass
        if not _proj and _bist:
            try:
                from seans_profili import rvol_paydasi
                _pay, _kd = rvol_paydasi(_v20, src.index[-1])
                if _pay and _pay > 0: _v20 = float(_pay)
            except Exception:
                _kd = {}
        if (_kd or {}).get('kismi') and not (_kd or {}).get('yeterli', True):
            vm = None; vm_not = 'seans yeni başladı'
        else:
            vm = _vson / _v20 if _v20 > 0 and _vson > 0 else None
            if _proj:
                _yz = int(round(_prog * 100))
                try:
                    from seans_profili import yuzde_eki
                    _ek = yuzde_eki(_yz)
                except Exception:
                    _ek = 'i'
                vm_not = f"tam gün tahmini · seansın %{_yz}'{_ek}"
            elif (_kd or {}).get('kismi'):
                vm_not = str(_kd.get('rozet') or '')
    except Exception:
        pass
    # Fiyat · değişim · 52H · RSI 1g–5g–14g da AYNI veriden (Codex denetimi 30 Eyl:
    # kartlar app verisinden, 52H/RSI parquet'ten geliyordu → seans içinde ayrışıyordu).
    hdr = dict(d)
    if adf is not None:
        try:
            hdr = ig.compute(ticker, src)
        except Exception:
            try:
                _c = src['Close'].astype(float)
                hdr['last'] = float(_c.iloc[-1]); hdr['chg'] = (float(_c.iloc[-1]) / float(_c.iloc[-2]) - 1) * 100
            except Exception:
                pass
    try:
        import terazi_core
        rz = terazi_core.rsi_uc_rozeti(src['Close'], is_index=is_idx)
    except Exception:
        rz = None
    above50, days50 = _x_sma50_run(src)
    # Alt grafik: app'in 'Para Akış İvmesi & Fiyat' + 'Sentiment & Fiyat' panelleriyle AYNI veri
    # (sentiment_chart_core). Model veri veremezse eski İvme/Denge figürüne düşer.
    pair = None
    _prof = _x_sentiment_profile(ticker)
    if _prof:
        try:
            from sentiment_chart_core import calculate_sentiment_chart
            _sd = calculate_sentiment_chart(ticker, 'daily', market_profile=_prof)
            # bar ekseni: app'in endeks kuralı (XU/XB/XT/XY/^) — emtia/kripto DEĞİL
            _bar_idx = str(ticker).upper().replace('.IS', '').startswith(('XU', 'XB', 'XT', 'XY', '^'))
            pair = cc.build_sentiment_pair_fig(_sd, tk, is_index=_bar_idx, width=1060, height=300)
        except Exception:
            pair = None
    figs = _render_figs_batch({
        'chart': cc.build_fig(ticker, height=440, width=760),
        'ivme':  pair if pair is not None else cc.build_ivme_fig(ticker, width=1060, height=270, big=True),
    })
    b64 = {k: base64.b64encode(v).decode() for k, v in figs.items()}

    s5, c5 = _x_side(cmf5)
    left = [_x_rsi_card(rz, hdr),
            "<div style='display:flex;gap:10px;'>"
            # Codex denetimi 30 Eyl: '−%51,8' gerçek para yüzdesi sanılıyordu → CMF kendi ölçeğinde (−1..+1)
            + _x_metric('5 gün para akışı (CMF)',
                        f"{cmf5:+.2f}".replace('.', ',').replace('-', '−') if cmf5 is not None else '—', c5)
            + _x_metric('Hacim / ortalama' + (f" · {vm_not}" if vm_not else ''),
                        f"{vm:.2f}×".replace('.', ',') if vm is not None else '—',
                        (UP if vm >= 1.2 else DN if vm < 0.8 else TXT) if vm is not None else TXT)
            + "</div>",
            _x_timemap(cmf5, cmf20, above50, days50),
            _x_close_strength(src)]
    left_html = "".join(f"<div>{x}</div>" for x in left if x)

    def box(title, img, grow=False):
        if not img: return ''
        _g = "height:100%;box-sizing:border-box;display:flex;flex-direction:column;" if grow else ""
        return (f"<div style='background:{CARD};border:1px solid {LINE};border-radius:10px;padding:10px;{_g}'>"
                f"<div style='font-size:14px;font-weight:700;color:{MUT};margin-bottom:6px;'>{title}</div>"
                f"<img src='data:image/png;base64,{img}' style='width:100%;display:block;border-radius:8px;'/>")

    # Grafik kutusu sol kolonun boyuna UZAR; 52H çubuğu kutunun dibine oturur (delik kalmaz).
    chart_box = (box('Teknik yapı · mumlar + SMA50/EMA144/SMA100/SMA200 + POC + VWAP', b64.get('chart'), grow=True)
                 + f"<div style='margin-top:auto;'>{_x_52h(hdr)}</div></div>") if b64.get('chart') else ''
    # Sentiment çifti kendi başlıklarını taşır → dış başlık yalnız eski figürde.
    if b64.get('ivme') and pair is not None:
        ivme_box = (f"<div style='background:{CARD};border:1px solid {LINE};border-radius:10px;padding:10px;'>"
                    f"<img src='data:image/png;base64,{b64['ivme']}' style='width:100%;display:block;border-radius:8px;'/></div>")
    else:
        ivme_box = (box(f'Para Akış İvmesi & Fiyat Dengesi — {tk}', b64.get('ivme')) + "</div>") if b64.get('ivme') else ''
    # SABİT 1100px tasarım + ekrana sığdırma (zoom): her ekran genişliğinde AYNI oran →
    # dar iframe'de grafik küçülüp altında boşluk kalmaz.
    return f"""<div id="smrx" style="width:1100px;box-sizing:border-box;background:{BG};padding:20px;color:{TXT};font-family:'Segoe UI',Arial,sans-serif;border-radius:12px;">
  {_x_header(tk, hdr, cmf5, rz, above50, vm)}
  <div style="display:grid;grid-template-columns:300px 1fr;gap:14px;align-items:stretch;">
    <div style="display:flex;flex-direction:column;justify-content:space-between;gap:10px;">{left_html}</div>
    <div>{chart_box}</div>
  </div>
  <div style="margin-top:14px;">{ivme_box}</div>
  <div style="margin-top:12px;display:flex;">{_notice_badge(fs=12)}</div>
</div>
<script>(function(){{var e=document.getElementById('smrx');if(!e)return;
function f(){{var w=(document.documentElement.clientWidth||window.innerWidth)-4;e.style.zoom=Math.min(1,w/1100);}}
f();window.addEventListener('resize',f);}})();</script>"""


def render(ticker, out=None):
    from playwright.sync_api import sync_playwright   # lazy — chromium sadece PNG için
    html = build_html(ticker)
    if html is None: return None
    out = out or os.path.join(BASE, f'infografik_{ticker}.png')
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 1020, 'height': 820}, device_scale_factor=2)
        pg.set_content(html, wait_until='networkidle')
        pg.locator('#infografik').screenshot(path=out)
        b.close()
    return out


def render_bytes(ticker):
    """render() gibi chromium ile PNG üretir ama dosyaya değil BELLEĞE alır → bayt döndürür.
    Bot/SMR-ELITE için (dosya çakışması yok). Veri/chromium yoksa None."""
    from playwright.sync_api import sync_playwright   # lazy — chromium sadece PNG için
    html = build_html(ticker)
    if html is None: return None
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 1020, 'height': 820}, device_scale_factor=2)
        pg.set_content(html, wait_until='networkidle')
        png = pg.locator('#infografik').screenshot()
        b.close()
    return png


if __name__ == '__main__':
    tk = sys.argv[1] if len(sys.argv) > 1 else 'SAHOL'
    out = render(tk)
    print(f'✅ {out}' if out else 'veri yok')
