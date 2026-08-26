#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TAVAN ADAYLARI → SMR Free kanalı (her sabah 09:45 TR).
app.py'deki tavan motorunun (_tav_*) BİREBİR standalone kopyası — Streamlit'siz,
veriler/*.parquet'ten taze hesaplar. Skor≥150 ALARM listesini kanala atar.
İsabet %11 · ortalamanın 3.4 katı (60g/1131 tavan backtest).
"""
import os, sys, glob, json, requests
from datetime import datetime
import pytz
import numpy as np
import pandas as pd
from patron2_yuksek_getiri_db import mark_run_published, settle_results, stage_candidates
from tarama_sureklilik import DataUnavailable, guarded_main, require_data_date, skip_previous_report
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
VERILER = os.path.join(BASE, 'veriler')
FREE_CHAT = '-1003943892201'    # SMR Free kanalı
SOHBET_CHAT = '-1003851678286'  # SMR Sohbet & Eğitim grubu
ADMIN_ID = '1034525990'
BROADCAST = [FREE_CHAT, SOHBET_CHAT]   # üretimde sonuçların gittiği tüm kanallar
ALARM_ESIK = 150
TOPN = 12
TEST = '--test' in sys.argv
STATE_FILE = os.path.join(BASE, 'tavan_state.json')   # bir önceki günün yollanan listesi (karne için)
PATRON2_DB = os.path.join(BASE, 'patron2.db')


def _token():
    for p in ('/home/wm11tr/weektweet/.env', '/home/wm11tr/insider/.env'):
        try:
            for line in open(p):
                if line.startswith('TELEGRAM_BOT_TOKEN='):
                    return line.split('=', 1)[1].strip()
        except Exception:
            pass
    return os.environ.get('TELEGRAM_BOT_TOKEN')


def tg_send(chat_id, text):
    tok = _token()
    if not tok:
        print('token yok'); return False
    try:
        r = requests.post(f'https://api.telegram.org/bot{tok}/sendMessage',
                          json={'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True}, timeout=25)
        if r.status_code != 200:
            print('telegram HTTP', r.status_code, r.text[:160]); return False
        return True
    except Exception as e:
        print('telegram hata', e); return False


# ═══════════ app.py _tav_* MOTORU — BİREBİR KOPYA ═══════════
def _liquidity_manip(df):
    """app.py BİREBİR — ince tahta + dik koşu + hacim sıçraması = pompa riski."""
    out = {'tier': None, 'manip': None}
    try:
        c = df['Close']; v = df['Volume']
        if len(c) < 20:
            return out
        adv = float((c * v).tail(20).mean())
        out['tier'] = 'yüksek' if adv >= 300e6 else ('orta' if adv >= 30e6 else 'düşük')
        avgv = float(v.tail(20).mean())
        rvol = float(v.iloc[-1] / avgv) if avgv > 0 else 0.0
        run5 = float(c.iloc[-1] / c.iloc[-6] - 1) if len(c) > 6 else 0.0
        run10 = float(c.iloc[-1] / c.iloc[-11] - 1) if len(c) > 11 else 0.0
        thin = out['tier'] in ('düşük', 'orta')
        steep = (run5 > 0.20) or (run10 > 0.40)
        spike = rvol > 3
        out['manip'] = 'yüksek' if (thin and steep and spike) else (
            'orta' if (thin and (steep or spike)) else 'yok')
    except Exception:
        pass
    return out


def _tav_rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _tav_is_manipulated(df, i):
    # Yakın-dönem dikey tavan (BRMEN tipi: bugün tavanda ya da son 5g'de 2+ tavan) → direkt ele
    _rc = df['Close'].pct_change() * 100
    try:
        if _rc.iloc[i] >= 9.0 or int((_rc.iloc[max(0, i - 4):i + 1] >= 9.0).sum()) >= 2:
            return True
    except Exception:
        pass
    if i < 60:
        return False
    last30 = df.iloc[i - 30:i]
    rng = last30['High'] - last30['Low']
    body = (last30['Close'] - last30['Open']).abs()
    body_ratio = (body / rng.replace(0, np.nan)).fillna(1.0)
    fitilsiz_oran = (body_ratio > 0.85).mean()
    last60 = df.iloc[max(0, i - 60):i]
    tavan_taban_oran = (last60['Close'].pct_change().abs() * 100 > 9.5).mean()
    last20 = df.iloc[i - 20:i]
    range_collapse = (((last20['High'] - last20['Low']) / last20['Close']) * 100 < 1.0).mean()
    kirmizi = (fitilsiz_oran > 0.45) + (tavan_taban_oran > 0.12) + (range_collapse > 0.30)
    return int(kirmizi) >= 2


def _tav_features(df, i):
    if i < 60:
        return None
    if _tav_is_manipulated(df, i):
        return None
    t = df.iloc[i]
    hist = df.iloc[:i + 1]
    close = hist['Close']; high = hist['High']; low = hist['Low']; vol = hist['Volume']
    rsi14 = _tav_rsi(close).iloc[-1]
    look = min(252, len(hist))
    hh = high.tail(look).max(); ll = low.tail(look).min()
    pos_52h = (t['Close'] - ll) / (hh - ll) * 100 if hh > ll else np.nan
    bb_w = (close.tail(20).std() / close.tail(20).mean()) * 100
    bb_60 = close.rolling(20).std() / close.rolling(20).mean() * 100
    bb_pct_rank = (bb_60.tail(60) <= bb_w).mean() * 100
    vol20 = vol.tail(20).mean()
    vr_t = t['Volume'] / vol20 if vol20 > 0 else np.nan
    ret_10g = (t['Close'] / df.iloc[i - 10]['Close'] - 1) * 100 if i >= 10 else np.nan
    ret_5g = (t['Close'] / df.iloc[i - 5]['Close'] - 1) * 100 if i >= 5 else np.nan
    near_h20 = (t['Close'] / close.tail(20).max()) * 100
    pct_seq = []; vol_seq = []
    for k in range(5):
        idx = i - (4 - k)
        if idx < 1:
            pct_seq.append(np.nan); vol_seq.append(np.nan); continue
        prev = df.iloc[idx - 1]['Close']; cur = df.iloc[idx]['Close']
        pct_seq.append((cur / prev - 1) * 100 if prev > 0 else np.nan)
        v20 = df.iloc[max(0, idx - 20):idx]['Volume'].mean()
        vol_seq.append(df.iloc[idx]['Volume'] / v20 if v20 > 0 else np.nan)
    vs = [v for v in vol_seq if not (v is None or (isinstance(v, float) and np.isnan(v)))]
    vol_5g_slope = (vs[-1] - vs[0]) if len(vs) >= 2 else 0
    pct_T = pct_seq[-1] if pct_seq else np.nan
    vol_T = vol_seq[-1] if vol_seq else np.nan
    rng = t['High'] - t['Low']; body = abs(t['Close'] - t['Open'])
    body_pct = body / rng * 100 if rng > 0 else 0
    is_doji = body_pct < 10
    is_green = t['Close'] > t['Open']
    lower_wick = min(t['Close'], t['Open']) - t['Low']
    lw_pct = lower_wick / rng * 100 if rng > 0 else 0
    is_hammer = (lw_pct > 40) and (body_pct > 10)
    return dict(close=t['Close'], rsi=rsi14, pos_52h=pos_52h, bb_rank=bb_pct_rank,
                vr_t=vr_t, near_h20=near_h20, ret_5g=ret_5g, ret_10g=ret_10g,
                vol_tl=t['Close'] * t['Volume'], pct_T=pct_T, vol_T=vol_T,
                vol_5g_slope=vol_5g_slope, is_doji=is_doji, is_green=is_green,
                is_hammer=is_hammer, body_pct=body_pct)


def _tav_score_A(f):
    s = 0
    if f['ret_10g'] >= 20: s += 35
    elif f['ret_10g'] >= 15: s += 25
    elif f['ret_10g'] >= 10: s += 15
    elif f['ret_10g'] >= 5: s += 5
    if f['pos_52h'] >= 90: s += 25
    elif f['pos_52h'] >= 75: s += 15
    elif f['pos_52h'] >= 60: s += 6
    if f['near_h20'] >= 99: s += 20
    elif f['near_h20'] >= 95: s += 8
    if 70 <= f['rsi'] <= 85: s += 12
    elif 60 <= f['rsi'] <= 90: s += 5
    if 1.0 <= f['vr_t'] <= 2.5: s += 8
    elif 0.7 <= f['vr_t'] <= 3.5: s += 3
    return s


def _tav_score_C(f):
    s = 0
    if f['bb_rank'] <= 10: s += 40
    elif f['bb_rank'] <= 20: s += 28
    elif f['bb_rank'] <= 30: s += 15
    elif f['bb_rank'] <= 40: s += 5
    if f['near_h20'] >= 97: s += 25
    elif f['near_h20'] >= 93: s += 15
    elif f['near_h20'] >= 88: s += 5
    if 45 <= f['rsi'] <= 60: s += 15
    elif 35 <= f['rsi'] <= 68: s += 7
    if 35 <= f['pos_52h'] <= 65: s += 12
    elif 25 <= f['pos_52h'] <= 75: s += 5
    if 0.8 <= f['vr_t'] <= 1.3: s += 8
    return s


def _tav_score_E(f):
    s = 0
    if f['near_h20'] >= 99.5: s += 35
    elif f['near_h20'] >= 97: s += 22
    elif f['near_h20'] >= 94: s += 8
    if 65 <= f['pos_52h'] <= 85: s += 22
    elif 55 <= f['pos_52h'] <= 90: s += 10
    if 60 <= f['rsi'] <= 75: s += 15
    elif 50 <= f['rsi'] <= 80: s += 6
    if f['vr_t'] >= 1.2: s += 15
    elif f['vr_t'] >= 0.9: s += 6
    if 3 <= f['ret_10g'] <= 14: s += 10
    elif 0 <= f['ret_10g'] <= 20: s += 4
    return s


def _tav_score_D(f):
    s = 0
    if f['pos_52h'] <= 10: s += 35
    elif f['pos_52h'] <= 18: s += 22
    elif f['pos_52h'] <= 28: s += 8
    if f['rsi'] <= 28: s += 25
    elif f['rsi'] <= 38: s += 15
    elif f['rsi'] <= 45: s += 5
    if f['vr_t'] <= 0.65: s += 20
    elif f['vr_t'] <= 0.9: s += 8
    if f['ret_10g'] <= -10: s += 15
    elif f['ret_10g'] <= -4: s += 8
    return s


_TAV_REJIM_AGIRLIK = {
    'HIZLI_RALLI':   {'A': 1.1, 'C': 0.8, 'E': 1.0, 'D': 0.7},
    'ILIMLI_YUKARI': {'A': 1.1, 'C': 0.9, 'E': 1.0, 'D': 0.9},
    'YATAY':         {'A': 1.2, 'C': 0.9, 'E': 1.1, 'D': 0.9},
    'ZAYIF':         {'A': 1.1, 'C': 0.8, 'E': 1.0, 'D': 1.0},
    'DUSUS':         {'A': 0.9, 'C': 0.7, 'E': 0.8, 'D': 1.0},
    'BILINMEZ':      {'A': 1.0, 'C': 1.0, 'E': 1.0, 'D': 1.0},
}
_TAV_KAT_INFO = {'A': ('📈', 'Momentum'), 'C': ('🤐', 'Sıkışma'), 'E': ('🎯', 'Direnç'), 'D': ('🔄', 'Dip Dönüş')}


def _tav_hikaye(r):
    k = r['kat']
    if k == 'A': return f"10g %{r['ret_10g']:+.0f} · RSI {r['RSI']} · 52H zirvede"
    if k == 'C': return f"Bant darlığı {r['bb_rank']} · 20g direncin %{r['near_h20']}'inde"
    if k == 'E': return f"20g zirvenin %{r['near_h20']}'inde · hacim {r['vr_t']:.1f}x"
    if k == 'D': return f"52H'nin %{r['pos_52h']}'inde · RSI {r['RSI']} · sessiz"
    return ""


def compute_panel():
    files = glob.glob(f'{VERILER}/*.IS_1d.parquet')
    if not files:
        return pd.DataFrame(), 'BILINMEZ', 0.0, None
    rejim, chg, target_date = 'BILINMEZ', 0.0, None
    today = pd.Timestamp.now(tz='UTC').date()   # UTC günü (tz-bağımsız .date karşılaştırması)
    drop_today = False   # bugünün barı seans-içi (henüz kapanmamış) mı? → son TAM güne göre hesapla
    xu_path = f'{VERILER}/XU100.IS_1d.parquet'
    if os.path.exists(xu_path):
        try:
            xu = pd.read_parquet(xu_path)
            xt = xu[xu.index.date >= today]
            if not xt.empty and float(xt.iloc[-1]['Volume']) <= 0:
                drop_today = True            # XU100 hacmi 0 → BIST seansı açık → yarım barı at
                xu = xu[xu.index.date < today]
            if len(xu) >= 11:
                chg = (xu.iloc[-1]['Close'] / xu.iloc[-11]['Close'] - 1) * 100
                rejim = ('HIZLI_RALLI' if chg >= 5 else 'ILIMLI_YUKARI' if chg >= 2 else
                         'YATAY' if chg >= -2 else 'ZAYIF' if chg >= -5 else 'DUSUS')
                target_date = xu.index[-1]
        except Exception:
            pass
    agirlik = _TAV_REJIM_AGIRLIK[rejim]
    MIN_VOL_TL = 2_000_000
    rows = []
    for f in files:
        tk = os.path.basename(f).replace('.IS_1d.parquet', '')
        if tk in ('XU100', 'XU030', 'XU050', 'XBANK', 'XUSIN', 'XUMAL'):
            continue
        try:
            df = pd.read_parquet(f)
            if drop_today:
                df = df[df.index.date < today]     # seans-içi bugünün yarım barını at
            if len(df) < 80:
                continue
            i = len(df) - 1
            feat = _tav_features(df, i)
            if feat is None or feat['vol_tl'] < MIN_VOL_TL:
                continue
            if _liquidity_manip(df).get('manip') == 'yüksek':   # ince tahta pompa → ele
                continue
            sA = _tav_score_A(feat) * agirlik['A']; sC = _tav_score_C(feat) * agirlik['C']
            sE = _tav_score_E(feat) * agirlik['E']; sD = _tav_score_D(feat) * agirlik['D']
            bA = bC = bE = bD = 0
            if pd.notna(feat['pct_T']) and pd.notna(feat['vol_T']):
                if feat['pct_T'] > 2 and feat['vol_T'] > 1.2: bA += 12; bE += 18; bC += 6
                elif feat['pct_T'] > 1: bA += 6; bE += 9; bC += 3
                elif feat['pct_T'] < -3 and feat['vol_T'] < 0.7: bD += 15
            if pd.notna(feat['vol_5g_slope']):
                if feat['vol_5g_slope'] > 0.5: bA += 8; bE += 10; bC += 8
                elif feat['vol_5g_slope'] > 0.2: bA += 4; bE += 5; bC += 4
            if feat['is_doji']: bC += 12
            if feat['is_green'] and feat['body_pct'] > 60: bA += 8; bE += 10
            if feat['is_hammer']: bD += 10
            if pd.notna(feat['ret_5g']):
                if feat['ret_5g'] > 10: bA += 8
                elif feat['ret_5g'] < -8: bD += 8
            sA += bA; sC += bC; sE += bE; sD += bD
            scores = {'A': sA, 'C': sC, 'E': sE, 'D': sD}
            best_kat = max(scores, key=scores.get); best_score = scores[best_kat]
            srt = sorted(scores.values(), reverse=True)
            conf = max(0, (srt[1] - 30)) * 0.6
            if len(srt) >= 3 and srt[2] > 30:
                conf += (srt[2] - 30) * 0.3
            rows.append({'tk': tk, 'fiyat': round(feat['close'], 2), 'skor': round(best_score + conf, 1),
                         'kat': best_kat, 'RSI': int(round(feat['rsi'])) if pd.notna(feat['rsi']) else None,
                         'pos_52h': int(round(feat['pos_52h'])) if pd.notna(feat['pos_52h']) else None,
                         'bb_rank': int(round(feat['bb_rank'])) if pd.notna(feat['bb_rank']) else None,
                         'vr_t': round(feat['vr_t'], 2) if pd.notna(feat['vr_t']) else None,
                         'near_h20': int(round(feat['near_h20'])) if pd.notna(feat['near_h20']) else None,
                         'ret_10g': round(feat['ret_10g'], 1) if pd.notna(feat['ret_10g']) else None})
        except Exception:
            continue
    df = pd.DataFrame(rows).sort_values('skor', ascending=False).reset_index(drop=True)
    return df, rejim, round(chg, 2), target_date


# ═══════════ DÜNKÜ LİSTE KARNESİ (gün içi en yükseğe göre) ═══════════
def _load_state():
    try:
        with open(STATE_FILE, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return None


def _save_state(target_date, sent_rows):
    """Bugün yollanan listeyi yarının karnesi için kaydet (ticker + kategori + referans tarih)."""
    try:
        as_of = target_date.strftime('%Y-%m-%d') if target_date is not None else None
        lst = [
            {
                'rank': rank,
                'tk': r['tk'],
                'kat': r['kat'],
                'skor': float(r['skor']),
                'close': float(r['fiyat']),
            }
            for rank, (_, r) in enumerate(sent_rows.iterrows(), start=1)
        ]
        with open(STATE_FILE, 'w', encoding='utf-8') as fh:
            json.dump({
                'engine': 'v1',
                'as_of': as_of,
                'sent_at': datetime.now(pytz.timezone('Europe/Istanbul')).isoformat(),
                'list': lst,
            }, fh, ensure_ascii=False, indent=1)
    except Exception as e:
        print('state kaydı hata:', e)


def _eval_prev_list(state):
    """Önceki listedeki her hisse için: SONRAKİ seansın gün-içi EN YÜKSEĞİ ÷ referans kapanış.
    Referans = listenin yollandığı andaki son bar (as_of) kapanışı."""
    if not state:
        print('  [karne-tani] state dosyası okunamadı/yok')
        return [], None
    if not state.get('as_of') or not state.get('list'):
        print(f'  [karne-tani] state eksik alan: as_of={state.get("as_of")!r} list_len={len(state.get("list") or [])}')
        return [], None
    as_of = pd.Timestamp(state['as_of'])
    res, ev_date = [], None
    skipped = []
    for it in state['list']:
        tk = it.get('tk')
        p = f'{VERILER}/{tk}.IS_1d.parquet'
        if not tk:
            skipped.append((tk, 'ticker_yok')); continue
        if not os.path.exists(p):
            skipped.append((tk, 'parquet_yok')); continue
        try:
            d = pd.read_parquet(p)
            mask = d.index <= as_of
            if not mask.any():
                skipped.append((tk, f'as_of({as_of.date()})_oncesi_bar_yok son_bar={d.index[-1].date() if len(d) else None}'))
                continue
            bpos = int(mask.sum()) - 1          # referans bar (as_of)
            if bpos + 1 >= len(d):               # değerlendirilecek seans henüz kapanmamış
                skipped.append((tk, f'sonraki_seans_henuz_yok son_bar={d.index[-1].date()}'))
                continue
            base = float(d.iloc[bpos]['Close'])
            ev = d.iloc[bpos + 1]                # adayların hareket ettiği seans
            open_price = float(ev['Open'])
            high = float(ev['High'])
            close = float(ev['Close'])
            if min(base, open_price, high, close) <= 0:
                skipped.append((tk, 'base_close_sifir_veya_negatif')); continue
            res.append({'tk': tk, 'kat': it.get('kat'),
                        'base': base, 'open': open_price, 'high': high, 'close': close,
                        'ret': (high / base - 1) * 100,
                        'close_ret': (close / base - 1) * 100,
                        'open_high_ret': (high / open_price - 1) * 100,
                        'open_close_ret': (close / open_price - 1) * 100})
            ev_date = d.index[bpos + 1]
        except Exception as e:
            skipped.append((tk, f'exception: {e}')); continue
    if skipped:
        print(f'  [karne-tani] {len(skipped)} hisse atlandı:')
        for tk, why in skipped:
            print(f'    - {tk}: {why}')
    res.sort(key=lambda x: x['ret'], reverse=True)
    return res, ev_date


def _build_prev_report(res, ev_date):
    if not res:
        return None
    ds = ev_date.strftime('%d.%m.%Y') if ev_date is not None else ''
    L = ["📊 DÜNKÜ YÜKSEK GETİRİ ADAYLARI — NE YAPTI?",
         f"{ds} seansı · gün içi EN YÜKSEĞE göre (önceki kapanış → gün-içi zirve)", ""]
    medals = ['🥇', '🥈', '🥉']
    for n, r in enumerate(res):
        m = medals[n] if n < 3 else '▫️'
        L.append(f"{m} {r['tk']:<6} {r['ret']:+.1f}%   {r['base']:.2f} → {r['high']:.2f}")
    rets = [r['ret'] for r in res]
    avg = sum(rets) / len(rets)
    poz = sum(1 for x in rets if x > 0)
    best = res[0]
    L.append("───────────────")
    L.append(f"🏆 En iyi: {best['tk']} {best['ret']:+.1f}%  ·  Ortalama {avg:+.1f}%  ·  {len(res)} adaydan {poz} tanesi artıda")
    L.append("\nℹ️ Gün-içi zirveye göre — kapanışı yansıtmaz. Geçmiş performans, gelecek getiri vaadi değildir.")
    return "\n".join(L)


def _candidate_rows(frame):
    return [
        {
            'rank': rank,
            'symbol': str(row['tk']),
            'score': float(row['skor']),
            'category': str(row['kat']),
            'signal_close': float(row['fiyat']),
            'reason': _tav_hikaye(row),
        }
        for rank, (_, row) in enumerate(frame.iterrows(), start=1)
    ]


def _state_candidate_rows(state, results=None):
    by_symbol = {str(row['tk']).strip().upper(): row for row in (results or [])}
    rows = []
    for fallback_rank, item in enumerate(state.get('list') or [], start=1):
        symbol = str(item.get('tk', '')).strip().upper()
        result = by_symbol.get(symbol)
        rows.append({
            'rank': int(item.get('rank', fallback_rank)),
            'symbol': symbol,
            'score': item.get('skor'),
            'category': str(item.get('kat', '')),
            'signal_close': float(result['base']) if result else item.get('close'),
        })
    return rows


def _result_rows(results):
    return [
        {
            'symbol': row['tk'],
            'signal_close': row['base'],
            't1_open': row['open'],
            't1_high': row['high'],
            't1_close': row['close'],
            'close_to_high_return_pct': row['ret'],
            'close_to_close_return_pct': row['close_ret'],
            'open_to_high_return_pct': row['open_high_ret'],
            'open_to_close_return_pct': row['open_close_ret'],
        }
        for row in results
    ]


def main():
    df, rejim, chg, target_date = compute_panel()
    if df.empty:
        raise DataUnavailable('V1 tarama havuzu boş; güncel günlük parquet verisi üretilemedi.')
    require_data_date(target_date, timing='morning', enabled=not TEST)
    alarm = df[df['skor'] >= ALARM_ESIK]
    targets = [ADMIN_ID] if TEST else BROADCAST

    # ── 1) ÖNCE: dünkü adayların karnesi ──
    state = None if skip_previous_report() else _load_state()
    res, ev_date = _eval_prev_list(state)
    rep = _build_prev_report(res, ev_date)

    if not TEST:
        try:
            if state and state.get('as_of') and state.get('list'):
                stage_candidates(
                    PATRON2_DB,
                    'v1',
                    str(state['as_of']),
                    _state_candidate_rows(state, res),
                    published=True,
                    published_at=state.get('sent_at'),
                )
                settle_results(
                    PATRON2_DB,
                    'v1',
                    str(state['as_of']),
                    ev_date,
                    _result_rows(res),
                )
            signal_date = pd.Timestamp(target_date).strftime('%Y-%m-%d')
            stage_candidates(PATRON2_DB, 'v1', signal_date, _candidate_rows(alarm.head(TOPN)))
        except Exception as e:
            print('patron2.db V1 kaydı başarısız; izsiz ilan yapılmadı:', e)
            return 1
    if rep:
        print(rep, '\n')
        for ch in targets:
            print('karne gönderim:', 'OK' if tg_send(ch, rep) else 'BAŞARISIZ', '→', ch)
    else:
        print('(önceki gün listesi yok — karne atlandı)')

    # ── 2) SONRA: bugünün listesi ──
    # Başlıkta BUGÜNÜN (TR) tarihi gösterilir — taramanın hedefi bugünkü seans.
    # target_date (son kapanış) sadece state/karne referansı için kullanılır, mesaj başlığına YAZILMAZ.
    _ds = datetime.now(pytz.timezone("Europe/Istanbul")).strftime('%d.%m.%Y')
    L = [f"🚀 BUGÜNKÜ YÜKSEK GETİRİ ADAYLARI — {_ds}",
         "İsabet oranı %11 · ortalamanın 3.4 katı (60g/1131 tavan backtest)",
         f"Rejim: {rejim.replace('_',' ')} · XU100 {chg:+.2f}%"]
    if alarm.empty:
        L.append("\nBugün Skor≥150 alarm yok — piyasa tavan üretmiyor.")
    else:
        L.append(f"\n🚨 ALARM (Skor ≥150 · {len(alarm)} hisse, top {min(TOPN,len(alarm))}):")
        for _, r in alarm.head(TOPN).iterrows():
            ic, knm = _TAV_KAT_INFO.get(r['kat'], ('•', ''))
            L.append(f"{ic} {r['tk']:<6} ({r['skor']:.0f}) {knm} — {_tav_hikaye(r)}")
    L.append("\nℹ️ Algoritmanın izlediği YÜKSEK GETİRİ adayları — kesinlik değil, olasılık. Yatırım tavsiyesi değildir.")
    msg = "\n".join(L)
    print(msg)
    for ch in targets:
        print('gönderim:', 'OK' if tg_send(ch, msg) else 'BAŞARISIZ', '→', ch)

    # ── 3) Bugünün listesini yarının karnesi için kaydet (test modunda state'e dokunma) ──
    if not TEST:
        _save_state(target_date, alarm.head(TOPN))
        try:
            mark_run_published(PATRON2_DB, 'v1', signal_date, min(TOPN, len(alarm)))
        except Exception as e:
            print('V1 ilanı gönderildi fakat patron2.db yayın işareti yazılamadı:', e)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(guarded_main(
        engine='v1_yuksek_getiri',
        label='V1 YÜKSEK GETİRİ MOTORU',
        main_func=main,
        targets=(ADMIN_ID,) if TEST else BROADCAST,
        base=BASE,
        live=not TEST,
    ))
