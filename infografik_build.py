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
# Sağ kolonun tam genişliğini kaplar (yanında boşluk kalmaz).
NOTICE_BADGE = (
    f"<div style='background:{CARD2};border:1px solid {GOLD}44;border-radius:10px;"
    f"padding:10px 13px;flex:1;display:flex;align-items:center;'>"
    f"<span style='font-size:17px;line-height:1.55;color:{MUT};letter-spacing:0.2px;'>"
    f"<b style='color:{GOLD};'>DİKKAT.</b> BU GÖRSELİN HER HAKKI MAHFUZDUR. #SMARTMONEYRADAR EĞİTİM AMAÇLIDIR, YATIRIM TAVSİYESİ DEĞİLDİR. "
    f"YAPAY ZEKA ÜRETİMİ DEĞİLDİR. 52.000 SATIRLIK ALGORİTMAMIN ÇIKTISIDIR. "
    f"<b style='color:{INFO};'>#SMARTMONEYRADAR</b></span></div>"
)


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
        _proc = subprocess.Popen(
            [sys.executable, _script, _td, str(scale), str(_KALEIDO_FIG_TIMEOUT)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_flags,
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


def _figs_b64_3lu(ticker):
    """İnfografiğin 3 figürünü (ana grafik + ivme + momentum) TEK Chrome açılışıyla render eder.
    Döner: (chart_b64, ivme_b64, harsi_block_html). Eski yol 3 ayrı render = 3 kat yavaş + kırılgandı."""
    _figs = _render_figs_batch({
        'chart': cc.build_fig(ticker),
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
    out = dict(rs=None, beta=None, rvol=None)
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
    rs = ms['rs']; rs_v = f"{rs:.2f}×" if rs else "—"; rs_c = UP if (rs or 1) >= 1 else DN
    mom5 = d.get('mom_5'); mom20 = d.get('mom_20')
    if mom5 is not None and mom20 is not None:
        _m5a = '▲' if mom5 >= 0 else '▼'
        _m20a = '▲' if mom20 >= 0 else '▼'
        mom_v = f"{_m5a}%{abs(mom5):.1f} · {_m20a}%{abs(mom20):.1f}"
        mom_c = UP if mom5 >= 0 and mom20 >= 0 else (DN if mom5 < 0 and mom20 < 0 else GOLD)
        mom_lbl = 'MOMENTUM <span style="font-size:8px;opacity:0.65;">5g · 20g</span>'
    else:
        mom = d.get('mom'); mom_v = f"%{abs(mom):.1f}" if mom is not None else "—"
        mom_c = UP if (mom or 0) >= 0 else DN
        mom_lbl = 'MOMENTUM'
    sa = ms.get('stp_above'); sd = ms.get('stp_days'); sx = ms.get('stp_crossed')
    if sa is None:
        stp_v = '—'; stp_c = TXT
    else:
        ar = '↑' if sa else '↓'; stp_c = UP if sa else DN
        stp_v = f"{ar} kesti" if sx else f"{ar} {sd}g"
    # 29 Tem 2026 — sadeleştirme ("nokta atışı"): RSI + BETA hücreleri KALDIRILDI.
    # HACİM yalnızca ortalamadan ±%20+ SAPARSA gösterilir (o değilse gürültü) →
    # sadece "1.4×" (yorum yok). Sapma yoksa hücre hiç basılmaz.
    items = [('RS GÜCÜ <span style="font-size:8px;opacity:0.65;">10g</span>', rs_v, rs_c), (mom_lbl, mom_v, mom_c)]
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


def _decision_box(df, d, ms):
    """Görseldeki verilerden kısa-vade karar özeti üretir; AL/SAT emri değildir."""
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
    _body = (f"<div style='font-size:11px;line-height:1.45;color:{TXT};"
             f"max-width:100%;'>{_summary}</div>")
    return (f"<div style='background:{CARD};border:1px solid {INFO}66;border-radius:10px;"
            f"padding:9px 11px;margin-bottom:10px;width:100%;'>"
            f"<div style='font-size:12px;font-weight:800;color:{INFO};margin-bottom:5px;'>"
            f"SMART MONEY RADAR · ALGORİTMİK ÖZET</div>"
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


def _signal_box(df, d):
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

    def _cell(lbl, sig):
        if sig > 0:   ar, clr = "▲", UP
        elif sig < 0: ar, clr = "▼", DN
        else:         ar, clr = "→", NEU
        return (f"<div style='padding:3px 8px;background:{clr}1a;border:1px solid {clr}4d;"
                f"border-radius:5px;display:flex;align-items:center;justify-content:space-between;"
                f"gap:8px;line-height:1.1;min-width:74px;'>"
                f"<span style='font-size:9px;color:{LBL};font-weight:700;letter-spacing:0.04em;'>{lbl}</span>"
                f"<span style='font-size:13px;color:{clr};font-weight:900;'>{ar}</span></div>")
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

    # RSI + YAPI — aile değil, mevcut çip görselleriyle + açıklama (destekleyici teknik)
    _yapi_txt = ("Fiyat yapısı yukarı eğimli" if sig_yapi > 0 else
                 "Fiyat yapısı aşağı eğimli" if sig_yapi < 0 else "Yapı yatay/kararsız")
    _rsi_txt = ("Momentum güçlü (güç devamı)" if sig_rsi > 0 else
                "Dip görüntüsü (tuzak riski)" if sig_rsi < 0 else "Momentum nötr (kısa + orta)")
    support_html = (
        f"<div style='font-size:8.5px;color:{NEU};letter-spacing:0.05em;margin-bottom:4px;'>"
        f"DESTEKLEYİCİ TEKNİK · aile değil</div>"
        f"<div style='display:flex;flex-direction:column;gap:4px;'>"
        f"<div style='display:flex;align-items:center;gap:7px;'>{_cell('YAPI', sig_yapi)}"
        f"<span style='font-size:9.5px;color:{MUT};'>{_yapi_txt}</span></div>"
        f"<div style='display:flex;align-items:center;gap:7px;'>{_cell('RSI ×2', sig_rsi)}"
        f"<span style='font-size:9.5px;color:{MUT};'>{_rsi_txt}</span></div></div>")

    # ── 5 mumluk mini şerit (son 5 OHLC) ──────────────────────────────
    mini = ""
    try:
        _d5f = df.tail(5)
        hs = _d5f['High'].astype(float).values; ls = _d5f['Low'].astype(float).values
        oo = _d5f['Open'].astype(float).values; cc = _d5f['Close'].astype(float).values
        pmax = float(hs.max()); rng = max(pmax - float(ls.min()), 1e-9)
        W, H = 110, 80; cw = W / 5; bw = cw * 0.42; parts = []
        for i in range(5):
            o, h, l, c = float(oo[i]), float(hs[i]), float(ls[i]), float(cc[i])
            xc = i * cw + cw / 2
            yh = (pmax - h) / rng * H; yl = (pmax - l) / rng * H
            yo = (pmax - o) / rng * H; yc = (pmax - c) / rng * H
            _cc = UP if c >= o else DN
            parts.append(f"<line x1='{xc:.1f}' y1='{yh:.1f}' x2='{xc:.1f}' y2='{yl:.1f}' stroke='{_cc}' stroke-width='1.4'/>")
            bt = min(yo, yc); bh = max(abs(yc - yo), 1.5)
            parts.append(f"<rect x='{xc - bw/2:.1f}' y='{bt:.1f}' width='{bw:.1f}' height='{bh:.1f}' fill='{_cc}'/>")
        # 13 Tem 2026 — tazelik etiketi (app paneli ile aynı): son mum tarihi
        _lc = ""
        try:
            _ld = _d5f.index[-1]
            _lc = (f"<div style='font-size:9px;color:{NEU};text-align:center;"
                   f"margin-top:1px;'>son mum: {_ld.day:02d}.{_ld.month:02d}</div>")
        except Exception:
            pass
        mini = (f"<div style='display:flex;flex-direction:column;align-items:center;'>"
                f"<div style='font-size:9px;color:{NEU};margin-bottom:1px;letter-spacing:0.03em;'>son 5 mum</div>"
                f"<svg width='100%' height='{H}' viewBox='0 0 {W} {H}' preserveAspectRatio='xMidYMid meet' "
                f"style='max-width:110px;'>" + "".join(parts) + "</svg>" + _lc + "</div>")
    except Exception:
        pass

    # ── AKILLI PARA İZLERİ — 3 sparkline (20 Tem 2026: çizgi→BAR + 3. sinyal CMF) ──
    # Sıfır-merkezli günlük histogram (GENEL ÖZET diliyle uyumlu; MACD-histogram mantığı).
    # 3 ayrı lens: (1) Momentum ivme deltası (2) OBV deltası = close YÖNÜ×hacim
    # (3) CMF = close'un gün-içi RANGE KONUMU → ilk ikisinin göremediği gizli dağıtımı yakalar.
    def _delta_bars(vals, pos_clr, neg_clr, title, pos_w, neg_w):
        try:
            if not vals or len(vals) < 5:
                return ""
            n = len(vals); w, h = 220, 40; mid = h / 2
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
                    f"<svg width='100%' height='40' viewBox='0 0 220 40' preserveAspectRatio='none' "
                    f"style='display:block;'>" + "".join(p) + "</svg></div>")
        except Exception:
            return ""

    gidisat = ""
    try:
        from indicators import compute_flow_momentum
        _mf, _ = compute_flow_momentum(df)
        _mvals = []
        if _mf is not None:
            _mfl = [float(x) for x in _mf.tail(21) if x == x]
            _mvals = [_mfl[i] - _mfl[i - 1] for i in range(1, len(_mfl))]
        _ovals = _cvals = []
        if 'Volume' in df.columns and {'High', 'Low', 'Close'}.issubset(df.columns):
            _vv = df['Volume'].fillna(0).astype(float)
            _cc2 = df['Close'].astype(float)
            _oa = float(_vv.rolling(20).mean().iloc[-1])
            if _oa > 0:
                # OBV deltası (close yönü × hacim)
                _osm = (np.sign(_cc2.diff()).fillna(0) * _vv).cumsum().ewm(span=5, adjust=False).mean()
                _ovals = [float(x) / _oa for x in _osm.diff().tail(20) if x == x]
                # CMF günlük (close'un gün-içi range konumu × hacim) — hacme normalize, sıfır-merkezli
                _hl = (df['High'].astype(float) - df['Low'].astype(float))
                _mfm = np.where(_hl > 0,
                                ((_cc2 - df['Low'].astype(float)) - (df['High'].astype(float) - _cc2)) / _hl,
                                0.0)
                _mfv = _vv * _mfm   # Series × np-array = Series (pozisyonel, _vv indeksinde); pd import gerekmez
                _cvals = [float(x) / _oa for x in _mfv.tail(20) if x == x]
        _l1 = _delta_bars(_mvals, "#5B84C4", "#ef4444", "Momentum ivmesi", "güçleniyor", "zayıflıyor")
        _l2 = _delta_bars(_ovals, "#4ade80", "#f59e0b", "OBV gidişatı", "birikim", "dağıtım")
        # "Kapanış gücü" (para akışı DEĞİL): metin zaten "para akışı" = 20g CMF toplamını kullanıyor;
        # bu sparkline BUGÜNKÜ günün range-içi kapanışı → aynı isim iki yönde çelişki görünüyordu.
        _l3 = _delta_bars(_cvals, "#22d3ee", "#fb7185", "Kapanış gücü", "alıcı baskın", "satıcı baskın")
        if _l1 or _l2 or _l3:
            # ALT ALTA (dar sol kolonda yan yana 3'ü başlığı kırpıyordu) → her biri tam genişlik
            gidisat = (f"<div style='display:flex;flex-direction:column;gap:6px;margin-top:8px;'>"
                       + _l1 + _l2 + _l3 + "</div>")
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
        tf = ("<div style='display:flex;flex-direction:column;gap:3px;flex:0 0 auto;justify-content:center;'>"
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
            f"<div style='display:flex;gap:8px;margin-top:6px;align-items:flex-start;'>"
            f"<div style='flex:1;'>{support_html}</div>"
            f"<div style='display:flex;justify-content:center;align-items:flex-start;'>{mini}</div>{tf}</div>"
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
    decision = _decision_box(df, d, _ms)
    levels = ''
    sbox = _signal_box(df, d)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',Arial,sans-serif;}}
body{{background:{BG};}}
img{{display:block;border-radius:8px;}}
</style></head><body>
<div id="infografik" style="width:980px;background:{BG};padding:16px;color:{TXT};">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px;">
    <div style="display:flex;align-items:center;gap:10px;flex:1;min-width:0;">
      <div><div style="font-size:24px;font-weight:800;">{tk} · Teknik Görünüm</div>
      <div style="font-size:14px;color:{MUT};letter-spacing:0.5px;">SMART MONEY RADAR · ALGORİTMİK ÖZET</div></div>
    </div>
    <div style="flex:0 0 auto;">{stats}</div>
    <div style="background:#0c2238;border:1px solid {LINE};border-radius:10px;padding:11px 22px;text-align:right;display:flex;flex-direction:column;justify-content:center;">
      <div style="font-size:34px;font-weight:800;line-height:1.04;">{d['last']:.2f}</div>
      <div style="font-size:20px;font-weight:700;color:{chg_clr};margin-top:2px;">{arrow} %{abs(d['chg']):.2f}</div>
    </div>
  </div>
  <div style="background:{INFO}1a;border:1px solid {INFO}55;border-radius:8px;padding:8px 14px;margin-bottom:12px;font-weight:700;color:{INFO};font-size:14px;">{g['hook']}</div>
  <div style="display:grid;grid-template-columns:330px 1fr;gap:12px;align-items:stretch;">
    <div>{compass}<div style="height:8px;"></div>{sbox}</div>
    <div style="display:flex;flex-direction:column;">
      {decision}
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
  <div style="margin-top:12px;display:flex;">{NOTICE_BADGE}</div>
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
    decision = _decision_box(df, d, _ms)
    levels = ''
    sbox = _signal_box(df, d)
    # 23 Tem 2026 — ANA KART parçaları buraya ENJEKTE edilir (app.py, ana thread).
    # Bu fonksiyon arka planda/terazisiz koştuğu için burada YALNIZ yer tutucu var;
    # terazi verisiyle doldurma _render_infografik_inapp'te yapılır (tek kaynak).
    #   <!--EKRANV2_YON-->        → başlık yanı (AŞAĞI/YUKARI + terazi barı)
    #   <!--EKRANV2_GECERSIZLIK--> → fiyatın yanı (ince geçersizlik şeridi)
    #   <!--EKRANV2_OYLAR-->      → başlık altı (aşağı/yukarı diyenler + ne değişti)
    return f"""<div style="background:{BG};padding:16px;color:{TXT};font-family:'Segoe UI',Arial,sans-serif;border-radius:12px;">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px;">
    <div style="display:flex;align-items:center;gap:14px;flex:1;min-width:0;">
      <div><div style="font-size:24px;font-weight:800;">{tk} · Teknik Görünüm</div>
      <div style="font-size:14px;color:{MUT};">SMART MONEY RADAR · ALGORİTMİK ÖZET</div></div>
    </div>
    <div style="display:flex;align-items:center;gap:10px;"><!--EKRANV2_YON--><!--EKRANV2_GECERSIZLIK--></div>
    <div style="flex:0 0 auto;">{stats}</div>
    <div style="background:#0c2238;border:1px solid {LINE};border-radius:10px;padding:11px 22px;text-align:right;display:flex;flex-direction:column;justify-content:center;">
      <div style="font-size:34px;font-weight:800;line-height:1.04;">{d['last']:.2f}</div>
      <div style="font-size:20px;font-weight:700;color:{chg_clr};margin-top:2px;">{arrow} %{abs(d['chg']):.2f}</div>
    </div>
  </div>
  <!--EKRANV2_OYLAR-->
  <!-- 23 Tem 2026: hook barı ({{g['hook']}}) kaldırıldı — "okunan bir bar değildi" (kullanıcı). -->
  <div style="display:grid;grid-template-columns:330px 1fr;gap:12px;align-items:stretch;">
    <div>{compass}<div style="height:8px;"></div>{sbox}</div>
    <div style="display:flex;flex-direction:column;">
      {decision}
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
  <div style="margin-top:12px;display:flex;">{NOTICE_BADGE}</div>
</div>"""


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
